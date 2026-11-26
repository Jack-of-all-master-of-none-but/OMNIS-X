import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.models.catalog import DEPARTMENTS
from app.services import store
from app.services.risk_intelligence import (
    RISK_FACTOR_WEIGHTS,
    calculate_department_risks,
    risk_level,
    weighted_risk_score,
)
from api_test_support import authenticated_client, isolate_test_user


class RiskIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temporary_directory.name) / "incidents.json"
        self.data_file_patch = patch.object(store, "DATA_FILE", self.data_file)
        self.data_file_patch.start()
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

    def test_weights_sum_to_100_and_weighted_score_boundaries_are_clamped(self):
        self.assertEqual(sum(RISK_FACTOR_WEIGHTS.values()), 100)
        factors = {factor: 100 for factor in RISK_FACTOR_WEIGHTS}
        self.assertEqual(weighted_risk_score(factors), 100.0)
        self.assertEqual(weighted_risk_score({factor: 0 for factor in RISK_FACTOR_WEIGHTS}), 0.0)

    def test_risk_level_threshold_boundaries(self):
        cases = (
            (0, "LOW"),
            (24.9, "LOW"),
            (25, "MODERATE"),
            (49.9, "MODERATE"),
            (50, "HIGH"),
            (74.9, "HIGH"),
            (75, "CRITICAL"),
            (100, "CRITICAL"),
            (-5, "LOW"),
            (120, "CRITICAL"),
        )
        for score, expected in cases:
            with self.subTest(score=score):
                self.assertEqual(risk_level(score), expected)

    def test_every_department_has_complete_explainable_risk_data(self):
        result = calculate_department_risks({"incidents": []})
        rows = result["departments"]
        self.assertEqual(len(rows), 12)
        self.assertEqual({row["departmentId"] for row in rows}, {item["id"] for item in DEPARTMENTS})
        self.assertEqual(sum(result["institutionalRisk"]["riskLevelCounts"].values()), 12)
        self.assertGreaterEqual(result["institutionalRisk"]["averageInstitutionalRiskScore"], 0)
        self.assertLessEqual(result["institutionalRisk"]["averageInstitutionalRiskScore"], 100)
        sorted_rows = sorted(rows, key=lambda row: (-row["riskScore"], row["departmentName"]))
        self.assertEqual(
            [row["departmentId"] for row in result["institutionalRisk"]["highestRiskDepartments"]],
            [row["departmentId"] for row in sorted_rows[:5]],
        )
        self.assertTrue(result["institutionalRisk"]["mostImportantContributingFactors"])
        for row in rows:
            with self.subTest(department=row["departmentId"]):
                self.assertGreaterEqual(row["riskScore"], 0)
                self.assertLessEqual(row["riskScore"], 100)
                self.assertIn(row["riskLevel"], {"LOW", "MODERATE", "HIGH", "CRITICAL"})
                self.assertEqual(set(row["factorScores"]), set(RISK_FACTOR_WEIGHTS))
                self.assertTrue(row["explanation"])
                self.assertTrue(row["mainContributingFactors"])
            by_id = {row["departmentId"]: row for row in rows}
            self.assertEqual(by_id["infrastructure"]["factorScores"]["operationalStatus"], 100)
            self.assertEqual(by_id["laboratories"]["factorScores"]["operationalStatus"], 60)

    def test_dependency_impact_uses_transitive_directed_reachability(self):
        result = calculate_department_risks({"incidents": []})
        by_id = {row["departmentId"]: row for row in result["departments"]}
        administration = by_id["administration"]
        students = by_id["students"]
        self.assertEqual(administration["reachableDepartmentCount"], 10)
        self.assertEqual(administration["factorScores"]["dependencyImpact"], 90.9)
        self.assertEqual(students["reachableDepartmentCount"], 8)
        self.assertGreater(
            administration["factorScores"]["dependencyImpact"],
            students["factorScores"]["dependencyImpact"],
        )

    def test_active_incident_severity_increases_risk_for_affected_departments(self):
        incident = {
            "id": "incident-risk-test",
            "status": "active",
            "departmentId": "laboratories",
            "severity": "critical",
            "simulationResult": {
                "affectedDepartmentIds": ["laboratories", "classrooms"],
            },
        }
        baseline = calculate_department_risks({"incidents": []})
        with_incident = calculate_department_risks({"incidents": [incident]})
        baseline_by_id = {row["departmentId"]: row for row in baseline["departments"]}
        incident_by_id = {row["departmentId"]: row for row in with_incident["departments"]}

        for department_id in ("laboratories", "classrooms"):
            self.assertEqual(incident_by_id[department_id]["factorScores"]["activeIncidentSeverity"], 100)
            self.assertGreater(incident_by_id[department_id]["riskScore"], baseline_by_id[department_id]["riskScore"])
        self.assertEqual(incident_by_id["students"]["factorScores"]["activeIncidentSeverity"], 0)

    def test_results_are_deterministic_for_same_current_state(self):
        state = {
            "incidents": [
                {
                    "id": "incident-deterministic",
                    "status": "active",
                    "departmentId": "infrastructure",
                    "severity": "high",
                    "simulationResult": {"affectedDepartmentIds": ["infrastructure", "placements"]},
                }
            ]
        }
        self.assertEqual(calculate_department_risks(state), calculate_department_risks(state))

    def test_risk_endpoint_shape_and_reads_do_not_persist_reports(self):
        created = self.client.post(
            "/api/incidents",
            json={
                "departmentId": "laboratories",
                "failureType": "equipment_failure",
                "severity": "high",
                "description": "risk endpoint test incident",
            },
        )
        self.assertEqual(created.status_code, 201)
        incident_id = created.get_json()["id"]
        simulated = self.client.post("/api/simulations", json={"incidentId": incident_id})
        self.assertEqual(simulated.status_code, 200)
        before = self.data_file.read_bytes()
        analytics_before = self.client.get("/api/analytics/recovery").get_json()

        first = self.client.get("/api/analytics/risks")
        second = self.client.get("/api/analytics/risks")

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.is_json)
        payload = first.get_json()
        self.assertEqual(set(payload), {"riskWeights", "departments", "institutionalRisk"})
        self.assertEqual(len(payload["departments"]), 12)
        self.assertIn("highestRiskDepartments", payload["institutionalRisk"])
        self.assertIn("riskLevelCounts", payload["institutionalRisk"])
        self.assertIn("averageInstitutionalRiskScore", payload["institutionalRisk"])
        self.assertIn("mostImportantContributingFactors", payload["institutionalRisk"])
        self.assertEqual(payload, second.get_json())
        self.assertEqual(self.data_file.read_bytes(), before)
        self.assertEqual(self.client.get("/api/analytics/recovery").get_json(), analytics_before)


if __name__ == "__main__":
    unittest.main()
