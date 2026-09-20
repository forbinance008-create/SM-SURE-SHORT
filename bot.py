# ============================================================
# SM QUATEX SURE SHORT
# FULL TELEGRAM BOT - ALL IN ONE FILE
# pyTelegramBotAPI + SQLite
# Bangladesh Time: Asia/Dhaka
# ============================================================

import os
import re
import time
import sqlite3
import threading
import logging
import shutil
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from html import escape

import telebot
from telebot import types


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6470135702"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

TZ = ZoneInfo("Asia/Dhaka")
DB_FILE = "bot.db"
BACKUP_DIR = "backups"

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML",
    threaded=True
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

DB_LOCK = threading.RLock()
STATE = {}
TEMP = {}

LIVE = {
    "active": False,
    "started_at": None,
    "admin_id": None,
    "count": 0
}

LAST_BACKUP_DAY = ""


# ============================================================
# DATABASE
# ============================================================

def db():
    c = sqlite3.connect(
        DB_FILE,
        check_same_thread=False,
        timeout=30
    )
    c.row_factory = sqlite3.Row
    return c


def q(sql, params=(), fetch=False):
    with DB_LOCK:
        c = db()
        cur = c.cursor()
        cur.execute(sql, params)
        result = cur.fetchall() if fetch else None
        c.commit()
        c.close()
        return result


def one(sql, params=()):
    r = q(sql, params, True)
    return r[0] if r else None


def now():
    return datetime.now(TZ)


def now_str():
    return now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return now().strftime("%Y-%m-%d")


def init_db():

    q("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        balance REAL DEFAULT 0,
        is_vip INTEGER DEFAULT 0,
        vip_until TEXT,
        uid TEXT UNIQUE,
        pending_uid TEXT,
        referred_by INTEGER,
        notifications INTEGER DEFAULT 1,
        live_signal INTEGER DEFAULT 1,
        free_limit_override INTEGER DEFAULT -1,
        created_at TEXT
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS admins(
        user_id INTEGER PRIMARY KEY,
        role TEXT DEFAULT 'sub_admin',
        permissions TEXT DEFAULT ''
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS signals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_date TEXT,
        signal_time TEXT,
        pair TEXT,
        direction TEXT,
        confidence TEXT DEFAULT '',
        status TEXT DEFAULT 'scheduled',
        auto_sent INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS deliveries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER,
        user_id INTEGER,
        delivery_type TEXT,
        delivered_at TEXT,
        UNIQUE(signal_id,user_id)
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS votes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER,
        user_id INTEGER,
        vote TEXT,
        created_at TEXT,
        UNIQUE(signal_id,user_id)
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS signal_results(
        signal_id INTEGER PRIMARY KEY,
        result TEXT,
        updated_at TEXT
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS withdrawals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        processed_at TEXT
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS wallet_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        type TEXT,
        description TEXT,
        created_at TEXT
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS referrals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        bonus REAL DEFAULT 0,
        created_at TEXT
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS live_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        pair TEXT DEFAULT '',
        signal_time TEXT DEFAULT '',
        direction TEXT DEFAULT '',
        message TEXT,
        created_at TEXT
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS notification_targets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER UNIQUE,
        title TEXT DEFAULT '',
        chat_type TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1,
        created_at TEXT
    )
    """)

    q("""
    CREATE TABLE IF NOT EXISTS mm(
        user_id INTEGER PRIMARY KEY,
        trading_balance REAL DEFAULT 0,
        profit_target REAL DEFAULT 0,
        loss_limit REAL DEFAULT 0,
        base_trade REAL DEFAULT 0,
        m1_trade REAL DEFAULT 0,
        max_trades_day INTEGER DEFAULT 0,
        stop_trading INTEGER DEFAULT 0,
        daily_pl REAL DEFAULT 0,
        daily_trades INTEGER DEFAULT 0,
        recovery_state TEXT DEFAULT 'BASE',
        session_loss REAL DEFAULT 0,
        last_day TEXT
    )
    """)

    defaults = {

        # General
        "maintenance": "0",
        "free_limit": "4",
        "withdraw_enabled": "1",
        "withdraw_hold": "1",
        "min_withdraw": "5",
        "referral_bonus": "1",
        "auto_send": "1",
        "auto_send_minutes": "5",
        "future_audience": "ALL",
        "vote_public": "0",
        "vip_reminder_days": "3",
        "confidence_default": "95–99%",

        # Text
        "welcome":
            "🚀 <b>SM QUATEX SURE SHORT</b>\n\n"
            "Welcome <b>{user_name}</b>!\n\n"
            "Select an option below.",

        "notice":
            "📢 <b>Notice</b>\n\nNo new notice.",

        "trading_contract":
            "📜 <b>Trading Contract</b>\n\n"
            "Please follow the signal time and direction carefully.",

        "trading_rules":
            "📋 <b>Trading Rules</b>\n\n"
            "• Follow the signal time\n"
            "• Use your own risk management\n"
            "• Do not overtrade\n"
            "• Use Money Management if needed",

        "help":
            "❓ <b>Help</b>\n\n"
            "Use the buttons below to access every feature.",

        "invalid":
            "❌ Invalid input. Please try again.",

        "maintenance_msg":
            "🛠 <b>Maintenance Mode</b>\n\n"
            "Please try again later.",

        "no_signal":
            "📭 No signal available right now.",

        "quota":
            "📊 Free signal remaining: <b>{remaining_signals}</b>",

        "vip_msg":
            "💎 <b>VIP MEMBER</b>\n\n"
            "VIP access is active.",

        "signal_template":
            "📅 <b>{date}</b>\n\n"
            "💹 <b>{pair}</b>\n"
            "⏰ <b>{time}</b>\n\n"
            "📌 <b>{direction}</b>\n"
            "🎯 <b>Signal Confidence: {confidence}</b>",

        "live_template":
            "⚡ <b>LIVE SIGNAL</b>\n\n"
            "💹 <b>{pair}</b>\n"
            "⏰ <b>{time}</b>\n\n"
            "📌 <b>{direction}</b>\n"
            "🎯 <b>{confidence}</b>",

        "mm_template":
            "💰 <b>Money Management</b>\n\n"
            "💵 Balance: ${balance}\n"
            "🎯 Profit Target: ${profit_target}\n"
            "🛑 Loss Limit: ${loss_limit}\n"
            "💲 Base Trade: ${base_trade}\n"
            "🔄 M1 Trade: ${m1_trade}\n"
            "📊 Today's P/L: ${daily_pl}\n"
            "🎮 Today's Trades: {daily_trades}\n"
            "➡️ Next Trade: ${next_trade}\n"
            "🔹 Mode: {recovery_state}",

        "win_text":
            "✅ <b>WIN</b>\n\n"
            "Next trade: <b>${next_trade}</b>",

        "loss_text":
            "❌ <b>LOSS</b>\n\n"
            "Next trade: <b>${next_trade}</b>",

        "skip_text":
            "⏭ <b>SKIPPED</b>\n\n"
            "Next trade: <b>${next_trade}</b>",

        "stop_text":
            "🛑 Trading stopped for today.",

        "vip_expiry":
            "💎 Your VIP expires on <b>{vip_until}</b>.",

        "main_menu":
            "🏠 Main Menu",

        "back":
            "🔙 Back",

        # Main buttons
        "b_future": "📥 Get Signal",
        "b_live": "⚡ Live Signal",
        "b_mm": "💰 Money Management",
        "b_uid": "🆔 Quotex UID",
        "b_wallet": "💳 Wallet",
        "b_withdraw": "💸 Withdraw",
        "b_referral": "👥 Referral",
        "b_history": "📜 Signal History",
        "b_vote": "🗳 Vote Signal",
        "b_notice": "📢 Notice",
        "b_rules": "📋 Trading Rules",
        "b_contract": "📜 Trading Contract",
        "b_help": "❓ Help",
        "b_notify": "🔔 Notifications",
        "b_vip": "💎 VIP",
        "b_result": "📈 Signal Result",
        "b_admin": "⚙️ Admin Panel",

        # MM buttons
        "mm_balance": "💵 Trading Balance",
        "mm_profit": "🎯 Profit Target",
        "mm_loss": "🛑 Loss Limit",
        "mm_base": "💲 Base Trade",
        "mm_m1": "🔄 M1 Trade",
        "mm_max": "📊 Max Trades/Day",
        "mm_stop": "⛔ Stop Trading",

        # Admin
        "a_future": "📡 Future Signals",
        "a_live": "⚡ Live Session",
        "a_users": "👥 Users",
        "a_vip": "💎 VIP Management",
        "a_withdraw": "💸 Withdrawals",
        "a_broadcast": "📣 Broadcast",
        "a_notice": "📢 Edit Notice",
        "a_text": "📝 Bot Text Editor",
        "a_settings": "⚙️ Settings",
        "a_subadmin": "👮 Admin Management",
        "a_targets": "📢 Notification Targets",
        "a_stats": "📊 Statistics",
        "a_backup": "💾 Backup",
        "a_vote": "🗳 Vote Stats",

        "live_start": "▶️ Start Live Session",
        "live_send": "📤 Send Live Signal",
        "live_text": "✏️ Send Live Text",
        "live_stats": "📊 Live Stats",
        "live_end": "🛑 End Live Session",

        "future_import": "📥 Import Signals",
        "future_list": "📋 Signal List",
        "future_clear": "🗑 Clear Today's Signals",
        "future_auto": "🤖 Auto Send",

        "uid_submit": "🆔 Submit UID",

        "result_win": "✅ WIN",
        "result_loss": "❌ LOSS",
        "result_skip": "⏭ SKIP",
    }

    for k, v in defaults.items():
        q(
            "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
            (k, v)
        )

    q(
        "INSERT OR IGNORE INTO admins(user_id,role,permissions) VALUES(?,?,?)",
        (
            ADMIN_ID,
            "owner",
            "all"
        )
    )


# ============================================================
# SETTINGS
# ============================================================

def get_setting(key, default=""):
    r = one(
        "SELECT value FROM settings WHERE key=?",
        (key,)
    )
    return r["value"] if r else default


def set_setting(key, value):
    q("""
    INSERT INTO settings(key,value)
    VALUES(?,?)
    ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, str(value)))


