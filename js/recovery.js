import { DEPARTMENTS, OPERATIONS, RESOURCES } from "./data.js";

const MAX_BUNDLES_PER_REQUIREMENT = 16;
const MAX_COMBINED_SEARCH_STATES = 8000;

/**
 * Recovery allocations are separate from the sample resource catalog.
 * @typedef {{ resourceId: string, operationId: string, requirementId: string, quantity: number, unit: string }} RecoveryAllocation
 * @typedef {{ allocations: RecoveryAllocation[], restoredOperationIds: string[], appliedPlanIds: string[] }} RecoveryState
 */

function failure(code, message, extra = {}) {
  return { ok: false, error: { code, message }, ...extra };
}

function unique(values) {
  return [...new Set(values)];
}

function isValidSimulationResult(simulationResult) {
  if (
    !simulationResult ||
    simulationResult.ok !== true ||
    !simulationResult.initialFailure ||
    typeof simulationResult.initialFailure.departmentId !== "string" ||
    !simulationResult.initialFailure.departmentId ||
    !Array.isArray(simulationResult.affectedDepartmentIds) ||
    simulationResult.affectedDepartmentIds.length === 0
  ) {
    return false;
  }

  const affectedDepartmentIds = simulationResult.affectedDepartmentIds;
  return (
    affectedDepartmentIds.every((departmentId) => typeof departmentId === "string" && departmentId) &&
    new Set(affectedDepartmentIds).size === affectedDepartmentIds.length &&
    affectedDepartmentIds.includes(simulationResult.initialFailure.departmentId)
  );
}

function isOptionsObject(options) {
  return Boolean(options) && typeof options === "object" && !Array.isArray(options);
}

