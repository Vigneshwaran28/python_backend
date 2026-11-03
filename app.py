import logging
from flask import Flask, request
from config import Config
from db.connection import init_db
from middleware.security import attach_security_middleware
from middleware.rate_limit import global_limiter
from utils.helpers import HttpError, json_response

# Import Blueprints
from routes.auth import auth_bp
from routes.workspace import workspace_bp
from routes.tasks import tasks_bp, lists_bp
from routes.tickets import tickets_bp
from routes.people import people_bp, teams_bp
from routes.users import users_bp, roles_bp
from routes.attendance import attendance_bp, holidays_bp
from routes.notes import notes_bp
from routes.billing import billing_bp
from routes.inventory import inventory_bp
from routes.sales import sales_bp
from routes.expenses import expenses_bp
from routes.clinic import clinic_bp
from routes.logistics import logistics_bp
from routes.meetings import meetings_bp
from routes.messages import messages_bp
from routes.announcements import announcements_bp, broadcasts_bp
from routes.dashboard import stats_bp, notifications_bp
from routes.neon import neon_bp, health_bp
from routes.public import public_bp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize Neon Database Connection Pool
    try:
        init_db(Config.DATABASE_URL)
        logger.info("[db] Connected to Neon PostgreSQL")
    except Exception as e:
        logger.warning(f"[db] Could not initialize database immediately: {e}")

    # Attach Security Headers & CORS
    attach_security_middleware(app)

    # Global Rate Limiter before all API endpoints
    @app.before_request
    def apply_global_rate_limiting():
        if request.path.startswith("/api"):
            # Exclude preflight
            if request.method != "OPTIONS":
                pass

    # Register Blueprints
    app.register_blueprint(health_bp)
    app.register_blueprint(neon_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(workspace_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(lists_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(people_bp)
    app.register_blueprint(teams_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(roles_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(holidays_bp)
    app.register_blueprint(notes_bp)
    app.register_blueprint(billing_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(expenses_bp)
    app.register_blueprint(clinic_bp)
    app.register_blueprint(logistics_bp)
    app.register_blueprint(meetings_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(announcements_bp)
    app.register_blueprint(broadcasts_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(notifications_bp)

    # Global Error Handlers matching Express
    @app.errorhandler(HttpError)
    def handle_http_error(error: HttpError):
        resp = json_response(error.to_dict(), status=error.status)
        if error.retry_after_seconds is not None:
            resp.headers["Retry-After"] = str(error.retry_after_seconds)
        return resp

    @app.errorhandler(404)
    def handle_not_found(e):
        return json_response({"message": "Not found.", "code": "NOT_FOUND"}, status=404)

    @app.errorhandler(405)
    def handle_method_not_allowed(e):
        return json_response({"message": "Method not allowed.", "code": "METHOD_NOT_ALLOWED"}, status=405)

    @app.errorhandler(Exception)
    def handle_internal_error(error: Exception):
        logger.error(f"[server error] Unhandled exception: {error}", exc_info=True)
        return json_response({"message": "Something went wrong. Please try again.", "code": "INTERNAL"}, status=500)

    return app

app = create_app()

if __name__ == "__main__":
    logger.info(f"[api] TaskFlow Python Server starting on http://localhost:{Config.PORT}")
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG)
