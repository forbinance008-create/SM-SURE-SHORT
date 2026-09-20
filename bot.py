# ============================================================
# SM QUATEX SURE SHORT - COMPLETE BOT V2
# Python 3.10+
# Requirements:
#   pyTelegramBotAPI==4.36.1
# ============================================================

import os
import re
import time
import sqlite3
import shutil
import logging
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from html import escape

import telebot
from telebot import types


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "6470135702"))

DB_FILE = os.getenv("DB_FILE", "bot_database.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")

BD_TZ = ZoneInfo("Asia/Dhaka")
UTC = timezone.utc

BRAND = "SM QUATEX SURE SHORT"

DEFAULT_FREE_LIMIT = 4
DEFAULT_FREE_CYCLE_DAYS = 2

DEFAULT_REFERRAL_BONUS = 100       # cents
DEFAULT_MIN_WITHDRAW = 500         # $5
DEFAULT_WITHDRAW_HOLD = 0          # hours

DEFAULT_BASE_PERCENT = 1.0
DEFAULT_M1_PERCENT = 2.0
DEFAULT_MAX_DAILY_LOSS_PERCENT = 5.0
DEFAULT_MAX_TRADES = 20
DEFAULT_PROFIT_TARGET_PERCENT = 1.85

QUOTEX_REF_LINK = os.getenv(
    "QUOTEX_REF_LINK",
    "https://broker-qx.pro/sign-up/?lid=2350796"
)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")

os.makedirs(BACKUP_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(BRAND)

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML",
    threaded=True
)

db_lock = threading.RLock()
states = {}


# ============================================================
# TIME
# ============================================================

CYCLE_EPOCH = datetime(2026, 1, 1, tzinfo=BD_TZ).date()


def now_bd():
    return datetime.now(BD_TZ)


def now_utc():
    return datetime.now(UTC)


def utc_iso(dt):
    return dt.astimezone(UTC).isoformat()


def bd_from_iso(value):
    return datetime.fromisoformat(value).astimezone(BD_TZ)


def cycle_key():
    today = now_bd().date()
    days = (today - CYCLE_EPOCH).days
    start = (days // DEFAULT_FREE_CYCLE_DAYS) * DEFAULT_FREE_CYCLE_DAYS
    return (CYCLE_EPOCH + timedelta(days=start)).isoformat()


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def column_exists(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def add_column(conn, table, column, definition):
    if not column_exists(conn, table, column):
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db():
    with db_lock:
        conn = db()

        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                status TEXT NOT NULL DEFAULT 'FREE',
                vip_until TEXT,
                wallet_cents INTEGER NOT NULL DEFAULT 0,

                referred_by INTEGER,
                refs_count INTEGER NOT NULL DEFAULT 0,
                referral_bonus_paid INTEGER NOT NULL DEFAULT 0,

                free_cycle_key TEXT,
                free_used INTEGER NOT NULL DEFAULT 0,
                free_limit_override INTEGER,

                notifications INTEGER NOT NULL DEFAULT 1,
                blocked INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT,
                signal_date TEXT,
                signal_time TEXT,
                direction TEXT,
                confidence TEXT,
                signal_text TEXT NOT NULL,
                audience TEXT NOT NULL DEFAULT 'ALL',
                selected_users TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'SCHEDULED',
                delivered INTEGER NOT NULL DEFAULT 0,
                result TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS signal_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                accessed_at TEXT NOT NULL,
                UNIQUE(user_id, signal_id)
            );

            CREATE TABLE IF NOT EXISTS signal_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                vote TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, signal_id)
            );

            CREATE TABLE IF NOT EXISTS uid_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                uid TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                submitted_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by INTEGER
            );

            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount_cents INTEGER NOT NULL,
                method TEXT NOT NULL,
                account TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by INTEGER
            );

            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                balance_after_cents INTEGER NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                permissions TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS live_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                started_at TEXT NOT NULL,
                ended_at TEXT
            );

            CREATE TABLE IF NOT EXISTS live_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                pair TEXT,
                direction TEXT,
                confidence TEXT,
                signal_text TEXT,
                result TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS risk_profiles (
                user_id INTEGER PRIMARY KEY,
                base_percent REAL,
                max_m1_amount_cents INTEGER,
                max_daily_loss_percent REAL,
                max_trades INTEGER,
                stop_trading INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS trading_days (
                user_id INTEGER NOT NULL,
                day_key TEXT NOT NULL,
                starting_balance_cents INTEGER DEFAULT 0,
                current_balance_cents INTEGER DEFAULT 0,
                pnl_cents INTEGER DEFAULT 0,
                trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                skips INTEGER DEFAULT 0,
                stage TEXT DEFAULT 'BASE',
                current_trade_cents INTEGER DEFAULT 0,
                daily_stopped INTEGER DEFAULT 0,
                profit_target_hit INTEGER DEFAULT 0,
                PRIMARY KEY(user_id, day_key)
            );

            CREATE TABLE IF NOT EXISTS trade_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                signal_id INTEGER,
                stage TEXT,
                amount_cents INTEGER,
                result TEXT,
                pnl_cents INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS session_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                user_id INTEGER,
                result TEXT,
                created_at TEXT NOT NULL
            );
            """)

            # Migration for older versions.
            add_column(conn, "users", "vip_until", "TEXT")
            add_column(conn, "users", "free_limit_override", "INTEGER")
            add_column(conn, "users", "notifications", "INTEGER NOT NULL DEFAULT 1")

            add_column(conn, "signals", "pair", "TEXT")
            add_column(conn, "signals", "signal_date", "TEXT")
            add_column(conn, "signals", "signal_time", "TEXT")
            add_column(conn, "signals", "direction", "TEXT")
            add_column(conn, "signals", "confidence", "TEXT")
            add_column(conn, "signals", "audience", "TEXT NOT NULL DEFAULT 'ALL'")
            add_column(conn, "signals", "selected_users", "TEXT DEFAULT ''")
            add_column(conn, "signals", "status", "TEXT NOT NULL DEFAULT 'SCHEDULED'")
            add_column(conn, "signals", "delivered", "INTEGER NOT NULL DEFAULT 0")
            add_column(conn, "signals", "result", "TEXT DEFAULT ''")

            defaults = {
                "free_limit": str(DEFAULT_FREE_LIMIT),
                "referral_bonus": str(DEFAULT_REFERRAL_BONUS),
                "min_withdraw": str(DEFAULT_MIN_WITHDRAW),
                "withdraw_hold": str(DEFAULT_WITHDRAW_HOLD),
                "withdrawals_enabled": "1",
                "maintenance": "0",
                "live_mode": "1",
                "welcome": (
                    f"🔥 <b>{BRAND}</b>\n\n"
                    "Welcome! নিচের menu থেকে service ব্যবহার করুন।"
                ),
                "notice": "📢 কোনো নতুন notice নেই।",
                "trading_rules": "📖 Trading Rules এখনো সেট করা হয়নি।",
                "vip_rules": "⭐ VIP users Future Signals-এ unlimited access পাবে।",
                "signal_template": (
                    "📊 <b>{brand}</b>\n\n"
                    "📅 Date: <b>{date}</b>\n"
                    "💱 Pair: <b>{pair}</b>\n"
                    "🕐 Time: <b>{time}</b>\n"
                    "🎯 Direction: <b>{direction}</b>\n"
                    "📈 Confidence: <b>{confidence}</b>"
                ),
                "result_reveal": "0",
                "base_percent": str(DEFAULT_BASE_PERCENT),
                "m1_percent": str(DEFAULT_M1_PERCENT),
                "max_daily_loss": str(DEFAULT_MAX_DAILY_LOSS_PERCENT),
                "max_trades": str(DEFAULT_MAX_TRADES),
                "profit_target": str(DEFAULT_PROFIT_TARGET_PERCENT),
                "last_vip_reminder": ""
            }

            for key, value in defaults.items():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO settings(key,value)
                    VALUES(?,?)
                    """,
                    (key, value)
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
                "SELECT value FROM settings WHERE key=?",
                (key,)
            ).fetchone()

            return row["value"] if row else default

        finally:
            conn.close()


def set_setting(key, value):
    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                INSERT INTO settings(key,value)
                VALUES(?,?)
                ON CONFLICT(key)
                DO UPDATE SET value=excluded.value
                """,
                (key, str(value))
            )
            conn.commit()

        finally:
            conn.close()


# ============================================================
# USERS
# ============================================================

def get_user(user_id):
    with db_lock:
        conn = db()

        try:
            return conn.execute(
                "SELECT * FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()

        finally:
            conn.close()


def register_user(tg_user, referred_by=None):
    uid = tg_user.id
    now = utc_iso(now_utc())

    with db_lock:
        conn = db()

        try:
            existing = conn.execute(
                "SELECT user_id FROM users WHERE user_id=?",
                (uid,)
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE users
                    SET username=?, first_name=?, last_seen=?
                    WHERE user_id=?
                    """,
                    (
                        tg_user.username or "",
                        tg_user.first_name or "",
                        now,
                        uid
                    )
                )
            else:
                ref = None

                if referred_by and referred_by != uid:
                    ref_exists = conn.execute(
                        "SELECT user_id FROM users WHERE user_id=?",
                        (referred_by,)
                    ).fetchone()

                    if ref_exists:
                        ref = referred_by

                conn.execute(
                    """
                    INSERT INTO users(
                        user_id,username,first_name,
                        referred_by,free_cycle_key,
                        created_at,last_seen
                    )
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        uid,
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

    ensure_trading_day(uid)


def ensure_cycle(user_id):
    key = cycle_key()

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

            if row and row["free_cycle_key"] != key:
                conn.execute(
                    """
                    UPDATE users
                    SET free_cycle_key=?,free_used=0
                    WHERE user_id=?
                    """,
                    (key, user_id)
                )
                conn.commit()

        finally:
            conn.close()


def effective_free_limit(user_id):
    user = get_user(user_id)

    if not user:
        return int(get_setting("free_limit", DEFAULT_FREE_LIMIT))

    if user["free_limit_override"] is not None:
        return max(0, int(user["free_limit_override"]))

    return max(
        0,
        int(get_setting("free_limit", DEFAULT_FREE_LIMIT))
    )


def is_vip(user_id):
    user = get_user(user_id)

    if not user:
        return False

    if user["status"] != "VIP":
        return False

    expiry = user["vip_until"]

    if not expiry:
        return True

    try:
        return datetime.fromisoformat(expiry) > now_utc()
    except Exception:
        return True


def set_vip(user_id, days=None):
    expiry = None

    if days is not None and int(days) > 0:
        expiry = utc_iso(
            now_utc() + timedelta(days=int(days))
        )

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                UPDATE users
                SET status='VIP',vip_until=?
                WHERE user_id=?
                """,
                (expiry, user_id)
            )
            conn.commit()

        finally:
            conn.close()


