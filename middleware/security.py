from flask import Flask, request, Response
from config import Config

def attach_security_middleware(app: Flask):
    """
    Attaches security headers and robust CORS handlers matching the Express server.
    """

    @app.before_request
    def handle_preflight():
        if request.method == "OPTIONS":
            resp = Response(status=204)
            origin = request.headers.get("Origin")
            if origin:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Access-Control-Allow-Credentials"] = "true"
                resp.headers["Access-Control-Allow-Headers"] = (
                    "Content-Type, Authorization, X-Workspace, Cookie, X-Requested-With, Cache-Control, Accept"
                )
                resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
                resp.headers["Access-Control-Max-Age"] = "86400"
                resp.headers["Vary"] = "Origin"
            return resp

    @app.after_request
    def add_security_and_cors_headers(response: Response) -> Response:
        # Standard security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
        )

        # CORS Headers
        origin = request.headers.get("Origin")
        if origin:
            # If allowed origins list is empty or origin matches
            allowed = Config.ALLOWED_ORIGINS
            if not allowed or origin in allowed or "localhost" in origin or "127.0.0.1" in origin or "onrender.com" in origin:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Access-Control-Allow-Headers"] = (
                    "Content-Type, Authorization, X-Workspace, Cookie, X-Requested-With, Cache-Control, Accept"
                )
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
                response.headers["Access-Control-Max-Age"] = "86400"
                response.headers["Vary"] = "Origin"

        return response
