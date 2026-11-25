/**
 * Dependency lookup and rule-based failure simulation.
 * Results use simulated sample data and are not real-world predictions.
 *
 * @typedef {import("./data.js").Incident} Incident
 * @typedef {import("./data.js").Department} Department
 * @typedef {import("./data.js").Dependency} Dependency
 * @typedef {Object} PropagationStep
 * @property {string} departmentId Newly affected department.
 * @property {string} departmentName Display name of the affected department.
 * @property {string} triggeredByDepartmentId Previously failed department that caused this step.
 * @property {number} depth One for direct effects; higher values are indirect effects.
 * @property {string} relationship Explanation from the dependency link.
 * @property {string | null} service Shared service involved, if specified.
 *
 * @typedef {Object} SimulationRequest
 * @property {Incident} incident The incident to evaluate in a future phase.
 * @property {Department[]} departments Department records for ID lookup.
 * @property {Dependency[]} dependencies Directed upstream-to-downstream links.
 *
 * @typedef {Object} SimulationResult
 * @property {{departmentId: string, departmentName: string, service: string | null}} initialFailure
 * @property {string[]} directlyAffectedDepartmentIds Departments one link from the initial failure.
 * @property {string[]} indirectlyAffectedDepartmentIds Departments reached after the first link.
 * @property {string[]} secondaryAffectedDepartmentIds All affected departments except the initial failure.
 * @property {string[]} affectedDepartmentIds All affected departments, including the initial failure.
 * @property {PropagationStep[]} propagationOrder Ordered direct and indirect effects.
 * @property {{directlyAffectedCount: number, indirectlyAffectedCount: number, totalAffectedCount: number, institutionalImpactPercent: number, severity: string, severityRationale: string}} impact
 */

import { DEPARTMENTS, DEPENDENCIES } from "./data.js";

function createGraphIndex(departments, dependencies) {
  if (!Array.isArray(departments) || !Array.isArray(dependencies)) {
    return {
      ok: false,
      error: {
        code: "INVALID_GRAPH_DATA",
        message: "Departments and dependencies must both be arrays.",
      },
    };
  }

  const departmentById = new Map();
  const errors = [];
  const duplicateDepartmentIds = new Set();

  departments.forEach((department, index) => {
    if (!department || typeof department.id !== "string" || department.id.trim() === "") {
      errors.push({
        code: "INVALID_DEPARTMENT_ID",
        message: `Department at index ${index} has no valid ID.`,
      });
      return;
    }

    if (departmentById.has(department.id)) {
      duplicateDepartmentIds.add(department.id);
      return;
    }
    departmentById.set(department.id, department);
  });

  const uniqueRelationships = [];
  const relationshipKeys = new Set();
  const duplicateRelationships = [];

  dependencies.forEach((dependency, index) => {
    if (
      !dependency ||
      typeof dependency.upstreamDepartmentId !== "string" ||
      typeof dependency.downstreamDepartmentId !== "string"
    ) {
      errors.push({
        code: "INVALID_RELATIONSHIP",
        message: `Dependency at index ${index} must have upstream and downstream department IDs.`,
      });
      return;
    }

    const { upstreamDepartmentId, downstreamDepartmentId } = dependency;
    const missingIds = [
      upstreamDepartmentId,
      downstreamDepartmentId,
    ].filter((id) => !departmentById.has(id));

    if (missingIds.length > 0) {
      errors.push({
        code: "UNKNOWN_DEPARTMENT_REFERENCE",
        message: `Dependency at index ${index} references an unknown department.`,
        departmentIds: missingIds,
      });
      return;
    }

    const relationshipKey = JSON.stringify([
      upstreamDepartmentId,
      downstreamDepartmentId,
    ]);
    if (relationshipKeys.has(relationshipKey)) {
      duplicateRelationships.push(relationshipKey);
      return;
    }

    relationshipKeys.add(relationshipKey);
    uniqueRelationships.push(dependency);
  });

  if (duplicateDepartmentIds.size > 0) {
    errors.push({
      code: "DUPLICATE_DEPARTMENT_ID",
      message: "Department IDs must be unique.",
      departmentIds: Array.from(duplicateDepartmentIds),
    });
  }

  if (errors.length > 0) {
    return {
      ok: false,
      error: {
        code: "INVALID_GRAPH_DATA",
        message: "The department dependency graph contains invalid data.",
        details: errors,
      },
    };
  }

  const outgoingById = new Map();
  uniqueRelationships.forEach((dependency) => {
    const outgoing = outgoingById.get(dependency.upstreamDepartmentId) || [];
    outgoing.push(dependency);
    outgoingById.set(dependency.upstreamDepartmentId, outgoing);
  });

  return {
    ok: true,
    departmentById,
    relationships: uniqueRelationships,
    outgoingById,
    duplicateRelationships,
  };
}