def remove_vip(user_id):
    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                UPDATE users
                SET status='FREE',vip_until=NULL
                WHERE user_id=?
                """,
                (user_id,)
            )
            conn.commit()

        finally:
            conn.close()


# ============================================================
# ADMIN
# ============================================================

ALL_PERMISSIONS = {
    "signals",
    "uid",
    "users",
    "wallet",
    "withdraw",
    "broadcast",
    "settings",
    "analytics",
    "vip",
    "risk",
    "live",
    "results"
}


def is_master(user_id):
    return int(user_id) == ADMIN_ID


def get_permissions(user_id):
    if is_master(user_id):
        return ALL_PERMISSIONS | {"subadmins"}

    with db_lock:
        conn = db()

        try:
            row = conn.execute(
                "SELECT permissions FROM admins WHERE user_id=?",
                (user_id,)
            ).fetchone()

            if not row:
                return set()

            return {
                x.strip()
                for x in row["permissions"].split(",")
                if x.strip()
            }

        finally:
            conn.close()


def is_admin(user_id):
    return is_master(user_id) or bool(get_permissions(user_id))


def can(user_id, permission):
    return is_master(user_id) or permission in get_permissions(user_id)


def add_subadmin(user_id, permissions):
    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                INSERT INTO admins(user_id,permissions)
                VALUES(?,?)
                ON CONFLICT(user_id)
                DO UPDATE SET permissions=excluded.permissions
                """,
                (
                    user_id,
                    ",".join(sorted(permissions))
                )
            )
            conn.commit()

        finally:
            conn.close()


def remove_subadmin(user_id):
    with db_lock:
        conn = db()

        try:
            conn.execute(
                "DELETE FROM admins WHERE user_id=?",
                (user_id,)
            )
            conn.commit()

        finally:
            conn.close()


# ============================================================
# MAINTENANCE
# ============================================================

def maintenance_blocked(user_id):
    if is_admin(user_id):
        return False

    return get_setting("maintenance", "0") == "1"


# ============================================================
# WALLET
# ============================================================

def wallet_adjust(user_id, delta_cents, note, tx_type="ADJUSTMENT"):
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

            new_balance = row["wallet_cents"] + delta_cents

            if new_balance < 0:
                return False, row["wallet_cents"]

            conn.execute(
                """
                UPDATE users
                SET wallet_cents=?
                WHERE user_id=?
                """,
                (new_balance, user_id)
            )

            conn.execute(
                """
                INSERT INTO wallet_transactions(
                    user_id,type,amount_cents,
                    balance_after_cents,note,created_at
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    user_id,
                    tx_type,
                    delta_cents,
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
# REFERRAL
# ============================================================

def process_referral_bonus(user_id):
    bonus = int(
        get_setting(
            "referral_bonus",
            DEFAULT_REFERRAL_BONUS
        )
    )

    if bonus <= 0:
        return False

    with db_lock:
        conn = db()

        try:
            user = conn.execute(
                """
                SELECT referred_by,referral_bonus_paid
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            if not user:
                return False

            if not user["referred_by"]:
                return False

            if user["referral_bonus_paid"]:
                return False

            referrer = user["referred_by"]

            ref = conn.execute(
                "SELECT wallet_cents FROM users WHERE user_id=?",
                (referrer,)
            ).fetchone()

            if not ref:
                return False

            new_balance = ref["wallet_cents"] + bonus

            conn.execute(
                """
                UPDATE users
                SET wallet_cents=?,
                    refs_count=refs_count+1
                WHERE user_id=?
                """,
                (new_balance, referrer)
            )

            conn.execute(
                """
                INSERT INTO wallet_transactions(
                    user_id,type,amount_cents,
                    balance_after_cents,note,created_at
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    referrer,
                    "REFERRAL_BONUS",
                    bonus,
                    new_balance,
                    f"Referral bonus from {user_id}",
                    utc_iso(now_utc())
                )
            )

            conn.execute(
                """
                UPDATE users
                SET referral_bonus_paid=1
                WHERE user_id=?
                """,
                (user_id,)
            )

            conn.commit()

        finally:
            conn.close()

    try:
        bot.send_message(
            referrer,
            "🎉 <b>Referral Bonus</b>\n\n"
            f"${bonus/100:.2f} wallet-এ যোগ হয়েছে।"
        )
    except Exception:
        pass

    return True


# ============================================================
# UID
# ============================================================

def save_uid_value(user_id, value):
    value = value.strip()

    if not 3 <= len(value) <= 100:
        return False, "UID format invalid."

    with db_lock:
        conn = db()

        try:
            # Never allow same UID for another account.
            exists = conn.execute(
                """
                SELECT user_id,status
                FROM uid_submissions
                WHERE LOWER(uid)=LOWER(?)
                AND status IN ('PENDING','APPROVED')
                AND user_id != ?
                LIMIT 1
                """,
                (value, user_id)
            ).fetchone()

            if exists:
                return False, "এই Quotex UID অন্য account-এ already submitted."

            own_approved = conn.execute(
                """
                SELECT id
                FROM uid_submissions
                WHERE user_id=? AND status='APPROVED'
                LIMIT 1
                """,
                (user_id,)
            ).fetchone()

            if own_approved:
                return False, "আপনার UID already approved."

            conn.execute(
                """
                UPDATE uid_submissions
                SET status='REPLACED',reviewed_at=?
                WHERE user_id=? AND status='PENDING'
                """,
                (utc_iso(now_utc()), user_id)
            )

            conn.execute(
                """
                INSERT INTO uid_submissions(
                    user_id,uid,status,submitted_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    user_id,
                    value,
                    "PENDING",
                    utc_iso(now_utc())
                )
            )

            conn.commit()

            return True, "UID submitted."

        finally:
            conn.close()


# ============================================================
# SIGNAL HELPERS
# ============================================================

def normalize_direction(value):
    v = value.strip().upper()

    if v in ("UP", "BUY", "CALL", "⬆️", "GREEN"):
        return "🟢 UP / BUY"

    if v in ("DOWN", "SELL", "PUT", "⬇️", "RED"):
        return "🔴 DOWN / SELL"

    return value.strip()


def signal_datetime(row):
    if row["signal_date"] and row["signal_time"]:
        try:
            return datetime.strptime(
                f"{row['signal_date']} {row['signal_time']}",
                "%Y-%m-%d %H:%M"
            ).replace(tzinfo=BD_TZ)
        except Exception:
            pass

    return None


def format_signal(row):
    dt = signal_datetime(row)

    date_text = (
        dt.strftime("%d-%m-%Y")
        if dt else "-"
    )

    time_text = (
        dt.strftime("%I:%M %p")
        if dt else "-"
    )

    template = get_setting(
        "signal_template",
        ""
    )

    values = {
        "brand": BRAND,
        "date": date_text,
        "time": time_text,
        "pair": row["pair"] or "-",
        "direction": normalize_direction(
            row["direction"] or row["signal_text"]
        ),
        "confidence": row["confidence"] or "-"
    }

    try:
        return template.format(**values)
    except Exception:
        return (
            f"📊 <b>{BRAND}</b>\n\n"
            f"📅 Date: <b>{date_text}</b>\n"
            f"💱 Pair: <b>{escape(row['pair'] or '-')}</b>\n"
            f"🕐 Time: <b>{time_text}</b>\n"
            f"🎯 Direction: <b>{escape(values['direction'])}</b>\n"
            f"📈 Confidence: <b>{escape(values['confidence'])}</b>"
        )


def audience_allowed(row, user_id):
    audience = (row["audience"] or "ALL").upper()

    if audience == "ALL":
        return True

    if audience == "VIP":
        return is_vip(user_id)

    if audience == "SELECTED":
        ids = {
            x.strip()
            for x in (row["selected_users"] or "").split(",")
            if x.strip()
        }
        return str(user_id) in ids

    return False


def deliver_signal(user_id, signal_id):
    ensure_cycle(user_id)

    with db_lock:
        conn = db()

        try:
            user = conn.execute(
                "SELECT * FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()

            signal = conn.execute(
                "SELECT * FROM signals WHERE id=?",
                (signal_id,)
            ).fetchone()

            if not user or not signal:
                return False, "not_found"

            if not audience_allowed(signal, user_id):
                return False, "audience"

            already = conn.execute(
                """
                SELECT id
                FROM signal_access
                WHERE user_id=? AND signal_id=?
                """,
                (user_id, signal_id)
            ).fetchone()

            if already:
                return True, "already"

            if not is_vip(user_id):
                used = user["free_used"]
                limit = effective_free_limit(user_id)

                if used >= limit:
                    return False, "limit"

                conn.execute(
                    """
                    UPDATE users
                    SET free_used=free_used+1
                    WHERE user_id=?
                    """,
                    (user_id,)
                )

            conn.execute(
                """
                INSERT INTO signal_access(
                    user_id,signal_id,accessed_at
                )
                VALUES(?,?,?)
                """,
                (
                    user_id,
                    signal_id,
                    utc_iso(now_utc())
                )
            )

            conn.commit()

            return True, "ok"

        finally:
            conn.close()


# ============================================================
# TRADING DAY / MONEY MANAGEMENT
# ============================================================

def today_key():
    return now_bd().strftime("%Y-%m-%d")


def ensure_trading_day(user_id):
    day = today_key()

    with db_lock:
        conn = db()

        try:
            row = conn.execute(
                """
                SELECT user_id
                FROM trading_days
                WHERE user_id=? AND day_key=?
                """,
                (user_id, day)
            ).fetchone()

            if not row:
                conn.execute(
                    """
                    INSERT INTO trading_days(
                        user_id,day_key
                    )
                    VALUES(?,?)
                    """,
                    (user_id, day)
                )
                conn.commit()

        finally:
            conn.close()


def set_daily_balance(user_id, balance_cents):
    ensure_trading_day(user_id)

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                UPDATE trading_days
                SET starting_balance_cents=?,
                    current_balance_cents=?
                WHERE user_id=? AND day_key=?
                """,
                (
                    balance_cents,
                    balance_cents,
                    user_id,
                    today_key()
                )
            )
            conn.commit()

        finally:
            conn.close()


def get_trading_day(user_id):
    ensure_trading_day(user_id)

    with db_lock:
        conn = db()

        try:
            return conn.execute(
                """
                SELECT *
                FROM trading_days
                WHERE user_id=? AND day_key=?
                """,
                (user_id, today_key())
            ).fetchone()

        finally:
            conn.close()


def get_risk(user_id):
    with db_lock:
        conn = db()

        try:
            row = conn.execute(
                """
                SELECT *
                FROM risk_profiles
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            if row:
                return row

            return {
                "base_percent": float(
                    get_setting(
                        "base_percent",
                        DEFAULT_BASE_PERCENT
                    )
                ),
                "max_m1_amount_cents": 0,
                "max_daily_loss_percent": float(
                    get_setting(
                        "max_daily_loss",
                        DEFAULT_MAX_DAILY_LOSS_PERCENT
                    )
                ),
                "max_trades": int(
                    get_setting(
                        "max_trades",
                        DEFAULT_MAX_TRADES
                    )
                ),
                "stop_trading": 0
            }

        finally:
            conn.close()


def calculate_trade_amount(user_id):
    day = get_trading_day(user_id)
    risk = get_risk(user_id)

    if not day or day["current_balance_cents"] <= 0:
        return 0

    percent = float(risk["base_percent"])

    if day["stage"] == "M1":
        percent = float(
            get_setting(
                "m1_percent",
                DEFAULT_M1_PERCENT
            )
        )

        amount = int(
            day["starting_balance_cents"] *
            percent / 100
        )

        max_m1 = int(
            risk["max_m1_amount_cents"] or 0
        )

        if max_m1 > 0:
            amount = min(amount, max_m1)

        return amount

    return int(
        day["starting_balance_cents"] *
        percent / 100
    )


def risk_allows_trade(user_id):
    day = get_trading_day(user_id)
    risk = get_risk(user_id)

    if not day:
        return False, "Daily balance set করা হয়নি।"

    if day["starting_balance_cents"] <= 0:
        return False, "আজকের trading balance আগে set করো।"

    if risk["stop_trading"]:
        return False, "Trading আপনার জন্য stopped।"

    if day["daily_stopped"]:
        return False, "আজকের loss limit reached।"

    if day["profit_target_hit"]:
        return False, "আজকের profit target reached।"

    max_trades = int(risk["max_trades"])

    if max_trades > 0 and day["trades"] >= max_trades:
        return False, "আজকের maximum trades reached।"

    max_loss = float(risk["max_daily_loss_percent"])

    if max_loss > 0:
        loss_limit = (
            day["starting_balance_cents"] *
            max_loss / 100
        )

        if -day["pnl_cents"] >= loss_limit:
            return False, "আজকের maximum daily loss reached।"

    amount = calculate_trade_amount(user_id)

    if amount <= 0:
        return False, "Trade amount calculate করা যায়নি।"

    return True, amount


def record_trade(user_id, signal_id, result):
    result = result.upper()

    if result not in ("WIN", "LOSS", "SKIP"):
        return False, "Invalid result."

    day = get_trading_day(user_id)

    if not day:
        return False, "Daily balance নেই।"

    if result == "SKIP":
        with db_lock:
            conn = db()

            try:
                conn.execute(
                    """
                    UPDATE trading_days
                    SET skips=skips+1
                    WHERE user_id=? AND day_key=?
                    """,
                    (user_id, today_key())
                )
                conn.commit()

            finally:
                conn.close()

        return True, 0

    amount = calculate_trade_amount(user_id)

    if amount <= 0:
        return False, "Trade amount invalid."

    # Simple 1:1 model:
    # WIN = +trade amount
    # LOSS = -trade amount
    pnl = amount if result == "WIN" else -amount

    old_stage = day["stage"]

    if result == "WIN":
        next_stage = "BASE"
    else:
        # Base LOSS -> M1
        # M1 LOSS -> BASE
        next_stage = (
            "M1"
            if old_stage == "BASE"
            else "BASE"
        )

    new_balance = day["current_balance_cents"] + pnl

    profit_target = float(
        get_setting(
            "profit_target",
            DEFAULT_PROFIT_TARGET_PERCENT
        )
    )

    target_hit = 0

    if (
        day["starting_balance_cents"] > 0 and
        new_balance >= day["starting_balance_cents"] *
        (1 + profit_target / 100)
    ):
        target_hit = 1

    max_loss = float(
        get_setting(
            "max_daily_loss",
            DEFAULT_MAX_DAILY_LOSS_PERCENT
        )
    )

    daily_stopped = 0

    if (
        day["starting_balance_cents"] > 0 and
        -(
            day["pnl_cents"] + pnl
        ) >= day["starting_balance_cents"] *
        max_loss / 100
    ):
        daily_stopped = 1

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                UPDATE trading_days
                SET current_balance_cents=?,
                    pnl_cents=pnl_cents+?,
                    trades=trades+1,
                    wins=wins+?,
                    losses=losses+?,
                    stage=?,
                    current_trade_cents=?,
                    daily_stopped=?,
                    profit_target_hit=?
                WHERE user_id=? AND day_key=?
                """,
                (
                    new_balance,
                    pnl,
                    1 if result == "WIN" else 0,
                    1 if result == "LOSS" else 0,
                    next_stage,
                    (
                        calculate_trade_amount(user_id)
                        if next_stage == "M1"
                        else 0
                    ),
                    daily_stopped,
                    target_hit,
                    user_id,
                    today_key()
                )
            )

            conn.execute(
                """
                INSERT INTO trade_results(
                    user_id,signal_id,stage,
                    amount_cents,result,pnl_cents,created_at
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    user_id,
                    signal_id,
                    old_stage,
                    amount,
                    result,
                    pnl,
                    utc_iso(now_utc())
                )
            )

            conn.commit()

        finally:
            conn.close()

    return True, pnl


# ============================================================
# SIGNAL RESULT
# ============================================================

def set_signal_result(signal_id, result):
    result = result.upper()

    if result not in ("WIN", "LOSS", "SKIP"):
        return False

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                UPDATE signals
                SET result=?,status='RESULT'
                WHERE id=?
                """,
                (result, signal_id)
            )
            conn.commit()

        finally:
            conn.close()

    return True


def signal_vote(user_id, signal_id, vote):
    vote = vote.upper()

    if vote not in ("WIN", "LOSS", "SKIP"):
        return False, "Invalid."

    with db_lock:
        conn = db()

        try:
            exists = conn.execute(
                """
                SELECT id
                FROM signal_votes
                WHERE user_id=? AND signal_id=?
                """,
                (user_id, signal_id)
            ).fetchone()

            if exists:
                return False, "আপনি already vote দিয়েছেন।"

            conn.execute(
                """
                INSERT INTO signal_votes(
                    user_id,signal_id,vote,created_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    user_id,
                    signal_id,
                    vote,
                    utc_iso(now_utc())
                )
            )

            conn.commit()

        finally:
            conn.close()

    return True, "Vote saved."


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard(user_id):
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True
    )

    kb.row(
        "📊 Future Signals",
        "⚡ Live Signals"
    )

    kb.row(
        "💰 Trading",
        "👤 My Status"
    )

    kb.row(
        "🆔 Submit Quotex UID",
        "💳 Wallet"
    )

    kb.row(
        "👥 Referral Link",
        "⭐ VIP"
    )

    kb.row(
        "📜 Signal History",
        "📖 Rules"
    )

    kb.row(
        "🔔 Notifications",
        "📢 Notice"
    )

    kb.row("❓ Help")

    if is_admin(user_id):
        kb.row("👑 Admin Control")

    return kb


