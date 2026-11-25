import {
  DEPARTMENTS,
  INCIDENT_FAILURE_TYPES,
  SAMPLE_INCIDENTS,
} from "./data.js";
import { simulateFailure } from "./simulation.js";
import {
  applyRecoveryPlan,
  createRecoveryState,
  generateRecoveryPlans,
} from "./recovery.js";

const STORAGE_KEY = "omnis-x-demo-state-v1";
const SEVERITIES = ["low", "medium", "high", "critical"];
const departmentIds = new Set(DEPARTMENTS.map((department) => department.id));
const failureTypesByValue = new Map(
  INCIDENT_FAILURE_TYPES.map((failureType) => [failureType.value, failureType]),
);

function failure(code, message) {
  return { ok: false, error: { code, message } };
}

function isSimulationResult(result) {
  return Boolean(
    result &&
      result.ok === true &&
      result.initialFailure &&
      typeof result.initialFailure.departmentId === "string" &&
      Array.isArray(result.affectedDepartmentIds) &&
      Array.isArray(result.directlyAffectedDepartmentIds) &&
      Array.isArray(result.indirectlyAffectedDepartmentIds),
  );
}

function isRecoveryState(state) {
  return Boolean(
    state &&
      Array.isArray(state.allocations) &&
      Array.isArray(state.restoredOperationIds) &&
      Array.isArray(state.appliedPlanIds),
  );
}

function createIncidentRecord(incident) {
  return {
    id: incident.id,
    departmentId: incident.departmentId,
    title: incident.title,
    failureType: incident.failureType,
    severity: incident.severity,
    description: incident.description || incident.details,
    timestamp: incident.timestamp || incident.reportedAt,
    status: incident.status,
    simulated: true,
    simulationResult: null,
    recoveryState: createRecoveryState(),
    recoveryPlans: [],
    rejectedOptions: [],
    unresolvedOperations: [],
    unmodeledAffectedDepartmentIds: [],
  };
}

export function createInitialIncidentState() {
  return {
    version: 1,
    incidents: SAMPLE_INCIDENTS.map(createIncidentRecord),
    recoveryAllocations: [],
    recoveryActivity: [],
  };
}

function isValidIncident(incident) {
  return Boolean(
    incident &&
      typeof incident.id === "string" &&
      incident.id.trim() &&
      departmentIds.has(incident.departmentId) &&
      typeof incident.title === "string" &&
      typeof incident.failureType === "string" &&
      SEVERITIES.includes(incident.severity) &&
      typeof incident.description === "string" &&
      typeof incident.timestamp === "string" &&
      ["active", "resolved"].includes(incident.status) &&
      (incident.simulationResult === null || isSimulationResult(incident.simulationResult)) &&
      isRecoveryState(incident.recoveryState) &&
      Array.isArray(incident.recoveryPlans) &&
      Array.isArray(incident.rejectedOptions) &&
      Array.isArray(incident.unresolvedOperations) &&
      Array.isArray(incident.unmodeledAffectedDepartmentIds),
  );
}

function isValidIncidentState(state) {
  if (
    !state ||
    state.version !== 1 ||
    !Array.isArray(state.incidents) ||
    !Array.isArray(state.recoveryAllocations) ||
    !Array.isArray(state.recoveryActivity)
  ) {
    return false;
  }

  const incidentIds = new Set();
  for (const incident of state.incidents) {
    if (!isValidIncident(incident) || incidentIds.has(incident.id)) return false;
    incidentIds.add(incident.id);
  }

  const activityIds = new Set();
  const activityIsValid = state.recoveryActivity.every((event) => {
    if (
      !event ||
      typeof event.id !== "string" ||
      activityIds.has(event.id) ||
      !["plans-generated", "plan-applied"].includes(event.type) ||
      typeof event.timestamp !== "string"
    ) {
      return false;
    }
    activityIds.add(event.id);
    if (event.type === "plans-generated") {
      return Number.isInteger(event.planCount) && event.planCount >= 0;
    }
    return Array.isArray(event.operationIds) && Array.isArray(event.resourceAllocations);
  });

  return (
    activityIsValid &&
    state.recoveryAllocations.every(
      (allocation) =>
        allocation &&
        typeof allocation.resourceId === "string" &&
        typeof allocation.operationId === "string" &&
        Number.isFinite(allocation.quantity) &&
        allocation.quantity > 0,
    ) &&
    state.recoveryActivity.every(
      (event) =>
        event &&
        typeof event.id === "string" &&
        ["plans-generated", "plan-applied"].includes(event.type) &&
        typeof event.timestamp === "string",
    )
  );
}

