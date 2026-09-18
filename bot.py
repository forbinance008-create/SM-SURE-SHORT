import os
import re
import time
import sqlite3
import shutil
import logging
import threading
from datetime import datetime, timedelta, timezone, time as dt_time
from zoneinfo import ZoneInfo
from html import escape

import telebot
from telebot import types


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "6470135702"))

DB_FILE = "bot_database.db"
BACKUP_DIR = "backups"

BD_TZ = ZoneInfo("Asia/Dhaka")
UTC = timezone.utc

FREE_SIGNALS_PER_CYCLE = 4

REFERRAL_BONUS_CENTS = 100
MIN_WITHDRAW_CENTS = 500
VIP_DEPOSIT_CENTS = 1500

QUOTEX_REF_LINK = os.getenv(
    "QUOTEX_REF_LINK",
    "https://broker-qx.pro/sign-up/?lid=2350796"
)

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing. Set BOT_TOKEN in Railway Variables."
    )

os.makedirs(BACKUP_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("SM_QUATEX_SURE_SHORT")

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML",
    threaded=True
)

db_lock = threading.RLock()

# Runtime-only conversation states.
# Permanent user/admin data is stored in SQLite.
states = {}


# ============================================================
# TIME FUNCTIONS
# ============================================================

CYCLE_EPOCH = datetime(
    2026,
    1,
    1,
    tzinfo=BD_TZ
).date()


def now_bd():
    return datetime.now(BD_TZ)


def now_utc():
    return datetime.now(UTC)


def utc_iso(dt):
    return dt.astimezone(UTC).isoformat()


def bd_from_iso(value):
    return datetime.fromisoformat(value).astimezone(BD_TZ)


