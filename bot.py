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

DB_FILE = "bot_database.db"
BACKUP_DIR = "backups"

BD_TZ = ZoneInfo("Asia/Dhaka")
UTC = timezone.utc

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in Railway Variables.")

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
states = {}


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


def today_bd():
    return now_bd().date()


def cycle_key():
    # 2-day calendar cycle
    epoch = datetime(2026, 1, 1, tzinfo=BD_TZ).date()
    days = (today_bd() - epoch).days
    return (epoch + timedelta(days=(days // 2) * 2)).isoformat()


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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():
    with db_lock:
        conn = db()
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                status TEXT DEFAULT 'FREE',
                vip_until TEXT,
                wallet_cents INTEGER DEFAULT 0,
                referred_by INTEGER,
                refs_count INTEGER DEFAULT 0,
                referral_paid INTEGER DEFAULT 0,
                free_cycle TEXT,
                free_used INTEGER DEFAULT 0,
                free_limit INTEGER DEFAULT 4,
                notifications_enabled INTEGER DEFAULT 1,
                blocked INTEGER DEFAULT 0,
                created_at TEXT,
                last_seen TEXT
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_date TEXT NOT NULL,
                signal_time TEXT NOT NULL,
                pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                confidence TEXT DEFAULT '',
                signal_at_utc TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sent INTEGER DEFAULT 0,
                sent_at TEXT,
                UNIQUE(signal_date, signal_time, pair, direction)
            );

            CREATE TABLE IF NOT EXISTS signal_access (
                user_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                accessed_at TEXT NOT NULL,
                PRIMARY KEY(user_id, signal_id)
            );

            CREATE TABLE IF NOT EXISTS signal_votes (
                user_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                vote TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id, signal_id)
            );

            CREATE TABLE IF NOT EXISTS uid_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                uid TEXT NOT NULL,
                status TEXT DEFAULT 'PENDING',
                submitted_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by INTEGER
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_uid_unique
            ON uid_submissions(uid)
            WHERE status IN ('PENDING','APPROVED');

            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount_cents INTEGER NOT NULL,
                method TEXT NOT NULL,
                account TEXT NOT NULL,
                status TEXT DEFAULT 'PENDING',
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
                permissions TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notification_targets (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                enabled INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS live_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                active INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS live_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                pair TEXT,
                direction TEXT,
                confidence TEXT,
                raw_text TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS money_management (
                user_id INTEGER PRIMARY KEY,
                balance_cents INTEGER DEFAULT 0,
                profit_target_cents INTEGER DEFAULT 0,
                loss_limit_cents INTEGER DEFAULT 0,
                base_trade_cents INTEGER DEFAULT 0,
                m1_trade_cents INTEGER DEFAULT 0,
                daily_pl_cents INTEGER DEFAULT 0,
                trades_today INTEGER DEFAULT 0,
                stop_trading INTEGER DEFAULT 0,
                session_losses_cents INTEGER DEFAULT 0,
                session_level INTEGER DEFAULT 0,
                trade_date TEXT
            );
            """)

            defaults = {
                "maintenance": "0",
                "auto_send": "1",
                "send_before_minutes": "5",
                "free_signal_limit": "4",
                "min_withdraw": "5",
                "withdrawals": "1",
                "referral_bonus": "1",
                "live_mode": "1",

                # Editable texts
                "welcome_text":
                    "🎉 <b>WELCOME TO SM QUATEX SURE SHORT</b>\n\n"
                    "আপনার account ready.\n\n"
                    "📊 Future Signal থেকে signal নিতে পারবেন।",

                "maintenance_text":
                    "🛠️ Bot এখন maintenance mode-এ আছে।",

                "no_signal_text":
                    "📭 এখন কোনো upcoming signal নেই।",

                "quota_text":
                    "⛔ আপনার free signal quota শেষ।",

                "vip_text":
                    "⭐ VIP account-এ free signal limit নেই।",

                "notice_text":
                    "📢 <b>NOTICE</b>\n\nAdmin notice এখানে লিখতে পারবেন।",

                "trading_rules_text":
                    "📖 <b>TRADING RULES</b>\n\n"
                    "আপনার trading rules এখানে লিখুন।",

                "help_text":
                    "❓ <b>HELP</b>\n\n"
                    "প্রয়োজনে admin-এর সাথে যোগাযোগ করুন।",

                "signal_footer":
                    "⚠️ Signal confidence একটি estimate; guaranteed result নয়।",

                "win_text": "✅ WIN",
                "loss_text": "❌ LOSS",
                "skip_text": "⏭ SKIP",
            }

            for key, value in defaults.items():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO settings(key,value)
                    VALUES(?,?)
                    """,
                    (key, value)
                )

            # Button labels
            buttons = {
                "btn_future": "📥 Get Signal",
                "btn_live": "⚡ Live Signals",
                "btn_uid": "🆔 Submit Quotex UID",
                "btn_status": "👤 My Status",
                "btn_wallet": "💰 Wallet",
                "btn_withdraw": "💸 Withdraw",
                "btn_referral": "👥 Referral Link",
                "btn_vip": "⭐ VIP Rules",
                "btn_history": "📜 Signal History",
                "btn_notice": "📢 Notice",
                "btn_rules": "📖 Trading Rules",
                "btn_help": "❓ Help",
                "btn_notifications": "🔔 Notifications",
                "btn_money": "💰 Money Management",
                "btn_admin": "👑 Admin Control",
                "btn_back": "🔙 Back",
                "btn_home": "🏠 Main Menu",
                "btn_add_signal": "➕ Add Signal",
                "btn_signal_list": "📊 Signal List",
                "btn_auto_send": "📡 Auto Send",
                "btn_clear": "🗑 Clear Future",
                "btn_pending_uid": "🆔 Pending UID",
                "btn_users": "👥 Users",
                "btn_wallet_adjust": "💳 Wallet Adjust",
                "btn_withdrawals": "💸 Withdrawals",
                "btn_broadcast": "📢 Broadcast",
                "btn_settings": "⚙️ Settings",
                "btn_edit_text": "📝 Edit Text",
                "btn_live_session": "⚡ Live Session",
                "btn_analytics": "📈 Analytics",
                "btn_subadmins": "🛡 Sub-admins",
                "btn_targets": "📣 Notification Targets",
                "btn_vip_manage": "⭐ Manage VIP",
            }

            for key, value in buttons.items():
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
# BUTTON SYSTEM
# ============================================================

def label(key):
    return get_setting(key, key)


def action_of(text):
    if not text:
        return None

    keys = [
        "btn_future", "btn_live", "btn_uid", "btn_status",
        "btn_wallet", "btn_withdraw", "btn_referral", "btn_vip",
        "btn_history", "btn_notice", "btn_rules", "btn_help",
        "btn_notifications", "btn_money", "btn_admin",
        "btn_back", "btn_home",

        "btn_add_signal", "btn_signal_list", "btn_auto_send",
        "btn_clear", "btn_pending_uid", "btn_users",
        "btn_wallet_adjust", "btn_withdrawals", "btn_broadcast",
        "btn_settings", "btn_edit_text", "btn_live_session",
        "btn_analytics", "btn_subadmins", "btn_targets",
        "btn_vip_manage"
    ]

    for key in keys:
        if text == label(key):
            return key

    # Fixed actions
    if text in ("➕", "ADD"):
        return "btn_add_signal"

    return None


def reply_keyboard(rows):
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )

    for row in rows:
        kb.row(*row)

    return kb


def main_keyboard(user_id):
    rows = [
        [label("btn_future"), label("btn_live")],
        [label("btn_status"), label("btn_wallet")],
        [label("btn_withdraw"), label("btn_referral")],
        [label("btn_history"), label("btn_notifications")],
        [label("btn_money")],
        [label("btn_uid"), label("btn_vip")],
        [label("btn_notice"), label("btn_rules")],
        [label("btn_help")]
    ]

    if is_admin(user_id):
        rows.append([label("btn_admin")])

    return reply_keyboard(rows)


def back_keyboard():
    return reply_keyboard([
        [label("btn_back"), label("btn_home")]
    ])


def user_sub_keyboard():
    return reply_keyboard([
        [label("btn_money")],
        [label("btn_back"), label("btn_home")]
    ])


def admin_keyboard():
    return reply_keyboard([
        [label("btn_add_signal"), label("btn_signal_list")],
        [label("btn_auto_send"), label("btn_clear")],
        [label("btn_pending_uid"), label("btn_users")],
        [label("btn_wallet_adjust"), label("btn_withdrawals")],
        [label("btn_broadcast"), label("btn_settings")],
        [label("btn_edit_text"), label("btn_live_session")],
        [label("btn_analytics"), label("btn_subadmins")],
        [label("btn_targets"), label("btn_vip_manage")],
        [label("btn_home")]
    ])


# ============================================================
# USERS
# ============================================================

def register_user(tg_user, referred_by=None):
    uid = tg_user.id
    now = utc_iso(now_utc())

    with db_lock:
        conn = db()
        try:
            old = conn.execute(
                "SELECT user_id FROM users WHERE user_id=?",
                (uid,)
            ).fetchone()

            if old:
                conn.execute(
                    """
                    UPDATE users
                    SET username=?, first_name=?, last_seen=?, blocked=0
                    WHERE user_id=?
                    """,
                    (
                        tg_user.username,
                        tg_user.first_name,
                        now,
                        uid
                    )
                )
            else:
                if referred_by == uid:
                    referred_by = None

                if referred_by:
                    exists = conn.execute(
                        "SELECT user_id FROM users WHERE user_id=?",
                        (referred_by,)
                    ).fetchone()

                    if not exists:
                        referred_by = None

                conn.execute(
                    """
                    INSERT INTO users(
                        user_id,username,first_name,status,
                        referred_by,free_cycle,free_used,
                        free_limit,created_at,last_seen
                    )
                    VALUES(?,?,?,'FREE',?,?,?,?,?,?)
                    """,
                    (
                        uid,
                        tg_user.username,
                        tg_user.first_name,
                        referred_by,
                        cycle_key(),
                        0,
                        int(get_setting("free_signal_limit", "4")),
                        now,
                        now
                    )
                )

            conn.commit()

        finally:
            conn.close()


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


def ensure_cycle(user_id):
    user = get_user(user_id)
    if not user:
        return

    ck = cycle_key()

    if user["free_cycle"] != ck:
        with db_lock:
            conn = db()
            try:
                conn.execute(
                    """
                    UPDATE users
                    SET free_cycle=?, free_used=0
                    WHERE user_id=?
                    """,
                    (ck, user_id)
                )
                conn.commit()
            finally:
                conn.close()


