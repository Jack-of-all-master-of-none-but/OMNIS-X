const API_BASE_URL = "http://127.0.0.1:5000/api";

export class ApiError extends Error {
  constructor(message, status = 0, code = "API_ERROR") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function request(path, options = {}) {
  const headers = { Accept: "application/json" };
  const fetchOptions = {
    method: options.method || "GET",
    headers,
  };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    fetchOptions.body = JSON.stringify(options.body);
  }

  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, fetchOptions);
  } catch (error) {
    throw new ApiError(
      `Could not reach the OMNIS-X backend at ${API_BASE_URL}: ${error.message}`,
      0,
      "BACKEND_UNAVAILABLE",
    );
  }

  const responseText = await response.text();
  let data = null;
  if (responseText) {
    try {
      data = JSON.parse(responseText);
    } catch (error) {
      throw new ApiError(
        `The backend returned invalid JSON (HTTP ${response.status}).`,
        response.status,
        "INVALID_API_RESPONSE",
      );
    }
  }

  if (!response.ok) {
    throw new ApiError(
      data?.message || `Backend request failed with HTTP ${response.status}.`,
      response.status,
      data?.code || "API_REQUEST_FAILED",
    );
  }
  if (data === null) {
    throw new ApiError(
      `The backend returned an empty response (HTTP ${response.status}).`,
      response.status,
      "EMPTY_API_RESPONSE",
    );
  }
  return data;
}

const incidentPath = (incidentId) =>
  `/incidents/${encodeURIComponent(incidentId)}`;
const recoveryPath = (incidentId) =>
  `/recovery/${encodeURIComponent(incidentId)}`;
const simulationPath = (incidentId) =>
  `/simulations/${encodeURIComponent(incidentId)}`;

export const api = {
  getHealth: () => request("/health"),
  getDepartments: () => request("/departments"),
  getIncidents: () => request("/incidents"),
  createIncident: (incident) =>
    request("/incidents", { method: "POST", body: incident }),
  resolveIncident: (incidentId) =>
    request(`${incidentPath(incidentId)}/resolve`, { method: "PATCH" }),
  deleteIncident: (incidentId) =>
    request(incidentPath(incidentId), { method: "DELETE" }),
  simulateIncident: (incidentId) =>
    request("/simulations", {
      method: "POST",
      body: { incidentId },
    }),
  getSimulation: (incidentId) => request(simulationPath(incidentId)),
  getRecovery: (incidentId) => request(recoveryPath(incidentId)),
  generateRecoveryPlans: (incidentId) =>
    request(`${recoveryPath(incidentId)}/plans`, { method: "POST" }),
  applyRecoveryPlan: (incidentId, planId) =>
    request(`${recoveryPath(incidentId)}/apply`, {
      method: "POST",
      body: { planId },
    }),
  getAnalyticsOverview: () => request("/analytics/overview"),
  getAnalyticsDepartments: () => request("/analytics/departments"),
  getAnalyticsRecovery: () => request("/analytics/recovery"),
};
