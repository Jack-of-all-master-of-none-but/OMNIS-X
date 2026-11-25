import {
  DATA_PROVENANCE,
  DEPARTMENTS,
  DEPENDENCIES,
  INCIDENT_FAILURE_TYPES,
  OPERATIONS,
  RESOURCES,
} from "./data.js";
import { api } from "./api.js";
import {
  getDependentDepartments,
  getDirectDependencies,
  getDirectDependents,
  simulateFailure,
  validateDepartmentGraph,
} from "./simulation.js";
import {
  applyRecoveryPlan,
  createRecoveryComparison,
  createRecoveryState,
  generateRecoveryPlans,
} from "./recovery.js";
import {
  getIncidentAnalytics,
  getIncidentRecoveryState,
  loadIncidentState,
  recordScenarioPlanApplication,
  recordScenarioPlanGeneration,
  resetIncidentState,
  saveIncidentState,
} from "./incident-store.js";

const STATUS_LABELS = {
  operational: "Operational",
  warning: "Warning",
  failed: "Failed",
};

const departmentGrid = document.querySelector("#department-grid");
const dependencySvg = document.querySelector("#dependency-network");
const simulationForm = document.querySelector("#simulation-form");
const simulationTarget = document.querySelector("#simulation-target");
const simulationResults = document.querySelector("#simulation-results");
const simulationError = document.querySelector("#simulation-error");
const recoverySection = document.querySelector("#recovery-section");
const recoveryError = document.querySelector("#recovery-error");
const recoveryContent = document.querySelector("#recovery-content");
const recoveryPlanList = document.querySelector("#recovery-plan-list");
const recoveryRejectedList = document.querySelector("#recovery-rejected-list");
const applyRecoveryButton = document.querySelector("#apply-selected-recovery");
const recoveryStateMessage = document.querySelector("#recovery-state-message");
const incidentForm = document.querySelector("#incident-form");
const incidentDepartment = document.querySelector("#incident-department");
const incidentFailureType = document.querySelector("#incident-failure-type");
const incidentHistory = document.querySelector("#incident-history");
const incidentError = document.querySelector("#incident-error");
const appFeedback = document.querySelector("#app-feedback");
const resetDemoButton = document.querySelector("#reset-demo");
const svgNamespace = "http://www.w3.org/2000/svg";
const nodeWidth = 214;
const nodeHeight = 64;
const loadedIncidentState = loadIncidentState();
const emptyServerState = {
  version: 1,
  incidents: [],
  recoveryAllocations: [],
  recoveryActivity: [],
};
let incidentState = { ...emptyServerState };
let scenarioState = {
  ...loadedIncidentState.state,
  incidents: [],
  recoveryAllocations: loadedIncidentState.state.recoveryAllocations.filter(
    (allocation) => allocation.incidentId === null,
  ),
  recoveryActivity: loadedIncidentState.state.recoveryActivity.filter(
    (event) => event.incidentId === null,
  ),
};
let backendReady = false;
let backendAnalytics = null;
let backendAnalyticsError = "";
let backendAnalyticsLoading = true;

const departmentPositions = {
  administration: { x: 145, y: 100 },
  infrastructure: { x: 540, y: 100 },
  "faculty-management": { x: 935, y: 100 },
  "academic-management": { x: 145, y: 270 },
  students: { x: 540, y: 270 },
  classrooms: { x: 935, y: 270 },
  laboratories: { x: 145, y: 440 },
  examinations: { x: 540, y: 440 },
  transportation: { x: 935, y: 440 },
  hostels: { x: 145, y: 610 },
  placements: { x: 540, y: 610 },
  events: { x: 935, y: 610 },
};

let selectedDepartmentId = "infrastructure";
let currentSimulationResult = null;
let currentRecoveryState = createRecoveryState();
let currentRecoveryResult = null;
let currentRecoveryComparison = null;
let selectedRecoveryPlanId = null;
let recoveryStatusMessage = "Run a failure simulation to start recovery planning.";
let currentScenarioLabel = "Standalone scenario";

function getActiveRecoveryComparisons() {
  const comparisons = incidentState.incidents
    .filter((incident) => incident.status === "active" && incident.simulationResult)
    .map((incident) =>
      createRecoveryComparison(
        incident.simulationResult,
        getIncidentRecoveryState(incidentState, incident),
      ),
    )
    .filter((comparison) => comparison.ok);

  if (currentSimulationResult && currentRecoveryComparison?.ok) {
    comparisons.push(currentRecoveryComparison);
  }
  return comparisons;
}

function getCurrentlyAffectedDepartmentIds() {
  return new Set(
    getActiveRecoveryComparisons().flatMap(
      (comparison) => comparison.after.remainingAffectedDepartmentIds,
    ),
  );
}

function getCurrentlyRestoredDepartmentIds() {
  return new Set(
    getActiveRecoveryComparisons().flatMap(
      (comparison) => comparison.after.restoredDepartmentIds,
    ),
  );
}

function getDisplayedStatus(department) {
  if (getCurrentlyAffectedDepartmentIds().has(department.id)) return "failed";
  if (
    getCurrentlyRestoredDepartmentIds().has(department.id) &&
    department.status === "failed"
  ) {
    return "operational";
  }
  return department.status;
}

function getInstitutionalHealthScore() {
  const scoreByStatus = { operational: 100, warning: 60, failed: 0 };
  const total = DEPARTMENTS.reduce(
    (sum, department) => sum + scoreByStatus[getDisplayedStatus(department)],
    0,
  );
  return Number((total / DEPARTMENTS.length).toFixed(1));
}

function getStatusCounts(departments) {
  return departments.reduce(
    (counts, department) => {
      counts[getDisplayedStatus(department)] += 1;
      return counts;
    },
    { operational: 0, warning: 0, failed: 0 },
  );
}

function renderSummary() {
  const counts = getStatusCounts(DEPARTMENTS);
  const activeIncidents = incidentState.incidents.filter(
    (incident) => incident.status === "active",
  ).length;

  document.querySelector("#total-departments").textContent = DEPARTMENTS.length;
  document.querySelector("#operational-departments").textContent = counts.operational;
  document.querySelector("#warning-departments").textContent = counts.warning;
  document.querySelector("#failed-departments").textContent = counts.failed;
  document.querySelector("#active-incidents").textContent = activeIncidents;
  document.querySelector("#department-count").textContent =
    `${DEPARTMENTS.length} departments`;
  document.querySelector(".simulation-label").setAttribute(
    "aria-label",
    DATA_PROVENANCE,
  );
}

function showAppFeedback(message, isError = false) {
  appFeedback.textContent = message;
  appFeedback.classList.toggle("is-error", isError);
  appFeedback.hidden = !message;
}

function persistScenarioState() {
  const result = saveIncidentState(scenarioState);
  if (!result.ok) showAppFeedback(result.error.message, true);
  return result.ok;
}

function normalizeServerIncident(incident) {
  const recoveryState = incident.recoveryState || {};
  return {
    ...incident,
    description: incident.description || "",
    timestamp: incident.timestamp || "",
    simulationResult: incident.simulationResult || null,
    recoveryState: {
      allocations: Array.isArray(recoveryState.allocations)
        ? recoveryState.allocations
        : [],
      restoredOperationIds: Array.isArray(recoveryState.restoredOperationIds)
        ? recoveryState.restoredOperationIds
        : [],
      appliedPlanIds: Array.isArray(recoveryState.appliedPlanIds)
        ? recoveryState.appliedPlanIds
        : [],
    },
    recoveryPlans: Array.isArray(incident.recoveryPlans)
      ? incident.recoveryPlans
      : [],
    rejectedOptions: Array.isArray(incident.rejectedOptions)
      ? incident.rejectedOptions
      : [],
    unresolvedOperations: Array.isArray(incident.unresolvedOperations)
      ? incident.unresolvedOperations
      : [],
    unmodeledAffectedDepartmentIds: Array.isArray(
      incident.unmodeledAffectedDepartmentIds,
    )
      ? incident.unmodeledAffectedDepartmentIds
      : [],
  };
}

