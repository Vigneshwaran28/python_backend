from flask import Blueprint
from db.connection import test_connection, is_db_ready
from utils.helpers import json_response, now_iso
from config import Config

neon_bp = Blueprint("neon", __name__, url_prefix="/api/neon")
health_bp = Blueprint("health", __name__, url_prefix="/api")

@health_bp.route("/health", methods=["GET"])
def health_check():
    return json_response({"ok": True, "engine": "python-flask", "time": now_iso()})

@neon_bp.route("/status", methods=["GET"])
def neon_status():
    result = {
        "timestamp": now_iso(),
        "postgres": {"ok": False, "details": None},
        "auth": {"ok": False, "details": None},
        "storage": {"ok": False, "details": None}
    }

    try:
        if is_db_ready():
            db_info = test_connection()
            result["postgres"] = {
                "ok": True,
                "version": db_info.get("version"),
                "database": db_info.get("database")
            }
        else:
            result["postgres"] = {"ok": False, "error": "Database not initialized"}
    except Exception as e:
        result["postgres"] = {"ok": False, "error": str(e)}

    # Neon Auth config
    if Config.NEON_AUTH_BASE_URL:
        result["auth"] = {
            "ok": True,
            "configured": True,
            "baseUrl": Config.NEON_AUTH_BASE_URL
        }
    else:
        result["auth"] = {"ok": False, "configured": False}

    return json_response(result)

@neon_bp.route("/auth/config", methods=["GET"])
def neon_auth_config():
    return json_response({
        "configured": bool(Config.NEON_AUTH_BASE_URL),
        "baseUrl": Config.NEON_AUTH_BASE_URL or None,
        "jwksUrl": Config.NEON_AUTH_JWKS_URL or None
    })
