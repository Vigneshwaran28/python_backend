import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from the current directory or parent directory
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

class Config:
    ENV = os.getenv("ENV", "production")
    DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")
    PORT = int(os.getenv("PORT", "5000"))

    # Neon PostgreSQL Database Connection (loaded strictly from environment variable or .env)
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    DATABASE_URL_UNPOOLED = os.getenv("DATABASE_URL_UNPOOLED", "")

    # Security & JWT Tokens (loaded strictly from environment variable or .env)
    JWT_SECRET = os.getenv("JWT_SECRET", "")
    SESSION_COOKIE = "tf_session"
    WORKSPACE_COOKIE_PREFIX = "tf_s_"
    WORKSPACE_HEADER = "x-workspace"

    # CORS
    ALLOWED_ORIGINS = [
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", "").split(",")
        if o.strip()
    ]

    # Rate Limiting Windows (in milliseconds/seconds matching Node.js)
    RATE_LIMIT_GLOBAL = 600  # 600 req/min
    RATE_LIMIT_AUTH = 20 if ENV == "production" else 300  # 20 req/min
    RATE_LIMIT_SETUP = 25   # 25 req/min
    RATE_LIMIT_ADMIN = 40   # 40 req/min
    RATE_LIMIT_PUBLIC = 120 # 120 req/min

    # Neon specifics
    NEON_BRANCH = os.getenv("NEON_BRANCH", "production")
    NEON_AUTH_BASE_URL = os.getenv("NEON_AUTH_BASE_URL", "")
    NEON_AUTH_JWKS_URL = os.getenv("NEON_AUTH_JWKS_URL", "")
