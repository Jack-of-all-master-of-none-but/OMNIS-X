from math import ceil

from ..models.catalog import DEPARTMENTS, INCIDENT_FAILURE_TYPES, INCIDENT_SEVERITIES
from .analytics import analytics_overview
from .engine import simulate
from .risk_intelligence import calculate_department_risks


SERVICE_FAILURES = {"power_failure": "Electricity", "internet_failure": "Internet"}
DEPARTMENT_BY_ID = {department["id"]: department for department in DEPARTMENTS}


def _ordered_ids(values):
    value_set = set(values)
    return [department["id"] for department in DEPARTMENTS if department["id"] in value_set]


def _simulate_origins(department_ids, failure_type):
    service = SERVICE_FAILURES.get(failure_type)
    simulations = []
    for department_id in department_ids:
        result = simulate(department_id, service if department_id == "infrastructure" else None)
        if result.get("ok") is False:
            return result
        simulations.append(result)
    return {"ok": True, "simulations": simulations, "service": service}


def _merge_simulations(department_ids, results, service):
    origin_ids = set(department_ids)
    direct_ids = []
    indirect_ids = []
    for result in results:
        for department_id in result["directlyAffectedDepartmentIds"]:
            if department_id not in origin_ids and department_id not in direct_ids:
                direct_ids.append(department_id)
        for department_id in result["indirectlyAffectedDepartmentIds"]:
            if department_id not in origin_ids and department_id not in indirect_ids:
                indirect_ids.append(department_id)
    indirect_ids = [department_id for department_id in indirect_ids if department_id not in direct_ids]
    affected_ids = origin_ids | set(direct_ids) | set(indirect_ids)
    direct = direct_ids
    indirect = indirect_ids
    affected = _ordered_ids(affected_ids)
    primary_id = department_ids[0]
    return {
        "ok": True,
        "initialFailure": {
            "departmentId": primary_id,
            "departmentName": DEPARTMENT_BY_ID[primary_id]["name"],
            "service": service,
        },
        "initialDepartmentIds": list(department_ids),
        "directlyAffectedDepartmentIds": direct,
        "indirectlyAffectedDepartmentIds": indirect,
        "secondaryAffectedDepartmentIds": direct + indirect,
        "affectedDepartmentIds": affected,
        "impact": {
            "directlyAffectedCount": len(direct),
            "indirectlyAffectedCount": len(indirect),
            "totalAffectedCount": len(affected),
            "institutionalImpactPercent": round(100 * len(affected) / len(DEPARTMENTS), 1),
        },
    }