def txt(key):
    return get_setting(key, key)


# ============================================================
# USERS
# ============================================================

def register_user(message, ref=None):

    uid = message.from_user.id
    username = message.from_user.username or ""
    first = message.from_user.first_name or ""

    old = one(
        "SELECT * FROM users WHERE user_id=?",
        (uid,)
    )

    if not old:
        referred_by = None

        if ref:
            try:
                rid = int(ref)
                if rid != uid and one(
                    "SELECT user_id FROM users WHERE user_id=?",
                    (rid,)
                ):
                    referred_by = rid
            except:
                pass

        q("""
        INSERT INTO users(
            user_id,username,first_name,referred_by,created_at
        )
        VALUES(?,?,?,?,?)
        """, (
            uid,
            username,
            first,
            referred_by,
            now_str()
        ))

        if referred_by:
            bonus = float(get_setting("referral_bonus", "1"))

            q("""
            INSERT OR IGNORE INTO referrals(
                referrer_id,referred_id,bonus,created_at
            )
            VALUES(?,?,?,?)
            """, (
                referred_by,
                uid,
                bonus,
                now_str()
            ))

            add_balance(
                referred_by,
                bonus,
                "REFERRAL",
                f"Referral bonus from {uid}"
            )

    else:
        q("""
        UPDATE users
        SET username=?,first_name=?
        WHERE user_id=?
        """, (
            username,
            first,
            uid
        ))

    return one(
        "SELECT * FROM users WHERE user_id=?",
        (uid,)
    )


def user(uid):
    return one(
        "SELECT * FROM users WHERE user_id=?",
        (uid,)
    )


def is_vip(uid):

    u = user(uid)

    if not u:
        return False

    if not u["is_vip"]:
        return False

    if u["vip_until"]:
        try:
            expiry = datetime.fromisoformat(u["vip_until"])

            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=TZ)

            if expiry < now():
                q(
                    "UPDATE users SET is_vip=0 WHERE user_id=?",
                    (uid,)
                )
                return False
        except:
            pass

    return True


# ============================================================
# ADMIN PERMISSION
# ============================================================

def admin_info(uid):
    return one(
        "SELECT * FROM admins WHERE user_id=?",
        (uid,)
    )


def is_admin(uid):
    return admin_info(uid) is not None


def can(uid, permission):

    if uid == ADMIN_ID:
        return True

    a = admin_info(uid)

    if not a:
        return False

    if a["permissions"] == "all":
        return True

    return permission in [
        x.strip()
        for x in a["permissions"].split(",")
        if x.strip()
    ]


# ============================================================
# KEYBOARD HELPERS
# ============================================================

def kb(rows, resize=True):
    k = types.ReplyKeyboardMarkup(
        resize_keyboard=resize
    )

    for row in rows:
        k.row(*row)

    return k


def main_keyboard(uid):

    rows = [
        [txt("b_future"), txt("b_live")],
        [txt("b_mm"), txt("b_uid")],
        [txt("b_wallet"), txt("b_withdraw")],
        [txt("b_referral"), txt("b_history")],
        [txt("b_vote"), txt("b_result")],
        [txt("b_notice"), txt("b_rules")],
        [txt("b_contract"), txt("b_help")],
        [txt("b_notify"), txt("b_vip")]
    ]

    if is_admin(uid):
        rows.append([txt("b_admin")])

    return kb(rows)


def back_keyboard():
    return kb([
        [txt("back"), txt("main_menu")]
    ])


def admin_keyboard(uid):

    rows = [
        [txt("a_future"), txt("a_live")],
        [txt("a_users"), txt("a_vip")],
        [txt("a_withdraw"), txt("a_broadcast")],
        [txt("a_notice"), txt("a_text")],
        [txt("a_settings"), txt("a_subadmin")],
        [txt("a_targets"), txt("a_stats")],
        [txt("a_vote"), txt("a_backup")],
        [txt("main_menu")]
    ]

    return kb(rows)


def future_admin_keyboard():
    return kb([
        [txt("future_import")],
        [txt("future_list")],
        [txt("future_clear")],
        [txt("future_auto")],
        [txt("back"), txt("main_menu")]
    ])


def live_admin_keyboard():
    return kb([
        [txt("live_start")],
        [txt("live_send"), txt("live_text")],
        [txt("live_stats")],
        [txt("live_end")],
        [txt("back"), txt("main_menu")]
    ])


# ============================================================
# MESSAGE SENDER
# ============================================================

def send(chat_id, text, keyboard=None):

    try:
        bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard
        )
        return True
    except Exception as e:
        logging.warning(
            "Send failed %s: %s",
            chat_id,
            e
        )
        return False


def safe_send(chat_id, text):
    try:
        bot.send_message(chat_id, text)
        return True
    except:
        return False


# ============================================================
# MAINTENANCE
# ============================================================

def maintenance_on():
    return get_setting("maintenance", "0") == "1"


# ============================================================
# QUOTA - 2 DAY CALENDAR CYCLE
# ============================================================

def cycle_start():

    d = now().date()

    if d.day % 2 == 0:
        return d
    return d - timedelta(days=1)


def cycle_key():
    return cycle_start().strftime("%Y-%m-%d")


def user_limit(uid):

    u = user(uid)

    if not u:
        return int(get_setting("free_limit", "4"))

    if u["free_limit_override"] >= 0:
        return u["free_limit_override"]

    return int(get_setting("free_limit", "4"))


def used_in_cycle(uid):

    start = cycle_start().strftime("%Y-%m-%d")

    row = one("""
    SELECT COUNT(*) AS c
    FROM deliveries d
    JOIN signals s ON s.id=d.signal_id
    WHERE d.user_id=?
      AND d.delivery_type IN ('manual','auto')
      AND s.signal_date>=?
    """, (
        uid,
        start
    ))

    return int(row["c"]) if row else 0


def remaining(uid):

    if is_vip(uid):
        return 999999

    return max(
        0,
        user_limit(uid) - used_in_cycle(uid)
    )


# ============================================================
# SIGNAL PARSER
# ============================================================

SIGNAL_RE = re.compile(
    r"^\s*(\d{1,2}:\d{2})\s*[-|]\s*"
    r"([A-Za-z0-9/_-]+(?:-[A-Za-z0-9]+)?)\s*[-|]\s*"
    r"(UP|DOWN|BUY|SELL)\b",
    re.I
)


def normalize_direction(d):
    d = d.upper()

    if d in ("UP", "BUY"):
        return "⬆️ UP"

    return "⬇️ DOWN"


def parse_signals(text):

    found = []

    for line in text.splitlines():

        line = line.strip()

        if not line:
            continue

        m = SIGNAL_RE.match(line)

        if not m:
            continue

        tm = m.group(1)
        pair = m.group(2).upper()
        direction = normalize_direction(m.group(3))

        try:
            datetime.strptime(tm, "%H:%M")
        except:
            continue

        found.append(
            (
                tm,
                pair,
                direction
            )
        )

    return found


# ============================================================
# SIGNAL FORMAT
# ============================================================

def signal_text(signal):

    return txt("signal_template").format(
        date=signal["signal_date"],
        pair=escape(signal["pair"]),
        time=signal["signal_time"],
        direction=signal["direction"],
        confidence=escape(
            signal["confidence"]
            or get_setting(
                "confidence_default",
                "95–99%"
            )
        )
    )


# ============================================================
# AUDIENCE
# ============================================================

def audience_users():

    mode = get_setting(
        "future_audience",
        "ALL"
    )

    if mode == "VIP":
        return q(
            "SELECT user_id FROM users WHERE is_vip=1",
            fetch=True
        )

    if mode == "SELECTED":
        ids = get_setting(
            "selected_users",
            ""
        )

        if not ids:
            return []

        result = []

        for x in ids.split(","):
            try:
                r = one(
                    "SELECT user_id FROM users WHERE user_id=?",
                    (int(x),)
                )
                if r:
                    result.append(r)
            except:
                pass

        return result

    return q(
        "SELECT user_id FROM users",
        fetch=True
    )


# ============================================================
# DELIVERY
# ============================================================

def delivered(signal_id, uid):

    return one("""
    SELECT id FROM deliveries
    WHERE signal_id=? AND user_id=?
    """, (
        signal_id,
        uid
    )) is not None


def record_delivery(signal_id, uid, dtype):

    try:
        q("""
        INSERT OR IGNORE INTO deliveries(
            signal_id,user_id,delivery_type,delivered_at
        )
        VALUES(?,?,?,?)
        """, (
            signal_id,
            uid,
            dtype,
            now_str()
        ))
        return True
    except:
        return False


def can_receive_signal(uid):

    if is_vip(uid):
        return True

    return remaining(uid) > 0


def next_signal(uid):

    rows = q("""
    SELECT *
    FROM signals
    WHERE signal_date=?
      AND status='scheduled'
    ORDER BY signal_time,id
    """, (
        today(),
    ), True)

    for s in rows:

        if delivered(s["id"], uid):
            continue

        return s

    return None


# ============================================================
# SEND SIGNAL TO USER
# ============================================================

def deliver_signal(uid, signal, dtype="manual"):

    if delivered(signal["id"], uid):
        return False

    if not can_receive_signal(uid):
        return False

    text = signal_text(signal)

    send(
        uid,
        text,
        kb([
            [txt("b_vote"), txt("b_result")],
            [txt("b_future")],
            [txt("back"), txt("main_menu")]
        ])
    )

    record_delivery(
        signal["id"],
        uid,
        dtype
    )

    return True


