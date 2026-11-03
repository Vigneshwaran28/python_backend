import logging
import urllib.parse
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from config import Config

logger = logging.getLogger(__name__)

_pool: Optional[pool.ThreadedConnectionPool] = None

def clean_database_url(url: str) -> str:
    """
    Cleans database URL for psycopg2 compatibility (e.g. Neon connection string).
    Removes parameters like channel_binding if present, ensures sslmode=require.
    """
    parsed = urllib.parse.urlparse(url)
    query_params = urllib.parse.parse_qs(parsed.query)
    
    # channel_binding can cause issues on older libpq in Python environments; ensure sslmode
    query_params.pop("channel_binding", None)
    if "sslmode" not in query_params:
        query_params["sslmode"] = ["require"]
        
    new_query = urllib.parse.urlencode(query_params, doseq=True)
    cleaned = urllib.parse.urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))
    return cleaned

def init_db(database_url: Optional[str] = None):
    global _pool
    if _pool is not None:
        return _pool

    url = clean_database_url(database_url or Config.DATABASE_URL)
    logger.info("[db] Initializing connection pool to Neon PostgreSQL...")
    try:
        _pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=url
        )
        logger.info("[db] Connected to Neon PostgreSQL successfully")
        return _pool
    except Exception as e:
        logger.error(f"[db] Failed to initialize database connection: {e}")
        _pool = None
        raise

def close_db():
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("[db] Database pool closed")

def is_db_ready() -> bool:
    return _pool is not None

@contextmanager
def get_db():
    """Context manager for thread-safe database connection from pool."""
    global _pool
    if _pool is None:
        init_db()

    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)

def fetch_all(query: str, params: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Executes SQL and returns all matching rows as dictionaries."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params or ())
            rows = cur.fetchall()
            return [dict(row) for row in rows]

def fetch_one(query: str, params: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """Executes SQL and returns single row or None."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params or ())
            row = cur.fetchone()
            return dict(row) if row else None

def execute(query: str, params: Optional[Any] = None) -> int:
    """Executes SQL (UPDATE, DELETE, etc.) and returns affected row count."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return cur.rowcount

def insert_and_get_id(table: str, data: Dict[str, Any], returning: str = "id") -> Any:
    """
    Inserts a row into table and returns the specified returning column.
    """
    columns = list(data.keys())
    values = list(data.values())
    col_names = ", ".join(f'"{col}"' for col in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    query = f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders}) RETURNING "{returning}";'

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, values)
            row = cur.fetchone()
            return row[0] if row else None

def test_connection() -> Dict[str, Any]:
    """Tests connection to Neon DB and returns version info."""
    row = fetch_one("SELECT version() as version, current_database() as db;")
    return {
        "ok": True,
        "version": row.get("version", "").split(",")[0] if row else "PostgreSQL",
        "database": row.get("db") if row else "neondb"
    }