function getGraphForDepartment(departmentId, departments, dependencies) {
  const graph = createGraphIndex(departments, dependencies);
  if (!graph.ok) {
    return graph;
  }

  if (typeof departmentId !== "string" || departmentId.trim() === "") {
    return {
      ok: false,
      error: {
        code: "INVALID_DEPARTMENT_ID",
        message: "A non-empty department ID is required.",
      },
    };
  }

  const department = graph.departmentById.get(departmentId);
  if (!department) {
    return {
      ok: false,
      error: {
        code: "UNKNOWN_DEPARTMENT",
        message: `No department exists with ID "${departmentId}".`,
        departmentId,
      },
    };
  }

  return { ...graph, department };
}

function createRelationshipResult(dependency, department) {
  return {
    department,
    upstreamDepartmentId: dependency.upstreamDepartmentId,
    downstreamDepartmentId: dependency.downstreamDepartmentId,
    service: dependency.service || null,
    relationship: dependency.relationship,
  };
}

function getImpactSeverity(totalAffectedCount, departmentCount) {
  const impactPercent = (totalAffectedCount / departmentCount) * 100;

  if (impactPercent >= 50) {
    return {
      severity: "critical",
      rationale: "Critical: at least 50% of modeled departments are affected.",
    };
  }
  if (impactPercent >= 25) {
    return {
      severity: "high",
      rationale: "High: at least 25% but less than 50% of modeled departments are affected.",
    };
  }
  if (impactPercent >= 10) {
    return {
      severity: "medium",
      rationale: "Medium: at least 10% but less than 25% of modeled departments are affected.",
    };
  }

  return {
    severity: "low",
    rationale: "Low: fewer than 10% of modeled departments are affected.",
  };
}

/**
 * Simulate a department or shared-service failure by following downstream
 * dependency links with a queue-based breadth-first search.
 *
 * When `options.service` is supplied, only matching links from the initial
 * department are used for the first step. Later propagation follows all links.
 * The dependency data is read-only; the function builds local traversal state.
 *
 * @param {string} initialFailedDepartmentId
 * @param {{service?: string, departments?: Department[], dependencies?: Dependency[]}} [options]
 * @returns {SimulationResult | {ok: false, error: Object}}
 */
