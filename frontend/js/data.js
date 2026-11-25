/**
 * Simulated sample data for the OMNIS-X demonstration.
 *
 * @typedef {"operational" | "warning" | "failed"} DepartmentStatus
 * @typedef {"critical" | "high" | "medium"} Priority
 * @typedef {"low" | "medium" | "high" | "critical"} Severity
 *
 * @typedef {Object} Department
 * @property {string} id Stable ID used by dependencies, incidents, and resources.
 * @property {string} name Display name.
 * @property {string} description Short operational summary.
 * @property {DepartmentStatus} status Current simulated status.
 * @property {Priority} priority Institutional priority.
 *
 * @typedef {Object} Dependency
 * @property {string} upstreamDepartmentId Department providing a required service.
 * @property {string} downstreamDepartmentId Department relying on that service.
 * @property {string} relationship Short explanation of the dependency.
 * @property {string} [service] Shared infrastructure service represented by this link.
 *
 * @typedef {"available" | "limited" | "unavailable"} ResourceStatus
 *
 * @typedef {Object} Resource
 * @property {string} id Stable resource ID.
 * @property {string} departmentId Department responsible for the resource.
 * @property {string} name Resource label.
 * @property {string} type Resource category.
 * @property {number} capacity Total modeled capacity.
 * @property {number} available Number of units currently available.
 * @property {string} unit Unit used for capacity and availability.
 * @property {ResourceStatus} status Simulated operating condition.
 * @property {string[]} compatibleOperations Operations this resource can support.
 *
 * @typedef {Object} OperationRequirement
 * @property {string} id Stable requirement ID within its operation.
 * @property {string} resourceType Required resource category.
 * @property {string} operation Compatibility key required on a resource.
 * @property {number} demand Required capacity.
 * @property {string} unit Unit used by the requirement.
 *
 * @typedef {Object} Operation
 * @property {string} id Stable operation ID.
 * @property {string} name Display name.
 * @property {string} departmentId Department responsible for the operation.
 * @property {Priority} priority Recovery priority.
 * @property {number} estimatedRecoveryMinutes Simulated recovery duration.
 * @property {string[]} primaryResourceIds Resources assumed to be lost with this operation.
 * @property {OperationRequirement[]} requirements Resources and capacity needed to restore the operation.
 * @property {string} [service] Infrastructure service associated with the operation.
 *
 * @typedef {Object} Incident
 * @property {string} id Stable incident ID.
 * @property {string} departmentId Department directly affected.
 * @property {string} title Short incident label.
 * @property {string} failureType Category of failure.
 * @property {Severity} severity Incident severity.
 * @property {string} details Simulated incident description.
 * @property {"active" | "resolved"} status Incident status.
 * @property {string} reportedAt Simulated reporting time.
 * @property {string} [timestamp] ISO timestamp for user-reported incidents.
 */

export const DATA_PROVENANCE = "Simulated demonstration data";

export const INCIDENT_FAILURE_TYPES = [
  { value: "department_outage", label: "Department service outage" },
  { value: "equipment_failure", label: "Equipment failure" },
  { value: "faculty_unavailability", label: "Faculty unavailability" },
  { value: "transport_disruption", label: "Transportation disruption" },
  { value: "power_failure", label: "Electricity outage", service: "Electricity" },
  { value: "internet_failure", label: "Internet outage", service: "Internet" },
];