def _scenario_impact(scenario, scenario_index, department_ids, existing_state, baseline_health, baseline_risk):
    simulated = _simulate_origins(department_ids, scenario["failureType"])
    if not simulated["ok"]:
        return simulated

    simulation_result = _merge_simulations(
        department_ids,
        simulated["simulations"],
        simulated["service"],
    )
    hypothetical_incident = {
        "id": f"scenario-{scenario_index}",
        "departmentId": department_ids[0],
        "departmentIds": list(department_ids),
        "title": scenario["name"],
        "failureType": scenario["failureType"],
        "severity": scenario["severity"],
        "description": scenario.get("description", ""),
        "status": "active",
        "simulationResult": simulation_result,
        "recoveryState": {"allocations": [], "restoredOperationIds": [], "appliedPlanIds": []},
    }
    current_incidents = existing_state.get("incidents", [])
    if not isinstance(current_incidents, list):
        current_incidents = []
    hypothetical_state = {**existing_state, "incidents": [*current_incidents, hypothetical_incident]}
    hypothetical_health = analytics_overview(hypothetical_state)["institutionalHealth"]["healthScore"]
    hypothetical_risk = calculate_department_risks(hypothetical_state)["institutionalRisk"]["averageInstitutionalRiskScore"]

    affected_ids = set(simulation_result["affectedDepartmentIds"])
    critical_departments = [
        {
            "departmentId": department_id,
            "departmentName": DEPARTMENT_BY_ID[department_id]["name"],
            "priority": DEPARTMENT_BY_ID[department_id].get("priority", "medium"),
        }
        for department_id in simulation_result["affectedDepartmentIds"]
        if DEPARTMENT_BY_ID[department_id].get("priority") == "critical"
    ]
    direct = simulation_result["directlyAffectedDepartmentIds"]
    indirect = simulation_result["indirectlyAffectedDepartmentIds"]
    dependency_impact = len(direct) + len(indirect)
    health_impact = round(max(0, baseline_health - hypothetical_health), 1)
    risk_impact = round(
        max(0, hypothetical_risk - baseline_risk),
        1,
    )
    health_after = round(hypothetical_health, 1)
    risk_after = round(hypothetical_risk, 1)
    explanation = (
        f"This scenario affects {len(affected_ids)} of {len(DEPARTMENTS)} departments, "
        f"including {len(critical_departments)} critical-priority departments. "
        f"The estimated health score decreases by {health_impact} points and average risk "
        f"increases by {risk_impact} points compared with the current stored state."
    )
    return {
        "scenarioId": f"scenario-{scenario_index}",
        "scenarioIndex": scenario_index,
        "name": scenario["name"],
        "departmentId": department_ids[0],
        "departmentIds": list(department_ids),
        "failureType": scenario["failureType"],
        "severity": scenario["severity"],
        "description": scenario.get("description", ""),
        "directlyAffected": direct,
        "indirectlyAffected": indirect,
        "affectedDepartmentCount": len(affected_ids),
        "dependencyImpact": dependency_impact,
        "dependencyImpactPercent": round(100 * dependency_impact / len(DEPARTMENTS), 1),
        "currentInstitutionalHealthScore": round(baseline_health, 1),
        "estimatedHealthImpact": health_impact,
        "estimatedInstitutionalHealthAfter": health_after,
        "currentAverageInstitutionalRiskScore": round(baseline_risk, 1),
        "estimatedRiskImpact": risk_impact,
        "estimatedInstitutionalRiskAfter": risk_after,
        "criticalDepartmentsAffected": critical_departments,
        "explanation": explanation,
        "calculationBasis": {
            "health": "Difference in existing status-based institutional health score before and after the hypothetical active scenario.",
            "risk": "Difference in existing average departmental risk score before and after the hypothetical active scenario.",
        },
    }