export function simulateFailure(initialFailedDepartmentId, options = {}) {
  if (!options || typeof options !== "object" || Array.isArray(options)) {
    return {
      ok: false,
      error: {
        code: "INVALID_SIMULATION_OPTIONS",
        message: "Simulation options must be an object.",
      },
    };
  }

  const {
    service = null,
    departments = DEPARTMENTS,
    dependencies = DEPENDENCIES,
  } = options;
  const graph = getGraphForDepartment(
    initialFailedDepartmentId,
    departments,
    dependencies,
  );
  if (!graph.ok) {
    return graph;
  }

  if (service !== null && (typeof service !== "string" || service.trim() === "")) {
    return {
      ok: false,
      error: {
        code: "INVALID_SERVICE",
        message: "A service filter must be a non-empty string.",
      },
    };
  }

  const initialOutgoing = graph.outgoingById.get(initialFailedDepartmentId) || [];
  if (service && !initialOutgoing.some((dependency) => dependency.service === service)) {
    return {
      ok: false,
      error: {
        code: "UNKNOWN_SERVICE_FOR_DEPARTMENT",
        message: `Department "${initialFailedDepartmentId}" does not provide the "${service}" service in this graph.`,
        departmentId: initialFailedDepartmentId,
        service,
      },
    };
  }

  const visited = new Set([initialFailedDepartmentId]);
  const queue = [{ departmentId: initialFailedDepartmentId, depth: 0 }];
  const propagationOrder = [];

  for (let queueIndex = 0; queueIndex < queue.length; queueIndex += 1) {
    const current = queue[queueIndex];
    const outgoing = graph.outgoingById.get(current.departmentId) || [];
    const applicableRelationships =
      current.depth === 0 && service
        ? outgoing.filter((dependency) => dependency.service === service)
        : outgoing;

    applicableRelationships.forEach((dependency) => {
      const downstreamId = dependency.downstreamDepartmentId;
      if (visited.has(downstreamId)) {
        return;
      }

      visited.add(downstreamId);
      const downstreamDepartment = graph.departmentById.get(downstreamId);
      const depth = current.depth + 1;
      propagationOrder.push({
        departmentId: downstreamId,
        departmentName: downstreamDepartment.name,
        triggeredByDepartmentId: current.departmentId,
        depth,
        relationship: dependency.relationship,
        service: dependency.service || null,
      });
      queue.push({ departmentId: downstreamId, depth });
    });
  }

  const directlyAffectedDepartmentIds = propagationOrder
    .filter((step) => step.depth === 1)
    .map((step) => step.departmentId);
  const indirectlyAffectedDepartmentIds = propagationOrder
    .filter((step) => step.depth > 1)
    .map((step) => step.departmentId);
  const secondaryAffectedDepartmentIds = propagationOrder.map(
    (step) => step.departmentId,
  );
  const affectedDepartmentIds = [
    initialFailedDepartmentId,
    ...secondaryAffectedDepartmentIds,
  ];
  const totalAffectedCount = affectedDepartmentIds.length;
  const impactPercent =
    (totalAffectedCount / graph.departmentById.size) * 100;
  const impactSeverity = getImpactSeverity(
    totalAffectedCount,
    graph.departmentById.size,
  );

  return {
    ok: true,
    initialFailure: {
      departmentId: initialFailedDepartmentId,
      departmentName: graph.department.name,
      service,
    },
    directlyAffectedDepartmentIds,
    indirectlyAffectedDepartmentIds,
    secondaryAffectedDepartmentIds,
    affectedDepartmentIds,
    propagationOrder,
    impact: {
      directlyAffectedCount: directlyAffectedDepartmentIds.length,
      indirectlyAffectedCount: indirectlyAffectedDepartmentIds.length,
      totalAffectedCount,
      institutionalImpactPercent: Number(impactPercent.toFixed(1)),
      severity: impactSeverity.severity,
      severityRationale: impactSeverity.rationale,
    },
  };
}

/**
 * Check department IDs and dependency references, and report repeated links.
 * Duplicate directed department pairs are identified by their upstream and
 * downstream IDs, regardless of their descriptive label.
 *
 * @param {Department[]} [departments=DEPARTMENTS]
 * @param {Dependency[]} [dependencies=DEPENDENCIES]
 * @returns {{valid: boolean, departmentCount: number, relationshipCount: number, duplicateRelationships: string[], errors: Object[]}}
 */
export function validateDepartmentGraph(
  departments = DEPARTMENTS,
  dependencies = DEPENDENCIES,
) {
  const graph = createGraphIndex(departments, dependencies);
  if (!graph.ok) {
    return {
      valid: false,
      departmentCount: Array.isArray(departments) ? departments.length : 0,
      relationshipCount: 0,
      duplicateRelationships: [],
      errors: graph.error.details || [graph.error],
    };
  }

  const duplicateErrors = graph.duplicateRelationships.map((relationship) => ({
    code: "DUPLICATE_RELATIONSHIP",
    message: "Only one directed relationship is allowed for each department pair.",
    relationship,
  }));

  return {
    valid: graph.duplicateRelationships.length === 0,
    departmentCount: graph.departmentById.size,
    relationshipCount: graph.relationships.length,
    duplicateRelationships: graph.duplicateRelationships,
    errors: duplicateErrors,
  };
}