def admin_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)

    buttons = [
        ("➕ Add Signal", "adm_add_signal"),
        ("📊 Signals", "adm_signals"),
        ("🆔 UID", "adm_uids"),
        ("⭐ VIP", "adm_vip"),
        ("💸 Withdrawals", "adm_withdrawals"),
        ("💳 Wallet", "adm_wallet"),
        ("👥 Users", "adm_users"),
        ("📢 Broadcast", "adm_broadcast"),
        ("⚡ Live Session", "adm_live"),
        ("📈 Analytics", "adm_analytics"),
        ("🛡 Sub-admins", "adm_subadmins"),
        ("🛠 Risk Control", "adm_risk"),
        ("⚙️ Settings", "adm_settings"),
    ]

    for i in range(0, len(buttons), 2):
        row = []

        for title, data in buttons[i:i + 2]:
            row.append(
                types.InlineKeyboardButton(
                    title,
                    callback_data=data
                )
            )

        kb.row(*row)

    kb.add(
        types.InlineKeyboardButton(
            "🗑 Clear Future",
            callback_data="adm_clear"
        )
    )

    return kb


def back_admin_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "⬅️ Admin Panel",
            callback_data="adm_home"
        )
    )
    return kb


# ============================================================
# START
# ============================================================

@bot.message_handler(commands=["start"])
def start_cmd(message):
    try:
        referred_by = None

        parts = message.text.split(maxsplit=1)

        if len(parts) == 2 and parts[1].startswith("ref_"):
            try:
                referred_by = int(parts[1][4:])
            except Exception:
                pass

        register_user(
            message.from_user,
            referred_by
        )

        if maintenance_blocked(message.from_user.id):
            bot.send_message(
                message.chat.id,
                "🛠️ <b>Maintenance Mode</b>\n\n"
                "Bot বর্তমানে maintenance-এ আছে।",
                reply_markup=main_keyboard(
                    message.from_user.id
                )
            )
            return

        welcome = get_setting(
            "welcome",
            f"🔥 <b>{BRAND}</b>"
        )

        bot.send_message(
            message.chat.id,
            welcome,
            reply_markup=main_keyboard(
                message.from_user.id
            )
        )

    except Exception:
        logger.exception("start error")
        bot.send_message(
            message.chat.id,
            "❌ Start error. আবার /start দাও।"
        )


# ============================================================
# STATUS
# ============================================================

@bot.message_handler(func=lambda m: m.text == "👤 My Status")
def status_cmd(message):
    register_user(message.from_user)
    ensure_cycle(message.from_user.id)

    u = get_user(message.from_user.id)
    day = get_trading_day(message.from_user.id)

    if is_vip(message.from_user.id):
        vip = "⭐ VIP"
        expiry = (
            "Unlimited"
            if not u["vip_until"]
            else bd_from_iso(
                u["vip_until"]
            ).strftime("%d-%m-%Y %I:%M %p")
        )
    else:
        vip = "FREE"
        expiry = "-"

    remaining = max(
        0,
        effective_free_limit(
            message.from_user.id
        ) - u["free_used"]
    )

    bot.send_message(
        message.chat.id,
        "👤 <b>MY STATUS</b>\n\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n"
        f"⭐ Status: <b>{vip}</b>\n"
        f"📅 VIP Expiry: <b>{expiry}</b>\n"
        f"🎟 Free Remaining: <b>{remaining}</b>\n"
        f"💰 Wallet: <b>${u['wallet_cents']/100:.2f}</b>\n"
        f"👥 Referrals: <b>{u['refs_count']}</b>\n\n"
        f"💵 Today's Balance: "
        f"<b>${day['current_balance_cents']/100:.2f}</b>\n"
        f"📈 P/L: <b>${day['pnl_cents']/100:.2f}</b>\n"
        f"🎯 Stage: <b>{day['stage']}</b>\n"
        f"📊 Trades: <b>{day['trades']}</b>\n"
        f"🏆 W/L: <b>{day['wins']}/{day['losses']}</b>"
    )