# ============================================================
# AUTO SEND
# ============================================================

def auto_send_signals():

    if get_setting("auto_send", "1") != "1":
        return

    minute = int(
        get_setting(
            "auto_send_minutes",
            "5"
        )
    )

    current = now()

    rows = q("""
    SELECT *
    FROM signals
    WHERE signal_date=?
      AND auto_sent=0
      AND status='scheduled'
    ORDER BY signal_time,id
    """, (
        today(),
    ), True)

    for s in rows:

        try:
            dt = datetime.strptime(
                s["signal_date"] + " " + s["signal_time"],
                "%Y-%m-%d %H:%M"
            ).replace(tzinfo=TZ)
        except:
            continue

        start = dt - timedelta(minutes=minute)

        if start <= current < dt:

            for u in audience_users():

                uid = int(u["user_id"])

                if not u["notifications"]:
                    continue

                deliver_signal(
                    uid,
                    s,
                    "auto"
                )

            targets = q("""
            SELECT * FROM notification_targets
            WHERE enabled=1
            """, fetch=True)

            message = signal_text(s)

            for t in targets:
                safe_send(
                    int(t["chat_id"]),
                    message
                )

            q(
                "UPDATE signals SET auto_sent=1 WHERE id=?",
                (s["id"],)
            )


# ============================================================
# MONEY MANAGEMENT
# ============================================================

def ensure_mm(uid):

    row = one(
        "SELECT * FROM mm WHERE user_id=?",
        (uid,)
    )

    if not row:
        q("""
        INSERT INTO mm(
            user_id,last_day
        )
        VALUES(?,?)
        """, (
            uid,
            today()
        ))

    row = one(
        "SELECT * FROM mm WHERE user_id=?",
        (uid,)
    )

    if row["last_day"] != today():

        q("""
        UPDATE mm
        SET daily_pl=0,
            daily_trades=0,
            stop_trading=0,
            recovery_state='BASE',
            session_loss=0,
            last_day=?
        WHERE user_id=?
        """, (
            today(),
            uid
        ))

    return one(
        "SELECT * FROM mm WHERE user_id=?",
        (uid,)
    )


def next_trade_amount(uid):

    m = ensure_mm(uid)

    if m["recovery_state"] == "M1":
        return float(m["m1_trade"])

    return float(m["base_trade"])


def mm_status(uid):

    m = ensure_mm(uid)

    return txt("mm_template").format(
        balance=f"{m['trading_balance']:.2f}",
        profit_target=f"{m['profit_target']:.2f}",
        loss_limit=f"{m['loss_limit']:.2f}",
        base_trade=f"{m['base_trade']:.2f}",
        m1_trade=f"{m['m1_trade']:.2f}",
        daily_pl=f"{m['daily_pl']:.2f}",
        daily_trades=m["daily_trades"],
        next_trade=f"{next_trade_amount(uid):.2f}",
        recovery_state=m["recovery_state"]
    )


def mm_can_trade(uid):

    m = ensure_mm(uid)

    if m["stop_trading"]:
        return False

    if m["max_trades_day"] > 0:
        if m["daily_trades"] >= m["max_trades_day"]:
            return False

    if m["profit_target"] > 0:
        if m["daily_pl"] >= m["profit_target"]:
            return False

    if m["loss_limit"] > 0:
        if m["daily_pl"] <= -abs(m["loss_limit"]):
            return False

    return True


def record_result(uid, result):

    m = ensure_mm(uid)

    amount = next_trade_amount(uid)

    if amount <= 0:
        return 0, "BASE"

    if result == "WIN":

        pl = amount

        new_pl = m["daily_pl"] + pl

        q("""
        UPDATE mm
        SET daily_pl=?,
            daily_trades=daily_trades+1,
            recovery_state='BASE',
            session_loss=0
        WHERE user_id=?
        """, (
            new_pl,
            uid
        ))

        if (
            m["profit_target"] > 0
            and new_pl >= m["profit_target"]
        ):
            q(
                "UPDATE mm SET stop_trading=1 WHERE user_id=?",
                (uid,)
            )

        return amount, "BASE"

    if result == "LOSS":

        pl = -amount

        new_pl = m["daily_pl"] + pl

        if m["recovery_state"] == "BASE":

            q("""
            UPDATE mm
            SET daily_pl=?,
                daily_trades=daily_trades+1,
                recovery_state='M1',
                session_loss=session_loss+?
            WHERE user_id=?
            """, (
                new_pl,
                amount,
                uid
            ))

            next_amt = float(m["m1_trade"])

            if (
                m["max_trades_day"] > 0
                and m["daily_trades"] + 1 >= m["max_trades_day"]
            ):
                q(
                    "UPDATE mm SET stop_trading=1 WHERE user_id=?",
                    (uid,)
                )

            return next_amt, "M1"

        # M1 LOSS -> no M2
        q("""
        UPDATE mm
        SET daily_pl=?,
            daily_trades=daily_trades+1,
            recovery_state='BASE',
            session_loss=session_loss+?,
            stop_trading=CASE
                WHEN ? > 0 AND ? <= -? THEN 1
                ELSE stop_trading
            END
        WHERE user_id=?
        """, (
            new_pl,
            amount,
            m["loss_limit"],
            new_pl,
            abs(m["loss_limit"]),
            uid
        ))

        return float(m["base_trade"]), "BASE"

    return amount, m["recovery_state"]


# ============================================================
# WALLET
# ============================================================

def add_balance(uid, amount, typ, description):

    u = user(uid)

    if not u:
        return

    new_balance = float(u["balance"]) + float(amount)

    q(
        "UPDATE users SET balance=? WHERE user_id=?",
        (new_balance, uid)
    )

    q("""
    INSERT INTO wallet_history(
        user_id,amount,type,description,created_at
    )
    VALUES(?,?,?,?,?)
    """, (
        uid,
        amount,
        typ,
        description,
        now_str()
    ))


# ============================================================
# VIP
# ============================================================

def set_vip(uid, days):

    expiry = now() + timedelta(days=days)

    q("""
    UPDATE users
    SET is_vip=1,vip_until=?
    WHERE user_id=?
    """, (
        expiry.isoformat(),
        uid
    ))

    return expiry


def remove_vip(uid):

    q("""
    UPDATE users
    SET is_vip=0,vip_until=NULL
    WHERE user_id=?
    """, (
        uid,
    ))


def vip_reminders():

    days = int(
        get_setting(
            "vip_reminder_days",
            "3"
        )
    )

    target_date = now().date() + timedelta(days=days)

    users = q("""
    SELECT * FROM users
    WHERE is_vip=1
      AND vip_until IS NOT NULL
    """, fetch=True)

    for u in users:

        try:
            expiry = datetime.fromisoformat(
                u["vip_until"]
            ).date()
        except:
            continue

        if expiry == target_date:

            send(
                u["user_id"],
                txt("vip_expiry").format(
                    vip_until=expiry
                ),
                main_keyboard(u["user_id"])
            )


# ============================================================
# VOTING
# ============================================================

def vote_for(uid, signal_id, vote):

    try:
        q("""
        INSERT INTO votes(
            signal_id,user_id,vote,created_at
        )
        VALUES(?,?,?,?)
        """, (
            signal_id,
            uid,
            vote,
            now_str()
        ))
        return True
    except:
        return False


def vote_stats(signal_id):

    rows = q("""
    SELECT vote,COUNT(*) c
    FROM votes
    WHERE signal_id=?
    GROUP BY vote
    """, (
        signal_id,
    ), True)

    data = {
        "UP": 0,
        "DOWN": 0,
        "SKIP": 0
    }

    for r in rows:
        data[r["vote"]] = r["c"]

    return data


# ============================================================
# SIGNAL RESULT
# ============================================================

def latest_action_signal(uid):

    r = one("""
    SELECT s.*
    FROM signals s
    JOIN deliveries d ON d.signal_id=s.id
    WHERE d.user_id=?
    ORDER BY d.id DESC
    LIMIT 1
    """, (
        uid,
    ))

    return r


# ============================================================
# BACKUP
# ============================================================

def backup_db():

    global LAST_BACKUP_DAY

    d = today()

    if LAST_BACKUP_DAY == d:
        return

    if not os.path.exists(DB_FILE):
        return

    os.makedirs(
        BACKUP_DIR,
        exist_ok=True
    )

    filename = os.path.join(
        BACKUP_DIR,
        f"bot_{d}.db"
    )

    try:
        shutil.copy2(
            DB_FILE,
            filename
        )
        LAST_BACKUP_DAY = d
        logging.info(
            "Database backup created"
        )
    except Exception as e:
        logging.error(
            "Backup error: %s",
            e
        )


# ============================================================
# SCHEDULER
# ============================================================

def scheduler():

    while True:

        try:
            auto_send_signals()
            vip_reminders()
            backup_db()
        except Exception as e:
            logging.exception(
                "Scheduler error: %s",
                e
            )

        time.sleep(10)


# ============================================================
# /START
# ============================================================

@bot.message_handler(commands=["start"])
def start(message):

    args = message.text.split(maxsplit=1)

    ref = None

    if len(args) > 1:
        ref = args[1]

    register_user(
        message,
        ref
    )

    uid = message.from_user.id

    if maintenance_on() and not is_admin(uid):
        send(
            uid,
            txt("maintenance_msg")
        )
        return

    u = user(uid)

    text = txt("welcome").format(
        user_name=escape(
            u["first_name"] or "User"
        ),
        balance=f"{u['balance']:.2f}",
        remaining_signals=remaining(uid)
    )

    send(
        uid,
        text,
        main_keyboard(uid)
    )