/** @type {Department[]} */
export const DEPARTMENTS = [
  {
    id: "academic-management",
    name: "Academic Management",
    description: "Coordinates programs, timetables, and academic delivery.",
    status: "operational",
    priority: "critical",
  },
  {
    id: "faculty-management",
    name: "Faculty Management",
    description: "Coordinates faculty availability and teaching assignments.",
    status: "operational",
    priority: "high",
  },
  {
    id: "students",
    name: "Student Services",
    description: "Supports student records, advising, and campus services.",
    status: "operational",
    priority: "high",
  },
  {
    id: "laboratories",
    name: "Laboratories",
    description: "Provides scheduled practical teaching and lab equipment.",
    status: "warning",
    priority: "high",
  },
  {
    id: "classrooms",
    name: "Classrooms",
    description: "Provides teaching spaces and classroom equipment.",
    status: "operational",
    priority: "high",
  },
  {
    id: "examinations",
    name: "Examinations",
    description: "Coordinates examination schedules, venues, and records.",
    status: "warning",
    priority: "critical",
  },
  {
    id: "transportation",
    name: "Transportation",
    description: "Operates student and staff campus transport services.",
    status: "warning",
    priority: "medium",
  },
  {
    id: "hostels",
    name: "Hostels",
    description: "Provides residential accommodation and student support.",
    status: "operational",
    priority: "high",
  },
  {
    id: "placements",
    name: "Placements",
    description: "Coordinates employer engagement and placement activity.",
    status: "operational",
    priority: "medium",
  },
  {
    id: "events",
    name: "Events",
    description: "Coordinates institutional events and campus gatherings.",
    status: "operational",
    priority: "medium",
  },
  {
    id: "infrastructure",
    name: "Infrastructure",
    description: "Maintains campus utilities, facilities, and core systems.",
    status: "failed",
    priority: "critical",
  },
  {
    id: "administration",
    name: "Administration",
    description: "Coordinates institutional administration and approvals.",
    status: "operational",
    priority: "critical",
  },
];

/**
 * Direction means the downstream department relies on the upstream one.
 * Electricity and Internet are modeled as services provided by Infrastructure,
 * rather than as extra departments outside the 12-department model.
 */
/** @type {Dependency[]} */
export const DEPENDENCIES = [
  { upstreamDepartmentId: "administration", downstreamDepartmentId: "academic-management", relationship: "Approvals and academic policy" },
  { upstreamDepartmentId: "administration", downstreamDepartmentId: "faculty-management", relationship: "Staffing and personnel coordination" },
  { upstreamDepartmentId: "administration", downstreamDepartmentId: "infrastructure", relationship: "Maintenance approvals and operating budgets" },
  { upstreamDepartmentId: "administration", downstreamDepartmentId: "transportation", relationship: "Operating approvals and service budgets" },
  { upstreamDepartmentId: "administration", downstreamDepartmentId: "hostels", relationship: "Residential policy and administration" },
  { upstreamDepartmentId: "infrastructure", downstreamDepartmentId: "administration", relationship: "Campus network access for administrative systems", service: "Internet" },
  { upstreamDepartmentId: "infrastructure", downstreamDepartmentId: "placements", relationship: "Campus network access for employer portals", service: "Internet" },
  { upstreamDepartmentId: "academic-management", downstreamDepartmentId: "classrooms", relationship: "Timetables and teaching schedules" },
  { upstreamDepartmentId: "academic-management", downstreamDepartmentId: "laboratories", relationship: "Practical course schedules" },
  { upstreamDepartmentId: "academic-management", downstreamDepartmentId: "examinations", relationship: "Course calendars and assessment schedules" },
  { upstreamDepartmentId: "academic-management", downstreamDepartmentId: "placements", relationship: "Academic calendars and student eligibility" },
  { upstreamDepartmentId: "faculty-management", downstreamDepartmentId: "academic-management", relationship: "Teaching staff availability" },
  { upstreamDepartmentId: "faculty-management", downstreamDepartmentId: "laboratories", relationship: "Lab supervision and instruction" },
  { upstreamDepartmentId: "faculty-management", downstreamDepartmentId: "examinations", relationship: "Assessment supervision" },
  { upstreamDepartmentId: "students", downstreamDepartmentId: "academic-management", relationship: "Enrollment and course participation" },
  { upstreamDepartmentId: "students", downstreamDepartmentId: "examinations", relationship: "Candidate records and accommodations" },
  { upstreamDepartmentId: "students", downstreamDepartmentId: "hostels", relationship: "Resident records and support needs" },
  { upstreamDepartmentId: "students", downstreamDepartmentId: "placements", relationship: "Student profiles and eligibility" },
  { upstreamDepartmentId: "students", downstreamDepartmentId: "transportation", relationship: "Route demand and passenger requirements" },
  { upstreamDepartmentId: "infrastructure", downstreamDepartmentId: "classrooms", relationship: "Electricity supply and room maintenance", service: "Electricity" },
  { upstreamDepartmentId: "infrastructure", downstreamDepartmentId: "laboratories", relationship: "Electricity for lab equipment and safety systems", service: "Electricity" },
  { upstreamDepartmentId: "infrastructure", downstreamDepartmentId: "hostels", relationship: "Building utilities and residential maintenance" },
  { upstreamDepartmentId: "infrastructure", downstreamDepartmentId: "transportation", relationship: "Fleet facilities and campus access" },
  { upstreamDepartmentId: "infrastructure", downstreamDepartmentId: "events", relationship: "Venue utilities and campus facilities" },
  { upstreamDepartmentId: "classrooms", downstreamDepartmentId: "examinations", relationship: "Available examination venues" },
  { upstreamDepartmentId: "transportation", downstreamDepartmentId: "events", relationship: "Transport for scheduled campus events" },
];