def cycle_key():
    """
    2-day calendar cycle using Bangladesh time.

    Example:
    18-19 September = one cycle
    20-21 September = next cycle
    """
    today = now_bd().date()

    days = (today - CYCLE_EPOCH).days
    start_days = (days // 2) * 2

    return (
        CYCLE_EPOCH + timedelta(days=start_days)
    ).isoformat()


def format_bd_time(dt):
    return dt.astimezone(BD_TZ).strftime(
        "%d-%m-%Y %I:%M %p"
    )


def format_signal_time(dt):
    return dt.astimezone(BD_TZ).strftime(
        "%I:%M %p"
    )


# ============================================================
# DATABASE CONNECTION
# ============================================================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys=ON"
    )

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    conn.execute(
        "PRAGMA busy_timeout=30000"
    )

    return conn


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():

    with db_lock:

        conn = db()

        try:

            conn.executescript("""

            CREATE TABLE IF NOT EXISTS users (

                user_id INTEGER PRIMARY KEY,

                username TEXT,

                first_name TEXT,

                status TEXT NOT NULL
                    DEFAULT 'FREE',

                wallet_cents INTEGER NOT NULL
                    DEFAULT 0,

                referred_by INTEGER,

                refs_count INTEGER NOT NULL
                    DEFAULT 0,

                referral_bonus_paid INTEGER NOT NULL
                    DEFAULT 0,

                free_cycle_key TEXT,

                free_used INTEGER NOT NULL
                    DEFAULT 0,

                created_at TEXT NOT NULL,

                last_seen TEXT NOT NULL,

                blocked INTEGER NOT NULL
                    DEFAULT 0,

                FOREIGN KEY(referred_by)
                    REFERENCES users(user_id)
                    ON DELETE SET NULL
            );


            CREATE TABLE IF NOT EXISTS signals (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                signal_text TEXT NOT NULL,

                signal_at_utc TEXT NOT NULL,

                created_at TEXT NOT NULL,

                UNIQUE(
                    signal_text,
                    signal_at_utc
                )
            );


            CREATE TABLE IF NOT EXISTS signal_access (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id INTEGER NOT NULL,

                signal_id INTEGER NOT NULL,

                accessed_at TEXT NOT NULL,

                UNIQUE(
                    user_id,
                    signal_id
                ),

                FOREIGN KEY(user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE,

                FOREIGN KEY(signal_id)
                    REFERENCES signals(id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS signal_votes (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id INTEGER NOT NULL,

                signal_id INTEGER NOT NULL,

                vote TEXT NOT NULL,

                created_at TEXT NOT NULL,

                UNIQUE(
                    user_id,
                    signal_id
                ),

                FOREIGN KEY(user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE,

                FOREIGN KEY(signal_id)
                    REFERENCES signals(id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS uid_submissions (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id INTEGER NOT NULL,

                uid TEXT NOT NULL,

                status TEXT NOT NULL
                    DEFAULT 'PENDING',

                submitted_at TEXT NOT NULL,

                reviewed_at TEXT,

                reviewed_by INTEGER,

                FOREIGN KEY(user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS withdrawals (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id INTEGER NOT NULL,

                amount_cents INTEGER NOT NULL,

                method TEXT NOT NULL,

                account TEXT NOT NULL,

                status TEXT NOT NULL
                    DEFAULT 'PENDING',

                created_at TEXT NOT NULL,

                reviewed_at TEXT,

                reviewed_by INTEGER,

                FOREIGN KEY(user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS wallet_transactions (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id INTEGER NOT NULL,

                type TEXT NOT NULL,

                amount_cents INTEGER NOT NULL,

                balance_after_cents INTEGER NOT NULL,

                note TEXT,

                created_at TEXT NOT NULL,

                FOREIGN KEY(user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS admins (

                user_id INTEGER PRIMARY KEY,

                permissions TEXT NOT NULL
                    DEFAULT ''
            );


            CREATE TABLE IF NOT EXISTS signal_notifications (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id INTEGER NOT NULL,

                signal_id INTEGER NOT NULL,

                sent_at TEXT NOT NULL,

                UNIQUE(
                    user_id,
                    signal_id
                ),

                FOREIGN KEY(user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE,

                FOREIGN KEY(signal_id)
                    REFERENCES signals(id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS admin_audit (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                admin_id INTEGER NOT NULL,

                action TEXT NOT NULL,

                target_user_id INTEGER,

                details TEXT,

                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS settings (

                key TEXT PRIMARY KEY,

                value TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS user_limits (

                user_id INTEGER PRIMARY KEY,

                free_limit INTEGER NOT NULL,

                FOREIGN KEY(user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS live_sessions (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                started_at TEXT NOT NULL,

                ended_at TEXT,

                started_by INTEGER NOT NULL,

                status TEXT NOT NULL
                    DEFAULT 'ACTIVE'
            );


            CREATE TABLE IF NOT EXISTS live_signals (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                signal_text TEXT NOT NULL,

                signal_at_utc TEXT NOT NULL,

                created_at TEXT NOT NULL,

                session_id INTEGER NOT NULL,

                FOREIGN KEY(session_id)
                    REFERENCES live_sessions(id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS vip_expiry (

                user_id INTEGER PRIMARY KEY,

                expires_at_utc TEXT NOT NULL,

                FOREIGN KEY(user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS risk_profiles (

                user_id INTEGER PRIMARY KEY,

                daily_balance_cents INTEGER NOT NULL
                    DEFAULT 0,

                base_trade_cents INTEGER NOT NULL
                    DEFAULT 0,

                max_daily_loss_cents INTEGER NOT NULL
                    DEFAULT 0,

                max_m1_cents INTEGER NOT NULL
                    DEFAULT 0,

                max_trades INTEGER NOT NULL
                    DEFAULT 0,

                profit_target_percent REAL NOT NULL
                    DEFAULT 1.85,

                current_trade_cents INTEGER NOT NULL
                    DEFAULT 0,

                stage TEXT NOT NULL
                    DEFAULT 'BASE',

                daily_profit_cents INTEGER NOT NULL
                    DEFAULT 0,

                daily_loss_cents INTEGER NOT NULL
                    DEFAULT 0,

                trade_count INTEGER NOT NULL
                    DEFAULT 0,

                wins INTEGER NOT NULL
                    DEFAULT 0,

                losses INTEGER NOT NULL
                    DEFAULT 0,

                day_key TEXT NOT NULL,

                FOREIGN KEY(user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS signal_results (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                signal_id INTEGER NOT NULL,

                user_id INTEGER NOT NULL,

                result TEXT NOT NULL,

                amount_cents INTEGER NOT NULL
                    DEFAULT 0,

                profit_cents INTEGER NOT NULL
                    DEFAULT 0,

                created_at TEXT NOT NULL,

                UNIQUE(
                    signal_id,
                    user_id
                ),

                FOREIGN KEY(signal_id)
                    REFERENCES signals(id)
                    ON DELETE CASCADE,

                FOREIGN KEY(user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS user_signal_state (

                user_id INTEGER PRIMARY KEY,

                signal_id INTEGER,

                trade_amount_cents INTEGER NOT NULL
                    DEFAULT 0,

                stage TEXT NOT NULL
                    DEFAULT 'BASE',

                updated_at TEXT NOT NULL,

                FOREIGN KEY(user_id)
                    REFERENCES users(user_id)
                    ON DELETE CASCADE
            );

            """)

            # One Quotex UID cannot belong to more than one
            # active/pending Telegram account.

            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                idx_uid_active_unique
                ON uid_submissions(uid)
                WHERE status IN ('PENDING','APPROVED')
            """)

            # Default settings.

            defaults = {

                "free_signal_limit": "4",

                "referral_bonus_cents": "100",

                "min_withdraw_cents": "500",

                "vip_deposit_cents": "1500",

                "withdraw_enabled": "1",

                "withdraw_hold": "0",

                "maintenance_mode": "0",

                "live_mode": "0",

                "notice": "",

                "trading_rules": "",

                "welcome_message": "",

                "signal_template": "",

                "signal_confidence": "95-99%",

                "vote_results_public": "0",

                "profit_target_percent": "1.85",

                "base_trade_percent": "2",

                "max_m1_percent": "4",

                "max_daily_loss_percent": "5",

                "max_trades_per_day": "20",

                "vip_reminder_days": "3"
            }

            for key, value in defaults.items():

                conn.execute(
                    """
                    INSERT OR IGNORE INTO settings(
                        key,
                        value
                    )
                    VALUES(?, ?)
                    """,
                    (key, value)
                )

            # Make sure master admin exists.

            conn.execute(
                """
                INSERT OR IGNORE INTO admins(
                    user_id,
                    permissions
                )
                VALUES(?, ?)
                """,
                (
                    ADMIN_ID,
                    "ALL"
                )
            )

            conn.commit()

        finally:

            conn.close()


# ============================================================
# SETTINGS
# ============================================================

def get_setting(key, default=""):

    with db_lock:

        conn = db()

        try:

            row = conn.execute(
                """
                SELECT value
                FROM settings
                WHERE key=?
                """,
                (key,)
            ).fetchone()

            if row is None:
                return default

            return row["value"]

        finally:

            conn.close()


def set_setting(key, value):

    with db_lock:

        conn = db()

        try:

            conn.execute(
                """
                INSERT INTO settings(
                    key,
                    value
                )
                VALUES(?, ?)

                ON CONFLICT(key)
                DO UPDATE SET value=excluded.value
                """,
                (
                    key,
                    str(value)
                )
            )

            conn.commit()

        finally:

            conn.close()


def free_signal_limit():

    try:
        return max(
            0,
            int(
                get_setting(
                    "free_signal_limit",
                    str(FREE_SIGNALS_PER_CYCLE)
                )
            )
        )

    except Exception:
        return FREE_SIGNALS_PER_CYCLE


def referral_bonus_cents():

    try:
        return max(
            0,
            int(
                get_setting(
                    "referral_bonus_cents",
                    str(REFERRAL_BONUS_CENTS)
                )
            )
        )

    except Exception:
        return REFERRAL_BONUS_CENTS


def min_withdraw_cents():

    try:
        return max(
            0,
            int(
                get_setting(
                    "min_withdraw_cents",
                    str(MIN_WITHDRAW_CENTS)
                )
            )
        )

    except Exception:
        return MIN_WITHDRAW_CENTS


def vip_deposit_cents():

    try:
        return max(
            0,
            int(
                get_setting(
                    "vip_deposit_cents",
                    str(VIP_DEPOSIT_CENTS)
                )
            )

    except Exception:
        return VIP_DEPOSIT_CENTS


def signal_confidence():

    return get_setting(
        "signal_confidence",
        "95-99%"
    )


# ============================================================
# USER HELPERS
# ============================================================

def get_user(user_id):

    with db_lock:

        conn = db()

        try:

            return conn.execute(
                """
                SELECT *
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

        finally:

            conn.close()


