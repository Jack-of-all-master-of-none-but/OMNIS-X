import math
from datetime import datetime, timezone
from uuid import uuid4

from ..models.catalog import DEPARTMENTS, OPERATIONS, RESOURCES

MAX_BUNDLES_PER_REQUIREMENT = 16
MAX_COMBINED_SEARCH_STATES = 8000


def _failure(code, message, **extra):
    return {"ok": False, "error": {"code": code, "message": message}, **extra}


def _unique(values):
    return list(dict.fromkeys(values))


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_catalog(operations, resources):
    errors = []
    department_ids = {department["id"] for department in DEPARTMENTS}
    resources_by_id = {}
    for resource in resources:
        if not isinstance(resource, dict) or not isinstance(resource.get("id"), str) or not resource["id"]:
            errors.append("Each resource must have a non-empty ID.")
            continue
        if resource["id"] in resources_by_id:
            errors.append(f"Duplicate resource ID: {resource['id']}.")
        resources_by_id[resource["id"]] = resource
        if resource.get("departmentId") not in department_ids:
            errors.append(f"{resource.get('name', resource['id'])} refers to an unknown department.")
        capacity = resource.get("capacity")
        available = resource.get("available")
        if not _is_number(capacity) or capacity < 0:
            errors.append(f"{resource.get('name', resource['id'])} has invalid capacity.")
        if not _is_number(available) or available < 0 or ( _is_number(capacity) and available > capacity):
            errors.append(f"{resource.get('name', resource['id'])} has invalid availability.")
        if not isinstance(resource.get("type"), str) or not resource["type"]:
            errors.append(f"{resource.get('name', resource['id'])} has no resource type.")
        if not isinstance(resource.get("unit"), str) or not resource["unit"]:
            errors.append(f"{resource.get('name', resource['id'])} has no unit.")
        if not isinstance(resource.get("compatibleOperations"), list):
            errors.append(f"{resource.get('name', resource['id'])} has no compatibility list.")
        if resource.get("status") not in ("available", "limited", "unavailable"):
            errors.append(f"{resource.get('name', resource['id'])} has an invalid status.")

    operation_ids = set()
    for operation in operations:
        if not isinstance(operation, dict) or not isinstance(operation.get("id"), str) or not operation["id"]:
            errors.append("Each operation must have a non-empty ID.")
            continue
        if operation["id"] in operation_ids:
            errors.append(f"Duplicate operation ID: {operation['id']}.")
        operation_ids.add(operation["id"])
        if operation.get("departmentId") not in department_ids:
            errors.append(f"{operation.get('name', operation['id'])} refers to an unknown department.")
        if not isinstance(operation.get("requirements"), list) or not operation["requirements"]:
            errors.append(f"{operation.get('name', operation['id'])} has no resource requirements.")
            continue
        for requirement in operation["requirements"]:
            if (
                not isinstance(requirement, dict)
                or not isinstance(requirement.get("id"), str)
                or not requirement["id"]
                or not isinstance(requirement.get("resourceType"), str)
                or not requirement["resourceType"]
                or not isinstance(requirement.get("operation"), str)
                or not requirement["operation"]
                or not _is_number(requirement.get("demand"))
                or requirement["demand"] <= 0
                or not isinstance(requirement.get("unit"), str)
                or not requirement["unit"]
            ):
                errors.append(f"{operation.get('name', operation['id'])} has an invalid requirement.")
        for resource_id in operation.get("primaryResourceIds", []):
            if resource_id not in resources_by_id:
                errors.append(f"{operation['name']} refers to an unknown primary resource: {resource_id}.")
    return errors


def _validate_allocations(allocations):
    if not isinstance(allocations, list):
        return None, ["Allocations must be an array."]
    quantities = {}
    reasons = []
    for allocation in allocations:
        if not isinstance(allocation, dict) or not isinstance(allocation.get("resourceId"), str):
            reasons.append("Every allocation must identify a resource.")
            continue
        quantity = allocation.get("quantity", 0)
        if not _is_number(quantity) or quantity < 0:
            reasons.append(f"Allocation for {allocation['resourceId']} has an invalid quantity.")
            continue
        quantities[allocation["resourceId"]] = quantities.get(allocation["resourceId"], 0) + quantity
    return quantities, reasons


