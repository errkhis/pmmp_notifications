import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row


USERS_TABLE = "notifications_users"
WATCHES_TABLE = "bid_result_watches"
ADMIN_ACTIONS_TABLE = "notifications_admin_actions"
_DB_INITIALIZED = False
PREMIUM_YEARS_DEFAULT = 1
FREE_ACTIVE_WATCH_LIMIT = 1


class DatabaseNotConfigured(RuntimeError):
    pass


@dataclass
class User:
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    plan: str
    premium_expires_at: Optional[datetime]

    @property
    def is_premium(self) -> bool:
        return (
            self.plan == "premium"
            and self.premium_expires_at is not None
            and self.premium_expires_at > datetime.now(timezone.utc)
        )


@dataclass
class BidWatch:
    id: int
    telegram_id: int
    consultation_reference: str
    org_acronyme: str
    consultation_url: str
    consultation_title: Optional[str]
    status: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    last_checked_at: Optional[datetime]


def _load_local_env() -> None:
    env_path = Path(__file__).with_name(".env.local")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _database_url() -> str:
    _load_local_env()
    for name in ("DATABASE_URL", "POSTGRES_URL", "SUPABASE_DB_URL"):
        url = os.environ.get(name, "").strip()
        if url:
            return _clean_database_url(url)
    raise DatabaseNotConfigured("DATABASE_URL, POSTGRES_URL, or SUPABASE_DB_URL is not configured")


def _clean_database_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    allowed = {
        "application_name",
        "connect_timeout",
        "gssencmode",
        "keepalives",
        "keepalives_count",
        "keepalives_idle",
        "keepalives_interval",
        "sslcert",
        "sslcompression",
        "sslcrl",
        "sslkey",
        "sslmode",
        "sslrootcert",
        "target_session_attrs",
    }
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key in allowed]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _connect():
    return psycopg.connect(_database_url(), autocommit=True, row_factory=dict_row)


