import copy
import unittest
from unittest.mock import patch

from app.models.catalog import DEPARTMENTS, OPERATIONS, RESOURCES
from app.services import recovery_engine
from app.services.engine import apply_plan, build_recovery, simulate


class RecoveryTests(unittest.TestCase):
    def test_backend_catalog_contains_original_resource_and_operation_records(self):
        resource_ids = [resource["id"] for resource in RESOURCES]
        operation_ids = [operation["id"] for operation in OPERATIONS]
        expected_resource_ids = {
            "resource-faculty", "resource-student-advisors", "resource-lab-benches", "resource-lab-computers",
            "resource-classrooms", "resource-exam-seats", "resource-buses", "resource-drivers",
            "resource-hostel-beds", "resource-placement-rooms", "resource-event-venues",
            "resource-maintenance-crews", "resource-generators", "resource-admin-staff",
            "resource-lab-a", "resource-lab-b", "resource-lab-c", "resource-research-room",
            "resource-classroom-main", "resource-classroom-backup", "resource-exam-hall-main",
            "resource-exam-hall-b", "resource-exam-hall-c", "resource-faculty-primary-slot",
            "resource-substitute-faculty", "resource-campus-grid", "resource-backup-electricity",
            "resource-campus-internet", "resource-backup-internet-a", "resource-backup-internet-b",
            "resource-backup-internet-c", "resource-transport-route-primary", "resource-transport-van",
            "resource-transport-minibus", "resource-hostel-wing-primary", "resource-hostel-annex",
            "resource-academic-system-primary", "resource-academic-system-backup", "resource-event-hall-primary",
            "resource-event-hall-b", "resource-event-hall-c",
        }
        expected_operation_ids = {
            "operation-laboratory-a-practical", "operation-classroom-lecture", "operation-examination-session",
            "operation-faculty-teaching", "operation-campus-electricity", "operation-campus-internet",
            "operation-placement-interview", "operation-admin-systems", "operation-transport-route",
            "operation-hostel-residency", "operation-academic-scheduling", "operation-campus-event",
        }

        self.assertEqual(len(resource_ids), 41)
        self.assertEqual(len(set(resource_ids)), 41)
        self.assertEqual(set(resource_ids), expected_resource_ids)
        self.assertEqual(len(operation_ids), 12)
        self.assertEqual(len(set(operation_ids)), 12)
        self.assertEqual(set(operation_ids), expected_operation_ids)
        self.assertEqual(
            next(resource for resource in RESOURCES if resource["id"] == "resource-lab-b")["capacity"],
            20,
        )
        self.assertEqual(
            next(resource for resource in RESOURCES if resource["id"] == "resource-lab-c")["capacity"],
            20,
        )
        self.assertEqual(
            next(resource for resource in RESOURCES if resource["id"] == "resource-lab-a")["status"],
            "unavailable",
        )

    def test_bfs_cycle_terminates_without_duplicate_departments(self):
        simulation = simulate("administration")
        affected = simulation["affectedDepartmentIds"]
        propagated = [step["departmentId"] for step in simulation["propagationOrder"]]

        self.assertTrue(simulation["ok"])
        self.assertEqual(len(affected), len(set(affected)))
        self.assertEqual(len(propagated), len(set(propagated)))
        self.assertEqual(affected.count("administration"), 1)
        self.assertEqual(
            set(affected),
            {department["id"] for department in DEPARTMENTS} - {"students"},
        )

    def test_laboratory_capacity_bundle_and_incompatible_rejections(self):
        simulation = simulate("laboratories")
        result = build_recovery(simulation)
        operation_id = "operation-laboratory-a-practical"
        plan = next(plan for plan in result["plans"] if plan["operationIds"] == [operation_id])

        self.assertTrue(plan["feasible"])
        self.assertEqual(
            {item["resourceId"] for item in plan["resourceAllocations"]},
            {"resource-lab-b", "resource-lab-c"},
        )
        self.assertEqual(sum(item["quantity"] for item in plan["resourceAllocations"]), 40)
        self.assertEqual(plan["capacityComparisons"][0]["provided"], 40)
        rejected = {item["resourceId"]: item for item in result["rejectedOptions"]}
        self.assertFalse(rejected["resource-research-room"]["feasible"])
        self.assertTrue(any("not compatible" in reason for reason in rejected["resource-research-room"]["reasons"]))
        self.assertTrue(any("failed primary resource" in reason for reason in rejected["resource-lab-a"]["reasons"]))
        self.assertTrue(any("Insufficient capacity on its own" in reason for reason in rejected["resource-lab-b"]["reasons"]))

    def test_exam_transport_and_event_operations_use_multiple_resources(self):
        scenarios = (
            ("examinations", "operation-examination-session", 120),
            ("transportation", "operation-transport-route", 30),
            ("events", "operation-campus-event", 100),
        )
        for department_id, operation_id, demand in scenarios:
            with self.subTest(operation=operation_id):
                result = build_recovery(simulate(department_id))
                plan = next(item for item in result["plans"] if item["operationIds"] == [operation_id])
                self.assertGreater(len(plan["resourceAllocations"]), 1)
                self.assertEqual(sum(item["quantity"] for item in plan["resourceAllocations"]), demand)
                self.assertTrue(all(item["quantity"] <= item["capacity"] for item in plan["resourceAllocations"]))

    def test_all_alternatives_and_combined_plan_are_ranked_explainably(self):
        simulation = simulate("infrastructure", "Internet")
        result = build_recovery(simulation)
        placement_plans = [
            plan for plan in result["plans"]
            if plan["operationIds"] == ["operation-placement-interview"]
        ]
        self.assertGreaterEqual(len(placement_plans), 3)
        self.assertEqual([plan["rank"] for plan in result["plans"]], list(range(1, len(result["plans"]) + 1)))
        self.assertTrue(all(plan["feasible"] is True for plan in result["plans"]))
        self.assertTrue(all("estimated recovery" in plan["rankingReason"] for plan in result["plans"]))
        self.assertTrue(all("uses" in plan["rankingReason"] for plan in result["plans"]))

        combined = next(plan for plan in result["plans"] if plan["restoredOperationCount"] > 1)
        self.assertGreater(combined["restoredOperationCount"], 1)
        self.assertEqual(combined["restoredOperationCount"], len(combined["operationIds"]))
        self.assertEqual(len(combined["resourceIds"]), len(set(combined["resourceIds"])))

    def test_shared_allocations_remove_reserved_capacity_from_new_plans(self):
        allocations = [
            {
                "resourceId": "resource-lab-b",
                "operationId": "operation-laboratory-a-practical",
                "requirementId": "lab-seats",
                "quantity": 20,
                "unit": "seats",
                "incidentId": "incident-other-lab",
            }
        ]
        result = build_recovery(simulate("laboratories"), allocations)

        self.assertFalse(any(plan["operationIds"] == ["operation-laboratory-a-practical"] for plan in result["plans"]))
        unresolved = next(item for item in result["unresolvedOperations"] if item["operationId"] == "operation-laboratory-a-practical")
        self.assertTrue(any("compatible available capacity" in reason for reason in unresolved["reasons"]))
        reserved = next(item for item in result["rejectedOptions"] if item["resourceId"] == "resource-lab-b")
        self.assertTrue(any("already allocated" in reason for reason in reserved["reasons"]))

    def test_application_rejects_tampered_capacity_and_compatibility_without_mutation(self):
        simulation = simulate("laboratories")
        plan = next(
            item for item in build_recovery(simulation)["plans"]
            if item["operationIds"] == ["operation-laboratory-a-practical"]
        )
        initial_state = {"allocations": [], "restoredOperationIds": [], "appliedPlanIds": []}

        excessive = copy.deepcopy(plan)
        excessive["resourceAllocations"][0]["quantity"] += 1
        before = copy.deepcopy(initial_state)
        failed = apply_plan(excessive, initial_state, [], "incident-lab", simulation)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"]["code"], "CONSTRAINTS_NOT_SATISFIED")
        self.assertEqual(initial_state, before)

        incompatible = copy.deepcopy(plan)
        assignment = incompatible["resourceAllocations"][0]
        assignment.update(
            {
                "resourceId": "resource-research-room",
                "resourceName": "Research-only room",
                "capacity": 48,
                "availableAtGeneration": 48,
            }
        )
        incompatible["resourceIds"] = [item["resourceId"] for item in incompatible["resourceAllocations"]]
        incompatible["resourceNames"] = [item["resourceName"] for item in incompatible["resourceAllocations"]]
        failed = apply_plan(incompatible, initial_state, [], "incident-lab", simulation)
        self.assertFalse(failed["ok"])
        self.assertIn("not compatible", failed["error"]["message"])
        self.assertEqual(initial_state, before)

        invalid = copy.deepcopy(plan)
        invalid["feasible"] = False
        failed = apply_plan(invalid, initial_state, [], "incident-lab", simulation)
        self.assertEqual(failed["error"]["code"], "INVALID_PLAN")

    def test_shared_resource_conflict_across_incidents_is_rejected(self):
        simulation = simulate("infrastructure", "Internet")
        result = build_recovery(simulation)
        admin_plan = next(
            plan for plan in result["plans"]
            if plan["operationIds"] == ["operation-admin-systems"]
            and plan["resourceIds"] == ["resource-backup-internet-a"]
        )
        placement_plan = next(
            plan for plan in result["plans"]
            if plan["operationIds"] == ["operation-placement-interview"]
            and plan["resourceIds"] == ["resource-backup-internet-a"]
        )
        empty_state = {"allocations": [], "restoredOperationIds": [], "appliedPlanIds": []}
        first = apply_plan(admin_plan, empty_state, [], "incident-one", simulation)
        self.assertTrue(first["ok"])
        second = apply_plan(
            placement_plan,
            empty_state,
            first["recoveryState"]["allocations"],
            "incident-two",
            simulation,
        )
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"]["code"], "RESOURCE_ALREADY_ALLOCATED")
        self.assertEqual(empty_state, {"allocations": [], "restoredOperationIds": [], "appliedPlanIds": []})

    def test_apply_rechecks_resource_status_and_incident_scope(self):
        simulation = simulate("infrastructure", "Electricity")
        plan = next(item for item in build_recovery(simulation)["plans"] if item["operationIds"] == ["operation-campus-electricity"])
        changed_resources = copy.deepcopy(RESOURCES)
        backup = next(item for item in changed_resources if item["id"] == "resource-backup-electricity")
        backup["available"] = 0
        backup["status"] = "unavailable"
        state = {"allocations": [], "restoredOperationIds": [], "appliedPlanIds": []}

        with patch.object(recovery_engine, "RESOURCES", changed_resources):
            stale = apply_plan(plan, state, [], "incident-electricity", simulation)
        self.assertFalse(stale["ok"])
        self.assertEqual(state["allocations"], [])

        out_of_scope = apply_plan(plan, state, [], "incident-electricity", simulate("transportation"))
        self.assertFalse(out_of_scope["ok"])
        self.assertEqual(out_of_scope["error"]["code"], "INVALID_PLAN")


if __name__ == "__main__":
    unittest.main()