def is_vip(user_id):
    u = get_user(user_id)

    if not u:
        return False

    if u["status"] != "VIP":
        return False

    if u["vip_until"]:
        try:
            until = datetime.fromisoformat(u["vip_until"])
            if until <= now_utc():
                with db_lock:
                    conn = db()
                    try:
                        conn.execute(
                            """
                            UPDATE users
                            SET status='FREE'
                            WHERE user_id=?
                            """,
                            (user_id,)
                        )
                        conn.commit()
                    finally:
                        conn.close()

                return False
        except Exception:
            pass

    return True


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
    "live"
}


def is_master(user_id):
    return user_id == ADMIN_ID


def is_admin(user_id):
    if is_master(user_id):
        return True

    with db_lock:
        conn = db()
        try:
            row = conn.execute(
                "SELECT user_id FROM admins WHERE user_id=?",
                (user_id,)
            ).fetchone()
            return bool(row)
        finally:
            conn.close()


def permissions(user_id):
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


def can(user_id, perm):
    return is_master(user_id) or perm in permissions(user_id)


def save_admin(uid, perms):
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
                (uid, ",".join(sorted(perms)))
            )
            conn.commit()
        finally:
            conn.close()


# ============================================================
# SIGNAL PARSER
# ============================================================

def normalize_direction(value):
    v = value.strip().upper()

    v = v.replace("⬆️", "UP")
    v = v.replace("⬆", "UP")
    v = v.replace("🔼", "UP")

    v = v.replace("⬇️", "DOWN")
    v = v.replace("⬇", "DOWN")
    v = v.replace("🔽", "DOWN")

    if v in {
        "UP",
        "BUY",
        "CALL",
        "CALLS",
        "UPWARD"
    }:
        return "UP"

    if v in {
        "DOWN",
        "SELL",
        "PUT",
        "PUTS",
        "DOWNWARD"
    }:
        return "DOWN"

    return None


def parse_signal_line(line, upload_date=None):
    line = line.strip()

    if not line:
        return None

    # Ignore header
    upper = line.upper()

    if (
        "ACCURATE" in upper
        or upper.startswith("SIGNAL")
        or upper.startswith("RED DRAGON")
        or upper.startswith("FUTURE")
    ):
        return None

    # YYYY-MM-DD HH:MM - PAIR - DIRECTION
    m = re.match(
        r"""
        ^\s*
        (\d{4}-\d{2}-\d{2})
        [ T]+
        (\d{1,2}):(\d{2})
        \s*[-|]\s*
        ([^-|]+?)
        \s*[-|]\s*
        ([A-Za-z⬆⬇️🔼🔽]+)
        \s*$
        """,
        line,
        re.X
    )

    if m:
        date_s = m.group(1)
        hour = int(m.group(2))
        minute = int(m.group(3))
        pair = m.group(4).strip().upper()
        direction = normalize_direction(m.group(5))

        if direction is None:
            return None

        try:
            target = datetime.strptime(
                f"{date_s} {hour:02d}:{minute:02d}",
                "%Y-%m-%d %H:%M"
            ).replace(tzinfo=BD_TZ)
        except ValueError:
            return None

    else:
        # HH:MM - PAIR - DIRECTION
        m = re.match(
            r"""
            ^\s*
            (\d{1,2}):(\d{2})
            \s*[-|]\s*
            ([^-|]+?)
            \s*[-|]\s*
            ([A-Za-z⬆⬇️🔼🔽]+)
            \s*$
            """,
            line,
            re.X
        )

        if not m:
            return None

        hour = int(m.group(1))
        minute = int(m.group(2))
        pair = m.group(3).strip().upper()
        direction = normalize_direction(m.group(4))

        if direction is None:
            return None

        if hour > 23 or minute > 59:
            return None

        date_s = (
            upload_date.isoformat()
            if upload_date
            else today_bd().isoformat()
        )

        target = datetime.combine(
            upload_date or today_bd(),
            datetime.min.time(),
            tzinfo=BD_TZ
        ).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0
        )

    # VERY IMPORTANT:
    # Never move old signal to tomorrow.
    # Old = rejected.
    if target <= now_bd():
        return {
            "expired": True,
            "date": target.date().isoformat(),
            "time": target.strftime("%H:%M"),
            "pair": pair,
            "direction": direction
        }

    return {
        "expired": False,
        "date": target.date().isoformat(),
        "time": target.strftime("%H:%M"),
        "pair": pair,
        "direction": direction,
        "target": target
    }


def add_bulk_signals(text):
    added = 0
    duplicate = 0
    expired = 0
    invalid = 0

    upload_date = today_bd()

    with db_lock:
        conn = db()

        try:
            for raw_line in text.splitlines():

                if not raw_line.strip():
                    continue

                parsed = parse_signal_line(
                    raw_line,
                    upload_date
                )

                # Header / blank
                if parsed is None:
                    if (
                        "-" not in raw_line
                        or not re.search(r"\d{1,2}:\d{2}", raw_line)
                    ):
                        continue

                    invalid += 1
                    continue

                if parsed.get("expired"):
                    expired += 1
                    continue

                target = parsed["target"]

                try:
                    conn.execute(
                        """
                        INSERT INTO signals(
                            signal_date,
                            signal_time,
                            pair,
                            direction,
                            signal_at_utc,
                            created_at
                        )
                        VALUES(?,?,?,?,?,?)
                        """,
                        (
                            parsed["date"],
                            parsed["time"],
                            parsed["pair"],
                            parsed["direction"],
                            utc_iso(target),
                            utc_iso(now_utc())
                        )
                    )
                    added += 1

                except sqlite3.IntegrityError:
                    duplicate += 1

            conn.commit()

        finally:
            conn.close()

    return added, duplicate, expired, invalid


# ============================================================
# SIGNAL ACCESS
# ============================================================

def get_next_signal(user_id):
    ensure_cycle(user_id)

    with db_lock:
        conn = db()

        try:
            now = utc_iso(now_utc())

            return conn.execute(
                """
                SELECT s.*
                FROM signals s
                WHERE s.signal_at_utc > ?
                AND NOT EXISTS(
                    SELECT 1
                    FROM signal_access a
                    WHERE a.user_id=?
                    AND a.signal_id=s.id
                )
                ORDER BY s.signal_at_utc ASC
                LIMIT 1
                """,
                (now, user_id)
            ).fetchone()

        finally:
            conn.close()


