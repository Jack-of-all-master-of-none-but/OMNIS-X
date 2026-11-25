import os

from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from .errors import APIError
from .routes.api import api
from .services.store import StoreCorruptionError, StoreError, StoreValidationError


def error_response(message, code, status_code, details=None):
    response = {"status": "error", "message": message, "code": code}
    if details:
        response["details"] = details
    return jsonify(response), status_code


def create_app():
    app = Flask(__name__)
    origins = os.getenv(
        "FRONTEND_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500",
    ).split(",")
    CORS(app, resources={r"/api/*": {"origins": [origin.strip() for origin in origins]}})
    app.register_blueprint(api, url_prefix="/api")

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