def _valid_simulation(simulation_result):
    if not isinstance(simulation_result, dict) or simulation_result.get("ok") is not True:
        return False
    initial = simulation_result.get("initialFailure")
    affected = simulation_result.get("affectedDepartmentIds")
    if not isinstance(initial, dict) or not isinstance(initial.get("departmentId"), str):
        return False
    if not isinstance(affected, list) or not affected:
        return False
    return (
        all(isinstance(item, str) and item for item in affected)
        and len(set(affected)) == len(affected)
        and initial["departmentId"] in affected
    )


def _get_affected_operations(simulation_result, operations):
    affected = set(simulation_result["affectedDepartmentIds"])
    initial = simulation_result.get("initialFailure", {})
    return [
        operation
        for operation in operations
        if operation["departmentId"] in affected
        and (
            not initial.get("service")
            or operation["departmentId"] != initial["departmentId"]
            or operation.get("service") == initial["service"]
        )
    ]


def _requirement_name(requirement):
    return requirement.get("name") or requirement["id"].replace("-", " ").replace("_", " ").title()


def _make_rejection(operation, requirement, resource, reasons, **extra):
    return {
        "id": f"{operation['id']}:{requirement['id']}:{resource['id'] if resource else 'no-resource'}",
        "operationId": operation["id"],
        "operationName": operation["name"],
        "requirementId": requirement["id"],
        "requirementName": _requirement_name(requirement),
        "resourceId": resource["id"] if resource else None,
        "resourceName": resource["name"] if resource else "No matching resource",
        "required": requirement["demand"],
        "unit": requirement["unit"],
        "feasible": False,
        "reasons": _unique(reasons),
        **extra,
    }


def _get_requirement_bundles(
    operation,
    requirement,
    resources,
    allocated_quantities,
    unavailable_resource_ids,
    rejected_options,
):
    typed_resources = [resource for resource in resources if resource["type"] == requirement["resourceType"]]
    candidates = []
    failed_primary_ids = set(operation.get("primaryResourceIds", []))

    for resource in typed_resources:
        reasons = []
        if resource["id"] in failed_primary_ids:
            reasons.append("This is the operation's failed primary resource.")
        if requirement["operation"] not in resource["compatibleOperations"]:
            reasons.append(f"Resource is not compatible with {requirement['operation']}.")
        if resource["unit"] != requirement["unit"]:
            reasons.append(f"Resource uses {resource['unit']}, not {requirement['unit']}.")
        if resource["status"] == "unavailable" or resource["id"] in unavailable_resource_ids:
            reasons.append("Resource is marked unavailable for this recovery.")
        if resource["id"] in allocated_quantities:
            reasons.append("Resource is already allocated to another recovery operation.")

        usable_capacity = max(
            0,
            min(resource["capacity"], resource["available"])
            - allocated_quantities.get(resource["id"], 0),
        )
        if usable_capacity <= 0:
            reasons.append("Resource has no available capacity.")
        if reasons:
            rejected_options.append(_make_rejection(operation, requirement, resource, reasons))
            continue

        candidates.append({"resource": resource, "usableCapacity": usable_capacity})
        if usable_capacity < requirement["demand"]:
            rejected_options.append(
                _make_rejection(
                    operation,
                    requirement,
                    resource,
                    [
                        f"Insufficient capacity on its own: {usable_capacity} of "
                        f"{requirement['demand']} {requirement['unit']} required."
                    ],
                    availableCapacity=usable_capacity,
                )
            )

    if not typed_resources:
        rejected_options.append(
            _make_rejection(
                operation,
                requirement,
                None,
                [f"No {requirement['resourceType']} resources are defined."],
            )
        )

    bundles = []
    selected = []

    def collect_bundles(start_index, total_capacity):
        if total_capacity >= requirement["demand"]:
            assignments = []
            for item in selected:
                resource = item["resource"]
                assignments.append(
                    {
                        "operationId": operation["id"],
                        "operationName": operation["name"],
                        "requirementId": requirement["id"],
                        "requirementName": _requirement_name(requirement),
                        "resourceId": resource["id"],
                        "resourceName": resource["name"],
                        "quantity": item["quantity"],
                        "unit": requirement["unit"],
                        "capacity": resource["capacity"],
                        "availableAtGeneration": resource["available"],
                    }
                )
            bundles.append(assignments)
            return
        if len(bundles) >= MAX_BUNDLES_PER_REQUIREMENT:
            return
        for index in range(start_index, len(candidates)):
            candidate = candidates[index]
            quantity = min(candidate["usableCapacity"], requirement["demand"] - total_capacity)
            if quantity <= 0:
                continue
            selected.append({"resource": candidate["resource"], "quantity": quantity})
            collect_bundles(index + 1, total_capacity + quantity)
            selected.pop()
            if len(bundles) >= MAX_BUNDLES_PER_REQUIREMENT:
                return

    collect_bundles(0, 0)
    compatible_capacity = sum(candidate["usableCapacity"] for candidate in candidates)
    if not bundles and candidates:
        rejected_options.append(
            _make_rejection(
                operation,
                requirement,
                None,
                [
                    f"Combined compatible capacity is {compatible_capacity} of "
                    f"{requirement['demand']} {requirement['unit']} required."
                ],
                availableCapacity=compatible_capacity,
            )
        )
    return bundles, compatible_capacity