/** @type {Resource[]} */
export const RESOURCES = [
  { id: "resource-faculty", departmentId: "faculty-management", name: "Teaching faculty", type: "staff", capacity: 84, available: 76, unit: "staff", status: "limited", compatibleOperations: ["faculty-teaching", "lab-instruction", "exam-supervision"] },
  { id: "resource-student-advisors", departmentId: "students", name: "Student advisors", type: "staff", capacity: 12, available: 10, unit: "staff", status: "limited", compatibleOperations: ["student-support"] },
  { id: "resource-lab-benches", departmentId: "laboratories", name: "Engineering lab benches", type: "equipment", capacity: 160, available: 118, unit: "benches", status: "limited", compatibleOperations: ["lab-practical"] },
  { id: "resource-lab-computers", departmentId: "laboratories", name: "Lab computers", type: "equipment", capacity: 90, available: 64, unit: "devices", status: "limited", compatibleOperations: ["computer-lab"] },
  { id: "resource-classrooms", departmentId: "classrooms", name: "Teaching rooms", type: "facility", capacity: 32, available: 29, unit: "rooms", status: "limited", compatibleOperations: ["lecture", "exam-session"] },
  { id: "resource-exam-seats", departmentId: "examinations", name: "Examination seats", type: "facility", capacity: 1200, available: 970, unit: "seats", status: "limited", compatibleOperations: ["exam-session"] },
  { id: "resource-buses", departmentId: "transportation", name: "Campus buses", type: "vehicle", capacity: 18, available: 13, unit: "vehicles", status: "limited", compatibleOperations: ["transport-route"] },
  { id: "resource-drivers", departmentId: "transportation", name: "Licensed drivers", type: "staff", capacity: 22, available: 17, unit: "staff", status: "limited", compatibleOperations: ["transport-route"] },
  { id: "resource-hostel-beds", departmentId: "hostels", name: "Hostel beds", type: "facility", capacity: 900, available: 48, unit: "beds", status: "limited", compatibleOperations: ["hostel-residency"] },
  { id: "resource-placement-rooms", departmentId: "placements", name: "Interview rooms", type: "facility", capacity: 10, available: 8, unit: "rooms", status: "limited", compatibleOperations: ["placement-interview"] },
  { id: "resource-event-venues", departmentId: "events", name: "Event venues", type: "facility", capacity: 6, available: 4, unit: "venues", status: "limited", compatibleOperations: ["campus-event"] },
  { id: "resource-maintenance-crews", departmentId: "infrastructure", name: "Maintenance crews", type: "staff", capacity: 8, available: 3, unit: "crews", status: "limited", compatibleOperations: ["facility-maintenance"] },
  { id: "resource-generators", departmentId: "infrastructure", name: "Backup generators", type: "equipment", capacity: 5, available: 2, unit: "units", status: "limited", compatibleOperations: ["backup-power"] },
  { id: "resource-admin-staff", departmentId: "administration", name: "Administrative staff", type: "staff", capacity: 20, available: 18, unit: "staff", status: "limited", compatibleOperations: ["admin-operations"] },
  { id: "resource-lab-a", departmentId: "laboratories", name: "Laboratory A", type: "laboratory-space", capacity: 40, available: 0, unit: "seats", status: "unavailable", compatibleOperations: ["lab-practical"] },
  { id: "resource-lab-b", departmentId: "laboratories", name: "Lab B", type: "laboratory-space", capacity: 20, available: 20, unit: "seats", status: "available", compatibleOperations: ["lab-practical"] },
  { id: "resource-lab-c", departmentId: "laboratories", name: "Lab C", type: "laboratory-space", capacity: 20, available: 20, unit: "seats", status: "available", compatibleOperations: ["lab-practical"] },
  { id: "resource-research-room", departmentId: "laboratories", name: "Research-only room", type: "laboratory-space", capacity: 48, available: 48, unit: "seats", status: "available", compatibleOperations: ["research-only"] },
  { id: "resource-classroom-main", departmentId: "classrooms", name: "Main lecture room", type: "classroom-space", capacity: 40, available: 0, unit: "seats", status: "unavailable", compatibleOperations: ["lecture"] },
  { id: "resource-classroom-backup", departmentId: "classrooms", name: "Backup classroom B", type: "classroom-space", capacity: 32, available: 32, unit: "seats", status: "available", compatibleOperations: ["lecture"] },
  { id: "resource-exam-hall-main", departmentId: "examinations", name: "Main examination hall", type: "exam-hall", capacity: 120, available: 0, unit: "seats", status: "unavailable", compatibleOperations: ["exam-session"] },
  { id: "resource-exam-hall-b", departmentId: "examinations", name: "Examination Hall B", type: "exam-hall", capacity: 80, available: 80, unit: "seats", status: "available", compatibleOperations: ["exam-session"] },
  { id: "resource-exam-hall-c", departmentId: "examinations", name: "Examination Hall C", type: "exam-hall", capacity: 50, available: 50, unit: "seats", status: "available", compatibleOperations: ["exam-session"] },
  { id: "resource-faculty-primary-slot", departmentId: "faculty-management", name: "Primary teaching roster", type: "faculty-slots", capacity: 3, available: 0, unit: "staff", status: "unavailable", compatibleOperations: ["faculty-teaching"] },
  { id: "resource-substitute-faculty", departmentId: "faculty-management", name: "Substitute faculty pool", type: "faculty-slots", capacity: 3, available: 3, unit: "staff", status: "available", compatibleOperations: ["faculty-teaching"] },
  { id: "resource-campus-grid", departmentId: "infrastructure", name: "Campus electricity feed", type: "power-feed", capacity: 1, available: 0, unit: "feeds", status: "unavailable", compatibleOperations: ["electricity-service"] },
  { id: "resource-backup-electricity", departmentId: "infrastructure", name: "Mobile backup generator", type: "power-feed", capacity: 1, available: 1, unit: "feeds", status: "available", compatibleOperations: ["electricity-service"] },
  { id: "resource-campus-internet", departmentId: "infrastructure", name: "Primary campus internet link", type: "internet-bandwidth", capacity: 300, available: 0, unit: "Mbps", status: "unavailable", compatibleOperations: ["campus-network", "placement-interview", "admin-systems"] },
  { id: "resource-backup-internet-a", departmentId: "infrastructure", name: "Backup internet link A", type: "internet-bandwidth", capacity: 150, available: 150, unit: "Mbps", status: "available", compatibleOperations: ["campus-network", "placement-interview", "admin-systems"] },
  { id: "resource-backup-internet-b", departmentId: "infrastructure", name: "Backup internet link B", type: "internet-bandwidth", capacity: 120, available: 120, unit: "Mbps", status: "available", compatibleOperations: ["campus-network", "placement-interview", "admin-systems"] },
  { id: "resource-backup-internet-c", departmentId: "infrastructure", name: "Backup internet link C", type: "internet-bandwidth", capacity: 100, available: 100, unit: "Mbps", status: "available", compatibleOperations: ["campus-network", "placement-interview"] },
  { id: "resource-transport-route-primary", departmentId: "transportation", name: "North campus bus route", type: "passenger-seats", capacity: 30, available: 0, unit: "seats", status: "unavailable", compatibleOperations: ["transport-route"] },
  { id: "resource-transport-van", departmentId: "transportation", name: "Accessible shuttle vans", type: "passenger-seats", capacity: 18, available: 18, unit: "seats", status: "available", compatibleOperations: ["transport-route"] },
  { id: "resource-transport-minibus", departmentId: "transportation", name: "Reserve minibus", type: "passenger-seats", capacity: 16, available: 16, unit: "seats", status: "available", compatibleOperations: ["transport-route"] },
  { id: "resource-hostel-wing-primary", departmentId: "hostels", name: "Hostel East wing", type: "hostel-beds", capacity: 30, available: 0, unit: "beds", status: "unavailable", compatibleOperations: ["hostel-residency"] },
  { id: "resource-hostel-annex", departmentId: "hostels", name: "Hostel annex beds", type: "hostel-beds", capacity: 14, available: 14, unit: "beds", status: "available", compatibleOperations: ["hostel-residency"] },
  { id: "resource-academic-system-primary", departmentId: "academic-management", name: "Primary academic scheduling system", type: "scheduling-system", capacity: 1, available: 0, unit: "systems", status: "unavailable", compatibleOperations: ["academic-scheduling"] },
  { id: "resource-academic-system-backup", departmentId: "academic-management", name: "Offline timetable workstation", type: "scheduling-system", capacity: 1, available: 1, unit: "systems", status: "available", compatibleOperations: ["academic-scheduling"] },
  { id: "resource-event-hall-primary", departmentId: "events", name: "Main event hall", type: "event-seats", capacity: 120, available: 0, unit: "seats", status: "unavailable", compatibleOperations: ["campus-event"] },
  { id: "resource-event-hall-b", departmentId: "events", name: "Auditorium B", type: "event-seats", capacity: 80, available: 80, unit: "seats", status: "available", compatibleOperations: ["campus-event"] },
  { id: "resource-event-hall-c", departmentId: "events", name: "Seminar hall C", type: "event-seats", capacity: 60, available: 60, unit: "seats", status: "available", compatibleOperations: ["campus-event"] },
];