def access_signal(user_id, signal_id):
    with db_lock:
        conn = db()

        try:
            signal = conn.execute(
                """
                SELECT *
                FROM signals
                WHERE id=?
                """,
                (signal_id,)
            ).fetchone()

            user = conn.execute(
                """
                SELECT *
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            if not signal or not user:
                return False, "not_found"

            # Hard past check
            if bd_from_iso(signal["signal_at_utc"]) <= now_bd():
                return False, "expired"

            exists = conn.execute(
                """
                SELECT 1
                FROM signal_access
                WHERE user_id=? AND signal_id=?
                """,
                (user_id, signal_id)
            ).fetchone()

            if exists:
                return False, "already"

            if not is_vip(user_id):

                limit = int(
                    user["free_limit"]
                    or get_setting("free_signal_limit", "4")
                )

                used = user["free_used"]

                if user["free_cycle"] != cycle_key():
                    used = 0

                    conn.execute(
                        """
                        UPDATE users
                        SET free_cycle=?, free_used=0
                        WHERE user_id=?
                        """,
                        (cycle_key(), user_id)
                    )

                if used >= limit:
                    conn.commit()
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


def signal_text(signal):
    direction = signal["direction"]

    if direction == "UP":
        arrow = "🟢⬆️"
        word = "BUY / UP"
    else:
        arrow = "🔴⬇️"
        word = "SELL / DOWN"

    t = bd_from_iso(signal["signal_at_utc"])

    confidence = signal["confidence"]

    confidence_line = ""
    if confidence:
        confidence_line = (
            f"\n🎯 Confidence: <b>{escape(confidence)}</b>"
        )

    return (
        "📊 <b>SM QUATEX SURE SHORT</b>\n\n"
        f"📅 Date: <b>{t.strftime('%d-%m-%Y')}</b>\n"
        f"⏰ Time: <b>{t.strftime('%H:%M')}</b>\n"
        f"💱 Pair: <b>{escape(signal['pair'])}</b>\n"
        f"📈 Direction: {arrow} <b>{word}</b>"
        f"{confidence_line}\n\n"
        f"{get_setting('signal_footer')}"
    )


# ============================================================
# REFERRAL
# ============================================================

def referral_bonus(user_id):
    with db_lock:
        conn = db()

        try:
            user = conn.execute(
                """
                SELECT referred_by,referral_paid
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            if not user:
                return

            if not user["referred_by"] or user["referral_paid"]:
                return

            amount = int(
                float(get_setting("referral_bonus", "1")) * 100
            )

            ref = user["referred_by"]

            conn.execute(
                """
                UPDATE users
                SET wallet_cents=wallet_cents+?,
                    refs_count=refs_count+1
                WHERE user_id=?
                """,
                (amount, ref)
            )

            bal = conn.execute(
                """
                SELECT wallet_cents
                FROM users
                WHERE user_id=?
                """,
                (ref,)
            ).fetchone()["wallet_cents"]

            conn.execute(
                """
                INSERT INTO wallet_transactions(
                    user_id,type,amount_cents,
                    balance_after_cents,note,created_at
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    ref,
                    "REFERRAL",
                    amount,
                    bal,
                    f"Referral from {user_id}",
                    utc_iso(now_utc())
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

            conn.commit()

            try:
                bot.send_message(
                    ref,
                    f"🎉 Referral bonus added: "
                    f"<b>${amount/100:.2f}</b>"
                )
            except Exception:
                pass

        finally:
            conn.close()


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
# MONEY MANAGEMENT
# ============================================================

def mm_get(user_id):
    with db_lock:
        conn = db()

        try:
            row = conn.execute(
                """
                SELECT *
                FROM money_management
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            if not row:
                today = today_bd().isoformat()

                conn.execute(
                    """
                    INSERT INTO money_management(
                        user_id,trade_date
                    )
                    VALUES(?,?)
                    """,
                    (user_id, today)
                )

                conn.commit()

                row = conn.execute(
                    """
                    SELECT *
                    FROM money_management
                    WHERE user_id=?
                    """,
                    (user_id,)
                ).fetchone()

            if row["trade_date"] != today_bd().isoformat():
                conn.execute(
                    """
                    UPDATE money_management
                    SET daily_pl_cents=0,
                        trades_today=0,
                        stop_trading=0,
                        session_losses_cents=0,
                        session_level=0,
                        trade_date=?
                    WHERE user_id=?
                    """,
                    (
                        today_bd().isoformat(),
                        user_id
                    )
                )

                conn.commit()

                row = conn.execute(
                    """
                    SELECT *
                    FROM money_management
                    WHERE user_id=?
                    """,
                    (user_id,)
                ).fetchone()

            return row

        finally:
            conn.close()


def mm_save(user_id, **kwargs):
    mm_get(user_id)

    allowed = {
        "balance_cents",
        "profit_target_cents",
        "loss_limit_cents",
        "base_trade_cents",
        "m1_trade_cents",
        "stop_trading"
    }

    kwargs = {
        k: v for k, v in kwargs.items()
        if k in allowed
    }

    if not kwargs:
        return

    sql = ", ".join(
        f"{key}=?" for key in kwargs
    )

    values = list(kwargs.values())
    values.append(user_id)

    with db_lock:
        conn = db()

        try:
            conn.execute(
                f"""
                UPDATE money_management
                SET {sql}
                WHERE user_id=?
                """,
                values
            )

            conn.commit()

        finally:
            conn.close()


# ============================================================
# UID
# ============================================================

def submit_uid(user_id, uid_value):
    uid_value = uid_value.strip()

    if len(uid_value) < 3:
        return False, "invalid"

    if is_vip(user_id):
        return False, "vip"

    with db_lock:
        conn = db()

        try:
            existing = conn.execute(
                """
                SELECT user_id,status
                FROM uid_submissions
                WHERE uid=?
                AND status IN ('PENDING','APPROVED')
                """,
                (uid_value,)
            ).fetchone()

            if existing and existing["user_id"] != user_id:
                return False, "duplicate"

            pending = conn.execute(
                """
                SELECT id
                FROM uid_submissions
                WHERE user_id=?
                AND status='PENDING'
                """,
                (user_id,)
            ).fetchone()

            if pending:
                return False, "pending"

            cur = conn.execute(
                """
                INSERT INTO uid_submissions(
                    user_id,uid,status,submitted_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    user_id,
                    uid_value,
                    "PENDING",
                    utc_iso(now_utc())
                )
            )

            sid = cur.lastrowid
            conn.commit()

        finally:
            conn.close()

    states.pop(user_id, None)

    bot.send_message(
        ADMIN_ID,
        "🆔 <b>NEW UID</b>\n\n"
        f"User: <code>{user_id}</code>\n"
        f"UID: <code>{escape(uid_value)}</code>\n"
        f"Submission ID: <code>{sid}</code>\n\n"
        "Use Pending UID menu to approve/reject."
    )

    return True, "ok"


# ============================================================
# MAIN START
# ============================================================

@bot.message_handler(commands=["start"])
def start_cmd(message):
    try:
        ref = None

        parts = message.text.split(maxsplit=1)

        if len(parts) == 2 and parts[1].startswith("ref_"):
            try:
                ref = int(parts[1][4:])
            except Exception:
                pass

        register_user(
            message.from_user,
            referred_by=ref
        )

        text = get_setting("welcome_text")

        bot.send_message(
            message.chat.id,
            text,
            reply_markup=main_keyboard(
                message.from_user.id
            )
        )

    except Exception:
        logger.exception("start error")

        bot.send_message(
            message.chat.id,
            "❌ Error. আবার /start দিন।"
        )


@bot.message_handler(commands=["cancel"])
def cancel_cmd(message):
    states.pop(
        message.from_user.id,
        None
    )

    bot.send_message(
        message.chat.id,
        "❌ Operation cancelled.",
        reply_markup=main_keyboard(
            message.from_user.id
        )
    )


# ============================================================
# HOME / BACK
# ============================================================

def go_home(message):
    states.pop(
        message.from_user.id,
        None
    )

    bot.send_message(
        message.chat.id,
        "🏠 <b>MAIN MENU</b>",
        reply_markup=main_keyboard(
            message.from_user.id
        )
    )


def go_back(message):
    uid = message.from_user.id
    st = states.get(uid)

    if st and st.get("parent") == "admin":
        states.pop(uid, None)

        bot.send_message(
            message.chat.id,
            "👑 <b>ADMIN CONTROL</b>",
            reply_markup=admin_keyboard()
        )
        return

    if st and st.get("parent") == "money":
        states.pop(uid, None)

        bot.send_message(
            message.chat.id,
            "💰 <b>MONEY MANAGEMENT</b>",
            reply_markup=user_sub_keyboard()
        )
        return

    states.pop(uid, None)

    bot.send_message(
        message.chat.id,
        "🏠 <b>MAIN MENU</b>",
        reply_markup=main_keyboard(uid)
    )


# ============================================================
# USER ACTIONS
# ============================================================

def handle_future(message):
    uid = message.from_user.id

    if get_setting("maintenance", "0") == "1" and not is_admin(uid):
        bot.send_message(
            message.chat.id,
            get_setting("maintenance_text")
        )
        return

    register_user(message.from_user)
    ensure_cycle(uid)

    signal = get_next_signal(uid)

    if not signal:
        bot.send_message(
            message.chat.id,
            get_setting("no_signal_text")
        )
        return

    ok, reason = access_signal(
        uid,
        signal["id"]
    )

    if not ok:
        if reason == "limit":
            bot.send_message(
                message.chat.id,
                get_setting("quota_text")
            )
        elif reason == "expired":
            bot.send_message(
                message.chat.id,
                "⏰ এই signal-এর সময় চলে গেছে।"
            )
        else:
            bot.send_message(
                message.chat.id,
                "❌ Signal পাওয়া যায়নি।"
            )
        return

    referral_bonus(uid)

    user = get_user(uid)

    if is_vip(uid):
        quota = "♾️ VIP Unlimited"
    else:
        limit = user["free_limit"]
        remaining = max(
            0,
            limit - user["free_used"]
        )
        quota = (
            f"🎟️ Remaining: "
            f"<b>{remaining}/{limit}</b>"
        )

    bot.send_message(
        message.chat.id,
        signal_text(signal)
        + "\n\n"
        + quota,
        reply_markup=reply_keyboard([
            [get_setting("win_text"),
             get_setting("loss_text")],
            [get_setting("skip_text")],
            [label("btn_back"), label("btn_home")]
        ])
    )

    states[uid] = {
        "action": "signal_result",
        "signal_id": signal["id"],
        "parent": "user"
    }


def handle_signal_result(message):
    uid = message.from_user.id
    st = states.get(uid)

    if not st or not st.get("signal_id"):
        bot.send_message(
            message.chat.id,
            "❌ কোনো active signal নেই।"
        )
        return

    text = message.text

    if text == get_setting("win_text"):
        vote = "WIN"
    elif text == get_setting("loss_text"):
        vote = "LOSS"
    elif text == get_setting("skip_text"):
        vote = "SKIP"
    else:
        return

    sid = st["signal_id"]

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO signal_votes(
                    user_id,signal_id,vote,created_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    uid,
                    sid,
                    vote,
                    utc_iso(now_utc())
                )
            )

            conn.commit()

        finally:
            conn.close()

    states.pop(uid, None)

    bot.send_message(
        message.chat.id,
        f"✅ Result saved: <b>{vote}</b>",
        reply_markup=main_keyboard(uid)
    )


# ============================================================
# USER TEXT ACTIONS
# ============================================================

@bot.message_handler(content_types=["text"])
def all_text_handler(message):
    uid = message.from_user.id
    text = (message.text or "").strip()

    if text == label("btn_home"):
        go_home(message)
        return

    if text == label("btn_back"):
        go_back(message)
        return

    register_user(message.from_user)

    # Active interactive state always gets priority.
    st = states.get(uid)

    if st:
        action = st.get("action")

        if action == "signal_result":
            handle_signal_result(message)
            return

        if action == "uid":
            ok, reason = submit_uid(
                uid,
                text
            )

            if ok:
                bot.send_message(
                    message.chat.id,
                    "✅ UID submitted.\n"
                    "Admin verification-এর জন্য অপেক্ষা করুন।",
                    reply_markup=main_keyboard(uid)
                )
            else:
                messages = {
                    "invalid": "❌ UID invalid.",
                    "vip": "⭐ আপনি ইতিমধ্যে VIP.",
                    "duplicate":
                        "❌ এই UID অন্য account-এ already submitted.",
                    "pending":
                        "⏳ আপনার UID already pending."
                }

                bot.send_message(
                    message.chat.id,
                    messages.get(
                        reason,
                        "❌ UID submit করা যায়নি।"
                    )
                )

            return

        if action == "mm_balance":
            save_mm_input(
                message,
                "balance_cents"
            )
            return

        if action == "mm_profit":
            save_mm_input(
                message,
                "profit_target_cents"
            )
            return

        if action == "mm_loss":
            save_mm_input(
                message,
                "loss_limit_cents"
            )
            return

        if action == "mm_base":
            save_mm_input(
                message,
                "base_trade_cents"
            )
            return

        if action == "mm_m1":
            save_mm_input(
                message,
                "m1_trade_cents"
            )
            return

        if action == "withdraw":
            save_withdraw(message)
            return

        if action == "wallet_adjust":
            save_wallet_adjust(message)
            return

        if action == "broadcast":
            send_broadcast(message)
            return

        if action == "notice":
            set_setting(
                "notice_text",
                text
            )

            states.pop(uid, None)

            bot.send_message(
                message.chat.id,
                "✅ Notice updated.",
                reply_markup=admin_keyboard()
            )
            return

        if action == "edit_text":
            save_edited_text(message)
            return

        if action == "add_signal":
            added, dup, expired, invalid = add_bulk_signals(
                text
            )

            states.pop(uid, None)

            bot.send_message(
                message.chat.id,
                "📥 <b>SIGNAL IMPORT</b>\n\n"
                f"✅ Added: <b>{added}</b>\n"
                f"♻️ Duplicate: <b>{dup}</b>\n"
                f"⏰ Expired/old: <b>{expired}</b>\n"
                f"❌ Invalid: <b>{invalid}</b>\n\n"
                "BUY/CALL/UP → UP\n"
                "SELL/PUT/DOWN → DOWN",
                reply_markup=admin_keyboard()
            )
            return

        if action == "live_signal":
            save_live_signal(message)
            return

        if action == "subadmin":
            save_subadmin_input(message)
            return

        if action == "target":
            save_target(message)
            return

        if action == "vip_manage":
            save_vip_manage(message)
            return

        if action == "free_limit":
            try:
                value = int(text)

                with db_lock:
                    conn = db()
                    try:
                        conn.execute(
                            """
                            UPDATE users
                            SET free_limit=?
                            """,
                            (value,)
                        )
                        conn.commit()
                    finally:
                        conn.close()

                states.pop(uid, None)

                bot.send_message(
                    message.chat.id,
                    f"✅ Global free limit set to <b>{value}</b>.",
                    reply_markup=admin_keyboard()
                )

            except Exception:
                bot.send_message(
                    message.chat.id,
                    "❌ Number দিন।"
                )

            return

        if action == "send_before":
            try:
                value = int(text)

                if value < 0 or value > 120:
                    raise ValueError

                set_setting(
                    "send_before_minutes",
                    value
                )

                states.pop(uid, None)

                bot.send_message(
                    message.chat.id,
                    f"✅ Auto-send time: <b>{value} minutes before</b>.",
                    reply_markup=admin_keyboard()
                )

            except Exception:
                bot.send_message(
                    message.chat.id,
                    "❌ 0 থেকে 120-এর মধ্যে minute দিন।"
                )

            return

        # Unknown state
        states.pop(uid, None)

    # --------------------------------------------------------
    # USER BUTTONS
    # --------------------------------------------------------

    act = action_of(text)

    if act == "btn_future":
        handle_future(message)
        return

    if act == "btn_live":
        show_live(message)
        return

    if act == "btn_uid":
        start_uid(message)
        return

    if act == "btn_status":
        show_status(message)
        return

    if act == "btn_wallet":
        show_wallet(message)
        return

    if act == "btn_withdraw":
        start_withdraw(message)
        return

    if act == "btn_referral":
        show_referral(message)
        return

    if act == "btn_vip":
        bot.send_message(
            message.chat.id,
            get_setting("trading_rules_text")
        )
        return

    if act == "btn_history":
        show_history(message)
        return

    if act == "btn_notice":
        bot.send_message(
            message.chat.id,
            get_setting("notice_text")
        )
        return

    if act == "btn_rules":
        bot.send_message(
            message.chat.id,
            get_setting("trading_rules_text")
        )
        return

    if act == "btn_help":
        bot.send_message(
            message.chat.id,
            get_setting("help_text")
        )
        return

    if act == "btn_notifications":
        toggle_notifications(message)
        return

    if act == "btn_money":
        money_menu(message)
        return

    if act == "btn_admin":
        if not is_admin(uid):
            return

        bot.send_message(
            message.chat.id,
            "👑 <b>ADMIN CONTROL</b>",
            reply_markup=admin_keyboard()
        )
        return

    # --------------------------------------------------------
    # ADMIN BUTTONS
    # --------------------------------------------------------

    if not is_admin(uid):
        return

    if act == "btn_add_signal":
        if can(uid, "signals"):
            start_add_signal(message)
        return

    if act == "btn_signal_list":
        if can(uid, "signals"):
            signal_list(message)
        return

    if act == "btn_auto_send":
        if can(uid, "settings"):
            toggle_auto_send(message)
        return

    if act == "btn_clear":
        if is_master(uid):
            clear_future(message)
        return

    if act == "btn_pending_uid":
        if can(uid, "uid"):
            pending_uids(message)
        return

    if act == "btn_users":
        if can(uid, "users"):
            user_list(message)
        return

    if act == "btn_wallet_adjust":
        if can(uid, "wallet"):
            start_wallet_adjust(message)
        return

    if act == "btn_withdrawals":
        if can(uid, "withdraw"):
            withdrawals_list(message)
        return

    if act == "btn_broadcast":
        if can(uid, "broadcast"):
            start_broadcast(message)
        return

    if act == "btn_settings":
        if can(uid, "settings"):
            settings_menu(message)
        return

    if act == "btn_edit_text":
        if can(uid, "settings"):
            edit_text_menu(message)
        return

    if act == "btn_live_session":
        if can(uid, "live"):
            live_admin_menu(message)
        return

    if act == "btn_analytics":
        if can(uid, "analytics"):
            analytics(message)
        return

    if act == "btn_subadmins":
        if is_master(uid):
            subadmins_menu(message)
        return

    if act == "btn_targets":
        if can(uid, "settings"):
            targets_menu(message)
        return

    if act == "btn_vip_manage":
        if can(uid, "users"):
            start_vip_manage(message)
        return

    # Unknown text
    bot.send_message(
        message.chat.id,
        "❌ এই command/button পাওয়া যায়নি।",
        reply_markup=main_keyboard(uid)
    )


# ============================================================
# UID UI
# ============================================================

def start_uid(message):
    uid = message.from_user.id

    if is_vip(uid):
        bot.send_message(
            message.chat.id,
            "⭐ আপনি ইতিমধ্যে VIP."
        )
        return

    states[uid] = {
        "action": "uid",
        "parent": "user"
    }

    bot.send_message(
        message.chat.id,
        "🆔 আপনার Quotex UID পাঠান।\n\n"
        "/cancel দিয়ে বাতিল করতে পারবেন।"
    )


# ============================================================
# STATUS
# ============================================================

def show_status(message):
    uid = message.from_user.id

    register_user(message.from_user)
    ensure_cycle(uid)

    u = get_user(uid)

    vip = is_vip(uid)

    if vip:
        status = "⭐ VIP"
        remaining = "♾️ Unlimited"
    else:
        remaining = max(
            0,
            u["free_limit"] - u["free_used"]
        )
        status = "FREE"

    bot.send_message(
        message.chat.id,
        "👤 <b>MY STATUS</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"⭐ Status: <b>{status}</b>\n"
        f"🎟️ Remaining Signals: <b>{remaining}</b>\n"
        f"💰 Wallet: <b>${u['wallet_cents']/100:.2f}</b>\n"
        f"👥 Referrals: <b>{u['refs_count']}</b>\n"
        f"🇧🇩 BD Time: <b>{now_bd().strftime('%Y-%m-%d %H:%M')}</b>"
    )


# ============================================================
# WALLET
# ============================================================

def show_wallet(message):
    u = get_user(message.from_user.id)

    bot.send_message(
        message.chat.id,
        "💰 <b>WALLET</b>\n\n"
        f"Balance: <b>${u['wallet_cents']/100:.2f}</b>\n"
        f"Referrals: <b>{u['refs_count']}</b>",
        reply_markup=reply_keyboard([
            [label("btn_withdraw")],
            [label("btn_back"), label("btn_home")]
        ])
    )


def start_withdraw(message):
    uid = message.from_user.id

    if get_setting("withdrawals", "1") != "1":
        bot.send_message(
            message.chat.id,
            "❌ Withdrawals currently OFF."
        )
        return

    minimum = float(
        get_setting("min_withdraw", "5")
    )

    u = get_user(uid)

    if u["wallet_cents"] < int(minimum * 100):
        bot.send_message(
            message.chat.id,
            f"❌ Minimum withdrawal: <b>${minimum:.2f}</b>"
        )
        return

    states[uid] = {
        "action": "withdraw",
        "parent": "user"
    }

    bot.send_message(
        message.chat.id,
        "💸 Withdraw format:\n\n"
        "<code>10 bKash 017XXXXXXXX</code>\n\n"
        "Amount USD + method + account দিন।"
    )


def save_withdraw(message):
    uid = message.from_user.id

    try:
        parts = message.text.strip().split()

        if len(parts) < 3:
            raise ValueError

        amount = float(parts[0])
        method = parts[1]
        account = " ".join(parts[2:])

        cents = int(round(amount * 100))

        minimum = int(
            float(get_setting("min_withdraw", "5")) * 100
        )

        if cents < minimum:
            raise ValueError

        with db_lock:
            conn = db()

            try:
                user = conn.execute(
                    """
                    SELECT wallet_cents
                    FROM users
                    WHERE user_id=?
                    """,
                    (uid,)
                ).fetchone()

                if not user or user["wallet_cents"] < cents:
                    raise ValueError

                conn.execute(
                    """
                    UPDATE users
                    SET wallet_cents=wallet_cents-?
                    WHERE user_id=?
                    """,
                    (cents, uid)
                )

                conn.execute(
                    """
                    INSERT INTO withdrawals(
                        user_id,amount_cents,
                        method,account,created_at
                    )
                    VALUES(?,?,?,?,?)
                    """,
                    (
                        uid,
                        cents,
                        method,
                        account,
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

        bot.send_message(
            ADMIN_ID,
            "💸 <b>NEW WITHDRAWAL</b>\n\n"
            f"User: <code>{uid}</code>\n"
            f"Amount: <b>${amount:.2f}</b>\n"
            f"Method: <b>{escape(method)}</b>\n"
            f"Account: <code>{escape(account)}</code>"
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Format বা balance ভুল।\n"
            "Example: <code>10 bKash 017XXXXXXXX</code>"
        )


# ============================================================
# REFERRAL
# ============================================================

def show_referral(message):
    try:
        me = bot.get_me()

        link = (
            f"https://t.me/{me.username}"
            f"?start=ref_{message.from_user.id}"
        )

        bonus = get_setting(
            "referral_bonus",
            "1"
        )

        bot.send_message(
            message.chat.id,
            "👥 <b>REFERRAL</b>\n\n"
            f"<code>{link}</code>\n\n"
            f"Bonus: <b>${float(bonus):.2f}</b>"
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Referral link তৈরি করা যায়নি।"
        )


# ============================================================
# HISTORY
# ============================================================

def show_history(message):
    uid = message.from_user.id

    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT s.*
                FROM signals s
                JOIN signal_access a
                ON a.signal_id=s.id
                WHERE a.user_id=?
                ORDER BY a.accessed_at DESC
                LIMIT 15
                """,
                (uid,)
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        bot.send_message(
            message.chat.id,
            "📭 Signal history empty."
        )
        return

    lines = [
        "📜 <b>SIGNAL HISTORY</b>\n"
    ]

    for r in rows:
        t = bd_from_iso(
            r["signal_at_utc"]
        )

        lines.append(
            f"#{r['id']} | "
            f"{t.strftime('%d-%m %H:%M')} | "
            f"{escape(r['pair'])} | "
            f"<b>{r['direction']}</b>"
        )

    bot.send_message(
        message.chat.id,
        "\n".join(lines),
        reply_markup=back_keyboard()
    )


# ============================================================
# NOTIFICATION
# ============================================================

def toggle_notifications(message):
    uid = message.from_user.id
    u = get_user(uid)

    new_value = 0 if u["notifications_enabled"] else 1

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                UPDATE users
                SET notifications_enabled=?
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
        f"<b>{'ON' if new_value else 'OFF'}</b>"
    )


# ============================================================
# MONEY MANAGEMENT UI
# ============================================================

def money_menu(message):
    mm = mm_get(message.from_user.id)

    bot.send_message(
        message.chat.id,
        "💰 <b>MONEY MANAGEMENT</b>\n\n"
        f"💵 Balance: <b>${mm['balance_cents']/100:.2f}</b>\n"
        f"🎯 Profit Target: <b>${mm['profit_target_cents']/100:.2f}</b>\n"
        f"🛑 Loss Limit: <b>${mm['loss_limit_cents']/100:.2f}</b>\n"
        f"💼 Base Trade: <b>${mm['base_trade_cents']/100:.2f}</b>\n"
        f"🔄 M1 Trade: <b>${mm['m1_trade_cents']/100:.2f}</b>\n"
        f"📊 Daily P/L: <b>${mm['daily_pl_cents']/100:.2f}</b>\n"
        f"🛑 Stop Trading: "
        f"<b>{'ON' if mm['stop_trading'] else 'OFF'}</b>",
        reply_markup=reply_keyboard([
            ["💵 Set Balance", "🎯 Set Profit Target"],
            ["🛑 Set Loss Limit", "💼 Set Base Trade"],
            ["🔄 Set M1 Trade", "📊 MM Status"],
            ["🛑 Stop MM Today"],
            [label("btn_back"), label("btn_home")]
        ])
    )


@bot.message_handler(
    func=lambda m: m.text in {
        "💵 Set Balance",
        "🎯 Set Profit Target",
        "🛑 Set Loss Limit",
        "💼 Set Base Trade",
        "🔄 Set M1 Trade",
        "📊 MM Status",
        "🛑 Stop MM Today"
    }
)
def money_actions(message):
    uid = message.from_user.id
    text = message.text

    if text == "💵 Set Balance":
        states[uid] = {
            "action": "mm_balance",
            "parent": "money"
        }

        bot.send_message(
            message.chat.id,
            "💵 Trading Balance USD amount দিন।"
        )
        return

    if text == "🎯 Set Profit Target":
        states[uid] = {
            "action": "mm_profit",
            "parent": "money"
        }

        bot.send_message(
            message.chat.id,
            "🎯 Profit Target USD amount দিন।"
        )
        return

    if text == "🛑 Set Loss Limit":
        states[uid] = {
            "action": "mm_loss",
            "parent": "money"
        }

        bot.send_message(
            message.chat.id,
            "🛑 Loss Limit USD amount দিন।"
        )
        return

    if text == "💼 Set Base Trade":
        states[uid] = {
            "action": "mm_base",
            "parent": "money"
        }

        bot.send_message(
            message.chat.id,
            "💼 Base Trade USD amount দিন।"
        )
        return

    if text == "🔄 Set M1 Trade":
        states[uid] = {
            "action": "mm_m1",
            "parent": "money"
        }

        bot.send_message(
            message.chat.id,
            "🔄 M1 Trade USD amount দিন।"
        )
        return

    if text == "📊 MM Status":
        mm = mm_get(uid)

        bot.send_message(
            message.chat.id,
            "📊 <b>MM STATUS</b>\n\n"
            f"Balance: ${mm['balance_cents']/100:.2f}\n"
            f"Profit Target: ${mm['profit_target_cents']/100:.2f}\n"
            f"Loss Limit: ${mm['loss_limit_cents']/100:.2f}\n"
            f"Base: ${mm['base_trade_cents']/100:.2f}\n"
            f"M1: ${mm['m1_trade_cents']/100:.2f}\n"
            f"Daily P/L: ${mm['daily_pl_cents']/100:.2f}\n"
            f"Stop: {'ON' if mm['stop_trading'] else 'OFF'}",
            reply_markup=user_sub_keyboard()
        )
        return

    if text == "🛑 Stop MM Today":
        mm_save(
            uid,
            stop_trading=1
        )

        bot.send_message(
            message.chat.id,
            "🛑 Money Management আজকের জন্য stopped.",
            reply_markup=user_sub_keyboard()
        )


def save_mm_input(message, field):
    try:
        amount = float(
            message.text.strip()
        )

        if amount < 0:
            raise ValueError

        mm_save(
            message.from_user.id,
            **{
                field: int(
                    round(amount * 100)
                )
            }
        )

        states.pop(
            message.from_user.id,
            None
        )

        bot.send_message(
            message.chat.id,
            "✅ Saved.",
            reply_markup=user_sub_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ শুধু valid USD amount দিন।"
        )


# ============================================================
# LIVE USER
# ============================================================

def show_live(message):
    live = get_setting(
        "live_mode",
        "1"
    )

    if live != "1":
        bot.send_message(
            message.chat.id,
            "🔴 Live session বর্তমানে OFF."
        )
        return

    with db_lock:
        conn = db()

        try:
            session = conn.execute(
                """
                SELECT id
                FROM live_sessions
                WHERE active=1
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

            if not session:
                bot.send_message(
                    message.chat.id,
                    "📭 কোনো active Live Session নেই।"
                )
                return

            signal = conn.execute(
                """
                SELECT *
                FROM live_signals
                WHERE session_id=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (session["id"],)
            ).fetchone()

        finally:
            conn.close()

    if not signal:
        bot.send_message(
            message.chat.id,
            "📭 Live signal এখনো দেওয়া হয়নি।"
        )
        return

    direction = signal["direction"]

    arrow = (
        "🟢⬆️ BUY / UP"
        if direction == "UP"
        else "🔴⬇️ SELL / DOWN"
    )

    bot.send_message(
        message.chat.id,
        "⚡ <b>LIVE SIGNAL</b>\n\n"
        f"💱 Pair: <b>{escape(signal['pair'])}</b>\n"
        f"📈 Direction: <b>{arrow}</b>\n"
        f"🎯 Confidence: <b>{escape(signal['confidence'] or '')}</b>"
    )


# ============================================================
# ADMIN: ADD SIGNAL
# ============================================================

def start_add_signal(message):
    states[message.from_user.id] = {
        "action": "add_signal",
        "parent": "admin"
    }

    bot.send_message(
        message.chat.id,
        "➕ <b>ADD FUTURE SIGNAL</b>\n\n"
        "একসাথে যত signal আছে সব paste করো.\n\n"
        "Example:\n"
        "<code>"
        "13:04 - USD/BDT-OTC - BUY\n"
        "13:14 - USD/PHP-OTC - SELL\n"
        "13:21 - USD/COP-OTC - UP\n"
        "13:30 - USD/MXN-OTC - DOWN"
        "</code>\n\n"
        "BUY/CALL/UP = UP\n"
        "SELL/PUT/DOWN = DOWN\n\n"
        "⚠️ Past time signal Add হবে না।"
    )


# ============================================================
# ADMIN: SIGNAL LIST
# ============================================================

def signal_list(message):
    now = utc_iso(now_utc())

    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT *
                FROM signals
                WHERE signal_at_utc > ?
                ORDER BY signal_at_utc ASC
                LIMIT 50
                """,
                (now,)
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        bot.send_message(
            message.chat.id,
            "📭 No upcoming signals.",
            reply_markup=admin_keyboard()
        )
        return

    lines = [
        "📊 <b>UPCOMING SIGNALS</b>\n"
    ]

    for r in rows:
        t = bd_from_iso(
            r["signal_at_utc"]
        )

        lines.append(
            f"#{r['id']} | "
            f"{t.strftime('%d-%m %H:%M')} | "
            f"{escape(r['pair'])} | "
            f"<b>{r['direction']}</b>"
        )

    bot.send_message(
        message.chat.id,
        "\n".join(lines),
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN: AUTO SEND
# ============================================================

def toggle_auto_send(message):
    old = get_setting(
        "auto_send",
        "1"
    )

    new = "0" if old == "1" else "1"

    set_setting(
        "auto_send",
        new
    )

    bot.send_message(
        message.chat.id,
        "📡 Auto Send: "
        f"<b>{'ON' if new == '1' else 'OFF'}</b>\n\n"
        f"Send before: "
        f"<b>{get_setting('send_before_minutes','5')} min</b>",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN: CLEAR
# ============================================================

def clear_future(message):
    if not is_master(message.from_user.id):
        return

    with db_lock:
        conn = db()

        try:
            conn.execute(
                """
                DELETE FROM signals
                WHERE signal_at_utc > ?
                """,
                (utc_iso(now_utc()),)
            )

            conn.commit()

        finally:
            conn.close()

    bot.send_message(
        message.chat.id,
        "🗑 Upcoming signals cleared.",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN: PENDING UID
# ============================================================

def pending_uids(message):
    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT *
                FROM uid_submissions
                WHERE status='PENDING'
                ORDER BY id DESC
                LIMIT 30
                """
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        bot.send_message(
            message.chat.id,
            "📭 No pending UID.",
            reply_markup=admin_keyboard()
        )
        return

    lines = [
        "🆔 <b>PENDING UID</b>\n"
    ]

    for r in rows:
        lines.append(
            f"#{r['id']} | "
            f"User: <code>{r['user_id']}</code> | "
            f"UID: <code>{escape(r['uid'])}</code>"
        )

    bot.send_message(
        message.chat.id,
        "\n".join(lines)
        + "\n\nApprove format:\n"
        "<code>/approve UID_SUBMISSION_ID days</code>\n\n"
        "Reject:\n"
        "<code>/reject UID_SUBMISSION_ID</code>",
        reply_markup=admin_keyboard()
    )


# ============================================================
# UID APPROVE / REJECT COMMANDS
# ============================================================

@bot.message_handler(commands=["approve"])
def approve_uid(message):
    if not can(message.from_user.id, "uid"):
        return

    try:
        parts = message.text.split()

        sid = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 30

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
                    raise ValueError

                until = now_utc() + timedelta(
                    days=days
                )

                conn.execute(
                    """
                    UPDATE uid_submissions
                    SET status='APPROVED',
                        reviewed_at=?,
                        reviewed_by=?
                    WHERE id=?
                    """,
                    (
                        utc_iso(now_utc()),
                        message.from_user.id,
                        sid
                    )
                )

                conn.execute(
                    """
                    UPDATE users
                    SET status='VIP',
                        vip_until=?
                    WHERE user_id=?
                    """,
                    (
                        until.isoformat(),
                        row["user_id"]
                    )
                )

                conn.commit()

            finally:
                conn.close()

        bot.send_message(
            row["user_id"],
            f"⭐ <b>VIP Approved</b>\n\n"
            f"VIP valid for <b>{days} days</b>."
        )

        bot.send_message(
            message.chat.id,
            "✅ VIP approved.",
            reply_markup=admin_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Format: <code>/approve ID 30</code>"
        )


@bot.message_handler(commands=["reject"])
def reject_uid(message):
    if not can(message.from_user.id, "uid"):
        return

    try:
        sid = int(
            message.text.split()[1]
        )

        with db_lock:
            conn = db()

            try:
                row = conn.execute(
                    """
                    SELECT user_id
                    FROM uid_submissions
                    WHERE id=? AND status='PENDING'
                    """,
                    (sid,)
                ).fetchone()

                if not row:
                    raise ValueError

                conn.execute(
                    """
                    UPDATE uid_submissions
                    SET status='REJECTED',
                        reviewed_at=?,
                        reviewed_by=?
                    WHERE id=?
                    """,
                    (
                        utc_iso(now_utc()),
                        message.from_user.id,
                        sid
                    )
                )

                conn.commit()

            finally:
                conn.close()

        bot.send_message(
            row["user_id"],
            "❌ UID rejected by admin."
        )

        bot.send_message(
            message.chat.id,
            "✅ UID rejected.",
            reply_markup=admin_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Format: <code>/reject ID</code>"
        )


# ============================================================
# ADMIN: USERS
# ============================================================

def user_list(message):
    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT user_id,username,status,
                       wallet_cents,free_used,free_limit
                FROM users
                ORDER BY user_id DESC
                LIMIT 40
                """
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        bot.send_message(
            message.chat.id,
            "No users."
        )
        return

    lines = [
        "👥 <b>USERS</b>\n"
    ]

    for u in rows:
        lines.append(
            f"<code>{u['user_id']}</code> | "
            f"{escape(u['username'] or '')} | "
            f"{u['status']} | "
            f"${u['wallet_cents']/100:.2f}"
        )

    bot.send_message(
        message.chat.id,
        "\n".join(lines),
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN: WALLET ADJUST
# ============================================================

def start_wallet_adjust(message):
    states[message.from_user.id] = {
        "action": "wallet_adjust",
        "parent": "admin"
    }

    bot.send_message(
        message.chat.id,
        "💳 Format:\n\n"
        "<code>USER_ID +10</code>\n"
        "<code>USER_ID -5</code>"
    )


def save_wallet_adjust(message):
    try:
        parts = message.text.split()

        uid = int(parts[0])
        amount = float(parts[1])

        ok, balance = wallet_adjust(
            uid,
            int(round(amount * 100)),
            "Admin adjustment"
        )

        if not ok:
            raise ValueError

        states.pop(
            message.from_user.id,
            None
        )

        bot.send_message(
            message.chat.id,
            "✅ Wallet updated.\n"
            f"New balance: <b>${balance/100:.2f}</b>",
            reply_markup=admin_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Example: <code>123456789 +10</code>"
        )


# ============================================================
# ADMIN: WITHDRAWALS
# ============================================================

def withdrawals_list(message):
    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT *
                FROM withdrawals
                WHERE status='PENDING'
                ORDER BY id DESC
                LIMIT 30
                """
            ).fetchall()

        finally:
            conn.close()

    if not rows:
        bot.send_message(
            message.chat.id,
            "📭 No pending withdrawals.",
            reply_markup=admin_keyboard()
        )
        return

    lines = [
        "💸 <b>PENDING WITHDRAWALS</b>\n"
    ]

    for r in rows:
        lines.append(
            f"#{r['id']} | "
            f"User <code>{r['user_id']}</code> | "
            f"${r['amount_cents']/100:.2f} | "
            f"{escape(r['method'])} | "
            f"<code>{escape(r['account'])}</code>"
        )

    lines.append(
        "\nApprove: <code>/wdapprove ID</code>"
    )
    lines.append(
        "Reject: <code>/wdreject ID</code>"
    )

    bot.send_message(
        message.chat.id,
        "\n".join(lines),
        reply_markup=admin_keyboard()
    )


@bot.message_handler(commands=["wdapprove"])
def wdapprove(message):
    if not can(message.from_user.id, "withdraw"):
        return

    try:
        wid = int(
            message.text.split()[1]
        )

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
                    raise ValueError

                conn.execute(
                    """
                    UPDATE withdrawals
                    SET status='APPROVED',
                        reviewed_at=?,
                        reviewed_by=?
                    WHERE id=?
                    """,
                    (
                        utc_iso(now_utc()),
                        message.from_user.id,
                        wid
                    )
                )

                conn.commit()

            finally:
                conn.close()

        bot.send_message(
            row["user_id"],
            "✅ Withdrawal approved."
        )

        bot.send_message(
            message.chat.id,
            "✅ Withdrawal approved.",
            reply_markup=admin_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Format: <code>/wdapprove ID</code>"
        )


@bot.message_handler(commands=["wdreject"])
def wdreject(message):
    if not can(message.from_user.id, "withdraw"):
        return

    try:
        wid = int(
            message.text.split()[1]
        )

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
                    raise ValueError

                conn.execute(
                    """
                    UPDATE withdrawals
                    SET status='REJECTED',
                        reviewed_at=?,
                        reviewed_by=?
                    WHERE id=?
                    """,
                    (
                        utc_iso(now_utc()),
                        message.from_user.id,
                        wid
                    )
                )

                # Return money
                conn.execute(
                    """
                    UPDATE users
                    SET wallet_cents=wallet_cents+?
                    WHERE user_id=?
                    """,
                    (
                        row["amount_cents"],
                        row["user_id"]
                    )
                )

                conn.commit()

            finally:
                conn.close()

        bot.send_message(
            row["user_id"],
            "❌ Withdrawal rejected.\n"
            "Amount returned to wallet."
        )

        bot.send_message(
            message.chat.id,
            "✅ Withdrawal rejected and refunded.",
            reply_markup=admin_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Format: <code>/wdreject ID</code>"
        )


# ============================================================
# ADMIN: BROADCAST
# ============================================================

def start_broadcast(message):
    states[message.from_user.id] = {
        "action": "broadcast",
        "parent": "admin"
    }

    bot.send_message(
        message.chat.id,
        "📢 Broadcast message পাঠাও।"
    )


def send_broadcast(message):
    text = message.text

    states.pop(
        message.from_user.id,
        None
    )

    with db_lock:
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
    failed = 0

    for row in users:
        try:
            bot.send_message(
                row["user_id"],
                text
            )
            sent += 1

        except Exception:
            failed += 1

    bot.send_message(
        message.chat.id,
        f"📢 Broadcast finished.\n\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}",
        reply_markup=admin_keyboard()
    )


# ============================================================
# ADMIN SETTINGS
# ============================================================

def settings_menu(message):
    auto = get_setting(
        "auto_send",
        "1"
    )

    maintenance = get_setting(
        "maintenance",
        "0"
    )

    withdraw = get_setting(
        "withdrawals",
        "1"
    )

    before = get_setting(
        "send_before_minutes",
        "5"
    )

    bot.send_message(
        message.chat.id,
        "⚙️ <b>SETTINGS</b>\n\n"
        f"📡 Auto Send: <b>{'ON' if auto=='1' else 'OFF'}</b>\n"
        f"⏱ Send Before: <b>{before} min</b>\n"
        f"🛠 Maintenance: <b>{'ON' if maintenance=='1' else 'OFF'}</b>\n"
        f"💸 Withdrawals: <b>{'ON' if withdraw=='1' else 'OFF'}</b>\n"
        f"🎟 Free Limit: <b>{get_setting('free_signal_limit','4')}</b>\n"
        f"💵 Min Withdraw: <b>${get_setting('min_withdraw','5')}</b>",
        reply_markup=reply_keyboard([
            ["🛠 Maintenance", "💸 Withdrawals"],
            ["🎟 Free Signal Limit", "⏱ Send Before"],
            ["💵 Min Withdraw"],
            [label("btn_back"), label("btn_home")]
        ])
    )


@bot.message_handler(
    func=lambda m: m.text in {
        "🛠 Maintenance",
        "💸 Withdrawals",
        "🎟 Free Signal Limit",
        "⏱ Send Before",
        "💵 Min Withdraw"
    }
)
def settings_actions(message):
    uid = message.from_user.id

    if not can(uid, "settings"):
        return

    text = message.text

    if text == "🛠 Maintenance":
        old = get_setting(
            "maintenance",
            "0"
        )

        new = "0" if old == "1" else "1"

        set_setting(
            "maintenance",
            new
        )

        bot.send_message(
            message.chat.id,
            f"🛠 Maintenance: "
            f"<b>{'ON' if new=='1' else 'OFF'}</b>",
            reply_markup=admin_keyboard()
        )
        return

    if text == "💸 Withdrawals":
        old = get_setting(
            "withdrawals",
            "1"
        )

        new = "0" if old == "1" else "1"

        set_setting(
            "withdrawals",
            new
        )

        bot.send_message(
            message.chat.id,
            f"💸 Withdrawals: "
            f"<b>{'ON' if new=='1' else 'OFF'}</b>",
            reply_markup=admin_keyboard()
        )
        return

    if text == "🎟 Free Signal Limit":
        states[uid] = {
            "action": "free_limit",
            "parent": "admin"
        }

        bot.send_message(
            message.chat.id,
            "🎟 Global free signal limit number দিন।"
        )
        return

    if text == "⏱ Send Before":
        states[uid] = {
            "action": "send_before",
            "parent": "admin"
        }

        bot.send_message(
            message.chat.id,
            "⏱ কত মিনিট আগে signal পাঠাবে?\n"
            "Example: <code>5</code>"
        )
        return

    if text == "💵 Min Withdraw":
        states[uid] = {
            "action": "min_withdraw",
            "parent": "admin"
        }

        bot.send_message(
            message.chat.id,
            "💵 Minimum withdrawal USD amount দিন।"
        )


# ============================================================
# ADMIN TEXT EDITOR
# ============================================================

TEXT_KEYS = [
    "welcome_text",
    "maintenance_text",
    "no_signal_text",
    "quota_text",
    "vip_text",
    "notice_text",
    "trading_rules_text",
    "help_text",
    "signal_footer",
    "win_text",
    "loss_text",
    "skip_text"
]

BUTTON_KEYS = [
    "btn_future",
    "btn_live",
    "btn_uid",
    "btn_status",
    "btn_wallet",
    "btn_withdraw",
    "btn_referral",
    "btn_vip",
    "btn_history",
    "btn_notice",
    "btn_rules",
    "btn_help",
    "btn_notifications",
    "btn_money",
    "btn_admin",
    "btn_back",
    "btn_home",
    "btn_add_signal",
    "btn_signal_list",
    "btn_auto_send",
    "btn_clear",
    "btn_pending_uid",
    "btn_users",
    "btn_wallet_adjust",
    "btn_withdrawals",
    "btn_broadcast",
    "btn_settings",
    "btn_edit_text",
    "btn_live_session",
    "btn_analytics",
    "btn_subadmins",
    "btn_targets",
    "btn_vip_manage"
]


def edit_text_menu(message):
    rows = []

    for key in TEXT_KEYS:
        rows.append([
            f"✏️ {key}"
        ])

    rows.append([
        "🔘 Edit Button"
    ])

    rows.append([
        label("btn_back"),
        label("btn_home")
    ])

    bot.send_message(
        message.chat.id,
        "📝 <b>BOT TEXT EDITOR</b>\n\n"
        "যে text পরিবর্তন করতে চান সেটি select করুন।",
        reply_markup=reply_keyboard(rows)
    )

    states[message.from_user.id] = {
        "action": "edit_text_menu",
        "parent": "admin"
    }


@bot.message_handler(
    func=lambda m:
        m.text.startswith("✏️ ")
        or m.text == "🔘 Edit Button"
)
def edit_text_select(message):
    uid = message.from_user.id

    if not can(uid, "settings"):
        return

    if message.text == "🔘 Edit Button":
        rows = []

        for key in BUTTON_KEYS:
            rows.append([
                f"🔘 {key}"
            ])

        rows.append([
            label("btn_back"),
            label("btn_home")
        ])

        states[uid] = {
            "action": "edit_button_select",
            "parent": "admin"
        }

        bot.send_message(
            message.chat.id,
            "🔘 যে button-এর নাম change করবেন সেটি select করুন।",
            reply_markup=reply_keyboard(rows)
        )
        return

    key = message.text.replace(
        "✏️ ",
        "",
        1
    )

    if key not in TEXT_KEYS:
        return

    states[uid] = {
        "action": "edit_text",
        "key": key,
        "parent": "admin"
    }

    bot.send_message(
        message.chat.id,
        f"📝 নতুন text পাঠান।\n\n"
        f"Current:\n"
        f"<code>{escape(get_setting(key))}</code>"
    )


@bot.message_handler(
    func=lambda m: m.text.startswith("🔘 btn_")
)
def edit_button_select(message):
    uid = message.from_user.id

    if not can(uid, "settings"):
        return

    key = message.text.replace(
        "🔘 ",
        "",
        1
    )

    if key not in BUTTON_KEYS:
        return

    states[uid] = {
        "action": "edit_button_value",
        "key": key,
        "parent": "admin"
    }

    bot.send_message(
        message.chat.id,
        "🔘 নতুন button name পাঠান।\n\n"
        f"Current: <b>{escape(get_setting(key))}</b>"
    )


def save_edited_text(message):
    uid = message.from_user.id
    st = states.get(uid)

    if not st:
        return

    action = st.get("action")

    if action == "edit_text":
        key = st["key"]

        set_setting(
            key,
            message.text
        )

        states.pop(uid, None)

        bot.send_message(
            message.chat.id,
            "✅ Text updated.",
            reply_markup=admin_keyboard()
        )
        return

    if action == "edit_button_value":
        key = st["key"]

        if not message.text.strip():
            bot.send_message(
                message.chat.id,
                "❌ Empty button name allowed নয়।"
            )
            return

        set_setting(
            key,
            message.text.strip()
        )

        states.pop(uid, None)

        bot.send_message(
            message.chat.id,
            "✅ Button updated.\n\n"
            "Button-এর internal action একই থাকবে।",
            reply_markup=admin_keyboard()
        )


# ============================================================
# ADMIN LIVE SESSION
# ============================================================

def live_admin_menu(message):
    live = get_setting(
        "live_mode",
        "1"
    )

    bot.send_message(
        message.chat.id,
        "⚡ <b>LIVE SESSION</b>\n\n"
        f"Mode: <b>{'ON' if live=='1' else 'OFF'}</b>",
        reply_markup=reply_keyboard([
            ["▶️ Start Live Session"],
            ["📤 Send Live Signal"],
            ["⏹ End Live Session"],
            [label("btn_back"), label("btn_home")]
        ])
    )


@bot.message_handler(
    func=lambda m: m.text in {
        "▶️ Start Live Session",
        "📤 Send Live Signal",
        "⏹ End Live Session"
    }
)
def live_admin_actions(message):
    uid = message.from_user.id

    if not can(uid, "live"):
        return

    if message.text == "▶️ Start Live Session":

        with db_lock:
            conn = db()

            try:
                conn.execute(
                    """
                    UPDATE live_sessions
                    SET active=0
                    WHERE active=1
                    """
                )

                conn.execute(
                    """
                    INSERT INTO live_sessions(
                        started_at,active
                    )
                    VALUES(?,1)
                    """,
                    (utc_iso(now_utc()),)
                )

                conn.commit()

            finally:
                conn.close()

        bot.send_message(
            message.chat.id,
            "▶️ Live Session started.",
            reply_markup=reply_keyboard([
                ["📤 Send Live Signal"],
                ["⏹ End Live Session"],
                [label("btn_back"), label("btn_home")]
            ])
        )
        return

    if message.text == "📤 Send Live Signal":

        states[uid] = {
            "action": "live_signal",
            "parent": "admin"
        }

        bot.send_message(
            message.chat.id,
            "📤 Live signal details পাঠাও।\n\n"
            "Example:\n"
            "<code>USD/BDT-OTC - BUY - 95%</code>"
        )
        return

    if message.text == "⏹ End Live Session":

        with db_lock:
            conn = db()

            try:
                conn.execute(
                    """
                    UPDATE live_sessions
                    SET active=0,
                        ended_at=?
                    WHERE active=1
                    """,
                    (utc_iso(now_utc()),)
                )

                conn.commit()

            finally:
                conn.close()

        bot.send_message(
            message.chat.id,
            "⏹ Live Session ended.",
            reply_markup=admin_keyboard()
        )


def save_live_signal(message):
    uid = message.from_user.id

    with db_lock:
        conn = db()

        try:
            session = conn.execute(
                """
                SELECT id
                FROM live_sessions
                WHERE active=1
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

            if not session:
                raise ValueError

            raw = message.text.strip()

            parts = [
                x.strip()
                for x in re.split(
                    r"\s*-\s*",
                    raw
                )
            ]

            if len(parts) < 2:
                raise ValueError

            pair = parts[0].upper()

            direction = normalize_direction(
                parts[1]
            )

            if not direction:
                raise ValueError

            confidence = (
                parts[2]
                if len(parts) >= 3
                else ""
            )

            conn.execute(
                """
                INSERT INTO live_signals(
                    session_id,pair,direction,
                    confidence,raw_text,created_at
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    session["id"],
                    pair,
                    direction,
                    confidence,
                    raw,
                    utc_iso(now_utc())
                )
            )

            conn.commit()

        finally:
            conn.close()

    states.pop(uid, None)

    send_live_to_users(
        pair,
        direction,
        confidence
    )

    bot.send_message(
        message.chat.id,
        "✅ Live signal sent.",
        reply_markup=admin_keyboard()
    )


def send_live_to_users(
    pair,
    direction,
    confidence
):
    arrow = (
        "🟢⬆️ BUY / UP"
        if direction == "UP"
        else "🔴⬇️ SELL / DOWN"
    )

    text = (
        "⚡ <b>LIVE SIGNAL</b>\n\n"
        f"💱 Pair: <b>{escape(pair)}</b>\n"
        f"📈 Direction: <b>{arrow}</b>\n"
        f"🎯 Confidence: <b>{escape(confidence)}</b>"
    )

    with db_lock:
        conn = db()

        try:
            users = conn.execute(
                """
                SELECT user_id
                FROM users
                WHERE blocked=0
                AND notifications_enabled=1
                """
            ).fetchall()

            targets = conn.execute(
                """
                SELECT chat_id
                FROM notification_targets
                WHERE enabled=1
                """
            ).fetchall()

        finally:
            conn.close()

    for row in users:
        try:
            bot.send_message(
                row["user_id"],
                text
            )
        except Exception:
            pass

    for row in targets:
        try:
            bot.send_message(
                row["chat_id"],
                text
            )
        except Exception:
            pass


# ============================================================
# ADMIN ANALYTICS
# ============================================================

def analytics(message):
    with db_lock:
        conn = db()

        try:
            users = conn.execute(
                "SELECT COUNT(*) c FROM users"
            ).fetchone()["c"]

            vip = conn.execute(
                """
                SELECT COUNT(*) c
                FROM users
                WHERE status='VIP'
                """
            ).fetchone()["c"]

            future = conn.execute(
                """
                SELECT COUNT(*) c
                FROM signals
                WHERE signal_at_utc > ?
                """,
                (utc_iso(now_utc()),)
            ).fetchone()["c"]

            votes = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN vote='WIN' THEN 1 ELSE 0 END) win,
                    SUM(CASE WHEN vote='LOSS' THEN 1 ELSE 0 END) loss,
                    SUM(CASE WHEN vote='SKIP' THEN 1 ELSE 0 END) skip
                FROM signal_votes
                """
            ).fetchone()

        finally:
            conn.close()

    bot.send_message(
        message.chat.id,
        "📈 <b>ANALYTICS</b>\n\n"
        f"👥 Users: <b>{users}</b>\n"
        f"⭐ VIP: <b>{vip}</b>\n"
        f"📊 Future Signals: <b>{future}</b>\n"
        f"✅ WIN votes: <b>{votes['win'] or 0}</b>\n"
        f"❌ LOSS votes: <b>{votes['loss'] or 0}</b>\n"
        f"⏭ SKIP votes: <b>{votes['skip'] or 0}</b>",
        reply_markup=admin_keyboard()
    )


# ============================================================
# SUB ADMINS
# ============================================================

def subadmins_menu(message):
    if not is_master(message.from_user.id):
        return

    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT *
                FROM admins
                ORDER BY user_id
                """
            ).fetchall()

        finally:
            conn.close()

    text = "🛡 <b>SUB-ADMINS</b>\n\n"

    if rows:
        for r in rows:
            text += (
                f"<code>{r['user_id']}</code> → "
                f"{escape(r['permissions'])}\n"
            )
    else:
        text += "None.\n"

    text += (
        "\nAdd/update:\n"
        "<code>USER_ID signals,uid,users,wallet,"
        "withdraw,broadcast,settings,analytics,live</code>\n\n"
        "Remove:\n"
        "<code>/removeadmin USER_ID</code>"
    )

    states[message.from_user.id] = {
        "action": "subadmin",
        "parent": "admin"
    }

    bot.send_message(
        message.chat.id,
        text
    )