def _combine_requirement_bundles(operation, bundles_by_requirement):
    combinations = [[]]
    for requirement_bundles in bundles_by_requirement:
        next_combinations = []
        for existing in combinations:
            used_resources = {assignment["resourceId"] for assignment in existing}
            for bundle in requirement_bundles:
                if any(assignment["resourceId"] in used_resources for assignment in bundle):
                    continue
                next_combinations.append(existing + bundle)
        combinations = next_combinations
        if not combinations:
            break
    return [{"operation": operation, "assignments": assignments} for assignments in combinations]


def _validate_operation_assignments(
    operation,
    assignments,
    resources_by_id,
    allocated_quantities,
    unavailable_resource_ids,
):
    reasons = []
    requirements = {requirement["id"]: requirement for requirement in operation["requirements"]}
    supplied = {requirement_id: 0 for requirement_id in requirements}
    seen_resources = set()

    if not isinstance(assignments, list):
        return ["Assignments must be an array."]

    for assignment in assignments:
        if not isinstance(assignment, dict):
            reasons.append("Every resource assignment must be an object.")
            continue
        if assignment.get("operationId") != operation["id"]:
            reasons.append("Assignment refers to an operation outside this recovery.")
            continue
        requirement = requirements.get(assignment.get("requirementId"))
        if not requirement:
            reasons.append(f"Assignment refers to an unknown requirement: {assignment.get('requirementId')}.")
            continue
        resource = resources_by_id.get(assignment.get("resourceId"))
        if not resource:
            reasons.append(f"Resource was not found: {assignment.get('resourceId')}.")
            continue
        if resource["id"] in seen_resources:
            reasons.append(f"Resource {resource['name']} is assigned more than once in this plan.")
        seen_resources.add(resource["id"])
        if resource["type"] != requirement["resourceType"]:
            reasons.append(f"{resource['name']} is not a {requirement['resourceType']} resource.")
        if requirement["operation"] not in resource["compatibleOperations"]:
            reasons.append(f"{resource['name']} is not compatible with {requirement['operation']}.")
        if resource["unit"] != requirement["unit"] or assignment.get("unit") != requirement["unit"]:
            reasons.append(f"{resource['name']} does not provide the required {requirement['unit']} unit.")
        if resource["status"] == "unavailable" or resource["id"] in unavailable_resource_ids:
            reasons.append(f"{resource['name']} is unavailable for this recovery.")
        if resource["id"] in allocated_quantities:
            reasons.append(f"{resource['name']} is already allocated.")

        quantity = assignment.get("quantity")
        if not _is_number(quantity) or quantity <= 0:
            reasons.append(f"{resource['name']} has an invalid allocation quantity.")
            continue
        usable_capacity = max(
            0,
            min(resource["capacity"], resource["available"])
            - allocated_quantities.get(resource["id"], 0),
        )
        if quantity > usable_capacity:
            reasons.append(
                f"{resource['name']} has {usable_capacity} {requirement['unit']} available; "
                f"{quantity} were requested."
            )
        supplied[requirement["id"]] += quantity

    for requirement_id, requirement in requirements.items():
        amount = supplied[requirement_id]
        if amount < requirement["demand"]:
            reasons.append(
                f"{_requirement_name(requirement)} needs {requirement['demand']} "
                f"{requirement['unit']}; only {amount} are assigned."
            )
        elif amount > requirement["demand"]:
            reasons.append(
                f"{_requirement_name(requirement)} requires exactly {requirement['demand']} "
                f"{requirement['unit']}; {amount} are assigned."
            )
    return _unique(reasons)