def init_db() -> None:
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    with _connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {USERS_TABLE} (
                telegram_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                plan TEXT NOT NULL DEFAULT 'free'
                    CHECK (plan IN ('free', 'premium')),
                premium_expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {WATCHES_TABLE} (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL REFERENCES {USERS_TABLE}(telegram_id),
                consultation_reference TEXT NOT NULL,
                org_acronyme TEXT NOT NULL DEFAULT '',
                consultation_url TEXT NOT NULL,
                consultation_title TEXT,
                status TEXT NOT NULL DEFAULT 'watching'
                    CHECK (status IN ('watching', 'notified', 'stopped')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_checked_at TIMESTAMPTZ,
                published_at TIMESTAMPTZ,
                notified_at TIMESTAMPTZ,
                last_error TEXT,
                UNIQUE (telegram_id, consultation_reference, org_acronyme)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {ADMIN_ACTIONS_TABLE} (
                id BIGSERIAL PRIMARY KEY,
                admin_telegram_id BIGINT,
                target_telegram_id BIGINT NOT NULL,
                action TEXT NOT NULL,
                payload JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    _DB_INITIALIZED = True


def upsert_telegram_user(tg_user: dict) -> User:
    init_db()
    telegram_id = int(tg_user["id"])
    username = tg_user.get("username")
    first_name = tg_user.get("first_name")
    with _connect() as conn:
        row = conn.execute(
            f"""
            INSERT INTO {USERS_TABLE} (telegram_id, username, first_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                updated_at = NOW()
            RETURNING telegram_id, username, first_name, plan, premium_expires_at
            """,
            (telegram_id, username, first_name),
        ).fetchone()
    return _row_to_user(row)


def count_users() -> int:
    init_db()
    with _connect() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS total FROM {USERS_TABLE}").fetchone()
    return int(row["total"])


def count_active_bid_watches(telegram_id: int) -> int:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM {WATCHES_TABLE}
            WHERE telegram_id = %s
              AND status = 'watching'
            """,
            (telegram_id,),
        ).fetchone()
    return int(row["total"])


def has_active_bid_watch(telegram_id: int, reference: str, org_acronyme: str = "") -> bool:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT 1
            FROM {WATCHES_TABLE}
            WHERE telegram_id = %s
              AND consultation_reference = %s
              AND org_acronyme = %s
              AND status = 'watching'
            LIMIT 1
            """,
            (telegram_id, reference, org_acronyme or ""),
        ).fetchone()
    return row is not None


def can_create_bid_watch(user: User, reference: str, org_acronyme: str = "") -> bool:
    if user.is_premium:
        return True
    if has_active_bid_watch(user.telegram_id, reference, org_acronyme):
        return True
    return count_active_bid_watches(user.telegram_id) < FREE_ACTIVE_WATCH_LIMIT


def grant_premium(telegram_id: int, years: int = PREMIUM_YEARS_DEFAULT, admin_telegram_id: Optional[int] = None) -> User:
    init_db()
    years = max(1, years)
    with _connect() as conn:
        with conn.transaction():
            row = conn.execute(
                f"""
                INSERT INTO {USERS_TABLE} (telegram_id, plan, premium_expires_at)
                VALUES (%s, 'premium', NOW() + (%s || ' years')::interval)
                ON CONFLICT (telegram_id) DO UPDATE SET
                    plan = 'premium',
                    premium_expires_at = CASE
                        WHEN {USERS_TABLE}.premium_expires_at IS NOT NULL
                         AND {USERS_TABLE}.premium_expires_at > NOW()
                        THEN {USERS_TABLE}.premium_expires_at + (%s || ' years')::interval
                        ELSE NOW() + (%s || ' years')::interval
                    END,
                    updated_at = NOW()
                RETURNING telegram_id, username, first_name, plan, premium_expires_at
                """,
                (telegram_id, years, years, years),
            ).fetchone()
            _record_admin_action(conn, admin_telegram_id, telegram_id, "grant_premium", {"years": years})
    return _row_to_user(row)


def set_free(telegram_id: int, admin_telegram_id: Optional[int] = None) -> User:
    init_db()
    with _connect() as conn:
        with conn.transaction():
            row = conn.execute(
                f"""
                INSERT INTO {USERS_TABLE} (telegram_id, plan)
                VALUES (%s, 'free')
                ON CONFLICT (telegram_id) DO UPDATE SET
                    plan = 'free',
                    premium_expires_at = NULL,
                    updated_at = NOW()
                RETURNING telegram_id, username, first_name, plan, premium_expires_at
                """,
                (telegram_id,),
            ).fetchone()
            _record_admin_action(conn, admin_telegram_id, telegram_id, "set_free", {})
    return _row_to_user(row)


def watch_bid_result(
    telegram_id: int,
    url: str,
    reference: str,
    org_acronyme: str = "",
    consultation_title: Optional[str] = None,
) -> BidWatch:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            f"""
            INSERT INTO {WATCHES_TABLE} (
                telegram_id, consultation_reference, org_acronyme,
                consultation_url, consultation_title, status
            )
            VALUES (%s, %s, %s, %s, %s, 'watching')
            ON CONFLICT (telegram_id, consultation_reference, org_acronyme)
            DO UPDATE SET
                consultation_url = EXCLUDED.consultation_url,
                consultation_title = COALESCE(EXCLUDED.consultation_title, {WATCHES_TABLE}.consultation_title),
                status = 'watching',
                updated_at = NOW(),
                published_at = NULL,
                notified_at = NULL,
                last_error = NULL
            RETURNING id, telegram_id, consultation_reference, org_acronyme,
                consultation_url, consultation_title, status, created_at, updated_at,
                last_checked_at
            """,
            (telegram_id, reference, org_acronyme or "", url, consultation_title),
        ).fetchone()
    return _row_to_bid_watch(row)


def list_pending_bid_watches(telegram_id: int) -> list[BidWatch]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, telegram_id, consultation_reference, org_acronyme,
                consultation_url, consultation_title, status, created_at, updated_at,
                last_checked_at
            FROM {WATCHES_TABLE}
            WHERE telegram_id = %s
              AND status = 'watching'
            ORDER BY created_at DESC, id DESC
            """,
            (telegram_id,),
        ).fetchall()
    return [_row_to_bid_watch(row) for row in rows]


def stop_bid_watch(telegram_id: int, watch_id: int) -> Optional[BidWatch]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            f"""
            UPDATE {WATCHES_TABLE}
            SET status = 'stopped',
                updated_at = NOW()
            WHERE id = %s
              AND telegram_id = %s
              AND status = 'watching'
            RETURNING id, telegram_id, consultation_reference, org_acronyme,
                consultation_url, consultation_title, status, created_at, updated_at,
                last_checked_at
            """,
            (watch_id, telegram_id),
        ).fetchone()
    return _row_to_bid_watch(row) if row else None


def update_bid_watch_title(watch_id: int, consultation_title: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            f"""
            UPDATE {WATCHES_TABLE}
            SET consultation_title = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (consultation_title, watch_id),
        )


def claim_due_bid_watches(limit: int = 10) -> list[BidWatch]:
    init_db()
    limit = max(1, min(limit, 50))
    with _connect() as conn:
        with conn.transaction():
            rows = conn.execute(
                f"""
                WITH due AS (
                    SELECT id
                    FROM {WATCHES_TABLE}
                    WHERE status = 'watching'
                      AND (
                        last_checked_at IS NULL
                        OR last_checked_at < NOW() - INTERVAL '50 seconds'
                      )
                    ORDER BY COALESCE(last_checked_at, created_at), id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE {WATCHES_TABLE} w
                SET last_checked_at = NOW(),
                    updated_at = NOW(),
                    last_error = NULL
                FROM due
                WHERE w.id = due.id
                RETURNING w.id, w.telegram_id, w.consultation_reference,
                    w.org_acronyme, w.consultation_url, w.consultation_title,
                    w.status, w.created_at, w.updated_at, w.last_checked_at
                """,
                (limit,),
            ).fetchall()
    return [_row_to_bid_watch(row) for row in rows]


def mark_bid_watch_notified(watch_id: int) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            f"""
            UPDATE {WATCHES_TABLE}
            SET status = 'notified',
                published_at = NOW(),
                notified_at = NOW(),
                updated_at = NOW(),
                last_error = NULL
            WHERE id = %s
            """,
            (watch_id,),
        )


def mark_bid_watch_error(watch_id: int, error: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            f"""
            UPDATE {WATCHES_TABLE}
            SET last_error = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (error[:800], watch_id),
        )


def _record_admin_action(conn, admin_telegram_id: Optional[int], target_telegram_id: int, action: str, payload: dict) -> None:
    conn.execute(
        f"""
        INSERT INTO {ADMIN_ACTIONS_TABLE} (admin_telegram_id, target_telegram_id, action, payload)
        VALUES (%s, %s, %s, %s::jsonb)
        """,
        (admin_telegram_id, target_telegram_id, action, json.dumps(payload)),
    )


def _row_to_user(row) -> User:
    return User(
        telegram_id=row["telegram_id"],
        username=row["username"],
        first_name=row["first_name"],
        plan=row["plan"],
        premium_expires_at=row["premium_expires_at"],
    )


def _row_to_bid_watch(row) -> BidWatch:
    return BidWatch(
        id=row["id"],
        telegram_id=row["telegram_id"],
        consultation_reference=row["consultation_reference"],
        org_acronyme=row["org_acronyme"],
        consultation_url=row["consultation_url"],
        consultation_title=row.get("consultation_title"),
        status=row["status"],
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        last_checked_at=row.get("last_checked_at"),
    )