/** @type {Operation[]} */
export const OPERATIONS = [
  {
    id: "operation-laboratory-a-practical",
    name: "Laboratory A · 40-student practical",
    departmentId: "laboratories",
    priority: "critical",
    estimatedRecoveryMinutes: 30,
    primaryResourceIds: ["resource-lab-a"],
    requirements: [
      { id: "lab-seats", resourceType: "laboratory-space", operation: "lab-practical", demand: 40, unit: "seats" },
    ],
  },
  {
    id: "operation-classroom-lecture",
    name: "Main lecture room · 30 students",
    departmentId: "classrooms",
    priority: "high",
    estimatedRecoveryMinutes: 20,
    primaryResourceIds: ["resource-classroom-main"],
    requirements: [
      { id: "lecture-seats", resourceType: "classroom-space", operation: "lecture", demand: 30, unit: "seats" },
    ],
  },
  {
    id: "operation-examination-session",
    name: "Examination session · 120 candidates",
    departmentId: "examinations",
    priority: "critical",
    estimatedRecoveryMinutes: 45,
    primaryResourceIds: ["resource-exam-hall-main"],
    requirements: [
      { id: "exam-seats", resourceType: "exam-hall", operation: "exam-session", demand: 120, unit: "seats" },
    ],
  },
  {
    id: "operation-faculty-teaching",
    name: "Scheduled teaching · 3 faculty",
    departmentId: "faculty-management",
    priority: "high",
    estimatedRecoveryMinutes: 25,
    primaryResourceIds: ["resource-faculty-primary-slot"],
    requirements: [
      { id: "teaching-staff", resourceType: "faculty-slots", operation: "faculty-teaching", demand: 3, unit: "staff" },
    ],
  },
  {
    id: "operation-campus-electricity",
    name: "Critical campus electricity service",
    departmentId: "infrastructure",
    service: "Electricity",
    priority: "critical",
    estimatedRecoveryMinutes: 15,
    primaryResourceIds: ["resource-campus-grid"],
    requirements: [
      { id: "power-feed", resourceType: "power-feed", operation: "electricity-service", demand: 1, unit: "feeds" },
    ],
  },
  {
    id: "operation-campus-internet",
    name: "Campus internet service · 100 Mbps",
    departmentId: "infrastructure",
    service: "Internet",
    priority: "critical",
    estimatedRecoveryMinutes: 20,
    primaryResourceIds: ["resource-campus-internet"],
    requirements: [
      { id: "network-bandwidth", resourceType: "internet-bandwidth", operation: "campus-network", demand: 100, unit: "Mbps" },
    ],
  },
  {
    id: "operation-placement-interview",
    name: "Online placement interviews · 100 Mbps",
    departmentId: "placements",
    priority: "high",
    estimatedRecoveryMinutes: 35,
    primaryResourceIds: ["resource-campus-internet"],
    requirements: [
      { id: "placement-bandwidth", resourceType: "internet-bandwidth", operation: "placement-interview", demand: 100, unit: "Mbps" },
    ],
  },
  {
    id: "operation-admin-systems",
    name: "Administrative online systems · 60 Mbps",
    departmentId: "administration",
    priority: "critical",
    estimatedRecoveryMinutes: 20,
    primaryResourceIds: ["resource-campus-internet"],
    requirements: [
      { id: "admin-bandwidth", resourceType: "internet-bandwidth", operation: "admin-systems", demand: 60, unit: "Mbps" },
    ],
  },
  {
    id: "operation-transport-route",
    name: "North campus route · 30 passengers",
    departmentId: "transportation",
    priority: "medium",
    estimatedRecoveryMinutes: 40,
    primaryResourceIds: ["resource-transport-route-primary"],
    requirements: [
      { id: "passenger-seats", resourceType: "passenger-seats", operation: "transport-route", demand: 30, unit: "seats" },
    ],
  },
  {
    id: "operation-hostel-residency",
    name: "Hostel East wing · 10 residents",
    departmentId: "hostels",
    priority: "high",
    estimatedRecoveryMinutes: 50,
    primaryResourceIds: ["resource-hostel-wing-primary"],
    requirements: [
      { id: "hostel-beds", resourceType: "hostel-beds", operation: "hostel-residency", demand: 10, unit: "beds" },
    ],
  },
  {
    id: "operation-academic-scheduling",
    name: "Academic timetable continuity",
    departmentId: "academic-management",
    priority: "critical",
    estimatedRecoveryMinutes: 25,
    primaryResourceIds: ["resource-academic-system-primary"],
    requirements: [
      { id: "schedule-system", resourceType: "scheduling-system", operation: "academic-scheduling", demand: 1, unit: "systems" },
    ],
  },
  {
    id: "operation-campus-event",
    name: "Campus event · 100 attendees",
    departmentId: "events",
    priority: "medium",
    estimatedRecoveryMinutes: 60,
    primaryResourceIds: ["resource-event-hall-primary"],
    requirements: [
      { id: "event-seats", resourceType: "event-seats", operation: "campus-event", demand: 100, unit: "seats" },
    ],
  },
];

/** @type {Incident[]} */
export const SAMPLE_INCIDENTS = [
  {
    id: "incident-infra-power",
    departmentId: "infrastructure",
    title: "Campus power distribution fault",
    failureType: "utility_outage",
    severity: "critical",
    details: "A simulated electrical fault has reduced campus utility support.",
    status: "active",
    reportedAt: "Simulated scenario · 09:10",
  },
  {
    id: "incident-lab-equipment",
    departmentId: "laboratories",
    title: "Laboratory equipment availability",
    failureType: "equipment_shortage",
    severity: "high",
    details: "A simulated equipment issue has reduced practical session capacity.",
    status: "active",
    reportedAt: "Simulated scenario · 09:24",
  },
  {
    id: "incident-transport-fleet",
    departmentId: "transportation",
    title: "Reduced campus bus fleet",
    failureType: "vehicle_shortage",
    severity: "medium",
    details: "A simulated maintenance delay has taken several buses out of service.",
    status: "active",
    reportedAt: "Simulated scenario · 09:41",
  },
];