def analyze_scenarios(scenarios, state):
    baseline_health = analytics_overview(state)["institutionalHealth"]["healthScore"]
    baseline_risk = calculate_department_risks(state)["institutionalRisk"]["averageInstitutionalRiskScore"]
    calculated = []
    for index, scenario in enumerate(scenarios, 1):
        department_ids = list(dict.fromkeys(scenario["departmentIds"]))
        invalid_department = next(
            (department_id for department_id in department_ids if department_id not in DEPARTMENT_BY_ID),
            None,
        )
        if invalid_department:
            return {"ok": False, "error": {"code": "UNKNOWN_DEPARTMENT", "departmentId": invalid_department}}
        service = SERVICE_FAILURES.get(scenario["failureType"])
        if service and any(department_id != "infrastructure" for department_id in department_ids):
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_SERVICE_DEPARTMENT",
                    "message": f"{service} failure scenarios must target Infrastructure only.",
                    "departmentIds": department_ids,
                },
            }
        if scenario["failureType"] not in INCIDENT_FAILURE_TYPES:
            return {"ok": False, "error": {"code": "INVALID_FAILURE_TYPE"}}
        if scenario["severity"] not in INCIDENT_SEVERITIES:
            return {"ok": False, "error": {"code": "INVALID_SEVERITY"}}
        result = _scenario_impact(
            scenario,
            index,
            department_ids,
            state,
            baseline_health,
            baseline_risk,
        )
        if result.get("ok") is False:
            return result
        calculated.append(result)

    by_impact = sorted(
        calculated,
        key=lambda item: (-item["affectedDepartmentCount"], -item["estimatedHealthImpact"], item["scenarioIndex"]),
    )
    by_risk = sorted(
        calculated,
        key=lambda item: (-item["estimatedRiskImpact"], -item["affectedDepartmentCount"], item["scenarioIndex"]),
    )
    ranked = [
        {
            "scenarioId": item["scenarioId"],
            "scenarioIndex": item["scenarioIndex"],
            "name": item["name"],
            "affectedDepartmentCount": item["affectedDepartmentCount"],
            "estimatedHealthImpact": item["estimatedHealthImpact"],
            "estimatedRiskImpact": item["estimatedRiskImpact"],
        }
        for item in by_impact
    ]

    frequency = {department["id"]: 0 for department in DEPARTMENTS}
    for scenario_result in calculated:
        affected = set(scenario_result["departmentIds"])
        affected.update(scenario_result["directlyAffected"])
        affected.update(scenario_result["indirectlyAffected"])
        for department_id in affected:
            frequency[department_id] += 1
    frequency_rows = [
        {
            "departmentId": department["id"],
            "departmentName": department["name"],
            "scenarioCount": frequency[department["id"]],
        }
        for department in DEPARTMENTS
        if frequency[department["id"]] > 0
    ]
    frequency_rows.sort(key=lambda item: (-item["scenarioCount"], item["departmentName"]))
    most_frequent = [item for item in frequency_rows if item["scenarioCount"] == frequency_rows[0]["scenarioCount"]] if frequency_rows else []
    average_affected = round(
        sum(item["affectedDepartmentCount"] for item in calculated) / max(1, len(calculated)),
        1,
    )
    highest_impact = by_impact[0] if by_impact else None
    highest_risk = by_risk[0] if by_risk else None
    high_impact_threshold = ceil(len(DEPARTMENT_BY_ID) * 0.25)
    high_impact_scenarios = [item for item in calculated if item["affectedDepartmentCount"] >= high_impact_threshold]
    repeated_frequency = {department["id"]: 0 for department in DEPARTMENTS}
    for scenario_result in high_impact_scenarios:
        affected = set(scenario_result["departmentIds"] + scenario_result["directlyAffected"] + scenario_result["indirectlyAffected"])
        for department_id in affected:
            repeated_frequency[department_id] += 1
    repeated_high_impact = [
        {"departmentId": department["id"], "departmentName": department["name"], "scenarioCount": count}
        for department in DEPARTMENTS
        if (count := repeated_frequency[department["id"]]) >= 2
    ]
    repeated_high_impact.sort(key=lambda item: (-item["scenarioCount"], item["departmentName"]))

    return {
        "ok": True,
        "status": "success",
        "scenarioCount": len(calculated),
        "scenarios": calculated,
        "comparison": {
            "highestImpactScenario": highest_impact["name"] if highest_impact else "",
            "highestImpactScenarioIndex": highest_impact["scenarioIndex"] if highest_impact else None,
            "highestRiskScenario": highest_risk["name"] if highest_risk else "",
            "highestRiskScenarioIndex": highest_risk["scenarioIndex"] if highest_risk else None,
            "highestHealthImpactScenario": max(calculated, key=lambda item: (item["estimatedHealthImpact"], -item["scenarioIndex"]))["name"] if calculated else "",
            "highestHealthImpactScenarioIndex": max(calculated, key=lambda item: (item["estimatedHealthImpact"], -item["scenarioIndex"]))["scenarioIndex"] if calculated else None,
            "rankedScenarios": ranked,
        },
        "departmentImpactFrequency": frequency_rows,
        "institutionalSummary": {
            "mostFrequentlyAffectedDepartments": most_frequent,
            "averageAffectedDepartments": average_affected,
            "worstCaseScenario": highest_impact["name"] if highest_impact else "",
            "worstCaseScenarioIndex": highest_impact["scenarioIndex"] if highest_impact else None,
            "repeatedHighImpactDepartments": repeated_high_impact,
            "highImpactThresholdAffectedDepartments": high_impact_threshold,
        },
        "disclaimer": "Scenario results are simulated estimates, not live institutional predictions.",
    }
