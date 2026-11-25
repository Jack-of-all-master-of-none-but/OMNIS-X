from collections import deque

from ..models.catalog import DEPARTMENTS, DEPENDENCIES
from .recovery_engine import apply_plan, build_recovery


def simulate(department_id, service=None):
    by_id = {item["id"]: item for item in DEPARTMENTS}
    if department_id not in by_id:
        return {"ok": False, "error": {"code": "UNKNOWN_DEPARTMENT", "message": "Unknown department."}}
    outgoing = {}
    for upstream, downstream, relationship, edge_service in DEPENDENCIES:
        outgoing.setdefault(upstream, []).append((downstream, relationship, edge_service))
    if service and not any(edge[2] == service for edge in outgoing.get(department_id, [])):
        return {"ok": False, "error": {"code": "UNKNOWN_SERVICE_FOR_DEPARTMENT", "message": f"Department does not provide the {service} service."}}
    visited = {department_id}
    queue = deque([(department_id, 0)])
    propagation = []
    while queue:
        current, depth = queue.popleft()
        edges = outgoing.get(current, [])
        if depth == 0 and service:
            edges = [edge for edge in edges if edge[2] == service]
        for downstream, relationship, edge_service in edges:
            if downstream in visited:
                continue
            visited.add(downstream)
            next_depth = depth + 1
            propagation.append({"departmentId": downstream, "departmentName": by_id[downstream]["name"], "triggeredByDepartmentId": current, "depth": next_depth, "relationship": relationship, "service": edge_service})
            queue.append((downstream, next_depth))
    direct = [step["departmentId"] for step in propagation if step["depth"] == 1]
    indirect = [step["departmentId"] for step in propagation if step["depth"] > 1]
    affected = [department_id] + [step["departmentId"] for step in propagation]
    impact_percent = round(100 * len(affected) / len(DEPARTMENTS), 1)
    if impact_percent >= 50:
        severity, rationale = "critical", "Critical: at least 50% of modeled departments are affected."
    elif impact_percent >= 25:
        severity, rationale = "high", "High: at least 25% but less than 50% of modeled departments are affected."
    elif impact_percent >= 10:
        severity, rationale = "medium", "Medium: at least 10% but less than 25% of modeled departments are affected."
    else:
        severity, rationale = "low", "Low: fewer than 10% of modeled departments are affected."
    return {"ok": True, "initialFailure": {"departmentId": department_id, "departmentName": by_id[department_id]["name"], "service": service}, "directlyAffectedDepartmentIds": direct, "indirectlyAffectedDepartmentIds": indirect, "secondaryAffectedDepartmentIds": direct + indirect, "affectedDepartmentIds": affected, "propagationOrder": propagation, "impact": {"directlyAffectedCount": len(direct), "indirectlyAffectedCount": len(indirect), "totalAffectedCount": len(affected), "institutionalImpactPercent": impact_percent, "severity": severity, "severityRationale": rationale}}