function uniqueAllocations(allocations) {
  const allocationsByKey = new Map();
  allocations.forEach((allocation) => {
    const key = JSON.stringify([
      allocation.resourceId,
      allocation.operationId,
      allocation.requirementId,
      allocation.planId,
      allocation.incidentId,
    ]);
    allocationsByKey.set(key, allocation);
  });
  return [...allocationsByKey.values()];
}

async function refreshBackendState() {
  const serverIncidents = await api.getIncidents();
  if (!Array.isArray(serverIncidents)) {
    throw new Error("The incidents endpoint did not return an array.");
  }

  const incidentsById = new Set();
  const incidents = serverIncidents.map((record) => {
    if (!record || typeof record.id !== "string" || incidentsById.has(record.id)) {
      throw new Error("The backend returned an invalid or duplicate incident ID.");
    }
    incidentsById.add(record.id);
    return normalizeServerIncident(record);
  });
  const recoverySnapshots = [];

  await Promise.all(
    incidents.map(async (incident) => {
      if (!incident.simulationResult) return;
      incident.simulationResult = await api.getSimulation(incident.id);
      if (incident.status !== "active") return;

      const recovery = await api.getRecovery(incident.id);
      if (!recovery || !recovery.recoveryState) {
        throw new Error(`Recovery data for incident ${incident.id} is incomplete.`);
      }
      incident.recoveryState.restoredOperationIds =
        recovery.recoveryState.restoredOperationIds || [];
      incident.recoveryState.appliedPlanIds =
        recovery.recoveryState.appliedPlanIds || [];
      incident.recoveryPlans = recovery.recoveryPlans || [];
      incident.rejectedOptions = recovery.rejectedOptions || [];
      incident.unresolvedOperations = recovery.unresolvedOperations || [];
      incident.unmodeledAffectedDepartmentIds =
        recovery.unmodeledAffectedDepartmentIds || [];
      recoverySnapshots.push(recovery);
    }),
  );

  const allocationsFromRecovery = recoverySnapshots[0]?.recoveryState.allocations;
  const allocations = Array.isArray(allocationsFromRecovery)
    ? allocationsFromRecovery
    : incidents.flatMap((incident) => incident.recoveryState.allocations);
  const activityById = new Map();
  recoverySnapshots.forEach((recovery) => {
    (recovery.recoveryActivity || []).forEach((event) => {
      if (event && typeof event.id === "string") activityById.set(event.id, event);
    });
  });
  incidentState = {
    version: 1,
    incidents,
    recoveryAllocations: uniqueAllocations(allocations),
    recoveryActivity: [...activityById.values()],
  };
}

async function refreshBackendAnalytics() {
  backendAnalyticsLoading = true;
  backendAnalyticsError = "";
  backendAnalytics = null;
  renderAnalytics();

  try {
    const [overview, departments, recovery] = await Promise.all([
      api.getAnalyticsOverview(),
      api.getAnalyticsDepartments(),
      api.getAnalyticsRecovery(),
    ]);
    backendAnalytics = { overview, departments, recovery };
  } catch (error) {
    backendAnalyticsError = error.message;
  } finally {
    backendAnalyticsLoading = false;
    renderAnalytics();
  }
}

async function synchronizeBackendState() {
  try {
    await refreshBackendState();
    backendReady = true;
    await refreshBackendAnalytics();
  } catch (error) {
    backendReady = false;
    incidentState = { ...emptyServerState };
    backendAnalytics = null;
    backendAnalyticsLoading = false;
    backendAnalyticsError = error.message;
    renderDashboard();
    throw error;
  }
}

async function initializeBackend() {
  const submitButton = incidentForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  showAppFeedback("Connecting to the OMNIS-X backend…");

  try {
    const [health, departments] = await Promise.all([
      api.getHealth(),
      api.getDepartments(),
    ]);
    const expectedIds = DEPARTMENTS.map((department) => department.id).sort();
    const receivedIds = Array.isArray(departments)
      ? departments.map((department) => department.id).sort()
      : [];
    if (
      health.status !== "success" ||
      DEPARTMENTS.length !== 12 ||
      DEPENDENCIES.length !== 26 ||
      receivedIds.length !== expectedIds.length ||
      receivedIds.some((departmentId, index) => departmentId !== expectedIds[index])
    ) {
      throw new Error(
        "Backend health or department data does not match the 12-department, 26-relationship demo model.",
      );
    }

    await synchronizeBackendState();
    renderDashboard();
    if (backendAnalyticsError) {
      showAppFeedback(`Connected to the backend. Analytics unavailable: ${backendAnalyticsError}`, true);
    } else {
      showAppFeedback("");
    }
  } catch (error) {
    backendReady = false;
    incidentState = { ...emptyServerState };
    backendAnalytics = null;
    backendAnalyticsLoading = false;
    backendAnalyticsError = error.message;
    renderDashboard();
    showAppFeedback(`Backend connection failed: ${error.message}`, true);
  } finally {
    submitButton.disabled = !backendReady;
  }
}