# ============================================================
# MAIN MENU
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("main_menu")
)
def main_menu(message):

    register_user(message)

    send(
        message.chat.id,
        txt("welcome").format(
            user_name=escape(
                message.from_user.first_name or "User"
            ),
            balance=f"{user(message.from_user.id)['balance']:.2f}",
            remaining_signals=remaining(
                message.from_user.id
            )
        ),
        main_keyboard(message.from_user.id)
    )


# ============================================================
# GET SIGNAL
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_future")
)
def get_signal(message):

    uid = message.from_user.id

    if maintenance_on() and not is_admin(uid):
        send(
            uid,
            txt("maintenance_msg")
        )
        return

    signal = next_signal(uid)

    if not signal:

        send(
            uid,
            txt("no_signal"),
            main_keyboard(uid)
        )
        return

    if not is_vip(uid):

        if remaining(uid) <= 0:

            send(
                uid,
                txt("quota").format(
                    remaining_signals=0
                ),
                main_keyboard(uid)
            )
            return

    deliver_signal(
        uid,
        signal,
        "manual"
    )

    if not is_vip(uid):

        send(
            uid,
            txt("quota").format(
                remaining_signals=remaining(uid)
            ),
            main_keyboard(uid)
        )


# ============================================================
# NOTICE
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_notice")
)
def notice(message):

    send(
        message.chat.id,
        txt("notice"),
        back_keyboard()
    )


# ============================================================
# RULES
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_rules")
)
def rules(message):

    send(
        message.chat.id,
        txt("trading_rules"),
        back_keyboard()
    )


# ============================================================
# CONTRACT
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_contract")
)
def contract(message):

    send(
        message.chat.id,
        txt("trading_contract"),
        back_keyboard()
    )


# ============================================================
# HELP
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_help")
)
def help_menu(message):

    send(
        message.chat.id,
        txt("help"),
        back_keyboard()
    )


# ============================================================
# NOTIFICATIONS
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_notify")
)
def notification_menu(message):

    uid = message.from_user.id
    u = user(uid)

    status = "ON 🔔" if u["notifications"] else "OFF 🔕"

    send(
        uid,
        f"🔔 <b>Notifications: {status}</b>",
        kb([
            ["🔔 ON", "🔕 OFF"],
            [txt("back"), txt("main_menu")]
        ])
    )


@bot.message_handler(
    func=lambda m: m.text in ["🔔 ON", "🔕 OFF"]
)
def notification_change(message):

    uid = message.from_user.id

    value = 1 if message.text == "🔔 ON" else 0

    q(
        "UPDATE users SET notifications=? WHERE user_id=?",
        (value, uid)
    )

    send(
        uid,
        "✅ Notification setting updated.",
        main_keyboard(uid)
    )


# ============================================================
# UID
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_uid")
)
def uid_menu(message):

    uid = message.from_user.id
    u = user(uid)

    if u["uid"]:
        send(
            uid,
            f"🆔 Your Quotex UID:\n\n<b>{escape(u['uid'])}</b>",
            main_keyboard(uid)
        )
        return

    if u["pending_uid"]:
        send(
            uid,
            f"⏳ UID pending approval:\n\n<b>{escape(u['pending_uid'])}</b>",
            main_keyboard(uid)
        )
        return

    STATE[uid] = "UID"

    send(
        uid,
        "🆔 <b>Enter your Quotex UID:</b>",
        back_keyboard()
    )


# ============================================================
# MONEY MANAGEMENT
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_mm")
)
def money_management(message):

    uid = message.from_user.id

    send(
        uid,
        mm_status(uid),
        kb([
            [txt("mm_balance"), txt("mm_profit")],
            [txt("mm_loss"), txt("mm_base")],
            [txt("mm_m1"), txt("mm_max")],
            [txt("mm_stop")],
            [txt("back"), txt("main_menu")]
        ])
    )


def mm_input(uid, field, prompt):

    STATE[uid] = "MM_" + field

    send(
        uid,
        prompt,
        back_keyboard()
    )


@bot.message_handler(
    func=lambda m: m.text == txt("mm_balance")
)
def mm_balance(message):
    mm_input(
        message.from_user.id,
        "balance",
        "💵 Enter Trading Balance in USD:"
    )


@bot.message_handler(
    func=lambda m: m.text == txt("mm_profit")
)
def mm_profit(message):
    mm_input(
        message.from_user.id,
        "profit",
        "🎯 Enter Profit Target in USD:"
    )


@bot.message_handler(
    func=lambda m: m.text == txt("mm_loss")
)
def mm_loss(message):
    mm_input(
        message.from_user.id,
        "loss",
        "🛑 Enter Loss Limit in USD:"
    )


@bot.message_handler(
    func=lambda m: m.text == txt("mm_base")
)
def mm_base(message):
    mm_input(
        message.from_user.id,
        "base",
        "💲 Enter Base Trade amount in USD:"
    )


@bot.message_handler(
    func=lambda m: m.text == txt("mm_m1")
)
def mm_m1(message):
    mm_input(
        message.from_user.id,
        "m1",
        "🔄 Enter M1 Trade amount in USD:"
    )


@bot.message_handler(
    func=lambda m: m.text == txt("mm_max")
)
def mm_max(message):
    mm_input(
        message.from_user.id,
        "max",
        "📊 Enter Max Trades per Day:\n\nUse 0 for unlimited."
    )


@bot.message_handler(
    func=lambda m: m.text == txt("mm_stop")
)
def mm_stop(message):

    uid = message.from_user.id

    m = ensure_mm(uid)

    value = 0 if m["stop_trading"] else 1

    q(
        "UPDATE mm SET stop_trading=? WHERE user_id=?",
        (value, uid)
    )

    send(
        uid,
        "✅ Stop Trading turned " +
        ("ON" if value else "OFF"),
        main_keyboard(uid)
    )


# ============================================================
# WALLET
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_wallet")
)
def wallet(message):

    uid = message.from_user.id
    u = user(uid)

    rows = q("""
    SELECT * FROM wallet_history
    WHERE user_id=?
    ORDER BY id DESC
    LIMIT 10
    """, (
        uid,
    ), True)

    text = (
        f"💳 <b>Wallet</b>\n\n"
        f"Balance: <b>${u['balance']:.2f}</b>\n\n"
    )

    if not rows:
        text += "No transaction history."

    else:
        for r in rows:
            text += (
                f"• {r['type']}: "
                f"${r['amount']:.2f}\n"
                f"  {escape(r['description'])}\n"
            )

    send(
        uid,
        text,
        main_keyboard(uid)
    )


# ============================================================
# WITHDRAW
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_withdraw")
)
def withdraw(message):

    uid = message.from_user.id

    if get_setting("withdraw_enabled", "1") != "1":

        send(
            uid,
            "❌ Withdraw is currently disabled.",
            main_keyboard(uid)
        )
        return

    u = user(uid)

    minimum = float(
        get_setting(
            "min_withdraw",
            "5"
        )
    )

    send(
        uid,
        f"💸 <b>Withdraw</b>\n\n"
        f"Balance: ${u['balance']:.2f}\n"
        f"Minimum: ${minimum:.2f}\n\n"
        f"Enter amount:",
        back_keyboard()
    )

    STATE[uid] = "WITHDRAW"


# ============================================================
# REFERRAL
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_referral")
)
def referral(message):

    uid = message.from_user.id

    u = user(uid)

    me = bot.get_me()

    link = (
        f"https://t.me/{me.username}?start={uid}"
    )

    count = one("""
    SELECT COUNT(*) c
    FROM referrals
    WHERE referrer_id=?
    """, (
        uid,
    ))

    send(
        uid,
        f"👥 <b>Referral</b>\n\n"
        f"Your referrals: <b>{count['c']}</b>\n\n"
        f"Your link:\n"
        f"<code>{link}</code>\n\n"
        f"Bonus: ${get_setting('referral_bonus','1')}",
        main_keyboard(uid)
    )


# ============================================================
# VIP USER
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_vip")
)
def vip_user(message):

    uid = message.from_user.id
    u = user(uid)

    if is_vip(uid):

        send(
            uid,
            txt("vip_msg") +
            "\n\n" +
            txt("vip_expiry").format(
                vip_until=u["vip_until"]
            ),
            main_keyboard(uid)
        )

    else:

        send(
            uid,
            "💎 You are currently a normal member.\n\n"
            "Contact admin for VIP access.",
            main_keyboard(uid)
        )


# ============================================================
# SIGNAL HISTORY
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_history")
)
def history(message):

    uid = message.from_user.id

    rows = q("""
    SELECT s.*,d.delivery_type
    FROM signals s
    JOIN deliveries d ON d.signal_id=s.id
    WHERE d.user_id=?
    ORDER BY d.id DESC
    LIMIT 20
    """, (
        uid,
    ), True)

    if not rows:

        send(
            uid,
            "📜 No signal history.",
            main_keyboard(uid)
        )
        return

    text = "📜 <b>Signal History</b>\n\n"

    for s in rows:

        result = one(
            "SELECT result FROM signal_results WHERE signal_id=?",
            (s["id"],)
        )

        r = result["result"] if result else "PENDING"

        text += (
            f"#{s['id']} "
            f"{s['signal_date']} "
            f"{s['signal_time']}\n"
            f"{s['pair']} — {s['direction']}\n"
            f"Result: <b>{r}</b>\n\n"
        )

    send(
        uid,
        text,
        main_keyboard(uid)
    )


# ============================================================
# VOTE
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_vote")
)
def vote_menu(message):

    uid = message.from_user.id

    signal = latest_action_signal(uid)

    if not signal:

        send(
            uid,
            txt("no_signal"),
            main_keyboard(uid)
        )
        return

    existing = one("""
    SELECT * FROM votes
    WHERE signal_id=? AND user_id=?
    """, (
        signal["id"],
        uid
    ))

    if existing:

        send(
            uid,
            "✅ You already voted for this signal.",
            main_keyboard(uid)
        )
        return

    DATA[uid] = {
        "vote_signal": signal["id"]
    }

    send(
        uid,
        f"🗳 <b>Vote for Signal #{signal['id']}</b>",
        kb([
            ["⬆️ UP", "⬇️ DOWN"],
            ["🤝 SKIP"],
            [txt("back"), txt("main_menu")]
        ])
    )