def save_subadmin_input(message):
    try:
        parts = message.text.split(
            maxsplit=1
        )

        if len(parts) != 2:
            raise ValueError

        uid = int(parts[0])

        perms = {
            p.strip()
            for p in parts[1].split(",")
            if p.strip() in ALL_PERMISSIONS
        }

        if not perms:
            raise ValueError

        save_admin(
            uid,
            perms
        )

        states.pop(
            message.from_user.id,
            None
        )

        bot.send_message(
            message.chat.id,
            "✅ Sub-admin saved.",
            reply_markup=admin_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Format ভুল।"
        )


@bot.message_handler(commands=["removeadmin"])
def remove_admin_cmd(message):
    if not is_master(message.from_user.id):
        return

    try:
        uid = int(
            message.text.split()[1]
        )

        with db_lock:
            conn = db()

            try:
                conn.execute(
                    "DELETE FROM admins WHERE user_id=?",
                    (uid,)
                )

                conn.commit()

            finally:
                conn.close()

        bot.send_message(
            message.chat.id,
            "✅ Sub-admin removed.",
            reply_markup=admin_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Format: <code>/removeadmin USER_ID</code>"
        )


# ============================================================
# NOTIFICATION TARGETS
# ============================================================

def targets_menu(message):
    with db_lock:
        conn = db()

        try:
            rows = conn.execute(
                """
                SELECT *
                FROM notification_targets
                ORDER BY chat_id
                """
            ).fetchall()

        finally:
            conn.close()

    text = "📣 <b>NOTIFICATION TARGETS</b>\n\n"

    if rows:
        for r in rows:
            text += (
                f"<code>{r['chat_id']}</code> | "
                f"{escape(r['title'] or '')} | "
                f"{'ON' if r['enabled'] else 'OFF'}\n"
            )
    else:
        text += "No target.\n"

    text += (
        "\nAdd target:\n"
        "<code>CHAT_ID Group/Channel Name</code>\n\n"
        "Remove:\n"
        "<code>/removetarget CHAT_ID</code>"
    )

    states[message.from_user.id] = {
        "action": "target",
        "parent": "admin"
    }

    bot.send_message(
        message.chat.id,
        text
    )


