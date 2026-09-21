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

            CREATE TABLE IF NOT EXISTS admin_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL, action TEXT NOT NULL,
                target_type TEXT, target_id TEXT, details TEXT, created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS referral_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, flag TEXT NOT NULL,
                details TEXT, created_at TEXT NOT NULL
            );

            """)

            for migration in (
                "ALTER TABLE notify_targets ADD COLUMN target_type TEXT NOT NULL DEFAULT 'GROUP'",
                "ALTER TABLE notify_targets ADD COLUMN audience TEXT NOT NULL DEFAULT 'ALL'",
                "ALTER TABLE notify_targets ADD COLUMN selected_users TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE withdrawals ADD COLUMN reviewed_by INTEGER",
                "ALTER TABLE withdrawals ADD COLUMN risk_status TEXT NOT NULL DEFAULT 'NORMAL'",
                "ALTER TABLE withdrawals ADD COLUMN risk_note TEXT NOT NULL DEFAULT ''",
            ):
                try: conn.execute(migration)
                except sqlite3.OperationalError: pass

            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_uid_normalized_unique ON uid_submissions(lower(trim(quotex_uid)))")
            except sqlite3.IntegrityError:
                pass


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
# SECURITY / AUDIT HELPERS
# ============================================================

def audit_admin(admin_id, action, target_type="", target_id="", details=""):
    try:
        with DB_LOCK:
            conn=db()
            try:
                conn.execute("INSERT INTO admin_audit(admin_id,action,target_type,target_id,details,created_at) VALUES(?,?,?,?,?,?)",(int(admin_id),action,target_type,str(target_id),details,utc_iso(now_utc())))
                conn.commit()
            finally: conn.close()
    except Exception: logger.exception("audit failed")


def referral_security_report(user_id):
    flags=[]; referred=[]; total_bonus=0
    with DB_LOCK:
        conn=db()
        try:
            user=conn.execute("SELECT * FROM users WHERE user_id=?",(user_id,)).fetchone()
            if not user: return {"total":0,"valid":0,"suspicious":0,"bonus":0,"flags":[],"referred":[]}
            rows=conn.execute("""SELECT u.user_id,u.username,u.first_name,u.created_at,u.blocked,u.status,r.bonus_cents,r.created_at AS referral_at
                FROM users u LEFT JOIN referrals r ON r.referred_id=u.user_id AND r.referrer_id=? WHERE u.referred_by=? ORDER BY u.created_at ASC""",(user_id,user_id)).fetchall()
            for r in rows:
                total_bonus += int(r["bonus_cents"] or 0); referred.append(dict(r))
                if r["blocked"]: flags.append(f"Blocked referred account: {r['user_id']}")
                if r["bonus_cents"] is None: flags.append(f"Referral bonus not recorded: {r['user_id']}")
            if user["referred_by"]==user_id: flags.append("Self-referral record exists")
            recent=conn.execute("SELECT COUNT(*) n FROM referrals WHERE referrer_id=? AND created_at>=?",(user_id,utc_iso(now_utc()-timedelta(hours=24)))).fetchone()["n"]
            if recent>=5: flags.append(f"High referral volume in last 24h: {recent}")
            valid=sum(1 for r in referred if r["bonus_cents"] is not None and not r["blocked"])
            return {"total":len(referred),"valid":valid,"suspicious":len(flags),"bonus":total_bonus,"flags":flags,"referred":referred}
        finally: conn.close()


def withdrawal_risk(withdrawal):
    report=referral_security_report(withdrawal["user_id"]); flags=list(report["flags"])
    with DB_LOCK:
        conn=db()
        try: recent=conn.execute("SELECT COUNT(*) n FROM wallet_tx WHERE user_id=? AND kind='REFERRAL' AND created_at>=?",(withdrawal["user_id"],utc_iso(now_utc()-timedelta(hours=24)))).fetchone()["n"]
        finally: conn.close()
    if recent>=3: flags.append(f"{recent} referral bonuses in last 24h")
    return ("REVIEW" if flags else "NORMAL", "; ".join(flags) if flags else "No observable referral anomaly found.", report)


def save_withdrawal_risk(withdrawal_id,status,note):
    with DB_LOCK:
        conn=db()
        try: conn.execute("UPDATE withdrawals SET risk_status=?,risk_note=? WHERE id=?",(status,note,withdrawal_id)); conn.commit()
        finally: conn.close()


def notify_audience(target_row,user_id):
    audience=(target_row["audience"] or "ALL").upper() if "audience" in target_row.keys() else "ALL"
    if audience=="ALL": return True
    u=get_user(user_id)
    if not u: return False
    if audience=="VIP": return u["status"]=="VIP"
    if audience=="SELECTED":
        ids=[x.strip() for x in (target_row["selected_users"] or "").split(",") if x.strip()] if "selected_users" in target_row.keys() else []
        return str(user_id) in ids
    return True


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
            "👥 Referral Link",
            "📜 Signal History"
        ],

        [
            "📖 VIP Rules",
            "📜 Trading Contract"
        ],

        [
            "🔔 Notifications",
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
            "👥 Referral History",
            "🛡️ Security Logs"
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
                                    SELECT *
                                    FROM notify_targets
                                    WHERE enabled=1
                                    """
                                ).fetchall()

                            finally:

                                conn.close()


                        for target in targets:

                            try:
                                if get_setting("target_notify", "ON") != "ON":
                                    continue
                                bot.send_message(
                                    target["chat_id"],
                                    target_message
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

    try:

        referred_by = None

        parts = (
            message.text or ""
        ).split(
            maxsplit=1
        )


        if (
            len(parts) == 2
            and
            parts[1].startswith("ref_")
        ):

            try:

                referred_by = int(
                    parts[1][4:]
                )

            except ValueError:

                referred_by = None


        register_user(
            message.from_user,
            referred_by
        )

        reset_cycle_if_needed(
            message.from_user.id
        )


        welcome = get_setting(
            "welcome",
            "🎉 Welcome!"
        )


        bot.send_message(
            message.chat.id,
            welcome,
            reply_markup=main_keyboard(
                message.from_user.id
            )
        )


    except Exception:

        logger.exception(
            "Start error"
        )


        bot.send_message(
            message.chat.id,
            "❌ /start process করা যায়নি। "
            "আবার /start দিন।"
        )


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
    if not can(message.from_user.id,"vip"):
        bot.send_message(message.chat.id,"⛔ Access denied.",reply_markup=admin_keyboard()); return
    with DB_LOCK:
        conn=db()
        try: rows=conn.execute("SELECT * FROM uid_submissions WHERE status='PENDING' ORDER BY id ASC LIMIT 20").fetchall()
        finally: conn.close()
    if not rows:
        clear_state(message.from_user.id); bot.send_message(message.chat.id,"📭 কোনো pending UID নেই.",reply_markup=admin_keyboard()); return
    buttons=[[f"🆔 #{r['id']} • {r['user_id']}"] for r in rows]
    buttons.append(["🔙 Back","🏠 Main Menu"])
    STATES[message.from_user.id]={"action":"uid_select"}
    bot.send_message(message.chat.id,"🆔 <b>PENDING UID</b>\n\nএকটি UID নির্বাচন করুন:",reply_markup=make_keyboard(buttons))

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
    if not can(message.from_user.id,"withdraw"):
        bot.send_message(message.chat.id,"⛔ Access denied.",reply_markup=admin_keyboard()); return
    with DB_LOCK:
        conn=db()
        try: rows=conn.execute("SELECT * FROM withdrawals WHERE status='PENDING' ORDER BY id ASC LIMIT 20").fetchall()
        finally: conn.close()
    if not rows:
        clear_state(message.from_user.id); bot.send_message(message.chat.id,"💸 <b>PENDING WITHDRAWALS</b>\n\nNone.",reply_markup=admin_keyboard()); return
    buttons=[[f"💸 #{r['id']} • ${r['amount_cents']/100:.2f} • {r['user_id']}"] for r in rows]
    buttons.append(["🔙 Back","🏠 Main Menu"])
    STATES[message.from_user.id]={"action":"withdraw_select"}
    bot.send_message(message.chat.id,"💸 <b>PENDING WITHDRAWALS</b>\n\nএকটি withdrawal নির্বাচন করুন:",reply_markup=make_keyboard(buttons))

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
    if not is_master(message.from_user.id):
        bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard()); return
    STATES[message.from_user.id]={"action":"subadmin_menu"}
    bot.send_message(message.chat.id,"🛡️ <b>SUB-ADMIN MANAGEMENT</b>",reply_markup=make_keyboard([["➕ Add Sub Admin","📋 Sub-admin List"],["🗑 Remove Sub Admin"],["🔙 Back","🏠 Main Menu"]]))