export function loadIncidentState() {
  let serialized;
  try {
    serialized = window.localStorage.getItem(STORAGE_KEY);
  } catch (error) {
    return {
      state: createInitialIncidentState(),
      warning: `Saved demo data could not be read: ${error.message}`,
    };
  }

  if (serialized === null) {
    return { state: createInitialIncidentState(), warning: "" };
  }

  let storedState;
  try {
    storedState = JSON.parse(serialized);
  } catch (error) {
    return {
      state: createInitialIncidentState(),
      warning: `Saved demo data was invalid JSON and has been replaced: ${error.message}`,
    };
  }

  if (!isValidIncidentState(storedState)) {
    return {
      state: createInitialIncidentState(),
      warning: "Saved demo data did not match the current data structure and has been replaced.",
    };
  }

  return { state: storedState, warning: "" };
}

export function saveIncidentState(state) {
  if (!isValidIncidentState(state)) {
    return failure("INVALID_STATE", "Demo state could not be saved because its data is invalid.");
  }

  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    return { ok: true };
  } catch (error) {
    return failure("STORAGE_UNAVAILABLE", `Demo state could not be saved: ${error.message}`);
  }
}

export function resetIncidentState() {
  const state = createInitialIncidentState();
  try {
    window.localStorage.removeItem(STORAGE_KEY);
    return { ok: true, state };
  } catch (error) {
    return {
      ok: false,
      state,
      error: {
        code: "STORAGE_UNAVAILABLE",
        message: `Demo state was reset in memory but saved data could not be cleared: ${error.message}`,
      },
    };
  }
}