def save_target(message):
    try:
        parts = message.text.split(
            maxsplit=1
        )

        chat_id = int(parts[0])
        title = (
            parts[1]
            if len(parts) > 1
            else str(chat_id)
        )

        with db_lock:
            conn = db()

            try:
                conn.execute(
                    """
                    INSERT INTO notification_targets(
                        chat_id,title,enabled
                    )
                    VALUES(?,?,1)
                    ON CONFLICT(chat_id)
                    DO UPDATE SET title=excluded.title,
                                  enabled=1
                    """,
                    (chat_id, title)
                )

                conn.commit()

            finally:
                conn.close()

        states.pop(
            message.from_user.id,
            None
        )

        bot.send_message(
            message.chat.id,
            "✅ Notification target saved.",
            reply_markup=admin_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Example: <code>-100123456789 Group</code>"
        )


@bot.message_handler(commands=["removetarget"])
def remove_target(message):
    if not can(message.from_user.id, "settings"):
        return

    try:
        chat_id = int(
            message.text.split()[1]
        )

        with db_lock:
            conn = db()

            try:
                conn.execute(
                    """
                    DELETE FROM notification_targets
                    WHERE chat_id=?
                    """,
                    (chat_id,)
                )

                conn.commit()

            finally:
                conn.close()

        bot.send_message(
            message.chat.id,
            "✅ Target removed.",
            reply_markup=admin_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Format: <code>/removetarget CHAT_ID</code>"
        )


