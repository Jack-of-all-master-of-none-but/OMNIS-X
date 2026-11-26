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

## Authentication Foundation

Authentication endpoints are available under `/api/auth`:

- `GET /api/auth/csrf` returns a CSRF token for state-changing auth requests.
- `POST /api/auth/register` creates a `user` account; submitted roles are ignored and public registration never creates an administrator.
- `POST /api/auth/login` validates email/password and creates a server-side session.
- `POST /api/auth/logout` invalidates the current session.
- `GET /api/auth/me` returns the current public user record or `401` if unauthenticated.

Set `FLASK_SECRET_KEY` to a strong random environment secret before running outside tests. Set `OMNIS_ENV=production` and `SESSION_COOKIE_SECURE=true` when served over HTTPS. `FRONTEND_ORIGINS` may contain exact comma-separated frontend origins. `SESSION_FILE_DIR` can override the server-side session directory.

Provision administrators with environment variables, not a frontend field or hardcoded password:

```powershell
$env:OMNIS_ADMIN_EMAIL='admin@example.com'
$env:OMNIS_ADMIN_PASSWORD='replace-with-a-long-random-password'
python provision_admin.py
```

Remove the environment variables after provisioning. User records are stored separately in `app/data/users.json`; passwords are stored only as Werkzeug scrypt hashes. All institutional `/api` endpoints except `GET /api/health` require a valid authenticated session. State-changing requests also require a CSRF token. Auth registration, login, CSRF-token retrieval, logout, and current-user routes remain available under `/api/auth`; logout and current-user retain their session-dependent behavior.
