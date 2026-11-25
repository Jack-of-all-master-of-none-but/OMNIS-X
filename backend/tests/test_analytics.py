import unittest
from unittest.mock import patch

from app.models.catalog import DEPARTMENTS, OPERATIONS
from app.services import analytics
from app.services.analytics import (
    active_department_statuses,
    analytics_departments,
    analytics_overview,
    analytics_recovery,
    incident_restoration,
)


def incident_record(
    incident_id="incident-test",
    *,
    status="active",
    affected=None,
    department_id="infrastructure",
    service="Electricity",
    restored=None,
    plans=None,
    recovery_activity=None,
):
    return {
        "id": incident_id,
        "status": status,
        "departmentId": department_id,
        "severity": "high",
        "simulationResult": {
            "ok": True,
            "initialFailure": {"departmentId": department_id, "service": service},
            "affectedDepartmentIds": affected or ["infrastructure", "laboratories"],
            "directlyAffectedDepartmentIds": ["laboratories"],
            "indirectlyAffectedDepartmentIds": [],
        },
        "recoveryState": {
            "allocations": [],
            "restoredOperationIds": restored or [],
            "appliedPlanIds": [],
        },
        "recoveryPlans": plans or [],
    }


class AnalyticsTests(unittest.TestCase):
    def test_institutional_health_uses_frontend_status_score_average(self):
        base = analytics_overview({"incidents": [], "recoveryActivity": []})
        self.assertEqual(base["institutionalHealth"]["healthScore"], 81.7)

        failed = incident_record()
        failed_health = analytics_overview({"incidents": [failed], "recoveryActivity": []})
        self.assertEqual(failed_health["institutionalHealth"]["healthScore"], 76.7)
        self.assertEqual(failed_health["institutionalHealth"]["currentlyAffectedCount"], 2)

        fully_restored = incident_record(
            restored=["operation-campus-electricity", "operation-laboratory-a-practical"]
        )
        restored_health = analytics_overview(
            {"incidents": [fully_restored], "recoveryActivity": []}
        )
        self.assertEqual(restored_health["institutionalHealth"]["healthScore"], 90.0)
        self.assertEqual(restored_health["institutionalHealth"]["currentlyAffectedCount"], 0)
        self.assertEqual(restored_health["institutionalHealth"]["currentlyRestoredCount"], 2)

    def test_department_is_partial_until_every_relevant_operation_is_restored(self):
        operations = [
            {"id": "lab-operation-a", "departmentId": "laboratories", "service": None},
            {"id": "lab-operation-b", "departmentId": "laboratories", "service": None},
        ]
        incident = incident_record(
            department_id="laboratories",
            service=None,
            affected=["laboratories"],
            restored=["lab-operation-a"],
        )
        with patch.object(analytics, "OPERATIONS", operations):
            partial = incident_restoration(incident)
            department_metrics = analytics_departments({"incidents": [incident]})
            self.assertEqual(partial["departments"]["laboratories"], "partially_restored")
            lab_row = next(
                row for row in department_metrics["departments"]
                if row["departmentId"] == "laboratories"
            )
            self.assertEqual(lab_row["restorationStatus"], "partially_restored")
            self.assertEqual(lab_row["partiallyRestoredCount"], 1)
            self.assertEqual(lab_row["affectedCount"], 1)

            incident["recoveryState"]["restoredOperationIds"] = ["lab-operation-a", "lab-operation-b"]
            complete = incident_restoration(incident)
            complete_metrics = analytics_departments({"incidents": [incident]})
        self.assertEqual(complete["departments"]["laboratories"], "fully_restored")
        lab_row = next(
            row for row in complete_metrics["departments"]
            if row["departmentId"] == "laboratories"
        )
        self.assertEqual(lab_row["restorationStatus"], "fully_restored")
        self.assertEqual(lab_row["affectedCount"], 0)

    def test_unrestored_and_zero_operation_impacts_are_not_marked_restored(self):
        no_modeled_operation = incident_record(
            department_id="students",
            service=None,
            affected=["students"],
        )
        progress = incident_restoration(no_modeled_operation)
        self.assertEqual(progress["departments"]["students"], "unrestored")
        self.assertEqual(progress["incidentStatus"], "unrestored")

    def test_resolved_incident_with_partial_work_is_reported_as_incomplete(self):
        operations = [
            {"id": "lab-operation-a", "departmentId": "laboratories", "service": None},
            {"id": "lab-operation-b", "departmentId": "laboratories", "service": None},
        ]
        resolved_partial = incident_record(
            status="resolved",
            department_id="laboratories",
            service=None,
            affected=["laboratories"],
            restored=["lab-operation-a"],
        )
        with patch.object(analytics, "OPERATIONS", operations):
            overview = analytics_overview(
                {"incidents": [resolved_partial], "recoveryActivity": []}
            )
        self.assertEqual(overview["resolvedIncidents"], 1)
        self.assertEqual(overview["incidentRestoration"]["partiallyRestored"], 1)
        self.assertEqual(overview["incidentRestoration"]["resolvedButNotFullyRestored"], 1)

    def test_legacy_records_with_missing_or_null_recovery_arrays_remain_readable(self):
        legacy = {
            "id": "incident-legacy",
            "status": "active",
            "simulationResult": {
                "affectedDepartmentIds": ["laboratories"],
                "directlyAffectedDepartmentIds": None,
                "indirectlyAffectedDepartmentIds": None,
            },
            "recoveryState": {"restoredOperationIds": None, "appliedPlanIds": None, "allocations": None},
            "recoveryPlans": [{"id": "old-plan"}],
        }
        state = {"incidents": [legacy], "recoveryActivity": None}
        self.assertEqual(analytics_overview(state)["directImpactCount"], 0)
        self.assertEqual(analytics_recovery(state)["totalRecoveryPlansGenerated"], 0)
        self.assertEqual(analytics_recovery(state)["operationsRestored"], 0)

    def test_one_incident_cannot_hide_another_incidents_remaining_impact(self):
        complete = incident_record(
            "incident-complete",
            restored=["operation-campus-electricity", "operation-laboratory-a-practical"],
        )
        outstanding = incident_record("incident-outstanding")
        statuses = active_department_statuses(
            {"incidents": [complete, outstanding], "recoveryActivity": []}
        )
        self.assertIn("infrastructure", statuses["affectedDepartmentIds"])
        self.assertNotIn("infrastructure", statuses["restoredDepartmentIds"])

    def test_recovery_plan_counts_use_generation_events_not_stored_plan_snapshots(self):
        old_snapshot = incident_record(plans=[{"id": "stale-plan-a"}, {"id": "stale-plan-b"}])
        state = {"incidents": [old_snapshot], "recoveryActivity": []}
        self.assertEqual(analytics_recovery(state)["totalRecoveryPlansGenerated"], 0)

        plan_ids = ["plan-one", "plan-two"]
        state["recoveryActivity"] = [
            {"id": "generation-one", "incidentId": old_snapshot["id"], "type": "plans_generated", "planCount": 2, "planIds": plan_ids},
            {"id": "generation-retry", "incidentId": old_snapshot["id"], "type": "plans_generated", "planCount": 2, "planIds": plan_ids},
        ]
        metrics = analytics_recovery(state)
        self.assertEqual(metrics["totalRecoveryPlansGenerated"], 2)
        self.assertEqual(len(metrics["recoveryActivity"]), 2)

    def test_legacy_applied_plan_state_and_new_application_events_are_deduplicated(self):
        incident = incident_record()
        incident["recoveryState"].update(
            {
                "appliedPlanIds": ["legacy-plan", "applied-plan"],
                "restoredOperationIds": ["operation-campus-electricity"],
                "allocations": [{"resourceId": "resource-backup-electricity"}],
            }
        )
        state = {
            "incidents": [incident],
            "recoveryActivity": [
                {"id": "apply-event", "incidentId": incident["id"], "type": "plan_applied", "planId": "applied-plan", "operationIds": ["operation-campus-electricity"]},
                {"id": "retry-event", "incidentId": incident["id"], "type": "plan_applied", "planId": "applied-plan", "operationIds": ["operation-campus-electricity"]},
            ],
        }
        metrics = analytics_recovery(state)
        self.assertEqual(metrics["totalRecoveryPlansApplied"], 2)
        self.assertEqual(metrics["operationsRestored"], 1)
        self.assertEqual(metrics["resourcesAllocated"], 1)

    def test_response_keys_remain_available_and_include_restoration_detail(self):
        state = {"incidents": [incident_record()], "recoveryActivity": []}
        overview = analytics_overview(state)
        self.assertTrue(
            {"totalIncidents", "activeIncidents", "resolvedIncidents", "directImpactCount", "indirectImpactCount", "severityDistribution", "institutionalHealth"}
            <= overview.keys()
        )
        self.assertTrue(
            {"healthScore", "currentlyAffectedCount", "currentlyRestoredCount", "departmentStatusCounts"}
            <= overview["institutionalHealth"].keys()
        )
        departments = analytics_departments(state)
        self.assertEqual(set(departments["statusSummary"]), {"operational", "warning", "failed"})
        self.assertTrue(
            {"departmentId", "departmentName", "status", "affectedCount", "restorationStatus"}
            <= departments["departments"][0].keys()
        )
        recovery = analytics_recovery(state)
        self.assertTrue(
            {"totalRecoveryPlansGenerated", "totalRecoveryPlansApplied", "operationsRestored", "resourcesAllocated", "recoveryActivity"}
            <= recovery.keys()
        )

    def test_department_count_stays_aligned_with_existing_catalog(self):
        self.assertEqual(len(DEPARTMENTS), 12)
        self.assertEqual(len(OPERATIONS), 12)


if __name__ == "__main__":
    unittest.main()