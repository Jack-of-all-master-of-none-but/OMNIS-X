# OMNIS-X Backend Tests

Run the complete backend suite from this directory:

```powershell
python -m unittest discover -s tests -v
```

The API and persistence tests replace the store path with a temporary directory per test. Concurrent API tests share only that temporary test store. The suite does not use or overwrite `app/data/incidents.json`.

The suite covers storage integrity and locking, API validation and response contracts, recovery constraints, analytics, read-only GET behavior, full incident workflows, concurrent incident creation, concurrent plan generation, and conflicting plan application.

## Risk Intelligence

`GET /api/analytics/risks` calculates each risk report from current department, dependency, and active-incident data without persisting the result. The configurable weights in `app/services/risk_intelligence.py` are department criticality 25%, dependency impact 30%, operational status 25%, and active incident severity 20% (100% total). Criticality scores are critical = 100, high = 75, medium = 50; dependency impact is reachable downstream departments divided by the other 11 departments; operational status scores are operational = 0, warning = 60, failed = 100; active incident severity scores are low = 25, medium = 50, high = 75, critical = 100, with the highest active severity used for each affected department. The weighted score is the sum of each factor score times its weight, divided by 100. Risk levels are LOW below 25, MODERATE from 25 to below 50, HIGH from 50 to below 75, and CRITICAL from 75 through 100.

`GET /api/analytics/dependencies` reports unique direct and transitive downstream reach with cycle-safe traversal. A confirmed single point has no currently available, compatible, same-unit alternative resource set with enough total capacity; a potential single point has a workaround, but fewer than two resource-disjoint alternative configurations. A department without a modeled recovery operation is listed as a confirmed catalog-coverage gap. Current persisted resource allocations reduce the alternative capacity considered by this read-only report.