# ============================================================
# ADMIN VIP MANAGEMENT
# ============================================================

def start_vip_manage(message):
    states[message.from_user.id] = {
        "action": "vip_manage",
        "parent": "admin"
    }

    bot.send_message(
        message.chat.id,
        "⭐ Format:\n\n"
        "<code>USER_ID 30</code>\n\n"
        "VIP 30 days করার জন্য।\n"
        "0 দিলে VIP remove হবে।"
    )


def save_vip_manage(message):
    try:
        parts = message.text.split()

        uid = int(parts[0])
        days = int(parts[1])

        with db_lock:
            conn = db()

            try:
                if days <= 0:
                    conn.execute(
                        """
                        UPDATE users
                        SET status='FREE',
                            vip_until=NULL
                        WHERE user_id=?
                        """,
                        (uid,)
                    )
                else:
                    until = now_utc() + timedelta(
                        days=days
                    )

                    conn.execute(
                        """
                        UPDATE users
                        SET status='VIP',
                            vip_until=?
                        WHERE user_id=?
                        """,
                        (
                            until.isoformat(),
                            uid
                        )
                    )

                conn.commit()

            finally:
                conn.close()

        states.pop(
            message.from_user.id,
            None
        )

        bot.send_message(
            message.chat.id,
            "✅ VIP updated.",
            reply_markup=admin_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Example: <code>123456789 30</code>"
        )