def register_user(tg_user, referred_by=None):

    user_id = tg_user.id
    now = utc_iso(now_utc())

    with db_lock:

        conn = db()

        try:

            existing = conn.execute(
                """
                SELECT user_id
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            if existing:

                conn.execute(
                    """
                    UPDATE users
                    SET username=?,
                        first_name=?,
                        last_seen=?
                    WHERE user_id=?
                    """,
                    (
                        tg_user.username or "",
                        tg_user.first_name or "",
                        now,
                        user_id
                    )
                )

            else:

                ref = None

                if referred_by:

                    try:
                        referred_by = int(referred_by)
                    except Exception:
                        referred_by = None

                    if (
                        referred_by
                        and referred_by != user_id
                    ):

                        ref_exists = conn.execute(
                            """
                            SELECT user_id
                            FROM users
                            WHERE user_id=?
                            """,
                            (referred_by,)
                        ).fetchone()

                        if ref_exists:
                            ref = referred_by

                conn.execute(
                    """
                    INSERT INTO users(
                        user_id,
                        username,
                        first_name,
                        status,
                        wallet_cents,
                        referred_by,
                        refs_count,
                        referral_bonus_paid,
                        free_cycle_key,
                        free_used,
                        created_at,
                        last_seen,
                        blocked
                    )
                    VALUES(
                        ?, ?, ?, 'FREE', 0, ?, 0, 0,
                        ?, 0, ?, ?, 0
                    )
                    """,
                    (
                        user_id,
                        tg_user.username or "",
                        tg_user.first_name or "",
                        ref,
                        cycle_key(),
                        now,
                        now
                    )
                )

            conn.commit()

        finally:

            conn.close()


def ensure_cycle(user_id):

    current = cycle_key()

    with db_lock:

        conn = db()

        try:

            row = conn.execute(
                """
                SELECT free_cycle_key
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            if row is None:
                return

            if row["free_cycle_key"] != current:

                conn.execute(
                    """
                    UPDATE users
                    SET free_cycle_key=?,
                        free_used=0
                    WHERE user_id=?
                    """,
                    (
                        current,
                        user_id
                    )
                )

                conn.commit()


# ============================================================
# WALLET
# ============================================================

def wallet_balance(user_id):

    with db_lock:

        conn = db()

        try:

            row = conn.execute(
                """
                SELECT wallet_cents
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            if not row:
                return 0

            return int(row["wallet_cents"])

        finally:

            conn.close()


def wallet_adjust(
    user_id,
    amount_cents,
    tx_type,
    note=""
):

    with db_lock:

        conn = db()

        try:

            row = conn.execute(
                """
                SELECT wallet_cents
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            if not row:
                return False, 0

            old_balance = int(
                row["wallet_cents"]
            )

            new_balance = (
                old_balance
                + int(amount_cents)
            )

            # Never allow negative wallet.

            if new_balance < 0:
                return False, old_balance

            conn.execute(
                """
                UPDATE users
                SET wallet_cents=?
                WHERE user_id=?
                """,
                (
                    new_balance,
                    user_id
                )
            )

            conn.execute(
                """
                INSERT INTO wallet_transactions(
                    user_id,
                    type,
                    amount_cents,
                    balance_after_cents,
                    note,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    tx_type,
                    int(amount_cents),
                    new_balance,
                    note,
                    utc_iso(now_utc())
                )
            )

            conn.commit()

            return True, new_balance

        finally:

            conn.close()


# ============================================================
# ADMIN PERMISSION
# ============================================================

ALL_PERMISSIONS = {
    "users",
    "signals",
    "uid",
    "withdrawals",
    "broadcast",
    "wallet",
    "analytics",
    "settings",
    "admins",
    "live"
}


def is_master(user_id):

    return int(user_id) == ADMIN_ID


def get_permissions(user_id):

    if is_master(user_id):
        return {"ALL"}

    with db_lock:

        conn = db()

        try:

            row = conn.execute(
                """
                SELECT permissions
                FROM admins
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            if not row:
                return set()

            raw = row["permissions"] or ""

            return {
                x.strip()
                for x in raw.split(",")
                if x.strip()
            }

        finally:

            conn.close()


def can(user_id, permission):

    if is_master(user_id):
        return True

    permissions = get_permissions(user_id)

    return (
        "ALL" in permissions
        or permission in permissions
    )


def audit(
    admin_id,
    action,
    target_user_id=None,
    details=""
):

    try:

        with db_lock:

            conn = db()

            try:

                conn.execute(
                    """
                    INSERT INTO admin_audit(
                        admin_id,
                        action,
                        target_user_id,
                        details,
                        created_at
                    )
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        admin_id,
                        action,
                        target_user_id,
                        details,
                        utc_iso(now_utc())
                    )
                )

                conn.commit()

            finally:

                conn.close()

    except Exception:

        logger.exception(
            "Audit logging failed"
        )


# ============================================================
# VIP
# ============================================================

def vip_active(user_id):

    user = get_user(user_id)

    if not user:
        return False

    if user["status"] != "VIP":
        return False

    with db_lock:

        conn = db()

        try:

            row = conn.execute(
                """
                SELECT expires_at_utc
                FROM vip_expiry
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            # Old VIP accounts without expiry remain VIP.
            if not row:
                return True

            try:

                return (
                    datetime.fromisoformat(
                        row["expires_at_utc"]
                    )
                    > now_utc()
                )

            except Exception:

                return True

        finally:

            conn.close()