# ============================================================
# FUTURE SIGNALS
# ============================================================

@bot.message_handler(func=lambda m: m.text == "📊 Future Signals")
def future_signals(message):
    uid = message.from_user.id

    register_user(message.from_user)

    if maintenance_blocked(uid):
        bot.send_message(
            message.chat.id,
            "🛠️ Maintenance Mode."
        )
        return

    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT *
                FROM signals
                WHERE status IN ('SCHEDULED','SENT')
                ORDER BY signal_date,signal_time,id
                LIMIT 20
                """
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        bot.send_message(
            message.chat.id,
            "📭 কোনো upcoming signal নেই।"
        )
        return

    shown = 0

    for row in rows:
        if not audience_allowed(row, uid):
            continue

        dt = signal_datetime(row)

        if dt and dt <= now_bd():
            continue

        # Only next available signal per click.
        ok, reason = deliver_signal(uid, row["id"])

        if not ok:
            if reason == "limit":
                bot.send_message(
                    message.chat.id,
                    "⛔ আপনার free signal quota শেষ।"
                )
            elif reason == "audience":
                continue

            return

        process_referral_bonus(uid)

        kb = types.InlineKeyboardMarkup(row_width=3)

        kb.row(
            types.InlineKeyboardButton(
                "✅ WIN",
                callback_data=f"vote_WIN_{row['id']}"
            ),
            types.InlineKeyboardButton(
                "❌ LOSS",
                callback_data=f"vote_LOSS_{row['id']}"
            ),
            types.InlineKeyboardButton(
                "⏭ SKIP",
                callback_data=f"vote_SKIP_{row['id']}"
            )
        )

        bot.send_message(
            message.chat.id,
            format_signal(row),
            reply_markup=kb
        )

        shown += 1
        break

    if shown == 0:
        bot.send_message(
            message.chat.id,
            "📭 আপনার জন্য কোনো available upcoming signal নেই।"
        )


# ============================================================
# VOTE
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data.startswith("vote_")
)
def vote_callback(call):
    try:
        _, vote, sid = call.data.split("_")

        sid = int(sid)

        ok, msg = signal_vote(
            call.from_user.id,
            sid,
            vote
        )

        if not ok:
            bot.answer_callback_query(
                call.id,
                msg
            )
            return

        bot.answer_callback_query(
            call.id,
            "Vote saved ✅"
        )

        # Hide vote buttons after user's vote.
        try:
            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None
            )
        except Exception:
            pass

    except Exception:
        logger.exception("vote error")
        bot.answer_callback_query(
            call.id,
            "Vote save হয়নি।"
        )


# ============================================================
# TRADING
# ============================================================

@bot.message_handler(func=lambda m: m.text == "💰 Trading")
def trading_cmd(message):
    register_user(message.from_user)

    day = get_trading_day(
        message.from_user.id
    )

    amount = calculate_trade_amount(
        message.from_user.id
    )

    bot.send_message(
        message.chat.id,
        "💰 <b>TRADING / MONEY MANAGEMENT</b>\n\n"
        f"💵 Starting: "
        f"<b>${day['starting_balance_cents']/100:.2f}</b>\n"
        f"💵 Current: "
        f"<b>${day['current_balance_cents']/100:.2f}</b>\n"
        f"📈 P/L: "
        f"<b>${day['pnl_cents']/100:.2f}</b>\n"
        f"🎯 Stage: <b>{day['stage']}</b>\n"
        f"💵 Next Trade: "
        f"<b>${amount/100:.2f}</b>\n"
        f"📊 Trades: <b>{day['trades']}</b>\n"
        f"🏆 WIN: <b>{day['wins']}</b>\n"
        f"❌ LOSS: <b>{day['losses']}</b>\n"
        f"⏭ SKIP: <b>{day['skips']}</b>\n\n"
        "Base LOSS → M1\n"
        "Base WIN → Base\n"
        "M1 WIN → Base\n"
        "M1 LOSS → Base\n\n"
        "⚠️ M2/M3 নেই।"
    )


# ============================================================
# SET BALANCE
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "💵 Set Trading Balance"
)
def set_balance_cmd(message):
    states[message.from_user.id] = {
        "action": "set_balance"
    }

    bot.send_message(
        message.chat.id,
        "💵 আজকের current trading balance পাঠাও।\n\n"
        "Example: <code>100</code>"
    )


# ============================================================
# NOTIFICATIONS
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "🔔 Notifications"
)
def notification_cmd(message):
    uid = message.from_user.id
    user = get_user(uid)

    new_value = 0 if user["notifications"] else 1

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                UPDATE users
                SET notifications=?
                WHERE user_id=?
                """,
                (new_value, uid)
            )
            conn.commit()

        finally:
            conn.close()

    bot.send_message(
        message.chat.id,
        "🔔 Notifications: "
        + ("🟢 ON" if new_value else "🔴 OFF")
    )


# ============================================================
# WALLET
# ============================================================

@bot.message_handler(func=lambda m: m.text == "💳 Wallet")
def wallet_cmd(message):
    user = get_user(message.from_user.id)

    bot.send_message(
        message.chat.id,
        "💳 <b>WALLET</b>\n\n"
        f"Balance: <b>${user['wallet_cents']/100:.2f}</b>\n\n"
        "নিচের command ব্যবহার করো:\n"
        "💸 Withdraw\n"
        "📜 Transactions"
    )


@bot.message_handler(commands=["withdraw"])
def withdraw_cmd(message):
    uid = message.from_user.id

    if get_setting(
        "withdrawals_enabled",
        "1"
    ) != "1":
        bot.send_message(
            message.chat.id,
            "💸 Withdrawal বর্তমানে বন্ধ।"
        )
        return

    states[uid] = {
        "action": "withdraw_amount"
    }

    bot.send_message(
        message.chat.id,
        "💸 Withdrawal amount পাঠাও।\n\n"
        f"Minimum: ${int(get_setting('min_withdraw', DEFAULT_MIN_WITHDRAW))/100:.2f}"
    )


@bot.message_handler(commands=["transactions"])
def transactions_cmd(message):
    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT *
                FROM wallet_transactions
                WHERE user_id=?
                ORDER BY id DESC
                LIMIT 15
                """,
                (message.from_user.id,)
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        bot.send_message(
            message.chat.id,
            "📭 কোনো transaction নেই।"
        )
        return

    text = "📜 <b>TRANSACTIONS</b>\n\n"

    for row in rows:
        text += (
            f"• {row['type']}: "
            f"${row['amount_cents']/100:.2f}\n"
            f"  {row['note'] or ''}\n"
        )

    bot.send_message(
        message.chat.id,
        text
    )


# ============================================================
# VIP
# ============================================================

@bot.message_handler(func=lambda m: m.text == "⭐ VIP")
def vip_cmd(message):
    bot.send_message(
        message.chat.id,
        "⭐ <b>VIP</b>\n\n"
        + get_setting("vip_rules", "")
        + "\n\n"
        "UID verification-এর মাধ্যমে VIP activate করা হবে।\n\n"
        f"Registration:\n{escape(QUOTEX_REF_LINK)}"
    )


# ============================================================
# REFERRAL
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "👥 Referral Link"
)
def referral_cmd(message):
    try:
        me = bot.get_me()

        link = (
            f"https://t.me/{me.username}"
            f"?start=ref_{message.from_user.id}"
        )

        bonus = int(
            get_setting(
                "referral_bonus",
                DEFAULT_REFERRAL_BONUS
            )
        )

        bot.send_message(
            message.chat.id,
            "👥 <b>REFERRAL</b>\n\n"
            f"<code>{link}</code>\n\n"
            f"Bonus: <b>${bonus/100:.2f}</b>"
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Referral link তৈরি হয়নি।"
        )


# ============================================================
# UID USER
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "🆔 Submit Quotex UID"
)
def uid_start(message):
    uid = message.from_user.id

    user = get_user(uid)

    if is_vip(uid):
        bot.send_message(
            message.chat.id,
            "⭐ আপনি already VIP।"
        )
        return

    states[uid] = {
        "action": "uid"
    }

    bot.send_message(
        message.chat.id,
        "🆔 আপনার Quotex UID পাঠাও।\n\n"
        "Cancel করতে /cancel পাঠাও।"
    )


# ============================================================
# SIGNAL HISTORY
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "📜 Signal History"
)
def signal_history(message):
    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT s.*,sa.accessed_at
                FROM signal_access sa
                JOIN signals s ON s.id=sa.signal_id
                WHERE sa.user_id=?
                ORDER BY sa.id DESC
                LIMIT 20
                """,
                (message.from_user.id,)
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        bot.send_message(
            message.chat.id,
            "📭 Signal history empty."
        )
        return

    text = "📜 <b>SIGNAL HISTORY</b>\n\n"

    for row in rows:
        text += (
            f"#{row['id']} "
            f"{escape(row['pair'] or row['signal_text'])}\n"
            f"Result: <b>{row['result'] or 'PENDING'}</b>\n\n"
        )

    bot.send_message(
        message.chat.id,
        text
    )


# ============================================================
# RULES / NOTICE / HELP
# ============================================================

@bot.message_handler(func=lambda m: m.text == "📖 Rules")
def rules_cmd(message):
    bot.send_message(
        message.chat.id,
        "📖 <b>TRADING RULES</b>\n\n"
        + get_setting("trading_rules", "")
    )


@bot.message_handler(func=lambda m: m.text == "📢 Notice")
def notice_cmd(message):
    bot.send_message(
        message.chat.id,
        get_setting("notice", "")
    )


@bot.message_handler(func=lambda m: m.text == "❓ Help")
def help_cmd(message):
    bot.send_message(
        message.chat.id,
        "❓ <b>HELP</b>\n\n"
        "Future Signals → upcoming signal\n"
        "Trading → money management\n"
        "Wallet → balance/withdrawal\n"
        "VIP → VIP information\n"
        "UID → UID verification\n\n"
        "Support: Admin-এর সাথে যোগাযোগ করুন।"
    )


