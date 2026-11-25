from collections import Counter, defaultdict

from ..models.catalog import DEPARTMENTS, OPERATIONS


STATUS_SCORES = {"operational": 100, "warning": 60, "failed": 0}


def _list_value(value):
    return value if isinstance(value, list) else []


def incident_simulation(incident):
    result = incident.get("simulationResult") or {}
    return result if isinstance(result, dict) else {}


def affected_operations(incident, operations=None):
    operations = OPERATIONS if operations is None else operations
    result = incident_simulation(incident)
    affected_departments = set(_list_value(result.get("affectedDepartmentIds")))
    initial_failure = result.get("initialFailure") or {}
    service = initial_failure.get("service")
    initial_department_id = initial_failure.get("departmentId")
    return [
        operation
        for operation in operations
        if operation.get("departmentId") in affected_departments
        and (
            not service
            or operation.get("departmentId") != initial_department_id
            or operation.get("service") == service
        )
    ]


def incident_restoration(incident, operations=None):
    scoped_operations = affected_operations(incident, operations)
    recovery_state = incident.get("recoveryState")
    if not isinstance(recovery_state, dict):
        recovery_state = {}
    restored_ids = set(_list_value(recovery_state.get("restoredOperationIds")))
    relevant_ids = {operation["id"] for operation in scoped_operations}
    restored_relevant_ids = relevant_ids & restored_ids
    restored_by_department = defaultdict(set)
    relevant_by_department = defaultdict(set)
    for operation in scoped_operations:
        department_id = operation["departmentId"]
        relevant_by_department[department_id].add(operation["id"])
        if operation["id"] in restored_relevant_ids:
            restored_by_department[department_id].add(operation["id"])

    affected_department_ids = set(
        _list_value(incident_simulation(incident).get("affectedDepartmentIds"))
    )
    status_by_department = {}
    for department_id in affected_department_ids:
        relevant = relevant_by_department.get(department_id, set())
        restored = restored_by_department.get(department_id, set())
        if relevant and len(restored) == len(relevant):
            status_by_department[department_id] = "fully_restored"
        elif restored:
            status_by_department[department_id] = "partially_restored"
        else:
            status_by_department[department_id] = "unrestored"

    if not incident_simulation(incident):
        incident_status = "unrestored"
    elif relevant_ids and restored_relevant_ids == relevant_ids:
        incident_status = "fully_restored"
    elif restored_relevant_ids:
        incident_status = "partially_restored"
    else:
        incident_status = "unrestored"

    return {
        "incidentStatus": incident_status,
        "relevantOperationIds": relevant_ids,
        "restoredOperationIds": restored_relevant_ids,
        "departments": status_by_department,
    }


def active_department_statuses(state):
    """Apply the frontend rule: affected is failed; fully restored failed baselines become operational."""
    active_incidents = [incident for incident in _list_value(state.get("incidents")) if incident.get("status") == "active"]
    affected_ids = set()
    restored_ids = set()
    partial_ids = set()
    incident_restoration_counts = Counter()

    for incident in active_incidents:
        progress = incident_restoration(incident)
        incident_restoration_counts[progress["incidentStatus"]] += 1
        for department_id, restoration_status in progress["departments"].items():
            if restoration_status == "fully_restored":
                restored_ids.add(department_id)
            else:
                affected_ids.add(department_id)
                if restoration_status == "partially_restored":
                    partial_ids.add(department_id)

    displayed_statuses = {}
    for department in DEPARTMENTS:
        department_id = department["id"]
        if department_id in affected_ids:
            displayed_statuses[department_id] = "failed"
        elif department_id in restored_ids and department["status"] == "failed":
            displayed_statuses[department_id] = "operational"
        else:
            displayed_statuses[department_id] = department["status"]

    return {
        "displayedStatuses": displayed_statuses,
        "affectedDepartmentIds": affected_ids,
        "restoredDepartmentIds": restored_ids - affected_ids,
        "partiallyRestoredDepartmentIds": partial_ids,
        "incidentRestorationCounts": incident_restoration_counts,
    }