@bot.message_handler(
    func=lambda m: m.text in ["⬆️ UP", "⬇️ DOWN", "🤝 SKIP"]
)
def vote_submit(message):

    uid = message.from_user.id

    signal_id = DATA.get(uid, {}).get(
        "vote_signal"
    )

    if not signal_id:
        return

    vote = {
        "⬆️ UP": "UP",
        "⬇️ DOWN": "DOWN",
        "🤝 SKIP": "SKIP"
    }[message.text]

    if vote_for(
        uid,
        signal_id,
        vote
    ):
        send(
            uid,
            "✅ Vote submitted.",
            main_keyboard(uid)
        )
    else:
        send(
            uid,
            "⚠️ You already voted.",
            main_keyboard(uid)
        )

    DATA.pop(uid, None)


# ============================================================
# RESULT
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_result")
)
def result_menu(message):

    uid = message.from_user.id

    signal = latest_action_signal(uid)

    if not signal:

        send(
            uid,
            txt("no_signal"),
            main_keyboard(uid)
        )
        return

    old = one(
        "SELECT result FROM signal_results WHERE signal_id=?",
        (signal["id"],)
    )

    if old:

        send(
            uid,
            f"Signal #{signal['id']} result: "
            f"<b>{old['result']}</b>",
            main_keyboard(uid)
        )
        return

    DATA[uid] = {
        "result_signal": signal["id"]
    }

    send(
        uid,
        f"📈 <b>Signal #{signal['id']} Result</b>",
        kb([
            [txt("result_win"), txt("result_loss")],
            [txt("result_skip")],
            [txt("back"), txt("main_menu")]
        ])
    )


@bot.message_handler(
    func=lambda m: m.text in [
        txt("result_win"),
        txt("result_loss"),
        txt("result_skip")
    ]
)
def result_submit(message):

    uid = message.from_user.id

    signal_id = DATA.get(uid, {}).get(
        "result_signal"
    )

    if not signal_id:
        return

    result_map = {
        txt("result_win"): "WIN",
        txt("result_loss"): "LOSS",
        txt("result_skip"): "SKIP"
    }

    result = result_map[message.text]

    old = one(
        "SELECT result FROM signal_results WHERE signal_id=?",
        (signal_id,)
    )

    if old:
        send(
            uid,
            "⚠️ Result already submitted.",
            main_keyboard(uid)
        )
        return

    q("""
    INSERT INTO signal_results(
        signal_id,result,updated_at
    )
    VALUES(?,?,?)
    """, (
        signal_id,
        result,
        now_str()
    ))

    if result == "WIN":
        next_amount, mode = record_result(
            uid,
            "WIN"
        )

        text = txt("win_text").format(
            next_trade=f"{next_amount:.2f}"
        )

    elif result == "LOSS":

        next_amount, mode = record_result(
            uid,
            "LOSS"
        )

        text = txt("loss_text").format(
            next_trade=f"{next_amount:.2f}"
        )

    else:

        m = ensure_mm(uid)

        text = txt("skip_text").format(
            next_trade=f"{next_trade_amount(uid):.2f}"
        )

    send(
        uid,
        text,
        main_keyboard(uid)
    )

    DATA.pop(uid, None)


# ============================================================
# LIVE USER
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_live")
)
def live_user(message):

    send(
        message.chat.id,
        "⚡ <b>Live Signal</b>\n\n"
        "Live signals will appear here when a session is active.",
        main_keyboard(message.from_user.id)
    )


# ============================================================
# ADMIN PANEL
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("b_admin")
)
def admin_panel(message):

    uid = message.from_user.id

    if not is_admin(uid):
        return

    send(
        uid,
        "⚙️ <b>ADMIN PANEL</b>\n\n"
        "Select an option:",
        admin_keyboard(uid)
    )


# ============================================================
# ADMIN FUTURE SIGNAL
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_future")
)
def admin_future(message):

    if not can(message.from_user.id, "signals"):
        return

    send(
        message.chat.id,
        "📡 <b>Future Signals</b>\n\n"
        "You can import ALL signals at once.\n\n"
        "Example:\n\n"
        "13:04 - USD/BDT-OTC - UP\n"
        "13:14 - USD/PHP-OTC - DOWN\n"
        "13:21 - USD/COP-OTC - UP\n\n"
        "Blank lines and headers are ignored.",
        future_admin_keyboard()
    )


@bot.message_handler(
    func=lambda m: m.text == txt("future_import")
)
def future_import(message):

    uid = message.from_user.id

    if not can(uid, "signals"):
        return

    STATE[uid] = "IMPORT_SIGNALS"

    send(
        uid,
        "📥 <b>Paste ALL today's signals in one message.</b>\n\n"
        "The bot will automatically detect:\n"
        "TIME - PAIR - UP/DOWN\n\n"
        "Example:\n"
        "13:04 - USD/BDT-OTC - UP\n"
        "13:14 - USD/PHP-OTC - DOWN",
        back_keyboard()
    )


@bot.message_handler(
    func=lambda m: m.text == txt("future_list")
)
def future_list(message):

    rows = q("""
    SELECT * FROM signals
    WHERE signal_date=?
    ORDER BY signal_time,id
    """, (
        today(),
    ), True)

    if not rows:

        send(
            message.chat.id,
            "📭 No signals for today.",
            future_admin_keyboard()
        )
        return

    text = "📋 <b>Today's Signals</b>\n\n"

    for s in rows:

        text += (
            f"#{s['id']} "
            f"{s['signal_time']} | "
            f"{s['pair']} | "
            f"{s['direction']} | "
            f"{'AUTO SENT' if s['auto_sent'] else 'WAITING'}\n"
        )

    send(
        message.chat.id,
        text,
        future_admin_keyboard()
    )


@bot.message_handler(
    func=lambda m: m.text == txt("future_clear")
)
def future_clear(message):

    uid = message.from_user.id

    if not can(uid, "signals"):
        return

    q(
        "DELETE FROM signals WHERE signal_date=?",
        (today(),)
    )

    send(
        uid,
        "🗑 Today's signals cleared.",
        future_admin_keyboard()
    )


@bot.message_handler(
    func=lambda m: m.text == txt("future_auto")
)
def future_auto(message):

    uid = message.from_user.id

    current = get_setting(
        "auto_send",
        "1"
    )

    new = "0" if current == "1" else "1"

    set_setting(
        "auto_send",
        new
    )

    status = "ON 🤖" if new == "1" else "OFF 🔕"

    send(
        uid,
        f"🤖 Future Signal Auto Send: <b>{status}</b>\n\n"
        f"Current setting sends signals "
        f"{get_setting('auto_send_minutes','5')} minutes "
        f"before trading time.",
        future_admin_keyboard()
    )


# ============================================================
# ADMIN LIVE SESSION
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_live")
)
def admin_live(message):

    if not can(message.from_user.id, "live"):
        return

    send(
        message.chat.id,
        "⚡ <b>LIVE SESSION</b>\n\n"
        f"Status: {'ACTIVE 🟢' if LIVE['active'] else 'OFF 🔴'}",
        live_admin_keyboard()
    )


@bot.message_handler(
    func=lambda m: m.text == txt("live_start")
)
def live_start(message):

    if not can(message.from_user.id, "live"):
        return

    LIVE["active"] = True
    LIVE["started_at"] = now_str()
    LIVE["admin_id"] = message.from_user.id
    LIVE["count"] = 0

    send(
        message.chat.id,
        "🟢 <b>Live Session Started</b>\n\n"
        "Now use <b>Send Live Signal</b> or "
        "<b>Send Live Text</b>.",
        live_admin_keyboard()
    )


@bot.message_handler(
    func=lambda m: m.text == txt("live_send")
)
def live_send(message):

    if not LIVE["active"]:
        send(
            message.chat.id,
            "🔴 Live session is not active.",
            live_admin_keyboard()
        )
        return

    STATE[message.from_user.id] = "LIVE_SIGNAL"

    send(
        message.chat.id,
        "📤 Send live signal:\n\n"
        "Example:\n"
        "13:59 - USD/BDT-OTC - DOWN",
        back_keyboard()
    )


@bot.message_handler(
    func=lambda m: m.text == txt("live_text")
)
def live_text(message):

    if not LIVE["active"]:
        send(
            message.chat.id,
            "🔴 Live session is not active.",
            live_admin_keyboard()
        )
        return

    STATE[message.from_user.id] = "LIVE_TEXT"

    send(
        message.chat.id,
        "✏️ Send the live message/text now.",
        back_keyboard()
    )


@bot.message_handler(
    func=lambda m: m.text == txt("live_stats")
)
def live_stats(message):

    count = LIVE["count"]

    row = one("""
    SELECT COUNT(*) c
    FROM live_history
    WHERE date(created_at)=?
    """, (
        today(),
    ))

    send(
        message.chat.id,
        f"📊 <b>Live Statistics</b>\n\n"
        f"Current session: <b>{count}</b>\n"
        f"Today's total: <b>{row['c']}</b>",
        live_admin_keyboard()
    )


@bot.message_handler(
    func=lambda m: m.text == txt("live_end")
)
def live_end(message):

    LIVE["active"] = False
    LIVE["started_at"] = None
    LIVE["admin_id"] = None

    send(
        message.chat.id,
        "🛑 <b>Live Session Ended</b>",
        admin_keyboard(message.from_user.id)
    )