# ============================================================
# LIVE USER
# ============================================================

@bot.message_handler(func=lambda m: m.text == "⚡ Live Signals")
def live_user(message):
    if get_setting("live_mode", "1") != "1":
        bot.send_message(
            message.chat.id,
            "🔴 Live mode disabled."
        )
        return

    with db_lock:
        conn = db()

        try:
            session = conn.execute(
                """
                SELECT *
                FROM live_sessions
                WHERE status='ACTIVE'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

            if not session:
                bot.send_message(
                    message.chat.id,
                    "📭 কোনো active live session নেই।"
                )
                return

            rows = conn.execute(
                """
                SELECT *
                FROM live_signals
                WHERE session_id=?
                ORDER BY id DESC
                LIMIT 5
                """,
                (session["id"],)
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        bot.send_message(
            message.chat.id,
            "⚡ Live session active, কিন্তু signal নেই।"
        )
        return

    for row in rows:
        bot.send_message(
            message.chat.id,
            "⚡ <b>LIVE SIGNAL</b>\n\n"
            f"💱 Pair: <b>{escape(row['pair'] or '-')}</b>\n"
            f"🎯 Direction: <b>{escape(row['direction'] or '-')}</b>\n"
            f"📈 Confidence: <b>{escape(row['confidence'] or '-')}</b>\n"
            f"Result: <b>{row['result'] or 'LIVE'}</b>"
        )


# ============================================================
# ADMIN COMMAND
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "👑 Admin Control"
)
def admin_cmd(message):
    if not is_admin(message.from_user.id):
        bot.send_message(
            message.chat.id,
            "⛔ Access denied."
        )
        return

    bot.send_message(
        message.chat.id,
        "👑 <b>ADMIN CONTROL</b>",
        reply_markup=admin_keyboard()
    )


@bot.callback_query_handler(
    func=lambda c: c.data == "adm_home"
)
def adm_home(call):
    if not is_admin(call.from_user.id):
        return

    bot.answer_callback_query(call.id)

    try:
        bot.edit_message_text(
            "👑 <b>ADMIN CONTROL</b>",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_keyboard()
        )
    except Exception:
        bot.send_message(
            call.message.chat.id,
            "👑 <b>ADMIN CONTROL</b>",
            reply_markup=admin_keyboard()
        )


# ============================================================
# ADMIN ADD SIGNAL
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_add_signal"
)
def adm_add_signal(call):
    if not can(call.from_user.id, "signals"):
        bot.answer_callback_query(
            call.id,
            "Access denied."
        )
        return

    states[call.from_user.id] = {
        "action": "add_signal"
    }

    bot.answer_callback_query(call.id)

    bot.send_message(
        call.message.chat.id,
        "➕ <b>ADD SIGNAL</b>\n\n"
        "এক লাইনে পাঠাও:\n\n"
        "<code>DATE|TIME|PAIR|DIRECTION|CONFIDENCE|AUDIENCE|SELECTED_IDS</code>\n\n"
        "Example:\n"
        "<code>"
        "2026-09-20|20:30|EURUSD|UP|95%|ALL|"
        "</code>\n\n"
        "VIP:\n"
        "<code>"
        "2026-09-20|20:30|EURUSD|DOWN|97%|VIP|"
        "</code>\n\n"
        "Selected:\n"
        "<code>"
        "2026-09-20|20:30|EURUSD|UP|96%|SELECTED|123,456"
        "</code>"
    )


def create_signal_from_text(text):
    parts = [x.strip() for x in text.split("|")]

    if len(parts) < 6:
        return False, "Format ভুল।"

    date_text, time_text, pair, direction, confidence, audience = parts[:6]
    selected = parts[6] if len(parts) >= 7 else ""

    try:
        datetime.strptime(
            f"{date_text} {time_text}",
            "%Y-%m-%d %H:%M"
        )
    except Exception:
        return False, "Date/time invalid."

    audience = audience.upper()

    if audience not in ("ALL", "VIP", "SELECTED"):
        return False, "Audience ALL/VIP/SELECTED হতে হবে।"

    if audience == "SELECTED" and not selected:
        return False, "Selected user IDs দিতে হবে।"

    signal_text = f"{pair} {direction}"

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                INSERT INTO signals(
                    pair,signal_date,signal_time,
                    direction,confidence,signal_text,
                    audience,selected_users,status,
                    created_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pair,
                    date_text,
                    time_text,
                    direction,
                    confidence,
                    signal_text,
                    audience,
                    selected,
                    "SCHEDULED",
                    utc_iso(now_utc())
                )
            )

            conn.commit()

        finally:
            conn.close()

    return True, "Signal added."


# ============================================================
# ADMIN SIGNAL LIST
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_signals"
)
def adm_signals(call):
    if not can(call.from_user.id, "signals"):
        return

    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT *
                FROM signals
                ORDER BY id DESC
                LIMIT 15
                """
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        text = "📭 No signals."
    else:
        text = "📊 <b>SIGNALS</b>\n\n"

        for r in rows:
            text += (
                f"#{r['id']} "
                f"{r['signal_date'] or '-'} "
                f"{r['signal_time'] or '-'} "
                f"{r['pair'] or ''} "
                f"{r['direction'] or ''}\n"
                f"Audience: {r['audience']}\n"
                f"Result: {r['result'] or '-'}\n\n"
            )

    bot.send_message(
        call.message.chat.id,
        text,
        reply_markup=back_admin_keyboard()
    )


# ============================================================
# ADMIN UID
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_uids"
)
def adm_uids(call):
    if not can(call.from_user.id, "uid"):
        return

    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT *
                FROM uid_submissions
                WHERE status='PENDING'
                ORDER BY id ASC
                LIMIT 20
                """
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        bot.send_message(
            call.message.chat.id,
            "📭 Pending UID নেই।"
        )
        return

    for row in rows:
        kb = types.InlineKeyboardMarkup()

        kb.row(
            types.InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"uid_ok_{row['id']}"
            ),
            types.InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"uid_no_{row['id']}"
            )
        )

        bot.send_message(
            call.message.chat.id,
            "🆔 <b>PENDING UID</b>\n\n"
            f"User: <code>{row['user_id']}</code>\n"
            f"UID: <code>{escape(row['uid'])}</code>",
            reply_markup=kb
        )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("uid_")
)
def uid_review(call):
    if not can(call.from_user.id, "uid"):
        return

    _, action, sid = call.data.split("_")
    sid = int(sid)

    with db_lock:
        conn = db()

        try:
            row = conn.execute(
                """
                SELECT *
                FROM uid_submissions
                WHERE id=? AND status='PENDING'
                """,
                (sid,)
            ).fetchone()

            if not row:
                bot.answer_callback_query(
                    call.id,
                    "Already processed."
                )
                return

            new_status = (
                "APPROVED"
                if action == "ok"
                else "REJECTED"
            )

            conn.execute(
                """
                UPDATE uid_submissions
                SET status=?,reviewed_at=?,reviewed_by=?
                WHERE id=?
                """,
                (
                    new_status,
                    utc_iso(now_utc()),
                    call.from_user.id,
                    sid
                )
            )

            if new_status == "APPROVED":
                conn.execute(
                    """
                    UPDATE users
                    SET status='VIP'
                    WHERE user_id=?
                    """,
                    (row["user_id"],)
                )

            conn.commit()

        finally:
            conn.close()

    bot.answer_callback_query(call.id, new_status)

    try:
        bot.send_message(
            row["user_id"],
            (
                "⭐ <b>VIP Approved</b>\n\n"
                "আপনার UID approved হয়েছে।"
                if new_status == "APPROVED"
                else
                "❌ <b>UID Rejected</b>\n\n"
                "Admin আপনার UID reject করেছে।"
            )
        )
    except Exception:
        pass


# ============================================================
# ADMIN VIP
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_vip"
)
def adm_vip(call):
    if not can(call.from_user.id, "vip"):
        return

    states[call.from_user.id] = {
        "action": "vip_manage"
    }

    bot.send_message(
        call.message.chat.id,
        "⭐ VIP Management\n\n"
        "Add:\n"
        "<code>ADD USER_ID DAYS</code>\n\n"
        "Remove:\n"
        "<code>REMOVE USER_ID</code>\n\n"
        "Example:\n"
        "<code>ADD 123456789 30</code>"
    )


# ============================================================
# ADMIN WITHDRAWALS
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_withdrawals"
)
def adm_withdrawals(call):
    if not can(call.from_user.id, "withdraw"):
        return

    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT *
                FROM withdrawals
                WHERE status='PENDING'
                ORDER BY id ASC
                LIMIT 20
                """
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        bot.send_message(
            call.message.chat.id,
            "📭 Pending withdrawal নেই।"
        )
        return

    for row in rows:
        kb = types.InlineKeyboardMarkup()

        kb.row(
            types.InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"wd_ok_{row['id']}"
            ),
            types.InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"wd_no_{row['id']}"
            )
        )

        bot.send_message(
            call.message.chat.id,
            "💸 <b>WITHDRAWAL</b>\n\n"
            f"ID: <code>{row['id']}</code>\n"
            f"User: <code>{row['user_id']}</code>\n"
            f"Amount: <b>${row['amount_cents']/100:.2f}</b>\n"
            f"Method: <b>{escape(row['method'])}</b>\n"
            f"Account: <code>{escape(row['account'])}</code>",
            reply_markup=kb
        )


