import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.models.catalog import DEPARTMENTS
from app.services import store
from app.services.dependency_intelligence import calculate_dependency_intelligence
from api_test_support import authenticated_client, isolate_test_user


class DependencyIntelligenceTests(unittest.TestCase):
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

    def test_dependency_counts_handle_direct_edges_indirect_reach_and_cycles(self):
        result = calculate_dependency_intelligence({"incidents": []})
        by_id = {item["departmentId"]: item for item in result["departments"]}

        administration = by_id["administration"]
        self.assertEqual(administration["directDownstreamDependencyCount"], 5)
        self.assertEqual(administration["indirectlyAffectedDepartmentCount"], 5)
        self.assertEqual(administration["totalDependencyReach"], 10)
        self.assertEqual(by_id["laboratories"]["directDownstreamDependencyCount"], 0)
        self.assertEqual(by_id["laboratories"]["indirectlyAffectedDepartmentCount"], 0)
        self.assertEqual(by_id["laboratories"]["totalDependencyReach"], 0)
        self.assertNotIn("administration", administration.get("downstreamDepartmentIds", []))
        self.assertEqual(len(result["departments"]), 12)
        self.assertEqual(len({item["departmentId"] for item in result["departments"]}), 12)

    def test_existing_catalog_finds_confirmed_and_potential_single_points(self):
        result = calculate_dependency_intelligence({"incidents": []})
        findings = result["institutionalSummary"]["singlePointsOfFailure"]
        confirmed_departments = {item["departmentId"] for item in findings["confirmed"]["departments"]}
        potential_operations = {item["operationId"] for item in findings["potential"]["operations"]}
        limited_redundancy_operations = {item["operationId"] for item in findings["potential"]["operations"]}

        self.assertIn("students", confirmed_departments)
        self.assertIn("operation-laboratory-a-practical", potential_operations)
        self.assertIn("operation-transport-route", potential_operations)
        self.assertNotIn("operation-campus-internet", limited_redundancy_operations)
        self.assertGreaterEqual(findings["countsByClassification"]["CONFIRMED"]["departments"], 1)
        self.assertGreaterEqual(findings["countsByClassification"]["POTENTIAL"]["operations"], 1)

    def test_alternatives_require_matching_compatibility_and_sufficient_capacity(self):
        departments = [
            {"id": "alpha", "name": "Alpha", "priority": "high", "status": "operational"},
            {"id": "beta", "name": "Beta", "priority": "medium", "status": "operational"},
        ]
        operations = [
            {
                "id": "operation-alpha",
                "name": "Alpha service",
                "departmentId": "alpha",
                "primaryResourceIds": ["primary"],
                "requirements": [
                    {"id": "need", "resourceType": "service", "operation": "run", "demand": 10, "unit": "units"}
                ],
            }
        ]
        resources = [
            {"id": "primary", "name": "Primary", "departmentId": "alpha", "type": "service", "capacity": 10, "available": 0, "unit": "units", "status": "unavailable", "compatibleOperations": ["run"]},
            {"id": "backup-a", "name": "Backup A", "departmentId": "alpha", "type": "service", "capacity": 4, "available": 4, "unit": "units", "status": "available", "compatibleOperations": ["run"]},
            {"id": "backup-b", "name": "Backup B", "departmentId": "alpha", "type": "service", "capacity": 5, "available": 5, "unit": "units", "status": "available", "compatibleOperations": ["run"]},
            {"id": "wrong-operation", "name": "Incompatible", "departmentId": "alpha", "type": "service", "capacity": 100, "available": 100, "unit": "units", "status": "available", "compatibleOperations": ["other"]},
        ]
        result = calculate_dependency_intelligence(
            {"incidents": []},
            departments=departments,
            dependencies=[],
            operations=operations,
            resources=resources,
        )
        findings = result["institutionalSummary"]["singlePointsOfFailure"]
        self.assertIn("operation-alpha", {item["operationId"] for item in findings["confirmed"]["operations"]})

        resources[2]["capacity"] = 6
        resources[2]["available"] = 6
        result = calculate_dependency_intelligence(
            {"incidents": []},
            departments=departments,
            dependencies=[],
            operations=operations,
            resources=resources,
        )
        findings = result["institutionalSummary"]["singlePointsOfFailure"]
        potential = next(item for item in findings["potential"]["operations"] if item["operationId"] == "operation-alpha")
        self.assertEqual(potential["alternativeConfigurationCount"], 1)
        self.assertNotIn("operation-alpha", {item["operationId"] for item in findings["confirmed"]["operations"]})

        resources.append(
            {"id": "backup-c", "name": "Backup C", "departmentId": "alpha", "type": "service", "capacity": 10, "available": 10, "unit": "units", "status": "available", "compatibleOperations": ["run"]}
        )
        result = calculate_dependency_intelligence(
            {"incidents": []},
            departments=departments,
            dependencies=[],
            operations=operations,
            resources=resources,
        )
        findings = result["institutionalSummary"]["singlePointsOfFailure"]
        self.assertNotIn("operation-alpha", {item["operationId"] for item in findings["potential"]["operations"]})

    def test_incident_summary_and_dependency_chain_are_included(self):
        state = {
            "incidents": [
                {
                    "id": "incident-alpha",
                    "status": "active",
                    "departmentId": "administration",
                    "simulationResult": {"affectedDepartmentIds": ["administration", "infrastructure"]},
                }
            ]
        }
        result = calculate_dependency_intelligence(state)
        summary = result["institutionalSummary"]
        self.assertEqual(summary["activeIncidentAffectedDepartmentCount"], 2)
        self.assertTrue(summary["mostCriticalDepartments"])
        self.assertTrue(summary["highestImpactDependencyChains"])
        chain = summary["highestImpactDependencyChains"][0]
        self.assertGreaterEqual(chain["chainLength"], 1)
        self.assertEqual(chain["pathDepartmentIds"][0], chain["startDepartmentId"])
        self.assertEqual(chain["pathDepartmentIds"][-1], chain["endDepartmentId"])

    def test_api_response_is_valid_and_repeated_get_does_not_persist(self):
        created = self.client.post(
            "/api/incidents",
            json={
                "departmentId": "administration",
                "failureType": "department_outage",
                "severity": "high",
                "description": "dependency intelligence API test",
            },
        )
        self.assertEqual(created.status_code, 201)
        incident_id = created.get_json()["id"]
        simulated = self.client.post("/api/simulations", json={"incidentId": incident_id})
        self.assertEqual(simulated.status_code, 200)
        before = self.data_file.read_bytes()

        first = self.client.get("/api/analytics/dependencies")
        second = self.client.get("/api/analytics/dependencies")

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.is_json)
        self.assertEqual(first.get_json(), second.get_json())
        payload = first.get_json()
        self.assertTrue({"departments", "institutionalSummary"} <= payload.keys())
        self.assertEqual(len(payload["departments"]), 12)
        summary = payload["institutionalSummary"]
        self.assertTrue(
            {
                "mostCriticalDepartments",
                "highestImpactDependencyChains",
                "singlePointsOfFailure",
                "departmentCriticalityCounts",
                "activeIncidentAffectedDepartmentCount",
            }
            <= summary.keys()
        )
        self.assertTrue(
            {"CONFIRMED", "POTENTIAL"}
            <= summary["singlePointsOfFailure"]["countsByClassification"].keys()
        )
        self.assertTrue(
            {
                "departmentId",
                "departmentName",
                "directDownstreamDependencyCount",
                "indirectlyAffectedDepartmentCount",
                "totalDependencyReach",
                "criticalityClassification",
                "explanation",
            }
            <= payload["departments"][0].keys()
        )
        self.assertEqual(self.data_file.read_bytes(), before)

    def test_department_catalog_remains_complete(self):
        self.assertEqual(len(DEPARTMENTS), 12)
        self.assertEqual(len(calculate_dependency_intelligence({"incidents": []})["departments"]), 12)


if __name__ == "__main__":
    unittest.main()
