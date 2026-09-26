import os
import re
import time
import sqlite3
import threading
import logging
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


def main_keyboard(user_id):

    rows = [

        [
            "📊 Future Signals",
            "⚡ Live Signals"
        ],

        [
            "🗳️ Vote",
            "📈 Signal Result"
        ],

        [
            "👤 My Status",
            "🆔 Submit Quotex UID"
        ],

        [
            "💰 Money Management",
            "💵 Wallet"
        ],

        [
            "💸 Request Withdraw",
            "👥 Referral Link"
        ],

        [
            "📜 Signal History",
            "📖 VIP Rules"
        ],

        [
            "📜 Trading Contract",
            "🔔 Notifications"
        ],

        [
            "❓ Help / FAQ"
        ]

    ]

    if is_master(user_id) or get_permissions(user_id):

        rows.append([
            "👑 Admin Control"
        ])

    return make_keyboard(rows)


def back_keyboard():

    return make_keyboard([
        [
            "🔙 Back",
            "🏠 Main Menu"
        ]
    ])


def legacy_admin_keyboard():

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

        [
            "💵 Set Balance",
            "🎯 Set Profit Target"
        ],

        [
            "🛑 Set Loss Limit",
            "💲 Set Base Trade"
        ],

        [
            "1️⃣ Set M1 Trade",
            "🔢 Max Trades/Day"
        ],

        [
            "📊 MM Status",
            "🛑 Stop MM Today"
        ],

        [
            "🔙 Back",
            "🏠 Main Menu"
        ]

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
                                audience = (target["audience"] or "ALL").upper()
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