@bot.callback_query_handler(
    func=lambda c: c.data.startswith("wd_")
)
def withdraw_review(call):
    if not can(call.from_user.id, "withdraw"):
        return

    _, action, wid = call.data.split("_")
    wid = int(wid)

    with db_lock:
        conn = db()

        try:
            row = conn.execute(
                """
                SELECT *
                FROM withdrawals
                WHERE id=? AND status='PENDING'
                """,
                (wid,)
            ).fetchone()

            if not row:
                bot.answer_callback_query(
                    call.id,
                    "Already processed."
                )
                return

            status = (
                "APPROVED"
                if action == "ok"
                else "REJECTED"
            )

            conn.execute(
                """
                UPDATE withdrawals
                SET status=?,reviewed_at=?,reviewed_by=?
                WHERE id=?
                """,
                (
                    status,
                    utc_iso(now_utc()),
                    call.from_user.id,
                    wid
                )
            )

            if status == "REJECTED":
                current = conn.execute(
                    """
                    SELECT wallet_cents
                    FROM users
                    WHERE user_id=?
                    """,
                    (row["user_id"],)
                ).fetchone()

                balance = (
                    current["wallet_cents"] +
                    row["amount_cents"]
                )

                conn.execute(
                    """
                    UPDATE users
                    SET wallet_cents=?
                    WHERE user_id=?
                    """,
                    (balance, row["user_id"])
                )

                conn.execute(
                    """
                    INSERT INTO wallet_transactions(
                        user_id,type,amount_cents,
                        balance_after_cents,note,created_at
                    )
                    VALUES(?,?,?,?,?,?)
                    """,
                    (
                        row["user_id"],
                        "WITHDRAW_REFUND",
                        row["amount_cents"],
                        balance,
                        f"Withdrawal #{wid} rejected",
                        utc_iso(now_utc())
                    )
                )

            conn.commit()

        finally:
            conn.close()

    bot.answer_callback_query(
        call.id,
        status
    )

    try:
        bot.send_message(
            row["user_id"],
            (
                f"✅ Withdrawal #{wid} approved."
                if status == "APPROVED"
                else
                f"❌ Withdrawal #{wid} rejected.\n"
                "Amount wallet-এ ফেরত দেওয়া হয়েছে।"
            )
        )
    except Exception:
        pass

    try:
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
        )
    except Exception:
        pass


# ============================================================
# ADMIN WALLET
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_wallet"
)
def adm_wallet(call):
    if not can(call.from_user.id, "wallet"):
        return

    states[call.from_user.id] = {
        "action": "wallet_adjust"
    }

    bot.send_message(
        call.message.chat.id,
        "💳 Format:\n"
        "<code>USER_ID AMOUNT</code>\n\n"
        "Add:\n"
        "<code>123456789 5</code>\n\n"
        "Deduct:\n"
        "<code>123456789 -2</code>"
    )


# ============================================================
# ADMIN BROADCAST
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_broadcast"
)
def adm_broadcast(call):
    if not can(call.from_user.id, "broadcast"):
        return

    states[call.from_user.id] = {
        "action": "broadcast"
    }

    bot.send_message(
        call.message.chat.id,
        "📢 Broadcast format:\n\n"
        "<code>ALL|Your message</code>\n"
        "<code>VIP|Your message</code>\n"
        "<code>SELECTED|123,456|Your message</code>"
    )


# ============================================================
# ADMIN USERS
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_users"
)
def adm_users(call):
    if not can(call.from_user.id, "users"):
        return

    with db_lock:
        conn = db()

        try:
            total = conn.execute(
                "SELECT COUNT(*) c FROM users"
            ).fetchone()["c"]

            vip = conn.execute(
                """
                SELECT COUNT(*) c
                FROM users
                WHERE status='VIP'
                """
            ).fetchone()["c"]

            blocked = conn.execute(
                """
                SELECT COUNT(*) c
                FROM users
                WHERE blocked=1
                """
            ).fetchone()["c"]

        finally:
            conn.close()

    bot.send_message(
        call.message.chat.id,
        "👥 <b>USERS</b>\n\n"
        f"Total: <b>{total}</b>\n"
        f"VIP: <b>{vip}</b>\n"
        f"Blocked: <b>{blocked}</b>",
        reply_markup=back_admin_keyboard()
    )


# ============================================================
# ADMIN ANALYTICS
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_analytics"
)
def adm_analytics(call):
    if not can(call.from_user.id, "analytics"):
        return

    with db_lock:
        conn = db()

        try:
            total = conn.execute(
                "SELECT COUNT(*) c FROM users"
            ).fetchone()["c"]

            vip = conn.execute(
                """
                SELECT COUNT(*) c
                FROM users
                WHERE status='VIP'
                """
            ).fetchone()["c"]

            signals = conn.execute(
                "SELECT COUNT(*) c FROM signals"
            ).fetchone()["c"]

            deliveries = conn.execute(
                "SELECT COUNT(*) c FROM signal_access"
            ).fetchone()["c"]

            votes = conn.execute(
                "SELECT COUNT(*) c FROM signal_votes"
            ).fetchone()["c"]

            pending_uid = conn.execute(
                """
                SELECT COUNT(*) c
                FROM uid_submissions
                WHERE status='PENDING'
                """
            ).fetchone()["c"]

            pending_wd = conn.execute(
                """
                SELECT COUNT(*) c
                FROM withdrawals
                WHERE status='PENDING'
                """
            ).fetchone()["c"]

        finally:
            conn.close()

    bot.send_message(
        call.message.chat.id,
        "📈 <b>ANALYTICS</b>\n\n"
        f"👥 Users: <b>{total}</b>\n"
        f"⭐ VIP: <b>{vip}</b>\n"
        f"📊 Signals: <b>{signals}</b>\n"
        f"👁 Deliveries: <b>{deliveries}</b>\n"
        f"🗳 Votes: <b>{votes}</b>\n"
        f"🆔 Pending UID: <b>{pending_uid}</b>\n"
        f"💸 Pending Withdrawals: <b>{pending_wd}</b>",
        reply_markup=back_admin_keyboard()
    )


# ============================================================
# ADMIN SETTINGS
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_settings"
)
def adm_settings(call):
    if not can(call.from_user.id, "settings"):
        return

    maintenance = (
        get_setting("maintenance", "0") == "1"
    )

    withdrawals = (
        get_setting(
            "withdrawals_enabled",
            "1"
        ) == "1"
    )

    live = (
        get_setting("live_mode", "1") == "1"
    )

    bot.send_message(
        call.message.chat.id,
        "⚙️ <b>SETTINGS</b>\n\n"
        f"Maintenance: "
        f"<b>{'ON' if maintenance else 'OFF'}</b>\n"
        f"Withdrawals: "
        f"<b>{'ON' if withdrawals else 'OFF'}</b>\n"
        f"Live: <b>{'ON' if live else 'OFF'}</b>\n\n"
        "Commands:\n"
        "/toggle_maintenance\n"
        "/toggle_withdrawals\n"
        "/toggle_live\n"
        "/set_freelimit 4\n"
        "/set_minwithdraw 5\n"
        "/set_refbonus 1\n"
        "/set_basepercent 1\n"
        "/set_m1percent 2\n"
        "/set_maxloss 5\n"
        "/set_maxtrades 20\n"
        "/set_target 1.85",
        reply_markup=back_admin_keyboard()
    )


# ============================================================
# SETTINGS COMMANDS
# ============================================================

@bot.message_handler(commands=["toggle_maintenance"])
def toggle_maintenance(message):
    if not is_master(message.from_user.id):
        return

    old = get_setting("maintenance", "0")
    set_setting(
        "maintenance",
        "0" if old == "1" else "1"
    )

    bot.send_message(
        message.chat.id,
        "Maintenance "
        + (
            "OFF"
            if old == "1"
            else "ON"
        )
    )


@bot.message_handler(commands=["toggle_withdrawals"])
def toggle_withdrawals(message):
    if not is_master(message.from_user.id):
        return

    old = get_setting(
        "withdrawals_enabled",
        "1"
    )

    set_setting(
        "withdrawals_enabled",
        "0" if old == "1" else "1"
    )

    bot.send_message(
        message.chat.id,
        "Withdrawals "
        + (
            "OFF"
            if old == "1"
            else "ON"
        )
    )


@bot.message_handler(commands=["toggle_live"])
def toggle_live(message):
    if not is_master(message.from_user.id):
        return

    old = get_setting(
        "live_mode",
        "1"
    )

    set_setting(
        "live_mode",
        "0" if old == "1" else "1"
    )

    bot.send_message(
        message.chat.id,
        "Live mode "
        + (
            "OFF"
            if old == "1"
            else "ON"
        )
    )


def simple_setting_command(
    message,
    command,
    key,
    cast=float,
    minimum=None
):
    if not is_master(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) != 2:
        bot.send_message(
            message.chat.id,
            f"Usage: /{command} VALUE"
        )
        return

    try:
        value = cast(parts[1])

        if minimum is not None and value < minimum:
            raise ValueError

        set_setting(key, value)

        bot.send_message(
            message.chat.id,
            f"✅ {key} = <b>{value}</b>"
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Invalid value."
        )


@bot.message_handler(commands=["set_freelimit"])
def set_freelimit(message):
    simple_setting_command(
        message,
        "set_freelimit",
        "free_limit",
        int,
        0
    )


@bot.message_handler(commands=["set_minwithdraw"])
def set_minwithdraw(message):
    simple_setting_command(
        message,
        "set_minwithdraw",
        "min_withdraw",
        lambda x: int(float(x) * 100),
        0
    )


@bot.message_handler(commands=["set_refbonus"])
def set_refbonus(message):
    simple_setting_command(
        message,
        "set_refbonus",
        "referral_bonus",
        lambda x: int(float(x) * 100),
        0
    )


@bot.message_handler(commands=["set_basepercent"])
def set_basepercent(message):
    simple_setting_command(
        message,
        "set_basepercent",
        "base_percent",
        float,
        0
    )


@bot.message_handler(commands=["set_m1percent"])
def set_m1percent(message):
    simple_setting_command(
        message,
        "set_m1percent",
        "m1_percent",
        float,
        0
    )


@bot.message_handler(commands=["set_maxloss"])
def set_maxloss(message):
    simple_setting_command(
        message,
        "set_maxloss",
        "max_daily_loss",
        float,
        0
    )


@bot.message_handler(commands=["set_maxtrades"])
def set_maxtrades(message):
    simple_setting_command(
        message,
        "set_maxtrades",
        "max_trades",
        int,
        0
    )


@bot.message_handler(commands=["set_target"])
def set_target(message):
    simple_setting_command(
        message,
        "set_target",
        "profit_target",
        float,
        0
    )


# ============================================================
# ADMIN RISK
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_risk"
)
def adm_risk(call):
    if not can(call.from_user.id, "risk"):
        return

    bot.send_message(
        call.message.chat.id,
        "🛠 <b>RISK CONTROL</b>\n\n"
        f"Base %: {get_setting('base_percent')}\n"
        f"M1 %: {get_setting('m1_percent')}\n"
        f"Max Daily Loss %: {get_setting('max_daily_loss')}\n"
        f"Max Trades: {get_setting('max_trades')}\n"
        f"Profit Target %: {get_setting('profit_target')}\n\n"
        "User-specific risk:\n"
        "/risk USER_ID BASE% MAX_M1_DOLLAR MAX_LOSS% MAX_TRADES STOP\n\n"
        "Example:\n"
        "<code>/risk 123456789 1 5 5 10 0</code>"
    )


