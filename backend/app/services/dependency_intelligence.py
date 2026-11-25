from collections import defaultdict, deque
from itertools import product

from ..models.catalog import DEPARTMENTS, DEPENDENCIES, OPERATIONS, RESOURCES


CRITICALITY_LEVELS = {"critical": "CRITICAL", "high": "HIGH", "medium": "MODERATE", "low": "LOW"}
MAX_ALTERNATIVE_CONFIGURATIONS = 256


def _reachable_graph(departments, dependencies):
    department_ids = {department["id"] for department in departments}
    outgoing = defaultdict(list)
    for upstream, downstream, _relationship, _service in dependencies:
        if upstream in department_ids and downstream in department_ids:
            if downstream not in outgoing[upstream]:
                outgoing[upstream].append(downstream)

    reachability = {}
    for department_id in department_ids:
        visited = {department_id}
        parents = {department_id: None}
        queue = deque([department_id])
        while queue:
            current = queue.popleft()
            for downstream in outgoing.get(current, []):
                if downstream in visited:
                    continue
                visited.add(downstream)
                parents[downstream] = current
                queue.append(downstream)
        reached = visited - {department_id}
        paths = {}
        for reached_id in reached:
            path = []
            cursor = reached_id
            while cursor is not None:
                path.append(cursor)
                cursor = parents[cursor]
            paths[reached_id] = list(reversed(path))
        reachability[department_id] = {
            "direct": set(outgoing.get(department_id, [])) - {department_id},
            "reached": reached,
            "paths": paths,
        }
    return reachability


def _allocated_quantities(state):
    quantities = defaultdict(float)
    incidents = state.get("incidents", []) if isinstance(state, dict) else []
    if not isinstance(incidents, list):
        return quantities
    for incident in incidents:
        if not isinstance(incident, dict):
            continue
        recovery_state = incident.get("recoveryState")
        if not isinstance(recovery_state, dict):
            continue
        allocations = recovery_state.get("allocations")
        if not isinstance(allocations, list):
            continue
        for allocation in allocations:
            if not isinstance(allocation, dict):
                continue
            resource_id = allocation.get("resourceId")
            quantity = allocation.get("quantity")
            if isinstance(resource_id, str) and isinstance(quantity, (int, float)) and quantity > 0:
                quantities[resource_id] += quantity
    return quantities


def _requirement_alternatives(operation, requirement, resources, allocated):
    primary_ids = set(operation.get("primaryResourceIds", []))
    candidates = []
    for resource in resources:
        if resource.get("id") in primary_ids:
            continue
        if resource.get("type") != requirement.get("resourceType"):
            continue
        if requirement.get("operation") not in resource.get("compatibleOperations", []):
            continue
        if resource.get("unit") != requirement.get("unit"):
            continue
        if resource.get("status") == "unavailable":
            continue
        usable = min(resource.get("capacity", 0), resource.get("available", 0))
        usable -= allocated.get(resource.get("id"), 0)
        if usable > 0:
            candidates.append((resource["id"], usable))

    bundles = []
    chosen = []

    def search(start, capacity):
        if capacity >= requirement["demand"]:
            bundles.append(tuple(item[0] for item in chosen))
            return
        if len(bundles) >= MAX_ALTERNATIVE_CONFIGURATIONS:
            return
        for index in range(start, len(candidates)):
            resource_id, available = candidates[index]
            amount = min(available, requirement["demand"] - capacity)
            if amount <= 0:
                continue
            chosen.append((resource_id, amount))
            search(index + 1, capacity + amount)
            chosen.pop()
            if len(bundles) >= MAX_ALTERNATIVE_CONFIGURATIONS:
                return

    search(0, 0)
    return sorted(set(bundles))


def _operation_alternatives(operation, resources, allocated):
    requirements = operation.get("requirements", [])
    if not isinstance(requirements, list) or not requirements:
        return []
    alternatives_per_requirement = [
        _requirement_alternatives(operation, requirement, resources, allocated)
        for requirement in requirements
    ]
    if any(not alternatives for alternatives in alternatives_per_requirement):
        return []

    configurations = set()
    for combination in product(*alternatives_per_requirement):
        flattened = [resource_id for bundle in combination for resource_id in bundle]
        if len(flattened) != len(set(flattened)):
            continue
        configurations.add(tuple(sorted(flattened)))
        if len(configurations) >= MAX_ALTERNATIVE_CONFIGURATIONS:
            break
    return sorted(configurations)


