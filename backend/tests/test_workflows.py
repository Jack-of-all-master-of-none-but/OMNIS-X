import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.models.catalog import DEPARTMENTS, OPERATIONS, RESOURCES
from app.services import store


class WorkflowAndReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temporary_directory.name) / "incidents.json"
        self.data_file_patch = patch.object(store, "DATA_FILE", self.data_file)
        self.data_file_patch.start()
        self.app = create_app()
        self.app.config.update(TESTING=True)

    def tearDown(self):
        self.data_file_patch.stop()
        self.temporary_directory.cleanup()

    def create_incident(self, department_id="laboratories", failure_type="equipment_failure", severity="high"):
        with self.app.test_client() as client:
            response = client.post(
                "/api/incidents",
                json={
                    "departmentId": department_id,
                    "failureType": failure_type,
                    "severity": severity,
                    "description": "Isolated workflow test incident",
                },
            )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def simulate(self, incident_id):
        with self.app.test_client() as client:
            response = client.post("/api/simulations", json={"incidentId": incident_id})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def generate_plans(self, incident_id):
        with self.app.test_client() as client:
            response = client.post(f"/api/recovery/{incident_id}/plans")
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["recoveryResult"]

    def test_complete_incident_simulation_recovery_analytics_and_cleanup_workflow(self):
        incident = self.create_incident()
        incident_id = incident["id"]
        try:
            simulation = self.simulate(incident_id)
            self.assertTrue(simulation["ok"])
            self.assertEqual(simulation["initialFailure"]["departmentId"], "laboratories")
            self.assertEqual(simulation["affectedDepartmentIds"], ["laboratories"])

            result = self.generate_plans(incident_id)
            plan = next(
                item for item in result["plans"]
                if item["operationIds"] == ["operation-laboratory-a-practical"]
            )
            self.assertTrue(plan["feasible"])
            self.assertEqual(
                {assignment["resourceId"] for assignment in plan["resourceAllocations"]},
                {"resource-lab-b", "resource-lab-c"},
            )

            with self.app.test_client() as client:
                applied = client.post(
                    f"/api/recovery/{incident_id}/apply",
                    json={"planId": plan["id"]},
                )
                self.assertEqual(applied.status_code, 200, applied.get_json())
                state = client.get(f"/api/recovery/{incident_id}").get_json()
                analytics_recovery = client.get("/api/analytics/recovery").get_json()
                analytics_departments = client.get("/api/analytics/departments").get_json()

            self.assertIn("operation-laboratory-a-practical", state["recoveryState"]["restoredOperationIds"])
            self.assertEqual(analytics_recovery["totalRecoveryPlansApplied"], 1)
            self.assertEqual(analytics_recovery["operationsRestored"], 1)
            self.assertGreaterEqual(analytics_recovery["totalRecoveryPlansGenerated"], 1)
            lab_row = next(
                row for row in analytics_departments["departments"]
                if row["departmentId"] == "laboratories"
            )
            self.assertEqual(lab_row["restorationStatus"], "fully_restored")
            self.assertEqual(lab_row["affectedCount"], 0)

            with self.app.test_client() as client:
                resolved = client.patch(f"/api/incidents/{incident_id}/resolve")
                self.assertEqual(resolved.status_code, 200)
                self.assertEqual(resolved.get_json()["status"], "resolved")
                deleted = client.delete(f"/api/incidents/{incident_id}")
                self.assertEqual(deleted.status_code, 200)
                self.assertEqual(client.get("/api/incidents").get_json(), [])
        finally:
            with self.app.test_client() as client:
                if any(item.get("id") == incident_id for item in store.load_state()["incidents"]):
                    client.delete(f"/api/incidents/{incident_id}")

    def test_concurrent_api_incident_creation_keeps_every_record(self):
        worker_count = 24

        def create(index):
            with self.app.test_client() as client:
                return client.post(
                    "/api/incidents",
                    json={
                        "departmentId": "laboratories",
                        "failureType": "equipment_failure",
                        "severity": "medium",
                        "description": f"Concurrent create {index}",
                    },
                )

        with ThreadPoolExecutor(max_workers=12) as executor:
            responses = list(executor.map(create, range(worker_count)))

        self.assertTrue(all(response.status_code == 201 for response in responses))
        incident_ids = [response.get_json()["id"] for response in responses]
        self.assertEqual(len(set(incident_ids)), worker_count)
        self.assertEqual(len(store.load_state()["incidents"]), worker_count)

    def test_concurrent_plan_generation_records_all_requests_without_count_inflation(self):
        incident = self.create_incident("infrastructure", "power_failure", "critical")
        incident_id = incident["id"]
        self.simulate(incident_id)
        request_count = 8

        with ThreadPoolExecutor(max_workers=request_count) as executor:
            results = list(executor.map(lambda _: self.generate_plans(incident_id), range(request_count)))

        self.assertTrue(all(result["ok"] for result in results))
        saved = store.load_state()
        events = [
            event for event in saved["recoveryActivity"]
            if event.get("incidentId") == incident_id and event.get("type") == "plans_generated"
        ]
        self.assertEqual(len(events), request_count)
        self.assertTrue(all(event["planCount"] == len(results[0]["plans"]) for event in events))
        unique_plan_ids = {plan["id"] for plan in results[0]["plans"]}
        with self.app.test_client() as client:
            metrics = client.get("/api/analytics/recovery").get_json()
        self.assertEqual(metrics["totalRecoveryPlansGenerated"], len(unique_plan_ids))

    def test_conflicting_concurrent_plan_applications_reserve_resource_once(self):
        incidents = [
            self.create_incident("infrastructure", "power_failure", "critical")
            for _ in range(2)
        ]
        plan_by_incident = {}
        for incident in incidents:
            self.simulate(incident["id"])
            generated = self.generate_plans(incident["id"])
            plan_by_incident[incident["id"]] = next(
                plan for plan in generated["plans"]
                if plan["operationIds"] == ["operation-campus-electricity"]
                and plan["resourceIds"] == ["resource-backup-electricity"]
            )["id"]

        def apply(incident):
            with self.app.test_client() as client:
                return client.post(
                    f"/api/recovery/{incident['id']}/apply",
                    json={"planId": plan_by_incident[incident["id"]]},
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(apply, incidents))

        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        saved = store.load_state()
        matching_allocations = [
            allocation
            for incident in saved["incidents"]
            for allocation in incident.get("recoveryState", {}).get("allocations", [])
            if allocation.get("resourceId") == "resource-backup-electricity"
        ]
        self.assertEqual(len(matching_allocations), 1)
        self.assertEqual(len({item["resourceId"] for item in matching_allocations}), 1)

    def test_backend_catalog_regression_counts(self):
        with self.app.test_client() as client:
            department_response = client.get("/api/departments")
        self.assertEqual(department_response.status_code, 200)
        department_ids = {department["id"] for department in department_response.get_json()}
        self.assertEqual(
            department_ids,
            {
                "academic-management", "faculty-management", "students", "laboratories",
                "classrooms", "examinations", "transportation", "hostels", "placements",
                "events", "infrastructure", "administration",
            },
        )
        self.assertEqual(len(RESOURCES), 41)
        self.assertEqual(len({item["id"] for item in RESOURCES}), 41)
        self.assertEqual(len(OPERATIONS), 12)
        self.assertEqual(len({item["id"] for item in OPERATIONS}), 12)
        self.assertEqual(len(DEPARTMENTS), 12)


if __name__ == "__main__":
    unittest.main()