function createTextElement(tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function createIncidentAction(label, action, incidentId, planId = "") {
  const button = document.createElement("button");
  button.className = "incident-action-button";
  button.type = "button";
  button.dataset.incidentAction = action;
  button.dataset.incidentId = incidentId;
  if (planId) button.dataset.planId = planId;
  button.textContent = label;
  if (action === "clear") button.classList.add("is-danger");
  return button;
}

function formatIncidentTimestamp(timestamp) {
  const parsed = Date.parse(timestamp);
  return Number.isNaN(parsed) ? timestamp : new Date(parsed).toLocaleString();
}

function createIncidentCard(incident) {
  const card = document.createElement("article");
  card.className = "incident-card";
  card.dataset.incidentId = incident.id;

  const header = document.createElement("div");
  header.className = "incident-card-header";
  const heading = document.createElement("div");
  heading.className = "incident-card-heading";
  heading.append(
    createTextElement("h3", "", incident.title),
    createTextElement(
      "span",
      `incident-severity incident-severity-${incident.severity}`,
      incident.severity,
    ),
    createTextElement(
      "span",
      `incident-status incident-status-${incident.status}`,
      incident.status,
    ),
  );
  header.append(
    heading,
    createTextElement("time", "incident-card-time", formatIncidentTimestamp(incident.timestamp)),
  );

  const department = DEPARTMENTS.find((item) => item.id === incident.departmentId);
  const meta = createTextElement(
    "p",
    "incident-record-meta",
    `${department.name} · ${incident.failureType.replace(/[_-]+/g, " ")}`,
  );
  const description = createTextElement("p", "incident-description", incident.description);
  const actions = document.createElement("div");
  actions.className = "incident-card-actions";

  if (incident.status === "active") {
    if (!incident.simulationResult) {
      actions.append(createIncidentAction("Run BFS simulation", "simulate", incident.id));
    } else {
      actions.append(
        createIncidentAction("Generate recovery plans", "generate", incident.id),
        createIncidentAction("Resolve incident", "resolve", incident.id),
      );
    }
  }
  actions.append(createIncidentAction("Clear record", "clear", incident.id));

  card.append(header, meta, description, actions);

  if (incident.simulationResult) {
    const result = incident.simulationResult;
    const impact = document.createElement("div");
    impact.className = "incident-impact";
    const stats = document.createElement("div");
    stats.className = "incident-impact-stats";
    [
      `Initial: ${result.initialFailure.departmentName}`,
      `Direct: ${result.impact.directlyAffectedCount}`,
      `Indirect: ${result.impact.indirectlyAffectedCount}`,
      `Affected: ${result.impact.totalAffectedCount}`,
      `Impact: ${result.impact.institutionalImpactPercent}%`,
    ].forEach((value) => stats.append(createTextElement("span", "", value)));
    impact.append(stats);
    impact.append(
      createTextElement(
        "p",
        "incident-affected-list",
        `Affected departments: ${getNames(result.affectedDepartmentIds, DEPARTMENTS)}`,
      ),
    );

    if (incident.recoveryPlans.length) {
      const plans = document.createElement("div");
      plans.className = "incident-plan-list";
      incident.recoveryPlans.forEach((plan) => {
        const row = document.createElement("div");
        row.className = "incident-plan";
        const description = document.createElement("span");
        description.textContent =
          `${plan.title} · ${plan.resourceNames.join(", ")}`;
        row.append(
          description,
          createIncidentAction("Apply", "apply", incident.id, plan.id),
        );
        plans.append(row);
      });
      impact.append(plans);
    } else if (incident.unresolvedOperations.length || incident.unmodeledAffectedDepartmentIds.length) {
      const unresolvedCount =
        incident.unresolvedOperations.length + incident.unmodeledAffectedDepartmentIds.length;
      impact.append(
        createTextElement(
          "p",
          "incident-recovery-list",
          `No feasible plan for ${unresolvedCount} affected operation or department. Review the recovery section for detailed constraints when using the standalone scenario tool.`,
        ),
      );
    }

    card.append(impact);
  }

  return card;
}

function renderIncidentHistory() {
  incidentHistory.replaceChildren();
  document.querySelector("#incident-history-count").textContent =
    `${incidentState.incidents.length} incident${incidentState.incidents.length === 1 ? "" : "s"}`;

  if (!incidentState.incidents.length) {
    incidentHistory.append(
      createTextElement("p", "incident-empty", "No incident records. Report a simulated incident to begin."),
    );
    return;
  }

  [...incidentState.incidents].reverse().forEach((incident) => {
    incidentHistory.append(createIncidentCard(incident));
  });
}

function createAnalyticsBar(container, label, value, maximum, category) {
  const row = document.createElement("div");
  row.className = "analytics-bar-row";
  row.append(createTextElement("span", "", label));
  const track = document.createElement("div");
  track.className = "analytics-bar-track";
  const fill = document.createElement("div");
  fill.className = `analytics-bar-fill is-${category}`;
  const width = maximum > 0 ? Math.min(100, (value / maximum) * 100) : 0;
  fill.style.setProperty("--bar-width", `${width}%`);
  track.append(fill);
  row.append(track, createTextElement("strong", "", String(value)));
  container.append(row);
}

function renderAnalytics() {
  const statusChart = document.querySelector("#department-status-chart");
  statusChart.replaceChildren();
  const severityChart = document.querySelector("#incident-severity-chart");
  severityChart.replaceChildren();
  const ranking = document.querySelector("#most-affected-departments");
  ranking.replaceChildren();

  if (!backendAnalytics) {
    const message = backendAnalyticsLoading
      ? "Loading analytics from the backend…"
      : `Analytics unavailable: ${backendAnalyticsError || "No backend data is available."}`;
    [
      "#analytics-total-incidents",
      "#analytics-active-incidents",
      "#analytics-resolved-incidents",
      "#analytics-direct-impact",
      "#analytics-indirect-impact",
      "#analytics-affected-departments",
      "#analytics-plans-generated",
      "#analytics-plans-applied",
      "#analytics-operations-restored",
      "#analytics-resources-consumed",
      "#analytics-health-score",
    ].forEach((selector) => {
      document.querySelector(selector).textContent = "—";
    });
    statusChart.append(createTextElement("p", "incident-empty", message));
    severityChart.append(createTextElement("p", "incident-empty", message));
    ranking.append(createTextElement("li", "incident-empty", message));
    return;
  }

  const { overview, departments, recovery } = backendAnalytics;
  const health = overview.institutionalHealth;
  const statusCounts = departments.statusSummary;
  document.querySelector("#analytics-total-incidents").textContent =
    overview.totalIncidents;
  document.querySelector("#analytics-active-incidents").textContent =
    overview.activeIncidents;
  document.querySelector("#analytics-resolved-incidents").textContent =
    overview.resolvedIncidents;
  document.querySelector("#analytics-direct-impact").textContent =
    overview.directImpactCount;
  document.querySelector("#analytics-indirect-impact").textContent =
    overview.indirectImpactCount;
  document.querySelector("#analytics-affected-departments").textContent =
    health.currentlyAffectedCount;
  document.querySelector("#analytics-plans-generated").textContent =
    recovery.totalRecoveryPlansGenerated;
  document.querySelector("#analytics-plans-applied").textContent =
    recovery.totalRecoveryPlansApplied;
  document.querySelector("#analytics-operations-restored").textContent =
    recovery.operationsRestored;
  document.querySelector("#analytics-resources-consumed").textContent =
    recovery.resourcesAllocated;
  document.querySelector("#analytics-health-score").textContent =
    `${health.healthScore}%`;

  createAnalyticsBar(statusChart, "Operational", statusCounts.operational, DEPARTMENTS.length, "operational");
  createAnalyticsBar(statusChart, "Warning", statusCounts.warning, DEPARTMENTS.length, "warning");
  createAnalyticsBar(statusChart, "Failed", statusCounts.failed, DEPARTMENTS.length, "failed");
  createAnalyticsBar(
    statusChart,
    "Affected",
    health.currentlyAffectedCount,
    DEPARTMENTS.length,
    "high",
  );
  createAnalyticsBar(
    statusChart,
    "Restored",
    health.currentlyRestoredCount,
    DEPARTMENTS.length,
    "restored",
  );

  const severityValues = overview.severityDistribution;
  const maxSeverity = Math.max(0, ...Object.values(severityValues));
  ["low", "medium", "high", "critical"].forEach((severity) => {
    createAnalyticsBar(severityChart, severity, severityValues[severity], maxSeverity, severity);
  });

  const mostAffected = departments.departments
    .filter((department) => department.affectedCount > 0)
    .sort(
      (first, second) =>
        second.affectedCount - first.affectedCount ||
        first.departmentName.localeCompare(second.departmentName),
    )
    .slice(0, 5)
    .map((department) => ({
      departmentName: department.departmentName,
      incidentCount: department.affectedCount,
    }));
  if (!mostAffected.length) {
    ranking.append(
      createTextElement("li", "incident-empty", "No incident simulations recorded yet."),
    );
    return;
  }
  mostAffected.forEach((item) => {
    const row = document.createElement("li");
    row.textContent = item.departmentName;
    row.append(createTextElement("span", "", `${item.incidentCount} incident${item.incidentCount === 1 ? "" : "s"}`));
    ranking.append(row);
  });
}

function getReportIncident(incident) {
  const result = incident.simulationResult;
  return {
    id: incident.id,
    departmentId: incident.departmentId,
    departmentName: DEPARTMENTS.find((item) => item.id === incident.departmentId).name,
    title: incident.title,
    failureType: incident.failureType,
    severity: incident.severity,
    description: incident.description,
    timestamp: incident.timestamp,
    status: incident.status,
    simulation: result
      ? {
          initialFailure: result.initialFailure,
          directlyAffectedDepartmentIds: result.directlyAffectedDepartmentIds,
          indirectlyAffectedDepartmentIds: result.indirectlyAffectedDepartmentIds,
          affectedDepartmentIds: result.affectedDepartmentIds,
          impact: result.impact,
        }
      : null,
    recovery: {
      restoredOperationIds: incident.recoveryState.restoredOperationIds,
      appliedPlanIds: incident.recoveryState.appliedPlanIds,
    },
  };
}

function buildIncidentReport() {
  const departmentStatusCounts = getStatusCounts(DEPARTMENTS);
  const analytics = getIncidentAnalytics(incidentState);
  const healthScore = getInstitutionalHealthScore();
  const departmentStatusSummary = {
    total: DEPARTMENTS.length,
    ...departmentStatusCounts,
    affected: getCurrentlyAffectedDepartmentIds().size,
    restored: getCurrentlyRestoredDepartmentIds().size,
    healthScore,
    departments: DEPARTMENTS.map((department) => ({
      id: department.id,
      name: department.name,
      baselineStatus: department.status,
      currentStatus: getDisplayedStatus(department),
    })),
  };

  return {
    reportType: "OMNIS-X institutional incident and recovery report",
    exportedAt: new Date().toISOString(),
    dataProvenance: DATA_PROVENANCE,
    disclaimer: "All incidents, dependencies, resource availability, impacts, health scores, and recovery activity in this report are simulated demo data. This report does not contain live institutional monitoring or prediction.",
    departmentStatusSummary,
    incidents: incidentState.incidents.map(getReportIncident),
    standaloneScenario: currentSimulationResult
      ? {
          label: currentScenarioLabel,
          initialFailure: currentSimulationResult.initialFailure,
          directlyAffectedDepartmentIds: currentSimulationResult.directlyAffectedDepartmentIds,
          indirectlyAffectedDepartmentIds: currentSimulationResult.indirectlyAffectedDepartmentIds,
          affectedDepartmentIds: currentSimulationResult.affectedDepartmentIds,
          impact: currentSimulationResult.impact,
          recoveryState: currentRecoveryState,
        }
      : null,
    affectedDepartments: [...getCurrentlyAffectedDepartmentIds()].map((departmentId) => ({
      id: departmentId,
      name: DEPARTMENTS.find((department) => department.id === departmentId).name,
    })),
    recoveryActivity: incidentState.recoveryActivity,
    recoveryAllocations: incidentState.recoveryAllocations,
    analytics: {
      ...analytics,
      currentInstitutionalHealthScore: healthScore,
      departmentStatusCounts,
      currentlyAffectedDepartmentIds: [...getCurrentlyAffectedDepartmentIds()],
      currentlyRestoredDepartmentIds: [...getCurrentlyRestoredDepartmentIds()],
    },
    currentInstitutionalHealthScore: healthScore,
  };
}

function exportIncidentReport() {
  try {
    const report = buildIncidentReport();
    const blob = new Blob([JSON.stringify(report, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `omnis-x-report-${report.exportedAt.replace(/[:.]/g, "-")}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    showAppFeedback("Current simulated incident and recovery report exported as JSON.");
  } catch (error) {
    showAppFeedback(`The JSON report could not be exported: ${error.message}`, true);
  }
}

function resetDemo() {
  if (!window.confirm("Reset the standalone scenario data? Backend incidents and their recovery allocations will not be deleted.")) {
    return;
  }

  const reset = resetIncidentState();
  scenarioState = {
    ...reset.state,
    incidents: [],
    recoveryAllocations: [],
    recoveryActivity: [],
  };
  currentSimulationResult = null;
  currentRecoveryState = createRecoveryState();
  currentRecoveryResult = null;
  currentRecoveryComparison = null;
  selectedRecoveryPlanId = null;
  currentScenarioLabel = "Standalone scenario";
  simulationResults.hidden = true;
  simulationError.hidden = true;
  recoveryError.hidden = true;
  incidentError.hidden = true;
  incidentForm.reset();
  syncIncidentDepartment();
  simulationTarget.value = "infrastructure";
  selectedDepartmentId = "infrastructure";
  recoveryStatusMessage = "Run a failure simulation to start recovery planning.";
  if (reset.ok) {
    showAppFeedback("Standalone scenario data reset. Backend incidents were not changed.");
  } else {
    showAppFeedback(reset.error.message, true);
  }
  renderRecoverySection();
  renderDashboard();
}

function createDepartmentCard(department) {
  const card = document.createElement("article");
  card.className = "department-card";
  const departmentStatus = getDisplayedStatus(department);

  const cardTop = document.createElement("div");
  cardTop.className = "department-card-top";

  const icon = document.createElement("span");
  icon.className = "department-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = department.name
    .split(/\s+/)
    .map((word) => word[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const status = document.createElement("span");
  status.className = `status-badge badge-${departmentStatus}`;
  status.innerHTML =
    `<span class="status-marker status-${departmentStatus}" aria-hidden="true"></span>` +
    STATUS_LABELS[departmentStatus];

  const name = document.createElement("h3");
  name.className = "department-name";
  name.textContent = department.name;

  const description = document.createElement("p");
  description.className = "department-description";
  description.textContent = department.description;

  const cardBottom = document.createElement("div");
  cardBottom.className = "department-card-bottom";

  const priorityLabel = document.createElement("span");
  priorityLabel.className = "card-meta-label";
  priorityLabel.textContent = "Institutional priority";

  const priority = document.createElement("span");
  priority.className = `priority-value priority-${department.priority}`;
  priority.textContent = department.priority;

  cardTop.append(icon, status);
  cardBottom.append(priorityLabel, priority);
  card.append(cardTop, name, description, cardBottom);
  return card;
}

function renderDepartments() {
  const fragment = document.createDocumentFragment();
  DEPARTMENTS.forEach((department) => {
    fragment.append(createDepartmentCard(department));
  });
  departmentGrid.replaceChildren(fragment);
}

function createSvgElement(tagName, attributes = {}) {
  const element = document.createElementNS(svgNamespace, tagName);
  Object.entries(attributes).forEach(([name, value]) => {
    element.setAttribute(name, value);
  });
  return element;
}

function getDependencyPath(start, end, index) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const distanceX = Math.abs(dx);
  const distanceY = Math.abs(dy);
  const startScale = 1 / Math.max(
    distanceX / (nodeWidth / 2),
    distanceY / (nodeHeight / 2),
  );
  const endScale = startScale;
  const startX = start.x + dx * startScale;
  const startY = start.y + dy * startScale;
  const endX = end.x - dx * endScale;
  const endY = end.y - dy * endScale;
  const distance = Math.hypot(dx, dy);
  const curve = ((index % 5) - 2) * 14;
  const controlX = (startX + endX) / 2 - (dy / distance) * curve;
  const controlY = (startY + endY) / 2 + (dx / distance) * curve;

  return `M ${startX} ${startY} Q ${controlX} ${controlY} ${endX} ${endY}`;
}

function createDependencyPath(dependency, index) {
  const start = departmentPositions[dependency.upstreamDepartmentId];
  const end = departmentPositions[dependency.downstreamDepartmentId];
  const upstream = DEPARTMENTS.find(
    (department) => department.id === dependency.upstreamDepartmentId,
  );
  const downstream = DEPARTMENTS.find(
    (department) => department.id === dependency.downstreamDepartmentId,
  );
  const path = createSvgElement("path", {
    d: getDependencyPath(start, end, index),
    class: "dependency-edge",
    "data-upstream-id": dependency.upstreamDepartmentId,
    "data-downstream-id": dependency.downstreamDepartmentId,
    "marker-end": "url(#dependency-arrow)",
  });

  const title = createSvgElement("title");
  title.textContent =
    `${upstream.name} → ${downstream.name}: ` +
    `${dependency.service ? `${dependency.service} — ` : ""}${dependency.relationship}`;
  path.append(title);
  return path;
}

function createDepartmentNode(department, connectedDepartmentIds, incidentImpact) {
  const position = departmentPositions[department.id];
  const departmentStatus = getDisplayedStatus(department);
  const isInitialFailure =
    Boolean(
      (currentSimulationResult &&
        currentSimulationResult.initialFailure.departmentId === department.id) ||
        incidentImpact.initialFailureIds.has(department.id),
    );
  const isSecondaryDisruption =
    Boolean(
      (currentSimulationResult &&
        currentSimulationResult.secondaryAffectedDepartmentIds.includes(department.id)) ||
        incidentImpact.secondaryAffectedIds.has(department.id),
    );
  const isRestoredDepartment =
    incidentImpact.restoredDepartmentIds.has(department.id) &&
    !incidentImpact.affectedDepartmentIds.has(department.id);
  const group = createSvgElement("g", {
    class: `dependency-node node-${departmentStatus}`,
    "data-department-id": department.id,
    role: "button",
    tabindex: "0",
    "aria-label": `${department.name}, ${STATUS_LABELS[departmentStatus]}. Select to inspect dependencies.`,
    "aria-pressed": String(department.id === selectedDepartmentId),
  });

  if (department.id === selectedDepartmentId) {
    group.classList.add("node-selected");
  } else if (connectedDepartmentIds.has(department.id)) {
    group.classList.add("node-connected");
  } else {
    group.classList.add("node-muted");
  }
  if (isRestoredDepartment) {
    group.classList.add("node-simulation-restored");
  } else if (isInitialFailure) {
    group.classList.add("node-simulation-initial");
  } else if (isSecondaryDisruption) {
    group.classList.add("node-simulation-affected");
  }

  group.append(createSvgElement("rect", {
    x: position.x - nodeWidth / 2,
    y: position.y - nodeHeight / 2,
    width: nodeWidth,
    height: nodeHeight,
    rx: 10,
  }));
  group.append(createSvgElement("circle", {
    class: "node-status-dot",
    cx: position.x - 83,
    cy: position.y,
    r: 5,
  }));

  const name = createSvgElement("text", {
    class: "node-name",
    x: position.x - 68,
    y: position.y - 2,
  });
  name.textContent = department.name;
  group.append(name);

  const status = createSvgElement("text", {
    class: "node-status",
    x: position.x - 68,
    y: position.y + 16,
  });
  status.textContent = isRestoredDepartment
    ? "RESTORED"
    : isInitialFailure
      ? "INITIAL FAILURE"
    : isSecondaryDisruption
      ? "SECONDARY DISRUPTION"
      : STATUS_LABELS[departmentStatus].toUpperCase();
  group.append(status);

  group.addEventListener("click", () => {
    selectDepartment(department.id, true);
  });
  group.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectDepartment(department.id, true);
    }
  });

  return group;
}

function createRelationshipItem(item, depth = null) {
  const listItem = document.createElement("li");
  listItem.className = "relationship-item";

  const departmentName = document.createElement("strong");
  departmentName.textContent = item.department.name;

  const relationship = document.createElement("span");
  relationship.className = "relationship-detail";
  relationship.textContent =
    `${item.service ? `${item.service} · ` : ""}${item.relationship}`;

  const distance = document.createElement("span");
  distance.className = "relationship-distance";
  if (depth !== null) {
    distance.textContent = depth === 1 ? "DIRECT" : `${depth} STEPS`;
  } else {
    distance.textContent = getDisplayedStatus(item.department).toUpperCase();
  }

  listItem.append(departmentName, distance, relationship);
  return listItem;
}

function renderRelationshipList(list, items, depthProperty = null) {
  list.replaceChildren();

  if (items.length === 0) {
    const emptyItem = document.createElement("li");
    emptyItem.className = "relationship-empty";
    emptyItem.textContent = "No linked departments";
    list.append(emptyItem);
    return;
  }

  items.forEach((item) => {
    const depth = depthProperty ? item[depthProperty] : null;
    list.append(createRelationshipItem(item, depth));
  });
}

function renderDependencyInspector(departmentId) {
  const department = DEPARTMENTS.find((item) => item.id === departmentId);
  const departmentStatus = getDisplayedStatus(department);
  const directDependencies = getDirectDependencies(departmentId);
  const dependentDepartments = getDependentDepartments(departmentId);
  const name = document.querySelector("#selected-department-name");
  const status = document.querySelector("#selected-department-status");

  name.textContent = department.name;
  status.className = `status-badge badge-${departmentStatus}`;
  status.innerHTML =
    `<span class="status-marker status-${departmentStatus}" aria-hidden="true"></span>` +
    STATUS_LABELS[departmentStatus];
  document.querySelector("#selected-department-description").textContent =
    department.description;

  renderRelationshipList(
    document.querySelector("#direct-dependency-list"),
    directDependencies.dependencies,
  );
  renderRelationshipList(
    document.querySelector("#dependent-department-list"),
    dependentDepartments.dependents,
    "depth",
  );
}

function renderDependencyNetwork(restoreFocus = false) {
  const validation = validateDepartmentGraph();
  document.querySelector("#dependency-count").textContent =
    `${validation.relationshipCount} connections`;

  if (!validation.valid) {
    console.error("Invalid institutional dependency data:", validation.errors);
    dependencySvg.replaceChildren();
    const message = createSvgElement("text", {
      class: "network-error",
      x: "50%",
      y: "50%",
      "text-anchor": "middle",
    });
    message.textContent = "Dependency data needs attention before it can be displayed.";
    dependencySvg.append(message);
    return;
  }

  const directDependencies = getDirectDependencies(selectedDepartmentId);
  const directDependents = getDirectDependents(selectedDepartmentId);
  const connectedDepartmentIds = new Set([
    ...directDependencies.dependencies.map((item) => item.department.id),
    ...directDependents.dependents.map((item) => item.department.id),
  ]);
  const definitions = createSvgElement("defs");
  const marker = createSvgElement("marker", {
    id: "dependency-arrow",
    viewBox: "0 0 10 10",
    refX: "8",
    refY: "5",
    markerWidth: "6",
    markerHeight: "6",
    orient: "auto-start-reverse",
  });
  marker.append(createSvgElement("path", { d: "M 0 0 L 10 5 L 0 10 z" }));
  definitions.append(marker);
  dependencySvg.replaceChildren(definitions);

  const simulatedRelationshipDepth = new Map();
  const incidentImpact = {
    initialFailureIds: new Set(),
    secondaryAffectedIds: new Set(),
    affectedDepartmentIds: getCurrentlyAffectedDepartmentIds(),
    restoredDepartmentIds: getCurrentlyRestoredDepartmentIds(),
    relationshipDepth: new Map(),
  };
  incidentState.incidents
    .filter((incident) => incident.status === "active" && incident.simulationResult)
    .forEach((incident) => {
      incidentImpact.initialFailureIds.add(
        incident.simulationResult.initialFailure.departmentId,
      );
      incident.simulationResult.secondaryAffectedDepartmentIds.forEach((id) =>
        incidentImpact.secondaryAffectedIds.add(id),
      );
      incident.simulationResult.propagationOrder.forEach((step) => {
        const key = JSON.stringify([
          step.triggeredByDepartmentId,
          step.departmentId,
        ]);
        incidentImpact.relationshipDepth.set(
          key,
          Math.max(incidentImpact.relationshipDepth.get(key) || 0, step.depth),
        );
      });
    });
  if (currentSimulationResult) {
    currentSimulationResult.propagationOrder.forEach((step) => {
      const relationshipKey = JSON.stringify([
        step.triggeredByDepartmentId,
        step.departmentId,
      ]);
      simulatedRelationshipDepth.set(relationshipKey, step.depth);
    });
  }

  DEPENDENCIES.forEach((dependency, index) => {
    const path = createDependencyPath(dependency, index);
    const relationshipKey = JSON.stringify([
      dependency.upstreamDepartmentId,
      dependency.downstreamDepartmentId,
    ]);
    const simulationDepth = Math.max(
      simulatedRelationshipDepth.get(relationshipKey) || 0,
      incidentImpact.relationshipDepth.get(relationshipKey) || 0,
    );
    if (
      dependency.upstreamDepartmentId === selectedDepartmentId ||
      dependency.downstreamDepartmentId === selectedDepartmentId
    ) {
      path.classList.add("dependency-edge-active");
    }
    if (simulationDepth === 1) {
      path.classList.add("dependency-edge-simulation-initial");
    } else if (simulationDepth > 1) {
      path.classList.add("dependency-edge-simulation-indirect");
    }
    dependencySvg.append(path);
  });

  DEPARTMENTS.forEach((department) => {
    dependencySvg.append(
      createDepartmentNode(department, connectedDepartmentIds, incidentImpact),
    );
  });

  renderDependencyInspector(selectedDepartmentId);
  if (restoreFocus) {
    dependencySvg
      .querySelector(`[data-department-id="${selectedDepartmentId}"]`)
      .focus();
  }
}

function selectDepartment(departmentId, restoreFocus = false) {
  selectedDepartmentId = departmentId;
  renderDependencyNetwork(restoreFocus);
}

function populateFailureScenarios() {
  const departmentOptions = document.createElement("optgroup");
  departmentOptions.label = "Department failures";
  DEPARTMENTS.forEach((department) => {
    const option = document.createElement("option");
    option.value = department.id;
    option.textContent = department.name;
    option.dataset.departmentId = department.id;
    departmentOptions.append(option);
  });

  const servicesByName = new Map();
  DEPENDENCIES.forEach((dependency) => {
    if (dependency.service && !servicesByName.has(dependency.service)) {
      servicesByName.set(dependency.service, dependency.upstreamDepartmentId);
    }
  });

  const serviceOptions = document.createElement("optgroup");
  serviceOptions.label = "Shared infrastructure services";
  servicesByName.forEach((departmentId, service) => {
    const department = DEPARTMENTS.find((item) => item.id === departmentId);
    const option = document.createElement("option");
    option.value = `service:${service}`;
    option.textContent = `${service} failure — ${department.name}`;
    option.dataset.departmentId = departmentId;
    option.dataset.service = service;
    serviceOptions.append(option);
  });

  simulationTarget.replaceChildren(departmentOptions, serviceOptions);
  simulationTarget.value = "infrastructure";
}

function createPropagationItem(step, index, isInitial = false) {
  const item = document.createElement("li");
  item.className = isInitial
    ? "propagation-step propagation-initial"
    : step.depth === 1
      ? "propagation-step propagation-direct"
      : "propagation-step propagation-indirect";

  const stepNumber = document.createElement("span");
  stepNumber.className = "propagation-number";
  stepNumber.textContent = isInitial ? "0" : String(index);

  const stepContent = document.createElement("div");
  stepContent.className = "propagation-content";

  const heading = document.createElement("div");
  heading.className = "propagation-heading";

  const departmentName = document.createElement("strong");
  departmentName.textContent = isInitial
    ? currentSimulationResult.initialFailure.departmentName
    : step.departmentName;

  const stepKind = document.createElement("span");
  stepKind.className = "propagation-kind";
  stepKind.textContent = isInitial
    ? "INITIAL FAILURE"
    : step.depth === 1
      ? "DIRECT DISRUPTION"
      : `INDIRECT · ${step.depth} STEPS`;
  heading.append(departmentName, stepKind);

  const explanation = document.createElement("p");
  explanation.className = "propagation-explanation";
  if (isInitial) {
    const service = currentSimulationResult.initialFailure.service;
    explanation.textContent = service
      ? `${service} service failure at the Infrastructure department`
      : "Selected department is the starting failure";
  } else {
    const trigger = DEPARTMENTS.find(
      (department) => department.id === step.triggeredByDepartmentId,
    );
    explanation.textContent =
      `${trigger.name} → ${step.relationship}`;
  }

  stepContent.append(heading, explanation);
  item.append(stepNumber, stepContent);
  return item;
}

function renderSimulationResults(result) {
  const initialFailure = result.initialFailure;
  const serviceSuffix = initialFailure.service
    ? ` · ${initialFailure.service} service`
    : "";
  document.querySelector("#simulation-result-title").textContent =
    `${initialFailure.departmentName}${serviceSuffix} failure`;

  const severity = document.querySelector("#simulation-severity");
  severity.className = `severity-badge severity-${result.impact.severity}`;
  severity.textContent = `${result.impact.severity.toUpperCase()} IMPACT`;
  document.querySelector("#simulation-initial-failure").textContent =
    `${initialFailure.departmentName}${serviceSuffix}`;
  document.querySelector("#simulation-direct-count").textContent =
    result.impact.directlyAffectedCount;
  document.querySelector("#simulation-indirect-count").textContent =
    result.impact.indirectlyAffectedCount;
  document.querySelector("#simulation-total-count").textContent =
    result.impact.totalAffectedCount;
  document.querySelector("#simulation-impact-percent").textContent =
    `${result.impact.institutionalImpactPercent}%`;
  document.querySelector("#simulation-severity-rationale").textContent =
    result.impact.severityRationale;

  const propagationList = document.querySelector("#simulation-propagation-list");
  propagationList.replaceChildren();
  propagationList.append(createPropagationItem(null, 0, true));
  result.propagationOrder.forEach((step, index) => {
    propagationList.append(createPropagationItem(step, index + 1));
  });

  const affectedList = document.querySelector("#simulation-affected-list");
  affectedList.replaceChildren();
  result.affectedDepartmentIds.forEach((departmentId) => {
    const department = DEPARTMENTS.find((item) => item.id === departmentId);
    const initial = departmentId === initialFailure.departmentId;
    const item = document.createElement("div");
    item.className = initial
      ? "affected-department affected-initial"
      : "affected-department affected-secondary";

    const marker = document.createElement("span");
    marker.className = "affected-marker";
    marker.setAttribute("aria-hidden", "true");

    const name = document.createElement("strong");
    name.textContent = department.name;

    const kind = document.createElement("span");
    kind.className = "affected-kind";
    kind.textContent = initial ? "INITIAL" : "SECONDARY";

    item.append(marker, name, kind);
    affectedList.append(item);
  });

  simulationResults.hidden = false;
}

function getNames(ids, records, property = "name") {
  if (!ids.length) return "None";
  const recordsById = new Map(records.map((record) => [record.id, record]));
  return ids
    .map((id) => (recordsById.get(id) ? recordsById.get(id)[property] : id))
    .join(", ");
}

function renderRecoveryPlanList() {
  recoveryPlanList.replaceChildren();
  const plans =
    currentRecoveryResult && currentRecoveryResult.ok ? currentRecoveryResult.plans : [];

  if (!plans.length) {
    const empty = document.createElement("p");
    empty.className = "recovery-empty";
    empty.textContent = "No feasible recovery plans satisfy the current resource constraints.";
    recoveryPlanList.append(empty);
    applyRecoveryButton.disabled = true;
    return;
  }

  plans.forEach((plan) => {
    const card = document.createElement("article");
    card.className = "recovery-plan-card";
    if (plan.id === selectedRecoveryPlanId) card.classList.add("is-selected");

    const heading = document.createElement("div");
    heading.className = "recovery-plan-top";
    const title = document.createElement("strong");
    title.textContent = plan.title;
    const rank = document.createElement("span");
    rank.className = "recovery-rank";
    rank.textContent = `Rank #${plan.rank}`;
    heading.append(title, rank);

    const reason = document.createElement("p");
    reason.textContent = plan.rankingReason;

    const resources = document.createElement("ul");
    resources.className = "recovery-resource-list";
    plan.resourceAllocations.forEach((allocation) => {
      const row = document.createElement("li");
      row.className = "recovery-resource-row";
      const resource = document.createElement("span");
      resource.textContent = `${allocation.resourceName} · ${allocation.operationName}`;
      const amount = document.createElement("span");
      amount.textContent = `${allocation.quantity} ${allocation.unit}`;
      row.append(resource, amount);
      resources.append(row);
    });

    const capacities = document.createElement("ul");
    capacities.className = "recovery-resource-list";
    plan.capacityComparisons.forEach((comparison) => {
      const row = document.createElement("li");
      row.className = "recovery-resource-row";
      const label = document.createElement("span");
      label.textContent = comparison.requirementName;
      const amount = document.createElement("span");
      amount.textContent =
        `${comparison.provided} / ${comparison.required} ${comparison.unit}`;
      row.append(label, amount);
      capacities.append(row);
    });

    const selectButton = document.createElement("button");
    selectButton.className = "plan-select-button";
    selectButton.type = "button";
    selectButton.dataset.recoveryPlanId = plan.id;
    selectButton.setAttribute("aria-pressed", String(plan.id === selectedRecoveryPlanId));
    selectButton.textContent =
      plan.id === selectedRecoveryPlanId ? "Selected plan" : "Select this plan";

    card.append(heading, reason, resources, capacities, selectButton);
    recoveryPlanList.append(card);
  });

  applyRecoveryButton.disabled = !selectedRecoveryPlanId;
}

function renderRejectedOptions() {
  recoveryRejectedList.replaceChildren();
  const result =
    currentRecoveryResult && currentRecoveryResult.ok ? currentRecoveryResult : null;
  if (!result) return;

  const fragment = document.createDocumentFragment();
  result.rejectedOptions.forEach((option) => {
    const card = document.createElement("article");
    card.className = "rejected-option";
    const heading = document.createElement("div");
    heading.className = "recovery-option-heading";
    const title = document.createElement("strong");
    title.textContent = `${option.resourceName} · ${option.operationName}`;
    const status = document.createElement("span");
    status.textContent = "REJECTED";
    heading.append(title, status);

    const requirement = document.createElement("p");
    requirement.textContent =
      `${option.requirementName}: ${option.required} ${option.unit} required.`;
    const reasons = document.createElement("ul");
    reasons.className = "recovery-reasons";
    option.reasons.forEach((reasonText) => {
      const item = document.createElement("li");
      item.textContent = reasonText;
      reasons.append(item);
    });
    card.append(heading, requirement, reasons);
    fragment.append(card);
  });

  result.unresolvedOperations.forEach((operation) => {
    const card = document.createElement("article");
    card.className = "unresolved-operation";
    const heading = document.createElement("div");
    heading.className = "recovery-option-heading";
    const title = document.createElement("strong");
    title.textContent = operation.operationName;
    const status = document.createElement("span");
    status.textContent = "UNRESOLVED";
    heading.append(title, status);
    card.append(heading);
    operation.reasons.forEach((reasonText) => {
      const reason = document.createElement("p");
      reason.textContent = reasonText;
      card.append(reason);
    });
    fragment.append(card);
  });

  result.unmodeledAffectedDepartmentIds.forEach((departmentId) => {
    const note = document.createElement("p");
    note.className = "recovery-empty";
    note.textContent =
      `${getNames([departmentId], DEPARTMENTS)} is affected, but has no modeled recovery operation.`;
    fragment.append(note);
  });

  if (!fragment.childNodes.length) {
    const empty = document.createElement("p");
    empty.className = "recovery-empty";
    empty.textContent = "No rejected options or unresolved modeled operations.";
    fragment.append(empty);
  }
  recoveryRejectedList.append(fragment);
}

function renderRecoveryComparison() {
  if (!currentRecoveryComparison) return;
  const comparison = currentRecoveryComparison;
  document.querySelector("#health-before").textContent =
    `${comparison.before.healthScore}%`;
  document.querySelector("#failed-before").textContent =
    getNames(comparison.before.failedDepartmentIds, DEPARTMENTS);
  document.querySelector("#affected-before").textContent =
    getNames(comparison.before.affectedDepartmentIds, DEPARTMENTS);
  document.querySelector("#unavailable-before").textContent =
    getNames(comparison.before.unavailableResourceIds, RESOURCES);
  document.querySelector("#health-after").textContent =
    `${comparison.after.healthScore}%`;
  document.querySelector("#restored-after").textContent =
    getNames(comparison.after.restoredDepartmentIds, DEPARTMENTS);
  document.querySelector("#remaining-after").textContent =
    getNames(comparison.after.remainingAffectedDepartmentIds, DEPARTMENTS);
  document.querySelector("#consumed-after").textContent =
    comparison.after.resourcesConsumed.length
      ? comparison.after.resourcesConsumed
          .map(
            (allocation) =>
              `${allocation.resourceName} (${allocation.quantity} ${allocation.unit})`,
          )
          .join(", ")
      : "None";
}

function renderRecoverySection() {
  recoverySection.hidden = !currentSimulationResult;
  recoveryContent.hidden = !currentRecoveryResult;
  recoveryStateMessage.textContent = recoveryStatusMessage;
  if (!currentRecoveryResult) return;

  renderRecoveryPlanList();
  renderRejectedOptions();
  renderRecoveryComparison();
}

function handleGenerateRecoveryPlans() {
  recoveryError.hidden = true;
  if (!currentSimulationResult) {
    recoveryError.textContent = "Run a failure simulation before generating recovery plans.";
    recoveryError.hidden = false;
    return;
  }

  const result = generateRecoveryPlans(currentSimulationResult, {
    recoveryState: {
      ...currentRecoveryState,
      allocations: scenarioState.recoveryAllocations,
    },
  });
  if (!result.ok) {
    currentRecoveryResult = null;
    selectedRecoveryPlanId = null;
    recoveryContent.hidden = true;
    recoveryError.textContent = result.error.message;
    recoveryError.hidden = false;
    return;
  }

  currentRecoveryResult = result;
  selectedRecoveryPlanId = result.plans.length ? result.plans[0].id : null;
  currentRecoveryState.allocations = scenarioState.recoveryAllocations.map(
    (allocation) => ({ ...allocation }),
  );
  currentRecoveryComparison = createRecoveryComparison(
    currentSimulationResult,
    currentRecoveryState,
  );
  currentScenarioLabel =
    `${currentSimulationResult.initialFailure.departmentName}` +
    (currentSimulationResult.initialFailure.service
      ? ` · ${currentSimulationResult.initialFailure.service}`
      : "");
  scenarioState = recordScenarioPlanGeneration(
    scenarioState,
    result,
    currentScenarioLabel,
  );
  persistScenarioState();
  recoveryStatusMessage =
    `${result.plans.length} feasible plan${result.plans.length === 1 ? "" : "s"} generated; ` +
    `${result.unresolvedOperations.length} modeled operation${result.unresolvedOperations.length === 1 ? "" : "s"} unresolved.`;
  renderRecoverySection();
  renderAnalytics();
}

function handleRecoveryPlanSelection(event) {
  const button = event.target.closest("[data-recovery-plan-id]");
  if (!button) return;
  selectedRecoveryPlanId = button.dataset.recoveryPlanId;
  recoveryPlanList.querySelectorAll("[data-recovery-plan-id]").forEach((item) => {
    const isSelected = item.dataset.recoveryPlanId === selectedRecoveryPlanId;
    item.setAttribute("aria-pressed", String(isSelected));
    item.textContent = isSelected ? "Selected plan" : "Select this plan";
    item.closest(".recovery-plan-card").classList.toggle("is-selected", isSelected);
  });
  applyRecoveryButton.disabled = false;
}

function handleApplyRecoveryPlan() {
  recoveryError.hidden = true;
  const plan = currentRecoveryResult && currentRecoveryResult.ok &&
    currentRecoveryResult.plans.find((item) => item.id === selectedRecoveryPlanId);
  const recoveryState = {
    ...currentRecoveryState,
    allocations: scenarioState.recoveryAllocations.map((allocation) => ({ ...allocation })),
  };
  const result = applyRecoveryPlan(plan, recoveryState);
  if (!result.ok) {
    recoveryError.textContent = result.error.message;
    recoveryError.hidden = false;
    return;
  }

  const newAllocations = result.state.allocations
    .slice(recoveryState.allocations.length)
    .map((allocation) => ({ ...allocation, incidentId: null }));
  scenarioState = {
    ...scenarioState,
    recoveryAllocations: [...scenarioState.recoveryAllocations, ...newAllocations],
  };
  scenarioState = recordScenarioPlanApplication(
    scenarioState,
    plan,
    currentScenarioLabel,
  );
  currentRecoveryState = {
    ...result.state,
    allocations: scenarioState.recoveryAllocations.map((allocation) => ({ ...allocation })),
  };
  currentRecoveryComparison = createRecoveryComparison(
    currentSimulationResult,
    currentRecoveryState,
  );
  const refreshedPlans = generateRecoveryPlans(currentSimulationResult, {
    recoveryState: {
      ...currentRecoveryState,
      allocations: scenarioState.recoveryAllocations,
    },
  });
  currentRecoveryResult = refreshedPlans;
  selectedRecoveryPlanId =
    refreshedPlans.ok && refreshedPlans.plans.length
      ? currentRecoveryResult.plans[0].id
      : null;
  recoveryStatusMessage = refreshedPlans.ok
    ? `Applied "${plan.title}". Allocated resources are reserved; remaining options were recalculated.`
    : `Applied "${plan.title}", but remaining options could not be recalculated.`;
  renderRecoverySection();
  if (!refreshedPlans.ok) {
    recoveryError.textContent = refreshedPlans.error.message;
    recoveryError.hidden = false;
  }
  persistScenarioState();
  renderSummary();
  renderAnalytics();
  renderDepartments();
  renderDependencyNetwork();
}

function handleSimulationSubmit(event) {
  event.preventDefault();
  simulationError.hidden = true;
  recoveryError.hidden = true;
  simulationResults.hidden = true;
  currentSimulationResult = null;
  currentRecoveryState = {
    ...createRecoveryState(),
    allocations: scenarioState.recoveryAllocations.map((allocation) => ({ ...allocation })),
  };
  currentRecoveryResult = null;
  currentRecoveryComparison = null;
  selectedRecoveryPlanId = null;
  recoveryStatusMessage = "Failure scenario ready. Generate plans to review feasible recovery options.";

  const selectedOption = simulationTarget.selectedOptions[0];
  if (!selectedOption) {
    simulationError.textContent = "Select a failure scenario before running the simulation.";
    simulationError.hidden = false;
    renderRecoverySection();
    renderSummary();
    renderAnalytics();
    renderDepartments();
    renderDependencyNetwork();
    return;
  }

  const departmentId = selectedOption.dataset.departmentId || selectedOption.value;
  const service = selectedOption.dataset.service;
  const department = DEPARTMENTS.find((item) => item.id === departmentId);
  currentScenarioLabel = `${department.name}${service ? ` · ${service}` : ""}`;
  const result = simulateFailure(
    departmentId,
    service ? { service } : {},
  );

  if (!result.ok) {
    simulationError.textContent = result.error.message;
    simulationError.hidden = false;
    renderRecoverySection();
    renderSummary();
    renderAnalytics();
    renderDepartments();
    renderDependencyNetwork();
    return;
  }

  currentSimulationResult = result;
  currentRecoveryComparison = createRecoveryComparison(
    currentSimulationResult,
    currentRecoveryState,
  );
  selectedDepartmentId = departmentId;
  renderSimulationResults(result);
  renderRecoverySection();
  renderSummary();
  renderAnalytics();
  renderDepartments();
  renderDependencyNetwork();
}

function handleScenarioChange() {
  const selectedOption = simulationTarget.selectedOptions[0];
  if (!selectedOption) {
    return;
  }

  selectedDepartmentId =
    selectedOption.dataset.departmentId || selectedOption.value;
  currentSimulationResult = null;
  currentRecoveryState = {
    ...createRecoveryState(),
    allocations: scenarioState.recoveryAllocations.map((allocation) => ({ ...allocation })),
  };
  currentRecoveryResult = null;
  currentRecoveryComparison = null;
  selectedRecoveryPlanId = null;
  recoveryStatusMessage = "Run a failure simulation to start recovery planning.";
  simulationResults.hidden = true;
  simulationError.hidden = true;
  recoveryError.hidden = true;
  renderRecoverySection();
  renderSummary();
  renderAnalytics();
  renderDepartments();
  renderDependencyNetwork();
}

function populateIncidentForm() {
  const departmentOptions = DEPARTMENTS.map((department) => {
    const option = document.createElement("option");
    option.value = department.id;
    option.textContent = department.name;
    return option;
  });
  const failureTypeOptions = INCIDENT_FAILURE_TYPES.map((failureType) => {
    const option = document.createElement("option");
    option.value = failureType.value;
    option.textContent = failureType.label;
    return option;
  });

  incidentDepartment.replaceChildren(...departmentOptions);
  incidentFailureType.replaceChildren(...failureTypeOptions);
  syncIncidentDepartment();
}

function syncIncidentDepartment() {
  const selectedType = INCIDENT_FAILURE_TYPES.find(
    (failureType) => failureType.value === incidentFailureType.value,
  );
  if (selectedType?.service) {
    incidentDepartment.value = "infrastructure";
    incidentDepartment.disabled = true;
  } else {
    incidentDepartment.disabled = false;
  }
}

function refreshCurrentScenarioState() {
  if (!currentSimulationResult) return;

  currentRecoveryState = {
    ...currentRecoveryState,
    allocations: scenarioState.recoveryAllocations.map((allocation) => ({ ...allocation })),
  };
  currentRecoveryComparison = createRecoveryComparison(
    currentSimulationResult,
    currentRecoveryState,
  );
  if (currentRecoveryResult) {
    currentRecoveryResult = generateRecoveryPlans(currentSimulationResult, {
      recoveryState: currentRecoveryState,
    });
    selectedRecoveryPlanId =
      currentRecoveryResult.ok && currentRecoveryResult.plans.length
        ? currentRecoveryResult.plans[0].id
        : null;
    renderRecoverySection();
  }
}

async function handleIncidentFormSubmit(event) {
  event.preventDefault();
  incidentError.hidden = true;
  if (!backendReady) {
    incidentError.textContent = "The backend is not connected. Incident data was not saved.";
    incidentError.hidden = false;
    return;
  }

  const submitButton = incidentForm.querySelector('button[type="submit"]');
  const originalLabel = submitButton.textContent;
  submitButton.disabled = true;
  submitButton.textContent = "Recording…";
  incidentForm.setAttribute("aria-busy", "true");
  let createdIncidentId = null;
  try {
    const incident = await api.createIncident({
      departmentId: incidentDepartment.value,
      failureType: incidentFailureType.value,
      severity: document.querySelector("#incident-severity").value,
      description: document.querySelector("#incident-description").value,
    });
    createdIncidentId = incident.id;
    await api.simulateIncident(createdIncidentId);
    await synchronizeBackendState();
    incidentForm.reset();
    syncIncidentDepartment();
    showAppFeedback(
      `Incident ${createdIncidentId} recorded and its simulation was saved.`,
    );
    renderDashboard();
  } catch (error) {
    let refreshMessage = "";
    try {
      await synchronizeBackendState();
      renderDashboard();
    } catch (refreshError) {
      refreshMessage = ` Backend refresh failed: ${refreshError.message}`;
    }
    incidentError.textContent = createdIncidentId
      ? `Incident ${createdIncidentId} was created, but the next backend operation failed: ${error.message}.${refreshMessage}`
      : `${error.message}${refreshMessage}`;
    incidentError.hidden = false;
  } finally {
    incidentForm.removeAttribute("aria-busy");
    submitButton.disabled = !backendReady;
    submitButton.textContent = originalLabel;
  }
}

async function handleIncidentAction(event) {
  const button = event.target.closest("[data-incident-action]");
  if (!button) return;

  const { incidentAction: action, incidentId, planId } = button.dataset;
  if (
    action === "clear" &&
    !window.confirm("Clear this incident and its associated recovery activity?")
  ) {
    return;
  }

  if (!backendReady) {
    showAppFeedback("The backend is not connected. No incident changes were made.", true);
    return;
  }

  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = "Working…";
  let message = "Incident updated.";
  try {
    if (action === "simulate") {
      await api.simulateIncident(incidentId);
      message = "Incident simulation completed and saved to its record.";
    } else if (action === "generate") {
      const result = await api.generateRecoveryPlans(incidentId);
      const planCount = result.recoveryResult.plans.length;
      message = `${planCount} feasible recovery plan${planCount === 1 ? "" : "s"} generated for this incident.`;
    } else if (action === "apply") {
      await api.applyRecoveryPlan(incidentId, planId);
      message = `Recovery plan applied to incident ${incidentId}; resource reservations were updated.`;
    } else if (action === "resolve") {
      await api.resolveIncident(incidentId);
      message = `Incident ${incidentId} marked resolved.`;
    } else if (action === "clear") {
      await api.deleteIncident(incidentId);
      message = `Incident ${incidentId} and its associated recovery activity cleared.`;
    } else {
      return;
    }

    await synchronizeBackendState();
    renderDashboard();
    showAppFeedback(message);
  } catch (error) {
    let refreshMessage = "";
    try {
      await synchronizeBackendState();
      renderDashboard();
    } catch (refreshError) {
      refreshMessage = ` Backend refresh failed: ${refreshError.message}`;
    }
    showAppFeedback(`${error.message}${refreshMessage}`, true);
  } finally {
    if (button.isConnected) {
      button.disabled = !backendReady;
      button.textContent = originalLabel;
    }
  }
}

function renderDashboard() {
  renderSummary();
  renderIncidentHistory();
  renderAnalytics();
  renderDepartments();
  renderDependencyNetwork();
}

populateFailureScenarios();
populateIncidentForm();
if (loadedIncidentState.warning) {
  showAppFeedback(loadedIncidentState.warning, true);
}
simulationForm.addEventListener("submit", handleSimulationSubmit);
simulationTarget.addEventListener("change", handleScenarioChange);
incidentForm.addEventListener("submit", handleIncidentFormSubmit);
incidentFailureType.addEventListener("change", syncIncidentDepartment);
incidentHistory.addEventListener("click", handleIncidentAction);
resetDemoButton.addEventListener("click", resetDemo);
document.querySelector("#export-report").addEventListener("click", exportIncidentReport);
document
  .querySelector("#generate-recovery-plans")
  .addEventListener("click", handleGenerateRecoveryPlans);
recoveryPlanList.addEventListener("click", handleRecoveryPlanSelection);
applyRecoveryButton.addEventListener("click", handleApplyRecoveryPlan);
renderDashboard();
initializeBackend();
