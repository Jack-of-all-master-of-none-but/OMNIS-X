import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.services import user_store


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.users_file = self.root / "users.json"
        self.sessions_dir = self.root / "sessions"
        self.users_patch = patch.object(user_store, "USERS_FILE", self.users_file)
        self.users_patch.start()
        self.app = create_app(
            {
                "TESTING": True,
                "WTF_CSRF_ENABLED": True,
                "SESSION_FILE_DIR": str(self.sessions_dir),
                "SESSION_COOKIE_SECURE": False,
                "SESSION_COOKIE_SAMESITE": "Lax",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.users_patch.stop()
        self.temporary_directory.cleanup()

    def csrf_token(self):
        response = self.client.get("/api/auth/csrf")
        self.assertEqual(response.status_code, 200)
        return response.get_json()["csrfToken"]

    def csrf_headers(self):
        return {"X-CSRFToken": self.csrf_token()}

    def register(self, email="person@example.com", password="valid-password", extra=None):
        payload = {"email": email, "password": password}
        if extra:
            payload.update(extra)
        return self.client.post(
            "/api/auth/register",
            json=payload,
            headers=self.csrf_headers(),
        )

    def test_valid_registration(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        user = response.get_json()["user"]
        self.assertEqual(user["email"], "person@example.com")
        self.assertEqual(user["role"], "user")
        self.assertNotIn("passwordHash", user)

    def test_invalid_email(self):
        response = self.register(email="not-an-email")
        self.assertEqual(response.status_code, 400)

    def test_invalid_password(self):
        for password in (None, "", "short", "x" * 129):
            with self.subTest(password=password):
                response = self.register(password=password)
                self.assertEqual(response.status_code, 400)

    def test_duplicate_registration(self):
        self.assertEqual(self.register().status_code, 201)
        duplicate = self.register(email=" PERSON@EXAMPLE.COM ")
        self.assertEqual(duplicate.status_code, 409)

    def test_registration_cannot_assign_admin(self):
        response = self.register(extra={"role": "admin"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["user"]["role"], "user")

    def test_password_is_hashed_in_user_store(self):
        password = "valid-password"
        response = self.register(password=password)
        self.assertEqual(response.status_code, 201)
        state = user_store.load_users()
        stored = state["users"][0]
        self.assertIn("passwordHash", stored)
        self.assertNotEqual(stored["passwordHash"], password)
        self.assertTrue(stored["passwordHash"])
        self.assertNotIn(password, stored["passwordHash"])

    def test_valid_login_and_current_user(self):
        self.assertEqual(self.register().status_code, 201)
        response = self.client.post(
            "/api/auth/login",
            json={"email": " PERSON@EXAMPLE.COM ", "password": "valid-password"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["user"]["role"], "user")
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.get_json()["user"]["email"], "person@example.com")

    def test_invalid_login_is_generic(self):
        self.assertEqual(self.register().status_code, 201)
        response = self.client.post(
            "/api/auth/login",
            json={"email": "person@example.com", "password": "wrong-password"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(response.status_code, 401)
        payload = response.get_json()
        self.assertEqual(payload["code"], "INVALID_CREDENTIALS")
        self.assertEqual(payload["message"], "Invalid email or password.")

    def test_current_user_before_login(self):
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 401)

    def test_logout_invalidates_session(self):
        self.assertEqual(self.register().status_code, 201)
        login = self.client.post(
            "/api/auth/login",
            json={"email": "person@example.com", "password": "valid-password"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
        logout = self.client.post("/api/auth/logout", headers=self.csrf_headers())
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_missing_csrf_token_is_rejected(self):
        response = self.client.post(
            "/api/auth/register",
            json={"email": "person@example.com", "password": "valid-password"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "CSRF_INVALID")

    def test_invalid_csrf_token_is_rejected(self):
        response = self.client.post(
            "/api/auth/register",
            json={"email": "person@example.com", "password": "valid-password"},
            headers={"X-CSRFToken": "invalid-token"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "CSRF_INVALID")

    def test_valid_csrf_token_is_accepted(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)

    def test_user_data_does_not_leak_password_hash(self):
        self.assertEqual(self.register().status_code, 201)
        login = self.client.post(
            "/api/auth/login",
            json={"email": "person@example.com", "password": "valid-password"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(login.status_code, 200)
        login_payload = login.get_json()["user"]
        self.assertNotIn("passwordHash", login_payload)
        me_payload = self.client.get("/api/auth/me").get_json()["user"]
        self.assertNotIn("passwordHash", me_payload)

    def test_session_cookie_has_security_flags(self):
        self.app.config.update(
            SESSION_COOKIE_SECURE=True,
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Lax",
        )
        self.assertEqual(self.register().status_code, 201)
        response = self.client.post(
            "/api/auth/login",
            json={"email": "person@example.com", "password": "valid-password"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(response.status_code, 200)
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Lax", cookie)


if __name__ == "__main__":
    unittest.main()