def legacy_referral_menu(message):

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


    with DB_LOCK:

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

        text = "📭 কোনো pending UID নেই."

    else:

        lines = [
            "🆔 <b>PENDING UID</b>\n"
        ]


        for row in rows:

            lines.append(

                f"#{row['id']} | "
                f"User: <code>{row['user_id']}</code> | "
                f"UID: <code>"
                f"{escape(row['quotex_uid'])}"
                f"</code>"

            )


        text = "\n".join(
            lines
        )


    STATES[
        message.from_user.id
    ] = {
        "action": "uid_review"
    }


    bot.send_message(

        message.chat.id,

        text
        +
        "\n\nApprove:\n"
        "<code>approve ID</code>\n\n"
        "Reject:\n"
        "<code>reject ID</code>",

        reply_markup=admin_keyboard()
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

def legacy_admin_subadmins(message):

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

def legacy_admin_notify_targets(message):

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
# ADMIN: USER-FRIENDLY SETTINGS CENTER
# ============================================================

def admin_settings_center_keyboard():
    return make_keyboard([
        ["🛠️ Maintenance ON/OFF", "⚡ Live ON/OFF"],
        ["📤 Auto Send ON/OFF", "🔔 Auto Notification ON/OFF"],
        ["💸 Withdraw ON/OFF", "⏳ Set Withdraw Hold"],
        ["🎟️ Set Free Limit", "💵 Set Min Withdraw"],
        ["👥 Set Referral Bonus", "🎯 Set Confidence"],
        ["📜 Edit Trading Contract", "📝 Bot Text Editor"],
        ["💾 Create DB Backup", "📊 Settings Summary"],
        ["🔙 Back", "🏠 Main Menu"],
    ])


def admin_settings_center(message):
    """Button-driven settings hub for the master admin/sub-admins."""
    uid = message.from_user.id
    if not can(uid, "settings"):
        return bot.send_message(
            message.chat.id,
            "⛔ এই Settings section-এর permission আপনার নেই.",
            reply_markup=admin_keyboard()
        )

    maintenance = get_setting("maintenance", "OFF")
    live_mode = get_setting("live_mode", "ON")
    auto_send = get_setting("auto_send", "ON")
    withdrawals = get_setting("withdrawals", "ON")
    auto_notification = get_setting("auto_notification", "ON")
    free_limit = get_setting("free_limit", "4")
    min_withdraw = get_setting("min_withdraw", "5.00")
    hold_hours = get_setting("withdraw_hold_hours", "0")
    referral_bonus = get_setting("referral_bonus", "1.00")
    confidence = get_setting("confidence", "95-99%")

    text = (
        "⚙️ <b>ADMIN SETTINGS CENTER</b>\n\n"
        f"🛠 Maintenance: <b>{escape(maintenance)}</b>\n"
        f"⚡ Live Mode: <b>{escape(live_mode)}</b>\n"
        f"📤 Auto Send: <b>{escape(auto_send)}</b>\n"
        f"💸 Withdrawals: <b>{escape(withdrawals)}</b>\n"
        f"🔔 Auto Notification: <b>{escape(auto_notification)}</b>\n"
        f"🎟 Free Limit / 2 days: <b>{escape(free_limit)}</b>\n"
        f"💵 Minimum Withdraw: <b>${escape(min_withdraw)}</b>\n"
        f"⏳ Withdraw Hold: <b>{escape(hold_hours)}h</b>\n"
        f"👥 Base Referral Bonus: <b>${escape(referral_bonus)}</b>\n"
        f"🎯 Signal Confidence Text: <b>{escape(confidence)}</b>\n\n"
        "নিচের button থেকে সরাসরি পরিবর্তন করুন।"
    )

    return bot.send_message(message.chat.id, text, reply_markup=admin_settings_center_keyboard())


def admin_settings_summary(message):
    uid = message.from_user.id
    if not can(uid, "settings"):
        return bot.send_message(message.chat.id, "⛔ Access denied.", reply_markup=admin_keyboard())
    values = [
        ("Maintenance", "maintenance", "OFF"),
        ("Live Mode", "live_mode", "ON"),
        ("Auto Send", "auto_send", "ON"),
        ("Auto Notification", "auto_notification", "ON"),
        ("Withdrawals", "withdrawals", "ON"),
        ("Free Limit", "free_limit", "4"),
        ("Minimum Withdraw", "min_withdraw", "5.00"),
        ("Withdraw Hold Hours", "withdraw_hold_hours", "0"),
        ("Referral Bonus", "referral_bonus", "1.00"),
        ("Confidence", "confidence", "95-99%"),
    ]
    lines = ["📊 <b>SETTINGS SUMMARY</b>"]
    for label, key, default in values:
        lines.append(f"• {label}: <b>{escape(get_setting(key, default))}</b>")
    return bot.send_message(message.chat.id, "\n".join(lines), reply_markup=make_keyboard([["⚙️ Settings"], ["🔙 Back", "🏠 Main Menu"]]))

# ============================================================
# ADMIN: TEXT EDITOR
# ============================================================

def legacy_admin_text_editor(message):

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

def legacy_handle_admin_button(message):

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

def legacy_handle_state(message):

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

    if text in (
        "🔙 Back",
        "🏠 Main Menu"
    ):

        clear_state(
            user_id
        )

        send_main_menu(
            message.chat.id,
            user_id,
            "🏠 <b>Main Menu</b>"
        )

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

LEGACY_HANDLE_STATE = legacy_handle_state
LEGACY_ADMIN_KEYBOARD = legacy_admin_keyboard
LEGACY_HANDLE_ADMIN_BUTTON = legacy_handle_admin_button
LEGACY_REFERRAL_MENU = legacy_referral_menu

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
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_uid_per_account ON uid_submissions(quotex_uid)")
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


def referral_menu(message):
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


def admin_subadmins(message):
    uid=message.from_user.id
    if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
    bot.send_message(message.chat.id,"🛡️ <b>SUB-ADMIN MANAGEMENT</b>",reply_markup=make_keyboard([["➕ Add Sub Admin","📋 Sub Admin List"],["🗑️ Remove Sub Admin"],["🔙 Back","🏠 Main Menu"]]))


def admin_notify_targets(message):
    uid=message.from_user.id
    if not is_master(uid): return bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard())
    bot.send_message(message.chat.id,"🎯 <b>NOTIFICATION TARGETS</b>",reply_markup=make_keyboard([["➕ Add Target","📋 Target List"],["🗑️ Remove Target","🧪 Test Target"],["🔔 Auto Notification ON/OFF"],["🔙 Back","🏠 Main Menu"]]))


def admin_text_editor(message):
    uid=message.from_user.id
    if not can(uid,"settings"): return bot.send_message(message.chat.id,"⛔ Access denied.",reply_markup=admin_keyboard())
    cats=[("👋 Welcome","welcome"),("📊 Future Signal","future_signal_text"),("⚡ Live Signal","live_signal_text"),("💰 Money Management","mm_text"),("⭐ VIP","vip_text"),("🆔 UID","uid_text"),("💵 Wallet","wallet_text"),("💸 Withdraw","withdraw_text"),("👥 Referral","referral_text"),("📣 Notification","notification_text"),("📜 Trading Contract","trading_rules"),("📢 Broadcast","broadcast_text"),("✅ WIN","win_text"),("❌ LOSS","loss_text"),("⏭️ SKIP","skip_text"),("🛠️ Errors","error_text"),("🔔 Reminders","reminder_text"),("✨ Feature Text","feature_text")]
    rows=[]
    for i in range(0,len(cats),2): rows.append([cats[i][0],cats[i+1][0] if i+1<len(cats) else "🔙 Back"])
    STATES[uid]={"action":"text_pick","map":dict(cats)}
    bot.send_message(message.chat.id,"📝 <b>BOT TEXT EDITOR</b>\nChoose a section:",reply_markup=make_keyboard(rows+[["🔙 Back","🏠 Main Menu"]]))


