import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.routes.api as api_routes
from app import create_app
from app.services import store
from api_test_support import authenticated_client, isolate_test_user


class ApiTests(unittest.TestCase):
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

    def assert_error(self, response, status_code):
        self.assertEqual(response.status_code, status_code)
        self.assertTrue(response.is_json)
        payload = response.get_json()
        self.assertEqual(payload.get("status"), "error")
        self.assertIsInstance(payload.get("message"), str)
        self.assertIsInstance(payload.get("code"), str)
        return payload

    def valid_incident_payload(self):
        return {
            "departmentId": "infrastructure",
            "failureType": "power_failure",
            "severity": "critical",
            "description": "API validation test",
        }

    def test_missing_malformed_and_non_object_json_return_400(self):
        responses = (
            self.client.post("/api/incidents", content_type="application/json"),
            self.client.post("/api/incidents", data='{"departmentId":', content_type="application/json"),
            self.client.post("/api/incidents", json=["not", "an", "object"]),
            self.client.post("/api/simulations", data="{broken", content_type="application/json"),
            self.client.post("/api/recovery/incident-valid/apply", json=[]),
        )
        for response in responses:
            with self.subTest(payload=response.get_json(silent=True)):
                self.assert_error(response, 400)

    def test_incident_fields_and_supported_values_are_validated(self):
        invalid_payloads = (
            {},
            {**self.valid_incident_payload(), "departmentId": 12},
            {**self.valid_incident_payload(), "departmentId": "unknown-department"},
            {**self.valid_incident_payload(), "failureType": "unknown_failure"},
            {**self.valid_incident_payload(), "failureType": None},
            {**self.valid_incident_payload(), "severity": "urgent"},
            {**self.valid_incident_payload(), "severity": 3},
            {**self.valid_incident_payload(), "description": "   "},
            {**self.valid_incident_payload(), "description": "x" * 501},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.assert_error(self.client.post("/api/incidents", json=payload), 400)

    def test_invalid_and_missing_incident_ids_return_400_and_404(self):
        self.assert_error(self.client.get("/api/simulations/not-an-id"), 400)
        self.assert_error(self.client.get("/api/simulations/incident-does-not-exist"), 404)
        self.assert_error(self.client.patch("/api/incidents/incident-does-not-exist/resolve"), 404)
        self.assert_error(self.client.delete("/api/incidents/incident-does-not-exist"), 404)
        self.assert_error(
            self.client.post("/api/simulations", json={"incidentId": "not-an-id"}),
            400,
        )

    def test_plan_ids_and_missing_resources_return_expected_statuses(self):
        self.assert_error(
            self.client.post(
                "/api/recovery/incident-does-not-exist/apply",
                json={"planId": "wrong-prefix"},
            ),
            400,
        )
        self.assert_error(
            self.client.post(
                "/api/recovery/incident-does-not-exist/apply",
                json={"planId": "recovery-not-found"},
            ),
            404,
        )
        self.assert_error(self.client.post("/api/recovery/not-an-id/plans"), 400)
        self.assert_error(
            self.client.get("/api/recovery/incident-does-not-exist"),
            404,
        )

    def test_storage_and_unexpected_errors_are_json_and_hide_internal_details(self):
        private_path = str(self.data_file)
        self.data_file.write_text("{broken", encoding="utf-8")
        response = self.client.get("/api/incidents")
        self.assert_error(response, 500)
        self.assertEqual(response.get_json()["code"], "STORE_DATA_INVALID")
        self.assertNotIn(private_path, response.get_data(as_text=True))
        self.assertEqual(self.data_file.read_text(encoding="utf-8"), "{broken")

        with patch.object(api_routes, "load_state", side_effect=RuntimeError(private_path)):
            response = self.client.get("/api/incidents")
        self.assert_error(response, 500)
        self.assertEqual(response.get_json()["code"], "INTERNAL_ERROR")
        self.assertNotIn(private_path, response.get_data(as_text=True))

    def test_unknown_routes_return_json_and_cors_remains_enabled(self):
        self.assert_error(self.client.get("/api/unknown"), 404)
        response = self.client.get(
            "/api/health",
            headers={"Origin": "http://127.0.0.1:5500"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "http://127.0.0.1:5500",
        )

    def test_successful_api_response_shapes_remain_unchanged(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.get_json(), {"status": "success", "service": "OMNIS-X API"})
        departments = self.client.get("/api/departments")
        self.assertEqual(len(departments.get_json()), 12)
        self.assertEqual(self.client.get("/api/departments/stats").get_json()["total"], 12)
        self.assertEqual(self.client.get("/api/incidents").get_json(), [])
        overview = self.client.get("/api/analytics/overview")
        self.assertEqual(overview.status_code, 200)
        self.assertTrue(
            {"totalIncidents", "activeIncidents", "resolvedIncidents", "severityDistribution", "institutionalHealth"}
            <= overview.get_json().keys()
        )
        department_analytics = self.client.get("/api/analytics/departments")
        self.assertEqual(department_analytics.status_code, 200)
        self.assertTrue({"statusSummary", "departments"} <= department_analytics.get_json().keys())
        recovery_analytics = self.client.get("/api/analytics/recovery")
        self.assertEqual(recovery_analytics.status_code, 200)
        self.assertTrue(
            {"totalRecoveryPlansGenerated", "totalRecoveryPlansApplied", "operationsRestored", "resourcesAllocated"}
            <= recovery_analytics.get_json().keys()
        )
        self.assertEqual(self.client.get("/api/analytics/export").status_code, 200)

        created = self.client.post("/api/incidents", json=self.valid_incident_payload())
        self.assertEqual(created.status_code, 201)
        incident = created.get_json()
        self.assertTrue(incident["id"].startswith("incident-"))
        incident_id = incident["id"]
        self.assert_error(
            self.client.get(f"/api/simulations/{incident_id}"),
            409,
        )

        simulated = self.client.post("/api/simulations", json={"incidentId": incident_id})
        self.assertEqual(simulated.status_code, 200)
        self.assertIn("propagationOrder", simulated.get_json())
        self.assertEqual(self.client.get(f"/api/simulations/{incident_id}").status_code, 200)

        recovery = self.client.get(f"/api/recovery/{incident_id}")
        self.assertEqual(recovery.status_code, 200)
        self.assertTrue(
            {"recoveryState", "recoveryPlans", "rejectedOptions", "unresolvedOperations", "unmodeledAffectedDepartmentIds", "recoveryActivity"}
            <= recovery.get_json().keys()
        )
        generated = self.client.post(f"/api/recovery/{incident_id}/plans")
        self.assertEqual(generated.status_code, 200)
        result = generated.get_json()["recoveryResult"]
        self.assertTrue({"plans", "rejectedOptions", "unresolvedOperations"} <= result.keys())
        if result["plans"]:
            applied = self.client.post(
                f"/api/recovery/{incident_id}/apply",
                json={"planId": result["plans"][0]["id"]},
            )
            self.assertEqual(applied.status_code, 200)
            self.assertTrue(applied.get_json()["ok"])
            self.assert_error(
                self.client.post(
                    f"/api/recovery/{incident_id}/apply",
                    json={"planId": result["plans"][0]["id"]},
                ),
                409,
            )

        self.assertEqual(
            self.client.patch(f"/api/incidents/{incident_id}/resolve").status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(f"/api/incidents/{incident_id}").status_code,
            200,
        )

    def test_failed_recovery_apply_does_not_change_persisted_state(self):
        created = self.client.post("/api/incidents", json=self.valid_incident_payload())
        incident_id = created.get_json()["id"]
        self.assertEqual(
            self.client.post("/api/simulations", json={"incidentId": incident_id}).status_code,
            200,
        )
        generated = self.client.post(f"/api/recovery/{incident_id}/plans")
        self.assertEqual(generated.status_code, 200)
        selected_plan = next(
            plan
            for plan in generated.get_json()["recoveryResult"]["plans"]
            if "resource-backup-electricity" in plan["resourceIds"]
        )

        state = store.load_state()
        saved_incident = next(item for item in state["incidents"] if item["id"] == incident_id)
        saved_plan = next(item for item in saved_incident["recoveryPlans"] if item["id"] == selected_plan["id"])
        saved_plan["resourceAllocations"][0]["quantity"] = 2
        store.save_state(state)
        before = copy.deepcopy(store.load_state())

        response = self.client.post(
            f"/api/recovery/{incident_id}/apply",
            json={"planId": selected_plan["id"]},
        )

        payload = self.assert_error(response, 409)
        self.assertEqual(payload["code"], "CONSTRAINTS_NOT_SATISFIED")
        self.assertEqual(store.load_state(), before)

    def test_large_combined_recovery_plan_can_be_applied_through_api(self):
        payload = self.valid_incident_payload()
        payload.update(
            {
                "departmentId": "infrastructure",
                "failureType": "internet_failure",
                "severity": "critical",
            }
        )
        created = self.client.post("/api/incidents", json=payload)
        incident_id = created.get_json()["id"]
        self.assertEqual(
            self.client.post("/api/simulations", json={"incidentId": incident_id}).status_code,
            200,
        )
        generated = self.client.post(f"/api/recovery/{incident_id}/plans")
        self.assertEqual(generated.status_code, 200)
        combined = next(
            plan
            for plan in generated.get_json()["recoveryResult"]["plans"]
            if plan["restoredOperationCount"] > 1
        )
        self.assertGreater(len(combined["id"]), 512)

        applied = self.client.post(
            f"/api/recovery/{incident_id}/apply",
            json={"planId": combined["id"]},
        )

        self.assertEqual(applied.status_code, 200, applied.get_json())
        self.assertEqual(
            len(applied.get_json()["recoveryState"]["restoredOperationIds"]),
            combined["restoredOperationCount"],
        )

    def test_repeated_plan_generation_and_application_analytics_are_event_based(self):
        created = self.client.post("/api/incidents", json=self.valid_incident_payload())
        incident_id = created.get_json()["id"]
        self.assertEqual(
            self.client.post("/api/simulations", json={"incidentId": incident_id}).status_code,
            200,
        )

        first = self.client.post(f"/api/recovery/{incident_id}/plans")
        second = self.client.post(f"/api/recovery/{incident_id}/plans")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        plan_ids = {plan["id"] for plan in second.get_json()["recoveryResult"]["plans"]}
        generation_events = [
            event for event in store.load_state()["recoveryActivity"]
            if event.get("incidentId") == incident_id and event.get("type") == "plans_generated"
        ]
        self.assertEqual(len(generation_events), 2)
        before_apply = self.client.get("/api/analytics/recovery").get_json()
        self.assertEqual(before_apply["totalRecoveryPlansGenerated"], len(plan_ids))
        self.assertEqual(before_apply["totalRecoveryPlansApplied"], 0)

        plan_id = next(iter(plan_ids))
        applied = self.client.post(
            f"/api/recovery/{incident_id}/apply",
            json={"planId": plan_id},
        )
        self.assertEqual(applied.status_code, 200)
        after_apply = self.client.get("/api/analytics/recovery").get_json()
        self.assertEqual(after_apply["totalRecoveryPlansApplied"], 1)

        repeated_apply = self.client.post(
            f"/api/recovery/{incident_id}/apply",
            json={"planId": plan_id},
        )
        self.assertEqual(repeated_apply.status_code, 409)
        self.assertEqual(
            self.client.get("/api/analytics/recovery").get_json()["totalRecoveryPlansApplied"],
            1,
        )

    def test_recovery_get_is_read_only_until_explicit_plan_generation(self):
        created = self.client.post("/api/incidents", json=self.valid_incident_payload())
        incident_id = created.get_json()["id"]
        self.assertEqual(
            self.client.post("/api/simulations", json={"incidentId": incident_id}).status_code,
            200,
        )
        data_before_get = self.data_file.read_bytes()
        analytics_before_get = self.client.get("/api/analytics/recovery").get_json()

        first = self.client.get(f"/api/recovery/{incident_id}")
        second = self.client.get(f"/api/recovery/{incident_id}")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        expected_keys = {
            "recoveryState",
            "recoveryPlans",
            "rejectedOptions",
            "unresolvedOperations",
            "unmodeledAffectedDepartmentIds",
            "recoveryActivity",
        }
        self.assertTrue(expected_keys <= first.get_json().keys())
        self.assertEqual(first.get_json()["recoveryPlans"], [])
        self.assertEqual(second.get_json()["recoveryPlans"], [])
        self.assertEqual(self.data_file.read_bytes(), data_before_get)
        self.assertEqual(
            self.client.get("/api/analytics/recovery").get_json(),
            analytics_before_get,
        )

        generated = self.client.post(f"/api/recovery/{incident_id}/plans")
        self.assertEqual(generated.status_code, 200)
        generated_plans = generated.get_json()["recoveryResult"]["plans"]
        self.assertTrue(generated_plans)
        saved = self.client.get(f"/api/recovery/{incident_id}")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["recoveryPlans"], generated_plans)
        persisted_after_post = self.data_file.read_bytes()
        self.assertEqual(self.client.get(f"/api/recovery/{incident_id}").status_code, 200)
        self.assertEqual(self.data_file.read_bytes(), persisted_after_post)

    def test_recovery_get_missing_incident_still_returns_json_404(self):
        response = self.client.get("/api/recovery/incident-does-not-exist")
        payload = self.assert_error(response, 404)
        self.assertEqual(payload["code"], "INCIDENT_NOT_FOUND")

    def test_all_get_endpoints_leave_persisted_json_unchanged(self):
        created = self.client.post("/api/incidents", json=self.valid_incident_payload())
        incident_id = created.get_json()["id"]
        self.assertEqual(
            self.client.post("/api/simulations", json={"incidentId": incident_id}).status_code,
            200,
        )
        self.assertEqual(self.client.post(f"/api/recovery/{incident_id}/plans").status_code, 200)
        before = self.data_file.read_bytes()
        recovery_metrics_before = self.client.get("/api/analytics/recovery").get_json()

        paths = (
            "/api/health",
            "/api/departments",
            "/api/departments/stats",
            "/api/incidents",
            f"/api/simulations/{incident_id}",
            f"/api/recovery/{incident_id}",
            "/api/analytics/overview",
            "/api/analytics/departments",
            "/api/analytics/recovery",
            "/api/analytics/export",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.is_json)
                self.assertEqual(self.data_file.read_bytes(), before)

        self.assertEqual(
            self.client.get("/api/analytics/recovery").get_json(),
            recovery_metrics_before,
        )


if __name__ == "__main__":
    unittest.main()