/**
 * Return the upstream departments that the selected department directly relies
 * on. The `dependencies` array contains edge details and the upstream record.
 *
 * @param {string} departmentId
 * @param {Department[]} [departments=DEPARTMENTS]
 * @param {Dependency[]} [dependencies=DEPENDENCIES]
 * @returns {{ok: boolean, department?: Department, dependencies?: Object[], ignoredDuplicateRelationships?: number, error?: Object}}
 */
export function getDirectDependencies(
  departmentId,
  departments = DEPARTMENTS,
  dependencies = DEPENDENCIES,
) {
  const graph = getGraphForDepartment(departmentId, departments, dependencies);
  if (!graph.ok) {
    return graph;
  }

  const directDependencies = graph.relationships
    .filter((dependency) => dependency.downstreamDepartmentId === departmentId)
    .map((dependency) => createRelationshipResult(
      dependency,
      graph.departmentById.get(dependency.upstreamDepartmentId),
    ));

  return {
    ok: true,
    department: graph.department,
    dependencies: directDependencies,
    ignoredDuplicateRelationships: graph.duplicateRelationships.length,
  };
}

/**
 * Return departments directly downstream from the selected department.
 *
 * @param {string} departmentId
 * @param {Department[]} [departments=DEPARTMENTS]
 * @param {Dependency[]} [dependencies=DEPENDENCIES]
 * @returns {{ok: boolean, department?: Department, dependents?: Object[], ignoredDuplicateRelationships?: number, error?: Object}}
 */
export function getDirectDependents(
  departmentId,
  departments = DEPARTMENTS,
  dependencies = DEPENDENCIES,
) {
  const graph = getGraphForDepartment(departmentId, departments, dependencies);
  if (!graph.ok) {
    return graph;
  }

  const directDependents = graph.relationships
    .filter((dependency) => dependency.upstreamDepartmentId === departmentId)
    .map((dependency) => createRelationshipResult(
      dependency,
      graph.departmentById.get(dependency.downstreamDepartmentId),
    ));

  return {
    ok: true,
    department: graph.department,
    dependents: directDependents,
    ignoredDuplicateRelationships: graph.duplicateRelationships.length,
  };
}

/**
 * Return every department reachable downstream from the selected department.
 * A visited set ensures each department appears once and cycles terminate.
 *
 * @param {string} departmentId
 * @param {Department[]} [departments=DEPARTMENTS]
 * @param {Dependency[]} [dependencies=DEPENDENCIES]
 * @returns {{ok: boolean, department?: Department, dependents?: Object[], ignoredDuplicateRelationships?: number, error?: Object}}
 */
export function getDependentDepartments(
  departmentId,
  departments = DEPARTMENTS,
  dependencies = DEPENDENCIES,
) {
  const graph = getGraphForDepartment(departmentId, departments, dependencies);
  if (!graph.ok) {
    return graph;
  }

  const visited = new Set([departmentId]);
  const queue = [{ id: departmentId, depth: 0, path: [departmentId] }];
  const dependents = [];

  for (let queueIndex = 0; queueIndex < queue.length; queueIndex += 1) {
    const current = queue[queueIndex];
    const outgoing = graph.outgoingById.get(current.id) || [];

    outgoing.forEach((dependency) => {
      const downstreamId = dependency.downstreamDepartmentId;
      if (visited.has(downstreamId)) {
        return;
      }

      visited.add(downstreamId);
      const depth = current.depth + 1;
      const path = [...current.path, downstreamId];
      dependents.push({
        department: graph.departmentById.get(downstreamId),
        depth,
        path,
        relationship: dependency.relationship,
        service: dependency.service || null,
      });
      queue.push({ id: downstreamId, depth, path });
    });
  }

  return {
    ok: true,
    department: graph.department,
    dependents,
    ignoredDuplicateRelationships: graph.duplicateRelationships.length,
  };
}

/**
 * Build the agreed input shape for a future failure simulation.
 *
 * @param {Incident} incident
 * @param {Department[]} departments
 * @param {Dependency[]} dependencies
 * @returns {SimulationRequest}
 */
export function createSimulationRequest(incident, departments, dependencies) {
  return { incident, departments, dependencies };
}