def admin_notify_targets(message):
    if not is_master(message.from_user.id):
        bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard()); return
    STATES[message.from_user.id]={"action":"notify_menu"}
    bot.send_message(message.chat.id,"🎯 <b>NOTIFICATION TARGETS</b>\n\nTarget যোগ/ম্যানেজ করতে নিচের button ব্যবহার করুন।",reply_markup=make_keyboard([["➕ Add Target","📋 Target List"],["🧪 Test Target","🗑 Remove Target"],["🔔 Auto Notification ON/OFF"],["🔙 Back","🏠 Main Menu"]]))

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
    if not is_master(message.from_user.id):
        bot.send_message(message.chat.id,"⛔ Master Admin only.",reply_markup=admin_keyboard()); return
    STATES[message.from_user.id]={"action":"text_menu"}
    bot.send_message(message.chat.id,"📝 <b>BOT TEXT EDITOR</b>\n\nCategory নির্বাচন করুন:",reply_markup=make_keyboard([["👋 Welcome","📊 Future Signal"],["⚡ Live Signal","💰 Money Management"],["⭐ VIP","🆔 UID"],["💵 Wallet","💸 Withdraw"],["👥 Referral","🔔 Notification"],["📜 Trading Rules","📢 Broadcast"],["✅ WIN","❌ LOSS"],["⏭️ SKIP","⚠️ Error Messages"],["🔔 Reminders","✨ Feature Text"],["🔙 Back","🏠 Main Menu"]]))

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


    if text == "👥 Referral History":
        return admin_referral_history(message)

    if text == "🛡️ Security Logs":
        return admin_security_logs(message)

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