def admin_keyboard():
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
        ["🔙 Back", "🏠 Main Menu"],
    ])


def handle_admin_button(message):
    text=message.text; uid=message.from_user.id
    permission_map={
        "➕ Add Future Signals":"signals","📋 Future Signal List":"signals","✏️ Edit Signal":"signals","🗑️ Delete Signal":"signals","🧹 Clear Future Signals":"signals","📤 Auto Send ON/OFF":"signals","🎯 Signal Audience":"signals",
        "⚡ Live Session":"live","🆔 Pending UID":"uid","⭐ Manage VIP":"vip","💸 Withdrawals":"withdraw","📊 Withdrawal Reports":"withdraw","👥 Referral History":"analytics","💳 Wallet Adjust":"wallet","📢 Broadcast":"broadcast","👥 Users":"users","📊 Analytics":"analytics","📈 Result Stats":"analytics","🎯 Notify Targets":"notifications","⚙️ Settings":"settings","📝 Bot Text Editor":"text","🎟️ Set Free Limit":"settings","💵 Set Min Withdraw":"settings","💸 Withdraw ON/OFF":"settings","🛠️ Maintenance ON/OFF":"settings","⚡ Live ON/OFF":"settings","🔔 Auto Notification ON/OFF":"notifications"
    }
    if text in permission_map:
        required = permission_map[text]
        allowed = can(uid, required)
        # Settings permission intentionally includes the operational toggles
        # shown inside the Settings Center, so sub-admins do not get confusing
        # "access denied" messages after entering Settings.
        if not allowed and required in ("signals", "notifications", "live"):
            allowed = can(uid, "settings")
        if not allowed:
            return bot.send_message(message.chat.id,"⛔ You do not have permission for this section.",reply_markup=admin_keyboard())
    if text=="📈 Result Stats":
        return admin_result_stats(message)
    if text=="⚙️ Settings":
        return admin_settings_center(message)
    if text=="📊 Settings Summary":
        return admin_settings_summary(message)
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
    if text == "⏳ Set Withdraw Hold":
        if not can(uid, "settings"):
            return bot.send_message(message.chat.id, "⛔ Access denied.", reply_markup=admin_keyboard())
        STATES[uid] = {"action": "settings_set_hold"}
        return bot.send_message(message.chat.id, "⏳ Withdrawal hold কত ঘণ্টা হবে?\n0 = no hold\nExample: 24", reply_markup=back_keyboard())

    if text == "👥 Set Referral Bonus":
        if not can(uid, "settings"):
            return bot.send_message(message.chat.id, "⛔ Access denied.", reply_markup=admin_keyboard())
        STATES[uid] = {"action": "settings_set_ref_bonus"}
        return bot.send_message(message.chat.id, "👥 Base referral bonus USD লিখুন.\nExample: 1.00", reply_markup=back_keyboard())

    if text == "🎯 Set Confidence":
        if not can(uid, "settings"):
            return bot.send_message(message.chat.id, "⛔ Access denied.", reply_markup=admin_keyboard())
        STATES[uid] = {"action": "settings_set_confidence"}
        return bot.send_message(message.chat.id, "🎯 Signal confidence text লিখুন.\nExample: 95–99%", reply_markup=back_keyboard())

    if text == "📜 Edit Trading Contract":
        if not can(uid, "settings"):
            return bot.send_message(message.chat.id, "⛔ Access denied.", reply_markup=admin_keyboard())
        STATES[uid] = {"action": "settings_edit_contract"}
        return bot.send_message(message.chat.id, "📜 নতুন Trading Contract text লিখুন:", reply_markup=back_keyboard())

    if text == "💾 Create DB Backup":
        if not is_master(uid):
            return bot.send_message(message.chat.id, "⛔ Master Admin only.", reply_markup=admin_keyboard())
        try:
            backup_database()
            return bot.send_message(message.chat.id, "✅ Database backup created successfully.", reply_markup=admin_settings_center_keyboard())
        except Exception as exc:
            logger.exception("Manual backup failed")
            return bot.send_message(message.chat.id, "❌ Backup failed: " + escape(str(exc)), reply_markup=admin_settings_center_keyboard())

    # Preserve all legacy admin buttons and handlers.
    return LEGACY_HANDLE_ADMIN_BUTTON(message)


def _subadmin_permission_keyboard(selected):
    labels=[("📊 Signals","signals"),("🆔 UID Management","uid"),("👥 Users","users"),("💳 Wallet","wallet"),("💸 Withdraw","withdraw"),("📢 Broadcast","broadcast"),("📊 Analytics","analytics"),("⭐ VIP Management","vip"),("🎯 Notifications","notifications"),("⚙️ Settings","settings"),("📝 Text Editor","text"),("⚡ Live Signals","live"),("💰 Money Management","mm")]
    rows=[]
    for i in range(0,len(labels),2): rows.append([("☑️ " if labels[i][1] in selected else "⬜ ")+labels[i][0], ("☑️ " if labels[i+1][1] in selected else "⬜ ")+labels[i+1][0] if i+1<len(labels) else "🔙 Back"])
    rows.append(["✅ Save Sub Admin","❌ Cancel"])
    return make_keyboard(rows)


