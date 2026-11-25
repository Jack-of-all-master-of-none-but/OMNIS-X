import re
from datetime import datetime, timezone
from uuid import uuid4

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from ..errors import APIError
from ..models.catalog import DEPARTMENTS, INCIDENT_FAILURE_TYPES, INCIDENT_SEVERITIES
from ..services.engine import apply_plan, build_recovery, simulate
from ..services.dependency_intelligence import calculate_dependency_intelligence
from ..services.risk_intelligence import calculate_department_risks
from ..services.scenario_intelligence import analyze_scenarios
from ..services.store import load_state, state_transaction
from ..services.analytics import analytics_departments, analytics_overview, analytics_recovery

api = Blueprint("api", __name__)
INCIDENT_ID_PATTERN = re.compile(r"incident-[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
PLAN_ID_PATTERN = re.compile(r"recovery-[A-Za-z0-9][A-Za-z0-9._:-]{0,1023}\Z")


def error(message, code, status, details=None):
    response = {"status": "error", "message": message, "code": code}
    if details:
        response["details"] = details
    return jsonify(response), status


def request_object():
    if not request.is_json:
        raise APIError(
            "A JSON request body is required.",
            "JSON_REQUIRED",
            400,
            {"body": "Set Content-Type to application/json and provide an object."},
        )
    try:
        payload = request.get_json()
    except (BadRequest, UnsupportedMediaType) as error:
        raise APIError(
            "The request body must contain valid JSON.",
            "MALFORMED_JSON",
            400,
            {"body": "Could not parse JSON."},
        ) from error
    if not isinstance(payload, dict):
        raise APIError(
            "The JSON request body must be an object.",
            "INVALID_BODY_TYPE",
            400,
            {"body": "Expected a JSON object."},
        )
    return payload


def required_string(payload, field, allowed=None, max_length=None):
    value = payload.get(field)
    if not isinstance(value, str):
        reason = "is required" if field not in payload else "must be a string"
        raise APIError(
            f"{field} {reason}.",
            "INVALID_FIELD",
            400,
            {field: reason},
        )
    value = value.strip()
    if not value:
        raise APIError(
            f"{field} is required.",
            "INVALID_FIELD",
            400,
            {field: "must not be empty"},
        )
    if max_length is not None and len(value) > max_length:
        raise APIError(
            f"{field} must be {max_length} characters or fewer.",
            "INVALID_FIELD",
            400,
            {field: f"maximum length is {max_length}"},
        )
    if allowed is not None and value not in allowed:
        raise APIError(
            f"{field} has an unsupported value.",
            "INVALID_FIELD",
            400,
            {field: {"allowed": sorted(allowed)}},
        )
    return value


def validate_incident_id(incident_id):
    if not INCIDENT_ID_PATTERN.fullmatch(incident_id):
        raise APIError(
            "Incident ID has an invalid format.",
            "INVALID_INCIDENT_ID",
            400,
            {"incidentId": "must be a valid incident ID"},
        )


def validate_plan_id(plan_id):
    if not PLAN_ID_PATTERN.fullmatch(plan_id):
        raise APIError(
            "Plan ID has an invalid format.",
            "INVALID_PLAN_ID",
            400,
            {"planId": "must be a valid recovery plan ID"},
        )


def incident_by_id(state, incident_id):
    return next((item for item in state["incidents"] if item["id"] == incident_id), None)


@api.get("/health")
def health():
    return jsonify({"status": "success", "service": "OMNIS-X API"})


@api.get("/departments")
def departments():
    return jsonify(DEPARTMENTS)


@api.get("/departments/stats")
def department_stats():
    counts = {key: 0 for key in ("operational", "warning", "failed")}
    for department in DEPARTMENTS:
        counts[department["status"]] += 1
    return jsonify({"total": len(DEPARTMENTS), "statusSummary": counts, "departments": DEPARTMENTS})


@api.get("/incidents")
def get_incidents():
    return jsonify(load_state()["incidents"])


@api.post("/incidents")
def create_incident():
    payload = request_object()
    department_id = required_string(
        payload,
        "departmentId",
        {department["id"] for department in DEPARTMENTS},
    )
    failure_type = required_string(payload, "failureType", INCIDENT_FAILURE_TYPES)
    severity = required_string(payload, "severity", INCIDENT_SEVERITIES)
    description = required_string(payload, "description", max_length=500)
    with state_transaction() as state:
        incident_id = f"incident-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        now = datetime.now(timezone.utc).isoformat()
        incident = {
            "id": incident_id,
            "departmentId": department_id,
            "title": failure_type.replace("_", " ").title(),
            "failureType": failure_type,
            "severity": severity,
            "description": description,
            "details": description,
            "status": "active",
            "timestamp": now,
            "reportedAt": now,
            "simulationResult": None,
            "recoveryState": {"allocations": [], "restoredOperationIds": [], "appliedPlanIds": []},
            "recoveryPlans": [],
            "rejectedOptions": [],
            "unresolvedOperations": [],
            "unmodeledAffectedDepartmentIds": [],
        }
        state["incidents"].append(incident)
    return jsonify(incident), 201


@api.patch("/incidents/<incident_id>/resolve")
def resolve_incident(incident_id):
    validate_incident_id(incident_id)
    with state_transaction() as state:
        incident = incident_by_id(state, incident_id)
        if not incident:
            return error("Incident not found.", "INCIDENT_NOT_FOUND", 404)
        incident["status"] = "resolved"
    return jsonify(incident)


@api.delete("/incidents/<incident_id>")
def delete_incident(incident_id):
    validate_incident_id(incident_id)
    with state_transaction() as state:
        remaining = [item for item in state["incidents"] if item["id"] != incident_id]
        if len(remaining) == len(state["incidents"]):
            return error("Incident not found.", "INCIDENT_NOT_FOUND", 404)
        state["incidents"] = remaining
        state["recoveryActivity"] = [event for event in state["recoveryActivity"] if event.get("incidentId") != incident_id]
    return jsonify({"status": "success", "deletedIncidentId": incident_id})


@api.post("/simulations")
def simulate_incident():
    payload = request_object()
    incident_id = required_string(payload, "incidentId")
    validate_incident_id(incident_id)
    with state_transaction() as state:
        incident = incident_by_id(state, incident_id)
        if not incident:
            return error("Incident not found.", "INCIDENT_NOT_FOUND", 404)
        result = simulate(incident["departmentId"], service_for(incident))
        if not result.get("ok"):
            return error(result["error"]["message"], result["error"]["code"], 400)
        incident["simulationResult"] = result
    return jsonify(result)


def service_for(incident):
    kind = incident.get("failureType", "")
    if "power" in kind or "electric" in kind:
        return "Electricity"
    if "internet" in kind or "network" in kind:
        return "Internet"
    return None


@api.get("/simulations/<incident_id>")
def get_simulation(incident_id):
    validate_incident_id(incident_id)
    incident = incident_by_id(load_state(), incident_id)
    if not incident:
        return error("Incident not found.", "INCIDENT_NOT_FOUND", 404)
    if not incident.get("simulationResult"):
        return error("Incident has not been simulated.", "SIMULATION_NOT_FOUND", 409)
    return jsonify(incident["simulationResult"])


@api.get("/recovery/<incident_id>")
def get_recovery(incident_id):
    validate_incident_id(incident_id)
    state = load_state()
    incident = incident_by_id(state, incident_id)
    if not incident:
        return error("Incident not found.", "INCIDENT_NOT_FOUND", 404)
    if not incident.get("simulationResult"):
        return error("Incident has not been simulated.", "SIMULATION_NOT_FOUND", 409)

    stored_recovery_state = incident.get("recoveryState")
    if not isinstance(stored_recovery_state, dict):
        stored_recovery_state = {}
    recovery_state = dict(stored_recovery_state)
    recovery_state.setdefault("restoredOperationIds", [])
    recovery_state.setdefault("appliedPlanIds", [])
    recovery_state["allocations"] = shared_allocations(state)

    return jsonify(
        {
            "recoveryState": recovery_state,
            "recoveryPlans": incident.get("recoveryPlans") if isinstance(incident.get("recoveryPlans"), list) else [],
            "rejectedOptions": incident.get("rejectedOptions") if isinstance(incident.get("rejectedOptions"), list) else [],
            "unresolvedOperations": incident.get("unresolvedOperations") if isinstance(incident.get("unresolvedOperations"), list) else [],
            "unmodeledAffectedDepartmentIds": incident.get("unmodeledAffectedDepartmentIds") if isinstance(incident.get("unmodeledAffectedDepartmentIds"), list) else [],
            "recoveryActivity": state["recoveryActivity"],
        }
    )


def shared_allocations(state):
    allocations = []
    for item in state["incidents"]:
        recovery_state = item.get("recoveryState")
        if isinstance(recovery_state, dict) and isinstance(recovery_state.get("allocations"), list):
            allocations.extend(recovery_state["allocations"])
    return allocations


@api.post("/recovery/<incident_id>/plans")
def generate_plans(incident_id):
    validate_incident_id(incident_id)
    with state_transaction() as state:
        incident = incident_by_id(state, incident_id)
        if not incident:
            return error("Incident not found.", "INCIDENT_NOT_FOUND", 404)
        if not incident.get("simulationResult"):
            return error("Run the incident simulation before generating recovery plans.", "SIMULATION_NOT_FOUND", 409)
        result = build_recovery(incident["simulationResult"], shared_allocations(state), incident["recoveryState"])
        if not result["ok"]:
            return error(result["error"]["message"], result["error"]["code"], 400)
        incident.update({"recoveryPlans": result["plans"], "rejectedOptions": result["rejectedOptions"], "unresolvedOperations": result["unresolvedOperations"], "unmodeledAffectedDepartmentIds": result["unmodeledAffectedDepartmentIds"]})
        state["recoveryActivity"].append(
            {
                "id": str(uuid4()),
                "incidentId": incident_id,
                "type": "plans_generated",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "planCount": len(result["plans"]),
                "planIds": [plan["id"] for plan in result["plans"]],
            }
        )
    return jsonify({"recoveryResult": result})


@api.post("/recovery/<incident_id>/apply")
def apply_recovery(incident_id):
    validate_incident_id(incident_id)
    payload = request_object()
    plan_id = required_string(payload, "planId")
    validate_plan_id(plan_id)
    with state_transaction() as state:
        incident = incident_by_id(state, incident_id)
        if not incident:
            return error("Incident not found.", "INCIDENT_NOT_FOUND", 404)
        plan = next((item for item in incident.get("recoveryPlans", []) if item.get("id") == plan_id), None)
        if not plan:
            return error("Recovery plan not found. Generate plans again before applying.", "PLAN_NOT_FOUND", 404)
        result = apply_plan(
            plan,
            incident["recoveryState"],
            shared_allocations(state),
            incident_id,
            incident.get("simulationResult"),
        )
        if not result["ok"]:
            return error(result["error"]["message"], result["error"]["code"], 409)
        incident["recoveryState"] = result["recoveryState"]
        state["recoveryActivity"].append(result["activity"])
    return jsonify(result)


@api.get("/analytics/overview")
def get_analytics_overview():
    return jsonify(analytics_overview(load_state()))


@api.get("/analytics/departments")
def get_analytics_departments():
    return jsonify(analytics_departments(load_state()))


@api.get("/analytics/recovery")
def get_analytics_recovery():
    return jsonify(analytics_recovery(load_state()))


@api.get("/analytics/risks")
def get_analytics_risks():
    return jsonify(calculate_department_risks(load_state()))


@api.get("/analytics/dependencies")
def get_analytics_dependencies():
    return jsonify(calculate_dependency_intelligence(load_state()))


@api.post("/analytics/scenarios")
def analyze_scenario_request():
    payload = request_object()
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise APIError(
            "scenarios must be an array.",
            "INVALID_SCENARIOS",
            400,
            {"scenarios": "Expected an array containing 1 to 10 scenarios."},
        )
    if not 1 <= len(scenarios) <= 10:
        raise APIError(
            "Provide between 1 and 10 scenarios.",
            "INVALID_SCENARIO_COUNT",
            400,
            {"scenarios": "The array must contain between 1 and 10 scenarios."},
        )

    department_ids = {department["id"] for department in DEPARTMENTS}
    normalized_scenarios = []
    for index, scenario in enumerate(scenarios, 1):
        if not isinstance(scenario, dict):
            raise APIError(
                f"Scenario {index} must be an object.",
                "INVALID_SCENARIO",
                400,
                {f"scenarios[{index - 1}]": "Expected an object."},
            )
        name = required_string(scenario, "name", max_length=120)
        failure_type = required_string(scenario, "failureType", INCIDENT_FAILURE_TYPES)
        severity = required_string(scenario, "severity", INCIDENT_SEVERITIES)

        supplied_ids = []
        if "departmentId" in scenario:
            supplied_ids.append(
                required_string(scenario, "departmentId", department_ids)
            )
        if "departmentIds" in scenario:
            values = scenario["departmentIds"]
            if not isinstance(values, list) or not values:
                raise APIError(
                    f"Scenario {index} departmentIds must be a non-empty array.",
                    "INVALID_FIELD",
                    400,
                    {f"scenarios[{index - 1}].departmentIds": "Expected a non-empty array of department IDs."},
                )
            for department_id in values:
                if not isinstance(department_id, str) or not department_id.strip():
                    raise APIError(
                        f"Scenario {index} contains an invalid department ID.",
                        "INVALID_FIELD",
                        400,
                        {f"scenarios[{index - 1}].departmentIds": "Each department ID must be a non-empty string."},
                    )
                department_id = department_id.strip()
                if department_id not in department_ids:
                    raise APIError(
                        f"Scenario {index} references an unknown department.",
                        "INVALID_FIELD",
                        400,
                        {f"scenarios[{index - 1}].departmentIds": {"invalid": department_id}},
                    )
                supplied_ids.append(department_id)
        if not supplied_ids:
            raise APIError(
                f"Scenario {index} requires departmentId or departmentIds.",
                "INVALID_FIELD",
                400,
                {f"scenarios[{index - 1}].departmentId": "At least one department ID is required."},
            )

        description = scenario.get("description", "")
        if not isinstance(description, str) or len(description) > 500:
            raise APIError(
                f"Scenario {index} description must be a string of 500 characters or fewer.",
                "INVALID_FIELD",
                400,
                {f"scenarios[{index - 1}].description": "Expected an optional string up to 500 characters."},
            )

        normalized_scenarios.append(
            {
                "name": name,
                "departmentIds": list(dict.fromkeys(supplied_ids)),
                "failureType": failure_type,
                "severity": severity,
                "description": description.strip(),
            }
        )

    result = analyze_scenarios(normalized_scenarios, load_state())
    if not result.get("ok"):
        error_details = result.get("error", {})
        code = error_details.get("code", "INVALID_SCENARIO")
        message = error_details.get("message", "The scenario could not be simulated.")
        raise APIError(message, code, 400, error_details)
    result.pop("ok", None)
    return jsonify(result)


@api.get("/analytics/export")
def export_analytics():
    state = load_state()
    from datetime import datetime, timezone
    return jsonify({"exportedAt": datetime.now(timezone.utc).isoformat(), "departments": DEPARTMENTS, "incidents": state["incidents"], "overview": analytics_overview(state), "departmentAnalytics": analytics_departments(state), "recovery": analytics_recovery(state), "recoveryActivity": state["recoveryActivity"]})