def analytics_overview(state):
    incidents = _list_value(state.get("incidents"))
    active = [item for item in incidents if item.get("status") == "active"]
    severity = Counter(item.get("severity", "low") for item in incidents)
    direct = indirect = 0
    for incident in incidents:
        result = incident_simulation(incident)
        direct += len(set(_list_value(result.get("directlyAffectedDepartmentIds"))))
        indirect += len(set(_list_value(result.get("indirectlyAffectedDepartmentIds"))))

    status = active_department_statuses(state)
    displayed_counts = Counter(status["displayedStatuses"].values())
    # Match the dashboard: score each displayed department status, then average the 12 departments.
    health_score = round(
        sum(STATUS_SCORES.get(value, 100) for value in status["displayedStatuses"].values())
        / max(1, len(DEPARTMENTS)),
        1,
    )
    restoration_counts = status["incidentRestorationCounts"]
    all_restoration_counts = Counter(
        incident_restoration(incident)["incidentStatus"] for incident in incidents
    )
    resolved_but_incomplete = sum(
        incident.get("status") == "resolved"
        and incident_restoration(incident)["incidentStatus"] != "fully_restored"
        for incident in incidents
    )
    return {
        "totalIncidents": len(incidents),
        "activeIncidents": len(active),
        "resolvedIncidents": sum(item.get("status") == "resolved" for item in incidents),
        "directImpactCount": direct,
        "indirectImpactCount": indirect,
        "severityDistribution": {
            key: severity.get(key, 0) for key in ("low", "medium", "high", "critical")
        },
        "incidentRestoration": {
            "fullyRestored": all_restoration_counts.get("fully_restored", 0),
            "partiallyRestored": all_restoration_counts.get("partially_restored", 0),
            "unrestored": all_restoration_counts.get("unrestored", 0),
            "resolvedButNotFullyRestored": resolved_but_incomplete,
        },
        "institutionalHealth": {
            "healthScore": health_score,
            "currentlyAffectedCount": len(status["affectedDepartmentIds"]),
            "currentlyRestoredCount": len(status["restoredDepartmentIds"]),
            "departmentStatusCounts": {
                key: displayed_counts.get(key, 0)
                for key in ("operational", "warning", "failed")
            },
            "currentlyPartiallyRestoredCount": len(status["partiallyRestoredDepartmentIds"]),
            "fullyRestoredIncidents": restoration_counts.get("fully_restored", 0),
            "partiallyRestoredIncidents": restoration_counts.get("partially_restored", 0),
            "unrestoredIncidents": restoration_counts.get("unrestored", 0),
        },
    }


def analytics_departments(state):
    incident_counts = defaultdict(Counter)
    for incident in _list_value(state.get("incidents")):
        if incident.get("status") != "active":
            continue
        for department_id, restoration_status in incident_restoration(incident)["departments"].items():
            incident_counts[department_id][restoration_status] += 1

    rows = []
    for department in DEPARTMENTS:
        counts = incident_counts[department["id"]]
        if counts["unrestored"]:
            restoration_status = "unrestored"
        elif counts["partially_restored"]:
            restoration_status = "partially_restored"
        elif counts["fully_restored"]:
            restoration_status = "fully_restored"
        else:
            restoration_status = "not_affected"
        rows.append(
            {
                "departmentId": department["id"],
                "departmentName": department["name"],
                "status": department["status"],
                "affectedCount": counts["unrestored"] + counts["partially_restored"],
                "restorationStatus": restoration_status,
                "fullyRestoredCount": counts["fully_restored"],
                "partiallyRestoredCount": counts["partially_restored"],
                "unrestoredCount": counts["unrestored"],
            }
        )
    baseline_counts = Counter(department["status"] for department in DEPARTMENTS)
    return {
        "statusSummary": {
            key: baseline_counts.get(key, 0) for key in ("operational", "warning", "failed")
        },
        "departments": rows,
    }


def _event_plan_ids(event):
    plan_ids = event.get("planIds")
    if isinstance(plan_ids, list):
        return [plan_id for plan_id in plan_ids if isinstance(plan_id, str) and plan_id]
    return []


def _plan_event_counts(state):
    generated_plan_keys = set()
    generated_legacy_events = set()
    applied_plan_keys = set()
    for event in _list_value(state.get("recoveryActivity")):
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        incident_id = event.get("incidentId")
        if event_type in ("plans_generated", "plans-generated"):
            plan_ids = _event_plan_ids(event)
            if plan_ids:
                generated_plan_keys.update((incident_id, plan_id) for plan_id in plan_ids)
            else:
                count = event.get("planCount")
                if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                    event_key = event.get("id") or (incident_id, event.get("timestamp"), count)
                    generated_legacy_events.add((event_key, count))
        elif event_type in ("plan_applied", "plan-applied"):
            plan_id = event.get("planId")
            if isinstance(plan_id, str) and plan_id:
                applied_plan_keys.add((incident_id, plan_id))

    for incident in _list_value(state.get("incidents")):
        incident_id = incident.get("id")
        recovery_state = incident.get("recoveryState") or {}
        for plan_id in _list_value(recovery_state.get("appliedPlanIds")):
            if isinstance(plan_id, str) and plan_id:
                applied_plan_keys.add((incident_id, plan_id))

    generated_from_events = len(generated_plan_keys) + sum(count for _, count in generated_legacy_events)
    return generated_from_events, len(applied_plan_keys)


def analytics_recovery(state):
    incidents = state.get("incidents", [])
    generated, applied = _plan_event_counts(state)
    restored_operation_keys = set()
    allocated_resource_ids = set()
    for incident in incidents:
        incident_id = incident.get("id")
        recovery_state = incident.get("recoveryState")
        if not isinstance(recovery_state, dict):
            recovery_state = {}
        restored_operation_keys.update(
            (incident_id, operation_id)
            for operation_id in _list_value(recovery_state.get("restoredOperationIds"))
            if isinstance(operation_id, str)
        )
        for allocation in _list_value(recovery_state.get("allocations")):
            if isinstance(allocation, dict) and isinstance(allocation.get("resourceId"), str):
                allocated_resource_ids.add(allocation["resourceId"])
    return {
        "totalRecoveryPlansGenerated": generated,
        "totalRecoveryPlansApplied": applied,
        "operationsRestored": len(restored_operation_keys),
        "resourcesAllocated": len(allocated_resource_ids),
        "recoveryActivity": _list_value(state.get("recoveryActivity")),
    }