@bot.message_handler(commands=["risk"])
def risk_command(message):
    if not can(message.from_user.id, "risk"):
        return

    parts = message.text.split()

    if len(parts) != 7:
        bot.send_message(
            message.chat.id,
            "Format:\n"
            "<code>/risk USER BASE MAX_M1 MAX_LOSS MAX_TRADES STOP</code>"
        )
        return

    try:
        uid = int(parts[1])
        base = float(parts[2])
        max_m1 = int(float(parts[3]) * 100)
        max_loss = float(parts[4])
        max_trades = int(parts[5])
        stop = int(parts[6])

        with db_lock:
            conn = db()

            try:
                conn.execute(
                    """
                    INSERT INTO risk_profiles(
                        user_id,base_percent,
                        max_m1_amount_cents,
                        max_daily_loss_percent,
                        max_trades,stop_trading
                    )
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(user_id)
                    DO UPDATE SET
                        base_percent=excluded.base_percent,
                        max_m1_amount_cents=excluded.max_m1_amount_cents,
                        max_daily_loss_percent=excluded.max_daily_loss_percent,
                        max_trades=excluded.max_trades,
                        stop_trading=excluded.stop_trading
                    """,
                    (
                        uid,
                        base,
                        max_m1,
                        max_loss,
                        max_trades,
                        stop
                    )
                )

                conn.commit()

            finally:
                conn.close()

        bot.send_message(
            message.chat.id,
            "✅ User risk updated."
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Invalid risk values."
        )


# ============================================================
# SUB ADMIN
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_subadmins"
)
def adm_subadmins(call):
    if not is_master(call.from_user.id):
        return

    bot.send_message(
        call.message.chat.id,
        "🛡 <b>SUB-ADMIN</b>\n\n"
        "Add:\n"
        "<code>/addsub USER_ID permissions</code>\n\n"
        "Permissions:\n"
        + ", ".join(sorted(ALL_PERMISSIONS))
        + "\n\n"
        "Example:\n"
        "<code>/addsub 123456789 signals,users,analytics</code>\n\n"
        "Remove:\n"
        "<code>/removesub USER_ID</code>"
    )


@bot.message_handler(commands=["addsub"])
def addsub_command(message):
    if not is_master(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)

    if len(parts) != 3:
        return

    uid = int(parts[1])

    permissions = {
        x.strip()
        for x in parts[2].split(",")
        if x.strip() in ALL_PERMISSIONS
    }

    if not permissions:
        bot.send_message(
            message.chat.id,
            "❌ Permission নেই।"
        )
        return

    add_subadmin(
        uid,
        permissions
    )

    bot.send_message(
        message.chat.id,
        "✅ Sub-admin added."
    )


@bot.message_handler(commands=["removesub"])
def removesub_command(message):
    if not is_master(message.from_user.id):
        return

    parts = message.text.split()

    if len(parts) != 2:
        return

    remove_subadmin(
        int(parts[1])
    )

    bot.send_message(
        message.chat.id,
        "✅ Sub-admin removed."
    )


# ============================================================
# LIVE ADMIN
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_live"
)
def adm_live(call):
    if not can(call.from_user.id, "live"):
        return

    bot.send_message(
        call.message.chat.id,
        "⚡ <b>LIVE SESSION</b>\n\n"
        "/startlive SESSION_NAME\n"
        "/livesignal PAIR|DIRECTION|CONFIDENCE\n"
        "/endlive"
    )


@bot.message_handler(commands=["startlive"])
def startlive(message):
    if not can(message.from_user.id, "live"):
        return

    name = message.text.replace(
        "/startlive",
        "",
        1
    ).strip() or "Live Session"

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                UPDATE live_sessions
                SET status='ENDED',ended_at=?
                WHERE status='ACTIVE'
                """,
                (utc_iso(now_utc()),)
            )

            conn.execute(
                """
                INSERT INTO live_sessions(
                    name,status,started_at
                )
                VALUES(?,?,?)
                """,
                (
                    name,
                    "ACTIVE",
                    utc_iso(now_utc())
                )
            )

            conn.commit()

        finally:
            conn.close()

    bot.send_message(
        message.chat.id,
        f"🟢 Live session started: <b>{escape(name)}</b>"
    )


@bot.message_handler(commands=["livesignal"])
def livesignal(message):
    if not can(message.from_user.id, "live"):
        return

    text = message.text.replace(
        "/livesignal",
        "",
        1
    ).strip()

    parts = [x.strip() for x in text.split("|")]

    if len(parts) != 3:
        bot.send_message(
            message.chat.id,
            "Format:\n"
            "<code>/livesignal PAIR|DIRECTION|CONFIDENCE</code>"
        )
        return

    with db_lock:
        conn = db()

        try:
            session = conn.execute(
                """
                SELECT *
                FROM live_sessions
                WHERE status='ACTIVE'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

            if not session:
                bot.send_message(
                    message.chat.id,
                    "❌ Active session নেই।"
                )
                return

            conn.execute(
                """
                INSERT INTO live_signals(
                    session_id,pair,direction,
                    confidence,signal_text,created_at
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    session["id"],
                    parts[0],
                    parts[1],
                    parts[2],
                    f"{parts[0]} {parts[1]}",
                    utc_iso(now_utc())
                )
            )

            conn.commit()

        finally:
            conn.close()

    # Send immediately to users who have notifications enabled.
    with db_lock:
        conn = db()

        try:
            users = conn.execute(
                """
                SELECT user_id
                FROM users
                WHERE blocked=0 AND notifications=1
                """
            ).fetchall()

        finally:
            conn.close()

    for user in users:
        try:
            bot.send_message(
                user["user_id"],
                "⚡ <b>LIVE SIGNAL</b>\n\n"
                f"💱 Pair: <b>{escape(parts[0])}</b>\n"
                f"🎯 Direction: <b>{escape(parts[1])}</b>\n"
                f"📈 Confidence: <b>{escape(parts[2])}</b>"
            )
        except Exception:
            pass

    bot.send_message(
        message.chat.id,
        "⚡ Live signal sent."
    )


@bot.message_handler(commands=["endlive"])
def endlive(message):
    if not can(message.from_user.id, "live"):
        return

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                UPDATE live_sessions
                SET status='ENDED',ended_at=?
                WHERE status='ACTIVE'
                """,
                (utc_iso(now_utc()),)
            )

            conn.commit()

        finally:
            conn.close()

    bot.send_message(
        message.chat.id,
        "🔴 Live session ended."
    )


# ============================================================
# ADMIN RESULT
# ============================================================

@bot.message_handler(commands=["result"])
def admin_result(message):
    if not can(message.from_user.id, "results"):
        return

    parts = message.text.split()

    if len(parts) != 3:
        bot.send_message(
            message.chat.id,
            "Format:\n"
            "<code>/result SIGNAL_ID WIN</code>"
        )
        return

    sid = int(parts[1])
    result = parts[2].upper()

    if result not in ("WIN", "LOSS", "SKIP"):
        return

    set_signal_result(
        sid,
        result
    )

    # Apply MM result to users who accessed this signal.
    with db_lock:
        conn = db()

        try:
            users = conn.execute(
                """
                SELECT user_id
                FROM signal_access
                WHERE signal_id=?
                """,
                (sid,)
            ).fetchall()

        finally:
            conn.close()

    for u in users:
        try:
            record_trade(
                u["user_id"],
                sid,
                result
            )
        except Exception:
            logger.exception(
                "trade result error"
            )

    bot.send_message(
        message.chat.id,
        f"✅ Signal #{sid} result = <b>{result}</b>"
    )


# ============================================================
# RESULT REVEAL
# ============================================================

@bot.message_handler(commands=["reveal"])
def reveal_result(message):
    if not can(message.from_user.id, "results"):
        return

    parts = message.text.split()

    if len(parts) != 2:
        return

    set_setting(
        "result_reveal",
        "1" if parts[1].lower() == "on" else "0"
    )

    bot.send_message(
        message.chat.id,
        "Result reveal "
        + (
            "ON"
            if get_setting("result_reveal") == "1"
            else "OFF"
        )
    )


# ============================================================
# ADMIN CLEAR FUTURE
# ============================================================

