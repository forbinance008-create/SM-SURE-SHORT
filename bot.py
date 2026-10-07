import os
import re
import time
import sqlite3
import threading
import logging
import base64
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from html import escape
import io

try:
    from PIL import Image
    import numpy as np
except ImportError:  # keep the bot alive; Screenshot Signal reports the problem
    Image = None
    np = None

import telebot
from telebot import types


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "6470135702"))

DB_FILE = os.getenv("DB_FILE", "bot_database.db")
BACKUP_DIR = "backups"

BD_TZ = ZoneInfo("Asia/Dhaka")
UTC = timezone.utc

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing. Add BOT_TOKEN in Railway Variables."
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

DB_LOCK = threading.RLock()

# Temporary conversation states.
# Permanent user data stays in SQLite.
STATES = {}


# ============================================================
# SIGNAL DIRECTION
# ============================================================

UP_WORDS = {
    "UP",
    "BUY",
    "CALL"
}

DOWN_WORDS = {
    "DOWN",
    "SELL",
    "PUT"
}


def normalize_direction(value):
    value = value.strip().upper()

    if value in UP_WORDS:
        return "UP"

    if value in DOWN_WORDS:
        return "DOWN"

    return None


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


def init_db():

    with DB_LOCK:

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
                referral_paid INTEGER NOT NULL DEFAULT 0,

                cycle_key TEXT,
                free_used INTEGER NOT NULL DEFAULT 0,

                notify INTEGER NOT NULL DEFAULT 1,
                blocked INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                signal_date TEXT NOT NULL,
                signal_time TEXT NOT NULL,
                signal_at_utc TEXT NOT NULL,

                pair TEXT NOT NULL,
                direction TEXT NOT NULL,

                confidence TEXT NOT NULL DEFAULT '95–99%',

                audience TEXT NOT NULL DEFAULT 'ALL',

                active INTEGER NOT NULL DEFAULT 1,
                auto_sent INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL,

                UNIQUE(
                    signal_date,
                    signal_time,
                    pair,
                    direction
                )
            );


            CREATE TABLE IF NOT EXISTS deliveries (
                signal_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,

                delivered_at TEXT NOT NULL,
                source TEXT NOT NULL,

                PRIMARY KEY (
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


            CREATE TABLE IF NOT EXISTS votes (
                signal_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,

                vote TEXT NOT NULL,
                created_at TEXT NOT NULL,

                PRIMARY KEY(
                    signal_id,
                    user_id
                ),

                FOREIGN KEY(signal_id)
                    REFERENCES signals(id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS results (
                signal_id INTEGER PRIMARY KEY,

                result TEXT NOT NULL,
                revealed INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL,

                FOREIGN KEY(signal_id)
                    REFERENCES signals(id)
                    ON DELETE CASCADE
            );


            CREATE TABLE IF NOT EXISTS uid_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id INTEGER NOT NULL,
                quotex_uid TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'PENDING',

                created_at TEXT NOT NULL,
                reviewed_at TEXT,

                UNIQUE(quotex_uid)
            );


            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL UNIQUE,

                bonus_cents INTEGER NOT NULL,

                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS wallet_tx (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id INTEGER NOT NULL,

                amount_cents INTEGER NOT NULL,

                kind TEXT NOT NULL,
                note TEXT,

                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                user_id INTEGER NOT NULL,

                amount_cents INTEGER NOT NULL,

                method TEXT NOT NULL,
                account TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'PENDING',

                created_at TEXT NOT NULL,
                reviewed_at TEXT
            );


            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                permissions TEXT NOT NULL DEFAULT ''
            );


            CREATE TABLE IF NOT EXISTS selected_users (
                signal_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,

                PRIMARY KEY(
                    signal_id,
                    user_id
                )
            );


            CREATE TABLE IF NOT EXISTS notify_targets (
                chat_id TEXT PRIMARY KEY,

                title TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            );


            CREATE TABLE IF NOT EXISTS live_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                started_at TEXT NOT NULL,
                ended_at TEXT,

                active INTEGER NOT NULL DEFAULT 1
            );


            CREATE TABLE IF NOT EXISTS live_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                session_id INTEGER NOT NULL,

                pair TEXT NOT NULL,
                signal_time TEXT NOT NULL,
                direction TEXT NOT NULL,

                confidence TEXT NOT NULL DEFAULT '95–99%',

                created_at TEXT NOT NULL
            );

            """)


            defaults = {

                "free_limit": "4",

                "referral_bonus": "1.00",

                "min_withdraw": "5.00",

                "withdrawals": "ON",

                "maintenance": "OFF",

                "auto_send": "ON",

                "audience": "ALL",

                "live_mode": "ON",

                "result_reveal": "OFF",

                "confidence": "95–99%",

                "notice": "",

                "welcome":
                    "🎉 <b>Welcome to SM QUATEX SURE SHORT</b>\n\n"
                    "Your account is ready.",

                "trading_rules":
                    "📜 <b>TRADING CONTRACT</b>\n\n"
                    "Follow the signal time and direction carefully. "
                    "Use your own risk limits."

            }


            for key, value in defaults.items():

                conn.execute(
                    """
                    INSERT OR IGNORE INTO settings(
                        key,
                        value
                    )
                    VALUES(?,?)
                    """,
                    (key, value)
                )


            conn.commit()

        finally:

            conn.close()


def get_setting(key, default=""):

    with DB_LOCK:

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

            if row:
                return row["value"]

            return default

        finally:

            conn.close()


def set_setting(key, value):

    with DB_LOCK:

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
# TIME
# ============================================================

def now_bd():
    return datetime.now(BD_TZ)


def now_utc():
    return datetime.now(UTC)


def utc_iso(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def bd_from_iso(value):
    return datetime.fromisoformat(value).astimezone(BD_TZ)


def cycle_key(date_value=None):

    if date_value is None:
        date_value = now_bd().date()

    epoch = datetime(
        2026,
        1,
        1,
        tzinfo=BD_TZ
    ).date()

    days = (date_value - epoch).days

    start_days = (days // 2) * 2

    return (
        epoch +
        timedelta(days=start_days)
    ).isoformat()


def money(cents):

    return f"${cents / 100:.2f}"


# ============================================================
# ADMIN
# ============================================================

def is_master(user_id):

    return int(user_id) == ADMIN_ID


def get_permissions(user_id):

    if is_master(user_id):
        return {"all"}

    with DB_LOCK:

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

            return {
                x.strip()
                for x in row["permissions"].split(",")
                if x.strip()
            }

        finally:

            conn.close()


def can(user_id, permission):

    if is_master(user_id):
        return True

    permissions = get_permissions(user_id)

    return (
        permission in permissions
        or "all" in permissions
    )


# ============================================================
# USER
# ============================================================

def register_user(telegram_user, referred_by=None):

    uid = telegram_user.id

    current_time = utc_iso(now_utc())

    with DB_LOCK:

        conn = db()

        try:

            existing = conn.execute(
                """
                SELECT *
                FROM users
                WHERE user_id=?
                """,
                (uid,)
            ).fetchone()


            if not existing:

                ref = None

                if referred_by and referred_by != uid:

                    ref = referred_by

                    ref_exists = conn.execute(
                        """
                        SELECT 1
                        FROM users
                        WHERE user_id=?
                        """,
                        (ref,)
                    ).fetchone()

                    if not ref_exists:
                        ref = None


                conn.execute(
                    """
                    INSERT INTO users(
                        user_id,
                        username,
                        first_name,
                        referred_by,
                        cycle_key,
                        created_at,
                        last_seen
                    )
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        uid,
                        telegram_user.username or "",
                        telegram_user.first_name or "",
                        ref,
                        cycle_key(),
                        current_time,
                        current_time
                    )
                )

            else:

                conn.execute(
                    """
                    UPDATE users
                    SET
                        username=?,
                        first_name=?,
                        last_seen=?
                    WHERE user_id=?
                    """,
                    (
                        telegram_user.username or "",
                        telegram_user.first_name or "",
                        current_time,
                        uid
                    )
                )


            conn.commit()

        finally:

            conn.close()


def get_user(user_id):

    with DB_LOCK:

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


def reset_cycle_if_needed(user_id):

    with DB_LOCK:

        conn = db()

        try:

            row = conn.execute(
                """
                SELECT cycle_key
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            current_cycle = cycle_key()

            if row and row["cycle_key"] != current_cycle:

                conn.execute(
                    """
                    UPDATE users
                    SET
                        cycle_key=?,
                        free_used=0
                    WHERE user_id=?
                    """,
                    (
                        current_cycle,
                        user_id
                    )
                )

                conn.commit()

        finally:

            conn.close()


def clear_state(user_id):

    STATES.pop(user_id, None)


def maintenance_blocked(user_id):

    return (
        get_setting("maintenance", "OFF") == "ON"
        and not is_master(user_id)
    )


# ============================================================
# KEYBOARDS
# ALL BUTTONS ARE REPLY KEYBOARD
# ============================================================

def make_keyboard(rows):

    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )

    for row in rows:
        keyboard.row(*row)

    return keyboard


def _vip_button_label(uid):
    try:
        u = get_user(uid)
        if u and vip_is_active(u):
            if u["vip_until"]:
                try:
                    d = (datetime.fromisoformat(u["vip_until"]).astimezone(BD_TZ) - now_bd()).days
                    return f"⭐ VIP: {max(d, 0)}d"
                except Exception:
                    pass
            return "⭐ VIP: ♾️"
    except Exception:
        pass
    return "⭐ JOIN VIP"


def _safe_vip_label(user_id):
    try:
        return _vip_button_label(user_id)
    except Exception:
        return "⭐ JOIN VIP"


def main_keyboard(user_id):

    rows = [
        ["📊 Signals", "💰 My Account"],
        ["💵 Money Management", "🎁 Referral"],
        ["⚙️ Settings", _safe_vip_label(user_id)],
    ]

    if is_master(user_id) or get_permissions(user_id):
        rows.append(["👑 Admin Control"])

    return make_keyboard(rows)


def back_keyboard():

    return make_keyboard([
        [
            "🔙 Back",
            "🏠 Main Menu"
        ]
    ])


def admin_keyboard():

    return make_keyboard([

        [
            "➕ Add Future Signals",
            "📋 Future Signal List"
        ],

        [
            "✏️ Edit Signal",
            "🗑️ Delete Signal"
        ],

        [
            "🧹 Clear Future Signals",
            "📤 Auto Send ON/OFF"
        ],

        [
            "🎯 Signal Audience",
            "⚡ Live Session"
        ],

        [
            "🆔 Pending UID",
            "⭐ Manage VIP"
        ],

        [
            "💸 Withdrawals",
            "💳 Wallet Adjust"
        ],

        [
            "📢 Broadcast",
            "👥 Users"
        ],

        [
            "🛡️ Sub-admins",
            "🎯 Notify Targets"
        ],

        [
            "📸 Screenshot Signal"
        ],

        [
            "📊 Analytics",
            "⚙️ Settings"
        ],

        [
            "📝 Bot Text Editor"
        ],

        [
            "🔙 Back",
            "🏠 Main Menu"
        ]

    ])


def mm_keyboard():
    return make_keyboard([
        ["⚙️ Setup MM", "🔘 MM ON/OFF"],
        ["📊 MM Status", "💵 Change Base"],
        ["💲 Change M1", "📈 Change Payout"],
        ["🛑 Stop MM Today"],
        ["🔙 Back", "🏠 Main Menu"]
    ])


# ============================================================
# SIGNAL PARSER
# ============================================================

def parse_signal_line(line):

    pattern = re.compile(
        r"""
        ^\s*

        (\d{1,2})
        :
        (\d{2})

        \s*[-|]\s*

        ([A-Za-z0-9_./-]+)

        \s*[-|]\s*

        ([A-Za-z]+)

        (?:\s*[-|]\s*(.*))?

        \s*$
        """,
        re.VERBOSE
    )

    match = pattern.match(line)

    if not match:

        return None, "format"


    hour = int(match.group(1))
    minute = int(match.group(2))

    pair = match.group(3).upper()

    raw_direction = match.group(4)

    extra = match.group(5)


    if hour > 23 or minute > 59:

        return None, "time"


    direction = normalize_direction(
        raw_direction
    )


    if not direction:

        return None, "direction"


    confidence = (
        extra.strip()
        if extra and extra.strip()
        else get_setting(
            "confidence",
            "95–99%"
        )
    )


    return {

        "hour": hour,
        "minute": minute,
        "pair": pair,
        "direction": direction,
        "confidence": confidence

    }, None


def add_future_signals(text):

    today = now_bd().date()

    current_time = now_bd()

    added = []
    duplicates = []
    rejected = []


    for raw_line in text.splitlines():

        line = raw_line.strip()


        if not line:
            continue


        # Ignore decorative headers.
        if not re.search(
            r"\d{1,2}:\d{2}",
            line
        ):
            continue


        parsed, error = parse_signal_line(line)


        if not parsed:

            rejected.append(
                (
                    line,
                    error
                )
            )

            continue


        local_datetime = datetime(
            today.year,
            today.month,
            today.day,
            parsed["hour"],
            parsed["minute"],
            tzinfo=BD_TZ
        )


        # IMPORTANT:
        # Past time is rejected.
        # It is NEVER moved to tomorrow.
        if local_datetime <= current_time:

            rejected.append(
                (
                    line,
                    "past"
                )
            )

            continue


        with DB_LOCK:

            conn = db()

            try:

                try:

                    conn.execute(
                        """
                        INSERT INTO signals(
                            signal_date,
                            signal_time,
                            signal_at_utc,
                            pair,
                            direction,
                            confidence,
                            audience,
                            created_at
                        )
                        VALUES(?,?,?,?,?,?,?,?)
                        """,
                        (
                            today.isoformat(),

                            f"{parsed['hour']:02d}:"
                            f"{parsed['minute']:02d}",

                            utc_iso(local_datetime),

                            parsed["pair"],

                            parsed["direction"],

                            parsed["confidence"],

                            get_setting(
                                "audience",
                                "ALL"
                            ),

                            utc_iso(now_utc())
                        )
                    )

                    signal_id = conn.execute(
                        "SELECT last_insert_rowid()"
                    ).fetchone()[0]

                    conn.commit()

                    added.append(signal_id)

                except sqlite3.IntegrityError:

                    duplicates.append(line)

            finally:

                conn.close()


    return added, duplicates, rejected


# ============================================================
# SIGNAL DATA
# ============================================================

def get_signal(signal_id):

    with DB_LOCK:

        conn = db()

        try:

            return conn.execute(
                """
                SELECT *
                FROM signals
                WHERE id=?
                """,
                (signal_id,)
            ).fetchone()

        finally:

            conn.close()


def format_signal(signal):

    signal_datetime = bd_from_iso(
        signal["signal_at_utc"]
    )

    if signal["direction"] == "UP":

        direction_icon = "🟢⬆️"

    else:

        direction_icon = "🔴⬇️"


    return (

        "━━━━━━━━━━━━━━━━━━\n"

        "🚨 <b>SM QUATEX SURE SHORT</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📅 <b>"
        f"{signal_datetime.strftime('%d %B %Y')}"
        f"</b>\n"

        f"💱 Pair: "
        f"<b>{escape(signal['pair'])}</b>\n"

        f"⏰ Time: "
        f"<b>{signal_datetime.strftime('%I:%M %p')}</b>\n"

        f"{direction_icon} Direction: "
        f"<b>{signal['direction']}</b>\n"

        f"🎯 Confidence: "
        f"<b>{escape(signal['confidence'])}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━"

    )


def audience_allows(signal, user_id):

    audience = signal["audience"]


    if audience == "ALL":

        return True


    current_user = get_user(user_id)


    if audience == "VIP":

        return bool(
            current_user
            and current_user["status"] == "VIP"
        )


    if audience == "SELECTED":

        with DB_LOCK:

            conn = db()

            try:

                return bool(
                    conn.execute(
                        """
                        SELECT 1
                        FROM selected_users
                        WHERE signal_id=?
                        AND user_id=?
                        """,
                        (
                            signal["id"],
                            user_id
                        )
                    ).fetchone()
                )

            finally:

                conn.close()


    return False


def quota_available(user_id):

    reset_cycle_if_needed(user_id)

    current_user = get_user(user_id)

    if not current_user:
        return False


    if current_user["status"] == "VIP":

        return True


    free_limit = int(
        get_setting(
            "free_limit",
            "4"
        )
    )


    return (
        current_user["free_used"]
        <
        free_limit
    )


def deliver_signal(
    user_id,
    signal_id,
    source="manual"
):

    signal = get_signal(signal_id)


    if not signal:
        return False, "not_found"


    if not signal["active"]:
        return False, "inactive"


    if not audience_allows(
        signal,
        user_id
    ):
        return False, "audience"


    if not quota_available(user_id):
        return False, "quota"


    with DB_LOCK:

        conn = db()

        try:

            already = conn.execute(
                """
                SELECT 1
                FROM deliveries
                WHERE signal_id=?
                AND user_id=?
                """,
                (
                    signal_id,
                    user_id
                )
            ).fetchone()


            if already:

                return False, "already"


            conn.execute(
                """
                INSERT INTO deliveries(
                    signal_id,
                    user_id,
                    delivered_at,
                    source
                )
                VALUES(?,?,?,?)
                """,
                (
                    signal_id,
                    user_id,
                    utc_iso(now_utc()),
                    source
                )
            )


            current_user = conn.execute(
                """
                SELECT status
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()


            if (
                current_user
                and
                current_user["status"] != "VIP"
            ):

                conn.execute(
                    """
                    UPDATE users
                    SET free_used=free_used+1
                    WHERE user_id=?
                    """,
                    (user_id,)
                )


            conn.commit()

        finally:

            conn.close()


    try:

        bot.send_message(
            user_id,
            format_signal(signal),
            reply_markup=main_keyboard(user_id)
        )

        return True, "sent"

    except Exception as exc:

        logger.warning(
            "Signal send failed: %s",
            exc
        )


        # Telegram failed.
        # Remove delivery record and restore quota.
        with DB_LOCK:

            conn = db()

            try:

                conn.execute(
                    """
                    DELETE FROM deliveries
                    WHERE signal_id=?
                    AND user_id=?
                    """,
                    (
                        signal_id,
                        user_id
                    )
                )


                current_user = conn.execute(
                    """
                    SELECT status
                    FROM users
                    WHERE user_id=?
                    """,
                    (user_id,)
                ).fetchone()


                if (
                    current_user
                    and
                    current_user["status"] != "VIP"
                ):

                    conn.execute(
                        """
                        UPDATE users
                        SET free_used=
                            MAX(0,free_used-1)
                        WHERE user_id=?
                        """,
                        (user_id,)
                    )


                conn.commit()

            finally:

                conn.close()


        return False, "send_error"


def next_signal_for_user(user_id):

    current_time = utc_iso(
        now_utc()
    )

    with DB_LOCK:

        conn = db()

        try:

            rows = conn.execute(
                """
                SELECT s.*
                FROM signals s

                WHERE
                    s.active=1

                    AND
                    s.signal_at_utc>?

                    AND
                    NOT EXISTS(
                        SELECT 1
                        FROM deliveries d
                        WHERE
                            d.signal_id=s.id
                            AND
                            d.user_id=?
                    )

                ORDER BY
                    s.signal_at_utc ASC
                """,
                (
                    current_time,
                    user_id
                )
            ).fetchall()


            for row in rows:

                if audience_allows(
                    row,
                    user_id
                ):

                    return row


            return None

        finally:

            conn.close()


# ============================================================
# REFERRAL BONUS
# ============================================================

def process_referral_bonus(user_id):

    with DB_LOCK:

        conn = db()

        try:

            current_user = conn.execute(
                """
                SELECT *
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()


            if not current_user:
                return


            if not current_user["referred_by"]:
                return


            if current_user["referral_paid"]:
                return


            referrer_id = current_user[
                "referred_by"
            ]


            referrer_exists = conn.execute(
                """
                SELECT 1
                FROM users
                WHERE user_id=?
                """,
                (referrer_id,)
            ).fetchone()


            if not referrer_exists:
                return


            bonus_cents = int(
                round(
                    float(
                        get_setting(
                            "referral_bonus",
                            "1.00"
                        )
                    )
                    * 100
                )
            )


            conn.execute(
                """
                UPDATE users

                SET
                    wallet_cents=
                        wallet_cents+?,

                    refs_count=
                        refs_count+1

                WHERE user_id=?
                """,
                (
                    bonus_cents,
                    referrer_id
                )
            )


            conn.execute(
                """
                UPDATE users
                SET referral_paid=1
                WHERE user_id=?
                """,
                (user_id,)
            )


            conn.execute(
                """
                INSERT INTO referrals(
                    referrer_id,
                    referred_id,
                    bonus_cents,
                    created_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    referrer_id,
                    user_id,
                    bonus_cents,
                    utc_iso(now_utc())
                )
            )


            conn.execute(
                """
                INSERT INTO wallet_tx(
                    user_id,
                    amount_cents,
                    kind,
                    note,
                    created_at
                )
                VALUES(?,?,?,?,?)
                """,
                (
                    referrer_id,
                    bonus_cents,
                    "REFERRAL",
                    "Referral bonus",
                    utc_iso(now_utc())
                )
            )


            conn.commit()

        finally:

            conn.close()


# ============================================================
# AUTO SEND
# 5 MINUTES BEFORE SIGNAL
# ============================================================

def eligible_users(signal):

    with DB_LOCK:

        conn = db()

        try:

            if signal["audience"] == "VIP":

                return conn.execute(
                    """
                    SELECT user_id
                    FROM users
                    WHERE
                        status='VIP'
                        AND blocked=0
                    """
                ).fetchall()


            if signal["audience"] == "SELECTED":

                return conn.execute(
                    """
                    SELECT u.user_id
                    FROM users u

                    JOIN selected_users s
                    ON s.user_id=u.user_id

                    WHERE
                        s.signal_id=?
                        AND u.blocked=0
                    """,
                    (signal["id"],)
                ).fetchall()


            return conn.execute(
                """
                SELECT user_id
                FROM users
                WHERE blocked=0
                """
            ).fetchall()

        finally:

            conn.close()


def auto_signal_loop():

    while True:

        try:

            if get_setting(
                "auto_send",
                "ON"
            ) == "ON":

                current_time = now_utc()


                with DB_LOCK:

                    conn = db()

                    try:

                        signals = conn.execute(
                            """
                            SELECT *
                            FROM signals

                            WHERE
                                active=1
                                AND auto_sent=0

                            ORDER BY
                                signal_at_utc ASC
                            """
                        ).fetchall()

                    finally:

                        conn.close()


                for signal in signals:

                    signal_time = (
                        datetime
                        .fromisoformat(
                            signal["signal_at_utc"]
                        )
                        .astimezone(UTC)
                    )


                    # If trading time has passed
                    # without being sent,
                    # mark it expired.
                    if current_time > signal_time:

                        with DB_LOCK:

                            conn = db()

                            try:

                                conn.execute(
                                    """
                                    UPDATE signals
                                    SET auto_sent=1
                                    WHERE id=?
                                    """,
                                    (
                                        signal["id"],
                                    )
                                )

                                conn.commit()

                            finally:

                                conn.close()

                        continue


                    remaining = (
                        signal_time
                        - current_time
                    )


                    # 5 minute window.
                    if (
                        remaining
                        <= timedelta(minutes=5)
                    ):

                        users = eligible_users(
                            signal
                        )


                        for row in users:

                            current_user = get_user(
                                row["user_id"]
                            )


                            # Auto notifications obey
                            # user's notification setting.
                            if (
                                current_user
                                and
                                current_user["notify"]
                            ):

                                deliver_signal(
                                    row["user_id"],
                                    signal["id"],
                                    "auto"
                                )


                        # Send to configured groups/channels.
                        target_message = format_signal(
                            signal
                        )


                        with DB_LOCK:

                            conn = db()

                            try:

                                targets = conn.execute(
                                    """
                                    SELECT chat_id, audience, selected_users, target_type
                                    FROM notify_targets
                                    WHERE enabled=1
                                    """
                                ).fetchall()

                            finally:

                                conn.close()


                        for target in targets:

                            try:
                                try:
                                    audience = (target["audience"] or "ALL").upper()
                                except Exception:
                                    audience = "ALL"
                                # A group/channel cannot enforce Telegram-user-level VIP membership.
                                # We therefore use the target audience as a signal-audience filter.
                                if audience == "VIP" and signal["audience"] != "VIP":
                                    continue
                                if audience == "SELECTED":
                                    # Selected-user targeting is meaningful for direct Telegram users;
                                    # for a group/channel, require at least one configured selected ID
                                    # and keep the target available for explicit admin use.
                                    ids = {x.strip() for x in (target["selected_users"] or "").split(",") if x.strip()}
                                    if not ids:
                                        continue
                                bot.send_message(
                                    target["chat_id"],
                                    target_message,
                                    reply_markup=result_buttons(signal["id"], int(target["chat_id"]) if str(target["chat_id"]).lstrip("-").isdigit() else 0)
                                )

                            except Exception as exc:

                                logger.warning(
                                    "Target send failed %s: %s",
                                    target["chat_id"],
                                    exc
                                )


                        with DB_LOCK:

                            conn = db()

                            try:

                                conn.execute(
                                    """
                                    UPDATE signals
                                    SET auto_sent=1
                                    WHERE id=?
                                    """,
                                    (
                                        signal["id"],
                                    )
                                )

                                conn.commit()

                            finally:

                                conn.close()


        except Exception:

            logger.exception(
                "Auto signal loop error"
            )


        time.sleep(10)


# ============================================================
# MONEY MANAGEMENT
# OPTIONAL
# ============================================================

def mm_get(user_id):

    keys = [
        "balance",
        "profit_target",
        "loss_limit",
        "base_trade",
        "m1_trade",
        "max_trades",
        "stop_mm"
    ]

    result = {}


    with DB_LOCK:

        conn = db()

        try:

            for key in keys:

                row = conn.execute(
                    """
                    SELECT value
                    FROM settings
                    WHERE key=?
                    """,
                    (
                        f"mm:{user_id}:{key}",
                    )
                ).fetchone()


                if row:

                    result[key] = row["value"]

                else:

                    if key == "stop_mm":

                        result[key] = "OFF"

                    elif key == "max_trades":

                        result[key] = "0"

                    else:

                        result[key] = "0"


        finally:

            conn.close()


    return result


def mm_set(user_id, key, value):

    set_setting(
        f"mm:{user_id}:{key}",
        value
    )


# ============================================================
# MAIN MENU
# ============================================================

def send_main_menu(
    chat_id,
    user_id,
    text="🏠 <b>Main Menu</b>"
):

    bot.send_message(
        chat_id,
        text,
        reply_markup=main_keyboard(user_id)
    )


# ============================================================
# /START
# ============================================================

@bot.message_handler(
    commands=["start"]
)
def start_command(message):
    """Reliable /start entry point, including Telegram referral deep-links."""
    user_id = message.from_user.id
    referred_by = None

    try:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2 and parts[1].startswith("ref_"):
            raw_ref = parts[1][4:].strip()
            if raw_ref.isdigit():
                candidate = int(raw_ref)
                if candidate != user_id:
                    referred_by = candidate

        # Registration must never prevent the welcome message from being sent.
        try:
            register_user(message.from_user, referred_by)
            reset_cycle_if_needed(user_id)
        except Exception:
            logger.exception("/start registration failed for user %s", user_id)

        welcome = get_setting("welcome", "🎉 Welcome!")
        if not welcome:
            welcome = "🎉 Welcome!"

        bot.send_message(
            message.chat.id,
            welcome,
            reply_markup=main_keyboard(user_id)
        )
    except Exception:
        logger.exception("/start send failed for user %s", user_id)
        try:
            bot.send_message(
                message.chat.id,
                "❌ /start process করা যায়নি। আবার /start দিন।"
            )
        except Exception:
            logger.exception("Could not send /start error to user %s", user_id)


# ============================================================
# /CANCEL
# ============================================================

@bot.message_handler(
    commands=["cancel"]
)
def cancel_command(message):

    clear_state(
        message.from_user.id
    )

    send_main_menu(
        message.chat.id,
        message.from_user.id,
        "❌ Operation cancelled."
    )


# ============================================================
# USER FEATURES
# ============================================================

def future_signal_menu(message):

    user_id = message.from_user.id

    if not quota_available(user_id):

        bot.send_message(
            message.chat.id,
            "⛔ আপনার free signal quota শেষ।\n\n"
            "⭐ VIP হলে unlimited Future Signal পাওয়া যাবে।",
            reply_markup=main_keyboard(user_id)
        )

        return


    signal = next_signal_for_user(
        user_id
    )


    if not signal:

        bot.send_message(
            message.chat.id,
            "📭 কোনো upcoming signal নেই।",
            reply_markup=main_keyboard(user_id)
        )

        return


    success, reason = deliver_signal(
        user_id,
        signal["id"],
        "manual"
    )


    if not success:

        bot.send_message(
            message.chat.id,
            "⛔ Signal পাওয়া যায়নি।",
            reply_markup=main_keyboard(user_id)
        )

        return


    process_referral_bonus(
        user_id
    )


def live_signal_menu(message):

    if get_setting(
        "live_mode",
        "ON"
    ) != "ON":

        bot.send_message(
            message.chat.id,
            "🔴 Live Session বর্তমানে বন্ধ।",
            reply_markup=main_keyboard(
                message.from_user.id
            )
        )

        return


    with DB_LOCK:

        conn = db()

        try:

            session = conn.execute(
                """
                SELECT *
                FROM live_sessions

                WHERE active=1

                ORDER BY id DESC

                LIMIT 1
                """
            ).fetchone()


            live_rows = []

            if session:

                live_rows = conn.execute(
                    """
                    SELECT *
                    FROM live_signals

                    WHERE session_id=?

                    ORDER BY id DESC

                    LIMIT 10
                    """,
                    (
                        session["id"],
                    )
                ).fetchall()

        finally:

            conn.close()


    if not session:

        bot.send_message(
            message.chat.id,
            "📭 কোনো Live Session চলছে না।",
            reply_markup=main_keyboard(
                message.from_user.id
            )
        )

        return


    lines = [
        "⚡ <b>LIVE SIGNAL SESSION</b>\n"
    ]


    for row in live_rows:

        icon = (
            "🟢⬆️"
            if row["direction"] == "UP"
            else
            "🔴⬇️"
        )


        lines.append(
            f"💱 <b>{escape(row['pair'])}</b>\n"
            f"⏰ <b>{escape(row['signal_time'])}</b>\n"
            f"{icon} <b>{row['direction']}</b>\n"
            f"🎯 {escape(row['confidence'])}\n"
        )


    bot.send_message(
        message.chat.id,
        "\n".join(lines),
        reply_markup=main_keyboard(
            message.from_user.id
        )
    )


def status_menu(message):

    user_id = message.from_user.id

    reset_cycle_if_needed(
        user_id
    )

    current_user = get_user(
        user_id
    )


    if current_user["status"] == "VIP":

        remaining = "∞ VIP"

    else:

        limit = int(
            get_setting(
                "free_limit",
                "4"
            )
        )

        remaining = str(
            max(
                0,
                limit -
                current_user["free_used"]
            )
        )


    bot.send_message(
        message.chat.id,

        "👤 <b>MY STATUS</b>\n\n"

        f"🆔 ID: "
        f"<code>{current_user['user_id']}</code>\n"

        f"⭐ Status: "
        f"<b>{current_user['status']}</b>\n"

        f"💰 Wallet: "
        f"<b>{money(current_user['wallet_cents'])}</b>\n"

        f"🎟️ Remaining Signals: "
        f"<b>{remaining}</b>\n"

        f"⭐ VIP Until: "
        f"<b>{escape(current_user['vip_until'] or '—')}</b>",

        reply_markup=main_keyboard(
            user_id
        )
    )


# ============================================================
# UID
# ============================================================

def start_uid_submission(message):

    user_id = message.from_user.id

    current_user = get_user(
        user_id
    )


    if (
        current_user
        and
        current_user["status"] == "VIP"
    ):

        bot.send_message(
            message.chat.id,
            "⭐ আপনি already VIP।",
            reply_markup=main_keyboard(
                user_id
            )
        )

        return


    with DB_LOCK:

        conn = db()

        try:

            pending = conn.execute(
                """
                SELECT 1
                FROM uid_submissions

                WHERE
                    user_id=?
                    AND status='PENDING'
                """,
                (
                    user_id,
                )
            ).fetchone()

        finally:

            conn.close()


    if pending:

        bot.send_message(
            message.chat.id,
            "⏳ আপনার UID already pending আছে।",
            reply_markup=main_keyboard(
                user_id
            )
        )

        return


    STATES[user_id] = {
        "action": "uid"
    }


    bot.send_message(
        message.chat.id,

        "🆔 <b>Quotex UID</b>\n\n"
        "আপনার Quotex UID পাঠান।\n\n"
        "/cancel দিয়ে বাতিল করতে পারবেন।",

        reply_markup=back_keyboard()
    )


# ============================================================
# WALLET
# ============================================================

def wallet_menu(message):

    user_id = message.from_user.id

    current_user = get_user(
        user_id
    )


    with DB_LOCK:

        conn = db()

        try:

            transactions = conn.execute(
                """
                SELECT *
                FROM wallet_tx

                WHERE user_id=?

                ORDER BY id DESC

                LIMIT 10
                """,
                (
                    user_id,
                )
            ).fetchall()

        finally:

            conn.close()


    lines = [

        "💵 <b>WALLET</b>\n",

        f"Balance: "
        f"<b>{money(current_user['wallet_cents'])}</b>\n"

    ]


    for row in transactions:

        lines.append(
            f"{escape(row['kind'])}: "
            f"{money(row['amount_cents'])} — "
            f"{escape(row['note'] or '')}"
        )


    bot.send_message(
        message.chat.id,

        "\n".join(lines),

        reply_markup=make_keyboard([
            [
                "💸 Request Withdraw"
            ],
            [
                "🔙 Back",
                "🏠 Main Menu"
            ]
        ])
    )


# ============================================================
# REFERRAL
# ============================================================

def referral_menu(message):

    user_id = message.from_user.id

    try:

        me = bot.get_me()

        referral_link = (
            f"https://t.me/"
            f"{me.username}"
            f"?start=ref_{user_id}"
        )


        current_user = get_user(
            user_id
        )


        bonus = get_setting(
            "referral_bonus",
            "1.00"
        )


        bot.send_message(
            message.chat.id,

            "👥 <b>REFERRAL</b>\n\n"

            f"🔗 <code>{referral_link}</code>\n\n"

            f"💰 Bonus: "
            f"<b>${float(bonus):.2f}</b>\n"

            f"👥 Referrals: "
            f"<b>{current_user['refs_count']}</b>",

            reply_markup=main_keyboard(
                user_id
            )
        )


    except Exception:

        bot.send_message(
            message.chat.id,
            "❌ Referral link তৈরি করা যায়নি।",
            reply_markup=main_keyboard(
                user_id
            )
        )


# ============================================================
# SIGNAL HISTORY
# ============================================================

def signal_history(message):

    user_id = message.from_user.id

    with DB_LOCK:

        conn = db()

        try:

            rows = conn.execute(
                """
                SELECT s.*

                FROM deliveries d

                JOIN signals s
                ON s.id=d.signal_id

                WHERE d.user_id=?

                ORDER BY d.delivered_at DESC

                LIMIT 20
                """,
                (
                    user_id,
                )
            ).fetchall()

        finally:

            conn.close()


    if not rows:

        text = "📜 কোনো signal history নেই."

    else:

        lines = [
            "📜 <b>SIGNAL HISTORY</b>\n"
        ]


        for row in rows:

            dt = bd_from_iso(
                row["signal_at_utc"]
            )


            icon = (
                "🟢⬆️"
                if row["direction"] == "UP"
                else
                "🔴⬇️"
            )


            lines.append(
                f"{dt.strftime('%d-%m %I:%M %p')} | "
                f"{escape(row['pair'])} | "
                f"{icon} "
                f"<b>{row['direction']}</b>"
            )


        text = "\n".join(
            lines
        )


    bot.send_message(
        message.chat.id,
        text,
        reply_markup=main_keyboard(
            user_id
        )
    )


# ============================================================
# VIP RULES
# ============================================================

def vip_rules(message):

    bot.send_message(

        message.chat.id,

        "⭐ <b>VIP RULES</b>\n\n"

        f"👤 Non-VIP Free Limit: "
        f"<b>{get_setting('free_limit','4')}</b> "
        f"signals প্রতি ২ দিনের cycle-এ.\n\n"

        "⭐ VIP: Future Signal limit নেই.\n\n"

        "🆔 UID verification admin review করবে.",

        reply_markup=main_keyboard(
            message.from_user.id
        )
    )


# ============================================================
# TRADING CONTRACT
# ============================================================

def trading_contract(message):

    bot.send_message(
        message.chat.id,

        get_setting(
            "trading_rules",
            ""
        ),

        reply_markup=main_keyboard(
            message.from_user.id
        )
    )


# ============================================================
# NOTIFICATIONS
# ============================================================

def notification_menu(message):

    current_user = get_user(
        message.from_user.id
    )


    bot.send_message(

        message.chat.id,

        "🔔 <b>NOTIFICATIONS</b>\n\n"

        f"Status: "
        f"<b>{'ON' if current_user['notify'] else 'OFF'}</b>",

        reply_markup=make_keyboard([
            [
                "🔔 Toggle Notifications"
            ],
            [
                "🔙 Back",
                "🏠 Main Menu"
            ]
        ])
    )


def toggle_notifications(message):

    user_id = message.from_user.id


    with DB_LOCK:

        conn = db()

        try:

            row = conn.execute(
                """
                SELECT notify
                FROM users
                WHERE user_id=?
                """,
                (
                    user_id,
                )
            ).fetchone()


            new_value = (
                0
                if row["notify"]
                else
                1
            )


            conn.execute(
                """
                UPDATE users
                SET notify=?
                WHERE user_id=?
                """,
                (
                    new_value,
                    user_id
                )
            )


            conn.commit()

        finally:

            conn.close()


    bot.send_message(
        message.chat.id,

        f"🔔 Notifications: "
        f"<b>{'ON' if new_value else 'OFF'}</b>",

        reply_markup=main_keyboard(
            user_id
        )
    )


# ============================================================
# HELP
# ============================================================

def help_menu(message):

    bot.send_message(

        message.chat.id,

        "❓ <b>HELP / FAQ</b>\n\n"

        "📊 Future Signals — upcoming signals\n"
        "⚡ Live Signals — live session\n"
        "🗳️ Vote — UP / DOWN / SKIP\n"
        "📈 Signal Result — admin result\n"
        "💰 Money Management — optional risk setup\n"
        "🆔 UID — VIP verification\n"
        "💵 Wallet — wallet and withdrawal\n"
        "👥 Referral — referral link",

        reply_markup=main_keyboard(
            message.from_user.id
        )
    )


# ============================================================
# VOTE
# ============================================================

def vote_menu(message):

    user_id = message.from_user.id

    signal = next_signal_for_user(
        user_id
    )


    if not signal:

        bot.send_message(
            message.chat.id,
            "📭 কোনো signal vote করার জন্য নেই।",
            reply_markup=main_keyboard(
                user_id
            )
        )

        return


    with DB_LOCK:

        conn = db()

        try:

            vote = conn.execute(
                """
                SELECT vote
                FROM votes

                WHERE
                    signal_id=?
                    AND user_id=?
                """,
                (
                    signal["id"],
                    user_id
                )
            ).fetchone()

        finally:

            conn.close()


    if vote:

        bot.send_message(
            message.chat.id,

            f"🗳️ আপনার vote already: "
            f"<b>{vote['vote']}</b>",

            reply_markup=main_keyboard(
                user_id
            )
        )

        return


    STATES[user_id] = {
        "action": "vote",
        "signal_id": signal["id"]
    }


    bot.send_message(

        message.chat.id,

        format_signal(signal)
        +
        "\n\n🗳️ Vote নির্বাচন করুন:",

        reply_markup=make_keyboard([
            [
                "🟢 UP / BUY",
                "🔴 DOWN / SELL"
            ],
            [
                "⏭️ SKIP"
            ],
            [
                "🔙 Back",
                "🏠 Main Menu"
            ]
        ])
    )


# ============================================================
# RESULT
# ============================================================

def result_menu(message):

    user_id = message.from_user.id


    if not can(
        user_id,
        "results"
    ):

        bot.send_message(
            message.chat.id,
            "⛔ Admin-only feature.",
            reply_markup=main_keyboard(
                user_id
            )
        )

        return


    with DB_LOCK:

        conn = db()

        try:

            signal = conn.execute(
                """
                SELECT *
                FROM signals

                WHERE active=1

                ORDER BY signal_at_utc DESC

                LIMIT 1
                """
            ).fetchone()

        finally:

            conn.close()


    if not signal:

        bot.send_message(
            message.chat.id,
            "📭 No signal.",
            reply_markup=admin_keyboard()
        )

        return


    STATES[user_id] = {
        "action": "result",
        "signal_id": signal["id"]
    }


    bot.send_message(

        message.chat.id,

        f"📈 Result for "
        f"#{signal['id']} — "
        f"{escape(signal['pair'])} — "
        f"{signal['direction']}",

        reply_markup=make_keyboard([
            [
                "✅ WIN",
                "❌ LOSS"
            ],
            [
                "⏭️ SKIP"
            ],
            [
                "🔙 Back",
                "🏠 Main Menu"
            ]
        ])
    )


# ============================================================
# MONEY MANAGEMENT
# ============================================================

def mm_menu(message):

    user_id = message.from_user.id

    mm = mm_get(
        user_id
    )


    bot.send_message(

        message.chat.id,

        "💰 <b>MONEY MANAGEMENT</b>\n\n"

        f"Balance: "
        f"${float(mm['balance']):.2f}\n"

        f"Profit Target: "
        f"${float(mm['profit_target']):.2f}\n"

        f"Loss Limit: "
        f"${float(mm['loss_limit']):.2f}\n"

        f"Base Trade: "
        f"${float(mm['base_trade']):.2f}\n"

        f"M1 Trade: "
        f"${float(mm['m1_trade']):.2f}\n"

        f"Max Trades/Day: "
        f"{mm['max_trades']}\n"

        f"Stop Today: "
        f"{mm['stop_mm']}\n\n"

        "ℹ️ Money Management optional.\n"
        "MM setup না করলেও Future Signal কাজ করবে.",

        reply_markup=mm_keyboard()
    )


# ============================================================
# ADMIN PANEL
# ============================================================

def admin_panel(message):

    user_id = message.from_user.id

    if not (
        is_master(user_id)
        or get_permissions(user_id)
    ):

        bot.send_message(
            message.chat.id,
            "⛔ Access denied.",
            reply_markup=main_keyboard(
                user_id
            )
        )

        return


    bot.send_message(
        message.chat.id,
        "👑 <b>ADMIN CONTROL</b>",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN: ADD FUTURE SIGNALS
# ============================================================

def admin_add_signals(message):

    user_id = message.from_user.id

    if not can(
        user_id,
        "signals"
    ):

        bot.send_message(
            message.chat.id,
            "⛔ Access denied.",
            reply_markup=admin_keyboard()
        )

        return


    STATES[user_id] = {
        "action": "add_future"
    }


    bot.send_message(

        message.chat.id,

        "➕ <b>ADD FUTURE SIGNALS</b>\n\n"

        "একসাথে যত signal চান paste করতে পারবেন.\n\n"

        "<code>"
        "13:04 - USD/BDT-OTC - UP\n"
        "13:14 - USD/PHP-OTC - BUY\n"
        "13:21 - USD/COP-OTC - CALL\n"
        "13:30 - USD/MXN-OTC - DOWN\n"
        "13:36 - USD/ARS-OTC - SELL\n"
        "13:40 - USD/ZAR-OTC - PUT"
        "</code>\n\n"

        "🟢 UP / BUY / CALL → UP\n"
        "🔴 DOWN / SELL / PUT → DOWN\n\n"

        "⚠️ আজকের Bangladesh date-এর signal হবে.\n"
        "⚠️ Past time reject হবে.\n"
        "⚠️ Past signal কখনো next day-এ যাবে না.",

        reply_markup=back_keyboard()
    )


# ============================================================
# ADMIN: SIGNAL LIST
# ============================================================

def admin_signal_list(message):

    with DB_LOCK:

        conn = db()

        try:

            rows = conn.execute(
                """
                SELECT *
                FROM signals

                WHERE active=1

                ORDER BY signal_at_utc ASC

                LIMIT 50
                """
            ).fetchall()

        finally:

            conn.close()


    if not rows:

        text = "📭 কোনো future signal নেই."

    else:

        lines = [
            "📋 <b>FUTURE SIGNALS</b>\n"
        ]


        for row in rows:

            dt = bd_from_iso(
                row["signal_at_utc"]
            )


            lines.append(

                f"#{row['id']} | "
                f"{dt.strftime('%d-%m %I:%M %p')} | "
                f"{escape(row['pair'])} | "
                f"<b>{row['direction']}</b> | "
                f"{row['audience']}"

            )


        text = "\n".join(
            lines
        )


    bot.send_message(
        message.chat.id,
        text,
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN: EDIT SIGNAL
# ============================================================

def admin_edit_signal(message):

    if not can(
        message.from_user.id,
        "signals"
    ):

        bot.send_message(
            message.chat.id,
            "⛔ Access denied.",
            reply_markup=admin_keyboard()
        )

        return


    STATES[
        message.from_user.id
    ] = {
        "action": "edit_signal"
    }


    bot.send_message(

        message.chat.id,

        "✏️ Format:\n\n"

        "<code>"
        "12 | 13:30 - USD/BDT-OTC - BUY"
        "</code>\n\n"

        "UP/BUY/CALL → UP\n"
        "DOWN/SELL/PUT → DOWN",

        reply_markup=back_keyboard()
    )


# ============================================================
# ADMIN: DELETE SIGNAL
# ============================================================

def admin_delete_signal(message):

    STATES[
        message.from_user.id
    ] = {
        "action": "delete_signal"
    }


    bot.send_message(
        message.chat.id,
        "🗑️ যে Signal ID delete করতে চান সেটা পাঠান.",
        reply_markup=back_keyboard()
    )


# ============================================================
# ADMIN: CLEAR FUTURE
# ============================================================

def admin_clear_future(message):

    if not is_master(
        message.from_user.id
    ):

        bot.send_message(
            message.chat.id,
            "⛔ Master Admin only.",
            reply_markup=admin_keyboard()
        )

        return


    with DB_LOCK:

        conn = db()

        try:

            conn.execute(
                """
                DELETE FROM signals
                WHERE signal_at_utc>?
                """,
                (
                    utc_iso(now_utc()),
                )
            )

            conn.commit()

        finally:

            conn.close()


    bot.send_message(
        message.chat.id,
        "🧹 সব upcoming signal clear হয়েছে.",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN: AUTO SEND
# ============================================================

def admin_toggle_auto(message):

    current = get_setting(
        "auto_send",
        "ON"
    )


    new_value = (
        "OFF"
        if current == "ON"
        else
        "ON"
    )


    set_setting(
        "auto_send",
        new_value
    )


    bot.send_message(

        message.chat.id,

        f"📤 Auto Send: "
        f"<b>{new_value}</b>\n\n"

        "ON থাকলে signal trading time-এর "
        "৫ মিনিট আগে পাঠানোর চেষ্টা করবে.",

        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN: AUDIENCE
# ============================================================

def admin_audience(message):

    if not is_master(
        message.from_user.id
    ):

        bot.send_message(
            message.chat.id,
            "⛔ Master Admin only.",
            reply_markup=admin_keyboard()
        )

        return


    STATES[
        message.from_user.id
    ] = {
        "action": "audience"
    }


    bot.send_message(

        message.chat.id,

        f"Current Audience: "
        f"<b>{get_setting('audience','ALL')}</b>\n\n"

        "Choose:",

        reply_markup=make_keyboard([
            [
                "🌐 ALL",
                "⭐ VIP"
            ],
            [
                "🎯 SELECTED"
            ],
            [
                "🔙 Back",
                "🏠 Main Menu"
            ]
        ])
    )


# ============================================================
# ADMIN: LIVE SESSION
# ============================================================

def admin_live_session(message):

    user_id = message.from_user.id

    if not can(
        user_id,
        "signals"
    ):

        bot.send_message(
            message.chat.id,
            "⛔ Access denied.",
            reply_markup=admin_keyboard()
        )

        return


    with DB_LOCK:

        conn = db()

        try:

            session = conn.execute(
                """
                SELECT *
                FROM live_sessions

                WHERE active=1

                ORDER BY id DESC

                LIMIT 1
                """
            ).fetchone()


            if not session:

                conn.execute(
                    """
                    INSERT INTO live_sessions(
                        started_at
                    )
                    VALUES(?)
                    """,
                    (
                        utc_iso(now_utc()),
                    )
                )

                session_id = conn.execute(
                    "SELECT last_insert_rowid()"
                ).fetchone()[0]

                conn.commit()

            else:

                session_id = session["id"]

        finally:

            conn.close()


    STATES[user_id] = {
        "action": "live_signal",
        "session_id": session_id
    }


    bot.send_message(

        message.chat.id,

        "⚡ <b>LIVE SESSION ACTIVE</b>\n\n"

        "Signal পাঠাও:\n"

        "<code>"
        "USD/BDT-OTC - UP - 95%"
        "</code>\n\n"

        "Direction support:\n"
        "UP / BUY / CALL → UP\n"
        "DOWN / SELL / PUT → DOWN\n\n"

        "Session বন্ধ করতে লিখুন:\n"
        "<code>END</code>",

        reply_markup=back_keyboard()
    )


# ============================================================
# ADMIN: PENDING UID
# ============================================================

def admin_pending_uid(message):
    uid = message.from_user.id
    if not can(uid, "vip"):
        bot.send_message(message.chat.id, "⛔ Access denied.", reply_markup=admin_keyboard())
        return

    with DB_LOCK:
        conn = db()
        try:
            rows = conn.execute(
                "SELECT * FROM uid_submissions WHERE status='PENDING' ORDER BY id ASC LIMIT 20"
            ).fetchall()
        finally:
            conn.close()

    if not rows:
        bot.send_message(message.chat.id, "📭 কোনো pending UID নেই.", reply_markup=admin_keyboard())
        return

    bot.send_message(message.chat.id, "🆔 <b>PENDING UID</b>", reply_markup=admin_keyboard())
    for row in rows:
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ Approve", callback_data=f"uidapprove:{row['id']}"),
            types.InlineKeyboardButton("❌ Reject", callback_data=f"uidreject:{row['id']}")
        )
        bot.send_message(
            message.chat.id,
            f"#{row['id']}\n👤 User: <code>{row['user_id']}</code>\n🆔 UID: <code>{escape(row['quotex_uid'])}</code>\n📅 {escape(row['created_at'])}",
            reply_markup=kb
        )


# ============================================================
# ADMIN: VIP
# ============================================================

def admin_vip(message):

    if not can(
        message.from_user.id,
        "vip"
    ):

        bot.send_message(
            message.chat.id,
            "⛔ Access denied.",
            reply_markup=admin_keyboard()
        )

        return


    STATES[
        message.from_user.id
    ] = {
        "action": "vip_manage"
    }


    bot.send_message(

        message.chat.id,

        "⭐ <b>VIP MANAGEMENT</b>\n\n"

        "Add:\n"
        "<code>add USER_ID DAYS</code>\n\n"

        "Remove:\n"
        "<code>remove USER_ID</code>\n\n"

        "Example:\n"
        "<code>add 123456789 30</code>",

        reply_markup=back_keyboard()
    )


# ============================================================
# ADMIN: WITHDRAWALS
# ============================================================

def admin_withdrawals(message):

    if not can(
        message.from_user.id,
        "withdraw"
    ):

        bot.send_message(
            message.chat.id,
            "⛔ Access denied.",
            reply_markup=admin_keyboard()
        )

        return


    with DB_LOCK:

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


    if rows:

        lines = [
            "💸 <b>PENDING WITHDRAWALS</b>\n"
        ]


        for row in rows:

            lines.append(

                f"#{row['id']} | "
                f"User: <code>{row['user_id']}</code> | "
                f"Amount: "
                f"<b>${row['amount_cents']/100:.2f}</b>\n"
                f"Method: {escape(row['method'])}\n"
                f"Account: <code>"
                f"{escape(row['account'])}"
                f"</code>\n"

            )


        text = "\n".join(
            lines
        )

    else:

        text = (
            "💸 <b>PENDING WITHDRAWALS</b>\n\n"
            "None."
        )


    STATES[
        message.from_user.id
    ] = {
        "action": "withdraw_review"
    }


    bot.send_message(

        message.chat.id,

        text
        +
        "\n\n"
        "Approve: <code>approve ID</code>\n"
        "Reject: <code>reject ID</code>",

        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN: WALLET
# ============================================================

def admin_wallet_adjust(message):

    STATES[
        message.from_user.id
    ] = {
        "action": "wallet_adjust"
    }


    bot.send_message(

        message.chat.id,

        "💳 Format:\n\n"

        "<code>"
        "USER_ID AMOUNT"
        "</code>\n\n"

        "Example add:\n"
        "<code>123456789 5.00</code>\n\n"

        "Example deduct:\n"
        "<code>123456789 -2.00</code>",

        reply_markup=back_keyboard()
    )


# ============================================================
# ADMIN: BROADCAST
# ============================================================

def admin_broadcast(message):

    STATES[
        message.from_user.id
    ] = {
        "action": "broadcast"
    }


    bot.send_message(

        message.chat.id,

        "📢 যে message broadcast করতে চান সেটা পাঠান.",

        reply_markup=back_keyboard()
    )


# ============================================================
# ADMIN: USERS
# ============================================================

def admin_users(message):

    with DB_LOCK:

        conn = db()

        try:

            total = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM users
                """
            ).fetchone()["n"]


            vip = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM users
                WHERE status='VIP'
                """
            ).fetchone()["n"]


            blocked = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM users
                WHERE blocked=1
                """
            ).fetchone()["n"]

        finally:

            conn.close()


    bot.send_message(

        message.chat.id,

        "👥 <b>USERS</b>\n\n"

        f"Total: <b>{total}</b>\n"
        f"VIP: <b>{vip}</b>\n"
        f"Blocked: <b>{blocked}</b>",

        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN: SUB ADMINS
# ============================================================

def admin_subadmins(message):

    if not is_master(
        message.from_user.id
    ):

        bot.send_message(
            message.chat.id,
            "⛔ Master Admin only.",
            reply_markup=admin_keyboard()
        )

        return


    STATES[
        message.from_user.id
    ] = {
        "action": "subadmin"
    }


    bot.send_message(

        message.chat.id,

        "🛡️ <b>SUB-ADMIN</b>\n\n"

        "Add:\n"
        "<code>"
        "add USER_ID signals,vip,withdraw,results,users,settings,broadcast"
        "</code>\n\n"

        "Remove:\n"
        "<code>"
        "remove USER_ID"
        "</code>",

        reply_markup=back_keyboard()
    )


# ============================================================
# ADMIN: NOTIFICATION TARGETS
# ============================================================

def admin_notify_targets(message):

    if not is_master(
        message.from_user.id
    ):

        bot.send_message(
            message.chat.id,
            "⛔ Master Admin only.",
            reply_markup=admin_keyboard()
        )

        return


    STATES[
        message.from_user.id
    ] = {
        "action": "notify_target"
    }


    bot.send_message(

        message.chat.id,

        "🎯 <b>NOTIFICATION TARGET</b>\n\n"

        "Add group/channel chat ID:\n"
        "<code>-1001234567890</code>\n\n"

        "Remove:\n"
        "<code>remove -1001234567890</code>\n\n"

        "Test:\n"
        "<code>test -1001234567890</code>\n\n"

        "Bot-কে group/channel-এ add করে "
        "message send করার permission দিতে হবে.",

        reply_markup=back_keyboard()
    )


# ============================================================
# ADMIN: ANALYTICS
# ============================================================

def admin_analytics(message):

    with DB_LOCK:

        conn = db()

        try:

            users = conn.execute(
                "SELECT COUNT(*) n FROM users"
            ).fetchone()["n"]


            deliveries = conn.execute(
                "SELECT COUNT(*) n FROM deliveries"
            ).fetchone()["n"]


            votes = conn.execute(
                "SELECT COUNT(*) n FROM votes"
            ).fetchone()["n"]


            wins = conn.execute(
                """
                SELECT COUNT(*) n
                FROM results
                WHERE result='WIN'
                """
            ).fetchone()["n"]


            losses = conn.execute(
                """
                SELECT COUNT(*) n
                FROM results
                WHERE result='LOSS'
                """
            ).fetchone()["n"]

        finally:

            conn.close()


    bot.send_message(

        message.chat.id,

        "📊 <b>ANALYTICS</b>\n\n"

        f"Users: <b>{users}</b>\n"
        f"Delivered Signals: <b>{deliveries}</b>\n"
        f"Votes: <b>{votes}</b>\n"
        f"WIN: <b>{wins}</b>\n"
        f"LOSS: <b>{losses}</b>",

        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN: SETTINGS
# ============================================================

def admin_settings(message):

    bot.send_message(

        message.chat.id,

        "⚙️ <b>SETTINGS</b>\n\n"

        f"Maintenance: "
        f"<b>{get_setting('maintenance')}</b>\n"

        f"Live Mode: "
        f"<b>{get_setting('live_mode')}</b>\n"

        f"Auto Send: "
        f"<b>{get_setting('auto_send')}</b>\n"

        f"Free Limit: "
        f"<b>{get_setting('free_limit')}</b>\n"

        f"Minimum Withdraw: "
        f"<b>${get_setting('min_withdraw')}</b>\n"

        f"Withdrawals: "
        f"<b>{get_setting('withdrawals')}</b>",

        reply_markup=make_keyboard([

            [
                "🛠️ Maintenance ON/OFF",
                "⚡ Live ON/OFF"
            ],

            [
                "🎟️ Set Free Limit",
                "💵 Set Min Withdraw"
            ],

            [
                "💸 Withdraw ON/OFF"
            ],

            [
                "🔙 Back",
                "🏠 Main Menu"
            ]

        ])
    )


# ============================================================
# ADMIN: TEXT EDITOR
# ============================================================

def admin_text_editor(message):

    STATES[
        message.from_user.id
    ] = {
        "action": "text_editor"
    }


    bot.send_message(

        message.chat.id,

        "📝 <b>BOT TEXT EDITOR</b>\n\n"

        "Format:\n"
        "<code>key=value</code>\n\n"

        "Available keys:\n"
        "<code>"
        "welcome\n"
        "trading_rules\n"
        "notice\n"
        "confidence\n"
        "referral_bonus\n"
        "min_withdraw\n"
        "free_limit"
        "</code>\n\n"

        "Example:\n"
        "<code>"
        "welcome=🎉 Welcome to SM QUATEX SURE SHORT"
        "</code>",

        reply_markup=back_keyboard()
    )


# ============================================================
# ADMIN ROUTER
# ============================================================

def handle_admin_button(message):

    text = message.text
    user_id = message.from_user.id


    if text == "➕ Add Future Signals":

        return admin_add_signals(
            message
        )


    if text == "📋 Future Signal List":

        return admin_signal_list(
            message
        )


    if text == "✏️ Edit Signal":

        return admin_edit_signal(
            message
        )


    if text == "🗑️ Delete Signal":

        return admin_delete_signal(
            message
        )


    if text == "🧹 Clear Future Signals":

        return admin_clear_future(
            message
        )


    if text == "📤 Auto Send ON/OFF":

        return admin_toggle_auto(
            message
        )


    if text == "🎯 Signal Audience":

        return admin_audience(
            message
        )


    if text == "⚡ Live Session":

        return admin_live_session(
            message
        )


    if text == "🆔 Pending UID":

        return admin_pending_uid(
            message
        )


    if text == "⭐ Manage VIP":

        return admin_vip(
            message
        )


    if text == "💸 Withdrawals":

        return admin_withdrawals(
            message
        )


    if text == "💳 Wallet Adjust":

        return admin_wallet_adjust(
            message
        )


    if text == "📢 Broadcast":

        return admin_broadcast(
            message
        )


    if text == "👥 Users":

        return admin_users(
            message
        )


    if text == "🛡️ Sub-admins":

        return admin_subadmins(
            message
        )


    if text == "🎯 Notify Targets":

        return admin_notify_targets(
            message
        )


    if text == "📊 Analytics":

        return admin_analytics(
            message
        )


    if text == "⚙️ Settings":

        return admin_settings(
            message
        )


    if text == "📝 Bot Text Editor":

        return admin_text_editor(
            message
        )


    if text == "🛠️ Maintenance ON/OFF":

        if not is_master(user_id):

            bot.send_message(
                message.chat.id,
                "⛔ Master Admin only.",
                reply_markup=admin_keyboard()
            )

            return


        old = get_setting(
            "maintenance",
            "OFF"
        )

        new = (
            "OFF"
            if old == "ON"
            else
            "ON"
        )

        set_setting(
            "maintenance",
            new
        )


        bot.send_message(
            message.chat.id,
            f"🛠️ Maintenance: <b>{new}</b>",
            reply_markup=admin_keyboard()
        )

        return


    if text == "⚡ Live ON/OFF":

        if not is_master(user_id):

            bot.send_message(
                message.chat.id,
                "⛔ Master Admin only.",
                reply_markup=admin_keyboard()
            )

            return


        old = get_setting(
            "live_mode",
            "ON"
        )

        new = (
            "OFF"
            if old == "ON"
            else
            "ON"
        )

        set_setting(
            "live_mode",
            new
        )


        bot.send_message(
            message.chat.id,
            f"⚡ Live Mode: <b>{new}</b>",
            reply_markup=admin_keyboard()
        )

        return


    if text == "🎟️ Set Free Limit":

        if not is_master(user_id):
            return


        STATES[user_id] = {
            "action": "set_free_limit"
        }


        bot.send_message(
            message.chat.id,
            "New free signal limit per 2-day cycle পাঠান.",
            reply_markup=back_keyboard()
        )

        return


    if text == "💵 Set Min Withdraw":

        if not is_master(user_id):
            return


        STATES[user_id] = {
            "action": "set_min_withdraw"
        }


        bot.send_message(
            message.chat.id,
            "New minimum withdrawal USD পাঠান.",
            reply_markup=back_keyboard()
        )

        return


    if text == "💸 Withdraw ON/OFF":

        if not is_master(user_id):
            return


        old = get_setting(
            "withdrawals",
            "ON"
        )

        new = (
            "OFF"
            if old == "ON"
            else
            "ON"
        )

        set_setting(
            "withdrawals",
            new
        )


        bot.send_message(
            message.chat.id,
            f"💸 Withdrawals: <b>{new}</b>",
            reply_markup=admin_keyboard()
        )

        return


# ============================================================
# STATE HANDLER
# ============================================================

def LEGACY_HANDLE_STATE(message):

    user_id = message.from_user.id

    state = STATES.get(
        user_id
    )


    if not state:
        return False


    text = (
        message.text or ""
    ).strip()


    action = state.get(
        "action"
    )


    # --------------------------------------------------------
    # UNIVERSAL BACK
    # --------------------------------------------------------

    if text in ("🔙 Back", "🏠 Main Menu"):
        if text == "🏠 Main Menu":
            clear_state(user_id)
            send_main_menu(message.chat.id, user_id, "🏠 <b>Main Menu</b>")
            return True

        # Back = previous menu. Admin workflows return to Admin Control;
        # normal user workflows return to the main menu.
        admin_actions = {
            "add_future", "edit_signal", "delete_signal", "clear_signals",
            "auto_send", "audience", "live_session", "uid_review", "vip_manage",
            "withdraw_review", "wallet_adjust", "broadcast", "user_list",
            "subadmin_add_id", "subadmin_remove", "notify_type", "notify_id",
            "notify_audience", "notify_save", "settings", "set_free_limit",
            "set_min_withdraw", "text_category", "text_edit", "text_preview",
            "set_referral_bonus", "set_referral_min_signals", "set_referral_hold_hours",
            "set_withdraw_hold_hours", "set_vip_days", "set_confidence",
            "set_auto_notification", "set_live_mode", "set_maintenance",
            "set_result_reveal"
        }
        clear_state(user_id)
        if action in admin_actions and (is_master(user_id) or get_permissions(user_id)):
            bot.send_message(message.chat.id, "🔙 <b>Admin Control</b>", reply_markup=admin_keyboard())
        else:
            send_main_menu(message.chat.id, user_id, "🔙 <b>Main Menu</b>")
        return True


    try:

        # ====================================================
        # ADD FUTURE SIGNALS
        # ====================================================

        if action == "add_future":

            added, duplicates, rejected = (
                add_future_signals(text)
            )


            clear_state(
                user_id
            )


            bot.send_message(

                message.chat.id,

                "✅ <b>SIGNAL IMPORT COMPLETE</b>\n\n"

                f"➕ Added: "
                f"<b>{len(added)}</b>\n"

                f"♻️ Duplicate: "
                f"<b>{len(duplicates)}</b>\n"

                f"❌ Rejected: "
                f"<b>{len(rejected)}</b>\n\n"

                "Direction rules:\n"

                "🟢 UP / BUY / CALL → UP\n"
                "🔴 DOWN / SELL / PUT → DOWN",

                reply_markup=admin_keyboard()
            )

            return True


        # ====================================================
        # EDIT SIGNAL
        # ====================================================

        if action == "edit_signal":

            parts = text.split(
                "|",
                1
            )


            if len(parts) != 2:

                raise ValueError(
                    "Format ভুল."
                )


            signal_id = int(
                parts[0].strip()
            )


            parsed, error = (
                parse_signal_line(
                    parts[1]
                )
            )


            if not parsed:

                raise ValueError(
                    f"Invalid signal: {error}"
                )


            today = now_bd().date()


            local_datetime = datetime(
                today.year,
                today.month,
                today.day,
                parsed["hour"],
                parsed["minute"],
                tzinfo=BD_TZ
            )


            if local_datetime <= now_bd():

                raise ValueError(
                    "Past time দেওয়া যাবে না."
                )


            with DB_LOCK:

                conn = db()

                try:

                    conn.execute(
                        """
                        UPDATE signals

                        SET
                            signal_time=?,
                            signal_at_utc=?,
                            pair=?,
                            direction=?,
                            confidence=?,
                            auto_sent=0

                        WHERE id=?
                        """,
                        (
                            f"{parsed['hour']:02d}:"
                            f"{parsed['minute']:02d}",

                            utc_iso(
                                local_datetime
                            ),

                            parsed["pair"],

                            parsed["direction"],

                            parsed["confidence"],

                            signal_id
                        )
                    )

                    conn.commit()

                finally:

                    conn.close()


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                "✏️ Signal updated successfully.",
                reply_markup=admin_keyboard()
            )

            return True


        # ====================================================
        # DELETE SIGNAL
        # ====================================================

        if action == "delete_signal":

            signal_id = int(
                text
            )


            with DB_LOCK:

                conn = db()

                try:

                    conn.execute(
                        """
                        DELETE FROM signals
                        WHERE id=?
                        """,
                        (
                            signal_id,
                        )
                    )

                    conn.commit()

                finally:

                    conn.close()


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                "🗑️ Signal deleted.",
                reply_markup=admin_keyboard()
            )

            return True


        # ====================================================
        # UID
        # ====================================================

        if action == "uid":

            current = get_user(user_id)
            if current and current["status"] == "VIP":
                raise ValueError("আপনি ইতিমধ্যে VIP। আবার UID submit করা যাবে না।")

            quotex_uid = normalize_uid(text)


            if not (
                3 <=
                len(quotex_uid)
                <= 100
            ):

                raise ValueError(
                    "UID format invalid."
                )


            with DB_LOCK:

                conn = db()

                try:

                    duplicate = conn.execute(
                        """
                        SELECT 1
                        FROM uid_submissions
                        WHERE quotex_uid=?
                        AND status IN('PENDING','APPROVED')
                        """,
                        (quotex_uid,)
                    ).fetchone()

                    existing_user_uid = conn.execute(
                        """
                        SELECT 1 FROM uid_submissions
                        WHERE user_id=? AND status IN('PENDING','APPROVED')
                        """,
                        (user_id,)
                    ).fetchone()

                    if existing_user_uid:
                        raise ValueError("আপনার UID ইতিমধ্যে submitted/approved আছে।")

                    if duplicate:

                        raise ValueError(
                            "এই Quotex UID already used."
                        )


                    pending = conn.execute(
                        """
                        SELECT 1
                        FROM uid_submissions

                        WHERE
                            user_id=?
                            AND status='PENDING'
                        """,
                        (
                            user_id,
                        )
                    ).fetchone()


                    if pending:

                        raise ValueError(
                            "আপনার একটি UID already pending."
                        )


                    conn.execute(
                        """
                        INSERT INTO uid_submissions(
                            user_id,
                            quotex_uid,
                            created_at
                        )
                        VALUES(?,?,?)
                        """,
                        (
                            user_id,
                            quotex_uid,
                            utc_iso(now_utc())
                        )
                    )


                    conn.commit()

                finally:

                    conn.close()


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                "✅ UID submitted.\n"
                "Admin review করবে.",
                reply_markup=main_keyboard(
                    user_id
                )
            )


            try:

                bot.send_message(

                    ADMIN_ID,

                    "🆔 <b>NEW UID PENDING</b>\n\n"

                    f"User: "
                    f"<code>{user_id}</code>\n"

                    f"UID: "
                    f"<code>"
                    f"{escape(quotex_uid)}"
                    f"</code>"

                )

            except Exception:

                pass


            return True


        # ====================================================
        # VOTE
        # ====================================================

        if action == "vote":

            if text == "🟢 UP / BUY":

                vote_value = "UP"

            elif text == "🔴 DOWN / SELL":

                vote_value = "DOWN"

            elif text == "⏭️ SKIP":

                vote_value = "SKIP"

            else:

                raise ValueError(
                    "Vote button ব্যবহার করুন."
                )


            with DB_LOCK:

                conn = db()

                try:

                    try:

                        conn.execute(
                            """
                            INSERT INTO votes(
                                signal_id,
                                user_id,
                                vote,
                                created_at
                            )
                            VALUES(?,?,?,?)
                            """,
                            (
                                state["signal_id"],
                                user_id,
                                vote_value,
                                utc_iso(now_utc())
                            )
                        )

                        conn.commit()

                    except sqlite3.IntegrityError:

                        raise ValueError(
                            "আপনি already vote দিয়েছেন."
                        )

                finally:

                    conn.close()


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                f"🗳️ Vote saved: "
                f"<b>{vote_value}</b>",
                reply_markup=main_keyboard(
                    user_id
                )
            )

            return True


        # ====================================================
        # RESULT
        # ====================================================

        if action == "result":

            if text == "✅ WIN":

                result_value = "WIN"

            elif text == "❌ LOSS":

                result_value = "LOSS"

            elif text == "⏭️ SKIP":

                result_value = "SKIP"

            else:

                raise ValueError(
                    "Result button ব্যবহার করুন."
                )


            reveal = (
                1
                if
                get_setting(
                    "result_reveal",
                    "OFF"
                ) == "ON"
                else
                0
            )


            with DB_LOCK:

                conn = db()

                try:

                    conn.execute(
                        """
                        INSERT INTO results(
                            signal_id,
                            result,
                            revealed,
                            created_at
                        )
                        VALUES(?,?,?,?)

                        ON CONFLICT(signal_id)

                        DO UPDATE SET
                            result=excluded.result,
                            revealed=excluded.revealed,
                            created_at=excluded.created_at
                        """,
                        (
                            state["signal_id"],
                            result_value,
                            reveal,
                            utc_iso(now_utc())
                        )
                    )

                    conn.commit()

                finally:

                    conn.close()


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                f"📈 Result saved: "
                f"<b>{result_value}</b>",
                reply_markup=admin_keyboard()
            )

            return True


        # ====================================================
        # AUDIENCE
        # ====================================================

        if action == "audience":

            if text == "🌐 ALL":

                set_setting(
                    "audience",
                    "ALL"
                )


                clear_state(
                    user_id
                )


                bot.send_message(
                    message.chat.id,
                    "🎯 Audience = ALL",
                    reply_markup=admin_keyboard()
                )

                return True


            if text == "⭐ VIP":

                set_setting(
                    "audience",
                    "VIP"
                )


                clear_state(
                    user_id
                )


                bot.send_message(
                    message.chat.id,
                    "🎯 Audience = VIP",
                    reply_markup=admin_keyboard()
                )

                return True


            if text == "🎯 SELECTED":

                STATES[user_id] = {
                    "action": "selected_users"
                }


                bot.send_message(
                    message.chat.id,
                    "Selected users-এর Telegram ID comma-separated পাঠান.\n\n"
                    "Example:\n"
                    "<code>123456789,987654321</code>",
                    reply_markup=back_keyboard()
                )

                return True


            raise ValueError(
                "Audience button ব্যবহার করুন."
            )


        # ====================================================
        # SELECTED USERS
        # ====================================================

        if action == "selected_users":

            ids = []

            for part in text.split(","):

                part = part.strip()

                if part.isdigit():

                    ids.append(
                        int(part)
                    )


            if not ids:

                raise ValueError(
                    "Valid Telegram ID পাওয়া যায়নি."
                )


            set_setting(
                "audience",
                "SELECTED"
            )


            STATES[user_id] = {
                "action": "selected_signal",
                "user_ids": ids
            }


            bot.send_message(
                message.chat.id,
                "এখন যে Signal ID-তে এই selected users দিতে চান সেটা পাঠান.",
                reply_markup=back_keyboard()
            )

            return True


        # ====================================================
        # SELECTED SIGNAL
        # ====================================================

        if action == "selected_signal":

            signal_id = int(
                text
            )


            signal = get_signal(
                signal_id
            )


            if not signal:

                raise ValueError(
                    "Signal ID পাওয়া যায়নি."
                )


            with DB_LOCK:

                conn = db()

                try:

                    for selected_id in state[
                        "user_ids"
                    ]:

                        conn.execute(
                            """
                            INSERT OR IGNORE INTO selected_users(
                                signal_id,
                                user_id
                            )
                            VALUES(?,?)
                            """,
                            (
                                signal_id,
                                selected_id
                            )
                        )


                    conn.execute(
                        """
                        UPDATE signals
                        SET audience='SELECTED'
                        WHERE id=?
                        """,
                        (
                            signal_id,
                        )
                    )


                    conn.commit()

                finally:

                    conn.close()


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                "🎯 Selected audience saved.",
                reply_markup=admin_keyboard()
            )

            return True


        # ====================================================
        # UID REVIEW
        # ====================================================

        if action == "uid_review":

            parts = text.lower().split()


            if (
                len(parts) != 2
                or
                parts[0]
                not in (
                    "approve",
                    "reject"
                )
            ):

                raise ValueError(
                    "approve ID অথবা reject ID"
                )


            submission_id = int(
                parts[1]
            )


            with DB_LOCK:

                conn = db()

                try:

                    submission = conn.execute(
                        """
                        SELECT *
                        FROM uid_submissions

                        WHERE
                            id=?
                            AND status='PENDING'
                        """,
                        (
                            submission_id,
                        )
                    ).fetchone()

                finally:

                    conn.close()


            if not submission:

                raise ValueError(
                    "Pending UID পাওয়া যায়নি."
                )


            if parts[0] == "approve":

                vip_until = (
                    now_bd()
                    +
                    timedelta(days=30)
                ).isoformat()


                with DB_LOCK:

                    conn = db()

                    try:

                        conn.execute(
                            """
                            UPDATE uid_submissions

                            SET
                                status='APPROVED',
                                reviewed_at=?

                            WHERE id=?
                            """,
                            (
                                utc_iso(now_utc()),
                                submission_id
                            )
                        )


                        conn.execute(
                            """
                            UPDATE users

                            SET
                                status='VIP',
                                vip_until=?

                            WHERE user_id=?
                            """,
                            (
                                vip_until,
                                submission["user_id"]
                            )
                        )


                        conn.commit()

                    finally:

                        conn.close()


                try:

                    bot.send_message(
                        submission["user_id"],
                        "⭐ আপনার VIP approved হয়েছে.",
                        reply_markup=main_keyboard(
                            submission["user_id"]
                        )
                    )

                except Exception:

                    pass


            else:

                with DB_LOCK:

                    conn = db()

                    try:

                        conn.execute(
                            """
                            UPDATE uid_submissions

                            SET
                                status='REJECTED',
                                reviewed_at=?

                            WHERE id=?
                            """,
                            (
                                utc_iso(now_utc()),
                                submission_id
                            )
                        )


                        conn.commit()

                    finally:

                        conn.close()


                try:

                    bot.send_message(
                        submission["user_id"],
                        "❌ আপনার UID rejected হয়েছে.",
                        reply_markup=main_keyboard(
                            submission["user_id"]
                        )
                    )

                except Exception:

                    pass


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                "✅ UID review complete.",
                reply_markup=admin_keyboard()
            )

            return True


        # ====================================================
        # VIP MANAGEMENT
        # ====================================================

        if action == "vip_manage":

            parts = text.split()


            if (
                len(parts) == 3
                and
                parts[0].lower() == "add"
            ):

                target_id = int(
                    parts[1]
                )

                days = int(
                    parts[2]
                )


                vip_until = (
                    now_bd()
                    +
                    timedelta(days=days)
                ).isoformat()


                with DB_LOCK:

                    conn = db()

                    try:

                        conn.execute(
                            """
                            UPDATE users

                            SET
                                status='VIP',
                                vip_until=?

                            WHERE user_id=?
                            """,
                            (
                                vip_until,
                                target_id
                            )
                        )


                        conn.commit()

                    finally:

                        conn.close()


                clear_state(
                    user_id
                )


                bot.send_message(
                    message.chat.id,
                    "⭐ VIP added.",
                    reply_markup=admin_keyboard()
                )

                return True


            if (
                len(parts) == 2
                and
                parts[0].lower() == "remove"
            ):

                target_id = int(
                    parts[1]
                )


                with DB_LOCK:

                    conn = db()

                    try:

                        conn.execute(
                            """
                            UPDATE users

                            SET
                                status='FREE',
                                vip_until=NULL

                            WHERE user_id=?
                            """,
                            (
                                target_id,
                            )
                        )


                        conn.commit()

                    finally:

                        conn.close()


                clear_state(
                    user_id
                )


                bot.send_message(
                    message.chat.id,
                    "⭐ VIP removed.",
                    reply_markup=admin_keyboard()
                )

                return True


            raise ValueError(
                "VIP format ভুল."
            )


        # ====================================================
        # WALLET ADJUST
        # ====================================================

        if action == "wallet_adjust":

            parts = text.split()


            if len(parts) != 2:

                raise ValueError(
                    "USER_ID AMOUNT"
                )


            target_id = int(
                parts[0]
            )


            amount_cents = int(
                round(
                    float(parts[1])
                    * 100
                )
            )


            with DB_LOCK:

                conn = db()

                try:

                    exists = conn.execute(
                        """
                        SELECT 1
                        FROM users
                        WHERE user_id=?
                        """,
                        (
                            target_id,
                        )
                    ).fetchone()


                    if not exists:

                        raise ValueError(
                            "User পাওয়া যায়নি."
                        )


                    conn.execute(
                        """
                        UPDATE users

                        SET
                            wallet_cents=
                                wallet_cents+?

                        WHERE user_id=?
                        """,
                        (
                            amount_cents,
                            target_id
                        )
                    )


                    conn.execute(
                        """
                        INSERT INTO wallet_tx(
                            user_id,
                            amount_cents,
                            kind,
                            note,
                            created_at
                        )
                        VALUES(?,?,?,?,?)
                        """,
                        (
                            target_id,
                            amount_cents,
                            "ADMIN",
                            "Admin wallet adjustment",
                            utc_iso(now_utc())
                        )
                    )


                    conn.commit()

                finally:

                    conn.close()


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                "💳 Wallet adjusted.",
                reply_markup=admin_keyboard()
            )

            return True


        # ====================================================
        # BROADCAST
        # ====================================================

        if action == "broadcast":

            with DB_LOCK:

                conn = db()

                try:

                    users = conn.execute(
                        """
                        SELECT user_id
                        FROM users
                        WHERE blocked=0
                        """
                    ).fetchall()

                finally:

                    conn.close()


            sent = 0


            for row in users:

                try:

                    bot.send_message(
                        row["user_id"],
                        text
                    )

                    sent += 1

                except Exception:

                    pass


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                f"📢 Broadcast complete.\n"
                f"Sent: <b>{sent}</b>",
                reply_markup=admin_keyboard()
            )

            return True


        # ====================================================
        # SUB ADMIN
        # ====================================================

        if action == "subadmin":

            parts = text.split()


            if (
                len(parts) == 2
                and
                parts[0].lower() == "remove"
            ):

                target_id = int(
                    parts[1]
                )


                with DB_LOCK:

                    conn = db()

                    try:

                        conn.execute(
                            """
                            DELETE FROM admins
                            WHERE user_id=?
                            """,
                            (
                                target_id,
                            )
                        )


                        conn.commit()

                    finally:

                        conn.close()


                clear_state(
                    user_id
                )


                bot.send_message(
                    message.chat.id,
                    "🛡️ Sub-admin removed.",
                    reply_markup=admin_keyboard()
                )

                return True


            if (
                len(parts) >= 3
                and
                parts[0].lower() == "add"
            ):

                target_id = int(
                    parts[1]
                )


                permission_string = ",".join(
                    parts[2:]
                )


                with DB_LOCK:

                    conn = db()

                    try:

                        conn.execute(
                            """
                            INSERT INTO admins(
                                user_id,
                                permissions
                            )
                            VALUES(?,?)

                            ON CONFLICT(user_id)

                            DO UPDATE SET
                                permissions=
                                    excluded.permissions
                            """,
                            (
                                target_id,
                                permission_string
                            )
                        )


                        conn.commit()

                    finally:

                        conn.close()


                clear_state(
                    user_id
                )


                bot.send_message(
                    message.chat.id,
                    "🛡️ Sub-admin saved.",
                    reply_markup=admin_keyboard()
                )

                return True


            raise ValueError(
                "Sub-admin format ভুল."
            )


        # ====================================================
        # NOTIFY TARGET
        # ====================================================

        if action == "notify_target":

            parts = text.split(
                maxsplit=1
            )


            if (
                len(parts) == 2
                and
                parts[0].lower()
                == "remove"
            ):

                with DB_LOCK:

                    conn = db()

                    try:

                        conn.execute(
                            """
                            DELETE FROM notify_targets
                            WHERE chat_id=?
                            """,
                            (
                                parts[1].strip(),
                            )
                        )


                        conn.commit()

                    finally:

                        conn.close()


                clear_state(
                    user_id
                )


                bot.send_message(
                    message.chat.id,
                    "🎯 Notification target removed.",
                    reply_markup=admin_keyboard()
                )

                return True


            if (
                len(parts) == 2
                and
                parts[0].lower()
                == "test"
            ):

                target = parts[1].strip()


                bot.send_message(
                    target,
                    "✅ SM QUATEX SURE SHORT notification test."
                )


                clear_state(
                    user_id
                )


                bot.send_message(
                    message.chat.id,
                    "✅ Test sent.",
                    reply_markup=admin_keyboard()
                )

                return True


            target = text.strip()


            with DB_LOCK:

                conn = db()

                try:

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO notify_targets(
                            chat_id,
                            title,
                            enabled
                        )
                        VALUES(?,?,1)
                        """,
                        (
                            target,
                            target
                        )
                    )


                    conn.commit()

                finally:

                    conn.close()


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                "🎯 Notification target added.",
                reply_markup=admin_keyboard()
            )

            return True


        # ====================================================
        # TEXT EDITOR
        # ====================================================

        if action == "text_editor":

            if "=" not in text:

                raise ValueError(
                    "key=value format ব্যবহার করুন."
                )


            key, value = text.split(
                "=",
                1
            )


            key = key.strip()
            value = value.strip()


            allowed = {
                "welcome",
                "trading_rules",
                "notice",
                "confidence",
                "referral_bonus",
                "min_withdraw",
                "free_limit"
            }


            if key not in allowed:

                raise ValueError(
                    "এই key editable নয়."
                )


            set_setting(
                key,
                value
            )


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                "📝 Text saved successfully.",
                reply_markup=admin_keyboard()
            )

            return True


        # ====================================================
        # LIVE SIGNAL
        # ====================================================

        if action == "live_signal":

            if text.upper() == "END":

                with DB_LOCK:

                    conn = db()

                    try:

                        conn.execute(
                            """
                            UPDATE live_sessions

                            SET
                                active=0,
                                ended_at=?

                            WHERE id=?
                            """,
                            (
                                utc_iso(now_utc()),
                                state["session_id"]
                            )
                        )


                        conn.commit()

                    finally:

                        conn.close()


                clear_state(
                    user_id
                )


                bot.send_message(
                    message.chat.id,
                    "⏹️ Live Session ended.",
                    reply_markup=admin_keyboard()
                )

                return True


            parts = [
                x.strip()
                for x in text.split("-")
            ]


            if len(parts) < 2:

                raise ValueError(
                    "PAIR - DIRECTION - CONFIDENCE"
                )


            pair = parts[0].upper()

            direction = normalize_direction(
                parts[1]
            )


            if not direction:

                raise ValueError(
                    "Direction invalid."
                )


            confidence = (
                parts[2]
                if len(parts) >= 3
                else
                get_setting(
                    "confidence",
                    "95–99%"
                )
            )


            signal_time = now_bd().strftime(
                "%I:%M %p"
            )


            with DB_LOCK:

                conn = db()

                try:

                    cur=conn.execute(
                        """
                        INSERT INTO live_signals(
                            session_id,
                            pair,
                            signal_time,
                            direction,
                            confidence,
                            created_at
                        )
                        VALUES(?,?,?,?,?,?)
                        """,
                        (
                            state["session_id"],
                            pair,
                            signal_time,
                            direction,
                            confidence,
                            utc_iso(now_utc())
                        )
                    )
                    live_signal_id=cur.lastrowid
                    conn.commit()

                finally:

                    conn.close()


            icon = (
                "🟢⬆️"
                if direction == "UP"
                else
                "🔴⬇️"
            )


            live_message = (

                "⚡ <b>LIVE SIGNAL</b>\n\n"

                f"💱 Pair: "
                f"<b>{escape(pair)}</b>\n"

                f"⏰ Time: "
                f"<b>{signal_time}</b>\n"

                f"{icon} Direction: "
                f"<b>{direction}</b>\n"

                f"🎯 Confidence: "
                f"<b>{escape(confidence)}</b>"

            )


            with DB_LOCK:

                conn = db()

                try:

                    users = conn.execute(
                        """
                        SELECT user_id
                        FROM users

                        WHERE
                            blocked=0
                            AND notify=1
                        """
                    ).fetchall()


                    targets = conn.execute(
                        """
                        SELECT chat_id
                        FROM notify_targets

                        WHERE enabled=1
                        """
                    ).fetchall()

                finally:

                    conn.close()


            for row in users:

                try:

                    bot.send_message(
                        row["user_id"],
                        live_message,
                        reply_markup=live_result_markup(live_signal_id)
                    )

                except Exception:

                    pass


            for target in targets:

                try:

                    bot.send_message(
                        target["chat_id"],
                        live_message
                    )

                except Exception:

                    pass


            bot.send_message(
                message.chat.id,
                "⚡ Live signal sent.",
                reply_markup=back_keyboard()
            )

            return True


        # ====================================================
        # MM
        # ====================================================

        if action == "mm":

            key = state["key"]

            value = text.replace(
                "$",
                ""
            ).strip()


            if key == "max_trades":

                int(value)

            else:

                float(value)


            mm_set(
                user_id,
                key,
                value
            )


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                "✅ Money Management value saved.",
                reply_markup=mm_keyboard()
            )

            return True


        # ====================================================
        # WITHDRAW
        # ====================================================

        if action == "withdraw":

            stage = state.get(
                "stage",
                "amount"
            )


            # ----------------------------
            # Amount
            # ----------------------------

            if stage == "amount":

                amount_cents = int(
                    round(
                        float(
                            text.replace(
                                "$",
                                ""
                            )
                        )
                        * 100
                    )
                )


                minimum = int(
                    round(
                        float(
                            get_setting(
                                "min_withdraw",
                                "5.00"
                            )
                        )
                        * 100
                    )
                )


                current_user = get_user(
                    user_id
                )


                if get_setting(
                    "withdrawals",
                    "ON"
                ) != "ON":

                    raise ValueError(
                        "Withdrawals বর্তমানে OFF."
                    )


                if amount_cents < minimum:

                    raise ValueError(
                        "Minimum withdrawal: "
                        f"${minimum/100:.2f}"
                    )


                if (
                    amount_cents
                    >
                    current_user["wallet_cents"]
                ):

                    raise ValueError(
                        "Wallet balance যথেষ্ট নয়."
                    )


                STATES[user_id] = {

                    "action": "withdraw",

                    "stage": "method",

                    "amount": amount_cents

                }


                bot.send_message(
                    message.chat.id,
                    "💸 Method পাঠান.\n"
                    "Example: bKash / Nagad / Bank",
                    reply_markup=back_keyboard()
                )

                return True


            # ----------------------------
            # Method
            # ----------------------------

            if stage == "method":

                STATES[user_id].update({

                    "stage": "account",

                    "method": text

                })


                bot.send_message(
                    message.chat.id,
                    "Account/Number পাঠান.",
                    reply_markup=back_keyboard()
                )

                return True


            # ----------------------------
            # Account
            # ----------------------------

            amount_cents = state[
                "amount"
            ]

            method = state[
                "method"
            ]

            account = text


            with DB_LOCK:

                conn = db()

                try:

                    current = conn.execute(
                        """
                        SELECT wallet_cents
                        FROM users
                        WHERE user_id=?
                        """,
                        (
                            user_id,
                        )
                    ).fetchone()


                    if (
                        not current
                        or
                        current["wallet_cents"]
                        <
                        amount_cents
                    ):

                        raise ValueError(
                            "Wallet balance যথেষ্ট নয়."
                        )


                    conn.execute(
                        """
                        UPDATE users

                        SET
                            wallet_cents=
                                wallet_cents-?

                        WHERE
                            user_id=?
                            AND wallet_cents>=?
                        """,
                        (
                            amount_cents,
                            user_id,
                            amount_cents
                        )
                    )


                    conn.execute(
                        """
                        INSERT INTO withdrawals(
                            user_id,
                            amount_cents,
                            method,
                            account,
                            created_at
                        )
                        VALUES(?,?,?,?,?)
                        """,
                        (
                            user_id,
                            amount_cents,
                            method,
                            account,
                            utc_iso(now_utc())
                        )
                    )


                    conn.execute(
                        """
                        INSERT INTO wallet_tx(
                            user_id,
                            amount_cents,
                            kind,
                            note,
                            created_at
                        )
                        VALUES(?,?,?,?,?)
                        """,
                        (
                            user_id,
                            -amount_cents,
                            "WITHDRAW_HOLD",
                            "Withdrawal request",
                            utc_iso(now_utc())
                        )
                    )


                    conn.commit()

                finally:

                    conn.close()


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                "✅ Withdrawal request submitted.",
                reply_markup=main_keyboard(
                    user_id
                )
            )


            try:

                bot.send_message(

                    ADMIN_ID,

                    "💸 <b>WITHDRAWAL PENDING</b>\n\n"

                    f"User: "
                    f"<code>{user_id}</code>\n"

                    f"Amount: "
                    f"<b>${amount_cents/100:.2f}</b>\n"

                    f"Method: "
                    f"{escape(method)}\n"

                    f"Account: "
                    f"<code>{escape(account)}</code>"

                )

            except Exception:

                pass


            return True


        # ====================================================
        # WITHDRAW REVIEW
        # ====================================================

        if action == "withdraw_review":

            parts = text.lower().split()


            if (
                len(parts) != 2
                or
                parts[0]
                not in (
                    "approve",
                    "reject"
                )
            ):

                raise ValueError(
                    "approve ID অথবা reject ID"
                )


            withdrawal_id = int(
                parts[1]
            )


            with DB_LOCK:

                conn = db()

                try:

                    withdrawal = conn.execute(
                        """
                        SELECT *
                        FROM withdrawals

                        WHERE
                            id=?
                            AND status='PENDING'
                        """,
                        (
                            withdrawal_id,
                        )
                    ).fetchone()

                finally:

                    conn.close()


            if not withdrawal:

                raise ValueError(
                    "Withdrawal পাওয়া যায়নি."
                )


            if parts[0] == "approve":

                new_status = "APPROVED"


            else:

                new_status = "REJECTED"


            with DB_LOCK:

                conn = db()

                try:

                    conn.execute(
                        """
                        UPDATE withdrawals

                        SET
                            status=?,
                            reviewed_at=?

                        WHERE id=?
                        """,
                        (
                            new_status,
                            utc_iso(now_utc()),
                            withdrawal_id
                        )
                    )


                    if new_status == "REJECTED":

                        conn.execute(
                            """
                            UPDATE users

                            SET
                                wallet_cents=
                                    wallet_cents+?

                            WHERE user_id=?
                            """,
                            (
                                withdrawal["amount_cents"],
                                withdrawal["user_id"]
                            )
                        )


                        conn.execute(
                            """
                            INSERT INTO wallet_tx(
                                user_id,
                                amount_cents,
                                kind,
                                note,
                                created_at
                            )
                            VALUES(?,?,?,?,?)
                            """,
                            (
                                withdrawal["user_id"],
                                withdrawal["amount_cents"],
                                "WITHDRAW_REFUND",
                                "Rejected withdrawal",
                                utc_iso(now_utc())
                            )
                        )


                    conn.commit()

                finally:

                    conn.close()


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                "✅ Withdrawal review complete.",
                reply_markup=admin_keyboard()
            )


            try:

                bot.send_message(
                    withdrawal["user_id"],

                    "💸 Withdrawal update:\n\n"
                    f"Amount: "
                    f"${withdrawal['amount_cents']/100:.2f}\n"
                    f"Status: "
                    f"<b>{new_status}</b>",

                    reply_markup=main_keyboard(
                        withdrawal["user_id"]
                    )
                )

            except Exception:

                pass


            return True


        # ====================================================
        # FREE LIMIT
        # ====================================================

        if action == "set_free_limit":

            value = int(
                text
            )


            if value < 0:

                raise ValueError(
                    "Limit negative হতে পারে না."
                )


            set_setting(
                "free_limit",
                value
            )


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                f"🎟️ Free limit updated: "
                f"<b>{value}</b>",
                reply_markup=admin_keyboard()
            )

            return True


        # ====================================================
        # MIN WITHDRAW
        # ====================================================

        if action == "set_min_withdraw":

            value = float(
                text.replace(
                    "$",
                    ""
                )
            )


            if value < 0:

                raise ValueError(
                    "Minimum negative হতে পারে না."
                )


            set_setting(
                "min_withdraw",
                f"{value:.2f}"
            )


            clear_state(
                user_id
            )


            bot.send_message(
                message.chat.id,
                f"💵 Minimum withdrawal: "
                f"<b>${value:.2f}</b>",
                reply_markup=admin_keyboard()
            )

            return True


        raise ValueError(
            "এই operation-এর input বুঝতে পারিনি."
        )


    except Exception as exc:

        bot.send_message(

            message.chat.id,

            f"❌ <b>Error</b>\n\n"
            f"{escape(str(exc))}\n\n"
            "আবার চেষ্টা করুন অথবা /cancel দিন.",

            reply_markup=back_keyboard()
        )


    return True


# ============================================================
# NAVIGATION LAYER — category menus, smart Back, missing buttons
# (added; no existing feature removed)
# ============================================================
import copy as _copy

NAV_MENU = {}    # user_id -> current menu key
NAV_INPUT = {}   # user_id -> {"action":..., ...}  (nav-layer private input state)

NAV_PARENT = {
    "main": "main", "signals": "main", "my_account": "main", "wallet": "my_account",
    "mm": "main", "referral": "main", "settings": "main", "vip": "main",
    "admin": "main", "admin_signals": "admin", "admin_users": "admin", "admin_vip": "admin",
    "admin_money": "admin", "admin_referral": "admin", "admin_settings": "admin",
    "admin_analytics": "admin", "admin_content": "admin", "admin_search": "admin_users", "admin_analysis": "admin_signals",
}

_BB = ["🔙 Back", "🏠 Main Menu"]


def _nav_is_admin(uid):
    try:
        return bool(is_master(uid) or get_permissions(uid))
    except Exception:
        return False


def signals_menu():
    return make_keyboard([["📊 Future Signals", "⚡ Live Signals"], ["🤖 AI Analysis"], _BB])


def my_account_menu():
    return make_keyboard([["👤 My Status", "💵 Wallet"], ["🆔 Submit Quotex UID"], ["📜 Trading Contract"], _BB])


def wallet_menu_kb():
    return make_keyboard([["💰 Balance", "💸 Withdraw"], ["📜 History"], _BB])


def referral_menu_kb():
    return make_keyboard([["👥 Referral Link", "📊 Referral Stats"], ["🏆 Leaderboard", "🎁 Bonus Info"], _BB])


def settings_menu_kb():
    return make_keyboard([["🔔 Notifications", "❓ Help / FAQ"], ["📖 Tutorial", "💬 Support"], ["📜 Terms"], _BB])


def vip_menu_kb(uid):
    u = get_user(uid)
    if u and vip_is_active(u):
        return make_keyboard([["🔄 Renew VIP", "📊 My Status"], _BB])
    return make_keyboard([["🆔 Submit UID", "🔗 Register on Quotex"], _BB])


def admin_signals_kb():
    return make_keyboard([
        ["➕ Add Future Signals", "📋 Future Signal List"], ["✏️ Edit Signal", "🗑️ Delete Signal"],
        ["🧹 Clear Future Signals", "📤 Auto Send ON/OFF"], ["🎯 Signal Audience", "⚡ Live Session"],
        ["📊 Signal Debug", "📈 Signal Performance"], ["🧠 Analysis Rules"], _BB])


def admin_users_kb():
    return make_keyboard([
        ["🔍 Search User", "👥 All Users"], ["🚫 Blocked Users", "⚠️ Warning List"],
        ["📩 Message User", "📢 Broadcast"], ["📋 Broadcast History", "📊 User Stats"],
        ["📤 Export Users CSV"], _BB])


def admin_vip_kb():
    return make_keyboard([
        ["📋 VIP List", "➕ Add VIP"], ["⏰ Expiry Table", "❌ Remove VIP"],
        ["📢 Send Reminder", "🔄 Pending Renewals"], ["🆔 Pending UID", "📊 VIP Stats"], _BB])


def admin_money_kb():
    return make_keyboard([
        ["💸 Withdrawals", "📊 Withdrawal Reports"], ["💳 Wallet Adjust", "💰 Referral History"],
        ["📊 Revenue Report"], _BB])


def admin_referral_kb():
    return make_keyboard([
        ["📋 Pending Referral", "✅ Approved Referral"], ["❌ Rejected Referral", "⚠️ Referral Warning"],
        ["🚫 Blocked Referral", "💰 Referral History"], ["🏆 Leaderboard"], _BB])


def admin_settings_kb():
    return make_keyboard([
        ["🛠️ Maintenance ON/OFF", "⚡ Live ON/OFF"], ["🎟️ Set Free Limit", "💵 Set Min Withdraw"],
        ["💸 Withdraw ON/OFF", "🔔 Auto Notification ON/OFF"], ["🤖 AI Settings", "🛡️ Sub-admins"],
        ["🎯 Notify Targets", "🔗 Quotex Link"], ["📊 Withdrawal Limit/Day", "📝 Bot Text Editor"], _BB])


def admin_analytics_kb():
    return make_keyboard([
        ["📊 Dashboard", "📈 Result Stats"], ["👥 User Stats", "💰 Revenue"],
        ["🤖 AI Usage Stats", "📊 Daily Report"], _BB])


def admin_content_kb():
    return make_keyboard([
        ["📝 Text Editor", "👋 Welcome Text"], ["📊 Signal Text", "⭐ VIP Text"],
        ["💰 MM Text", "💸 Withdraw Text"], ["🎁 Referral Text", "🛠️ Error Text"],
        ["🔔 Reminder Text"], _BB])


def admin_home_kb():
    return make_keyboard([
        ["📊 Signals", "👥 Users"], ["⭐ VIP", "💰 Money"], ["🎁 Referral", "⚙️ Settings"],
        ["📈 Analytics", "📝 Content"], ["🧠 Analysis Rules", "📸 Screenshot Signal"], _BB])


def admin_search_kb():
    return make_keyboard([["🆔 By ID", "👤 By Username"], ["📝 By Name"], _BB])


_MENU_TABLE = {
    "signals": ("📊 <b>SIGNALS</b>", lambda u: signals_menu()),
    "my_account": ("💰 <b>MY ACCOUNT</b>", lambda u: my_account_menu()),
    "mm": ("💵 <b>MONEY MANAGEMENT</b>", lambda u: mm_keyboard()),
    "admin_analysis": ("🧠 <b>ANALYSIS RULES</b>", lambda u: admin_analysis_kb()),
    "wallet": ("💵 <b>WALLET</b>", lambda u: wallet_menu_kb()),
    "referral": ("🎁 <b>REFERRAL</b>", lambda u: referral_menu_kb()),
    "settings": ("⚙️ <b>SETTINGS</b>", lambda u: settings_menu_kb()),
    "vip": ("⭐ <b>VIP</b>", lambda u: vip_menu_kb(u)),
    "admin": ("👑 <b>ADMIN CONTROL</b>", lambda u: admin_home_kb()),
    "admin_signals": ("📊 <b>SIGNALS</b>", lambda u: admin_signals_kb()),
    "admin_users": ("👥 <b>USERS</b>", lambda u: admin_users_kb()),
    "admin_vip": ("⭐ <b>VIP</b>", lambda u: admin_vip_kb()),
    "admin_money": ("💰 <b>MONEY</b>", lambda u: admin_money_kb()),
    "admin_referral": ("🎁 <b>REFERRAL</b>", lambda u: admin_referral_kb()),
    "admin_settings": ("⚙️ <b>SETTINGS</b>", lambda u: admin_settings_kb()),
    "admin_analytics": ("📈 <b>ANALYTICS</b>", lambda u: admin_analytics_kb()),
    "admin_content": ("📝 <b>CONTENT</b>", lambda u: admin_content_kb()),
    "admin_search": ("🔍 <b>SEARCH USER</b>", lambda u: admin_search_kb()),
}


def show_menu(chat_id, uid, key):
    try:
        if key == "main":
            NAV_MENU[uid] = "main"
            send_main_menu(chat_id, uid, "🏠 <b>Main Menu</b>")
            return True
        if key.startswith("admin") and not _nav_is_admin(uid):
            NAV_MENU[uid] = "main"
            send_main_menu(chat_id, uid, "⛔ Admin access নেই.")
            return True
        title, builder = _MENU_TABLE[key]
        NAV_MENU[uid] = key
        bot.send_message(chat_id, title, reply_markup=builder(uid))
    except Exception:
        logger.exception("show_menu failed")
        try:
            NAV_MENU[uid] = "main"
            send_main_menu(chat_id, uid, "🏠 <b>Main Menu</b>")
        except Exception:
            pass
    return True


def handle_back_button(message, state=None):
    uid = message.from_user.id
    cur = NAV_MENU.get(uid, "main")
    prev = (state or {}).get("previous_menu") if isinstance(state, dict) else None
    had_state = (uid in STATES) or (uid in NAV_INPUT)
    NAV_INPUT.pop(uid, None)
    try:
        clear_state(uid)
    except Exception:
        STATES.pop(uid, None)
    if prev and prev in _MENU_TABLE:
        target = prev
    elif had_state and cur in _MENU_TABLE:
        target = cur
    else:
        target = NAV_PARENT.get(cur, "main")
    if target not in _MENU_TABLE and target != "main":
        target = "main"
    return show_menu(message.chat.id, uid, target)


def set_user_state(user_id, action, previous_menu=None, **extra):
    data = {"action": action, "previous_menu": previous_menu or NAV_MENU.get(user_id, "main")}
    data.update(extra)
    try:
        _state_set(user_id, data)
    except Exception:
        STATES[user_id] = data


class _MsgProxy:
    """Wraps a Telegram message with overridden text (safe, no copy())."""
    def __init__(self, message, text):
        object.__setattr__(self, "_m", message)
        object.__setattr__(self, "text", text)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_m"), name)


def _legacy(message, legacy_text):
    return message_router(_MsgProxy(message, legacy_text))


def _admin_legacy(message, label):
    return handle_admin_button(_MsgProxy(message, label))


def _say(message, text, kb=None):
    bot.send_message(message.chat.id, text, reply_markup=kb)


def _q(sql, args=()):
    with DB_LOCK:
        conn = db()
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()


# ---------------- user leaf functions ----------------
def wallet_balance(message):
    u = get_user(message.from_user.id)
    bal = int((u["wallet_cents"] if u else 0) or 0)
    _say(message, f"💰 <b>BALANCE</b>\n\n💵 Wallet: <b>${bal/100:.2f}</b>", wallet_menu_kb())


def wallet_history(message):
    uid = message.from_user.id
    rows = _q("SELECT * FROM wallet_tx WHERE user_id=? ORDER BY id DESC LIMIT 15", (uid,))
    if not rows:
        return _say(message, "📭 কোনো transaction নেই.", wallet_menu_kb())
    out = ["📜 <b>HISTORY</b>", ""]
    for r in rows:
        k = r.keys()
        amt = int(r["amount_cents"]) if "amount_cents" in k and r["amount_cents"] is not None else 0
        note = escape(str(r["note"] if "note" in k and r["note"] else (r["type"] if "type" in k else "")))
        when = escape(str(r["created_at"] if "created_at" in k else ""))[:16]
        out.append(f"• {when} | ${amt/100:+.2f} | {note}")
    _say(message, "\n".join(out), wallet_menu_kb())


def referral_stats(message):
    u = get_user(message.from_user.id)
    n = int((u["refs_count"] if u else 0) or 0)
    _say(message, f"📊 <b>REFERRAL STATS</b>\n\n👥 Total Referrals: <b>{n}</b>", referral_menu_kb())


def referral_leaderboard(message):
    rows = _q("SELECT user_id,first_name,username,refs_count FROM users WHERE refs_count>0 ORDER BY refs_count DESC LIMIT 10")
    if not rows:
        return _say(message, "📭 এখনও কোনো referral নেই.", None)
    out = ["🏆 <b>LEADERBOARD</b>", ""]
    for i, r in enumerate(rows, 1):
        name = escape(r["first_name"] or r["username"] or str(r["user_id"]))
        out.append(f"{i}. {name} — {r['refs_count']} referrals")
    _say(message, "\n".join(out))


def referral_bonus_info(message):
    try:
        rows = _q("SELECT min_refs,bonus_cents FROM referral_levels WHERE enabled=1 ORDER BY min_refs")
    except Exception:
        rows = []
    out = ["🎁 <b>BONUS INFO</b>", ""]
    out += [f"👥 {r['min_refs']} referrals → ${r['bonus_cents']/100:.2f}" for r in rows] or ["Bonus tier এখনও set হয়নি."]
    _say(message, "\n".join(out), referral_menu_kb())


def _text_or(key, default):
    try:
        v = get_setting(key, "")
        return v if v else default
    except Exception:
        return default


def tutorial_menu(message):
    _say(message, _text_or("tutorial_text", "📖 <b>TUTORIAL</b>\n\n1️⃣ Quotex account খুলুন\n2️⃣ $15+ deposit করুন\n3️⃣ UID submit করুন\n4️⃣ Admin review → VIP\n5️⃣ Signal এ WIN/LOSS/SKIP দিন"), settings_menu_kb())


def support_menu(message):
    _say(message, _text_or("support_text", "💬 <b>SUPPORT</b>\n\nসাহায্যের জন্য admin-এর সাথে যোগাযোগ করুন."), settings_menu_kb())


def terms_menu(message):
    _say(message, _text_or("terms_text", "📜 <b>TERMS</b>\n\nTrading ঝুঁকিপূর্ণ. নিজ দায়িত্বে trade করুন."), settings_menu_kb())


def join_vip_menu(message):
    uid = message.from_user.id
    u = get_user(uid)
    if u and vip_is_active(u):
        left = "—"
        try:
            if u["vip_until"]:
                left = f"{max((datetime.fromisoformat(u['vip_until']).astimezone(BD_TZ) - now_bd()).days, 0)} days"
        except Exception:
            pass
        text = ("━━━━━━━━━━━━━━━━━━\n⭐ <b>VIP STATUS</b>\n━━━━━━━━━━━━━━━━━━\n\n🎉 আপনি VIP!\n\n"
                f"⭐ Status: ACTIVE\n⏰ Expires: {escape(_fmt_dt(u['vip_until'], 'Unlimited'))}\n⏳ Days Left: {left}\n\n"
                "✅ Unlimited Signals • Live Signals • AI 50/day • Priority Support\n\n"
                "📖 VIP RULES:\n⏰ Expiry 7 days আগে reminder\n🆔 Admin review")
    else:
        text = ("━━━━━━━━━━━━━━━━━━\n⭐ <b>JOIN VIP</b>\n━━━━━━━━━━━━━━━━━━\n\n🎉 VIP সুবিধা:\n"
                "✅ Unlimited Future & Live Signals\n✅ AI Analysis 50/day\n✅ Priority Support\n✅ No Free Limit\n\n"
                "📖 VIP RULES:\n👤 Non-VIP Free Limit: 4 signals (২ দিনের cycle)\n🆔 UID verification admin review করবে\n\n"
                "💰 VIP পেতে:\n১. 🔗 Quotex Account (referral link)\n২. 💰 $15+ deposit\n৩. 🆔 UID Submit\n৪. ✅ Admin Review")
    NAV_MENU[uid] = "vip"
    _say(message, text, vip_menu_kb(uid))


def renew_vip_menu(message):
    uid = message.from_user.id
    try:
        _legacy(message, "🆔 Submit Quotex UID")
    except Exception:
        _say(message, "🔄 Renew করতে UID আবার submit করুন.", vip_menu_kb(uid))


def _register_link(message):
    link = _text_or("quotex_ref_link", "")
    _say(message, f"🔗 <b>Quotex Register</b>\n\n{escape(link)}" if link else "⚠️ Admin এখনও Quotex link set করেনি.", vip_menu_kb(message.from_user.id))


# ---------------- admin leaf functions ----------------
def _count(sql, args=()):
    try:
        return int(_q(sql, args)[0][0] or 0)
    except Exception:
        return 0


def admin_user_stats(message):
    t = _count("SELECT COUNT(*) FROM users")
    v = _count("SELECT COUNT(*) FROM users WHERE status='VIP'")
    b = _count("SELECT COUNT(*) FROM users WHERE blocked=1")
    _say(message, f"📊 <b>USER STATS</b>\n\n👥 Total: {t}\n⭐ VIP: {v}\n🚫 Blocked: {b}\n🆓 Free: {max(t-v-b,0)}", admin_users_kb())


def admin_vip_stats(message):
    v = _count("SELECT COUNT(*) FROM users WHERE status='VIP'")
    soon = _count("SELECT COUNT(*) FROM users WHERE status='VIP' AND vip_until IS NOT NULL AND vip_until<=?", (utc_iso(now_utc() + timedelta(days=7)),))
    _say(message, f"📊 <b>VIP STATS</b>\n\n⭐ Active VIP: {v}\n⏰ Expiring ≤7d: {soon}", admin_vip_kb())


def admin_revenue_report(message):
    w = _count("SELECT COALESCE(SUM(wallet_cents),0) FROM users")
    vipn = _count("SELECT COUNT(*) FROM users WHERE status='VIP'")
    _say(message, f"📊 <b>REVENUE REPORT</b>\n\n💵 Total wallet liability: ${w/100:.2f}\n⭐ VIP: {vipn}", admin_money_kb())


def admin_daily_report(message):
    d = now_bd().strftime("%Y-%m-%d")
    nu = _count("SELECT COUNT(*) FROM users WHERE created_at LIKE ?", (d + "%",))
    sg = _count("SELECT COUNT(*) FROM signals WHERE signal_date=?", (d,))
    _say(message, f"📊 <b>DAILY REPORT</b> ({d})\n\n👥 New users: {nu}\n📊 Signals today: {sg}", admin_analytics_kb())


def admin_signal_debug(message):
    a = _count("SELECT COUNT(*) FROM signals WHERE active=1")
    s = _count("SELECT COUNT(*) FROM signals WHERE auto_sent=1")
    _say(message, f"📊 <b>SIGNAL DEBUG</b>\n\n✅ Active: {a}\n📤 Auto-sent: {s}\n🕒 Now (BD): {now_bd().strftime('%d %b %Y %I:%M %p')}", admin_signals_kb())


def admin_signal_performance(message):
    rows = _q("SELECT result,COUNT(*) n FROM signal_user_results GROUP BY result")
    d = {r["result"]: r["n"] for r in rows}
    w, l = d.get("WIN", 0), d.get("LOSS", 0)
    acc = (w / (w + l) * 100) if (w + l) else 0
    _say(message, f"📈 <b>SIGNAL PERFORMANCE</b>\n\n✅ WIN: {w}\n❌ LOSS: {l}\n⏭️ SKIP: {d.get('SKIP',0)}\n🎯 Accuracy: {acc:.1f}%", admin_signals_kb())


def admin_export_users(message):
    import csv, io
    rows = _q("SELECT user_id,username,first_name,status,vip_until,wallet_cents,refs_count,blocked,created_at FROM users")
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["user_id", "username", "first_name", "status", "vip_until", "wallet_cents", "refs_count", "blocked", "created_at"])
    for r in rows:
        wr.writerow(list(r))
    f = io.BytesIO(buf.getvalue().encode("utf-8"))
    f.name = "users.csv"
    bot.send_document(message.chat.id, f, reply_markup=admin_users_kb())


def admin_broadcast_history(message):
    try:
        rows = _q("SELECT * FROM broadcast_history ORDER BY id DESC LIMIT 10")
    except Exception:
        rows = []
    if not rows:
        return _say(message, "📭 Broadcast history নেই.", admin_users_kb())
    _say(message, "📋 <b>BROADCAST HISTORY</b>\n\n" + "\n".join(f"• {escape(str(dict(r))[:120])}" for r in rows), admin_users_kb())


def admin_pending_renewals(message):
    rows = _q("SELECT user_id,vip_until FROM users WHERE status='VIP' AND vip_until IS NOT NULL AND vip_until<=? ORDER BY vip_until LIMIT 30",
              (utc_iso(now_utc() + timedelta(days=7)),))
    _say(message, "🔄 <b>PENDING RENEWALS</b>\n\n" + ("\n".join(f"<code>{r['user_id']}</code> — {escape(str(r['vip_until']))[:10]}" for r in rows) or "📭 কেউ নেই."), admin_vip_kb())


def _ask_setting(message, key, label, back_menu, numeric=False):
    NAV_INPUT[message.from_user.id] = {"action": "set_setting", "key": key, "numeric": numeric, "back": back_menu}
    cur = _text_or(key, "—")
    _say(message, f"✏️ <b>{label}</b>\nCurrent: <code>{escape(str(cur))[:200]}</code>\n\nনতুন value পাঠান (বাতিল: 🔙 Back)", make_keyboard([_BB]))


CONTENT_KEYS = {
    "👋 Welcome Text": ("welcome", "Welcome Text"), "📊 Signal Text": ("signal_text", "Signal Text"),
    "⭐ VIP Text": ("vip_text", "VIP Text"), "💰 MM Text": ("mm_text", "MM Text"),
    "💸 Withdraw Text": ("withdraw_text", "Withdraw Text"), "🎁 Referral Text": ("referral_text", "Referral Text"),
    "🛠️ Error Text": ("error_text", "Error Text"), "🔔 Reminder Text": ("reminder_text", "Reminder Text"),
}


def _user_profile(message, row):
    uid = row["user_id"]
    refs = row["refs_count"] or 0
    text = ("━━━━━━━━━━━━━━━━━━\n👤 <b>USER PROFILE</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ID: <code>{uid}</code>\n📛 @{escape(row['username'] or '-')}\n📝 {escape(row['first_name'] or '-')}\n"
            f"⭐ Status: {escape(str(row['status']))}\n📅 VIP Until: {escape(str(row['vip_until'] or '—'))[:10]}\n"
            f"💰 Wallet: ${(row['wallet_cents'] or 0)/100:.2f}\n👥 Referrals: {refs}\n"
            f"🚫 Blocked: {'Yes' if row['blocked'] else 'No'}\n📅 Joined: {escape(str(row['created_at']))[:10]}\n━━━━━━━━━━━━━━━━━━")
    ik = types.InlineKeyboardMarkup()
    ik.row(types.InlineKeyboardButton("⭐ VIP", callback_data=f"navu:vip:{uid}"), types.InlineKeyboardButton("🚫 Block/Unblock", callback_data=f"navu:block:{uid}"))
    ik.row(types.InlineKeyboardButton("📩 Message", callback_data=f"navu:msg:{uid}"), types.InlineKeyboardButton("💳 Wallet", callback_data=f"navu:wallet:{uid}"))
    ik.row(types.InlineKeyboardButton("📊 Stats", callback_data=f"navu:stats:{uid}"), types.InlineKeyboardButton("♻️ Reset", callback_data=f"navu:reset:{uid}"))
    bot.send_message(message.chat.id, text, reply_markup=ik)
    bot.send_message(message.chat.id, "ℹ️ Search আবার করতে নিচের button ব্যবহার করুন.", reply_markup=admin_search_kb())


def _search_user(message, mode, query):
    q = query.strip().lstrip("@")
    if mode == "id":
        rows = _q("SELECT * FROM users WHERE user_id=?", (int(q),)) if q.isdigit() else []
    elif mode == "username":
        rows = _q("SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (q,))
    else:
        rows = _q("SELECT * FROM users WHERE LOWER(first_name) LIKE LOWER(?) LIMIT 5", (f"%{q}%",))
    if not rows:
        return _say(message, "📭 User পাওয়া যায়নি", admin_search_kb())
    for r in rows[:5]:
        _user_profile(message, r)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("navu:"))
def nav_user_action(call):
    try:
        if not _nav_is_admin(call.from_user.id):
            return bot.answer_callback_query(call.id, "⛔ Admin only", show_alert=True)
        _, act, uid_s = call.data.split(":", 2)
        tid = int(uid_s)
        if act == "block":
            with DB_LOCK:
                conn = db()
                try:
                    conn.execute("UPDATE users SET blocked=1-COALESCE(blocked,0) WHERE user_id=?", (tid,))
                    conn.commit()
                finally:
                    conn.close()
            bot.answer_callback_query(call.id, "✅ Block status toggled")
        elif act == "reset":
            with DB_LOCK:
                conn = db()
                try:
                    conn.execute("UPDATE users SET free_used=0 WHERE user_id=?", (tid,))
                    conn.commit()
                finally:
                    conn.close()
            bot.answer_callback_query(call.id, "♻️ Reset done")
        elif act == "stats":
            r = _q("SELECT result,COUNT(*) n FROM signal_user_results WHERE user_id=? GROUP BY result", (tid,))
            bot.answer_callback_query(call.id, ", ".join(f"{x['result']}:{x['n']}" for x in r) or "No results", show_alert=True)
        elif act == "msg":
            NAV_INPUT[call.from_user.id] = {"action": "msg_user", "target": tid, "back": "admin_users"}
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, f"📩 <code>{tid}</code> কে পাঠাতে message লিখুন:", reply_markup=make_keyboard([_BB]))
        elif act == "wallet":
            NAV_INPUT[call.from_user.id] = {"action": "wallet_adj", "target": tid, "back": "admin_users"}
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, f"💳 <code>{tid}</code> — amount দিন (USD, যেমন 5 বা -2.5):", reply_markup=make_keyboard([_BB]))
        elif act == "vip":
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, "⭐ VIP দিতে ⭐ VIP → ➕ Add VIP ব্যবহার করুন.", reply_markup=admin_vip_kb())
    except Exception:
        logger.exception("nav_user_action failed")
        try:
            bot.answer_callback_query(call.id, "❌ Failed")
        except Exception:
            pass


def _nav_input_handler(message):
    uid = message.from_user.id
    st = NAV_INPUT.get(uid)
    text = (message.text or "").strip()
    act = st["action"]
    back = st.get("back", "admin")
    try:
        if act == "search":
            NAV_INPUT.pop(uid, None)
            _search_user(message, st["mode"], text)
            return
        if act == "set_setting":
            val = text
            if st.get("numeric"):
                float(val)
            set_setting(st["key"], val)
            NAV_INPUT.pop(uid, None)
            _say(message, "✅ Saved.", None)
            return show_menu(message.chat.id, uid, back)
        if act == "wd_custom_reason":
            return _wd_custom_reason(message, st)
        if act == "msg_user_id":
            tid = int(text)
            NAV_INPUT[uid] = {"action": "msg_user", "target": tid, "back": back}
            return _say(message, f"📩 <code>{tid}</code> কে পাঠাতে message লিখুন:", make_keyboard([_BB]))
        if act == "msg_user":
            NAV_INPUT.pop(uid, None)
            bot.send_message(st["target"], text)
            _say(message, "✅ Message sent.")
            return show_menu(message.chat.id, uid, back)
        if act == "wallet_adj":
            cents = int(round(float(text) * 100))
            with DB_LOCK:
                conn = db()
                try:
                    conn.execute("UPDATE users SET wallet_cents=COALESCE(wallet_cents,0)+? WHERE user_id=?", (cents, st["target"]))
                    conn.commit()
                finally:
                    conn.close()
            NAV_INPUT.pop(uid, None)
            _say(message, f"✅ Wallet adjusted {cents/100:+.2f}$")
            return show_menu(message.chat.id, uid, back)
    except ValueError:
        _say(message, "❌ সঠিক সংখ্যা দিন.")
    except Exception as exc:
        logger.exception("nav input failed")
        NAV_INPUT.pop(uid, None)
        _say(message, "❌ " + escape(str(exc)))


# ---------------- dispatcher ----------------
_USER_TO_LEGACY = {
    "🤖 AI Analysis": "📸 AI Candle Analysis",
    "💸 Withdraw": "💸 Request Withdraw",
    "🆔 Submit UID": "🆔 Submit Quotex UID",
}
_NAV_CATS_USER = {"📊 Signals": "signals", "💰 My Account": "my_account", "💵 Wallet": "wallet",
                  "🎁 Referral": "referral", "⚙️ Settings": "settings"}
_NAV_CATS_ADMIN = {"📊 Signals": "admin_signals", "👥 Users": "admin_users", "⭐ VIP": "admin_vip",
                   "💰 Money": "admin_money", "🎁 Referral": "admin_referral", "⚙️ Settings": "admin_settings",
                   "📈 Analytics": "admin_analytics", "📝 Content": "admin_content"}
_ADMIN_OWN = {
    "📊 Signal Debug": admin_signal_debug, "📈 Signal Performance": admin_signal_performance,
    "📋 Broadcast History": admin_broadcast_history, "📊 User Stats": admin_user_stats,
    "👥 User Stats": admin_user_stats, "📤 Export Users CSV": admin_export_users,
    "🔄 Pending Renewals": admin_pending_renewals, "📊 VIP Stats": admin_vip_stats,
    "📊 Revenue Report": admin_revenue_report, "💰 Revenue": admin_revenue_report,
    "📊 Daily Report": admin_daily_report,
}
_ADMIN_LEGACY_ALIASES = {
    "🏆 Leaderboard": None, "📊 Dashboard": "📊 Dashboard", "📈 Result Stats": "📈 Result Stats",
    "🤖 AI Usage Stats": "🤖 AI Usage Stats",
}
_NAV_ALL = set(_USER_TO_LEGACY) | set(_NAV_CATS_USER) | set(_NAV_CATS_ADMIN) | set(_ADMIN_OWN) | set(CONTENT_KEYS) | {
    "🔙 Back", "🏠 Main Menu", "❌ Cancel", "👑 Admin Control", "🧠 Analysis Rules", "➕ Add Analysis Rule", "📋 Analysis Rule List", "🗑️ Remove Analysis Rule", "🔘 Rule ON/OFF", "📊 Analysis Stats", "🎯 Set Confidence Min", "🤖 Analysis ON/OFF", "🔀 Analysis Engine", "💰 My Account", "💵 Money Management",
    "💰 Balance", "📜 History", "📊 Referral Stats", "🏆 Leaderboard", "🎁 Bonus Info", "📖 Tutorial",
    "💬 Support", "📜 Terms", "📩 Message User", "📝 Text Editor", "📝 Bot Text Editor", "🔄 Renew VIP", "🔗 Register on Quotex", "🔗 Quotex Link", "📊 Withdrawal Limit/Day",
    "🔍 Search User", "🆔 By ID", "👤 By Username", "📝 By Name", "👥 Referral Link", "📊 My Status",
    "📜 Signal History",
}


_MENU_LABELS_CACHE = {}


def _menu_labels():
    """Every label that lives on a menu keyboard (not flow-specific buttons like '💸 WD #1')."""
    if _MENU_LABELS_CACHE.get("v"):
        return _MENU_LABELS_CACHE["v"]
    labels = set()
    for builder in (signals_menu, my_account_menu, wallet_menu_kb, referral_menu_kb, settings_menu_kb, admin_signals_kb, admin_users_kb,
                    admin_vip_kb, admin_money_kb, admin_referral_kb, admin_settings_kb, admin_analytics_kb, admin_content_kb,
                    admin_home_kb, admin_search_kb, mm_keyboard):
        try:
            for row in builder().keyboard:
                for b in row:
                    labels.add(b["text"] if isinstance(b, dict) else b.text)
        except Exception:
            pass
    labels |= {"📊 Signals", "💰 My Account", "💵 Money Management", "🎁 Referral", "⚙️ Settings", "👑 Admin Control"}
    labels -= {"🔙 Back", "🏠 Main Menu"}
    _MENU_LABELS_CACHE["v"] = labels
    return labels


def _nav_match(message):
    try:
        if message.content_type != "text":
            return False
        uid = message.from_user.id
        t = (message.text or "").strip()
        if t in ("🔙 Back", "🏠 Main Menu", "❌ Cancel"):
            return (uid in STATES) or (uid in NAV_INPUT) or t in _NAV_ALL
        # A real menu button always wins over a half-finished input state
        if t in _menu_labels() and (uid in STATES or uid in NAV_INPUT):
            STATES.pop(uid, None)
            NAV_INPUT.pop(uid, None)
            try: clear_state(uid)
            except Exception: pass
            return True
        if uid in NAV_INPUT:
            return True
        if uid in STATES:
            return False
        return t in _NAV_ALL or t.startswith("⭐ VIP") or t.startswith("⭐ JOIN VIP")
    except Exception:
        return False


@bot.message_handler(func=lambda m: ss_text_match(m), content_types=["text"])
def ss_admin_text_handler(message):
    ss_admin_text(message)


@bot.message_handler(func=_nav_match, content_types=["text"])
def nav_dispatcher(message):
    uid = message.from_user.id
    t = (message.text or "").strip()
    try:
        register_user(message.from_user)
        if t in ("🏠 Main Menu", "❌ Cancel"):
            NAV_INPUT.pop(uid, None)
            try:
                clear_state(uid)
            except Exception:
                STATES.pop(uid, None)
            return show_menu(message.chat.id, uid, "main")
        if t == "🔙 Back":
            return handle_back_button(message, STATES.get(uid))
        if uid in NAV_INPUT:
            return _nav_input_handler(message)
        if maintenance_blocked(uid):
            return _say(message, "🛠️ Bot maintenance mode-এ আছে.", main_keyboard(uid))

        cur = NAV_MENU.get(uid, "main")
        in_admin = cur.startswith("admin") and _nav_is_admin(uid)

        if t == "👑 Admin Control":
            return show_menu(message.chat.id, uid, "admin")
        if (t.startswith("⭐ VIP") or t.startswith("⭐ JOIN VIP")) and not (in_admin and t in CONTENT_KEYS):
            if in_admin and t == "⭐ VIP":
                return show_menu(message.chat.id, uid, "admin_vip")
            return join_vip_menu(message)

        if in_admin:
            if t in _NAV_CATS_ADMIN:
                return show_menu(message.chat.id, uid, _NAV_CATS_ADMIN[t])
            if t == "🔍 Search User":
                return show_menu(message.chat.id, uid, "admin_search")
            if t in ("🆔 By ID", "👤 By Username", "📝 By Name"):
                mode = {"🆔 By ID": "id", "👤 By Username": "username", "📝 By Name": "name"}[t]
                NAV_INPUT[uid] = {"action": "search", "mode": mode, "back": "admin_search"}
                return _say(message, "🔍 Search value পাঠান:", make_keyboard([_BB]))
            if t in _ADMIN_OWN:
                return _ADMIN_OWN[t](message)
            if t == "🏆 Leaderboard":
                return referral_leaderboard(message)
            if t in ANALYSIS_ADMIN_BUTTONS:
                return ANALYSIS_ADMIN_BUTTONS[t](message)
            if t in ("📝 Text Editor", "📝 Bot Text Editor"):
                return admin_text_editor(message)
            if t == "📩 Message User":
                NAV_INPUT[uid] = {"action": "msg_user_id", "back": cur}
                return _say(message, "📩 User Telegram ID দিন:", make_keyboard([_BB]))
            if t in CONTENT_KEYS:
                k, lbl = CONTENT_KEYS[t]
                return _ask_setting(message, k, lbl, "admin_content")
            if t == "🔗 Quotex Link":
                return _ask_setting(message, "quotex_ref_link", "Quotex Link", "admin_settings")
            if t == "📊 Withdrawal Limit/Day":
                return _ask_setting(message, "withdraw_limit_day", "Withdrawal Limit/Day", "admin_settings", True)
            _admin_legacy(message, t)
            if uid not in STATES and uid not in NAV_INPUT and cur in _MENU_TABLE:
                title, builder = _MENU_TABLE[cur]
                bot.send_message(message.chat.id, "📍 " + title, reply_markup=builder(uid))
            return

        # ---- user side ----
        if t in _NAV_CATS_USER:
            return show_menu(message.chat.id, uid, _NAV_CATS_USER[t])
        if t == "💰 My Account":
            return show_menu(message.chat.id, uid, "my_account")
        if t == "💵 Money Management":
            NAV_MENU[uid] = "mm"
            return _legacy(message, "💰 Money Management")
        if t == "💰 Balance":
            return wallet_balance(message)
        if t == "📜 History":
            return wallet_history(message)
        if t == "📊 Referral Stats":
            return referral_stats(message)
        if t == "🏆 Leaderboard":
            return referral_leaderboard(message)
        if t == "🎁 Bonus Info":
            return referral_bonus_info(message)
        if t == "📖 Tutorial":
            return tutorial_menu(message)
        if t == "💬 Support":
            return support_menu(message)
        if t == "📜 Terms":
            return terms_menu(message)
        if t == "🔄 Renew VIP":
            return renew_vip_menu(message)
        if t == "🔗 Register on Quotex":
            return _register_link(message)
        if t in _USER_TO_LEGACY:
            return _legacy(message, _USER_TO_LEGACY[t])
        if t == "📊 My Status":
            return _legacy(message, "👤 My Status")
        return _legacy(message, t)
    except Exception:
        logger.exception("nav_dispatcher failed for %r", t)
        try:
            show_menu(message.chat.id, uid, "main")
        except Exception:
            pass

# ============================================================
# END NAVIGATION LAYER
# ============================================================



# ============================================================
# WITHDRAWAL SERIAL + INLINE APPROVE/REJECT (serial_no is a STRING)
# ============================================================
WD_REJECT_REASONS = {"bal": "Balance issue", "method": "Wrong method", "acct": "Wrong account", "susp": "Suspicious"}


def _next_wd_serial(conn):
    day = now_bd().strftime("%Y%m%d")
    prefix = f"WD-{day}-"
    row = conn.execute("SELECT COUNT(*) FROM withdrawals WHERE serial_no LIKE ?", (prefix + "%",)).fetchone()
    n = int(row[0] or 0) + 1
    while conn.execute("SELECT 1 FROM withdrawals WHERE serial_no=?", (f"{prefix}{n:04d}",)).fetchone():
        n += 1
    return f"{prefix}{n:04d}"


def _wd_user_submitted_text(serial, cents, method, account):
    return ("━━━━━━━━━━━━━━━━━━\n✅ <b>WITHDRAWAL SUBMITTED</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 Serial: <code>{escape(serial)}</code>\n💸 Amount: {money(cents)}\n📱 Method: {escape(str(method))}\n"
            f"👤 Account: <code>{escape(str(account))}</code>\n\n📝 Admin review করবে\n⏳ 24 ঘণ্টায় process\n━━━━━━━━━━━━━━━━━━")


def _wd_admin_text(serial, uid, method, account, cents):
    return ("━━━━━━━━━━━━━━━━━━\n💸 <b>NEW WITHDRAWAL</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 Serial: <code>{escape(serial)}</code>\n👤 User: <code>{uid}</code>\n📱 Method: {escape(str(method))}\n"
            f"👤 Account: <code>{escape(str(account))}</code>\n💸 Amount: {money(cents)}\n━━━━━━━━━━━━━━━━━━")


def _wd_inline_kb(serial):
    ik = types.InlineKeyboardMarkup()
    ik.row(types.InlineKeyboardButton("✅ Approve", callback_data=f"wdok:{serial}"),
           types.InlineKeyboardButton("❌ Reject", callback_data=f"wdno:{serial}"))
    return ik


def _wd_reason_kb(serial):
    ik = types.InlineKeyboardMarkup()
    ik.row(types.InlineKeyboardButton("💰 Balance issue", callback_data=f"wdrs:{serial}:bal"))
    ik.row(types.InlineKeyboardButton("📱 Wrong method", callback_data=f"wdrs:{serial}:method"))
    ik.row(types.InlineKeyboardButton("👤 Wrong account", callback_data=f"wdrs:{serial}:acct"))
    ik.row(types.InlineKeyboardButton("⚠️ Suspicious", callback_data=f"wdrs:{serial}:susp"))
    ik.row(types.InlineKeyboardButton("✏️ Custom", callback_data=f"wdrs:{serial}:custom"))
    return ik


def _wd_decide(admin_id, serial, approve, reason=""):
    """Returns (ok, message, user_id, cents). Serial stays a string."""
    with DB_LOCK:
        conn = db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            r = conn.execute("SELECT * FROM withdrawals WHERE serial_no=? AND status='PENDING'", (serial,)).fetchone()
            if not r:
                conn.rollback()
                return False, "Withdrawal already processed বা পাওয়া যায়নি.", None, 0
            if approve:
                hu = r["hold_until"]
                if hu:
                    try:
                        dt = datetime.fromisoformat(hu)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=UTC)
                        if dt.astimezone(UTC) > now_utc():
                            conn.rollback()
                            return False, "Withdrawal hold এখনও শেষ হয়নি.", None, 0
                    except Exception:
                        pass
                conn.execute("UPDATE withdrawals SET status='APPROVED',reviewed_at=?,reviewed_by=? WHERE serial_no=? AND status='PENDING'",
                             (utc_iso(now_utc()), admin_id, serial))
            else:
                conn.execute("UPDATE withdrawals SET status='REJECTED',reviewed_at=?,reviewed_by=?,review_note=? WHERE serial_no=? AND status='PENDING'",
                             (utc_iso(now_utc()), admin_id, reason, serial))
                conn.execute("UPDATE users SET wallet_cents=wallet_cents+? WHERE user_id=?", (r["amount_cents"], r["user_id"]))
                conn.execute("INSERT INTO wallet_tx(user_id,amount_cents,kind,note,created_at) VALUES(?,?,?,?,?)",
                             (r["user_id"], r["amount_cents"], "WITHDRAW_REFUND", f"Rejected {serial}: {reason}", utc_iso(now_utc())))
            conn.commit()
            uid_, cents = r["user_id"], r["amount_cents"]
        except Exception:
            try: conn.rollback()
            except Exception: pass
            raise
        finally:
            conn.close()
    try: admin_audit(admin_id, "WITHDRAW_DECISION", uid_, f"{serial} {'APPROVED' if approve else 'REJECTED ' + reason}")
    except Exception: pass
    return True, "OK", uid_, cents


def _wd_notify_user(uid_, serial, cents, approve, reason=""):
    try:
        if approve:
            t = f"✅ <b>WITHDRAWAL APPROVED</b>\n\n🆔 {escape(serial)}\n💸 {money(cents)}"
        else:
            t = f"❌ <b>WITHDRAWAL REJECTED</b>\n\n🆔 {escape(serial)}\nReason: {escape(reason)}\n💰 Amount refunded"
        bot.send_message(uid_, t, reply_markup=main_keyboard(uid_))
    except Exception:
        logger.exception("WD user notify failed")


def _wd_admin_ok(call):
    if not (is_master(call.from_user.id) or can(call.from_user.id, "withdraw")):
        bot.answer_callback_query(call.id, "⛔ Access denied", show_alert=True)
        return False
    return True


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("wdok:"))
def wd_approve_callback(call):
    try:
        if not _wd_admin_ok(call): return
        serial = call.data.split(":", 1)[1]
        ok, msg, u, c = _wd_decide(call.from_user.id, serial, True)
        bot.answer_callback_query(call.id, "✅ Approved" if ok else msg, show_alert=not ok)
        if ok:
            _wd_notify_user(u, serial, c, True)
            try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            except Exception: pass
            bot.send_message(call.message.chat.id, f"✅ {escape(serial)} approved.")
    except Exception:
        logger.exception("wd_approve_callback failed")
        try: bot.answer_callback_query(call.id, "❌ Failed")
        except Exception: pass


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("wdno:"))
def wd_reject_callback(call):
    try:
        if not _wd_admin_ok(call): return
        serial = call.data.split(":", 1)[1]
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"❌ Reject reason বেছে নিন — <code>{escape(serial)}</code>", reply_markup=_wd_reason_kb(serial))
    except Exception:
        logger.exception("wd_reject_callback failed")


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("wdrs:"))
def wd_reason_callback(call):
    try:
        if not _wd_admin_ok(call): return
        _, serial, key = call.data.split(":", 2)
        if key == "custom":
            NAV_INPUT[call.from_user.id] = {"action": "wd_custom_reason", "serial": serial, "back": "admin_money"}
            bot.answer_callback_query(call.id)
            return bot.send_message(call.message.chat.id, "✏️ Custom reason লিখুন:", reply_markup=make_keyboard([["🔙 Back", "🏠 Main Menu"]]))
        reason = WD_REJECT_REASONS.get(key, "Rejected")
        ok, msg, u, c = _wd_decide(call.from_user.id, serial, False, reason)
        bot.answer_callback_query(call.id, "❌ Rejected" if ok else msg, show_alert=not ok)
        if ok:
            _wd_notify_user(u, serial, c, False, reason)
            bot.send_message(call.message.chat.id, f"❌ {escape(serial)} rejected ({escape(reason)}). Refunded.")
    except Exception:
        logger.exception("wd_reason_callback failed")
        try: bot.answer_callback_query(call.id, "❌ Failed")
        except Exception: pass


def _wd_custom_reason(message, st):
    ok, msg, u, c = _wd_decide(message.from_user.id, st["serial"], False, message.text.strip()[:200])
    NAV_INPUT.pop(message.from_user.id, None)
    if ok:
        _wd_notify_user(u, st["serial"], c, False, message.text.strip()[:200])
        _say(message, f"❌ {escape(st['serial'])} rejected. Refunded.", admin_money_kb())
    else:
        _say(message, "⚠️ " + escape(msg), admin_money_kb())


def _wd_column_migration():
    with DB_LOCK:
        conn = db()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(withdrawals)").fetchall()}
            for c, d in (("serial_no", "TEXT"), ("reviewed_by", "INTEGER"), ("hold_until", "TEXT"), ("review_note", "TEXT")):
                if c not in cols:
                    conn.execute(f"ALTER TABLE withdrawals ADD COLUMN {c} {d}")
            _extra = {
                "users": (("warning_count", "INTEGER NOT NULL DEFAULT 0"), ("warning_reason", "TEXT"), ("vip_reminder_7d_sent", "INTEGER NOT NULL DEFAULT 0"),
                          ("vip_reminder_3d_sent", "INTEGER NOT NULL DEFAULT 0"), ("vip_started_at", "TEXT"), ("ai_usage_count", "INTEGER NOT NULL DEFAULT 0"), ("ai_usage_date", "TEXT")),
                "referrals": (("status", "TEXT NOT NULL DEFAULT 'PENDING'"), ("quotex_uid", "TEXT"), ("deposit_amount_cents", "INTEGER NOT NULL DEFAULT 0"),
                              ("warning_count", "INTEGER NOT NULL DEFAULT 0"), ("risk_flag", "INTEGER NOT NULL DEFAULT 0"), ("risk_note", "TEXT"), ("reviewed_by", "INTEGER"),
                              ("reviewed_at", "TEXT"), ("qualified_at", "TEXT"), ("paid_at", "TEXT"), ("reject_reason", "TEXT"), ("reject_reason_type", "TEXT"),
                              ("rejected_by", "INTEGER"), ("rejected_at", "TEXT")),
                "notify_targets": (("target_type", "TEXT NOT NULL DEFAULT 'GROUP'"), ("audience", "TEXT NOT NULL DEFAULT 'ALL'"), ("selected_users", "TEXT NOT NULL DEFAULT ''")),
            }
            for _t, _cols in _extra.items():
                have = {r[1] for r in conn.execute(f"PRAGMA table_info({_t})").fetchall()}
                if not have:
                    continue
                for c, d in _cols:
                    if c not in have:
                        conn.execute(f"ALTER TABLE {_t} ADD COLUMN {c} {d}")
            for _ix in ("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)", "CREATE INDEX IF NOT EXISTS idx_users_name ON users(first_name)",
                        "CREATE INDEX IF NOT EXISTS idx_referrals_status ON referrals(status)"):
                try: conn.execute(_ix)
                except Exception: pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wd_serial ON withdrawals(serial_no)")
            # backfill old rows so every withdrawal has a serial
            for r in conn.execute("SELECT id,created_at FROM withdrawals WHERE serial_no IS NULL OR serial_no=''").fetchall():
                day = str(r["created_at"] or "")[:10].replace("-", "") or now_bd().strftime("%Y%m%d")
                conn.execute("UPDATE withdrawals SET serial_no=? WHERE id=?", (f"WD-{day}-{int(r['id']):04d}", r["id"]))
            conn.commit()
        finally:
            conn.close()


# ============================================================
# MAIN MESSAGE ROUTER
# ============================================================

@bot.message_handler(
    content_types=["text"]
)
def message_router(message):

    user_id = message.from_user.id

    text = (
        message.text or ""
    ).strip()


    # Always register user.
    register_user(
        message.from_user
    )


    # Active state gets priority.
    if user_id in STATES:

        handle_state(
            message
        )

        return


    # Navigation.
    if text == "🏠 Main Menu":

        send_main_menu(
            message.chat.id,
            user_id,
            "🏠 <b>Main Menu</b>"
        )

        return


    if text == "🔙 Back":

        send_main_menu(
            message.chat.id,
            user_id,
            "🔙 Back to Main Menu"
        )

        return


    # Maintenance.
    if maintenance_blocked(
        user_id
    ):

        bot.send_message(
            message.chat.id,
            "🛠️ Bot maintenance mode-এ আছে.",
            reply_markup=main_keyboard(
                user_id
            )
        )

        return


    # ========================================================
    # USER MENU
    # ========================================================

    if text == "📊 Future Signals":

        future_signal_menu(
            message
        )

        return


    if text == "⚡ Live Signals":

        live_signal_menu(
            message
        )

        return


    if text == "🗳️ Vote":

        vote_menu(
            message
        )

        return


    if text == "📈 Signal Result":

        result_menu(
            message
        )

        return


    if text == "👤 My Status":

        status_menu(
            message
        )

        return


    if text == "🆔 Submit Quotex UID":

        start_uid_submission(
            message
        )

        return


    if text == "💰 Money Management":

        mm_menu(
            message
        )

        return


    if text == "💵 Wallet":

        wallet_menu(
            message
        )

        return


    if text == "💸 Request Withdraw":

        start_withdrawal_flow(message)
        return


    if text == "👥 Referral Link":

        referral_menu(
            message
        )

        return


    if text == "📜 Signal History":

        signal_history(
            message
        )

        return


    if text == "📖 VIP Rules":

        vip_rules(
            message
        )

        return


    if text == "📜 Trading Contract":

        trading_contract(
            message
        )

        return


    if text == "🔔 Notifications":

        notification_menu(
            message
        )

        return


    if text == "🔔 Toggle Notifications":

        toggle_notifications(
            message
        )

        return


    if text == "📸 AI Candle Analysis":
        return start_candle_analysis(message)

    if text == "❓ Help / FAQ":

        help_menu(
            message
        )

        return


    # ========================================================
    # MONEY MANAGEMENT BUTTONS
    # ========================================================

    mm_buttons = {

        "💵 Set Balance":
            "balance",

        "🎯 Set Profit Target":
            "profit_target",

        "🛑 Set Loss Limit":
            "loss_limit",

        "💲 Set Base Trade":
            "base_trade",

        "1️⃣ Set M1 Trade":
            "m1_trade",

        "🔢 Max Trades/Day":
            "max_trades"

    }


    if text in mm_buttons:

        STATES[user_id] = {

            "action": "mm",

            "key":
                mm_buttons[text]

        }


        bot.send_message(

            message.chat.id,

            f"Enter "
            f"<b>"
            f"{mm_buttons[text].replace('_',' ').title()}"
            f"</b>:",

            reply_markup=back_keyboard()
        )

        return


    if text == "📊 MM Status":

        mm_menu(
            message
        )

        return


    if text == "🛑 Stop MM Today":

        mm_set(
            user_id,
            "stop_mm",
            "ON"
        )


        bot.send_message(
            message.chat.id,
            "🛑 Money Management আজকের জন্য stopped.",
            reply_markup=mm_keyboard()
        )

        return


    # ========================================================
    # FINAL MM / AI BUTTONS
    # ========================================================

    if text == "⚙️ Setup MM":
        _state_set(user_id, {"action":"mm_setup"})
        bot.send_message(message.chat.id, "💵 Starting Balance কত? Example: 50", reply_markup=back_keyboard())
        return
    if text == "🔘 MM ON/OFF":
        with DB_LOCK:
            conn=db()
            try:
                u=conn.execute("SELECT mm_enabled FROM users WHERE user_id=?",(user_id,)).fetchone()
                new=0 if u and int(u["mm_enabled"] or 0) else 1
                conn.execute("UPDATE users SET mm_enabled=? WHERE user_id=?",(new,user_id)); conn.commit()
            finally: conn.close()
        bot.send_message(message.chat.id, f"💰 MM: <b>{'ON' if new else 'OFF'}</b>", reply_markup=mm_keyboard())
        return
    if text == "💵 Change Base":
        _state_set(user_id,{"action":"mm_set_base"}); bot.send_message(message.chat.id,"New Base amount দিন. Minimum $1",reply_markup=back_keyboard()); return
    if text == "💲 Change M1":
        _state_set(user_id,{"action":"mm_set_m1"}); bot.send_message(message.chat.id,"New M1 amount দিন.",reply_markup=back_keyboard()); return
    if text == "📈 Change Payout":
        _state_set(user_id,{"action":"mm_set_payout"}); bot.send_message(message.chat.id,"Payout percent দিন. Example: 85",reply_markup=back_keyboard()); return
    if text == "🛑 Stop MM Today":
        with DB_LOCK:
            conn=db()
            try: conn.execute("UPDATE users SET mm_stop=1 WHERE user_id=?",(user_id,)); conn.commit()
            finally: conn.close()
        bot.send_message(message.chat.id,"🛑 MM আজকের জন্য stopped.",reply_markup=mm_keyboard()); return
    if text == "🤖 AI Candle Analysis":
        return start_candle_analysis(message)

    # ========================================================
    # ADMIN
    # ========================================================

    if text == "👑 Admin Control":

        admin_panel(
            message
        )

        return


    if (
        is_master(user_id)
        or
        get_permissions(user_id)
    ):

        handle_admin_button(
            message
        )

        return


    # ========================================================
    # UNKNOWN
    # ========================================================

    bot.send_message(

        message.chat.id,

        "❌ এই option বুঝতে পারিনি.\n"
        "নিচের menu থেকে option নির্বাচন করুন.",

        reply_markup=main_keyboard(
            user_id
        )
    )



# ============================================================
# FINAL FEATURE PATCHES
# ============================================================
# This block extends the original bot without replacing its core signal/database
# logic. It adds secure referral tiers, withdrawal confirmation/reporting,
# per-signal inline WIN/LOSE/SKIP buttons, UID protection, button-based admin
# tools, and persistent migrations.

LEGACY_ADMIN_KEYBOARD = admin_keyboard
LEGACY_HANDLE_ADMIN_BUTTON = handle_admin_button
LEGACY_REFERRAL_MENU = referral_menu

REFERRAL_LEVEL_DEFAULTS = [
    (0, 100, 1.00),
    (5, 150, 1.25),
    (10, 200, 1.50),
    (20, 250, 2.00),
    (30, 300, 2.50),
]


def _ensure_column(conn, table, column, definition):
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_final_schema():
    with DB_LOCK:
        conn = db()
        try:
            _ensure_column(conn, "referrals", "status", "TEXT NOT NULL DEFAULT 'PENDING'")
            _ensure_column(conn, "referrals", "qualified_at", "TEXT")
            _ensure_column(conn, "referrals", "paid_at", "TEXT")
            _ensure_column(conn, "referrals", "risk_flag", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "referrals", "risk_note", "TEXT DEFAULT ''")
            _ensure_column(conn, "withdrawals", "hold_until", "TEXT")
            _ensure_column(conn, "withdrawals", "reviewed_by", "INTEGER")
            _ensure_column(conn, "withdrawals", "review_note", "TEXT DEFAULT ''")
            _ensure_column(conn, "notify_targets", "target_type", "TEXT NOT NULL DEFAULT 'GROUP'")
            _ensure_column(conn, "notify_targets", "audience", "TEXT NOT NULL DEFAULT 'ALL'")
            _ensure_column(conn, "notify_targets", "selected_users", "TEXT DEFAULT ''")
            _ensure_column(conn, "users", "mm_balance_cents", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "users", "mm_profit_target_cents", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "users", "mm_loss_limit_cents", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "users", "mm_base_cents", "INTEGER NOT NULL DEFAULT 100")
            _ensure_column(conn, "users", "mm_m1_cents", "INTEGER NOT NULL DEFAULT 200")
            _ensure_column(conn, "users", "mm_max_trades", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "users", "mm_stop", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "users", "mm_daily_profit_cents", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "users", "mm_daily_loss_cents", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "users", "mm_trade_count", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "users", "mm_date", "TEXT")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS referral_levels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    min_refs INTEGER NOT NULL UNIQUE,
                    bonus_cents INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS referral_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER NOT NULL,
                    referred_id INTEGER,
                    action TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    target_id TEXT,
                    note TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mm_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    signal_id INTEGER,
                    result TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    pnl_cents INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_signal_user_results (
                    live_signal_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(live_signal_id,user_id)
                );
            """)
            for min_refs, bonus in [(a,b) for a,b,_ in REFERRAL_LEVEL_DEFAULTS]:
                pass
            for min_refs, _, bonus in REFERRAL_LEVEL_DEFAULTS:
                conn.execute("INSERT OR IGNORE INTO referral_levels(min_refs,bonus_cents) VALUES(?,?)", (min_refs, int(round(bonus*100))))
            # Enforce one pending withdrawal per user. If an old DB contains duplicates,
            # keep the oldest pending request and refund later duplicates before indexing.
            dup_groups = conn.execute("""
                SELECT user_id, COUNT(*) n FROM withdrawals
                WHERE status='PENDING' GROUP BY user_id HAVING COUNT(*)>1
            """).fetchall()
            for g in dup_groups:
                rows = conn.execute("SELECT * FROM withdrawals WHERE user_id=? AND status='PENDING' ORDER BY id ASC", (g["user_id"],)).fetchall()
                for row in rows[1:]:
                    conn.execute("UPDATE withdrawals SET status='REJECTED', reviewed_at=?, review_note=? WHERE id=?", (utc_iso(now_utc()), "Migration: duplicate pending withdrawal", row["id"]))
                    conn.execute("UPDATE users SET wallet_cents=wallet_cents+? WHERE user_id=?", (row["amount_cents"], row["user_id"]))
                    conn.execute("INSERT INTO wallet_tx(user_id,amount_cents,kind,note,created_at) VALUES(?,?,?,?,?)", (row["user_id"], row["amount_cents"], "WITHDRAW_REFUND", "Duplicate pending withdrawal migration refund", utc_iso(now_utc())))
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_pending_withdrawal_per_user ON withdrawals(user_id) WHERE status='PENDING'")
            # UID uniqueness is already enforced by uid_submissions.quotex_uid UNIQUE.
            conn.commit()
        finally:
            conn.close()


def admin_audit(admin_id, action, target_id="", note=""):
    with DB_LOCK:
        conn=db()
        try:
            conn.execute("INSERT INTO admin_audit(admin_id,action,target_id,note,created_at) VALUES(?,?,?,?,?)", (admin_id, action, str(target_id), note, utc_iso(now_utc())))
            conn.commit()
        finally:
            conn.close()


def normalize_uid(uid):
    return re.sub(r"\s+", "", str(uid or "")).strip().upper()


def referral_bonus_for_next(referrer_id):
    with DB_LOCK:
        conn=db()
        try:
            count=conn.execute("SELECT COUNT(*) n FROM referrals WHERE referrer_id=? AND status='PAID'", (referrer_id,)).fetchone()["n"]
            row=conn.execute("SELECT bonus_cents FROM referral_levels WHERE enabled=1 AND min_refs<=? ORDER BY min_refs DESC LIMIT 1", (count,)).fetchone()
            return int(row["bonus_cents"] if row else 100)
        finally:
            conn.close()


def qualify_referral(referred_id):
    """Mark referral qualified after the referred user has genuinely received a signal.
    Bonus remains pending until the configured hold period has elapsed."""
    with DB_LOCK:
        conn=db()
        try:
            u=conn.execute("SELECT referred_by FROM users WHERE user_id=?", (referred_id,)).fetchone()
            if not u or not u["referred_by"] or int(u["referred_by"])==int(referred_id):
                return False
            exists=conn.execute("SELECT * FROM referrals WHERE referred_id=?", (referred_id,)).fetchone()
            if exists:
                return exists["status"] in ("QUALIFIED","PAID")
            referrer=int(u["referred_by"])
            if not conn.execute("SELECT 1 FROM users WHERE user_id=?", (referrer,)).fetchone():
                return False
            signal_count=conn.execute("SELECT COUNT(*) n FROM deliveries WHERE user_id=?", (referred_id,)).fetchone()["n"]
            need=max(1,int(get_setting("referral_min_signals","1")))
            if signal_count < need:
                return False
            risk=0
            notes=[]
            if referrer==referred_id:
                risk=1; notes.append("self referral")
            # A referrer cannot have the same referred account twice because referred_id is UNIQUE.
            bonus=referral_bonus_for_next(referrer)
            now=utc_iso(now_utc())
            conn.execute("INSERT INTO referrals(referrer_id,referred_id,bonus_cents,created_at,status,qualified_at,risk_flag,risk_note) VALUES(?,?,?,?,?,?,?,?)", (referrer,referred_id,bonus,now,"QUALIFIED",now,risk,"; ".join(notes)))
            conn.execute("UPDATE users SET referral_paid=1 WHERE user_id=?", (referred_id,))
            conn.execute("INSERT INTO referral_audit(referrer_id,referred_id,action,note,created_at) VALUES(?,?,?,?,?)", (referrer,referred_id,"QUALIFIED",f"Bonus pending: {money(bonus)}",now))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            conn.rollback(); return False
        finally:
            conn.close()


def release_referral_bonuses():
    hold_hours=max(0,int(get_setting("referral_hold_hours","24")))
    cutoff=now_utc()-timedelta(hours=hold_hours)
    with DB_LOCK:
        conn=db()
        try:
            rows=conn.execute("SELECT * FROM referrals WHERE status='QUALIFIED' AND qualified_at IS NOT NULL").fetchall()
            for r in rows:
                try: q=bd_from_iso(r["qualified_at"]).astimezone(UTC)
                except Exception: continue
                if q>cutoff: continue
                # Basic risk checks; flagged referrals stay pending for manual review.
                if int(r["risk_flag"] or 0):
                    continue
                conn.execute("UPDATE users SET wallet_cents=wallet_cents+?, refs_count=refs_count+1 WHERE user_id=?", (r["bonus_cents"],r["referrer_id"]))
                conn.execute("INSERT INTO wallet_tx(user_id,amount_cents,kind,note,created_at) VALUES(?,?,?,?,?)", (r["referrer_id"],r["bonus_cents"],"REFERRAL","Qualified referral bonus",utc_iso(now_utc())))
                conn.execute("UPDATE referrals SET status='PAID',paid_at=? WHERE id=?", (utc_iso(now_utc()),r["id"]))
                conn.execute("INSERT INTO referral_audit(referrer_id,referred_id,action,note,created_at) VALUES(?,?,?,?,?)", (r["referrer_id"],r["referred_id"],"PAID",money(r["bonus_cents"]),utc_iso(now_utc())))
            conn.commit()
        finally:
            conn.close()


def secure_process_referral_bonus(user_id):
    qualify_referral(user_id)
    release_referral_bonuses()


def __clean_referral_menu_v2(message):
    uid=message.from_user.id
    u=get_user(uid)
    with DB_LOCK:
        conn=db()
        try:
            pending=conn.execute("SELECT COALESCE(SUM(bonus_cents),0) n FROM referrals WHERE referrer_id=? AND status IN ('PENDING','QUALIFIED')",(uid,)).fetchone()["n"]
            paid=conn.execute("SELECT COALESCE(SUM(bonus_cents),0) n FROM referrals WHERE referrer_id=? AND status='PAID'",(uid,)).fetchone()["n"]
            next_level=conn.execute("SELECT min_refs,bonus_cents FROM referral_levels WHERE enabled=1 AND min_refs>? ORDER BY min_refs ASC LIMIT 1",(u["refs_count"] if u else 0,)).fetchone()
        finally: conn.close()
    try:
        me=bot.get_me(); link=f"https://t.me/{me.username}?start=ref_{uid}"
    except Exception: link=f"ref_{uid}"
    level_text=f"Next level: {next_level['min_refs']} paid referrals → {money(next_level['bonus_cents'])}/referral" if next_level else "Highest configured level reached."
    bot.send_message(message.chat.id, "👥 <b>REFERRAL CENTER</b>\n\n"+f"🔗 <code>{escape(link)}</code>\n\n"+f"✅ Paid referrals: <b>{u['refs_count'] if u else 0}</b>\n"+f"⏳ Pending bonus: <b>{money(pending)}</b>\n"+f"💰 Paid referral earnings: <b>{money(paid)}</b>\n\n{level_text}", reply_markup=main_keyboard(uid))


def format_signal_with_results(signal, user_id):
    base=format_signal(signal)
    with DB_LOCK:
        conn=db()
        try:
            voted=conn.execute("SELECT vote FROM votes WHERE signal_id=? AND user_id=?",(signal["id"],user_id)).fetchone()
            res=conn.execute("SELECT result,revealed FROM results WHERE signal_id=?",(signal["id"],)).fetchone()
        finally: conn.close()
    if voted:
        return base+f"\n\n🗳️ Your vote: <b>{escape(voted['vote'])}</b>"
    if res and int(res["revealed"] or 0):
        return base+f"\n\n📈 Result: <b>{escape(res['result'])}</b>"
    return base


def signal_result_markup(signal_id, user_id):
    with DB_LOCK:
        conn=db()
        try:
            done=conn.execute("SELECT 1 FROM results WHERE signal_id=? AND user_id=?",(signal_id,user_id)).fetchone()
        finally: conn.close()
    # Results are stored globally by signal, so the per-user one-time result is tracked in signal_user_results.
    return None


def ensure_signal_user_results():
    with DB_LOCK:
        conn=db()
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS signal_user_results(
                signal_id INTEGER NOT NULL, user_id INTEGER NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(signal_id,user_id), FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE)
            """)
            conn.commit()
        finally: conn.close()


def result_buttons(signal_id, user_id):
    with DB_LOCK:
        conn=db()
        try:
            row=conn.execute("SELECT result FROM signal_user_results WHERE signal_id=? AND user_id=?",(signal_id,user_id)).fetchone()
        finally: conn.close()
    if row:
        return None
    kb=types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("✅ WIN", callback_data=f"sigres:{signal_id}:WIN"), types.InlineKeyboardButton("❌ LOSE", callback_data=f"sigres:{signal_id}:LOSS"), types.InlineKeyboardButton("⏭️ SKIP", callback_data=f"sigres:{signal_id}:SKIP"))
    return kb


def patched_deliver_signal(user_id, signal_id, source="manual"):
    signal=get_signal(signal_id)
    if not signal: return False,"not_found"
    if not signal["active"]: return False,"inactive"
    if not audience_allows(signal,user_id): return False,"audience"
    if not quota_available(user_id): return False,"quota"
    with DB_LOCK:
        conn=db()
        try:
            if conn.execute("SELECT 1 FROM deliveries WHERE signal_id=? AND user_id=?",(signal_id,user_id)).fetchone(): return False,"already"
            conn.execute("INSERT INTO deliveries(signal_id,user_id,delivered_at,source) VALUES(?,?,?,?)",(signal_id,user_id,utc_iso(now_utc()),source))
            st=conn.execute("SELECT status FROM users WHERE user_id=?",(user_id,)).fetchone()
            if st and st["status"]!="VIP": conn.execute("UPDATE users SET free_used=free_used+1 WHERE user_id=?",(user_id,))
            conn.commit()
        finally: conn.close()
    try:
        bot.send_message(user_id, format_signal(signal), reply_markup=result_buttons(signal_id,user_id))
        secure_process_referral_bonus(user_id)
        return True,"sent"
    except Exception as exc:
        logger.warning("Signal send failed: %s",exc)
        with DB_LOCK:
            conn=db()
            try:
                conn.execute("DELETE FROM deliveries WHERE signal_id=? AND user_id=?",(signal_id,user_id))
                st=conn.execute("SELECT status FROM users WHERE user_id=?",(user_id,)).fetchone()
                if st and st["status"]!="VIP": conn.execute("UPDATE users SET free_used=MAX(0,free_used-1) WHERE user_id=?",(user_id,))
                conn.commit()
            finally: conn.close()
        return False,"send_error"


def patched_format_signal(signal):
    signal_datetime=bd_from_iso(signal["signal_at_utc"])
    up=signal["direction"]=="UP"
    icon="🟢⬆️" if up else "🔴⬇️"
    label="UP / BUY" if up else "DOWN / SELL / PUT"
    return ("━━━━━━━━━━━━━━━━━━\n🚨 <b>SM QUATEX SURE SHORT</b>\n━━━━━━━━━━━━━━━━━━\n\n"+f"📅 <b>{signal_datetime.strftime('%d %B %Y')}</b>\n"+f"💱 Pair: <b>{escape(signal['pair'])}</b>\n"+f"⏰ Time: <b>{signal_datetime.strftime('%I:%M %p')}</b>\n"+f"{icon} Direction: <b>{label}</b>\n"+f"🎯 Confidence: <b>{escape(signal['confidence'])}</b>\n\n━━━━━━━━━━━━━━━━━━")


def admin_result_stats(message):
    uid=message.from_user.id
    if not can(uid,"analytics"):
        return bot.send_message(message.chat.id,"⛔ Access denied.",reply_markup=admin_keyboard())
    with DB_LOCK:
        conn=db()
        try:
            rows=conn.execute("""SELECT s.id,s.signal_date,s.signal_time,s.pair,s.direction,COUNT(r.user_id) n,
                SUM(CASE WHEN r.result='WIN' THEN 1 ELSE 0 END) wins,
                SUM(CASE WHEN r.result='LOSS' THEN 1 ELSE 0 END) losses,
                SUM(CASE WHEN r.result='SKIP' THEN 1 ELSE 0 END) skips,
                COALESCE((SELECT result FROM results z WHERE z.signal_id=s.id LIMIT 1),'') official,
                COALESCE((SELECT revealed FROM results z WHERE z.signal_id=s.id LIMIT 1),0) revealed
                FROM signals s LEFT JOIN signal_user_results r ON r.signal_id=s.id
                GROUP BY s.id ORDER BY s.signal_at_utc DESC LIMIT 15""").fetchall()
        finally: conn.close()
    lines=["📈 <b>RESULT STATISTICS</b>","Send a signal ID to inspect/reveal its result.",""]
    for r in rows:
        lines.append(f"#{r['id']} {escape(r['pair'])} {r['signal_time']} {r['direction']} — WIN {r['wins'] or 0} / LOSS {r['losses'] or 0} / SKIP {r['skips'] or 0} | Official: {r['official'] or '—'}")
    STATES[uid]={"action":"result_admin_select"}
    bot.send_message(message.chat.id,"\n".join(lines),reply_markup=back_keyboard())


def _result_admin_buttons():
    return make_keyboard([["📢 Reveal WIN","📢 Reveal LOSS"],["📢 Reveal SKIP","🔒 Hide Result"],["🔙 Back","🏠 Main Menu"]])

def admin_withdrawal_report(message):
    uid=message.from_user.id
    if not can(uid,"withdraw"): return bot.send_message(message.chat.id,"⛔ Access denied.",reply_markup=admin_keyboard())
    rows=[("Today",0), ("Yesterday",1), ("Last 3 Days",3), ("Last 7 Days",7), ("Last 10 Days",10), ("Last 30 Days",30)]
    out=["📊 <b>WITHDRAWAL REPORTS</b>"]
    now=now_bd()
    with DB_LOCK:
        conn=db()
        try:
            for label,days in rows:
                if label=="Today": start=now.replace(hour=0,minute=0,second=0,microsecond=0); end=now
                elif label=="Yesterday":
                    d=(now-timedelta(days=1)).date(); start=datetime(d.year,d.month,d.day,tzinfo=BD_TZ); end=start+timedelta(days=1)
                else: start=(now-timedelta(days=days)).replace(hour=0,minute=0,second=0,microsecond=0); end=now
                a=utc_iso(start.astimezone(UTC)); b=utc_iso(end.astimezone(UTC))
                r=conn.execute("SELECT COUNT(*) n,COALESCE(SUM(amount_cents),0) total FROM withdrawals WHERE created_at>=? AND created_at<?",(a,b)).fetchone()
                paid=conn.execute("SELECT COUNT(*) n,COALESCE(SUM(amount_cents),0) total FROM withdrawals WHERE created_at>=? AND created_at<? AND status='APPROVED'",(a,b)).fetchone()
                out.append(f"\n<b>{label}</b>: {r['n']} requests / {money(r['total'])}\n   ✅ Paid: {paid['n']} / {money(paid['total'])}")
            pending=conn.execute("SELECT COUNT(*) n,COALESCE(SUM(amount_cents),0) total FROM withdrawals WHERE status='PENDING'").fetchone()
        finally: conn.close()
    out.append(f"\n⏳ Pending now: {pending['n']} / {money(pending['total'])}")
    bot.send_message(message.chat.id,"\n".join(out),reply_markup=make_keyboard([["💸 Withdrawals","📊 Withdrawal Reports"],["🔙 Back","🏠 Main Menu"]]))


def admin_referral_history(message):
    uid=message.from_user.id
    if not can(uid,"users"): return bot.send_message(message.chat.id,"⛔ Access denied.",reply_markup=admin_keyboard())
    with DB_LOCK:
        conn=db()
        try:
            total=conn.execute("SELECT COUNT(*) n FROM referrals").fetchone()["n"]
            pending=conn.execute("SELECT COUNT(*) n,COALESCE(SUM(bonus_cents),0) total FROM referrals WHERE status!='PAID'").fetchone()
            paid=conn.execute("SELECT COUNT(*) n,COALESCE(SUM(bonus_cents),0) total FROM referrals WHERE status='PAID'").fetchone()
            top=conn.execute("SELECT referrer_id,COUNT(*) n,COALESCE(SUM(bonus_cents),0) total FROM referrals WHERE status='PAID' GROUP BY referrer_id ORDER BY n DESC LIMIT 10").fetchall()
        finally: conn.close()
    lines=["👥 <b>REFERRAL HISTORY</b>",f"Total referrals: <b>{total}</b>",f"⏳ Pending/Qualified: <b>{pending['n']}</b> / {money(pending['total'])}",f"✅ Paid: <b>{paid['n']}</b> / {money(paid['total'])}","","🏆 <b>Top Referrers</b>"]
    for r in top: lines.append(f"<code>{r['referrer_id']}</code> — {r['n']} refs — {money(r['total'])}")
    bot.send_message(message.chat.id,"\n".join(lines),reply_markup=make_keyboard([["📊 Withdrawal Reports"],["🔙 Back","🏠 Main Menu"]]))


def __clean_admin_subadmins_v2(message):
    uid=message.from_user.id
    if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
    bot.send_message(message.chat.id,"🛡️ <b>SUB-ADMIN MANAGEMENT</b>",reply_markup=make_keyboard([["➕ Add Sub Admin","📋 Sub Admin List"],["🗑️ Remove Sub Admin"],["🔙 Back","🏠 Main Menu"]]))


def __clean_admin_notify_targets_v2(message):
    uid=message.from_user.id
    if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
    bot.send_message(message.chat.id,"🎯 <b>NOTIFICATION TARGETS</b>",reply_markup=make_keyboard([["➕ Add Target","📋 Target List"],["🗑️ Remove Target","🧪 Test Target"],["🔔 Auto Notification ON/OFF"],["🔙 Back","🏠 Main Menu"]]))


def __clean_admin_text_editor_v2(message):
    uid=message.from_user.id
    if not can(uid,"settings"): return bot.send_message(message.chat.id,"⛔ Access denied.",reply_markup=admin_keyboard())
    cats=[("👋 Welcome","welcome"),("📊 Future Signal","future_signal_text"),("⚡ Live Signal","live_signal_text"),("💰 Money Management","mm_text"),("⭐ VIP","vip_text"),("🆔 UID","uid_text"),("💵 Wallet","wallet_text"),("💸 Withdraw","withdraw_text"),("👥 Referral","referral_text"),("📣 Notification","notification_text"),("📜 Trading Contract","trading_rules"),("📢 Broadcast","broadcast_text"),("✅ WIN","win_text"),("❌ LOSS","loss_text"),("⏭️ SKIP","skip_text"),("🛠️ Errors","error_text"),("🔔 Reminders","reminder_text"),("✨ Feature Text","feature_text")]
    rows=[]
    for i in range(0,len(cats),2): rows.append([cats[i][0],cats[i+1][0] if i+1<len(cats) else "🔙 Back"])
    STATES[uid]={"action":"text_pick","map":dict(cats)}
    bot.send_message(message.chat.id,"📝 <b>BOT TEXT EDITOR</b>\nChoose a section:",reply_markup=make_keyboard(rows+[["🔙 Back","🏠 Main Menu"]]))


def __clean_admin_keyboard_v2():
    # Keep legacy controls reachable while exposing the new admin tools.
    return make_keyboard([
        ["➕ Add Future Signals", "📋 Future Signal List"],
        ["✏️ Edit Signal", "🗑️ Delete Signal"],
        ["🧹 Clear Future Signals", "📤 Auto Send ON/OFF"],
        ["🎯 Signal Audience", "⚡ Live Session"],
        ["🆔 Pending UID", "⭐ Manage VIP"],
        ["💸 Withdrawals", "📊 Withdrawal Reports"],
        ["👥 Referral History", "💳 Wallet Adjust"],
        ["📢 Broadcast", "👥 Users"],
        ["🛡️ Sub-admins", "🎯 Notify Targets"],
        ["📊 Analytics", "📈 Result Stats"],
        ["⚙️ Settings", "📝 Bot Text Editor"],
        ["🛠️ Maintenance ON/OFF", "⚡ Live ON/OFF"],
        ["🎟️ Set Free Limit", "💵 Set Min Withdraw"],
        ["💸 Withdraw ON/OFF", "🔔 Auto Notification ON/OFF"],
        ["🤖 Candle AI ON/OFF", "🖼️ Max Candle Pics"],
        ["🔙 Back", "🏠 Main Menu"],
    ])


def __clean_handle_admin_button_v2(message):
    text=message.text; uid=message.from_user.id
    permission_map={
        "➕ Add Future Signals":"signals","📋 Future Signal List":"signals","✏️ Edit Signal":"signals","🗑️ Delete Signal":"signals","🧹 Clear Future Signals":"signals","📤 Auto Send ON/OFF":"signals","🎯 Signal Audience":"signals",
        "⚡ Live Session":"live","🆔 Pending UID":"uid","⭐ Manage VIP":"vip","💸 Withdrawals":"withdraw","📊 Withdrawal Reports":"withdraw","👥 Referral History":"analytics","💳 Wallet Adjust":"wallet","📢 Broadcast":"broadcast","👥 Users":"users","📊 Analytics":"analytics","📈 Result Stats":"analytics","🎯 Notify Targets":"notifications","⚙️ Settings":"settings","📝 Bot Text Editor":"text","🎟️ Set Free Limit":"settings","💵 Set Min Withdraw":"settings","💸 Withdraw ON/OFF":"settings","🛠️ Maintenance ON/OFF":"settings","⚡ Live ON/OFF":"settings","🔔 Auto Notification ON/OFF":"notifications","🤖 Candle AI ON/OFF":"settings","🖼️ Max Candle Pics":"settings"
    }
    if text in permission_map and not can(uid, permission_map[text]):
        return bot.send_message(message.chat.id,"⛔ You do not have permission for this section.",reply_markup=admin_keyboard())
    if text=="📈 Result Stats":
        return admin_result_stats(message)
    if text=="📊 Withdrawal Reports": return admin_withdrawal_report(message)
    if text=="👥 Referral History": return admin_referral_history(message)
    if text=="➕ Add Sub Admin":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        STATES[uid]={"action":"subadmin_add_id"}; return bot.send_message(message.chat.id,"🆔 Telegram ID অথবা previously seen @username লিখুন:",reply_markup=back_keyboard())
    if text=="📋 Sub Admin List":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        with DB_LOCK:
            conn=db()
            try: rows=conn.execute("SELECT * FROM admins ORDER BY user_id").fetchall()
            finally: conn.close()
        msg="🛡️ <b>SUB-ADMINS</b>\n\n"+"\n".join(f"<code>{r['user_id']}</code>: {escape(r['permissions'])}" for r in rows) if rows else "🛡️ No sub-admins."
        return bot.send_message(message.chat.id,msg,reply_markup=make_keyboard([["➕ Add Sub Admin","🗑️ Remove Sub Admin"],["🔙 Back","🏠 Main Menu"]]))
    if text=="🗑️ Remove Sub Admin":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        STATES[uid]={"action":"subadmin_remove"}; return bot.send_message(message.chat.id,"🆔 Sub-admin Telegram ID লিখুন:",reply_markup=back_keyboard())
    if text=="➕ Add Target":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        STATES[uid]={"action":"notify_type"}; return bot.send_message(message.chat.id,"Target type বেছে নিন:",reply_markup=make_keyboard([["👥 Group","📢 Channel"],["🔙 Back","🏠 Main Menu"]]))
    if text=="📋 Target List":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        with DB_LOCK:
            conn=db()
            try: rows=conn.execute("SELECT * FROM notify_targets ORDER BY chat_id").fetchall()
            finally: conn.close()
        msg="🎯 <b>TARGETS</b>\n\n"+"\n".join(f"{r['chat_id']} | {r['target_type']} | {r['audience']} | {'ON' if r['enabled'] else 'OFF'}" for r in rows) if rows else "No targets."
        return bot.send_message(message.chat.id,msg,reply_markup=make_keyboard([["➕ Add Target","🗑️ Remove Target"],["🧪 Test Target","🔙 Back"]]))
    if text=="🗑️ Remove Target":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        STATES[uid]={"action":"notify_remove"}; return bot.send_message(message.chat.id,"Target ID লিখুন:",reply_markup=back_keyboard())
    if text=="🧪 Test Target":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        STATES[uid]={"action":"notify_test"}; return bot.send_message(message.chat.id,"Target ID লিখুন:",reply_markup=back_keyboard())
    if text=="🔔 Auto Notification ON/OFF":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        old=get_setting("auto_notification","ON"); new="OFF" if old=="ON" else "ON"; set_setting("auto_notification",new); return bot.send_message(message.chat.id,f"🔔 Auto Notification: <b>{new}</b>",reply_markup=admin_keyboard())
    if text=="🤖 Candle AI ON/OFF":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        old=get_setting("candle_ai_enabled","OFF")
        new="OFF" if old=="ON" else "ON"
        set_setting("candle_ai_enabled",new)
        return bot.send_message(message.chat.id,f"🤖 Candle AI: <b>{new}</b>",reply_markup=admin_keyboard())
    if text=="🖼️ Max Candle Pics":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        STATES[uid]={"action":"set_candle_max_images"}
        return bot.send_message(message.chat.id,"🖼️ এক user সর্বোচ্চ কতটি screenshot upload করতে পারবে?\n১–১০ এর মধ্যে সংখ্যা দিন.",reply_markup=back_keyboard())
    # Preserve all legacy admin buttons and handlers.
    return LEGACY_HANDLE_ADMIN_BUTTON(message)


def _subadmin_permission_keyboard(selected):
    labels=[("📊 Signals","signals"),("🆔 UID Management","uid"),("👥 Users","users"),("💳 Wallet","wallet"),("💸 Withdraw","withdraw"),("📢 Broadcast","broadcast"),("📊 Analytics","analytics"),("⭐ VIP Management","vip"),("🎯 Notifications","notifications"),("⚙️ Settings","settings"),("📝 Text Editor","text"),("⚡ Live Signals","live"),("💰 Money Management","mm")]
    rows=[]
    for i in range(0,len(labels),2): rows.append([("☑️ " if labels[i][1] in selected else "⬜ ")+labels[i][0], ("☑️ " if labels[i+1][1] in selected else "⬜ ")+labels[i+1][0] if i+1<len(labels) else "🔙 Back"])
    rows.append(["✅ Save Sub Admin","❌ Cancel"])
    return make_keyboard(rows)


def __clean_handle_state_v2(message):
    uid=message.from_user.id; text=(message.text or "").strip(); st=STATES.get(uid)
    if not st: return LEGACY_HANDLE_STATE(message)
    if text in ("🔙 Back","🏠 Main Menu","❌ Cancel"):
        clear_state(uid); send_main_menu(message.chat.id,uid,"🏠 <b>Main Menu</b>"); return True
    try:
        action=st.get("action")
        if action=="set_candle_max_images":
            if not is_master(uid):
                raise ValueError("Master Admin only.")
            value=int(text)
            if value < 1 or value > 10:
                raise ValueError("১ থেকে ১০ এর মধ্যে সংখ্যা দিন.")
            set_setting("candle_ai_max_images",str(value))
            clear_state(uid)
            return bot.send_message(message.chat.id,f"🖼️ Max Candle Pics: <b>{value}</b>",reply_markup=admin_keyboard())
        if action=="candle_upload":
            analyze_candle_state(message)
            return True
        if action=="withdraw_method":
            methods={"🟢 BKASH":"BKASH","🔵 NAGAD":"NAGAD","🟠 BINANCE":"BINANCE"}
            if text not in methods: raise ValueError("Payment method button ব্যবহার করুন.")
            st.update({"method":methods[text],"stage":"account","action":"withdraw_account"})
            bot.send_message(message.chat.id,"📱 Account Number / Binance ID লিখুন:",reply_markup=back_keyboard()); return True
        if action=="withdraw_account":
            account=text
            if len(account)<4 or len(account)>120: raise ValueError("Account/ID ঠিকভাবে দিন.")
            st.update({"account":account,"stage":"amount","action":"withdraw_amount"})
            bot.send_message(message.chat.id,f"💰 কত withdraw করতে চান?\nMinimum: <b>${get_setting('min_withdraw','5.00')}</b>",reply_markup=back_keyboard()); return True
        if action=="withdraw_amount":
            amount_cents=int(round(float(text.replace("$",""))*100))
            minimum=int(round(float(get_setting("min_withdraw","5.00"))*100))
            if amount_cents<minimum: raise ValueError(f"Minimum withdrawal: {money(minimum)}")
            with DB_LOCK:
                conn=db()
                try:
                    u=conn.execute("SELECT wallet_cents FROM users WHERE user_id=?",(uid,)).fetchone()
                    if not u or u["wallet_cents"]<amount_cents: raise ValueError("Wallet balance যথেষ্ট নয়.")
                    if conn.execute("SELECT 1 FROM withdrawals WHERE user_id=? AND status='PENDING'",(uid,)).fetchone(): raise ValueError("আপনার একটি withdrawal already pending আছে.")
                finally: conn.close()
            st.update({"amount_cents":amount_cents,"stage":"confirm","action":"withdraw_confirm"})
            bot.send_message(message.chat.id,f"🧾 <b>WITHDRAW CONFIRMATION</b>\n\nMethod: <b>{st['method']}</b>\nAccount: <code>{escape(st['account'])}</code>\nAmount: <b>{money(amount_cents)}</b>\n\nConfirm করবেন?",reply_markup=make_keyboard([["✅ Confirm Withdraw","❌ Cancel Withdraw"],["🔙 Back","🏠 Main Menu"]])); return True
        if action=="withdraw_confirm":
            if text!="✅ Confirm Withdraw": raise ValueError("Confirm button ব্যবহার করুন.")
            a=int(st["amount_cents"]); method=st["method"]; account=st["account"]
            hold_hours=max(0,int(get_setting("withdraw_hold_hours","0"))); hold=(now_utc()+timedelta(hours=hold_hours)).isoformat()
            with DB_LOCK:
                conn=db()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    u=conn.execute("SELECT wallet_cents FROM users WHERE user_id=?",(uid,)).fetchone()
                    if not u or u["wallet_cents"]<a: raise ValueError("Wallet balance changed; request cancelled.")
                    if conn.execute("SELECT 1 FROM withdrawals WHERE user_id=? AND status='PENDING'",(uid,)).fetchone(): raise ValueError("Pending withdrawal exists.")
                    conn.execute("UPDATE users SET wallet_cents=wallet_cents-? WHERE user_id=? AND wallet_cents>=?",(a,uid,a))
                    serial=_next_wd_serial(conn); conn.execute("INSERT INTO withdrawals(user_id,amount_cents,method,account,status,created_at,hold_until,serial_no) VALUES(?,?,?,?,?,?,?,?)",(uid,a,method,account,"PENDING",utc_iso(now_utc()),hold,serial))
                    conn.execute("INSERT INTO wallet_tx(user_id,amount_cents,kind,note,created_at) VALUES(?,?,?,?,?)",(uid,-a,"WITHDRAW_HOLD","Withdrawal request",utc_iso(now_utc())))
                    conn.commit()
                except Exception:
                    conn.rollback(); raise
                finally: conn.close()
            admin_audit(uid,"WITHDRAW_REQUEST",uid,f"{method} {money(a)}")
            clear_state(uid)
            bot.send_message(message.chat.id,_wd_user_submitted_text(serial,a,method,account),reply_markup=main_keyboard(uid));
            try: bot.send_message(ADMIN_ID,_wd_admin_text(serial,uid,method,account,a),reply_markup=_wd_inline_kb(serial))
            except Exception: logger.exception("Withdrawal admin notify failed")
            return True
        if action=="text_pick":
            key=st["map"].get(text)
            if not key: return LEGACY_HANDLE_STATE(message)
            st.update({"key":key,"action":"text_edit"})
            bot.send_message(message.chat.id,f"Current text:\n\n<code>{escape(get_setting(key,''))}</code>\n\n✏️ নতুন text লিখুন:",reply_markup=back_keyboard()); return True
        if action=="text_edit":
            st["new_text"]=text; st["action"]="text_confirm"
            bot.send_message(message.chat.id,"👁 Preview:\n\n"+text,reply_markup=make_keyboard([["💾 Save Text","❌ Cancel"],["🔙 Back","🏠 Main Menu"]])); return True
        if action=="text_confirm":
            if text=="💾 Save Text":
                set_setting(st["key"],st["new_text"]); clear_state(uid); bot.send_message(message.chat.id,"✅ Text saved.",reply_markup=admin_keyboard()); return True
            raise ValueError("Save অথবা Cancel ব্যবহার করুন.")
        if action=="subadmin_add_id":
            target=text.strip(); target_id=None
            if target.isdigit(): target_id=int(target)
            else:
                target=target.lstrip("@").lower()
                with DB_LOCK:
                    conn=db()
                    try:
                        r=conn.execute("SELECT user_id FROM users WHERE lower(username)=?",(target,)).fetchone()
                    finally: conn.close()
                if r: target_id=int(r["user_id"])
            if not target_id: raise ValueError("ID পাওয়া যায়নি. User-কে আগে bot-এ /start করতে হবে.")
            st["target_id"]=target_id; st["selected"]=set(); st["action"]="subadmin_perms"
            bot.send_message(message.chat.id,"Permissions select করুন:",reply_markup=_subadmin_permission_keyboard(set())); return True
        if action=="subadmin_perms":
            labels={"📊 Signals":"signals","🆔 UID Management":"uid","👥 Users":"users","💳 Wallet":"wallet","💸 Withdraw":"withdraw","📢 Broadcast":"broadcast","📊 Analytics":"analytics","⭐ VIP Management":"vip","🎯 Notifications":"notifications","⚙️ Settings":"settings","📝 Text Editor":"text","⚡ Live Signals":"live","💰 Money Management":"mm"}
            clean=text.replace("☑️ ","").replace("⬜ ","")
            if clean in labels:
                p=labels[clean]
                if p in st["selected"]: st["selected"].remove(p)
                else: st["selected"].add(p)
                bot.send_message(message.chat.id,"Permissions:",reply_markup=_subadmin_permission_keyboard(st["selected"])); return True
            if text=="❌ Cancel": clear_state(uid); return bot.send_message(message.chat.id,"Cancelled.",reply_markup=admin_keyboard())
            if text=="✅ Save Sub Admin":
                perms=sorted(st["selected"])
                with DB_LOCK:
                    conn=db()
                    try:
                        conn.execute("INSERT INTO admins(user_id,permissions) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET permissions=excluded.permissions",(st["target_id"],",".join(perms))); conn.commit()
                    finally: conn.close()
                admin_audit(uid,"SUBADMIN_SAVE",st["target_id"],",".join(perms)); clear_state(uid); return bot.send_message(message.chat.id,"✅ Sub-admin saved.",reply_markup=admin_keyboard())
        if action=="subadmin_remove":
            if not text.isdigit(): raise ValueError("Telegram ID দিন.")
            with DB_LOCK:
                conn=db()
                try: conn.execute("DELETE FROM admins WHERE user_id=?",(int(text),)); conn.commit()
                finally: conn.close()
            admin_audit(uid,"SUBADMIN_REMOVE",text); clear_state(uid); bot.send_message(message.chat.id,"✅ Sub-admin removed.",reply_markup=admin_keyboard()); return True
        if action=="notify_type":
            if text not in ("👥 Group","📢 Channel"): raise ValueError("Type button ব্যবহার করুন.")
            st["target_type"]="GROUP" if text=="👥 Group" else "CHANNEL"; st["action"]="notify_id"
            bot.send_message(message.chat.id,"Group/Channel ID বা @username লিখুন:",reply_markup=back_keyboard()); return True
        if action=="notify_id":
            st["chat_id"]=text; st["action"]="notify_audience"
            bot.send_message(message.chat.id,"Audience:",reply_markup=make_keyboard([["🌐 All Users","⭐ VIP Only"],["🎯 Selected Users"],["🔙 Back","🏠 Main Menu"]])); return True
        if action=="notify_audience":
            aud={"🌐 All Users":"ALL","⭐ VIP Only":"VIP","🎯 Selected Users":"SELECTED"}.get(text)
            if not aud: raise ValueError("Audience button ব্যবহার করুন.")
            if aud=="SELECTED": st["action"]="notify_selected"; bot.send_message(message.chat.id,"Selected Telegram IDs comma-separated দিন:",reply_markup=back_keyboard()); return True
            st["audience"]=aud; st["selected_users"]=""; st["action"]="notify_save"; bot.send_message(message.chat.id,"Target save করবেন?",reply_markup=make_keyboard([["💾 Save Target","❌ Cancel"],["🔙 Back","🏠 Main Menu"]])); return True
        if action=="notify_selected":
            ids=[x.strip() for x in text.split(",") if x.strip().isdigit()]
            if not ids: raise ValueError("Valid IDs দিন.")
            st["audience"]="SELECTED"; st["selected_users"]=",".join(ids); st["action"]="notify_save"; bot.send_message(message.chat.id,"Target save করবেন?",reply_markup=make_keyboard([["💾 Save Target","❌ Cancel"],["🔙 Back","🏠 Main Menu"]])); return True
        if action=="notify_save":
            if text!="💾 Save Target": raise ValueError("Save button ব্যবহার করুন.")
            with DB_LOCK:
                conn=db()
                try: conn.execute("INSERT OR REPLACE INTO notify_targets(chat_id,title,enabled,target_type,audience,selected_users) VALUES(?,?,1,?,?,?)",(st["chat_id"],st["chat_id"],st["target_type"],st["audience"],st.get("selected_users",""))); conn.commit()
                finally: conn.close()
            admin_audit(uid,"NOTIFY_TARGET_ADD",st["chat_id"],st["audience"]); clear_state(uid); bot.send_message(message.chat.id,"✅ Target saved.",reply_markup=admin_keyboard()); return True
        if action=="notify_remove":
            with DB_LOCK:
                conn=db()
                try: conn.execute("DELETE FROM notify_targets WHERE chat_id=?",(text,)); conn.commit()
                finally: conn.close()
            clear_state(uid); bot.send_message(message.chat.id,"✅ Target removed.",reply_markup=admin_keyboard()); return True
        if action=="notify_test":
            with DB_LOCK:
                conn=db()
                try: r=conn.execute("SELECT chat_id FROM notify_targets WHERE chat_id=?",(text,)).fetchone()
                finally: conn.close()
            target=r["chat_id"] if r else text
            bot.send_message(target,"✅ SM QUATEX SURE SHORT notification test.")
            clear_state(uid); bot.send_message(message.chat.id,"✅ Test sent.",reply_markup=admin_keyboard()); return True
        if action=="withdraw_admin_select":
            _m=re.match(r"^💸\s*WD\s*#(\d+)$",text)
            if not _m: raise ValueError("Withdrawal button ব্যবহার করুন.")
            wid=int(_m.group(1))
            with DB_LOCK:
                conn=db()
                try:
                    r=conn.execute("SELECT w.*,u.username,u.first_name,(SELECT COUNT(*) FROM referrals r WHERE r.referrer_id=w.user_id AND r.risk_flag=1) risk_count,(SELECT COALESCE(SUM(bonus_cents),0) FROM referrals r WHERE r.referrer_id=w.user_id AND r.status!='PAID') pending_bonus FROM withdrawals w LEFT JOIN users u ON u.user_id=w.user_id WHERE w.id=? AND w.status='PENDING'",(wid,)).fetchone()
                finally: conn.close()
            if not r: raise ValueError("Pending withdrawal not found.")
            st["withdraw_id"]=wid; st["action"]="withdraw_admin_action"
            hold=r["hold_until"] or ""
            risk="⚠️ Referral risk flagged — review history." if int(r["risk_count"] or 0)>0 else "🟢 No stored referral risk flags."
            bot.send_message(message.chat.id,f"💸 <b>WITHDRAWAL #{wid}</b>\n\nUser: <code>{r['user_id']}</code>\nUsername: @{escape(r['username'] or '—')}\nMethod: <b>{r['method']}</b>\nAccount: <code>{escape(r['account'])}</code>\nAmount: <b>{money(r['amount_cents'])}</b>\nHold until: <code>{escape(hold or 'NOW')}</code>\nPending referral bonus: <b>{money(r['pending_bonus'] or 0)}</b>\n{risk}",reply_markup=withdraw_admin_action_keyboard()); return True
        if action=="withdraw_admin_action":
            wid=int(st["withdraw_id"])
            if text not in ("✅ Approve Withdrawal","❌ Reject Withdrawal"): raise ValueError("Approve/Reject button ব্যবহার করুন.")
            with DB_LOCK:
                conn=db()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    r=conn.execute("SELECT * FROM withdrawals WHERE id=? AND status='PENDING'",(wid,)).fetchone()
                    if not r: raise ValueError("Withdrawal already processed.")
                    if text=="✅ Approve Withdrawal":
                        hold_until = r["hold_until"]
                        if hold_until:
                            try:
                                hold_dt = datetime.fromisoformat(hold_until)
                                if hold_dt.tzinfo is None:
                                    hold_dt = hold_dt.replace(tzinfo=UTC)
                                if hold_dt.astimezone(UTC) > now_utc():
                                    raise ValueError("Withdrawal hold এখনও শেষ হয়নি.")
                            except ValueError:
                                # Includes both an active hold and malformed stored data.
                                raise
                            except Exception as exc:
                                raise ValueError("Withdrawal hold data invalid; manual review required.") from exc
                        updated = conn.execute("UPDATE withdrawals SET status='APPROVED',reviewed_at=?,reviewed_by=? WHERE id=? AND status='PENDING'",(utc_iso(now_utc()),uid,wid))
                        if updated.rowcount != 1:
                            raise ValueError("Withdrawal already processed.")
                        note="Withdrawal approved"
                    else:
                        conn.execute("UPDATE withdrawals SET status='REJECTED',reviewed_at=?,reviewed_by=?,review_note=? WHERE id=? AND status='PENDING'",(utc_iso(now_utc()),uid,"Rejected by admin",wid))
                        conn.execute("UPDATE users SET wallet_cents=wallet_cents+? WHERE user_id=?",(r["amount_cents"],r["user_id"]))
                        conn.execute("INSERT INTO wallet_tx(user_id,amount_cents,kind,note,created_at) VALUES(?,?,?,?,?)",(r["user_id"],r["amount_cents"],"WITHDRAW_REFUND","Withdrawal rejected/refunded",utc_iso(now_utc())))
                        note="Withdrawal rejected and refunded"
                    conn.commit()
                except Exception:
                    conn.rollback(); raise
                finally: conn.close()
            admin_audit(uid,"WITHDRAW_DECISION",wid,note); clear_state(uid)
            try: bot.send_message(r["user_id"],"✅ Withdrawal approved." if text.startswith("✅") else "❌ Withdrawal rejected; amount refunded.",reply_markup=main_keyboard(r["user_id"]))
            except Exception: pass
            bot.send_message(message.chat.id,"✅ Withdrawal updated.",reply_markup=admin_keyboard()); return True
        if action=="result_admin_select":
            if not text.isdigit(): raise ValueError("Signal ID দিন.")
            sid=int(text)
            with DB_LOCK:
                conn=db()
                try: r=conn.execute("SELECT s.*,COUNT(ur.user_id) n,SUM(CASE WHEN ur.result='WIN' THEN 1 ELSE 0 END) wins,SUM(CASE WHEN ur.result='LOSS' THEN 1 ELSE 0 END) losses,SUM(CASE WHEN ur.result='SKIP' THEN 1 ELSE 0 END) skips FROM signals s LEFT JOIN signal_user_results ur ON ur.signal_id=s.id WHERE s.id=? GROUP BY s.id",(sid,)).fetchone()
                finally: conn.close()
            if not r: raise ValueError("Signal not found.")
            st["signal_id"]=sid; st["action"]="result_admin_action"
            bot.send_message(message.chat.id,f"📈 <b>#{sid} {escape(r['pair'])}</b>\nWIN: {r['wins'] or 0}\nLOSS: {r['losses'] or 0}\nSKIP: {r['skips'] or 0}\n\nChoose official result:",reply_markup=_result_admin_buttons()); return True
        if action=="result_admin_action":
            sid=int(st["signal_id"])
            mapping={"📢 Reveal WIN":("WIN",1),"📢 Reveal LOSS":("LOSS",1),"📢 Reveal SKIP":("SKIP",1),"🔒 Hide Result":(None,0)}
            if text not in mapping: raise ValueError("Result button ব্যবহার করুন.")
            result,reveal=mapping[text]
            with DB_LOCK:
                conn=db()
                try:
                    if result is None:
                        conn.execute("UPDATE results SET revealed=0 WHERE signal_id=?",(sid,))
                    else:
                        conn.execute("INSERT INTO results(signal_id,result,revealed,created_at) VALUES(?,?,?,?) ON CONFLICT(signal_id) DO UPDATE SET result=excluded.result,revealed=excluded.revealed,created_at=excluded.created_at",(sid,result,reveal,utc_iso(now_utc())))
                    conn.commit()
                finally: conn.close()
            admin_audit(uid,"RESULT_REVEAL",sid,result or "HIDDEN")
            clear_state(uid); bot.send_message(message.chat.id,"✅ Result setting updated.",reply_markup=admin_keyboard()); return True
        # New withdrawal entry starts at method selection. Legacy amount-first state is intercepted below.
        if action=="withdraw":
            raise ValueError("Use the withdrawal buttons.")
        return LEGACY_HANDLE_STATE(message)
    except Exception as exc:
        bot.send_message(message.chat.id,"❌ <b>Error</b>\n\n"+escape(str(exc)),reply_markup=back_keyboard()); return True


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("uidapprove:"))
def uid_approve_callback(call):
    admin_id = call.from_user.id
    if not can(admin_id, "vip"):
        bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
        return
    try:
        sid = int(call.data.split(":", 1)[1])
        with DB_LOCK:
            conn = db()
            try:
                row = conn.execute("SELECT * FROM uid_submissions WHERE id=? AND status='PENDING'", (sid,)).fetchone()
            finally:
                conn.close()
        if not row:
            bot.answer_callback_query(call.id, "Pending UID পাওয়া যায়নি.", show_alert=True)
            return
        kb = types.InlineKeyboardMarkup()
        kb.row(types.InlineKeyboardButton("7 Days", callback_data=f"uidduration:{sid}:7"), types.InlineKeyboardButton("15 Days", callback_data=f"uidduration:{sid}:15"))
        kb.row(types.InlineKeyboardButton("30 Days", callback_data=f"uidduration:{sid}:30"), types.InlineKeyboardButton("60 Days", callback_data=f"uidduration:{sid}:60"))
        kb.row(types.InlineKeyboardButton("90 Days", callback_data=f"uidduration:{sid}:90"), types.InlineKeyboardButton("♾️ Unlimited", callback_data=f"uidduration:{sid}:0"))
        kb.row(types.InlineKeyboardButton("🔙 Cancel", callback_data=f"uidcancel:{sid}"))
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"⭐ <b>VIP Duration নির্বাচন করুন</b>\n\nUser: <code>{row['user_id']}</code>\nUID: <code>{escape(row['quotex_uid'])}</code>", reply_markup=kb)
    except Exception as exc:
        bot.answer_callback_query(call.id, f"Error: {exc}", show_alert=True)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("uidduration:"))
def uid_duration_callback(call):
    admin_id = call.from_user.id
    if not can(admin_id, "vip"):
        bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
        return
    try:
        _, sid_s, days_s = call.data.split(":", 2)
        sid, days = int(sid_s), int(days_s)
        with DB_LOCK:
            conn = db()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM uid_submissions WHERE id=? AND status='PENDING'", (sid,)).fetchone()
                if not row:
                    conn.rollback()
                    bot.answer_callback_query(call.id, "Pending UID পাওয়া যায়নি.", show_alert=True)
                    return
                # One Quotex UID can belong to only one Telegram account.
                other = conn.execute("SELECT user_id FROM uid_submissions WHERE quotex_uid=? AND status='APPROVED' AND user_id<>? LIMIT 1", (row['quotex_uid'], row['user_id'])).fetchone()
                if other:
                    conn.rollback()
                    bot.answer_callback_query(call.id, "এই UID অন্য account-এ already approved.", show_alert=True)
                    return
                vip_until = None if days == 0 else (now_bd() + timedelta(days=days)).isoformat()
                conn.execute("UPDATE uid_submissions SET status='APPROVED', reviewed_at=? WHERE id=? AND status='PENDING'", (utc_iso(now_utc()), sid))
                conn.execute("UPDATE users SET status='VIP', vip_until=? WHERE user_id=?", (vip_until, row['user_id']))
                conn.commit()
            finally:
                conn.close()
        bot.answer_callback_query(call.id, "VIP approved.")
        try:
            label = "Unlimited" if days == 0 else f"{days} days"
            bot.send_message(row['user_id'], f"⭐ <b>VIP Approved</b>\n\nVIP Duration: <b>{label}</b>\nUID: <code>{escape(row['quotex_uid'])}</code>", reply_markup=main_keyboard(row['user_id']))
        except Exception:
            pass
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
    except Exception as exc:
        bot.answer_callback_query(call.id, f"Error: {exc}", show_alert=True)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("uidreject:"))
def uid_reject_callback(call):
    admin_id = call.from_user.id
    if not can(admin_id, "vip"):
        bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
        return
    try:
        sid = int(call.data.split(":", 1)[1])
        with DB_LOCK:
            conn = db()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM uid_submissions WHERE id=? AND status='PENDING'", (sid,)).fetchone()
                if not row:
                    conn.rollback()
                    bot.answer_callback_query(call.id, "Pending UID পাওয়া যায়নি.", show_alert=True)
                    return
                conn.execute("UPDATE uid_submissions SET status='REJECTED', reviewed_at=? WHERE id=? AND status='PENDING'", (utc_iso(now_utc()), sid))
                conn.commit()
            finally:
                conn.close()
        bot.answer_callback_query(call.id, "UID rejected.")
        try:
            bot.send_message(row['user_id'], "❌ আপনার Quotex UID rejected হয়েছে.", reply_markup=main_keyboard(row['user_id']))
        except Exception:
            pass
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
    except Exception as exc:
        bot.answer_callback_query(call.id, f"Error: {exc}", show_alert=True)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("uidcancel:"))
def uid_cancel_callback(call):
    if not can(call.from_user.id, "vip"):
        bot.answer_callback_query(call.id, "Access denied.", show_alert=True)
        return
    bot.answer_callback_query(call.id, "Cancelled")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("sigres:"))
def signal_result_callback(call):
    try:
        _, sid_s, result=call.data.split(":",2); sid=int(sid_s); uid=call.from_user.id
        if result not in ("WIN","LOSS","SKIP"): raise ValueError("Invalid result")
        with DB_LOCK:
            conn=db()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute("SELECT 1 FROM signal_user_results WHERE signal_id=? AND user_id=?",(sid,uid)).fetchone():
                    conn.rollback(); bot.answer_callback_query(call.id,"Already submitted for this signal.",show_alert=True); return
                conn.execute("INSERT INTO signal_user_results(signal_id,user_id,result,created_at) VALUES(?,?,?,?)",(sid,uid,result,utc_iso(now_utc())))
                # Keep aggregate signal result as the latest admin-visible outcome only if admin explicitly reveals it later.
                conn.commit()
            finally: conn.close()
        bot.answer_callback_query(call.id,"Saved: "+result)
        try: bot.edit_message_reply_markup(call.message.chat.id,call.message.message_id,reply_markup=None)
        except Exception: pass
        if result in ("WIN","LOSS"):
            apply_mm_result(uid,sid,result)
    except Exception as exc:
        try: bot.answer_callback_query(call.id,"Error: "+str(exc),show_alert=True)
        except Exception: pass


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("liveres:"))
def live_result_callback(call):
    try:
        _, lid_s, result=call.data.split(":",2); lid=int(lid_s); uid=call.from_user.id
        if result not in ("WIN","LOSS","SKIP"): raise ValueError("Invalid result")
        with DB_LOCK:
            conn=db()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute("SELECT 1 FROM live_signal_user_results WHERE live_signal_id=? AND user_id=?",(lid,uid)).fetchone():
                    conn.rollback(); bot.answer_callback_query(call.id,"Already submitted for this live signal.",show_alert=True); return
                conn.execute("INSERT INTO live_signal_user_results(live_signal_id,user_id,result,created_at) VALUES(?,?,?,?)",(lid,uid,result,utc_iso(now_utc())))
                conn.commit()
            finally: conn.close()
        bot.answer_callback_query(call.id,"Saved: "+result)
        try: bot.edit_message_reply_markup(call.message.chat.id,call.message.message_id,reply_markup=None)
        except Exception: pass
    except Exception as exc:
        try: bot.answer_callback_query(call.id,"Error: "+str(exc),show_alert=True)
        except Exception: pass


def live_result_markup(live_signal_id):
    kb=types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("✅ WIN",callback_data=f"liveres:{live_signal_id}:WIN"),types.InlineKeyboardButton("❌ LOSE",callback_data=f"liveres:{live_signal_id}:LOSS"),types.InlineKeyboardButton("⏭️ SKIP",callback_data=f"liveres:{live_signal_id}:SKIP"))
    return kb


def patched_admin_withdrawals(message):
    uid=message.from_user.id
    if not can(uid,"withdraw"):
        return bot.send_message(message.chat.id,"⛔ Access denied.",reply_markup=admin_keyboard())
    with DB_LOCK:
        conn=db()
        try:
            rows=conn.execute("SELECT w.*,u.username,u.first_name,(SELECT COUNT(*) FROM referrals r WHERE r.referrer_id=w.user_id AND r.risk_flag=1) risk_count,(SELECT COUNT(*) FROM referrals r WHERE r.referrer_id=w.user_id AND r.status!='PAID') pending_refs FROM withdrawals w LEFT JOIN users u ON u.user_id=w.user_id WHERE w.status='PENDING' ORDER BY w.id ASC LIMIT 20").fetchall()
        finally: conn.close()
    if not rows:
        return bot.send_message(message.chat.id,"💸 <b>PENDING WITHDRAWALS</b>\n\nNone.",reply_markup=make_keyboard([["📊 Withdrawal Reports"],["🔙 Back","🏠 Main Menu"]]))
    lines=["💸 <b>PENDING WITHDRAWALS</b>",""]
    buttons=[]
    for r in rows:
        risk="⚠️ REVIEW" if int(r["risk_count"] or 0)>0 else "🟢 Normal"
        lines.append(f"#{r['id']} | <code>{r['user_id']}</code> | <b>{money(r['amount_cents'])}</b> | {r['method']} | {risk}")
        buttons.append([f"💸 WD #{r['id']}"])
    STATES[uid]={"action":"withdraw_admin_select"}
    bot.send_message(message.chat.id,"\n".join(lines),reply_markup=make_keyboard(buttons+[["📊 Withdrawal Reports"],["🔙 Back","🏠 Main Menu"]]))


def withdraw_admin_action_keyboard():
    return make_keyboard([["✅ Approve Withdrawal","❌ Reject Withdrawal"],["🔙 Back","🏠 Main Menu"]])

def apply_mm_result(user_id, signal_id, result):
    with DB_LOCK:
        conn=db()
        try:
            u=conn.execute("SELECT * FROM users WHERE user_id=?",(user_id,)).fetchone()
            if not u: return
            today=now_bd().date().isoformat()
            if u["mm_date"]!=today:
                conn.execute("UPDATE users SET mm_date=?,mm_daily_profit_cents=0,mm_daily_loss_cents=0,mm_trade_count=0,mm_stop=0 WHERE user_id=?",(today,user_id)); u=conn.execute("SELECT * FROM users WHERE user_id=?",(user_id,)).fetchone()
            if int(u["mm_stop"] or 0): return
            if int(u["mm_max_trades"] or 0)>0 and int(u["mm_trade_count"] or 0)>=int(u["mm_max_trades"]): return
            base=int(u["mm_base_cents"] or 100); m1=int(u["mm_m1_cents"] or 200)
            # Base -> M1 after a loss; after either result, next trade is Base. No M2.
            prev=conn.execute("SELECT result,amount_cents FROM mm_trades WHERE user_id=? ORDER BY id DESC LIMIT 1",(user_id,)).fetchone()
            amount=m1 if prev and prev["result"]=="LOSS" else base
            payout=int(round(amount*float(get_setting("mm_payout","0.80"))))
            pnl=payout if result=="WIN" else -amount
            conn.execute("INSERT INTO mm_trades(user_id,signal_id,result,amount_cents,pnl_cents,created_at) VALUES(?,?,?,?,?,?)",(user_id,signal_id,result,amount,pnl,utc_iso(now_utc())))
            conn.execute("UPDATE users SET mm_trade_count=mm_trade_count+1,mm_daily_profit_cents=mm_daily_profit_cents+?,mm_daily_loss_cents=mm_daily_loss_cents+? WHERE user_id=?",(max(pnl,0),max(-pnl,0),user_id))
            conn.commit()
        finally: conn.close()


def start_withdrawal_flow(message):
    uid=message.from_user.id
    if get_setting("withdrawals","ON")!="ON": return bot.send_message(message.chat.id,"💸 Withdrawals বর্তমানে OFF.",reply_markup=main_keyboard(uid))
    with DB_LOCK:
        conn=db()
        try:
            if conn.execute("SELECT 1 FROM withdrawals WHERE user_id=? AND status='PENDING'",(uid,)).fetchone(): return bot.send_message(message.chat.id,"⏳ আপনার একটি withdrawal already pending আছে.",reply_markup=main_keyboard(uid))
        finally: conn.close()
    STATES[uid]={"action":"withdraw_method","stage":"method"}
    bot.send_message(message.chat.id,"💸 <b>Choose Payment Method</b>",reply_markup=make_keyboard([["🟢 BKASH","🔵 NAGAD"],["🟠 BINANCE"],["🔙 Back","🏠 Main Menu"]]))


# Replace the old referral processor with the secure one for legacy calls.
process_referral_bonus = secure_process_referral_bonus
deliver_signal = patched_deliver_signal
format_signal = patched_format_signal
admin_withdrawals = patched_admin_withdrawals

# ============================================================
# SAFE STARTUP MIGRATION
# ============================================================

def safe_startup_migration():
    """Initialize/migrate the DB without letting a legacy-data issue kill polling."""
    try:
        init_db()
    except Exception:
        logger.exception("Base database initialization failed.")
        return False

    try:
        migrate_final_schema()
    except Exception:
        logger.exception("Final schema migration failed; continuing with base schema.")

    try:
        ensure_signal_user_results()
    except Exception:
        logger.exception("Signal-user result migration failed.")

    defaults = {
        "referral_min_signals": "1",
        "referral_hold_hours": "24",
        "withdraw_hold_hours": "0",
        "auto_notification": "ON",
        "mm_payout": "0.80",
        "candle_ai_enabled": "OFF",
        "candle_ai_max_images": "3",
        "candle_ai_model": "gemini-3.5-flash",
    }

    for key, value in defaults.items():
        try:
            if get_setting(key, "") == "":
                set_setting(key, value)
        except Exception:
            logger.exception("Could not initialize setting %s", key)

    return True


# ============================================================
# AI CANDLE / CHART SCREENSHOT ANALYSIS
# ============================================================

# (superseded: see rule-based/engine version below)


def candle_ai_max_images():
    try:
        return max(1, min(10, int(get_setting("candle_ai_max_images", "3"))))
    except Exception:
        return 3


def candle_ai_prompt():
    return (
        "You are analyzing screenshots of a Quotex-style trading chart. "
        "Analyze ONLY what is visibly present in the uploaded screenshots. "
        "Do not claim certainty and do not invent hidden candles, prices, indicators, or timeframes. "
        "Identify visible trend, momentum, support/resistance, candle patterns, and confirmations or contradictions. "
        "Give a probabilistic scenario for the NEXT 1 candle and NEXT 2 candles. "
        "For each use UP, DOWN, or WAIT and briefly explain the visible evidence. "
        "If the chart is unclear, cropped, or lacks enough candle context, use WAIT/INSUFFICIENT DATA. "
        "Never promise profit or guaranteed accuracy. "
        "Answer concisely in Bengali with headings: "
        "📊 Chart Analysis, 📈 Trend, 🕯️ Candle Pattern, 🧱 Support/Resistance, "
        "➡️ Next 1 Candle, ➡️ Next 2 Candles, ⚠️ Risk/Uncertainty."
    )


GEMINI_RETIRED_PREFIXES = ("gemini-1.", "gemini-2.0", "gemini-2.5", "gemini-pro")
GEMINI_STATIC_FALLBACKS = ["gemini-3.5-flash", "gemini-3-flash-preview", "gemini-3.1-flash-lite"]
_GEMINI_CACHE = {"ok": None, "found": [], "found_at": 0.0}


def _gemini_http(url, payload=None, timeout=60):
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"},
                                 method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _gemini_discover_models(api_key):
    """Ask Google which flash models exist right now (ListModels), newest first."""
    now = time.time()
    if _GEMINI_CACHE["found"] and now - _GEMINI_CACHE["found_at"] < 3600:
        return list(_GEMINI_CACHE["found"])
    found = []
    try:
        data = _gemini_http("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200&key=" + urllib.parse.quote(api_key, safe=""), None, 20)
        for m_ in data.get("models", []):
            name = str(m_.get("name", "")).replace("models/", "")
            if "generateContent" not in (m_.get("supportedGenerationMethods") or []):
                continue
            if "flash" not in name or any(x in name for x in ("image", "tts", "live", "audio", "embedding", "native", "computer", "robotics", "thinking-exp")):
                continue
            if name.startswith(GEMINI_RETIRED_PREFIXES):
                continue
            ver = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
            found.append((float(ver.group(1)) if ver else 0.0, "preview" not in name and "exp" not in name, "lite" not in name, name))
        found.sort(reverse=True)
        found = [x[3] for x in found]
        _GEMINI_CACHE.update(found=found, found_at=now)
    except Exception as exc:
        logger.warning("Gemini ListModels failed: %s", str(exc)[:120])
    return found


def gemini_analyze_candle_images(image_bytes_list):
    """Gemini REST call. Tries configured model, then auto-discovered current models. Never raises."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return ("⚠️ <b>AI Candle Analysis চালু করা যাচ্ছে না.</b>\n\n"
                "Admin-কে Railway Variables-এ <code>GEMINI_API_KEY</code> সেট করতে হবে।")
    configured = [os.getenv("GEMINI_MODEL", "").strip(), str(get_setting("candle_ai_model", "") or "").strip()]
    configured = [m_.replace("models/", "") for m_ in configured if m_]
    order, seen = [], set()
    def add(m_):
        if m_ and m_ not in seen:
            seen.add(m_); order.append(m_)
    add(_GEMINI_CACHE["ok"])
    for m_ in configured:
        if not m_.startswith(GEMINI_RETIRED_PREFIXES):
            add(m_)
    for m_ in _gemini_discover_models(api_key):
        add(m_)
    for m_ in GEMINI_STATIC_FALLBACKS:
        add(m_)
    for m_ in configured:           # retired names last, only if nothing else works
        add(m_)

    parts = [{"text": candle_ai_prompt()}]
    for raw in image_bytes_list:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(raw).decode("ascii")}})
    payload = json.dumps({"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1400}}).encode("utf-8")

    last_error = "unknown"
    for model in order[:6]:
        try:
            url = ("https://generativelanguage.googleapis.com/v1beta/models/" + urllib.parse.quote(model, safe="")
                   + ":generateContent?key=" + urllib.parse.quote(api_key, safe=""))
            data = _gemini_http(url, payload, 60)
            cands = data.get("candidates") or []
            if cands:
                out = "\n".join(str(p.get("text", "")).strip() for p in cands[0].get("content", {}).get("parts", []) if p.get("text")).strip()
                if out:
                    _GEMINI_CACHE["ok"] = model
                    return out
            last_error = f"{model}: empty response"
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:
                body = ""
            last_error = f"{model}: HTTP {exc.code} {body[:90]}"
            if _GEMINI_CACHE["ok"] == model:
                _GEMINI_CACHE["ok"] = None
            if exc.code in (401, 403):      # key problem, other models will not help
                break
        except Exception as exc:
            last_error = f"{model}: {str(exc)[:90]}"
    logger.error("Gemini failed: %s", last_error)
    return ("⚠️ <b>AI service error</b>\n\nসব model fail হয়েছে। শেষ error:\n"
            f"<code>{escape(last_error[:220])}</code>\n\n"
            "Admin: Railway Variables-এ <code>GEMINI_MODEL</code> (যেমন gemini-3.5-flash) দিন.")



# (superseded: see rule-based/engine version below)


def handle_candle_photo(message):
    uid = message.from_user.id
    state = STATES.get(uid)

    if not state or state.get("action") != "candle_upload":
        return False

    if not candle_ai_enabled() and not is_master(uid):
        clear_state(uid)
        bot.send_message(
            message.chat.id,
            "🤖 AI Candle Analysis বর্তমানে OFF.",
            reply_markup=main_keyboard(uid)
        )
        return True

    limit = candle_ai_max_images()
    images = state.setdefault("images", [])

    if len(images) >= limit:
        bot.send_message(
            message.chat.id,
            f"⚠️ Maximum <b>{limit}টি</b> screenshot already added.\n"
            "এখন 🔍 Analyze Candles চাপুন.",
            reply_markup=make_keyboard([
                ["🔍 Analyze Candles", "🗑️ Clear Candles"],
                ["🔙 Back", "🏠 Main Menu"]
            ])
        )
        return True

    try:
        photo = message.photo[-1]
        images.append(photo.file_id)
        remaining = limit - len(images)
        if remaining:
            notice = f"✅ Screenshot {len(images)}/{limit} added.\nআরও {remaining}টি দিতে পারেন."
        else:
            notice = "✅ Maximum screenshot count reached.\nএখন 🔍 Analyze Candles চাপুন."

        bot.send_message(
            message.chat.id,
            notice,
            reply_markup=make_keyboard([
                ["🔍 Analyze Candles", "🗑️ Clear Candles"],
                ["🔙 Back", "🏠 Main Menu"]
            ])
        )
    except Exception:
        logger.exception("Candle photo state error")
        bot.send_message(
            message.chat.id,
            "❌ Screenshot নেওয়া যায়নি. আবার চেষ্টা করুন."
        )

    return True


def analyze_candle_state(message):
    uid = message.from_user.id
    state = STATES.get(uid)

    if not state or state.get("action") != "candle_upload":
        return False

    text = (message.text or "").strip()

    if text == "🗑️ Clear Candles":
        state["images"] = []
        bot.send_message(
            message.chat.id,
            "🗑️ সব screenshot clear করা হয়েছে.\nআবার screenshot upload করুন.",
            reply_markup=make_keyboard([
                ["🔍 Analyze Candles", "🗑️ Clear Candles"],
                ["🔙 Back", "🏠 Main Menu"]
            ])
        )
        return True

    if text != "🔍 Analyze Candles":
        return False

    file_ids = list(state.get("images") or [])
    if not file_ids:
        bot.send_message(
            message.chat.id,
            "📸 আগে অন্তত ১টি candle/chart screenshot upload করুন.",
            reply_markup=make_keyboard([
                ["🔍 Analyze Candles", "🗑️ Clear Candles"],
                ["🔙 Back", "🏠 Main Menu"]
            ])
        )
        return True

    if not candle_ai_enabled() and not is_master(uid):
        clear_state(uid)
        bot.send_message(
            message.chat.id,
            "🤖 AI Candle Analysis বর্তমানে OFF.",
            reply_markup=main_keyboard(uid)
        )
        return True

    bot.send_message(
        message.chat.id,
        "🔎 Screenshotগুলো analyze করছি… একটু অপেক্ষা করুন."
    )

    image_bytes = []
    try:
        for file_id in file_ids:
            info = bot.get_file(file_id)
            raw = bot.download_file(info.file_path)
            image_bytes.append(raw)

        result = gemini_analyze_candle_images(image_bytes)
    except Exception:
        logger.exception("Could not download candle screenshot")
        result = "⚠️ Screenshot download/analyze করা যায়নি। আবার চেষ্টা করুন."

    clear_state(uid)

    bot.send_message(
        message.chat.id,
        "🤖 <b>AI CANDLE ANALYSIS</b>\n\n"
        + result
        + "\n\n⚠️ <b>নোট:</b> এটি chart screenshot-ভিত্তিক সম্ভাব্য analysis; "
          "পরের candle নিশ্চিতভাবে কী হবে তা guarantee করা যায় না.",
        reply_markup=main_keyboard(uid)
    )

    return True


@bot.message_handler(content_types=["photo"])
def candle_photo_handler(message):
    try:
        handle_candle_photo(message)
    except Exception:
        logger.exception("Unhandled candle photo handler error")
        try:
            bot.send_message(
                message.chat.id,
                "❌ Screenshot process করা যায়নি. আবার চেষ্টা করুন."
            )
        except Exception:
            pass



# ============================================================
# DATABASE BACKUP
# ============================================================

def backup_database():

    try:

        if not os.path.exists(
            DB_FILE
        ):

            return


        filename = (
            "bot_database_"
            +
            now_bd().strftime(
                "%Y%m%d_%H%M%S"
            )
            +
            ".db"
        )


        backup_path = os.path.join(
            BACKUP_DIR,
            filename
        )


        source = sqlite3.connect(
            DB_FILE
        )

        destination = sqlite3.connect(
            backup_path
        )


        try:

            source.backup(
                destination
            )

        finally:

            destination.close()
            source.close()


        files = sorted(

            [
                os.path.join(
                    BACKUP_DIR,
                    filename
                )

                for filename
                in os.listdir(
                    BACKUP_DIR
                )

                if filename.endswith(".db")
            ],

            key=os.path.getmtime,

            reverse=True

        )


        # Keep newest 7 backups.
        for old_file in files[7:]:

            try:

                os.remove(
                    old_file
                )

            except Exception:

                pass


        logger.info(
            "Database backup created."
        )


    except Exception:

        logger.exception(
            "Backup failed."
        )


def backup_loop():

    while True:

        try:
            release_referral_bonuses()
        except Exception:
            logger.exception("Referral release loop failed")
        time.sleep(
            6 * 60 * 60
        )

        backup_database()


# ============================================================
# STARTUP
# ============================================================

def main():

    if not safe_startup_migration():
        logger.error("Database startup initialization had errors; attempting polling anyway.")

    try:
        backup_database()
    except Exception:
        logger.exception("Initial backup failed; continuing.")


    threading.Thread(
        target=auto_signal_loop,
        daemon=True
    ).start()


    threading.Thread(
        target=backup_loop,
        daemon=True
    ).start()


    logger.info(
        "SM QUATEX SURE SHORT started."
    )


    while True:

        try:

            # A stale webhook or skipped pending update can make /start appear dead
            # immediately after deployment. Clear webhook state and process queued updates.
            try:
                bot.remove_webhook()
            except Exception:
                logger.exception("Webhook cleanup failed")

            try:
                me = bot.get_me()
                logger.info("Telegram bot connected as @%s (%s)", getattr(me, "username", "unknown"), getattr(me, "id", "?"))
            except Exception:
                logger.exception("Telegram authentication check failed")
                time.sleep(5)
                continue

            bot.infinity_polling(
                skip_pending=False,
                timeout=30,
                long_polling_timeout=30
            )

        except Exception:

            logger.exception(
                "Polling crashed."
            )

            time.sleep(
                5
            )



# ============================================================
# SM QUATEX SURE SHORT — FINAL UPDATE EXTENSION
# Additive/backward-compatible hardening and feature layer.
# ============================================================

# normalize_uid is intentionally defined here before runtime use by every
# handler; Python resolves the function when the handler executes.
def __clean_normalize_uid_v2(value):
    return re.sub(r"\D", "", str(value or "")).strip()


def _safe_add_column(conn, table, column, definition):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def final_migration():
    """Idempotent additive migration. Never drops old columns/data."""
    with DB_LOCK:
        conn = db()
        try:
            user_cols = {
                "vip_reminder_7d_sent": "INTEGER NOT NULL DEFAULT 0",
                "vip_reminder_3d_sent": "INTEGER NOT NULL DEFAULT 0",
                "vip_started_at": "TEXT",
                "warning_count": "INTEGER NOT NULL DEFAULT 0",
                "warning_reason": "TEXT",
                "uid_confirmed": "INTEGER NOT NULL DEFAULT 0",
                "uid_confirm_at": "TEXT",
                "mm_balance_cents": "INTEGER NOT NULL DEFAULT 0",
                "mm_target_cents": "INTEGER NOT NULL DEFAULT 0",
                "mm_loss_limit_cents": "INTEGER NOT NULL DEFAULT 0",
                "mm_base_cents": "INTEGER NOT NULL DEFAULT 100",
                "mm_m1_cents": "INTEGER NOT NULL DEFAULT 200",
                "mm_payout_percent": "REAL NOT NULL DEFAULT 85.0",
                "mm_current_mode": "TEXT NOT NULL DEFAULT 'BASE'",
                "mm_daily_win_cents": "INTEGER NOT NULL DEFAULT 0",
                "mm_daily_loss_cents": "INTEGER NOT NULL DEFAULT 0",
                "mm_trade_count": "INTEGER NOT NULL DEFAULT 0",
                "mm_stop": "INTEGER NOT NULL DEFAULT 0",
                "mm_date": "TEXT",
                "mm_enabled": "INTEGER NOT NULL DEFAULT 0",
                "mm_last_signal_id": "INTEGER",
                "ai_usage_count": "INTEGER NOT NULL DEFAULT 0",
                "ai_usage_date": "TEXT",
                "ai_limit_vip": "INTEGER NOT NULL DEFAULT 50",
                "ai_limit_nonvip": "INTEGER NOT NULL DEFAULT 3",
            }
            for c, d in user_cols.items():
                _safe_add_column(conn, "users", c, d)

            referral_cols = {
                "status": "TEXT NOT NULL DEFAULT 'PENDING'",
                "quotex_uid": "TEXT",
                "deposit_amount_cents": "INTEGER NOT NULL DEFAULT 0",
                "warning_count": "INTEGER NOT NULL DEFAULT 0",
                "risk_flag": "INTEGER NOT NULL DEFAULT 0",
                "risk_note": "TEXT",
                "reviewed_by": "INTEGER",
                "reviewed_at": "TEXT",
                "qualified_at": "TEXT",
                "paid_at": "TEXT",
                "reject_reason": "TEXT",
                "reject_reason_type": "TEXT",
                "rejected_by": "INTEGER",
                "rejected_at": "TEXT",
            }
            for c, d in referral_cols.items():
                _safe_add_column(conn, "referrals", c, d)

            for table, cols in {
                "uid_submissions": {"confirm_at": "TEXT", "deposit_attested": "INTEGER NOT NULL DEFAULT 0"},
                "withdrawals": {"reviewed_by": "INTEGER", "hold_until": "TEXT", "review_note": "TEXT"},
                "notify_targets": {"target_type": "TEXT NOT NULL DEFAULT 'GROUP'", "audience": "TEXT NOT NULL DEFAULT 'ALL'", "selected_users": "TEXT NOT NULL DEFAULT ''"},
            }.items():
                for c, d in cols.items():
                    _safe_add_column(conn, table, c, d)

            conn.executescript("""
            CREATE TABLE IF NOT EXISTS referral_warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referral_id INTEGER,
                user_id INTEGER NOT NULL,
                warning_type TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_by INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS uid_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                quotex_uid TEXT NOT NULL,
                event TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vip_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                days INTEGER,
                old_until TEXT,
                new_until TEXT,
                admin_id INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vip_reminders_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                UNIQUE(user_id, kind)
            );
            CREATE TABLE IF NOT EXISTS admin_actions_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target_id TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                admin_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mm_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                signal_id INTEGER,
                trade_mode TEXT NOT NULL DEFAULT 'BASE',
                result TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                pnl_cents INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                image_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                result_summary TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS referral_levels (
                min_refs INTEGER PRIMARY KEY,
                bonus_cents INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
            CREATE INDEX IF NOT EXISTS idx_users_name ON users(first_name);
            CREATE INDEX IF NOT EXISTS idx_referrals_status ON referrals(status);
            CREATE INDEX IF NOT EXISTS idx_uid_tracking_uid ON uid_tracking(quotex_uid);
            """)

            levels = [(1,100),(5,125),(10,150),(20,200),(30,250)]
            for n, cents in levels:
                conn.execute("INSERT OR IGNORE INTO referral_levels(min_refs,bonus_cents) VALUES(?,?)", (n,cents))

            defaults = {
                "free_cycle_days":"2", "withdraw_hold_minutes":"0",
                "referral_qualification":"UID + $15 deposit attestation + admin review",
                "candle_ai_enabled":"OFF", "candle_ai_max_images":"3",
                "candle_ai_vip_limit":"50", "candle_ai_nonvip_limit":"3",
                "candle_ai_confidence_min":"70", "candle_ai_model":"gemini-3.5-flash",
                "mm_payout_percent":"85", "mm_min_base":"1.00",
                "vip_reminder_days":"7,3", "result_reveal":"OFF",
            }
            for k,v in defaults.items():
                conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k,v))
            conn.commit()
        finally:
            conn.close()


def __clean_safe_startup_migration_v2():
    """Run original migrations plus additive migration without killing polling."""
    try:
        init_db()
    except Exception:
        logger.exception("Initial DB init failed")
        return False
    try:
        migrate_final_schema()
    except Exception:
        logger.exception("Legacy schema migration failed; continuing with safe migration")
    try:
        ensure_signal_user_results()
    except Exception:
        logger.exception("Signal-user result migration failed")
    try:
        final_migration()
    except Exception:
        logger.exception("Final additive migration failed")
    return True


def __clean_admin_audit_v2(admin_id, action, target_id="", note=""):
    try:
        with DB_LOCK:
            conn=db()
            try:
                conn.execute("INSERT INTO admin_actions_log(admin_id,action,target_id,note,created_at) VALUES(?,?,?,?,?)",(admin_id,str(action),str(target_id),str(note),utc_iso(now_utc())))
                conn.commit()
            finally: conn.close()
    except Exception:
        logger.exception("Admin audit failed")


def _daily_reset_key():
    now = now_bd()
    # Daily MM/AI reset is 06:00 Bangladesh time.
    return (now.date() if now.hour >= 6 else (now.date()-timedelta(days=1))).isoformat()


def reset_user_daily_state(user_id):
    day=_daily_reset_key()
    with DB_LOCK:
        conn=db()
        try:
            u=conn.execute("SELECT ai_usage_date,mm_date FROM users WHERE user_id=?",(user_id,)).fetchone()
            if not u: return
            if u["ai_usage_date"] != day:
                conn.execute("UPDATE users SET ai_usage_count=0,ai_usage_date=? WHERE user_id=?",(day,user_id))
            if u["mm_date"] != day:
                conn.execute("UPDATE users SET mm_daily_win_cents=0,mm_daily_loss_cents=0,mm_trade_count=0,mm_stop=0,mm_current_mode='BASE',mm_date=? WHERE user_id=?",(day,user_id))
            conn.commit()
        finally: conn.close()


def vip_is_active(user):
    if not user:
        return False
    if user["status"] != "VIP":
        return False
    if not user["vip_until"]:
        return True
    try:
        until = datetime.fromisoformat(user["vip_until"])
        if until.tzinfo is None:
            until = until.replace(tzinfo=BD_TZ)
        return until > now_bd()
    except Exception:
        return False


def vip_expiry_loop():
    while True:
        try:
            with DB_LOCK:
                conn=db()
                try:
                    rows=conn.execute("SELECT * FROM users WHERE status='VIP' AND vip_until IS NOT NULL").fetchall()
                finally: conn.close()
            now=now_bd()
            for u in rows:
                try: exp=datetime.fromisoformat(u["vip_until"]).astimezone(BD_TZ)
                except Exception: continue
                if exp <= now:
                    with DB_LOCK:
                        conn=db()
                        try:
                            conn.execute("UPDATE users SET status='FREE',vip_until=NULL,vip_reminder_7d_sent=0,vip_reminder_3d_sent=0 WHERE user_id=?",(u["user_id"],))
                            conn.execute("INSERT INTO vip_history(user_id,action,old_until,admin_id,created_at) VALUES(?,?,?,?,?)",(u["user_id"],"EXPIRED",u["vip_until"],ADMIN_ID,utc_iso(now_utc())))
                            conn.commit()
                        finally: conn.close()
                    try: bot.send_message(u["user_id"],"⏰ <b>VIP Expired</b>\nআপনার account এখন FREE status-এ ফিরে গেছে.",reply_markup=main_keyboard(u["user_id"]))
                    except Exception: pass
                    continue
                days=(exp-now).total_seconds()/86400
                if days <= 3 and not int(u["vip_reminder_3d_sent"] or 0):
                    _send_vip_reminder(u,"3d",exp)
                elif days <= 7 and not int(u["vip_reminder_7d_sent"] or 0):
                    _send_vip_reminder(u,"7d",exp)
        except Exception:
            logger.exception("VIP expiry loop error")
        time.sleep(300)


def _send_vip_reminder(user,kind,exp):
    text=f"⏰ <b>VIP Reminder</b>\n\n⭐ Your VIP expires: <b>{exp.strftime('%d %b %Y %I:%M %p')}</b>\n📅 Reminder: {kind.replace('d',' days')}"
    try: bot.send_message(user["user_id"],text,reply_markup=main_keyboard(user["user_id"]))
    except Exception: pass
    with DB_LOCK:
        conn=db()
        try:
            conn.execute("INSERT OR IGNORE INTO vip_reminders_log(user_id,kind,sent_at) VALUES(?,?,?)",(user["user_id"],kind,utc_iso(now_utc())))
            conn.execute("UPDATE users SET vip_reminder_7d_sent=CASE WHEN ?='7d' THEN 1 ELSE vip_reminder_7d_sent END,vip_reminder_3d_sent=CASE WHEN ?='3d' THEN 1 ELSE vip_reminder_3d_sent END WHERE user_id=?",(kind,kind,user["user_id"]))
            conn.commit()
        finally: conn.close()


def mm_daily_reset_loop():
    while True:
        try:
            day=_daily_reset_key()
            with DB_LOCK:
                conn=db()
                try:
                    conn.execute("UPDATE users SET mm_daily_win_cents=0,mm_daily_loss_cents=0,mm_trade_count=0,mm_stop=0,mm_current_mode='BASE',mm_date=? WHERE mm_enabled=1 AND (mm_date IS NULL OR mm_date<>?)",(day,day))
                    conn.commit()
                finally: conn.close()
        except Exception: logger.exception("MM daily reset error")
        time.sleep(60)


def ai_limit_reset_loop():
    while True:
        try:
            day=_daily_reset_key()
            with DB_LOCK:
                conn=db()
                try:
                    conn.execute("UPDATE users SET ai_usage_count=0,ai_usage_date=? WHERE ai_usage_date IS NULL OR ai_usage_date<>?",(day,day))
                    conn.commit()
                finally: conn.close()
        except Exception: logger.exception("AI limit reset error")
        time.sleep(60)


def cleanup_states_loop():
    while True:
        try:
            cutoff=time.time()-3600
            for uid,st in list(STATES.items()):
                if st.get("updated_ts",0) < cutoff:
                    STATES.pop(uid,None)
        except Exception: logger.exception("STATE cleanup error")
        time.sleep(300)


def _state_set(uid, data):
    data=dict(data); data["updated_ts"]=time.time(); STATES[uid]=data


def warn_user(user_id, reason, admin_id=None, referral_id=None, warning_type="MANUAL"):
    admin_id=admin_id or ADMIN_ID
    with DB_LOCK:
        conn=db()
        try:
            conn.execute("INSERT INTO referral_warnings(referral_id,user_id,warning_type,reason,created_by,created_at) VALUES(?,?,?,?,?,?)",(referral_id,user_id,warning_type,reason,admin_id,utc_iso(now_utc())))
            conn.execute("UPDATE users SET warning_count=warning_count+1,warning_reason=? WHERE user_id=?",(reason,user_id))
            row=conn.execute("SELECT warning_count,referred_by FROM users WHERE user_id=?",(user_id,)).fetchone()
            if row and int(row["warning_count"] or 0)>=3:
                conn.execute("UPDATE users SET blocked=1,status='BLOCKED' WHERE user_id=?",(user_id,))
            conn.commit()
        finally: conn.close()
    admin_audit(admin_id,"WARNING",user_id,reason)
    try:
        count=get_user(user_id)["warning_count"]
        bot.send_message(user_id,f"⚠️ <b>Warning</b>\n\n{escape(reason)}\n\n⚠️ Warning count: <b>{count}/3</b>" + ("\n🚫 3 warnings reached — account blocked." if count>=3 else ""),reply_markup=main_keyboard(user_id))
    except Exception: pass
    return count


def vip_duration_keyboard(prefix="vipdur"):
    kb=types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("7 Days",callback_data=f"{prefix}:7"),types.InlineKeyboardButton("15 Days",callback_data=f"{prefix}:15"))
    kb.row(types.InlineKeyboardButton("30 Days",callback_data=f"{prefix}:30"),types.InlineKeyboardButton("60 Days",callback_data=f"{prefix}:60"))
    kb.row(types.InlineKeyboardButton("90 Days",callback_data=f"{prefix}:90"),types.InlineKeyboardButton("♾️ Unlimited",callback_data=f"{prefix}:0"))
    return kb


def __clean_start_uid_submission_v2(message):
    uid=message.from_user.id
    u=get_user(uid)
    if u and u["status"]=="VIP":
        return bot.send_message(message.chat.id,"⭐ আপনি already VIP.",reply_markup=main_keyboard(uid))
    with DB_LOCK:
        conn=db()
        try: pending=conn.execute("SELECT 1 FROM uid_submissions WHERE user_id=? AND status='PENDING'",(uid,)).fetchone()
        finally: conn.close()
    if pending: return bot.send_message(message.chat.id,"⏳ আপনার UID already pending আছে.",reply_markup=main_keyboard(uid))
    _state_set(uid,{"action":"uid_confirm"})
    bot.send_message(message.chat.id,
        "🆔 <b>Quotex UID Verification</b>\n\n"
        "UID submit করার আগে confirmation প্রয়োজন:\n\n"
        "☐ আমি Quotex referral link দিয়ে account করেছি\n"
        "☐ আমি $15+ deposit করেছি\n\n"
        "⚠️ Screenshot লাগবে না। Admin review করবে.",
        reply_markup=make_keyboard([["✅ সব ঠিক আছে","❌ Cancel"],["🔗 Register on Quotex"],["🔙 Back","🏠 Main Menu"]]))


def _submit_uid_after_confirm(message):
    uid=message.from_user.id
    _state_set(uid,{"action":"uid"})
    bot.send_message(message.chat.id,"🆔 <b>Quotex UID</b>\n\n6–15 digit UID পাঠান.",reply_markup=back_keyboard())


def _record_uid_confirmation(uid):
    with DB_LOCK:
        conn=db()
        try:
            conn.execute("UPDATE users SET uid_confirmed=1,uid_confirm_at=? WHERE user_id=?",(utc_iso(now_utc()),uid))
            conn.commit()
        finally: conn.close()


def _create_referral_for_user(user_id, quotex_uid):
    with DB_LOCK:
        conn=db()
        try:
            u=conn.execute("SELECT referred_by FROM users WHERE user_id=?",(user_id,)).fetchone()
            if not u or not u["referred_by"]: return None
            existing=conn.execute("SELECT id FROM referrals WHERE referred_id=?",(user_id,)).fetchone()
            if existing: return existing["id"]
            bonus=100
            r=conn.execute("INSERT INTO referrals(referrer_id,referred_id,bonus_cents,status,quotex_uid,deposit_amount_cents,created_at) VALUES(?,?,?,?,?,?,?)",(u["referred_by"],user_id,bonus,"PENDING",quotex_uid,1500,utc_iso(now_utc())))
            rid=r.lastrowid
            conn.execute("INSERT INTO uid_tracking(user_id,quotex_uid,event,note,created_at) VALUES(?,?,?,?,?)",(user_id,quotex_uid,"SUBMITTED","Referral UID; $15 deposit attested",utc_iso(now_utc())))
            conn.commit(); return rid
        finally: conn.close()


def _qualify_referral_after_vip(user_id, admin_id):
    with DB_LOCK:
        conn=db()
        try:
            r=conn.execute("SELECT * FROM referrals WHERE referred_id=? AND status='PENDING'",(user_id,)).fetchone()
            if not r: return
            conn.execute("UPDATE referrals SET status='QUALIFIED',qualified_at=?,reviewed_by=?,reviewed_at=? WHERE id=?",(utc_iso(now_utc()),admin_id,utc_iso(now_utc()),r["id"]))
            conn.commit()
        finally: conn.close()
    try: bot.send_message(r["referrer_id"],f"✅ <b>Referral Qualified</b>\nUser: <code>{user_id}</code>\n💰 Bonus: {money(r['bonus_cents'])}\nStatus: QUALIFIED")
    except Exception: pass


def __clean_release_referral_bonuses_v2():
    """Release only QUALIFIED referrals; never pay at signal view time."""
    with DB_LOCK:
        conn=db()
        try:
            rows=conn.execute("SELECT * FROM referrals WHERE status='QUALIFIED'").fetchall()
            for r in rows:
                already=conn.execute("SELECT 1 FROM wallet_tx WHERE user_id=? AND kind='REFERRAL_BONUS' AND note LIKE ? LIMIT 1",(r["referrer_id"],f"Referral #{r['id']}%" )).fetchone()
                if already: continue
                conn.execute("UPDATE users SET wallet_cents=wallet_cents+?,refs_count=refs_count+1 WHERE user_id=?",(r["bonus_cents"],r["referrer_id"]))
                conn.execute("INSERT INTO wallet_tx(user_id,amount_cents,kind,note,created_at) VALUES(?,?,?,?,?)",(r["referrer_id"],r["bonus_cents"],"REFERRAL_BONUS",f"Referral #{r['id']} approved",utc_iso(now_utc())))
                conn.execute("UPDATE referrals SET status='PAID',paid_at=? WHERE id=?",(utc_iso(now_utc()),r["id"]))
            conn.commit()
        finally: conn.close()


def __clean_secure_process_referral_bonus_v2(user_id):
    # Compatibility shim: qualification is admin/VIP based, not signal-view based.
    return


def mm_calculate(balance_cents,target_cents,loss_cents,payout_percent=85.0):
    payout=max(1.0,float(payout_percent))/100.0
    base=max(100, int(round(target_cents/6.5/payout)))
    base_profit=int(round(base*payout))
    m1=max(base, int(round((base+base_profit)/payout)))
    return base,m1


def mm_configure(user_id,balance,target,loss,payout=85.0):
    balance=max(0,int(round(float(balance)*100))); target=max(0,int(round(float(target)*100))); loss=max(0,int(round(float(loss)*100)))
    base,m1=mm_calculate(balance,target,loss,payout)
    with DB_LOCK:
        conn=db()
        try:
            conn.execute("UPDATE users SET mm_balance_cents=?,mm_target_cents=?,mm_loss_limit_cents=?,mm_base_cents=?,mm_m1_cents=?,mm_payout_percent=?,mm_current_mode='BASE',mm_daily_win_cents=0,mm_daily_loss_cents=0,mm_trade_count=0,mm_stop=0,mm_enabled=1,mm_date=? WHERE user_id=?",(balance,target,loss,base,m1,payout,_daily_reset_key(),user_id))
            conn.commit()
        finally: conn.close()
    return base,m1


def mm_status_text(user_id):
    reset_user_daily_state(user_id); u=get_user(user_id)
    if not u: return "MM unavailable."
    enabled="ON" if int(u["mm_enabled"] or 0) else "OFF"
    mode=u["mm_current_mode"] or "BASE"
    amount=int(u["mm_m1_cents"] or 0) if mode=="M1" else int(u["mm_base_cents"] or 100)
    pnl=int(u["mm_daily_win_cents"] or 0)-int(u["mm_daily_loss_cents"] or 0)
    target=int(u["mm_target_cents"] or 0); loss=int(u["mm_loss_limit_cents"] or 0)
    return (f"💰 <b>MONEY MANAGEMENT</b>\n\n🔘 Status: <b>{enabled}</b>\n💵 Balance: <b>{money(u['mm_balance_cents'])}</b>\n🎯 Win Target: <b>{money(target)}</b>\n🛑 Loss Limit: <b>{money(loss)}</b>\n\n📊 Today Net: <b>{money(pnl)}</b>\n🔢 Trades: <b>{u['mm_trade_count']}</b>\n🎯 Target: <b>{money(target)}</b>\n🛑 Loss Limit: <b>{money(loss)}</b>\n\n➡️ Next: <b>{mode}</b>\n💵 Trade: <b>{money(amount)}</b>")


def __clean_mm_menu_v2(message):
    uid=message.from_user.id
    bot.send_message(message.chat.id,mm_status_text(uid),reply_markup=make_keyboard([["⚙️ Setup MM","🔘 MM ON/OFF"],["📊 MM Status","💵 Change Base"],["💲 Change M1","📈 Change Payout"],["🛑 Stop MM Today"],["🔙 Back","🏠 Main Menu"]]))


def _mm_trade_result(user_id, signal_id, result, mode=None):
    reset_user_daily_state(user_id)
    with DB_LOCK:
        conn=db()
        try:
            u=conn.execute("SELECT * FROM users WHERE user_id=?",(user_id,)).fetchone()
            if not u or not int(u["mm_enabled"] or 0) or int(u["mm_stop"] or 0): return
            mode=mode or (u["mm_current_mode"] or "BASE")
            amount=int(u["mm_m1_cents"] or 0) if mode=="M1" else int(u["mm_base_cents"] or 100)
            payout=max(0,float(u["mm_payout_percent"] or 85)/100)
            pnl=int(round(amount*payout)) if result in ("WIN","M1 WIN") else (-amount if result in ("LOSS","M1 LOSS") else 0)
            conn.execute("INSERT INTO mm_trades(user_id,signal_id,trade_mode,result,amount_cents,pnl_cents,created_at) VALUES(?,?,?,?,?,?,?)",(user_id,signal_id,mode,result,amount,pnl,utc_iso(now_utc())))
            if pnl>=0: conn.execute("UPDATE users SET mm_daily_win_cents=mm_daily_win_cents+? WHERE user_id=?",(pnl,user_id))
            else: conn.execute("UPDATE users SET mm_daily_loss_cents=mm_daily_loss_cents+? WHERE user_id=?",(-pnl,user_id))
            conn.execute("UPDATE users SET mm_trade_count=mm_trade_count+1 WHERE user_id=?",(user_id,))
            u=conn.execute("SELECT * FROM users WHERE user_id=?",(user_id,)).fetchone()
            if int(u["mm_loss_limit_cents"] or 0)>0 and int(u["mm_daily_loss_cents"] or 0)>=int(u["mm_loss_limit_cents"]):
                conn.execute("UPDATE users SET mm_stop=1 WHERE user_id=?",(user_id,))
            elif int(u["mm_target_cents"] or 0)>0 and int(u["mm_daily_win_cents"] or 0)>=int(u["mm_target_cents"]):
                conn.execute("UPDATE users SET mm_stop=1 WHERE user_id=?",(user_id,))
            else:
                # Base loss => M1. Base win => Base. M1 win/loss => Base.
                next_mode="M1" if (mode=="BASE" and result=="LOSS") else "BASE"
                conn.execute("UPDATE users SET mm_current_mode=? WHERE user_id=?",(next_mode,user_id))
            conn.commit()
        finally: conn.close()


def __clean_result_buttons_v2(signal_id,user_id):
    with DB_LOCK:
        conn=db()
        try: row=conn.execute("SELECT result FROM signal_user_results WHERE signal_id=? AND user_id=?",(signal_id,user_id)).fetchone()
        finally: conn.close()
    if row: return None
    reset_user_daily_state(user_id); u=get_user(user_id)
    mode=(u["mm_current_mode"] or "BASE") if u and int(u["mm_enabled"] or 0) and not int(u["mm_stop"] or 0) else "BASE"
    amount=int(u["mm_m1_cents"] or 0) if mode=="M1" else int(u["mm_base_cents"] or 100) if u else 100
    kb=types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton(f"💰 {mode} {money(amount)}",callback_data=f"mmnoop:{signal_id}"))
    if mode=="M1":
        kb.row(types.InlineKeyboardButton("✅ M1 WIN",callback_data=f"mmres:{signal_id}:M1:WIN"),types.InlineKeyboardButton("❌ M1 LOSS",callback_data=f"mmres:{signal_id}:M1:LOSS"),types.InlineKeyboardButton("⏭️ SKIP",callback_data=f"mmres:{signal_id}:M1:SKIP"))
    else:
        kb.row(types.InlineKeyboardButton("✅ WIN",callback_data=f"mmres:{signal_id}:BASE:WIN"),types.InlineKeyboardButton("❌ LOSS",callback_data=f"mmres:{signal_id}:BASE:LOSS"),types.InlineKeyboardButton("⏭️ SKIP",callback_data=f"mmres:{signal_id}:BASE:SKIP"))
    return kb


def __clean_handle_candle_photo_v2(message):
    uid=message.from_user.id
    st=STATES.get(uid)
    if not st or st.get("action")!="candle_upload": return False
    u=get_user(uid); reset_user_daily_state(uid); u=get_user(uid)
    if not candle_ai_enabled() and not is_master(uid):
        clear_state(uid); bot.send_message(message.chat.id,"🤖 AI Candle Analysis বর্তমানে OFF.",reply_markup=main_keyboard(uid)); return True
    vip=vip_is_active(u)
    limit=int(u["ai_limit_vip"] if vip else u["ai_limit_nonvip"]) if u else 3
    limit=max(1,limit)
    if int(u["ai_usage_count"] or 0)>=limit and not is_master(uid):
        bot.send_message(message.chat.id,f"⛔ আজকের AI limit শেষ।\n{'⭐ VIP' if vip else '👤 FREE'} limit: <b>{limit}/day</b>",reply_markup=main_keyboard(uid)); return True
    max_images=candle_ai_max_images(); images=st.setdefault("images",[])
    if len(images)>=max_images:
        bot.send_message(message.chat.id,f"⚠️ Maximum {max_images} screenshots reached. এখন 🔍 Analyze Candles চাপুন.",reply_markup=make_keyboard([["🔍 Analyze Candles","🗑️ Clear Candles"],["🔙 Back","🏠 Main Menu"]])); return True
    try:
        photo=message.photo[-1]; images.append(photo.file_id); _state_set(uid,st)
        bot.send_message(message.chat.id,f"✅ Screenshot {len(images)}/{max_images} added.",reply_markup=make_keyboard([["🔍 Analyze Candles","🗑️ Clear Candles"],["🔙 Back","🏠 Main Menu"]]))
    except Exception: logger.exception("AI photo upload failed")
    return True


def __clean_candle_ai_prompt_v2():
    return """You are analyzing a Quotex-style trading chart screenshot. Analyze ONLY visible evidence. Never invent indicators, prices, candles, volume, RSI, MACD, Fibonacci, Bollinger Bands, news, or time. If evidence is missing, say not visible. Check: (1) at least 20 visible candles, (2) timeframe if visible, (3) trend over ~20 candles, (4) momentum over last 5, (5) last 3 candle pattern, (6) support/resistance, (7) volume/RSI/MACD/Fibonacci/Bollinger only if visible, (8) confirmation using at least 2 independent visible signals. If fewer than 20 candles, blurry, cropped/unclear, text-only, or conflicting, output WAIT. Confidence must be evidence-based; never claim certainty. Output Bengali + English mix with: Chart Analysis, Trend, Candle Pattern, Support/Resistance, Momentum, Volatility, Signal UP/DOWN/WAIT, Confidence band, Next Candle Time if visible/inferable from visible clock/timeframe, Signal Time + Direction, Risk note. Strong UP requires multiple visible confirmations such as trend/support/bullish pattern. Strong DOWN requires multiple visible confirmations such as trend/resistance/bearish pattern. RSI/MACD/news may be used only when actually visible. Prediction is not a guarantee."""


def __clean_analyze_candle_state_v2(message):
    uid=message.from_user.id; st=STATES.get(uid)
    if not st or st.get("action")!="candle_upload": return False
    text=(message.text or "").strip()
    if text=="🗑️ Clear Candles": st["images"]=[]; _state_set(uid,st); bot.send_message(message.chat.id,"🗑️ Screenshots cleared.",reply_markup=make_keyboard([["🔍 Analyze Candles","🗑️ Clear Candles"],["🔙 Back","🏠 Main Menu"]])); return True
    if text!="🔍 Analyze Candles": return False
    file_ids=list(st.get("images") or [])
    if not file_ids: bot.send_message(message.chat.id,"📸 আগে screenshot upload করুন."); return True
    u=get_user(uid); reset_user_daily_state(uid); u=get_user(uid); vip=vip_is_active(u); limit=int(u["ai_limit_vip"] if vip else u["ai_limit_nonvip"])
    if int(u["ai_usage_count"] or 0)>=limit and not is_master(uid):
        clear_state(uid); bot.send_message(message.chat.id,"⛔ আজকের AI limit শেষ.",reply_markup=main_keyboard(uid)); return True
    bot.send_message(message.chat.id,"🔎 AI chart analysis চলছে…")
    try:
        raws=[]
        for fid in file_ids:
            info=bot.get_file(fid); raws.append(bot.download_file(info.file_path))
        result=gemini_analyze_candle_images(raws)
        status="OK" if result and not result.startswith("⚠️") else "FAILED"
        with DB_LOCK:
            conn=db()
            try:
                conn.execute("UPDATE users SET ai_usage_count=ai_usage_count+1,ai_usage_date=? WHERE user_id=?",(_daily_reset_key(),uid))
                conn.execute("INSERT INTO ai_usage_log(user_id,image_count,status,result_summary,created_at) VALUES(?,?,?,?,?)",(uid,len(raws),status,result[:1000],utc_iso(now_utc())))
                conn.commit()
            finally: conn.close()
    except Exception:
        logger.exception("AI analysis failed"); result="⚠️ ANALYSIS FAILED\n\n📸 Screenshot download/analyze করা যায়নি. Clear screenshot আবার পাঠান."; status="FAILED"
        try:
            with DB_LOCK:
                conn=db(); conn.execute("INSERT INTO ai_usage_log(user_id,image_count,status,result_summary,created_at) VALUES(?,?,?,?,?)",(uid,len(file_ids),status,result,utc_iso(now_utc()))); conn.commit(); conn.close()
        except Exception: pass
    clear_state(uid)
    u=get_user(uid); reset_user_daily_state(uid); u=get_user(uid); mode=u["mm_current_mode"] or "BASE"; amount=int(u["mm_m1_cents"] or 0) if mode=="M1" else int(u["mm_base_cents"] or 100)
    extra=f"\n\n━━━━━━━━━━━━━━━━━━\n💰 <b>Money Management</b>\n💵 Trade: <b>{money(amount)}</b>\n📈 WIN হলে: <b>+{money(int(round(amount*(float(u['mm_payout_percent'] or 85)/100))) )}</b>\n📉 LOSS হলে: <b>{'M1 '+money(u['mm_m1_cents']) if mode=='BASE' else 'Next BASE'}</b>" if int(u["mm_enabled"] or 0) else ""
    bot.send_message(message.chat.id,"🤖 <b>AI CANDLE ANALYSIS</b>\n\n"+result+extra+"\n\n⚠️ Prediction — guarantee নয়.",reply_markup=main_keyboard(uid)); return True


def ai_settings_menu(message):
    uid=message.from_user.id
    if not can(uid,"settings"): return bot.send_message(message.chat.id,"⛔ Access denied.",reply_markup=admin_keyboard())
    bot.send_message(message.chat.id,
        f"🤖 <b>AI SETTINGS</b>\n\nAI: <b>{get_setting('candle_ai_enabled','OFF')}</b>\nVIP limit: <b>{get_setting('candle_ai_vip_limit','50')}/day</b>\nNon-VIP limit: <b>{get_setting('candle_ai_nonvip_limit','3')}/day</b>\nMax screenshots: <b>{get_setting('candle_ai_max_images','3')}</b>\nConfidence min: <b>{get_setting('candle_ai_confidence_min','70')}%</b>",
        reply_markup=make_keyboard([["🤖 AI ON/OFF","⭐ VIP Limit"],["👤 Non-VIP Limit","🖼️ Max Screenshots"],["🎯 Confidence Min","📊 AI Usage Stats"],["🔙 Back","🏠 Main Menu"]]))


def _user_search(message, mode, value):
    value=value.strip(); row=None
    with DB_LOCK:
        conn=db()
        try:
            if mode=="ID" and value.isdigit(): row=conn.execute("SELECT * FROM users WHERE user_id=?",(int(value),)).fetchone()
            elif mode=="USERNAME": row=conn.execute("SELECT * FROM users WHERE lower(username)=lower(?)",(value.lstrip('@'),)).fetchone()
            elif mode=="NAME": row=conn.execute("SELECT * FROM users WHERE lower(first_name) LIKE lower(?) ORDER BY user_id DESC LIMIT 1",(f"%{value}%",)).fetchone()
        finally: conn.close()
    if not row: return bot.send_message(message.chat.id,"📭 User পাওয়া যায়নি.",reply_markup=admin_keyboard())
    _state_set(message.from_user.id,{"action":"user_action","target_id":row["user_id"]})
    bot.send_message(message.chat.id,f"👤 <b>USER DETAILS</b>\n\nID: <code>{row['user_id']}</code>\nUsername: @{escape(row['username'] or '—')}\nName: {escape(row['first_name'] or '—')}\nStatus: <b>{row['status']}</b>\nVIP Until: {escape(row['vip_until'] or 'Unlimited/—')}\nWallet: <b>{money(row['wallet_cents'])}</b>\nWarnings: <b>{row['warning_count']}</b>",reply_markup=make_keyboard([["⭐ VIP","🚫 Block"],["💳 Wallet","📩 Message"],["⚠️ Warn","♻️ Reset"],["👥 Referrals","📊 Stats"],["🔙 Back","🏠 Main Menu"]]))


def admin_category_keyboard():
    return make_keyboard([["📊 Signals","👥 Users"],["⭐ VIP","💰 Money"],["🎁 Referral","⚙️ Settings"],["📈 Analytics","📝 Content"],["🔙 Back","🏠 Main Menu"]])


def __clean_admin_keyboard_v3():
    return admin_category_keyboard()


def __clean_handle_admin_button_v3(message):
    text=(message.text or "").strip(); uid=message.from_user.id
    if text in ANALYSIS_ADMIN_BUTTONS: return ANALYSIS_ADMIN_BUTTONS[text](message)
    if text=="📊 Signals": return bot.send_message(message.chat.id,"📊 <b>SIGNALS</b>",reply_markup=make_keyboard([["➕ Add Future Signals","📋 Future Signal List"],["✏️ Edit Signal","🗑️ Delete Signal"],["🧹 Clear Future Signals","📤 Auto Send ON/OFF"],["🎯 Signal Audience","⚡ Live Session"],["🔙 Back","🏠 Main Menu"]]))
    if text=="👥 Users": return bot.send_message(message.chat.id,"👥 <b>USERS</b>",reply_markup=make_keyboard([["🔍 Search User","👥 All Users"],["🚫 Blocked Users","⚠️ Warning List"],["📩 Message User","📢 Broadcast"],["🔙 Back","🏠 Main Menu"]]))
    if text=="⭐ VIP": return bot.send_message(message.chat.id,"⭐ <b>VIP MANAGEMENT</b>",reply_markup=make_keyboard([["📋 VIP List","➕ Add VIP"],["⏰ Expiry Table","❌ Remove VIP"],["📢 Send Reminder","🆔 Pending UID"],["🔙 Back","🏠 Main Menu"]]))
    if text=="💰 Money": return bot.send_message(message.chat.id,"💰 <b>MONEY</b>",reply_markup=make_keyboard([["💸 Withdrawals","📊 Withdrawal Reports"],["💳 Wallet Adjust","💰 Referral History"],["🔙 Back","🏠 Main Menu"]]))
    if text=="🎁 Referral": return bot.send_message(message.chat.id,"🎁 <b>REFERRAL</b>",reply_markup=make_keyboard([["📋 Pending Referral","✅ Approved Referral"],["❌ Rejected Referral","⚠️ Referral Warning"],["🚫 Blocked Referral","💰 Referral History"],["🔙 Back","🏠 Main Menu"]]))
    if text=="⚙️ Settings": return bot.send_message(message.chat.id,"⚙️ <b>SETTINGS</b>",reply_markup=make_keyboard([["🛠️ Maintenance ON/OFF","⚡ Live ON/OFF"],["🎟️ Set Free Limit","💵 Set Min Withdraw"],["💸 Withdraw ON/OFF","🔔 Auto Notification ON/OFF"],["🤖 AI Settings","🛡️ Sub-admins"],["🎯 Notify Targets","⚙️ Trading Contract"],["🔙 Back","🏠 Main Menu"]]))
    if text=="📈 Analytics": return bot.send_message(message.chat.id,"📈 <b>ANALYTICS</b>",reply_markup=make_keyboard([["📊 Dashboard","📈 Result Stats"],["👥 User Stats","💰 Revenue"],["🤖 AI Usage Stats","🔙 Back"]]))
    if text=="📝 Content": return admin_text_editor(message)
    if text=="🤖 AI Settings": return ai_settings_menu(message)
    if text=="🤖 AI ON/OFF":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        old=get_setting("candle_ai_enabled","OFF"); new="OFF" if old=="ON" else "ON"; set_setting("candle_ai_enabled",new)
        return bot.send_message(message.chat.id,f"🤖 AI: <b>{new}</b>",reply_markup=admin_keyboard())
    if text=="⭐ VIP Limit":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        _state_set(uid,{"action":"set_ai_vip_limit"}); return bot.send_message(message.chat.id,"⭐ VIP AI daily limit দিন. Example: 50",reply_markup=back_keyboard())
    if text=="👤 Non-VIP Limit":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        _state_set(uid,{"action":"set_ai_nonvip_limit"}); return bot.send_message(message.chat.id,"👤 Non-VIP AI daily limit দিন. Example: 3",reply_markup=back_keyboard())
    if text=="🖼️ Max Screenshots":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        _state_set(uid,{"action":"set_candle_max_images"}); return bot.send_message(message.chat.id,"🖼️ Max screenshots দিন (1–10).",reply_markup=back_keyboard())
    if text=="🎯 Confidence Min":
        if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
        _state_set(uid,{"action":"set_ai_confidence"}); return bot.send_message(message.chat.id,"🎯 Minimum confidence % দিন. Example: 70",reply_markup=back_keyboard())
    if text=="📊 AI Usage Stats": return _ai_usage_stats(message)
    if text=="🔍 Search User": _state_set(uid,{"action":"user_search_mode"}); return bot.send_message(message.chat.id,"Search by:",reply_markup=make_keyboard([["🆔 By ID","👤 By Username"],["📝 By Name"],["🔙 Back","🏠 Main Menu"]]))
    if text=="⚠️ Warning List": return _warning_list(message)
    if text=="👥 All Users": return _all_users(message)
    if text=="🚫 Blocked Users": return _blocked_users(message)
    if text=="📋 VIP List": return _vip_list(message)
    if text=="⏰ Expiry Table": return _vip_expiry_table(message)
    if text=="➕ Add VIP": _state_set(uid,{"action":"vip_add_id","previous_menu":"admin_vip"}); return bot.send_message(message.chat.id,"🆔 Telegram ID অথবা @username লিখুন:",reply_markup=back_keyboard())
    if text=="❌ Remove VIP": _state_set(uid,{"action":"vip_remove_id"}); return bot.send_message(message.chat.id,"Telegram ID লিখুন:",reply_markup=back_keyboard())
    if text=="📢 Send Reminder": return _vip_expiry_table(message,reminder=True)
    if text=="📋 Pending Referral": return _referral_review_list(message,"PENDING")
    if text=="✅ Approved Referral": return _referral_review_list(message,"PAID")
    if text=="❌ Rejected Referral": return _referral_review_list(message,"REJECTED")
    if text=="⚠️ Referral Warning": _state_set(uid,{"action":"referral_warn_id"}); return bot.send_message(message.chat.id,"Referral user Telegram ID দিন:",reply_markup=back_keyboard())
    if text=="🚫 Blocked Referral": return _referral_review_list(message,"BLOCKED")
    if text=="💰 Referral History": return admin_referral_history(message)
    if text=="📊 Dashboard": return admin_analytics(message)
    if text=="👥 User Stats": return _all_users(message,stats=True)
    if text=="💰 Revenue": return admin_withdrawal_report(message)
    if text=="🤖 AI Usage Stats": return _ai_usage_stats(message)
    # legacy submenu buttons remain available (v2 adds permission checks)
    return __clean_handle_admin_button_v2(message)


def _all_users(message,stats=False):
    if not can(message.from_user.id,"users"): return bot.send_message(message.chat.id,"⛔ Access denied.",reply_markup=admin_keyboard())
    with DB_LOCK:
        conn=db()
        try: rows=conn.execute("SELECT user_id,username,first_name,status,wallet_cents,warning_count,vip_until FROM users ORDER BY user_id DESC LIMIT 30").fetchall()
        finally: conn.close()
    lines=["👥 <b>USERS</b>"]
    for r in rows: lines.append(f"<code>{r['user_id']}</code> @{escape(r['username'] or '—')} | {r['status']} | {money(r['wallet_cents'])} | W:{r['warning_count']}")
    bot.send_message(message.chat.id,"\n".join(lines),reply_markup=admin_keyboard())


def _blocked_users(message):
    with DB_LOCK:
        conn=db()
        try: rows=conn.execute("SELECT user_id,username,first_name,warning_count FROM users WHERE blocked=1 OR status='BLOCKED' ORDER BY user_id DESC").fetchall()
        finally: conn.close()
    msg="🚫 <b>BLOCKED USERS</b>\n\n"+"\n".join(f"<code>{r['user_id']}</code> @{escape(r['username'] or '—')} | W:{r['warning_count']}" for r in rows) if rows else "🚫 No blocked users."
    bot.send_message(message.chat.id,msg,reply_markup=admin_keyboard())


def _warning_list(message):
    with DB_LOCK:
        conn=db()
        try: rows=conn.execute("SELECT user_id,username,warning_count,warning_reason FROM users WHERE warning_count>0 ORDER BY warning_count DESC LIMIT 30").fetchall()
        finally: conn.close()
    msg="⚠️ <b>WARNING LIST</b>\n\n"+"\n".join(f"<code>{r['user_id']}</code> @{escape(r['username'] or '—')} | {r['warning_count']}/3 | {escape(r['warning_reason'] or '')}" for r in rows) if rows else "⚠️ No warnings."
    bot.send_message(message.chat.id,msg,reply_markup=admin_keyboard())


def _vip_list(message):
    with DB_LOCK:
        conn=db()
        try: rows=conn.execute("SELECT user_id,username,first_name,vip_until FROM users WHERE status='VIP' ORDER BY vip_until IS NULL DESC,vip_until ASC LIMIT 50").fetchall()
        finally: conn.close()
    msg="⭐ <b>VIP LIST</b>\n\n"+"\n".join(f"<code>{r['user_id']}</code> @{escape(r['username'] or '—')} | {escape(r['vip_until'] or 'Unlimited')}" for r in rows) if rows else "⭐ No VIP users."
    bot.send_message(message.chat.id,msg,reply_markup=admin_keyboard())



def _resolve_user_ref(raw):
    """Accepts Telegram ID or @username; returns int user_id or raises ValueError."""
    raw = str(raw or "").strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    name = raw.lstrip("@").strip().lower()
    if not name or not re.fullmatch(r"[a-z0-9_]{3,32}", name):
        raise ValueError("Telegram ID অথবা @username দিন.")
    with DB_LOCK:
        conn = db()
        try:
            row = conn.execute("SELECT user_id FROM users WHERE lower(username)=?", (name,)).fetchone()
        finally:
            conn.close()
    if not row:
        raise ValueError(f"@{name} নামের কোনো user পাওয়া যায়নি (user-কে আগে bot-এ /start দিতে হবে).")
    return int(row["user_id"])


def _fmt_dt(iso, default="—"):
    """ISO timestamp -> '08 Oct 2026 01:42 PM' (BD time)."""
    if not iso:
        return default
    try:
        dt = datetime.fromisoformat(str(iso))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(BD_TZ).strftime("%d %b %Y %I:%M %p")
    except Exception:
        return str(iso)[:16]


def _vip_expiry_table(message,reminder=False):
    with DB_LOCK:
        conn=db()
        try: rows=conn.execute("SELECT user_id,username,vip_until FROM users WHERE status='VIP' AND vip_until IS NOT NULL ORDER BY vip_until ASC LIMIT 50").fetchall()
        finally: conn.close()
    now=now_bd(); lines=["⏰ <b>VIP EXPIRY TABLE</b>"]
    for r in rows:
        try: days=max(0,int((datetime.fromisoformat(r['vip_until']).astimezone(BD_TZ)-now).total_seconds()/86400))
        except Exception: days=-1
        lines.append(f"<code>{r['user_id']}</code> @{escape(r['username'] or '—')} | {escape(_fmt_dt(r['vip_until']))} | {days} days")
        if reminder and days<=7:
            try: bot.send_message(r['user_id'],f"📢 VIP reminder\n⏰ Expires: <b>{escape(_fmt_dt(r['vip_until']))}</b>")
            except Exception: pass
    bot.send_message(message.chat.id,"\n".join(lines) if len(lines)>1 else "⏰ No expiring VIP.",reply_markup=admin_keyboard())


def _referral_review_list(message,status):
    with DB_LOCK:
        conn=db()
        try: rows=conn.execute("SELECT * FROM referrals WHERE status=? ORDER BY id DESC LIMIT 20",(status,)).fetchall()
        finally: conn.close()
    if not rows: return bot.send_message(message.chat.id,f"🎁 No {status} referrals.",reply_markup=admin_keyboard())
    for r in rows:
        kb=types.InlineKeyboardMarkup()
        if status in ("PENDING","QUALIFIED"):
            kb.row(types.InlineKeyboardButton("✅ Approve",callback_data=f"refapprove:{r['id']}"),types.InlineKeyboardButton("❌ Reject",callback_data=f"refreject:{r['id']}"),types.InlineKeyboardButton("⚠️ Warn",callback_data=f"refwarn:{r['id']}"))
        bot.send_message(message.chat.id,f"🎁 <b>Referral #{r['id']}</b>\nReferrer: <code>{r['referrer_id']}</code>\nUser: <code>{r['referred_id']}</code>\nUID: <code>{escape(r['quotex_uid'] or '—')}</code>\nDeposit attested: {money(r['deposit_amount_cents'])}\nStatus: <b>{r['status']}</b>\nRisk: {r['risk_flag']} {escape(r['risk_note'] or '')}",reply_markup=kb if status in ("PENDING","QUALIFIED") else None)


def _ai_usage_stats(message):
    with DB_LOCK:
        conn=db()
        try:
            a=conn.execute("SELECT COUNT(*) n,COALESCE(SUM(image_count),0) images FROM ai_usage_log WHERE date(created_at)=date('now')").fetchone()
            rows=conn.execute("SELECT user_id,COUNT(*) n FROM ai_usage_log GROUP BY user_id ORDER BY n DESC LIMIT 10").fetchall()
        finally: conn.close()
    lines=[f"🤖 <b>AI USAGE</b>\nToday: {a['n']} analyses / {a['images']} screenshots",""]
    lines += [f"<code>{r['user_id']}</code> — {r['n']} analyses" for r in rows]
    bot.send_message(message.chat.id,"\n".join(lines),reply_markup=admin_keyboard())


# ------------------------- State override ---------------------
_LEGACY_FINAL_STATE = __clean_handle_state_v2  # v2 covers withdraw/text/candle/subadmin states, then falls back to legacy

def __clean_handle_state_v3(message):
    uid=message.from_user.id; text=(message.text or "").strip(); st=STATES.get(uid)
    if st: _state_set(uid,st); st=STATES.get(uid)
    if text in ("🔙 Back","🏠 Main Menu","❌ Cancel") and st:
        clear_state(uid); send_main_menu(message.chat.id,uid,"🏠 Main Menu"); return True
    if not st: return _LEGACY_FINAL_STATE(message)
    try:
        action=st.get("action")
        _ar=_analysis_rules_state(message,st)
        if _ar is not None: return True
        if action=="uid_confirm":
            if text=="🔗 Register on Quotex":
                link=get_setting("quotex_ref_link",os.getenv("QUOTEX_REF_LINK","").strip())
                if link: bot.send_message(message.chat.id,f"🔗 <b>Register on Quotex</b>\n{escape(link)}")
                return True
            if text=="✅ সব ঠিক আছে": _record_uid_confirmation(uid); return _submit_uid_after_confirm(message)
            if text=="❌ Cancel": clear_state(uid); bot.send_message(message.chat.id,"❌ Cancelled.",reply_markup=main_keyboard(uid)); return True
            raise ValueError("Confirmation button ব্যবহার করুন.")
        if action=="user_search_mode":
            if text=="🆔 By ID": _state_set(uid,{"action":"user_search_value","mode":"ID"})
            elif text=="👤 By Username": _state_set(uid,{"action":"user_search_value","mode":"USERNAME"})
            elif text=="📝 By Name": _state_set(uid,{"action":"user_search_value","mode":"NAME"})
            else: raise ValueError("Search option বেছে নিন.")
            bot.send_message(message.chat.id,"Search value দিন:",reply_markup=back_keyboard()); return True
        if action=="user_search_value": _user_search(message,st["mode"],text); return True
        if action=="user_action":
            tid=int(st["target_id"])
            if text=="🚫 Block":
                with DB_LOCK:
                    conn=db(); conn.execute("UPDATE users SET blocked=1,status='BLOCKED' WHERE user_id=?",(tid,)); conn.commit(); conn.close()
                admin_audit(uid,"BLOCK",tid); clear_state(uid); return bot.send_message(message.chat.id,"🚫 User blocked.",reply_markup=admin_keyboard())
            if text=="⚠️ Warn": _state_set(uid,{"action":"warn_target_reason","target_id":tid}); return bot.send_message(message.chat.id,"Warning reason লিখুন:",reply_markup=back_keyboard())
            if text=="♻️ Reset":
                with DB_LOCK:
                    conn=db(); conn.execute("UPDATE users SET blocked=0,status='FREE',warning_count=0,warning_reason=NULL WHERE user_id=?",(tid,)); conn.commit(); conn.close()
                admin_audit(uid,"USER_RESET",tid); clear_state(uid); return bot.send_message(message.chat.id,"♻️ User reset.",reply_markup=admin_keyboard())
            if text=="📩 Message": _state_set(uid,{"action":"message_user","target_id":tid}); return bot.send_message(message.chat.id,"Message লিখুন:",reply_markup=back_keyboard())
            if text=="⭐ VIP": _state_set(uid,{"action":"vip_add_id","target_id":tid}); return bot.send_message(message.chat.id,"VIP duration নির্বাচন করুন:",reply_markup=make_keyboard([["7 Days","15 Days","30 Days"],["60 Days","90 Days","♾️ Unlimited"],["🔙 Back","🏠 Main Menu"]]))
            return True
        if action=="warn_target_reason":
            warn_user(int(st["target_id"]),text,uid); clear_state(uid); return bot.send_message(message.chat.id,"⚠️ Warning added.",reply_markup=admin_keyboard())
        if action=="message_user":
            tid=int(st["target_id"]); bot.send_message(tid,text); 
            with DB_LOCK:
                conn=db(); conn.execute("INSERT INTO user_messages(user_id,admin_id,message,created_at) VALUES(?,?,?,?)",(tid,uid,text,utc_iso(now_utc()))); conn.commit(); conn.close()
            clear_state(uid); return bot.send_message(message.chat.id,"📩 Message sent.",reply_markup=admin_keyboard())
        if action=="referral_warn_id":
            warn_user(int(text),"Admin referral review warning",uid); clear_state(uid); return bot.send_message(message.chat.id,"⚠️ Warning added.",reply_markup=admin_keyboard())
        if action=="vip_add_id":
            if not st.get("target_id"):
                try:
                    target=_resolve_user_ref(text)
                except ValueError as exc:
                    return bot.send_message(message.chat.id,f"❌ {escape(str(exc))}\nআবার দিন বা 🔙 Back চাপুন.",reply_markup=back_keyboard())
                st["target_id"]=target; _state_set(uid,st)
                return bot.send_message(message.chat.id,f"⭐ User: <code>{target}</code>\n\nDuration বেছে নিন:",reply_markup=make_keyboard([["7 Days","15 Days","30 Days"],["60 Days","90 Days","♾️ Unlimited"],["🔙 Back","🏠 Main Menu"]]))
            target=int(st["target_id"])
            if text in ("7 Days","15 Days","30 Days","60 Days","90 Days","♾️ Unlimited"):
                days={"7 Days":7,"15 Days":15,"30 Days":30,"60 Days":60,"90 Days":90,"♾️ Unlimited":0}[text]
                with DB_LOCK:
                    conn=db(); u=conn.execute("SELECT vip_until FROM users WHERE user_id=?",(target,)).fetchone()
                    if not u:
                        conn.close(); clear_state(uid)
                        return bot.send_message(message.chat.id,"❌ এই user bot-এ register করা নেই (আগে /start দিতে হবে).",reply_markup=admin_vip_kb())
                    old=u["vip_until"]
                    new=None if days==0 else (now_bd()+timedelta(days=days)).isoformat()
                    conn.execute("UPDATE users SET status='VIP',vip_until=?,vip_started_at=?,vip_reminder_7d_sent=0,vip_reminder_3d_sent=0 WHERE user_id=?",(new,utc_iso(now_utc()),target))
                    conn.execute("INSERT INTO vip_history(user_id,action,days,old_until,new_until,admin_id,created_at) VALUES(?,?,?,?,?,?,?)",(target,"GRANT",days,old,new,uid,utc_iso(now_utc()))); conn.commit(); conn.close()
                _qualify_referral_after_vip(target,uid); admin_audit(uid,"VIP_GRANT",target,text); clear_state(uid)
                label="Unlimited" if days==0 else f"{days} days"; exp="Unlimited" if not new else _fmt_dt(new)
                try: bot.send_message(target,f"⭐ <b>VIP হয়েছেন!</b>\n📅 Duration: <b>{label}</b>\n⏰ Expires: <b>{exp}</b>",reply_markup=main_keyboard(target))
                except Exception: pass
                release_referral_bonuses(); return bot.send_message(message.chat.id,"✅ VIP updated.",reply_markup=admin_vip_kb())
            raise ValueError("VIP duration button ব্যবহার করুন.")
        if action=="vip_remove_id":
            target=_resolve_user_ref(text)
            with DB_LOCK:
                conn=db(); conn.execute("UPDATE users SET status='FREE',vip_until=NULL WHERE user_id=?",(target,)); conn.execute("INSERT INTO vip_history(user_id,action,admin_id,created_at) VALUES(?,?,?,?)",(target,"REMOVE",uid,utc_iso(now_utc()))); conn.commit(); conn.close()
            admin_audit(uid,"VIP_REMOVE",target); clear_state(uid); return bot.send_message(message.chat.id,"❌ VIP removed.",reply_markup=admin_keyboard())
        if action=="set_ai_vip_limit":
            n=max(1,min(500,int(text))); set_setting("candle_ai_vip_limit",n)
            with DB_LOCK:
                conn=db(); conn.execute("UPDATE users SET ai_limit_vip=?",(n,)); conn.commit(); conn.close()
            clear_state(uid); return bot.send_message(message.chat.id,f"⭐ VIP AI limit: {n}/day",reply_markup=admin_keyboard())
        if action=="set_ai_nonvip_limit":
            n=max(1,min(100,int(text))); set_setting("candle_ai_nonvip_limit",n)
            with DB_LOCK:
                conn=db(); conn.execute("UPDATE users SET ai_limit_nonvip=?",(n,)); conn.commit(); conn.close()
            clear_state(uid); return bot.send_message(message.chat.id,f"👤 Non-VIP AI limit: {n}/day",reply_markup=admin_keyboard())
        if action=="set_ai_confidence":
            n=max(50,min(99,int(text))); set_setting("candle_ai_confidence_min",n); clear_state(uid); return bot.send_message(message.chat.id,f"🎯 Minimum confidence: {n}%",reply_markup=admin_keyboard())
        if action=="set_candle_max_images":
            n=max(1,min(10,int(text))); set_setting("candle_ai_max_images",n); clear_state(uid); return bot.send_message(message.chat.id,f"🖼️ Max screenshots: {n}",reply_markup=admin_keyboard())
        if action=="mm_setup":
            if "balance" not in st: st["balance"]=float(text.replace("$","")); st["step"]=1; _state_set(uid,st); return bot.send_message(message.chat.id,"🎯 Daily Win Target কত? Example: 30",reply_markup=back_keyboard())
            if st.get("step")==1: st["target"]=float(text.replace("$","")); st["step"]=2; _state_set(uid,st); return bot.send_message(message.chat.id,"🛑 Daily Loss Limit কত? Example: 20",reply_markup=back_keyboard())
            st["loss"]=float(text.replace("$","")); base,m1=mm_configure(uid,st["balance"],st["target"],st["loss"],float(get_setting("mm_payout_percent","85"))); clear_state(uid); return bot.send_message(message.chat.id,f"✅ MM configured.\n💵 Base: <b>{money(base)}</b>\n💲 M1: <b>{money(m1)}</b>\n📈 Payout: <b>{get_setting('mm_payout_percent','85')}%</b>",reply_markup=mm_keyboard())
        if action=="mm_set_base":
            n=max(1.0,float(text.replace("$",""))); with_conn=db();
            try: with_conn.execute("UPDATE users SET mm_base_cents=? WHERE user_id=?",(int(round(n*100)),uid)); with_conn.commit()
            finally: with_conn.close()
            clear_state(uid); return bot.send_message(message.chat.id,"✅ Base updated.",reply_markup=mm_keyboard())
        if action=="mm_set_m1":
            n=max(1.0,float(text.replace("$",""))); with_conn=db();
            try: with_conn.execute("UPDATE users SET mm_m1_cents=? WHERE user_id=?",(int(round(n*100)),uid)); with_conn.commit()
            finally: with_conn.close()
            clear_state(uid); return bot.send_message(message.chat.id,"✅ M1 updated.",reply_markup=mm_keyboard())
        if action=="mm_set_payout":
            n=max(1,min(100,float(text))); with_conn=db();
            try: with_conn.execute("UPDATE users SET mm_payout_percent=? WHERE user_id=?",(n,uid)); with_conn.commit()
            finally: with_conn.close()
            clear_state(uid); return bot.send_message(message.chat.id,"✅ Payout updated.",reply_markup=mm_keyboard())
        return _LEGACY_FINAL_STATE(message)
    except Exception as exc:
        logger.exception("Final state handler error")
        bot.send_message(message.chat.id,"❌ <b>Error</b>\n\n"+escape(str(exc)),reply_markup=back_keyboard()); return True


# ------------------------- Admin callback additions -----------
@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("refapprove:"))
def final_refapprove(call):
    if not can(call.from_user.id,"vip"):
        bot.answer_callback_query(call.id,"Access denied",show_alert=True); return
    rid=int(call.data.split(":",1)[1])
    with DB_LOCK:
        conn=db()
        try:
            r=conn.execute("SELECT * FROM referrals WHERE id=? AND status IN ('PENDING','QUALIFIED')",(rid,)).fetchone()
            if not r: bot.answer_callback_query(call.id,"Referral not pending",show_alert=True); return
            conn.execute("UPDATE referrals SET status='QUALIFIED',qualified_at=?,reviewed_by=?,reviewed_at=? WHERE id=?",(utc_iso(now_utc()),call.from_user.id,utc_iso(now_utc()),rid)); conn.commit()
        finally: conn.close()
    release_referral_bonuses(); admin_audit(call.from_user.id,"REFERRAL_APPROVE",rid); bot.answer_callback_query(call.id,"Approved");
    try: bot.send_message(r["referred_id"],"✅ Referral review completed: <b>APPROVED</b>")
    except Exception: pass
    try: bot.send_message(r["referrer_id"],f"💰 Referral #{rid} approved and bonus released: <b>{money(r['bonus_cents'])}</b>")
    except Exception: pass
    try: bot.edit_message_reply_markup(call.message.chat.id,call.message.message_id,reply_markup=None)
    except Exception: pass


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("refreject:"))
def final_refreject(call):
    if not can(call.from_user.id,"vip"):
        bot.answer_callback_query(call.id,"Access denied",show_alert=True); return
    rid=int(call.data.split(":",1)[1]);
    kb=types.InlineKeyboardMarkup(); reasons=[("💰 Deposit করা হয়নি","deposit"),("🆔 UID পাওয়া যায়নি","uid"),("📊 Trade করা হয়নি","trade"),("🔗 Link দিয়ে account করা হয়নি","link"),("⚠️ Fake Proof","fake"),("✏️ Custom Reason","custom")]
    for i in range(0,len(reasons),2): kb.row(*[types.InlineKeyboardButton(reasons[j][0],callback_data=f"refreason:{rid}:{reasons[j][1]}") for j in range(i,min(i+2,len(reasons)))])
    bot.answer_callback_query(call.id); bot.send_message(call.message.chat.id,"❌ <b>Reject Reason</b>",reply_markup=kb)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("refwarn:"))
def final_refwarn(call):
    if not can(call.from_user.id,"vip"):
        bot.answer_callback_query(call.id,"Access denied",show_alert=True); return
    rid=int(call.data.split(":",1)[1])
    with DB_LOCK:
        conn=db();
        try: r=conn.execute("SELECT referred_id FROM referrals WHERE id=?",(rid,)).fetchone()
        finally: conn.close()
    if r: warn_user(r["referred_id"],"Referral manual review warning",call.from_user.id,rid,"REFERRAL")
    bot.answer_callback_query(call.id,"Warning added")


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("refreason:"))
def final_refreason(call):
    if not can(call.from_user.id,"vip"):
        bot.answer_callback_query(call.id,"Access denied",show_alert=True); return
    _,rid,reason=call.data.split(":",2); rid=int(rid)
    labels={"deposit":"Deposit করা হয়নি","uid":"UID পাওয়া যায়নি","trade":"Trade করা হয়নি","link":"Link দিয়ে account করা হয়নি","fake":"Fake Proof"}
    if reason=="custom":
        _state_set(call.from_user.id,{"action":"ref_custom_reason","referral_id":rid}); bot.answer_callback_query(call.id); bot.send_message(call.message.chat.id,"Custom reject reason লিখুন:",reply_markup=back_keyboard()); return
    _reject_referral(rid,labels.get(reason,reason),call.from_user.id,reason); bot.answer_callback_query(call.id,"Rejected")


def _reject_referral(rid,reason,admin_id,reason_type="custom"):
    with DB_LOCK:
        conn=db()
        try:
            r=conn.execute("SELECT * FROM referrals WHERE id=?",(rid,)).fetchone()
            if not r: return
            conn.execute("UPDATE referrals SET status='REJECTED',reject_reason=?,reject_reason_type=?,rejected_by=?,rejected_at=?,reviewed_by=?,reviewed_at=? WHERE id=?",(reason,reason_type,admin_id,utc_iso(now_utc()),admin_id,utc_iso(now_utc()),rid)); conn.commit()
        finally: conn.close()
    admin_audit(admin_id,"REFERRAL_REJECT",rid,reason)
    for target in (r["referred_id"],r["referrer_id"]):
        try: bot.send_message(target,f"❌ <b>Referral #{rid} rejected</b>\nReason: {escape(reason)}")
        except Exception: pass


# Extend handle_state with custom referral reason by wrapping the final handler.
def __clean_handle_state_v4(message):
    uid = message.from_user.id
    st = STATES.get(uid)
    if st and st.get("action") == "ref_custom_reason":
        try:
            _reject_referral(int(st["referral_id"]), message.text or "Custom reason", uid, "custom")
            clear_state(uid)
            bot.send_message(message.chat.id, "❌ Referral rejected.", reply_markup=admin_keyboard())
            return True
        except Exception as exc:
            logger.exception("Referral custom reason failed")
            clear_state(uid)
            bot.send_message(message.chat.id, "❌ " + escape(str(exc)), reply_markup=admin_keyboard())
            return True
    return __clean_handle_state_v3(message)


def handle_state(message):
    """Single public state dispatcher."""
    try:
        return __clean_handle_state_v4(message)
    except Exception:
        logger.exception("State handler crashed; clearing user state")
        try:
            clear_state(message.from_user.id)
            bot.send_message(message.chat.id, "❌ Request process করা যায়নি. আবার চেষ্টা করুন.", reply_markup=main_keyboard(message.from_user.id))
        except Exception:
            pass
        return True


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("mmres:"))
def final_mm_result(call):
    try:
        _,sid_s,mode,res=call.data.split(":",3); sid=int(sid_s); uid=call.from_user.id
        if mode not in ("BASE","M1") or res not in ("WIN","LOSS","SKIP"): raise ValueError("Invalid result")
        with DB_LOCK:
            conn=db()
            try:
                if mode=="BASE":
                    if conn.execute("SELECT 1 FROM signal_user_results WHERE signal_id=? AND user_id=?",(sid,uid)).fetchone():
                        bot.answer_callback_query(call.id,"Already submitted.",show_alert=True); return
                    conn.execute("INSERT INTO signal_user_results(signal_id,user_id,result,created_at) VALUES(?,?,?,?)",(sid,uid,res,utc_iso(now_utc())))
                    conn.commit()
            finally: conn.close()
        if mode=="BASE" and res in ("WIN","LOSS"):
            _mm_trade_result(uid,sid,res,"BASE")
        if mode=="BASE" and res=="LOSS":
            u=get_user(uid); reset_user_daily_state(uid); u=get_user(uid)
            if u and int(u["mm_enabled"] or 0) and not int(u["mm_stop"] or 0):
                m1=int(u["mm_m1_cents"] or 0)
                kb=types.InlineKeyboardMarkup(); kb.row(types.InlineKeyboardButton(f"💵 M1 {money(m1)}",callback_data=f"mmnoop:{sid}")); kb.row(types.InlineKeyboardButton("✅ M1 WIN",callback_data=f"mmres:{sid}:M1:WIN"),types.InlineKeyboardButton("❌ M1 LOSS",callback_data=f"mmres:{sid}:M1:LOSS"),types.InlineKeyboardButton("⏭️ SKIP",callback_data=f"mmres:{sid}:M1:SKIP"))
                bot.edit_message_reply_markup(call.message.chat.id,call.message.message_id,reply_markup=kb)
            else:
                bot.edit_message_reply_markup(call.message.chat.id,call.message.message_id,reply_markup=None)
        elif mode=="BASE":
            bot.edit_message_reply_markup(call.message.chat.id,call.message.message_id,reply_markup=None)
        else:
            if res in ("WIN","LOSS"): _mm_trade_result(uid,sid,"M1 WIN" if res=="WIN" else "M1 LOSS","M1")
            bot.edit_message_reply_markup(call.message.chat.id,call.message.message_id,reply_markup=None)
        bot.answer_callback_query(call.id,"Result saved")
        u=get_user(uid)
        try:
            bot.send_message(uid, f"📊 Result: <b>{'M1 ' if mode=='M1' else ''}{res}</b>\n➡️ Next: <b>{u['mm_current_mode'] if u else 'BASE'}</b>")
        except Exception: pass
    except Exception as exc:
        logger.exception("MM result callback failed"); bot.answer_callback_query(call.id,"Error: "+str(exc),show_alert=True)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("mmnoop:"))
def final_mmnoop(call):
    bot.answer_callback_query(call.id,"Use WIN / LOSS / SKIP buttons below.")


# Replace the old signal result callback by adding M1 callbacks via a new
# dedicated prefix. Existing sigres remains backward-compatible for Base.
@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("m1res:"))
def final_m1_result(call):
    try:
        _,sid_s,res=call.data.split(":",2); sid=int(sid_s); uid=call.from_user.id
        if res not in ("WIN","LOSS","SKIP"): raise ValueError("Invalid result")
        _mm_trade_result(uid,sid,"M1 WIN" if res=="WIN" else "M1 LOSS" if res=="LOSS" else "SKIP","M1")
        bot.answer_callback_query(call.id,"M1 result saved")
        bot.edit_message_reply_markup(call.message.chat.id,call.message.message_id,reply_markup=None)
    except Exception as exc: bot.answer_callback_query(call.id,str(exc),show_alert=True)


# Add a safe wrapper for the old sigres function's post-processing by
# replacing its callable name is not enough for registered handlers, so patch
# the function body behavior through a small helper used by future deliveries.
def _base_result_markup(signal_id,user_id):
    with DB_LOCK:
        conn=db()
        try: row=conn.execute("SELECT result FROM signal_user_results WHERE signal_id=? AND user_id=?",(signal_id,user_id)).fetchone()
        finally: conn.close()
    if row: return None
    kb=types.InlineKeyboardMarkup(); kb.row(types.InlineKeyboardButton("✅ WIN",callback_data=f"sigres:{signal_id}:WIN"),types.InlineKeyboardButton("❌ LOSS",callback_data=f"sigres:{signal_id}:LOSS"),types.InlineKeyboardButton("⏭️ SKIP",callback_data=f"sigres:{signal_id}:SKIP")); return kb


def patched_format_signal_final(signal):
    signal_datetime=bd_from_iso(signal["signal_at_utc"]); up=signal["direction"]=="UP"; icon="🟢⬆️" if up else "🔴⬇️"; label="UP / BUY" if up else "DOWN / SELL / PUT"
    return ("━━━━━━━━━━━━━━━━━━\n🚨 <b>SM QUATEX SURE SHORT</b>\n━━━━━━━━━━━━━━━━━━\n\n"+f"📅 <b>{signal_datetime.strftime('%d %B %Y')}</b>\n💱 Pair: <b>{escape(signal['pair'])}</b>\n⏰ Time: <b>{signal_datetime.strftime('%I:%M %p')}</b>\n{icon} Direction: <b>{label}</b>\n🎯 Confidence: <b>{escape(signal['confidence'])}</b>\n\n━━━━━━━━━━━━━━━━━━")

format_signal=patched_format_signal_final


def patched_deliver_signal_final(user_id, signal_id, source="manual"):
    signal=get_signal(signal_id)
    if not signal or not signal["active"] or not audience_allows(signal,user_id) or not quota_available(user_id): return False,"not_allowed"
    with DB_LOCK:
        conn=db()
        try:
            if conn.execute("SELECT 1 FROM deliveries WHERE signal_id=? AND user_id=?",(signal_id,user_id)).fetchone(): return False,"already"
            conn.execute("INSERT INTO deliveries(signal_id,user_id,delivered_at,source) VALUES(?,?,?,?)",(signal_id,user_id,utc_iso(now_utc()),source))
            u=conn.execute("SELECT status FROM users WHERE user_id=?",(user_id,)).fetchone()
            if u and u["status"]!="VIP": conn.execute("UPDATE users SET free_used=free_used+1 WHERE user_id=?",(user_id,))
            conn.commit()
        finally: conn.close()
    try:
        reset_user_daily_state(user_id); u=get_user(user_id)
        text=patched_format_signal_final(signal)
        if u and int(u["mm_enabled"] or 0) and not int(u["mm_stop"] or 0):
            mode=u["mm_current_mode"] or "BASE"; amount=int(u["mm_m1_cents"] or 0) if mode=="M1" else int(u["mm_base_cents"] or 100); profit=int(round(amount*float(u["mm_payout_percent"] or 85)/100))
            text += f"\n\n💰 <b>Money Management</b>\n💵 Trade: <b>{money(amount)}</b>\n📈 WIN হলে: <b>+{money(profit)}</b>\n📉 LOSS হলে M1: <b>{money(u['mm_m1_cents'])}</b>"
        kb=result_buttons(signal_id,user_id)
        bot.send_message(user_id,text,reply_markup=kb if kb else None)
        return True,"sent"
    except Exception:
        logger.exception("Final signal delivery failed")
        with DB_LOCK:
            conn=db()
            try:
                conn.execute("DELETE FROM deliveries WHERE signal_id=? AND user_id=?",(signal_id,user_id)); conn.execute("UPDATE users SET free_used=MAX(0,free_used-1) WHERE user_id=? AND status!='VIP'",(user_id,)); conn.commit()
            finally: conn.close()
        return False,"send_error"

deliver_signal=patched_deliver_signal_final


def __clean_vote_menu_v2(message):
    uid=message.from_user.id; signal=next_signal_for_user(uid)
    if not signal: return bot.send_message(message.chat.id,"📭 কোনো signal vote করার জন্য নেই।",reply_markup=main_keyboard(uid))
    with DB_LOCK:
        conn=db()
        try: vote=conn.execute("SELECT vote FROM votes WHERE signal_id=? AND user_id=?",(signal["id"],uid)).fetchone()
        finally: conn.close()
    reset_user_daily_state(uid); u=get_user(uid)
    mm=(f"\n\n💰 <b>Money Management</b>\n💵 Trade: <b>{money(u['mm_m1_cents'] if (u['mm_current_mode']=='M1') else u['mm_base_cents'])}</b>\n📈 WIN হলে: <b>+{money(int(round((u['mm_m1_cents'] if u['mm_current_mode']=='M1' else u['mm_base_cents'])*float(u['mm_payout_percent'] or 85)/100)))}</b>\n📉 LOSS হলে M1: <b>{money(u['mm_m1_cents'])}</b>" if u and int(u['mm_enabled'] or 0) else "")
    if vote: return bot.send_message(message.chat.id,patched_format_signal_final(signal)+mm+f"\n\n🗳️ Your vote: <b>{escape(vote['vote'])}</b>",reply_markup=main_keyboard(uid))
    _state_set(uid,{"action":"vote","signal_id":signal["id"]})
    bot.send_message(message.chat.id,patched_format_signal_final(signal)+mm+"\n\n🗳️ Vote নির্বাচন করুন:",reply_markup=make_keyboard([["🟢 UP / BUY","🔴 DOWN / SELL"],["⏭️ SKIP"],["🔙 Back","🏠 Main Menu"]]))


def __clean_main_v2():
    if not safe_startup_migration(): logger.error("DB startup had errors; polling will still retry")
    try: backup_database()
    except Exception: logger.exception("Initial backup failed")
    # Threads are independent; one failure must never kill the bot.
    for target,name in [(auto_signal_loop,"auto_signal_loop"),(backup_loop,"backup_loop"),(vip_expiry_loop,"vip_expiry_loop"),(mm_daily_reset_loop,"mm_daily_reset_loop"),(cleanup_states_loop,"cleanup_states_loop"),(ai_limit_reset_loop,"ai_limit_reset_loop")]:
        try: threading.Thread(target=target,daemon=True,name=name).start()
        except Exception: logger.exception("Could not start %s",name)
    if not os.getenv("GEMINI_API_KEY","").strip():
        try: bot.send_message(ADMIN_ID,"⚠️ <b>Gemini API key missing</b>\nAI Candle Analysis will stay unavailable until GEMINI_API_KEY is added.")
        except Exception: logger.info("Gemini key missing; admin notification could not be sent")
    logger.info("SM QUATEX SURE SHORT started")
    while True:
        try:
            try: bot.remove_webhook()
            except Exception: logger.exception("Webhook cleanup failed")
            try: me=bot.get_me(); logger.info("Telegram connected as @%s (%s)",getattr(me,"username","unknown"),getattr(me,"id","?"))
            except Exception: logger.exception("Telegram authentication failed"); time.sleep(5); continue
            bot.infinity_polling(skip_pending=False,timeout=30,long_polling_timeout=30)
        except Exception:
            logger.exception("Polling crashed; reconnecting")
            time.sleep(5)





# ============================================================
# RULE-BASED CANDLE ANALYSIS (no Gemini) + ADMIN RULES PANEL
# ============================================================
ANALYSIS_RULE_TYPES = ["TREND", "PATTERN", "SUPPORT", "MOMENTUM", "CANDLE_COUNT"]


def _analysis_rules_migration():
    with DB_LOCK:
        conn = db()
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS analysis_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT, rule_name TEXT NOT NULL, rule_type TEXT NOT NULL,
                rule_value TEXT NOT NULL, direction TEXT NOT NULL, weight INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)""")
            if conn.execute("SELECT COUNT(*) FROM analysis_rules").fetchone()[0] == 0:
                defaults = [
                    ("Uptrend Detect", "TREND", "UPTREND", "UP", 3), ("Downtrend Detect", "TREND", "DOWNTREND", "DOWN", 3),
                    ("Strong Bull Momentum", "MOMENTUM", "STRONG_BULL", "UP", 2), ("Strong Bear Momentum", "MOMENTUM", "STRONG_BEAR", "DOWN", 2),
                    ("Bullish Engulfing", "PATTERN", "BULLISH_ENGULFING", "UP", 2), ("Bearish Engulfing", "PATTERN", "BEARISH_ENGULFING", "DOWN", 2),
                    ("Hammer Pattern", "PATTERN", "HAMMER", "UP", 2), ("Shooting Star", "PATTERN", "SHOOTING_STAR", "DOWN", 2),
                    ("Support Bounce", "SUPPORT", "SUPPORT_BOUNCE", "UP", 2), ("Resistance Reject", "SUPPORT", "RESISTANCE_REJECT", "DOWN", 2),
                    ("Minimum 20 Candles", "CANDLE_COUNT", "20", "WAIT", 1)]
                for name, rt, rv, d, w in defaults:
                    conn.execute("INSERT INTO analysis_rules(rule_name,rule_type,rule_value,direction,weight,enabled,created_at) VALUES(?,?,?,?,?,1,?)",
                                 (name, rt, rv, d, w, utc_iso(now_utc())))
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analysis_rules_type ON analysis_rules(rule_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analysis_rules_enabled ON analysis_rules(enabled)")
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('analysis_confidence_min','70')")
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('candle_analysis_enabled','ON')")
            conn.commit()
        finally:
            conn.close()


def candle_ai_enabled():
    return get_setting("candle_analysis_enabled", "ON") == "ON"


def analyze_candle_screenshot(images, user_id=None):
    """Colour-ratio analysis of chart screenshots (bytes or list of bytes) + admin rules.
    HONEST LIMITS: it measures green vs red pixels in the chart area only. It cannot
    recognise real candle patterns, support or resistance, so only TREND and MOMENTUM
    features are produced."""
    try:
        from PIL import Image
        import io
        if isinstance(images, (bytes, bytearray)):
            images = [bytes(images)]
        green = red = 0
        for raw in images:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            w, h = img.size
            if w < 200 or h < 200:
                return {"status": "INVALID", "message": "Screenshot too small."}
            # chart area only: skip top bar and the bottom UP/DOWN buttons
            x0, x1, y0, y1 = int(w * 0.02), int(w * 0.88), int(h * 0.12), int(h * 0.70)
            px = img.load()
            for y in range(y0, y1, 3):
                for x in range(x0, x1, 3):
                    r, g, b = px[x, y]
                    if r < 30 and g < 30 and b < 30:
                        continue
                    if g > r + 40 and g > b + 20:
                        green += 1
                    elif r > g + 40 and r > b + 20:
                        red += 1
        total = green + red
        if total < 50:
            return {"status": "INVALID", "message": "Candle পাওয়া যায়নি. Clear chart screenshot দিন."}
        gr = green / total
        feats = []
        if gr >= 0.58:
            feats += [("TREND", "UPTREND"), ("MOMENTUM", "STRONG_BULL")]
        elif gr >= 0.52:
            feats.append(("TREND", "UPTREND"))
        elif gr <= 0.42:
            feats += [("TREND", "DOWNTREND"), ("MOMENTUM", "STRONG_BEAR")]
        elif gr <= 0.48:
            feats.append(("TREND", "DOWNTREND"))
        else:
            feats.append(("TREND", "SIDEWAYS"))
        with DB_LOCK:
            conn = db()
            try:
                rules = conn.execute("SELECT * FROM analysis_rules WHERE enabled=1").fetchall()
            finally:
                conn.close()
        up = down = 0
        matched = []
        for rule in rules:
            for ft, fv in feats:
                if ft == rule["rule_type"] and fv == rule["rule_value"]:
                    if rule["direction"] == "UP":
                        up += rule["weight"]
                    elif rule["direction"] == "DOWN":
                        down += rule["weight"]
                    matched.append(f"{rule['rule_name']} (W:{rule['weight']})")
                    break
        if up + down == 0:
            return {"status": "WAIT", "message": "কোনো analysis rule match হয়নি."}
        if up == down:
            return {"status": "WAIT", "message": "Conflicting signals."}
        direction = "UP" if up > down else "DOWN"
        score = int(max(up, down) / (up + down) * 100)
        try:
            min_conf = int(get_setting("analysis_confidence_min", "70"))
        except Exception:
            min_conf = 70
        if score < min_conf:
            return {"status": "WAIT", "message": f"Rule score কম: {score}% (min {min_conf}%)"}
        return {"status": "SIGNAL", "direction": direction, "confidence": score, "matched_rules": matched,
                "green_ratio": round(gr * 100, 1), "total_candles": total}
    except ImportError:
        return {"status": "ERROR", "message": "PIL (Pillow) installed নেই. requirements.txt-এ Pillow যোগ করুন."}
    except Exception as exc:
        logger.exception("Analysis failed")
        return {"status": "ERROR", "message": str(exc)[:200]}


def start_candle_analysis(message):
    uid = message.from_user.id
    if not candle_ai_enabled() and not is_master(uid):
        return bot.send_message(message.chat.id, "🤖 <b>Candle Analysis বর্তমানে OFF.</b>", reply_markup=main_keyboard(uid))
    limit = candle_ai_max_images()
    STATES[uid] = {"action": "candle_upload", "images": [], "previous_menu": NAV_MENU.get(uid, "signals")}
    bot.send_message(message.chat.id,
        f"📸 <b>Candle Analysis</b>\n\nসর্বোচ্চ <b>{limit}টি</b> chart screenshot upload করুন, তারপর <b>🔍 Analyze Candles</b> চাপুন.\n\n"
        "⚠️ এটি শুধু chart-এর green/red রঙের অনুপাত দেখে. Real pattern/support বোঝে না; guarantee নয়.",
        reply_markup=make_keyboard([["🔍 Analyze Candles", "🗑️ Clear Candles"], ["🔙 Back", "🏠 Main Menu"]]))


def _record_analysis_signal(uid, direction, score):
    """Unique inactive signal row so WIN/LOSS buttons work for every analysis."""
    now = now_bd()
    with DB_LOCK:
        conn = db()
        try:
            cur = conn.execute(
                "INSERT INTO signals(signal_date,signal_time,signal_at_utc,pair,direction,confidence,audience,active,auto_sent,created_at) VALUES(?,?,?,?,?,?,?,0,1,?)",
                (now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S.%f"), utc_iso(now_utc()), f"ANALYSIS-{uid}-{int(time.time()*1000)}-{os.urandom(2).hex()}", direction, f"{score}%", "ANALYSIS", utc_iso(now_utc())))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def _analysis_engine():
    eng = str(get_setting("analysis_engine", "") or "").upper()
    if eng in ("GEMINI", "RULES"):
        return eng
    return "GEMINI" if os.getenv("GEMINI_API_KEY", "").strip() else "RULES"


def _new_analyze_candle_state(message):
    uid = message.from_user.id
    st = STATES.get(uid)
    if not st or st.get("action") != "candle_upload":
        return False
    text = (message.text or "").strip()
    if text == "🔍 Analyze Candles" and _analysis_engine() == "GEMINI":
        return __clean_analyze_candle_state_v2(message)
    kb = make_keyboard([["🔍 Analyze Candles", "🗑️ Clear Candles"], ["🔙 Back", "🏠 Main Menu"]])
    if text == "🗑️ Clear Candles":
        st["images"] = []
        _state_set(uid, st)
        bot.send_message(message.chat.id, "🗑️ Screenshots cleared.", reply_markup=kb)
        return True
    if text != "🔍 Analyze Candles":
        return False
    file_ids = list(st.get("images") or [])
    if not file_ids:
        bot.send_message(message.chat.id, "📸 আগে screenshot upload করুন.", reply_markup=kb)
        return True
    reset_user_daily_state(uid)
    u = get_user(uid)
    vip = vip_is_active(u)
    try:
        limit = int(u["ai_limit_vip"] if vip else u["ai_limit_nonvip"])
    except Exception:
        limit = 50 if vip else 3
    if int(u["ai_usage_count"] or 0) >= limit and not is_master(uid):
        clear_state(uid)
        bot.send_message(message.chat.id, "⛔ আজকের Analysis limit শেষ.", reply_markup=main_keyboard(uid))
        return True
    bot.send_message(message.chat.id, "🔎 Screenshot analyze করছি…")
    try:
        raws = [bot.download_file(bot.get_file(fid).file_path) for fid in file_ids]
        result = analyze_candle_screenshot(raws, uid)
        with DB_LOCK:
            conn = db()
            try:
                conn.execute("UPDATE users SET ai_usage_count=ai_usage_count+1,ai_usage_date=? WHERE user_id=?", (_daily_reset_key(), uid))
                conn.execute("INSERT INTO ai_usage_log(user_id,image_count,status,result_summary,created_at) VALUES(?,?,?,?,?)",
                             (uid, len(file_ids), result["status"], str(result)[:500], utc_iso(now_utc())))
                conn.commit()
            finally:
                conn.close()
    except Exception:
        logger.exception("Analysis error")
        clear_state(uid)
        bot.send_message(message.chat.id, "❌ Analysis failed. আবার চেষ্টা করুন.", reply_markup=main_keyboard(uid))
        return True
    clear_state(uid)
    if result["status"] != "SIGNAL":
        bot.send_message(message.chat.id, f"⏸️ <b>{'WAIT' if result['status'] in ('WAIT','INVALID') else 'ERROR'}</b>\n\n{escape(result.get('message', 'No signal'))}", reply_markup=main_keyboard(uid))
        return True
    d = result["direction"]
    icon = "🟢" if d == "UP" else "🔴"
    matched = "\n".join(f"✅ {escape(r)}" for r in result["matched_rules"])
    reset_user_daily_state(uid)
    u = get_user(uid)
    mm = ""
    if u and int(u["mm_enabled"] or 0) and not int(u["mm_stop"] or 0):
        mode = u["mm_current_mode"] or "BASE"
        amt = int(u["mm_m1_cents"] or 0) if mode == "M1" else int(u["mm_base_cents"] or 100)
        profit = int(round(amt * float(u["mm_payout_percent"] or 85) / 100))
        mm = (f"\n\n━━━━━━━━━━━━━━━━━━\n💰 <b>Money Management</b>\n📊 Mode: <b>{mode}</b>\n💵 Trade: <b>{money(amt)}</b>\n"
              f"📈 WIN হলে: <b>+{money(profit)}</b>\n📉 LOSS হলে M1: <b>{money(u['mm_m1_cents'])}</b>\n━━━━━━━━━━━━━━━━━━")
    text = ("━━━━━━━━━━━━━━━━━━\n🤖 <b>CANDLE ANALYSIS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Green Ratio: <b>{result['green_ratio']}%</b>\n🕯️ Sampled Candle Pixels: <b>{result['total_candles']}</b>\n\n"
            f"📋 <b>Matched Rules:</b>\n{matched}\n\n🎯 SIGNAL: {icon} <b>{d}</b>\n"
            f"📊 Rule Score: <b>{result['confidence']}%</b> <i>(rule-weight অনুপাত; win probability নয়)</i>\n"
            f"⏰ Next Candle: <b>1 min</b>{mm}\n\n⚠️ Colour-ratio analysis — guarantee নয়.\n━━━━━━━━━━━━━━━━━━")
    sid = _record_analysis_signal(uid, d, result["confidence"])
    kb2 = result_buttons(sid, uid)
    bot.send_message(message.chat.id, text, reply_markup=kb2 if kb2 else main_keyboard(uid))
    return True


def admin_analysis_kb():
    return make_keyboard([["➕ Add Analysis Rule", "📋 Analysis Rule List"], ["🗑️ Remove Analysis Rule", "🔘 Rule ON/OFF"],
                          ["📊 Analysis Stats", "🎯 Set Confidence Min"], ["🤖 Analysis ON/OFF", "🔀 Analysis Engine"], _BB])


def _rule_guard(message):
    if not can(message.from_user.id, "signals"):
        bot.send_message(message.chat.id, "⛔ Access denied.", reply_markup=admin_analysis_kb())
        return False
    return True


def admin_analysis_menu(message):
    if not _rule_guard(message):
        return
    with DB_LOCK:
        conn = db()
        try:
            q = lambda sql: conn.execute(sql).fetchone()[0]
            total, active = q("SELECT COUNT(*) FROM analysis_rules"), q("SELECT COUNT(*) FROM analysis_rules WHERE enabled=1")
            ups, downs = q("SELECT COUNT(*) FROM analysis_rules WHERE direction='UP' AND enabled=1"), q("SELECT COUNT(*) FROM analysis_rules WHERE direction='DOWN' AND enabled=1")
        finally:
            conn.close()
    NAV_MENU[message.from_user.id] = "admin_analysis"
    bot.send_message(message.chat.id,
        f"━━━━━━━━━━━━━━━━━━\n🧠 <b>ANALYSIS RULES PANEL</b>\n━━━━━━━━━━━━━━━━━━\n\n📋 Total Rules: <b>{total}</b>\n✅ Active: <b>{active}</b>\n"
        f"🟢 UP Rules: <b>{ups}</b>\n🔴 DOWN Rules: <b>{downs}</b>\n🎯 Min Score: <b>{get_setting('analysis_confidence_min','70')}%</b>\n"
        f"🤖 Analysis: <b>{get_setting('candle_analysis_enabled','ON')}</b>\n🔀 Engine: <b>{_analysis_engine()}</b>\n━━━━━━━━━━━━━━━━━━\n"
        "ℹ️ বর্তমানে শুধু TREND ও MOMENTUM rule match করে (colour-ratio ভিত্তিক).", reply_markup=admin_analysis_kb())


def admin_add_analysis_rule(message):
    if _rule_guard(message):
        set_user_state(message.from_user.id, "add_rule_name", "admin_analysis")
        bot.send_message(message.chat.id, "📝 Rule Name পাঠান:\nExample: <code>Uptrend Detect</code>", reply_markup=back_keyboard())


def admin_list_analysis_rules(message):
    if not _rule_guard(message):
        return
    rules = _q("SELECT * FROM analysis_rules ORDER BY id")
    if not rules:
        return bot.send_message(message.chat.id, "📭 কোনো rule নেই.", reply_markup=admin_analysis_kb())
    lines = ["📋 <b>ANALYSIS RULES</b>\n"]
    for r in rules:
        icon = "🟢" if r["direction"] == "UP" else "🔴" if r["direction"] == "DOWN" else "⏸️"
        lines.append(f"#{r['id']} {escape(r['rule_name'])} | {r['rule_type']}:{escape(r['rule_value'])} | {icon}{r['direction']} W:{r['weight']} {'✅' if r['enabled'] else '⛔'}")
    bot.send_message(message.chat.id, "\n".join(lines) + f"\n\n📊 Total: {len(rules)}", reply_markup=admin_analysis_kb())


def admin_remove_analysis_rule(message):
    if _rule_guard(message):
        set_user_state(message.from_user.id, "remove_rule_id", "admin_analysis")
        bot.send_message(message.chat.id, "🗑️ যে Rule ID delete করবেন পাঠান:", reply_markup=back_keyboard())


def admin_toggle_analysis_rule(message):
    if _rule_guard(message):
        set_user_state(message.from_user.id, "toggle_rule_id", "admin_analysis")
        bot.send_message(message.chat.id, "🔘 যে Rule ID ON/OFF করবেন পাঠান:", reply_markup=back_keyboard())


def admin_analysis_stats(message):
    if not _rule_guard(message):
        return
    c = lambda w="": _count("SELECT COUNT(*) FROM ai_usage_log" + w)
    bot.send_message(message.chat.id,
        f"━━━━━━━━━━━━━━━━━━\n📊 <b>ANALYSIS STATS</b>\n━━━━━━━━━━━━━━━━━━\n\n📊 Total: <b>{c()}</b>\n🎯 Signals: <b>{c(' WHERE status=\'SIGNAL\'')}</b>\n"
        f"⏸️ WAIT: <b>{c(' WHERE status=\'WAIT\'')}</b>\n❌ Invalid: <b>{c(' WHERE status=\'INVALID\'')}</b>\n📅 Today: <b>{c(' WHERE date(created_at)=date(\'now\')')}</b>\n━━━━━━━━━━━━━━━━━━",
        reply_markup=admin_analysis_kb())


def admin_set_analysis_confidence(message):
    if _rule_guard(message):
        set_user_state(message.from_user.id, "set_analysis_conf", "admin_analysis")
        bot.send_message(message.chat.id, f"🎯 Minimum Score দিন (50-99). Current: {get_setting('analysis_confidence_min','70')}%", reply_markup=back_keyboard())


def admin_toggle_analysis_enabled(message):
    if not _rule_guard(message):
        return
    new = "OFF" if get_setting("candle_analysis_enabled", "ON") == "ON" else "ON"
    set_setting("candle_analysis_enabled", new)
    bot.send_message(message.chat.id, f"🤖 Analysis: <b>{new}</b>", reply_markup=admin_analysis_kb())


def admin_toggle_analysis_engine(message):
    if not _rule_guard(message):
        return
    new = "RULES" if _analysis_engine() == "GEMINI" else "GEMINI"
    set_setting("analysis_engine", new)
    note = "" if new == "RULES" or os.getenv("GEMINI_API_KEY", "").strip() else "\n⚠️ GEMINI_API_KEY সেট করা নেই."
    bot.send_message(message.chat.id, f"🔀 Engine: <b>{new}</b>{note}", reply_markup=admin_analysis_kb())


ANALYSIS_ADMIN_BUTTONS = {
    "🔀 Analysis Engine": admin_toggle_analysis_engine,
    "🧠 Analysis Rules": admin_analysis_menu, "➕ Add Analysis Rule": admin_add_analysis_rule,
    "📋 Analysis Rule List": admin_list_analysis_rules, "🗑️ Remove Analysis Rule": admin_remove_analysis_rule,
    "🔘 Rule ON/OFF": admin_toggle_analysis_rule, "📊 Analysis Stats": admin_analysis_stats,
    "🎯 Set Confidence Min": admin_set_analysis_confidence, "🤖 Analysis ON/OFF": admin_toggle_analysis_enabled,
}


def _analysis_rules_state(message, st):
    """Returns True if handled, None if the action is not ours."""
    uid = message.from_user.id
    action = st.get("action")
    text = (message.text or "").strip()
    if action not in ("add_rule_name", "add_rule_type", "add_rule_value", "add_rule_direction", "add_rule_weight",
                      "remove_rule_id", "toggle_rule_id", "set_analysis_conf"):
        return None
    try:
        if action == "add_rule_name":
            if not text or len(text) > 60: raise ValueError("Rule name ১-৬০ অক্ষরের হতে হবে.")
            st.update(rule_name=text, action="add_rule_type"); _state_set(uid, st)
            bot.send_message(message.chat.id, "📊 Rule Type বেছে নিন:", reply_markup=make_keyboard([["TREND", "PATTERN"], ["SUPPORT", "MOMENTUM"], ["CANDLE_COUNT"], _BB]))
        elif action == "add_rule_type":
            if text not in ANALYSIS_RULE_TYPES: raise ValueError("Valid type: " + ", ".join(ANALYSIS_RULE_TYPES))
            st.update(rule_type=text, action="add_rule_value"); _state_set(uid, st)
            bot.send_message(message.chat.id, "📝 Rule Value পাঠান:\nExample: UPTREND, DOWNTREND, STRONG_BULL", reply_markup=back_keyboard())
        elif action == "add_rule_value":
            if not text or len(text) > 40: raise ValueError("Value ঠিকভাবে দিন.")
            st.update(rule_value=text.upper().strip(), action="add_rule_direction"); _state_set(uid, st)
            bot.send_message(message.chat.id, "🎯 Direction বেছে নিন:", reply_markup=make_keyboard([["🟢 UP", "🔴 DOWN"], ["⏸️ WAIT"], _BB]))
        elif action == "add_rule_direction":
            dm = {"🟢 UP": "UP", "🔴 DOWN": "DOWN", "⏸️ WAIT": "WAIT"}
            if text not in dm: raise ValueError("Direction button ব্যবহার করুন.")
            st.update(direction=dm[text], action="add_rule_weight"); _state_set(uid, st)
            bot.send_message(message.chat.id, "⚖️ Weight (1-10) পাঠান:", reply_markup=back_keyboard())
        elif action == "add_rule_weight":
            w = int(text)
            if not 1 <= w <= 10: raise ValueError("Weight 1-10 এর মধ্যে হতে হবে.")
            with DB_LOCK:
                conn = db()
                try:
                    conn.execute("INSERT INTO analysis_rules(rule_name,rule_type,rule_value,direction,weight,enabled,created_at) VALUES(?,?,?,?,?,1,?)",
                                 (st["rule_name"], st["rule_type"], st["rule_value"], st["direction"], w, utc_iso(now_utc())))
                    conn.commit()
                finally:
                    conn.close()
            clear_state(uid)
            bot.send_message(message.chat.id, f"✅ Rule added!\n\n📝 {escape(st['rule_name'])}\n📊 {st['rule_type']}: {escape(st['rule_value'])}\n🎯 {st['direction']} (W:{w})", reply_markup=admin_analysis_kb())
        elif action in ("remove_rule_id", "toggle_rule_id"):
            rid = int(text)
            with DB_LOCK:
                conn = db()
                try:
                    r = conn.execute("SELECT enabled FROM analysis_rules WHERE id=?", (rid,)).fetchone()
                    if not r: raise ValueError("Rule পাওয়া যায়নি.")
                    if action == "remove_rule_id":
                        conn.execute("DELETE FROM analysis_rules WHERE id=?", (rid,)); msg = f"🗑️ Rule #{rid} deleted."
                    else:
                        ns = 0 if r["enabled"] else 1
                        conn.execute("UPDATE analysis_rules SET enabled=? WHERE id=?", (ns, rid)); msg = f"🔘 Rule #{rid} → {'✅ Enabled' if ns else '⛔ Disabled'}"
                    conn.commit()
                finally:
                    conn.close()
            clear_state(uid)
            bot.send_message(message.chat.id, msg, reply_markup=admin_analysis_kb())
        elif action == "set_analysis_conf":
            n = int(text)
            if not 50 <= n <= 99: raise ValueError("50-99 এর মধ্যে হতে হবে.")
            set_setting("analysis_confidence_min", str(n)); clear_state(uid)
            bot.send_message(message.chat.id, f"🎯 Min Score: {n}%", reply_markup=admin_analysis_kb())
    except ValueError as exc:
        bot.send_message(message.chat.id, "❌ " + escape(str(exc)) + "\nআবার চেষ্টা করুন বা 🔙 Back চাপুন.")
    return True


# ============================================================
# FINAL RUNTIME BINDINGS
# ============================================================
admin_keyboard = admin_home_kb
referral_menu = __clean_referral_menu_v2
admin_audit = __clean_admin_audit_v2
normalize_uid = __clean_normalize_uid_v2
start_uid_submission = __clean_start_uid_submission_v2
release_referral_bonuses = __clean_release_referral_bonuses_v2
secure_process_referral_bonus = __clean_secure_process_referral_bonus_v2
mm_menu = __clean_mm_menu_v2
result_buttons = __clean_result_buttons_v2
candle_ai_prompt = __clean_candle_ai_prompt_v2
analyze_candle_state = __clean_analyze_candle_state_v2
vote_menu = __clean_vote_menu_v2
safe_startup_migration = __clean_safe_startup_migration_v2

_orig_safe_startup = safe_startup_migration
def _safe_startup_with_extras():
    ok = _orig_safe_startup()
    try:
        _wd_column_migration()
    except Exception:
        logger.exception("Withdrawal serial migration failed")
        ok = False
    try:
        _analysis_rules_migration()
    except Exception:
        logger.exception("Analysis rules migration failed")
        ok = False
    return ok

safe_startup_migration = _safe_startup_with_extras
handle_state = handle_state          # single public state dispatcher (explicit)
handle_candle_photo = __clean_handle_candle_photo_v2
analyze_candle_state = _new_analyze_candle_state

handle_admin_button = __clean_handle_admin_button_v3
admin_text_editor = __clean_admin_text_editor_v2
admin_subadmins = __clean_admin_subadmins_v2
admin_notify_targets = __clean_admin_notify_targets_v2
format_signal = patched_format_signal_final
deliver_signal = patched_deliver_signal_final
main = __clean_main_v2

# ============================================================
# SCREENSHOT SIGNAL (pixel-based, no AI) — PIL + numpy only
# ============================================================
SS_MENU_KEY = "admin_screenshot"


def _ss_is_admin(uid):
    try:
        return bool(is_master(uid) or get_permissions(uid))
    except Exception:
        return False


def _ss_migration():
    with DB_LOCK:
        conn = db()
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS signal_user_results (
                signal_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (signal_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS screenshot_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uploader_id INTEGER NOT NULL,
                uploader_type TEXT NOT NULL,
                photo_file_id TEXT,
                pair TEXT,
                trend TEXT,
                next1_direction TEXT,
                next1_confidence INTEGER,
                next2_direction TEXT,
                next2_confidence INTEGER,
                patterns_json TEXT,
                candles_count INTEGER,
                broadcast INTEGER DEFAULT 0,
                broadcast_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_screenshot_quota (
                user_id INTEGER PRIMARY KEY,
                used_today INTEGER DEFAULT 0,
                last_reset TEXT
            );
            CREATE TABLE IF NOT EXISTS screenshot_signal_deliveries (
                signal_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                delivered_at TEXT NOT NULL,
                PRIMARY KEY (signal_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS screenshot_signal_results (
                signal_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (signal_id, user_id)
            );
            """)
            conn.commit()
        finally:
            conn.close()
    defaults = {
        "screenshot_mode": "OFF",
        "screenshot_user_limit": "3",
        "screenshot_admin_auto_send": "ON",
        "screenshot_user_auto_send": "ON",
        "screenshot_min_confidence": "70",
    }
    for key, value in defaults.items():
        try:
            if get_setting(key, "") == "":
                set_setting(key, value)
        except Exception:
            logger.exception("Could not initialize setting %s", key)


_ss_prev_safe_startup = safe_startup_migration


def _ss_safe_startup():
    ok = _ss_prev_safe_startup()
    try:
        _ss_migration()
    except Exception:
        logger.exception("Screenshot signal migration failed")
        ok = False
    return ok


safe_startup_migration = _ss_safe_startup


# ---------------- candle detection ----------------

class CandleDetector:
    def __init__(self, image_bytes):
        if Image is None or np is None:
            raise RuntimeError("Pillow/numpy installed নেই (pip install pillow numpy)")
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        if img.width > 2000 or img.height > 2000:
            ratio = min(2000 / img.width, 2000 / img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
        self.pixels = np.array(img)
        self.height, self.width, _ = self.pixels.shape

    def detect_candles(self, max_candles=30):
        chart_top = int(self.height * 0.12)
        chart_bottom = int(self.height * 0.82)
        chart_left = int(self.width * 0.02)
        chart_right = int(self.width * 0.92)

        # Vectorised version of the per-pixel rules (int16 avoids uint8 overflow).
        sub = self.pixels[chart_top:chart_bottom:2, chart_left:chart_right:2].astype(np.int16)
        if sub.size == 0:
            return []
        r, g, b = sub[..., 0], sub[..., 1], sub[..., 2]
        gray = (np.abs(r - g) < 15) & (np.abs(g - b) < 15)
        white = (r > 200) & (g > 200) & (b > 200)
        green = (~gray) & (g > 100) & (g > r + 25) & (g > b + 10) & (~white)
        white2 = (r > 240) & (g > 240) & (b > 240)
        red = (~gray) & (~green) & (r > 120) & (r > g + 40) & (r > b + 40) & (~white2)

        col_data = []
        for ci in range(sub.shape[1]):
            x = chart_left + ci * 2
            g_idx = np.nonzero(green[:, ci])[0]
            r_idx = np.nonzero(red[:, ci])[0]
            if len(g_idx) >= 3 or len(r_idx) >= 3:
                if len(g_idx) >= len(r_idx):
                    col_data.append({"x": x, "color": "GREEN", "rows": (chart_top + g_idx * 2).tolist()})
                else:
                    col_data.append({"x": x, "color": "RED", "rows": (chart_top + r_idx * 2).tolist()})

        if not col_data:
            return []

        groups, current = [], [col_data[0]]
        for item in col_data[1:]:
            if item["x"] - current[-1]["x"] <= 4:
                current.append(item)
            else:
                groups.append(current)
                current = [item]
        groups.append(current)
        groups = [grp for grp in groups if len(grp) >= 2]

        candles = []
        for group in groups:
            xs = [it["x"] for it in group]
            rows_all = []
            for it in group:
                rows_all.extend(it["rows"])
            if not rows_all:
                continue
            colors = [it["color"] for it in group]
            color = max(set(colors), key=colors.count)
            rows_sorted = sorted(rows_all)
            n = len(rows_sorted)
            p5 = rows_sorted[max(0, int(n * 0.05))]
            p95 = rows_sorted[min(n - 1, int(n * 0.95))]
            body_top, body_bottom = p5, p95
            wick_top, wick_bottom = rows_sorted[0], rows_sorted[-1]
            body_height = body_bottom - body_top
            total_height = wick_bottom - wick_top
            if total_height < 4:
                continue
            candles.append({
                "x": int(np.mean(xs)), "color": color,
                "body_top": body_top, "body_bottom": body_bottom,
                "wick_top": wick_top, "wick_bottom": wick_bottom,
                "body_height": body_height, "total_height": total_height,
                "upper_wick": body_top - wick_top, "lower_wick": wick_bottom - body_bottom,
                "width": max(xs) - min(xs) + 1,
            })

        candles.sort(key=lambda c: c["x"], reverse=True)
        filtered = []
        for c in candles:
            if not filtered or abs(c["x"] - filtered[-1]["x"]) > 5:
                filtered.append(c)
        return filtered[:max_candles]

    def extract_pattern(self):
        candles = self.detect_candles()
        if len(candles) < 5:
            return None
        candles = list(reversed(candles))
        return {
            "total": len(candles),
            "green_count": sum(1 for c in candles if c["color"] == "GREEN"),
            "red_count": sum(1 for c in candles if c["color"] == "RED"),
            "sequence": [c["color"] for c in candles],
            "sizes": [c["body_height"] for c in candles],
            "candles": candles,
        }


# ---------------- pattern analyzer (20 hardcoded rules) ----------------

class PatternAnalyzer:
    def __init__(self, pattern):
        self.seq = pattern["sequence"]
        self.sizes = pattern["sizes"]
        self.candles = pattern["candles"]
        self.n = len(self.seq)
        self.score_up = 0
        self.score_down = 0
        self.reasons = []

    def _last(self, i=1):
        return self.candles[-i]

    def _avg_body(self, window=10):
        recent = self.sizes[-window:]
        return float(np.mean(recent)) if recent else 1.0

    def run_all_rules(self):
        last = self._last()
        body = max(last["body_height"], 1)

        # Rule 1: Doji
        if last["total_height"] > 0:
            ratio = last["body_height"] / last["total_height"]
            if ratio < 0.15:
                if last["color"] == "GREEN":
                    self.score_down += 20
                    self.reasons.append("Doji reversal (bearish)")
                else:
                    self.score_up += 20
                    self.reasons.append("Doji reversal (bullish)")

        # Rule 2: Hammer
        if last["lower_wick"] > body * 2 and last["upper_wick"] < body * 0.5:
            self.score_up += 30
            self.reasons.append("Hammer pattern")

        # Rule 3: Shooting Star
        if last["upper_wick"] > body * 2 and last["lower_wick"] < body * 0.5:
            self.score_down += 30
            self.reasons.append("Shooting star")

        # Rule 4: Bullish Engulfing
        if self.n >= 2:
            lc, prev = self._last(1), self._last(2)
            if (lc["color"] == "GREEN" and prev["color"] == "RED"
                    and lc["body_height"] > prev["body_height"] * 1.2):
                self.score_up += 35
                self.reasons.append("Bullish engulfing")

        # Rule 5: Bearish Engulfing
        if self.n >= 2:
            lc, prev = self._last(1), self._last(2)
            if (lc["color"] == "RED" and prev["color"] == "GREEN"
                    and lc["body_height"] > prev["body_height"] * 1.2):
                self.score_down += 35
                self.reasons.append("Bearish engulfing")

        # Rule 6: Three White Soldiers
        if self.n >= 3 and self.seq[-3:] == ["GREEN", "GREEN", "GREEN"]:
            s = self.sizes[-3:]
            if s[0] < s[1] < s[2]:
                self.score_up += 32
                self.reasons.append("Three white soldiers")

        # Rule 7: Three Black Crows
        if self.n >= 3 and self.seq[-3:] == ["RED", "RED", "RED"]:
            s = self.sizes[-3:]
            if s[0] < s[1] < s[2]:
                self.score_down += 32
                self.reasons.append("Three black crows")

        # Rule 8: Morning Star
        if self.n >= 3:
            c1, c2, c3 = self._last(3), self._last(2), self._last(1)
            avg = self._avg_body()
            if (c1["color"] == "RED" and c1["body_height"] > avg * 1.2
                    and c2["body_height"] < avg * 0.4
                    and c3["color"] == "GREEN" and c3["body_height"] > avg * 1.2):
                self.score_up += 28
                self.reasons.append("Morning star")

        # Rule 9: Evening Star
        if self.n >= 3:
            c1, c2, c3 = self._last(3), self._last(2), self._last(1)
            avg = self._avg_body()
            if (c1["color"] == "GREEN" and c1["body_height"] > avg * 1.2
                    and c2["body_height"] < avg * 0.4
                    and c3["color"] == "RED" and c3["body_height"] > avg * 1.2):
                self.score_down += 28
                self.reasons.append("Evening star")

        # Rule 10: Long Lower Wick
        if last["lower_wick"] > body * 1.5:
            self.score_up += 22
            self.reasons.append("Long lower wick")

        # Rule 11: Long Upper Wick
        if last["upper_wick"] > body * 1.5:
            self.score_down += 22
            self.reasons.append("Long upper wick")

        # Rule 12: Strong Green Body
        avg = self._avg_body()
        if last["color"] == "GREEN" and last["body_height"] > avg * 1.5:
            self.score_up += 20
            self.reasons.append("Strong green body")

        # Rule 13: Strong Red Body
        if last["color"] == "RED" and last["body_height"] > avg * 1.5:
            self.score_down += 20
            self.reasons.append("Strong red body")

        # Rule 14 & 15: Exhaustion
        if self.n >= 5:
            last5 = self.seq[-5:]
            if last5 == ["GREEN"] * 5:
                self.score_down += 25
                self.reasons.append("Exhaustion 5 green")
            elif last5 == ["RED"] * 5:
                self.score_up += 25
                self.reasons.append("Exhaustion 5 red")

        # Rule 16: Uptrend
        recent10 = self.seq[-10:]
        if recent10.count("GREEN") >= 6:
            self.score_up += 15
            self.reasons.append("Uptrend")

        # Rule 17: Downtrend
        if recent10.count("RED") >= 6:
            self.score_down += 15
            self.reasons.append("Downtrend")

        # Rule 18: Support Bounce
        if self.n >= 3:
            recent3 = self.candles[-3:]
            bottoms = [c["wick_bottom"] for c in recent3]
            if max(bottoms) - min(bottoms) < 6:
                if all(c["lower_wick"] > c["body_height"] for c in recent3):
                    self.score_up += 25
                    self.reasons.append("Support bounce")

        # Rule 19: Resistance Reject
        if self.n >= 3:
            recent3 = self.candles[-3:]
            tops = [c["wick_top"] for c in recent3]
            if max(tops) - min(tops) < 6:
                if all(c["upper_wick"] > c["body_height"] for c in recent3):
                    self.score_down += 25
                    self.reasons.append("Resistance reject")

        # Rule 20: Pin Bar
        if (last["color"] == "GREEN" and last["lower_wick"] > body * 2
                and last["upper_wick"] < body * 0.3):
            self.score_up += 25
            self.reasons.append("Bullish pin bar")
        if (last["color"] == "RED" and last["upper_wick"] > body * 2
                and last["lower_wick"] < body * 0.3):
            self.score_down += 25
            self.reasons.append("Bearish pin bar")

        return self.score_up, self.score_down, self.reasons


def analyze_chart(image_bytes):
    try:
        detector = CandleDetector(image_bytes)
        pattern = detector.extract_pattern()
        if not pattern or pattern["total"] < 5:
            return {"status": "ERROR", "message": "কমপক্ষে ৫টি candle দরকার"}

        analyzer = PatternAnalyzer(pattern)
        up, down, reasons = analyzer.run_all_rules()
        total = up + down

        green, red = pattern["green_count"], pattern["red_count"]
        if green > red + 3:
            trend = "BULLISH"
        elif red > green + 3:
            trend = "BEARISH"
        else:
            trend = "SIDEWAYS"

        if total < 20:
            return {"status": "WAIT", "message": "Pattern দুর্বল", "trend": trend}

        diff = up - down
        if diff > 0:
            n1_dir = "UP"
        elif diff < 0:
            n1_dir = "DOWN"
        else:
            n1_dir = "UP" if pattern["sequence"][-1] == "GREEN" else "DOWN"

        n1_conf = min(85, 50 + min(35, abs(diff)))

        if abs(diff) > 25:
            n2_dir = "DOWN" if n1_dir == "UP" else "UP"
            n2_reason = "Possible reversal after strong move"
        else:
            n2_dir = n1_dir
            n2_reason = "Trend continuation"

        n2_conf = max(45, n1_conf - 15)

        return {
            "status": "SIGNAL",
            "trend": trend,
            "next1": {"direction": n1_dir, "confidence": n1_conf,
                      "reason": " + ".join(reasons[:3]) if reasons else "Pattern"},
            "next2": {"direction": n2_dir, "confidence": n2_conf, "reason": n2_reason},
            "patterns": reasons[:5],
            "candles_count": pattern["total"],
        }
    except Exception as e:
        logger.exception("Analysis failed")
        return {"status": "ERROR", "message": str(e)[:200]}


# ---------------- helpers ----------------

def check_user_quota(uid):
    limit = int(get_setting("screenshot_user_limit", "3"))
    today = now_bd().date().isoformat()
    with DB_LOCK:
        conn = db()
        try:
            row = conn.execute(
                "SELECT used_today, last_reset FROM user_screenshot_quota WHERE user_id=?", (uid,)
            ).fetchone()
            if not row or row["last_reset"] != today:
                conn.execute(
                    "INSERT INTO user_screenshot_quota(user_id, used_today, last_reset) VALUES(?,?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET used_today=0, last_reset=excluded.last_reset",
                    (uid, 0, today)
                )
                conn.commit()
                return True, 0, limit
            return row["used_today"] < limit, row["used_today"], limit
        finally:
            conn.close()


def increment_user_quota(uid):
    today = now_bd().date().isoformat()
    with DB_LOCK:
        conn = db()
        try:
            conn.execute(
                "INSERT INTO user_screenshot_quota(user_id, used_today, last_reset) VALUES(?,1,?) "
                "ON CONFLICT(user_id) DO UPDATE SET used_today=used_today+1",
                (uid, today)
            )
            conn.commit()
        finally:
            conn.close()


def save_screenshot_signal(uid, uploader_type, photo_file_id, pair, result):
    with DB_LOCK:
        conn = db()
        try:
            cur = conn.execute(
                "INSERT INTO screenshot_signals("
                "uploader_id, uploader_type, photo_file_id, pair, trend, "
                "next1_direction, next1_confidence, next2_direction, next2_confidence, "
                "patterns_json, candles_count, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, uploader_type, photo_file_id, pair, result["trend"],
                 result["next1"]["direction"], result["next1"]["confidence"],
                 result["next2"]["direction"], result["next2"]["confidence"],
                 json.dumps(result["patterns"]), result["candles_count"],
                 utc_iso(now_utc()))
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def format_chart_signal(pair, result):
    n1 = result["next1"]
    n2 = result["next2"]
    trend_icon = {"BULLISH": "📈", "BEARISH": "📉", "SIDEWAYS": "➡️"}.get(result["trend"], "❓")
    i1 = "🟢⬆️" if n1["direction"] == "UP" else "🔴⬇️"
    i2 = "🟢⬆️" if n2["direction"] == "UP" else "🔴⬇️"
    patterns = "\n".join(f"✅ {escape(p)}" for p in result["patterns"][:4])
    return (
        "━━━━━━━━━━━━━━━━━━\n"
        "🤖 <b>CANDLE ANALYSIS SIGNAL</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💱 Pair: <b>{escape(pair)}</b>\n"
        f"⏰ Time: <b>{now_bd().strftime('%I:%M %p')}</b>\n"
        f"📊 Candles: <b>{result['candles_count']}</b>\n\n"
        f"{trend_icon} Trend: <b>{result['trend']}</b>\n\n"
        f"<b>Detected Patterns:</b>\n{patterns}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🕯️ <b>NEXT 1 CANDLE</b>\n"
        f"{i1} <b>{n1['direction']}</b>\n"
        f"🎯 Confidence: <b>{n1['confidence']}%</b>\n"
        f"💡 {escape(n1['reason'])}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🕯️ <b>NEXT 2 CANDLE</b>\n"
        f"{i2} <b>{n2['direction']}</b>\n"
        f"🎯 Confidence: <b>{n2['confidence']}%</b>\n"
        f"💡 {escape(n2['reason'])}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Educational purpose only"
    )


def broadcast_screenshot_signal(signal_id, text, kb):
    with DB_LOCK:
        conn = db()
        try:
            users = conn.execute("SELECT user_id FROM users WHERE blocked=0 AND notify=1").fetchall()
        finally:
            conn.close()

    sent = 0
    for u in users:
        uid = u["user_id"]
        with DB_LOCK:
            conn = db()
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO screenshot_signal_deliveries(signal_id, user_id, delivered_at) "
                    "VALUES(?,?,?)", (signal_id, uid, utc_iso(now_utc()))
                )
                inserted = cur.rowcount > 0
                conn.commit()
            finally:
                conn.close()
        if not inserted:
            continue
        try:
            bot.send_message(uid, text, reply_markup=kb)
            sent += 1
        except Exception:
            with DB_LOCK:
                conn = db()
                try:
                    conn.execute("DELETE FROM screenshot_signal_deliveries WHERE signal_id=? AND user_id=?",
                                 (signal_id, uid))
                    conn.commit()
                finally:
                    conn.close()
        time.sleep(0.05)  # stay under Telegram's ~30 msg/s limit

    with DB_LOCK:
        conn = db()
        try:
            conn.execute("UPDATE screenshot_signals SET broadcast=1, broadcast_count=? WHERE id=?",
                         (sent, signal_id))
            conn.commit()
        finally:
            conn.close()
    return sent


def _ss_broadcast_worker(signal_id, text, kb, chat_id, reply_to_id):
    try:
        sent = broadcast_screenshot_signal(signal_id, text, kb)
        bot.send_message(chat_id, f"📤 {sent} users দের কাছে পাঠানো হয়েছে (Signal #{signal_id})",
                         reply_to_message_id=reply_to_id)
    except Exception:
        logger.exception("Screenshot broadcast failed")


# ---------------- photo flow ----------------

def ss_handle_chart_photo(message):
    uid = message.from_user.id
    mode = get_setting("screenshot_mode", "OFF")
    is_admin_user = _ss_is_admin(uid)

    if mode == "OFF":
        if is_admin_user:
            bot.reply_to(message, "⚠️ Screenshot mode বন্ধ।")
        return False

    if not is_admin_user and mode != "USER_ALLOWED":
        bot.reply_to(message, "❌ শুধু Admin screenshot দিতে পারবে।")
        return True

    if not is_admin_user:
        ok, used, limit = check_user_quota(uid)
        if not ok:
            bot.reply_to(message, f"❌ আজকের limit শেষ ({used}/{limit})")
            return True

    try:
        bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        pass
    photo = message.photo[-1]

    try:
        file_info = bot.get_file(photo.file_id)
        image_bytes = bot.download_file(file_info.file_path)
    except Exception:
        bot.reply_to(message, "❌ ছবি download করতে ব্যর্থ")
        return True

    result = analyze_chart(image_bytes)

    if result["status"] == "ERROR":
        bot.reply_to(message, f"❌ {escape(result['message'])}")
        return True

    if result["status"] == "WAIT":
        bot.reply_to(message, f"⏸️ {result['message']}\nTrend: {result['trend']}")
        return True

    try:
        min_conf = int(get_setting("screenshot_min_confidence", "70"))
    except ValueError:
        min_conf = 70
    if result["next1"]["confidence"] < min_conf:
        bot.reply_to(message, f"⏸️ Confidence কম ({result['next1']['confidence']}% < {min_conf}%)")
        return True

    pair = "AUTO"
    if message.caption:
        m = re.search(r"[A-Z]{3}[/_][A-Z]{3}", message.caption.upper())
        if m:
            pair = m.group(0).replace("_", "/")

    signal_id = save_screenshot_signal(uid, "ADMIN" if is_admin_user else "USER",
                                       photo.file_id, pair, result)
    if not is_admin_user:
        increment_user_quota(uid)

    text = format_chart_signal(pair, result)

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ WIN", callback_data=f"ssres:{signal_id}:WIN"),
        types.InlineKeyboardButton("❌ LOSE", callback_data=f"ssres:{signal_id}:LOSS"),
        types.InlineKeyboardButton("⏭️ SKIP", callback_data=f"ssres:{signal_id}:SKIP"),
    )

    if is_admin_user:
        should_broadcast = get_setting("screenshot_admin_auto_send", "ON") == "ON"
    else:
        should_broadcast = get_setting("screenshot_user_auto_send", "ON") == "ON"

    if should_broadcast:
        bot.reply_to(message, text + "\n\n📤 Broadcast শুরু হচ্ছে...")
        threading.Thread(
            target=_ss_broadcast_worker,
            args=(signal_id, text, kb, message.chat.id, message.message_id),
            daemon=True, name="ss_broadcast"
        ).start()
    else:
        bot.reply_to(message, text, reply_markup=kb)
    return True


_ss_prev_candle_photo = handle_candle_photo


def handle_candle_photo(message):
    """Existing AI-candle upload flow first; otherwise the Screenshot Signal flow."""
    handled = _ss_prev_candle_photo(message)
    if handled:
        return handled
    return ss_handle_chart_photo(message)


@bot.callback_query_handler(func=lambda call: (call.data or "").startswith("ssres:"))
def handle_screenshot_result(call):
    try:
        _, sid_s, result = call.data.split(":", 2)
        signal_id = int(sid_s)
        uid = call.from_user.id
        if result == "LOSE":
            result = "LOSS"
        if result not in ("WIN", "LOSS", "SKIP"):
            raise ValueError("Invalid result")
        with DB_LOCK:
            conn = db()
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO screenshot_signal_results(signal_id, user_id, result, created_at) "
                    "VALUES(?,?,?,?)", (signal_id, uid, result, utc_iso(now_utc()))
                )
                inserted = cur.rowcount > 0
                conn.commit()
            finally:
                conn.close()
        if not inserted:
            bot.answer_callback_query(call.id, "Already submitted for this signal.", show_alert=True)
            return
        bot.answer_callback_query(call.id, {"WIN": "✅ WIN!", "LOSS": "❌ LOSE", "SKIP": "⏭️ Skipped"}[result])
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
    except Exception:
        logger.exception("ssres handler failed")
        try:
            bot.answer_callback_query(call.id, "Error")
        except Exception:
            pass


# ---------------- admin panel ----------------

def admin_screenshot_kb():
    mode = get_setting("screenshot_mode", "OFF")
    a_auto = get_setting("screenshot_admin_auto_send", "ON")
    u_auto = get_setting("screenshot_user_auto_send", "ON")
    limit = get_setting("screenshot_user_limit", "3")
    minc = get_setting("screenshot_min_confidence", "70")
    return make_keyboard([
        [f"🔘 Mode: {mode}", "🔄 Change Mode"],
        [f"📤 Admin Auto: {a_auto}", "🔄 Toggle Admin"],
        [f"👤 User Auto: {u_auto}", "🔄 Toggle User"],
        [f"👥 User Limit: {limit}", "✏️ Set Limit"],
        [f"🎯 Min Conf: {minc}%", "✏️ Set Conf"],
        ["📊 Stats", "📋 Recent"],
        ["🔙 Back", "🏠 Main Menu"],
    ])


_MENU_TABLE[SS_MENU_KEY] = ("📸 <b>Screenshot Signal Settings</b>", lambda u: admin_screenshot_kb())
NAV_PARENT[SS_MENU_KEY] = "admin"


def _ss_panel(chat_id, uid, note=None):
    NAV_MENU[uid] = SS_MENU_KEY
    bot.send_message(chat_id, note or "📸 <b>Screenshot Signal Settings</b>",
                     reply_markup=admin_screenshot_kb())


def _ss_stats_text():
    today_start = now_bd().replace(hour=0, minute=0, second=0, microsecond=0)
    lo = utc_iso(today_start)
    hi = utc_iso(today_start + timedelta(days=1))
    with DB_LOCK:
        conn = db()
        try:
            total = conn.execute("SELECT COUNT(*) c FROM screenshot_signals").fetchone()["c"]
            td = conn.execute("SELECT COUNT(*) c FROM screenshot_signals WHERE created_at>=? AND created_at<?",
                              (lo, hi)).fetchone()["c"]
            wins = conn.execute("SELECT COUNT(*) c FROM screenshot_signal_results WHERE result='WIN'").fetchone()["c"]
            loss = conn.execute("SELECT COUNT(*) c FROM screenshot_signal_results WHERE result='LOSS'").fetchone()["c"]
        finally:
            conn.close()
    return (f"📊 <b>Screenshot Stats</b>\nTotal: {total}\nToday: {td}\n"
            f"User-reported WIN: {wins} | LOSE: {loss}")


def _ss_recent_text():
    with DB_LOCK:
        conn = db()
        try:
            rows = conn.execute("SELECT * FROM screenshot_signals ORDER BY id DESC LIMIT 5").fetchall()
        finally:
            conn.close()
    if not rows:
        return "No recent signals"
    lines = ["📋 <b>Recent Screenshot Signals</b>", ""]
    for r in rows:
        lines.append(f"#{r['id']} | {escape(str(r['pair']))} | N1: {r['next1_direction']} "
                     f"({r['next1_confidence']}%) | N2: {r['next2_direction']} ({r['next2_confidence']}%)")
    return "\n".join(lines)


_SS_BUTTONS = {"🔄 Change Mode", "🔄 Toggle Admin", "🔄 Toggle User", "✏️ Set Limit",
               "✏️ Set Conf", "📊 Stats", "📋 Recent"}
_SS_LABEL_PREFIXES = ("🔘 Mode:", "📤 Admin Auto:", "👤 User Auto:", "👥 User Limit:", "🎯 Min Conf:")


def ss_text_match(message):
    try:
        if message.content_type != "text":
            return False
        t = (message.text or "").strip()
        uid = message.from_user.id
        if t == "📸 Screenshot Signal":
            return _ss_is_admin(uid)
        if NAV_MENU.get(uid) == SS_MENU_KEY and (t in _SS_BUTTONS or t.startswith(_SS_LABEL_PREFIXES)):
            return _ss_is_admin(uid)
        return False
    except Exception:
        return False


def ss_admin_text(message):
    uid = message.from_user.id
    chat = message.chat.id
    t = (message.text or "").strip()
    try:
        # A real menu button always wins over a half-finished input.
        STATES.pop(uid, None)
        NAV_INPUT.pop(uid, None)

        if not can(uid, "settings"):
            return bot.send_message(chat, "⛔ Access denied.", reply_markup=admin_keyboard())

        if t == "📸 Screenshot Signal":
            return _ss_panel(chat, uid)

        if t == "🔄 Change Mode":
            modes = ["OFF", "ADMIN_ONLY", "USER_ALLOWED"]
            cur = get_setting("screenshot_mode", "OFF")
            new_mode = modes[((modes.index(cur) if cur in modes else 0) + 1) % 3]
            set_setting("screenshot_mode", new_mode)
            return _ss_panel(chat, uid, f"✅ Mode: <b>{new_mode}</b>")

        if t == "🔄 Toggle Admin":
            new = "OFF" if get_setting("screenshot_admin_auto_send", "ON") == "ON" else "ON"
            set_setting("screenshot_admin_auto_send", new)
            return _ss_panel(chat, uid, f"✅ Admin Auto: <b>{new}</b>")

        if t == "🔄 Toggle User":
            new = "OFF" if get_setting("screenshot_user_auto_send", "ON") == "ON" else "ON"
            set_setting("screenshot_user_auto_send", new)
            return _ss_panel(chat, uid, f"✅ User Auto: <b>{new}</b>")

        if t == "✏️ Set Limit":
            _state_set(uid, {"action": "set_screenshot_limit", "previous_menu": SS_MENU_KEY})
            return bot.send_message(chat, "Daily limit number পাঠান (1-100)", reply_markup=back_keyboard())

        if t == "✏️ Set Conf":
            _state_set(uid, {"action": "set_screenshot_conf", "previous_menu": SS_MENU_KEY})
            return bot.send_message(chat, "Min confidence পাঠান (50-85)", reply_markup=back_keyboard())

        if t == "📊 Stats":
            return _ss_panel(chat, uid, _ss_stats_text())

        if t == "📋 Recent":
            return _ss_panel(chat, uid, _ss_recent_text())

        # Display-only labels (e.g. "🔘 Mode: OFF") just refresh the panel.
        return _ss_panel(chat, uid)
    except Exception:
        logger.exception("Screenshot admin panel failed")
        try:
            bot.send_message(chat, "❌ Error. আবার চেষ্টা করুন.", reply_markup=admin_keyboard())
        except Exception:
            pass


_ss_prev_handle_state = handle_state


def handle_state(message):
    uid = message.from_user.id
    st = STATES.get(uid)
    action = st.get("action") if st else None
    if action in ("set_screenshot_limit", "set_screenshot_conf"):
        text = (message.text or "").strip()
        chat = message.chat.id
        if not can(uid, "settings"):
            clear_state(uid)
            bot.send_message(chat, "⛔ Access denied.", reply_markup=admin_keyboard())
            return True
        if action == "set_screenshot_limit":
            lo, hi, key, label = 1, 100, "screenshot_user_limit", "Limit"
        else:
            lo, hi, key, label = 50, 85, "screenshot_min_confidence", "Min Conf"
        try:
            n = int(text)
        except ValueError:
            bot.send_message(chat, "সংখ্যা দিন")
            return True
        if not (lo <= n <= hi):
            bot.send_message(chat, f"{lo}-{hi} এর মধ্যে দিন")
            return True
        set_setting(key, str(n))
        clear_state(uid)
        _ss_panel(chat, uid, f"✅ {label}: {n}{'%' if key == 'screenshot_min_confidence' else ''}")
        return True
    return _ss_prev_handle_state(message)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
