import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.services import store
from api_test_support import authenticated_client, isolate_test_user


class APIAccessControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.data_file_patch = patch.object(store, "DATA_FILE", self.root / "incidents.json")
        self.data_file_patch.start()
        self.user_file_patch, self.user_id = isolate_test_user(self.root)
        self.app = create_app({
            "TESTING": True,
            "SESSION_FILE_DIR": str(self.root / "sessions"),
        })

    def tearDown(self):
        self.user_file_patch.stop()
        self.data_file_patch.stop()
        self.temporary_directory.cleanup()

    def assert_json_error(self, response, status_code, code):
        self.assertEqual(response.status_code, status_code)
        payload = response.get_json()
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["code"], code)
        self.assertIsInstance(payload["message"], str)

    def test_all_institutional_route_groups_require_authentication(self):
        requests = (
            ("GET", "/api/departments"),
            ("GET", "/api/departments/stats"),
            ("GET", "/api/incidents"),
            ("POST", "/api/incidents"),
            ("PATCH", "/api/incidents/incident-test/resolve"),
            ("DELETE", "/api/incidents/incident-test"),
            ("POST", "/api/simulations"),
            ("GET", "/api/simulations/incident-test"),
            ("GET", "/api/recovery/incident-test"),
            ("POST", "/api/recovery/incident-test/plans"),
            ("POST", "/api/recovery/incident-test/apply"),
            ("GET", "/api/analytics/overview"),
            ("GET", "/api/analytics/departments"),
            ("GET", "/api/analytics/recovery"),
            ("GET", "/api/analytics/risks"),
            ("GET", "/api/analytics/dependencies"),
            ("POST", "/api/analytics/scenarios"),
            ("GET", "/api/analytics/export"),
        )
        client = self.app.test_client()

        for method, path in requests:
            with self.subTest(method=method, path=path):
                response = client.open(path, method=method)
                self.assert_json_error(response, 401, "UNAUTHENTICATED")

    def test_public_health_and_auth_lifecycle_remain_available(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/api/health").status_code, 200)
        preflight = client.options(
            "/api/incidents",
            headers={
                "Origin": "http://127.0.0.1:5500",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-csrftoken",
            },
        )
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(
            preflight.headers.get("Access-Control-Allow-Origin"),
            "http://127.0.0.1:5500",
        )
        self.assertEqual(preflight.headers.get("Access-Control-Allow-Credentials"), "true")
        csrf = client.get("/api/auth/csrf")
        self.assertEqual(csrf.status_code, 200)
        headers = {"X-CSRFToken": csrf.get_json()["csrfToken"]}
        credentials = {"email": "public@example.com", "password": "valid-password"}

        registered = client.post("/api/auth/register", json=credentials, headers=headers)
        self.assertEqual(registered.status_code, 201)
        self.assertNotIn("passwordHash", registered.get_json()["user"])

        csrf = client.get("/api/auth/csrf").get_json()["csrfToken"]
        login = client.post(
            "/api/auth/login",
            json=credentials,
            headers={"X-CSRFToken": csrf},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(client.get("/api/auth/me").status_code, 200)
        self.assertEqual(client.get("/api/departments").status_code, 200)

        csrf = client.get("/api/auth/csrf").get_json()["csrfToken"]
        logout = client.post("/api/auth/logout", headers={"X-CSRFToken": csrf})
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_authenticated_user_access_and_mutations_still_work_with_csrf(self):
        user = self.app.test_client()
        with user.session_transaction() as user_session:
            user_session["user_id"] = self.user_id
            user_session["role"] = "user"

        missing_csrf = user.post(
            "/api/incidents",
            json={
                "departmentId": "infrastructure",
                "failureType": "power_failure",
                "severity": "critical",
                "description": "CSRF test",
            },
        )
        self.assert_json_error(missing_csrf, 400, "CSRF_INVALID")

        client = authenticated_client(user, self.user_id)
        self.assertEqual(client.get("/api/departments").status_code, 200)
        created = client.post(
            "/api/incidents",
            json={
                "departmentId": "infrastructure",
                "failureType": "power_failure",
                "severity": "critical",
                "description": "Authenticated API test",
            },
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        self.assertEqual(client.get("/api/incidents").status_code, 200)


if __name__ == "__main__":
    unittest.main()