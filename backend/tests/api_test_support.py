from pathlib import Path
from unittest.mock import patch

from app.services import user_store


def isolate_test_user(root):
    users_file_patch = patch.object(user_store, "USERS_FILE", Path(root) / "users.json")
    users_file_patch.start()
    user = user_store.create_user("api-test@example.com", "valid-password")
    return users_file_patch, user["id"]


def authenticated_client(client, user_id):
    with client.session_transaction() as user_session:
        user_session["user_id"] = user_id
        user_session["role"] = "user"
    csrf_token = client.get("/api/auth/csrf").get_json()["csrfToken"]
    client.environ_base["HTTP_X_CSRFTOKEN"] = csrf_token
    return client