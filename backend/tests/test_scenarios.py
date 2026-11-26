import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.services import store
from api_test_support import authenticated_client, isolate_test_user


class ScenarioIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temporary_directory.name) / "incidents.json"
        self.data_file_patch = patch.object(store, "DATA_FILE", self.data_file)
        self.data_file_patch.start()
        store.save_state(store.empty_state())
        self.user_file_patch, self.user_id = isolate_test_user(self.temporary_directory.name)
        self.app = create_app({
            "TESTING": True,
            "SESSION_FILE_DIR": str(Path(self.temporary_directory.name) / "sessions"),
        })
        self.client = authenticated_client(self.app.test_client(), self.user_id)

    def tearDown(self):
        self.user_file_patch.stop()
        self.data_file_patch.stop()
        self.temporary_directory.cleanup()

    @staticmethod
    def scenario(name, department_id, failure_type="department_outage", severity="high", **extra):
        return {
            "name": name,
            "departmentId": department_id,
            "failureType": failure_type,
            "severity": severity,
            **extra,
        }

    def analyze(self, scenarios):
        return self.client.post("/api/analytics/scenarios", json={"scenarios": scenarios})

    def assert_invalid(self, response):
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.is_json)
        payload = response.get_json()
        self.assertEqual(payload["status"], "error")
        self.assertIsInstance(payload["message"], str)
        self.assertIsInstance(payload["code"], str)
        return payload

    def test_single_scenario_uses_bfs_and_returns_estimated_fields(self):
        response = self.analyze(
            [self.scenario("Infrastructure Failure", "infrastructure", "power_failure", "critical")]
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["scenarioCount"], 1)
        scenario = payload["scenarios"][0]
        self.assertEqual(scenario["departmentId"], "infrastructure")
        self.assertEqual(scenario["directlyAffected"], ["classrooms", "laboratories"])
        self.assertIn("examinations", scenario["indirectlyAffected"])
        self.assertEqual(scenario["affectedDepartmentCount"], 4)
        self.assertGreater(scenario["estimatedHealthImpact"], 0)
        self.assertGreater(scenario["estimatedRiskImpact"], 0)
        self.assertIn("currentInstitutionalHealthScore", scenario)
        self.assertIn("currentAverageInstitutionalRiskScore", scenario)
        self.assertIn("estimatedInstitutionalHealthAfter", scenario)
        self.assertIn("estimatedInstitutionalRiskAfter", scenario)
        self.assertTrue(scenario["criticalDepartmentsAffected"])
        self.assertIn("estimated", scenario["explanation"])
        self.assertIn("simulated estimates", payload["disclaimer"])

    def test_multiple_scenarios_severity_and_multi_department_roots(self):
        scenarios = [
            self.scenario("Light outage", "infrastructure", "power_failure", "low"),
            self.scenario("Severe outage", "infrastructure", "power_failure", "critical"),
            self.scenario(
                "Combined disruption",
                "laboratories",
                "equipment_failure",
                "high",
                departmentIds=["transportation"],
                description="Two hypothetical initiating departments",
            ),
        ]
        response = self.analyze(scenarios)
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        self.assertEqual(result["scenarioCount"], 3)
        by_name = {item["name"]: item for item in result["scenarios"]}
        self.assertGreater(
            by_name["Severe outage"]["estimatedRiskImpact"],
            by_name["Light outage"]["estimatedRiskImpact"],
        )
        combined = by_name["Combined disruption"]
        self.assertEqual(combined["departmentIds"], ["laboratories", "transportation"])
        affected_ids = set(
            combined["directlyAffected"]
            + combined["indirectlyAffected"]
            + combined["departmentIds"]
        )
        self.assertEqual(len(affected_ids), combined["affectedDepartmentCount"])
        self.assertEqual(len(set(combined["directlyAffected"]) & set(combined["indirectlyAffected"])), 0)

    def test_comparison_worst_case_and_department_frequency_are_deterministic(self):
        scenarios = [
            self.scenario("Narrow", "laboratories", "equipment_failure", "medium"),
            self.scenario("Broad", "administration", "department_outage", "high"),
            self.scenario("Transit", "transportation", "transport_disruption", "critical"),
        ]
        first = self.analyze(scenarios)
        second = self.analyze(scenarios)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json(), second.get_json())
        payload = first.get_json()
        scenario_by_name = {item["name"]: item for item in payload["scenarios"]}
        self.assertEqual(payload["comparison"]["highestImpactScenario"], "Broad")
        self.assertEqual(payload["comparison"]["highestRiskScenario"], "Broad")
        self.assertEqual(payload["institutionalSummary"]["worstCaseScenario"], "Broad")
        self.assertEqual(
            payload["institutionalSummary"]["averageAffectedDepartments"],
            round(sum(item["affectedDepartmentCount"] for item in payload["scenarios"]) / 3, 1),
        )
        freq = {item["departmentId"]: item["scenarioCount"] for item in payload["departmentImpactFrequency"]}
        self.assertEqual(freq["infrastructure"], 1)
        self.assertEqual(freq["transportation"], 2)
        self.assertTrue(payload["institutionalSummary"]["mostFrequentlyAffectedDepartments"])
        self.assertEqual(len(payload["comparison"]["rankedScenarios"]), 3)
        self.assertIn(scenario_by_name["Broad"]["scenarioIndex"], (1, 2, 3))

    def test_duplicate_names_are_allowed_and_disambiguated_by_scenario_index(self):
        response = self.analyze(
            [
                self.scenario("Duplicate", "laboratories", "equipment_failure", "low"),
                self.scenario("Duplicate", "administration", "department_outage", "high"),
            ]
        )
        self.assertEqual(response.status_code, 200)
        scenarios = response.get_json()["scenarios"]
        self.assertEqual([item["name"] for item in scenarios], ["Duplicate", "Duplicate"])
        self.assertEqual([item["scenarioIndex"] for item in scenarios], [1, 2])
        self.assertEqual([item["scenarioId"] for item in scenarios], ["scenario-1", "scenario-2"])
        self.assertIn(response.get_json()["comparison"]["highestRiskScenarioIndex"], (1, 2))

    def test_invalid_scenarios_and_request_limits_use_json_errors(self):
        malformed = self.client.post("/api/analytics/scenarios", data="{broken", content_type="application/json")
        self.assert_invalid(malformed)
        self.assert_invalid(self.client.post("/api/analytics/scenarios", json={}))
        self.assert_invalid(self.analyze([]))
        self.assert_invalid(self.analyze([self.scenario(f"scenario-{i}", "laboratories") for i in range(11)]))
        maximum = self.analyze(
            [self.scenario(f"scenario-{i}", "laboratories", "equipment_failure", "low") for i in range(10)]
        )
        self.assertEqual(maximum.status_code, 200, maximum.get_json())
        self.assertEqual(maximum.get_json()["scenarioCount"], 10)
        for bad in (
            {"departmentId": "laboratories", "failureType": "equipment_failure", "severity": "high"},
            self.scenario(" ", "laboratories"),
            self.scenario("Bad department", "unknown"),
            self.scenario("Bad type", "laboratories", "unknown_failure"),
            self.scenario("Bad severity", "laboratories", severity="urgent"),
            self.scenario("Bad description", "laboratories", description=42),
            self.scenario("Bad department list", "laboratories", departmentIds="transportation"),
            self.scenario("Power at lab", "laboratories", "power_failure"),
        ):
            with self.subTest(scenario=bad):
                self.assert_invalid(self.analyze([bad]))

    def test_duplicate_departments_are_deduplicated_and_scenario_request_never_persists(self):
        before = self.data_file.read_bytes()
        response = self.analyze(
            [
                self.scenario(
                    "Deduplicated roots",
                    "laboratories",
                    "equipment_failure",
                    departmentIds=["laboratories", "transportation", "transportation"],
                )
            ]
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        scenario = response.get_json()["scenarios"][0]
        self.assertEqual(scenario["departmentIds"], ["laboratories", "transportation"])
        self.assertEqual(self.data_file.read_bytes(), before)
        self.assertEqual(self.client.get("/api/incidents").get_json(), [])


if __name__ == "__main__":
    unittest.main()
