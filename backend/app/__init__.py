import os
import secrets

from flask import Flask, jsonify, request, session
from flask_cors import CORS
from flask_session import Session
from cachelib.file import FileSystemCache
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.exceptions import HTTPException

from .errors import APIError
from .routes.api import api
from .routes.auth import auth
from .services.store import StoreCorruptionError, StoreError, StoreValidationError
from .services.user_store import find_user_by_id


def error_response(message, code, status_code, details=None):
    response = {"status": "error", "message": message, "code": code}
    if details:
        response["details"] = details
    return jsonify(response), status_code


def create_app(test_config=None):
    app = Flask(__name__)
    if test_config:
        app.config.update(test_config)

    secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY")
    if not secret_key:
        if app.config.get("TESTING"):
            secret_key = secrets.token_hex(32)
        elif os.getenv("OMNIS_ENV", "development").lower() in ("production", "prod"):
            raise RuntimeError("FLASK_SECRET_KEY is required in production.")
        else:
            secret_key = secrets.token_hex(32)

    production = os.getenv("OMNIS_ENV", "development").lower() in ("production", "prod")
    secure_cookies = os.getenv("SESSION_COOKIE_SECURE")
    if secure_cookies is None:
        secure_cookie_value = production
    else:
        secure_cookie_value = secure_cookies.lower() in ("1", "true", "yes", "on")

    session_dir = (
        test_config.get("SESSION_FILE_DIR")
        if test_config and test_config.get("SESSION_FILE_DIR")
        else os.getenv(
            "SESSION_FILE_DIR",
            str(__import__("pathlib").Path(__file__).resolve().parents[0] / "data" / "sessions"),
        )
    )
    os.makedirs(session_dir, exist_ok=True)

    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_TYPE="cachelib",
        SESSION_CACHELIB=FileSystemCache(session_dir),
        SESSION_PERMANENT=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE=os.getenv("SESSION_COOKIE_SAMESITE", "Lax"),
        SESSION_COOKIE_SECURE=secure_cookie_value,
        SESSION_COOKIE_NAME=os.getenv("SESSION_COOKIE_NAME", "omnis_session"),
        WTF_CSRF_ENABLED=True,
        WTF_CSRF_TIME_LIMIT=None,
        WTF_CSRF_HEADERS=["X-CSRFToken", "X-CSRF-Token"],
    )

    origins = os.getenv(
        "FRONTEND_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500",
    ).split(",")
    CORS(
        app,
        resources={r"/api/*": {"origins": [origin.strip() for origin in origins]}},
        supports_credentials=True,
        allow_headers=["Accept", "Content-Type", "X-CSRFToken", "X-CSRF-Token"],
        methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    )

    @app.before_request
    def require_institutional_api_session():
        if (
            request.blueprint != "api"
            or request.endpoint == "api.health"
            or request.method == "OPTIONS"
        ):
            return
        user_id = session.get("user_id")
        if not isinstance(user_id, str) or not find_user_by_id(user_id):
            if isinstance(user_id, str):
                session.clear()
            raise APIError("Authentication is required.", "UNAUTHENTICATED", 401)

    csrf = CSRFProtect(app)
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(auth, url_prefix="/api/auth")
    Session(app)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        return error_response("A valid CSRF token is required.", "CSRF_INVALID", 400)

    @app.errorhandler(APIError)
    def handle_api_error(error):
        return error_response(error.message, error.code, error.status_code, error.details)

    @app.errorhandler(StoreCorruptionError)
    @app.errorhandler(StoreValidationError)
    def handle_invalid_store(error):
        return error_response(
            "Stored data is invalid and has been preserved for recovery.",
            "STORE_DATA_INVALID",
            500,
        )

    @app.errorhandler(StoreError)
    def handle_store_error(error):
        return error_response(
            "Persistent storage is temporarily unavailable.",
            "STORE_UNAVAILABLE",
            500,
        )

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        return error_response(error.description, f"HTTP_{error.code}", error.code)

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        app.logger.exception("Unhandled API error")
        return error_response(
            "An unexpected server error occurred.",
            "INTERNAL_ERROR",
            500,
        )

    return app