def handle_state(message):

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

            quotex_uid = text


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
                        SELECT user_id,status
                        FROM uid_submissions
                        WHERE lower(trim(quotex_uid))=lower(trim(?))
                          AND status IN('PENDING','APPROVED')
                        LIMIT 1
                        """,
                        (quotex_uid,)
                    ).fetchone()

                    if duplicate and duplicate["user_id"] != user_id:
                        raise ValueError(
                            f"এই Quotex UID অন্য Telegram account-এ already linked/pending: {duplicate['user_id']}"
                        )

                    existing_user_uid = conn.execute(
                        """SELECT quotex_uid,status FROM uid_submissions
                           WHERE user_id=? AND status IN('PENDING','APPROVED')
                           ORDER BY id DESC LIMIT 1""",
                        (user_id,)
                    ).fetchone()
                    if existing_user_uid:
                        raise ValueError("আপনার UID already pending/approved. নতুন UID submit করা যাবে না.")

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

                    conn.execute(
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
                        SELECT *
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
                        live_message
                    )

                except Exception:

                    pass


            for target in targets:

                try:
                    if get_setting("target_notify", "ON") != "ON":
                        continue
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
        # BUTTON-BASED ADMIN FLOWS
        # ====================================================
        if action == "uid_select":
            m = re.match(r"🆔 #(\d+)", text)
            if not m:
                raise ValueError("একটি Pending UID button নির্বাচন করুন.")
            sid = int(m.group(1))
            with DB_LOCK:
                conn = db()
                try:
                    row = conn.execute("SELECT * FROM uid_submissions WHERE id=? AND status='PENDING'", (sid,)).fetchone()
                finally:
                    conn.close()
            if not row:
                raise ValueError("Pending UID পাওয়া যায়নি.")
            STATES[user_id] = {"action":"uid_review_buttons", "submission_id":sid}
            bot.send_message(message.chat.id,
                f"🆔 <b>UID REVIEW</b>\n\nID: <code>{sid}</code>\nUser: <code>{row['user_id']}</code>\nUID: <code>{escape(row['quotex_uid'])}</code>",
                reply_markup=make_keyboard([["✅ Approve UID","❌ Reject UID"],["🔙 Back","🏠 Main Menu"]]))
            return True

        if action == "uid_review_buttons":
            sid = state["submission_id"]
            if text not in ("✅ Approve UID", "❌ Reject UID"):
                raise ValueError("Approve/Reject button ব্যবহার করুন.")
            with DB_LOCK:
                conn = db()
                try:
                    row = conn.execute("SELECT * FROM uid_submissions WHERE id=? AND status='PENDING'", (sid,)).fetchone()
                finally:
                    conn.close()
            if not row:
                raise ValueError("UID already reviewed.")
            if text == "✅ Approve UID":
                with DB_LOCK:
                    conn = db()
                    try:
                        conflict = conn.execute(
                            "SELECT id,user_id FROM uid_submissions WHERE lower(trim(quotex_uid))=lower(trim(?)) AND id<>? AND status IN ('PENDING','APPROVED')",
                            (row["quotex_uid"], sid)).fetchone()
                        if conflict:
                            raise ValueError(f"এই Quotex UID অন্য Telegram account-এ already linked: {conflict['user_id']}")
                        vip_until = (now_bd() + timedelta(days=30)).isoformat()
                        conn.execute("UPDATE uid_submissions SET status='APPROVED',reviewed_at=? WHERE id=?", (utc_iso(now_utc()), sid))
                        conn.execute("UPDATE users SET status='VIP',vip_until=? WHERE user_id=?", (vip_until, row["user_id"]))
                        conn.commit()
                    finally:
                        conn.close()
                audit_admin(user_id, "APPROVE_UID", "UID", sid, f"UID {row['quotex_uid']} -> TG {row['user_id']}")
                try:
                    bot.send_message(row["user_id"], "⭐ আপনার VIP approved হয়েছে.", reply_markup=main_keyboard(row["user_id"]))
                except Exception:
                    pass
            else:
                with DB_LOCK:
                    conn = db()
                    try:
                        conn.execute("UPDATE uid_submissions SET status='REJECTED',reviewed_at=? WHERE id=?", (utc_iso(now_utc()), sid))
                        conn.commit()
                    finally:
                        conn.close()
                audit_admin(user_id, "REJECT_UID", "UID", sid, f"UID {row['quotex_uid']} rejected")
                try:
                    bot.send_message(row["user_id"], "❌ আপনার UID rejected হয়েছে.", reply_markup=main_keyboard(row["user_id"]))
                except Exception:
                    pass
            clear_state(user_id)
            bot.send_message(message.chat.id, "✅ UID review complete.", reply_markup=admin_keyboard())
            return True

        if action == "withdraw_select":
            m = re.match(r"💸 #(\d+)", text)
            if not m:
                raise ValueError("একটি withdrawal button নির্বাচন করুন.")
            wid = int(m.group(1))
            with DB_LOCK:
                conn = db()
                try:
                    w = conn.execute("SELECT * FROM withdrawals WHERE id=? AND status='PENDING'", (wid,)).fetchone()
                finally:
                    conn.close()
            if not w:
                raise ValueError("Pending withdrawal পাওয়া যায়নি.")
            risk, note, report = withdrawal_risk(w)
            save_withdrawal_risk(wid, risk, note)
            lines = [
                f"💸 <b>WITHDRAWAL #{wid}</b>",
                f"User: <code>{w['user_id']}</code>",
                f"Amount: <b>{money(w['amount_cents'])}</b>",
                f"Method: {escape(w['method'])}",
                f"Account: <code>{escape(w['account'])}</code>",
                f"\n👥 Referrals: {report['total']} | Valid: {report['valid']}",
                f"Referral earnings: {money(report['bonus'])}"
            ]
            if report["referred"]:
                lines.append("\n📋 <b>Referred users:</b>")
                lines += [f"• {r['user_id']} @{escape(r['username'] or 'none')} | {r['referral_at'] or 'bonus pending'}" for r in report["referred"][:20]]
            lines.append("\n⚠️ <b>Review status:</b> " + risk)
            if note:
                lines.append(escape(note))
            STATES[user_id] = {"action":"withdraw_review_buttons", "withdrawal_id":wid}
            bot.send_message(message.chat.id, "\n".join(lines), reply_markup=make_keyboard([
                ["🔍 Referral Details", "📜 User History"],
                ["✅ Approve Withdrawal", "❌ Reject Withdrawal"],
                ["🔙 Back", "🏠 Main Menu"]
            ]))
            return True