# ============================================================
# ADMIN USERS
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_users")
)
def admin_users(message):

    rows = q("""
    SELECT COUNT(*) c FROM users
    """, True)

    vip = q("""
    SELECT COUNT(*) c FROM users
    WHERE is_vip=1
    """, True)

    pending = q("""
    SELECT COUNT(*) c FROM users
    WHERE pending_uid IS NOT NULL
    """, True)

    send(
        message.chat.id,
        f"👥 <b>Users</b>\n\n"
        f"Total: <b>{rows[0]['c']}</b>\n"
        f"VIP: <b>{vip[0]['c']}</b>\n"
        f"Pending UID: <b>{pending[0]['c']}</b>",
        admin_keyboard(message.from_user.id)
    )


# ============================================================
# ADMIN VIP
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_vip")
)
def admin_vip(message):

    send(
        message.chat.id,
        "💎 <b>VIP Management</b>\n\n"
        "Use:\n"
        "ADD 123456789 30\n"
        "→ Add VIP for 30 days\n\n"
        "REMOVE 123456789\n"
        "→ Remove VIP",
        back_keyboard()
    )

    STATE[message.from_user.id] = "VIP_ADMIN"


# ============================================================
# ADMIN WITHDRAWALS
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_withdraw")
)
def admin_withdrawals(message):

    rows = q("""
    SELECT * FROM withdrawals
    WHERE status='pending'
    ORDER BY id ASC
    """, fetch=True)

    if not rows:

        send(
            message.chat.id,
            "📭 No pending withdrawals.",
            admin_keyboard(message.from_user.id)
        )
        return

    text = "💸 <b>Pending Withdrawals</b>\n\n"

    for r in rows:

        text += (
            f"#{r['id']} | "
            f"User: <code>{r['user_id']}</code> | "
            f"${r['amount']:.2f}\n"
        )

    text += (
        "\nUse:\n"
        "APPROVE ID\n"
        "REJECT ID"
    )

    send(
        message.chat.id,
        text,
        back_keyboard()
    )

    STATE[message.from_user.id] = "WITHDRAW_ADMIN"


# ============================================================
# ADMIN BROADCAST
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_broadcast")
)
def admin_broadcast(message):

    send(
        message.chat.id,
        "📣 <b>Broadcast</b>\n\n"
        "Use:\n"
        "ALL your message\n"
        "VIP your message\n"
        "SELECTED your message",
        back_keyboard()
    )

    STATE[message.from_user.id] = "BROADCAST"


# ============================================================
# ADMIN NOTICE
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_notice")
)
def admin_notice(message):

    send(
        message.chat.id,
        "📢 Send the new Notice text.\n\n"
        "HTML formatting is supported.",
        back_keyboard()
    )

    STATE[message.from_user.id] = "NOTICE_ADMIN"


# ============================================================
# ADMIN TEXT EDITOR
# ============================================================

TEXT_KEYS = {
    "Welcome": "welcome",
    "Notice": "notice",
    "Trading Contract": "trading_contract",
    "Trading Rules": "trading_rules",
    "Help": "help",
    "Invalid": "invalid",
    "Maintenance": "maintenance_msg",
    "Signal Template": "signal_template",
    "Live Template": "live_template",
    "Money Management": "mm_template",
    "WIN Text": "win_text",
    "LOSS Text": "loss_text",
    "SKIP Text": "skip_text",
    "VIP Expiry": "vip_expiry",
    "Main Menu": "main_menu",
    "Back": "back",
    "Get Signal Button": "b_future",
    "Live Signal Button": "b_live",
    "Money Management Button": "b_mm",
    "UID Button": "b_uid",
    "Wallet Button": "b_wallet",
    "Withdraw Button": "b_withdraw",
    "Referral Button": "b_referral",
    "History Button": "b_history",
    "Vote Button": "b_vote",
    "Result Button": "b_result",
    "Notice Button": "b_notice",
    "Rules Button": "b_rules",
    "Contract Button": "b_contract",
    "Help Button": "b_help",
    "Notification Button": "b_notify",
    "VIP Button": "b_vip",
    "Admin Button": "b_admin",
}


@bot.message_handler(
    func=lambda m: m.text == txt("a_text")
)
def text_editor(message):

    rows = []

    names = list(TEXT_KEYS.keys())

    for i in range(0, len(names), 2):
        rows.append(
            names[i:i+2]
        )

    rows.append(
        [txt("back"), txt("main_menu")]
    )

    send(
        message.chat.id,
        "📝 <b>Bot Text Editor</b>\n\n"
        "Select any text you want to edit.",
        kb(rows)
    )


@bot.message_handler(
    func=lambda m: m.text in TEXT_KEYS
)
def choose_text(message):

    key = TEXT_KEYS[message.text]

    DATA[message.from_user.id] = {
        "edit_key": key
    }

    STATE[message.from_user.id] = "TEXT_EDIT"

    send(
        message.chat.id,
        "✏️ <b>Send the new text.</b>\n\n"
        "Supported placeholders:\n"
        "{user_name}\n"
        "{date}\n"
        "{time}\n"
        "{pair}\n"
        "{direction}\n"
        "{confidence}\n"
        "{balance}\n"
        "{remaining_signals}\n"
        "{next_trade}",
        back_keyboard()
    )


# ============================================================
# ADMIN SETTINGS
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_settings")
)
def admin_settings(message):

    send(
        message.chat.id,
        f"⚙️ <b>Settings</b>\n\n"
        f"Free limit: {get_setting('free_limit')}\n"
        f"Min withdraw: ${get_setting('min_withdraw')}\n"
        f"Withdraw: {get_setting('withdraw_enabled')}\n"
        f"Withdraw hold: {get_setting('withdraw_hold')}\n"
        f"Referral bonus: ${get_setting('referral_bonus')}\n"
        f"Auto Send: {get_setting('auto_send')}\n"
        f"Auto minutes: {get_setting('auto_send_minutes')}\n"
        f"Audience: {get_setting('future_audience')}\n"
        f"Vote public: {get_setting('vote_public')}\n\n"
        f"Use:\n"
        f"FREE 4\n"
        f"MINWD 5\n"
        f"REF 1\n"
        f"AUTO 1\n"
        f"MINUTES 5\n"
        f"AUDIENCE ALL\n"
        f"MAINTENANCE 0\n"
        f"VOTE_PUBLIC 0",
        back_keyboard()
    )

    STATE[message.from_user.id] = "SETTINGS_ADMIN"


# ============================================================
# ADMIN SUB ADMIN
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_subadmin")
)
def subadmin_menu(message):

    rows = q("""
    SELECT * FROM admins
    WHERE user_id!=?
    ORDER BY user_id
    """, (
        ADMIN_ID,
    ), True)

    text = (
        "👮 <b>Admin Management</b>\n\n"
        "Add:\n"
        "ADDADMIN 123456789 signals,vip,withdraw\n\n"
        "Remove:\n"
        "DELADMIN 123456789\n\n"
        "Current:\n"
    )

    for r in rows:
        text += (
            f"{r['user_id']} — "
            f"{r['permissions']}\n"
        )

    send(
        message.chat.id,
        text,
        back_keyboard()
    )

    STATE[message.from_user.id] = "SUBADMIN"


# ============================================================
# NOTIFICATION TARGETS
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_targets")
)
def targets_menu(message):

    rows = q("""
    SELECT * FROM notification_targets
    ORDER BY id
    """, fetch=True)

    text = (
        "📢 <b>Notification Targets</b>\n\n"
        "Add target:\n"
        "ADD -1001234567890 Group Name\n\n"
        "Delete:\n"
        "DEL -1001234567890\n\n"
    )

    for r in rows:
        text += (
            f"• {r['chat_id']} — "
            f"{escape(r['title'])}\n"
        )

    send(
        message.chat.id,
        text,
        back_keyboard()
    )

    STATE[message.from_user.id] = "TARGETS"


# ============================================================
# ADMIN STATS
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_stats")
)
def admin_stats(message):

    users = one(
        "SELECT COUNT(*) c FROM users"
    )["c"]

    vip = one(
        "SELECT COUNT(*) c FROM users WHERE is_vip=1"
    )["c"]

    signals = one(
        "SELECT COUNT(*) c FROM signals WHERE signal_date=?",
        (today(),)
    )["c"]

    deliveries = one("""
    SELECT COUNT(*) c
    FROM deliveries d
    JOIN signals s ON s.id=d.signal_id
    WHERE s.signal_date=?
    """, (
        today(),
    ))["c"]

    votes = one(
        "SELECT COUNT(*) c FROM votes"
    )["c"]

    pending = one("""
    SELECT COUNT(*) c
    FROM withdrawals
    WHERE status='pending'
    """)["c"]

    send(
        message.chat.id,
        f"📊 <b>Bot Statistics</b>\n\n"
        f"Users: {users}\n"
        f"VIP: {vip}\n"
        f"Today's Signals: {signals}\n"
        f"Today's Deliveries: {deliveries}\n"
        f"Votes: {votes}\n"
        f"Pending Withdrawals: {pending}",
        admin_keyboard(message.from_user.id)
    )


# ============================================================
# ADMIN BACKUP
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_backup")
)
def admin_backup(message):

    if os.path.exists(DB_FILE):

        os.makedirs(
            BACKUP_DIR,
            exist_ok=True
        )

        filename = os.path.join(
            BACKUP_DIR,
            "manual_" +
            now().strftime("%Y%m%d_%H%M%S") +
            ".db"
        )

        shutil.copy2(
            DB_FILE,
            filename
        )

        send(
            message.chat.id,
            "💾 Database backup created.",
            admin_keyboard(message.from_user.id)
        )