def _priority(operation):
    return {"critical": 4, "high": 3, "medium": 2}.get(str(operation.get("priority", "")).lower(), 1)


def _build_plan(operation_options, all_affected_operations, resources):
    operations = [option["operation"] for option in operation_options]
    assignments = [assignment for option in operation_options for assignment in option["assignments"]]
    operation_ids = [operation["id"] for operation in operations]
    selected_ids = set(operation_ids)
    resources_by_id = {resource["id"]: resource for resource in resources}
    resource_ids = _unique(assignment["resourceId"] for assignment in assignments)
    restored_department_ids = _unique(
        operation["departmentId"]
        for operation in operations
        if all(
            candidate["id"] in selected_ids
            for candidate in all_affected_operations
            if candidate["departmentId"] == operation["departmentId"]
        )
    )
    capacity_comparisons = []
    for operation in operations:
        for requirement in operation["requirements"]:
            provided = sum(
                assignment["quantity"]
                for assignment in assignments
                if assignment["operationId"] == operation["id"]
                and assignment["requirementId"] == requirement["id"]
            )
            capacity_comparisons.append(
                {
                    "operationId": operation["id"],
                    "operationName": operation["name"],
                    "requirementId": requirement["id"],
                    "requirementName": _requirement_name(requirement),
                    "required": requirement["demand"],
                    "provided": provided,
                    "unit": requirement["unit"],
                }
            )
    availability_scores = [
        (resources_by_id[resource_id]["available"] / resources_by_id[resource_id]["capacity"] * 100)
        if resources_by_id[resource_id]["capacity"] > 0
        else 0
        for resource_id in resource_ids
    ]
    availability_score = sum(availability_scores) / len(availability_scores) if availability_scores else 0
    satisfaction_score = (
        sum(item["provided"] >= item["required"] for item in capacity_comparisons)
        / len(capacity_comparisons)
        * 100
        if capacity_comparisons
        else 0
    )
    priority_score = sum(_priority(operation) for operation in operations)
    recovery_minutes = sum(operation.get("estimatedRecoveryMinutes", 0) for operation in operations)
    plan_id = "recovery-" + "-".join(sorted(operation_ids)) + "-" + "-".join(sorted(resource_ids))
    if len(operations) == 1:
        title = (
            f"Restore {operations[0]['name']} using {len(resource_ids)} alternative resource"
            f"{'s' if len(resource_ids) != 1 else ''}"
        )
    else:
        title = f"Restore {len(operations)} operations using {len(resource_ids)} alternative resources"
    return {
        "id": plan_id,
        "title": title,
        "feasible": True,
        "operationIds": operation_ids,
        "operationNames": [operation["name"] for operation in operations],
        "restoredOperationCount": len(operations),
        "restoredDepartmentIds": restored_department_ids,
        "resourceIds": resource_ids,
        "resourceNames": [resources_by_id[resource_id]["name"] for resource_id in resource_ids],
        "resourceAllocations": assignments,
        "capacityComparisons": capacity_comparisons,
        "priorityScore": priority_score,
        "resourceAvailabilityScore": round(availability_score, 1),
        "capacitySatisfactionScore": round(satisfaction_score, 1),
        "estimatedRecoveryMinutes": recovery_minutes,
        "rankingReason": "",
        "rank": 0,
    }


def _plan_key(plan):
    return (
        -plan["restoredOperationCount"],
        -plan["priorityScore"],
        plan["estimatedRecoveryMinutes"],
        len(plan["resourceIds"]),
        -plan["resourceAvailabilityScore"],
        -plan["capacitySatisfactionScore"],
        plan["id"],
    )


