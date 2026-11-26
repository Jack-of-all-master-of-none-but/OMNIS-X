from flask import Blueprint, jsonify, request, session
from flask_wtf.csrf import generate_csrf

from ..errors import APIError
from ..services.user_store import (
    DuplicateUserError,
    InvalidUserDataError,
    create_user,
    find_user_by_email,
    find_user_by_id,
    public_user,
    verify_password,
)


auth = Blueprint("auth", __name__, url_prefix="/auth")


def required_email(payload):
    email = payload.get("email")
    if not isinstance(email, str) or not email.strip():
        raise APIError("Email is required.", "INVALID_FIELD", 400, {"email": "must be a valid email"})
    email = email.strip().lower()
    if "@" not in email or len(email) > 254:
        raise APIError("Email is required.", "INVALID_FIELD", 400, {"email": "must be a valid email"})
    return email


def request_object():
    if not request.is_json:
        raise APIError("A JSON request body is required.", "JSON_REQUIRED", 400)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise APIError("The JSON request body must be an object.", "INVALID_BODY_TYPE", 400)
    return payload


def required_password(payload):
    password = payload.get("password")
    if not isinstance(password, str) or len(password) < 8 or len(password) > 128:
        raise APIError(
            "Password must contain 8 to 128 characters.",
            "INVALID_FIELD",
            400,
            {"password": "must contain 8 to 128 characters"},
        )
    return password


@auth.get("/csrf")
def csrf_token():
    return jsonify({"csrfToken": generate_csrf()})


@auth.post("/register")
def register():
    payload = request_object()
    email = required_email(payload)
    password = required_password(payload)
    try:
        user = create_user(email, password, role="user")
    except DuplicateUserError:
        raise APIError("An account already exists for that email.", "EMAIL_ALREADY_REGISTERED", 409)
    except InvalidUserDataError as error:
        raise APIError(str(error), "INVALID_FIELD", 400)
    return jsonify({"status": "success", "user": user}), 201


@auth.post("/login")
def login():
    payload = request_object()
    email = required_email(payload)
    password = required_password(payload)
    user = find_user_by_email(email)
    if not user or not verify_password(user, password):
        raise APIError("Invalid email or password.", "INVALID_CREDENTIALS", 401)

    session.clear()
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session.permanent = False
    session.modified = True
    return jsonify({"status": "success", "user": public_user(user)})


@auth.post("/logout")
def logout():
    session.clear()
    session.modified = True
    return jsonify({"status": "success"})


@auth.get("/me")
def current_user():
    user_id = session.get("user_id")
    if not isinstance(user_id, str):
        raise APIError("Authentication is required.", "UNAUTHENTICATED", 401)
    user = find_user_by_id(user_id)
    if not user:
        session.clear()
        session.modified = True
        raise APIError("Authentication is required.", "UNAUTHENTICATED", 401)
    return jsonify({"status": "success", "user": public_user(user)})