def _independent_configuration_count(configurations):
    if not configurations:
        return 0
    for index, first in enumerate(configurations):
        first_resources = set(first)
        for second in configurations[index + 1:]:
            if first_resources.isdisjoint(second):
                return 2
    return 1


def _active_incident_ids(state, department_id):
    incidents = state.get("incidents", []) if isinstance(state, dict) else []
    if not isinstance(incidents, list):
        return []
    affected_by = []
    for incident in incidents:
        if not isinstance(incident, dict) or incident.get("status") != "active":
            continue
        affected_ids = set()
        simulation = incident.get("simulationResult")
        if isinstance(simulation, dict) and isinstance(simulation.get("affectedDepartmentIds"), list):
            affected_ids.update(simulation["affectedDepartmentIds"])
        if isinstance(incident.get("departmentId"), str):
            affected_ids.add(incident["departmentId"])
        if department_id in affected_ids:
            affected_by.append(incident.get("id", "unknown"))
    return affected_by


def calculate_dependency_intelligence(
    state,
    departments=None,
    dependencies=None,
    operations=None,
    resources=None,
):
    departments = DEPARTMENTS if departments is None else departments
    dependencies = DEPENDENCIES if dependencies is None else dependencies
    operations = OPERATIONS if operations is None else operations
    resources = RESOURCES if resources is None else resources
    reachability = _reachable_graph(departments, dependencies)
    department_by_id = {department["id"]: department for department in departments}
    operations_by_department = defaultdict(list)
    for operation in operations:
        if operation.get("departmentId") in department_by_id:
            operations_by_department[operation["departmentId"]].append(operation)

    allocated = _allocated_quantities(state)
    operation_findings = {"confirmed": [], "potential": []}
    department_findings = {"confirmed": [], "potential": []}
    resource_findings = {"confirmed": [], "potential": []}
    departments_report = []

    for department in departments:
        department_id = department["id"]
        graph = reachability[department_id]
        priority = department.get("priority", "low")
        classification = CRITICALITY_LEVELS.get(priority, "LOW")
        direct_count = len(graph["direct"])
        reached = graph["reached"]
        indirect_count = len(reached - graph["direct"])
        total_reach = len(reached)
        reason = (
            f"{department['name']} is classified {classification} by its modeled {priority} priority; "
            f"a failure can reach {total_reach} other departments ({direct_count} direct and "
            f"{indirect_count} indirect)."
        )
        departments_report.append(
            {
                "departmentId": department_id,
                "departmentName": department["name"],
                "directDownstreamDependencyCount": direct_count,
                "indirectlyAffectedDepartmentCount": indirect_count,
                "totalDependencyReach": total_reach,
                "criticalityClassification": classification,
                "explanation": reason,
            }
        )

        department_operations = operations_by_department.get(department_id, [])
        if not department_operations:
            finding = {
                "departmentId": department_id,
                "departmentName": department["name"],
                "classification": "CONFIRMED",
                "reason": "No recovery operation is modeled for this department, so a catalog-backed workaround cannot be verified.",
                "operationIds": [],
            }
            department_findings["confirmed"].append(finding)
            continue

        department_confirmed = []
        department_potential = []
        for operation in department_operations:
            configurations = _operation_alternatives(operation, resources, allocated)
            if not configurations:
                finding = {
                    "operationId": operation["id"],
                    "operationName": operation["name"],
                    "departmentId": department_id,
                    "primaryResourceIds": list(operation.get("primaryResourceIds", [])),
                    "classification": "CONFIRMED",
                    "alternativeConfigurationCount": 0,
                    "reason": "No available resource set satisfies every requirement's type, compatibility, unit, and capacity.",
                }
                operation_findings["confirmed"].append(finding)
                department_confirmed.append(finding)
                for resource_id in operation.get("primaryResourceIds", []):
                    resource = next((item for item in resources if item.get("id") == resource_id), None)
                    resource_findings["confirmed"].append(
                        {
                            "resourceId": resource_id,
                            "resourceName": resource.get("name", resource_id) if resource else resource_id,
                            "departmentId": department_id,
                            "operationId": operation["id"],
                            "reason": finding["reason"],
                        }
                    )
                continue

            independent_count = _independent_configuration_count(configurations)
            if independent_count < 2:
                finding = {
                    "operationId": operation["id"],
                    "operationName": operation["name"],
                    "departmentId": department_id,
                    "primaryResourceIds": list(operation.get("primaryResourceIds", [])),
                    "classification": "POTENTIAL",
                    "alternativeConfigurationCount": len(configurations),
                    "reason": "A compatible capacity workaround exists, but there are not two resource-disjoint alternative configurations.",
                }
                operation_findings["potential"].append(finding)
                department_potential.append(finding)
                for resource_id in operation.get("primaryResourceIds", []):
                    resource = next((item for item in resources if item.get("id") == resource_id), None)
                    resource_findings["potential"].append(
                        {
                            "resourceId": resource_id,
                            "resourceName": resource.get("name", resource_id) if resource else resource_id,
                            "departmentId": department_id,
                            "operationId": operation["id"],
                            "alternativeConfigurationCount": len(configurations),
                            "reason": finding["reason"],
                        }
                    )

        if department_confirmed:
            department_findings["confirmed"].append(
                {
                    "departmentId": department_id,
                    "departmentName": department["name"],
                    "classification": "CONFIRMED",
                    "reason": "At least one modeled operation has no valid catalog-backed alternative.",
                    "operationIds": [item["operationId"] for item in department_confirmed],
                }
            )
        elif department_potential:
            department_findings["potential"].append(
                {
                    "departmentId": department_id,
                    "departmentName": department["name"],
                    "classification": "POTENTIAL",
                    "reason": "At least one operation has only one resource-independent workaround configuration.",
                    "operationIds": [item["operationId"] for item in department_potential],
                }
            )

    departments_report.sort(key=lambda item: (-item["totalDependencyReach"], item["departmentName"]))
    criticality_rank = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
    most_critical = sorted(
        departments_report,
        key=lambda item: (
            criticality_rank[item["criticalityClassification"]],
            -item["totalDependencyReach"],
            item["departmentName"],
        ),
    )[:5]

    chains = []
    for item in departments_report:
        graph = reachability[item["departmentId"]]
        for target_id, path in graph["paths"].items():
            chains.append(
                {
                    "startDepartmentId": item["departmentId"],
                    "startDepartmentName": item["departmentName"],
                    "endDepartmentId": target_id,
                    "endDepartmentName": department_by_id[target_id]["name"],
                    "pathDepartmentIds": path,
                    "chainLength": len(path) - 1,
                    "totalReachFromStart": item["totalDependencyReach"],
                }
            )
    chains.sort(
        key=lambda item: (
            -item["totalReachFromStart"],
            -item["chainLength"],
            item["startDepartmentName"],
            item["endDepartmentName"],
        )
    )

    confirmed_counts = {
        "departments": len(department_findings["confirmed"]),
        "operations": len(operation_findings["confirmed"]),
        "resources": len(resource_findings["confirmed"]),
    }
    potential_counts = {
        "departments": len(department_findings["potential"]),
        "operations": len(operation_findings["potential"]),
        "resources": len(resource_findings["potential"]),
    }
    summary = {
        "mostCriticalDepartments": [
            {
                "departmentId": item["departmentId"],
                "departmentName": item["departmentName"],
                "criticalityClassification": item["criticalityClassification"],
                "totalDependencyReach": item["totalDependencyReach"],
            }
            for item in most_critical
        ],
        "highestImpactDependencyChains": chains[:5],
        "singlePointsOfFailure": {
            "confirmed": {
                "departments": department_findings["confirmed"],
                "operations": operation_findings["confirmed"],
                "resources": resource_findings["confirmed"],
            },
            "potential": {
                "departments": department_findings["potential"],
                "operations": operation_findings["potential"],
                "resources": resource_findings["potential"],
            },
            "countsByClassification": {
                "CONFIRMED": confirmed_counts,
                "POTENTIAL": potential_counts,
            },
        },
        "departmentCriticalityCounts": {
            level: sum(item["criticalityClassification"] == level for item in departments_report)
            for level in ("LOW", "MODERATE", "HIGH", "CRITICAL")
        },
        "activeIncidentAffectedDepartmentCount": sum(
            bool(_active_incident_ids(state, department["id"]))
            for department in departments
        ),
    }
    return {"departments": departments_report, "institutionalSummary": summary}