def _find_best_combined_plan(prioritized_operations, options_by_operation, all_affected_operations, resources):
    resources_by_id = {resource["id"]: resource for resource in resources}
    best_plan = None
    visited_states = 0

    def search(index, selected, used_resource_ids):
        nonlocal best_plan, visited_states
        visited_states += 1
        if visited_states > MAX_COMBINED_SEARCH_STATES:
            return
        if best_plan and len(selected) + len(prioritized_operations) - index < best_plan["restoredOperationCount"]:
            return
        if index == len(prioritized_operations):
            if len(selected) < 2:
                return
            candidate = _build_plan(selected, all_affected_operations, resources)
            if best_plan is None or _plan_key(candidate) < _plan_key(best_plan):
                best_plan = candidate
            return

        operation = prioritized_operations[index]
        options = sorted(
            options_by_operation[operation["id"]],
            key=lambda option: (
                len(option["assignments"]),
                -sum(
                    resources_by_id[item["resourceId"]]["available"]
                    / max(resources_by_id[item["resourceId"]]["capacity"], 1)
                    for item in option["assignments"]
                ),
            ),
        )
        for option in options:
            option_resource_ids = {item["resourceId"] for item in option["assignments"]}
            if option_resource_ids & used_resource_ids:
                continue
            search(index + 1, selected + [option], used_resource_ids | option_resource_ids)
            if visited_states > MAX_COMBINED_SEARCH_STATES:
                return
        search(index + 1, selected, used_resource_ids)

    search(0, [], set())
    return best_plan


def build_recovery(simulation_result, allocations=None, recovery_state=None, resources=None, operations=None):
    resources = RESOURCES if resources is None else resources
    operations = OPERATIONS if operations is None else operations
    allocations = [] if allocations is None else allocations
    recovery_state = recovery_state or {
        "allocations": [],
        "restoredOperationIds": [],
        "appliedPlanIds": [],
    }

    if not _valid_simulation(simulation_result):
        return _failure(
            "INVALID_SIMULATION",
            "A successful failure simulation result is required.",
            plans=[],
            rejectedOptions=[],
            unresolvedOperations=[],
        )
    if not isinstance(resources, list) or not isinstance(operations, list):
        return _failure("INVALID_CATALOG", "Resources and operations must be arrays.", plans=[], rejectedOptions=[], unresolvedOperations=[])
    catalog_errors = _validate_catalog(operations, resources)
    if catalog_errors:
        return _failure("INVALID_CATALOG", " ".join(catalog_errors), plans=[], rejectedOptions=[], unresolvedOperations=[])
    if not isinstance(recovery_state, dict) or any(
        not isinstance(recovery_state.get(field, []), list)
        for field in ("allocations", "restoredOperationIds", "appliedPlanIds")
    ):
        return _failure("INVALID_RECOVERY_STATE", "Recovery state has an invalid structure.", plans=[], rejectedOptions=[], unresolvedOperations=[])

    allocation_quantities, allocation_errors = _validate_allocations(allocations)
    if allocation_errors:
        return _failure("INVALID_RECOVERY_STATE", " ".join(allocation_errors), plans=[], rejectedOptions=[], unresolvedOperations=[])

    affected_ids = set(simulation_result["affectedDepartmentIds"])
    affected_operations = _get_affected_operations(simulation_result, operations)
    pending_operations = [
        operation
        for operation in affected_operations
        if operation["id"] not in recovery_state.get("restoredOperationIds", [])
    ]
    failed_primary_ids = {
        resource_id
        for operation in pending_operations
        for resource_id in operation.get("primaryResourceIds", [])
    }
    unavailable_ids = failed_primary_ids | {
        resource["id"] for resource in resources if resource["status"] == "unavailable"
    }
    resources_by_id = {resource["id"]: resource for resource in resources}
    rejected_options = []
    unresolved_operations = []
    options_by_operation = {}

    for operation in pending_operations:
        bundles_by_requirement = []
        unresolved_reasons = []
        for requirement in operation["requirements"]:
            bundles, compatible_capacity = _get_requirement_bundles(
                operation,
                requirement,
                resources,
                allocation_quantities,
                unavailable_ids,
                rejected_options,
            )
            bundles_by_requirement.append(bundles)
            if not bundles:
                unresolved_reasons.append(
                    f"{_requirement_name(requirement)}: compatible available capacity is "
                    f"{compatible_capacity} {requirement['unit']}; {requirement['demand']} required."
                )

        if any(not bundles for bundles in bundles_by_requirement):
            unresolved_operations.append(
                {
                    "operationId": operation["id"],
                    "operationName": operation["name"],
                    "departmentId": operation["departmentId"],
                    "reasons": _unique(unresolved_reasons),
                }
            )
            continue

        combinations = _combine_requirement_bundles(operation, bundles_by_requirement)
        valid_options = []
        for option in combinations:
            reasons = _validate_operation_assignments(
                operation,
                option["assignments"],
                resources_by_id,
                allocation_quantities,
                unavailable_ids,
            )
            if reasons:
                unresolved_reasons.extend(reasons)
            else:
                valid_options.append(option)
        if not valid_options:
            unresolved_operations.append(
                {
                    "operationId": operation["id"],
                    "operationName": operation["name"],
                    "departmentId": operation["departmentId"],
                    "reasons": _unique(unresolved_reasons or ["No combination satisfies all operation constraints."]),
                }
            )
            continue
        options_by_operation[operation["id"]] = valid_options

    plans_by_id = {}
    for options in options_by_operation.values():
        for option in options:
            plan = _build_plan([option], affected_operations, resources)
            plans_by_id[plan["id"]] = plan

    prioritized_operations = sorted(
        (operation for operation in pending_operations if operation["id"] in options_by_operation),
        key=lambda operation: (
            -_priority(operation),
            operation.get("estimatedRecoveryMinutes", 0),
            operation["id"],
        ),
    )
    combined_plan = _find_best_combined_plan(
        prioritized_operations,
        options_by_operation,
        affected_operations,
        resources,
    )
    if combined_plan:
        plans_by_id[combined_plan["id"]] = combined_plan

    plans = sorted(plans_by_id.values(), key=_plan_key)
    for rank, plan in enumerate(plans, 1):
        plan["rank"] = rank
        plan["rankingReason"] = (
            f"Ranked #{rank}: restores {plan['restoredOperationCount']} affected operation"
            f"{'s' if plan['restoredOperationCount'] != 1 else ''}; priority score {plan['priorityScore']}; "
            f"estimated recovery {plan['estimatedRecoveryMinutes']} minutes; "
            f"uses {len(plan['resourceIds'])} resource{'s' if len(plan['resourceIds']) != 1 else ''}; "
            f"resource availability {plan['resourceAvailabilityScore']}%; capacity satisfaction "
            f"{plan['capacitySatisfactionScore']}%. All assigned resources passed validation."
        )

    modeled_departments = {operation["departmentId"] for operation in affected_operations}
    deduplicated_rejections = {}
    for rejection in rejected_options:
        existing = deduplicated_rejections.get(rejection["id"])
        if existing:
            existing["reasons"] = _unique(existing["reasons"] + rejection["reasons"])
        else:
            deduplicated_rejections[rejection["id"]] = rejection

    return {
        "ok": True,
        "plans": plans,
        "rejectedOptions": list(deduplicated_rejections.values()),
        "unresolvedOperations": unresolved_operations,
        "unmodeledAffectedDepartmentIds": sorted(affected_ids - modeled_departments),
        "affectedOperationCount": len(affected_operations),
        "restoredOperationIds": list(recovery_state.get("restoredOperationIds", [])),
    }