# ============================================================
# MIN WITHDRAW / STATE EXTRA
# ============================================================

def process_min_withdraw_state(message):
    uid = message.from_user.id

    st = states.get(uid)

    if not st or st.get("action") != "min_withdraw":
        return False

    try:
        value = float(
            message.text.strip()
        )

        if value < 0:
            raise ValueError

        set_setting(
            "min_withdraw",
            value
        )

        states.pop(uid, None)

        bot.send_message(
            message.chat.id,
            f"✅ Minimum withdrawal set: "
            f"<b>${value:.2f}</b>",
            reply_markup=admin_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Valid USD amount দিন।"
        )

    return True


# ============================================================
# SCHEDULED FUTURE SIGNALS
# ============================================================

def scheduled_signal_loop():
    while True:
        try:
            if get_setting(
                "auto_send",
                "1"
            ) != "1":
                time.sleep(5)
                continue

            before = int(
                get_setting(
                    "send_before_minutes",
                    "5"
                )
            )

            now = now_utc()

            lower = now
            upper = now + timedelta(
                minutes=before
            )

            with db_lock:
                conn = db()

                try:
                    rows = conn.execute(
                        """
                        SELECT *
                        FROM signals
                        WHERE signal_at_utc > ?
                        AND signal_at_utc <= ?
                        AND sent=0
                        ORDER BY signal_at_utc ASC
                        """,
                        (
                            utc_iso(lower),
                            utc_iso(upper)
                        )
                    ).fetchall()

                    for signal in rows:
                        conn.execute(
                            """
                            UPDATE signals
                            SET sent=1,
                                sent_at=?
                            WHERE id=?
                            """,
                            (
                                utc_iso(now_utc()),
                                signal["id"]
                            )
                        )

                    conn.commit()

                finally:
                    conn.close()

            for signal in rows:
                send_scheduled_signal(
                    signal
                )

        except Exception:
            logger.exception(
                "scheduled signal error"
            )

        time.sleep(5)