# ============================================================
# ADMIN VOTE STATS
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == txt("a_vote")
)
def admin_vote_stats(message):

    rows = q("""
    SELECT s.id,s.signal_date,s.signal_time,s.pair,
           s.direction,
           SUM(CASE WHEN v.vote='UP' THEN 1 ELSE 0 END) up_count,
           SUM(CASE WHEN v.vote='DOWN' THEN 1 ELSE 0 END) down_count,
           SUM(CASE WHEN v.vote='SKIP' THEN 1 ELSE 0 END) skip_count
    FROM signals s
    LEFT JOIN votes v ON v.signal_id=s.id
    GROUP BY s.id
    ORDER BY s.id DESC
    LIMIT 20
    """, fetch=True)

    if not rows:

        send(
            message.chat.id,
            "No vote data.",
            admin_keyboard(message.from_user.id)
        )
        return

    text = "🗳 <b>Vote Statistics</b>\n\n"

    for r in rows:
        text += (
            f"#{r['id']} {r['pair']} "
            f"{r['signal_time']}\n"
            f"⬆️ {r['up_count']} | "
            f"⬇️ {r['down_count']} | "
            f"⏭ {r['skip_count']}\n\n"
        )

    send(
        message.chat.id,
        text,
        admin_keyboard(message.from_user.id)
    )


# ============================================================
# ADMIN LIVE BROADCAST
# ============================================================

def broadcast_live(text):

    users = q("""
    SELECT user_id
    FROM users
    WHERE live_signal=1
      AND notifications=1
    """, fetch=True)

    sent = 0

    for u in users:

        if safe_send(
            u["user_id"],
            text
        ):
            sent += 1

    targets = q("""
    SELECT chat_id
    FROM notification_targets
    WHERE enabled=1
    """, fetch=True)

    for t in targets:
        safe_send(
            t["chat_id"],
            text
        )

    return sent


# ============================================================
# STATE HANDLER
# ============================================================