function getRequirementName(requirement) {
  if (requirement.name) return requirement.name;
  return String(requirement.id || "resource requirement")
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function getAllocatedQuantity(resourceId, allocations) {
  return allocations
    .filter((allocation) => allocation.resourceId === resourceId)
    .reduce((total, allocation) => total + Number(allocation.quantity || 0), 0);
}

function hasAllocation(resourceId, allocations) {
  return allocations.some((allocation) => allocation.resourceId === resourceId);
}

/**
 * Check whether the requested amount fits within a resource's unused availability.
 * Resources with an existing allocation are kept reserved for that operation.
 */
export function validateResourceAvailability(resource, requiredQuantity, allocations = []) {
  const reasons = [];

  if (!Array.isArray(allocations)) {
    return { valid: false, reasons: ["Allocations must be an array."] };
  }
  if (!resource || typeof resource.id !== "string") {
    return { valid: false, reasons: ["Resource was not found."] };
  }
  if (!Number.isFinite(requiredQuantity) || requiredQuantity <= 0) {
    return { valid: false, reasons: ["Requested quantity must be greater than zero."] };
  }
  if (resource.status === "unavailable") {
    reasons.push("Resource is marked unavailable.");
  }
  if (!Number.isFinite(resource.available) || resource.available <= 0) {
    reasons.push("Resource has no available capacity.");
  } else if (resource.available < requiredQuantity) {
    reasons.push(
      `Only ${resource.available} ${resource.unit || "units"} are available; ${requiredQuantity} are required.`,
    );
  }
  if (hasAllocation(resource.id, allocations)) {
    const remaining = Math.max(0, resource.available - getAllocatedQuantity(resource.id, allocations));
    reasons.push(`Resource is already allocated (${remaining} ${resource.unit || "units"} remain).`);
  }

  return { valid: reasons.length === 0, reasons };
}

/** Check the resource's total capacity, independently of current availability. */
export function validateResourceCapacity(resource, requiredQuantity) {
  const reasons = [];
  if (!resource || !Number.isFinite(resource.capacity)) {
    return { valid: false, reasons: ["Resource capacity is not defined."] };
  }
  if (!Number.isFinite(requiredQuantity) || requiredQuantity <= 0) {
    return { valid: false, reasons: ["Requested quantity must be greater than zero."] };
  }
  if (resource.capacity < requiredQuantity) {
    reasons.push(
      `Capacity is ${resource.capacity} ${resource.unit || "units"}; ${requiredQuantity} are required.`,
    );
  }
  return { valid: reasons.length === 0, reasons };
}

/** Check whether a resource supports the operation's requested compatibility key. */
export function validateOperationCompatibility(resource, requirement) {
  if (!resource || !requirement || !Array.isArray(resource.compatibleOperations)) {
    return { valid: false, reasons: ["Resource compatibility information is missing."] };
  }
  const operationKey = requirement.compatibleOperation || requirement.operation;
  const compatible =
    typeof operationKey === "string" && resource.compatibleOperations.includes(operationKey);
  return {
    valid: compatible,
    reasons: compatible
      ? []
      : [`Resource is not compatible with ${operationKey || "the requested operation"}.`],
  };
}

/**
 * Validate all resource assignments for one operation.
 * Assignment shape: { operationId, requirementId, resourceId, quantity }.
 */
export function validateOperationRequirements(
  operation,
  assignments,
  resources = RESOURCES,
  allocations = [],
  unavailableResourceIds = [],
) {
  const reasons = [];
  if (!operation || !Array.isArray(operation.requirements) || operation.requirements.length === 0) {
    return { valid: false, reasons: ["Operation has no defined resource requirements."] };
  }
  if (
    !Array.isArray(assignments) ||
    !Array.isArray(resources) ||
    !Array.isArray(allocations) ||
    !Array.isArray(unavailableResourceIds)
  ) {
    return {
      valid: false,
      reasons: ["Assignments, resources, allocations, and unavailable resource IDs must be arrays."],
    };
  }

  const resourcesById = new Map(resources.map((resource) => [resource.id, resource]));
  const quantitiesByRequirement = new Map();
  const assignedResourceIds = new Set();
  const requirementsById = new Map(operation.requirements.map((requirement) => [requirement.id, requirement]));

  for (const assignment of assignments.filter((item) => item.operationId === operation.id)) {
    const requirement = requirementsById.get(assignment.requirementId);
    const resource = resourcesById.get(assignment.resourceId);

    if (!requirement) {
      reasons.push(`Assignment refers to an unknown requirement: ${assignment.requirementId}.`);
      continue;
    }
    if (!resource) {
      reasons.push(`Resource was not found: ${assignment.resourceId}.`);
      continue;
    }
    if (assignedResourceIds.has(resource.id)) {
      reasons.push(`Resource ${resource.name} is assigned more than once.`);
    }
    assignedResourceIds.add(resource.id);

    if (unavailableResourceIds.includes(resource.id)) {
      reasons.push(`${resource.name} is unavailable for this recovery.`);
    }
    const compatibility = validateOperationCompatibility(resource, requirement);
    reasons.push(...compatibility.reasons);

    const quantity = Number(assignment.quantity);
    const availability = validateResourceAvailability(resource, quantity, allocations);
    const capacity = validateResourceCapacity(resource, quantity);
    reasons.push(...availability.reasons, ...capacity.reasons);

    if (assignment.unit && assignment.unit !== requirement.unit) {
      reasons.push(`${resource.name} uses ${assignment.unit}, not ${requirement.unit}.`);
    }
    quantitiesByRequirement.set(
      requirement.id,
      (quantitiesByRequirement.get(requirement.id) || 0) + (Number.isFinite(quantity) ? quantity : 0),
    );
  }

  for (const requirement of operation.requirements) {
    const provided = quantitiesByRequirement.get(requirement.id) || 0;
    if (provided < requirement.demand) {
      reasons.push(
        `${getRequirementName(requirement)} needs ${requirement.demand} ${requirement.unit}; only ${provided} are assigned.`,
      );
    }
  }

  return { valid: reasons.length === 0, reasons: unique(reasons) };
}

/** Create an independent, empty recovery state. */
export function createRecoveryState() {
  return { allocations: [], restoredOperationIds: [], appliedPlanIds: [] };
}

function validateCatalog(operations, resources) {
  const errors = [];
  const resourceIds = new Set();
  const operationIds = new Set();

  for (const resource of resources) {
    if (!resource || typeof resource.id !== "string" || !resource.id) {
      errors.push("Each resource must have a unique ID.");
      continue;
    }
    if (resourceIds.has(resource.id)) errors.push(`Duplicate resource ID: ${resource.id}.`);
    resourceIds.add(resource.id);
    if (!Number.isFinite(resource.capacity) || resource.capacity < 0) {
      errors.push(`${resource.name || resource.id} has invalid capacity.`);
    }
    if (
      !Number.isFinite(resource.available) ||
      resource.available < 0 ||
      resource.available > resource.capacity
    ) {
      errors.push(`${resource.name || resource.id} has invalid available capacity.`);
    }
    if (!Array.isArray(resource.compatibleOperations)) {
      errors.push(`${resource.name || resource.id} has no compatibility list.`);
    }
  }

  for (const operation of operations) {
    if (!operation || typeof operation.id !== "string" || !operation.id) {
      errors.push("Each operation must have a unique ID.");
      continue;
    }
    if (operationIds.has(operation.id)) errors.push(`Duplicate operation ID: ${operation.id}.`);
    operationIds.add(operation.id);
    if (!Array.isArray(operation.requirements) || operation.requirements.length === 0) {
      errors.push(`${operation.name || operation.id} has no resource requirements.`);
      continue;
    }
    for (const requirement of operation.requirements) {
      if (
        !requirement.id ||
        !requirement.resourceType ||
        !(requirement.compatibleOperation || requirement.operation) ||
        !Number.isFinite(requirement.demand) ||
        requirement.demand <= 0 ||
        !requirement.unit
      ) {
        errors.push(`${operation.name || operation.id} has an invalid requirement.`);
      }
    }
  }
  return errors;
}

function makeRejection(operation, requirement, resource, reasons, extra = {}) {
  return {
    id: `${operation.id}:${requirement.id}:${resource ? resource.id : "no-resource"}`,
    operationId: operation.id,
    operationName: operation.name,
    requirementId: requirement.id,
    requirementName: getRequirementName(requirement),
    resourceId: resource ? resource.id : null,
    resourceName: resource ? resource.name : "No matching resource",
    required: requirement.demand,
    unit: requirement.unit,
    feasible: false,
    reasons: unique(reasons),
    ...extra,
  };
}

function getRequirementBundles(
  operation,
  requirement,
  resources,
  allocations,
  unavailableResourceIds,
  primaryResourceIds,
  rejectedOptions,
) {
  const candidates = [];
  const typedResources = resources.filter((resource) => resource.type === requirement.resourceType);

  for (const resource of typedResources) {
    const reasons = [];
    if (primaryResourceIds.has(resource.id)) {
      reasons.push("This is the operation's failed primary resource.");
    }
    reasons.push(...validateOperationCompatibility(resource, requirement).reasons);
    if (unavailableResourceIds.includes(resource.id)) {
      reasons.push("Resource is already marked unavailable.");
    }
    reasons.push(...validateResourceAvailability(resource, 1, allocations).reasons);

    if (reasons.length) {
      rejectedOptions.push(makeRejection(operation, requirement, resource, reasons));
      continue;
    }

    const usableCapacity = Math.max(
      0,
      Math.min(resource.capacity, resource.available) - getAllocatedQuantity(resource.id, allocations),
    );
    if (usableCapacity <= 0) {
      rejectedOptions.push(makeRejection(operation, requirement, resource, ["No usable capacity remains."]));
      continue;
    }
    candidates.push({ resource, usableCapacity });

    if (usableCapacity < requirement.demand) {
      rejectedOptions.push(
        makeRejection(
          operation,
          requirement,
          resource,
          [
            `Insufficient capacity on its own: ${usableCapacity} of ${requirement.demand} ${requirement.unit} required.`,
          ],
          { availableCapacity: usableCapacity },
        ),
      );
    }
  }

  if (!typedResources.length) {
    rejectedOptions.push(
      makeRejection(operation, requirement, null, [`No ${requirement.resourceType} resources are defined.`]),
    );
  }

  const bundles = [];
  const selected = [];
  const seenBundles = new Set();

  function collectBundles(startIndex, totalCapacity) {
    if (totalCapacity >= requirement.demand) {
      const sorted = [...selected].sort((a, b) => a.resource.id.localeCompare(b.resource.id));
      const key = sorted.map((item) => `${item.resource.id}:${item.quantity}`).join("|");
      if (!seenBundles.has(key)) {
        seenBundles.add(key);
        bundles.push(
          sorted.map((item) => ({
            operationId: operation.id,
            operationName: operation.name,
            requirementId: requirement.id,
            requirementName: getRequirementName(requirement),
            resourceId: item.resource.id,
            resourceName: item.resource.name,
            quantity: item.quantity,
            unit: requirement.unit,
            capacity: item.resource.capacity,
            availableAtGeneration: item.resource.available,
          })),
        );
      }
      return;
    }
    if (bundles.length >= MAX_BUNDLES_PER_REQUIREMENT) return;

    for (let index = startIndex; index < candidates.length; index += 1) {
      const candidate = candidates[index];
      const quantity = Math.min(candidate.usableCapacity, requirement.demand - totalCapacity);
      if (quantity <= 0) continue;
      selected.push({ resource: candidate.resource, quantity });
      collectBundles(index + 1, totalCapacity + quantity);
      selected.pop();
      if (bundles.length >= MAX_BUNDLES_PER_REQUIREMENT) return;
    }
  }

  collectBundles(0, 0);
  const totalCompatibleCapacity = candidates.reduce((total, item) => total + item.usableCapacity, 0);
  if (!bundles.length && candidates.length) {
    rejectedOptions.push(
      makeRejection(
        operation,
        requirement,
        null,
        [
          `Combined compatible capacity is ${totalCompatibleCapacity} of ${requirement.demand} ${requirement.unit} required.`,
        ],
        { availableCapacity: totalCompatibleCapacity },
      ),
    );
  }
  return { bundles, totalCompatibleCapacity };
}

function combineRequirementBundles(operation, bundlesByRequirement) {
  let combinations = [[]];

  for (const requirementBundles of bundlesByRequirement) {
    const nextCombinations = [];
    for (const existing of combinations) {
      const usedResources = new Set(existing.map((assignment) => assignment.resourceId));
      for (const bundle of requirementBundles) {
        if (bundle.some((assignment) => usedResources.has(assignment.resourceId))) continue;
        nextCombinations.push([...existing, ...bundle]);
      }
    }
    combinations = nextCombinations;
    if (!combinations.length) break;
  }

  return combinations.map((assignments) => ({
    operation,
    assignments: assignments.map((assignment) => ({ ...assignment })),
  }));
}

function operationPriority(operation) {
  const priority = String(operation.priority || "").toLowerCase();
  if (priority === "critical") return 4;
  if (priority === "high") return 3;
  if (priority === "medium") return 2;
  return 1;
}

function getAffectedOperations(simulationResult, operations) {
  const affectedDepartmentIds = new Set(simulationResult.affectedDepartmentIds);
  const initialFailure = simulationResult.initialFailure || {};
  return operations.filter((operation) => {
    if (!affectedDepartmentIds.has(operation.departmentId)) return false;
    if (
      initialFailure.service &&
      operation.departmentId === initialFailure.departmentId
    ) {
      return operation.service === initialFailure.service;
    }
    return true;
  });
}

function buildPlan(operationOptions, allAffectedOperations, resources) {
  const operations = operationOptions.map((option) => option.operation);
  const assignments = operationOptions.flatMap((option) => option.assignments);
  const operationIds = operations.map((operation) => operation.id);
  const selectedIds = new Set(operationIds);
  const resourcesById = new Map(resources.map((resource) => [resource.id, resource]));
  const resourceIds = unique(assignments.map((assignment) => assignment.resourceId));

  const restoredDepartmentIds = unique(
    operations
      .filter((operation) => {
        const sameDepartment = allAffectedOperations.filter(
          (candidate) => candidate.departmentId === operation.departmentId,
        );
        return sameDepartment.every((candidate) => selectedIds.has(candidate.id));
      })
      .map((operation) => operation.departmentId),
  );
  const capacityComparisons = operations.flatMap((operation) =>
    operation.requirements.map((requirement) => ({
      operationId: operation.id,
      operationName: operation.name,
      requirementId: requirement.id,
      requirementName: getRequirementName(requirement),
      required: requirement.demand,
      provided: assignments
        .filter(
          (assignment) =>
            assignment.operationId === operation.id && assignment.requirementId === requirement.id,
        )
        .reduce((total, assignment) => total + assignment.quantity, 0),
      unit: requirement.unit,
    })),
  );
  const availabilityScores = resourceIds.map((resourceId) => {
    const resource = resourcesById.get(resourceId);
    return resource.capacity > 0 ? (resource.available / resource.capacity) * 100 : 0;
  });
  const resourceAvailabilityScore = availabilityScores.length
    ? availabilityScores.reduce((total, score) => total + score, 0) / availabilityScores.length
    : 0;
  const capacitySatisfactionScore = capacityComparisons.length
    ? (capacityComparisons.filter((item) => item.provided >= item.required).length /
        capacityComparisons.length) *
      100
    : 0;
  const priorityScore = operations.reduce((total, operation) => total + operationPriority(operation), 0);
  const estimatedRecoveryMinutes = operations.reduce(
    (total, operation) => total + (Number(operation.estimatedRecoveryMinutes) || 0),
    0,
  );
  const id = `recovery-${unique(operationIds).sort().join("-")}-${resourceIds.sort().join("-")}`;

  return {
    id,
    title:
      operations.length === 1
        ? `Restore ${operations[0].name} using ${resourceIds.length} alternative resource${resourceIds.length === 1 ? "" : "s"}`
        : `Restore ${operations.length} operations using ${resourceIds.length} alternative resources`,
    feasible: true,
    operationIds,
    operationNames: operations.map((operation) => operation.name),
    restoredOperationCount: operations.length,
    restoredDepartmentIds,
    resourceIds,
    resourceNames: resourceIds.map((resourceId) => resourcesById.get(resourceId).name),
    resourceAllocations: assignments,
    capacityComparisons,
    priorityScore,
    resourceAvailabilityScore: Number(resourceAvailabilityScore.toFixed(1)),
    capacitySatisfactionScore: Number(capacitySatisfactionScore.toFixed(1)),
    estimatedRecoveryMinutes,
    rankingReason: "",
    rank: 0,
  };
}

function comparePlans(first, second) {
  return (
    second.restoredOperationCount - first.restoredOperationCount ||
    second.priorityScore - first.priorityScore ||
    second.resourceAvailabilityScore - first.resourceAvailabilityScore ||
    second.capacitySatisfactionScore - first.capacitySatisfactionScore ||
    first.estimatedRecoveryMinutes - second.estimatedRecoveryMinutes ||
    first.id.localeCompare(second.id)
  );
}

function findBestCombinedPlan(
  prioritizedOperations,
  operationOptions,
  allAffectedOperations,
  resources,
) {
  const resourcesById = new Map(resources.map((resource) => [resource.id, resource]));
  let bestPlan = null;
  let visitedStates = 0;

  function search(operationIndex, selectedOptions, usedResourceIds) {
    visitedStates += 1;
    if (visitedStates > MAX_COMBINED_SEARCH_STATES) return;
    if (
      bestPlan &&
      selectedOptions.length + prioritizedOperations.length - operationIndex <
        bestPlan.restoredOperationCount
    ) {
      return;
    }
    if (operationIndex === prioritizedOperations.length) {
      if (selectedOptions.length < 2) return;
      const candidate = buildPlan(selectedOptions, allAffectedOperations, resources);
      if (!bestPlan || comparePlans(candidate, bestPlan) < 0) bestPlan = candidate;
      return;
    }

    const operation = prioritizedOperations[operationIndex];
    const options = operationOptions.get(operation.id).slice().sort((first, second) => {
      const resourceCountDifference = first.assignments.length - second.assignments.length;
      if (resourceCountDifference) return resourceCountDifference;
      const getAvailability = (option) =>
        option.assignments.reduce((total, assignment) => {
          const resource = resourcesById.get(assignment.resourceId);
          return total + (resource.capacity > 0 ? resource.available / resource.capacity : 0);
        }, 0);
      return getAvailability(second) - getAvailability(first);
    });

    for (const option of options) {
      if (option.assignments.some((assignment) => usedResourceIds.has(assignment.resourceId))) {
        continue;
      }
      const nextUsedResourceIds = new Set(usedResourceIds);
      option.assignments.forEach((assignment) => nextUsedResourceIds.add(assignment.resourceId));
      search(operationIndex + 1, [...selectedOptions, option], nextUsedResourceIds);
      if (visitedStates > MAX_COMBINED_SEARCH_STATES) return;
    }
    search(operationIndex + 1, selectedOptions, usedResourceIds);
  }

  search(0, [], new Set());
  return bestPlan;
}

/**
 * Generate and rank feasible alternatives without changing the source resources.
 * A valid simulation result is the object returned by simulateFailure().
 */
export function generateRecoveryPlans(simulationResult, options = {}) {
  if (!isOptionsObject(options)) {
    return failure("INVALID_OPTIONS", "Recovery options must be an object.", {
      plans: [],
      rejectedOptions: [],
      unresolvedOperations: [],
    });
  }

  const resources = options.resources || RESOURCES;
  const operations = options.operations || OPERATIONS;
  const recoveryState = options.recoveryState || createRecoveryState();

  if (!isValidSimulationResult(simulationResult)) {
    return failure("INVALID_SIMULATION", "A successful failure simulation result is required.", {
      plans: [],
      rejectedOptions: [],
      unresolvedOperations: [],
    });
  }
  if (!Array.isArray(resources) || !Array.isArray(operations)) {
    return failure("INVALID_CATALOG", "Resources and operations must be arrays.", {
      plans: [],
      rejectedOptions: [],
      unresolvedOperations: [],
    });
  }
  const catalogErrors = validateCatalog(operations, resources);
  if (catalogErrors.length) {
    return failure("INVALID_CATALOG", catalogErrors.join(" "), {
      plans: [],
      rejectedOptions: [],
      unresolvedOperations: [],
    });
  }
  if (
    !recoveryState ||
    !Array.isArray(recoveryState.allocations) ||
    !Array.isArray(recoveryState.restoredOperationIds) ||
    !Array.isArray(recoveryState.appliedPlanIds)
  ) {
    return failure("INVALID_RECOVERY_STATE", "Recovery state has an invalid structure.", {
      plans: [],
      rejectedOptions: [],
      unresolvedOperations: [],
    });
  }

  const affectedDepartmentIds = new Set(simulationResult.affectedDepartmentIds);
  const allAffectedOperations = getAffectedOperations(simulationResult, operations);
  const pendingOperations = allAffectedOperations.filter(
    (operation) => !recoveryState.restoredOperationIds.includes(operation.id),
  );
  const failedPrimaryResourceIds = new Set(
    pendingOperations.flatMap((operation) => operation.primaryResourceIds || []),
  );
  const unavailableResourceIds = unique([
    ...failedPrimaryResourceIds,
    ...resources.filter((resource) => resource.status === "unavailable").map((resource) => resource.id),
  ]);
  const rejectedOptions = [];
  const unresolvedOperations = [];
  const operationOptions = new Map();

  for (const operation of pendingOperations) {
    const bundlesByRequirement = [];
    const reasons = [];

    for (const requirement of operation.requirements) {
      const result = getRequirementBundles(
        operation,
        requirement,
        resources,
        recoveryState.allocations,
        unavailableResourceIds,
        failedPrimaryResourceIds,
        rejectedOptions,
      );
      bundlesByRequirement.push(result.bundles);
      if (!result.bundles.length) {
        reasons.push(
          `${getRequirementName(requirement)}: compatible available capacity is ${result.totalCompatibleCapacity} ${requirement.unit}; ${requirement.demand} required.`,
        );
      }
    }

    if (bundlesByRequirement.some((bundles) => !bundles.length)) {
      unresolvedOperations.push({
        operationId: operation.id,
        operationName: operation.name,
        departmentId: operation.departmentId,
        reasons: unique(reasons),
      });
      continue;
    }

    const combinations = combineRequirementBundles(operation, bundlesByRequirement);
    const validOptions = combinations.filter((option) => {
      const validation = validateOperationRequirements(
        operation,
        option.assignments,
        resources,
        recoveryState.allocations,
        unavailableResourceIds,
      );
      if (!validation.valid) {
        reasons.push(...validation.reasons);
        return false;
      }
      return true;
    });

    if (!validOptions.length) {
      unresolvedOperations.push({
        operationId: operation.id,
        operationName: operation.name,
        departmentId: operation.departmentId,
        reasons: unique(reasons.length ? reasons : ["No combination satisfies all operation constraints."]),
      });
      continue;
    }
    operationOptions.set(operation.id, validOptions);
  }

  const plansById = new Map();
  for (const optionsForOperation of operationOptions.values()) {
    for (const option of optionsForOperation) {
      const plan = buildPlan([option], allAffectedOperations, resources);
      plansById.set(plan.id, plan);
    }
  }

  const prioritizedOperations = pendingOperations
    .filter((operation) => operationOptions.has(operation.id))
    .sort(
      (a, b) =>
        operationPriority(b) - operationPriority(a) ||
        (Number(a.estimatedRecoveryMinutes) || 0) - (Number(b.estimatedRecoveryMinutes) || 0),
    );
  const combinedPlan = findBestCombinedPlan(
    prioritizedOperations,
    operationOptions,
    allAffectedOperations,
    resources,
  );
  if (combinedPlan) plansById.set(combinedPlan.id, combinedPlan);

  const plans = [...plansById.values()].sort(comparePlans);
  plans.forEach((plan, index) => {
    plan.rank = index + 1;
    plan.rankingReason =
      `Ranked #${plan.rank}: restores ${plan.restoredOperationCount} affected operation` +
      `${plan.restoredOperationCount === 1 ? "" : "s"}; priority score ${plan.priorityScore}; ` +
      `resource availability ${plan.resourceAvailabilityScore}%; capacity satisfaction ` +
      `${plan.capacitySatisfactionScore}%; estimated recovery ${plan.estimatedRecoveryMinutes} minutes. ` +
      "All listed resources passed availability, compatibility, and capacity checks.";
  });

  const modeledDepartmentIds = new Set(allAffectedOperations.map((operation) => operation.departmentId));
  const unmodeledAffectedDepartmentIds = [...affectedDepartmentIds].filter(
    (departmentId) => !modeledDepartmentIds.has(departmentId),
  );

  return {
    ok: true,
    plans,
    rejectedOptions: [...new Map(rejectedOptions.map((item) => [item.id, item])).values()],
    unresolvedOperations,
    unmodeledAffectedDepartmentIds,
    affectedOperationCount: allAffectedOperations.length,
    restoredOperationIds: recoveryState.restoredOperationIds.slice(),
  };
}

/**
 * Apply one feasible plan to a new state after rechecking every constraint.
 * Neither the supplied state nor the resource catalog is mutated.
 */
export function applyRecoveryPlan(
  plan,
  recoveryState = createRecoveryState(),
  options = {},
) {
  if (!isOptionsObject(options)) {
    return failure("INVALID_OPTIONS", "Recovery options must be an object.");
  }

  const resources = options.resources || RESOURCES;
  const operations = options.operations || OPERATIONS;

  if (!plan || plan.feasible !== true || typeof plan.id !== "string") {
    return failure("INVALID_PLAN", "Select a feasible recovery plan before applying it.");
  }
  if (
    !recoveryState ||
    !Array.isArray(recoveryState.allocations) ||
    !Array.isArray(recoveryState.restoredOperationIds) ||
    !Array.isArray(recoveryState.appliedPlanIds)
  ) {
    return failure("INVALID_RECOVERY_STATE", "Recovery state has an invalid structure.");
  }
  if (recoveryState.appliedPlanIds.includes(plan.id)) {
    return failure("PLAN_ALREADY_APPLIED", "This recovery plan has already been applied.");
  }
  if (
    !Array.isArray(plan.operationIds) ||
    !plan.operationIds.length ||
    !Array.isArray(plan.resourceAllocations) ||
    !Array.isArray(resources) ||
    !Array.isArray(operations)
  ) {
    return failure("INVALID_PLAN", "The selected plan is missing its operations or allocations.");
  }
  const catalogErrors = validateCatalog(operations, resources);
  if (catalogErrors.length) {
    return failure("INVALID_CATALOG", catalogErrors.join(" "));
  }

  const operationsById = new Map(operations.map((operation) => [operation.id, operation]));
  const resourcesById = new Map(resources.map((resource) => [resource.id, resource]));
  const unavailableResourceIds = new Set(resources.filter((resource) => resource.status === "unavailable").map((resource) => resource.id));
  const seenResourceIds = new Set();
  const planAssignments = plan.resourceAllocations;
  const newOperationIds = unique(plan.operationIds);

  if (newOperationIds.length !== plan.operationIds.length) {
    return failure("DUPLICATE_OPERATION", "The plan contains a duplicate operation.");
  }
  for (const operationId of newOperationIds) {
    const operation = operationsById.get(operationId);
    if (!operation) return failure("UNKNOWN_OPERATION", `Operation was not found: ${operationId}.`);
    if (recoveryState.restoredOperationIds.includes(operationId)) {
      return failure("OPERATION_ALREADY_RESTORED", `${operation.name} has already been restored.`);
    }
    for (const resourceId of operation.primaryResourceIds || []) {
      unavailableResourceIds.add(resourceId);
    }
  }
  for (const assignment of planAssignments) {
    if (!newOperationIds.includes(assignment.operationId)) {
      return failure("INVALID_PLAN", "The plan assigns a resource to an operation outside the plan.");
    }
    if (seenResourceIds.has(assignment.resourceId)) {
      return failure("DUPLICATE_ALLOCATION", `Resource ${assignment.resourceId} is assigned more than once.`);
    }
    seenResourceIds.add(assignment.resourceId);
  }

  for (const operationId of newOperationIds) {
    const operation = operationsById.get(operationId);
    const assignments = planAssignments.filter((item) => item.operationId === operationId);
    const validation = validateOperationRequirements(
      operation,
      assignments,
      resources,
      recoveryState.allocations,
      [...unavailableResourceIds],
    );
    if (!validation.valid) {
      return failure(
        "CONSTRAINTS_NOT_SATISFIED",
        `Cannot apply recovery for ${operation.name}: ${validation.reasons.join(" ")}`,
      );
    }
  }

  const addedAllocations = planAssignments.map((assignment) => ({
    resourceId: assignment.resourceId,
    operationId: assignment.operationId,
    requirementId: assignment.requirementId,
    quantity: assignment.quantity,
    unit: assignment.unit,
    planId: plan.id,
  }));
  const nextState = {
    allocations: [...recoveryState.allocations.map((allocation) => ({ ...allocation })), ...addedAllocations],
    restoredOperationIds: unique([...recoveryState.restoredOperationIds, ...newOperationIds]),
    appliedPlanIds: [...recoveryState.appliedPlanIds, plan.id],
  };

  return { ok: true, state: nextState, appliedPlan: plan };
}

function calculateHealthScore(affectedDepartmentIds, restoredDepartmentIds) {
  const affected = new Set(affectedDepartmentIds);
  const restored = new Set(restoredDepartmentIds);
  const scoreByStatus = { operational: 100, warning: 60, failed: 0 };
  const total = DEPARTMENTS.reduce((sum, department) => {
    if (affected.has(department.id) && !restored.has(department.id)) return sum;
    const statusScore =
      restored.has(department.id) && department.status === "failed"
        ? 100
        : scoreByStatus[department.status] ?? 100;
    return sum + statusScore;
  }, 0);
  return Number((total / DEPARTMENTS.length).toFixed(1));
}

/** Summarize simulated status immediately before and after the applied recovery state. */
export function createRecoveryComparison(
  simulationResult,
  recoveryState = createRecoveryState(),
  options = {},
) {
  if (!isOptionsObject(options)) {
    return failure("INVALID_OPTIONS", "Recovery options must be an object.");
  }

  const resources = options.resources || RESOURCES;
  const operations = options.operations || OPERATIONS;
  if (!isValidSimulationResult(simulationResult)) {
    return failure("INVALID_SIMULATION", "A successful failure simulation result is required.");
  }
  if (!Array.isArray(resources) || !Array.isArray(operations)) {
    return failure("INVALID_CATALOG", "Resources and operations must be arrays.");
  }
  if (!recoveryState || !Array.isArray(recoveryState.allocations)) {
    return failure("INVALID_RECOVERY_STATE", "Recovery state has an invalid structure.");
  }

  const affectedDepartmentIds = unique(simulationResult.affectedDepartmentIds);
  const scopedOperations = getAffectedOperations(simulationResult, operations);
  const affectedOperationIds = new Set(scopedOperations.map((operation) => operation.id));
  const restoredOperationIds = new Set(
    (recoveryState.restoredOperationIds || []).filter((id) => affectedOperationIds.has(id)),
  );
  const restoredDepartmentIds = unique(
    scopedOperations
      .filter(
        (operation) =>
          affectedDepartmentIds.includes(operation.departmentId) &&
          restoredOperationIds.has(operation.id),
      )
      .filter((operation) =>
        scopedOperations
          .filter(
            (candidate) =>
              candidate.departmentId === operation.departmentId &&
              affectedOperationIds.has(candidate.id),
          )
          .every((candidate) => restoredOperationIds.has(candidate.id)),
      )
      .map((operation) => operation.departmentId),
  );
  const remainingAffectedDepartmentIds = affectedDepartmentIds.filter(
    (departmentId) => !restoredDepartmentIds.includes(departmentId),
  );
  const failedDepartmentIdsBefore = unique([
    ...DEPARTMENTS.filter((department) => department.status === "failed").map((department) => department.id),
    ...affectedDepartmentIds,
  ]);
  const failedDepartmentIdsAfter = unique([
    ...DEPARTMENTS.filter(
      (department) =>
        department.status === "failed" && !restoredDepartmentIds.includes(department.id),
    ).map((department) => department.id),
    ...remainingAffectedDepartmentIds,
  ]);
  const relevantOperations = scopedOperations;
  const unavailableResourceIds = unique([
    ...resources.filter((resource) => resource.status === "unavailable").map((resource) => resource.id),
    ...relevantOperations.flatMap((operation) => operation.primaryResourceIds || []),
  ]);
  const resourcesById = new Map(resources.map((resource) => [resource.id, resource]));
  const operationsById = new Map(operations.map((operation) => [operation.id, operation]));
  const resourcesConsumed = recoveryState.allocations
    .filter((allocation) => affectedOperationIds.has(allocation.operationId))
    .map((allocation) => ({
      ...allocation,
      resourceName: resourcesById.get(allocation.resourceId)?.name || allocation.resourceId,
      operationName: operationsById.get(allocation.operationId)?.name || allocation.operationId,
    }));

  return {
    ok: true,
    before: {
      failedDepartmentIds: failedDepartmentIdsBefore,
      affectedDepartmentIds,
      unavailableResourceIds,
      healthScore: calculateHealthScore(affectedDepartmentIds, []),
    },
    after: {
      failedDepartmentIds: failedDepartmentIdsAfter,
      restoredDepartmentIds,
      remainingAffectedDepartmentIds,
      resourcesConsumed,
      healthScore: calculateHealthScore(affectedDepartmentIds, restoredDepartmentIds),
    },
  };
}