def send_scheduled_signal(signal):
    text = signal_text(signal)

    with db_lock:
        conn = db()

        try:
            users = conn.execute(
                """
                SELECT user_id
                FROM users
                WHERE blocked=0
                AND notifications_enabled=1
                """
            ).fetchall()

            targets = conn.execute(
                """
                SELECT chat_id
                FROM notification_targets
                WHERE enabled=1
                """
            ).fetchall()

        finally:
            conn.close()

    # Every scheduled signal is sent individually.
    for row in users:
        try:
            bot.send_message(
                row["user_id"],
                text
            )
        except Exception:
            pass

    for row in targets:
        try:
            bot.send_message(
                row["chat_id"],
                text
            )
        except Exception:
            pass


# ============================================================
# VIP EXPIRY CHECK
# ============================================================

def vip_expiry_loop():
    while True:
        try:
            now = now_utc()

            with db_lock:
                conn = db()

                try:
                    rows = conn.execute(
                        """
                        SELECT user_id,vip_until
                        FROM users
                        WHERE status='VIP'
                        AND vip_until IS NOT NULL
                        """
                    ).fetchall()

                    for row in rows:
                        try:
                            until = datetime.fromisoformat(
                                row["vip_until"]
                            )

                            if until <= now:
                                conn.execute(
                                    """
                                    UPDATE users
                                    SET status='FREE',
                                        vip_until=NULL
                                    WHERE user_id=?
                                    """,
                                    (row["user_id"],)
                                )

                                try:
                                    bot.send_message(
                                        row["user_id"],
                                        "⭐ আপনার VIP মেয়াদ শেষ হয়েছে।"
                                    )
                                except Exception:
                                    pass

                        except Exception:
                            pass

                    conn.commit()

                finally:
                    conn.close()

        except Exception:
            logger.exception(
                "VIP expiry error"
            )

        time.sleep(300)


# ============================================================
# BACKUP
# ============================================================

def backup_loop():
    while True:
        try:
            if os.path.exists(DB_FILE):
                stamp = now_bd().strftime(
                    "%Y%m%d_%H%M%S"
                )

                path = os.path.join(
                    BACKUP_DIR,
                    f"bot_{stamp}.db"
                )

                shutil.copy2(
                    DB_FILE,
                    path
                )

                files = sorted(
                    [
                        os.path.join(
                            BACKUP_DIR,
                            x
                        )
                        for x in os.listdir(
                            BACKUP_DIR
                        )
                        if x.endswith(".db")
                    ]
                )

                for old in files[:-10]:
                    try:
                        os.remove(old)
                    except Exception:
                        pass

        except Exception:
            logger.exception(
                "backup error"
            )

        # 6 hours
        time.sleep(21600)


# ============================================================
# PATCH FOR MIN WITHDRAW STATE
# ============================================================

_original_all_text_handler = all_text_handler


# ============================================================
# BOT COMMANDS
# ============================================================

@bot.message_handler(commands=["id"])
def my_id(message):
    bot.send_message(
        message.chat.id,
        f"Your Telegram ID:\n<code>{message.from_user.id}</code>"
    )


@bot.message_handler(commands=["notice"])
def admin_notice_command(message):
    if not can(message.from_user.id, "settings"):
        return

    states[message.from_user.id] = {
        "action": "notice",
        "parent": "admin"
    }

    bot.send_message(
        message.chat.id,
        "📢 নতুন notice text পাঠাও।"
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@bot.middleware_handler(update_types=["message"])
def middleware_handler(bot_instance, message):
    try:
        if message.from_user:
            register_user(
                message.from_user
            )
    except Exception:
        pass


# ============================================================
# START THREADS
# ============================================================

def start_background_threads():
    threads = [
        threading.Thread(
            target=scheduled_signal_loop,
            daemon=True
        ),
        threading.Thread(
            target=vip_expiry_loop,
            daemon=True
        ),
        threading.Thread(
            target=backup_loop,
            daemon=True
        )
    ]

    for t in threads:
        t.start()


# ============================================================
# MAIN
# ============================================================

def main():
    init_db()

    # Ensure master admin always exists conceptually.
    logger.info(
        "SM QUATEX SURE SHORT started."
    )

    logger.info(
        "Bangladesh time: %s",
        now_bd().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    start_background_threads()

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