def handle_state(message):
    uid=message.from_user.id; text=(message.text or "").strip(); st=STATES.get(uid)
    if not st: return LEGACY_HANDLE_STATE(message)
    if text in ("🔙 Back","🏠 Main Menu","❌ Cancel"):
        clear_state(uid); send_main_menu(message.chat.id,uid,"🏠 <b>Main Menu</b>"); return True
    try:
        action=st.get("action")
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
                    conn.execute("INSERT INTO withdrawals(user_id,amount_cents,method,account,status,created_at,hold_until) VALUES(?,?,?,?,?,?,?)",(uid,a,method,account,"PENDING",utc_iso(now_utc()),hold))
                    conn.execute("INSERT INTO wallet_tx(user_id,amount_cents,kind,note,created_at) VALUES(?,?,?,?,?)",(uid,-a,"WITHDRAW_HOLD","Withdrawal request",utc_iso(now_utc())))
                    conn.commit()
                except Exception:
                    conn.rollback(); raise
                finally: conn.close()
            admin_audit(uid,"WITHDRAW_REQUEST",uid,f"{method} {money(a)}")
            clear_state(uid)
            bot.send_message(message.chat.id,"✅ Withdrawal request submitted. Admin review করবে.",reply_markup=main_keyboard(uid));
            try: bot.send_message(ADMIN_ID,f"💸 <b>NEW WITHDRAWAL</b>\nUser: <code>{uid}</code>\nMethod: {method}\nAccount: <code>{escape(account)}</code>\nAmount: <b>{money(a)}</b>",reply_markup=admin_keyboard())
            except Exception: pass
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
            if not text.startswith("💸 WD #") or not text[7:].isdigit(): raise ValueError("Withdrawal button ব্যবহার করুন.")
            wid=int(text[7:])
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
        if action == "settings_set_hold":
            if not text.isdigit() or int(text) < 0 or int(text) > 720:
                raise ValueError("0 থেকে 720 ঘণ্টার মধ্যে দিন.")
            set_setting("withdraw_hold_hours", str(int(text)))
            clear_state(uid)
            bot.send_message(message.chat.id, "✅ Withdrawal hold updated.", reply_markup=admin_settings_center(message))
            return True
        if action == "settings_set_ref_bonus":
            value = float(text.replace("$", "").strip())
            if value < 0 or value > 1000:
                raise ValueError("Referral bonus 0–1000 USD-এর মধ্যে দিন.")
            set_setting("referral_bonus", f"{value:.2f}")
            clear_state(uid)
            bot.send_message(message.chat.id, "✅ Referral bonus updated.", reply_markup=admin_settings_center(message))
            return True
        if action == "settings_set_confidence":
            if len(text) < 1 or len(text) > 100:
                raise ValueError("Confidence text 1–100 characters দিন.")
            set_setting("confidence", text)
            clear_state(uid)
            bot.send_message(message.chat.id, "✅ Confidence text updated.", reply_markup=admin_settings_center(message))
            return True
        if action == "settings_edit_contract":
            if len(text) < 1 or len(text) > 4000:
                raise ValueError("Trading Contract 1–4000 characters দিন.")
            set_setting("trading_rules", text)
            clear_state(uid)
            bot.send_message(message.chat.id, "✅ Trading Contract updated.", reply_markup=admin_settings_center(message))
            return True
        # New withdrawal entry starts at method selection. Legacy amount-first state is intercepted below.
        if action=="withdraw":
            raise ValueError("Use the withdrawal buttons.")
        return LEGACY_HANDLE_STATE(message)
    except Exception as exc:
        bot.send_message(message.chat.id,"❌ <b>Error</b>\n\n"+escape(str(exc)),reply_markup=back_keyboard()); return True


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

# Add new settings/defaults after migration.
# Safe import-time migration: create the base schema before reading settings.
init_db()
migrate_final_schema()
ensure_signal_user_results()
set_setting("referral_min_signals", get_setting("referral_min_signals","1"))
set_setting("referral_hold_hours", get_setting("referral_hold_hours","24"))
set_setting("withdraw_hold_hours", get_setting("withdraw_hold_hours","0"))
set_setting("auto_notification", get_setting("auto_notification","ON"))
set_setting("mm_payout", get_setting("mm_payout","0.80"))


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

    init_db()
    migrate_final_schema()
    ensure_signal_user_results()
    backup_database()


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
# RUN
# ============================================================

if __name__ == "__main__":

    main()
