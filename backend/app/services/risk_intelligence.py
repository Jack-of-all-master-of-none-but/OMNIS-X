from collections import Counter, defaultdict, deque

from ..models.catalog import DEPARTMENTS, DEPENDENCIES


# Percent weights are centralized and sum to 100.
RISK_FACTOR_WEIGHTS = {
    "departmentCriticality": 25,
    "dependencyImpact": 30,
    "operationalStatus": 25,
    "activeIncidentSeverity": 20,
}

CRITICALITY_SCORES = {"critical": 100, "high": 75, "medium": 50}
STATUS_SCORES = {"operational": 0, "warning": 60, "failed": 100}
SEVERITY_SCORES = {"low": 25, "medium": 50, "high": 75, "critical": 100}
RISK_LEVELS = ((25, "LOW"), (50, "MODERATE"), (75, "HIGH"), (101, "CRITICAL"))
FACTOR_LABELS = {
    "departmentCriticality": "Department criticality",
    "dependencyImpact": "Downstream dependency impact",
    "operationalStatus": "Current operational status",
    "activeIncidentSeverity": "Active incident severity",
}


def risk_level(score):
    score = max(0, min(100, float(score)))
    for threshold, level in RISK_LEVELS:
        if score < threshold:
            return level
    return "CRITICAL"


def weighted_risk_score(factor_scores, weights=None):
    weights = RISK_FACTOR_WEIGHTS if weights is None else weights
    if sum(weights.values()) != 100:
        raise ValueError("Risk factor weights must sum to 100.")
    return round(
        sum(factor_scores[factor] * weight for factor, weight in weights.items()) / 100,
        1,
    )


def _reachable_counts():
    department_ids = {department["id"] for department in DEPARTMENTS}
    outgoing = defaultdict(list)
    for upstream, downstream, _relationship, _service in DEPENDENCIES:
        outgoing[upstream].append(downstream)

    counts = {}
    for department_id in department_ids:
        visited = {department_id}
        queue = deque(outgoing.get(department_id, []))
        while queue:
            downstream_id = queue.popleft()
            if downstream_id in visited:
                continue
            visited.add(downstream_id)
            queue.extend(outgoing.get(downstream_id, []))
        counts[department_id] = len(visited - {department_id})
    return counts


def _active_incident_scores(state):
    scores = defaultdict(list)
    department_ids = {department["id"] for department in DEPARTMENTS}
    incidents = state.get("incidents", [])
    if not isinstance(incidents, list):
        return scores

    for incident in incidents:
        if not isinstance(incident, dict) or incident.get("status") != "active":
            continue
        severity_score = SEVERITY_SCORES.get(incident.get("severity"), 0)
        simulation = incident.get("simulationResult")
        affected_ids = set()
        if isinstance(simulation, dict):
            affected = simulation.get("affectedDepartmentIds")
            if isinstance(affected, list):
                affected_ids.update(item for item in affected if isinstance(item, str))
        initial_department_id = incident.get("departmentId")
        if isinstance(initial_department_id, str):
            affected_ids.add(initial_department_id)
        for department_id in affected_ids & department_ids:
            scores[department_id].append(severity_score)
    return scores


def _explanation(factor_scores, contributions, level):
    leading = sorted(contributions.items(), key=lambda item: (-item[1], item[0]))[:2]
    if not leading or leading[0][1] == 0:
        return f"Risk is {level}; no modeled factor currently contributes risk points."
    leading_labels = [FACTOR_LABELS[factor] for factor, value in leading if value > 0]
    if len(leading_labels) == 1:
        drivers = leading_labels[0]
    else:
        drivers = f"{leading_labels[0]} and {leading_labels[1]}"
    return f"Risk is {level}, primarily driven by {drivers}."


def calculate_department_risks(state):
    reachable = _reachable_counts()
    incident_scores = _active_incident_scores(state)
    denominator = max(1, len(DEPARTMENTS) - 1)
    rows = []
    factor_totals = Counter()

    for department in DEPARTMENTS:
        factor_scores = {
            "departmentCriticality": CRITICALITY_SCORES.get(department.get("priority"), 25),
            "dependencyImpact": round(100 * reachable[department["id"]] / denominator, 1),
            "operationalStatus": STATUS_SCORES.get(department.get("status"), 0),
            "activeIncidentSeverity": max(incident_scores.get(department["id"], [0])),
        }
        contributions = {
            factor: round(factor_scores[factor] * weight / 100, 2)
            for factor, weight in RISK_FACTOR_WEIGHTS.items()
        }
        score = weighted_risk_score(factor_scores)
        level = risk_level(score)
        contributing_factors = [
            FACTOR_LABELS[factor]
            for factor, contribution in sorted(
                contributions.items(), key=lambda item: (-item[1], item[0])
            )
            if contribution > 0
        ][:3]
        row = {
            "departmentId": department["id"],
            "departmentName": department["name"],
            "riskScore": score,
            "riskLevel": level,
            "factorScores": factor_scores,
            "factorContributions": contributions,
            "reachableDepartmentCount": reachable[department["id"]],
            "explanation": _explanation(factor_scores, contributions, level),
            "mainContributingFactors": contributing_factors,
        }
        rows.append(row)
        factor_totals.update(contributions)

    rows.sort(key=lambda item: (-item["riskScore"], item["departmentName"]))
    level_counts = Counter(item["riskLevel"] for item in rows)
    highest_risk = [
        {
            "departmentId": item["departmentId"],
            "departmentName": item["departmentName"],
            "riskScore": item["riskScore"],
            "riskLevel": item["riskLevel"],
        }
        for item in rows[:5]
    ]
    most_important_factors = [
        {
            "factor": factor,
            "label": FACTOR_LABELS[factor],
            "averageWeightedContribution": round(factor_totals[factor] / max(1, len(rows)), 2),
        }
        for factor in sorted(
            RISK_FACTOR_WEIGHTS,
            key=lambda item: (-factor_totals[item], item),
        )
    ]
    summary = {
        "highestRiskDepartments": highest_risk,
        "riskLevelCounts": {
            level: level_counts.get(level, 0)
            for level in ("LOW", "MODERATE", "HIGH", "CRITICAL")
        },
        "averageInstitutionalRiskScore": round(
            sum(item["riskScore"] for item in rows) / max(1, len(rows)), 1
        ),
        "mostImportantContributingFactors": most_important_factors,
    }
    return {
        "riskWeights": dict(RISK_FACTOR_WEIGHTS),
        "departments": rows,
        "institutionalRisk": summary,
    }