def apply_plan(plan, recovery_state, shared_allocations, incident_id, simulation_result=None):
    if not isinstance(plan, dict) or plan.get("feasible") is not True or not isinstance(plan.get("id"), str):
        return _failure("INVALID_PLAN", "Select a feasible recovery plan before applying it.")
    if not isinstance(recovery_state, dict) or any(
        not isinstance(recovery_state.get(field), list)
        for field in ("allocations", "restoredOperationIds", "appliedPlanIds")
    ):
        return _failure("INVALID_RECOVERY_STATE", "Recovery state has an invalid structure.")
    if plan["id"] in recovery_state["appliedPlanIds"]:
        return _failure("PLAN_ALREADY_APPLIED", "This recovery plan has already been applied.")

    operation_ids = plan.get("operationIds")
    assignments = plan.get("resourceAllocations")
    if not isinstance(operation_ids, list) or not operation_ids or not isinstance(assignments, list):
        return _failure("INVALID_PLAN", "The selected plan is missing its operations or allocations.")
    declared_resource_ids = plan.get("resourceIds")
    if not isinstance(declared_resource_ids, list) or any(not isinstance(item, str) for item in declared_resource_ids):
        return _failure("INVALID_PLAN", "The selected plan has an invalid resource list.")
    if any(not isinstance(item, str) for item in operation_ids) or len(set(operation_ids)) != len(operation_ids):
        return _failure("INVALID_PLAN", "The selected plan contains invalid or duplicate operations.")

    catalog_errors = _validate_catalog(OPERATIONS, RESOURCES)
    if catalog_errors:
        return _failure("INVALID_CATALOG", " ".join(catalog_errors))
    operations_by_id = {operation["id"]: operation for operation in OPERATIONS}
    resources_by_id = {resource["id"]: resource for resource in RESOURCES}
    selected_operations = []
    for operation_id in operation_ids:
        operation = operations_by_id.get(operation_id)
        if not operation:
            return _failure("UNKNOWN_OPERATION", f"Operation was not found: {operation_id}.")
        if operation_id in recovery_state["restoredOperationIds"]:
            return _failure("OPERATION_ALREADY_RESTORED", f"{operation['name']} has already been restored.")
        selected_operations.append(operation)

    if simulation_result is not None:
        if not _valid_simulation(simulation_result):
            return _failure("INVALID_SIMULATION", "A successful failure simulation result is required.")
        affected_operation_ids = {
            operation["id"] for operation in _get_affected_operations(simulation_result, OPERATIONS)
        }
        if not set(operation_ids) <= affected_operation_ids:
            return _failure("INVALID_PLAN", "The plan includes an operation outside this incident's impact.")

    assignment_operation_ids = set()
    plan_resource_ids = set()
    for assignment in assignments:
        if not isinstance(assignment, dict):
            return _failure("INVALID_PLAN", "Every resource assignment must be an object.")
        assignment_operation_ids.add(assignment.get("operationId"))
        resource_id = assignment.get("resourceId")
        if not isinstance(resource_id, str):
            return _failure("INVALID_PLAN", "Every assignment must identify a resource.")
        if resource_id in plan_resource_ids:
            return _failure("DUPLICATE_ALLOCATION", f"Resource {resource_id} is assigned more than once in the plan.")
        plan_resource_ids.add(resource_id)
    if assignment_operation_ids != set(operation_ids):
        return _failure("INVALID_PLAN", "The plan allocations do not match its operation list.")
    if set(declared_resource_ids) != plan_resource_ids:
        return _failure("INVALID_PLAN", "The plan resource list does not match its allocations.")

    allocation_quantities, allocation_errors = _validate_allocations(shared_allocations)
    if allocation_errors:
        return _failure("INVALID_RECOVERY_STATE", " ".join(allocation_errors))
    for resource_id in plan_resource_ids:
        if resource_id in allocation_quantities:
            resource_name = resources_by_id.get(resource_id, {}).get("name", resource_id)
            return _failure(
                "RESOURCE_ALREADY_ALLOCATED",
                f"Resource {resource_name} has already been allocated.",
            )
    unavailable_ids = {
        resource_id
        for operation in selected_operations
        for resource_id in operation.get("primaryResourceIds", [])
    }
    unavailable_ids.update(
        resource["id"] for resource in RESOURCES if resource["status"] == "unavailable"
    )
    for operation in selected_operations:
        operation_assignments = [
            assignment for assignment in assignments if assignment.get("operationId") == operation["id"]
        ]
        reasons = _validate_operation_assignments(
            operation,
            operation_assignments,
            resources_by_id,
            allocation_quantities,
            unavailable_ids,
        )
        if reasons:
            return _failure(
                "CONSTRAINTS_NOT_SATISFIED",
                f"Cannot apply recovery for {operation['name']}: {' '.join(reasons)}",
            )

    now = datetime.now(timezone.utc).isoformat()
    new_allocations = [
        {**assignment, "incidentId": incident_id, "planId": plan["id"]}
        for assignment in assignments
    ]
    next_state = {
        "allocations": recovery_state["allocations"] + new_allocations,
        "restoredOperationIds": sorted(set(recovery_state["restoredOperationIds"] + operation_ids)),
        "appliedPlanIds": recovery_state["appliedPlanIds"] + [plan["id"]],
    }
    activity = {
        "id": str(uuid4()),
        "incidentId": incident_id,
        "planId": plan["id"],
        "type": "plan_applied",
        "timestamp": now,
        "operationIds": operation_ids,
        "resourceIds": plan["resourceIds"],
    }
    return {"ok": True, "recoveryState": next_state, "activity": activity}