function createUniqueId(prefix, existingIds) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const id = `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    if (!existingIds.has(id)) return id;
  }
  return null;
}

function serviceForIncident(incident) {
  if (incident.failureType === "utility_outage" || incident.failureType === "power_failure") {
    return "Electricity";
  }
  if (incident.failureType === "internet_failure") return "Internet";
  return null;
}

function withIncident(state, incidentId, update) {
  const incidentIndex = state.incidents.findIndex((incident) => incident.id === incidentId);
  if (incidentIndex === -1) return null;
  const incidents = state.incidents.slice();
  incidents[incidentIndex] = update(state.incidents[incidentIndex]);
  return { ...state, incidents };
}

export function getIncidentRecoveryState(state, incident) {
  return {
    allocations: state.recoveryAllocations.map((allocation) => ({ ...allocation })),
    restoredOperationIds: incident.recoveryState.restoredOperationIds.slice(),
    appliedPlanIds: incident.recoveryState.appliedPlanIds.slice(),
  };
}

export function simulateIncident(state, incidentId) {
  if (!isValidIncidentState(state)) {
    return failure("INVALID_STATE", "Incident state is invalid.");
  }
  const incident = state.incidents.find((item) => item.id === incidentId);
  if (!incident) return failure("UNKNOWN_INCIDENT", "Incident was not found.");
  if (incident.status !== "active") {
    return failure("INCIDENT_RESOLVED", "Only active incidents can be simulated.");
  }
  if (incident.simulationResult) {
    return { ok: true, state, incident, simulationResult: incident.simulationResult };
  }

  const service = serviceForIncident(incident);
  const simulationResult = simulateFailure(
    incident.departmentId,
    service ? { service } : {},
  );
  if (!simulationResult.ok) return simulationResult;

  const nextState = withIncident(state, incidentId, (current) => ({
    ...current,
    simulationResult,
  }));
  return {
    ok: true,
    state: nextState,
    incident: nextState.incidents.find((item) => item.id === incidentId),
    simulationResult,
  };
}

export function createIncident(state, input) {
  if (!isValidIncidentState(state) || !input || typeof input !== "object") {
    return failure("INVALID_INPUT", "Incident state and form input must be valid.");
  }
  const failureType = failureTypesByValue.get(input.failureType);
  if (!departmentIds.has(input.departmentId)) {
    return failure("INVALID_DEPARTMENT", "Choose a valid affected department.");
  }
  if (!failureType) {
    return failure("INVALID_FAILURE_TYPE", "Choose a valid failure type.");
  }
  if (!SEVERITIES.includes(input.severity)) {
    return failure("INVALID_SEVERITY", "Choose a valid incident severity.");
  }
  const description = typeof input.description === "string" ? input.description.trim() : "";
  if (!description || description.length > 500) {
    return failure("INVALID_DESCRIPTION", "Enter an incident description of 1 to 500 characters.");
  }
  if (failureType.service && input.departmentId !== "infrastructure") {
    return failure(
      "INVALID_SERVICE_DEPARTMENT",
      `${failureType.label} incidents must be reported against Infrastructure.`,
    );
  }

  const existingIds = new Set(state.incidents.map((incident) => incident.id));
  const id = createUniqueId("incident", existingIds);
  if (!id) return failure("DUPLICATE_INCIDENT_ID", "A unique incident ID could not be generated.");

  const department = DEPARTMENTS.find((item) => item.id === input.departmentId);
  const incident = {
    id,
    departmentId: department.id,
    title: `${failureType.label} — ${department.name}`,
    failureType: failureType.value,
    severity: input.severity,
    description,
    timestamp: new Date().toISOString(),
    status: "active",
    simulated: true,
    simulationResult: null,
    recoveryState: createRecoveryState(),
    recoveryPlans: [],
    rejectedOptions: [],
    unresolvedOperations: [],
    unmodeledAffectedDepartmentIds: [],
  };

  const nextState = { ...state, incidents: [...state.incidents, incident] };
  const simulated = simulateIncident(nextState, id);
  if (!simulated.ok) return simulated;
  return { ok: true, state: simulated.state, incident: simulated.incident };
}

function makeActivityEvent(state, type, properties = {}) {
  const existingIds = new Set(state.recoveryActivity.map((event) => event.id));
  const id = createUniqueId("recovery-event", existingIds);
  if (!id) return null;
  return {
    id,
    type,
    timestamp: new Date().toISOString(),
    ...properties,
  };
}

function appendActivity(state, event) {
  return { ...state, recoveryActivity: [...state.recoveryActivity, event] };
}

export function generateIncidentRecoveryPlans(state, incidentId) {
  if (!isValidIncidentState(state)) {
    return failure("INVALID_STATE", "Incident state is invalid.");
  }
  const incident = state.incidents.find((item) => item.id === incidentId);
  if (!incident) return failure("UNKNOWN_INCIDENT", "Incident was not found.");
  if (incident.status !== "active") {
    return failure("INCIDENT_RESOLVED", "Recovery plans can only be generated for active incidents.");
  }
  if (!incident.simulationResult) {
    return failure("SIMULATION_REQUIRED", "Run the incident simulation before generating recovery plans.");
  }

  const recoveryResult = generateRecoveryPlans(incident.simulationResult, {
    recoveryState: getIncidentRecoveryState(state, incident),
  });
  if (!recoveryResult.ok) return recoveryResult;

  let nextState = withIncident(state, incidentId, (current) => ({
    ...current,
    recoveryPlans: recoveryResult.plans,
    rejectedOptions: recoveryResult.rejectedOptions,
    unresolvedOperations: recoveryResult.unresolvedOperations,
    unmodeledAffectedDepartmentIds: recoveryResult.unmodeledAffectedDepartmentIds,
  }));
  const event = makeActivityEvent(nextState, "plans-generated", {
      incidentId,
      planCount: recoveryResult.plans.length,
      planIds: recoveryResult.plans.map((plan) => plan.id),
    });
  if (!event) return failure("DUPLICATE_ACTIVITY_ID", "A unique recovery activity ID could not be generated.");
  nextState = appendActivity(nextState, event);
  return {
    ok: true,
    state: nextState,
    incident: nextState.incidents.find((item) => item.id === incidentId),
    recoveryResult,
  };
}

export function applyIncidentRecoveryPlan(state, incidentId, planId) {
  if (!isValidIncidentState(state)) {
    return failure("INVALID_STATE", "Incident state is invalid.");
  }
  const incident = state.incidents.find((item) => item.id === incidentId);
  if (!incident) return failure("UNKNOWN_INCIDENT", "Incident was not found.");
  if (incident.status !== "active") {
    return failure("INCIDENT_RESOLVED", "Recovery plans can only be applied to active incidents.");
  }
  const plan = incident.recoveryPlans.find((item) => item.id === planId);
  if (!plan) return failure("UNKNOWN_PLAN", "The selected recovery plan is no longer available.");

  const recoveryResult = applyRecoveryPlan(
    plan,
    getIncidentRecoveryState(state, incident),
  );
  if (!recoveryResult.ok) return recoveryResult;

  const newAllocations = recoveryResult.state.allocations
    .slice(state.recoveryAllocations.length)
    .map((allocation) => ({ ...allocation, incidentId }));
  const recoveryAllocations = [...state.recoveryAllocations, ...newAllocations];
  const updatedRecoveryState = {
    allocations: newAllocations,
    restoredOperationIds: recoveryResult.state.restoredOperationIds,
    appliedPlanIds: recoveryResult.state.appliedPlanIds,
  };
  let nextState = {
    ...state,
    recoveryAllocations,
  };
  nextState = withIncident(nextState, incidentId, (current) => ({
    ...current,
    recoveryState: updatedRecoveryState,
  }));

  const updatedIncident = nextState.incidents.find((item) => item.id === incidentId);
  const refreshed = generateRecoveryPlans(updatedIncident.simulationResult, {
    recoveryState: getIncidentRecoveryState(nextState, updatedIncident),
  });
  nextState = withIncident(nextState, incidentId, (current) => ({
    ...current,
    recoveryPlans: refreshed.ok ? refreshed.plans : [],
    rejectedOptions: refreshed.ok ? refreshed.rejectedOptions : [],
    unresolvedOperations: refreshed.ok ? refreshed.unresolvedOperations : [],
    unmodeledAffectedDepartmentIds: refreshed.ok
      ? refreshed.unmodeledAffectedDepartmentIds
      : [],
  }));
  const event = makeActivityEvent(nextState, "plan-applied", {
      incidentId,
      planId: plan.id,
      operationIds: plan.operationIds.slice(),
      resourceAllocations: newAllocations,
    });
  if (!event) return failure("DUPLICATE_ACTIVITY_ID", "A unique recovery activity ID could not be generated.");
  nextState = appendActivity(nextState, event);

  return {
    ok: true,
    state: nextState,
    incident: nextState.incidents.find((item) => item.id === incidentId),
    appliedPlan: plan,
  };
}

export function resolveIncident(state, incidentId) {
  if (!isValidIncidentState(state)) {
    return failure("INVALID_STATE", "Incident state is invalid.");
  }
  const incident = state.incidents.find((item) => item.id === incidentId);
  if (!incident) return failure("UNKNOWN_INCIDENT", "Incident was not found.");
  if (incident.status === "resolved") {
    return failure("INCIDENT_ALREADY_RESOLVED", "This incident is already resolved.");
  }

  const nextState = withIncident(state, incidentId, (current) => ({
    ...current,
    status: "resolved",
    resolvedAt: new Date().toISOString(),
  }));
  return { ok: true, state: nextState };
}

export function clearIncident(state, incidentId) {
  if (!isValidIncidentState(state)) {
    return failure("INVALID_STATE", "Incident state is invalid.");
  }
  if (!state.incidents.some((incident) => incident.id === incidentId)) {
    return failure("UNKNOWN_INCIDENT", "Incident was not found.");
  }

  return {
    ok: true,
    state: {
      ...state,
      incidents: state.incidents.filter((incident) => incident.id !== incidentId),
      recoveryAllocations: state.recoveryAllocations.filter(
        (allocation) => allocation.incidentId !== incidentId,
      ),
      recoveryActivity: state.recoveryActivity.filter(
        (event) => event.incidentId !== incidentId,
      ),
    },
  };
}

export function recordScenarioPlanGeneration(state, result, contextLabel) {
  if (!result || result.ok !== true || !Array.isArray(result.plans)) {
    return state;
  }
  const event = makeActivityEvent(state, "plans-generated", {
      incidentId: null,
      contextLabel,
      planCount: result.plans.length,
      planIds: result.plans.map((plan) => plan.id),
    });
  return event ? appendActivity(state, event) : state;
}

export function recordScenarioPlanApplication(state, plan, contextLabel) {
  if (!plan || plan.feasible !== true) return state;
  const event = makeActivityEvent(state, "plan-applied", {
      incidentId: null,
      contextLabel,
      planId: plan.id,
      operationIds: plan.operationIds.slice(),
      resourceAllocations: plan.resourceAllocations.map((allocation) => ({
        ...allocation,
        incidentId: null,
      })),
    });
  return event ? appendActivity(state, event) : state;
}

export function getIncidentAnalytics(state) {
  const severityDistribution = Object.fromEntries(
    SEVERITIES.map((severity) => [severity, 0]),
  );
  const affectedDepartmentCounts = new Map();
  let directImpactCount = 0;
  let indirectImpactCount = 0;

  state.incidents.forEach((incident) => {
    severityDistribution[incident.severity] += 1;
    if (!incident.simulationResult) return;

    directImpactCount += new Set(
      incident.simulationResult.directlyAffectedDepartmentIds,
    ).size;
    indirectImpactCount += new Set(
      incident.simulationResult.indirectlyAffectedDepartmentIds,
    ).size;
    new Set(incident.simulationResult.affectedDepartmentIds).forEach((departmentId) => {
      affectedDepartmentCounts.set(
        departmentId,
        (affectedDepartmentCounts.get(departmentId) || 0) + 1,
      );
    });
  });

  const departmentNames = new Map(DEPARTMENTS.map((item) => [item.id, item.name]));
  const mostAffectedDepartments = [...affectedDepartmentCounts.entries()]
    .map(([departmentId, incidentCount]) => ({
      departmentId,
      departmentName: departmentNames.get(departmentId) || departmentId,
      incidentCount,
    }))
    .sort(
      (first, second) =>
        second.incidentCount - first.incidentCount ||
        first.departmentName.localeCompare(second.departmentName),
    );
  const appliedEvents = state.recoveryActivity.filter((event) => event.type === "plan-applied");

  return {
    totalIncidents: state.incidents.length,
    activeIncidents: state.incidents.filter((incident) => incident.status === "active").length,
    resolvedIncidents: state.incidents.filter((incident) => incident.status === "resolved").length,
    severityDistribution,
    directImpactCount,
    indirectImpactCount,
    mostAffectedDepartments,
    recoveryPlansGenerated: state.recoveryActivity
      .filter((event) => event.type === "plans-generated")
      .reduce((total, event) => total + event.planCount, 0),
    recoveryPlansApplied: appliedEvents.length,
    operationsRestored: appliedEvents.reduce(
      (total, event) => total + event.operationIds.length,
      0,
    ),
    recoveryResourcesConsumed: new Set(
      state.recoveryAllocations.map((allocation) => allocation.resourceId),
    ).size,
  };
}