@bot.callback_query_handler(
    func=lambda c: c.data == "adm_clear"
)
def adm_clear(call):
    if not can(call.from_user.id, "signals"):
        return

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                DELETE FROM signals
                WHERE status='SCHEDULED'
                """
            )
            conn.commit()

        finally:
            conn.close()

    bot.answer_callback_query(
        call.id,
        "Future signals cleared."
    )

    bot.send_message(
        call.message.chat.id,
        "🗑 Future scheduled signals cleared."
    )


# ============================================================
# STATE HANDLER
# ============================================================

@bot.message_handler(
    content_types=["text"],
    func=lambda m: m.from_user.id in states
)
def state_handler(message):
    uid = message.from_user.id
    action = states.get(uid, {}).get("action")

    if message.text == "/cancel":
        states.pop(uid, None)

        bot.send_message(
            message.chat.id,
            "❌ Cancelled.",
            reply_markup=main_keyboard(uid)
        )
        return

    # UID
    if action == "uid":
        ok, msg = save_uid_value(
            uid,
            message.text
        )

        if ok:
            states.pop(uid, None)

            bot.send_message(
                message.chat.id,
                "✅ UID submitted.\n"
                "Admin review করবে।",
                reply_markup=main_keyboard(uid)
            )

            try:
                bot.send_message(
                    ADMIN_ID,
                    "🆔 <b>New UID Pending</b>\n\n"
                    f"User: <code>{uid}</code>\n"
                    f"UID: <code>{escape(message.text)}</code>"
                )
            except Exception:
                pass

        else:
            bot.send_message(
                message.chat.id,
                f"❌ {msg}"
            )

        return

    # Balance
    if action == "set_balance":
        try:
            amount = float(
                message.text.strip()
            )

            if amount <= 0:
                raise ValueError

            set_daily_balance(
                uid,
                int(amount * 100)
            )

            states.pop(uid, None)

            bot.send_message(
                message.chat.id,
                f"✅ Today's trading balance set: "
                f"<b>${amount:.2f}</b>",
                reply_markup=main_keyboard(uid)
            )

        except Exception:
            bot.send_message(
                message.chat.id,
                "❌ শুধু valid amount পাঠাও।"
            )

        return

    # Withdrawal
    if action == "withdraw_amount":
        try:
            amount = float(
                message.text.strip()
            )

            cents = int(
                round(amount * 100)
            )

            minimum = int(
                get_setting(
                    "min_withdraw",
                    DEFAULT_MIN_WITHDRAW
                )
            )

            user = get_user(uid)

            if cents < minimum:
                raise ValueError(
                    "Minimum withdrawal"
                )

            if cents > user["wallet_cents"]:
                raise ValueError(
                    "Insufficient balance"
                )

            # Hold funds immediately.
            ok, balance = wallet_adjust(
                uid,
                -cents,
                "Withdrawal hold",
                "WITHDRAW_HOLD"
            )

            if not ok:
                raise ValueError

            states[uid] = {
                "action": "withdraw_details",
                "amount": cents
            }

            bot.send_message(
                message.chat.id,
                "💳 এখন payment method এবং account পাঠাও।\n\n"
                "Example:\n"
                "<code>bKash 017XXXXXXXX</code>"
            )

        except ValueError as e:
            bot.send_message(
                message.chat.id,
                f"❌ {e}"
            )

        return

    if action == "withdraw_details":
        parts = message.text.strip().split(
            maxsplit=1
        )

        if len(parts) != 2:
            bot.send_message(
                message.chat.id,
                "Format: <code>bKash 017XXXXXXXX</code>"
            )
            return

        amount = states[uid]["amount"]

        with db_lock:
            conn = db()

            try:
                conn.execute(
                    """
                    INSERT INTO withdrawals(
                        user_id,amount_cents,
                        method,account,
                        status,created_at
                    )
                    VALUES(?,?,?,?,?,?)
                    """,
                    (
                        uid,
                        amount,
                        parts[0],
                        parts[1],
                        "PENDING",
                        utc_iso(now_utc())
                    )
                )

                conn.commit()

            finally:
                conn.close()

        states.pop(uid, None)

        bot.send_message(
            message.chat.id,
            "✅ Withdrawal request submitted.",
            reply_markup=main_keyboard(uid)
        )

        try:
            bot.send_message(
                ADMIN_ID,
                "💸 <b>New Withdrawal</b>\n\n"
                f"User: <code>{uid}</code>\n"
                f"Amount: <b>${amount/100:.2f}</b>\n"
                f"Method: <b>{escape(parts[0])}</b>\n"
                f"Account: <code>{escape(parts[1])}</code>"
            )
        except Exception:
            pass

        return

    # Wallet adjustment
    if action == "wallet_adjust":
        if not can(uid, "wallet"):
            states.pop(uid, None)
            return

        parts = message.text.split()

        try:
            target = int(parts[0])
            amount = float(parts[1])

            ok, balance = wallet_adjust(
                target,
                int(amount * 100),
                f"Admin {uid}",
                "ADMIN_ADJUST"
            )

            if not ok:
                raise ValueError

            states.pop(uid, None)

            bot.send_message(
                message.chat.id,
                f"✅ Wallet updated.\n"
                f"New balance: <b>${balance/100:.2f}</b>"
            )

        except Exception:
            bot.send_message(
                message.chat.id,
                "❌ Format ভুল।"
            )

        return

    # Add signal
    if action == "add_signal":
        if not can(uid, "signals"):
            states.pop(uid, None)
            return

        ok, msg = create_signal_from_text(
            message.text
        )

        if ok:
            states.pop(uid, None)

        bot.send_message(
            message.chat.id,
            ("✅ " if ok else "❌ ") + msg
        )

        return

    # VIP
    if action == "vip_manage":
        if not can(uid, "vip"):
            states.pop(uid, None)
            return

        parts = message.text.split()

        try:
            if parts[0].upper() == "ADD":
                target = int(parts[1])
                days = int(parts[2])

                set_vip(
                    target,
                    days
                )

                bot.send_message(
                    message.chat.id,
                    "⭐ VIP added."
                )

            elif parts[0].upper() == "REMOVE":
                target = int(parts[1])

                remove_vip(target)

                bot.send_message(
                    message.chat.id,
                    "❌ VIP removed."
                )

            states.pop(uid, None)

        except Exception:
            bot.send_message(
                message.chat.id,
                "❌ Format ভুল।"
            )

        return

    # Broadcast
    if action == "broadcast":
        if not can(uid, "broadcast"):
            states.pop(uid, None)
            return

        text = message.text
        parts = text.split("|", 2)

        if len(parts) < 2:
            bot.send_message(
                message.chat.id,
                "Format ভুল।"
            )
            return

        audience = parts[0].upper()
        msg = parts[-1]

        with db_lock:
            conn = db()

            try:
                if audience == "ALL":
                    users = conn.execute(
                        """
                        SELECT user_id
                        FROM users
                        WHERE blocked=0
                        """
                    ).fetchall()

                elif audience == "VIP":
                    users = conn.execute(
                        """
                        SELECT user_id
                        FROM users
                        WHERE status='VIP'
                        AND blocked=0
                        """
                    ).fetchall()

                elif audience == "SELECTED":
                    ids = parts[1].split(",")
                    users = [
                        {"user_id": int(x)}
                        for x in ids
                        if x.strip().isdigit()
                    ]

                else:
                    users = []

            finally:
                conn.close()

        sent = 0

        for user in users:
            try:
                bot.send_message(
                    user["user_id"],
                    msg
                )
                sent += 1
            except Exception:
                pass

        states.pop(uid, None)

        bot.send_message(
            message.chat.id,
            f"📢 Broadcast sent: <b>{sent}</b>"
        )

        return


# ============================================================
# SCHEDULED SIGNAL WORKER
# ============================================================

def scheduled_signal_worker():
    while True:
        try:
            now = now_bd()

            with db_lock:
                conn = db()

                try:
                    rows = conn.execute(
                        """
                        SELECT *
                        FROM signals
                        WHERE status='SCHEDULED'
                        """
                    ).fetchall()

                finally:
                    conn.close()

            for row in rows:
                dt = signal_datetime(row)

                if not dt:
                    continue

                if dt > now:
                    continue

                # Mark sent before delivery to avoid duplicate sends.
                with db_lock:
                    conn = db()

                    try:
                        conn.execute(
                            """
                            UPDATE signals
                            SET status='SENT',delivered=1
                            WHERE id=? AND status='SCHEDULED'
                            """,
                            (row["id"],)
                        )
                        conn.commit()

                    finally:
                        conn.close()

                with db_lock:
                    conn = db()

                    try:
                        users = conn.execute(
                            """
                            SELECT user_id,notifications
                            FROM users
                            WHERE blocked=0
                            """
                        ).fetchall()

                    finally:
                        conn.close()

                for user in users:
                    if not user["notifications"]:
                        continue

                    if not audience_allowed(
                        row,
                        user["user_id"]
                    ):
                        continue

                    ok, reason = deliver_signal(
                        user["user_id"],
                        row["id"]
                    )

                    if not ok:
                        continue

                    try:
                        kb = types.InlineKeyboardMarkup(
                            row_width=3
                        )

                        kb.row(
                            types.InlineKeyboardButton(
                                "✅ WIN",
                                callback_data=
                                f"vote_WIN_{row['id']}"
                            ),
                            types.InlineKeyboardButton(
                                "❌ LOSS",
                                callback_data=
                                f"vote_LOSS_{row['id']}"
                            ),
                            types.InlineKeyboardButton(
                                "⏭ SKIP",
                                callback_data=
                                f"vote_SKIP_{row['id']}"
                            )
                        )

                        bot.send_message(
                            user["user_id"],
                            format_signal(row),
                            reply_markup=kb
                        )

                    except Exception:
                        pass

            time.sleep(10)

        except Exception:
            logger.exception(
                "scheduled worker error"
            )
            time.sleep(10)


# ============================================================
# VIP REMINDER WORKER
# ============================================================

def vip_reminder_worker():
    while True:
        try:
            now = now_utc()

            with db_lock:
                conn = db()

                try:
                    rows = conn.execute(
                        """
                        SELECT *
                        FROM users
                        WHERE status='VIP'
                        AND vip_until IS NOT NULL
                        """
                    ).fetchall()

                finally:
                    conn.close()

            for user in rows:
                try:
                    expiry = datetime.fromisoformat(
                        user["vip_until"]
                    )

                    hours = (
                        expiry - now
                    ).total_seconds() / 3600

                    if 0 < hours <= 24:
                        bot.send_message(
                            user["user_id"],
                            "⚠️ <b>VIP Expiry Reminder</b>\n\n"
                            "আপনার VIP 24 ঘণ্টার মধ্যে expire হবে।"
                        )

                    elif hours <= 0:
                        remove_vip(
                            user["user_id"]
                        )

                        bot.send_message(
                            user["user_id"],
                            "ℹ️ আপনার VIP expired হয়েছে।"
                        )

                except Exception:
                    pass

            time.sleep(3600)

        except Exception:
            logger.exception(
                "VIP reminder error"
            )
            time.sleep(3600)


# ============================================================
# BACKUP
# ============================================================

def backup_database():
    try:
        if not os.path.exists(DB_FILE):
            return

        stamp = now_bd().strftime(
            "%Y%m%d_%H%M%S"
        )

        path = os.path.join(
            BACKUP_DIR,
            f"bot_database_{stamp}.db"
        )

        src = sqlite3.connect(DB_FILE)
        dst = sqlite3.connect(path)

        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        files = sorted(
            [
                os.path.join(
                    BACKUP_DIR,
                    f
                )
                for f in os.listdir(
                    BACKUP_DIR
                )
                if f.endswith(".db")
            ],
            key=os.path.getmtime,
            reverse=True
        )

        for old in files[7:]:
            try:
                os.remove(old)
            except Exception:
                pass

        logger.info(
            "Backup created: %s",
            path
        )

    except Exception:
        logger.exception(
            "Backup failed"
        )


def backup_loop():
    while True:
        time.sleep(6 * 60 * 60)
        backup_database()


# ============================================================
# FALLBACK
# ============================================================

@bot.message_handler(
    content_types=["text"]
)
def fallback(message):
    if message.text.startswith("/"):
        return

    if message.from_user.id in states:
        return

    bot.send_message(
        message.chat.id,
        "আমি এই option বুঝতে পারিনি। নিচের menu ব্যবহার করুন।",
        reply_markup=main_keyboard(
            message.from_user.id
        )
    )


# ============================================================
# STARTUP
# IMPORTANT: polling MUST be last
# ============================================================

def main():
    init_db()
    backup_database()

    threading.Thread(
        target=backup_loop,
        daemon=True
    ).start()

    threading.Thread(
        target=scheduled_signal_worker,
        daemon=True
    ).start()

    threading.Thread(
        target=vip_reminder_worker,
        daemon=True
    ).start()

    logger.info(
        "%s starting...",
        BRAND
    )

    while True:
        try:
            bot.infinity_polling(
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30
            )

        except Exception:
            logger.exception(
                "Polling crashed. Restarting..."
            )
            time.sleep(5)


if __name__ == "__main__":
    main()