@bot.message_handler(
    func=lambda m: STATE.get(m.from_user.id) is not None
)
def state_handler(message):

    uid = message.from_user.id
    state = STATE.get(uid)

    if message.text in [
        txt("back"),
        txt("main_menu")
    ]:
        STATE.pop(uid, None)
        TEMP.pop(uid, None)
        DATA.pop(uid, None)

        if message.text == txt("main_menu"):
            main_menu(message)
        else:
            send(
                uid,
                "🔙 Back",
                main_keyboard(uid)
            )

        return

    # --------------------------------------------------------
    # UID
    # --------------------------------------------------------

    if state == "UID":

        value = message.text.strip()

        if not re.fullmatch(
            r"[A-Za-z0-9_-]{3,30}",
            value
        ):
            send(
                uid,
                txt("invalid"),
                back_keyboard()
            )
            return

        duplicate = one(
            "SELECT user_id FROM users WHERE uid=? OR pending_uid=?",
            (value, value)
        )

        if duplicate:

            send(
                uid,
                "❌ This Quotex UID is already used "
                "or pending approval.",
                main_keyboard(uid)
            )

            STATE.pop(uid, None)
            return

        q(
            "UPDATE users SET pending_uid=? WHERE user_id=?",
            (value, uid)
        )

        send(
            uid,
            "✅ UID submitted.\n\n"
            "Admin approval is required.",
            main_keyboard(uid)
        )

        safe_send(
            ADMIN_ID,
            f"🆔 <b>New UID Request</b>\n\n"
            f"User: <code>{uid}</code>\n"
            f"UID: <code>{escape(value)}</code>\n\n"
            f"Use:\n"
            f"APPROVEUID {uid}\n"
            f"REJECTUID {uid}"
        )

        STATE.pop(uid, None)
        return

    # --------------------------------------------------------
    # MONEY MANAGEMENT
    # --------------------------------------------------------

    if state.startswith("MM_"):

        field = state[3:]

        try:
            value = float(message.text.strip())

            if value < 0:
                raise ValueError

        except:

            send(
                uid,
                "❌ Enter a valid number.",
                back_keyboard()
            )
            return

        ensure_mm(uid)

        columns = {
            "balance": "trading_balance",
            "profit": "profit_target",
            "loss": "loss_limit",
            "base": "base_trade",
            "m1": "m1_trade",
            "max": "max_trades_day"
        }

        col = columns.get(field)

        if not col:
            return

        q(
            f"UPDATE mm SET {col}=? WHERE user_id=?",
            (value, uid)
        )

        STATE.pop(uid, None)

        send(
            uid,
            "✅ Money Management updated.\n\n" +
            mm_status(uid),
            main_keyboard(uid)
        )

        return

    # --------------------------------------------------------
    # WITHDRAW USER
    # --------------------------------------------------------

    if state == "WITHDRAW":

        try:
            amount = float(
                message.text.strip()
            )
        except:
            send(
                uid,
                "❌ Invalid amount.",
                back_keyboard()
            )
            return

        minimum = float(
            get_setting(
                "min_withdraw",
                "5"
            )
        )

        u = user(uid)

        if amount < minimum:
            send(
                uid,
                f"❌ Minimum withdrawal is ${minimum:.2f}",
                main_keyboard(uid)
            )
            STATE.pop(uid, None)
            return

        if amount > float(u["balance"]):
            send(
                uid,
                "❌ Insufficient balance.",
                main_keyboard(uid)
            )
            STATE.pop(uid, None)
            return

        # Reserve balance immediately
        q(
            "UPDATE users SET balance=balance-? WHERE user_id=?",
            (amount, uid)
        )

        q("""
        INSERT INTO withdrawals(
            user_id,amount,status,created_at
        )
        VALUES(?,?,?,?)
        """, (
            uid,
            amount,
            "pending",
            now_str()
        ))

        send(
            uid,
            f"✅ Withdrawal request submitted.\n\n"
            f"Amount: <b>${amount:.2f}</b>\n"
            f"Status: Pending",
            main_keyboard(uid)
        )

        safe_send(
            ADMIN_ID,
            f"💸 <b>New Withdrawal</b>\n\n"
            f"User: <code>{uid}</code>\n"
            f"Amount: <b>${amount:.2f}</b>\n\n"
            f"APPROVEWD ID\n"
            f"REJECTWD ID"
        )

        STATE.pop(uid, None)
        return

    # --------------------------------------------------------
    # IMPORT FUTURE SIGNALS
    # --------------------------------------------------------

    if state == "IMPORT_SIGNALS":

        parsed = parse_signals(
            message.text
        )

        if not parsed:

            send(
                uid,
                "❌ No valid signal lines found.\n\n"
                "Format:\n"
                "13:04 - USD/BDT-OTC - UP",
                back_keyboard()
            )
            return

        added = 0
        duplicate = 0

        for tm, pair, direction in parsed:

            old = one("""
            SELECT id FROM signals
            WHERE signal_date=?
              AND signal_time=?
              AND pair=?
              AND direction=?
            """, (
                today(),
                tm,
                pair,
                direction
            ))

            if old:
                duplicate += 1
                continue

            q("""
            INSERT INTO signals(
                signal_date,
                signal_time,
                pair,
                direction,
                confidence,
                created_at
            )
            VALUES(?,?,?,?,?,?)
            """, (
                today(),
                tm,
                pair,
                direction,
                get_setting(
                    "confidence_default",
                    "95–99%"
                ),
                now_str()
            ))

            added += 1

        STATE.pop(uid, None)

        send(
            uid,
            f"✅ <b>Signal Import Complete</b>\n\n"
            f"Added: <b>{added}</b>\n"
            f"Duplicate: <b>{duplicate}</b>\n"
            f"Date: <b>{today()}</b>\n\n"
            f"Auto Send: "
            f"<b>{'ON' if get_setting('auto_send','1')=='1' else 'OFF'}</b>",
            future_admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # LIVE SIGNAL
    # --------------------------------------------------------

    if state == "LIVE_SIGNAL":

        parsed = parse_signals(
            message.text
        )

        if not parsed:

            send(
                uid,
                "❌ Invalid signal format.\n\n"
                "Example:\n"
                "13:59 - USD/BDT-OTC - DOWN",
                back_keyboard()
            )
            return

        tm, pair, direction = parsed[0]

        text = txt("live_template").format(
            pair=escape(pair),
            time=tm,
            direction=direction,
            confidence=get_setting(
                "confidence_default",
                "95–99%"
            )
        )

        broadcast_live(text)

        q("""
        INSERT INTO live_history(
            admin_id,pair,signal_time,direction,
            message,created_at
        )
        VALUES(?,?,?,?,?,?)
        """, (
            uid,
            pair,
            tm,
            direction,
            text,
            now_str()
        ))

        LIVE["count"] += 1

        STATE.pop(uid, None)

        send(
            uid,
            "✅ Live signal sent.",
            live_admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # LIVE TEXT
    # --------------------------------------------------------

    if state == "LIVE_TEXT":

        broadcast_live(
            message.text
        )

        q("""
        INSERT INTO live_history(
            admin_id,message,created_at
        )
        VALUES(?,?,?)
        """, (
            uid,
            message.text,
            now_str()
        ))

        LIVE["count"] += 1

        STATE.pop(uid, None)

        send(
            uid,
            "✅ Live text sent.",
            live_admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # ADMIN NOTICE
    # --------------------------------------------------------

    if state == "NOTICE_ADMIN":

        set_setting(
            "notice",
            message.text
        )

        STATE.pop(uid, None)

        send(
            uid,
            "✅ Notice updated.",
            admin_keyboard(uid)
        )

        return

    # --------------------------------------------------------
    # ADMIN TEXT EDIT
    # --------------------------------------------------------

    if state == "TEXT_EDIT":

        key = DATA.get(uid, {}).get(
            "edit_key"
        )

        if key:

            set_setting(
                key,
                message.text
            )

        STATE.pop(uid, None)
        DATA.pop(uid, None)

        send(
            uid,
            "✅ Bot text updated and saved permanently.",
            admin_keyboard(uid)
        )

        return

    # --------------------------------------------------------
    # ADMIN SETTINGS
    # --------------------------------------------------------

    if state == "SETTINGS_ADMIN":

        parts = message.text.strip().split(
            maxsplit=1
        )

        if len(parts) != 2:

            send(
                uid,
                "❌ Format: KEY VALUE",
                back_keyboard()
            )
            return

        key = parts[0].upper()
        value = parts[1]

        mapping = {
            "FREE": "free_limit",
            "MINWD": "min_withdraw",
            "REF": "referral_bonus",
            "AUTO": "auto_send",
            "MINUTES": "auto_send_minutes",
            "AUDIENCE": "future_audience",
            "MAINTENANCE": "maintenance",
            "VOTE_PUBLIC": "vote_public",
            "HOLD": "withdraw_hold"
        }

        real_key = mapping.get(key)

        if not real_key:

            send(
                uid,
                "❌ Unknown setting.",
                back_keyboard()
            )
            return

        if real_key == "future_audience":
            value = value.upper()

            if value not in [
                "ALL",
                "VIP",
                "SELECTED"
            ]:
                send(
                    uid,
                    "Use ALL, VIP or SELECTED.",
                    back_keyboard()
                )
                return

        set_setting(
            real_key,
            value
        )

        STATE.pop(uid, None)

        send(
            uid,
            "✅ Setting updated.",
            admin_keyboard(uid)
        )

        return

    # --------------------------------------------------------
    # VIP ADMIN
    # --------------------------------------------------------

    if state == "VIP_ADMIN":

        p = message.text.strip().split()

        if not p:

            return

        command = p[0].upper()

        if command == "ADD" and len(p) >= 3:

            try:
                target = int(p[1])
                days = int(p[2])

                if not user(target):
                    send(
                        uid,
                        "❌ User not found.",
                        back_keyboard()
                    )
                    return

                expiry = set_vip(
                    target,
                    days
                )

                send(
                    target,
                    f"💎 <b>VIP Activated</b>\n\n"
                    f"Expires: <b>{expiry.strftime('%Y-%m-%d %H:%M')}</b>",
                    main_keyboard(target)
                )

                send(
                    uid,
                    "✅ VIP added.",
                    admin_keyboard(uid)
                )

                STATE.pop(uid, None)

            except:

                send(
                    uid,
                    "❌ Example: ADD 123456789 30",
                    back_keyboard()
                )

            return

        if command == "REMOVE" and len(p) >= 2:

            try:
                target = int(p[1])

                remove_vip(target)

                send(
                    target,
                    "ℹ️ Your VIP status has been removed.",
                    main_keyboard(target)
                )

                send(
                    uid,
                    "✅ VIP removed.",
                    admin_keyboard(uid)
                )

                STATE.pop(uid, None)

            except:
                send(
                    uid,
                    "❌ Invalid user ID.",
                    back_keyboard()
                )

            return

        send(
            uid,
            "❌ Use:\nADD USER_ID DAYS\nor\nREMOVE USER_ID",
            back_keyboard()
        )

        return

    # --------------------------------------------------------
    # BROADCAST
    # --------------------------------------------------------

    if state == "BROADCAST":

        parts = message.text.split(
            maxsplit=1
        )

        if len(parts) < 2:

            send(
                uid,
                "Use:\nALL message\nVIP message\nSELECTED message",
                back_keyboard()
            )
            return

        target = parts[0].upper()
        body = parts[1]

        if target == "ALL":

            users = q(
                "SELECT user_id FROM users",
                fetch=True
            )

        elif target == "VIP":

            users = q(
                "SELECT user_id FROM users WHERE is_vip=1",
                fetch=True
            )

        elif target == "SELECTED":

            ids = get_setting(
                "selected_users",
                ""
            )

            users = []

            for x in ids.split(","):

                try:
                    r = one(
                        "SELECT user_id FROM users WHERE user_id=?",
                        (int(x),)
                    )
                    if r:
                        users.append(r)
                except:
                    pass

        else:

            send(
                uid,
                "❌ Target must be ALL, VIP or SELECTED.",
                back_keyboard()
            )
            return

        count = 0

        for u in users:

            if safe_send(
                u["user_id"],
                body
            ):
                count += 1

        send(
            uid,
            f"✅ Broadcast sent to {count} users.",
            admin_keyboard(uid)
        )

        STATE.pop(uid, None)

        return

    # --------------------------------------------------------
    # WITHDRAW ADMIN
    # --------------------------------------------------------

    if state == "WITHDRAW_ADMIN":

        p = message.text.strip().split()

        if len(p) != 2:

            send(
                uid,
                "Use APPROVE ID or REJECT ID",
                back_keyboard()
            )
            return

        action = p[0].upper()

        try:
            wid = int(p[1])
        except:
            send(
                uid,
                "❌ Invalid withdrawal ID.",
                back_keyboard()
            )
            return

        w = one(
            "SELECT * FROM withdrawals WHERE id=?",
            (wid,)
        )

        if not w or w["status"] != "pending":

            send(
                uid,
                "❌ Withdrawal not found/pending.",
                admin_keyboard(uid)
            )
            return

        if action == "APPROVE":

            q("""
            UPDATE withdrawals
            SET status='approved',
                processed_at=?
            WHERE id=?
            """, (
                now_str(),
                wid
            ))

            safe_send(
                w["user_id"],
                f"✅ Withdrawal approved.\n\n"
                f"Amount: ${w['amount']:.2f}"
            )

            send(
                uid,
                "✅ Withdrawal approved.",
                admin_keyboard(uid)
            )

        elif action == "REJECT":

            q("""
            UPDATE withdrawals
            SET status='rejected',
                processed_at=?
            WHERE id=?
            """, (
                now_str(),
                wid
            ))

            # Refund
            add_balance(
                w["user_id"],
                float(w["amount"]),
                "WITHDRAW_REFUND",
                f"Rejected withdrawal #{wid}"
            )

            safe_send(
                w["user_id"],
                f"❌ Withdrawal rejected.\n\n"
                f"Amount ${w['amount']:.2f} "
                f"has been returned to your wallet."
            )

            send(
                uid,
                "✅ Withdrawal rejected and refunded.",
                admin_keyboard(uid)
            )

        else:

            send(
                uid,
                "Use APPROVE ID or REJECT ID.",
                back_keyboard()
            )
            return

        STATE.pop(uid, None)
        return

    # --------------------------------------------------------
    # SUB ADMIN
    # --------------------------------------------------------

    if state == "SUBADMIN":

        p = message.text.strip().split(
            maxsplit=2
        )

        if not p:
            return

        command = p[0].upper()

        if command == "ADDADMIN" and len(p) >= 3:

            try:
                target = int(p[1])
                permissions = p[2]

                q("""
                INSERT INTO admins(
                    user_id,role,permissions
                )
                VALUES(?,?,?)
                ON CONFLICT(user_id)
                DO UPDATE SET permissions=excluded.permissions
                """, (
                    target,
                    "sub_admin",
                    permissions
                ))

                send(
                    uid,
                    "✅ Sub-admin added/updated.",
                    admin_keyboard(uid)
                )

                STATE.pop(uid, None)

            except:
                send(
                    uid,
                    "❌ Invalid format.",
                    back_keyboard()
                )

            return

        if command == "DELADMIN" and len(p) >= 2:

            try:
                target = int(p[1])

                if target == ADMIN_ID:

                    send(
                        uid,
                        "❌ Owner cannot be removed.",
                        back_keyboard()
                    )
                    return

                q(
                    "DELETE FROM admins WHERE user_id=?",
                    (target,)
                )

                send(
                    uid,
                    "✅ Admin removed.",
                    admin_keyboard(uid)
                )

                STATE.pop(uid, None)

            except:
                pass

            return

        send(
            uid,
            "❌ Use:\n"
            "ADDADMIN ID signals,vip,withdraw,live\n"
            "DELADMIN ID",
            back_keyboard()
        )

        return

    # --------------------------------------------------------
    # TARGETS
    # --------------------------------------------------------

    if state == "TARGETS":

        p = message.text.strip().split(
            maxsplit=2
        )

        if not p:
            return

        command = p[0].upper()

        if command == "ADD" and len(p) >= 2:

            try:
                chat_id = int(p[1])
                title = p[2] if len(p) >= 3 else ""

                q("""
                INSERT OR REPLACE INTO notification_targets(
                    chat_id,title,chat_type,enabled,created_at
                )
                VALUES(?,?,?,?,?)
                """, (
                    chat_id,
                    title,
                    "unknown",
                    1,
                    now_str()
                ))

                send(
                    uid,
                    "✅ Notification target added.",
                    admin_keyboard(uid)
                )

                STATE.pop(uid, None)

            except:
                send(
                    uid,
                    "❌ Invalid chat ID.",
                    back_keyboard()
                )

            return

        if command == "DEL" and len(p) >= 2:

            try:
                chat_id = int(p[1])

                q(
                    "DELETE FROM notification_targets WHERE chat_id=?",
                    (chat_id,)
                )

                send(
                    uid,
                    "✅ Notification target removed.",
                    admin_keyboard(uid)
                )

                STATE.pop(uid, None)

            except:
                pass

            return

        send(
            uid,
            "Use ADD CHAT_ID NAME\nor DEL CHAT_ID",
            back_keyboard()
        )

        return

    # --------------------------------------------------------
    # UNKNOWN STATE
    # --------------------------------------------------------

    STATE.pop(uid, None)

    send(
        uid,
        txt("invalid"),
        main_keyboard(uid)
    )


# ============================================================
# ERROR SAFE POLLING
# ============================================================

def run_bot():

    while True:

        try:

            logging.info(
                "Bot polling started..."
            )

            bot.infinity_polling(
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30
            )

        except Exception as e:

            logging.exception(
                "Polling crashed: %s",
                e
            )

            time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    init_db()

    os.makedirs(
        BACKUP_DIR,
        exist_ok=True
    )

    # Scheduler thread
    threading.Thread(
        target=scheduler,
        daemon=True
    ).start()

    logging.info(
        "SM QUATEX SURE SHORT started"
    )

    run_bot()
