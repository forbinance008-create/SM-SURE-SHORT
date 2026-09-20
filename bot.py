# ============================================================
# SM QUATEX SURE SHORT
# Complete Telegram Bot - pyTelegramBotAPI + SQLite
# ============================================================

import os
import re
import time
import math
import shutil
import sqlite3
import logging
import threading
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import telebot
from telebot import types


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "6470135702"))

DB_PATH = os.getenv("DB_PATH", "sm_quatex.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")

BD_TZ = ZoneInfo("Asia/Dhaka")

QUOTEX_REF_LINK = "https://broker-qx.pro/sign-up/?lid=2350796"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Add BOT_TOKEN in Railway Variables.")


bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

DB_LOCK = threading.RLock()
STATE = {}
LIVE_DRAFT = {}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("SM_QUATEX")


# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def db_execute(sql, params=(), fetchone=False, fetchall=False, commit=True):
    with DB_LOCK:
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)

            result = None

            if fetchone:
                result = cur.fetchone()
            elif fetchall:
                result = cur.fetchall()
            else:
                result = cur.lastrowid

            if commit:
                conn.commit()

            return result
        finally:
            conn.close()


def db_script(script):
    with DB_LOCK:
        conn = get_db()
        try:
            conn.executescript(script)
            conn.commit()
        finally:
            conn.close()


def init_db():

    db_script("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        balance_cents INTEGER DEFAULT 0,
        vip_until TEXT,
        uid TEXT UNIQUE,
        notification_on INTEGER DEFAULT 1,
        live_signal_on INTEGER DEFAULT 0,
        referral_code TEXT UNIQUE,
        referred_by INTEGER,
        referral_paid INTEGER DEFAULT 0,
        joined_at TEXT,
        last_seen TEXT
    );

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS admins (
        telegram_id INTEGER PRIMARY KEY,
        role TEXT DEFAULT 'subadmin',
        p_users INTEGER DEFAULT 0,
        p_vip INTEGER DEFAULT 0,
        p_signals INTEGER DEFAULT 0,
        p_wallet INTEGER DEFAULT 0,
        p_withdraw INTEGER DEFAULT 0,
        p_broadcast INTEGER DEFAULT 0,
        p_settings INTEGER DEFAULT 0,
        p_live INTEGER DEFAULT 0,
        p_text INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_date TEXT NOT NULL,
        signal_time TEXT NOT NULL,
        pair TEXT NOT NULL,
        direction TEXT NOT NULL,
        confidence TEXT DEFAULT '',
        audience TEXT DEFAULT 'ALL',
        status TEXT DEFAULT 'PENDING',
        created_by INTEGER,
        created_at TEXT,
        sent_at TEXT
    );

    CREATE TABLE IF NOT EXISTS selected_signal_users (
        signal_id INTEGER,
        telegram_id INTEGER,
        PRIMARY KEY(signal_id, telegram_id)
    );

    CREATE TABLE IF NOT EXISTS signal_access (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER,
        telegram_id INTEGER,
        delivered_at TEXT,
        consumed_quota INTEGER DEFAULT 0,
        UNIQUE(signal_id, telegram_id)
    );

    CREATE TABLE IF NOT EXISTS signal_votes (
        signal_id INTEGER,
        telegram_id INTEGER,
        vote TEXT,
        created_at TEXT,
        PRIMARY KEY(signal_id, telegram_id)
    );

    CREATE TABLE IF NOT EXISTS signal_results (
        signal_id INTEGER,
        telegram_id INTEGER,
        result TEXT,
        amount_cents INTEGER DEFAULT 0,
        created_at TEXT,
        PRIMARY KEY(signal_id, telegram_id)
    );

    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        amount_cents INTEGER,
        method TEXT,
        account TEXT,
        status TEXT DEFAULT 'PENDING',
        created_at TEXT,
        processed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS wallet_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        type TEXT,
        amount_cents INTEGER,
        description TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS uid_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        uid TEXT UNIQUE,
        status TEXT DEFAULT 'PENDING',
        created_at TEXT,
        processed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS live_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT,
        ended_at TEXT,
        status TEXT DEFAULT 'ACTIVE'
    );

    CREATE TABLE IF NOT EXISTS live_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        telegram_id INTEGER,
        content TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS mm_profiles (
        telegram_id INTEGER PRIMARY KEY,
        balance_cents INTEGER DEFAULT 0,
        profit_target_cents INTEGER DEFAULT 0,
        loss_limit_cents INTEGER DEFAULT 0,
        base_trade_cents INTEGER DEFAULT 200,
        m1_trade_cents INTEGER DEFAULT 300,
        m2_trade_cents INTEGER DEFAULT 0,
        payout_percent REAL DEFAULT 80,
        max_trades INTEGER DEFAULT 20,
        max_m1_cents INTEGER DEFAULT 0,
        stop_trading INTEGER DEFAULT 0,
        balance_confirmed INTEGER DEFAULT 0,
        daily_start_balance_cents INTEGER DEFAULT 0,
        daily_pl_cents INTEGER DEFAULT 0,
        trades_today INTEGER DEFAULT 0,
        wins_today INTEGER DEFAULT 0,
        losses_today INTEGER DEFAULT 0,
        stage TEXT DEFAULT 'BASE',
        session_loss_cents INTEGER DEFAULT 0,
        recovery_loss_cents INTEGER DEFAULT 0,
        session_no INTEGER DEFAULT 1,
        current_trade_cents INTEGER DEFAULT 200,
        last_reset_date TEXT
    );

    CREATE TABLE IF NOT EXISTS referral_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        amount_cents INTEGER,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS vip_reminders (
        telegram_id INTEGER,
        expiry_date TEXT,
        reminder_date TEXT,
        PRIMARY KEY(telegram_id, expiry_date, reminder_date)
    );
    """)

    defaults = {
        "maintenance": "0",
        "withdrawals": "1",
        "withdraw_hold_hours": "0",
        "min_withdraw": "5",
        "free_signal_limit": "4",
        "referral_bonus": "1",
        "vip_reminder_days": "3",
        "vote_reveal": "0",
        "channel_username": "",
        "trading_rules": "Use the signals according to your own risk management.",
        "notice": "Welcome to SM QUATEX SURE SHORT.",
        "welcome": (
            "👋 <b>Welcome to SM QUATEX SURE SHORT</b>\n\n"
            "Use the menu below."
        ),
        "maintenance_text": "🛠 Bot is currently under maintenance.",
        "error_text": "⚠️ Something went wrong. Please try again.",
        "invalid_text": "❌ Invalid input. Please use the buttons.",
        "vip_text": "⭐ VIP access is active for you.",
        "nonvip_text": "You are currently a FREE user.",
        "signal_template": (
            "📅 <b>{date}</b>\n\n"
            "💱 <b>{pair}</b>\n"
            "⏰ <b>{time}</b>\n"
            "{direction}\n"
            "🎯 Confidence: <b>{confidence}</b>\n"
            "💵 Trade: <b>${trade_amount}</b>\n"
            "📌 Stage: <b>{stage}</b>"
        ),
        "live_template": (
            "⚡ <b>LIVE SIGNAL</b>\n\n"
            "{content}"
        ),
        "win_text": "✅ WIN recorded.\nProfit: <b>${profit}</b>",
        "loss_text": "❌ LOSS recorded.\nLoss: <b>${loss}</b>",
        "skip_text": "⏭ SKIPPED.",
        "notification_on": "🔔 Notifications are ON.",
        "notification_off": "🔕 Notifications are OFF.",
        "withdraw_disabled": "💸 Withdrawals are currently OFF.",
        "withdraw_success": "✅ Withdrawal request submitted.",
        "withdraw_min": "Minimum withdrawal is ${amount}.",
        "uid_duplicate": "❌ This UID is already linked to another account.",
        "uid_pending": "⏳ Your UID is already waiting for admin approval.",
        "uid_approved": "✅ Your UID has been approved.",
        "uid_rejected": "❌ Your UID submission was rejected.",
        "quota_text": "📊 Free signals remaining: <b>{remaining}</b>",
        "balance_required": (
            "💰 Before receiving today's Future Signals, "
            "please confirm your trading balance."
        ),
        "main_menu_title": "🏠 Main Menu",
        "admin_menu_title": "👑 Admin Panel",
        "back_text": "🔙 Back",
        "home_text": "🏠 Main Menu",
        "rules_text": "📖 <b>Trading Rules</b>\n\n{rules}",
        "referral_text": (
            "👥 <b>Referral</b>\n\n"
            "Your referral link:\n{link}\n\n"
            "Bonus: ${bonus}"
        ),
        "vip_expired": "⚠️ Your VIP access has expired.",
        "live_off": "⚡ Live Signal is currently OFF for your account.",
        "live_started": "⚡ Live Session started.",
        "live_ended": "⛔ Live Session ended.",
    }

    for key, value in defaults.items():
        db_execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
            (key, value)
        )


init_db()


# ============================================================
# SETTINGS
# ============================================================

def get_setting(key, default=""):
    row = db_execute(
        "SELECT value FROM settings WHERE key=?",
        (key,),
        fetchone=True
    )
    return row["value"] if row else default


def set_setting(key, value):
    db_execute(
        """
        INSERT INTO settings(key,value)
        VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, str(value))
    )


# ============================================================
# UTILS
# ============================================================

def now_bd():
    return datetime.now(BD_TZ)


def now_str():
    return now_bd().strftime("%Y-%m-%d %H:%M:%S")


def money(cents):
    return f"{cents / 100:.2f}"


def cents(value):
    value = str(value).replace("$", "").replace(",", "").strip()
    return int(round(float(value) * 100))


def esc(text):
    if text is None:
        return ""
    return str(text)


def is_admin(user_id):
    return user_id == ADMIN_ID


def admin_row(user_id):
    return db_execute(
        "SELECT * FROM admins WHERE telegram_id=?",
        (user_id,),
        fetchone=True
    )


def has_permission(user_id, permission):
    if user_id == ADMIN_ID:
        return True

    row = admin_row(user_id)
    if not row:
        return False

    return bool(row[permission])


def is_vip(user_id):
    row = db_execute(
        "SELECT vip_until FROM users WHERE telegram_id=?",
        (user_id,),
        fetchone=True
    )

    if not row or not row["vip_until"]:
        return False

    try:
        return datetime.fromisoformat(row["vip_until"]) > now_bd()
    except Exception:
        return False


def user_row(user_id):
    return db_execute(
        "SELECT * FROM users WHERE telegram_id=?",
        (user_id,),
        fetchone=True
    )


def register_user(message):
    u = message.from_user
    existing = user_row(u.id)

    if existing:
        db_execute(
            """
            UPDATE users
            SET username=?, first_name=?, last_seen=?
            WHERE telegram_id=?
            """,
            (
                u.username or "",
                u.first_name or "",
                now_str(),
                u.id
            )
        )
        ensure_mm(u.id)
        return existing

    referrer = None
    if message.text and message.text.startswith("/start "):
        code = message.text.split(" ", 1)[1].strip()
        ref = db_execute(
            "SELECT telegram_id FROM users WHERE referral_code=?",
            (code,),
            fetchone=True
        )
        if ref and ref["telegram_id"] != u.id:
            referrer = ref["telegram_id"]

    referral_code = f"ref{u.id}"

    db_execute(
        """
        INSERT INTO users(
            telegram_id,username,first_name,referral_code,
            referred_by,joined_at,last_seen
        )
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            u.id,
            u.username or "",
            u.first_name or "",
            referral_code,
            referrer,
            now_str(),
            now_str()
        )
    )

    ensure_mm(u.id)

    return user_row(u.id)


# ============================================================
# MONEY MANAGEMENT
# ============================================================

def ensure_mm(user_id):
    row = db_execute(
        "SELECT * FROM mm_profiles WHERE telegram_id=?",
        (user_id,),
        fetchone=True
    )

    if row:
        return row

    db_execute(
        """
        INSERT INTO mm_profiles(
            telegram_id,last_reset_date
        )
        VALUES(?,?)
        """,
        (user_id, now_bd().date().isoformat())
    )

    return db_execute(
        "SELECT * FROM mm_profiles WHERE telegram_id=?",
        (user_id,),
        fetchone=True
    )


def reset_daily_mm_if_needed(user_id):
    row = ensure_mm(user_id)
    today = now_bd().date().isoformat()

    if row["last_reset_date"] != today:
        db_execute(
            """
            UPDATE mm_profiles
            SET balance_confirmed=0,
                daily_start_balance_cents=balance_cents,
                daily_pl_cents=0,
                trades_today=0,
                wins_today=0,
                losses_today=0,
                stop_trading=0,
                last_reset_date=?
            WHERE telegram_id=?
            """,
            (today, user_id)
        )

    return ensure_mm(user_id)


def current_trade_amount(user_id):
    row = reset_daily_mm_if_needed(user_id)

    stage = row["stage"]
    recovery = row["recovery_loss_cents"]

    if stage == "BASE" and recovery <= 0:
        return row["base_trade_cents"]

    if stage == "M1":
        if row["m1_trade_cents"] > 0:
            return row["m1_trade_cents"]

    if stage == "M2":
        if row["m2_trade_cents"] > 0:
            return row["m2_trade_cents"]

        payout = max(float(row["payout_percent"]), 1.0) / 100
        desired_profit = row["base_trade_cents"]

        calculated = math.ceil(
            (recovery + desired_profit) / payout
        )

        if row["max_m1_cents"] > 0:
            calculated = min(calculated, row["max_m1_cents"])

        return calculated

    if recovery > 0:
        payout = max(float(row["payout_percent"]), 1.0) / 100
        calculated = math.ceil(
            (recovery + row["base_trade_cents"]) / payout
        )
        return calculated

    return row["base_trade_cents"]


def mm_can_trade(user_id):
    row = reset_daily_mm_if_needed(user_id)

    if row["stop_trading"]:
        return False, "⛔ Trading is stopped."

    if row["max_trades"] > 0 and row["trades_today"] >= row["max_trades"]:
        return False, "⛔ Today's maximum trades have been reached."

    if row["profit_target_cents"] > 0 and row["daily_pl_cents"] >= row["profit_target_cents"]:
        return False, "🎯 Profit Target reached."

    if row["loss_limit_cents"] > 0 and row["daily_pl_cents"] <= -row["loss_limit_cents"]:
        return False, "🛑 Loss Limit reached."

    return True, ""


def apply_trade_result(user_id, signal_id, result):
    row = reset_daily_mm_if_needed(user_id)

    existing = db_execute(
        """
        SELECT * FROM signal_results
        WHERE signal_id=? AND telegram_id=?
        """,
        (signal_id, user_id),
        fetchone=True
    )

    if existing:
        return False, "⚠️ This signal result was already recorded."

    amount = current_trade_amount(user_id)

    if result == "WIN":
        profit = math.floor(
            amount * max(float(row["payout_percent"]), 0) / 100
        )

        db_execute(
            """
            UPDATE mm_profiles
            SET balance_cents=balance_cents+?,
                daily_pl_cents=daily_pl_cents+?,
                trades_today=trades_today+1,
                wins_today=wins_today+1,
                stage='BASE',
                session_loss_cents=0,
                recovery_loss_cents=0,
                current_trade_cents=?
            WHERE telegram_id=?
            """,
            (
                profit,
                profit,
                row["base_trade_cents"],
                user_id
            )
        )

        db_execute(
            """
            INSERT INTO signal_results(
                signal_id,telegram_id,result,amount_cents,created_at
            )
            VALUES(?,?,?,?,?)
            """,
            (signal_id, user_id, result, profit, now_str())
        )

        return True, get_setting("win_text").replace(
            "{profit}", money(profit)
        )

    if result == "LOSS":

        new_recovery = row["recovery_loss_cents"] + amount

        if row["stage"] == "BASE":
            new_stage = "M1"
        elif row["stage"] == "M1":
            new_stage = "M2"
        else:
            new_stage = "BASE"

        if row["stage"] == "M2":
            new_stage = "BASE"

        db_execute(
            """
            UPDATE mm_profiles
            SET balance_cents=balance_cents-?,
                daily_pl_cents=daily_pl_cents-?,
                trades_today=trades_today+1,
                losses_today=losses_today+1,
                stage=?,
                session_loss_cents=session_loss_cents+?,
                recovery_loss_cents=?,
                current_trade_cents=?
            WHERE telegram_id=?
            """,
            (
                amount,
                amount,
                new_stage,
                amount,
                new_recovery,
                amount,
                user_id
            )
        )

        new_amount = current_trade_amount(user_id)

        db_execute(
            """
            INSERT INTO signal_results(
                signal_id,telegram_id,result,amount_cents,created_at
            )
            VALUES(?,?,?,?,?)
            """,
            (signal_id, user_id, result, amount, now_str())
        )

        text = get_setting("loss_text").replace(
            "{loss}", money(amount)
        )

        text += (
            f"\n\n🔄 Next stage: <b>{new_stage}</b>"
            f"\n💵 Next recovery amount: <b>${money(new_amount)}</b>"
        )

        return True, text

    db_execute(
        """
        INSERT INTO signal_results(
            signal_id,telegram_id,result,amount_cents,created_at
        )
        VALUES(?,?,?,?,?)
        """,
        (signal_id, user_id, result, 0, now_str())
    )

    return True, get_setting("skip_text")


# ============================================================
# QUOTA
# ============================================================

def cycle_id():
    epoch = datetime(1970, 1, 1, tzinfo=BD_TZ)
    days = (now_bd() - epoch).days
    return days // 2


def quota_used(user_id):
    start = now_bd().date() - timedelta(
        days=(now_bd().date().toordinal() % 2)
    )

    rows = db_execute(
        """
        SELECT COUNT(*) AS c
        FROM signal_access
        WHERE telegram_id=?
          AND consumed_quota=1
          AND delivered_at >= ?
        """,
        (
            user_id,
            datetime.combine(
                start,
                datetime.min.time()
            ).replace(tzinfo=BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
        ),
        fetchone=True
    )

    return rows["c"] if rows else 0


def free_remaining(user_id):
    if is_vip(user_id):
        return 999999

    limit = int(get_setting("free_signal_limit", "4"))

    used = quota_used(user_id)

    return max(0, limit - used)


# ============================================================
# KEYBOARDS
# ============================================================

def kb(rows, resize=True):
    markup = types.ReplyKeyboardMarkup(
        resize_keyboard=resize
    )

    for row in rows:
        markup.row(*row)

    return markup


def user_keyboard(user_id):
    return kb([
        ["📡 Future Signals", "⚡ Live Signals"],
        ["💰 Money Management", "💼 Wallet"],
        ["💸 Withdraw", "👤 VIP / UID"],
        ["👥 Referral", "📊 Dashboard"],
        ["🗳 Vote Signal", "📈 Signal Result"],
        ["📜 Signal History", "📖 Trading Rules"],
        ["🔔 Notifications", "❓ Help"],
    ])


def admin_keyboard():
    return kb([
        ["📊 Admin Dashboard", "👥 Users"],
        ["📡 Future Signals", "⚡ Live Session"],
        ["⭐ VIP Management", "🆔 UID Requests"],
        ["💰 Wallet / Withdraw", "📢 Broadcast"],
        ["📝 Bot Text Editor", "⚙️ Settings"],
        ["👨‍💼 Sub-Admins", "📊 Vote Results"],
        ["📋 Signal History", "🛠 Maintenance"],
        ["🏠 Main Menu"],
    ])


def back_keyboard():
    return kb([
        ["🔙 Back", "🏠 Main Menu"]
    ])


def money_keyboard():
    return kb([
        ["💵 Set Balance", "🎯 Profit Target"],
        ["🛑 Loss Limit", "💵 Base Trade"],
        ["🔄 M1 Trade", "🔄 M2 Trade"],
        ["📈 Payout %", "🔢 Max Trades"],
        ["🚫 Stop Trading", "▶️ Resume Trading"],
        ["📊 MM Status", "🔙 Back"],
        ["🏠 Main Menu"],
    ])


def signal_keyboard():
    return kb([
        ["➕ Add Future Signals"],
        ["📋 Future Signal List"],
        ["🗑 Delete Future Signal"],
        ["🎯 Signal Audience"],
        ["🔙 Back", "🏠 Main Menu"],
    ])


def vip_keyboard():
    return kb([
        ["➕ Add VIP", "🔄 Renew VIP"],
        ["❌ Remove VIP", "📋 VIP List"],
        ["🔙 Back", "🏠 Main Menu"],
    ])


def wallet_admin_keyboard():
    return kb([
        ["💸 Pending Withdrawals"],
        ["💰 Add Balance"],
        ["➖ Remove Balance"],
        ["⚙️ Withdrawal Settings"],
        ["🔙 Back", "🏠 Main Menu"],
    ])


# ============================================================
# MESSAGE / TEMPLATE
# ============================================================

def direction_text(direction):
    d = direction.upper()

    if d in ("UP", "BUY", "CALL"):
        return "🟢⬆️ <b>UP / BUY</b>"

    if d in ("DOWN", "SELL", "PUT"):
        return "🔴⬇️ <b>DOWN / SELL</b>"

    return f"📌 <b>{d}</b>"


def signal_message(signal, user_id):
    row = reset_daily_mm_if_needed(user_id)
    amount = current_trade_amount(user_id)

    date = signal["signal_date"]
    tm = signal["signal_time"]

    template = get_setting("signal_template")

    values = {
        "date": date,
        "time": tm,
        "pair": signal["pair"],
        "direction": direction_text(signal["direction"]),
        "confidence": signal["confidence"] or "-",
        "signal_id": signal["id"],
        "trade_amount": money(amount),
        "stage": row["stage"],
        "remaining_signals": free_remaining(user_id)
    }

    try:
        return template.format(**values)
    except Exception:
        return (
            f"📅 <b>{date}</b>\n\n"
            f"💱 <b>{signal['pair']}</b>\n"
            f"⏰ <b>{tm}</b>\n"
            f"{direction_text(signal['direction'])}\n"
            f"🎯 Confidence: <b>{signal['confidence']}</b>"
        )


# ============================================================
# START / MAIN
# ============================================================

def send_main_menu(chat_id):
    if is_admin(chat_id) or admin_row(chat_id):
        bot.send_message(
            chat_id,
            get_setting("admin_menu_title"),
            reply_markup=admin_keyboard()
        )
    else:
        bot.send_message(
            chat_id,
            get_setting("main_menu_title"),
            reply_markup=user_keyboard(chat_id)
        )


@bot.message_handler(commands=["start"])
def start_handler(message):
    register_user(message)

    if get_setting("maintenance") == "1" and not is_admin(message.from_user.id):
        bot.send_message(
            message.chat.id,
            get_setting("maintenance_text"),
            reply_markup=user_keyboard(message.from_user.id)
        )
        return

    bot.send_message(
        message.chat.id,
        get_setting("welcome"),
        reply_markup=(
            admin_keyboard()
            if is_admin(message.from_user.id) or admin_row(message.from_user.id)
            else user_keyboard(message.from_user.id)
        )
    )


# ============================================================
# COMMON FUNCTIONS
# ============================================================

def set_state(user_id, action, data=None):
    STATE[user_id] = {
        "action": action,
        "data": data or {}
    }


def get_state(user_id):
    return STATE.get(user_id)


def clear_state(user_id):
    STATE.pop(user_id, None)


def send_back(chat_id):
    if is_admin(chat_id) or admin_row(chat_id):
        bot.send_message(
            chat_id,
            "↩️ Back",
            reply_markup=admin_keyboard()
        )
    else:
        bot.send_message(
            chat_id,
            "↩️ Back",
            reply_markup=user_keyboard(chat_id)
        )


# ============================================================
# USER FUTURE SIGNALS
# ============================================================

def handle_future_signals_user(message):

    user_id = message.from_user.id

    if not is_vip(user_id):

        reset_daily_mm_if_needed(user_id)

        mm = ensure_mm(user_id)

        if not mm["balance_confirmed"]:
            set_state(user_id, "SET_BALANCE_FOR_SIGNAL")

            bot.send_message(
                user_id,
                get_setting("balance_required"),
                reply_markup=back_keyboard()
            )
            return

    remaining = free_remaining(user_id)

    bot.send_message(
        user_id,
        get_setting("quota_text").replace(
            "{remaining}",
            "Unlimited" if is_vip(user_id) else str(remaining)
        ),
        reply_markup=kb([
            ["📅 Today's Signals"],
            ["📜 Signal History"],
            ["🔙 Back", "🏠 Main Menu"]
        ])
    )


def send_todays_signals(user_id):

    today = now_bd().date().isoformat()

    rows = db_execute(
        """
        SELECT * FROM signals
        WHERE signal_date=?
        ORDER BY signal_time
        """,
        (today,),
        fetchall=True
    )

    if not rows:
        bot.send_message(
            user_id,
            "📭 No signals available for today.",
            reply_markup=user_keyboard(user_id)
        )
        return

    sent = 0

    for signal in rows:
        if signal["status"] != "SENT":
            continue

        if signal["audience"] == "VIP" and not is_vip(user_id):
            continue

        if signal["audience"] == "SELECTED":
            selected = db_execute(
                """
                SELECT 1 FROM selected_signal_users
                WHERE signal_id=? AND telegram_id=?
                """,
                (signal["id"], user_id),
                fetchone=True
            )
            if not selected:
                continue

        existing = db_execute(
            """
            SELECT * FROM signal_access
            WHERE signal_id=? AND telegram_id=?
            """,
            (signal["id"], user_id),
            fetchone=True
        )

        if existing:
            continue

        consume = 0

        if signal["audience"] == "ALL" and not is_vip(user_id):
            if free_remaining(user_id) <= 0:
                break
            consume = 1

        try:
            bot.send_message(
                user_id,
                signal_message(signal, user_id)
            )

            db_execute(
                """
                INSERT INTO signal_access(
                    signal_id,telegram_id,delivered_at,consumed_quota
                )
                VALUES(?,?,?,?)
                """,
                (
                    signal["id"],
                    user_id,
                    now_str(),
                    consume
                )
            )

            sent += 1

        except Exception:
            logger.exception("Signal delivery error")

    bot.send_message(
        user_id,
        f"📡 Sent <b>{sent}</b> available signal(s).",
        reply_markup=user_keyboard(user_id)
    )


# ============================================================
# ADMIN FUTURE SIGNAL BULK PARSER
# ============================================================

def parse_signal_line(line, default_date=None):

    line = line.strip()

    if not line:
        return None

    # Full format:
    # 21-09-2026 12:30 | EURUSD | UP | 95
    m = re.match(
        r"^(\d{1,2}[-/]\d{1,2}[-/]\d{4})\s+"
        r"(\d{1,2}:\d{2})\s*\|\s*"
        r"([^|]+)\|\s*"
        r"(UP|DOWN|BUY|SELL|CALL|PUT)"
        r"(?:\s*\|\s*(\d+(?:\.\d+)?))?$",
        line,
        re.I
    )

    if m:
        raw_date, tm, pair, direction, confidence = m.groups()

        raw_date = raw_date.replace("/", "-")

        parts = raw_date.split("-")

        if len(parts[0]) == 4:
            date = raw_date
        else:
            date = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"

        return {
            "date": date,
            "time": tm,
            "pair": pair.strip().upper(),
            "direction": direction.upper(),
            "confidence": confidence or ""
        }

    # Short format:
    # 12:30 EURUSD UP 95
    if default_date:

        m = re.match(
            r"^(\d{1,2}:\d{2})\s+"
            r"([A-Za-z0-9._/-]+)\s+"
            r"(UP|DOWN|BUY|SELL|CALL|PUT)"
            r"(?:\s+(\d+(?:\.\d+)?))?$",
            line,
            re.I
        )

        if m:
            tm, pair, direction, confidence = m.groups()

            return {
                "date": default_date,
                "time": tm,
                "pair": pair.upper(),
                "direction": direction.upper(),
                "confidence": confidence or ""
            }

    return None


def save_bulk_signals(text, default_date, audience, admin_id):

    created = []
    failed = []

    for line in text.splitlines():

        item = parse_signal_line(line, default_date)

        if not item:
            failed.append(line)
            continue

        try:
            datetime.strptime(
                f"{item['date']} {item['time']}",
                "%Y-%m-%d %H:%M"
            )

            db_execute(
                """
                INSERT INTO signals(
                    signal_date,signal_time,pair,direction,
                    confidence,audience,status,created_by,created_at
                )
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    item["date"],
                    item["time"],
                    item["pair"],
                    item["direction"],
                    item["confidence"],
                    audience,
                    "PENDING",
                    admin_id,
                    now_str()
                )
            )

            created.append(item)

        except Exception:
            failed.append(line)

    return created, failed


# ============================================================
# LIVE SESSION
# ============================================================

def active_live_session():
    return db_execute(
        """
        SELECT * FROM live_sessions
        WHERE status='ACTIVE'
        ORDER BY id DESC LIMIT 1
        """,
        fetchone=True
    )


def start_live_session(admin_id):
    existing = active_live_session()

    if existing:
        return existing

    db_execute(
        """
        INSERT INTO live_sessions(started_at,status)
        VALUES(?,?)
        """,
        (now_str(), "ACTIVE")
    )

    return active_live_session()


def end_live_session():
    session = active_live_session()

    if not session:
        return

    db_execute(
        """
        UPDATE live_sessions
        SET ended_at=?,status='ENDED'
        WHERE id=?
        """,
        (now_str(), session["id"])
    )


def send_live_to_vips(content, admin_id):

    session = active_live_session()

    if not session:
        return 0

    users = db_execute(
        """
        SELECT telegram_id
        FROM users
        WHERE live_signal_on=1
        """,
        fetchall=True
    )

    sent = 0

    template = get_setting("live_template")

    try:
        formatted = template.format(content=content)
    except Exception:
        formatted = content

    for u in users:
        try:
            if is_vip(u["telegram_id"]):
                bot.send_message(
                    u["telegram_id"],
                    formatted
                )

                db_execute(
                    """
                    INSERT INTO live_signals(
                        session_id,telegram_id,content,created_at
                    )
                    VALUES(?,?,?,?)
                    """,
                    (
                        session["id"],
                        u["telegram_id"],
                        content,
                        now_str()
                    )
                )

                sent += 1

        except Exception:
            pass

    return sent


# ============================================================
# BROADCAST
# ============================================================

def broadcast(text, target="ALL", selected=None):

    if target == "VIP":
        users = db_execute(
            "SELECT telegram_id FROM users",
            fetchall=True
        )
        users = [
            u for u in users
            if is_vip(u["telegram_id"])
        ]

    elif target == "SELECTED":
        users = [
            {"telegram_id": x}
            for x in (selected or [])
        ]

    else:
        users = db_execute(
            "SELECT telegram_id FROM users",
            fetchall=True
        )

    sent = 0

    for u in users:
        try:
            bot.send_message(
                u["telegram_id"],
                text
            )
            sent += 1
        except Exception:
            pass

    return sent


# ============================================================
# ADMIN DASHBOARD
# ============================================================

def admin_dashboard(chat_id):

    users = db_execute(
        "SELECT COUNT(*) AS c FROM users",
        fetchone=True
    )["c"]

    vip = sum(
        1
        for u in db_execute(
            "SELECT telegram_id FROM users",
            fetchall=True
        )
        if is_vip(u["telegram_id"])
    )

    pending_uid = db_execute(
        """
        SELECT COUNT(*) AS c FROM uid_submissions
        WHERE status='PENDING'
        """,
        fetchone=True
    )["c"]

    pending_withdraw = db_execute(
        """
        SELECT COUNT(*) AS c FROM withdrawals
        WHERE status='PENDING'
        """,
        fetchone=True
    )["c"]

    signals = db_execute(
        """
        SELECT COUNT(*) AS c FROM signals
        WHERE signal_date=?
        """,
        (now_bd().date().isoformat(),),
        fetchone=True
    )["c"]

    text = (
        "📊 <b>Admin Dashboard</b>\n\n"
        f"👥 Users: <b>{users}</b>\n"
        f"⭐ VIP: <b>{vip}</b>\n"
        f"🆔 Pending UID: <b>{pending_uid}</b>\n"
        f"💸 Pending Withdraw: <b>{pending_withdraw}</b>\n"
        f"📡 Today's Signals: <b>{signals}</b>\n"
        f"🛠 Maintenance: <b>{'ON' if get_setting('maintenance') == '1' else 'OFF'}</b>\n"
        f"💸 Withdrawals: <b>{'ON' if get_setting('withdrawals') == '1' else 'OFF'}</b>"
    )

    bot.send_message(
        chat_id,
        text,
        reply_markup=admin_keyboard()
    )


# ============================================================
# UID
# ============================================================

def submit_uid(user_id, uid):

    uid = uid.strip()

    duplicate_user = db_execute(
        "SELECT telegram_id FROM users WHERE uid=?",
        (uid,),
        fetchone=True
    )

    if duplicate_user:
        return False, get_setting("uid_duplicate")

    pending = db_execute(
        """
        SELECT id FROM uid_submissions
        WHERE telegram_id=? AND status='PENDING'
        """,
        (user_id,),
        fetchone=True
    )

    if pending:
        return False, get_setting("uid_pending")

    db_execute(
        """
        INSERT INTO uid_submissions(
            telegram_id,uid,status,created_at
        )
        VALUES(?,?,?,?)
        """,
        (user_id, uid, "PENDING", now_str())
    )

    return True, "✅ UID submitted. Waiting for admin approval."


# ============================================================
# VIP
# ============================================================

def add_vip(user_id, days):

    until = now_bd() + timedelta(days=days)

    db_execute(
        """
        UPDATE users
        SET vip_until=?
        WHERE telegram_id=?
        """,
        (until.isoformat(), user_id)
    )


def remove_vip(user_id):

    db_execute(
        """
        UPDATE users
        SET vip_until=NULL
        WHERE telegram_id=?
        """,
        (user_id,)
    )


# ============================================================
# WALLET
# ============================================================

def wallet_balance(user_id):

    row = user_row(user_id)

    return row["balance_cents"] if row else 0


def wallet_add(user_id, amount, tx_type, description):

    db_execute(
        """
        UPDATE users
        SET balance_cents=balance_cents+?
        WHERE telegram_id=?
        """,
        (amount, user_id)
    )

    db_execute(
        """
        INSERT INTO wallet_transactions(
            telegram_id,type,amount_cents,description,created_at
        )
        VALUES(?,?,?,?,?)
        """,
        (
            user_id,
            tx_type,
            amount,
            description,
            now_str()
        )
    )


def wallet_remove(user_id, amount, tx_type, description):

    row = user_row(user_id)

    if not row or row["balance_cents"] < amount:
        return False

    db_execute(
        """
        UPDATE users
        SET balance_cents=balance_cents-?
        WHERE telegram_id=?
        """,
        (amount, user_id)
    )

    db_execute(
        """
        INSERT INTO wallet_transactions(
            telegram_id,type,amount_cents,description,created_at
        )
        VALUES(?,?,?,?,?)
        """,
        (
            user_id,
            tx_type,
            -amount,
            description,
            now_str()
        )
    )

    return True


# ============================================================
# REFERRAL
# ============================================================

def process_referral_bonus(user_id):

    user = user_row(user_id)

    if not user or not user["referred_by"] or user["referral_paid"]:
        return

    already = db_execute(
        """
        SELECT id FROM referral_transactions
        WHERE referred_id=?
        """,
        (user_id,),
        fetchone=True
    )

    if already:
        return

    qualifying = db_execute(
        """
        SELECT id FROM signal_access
        WHERE telegram_id=?
        LIMIT 1
        """,
        (user_id,),
        fetchone=True
    )

    if not qualifying:
        return

    bonus = cents(get_setting("referral_bonus", "1"))

    wallet_add(
        user["referred_by"],
        bonus,
        "REFERRAL",
        f"Referral bonus from {user_id}"
    )

    db_execute(
        """
        INSERT INTO referral_transactions(
            referrer_id,referred_id,amount_cents,created_at
        )
        VALUES(?,?,?,?)
        """,
        (
            user["referred_by"],
            user_id,
            bonus,
            now_str()
        )
    )

    db_execute(
        """
        UPDATE users
        SET referral_paid=1
        WHERE telegram_id=?
        """,
        (user_id,)
    )


# ============================================================
# WITHDRAW
# ============================================================

def create_withdraw(user_id, amount_cents, method, account):

    if get_setting("withdrawals") != "1":
        return False, get_setting("withdraw_disabled")

    minimum = cents(get_setting("min_withdraw", "5"))

    if amount_cents < minimum:
        return False, get_setting("withdraw_min").replace(
            "{amount}",
            money(minimum)
        )

    if not wallet_remove(
        user_id,
        amount_cents,
        "WITHDRAW_HOLD",
        "Withdrawal request"
    ):
        return False, "❌ Insufficient balance."

    db_execute(
        """
        INSERT INTO withdrawals(
            telegram_id,amount_cents,method,account,
            status,created_at
        )
        VALUES(?,?,?,?,?,?)
        """,
        (
            user_id,
            amount_cents,
            method,
            account,
            "PENDING",
            now_str()
        )
    )

    return True, get_setting("withdraw_success")


# ============================================================
# TEXT EDITOR
# ============================================================

TEXT_KEYS = {
    "👋 Welcome": "welcome",
    "📡 Signal Template": "signal_template",
    "⚡ Live Template": "live_template",
    "📢 Notice": "notice",
    "📖 Trading Rules": "trading_rules",
    "🛠 Maintenance Text": "maintenance_text",
    "❌ Error Text": "error_text",
    "⚠️ Invalid Text": "invalid_text",
    "⭐ VIP Text": "vip_text",
    "🔔 Notification ON": "notification_on",
    "🔕 Notification OFF": "notification_off",
    "💸 Withdraw Disabled": "withdraw_disabled",
    "✅ WIN Text": "win_text",
    "❌ LOSS Text": "loss_text",
    "⏭ SKIP Text": "skip_text",
    "⚠️ VIP Expired": "vip_expired",
    "⚡ Live OFF Text": "live_off",
}


def text_editor_keyboard():
    keys = list(TEXT_KEYS.keys())
    rows = []

    for i in range(0, len(keys), 2):
        rows.append(keys[i:i+2])

    rows.append(["🔙 Back", "🏠 Main Menu"])

    return kb(rows)


# ============================================================
# GENERIC INPUT HANDLER
# ============================================================

@bot.message_handler(content_types=["text"])
def all_text_handler(message):

    user_id = message.from_user.id
    text = (message.text or "").strip()

    register_user(message)

    # --------------------------------------------------------
    # BACK / HOME
    # --------------------------------------------------------

    if text == "🏠 Main Menu":
        clear_state(user_id)
        send_main_menu(user_id)
        return

    if text == "🔙 Back":
        clear_state(user_id)
        send_back(user_id)
        return

    state = get_state(user_id)

    # --------------------------------------------------------
    # STATE INPUTS
    # --------------------------------------------------------

    if state:

        action = state["action"]
        data = state["data"]

        # BALANCE
        if action == "SET_BALANCE_FOR_SIGNAL":
            try:
                amount = cents(text)
                if amount <= 0:
                    raise ValueError

                db_execute(
                    """
                    UPDATE mm_profiles
                    SET balance_cents=?,
                        daily_start_balance_cents=?,
                        balance_confirmed=1,
                        last_reset_date=?
                    WHERE telegram_id=?
                    """,
                    (
                        amount,
                        amount,
                        now_bd().date().isoformat(),
                        user_id
                    )
                )

                clear_state(user_id)

                bot.send_message(
                    user_id,
                    f"✅ Trading balance confirmed: <b>${money(amount)}</b>",
                    reply_markup=user_keyboard(user_id)
                )

            except Exception:
                bot.send_message(
                    user_id,
                    "❌ Enter a valid USD amount, e.g. <b>100</b>.",
                    reply_markup=back_keyboard()
                )
            return

        # MM generic numeric
        if action in (
            "MM_BALANCE",
            "MM_PROFIT",
            "MM_LOSS",
            "MM_BASE",
            "MM_M1",
            "MM_M2",
            "MM_PAYOUT",
            "MM_MAX_TRADES"
        ):
            try:
                if action == "MM_PAYOUT":
                    value = float(text)
                    if value <= 0 or value > 100:
                        raise ValueError

                    db_execute(
                        """
                        UPDATE mm_profiles
                        SET payout_percent=?
                        WHERE telegram_id=?
                        """,
                        (value, user_id)
                    )

                elif action == "MM_MAX_TRADES":
                    value = int(text)
                    if value < 0:
                        raise ValueError

                    db_execute(
                        """
                        UPDATE mm_profiles
                        SET max_trades=?
                        WHERE telegram_id=?
                        """,
                        (value, user_id)
                    )

                else:
                    value = cents(text)

                    if value < 0:
                        raise ValueError

                    field = {
                        "MM_BALANCE": "balance_cents",
                        "MM_PROFIT": "profit_target_cents",
                        "MM_LOSS": "loss_limit_cents",
                        "MM_BASE": "base_trade_cents",
                        "MM_M1": "m1_trade_cents",
                        "MM_M2": "m2_trade_cents",
                    }[action]

                    db_execute(
                        f"""
                        UPDATE mm_profiles
                        SET {field}=?
                        WHERE telegram_id=?
                        """,
                        (value, user_id)
                    )

                clear_state(user_id)

                bot.send_message(
                    user_id,
                    "✅ Money Management setting saved.",
                    reply_markup=money_keyboard()
                )

            except Exception:
                bot.send_message(
                    user_id,
                    "❌ Invalid value.",
                    reply_markup=money_keyboard()
                )

            return

        # UID
        if action == "UID_INPUT":
            ok, result = submit_uid(user_id, text)

            if ok:
                clear_state(user_id)

                # Notify admin
                try:
                    bot.send_message(
                        ADMIN_ID,
                        f"🆔 <b>New UID Request</b>\n\n"
                        f"User: <code>{user_id}</code>\n"
                        f"UID: <code>{text}</code>\n\n"
                        f"Use UID Requests menu to process."
                    )
                except Exception:
                    pass

                bot.send_message(
                    user_id,
                    result,
                    reply_markup=user_keyboard(user_id)
                )
            else:
                bot.send_message(
                    user_id,
                    result,
                    reply_markup=back_keyboard()
                )
            return

        # WITHDRAW AMOUNT
        if action == "WITHDRAW_AMOUNT":
            try:
                amount = cents(text)

                if amount <= 0:
                    raise ValueError

                set_state(
                    user_id,
                    "WITHDRAW_METHOD",
                    {"amount": amount}
                )

                bot.send_message(
                    user_id,
                    "💳 Enter withdrawal method.\nExample: bKash",
                    reply_markup=back_keyboard()
                )

            except Exception:
                bot.send_message(
                    user_id,
                    "❌ Enter a valid amount.",
                    reply_markup=back_keyboard()
                )

            return

        # WITHDRAW METHOD
        if action == "WITHDRAW_METHOD":

            data["method"] = text

            set_state(
                user_id,
                "WITHDRAW_ACCOUNT",
                data
            )

            bot.send_message(
                user_id,
                "📱 Enter your account number:",
                reply_markup=back_keyboard()
            )
            return

        # WITHDRAW ACCOUNT
        if action == "WITHDRAW_ACCOUNT":

            ok, result = create_withdraw(
                user_id,
                data["amount"],
                data["method"],
                text
            )

            clear_state(user_id)

            bot.send_message(
                user_id,
                result,
                reply_markup=user_keyboard(user_id)
            )

            return

        # BULK SIGNAL DATE
        if action == "SIGNAL_DATE":

            date = text.replace("/", "-")

            try:
                parts = date.split("-")

                if len(parts) == 3 and len(parts[0]) != 4:
                    date = (
                        f"{parts[2]}-{parts[1].zfill(2)}-"
                        f"{parts[0].zfill(2)}"
                    )

                datetime.strptime(date, "%Y-%m-%d")

                set_state(
                    user_id,
                    "SIGNAL_BULK",
                    {
                        "date": date,
                        "audience": "ALL"
                    }
                )

                bot.send_message(
                    user_id,
                    "📡 Now paste ALL signals at once.\n\n"
                    "<code>12:30 EURUSD UP 95\n"
                    "12:35 GBPUSD DOWN 97\n"
                    "12:40 USDJPY UP 96</code>\n\n"
                    "Or use full date format:\n"
                    "<code>21-09-2026 12:30 | EURUSD | UP | 95</code>",
                    reply_markup=back_keyboard()
                )

            except Exception:
                bot.send_message(
                    user_id,
                    "❌ Date format: <code>21-09-2026</code>",
                    reply_markup=back_keyboard()
                )

            return

        # BULK SIGNAL
        if action == "SIGNAL_BULK":

            created, failed = save_bulk_signals(
                text,
                data.get("date"),
                data.get("audience", "ALL"),
                user_id
            )

            clear_state(user_id)

            result = (
                f"✅ Added: <b>{len(created)}</b>\n"
                f"❌ Failed: <b>{len(failed)}</b>"
            )

            if failed:
                result += "\n\nFailed lines:\n"
                result += "\n".join(
                    f"• {x[:100]}" for x in failed[:10]
                )

            bot.send_message(
                user_id,
                result,
                reply_markup=signal_keyboard()
            )
            return

        # TEXT EDITOR
        if action == "EDIT_TEXT":

            key = data["key"]

            set_setting(key, text)

            clear_state(user_id)

            bot.send_message(
                user_id,
                "✅ Text updated and saved permanently.",
                reply_markup=text_editor_keyboard()
            )
            return

        # NOTICE
        if action == "NOTICE_INPUT":

            set_setting("notice", text)
            clear_state(user_id)

            bot.send_message(
                user_id,
                "✅ Notice updated.",
                reply_markup=admin_keyboard()
            )
            return

        # BROADCAST
        if action == "BROADCAST_INPUT":

            target = data.get("target", "ALL")

            sent = broadcast(text, target)

            clear_state(user_id)

            bot.send_message(
                user_id,
                f"📢 Broadcast complete.\nSent: <b>{sent}</b>",
                reply_markup=admin_keyboard()
            )
            return

        # LIVE TEXT
        if action == "LIVE_TEXT":

            if not active_live_session():
                bot.send_message(
                    user_id,
                    "⚠️ Live Session is not active.",
                    reply_markup=admin_keyboard()
                )
                clear_state(user_id)
                return

            sent = send_live_to_vips(
                text,
                user_id
            )

            clear_state(user_id)

            bot.send_message(
                user_id,
                f"⚡ Live message sent to <b>{sent}</b> VIP user(s).",
                reply_markup=admin_keyboard()
            )
            return

        # LIVE PAIR
        if action == "LIVE_PAIR":
            LIVE_DRAFT[user_id] = {
                "pair": text
            }

            clear_state(user_id)

            bot.send_message(
                user_id,
                "⏰ Enter signal time:",
                reply_markup=kb([
                    ["⬆️ UP", "⬇️ DOWN"],
                    ["📤 Send Live Signal"],
                    ["✏️ Send Live Text"],
                    ["⛔ End Live Mode"],
                    ["🔙 Back"]
                ])
            )

            LIVE_DRAFT[user_id]["direction"] = ""

            set_state(user_id, "LIVE_TIME")
            return

        # LIVE TIME
        if action == "LIVE_TIME":

            LIVE_DRAFT.setdefault(user_id, {})
            LIVE_DRAFT[user_id]["time"] = text

            clear_state(user_id)

            bot.send_message(
                user_id,
                "Choose direction:",
                reply_markup=kb([
                    ["⬆️ UP", "⬇️ DOWN"],
                    ["📤 Send Live Signal"],
                    ["✏️ Send Live Text"],
                    ["⛔ End Live Mode"],
                    ["🔙 Back"]
                ])
            )
            return

        # ADD VIP
        if action == "VIP_USER":
            try:
                target = int(text)
                set_state(
                    user_id,
                    "VIP_DAYS",
                    {"target": target}
                )

                bot.send_message(
                    user_id,
                    "📅 Enter VIP days:",
                    reply_markup=back_keyboard()
                )
            except Exception:
                bot.send_message(
                    user_id,
                    "❌ Enter Telegram User ID.",
                    reply_markup=back_keyboard()
                )
            return

        if action == "VIP_DAYS":
            try:
                days = int(text)

                if days <= 0:
                    raise ValueError

                target = data["target"]

                add_vip(target, days)

                clear_state(user_id)

                bot.send_message(
                    user_id,
                    f"✅ VIP added for <code>{target}</code> for {days} days.",
                    reply_markup=vip_keyboard()
                )

                try:
                    bot.send_message(
                        target,
                        f"⭐ VIP activated for <b>{days} days</b>."
                    )
                except Exception:
                    pass

            except Exception:
                bot.send_message(
                    user_id,
                    "❌ Invalid days.",
                    reply_markup=back_keyboard()
                )

            return

        # BALANCE ADD
        if action == "ADMIN_ADD_BALANCE":
            try:
                amount = cents(text)

                if amount <= 0:
                    raise ValueError

                target = data["target"]

                wallet_add(
                    target,
                    amount,
                    "ADMIN_ADD",
                    "Admin added balance"
                )

                clear_state(user_id)

                bot.send_message(
                    user_id,
                    "✅ Balance added.",
                    reply_markup=wallet_admin_keyboard()
                )

            except Exception:
                bot.send_message(
                    user_id,
                    "❌ Invalid amount.",
                    reply_markup=back_keyboard()
                )

            return

        # BALANCE REMOVE
        if action == "ADMIN_REMOVE_BALANCE":
            try:
                amount = cents(text)

                target = data["target"]

                if not wallet_remove(
                    target,
                    amount,
                    "ADMIN_REMOVE",
                    "Admin removed balance"
                ):
                    raise ValueError

                clear_state(user_id)

                bot.send_message(
                    user_id,
                    "✅ Balance removed.",
                    reply_markup=wallet_admin_keyboard()
                )

            except Exception:
                bot.send_message(
                    user_id,
                    "❌ Invalid amount or insufficient balance.",
                    reply_markup=back_keyboard()
                )

            return

        # SUB ADMIN ID
        if action == "SUBADMIN_ID":
            try:
                target = int(text)

                db_execute(
                    """
                    INSERT OR IGNORE INTO admins(
                        telegram_id,role
                    )
                    VALUES(?,?)
                    """,
                    (target, "subadmin")
                )

                set_state(
                    user_id,
                    "SUBADMIN_PERMISSION",
                    {"target": target}
                )

                bot.send_message(
                    user_id,
                    "Choose permission to toggle:",
                    reply_markup=kb([
                        ["👥 Users", "⭐ VIP"],
                        ["📡 Signals", "💰 Wallet"],
                        ["💸 Withdraw", "📢 Broadcast"],
                        ["⚙️ Settings", "⚡ Live"],
                        ["📝 Text Editor"],
                        ["💾 Save Permissions"],
                        ["🔙 Back"]
                    ])
                )

            except Exception:
                bot.send_message(
                    user_id,
                    "❌ Invalid Telegram ID.",
                    reply_markup=back_keyboard()
                )

            return

        # BROADCAST TARGET
        if action == "BROADCAST_TARGET":
            target = text

            if target == "👥 All Users":
                target = "ALL"
            elif target == "⭐ VIP Users":
                target = "VIP"
            else:
                target = "ALL"

            set_state(
                user_id,
                "BROADCAST_INPUT",
                {"target": target}
            )

            bot.send_message(
                user_id,
                "📢 Enter broadcast text:",
                reply_markup=back_keyboard()
            )
            return

        # WITHDRAW SETTINGS
        if action == "MIN_WITHDRAW":
            try:
                amount = float(text)

                if amount < 0:
                    raise ValueError

                set_setting("min_withdraw", amount)

                clear_state(user_id)

                bot.send_message(
                    user_id,
                    "✅ Minimum withdrawal updated.",
                    reply_markup=wallet_admin_keyboard()
                )

            except Exception:
                bot.send_message(
                    user_id,
                    "❌ Invalid amount.",
                    reply_markup=back_keyboard()
                )

            return

        # REFERRAL BONUS
        if action == "REFERRAL_BONUS":
            try:
                amount = float(text)

                if amount < 0:
                    raise ValueError

                set_setting("referral_bonus", amount)

                clear_state(user_id)

                bot.send_message(
                    user_id,
                    "✅ Referral bonus updated.",
                    reply_markup=admin_keyboard()
                )

            except Exception:
                bot.send_message(
                    user_id,
                    "❌ Invalid amount.",
                    reply_markup=back_keyboard()
                )

            return

    # ========================================================
    # USER MENU
    # ========================================================

    if text == "📡 Future Signals":
        handle_future_signals_user(message)
        return

    if text == "📅 Today's Signals":
        send_todays_signals(user_id)
        return

    if text == "⚡ Live Signals":

        current = user_row(user_id)

        if not is_vip(user_id):
            bot.send_message(
                user_id,
                "⭐ Live Signals are VIP-only.",
                reply_markup=user_keyboard(user_id)
            )
            return

        enabled = current["live_signal_on"]

        db_execute(
            """
            UPDATE users
            SET live_signal_on=?
            WHERE telegram_id=?
            """,
            (0 if enabled else 1, user_id)
        )

        bot.send_message(
            user_id,
            "⚡ Live Signal " + ("OFF" if enabled else "ON"),
            reply_markup=user_keyboard(user_id)
        )
        return

    if text == "💰 Money Management":
        reset_daily_mm_if_needed(user_id)

        bot.send_message(
            user_id,
            "💰 <b>Money Management</b>",
            reply_markup=money_keyboard()
        )
        return

    if text == "💵 Set Balance":
        set_state(user_id, "MM_BALANCE")
        bot.send_message(
            user_id,
            "💵 Enter current trading balance in USD:",
            reply_markup=back_keyboard()
        )
        return

    if text == "🎯 Profit Target":
        set_state(user_id, "MM_PROFIT")
        bot.send_message(
            user_id,
            "🎯 Enter daily Profit Target in USD:",
            reply_markup=back_keyboard()
        )
        return

    if text == "🛑 Loss Limit":
        set_state(user_id, "MM_LOSS")
        bot.send_message(
            user_id,
            "🛑 Enter daily Loss Limit in USD:",
            reply_markup=back_keyboard()
        )
        return

    if text == "💵 Base Trade":
        set_state(user_id, "MM_BASE")
        bot.send_message(
            user_id,
            "💵 Enter Base Trade amount:",
            reply_markup=back_keyboard()
        )
        return

    if text == "🔄 M1 Trade":
        set_state(user_id, "MM_M1")
        bot.send_message(
            user_id,
            "🔄 Enter M1 Trade amount:",
            reply_markup=back_keyboard()
        )
        return

    if text == "🔄 M2 Trade":
        set_state(user_id, "MM_M2")
        bot.send_message(
            user_id,
            "🔄 Enter M2 amount, or 0 for automatic recovery calculation:",
            reply_markup=back_keyboard()
        )
        return

    if text == "📈 Payout %":
        set_state(user_id, "MM_PAYOUT")
        bot.send_message(
            user_id,
            "📈 Enter expected payout percentage, e.g. 80:",
            reply_markup=back_keyboard()
        )
        return

    if text == "🔢 Max Trades":
        set_state(user_id, "MM_MAX_TRADES")
        bot.send_message(
            user_id,
            "🔢 Enter maximum trades per day:",
            reply_markup=back_keyboard()
        )
        return

    if text == "🚫 Stop Trading":
        db_execute(
            """
            UPDATE mm_profiles
            SET stop_trading=1
            WHERE telegram_id=?
            """,
            (user_id,)
        )

        bot.send_message(
            user_id,
            "⛔ Trading stopped.",
            reply_markup=money_keyboard()
        )
        return

    if text == "▶️ Resume Trading":
        db_execute(
            """
            UPDATE mm_profiles
            SET stop_trading=0
            WHERE telegram_id=?
            """,
            (user_id,)
        )

        bot.send_message(
            user_id,
            "▶️ Trading resumed.",
            reply_markup=money_keyboard()
        )
        return

    if text == "📊 MM Status":

        row = reset_daily_mm_if_needed(user_id)
        amount = current_trade_amount(user_id)

        bot.send_message(
            user_id,
            (
                "💰 <b>Money Management</b>\n\n"
                f"Balance: <b>${money(row['balance_cents'])}</b>\n"
                f"Daily P/L: <b>${money(row['daily_pl_cents'])}</b>\n"
                f"Profit Target: <b>${money(row['profit_target_cents'])}</b>\n"
                f"Loss Limit: <b>${money(row['loss_limit_cents'])}</b>\n"
                f"Base: <b>${money(row['base_trade_cents'])}</b>\n"
                f"M1: <b>${money(row['m1_trade_cents'])}</b>\n"
                f"Stage: <b>{row['stage']}</b>\n"
                f"Recovery: <b>${money(row['recovery_loss_cents'])}</b>\n"
                f"Next Trade: <b>${money(amount)}</b>\n"
                f"Trades: <b>{row['trades_today']}/{row['max_trades']}</b>"
            ),
            reply_markup=money_keyboard()
        )
        return

    if text == "💼 Wallet":

        row = user_row(user_id)

        bot.send_message(
            user_id,
            (
                "💼 <b>Wallet</b>\n\n"
                f"Balance: <b>${money(row['balance_cents'])}</b>"
            ),
            reply_markup=kb([
                ["💸 Withdraw"],
                ["📜 Wallet History"],
                ["🔙 Back", "🏠 Main Menu"]
            ])
        )
        return

    if text == "💸 Withdraw":

        if get_setting("withdrawals") != "1":
            bot.send_message(
                user_id,
                get_setting("withdraw_disabled"),
                reply_markup=user_keyboard(user_id)
            )
            return

        set_state(user_id, "WITHDRAW_AMOUNT")

        bot.send_message(
            user_id,
            "💸 Enter withdrawal amount in USD:",
            reply_markup=back_keyboard()
        )
        return

    if text == "👤 VIP / UID":

        u = user_row(user_id)

        vip_status = (
            u["vip_until"]
            if is_vip(user_id)
            else "Not active"
        )

        bot.send_message(
            user_id,
            (
                "👤 <b>VIP / UID</b>\n\n"
                f"VIP: <b>{vip_status}</b>\n"
                f"UID: <b>{u['uid'] or 'Not submitted'}</b>"
            ),
            reply_markup=kb([
                ["🆔 Submit UID"],
                ["⭐ VIP Status"],
                ["🔙 Back", "🏠 Main Menu"]
            ])
        )
        return

    if text == "🆔 Submit UID":

        u = user_row(user_id)

        if u["uid"]:
            bot.send_message(
                user_id,
                "✅ Your UID is already approved and linked.",
                reply_markup=user_keyboard(user_id)
            )
            return

        set_state(user_id, "UID_INPUT")

        bot.send_message(
            user_id,
            "🆔 Enter your Quotex UID:",
            reply_markup=back_keyboard()
        )
        return

    if text == "⭐ VIP Status":

        bot.send_message(
            user_id,
            (
                get_setting("vip_text")
                if is_vip(user_id)
                else get_setting("nonvip_text")
            ),
            reply_markup=user_keyboard(user_id)
        )
        return

    if text == "👥 Referral":

        link = (
            f"https://t.me/{bot.get_me().username}"
            f"?start=ref{user_id}"
        )

        bot.send_message(
            user_id,
            get_setting("referral_text")
            .replace("{link}", link)
            .replace(
                "{bonus}",
                get_setting("referral_bonus")
            ),
            reply_markup=user_keyboard(user_id)
        )
        return

    if text == "📊 Dashboard":

        u = user_row(user_id)
        mm = reset_daily_mm_if_needed(user_id)

        bot.send_message(
            user_id,
            (
                "📊 <b>Dashboard</b>\n\n"
                f"👤 User: <b>{u['first_name']}</b>\n"
                f"⭐ VIP: <b>{'YES' if is_vip(user_id) else 'NO'}</b>\n"
                f"💰 Wallet: <b>${money(u['balance_cents'])}</b>\n"
                f"📡 Free Signals: <b>"
                f"{'Unlimited' if is_vip(user_id) else free_remaining(user_id)}"
                f"</b>\n"
                f"📈 Today's P/L: <b>${money(mm['daily_pl_cents'])}</b>\n"
                f"✅ Wins: <b>{mm['wins_today']}</b>\n"
                f"❌ Losses: <b>{mm['losses_today']}</b>"
            ),
            reply_markup=user_keyboard(user_id)
        )
        return

    if text == "📈 Signal Result":

        row = db_execute(
            """
            SELECT s.*
            FROM signals s
            JOIN signal_access a
              ON a.signal_id=s.id
            WHERE a.telegram_id=?
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (user_id,),
            fetchone=True
        )

        if not row:
            bot.send_message(
                user_id,
                "📭 No actionable signal found.",
                reply_markup=user_keyboard(user_id)
            )
            return

        result = db_execute(
            """
            SELECT id FROM signal_results
            WHERE signal_id=? AND telegram_id=?
            """,
            (row["id"], user_id),
            fetchone=True
        )

        if result:
            bot.send_message(
                user_id,
                "⚠️ Result for the latest signal is already recorded.",
                reply_markup=user_keyboard(user_id)
            )
            return

        set_state(
            user_id,
            "RESULT_CHOICE",
            {"signal_id": row["id"]}
        )

        bot.send_message(
            user_id,
            f"📈 Result for <b>{row['pair']}</b>:",
            reply_markup=kb([
                ["✅ WIN", "❌ LOSS"],
                ["⏭ SKIP"],
                ["🔙 Back"]
            ])
        )
        return

    if text in ("✅ WIN", "❌ LOSS", "⏭ SKIP"):

        state = get_state(user_id)

        if not state or state["action"] != "RESULT_CHOICE":
            return

        result = {
            "✅ WIN": "WIN",
            "❌ LOSS": "LOSS",
            "⏭ SKIP": "SKIP"
        }[text]

        ok, response = apply_trade_result(
            user_id,
            state["data"]["signal_id"],
            result
        )

        clear_state(user_id)

        bot.send_message(
            user_id,
            response,
            reply_markup=user_keyboard(user_id)
        )

        process_referral_bonus(user_id)
        return

    if text == "🗳 Vote Signal":

        row = db_execute(
            """
            SELECT s.*
            FROM signals s
            JOIN signal_access a
              ON a.signal_id=s.id
            WHERE a.telegram_id=?
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (user_id,),
            fetchone=True
        )

        if not row:
            bot.send_message(
                user_id,
                "📭 No signal available for voting.",
                reply_markup=user_keyboard(user_id)
            )
            return

        existing = db_execute(
            """
            SELECT vote FROM signal_votes
            WHERE signal_id=? AND telegram_id=?
            """,
            (row["id"], user_id),
            fetchone=True
        )

        if existing:
            bot.send_message(
                user_id,
                "🗳 You already voted on this signal.",
                reply_markup=user_keyboard(user_id)
            )
            return

        set_state(
            user_id,
            "VOTE_CHOICE",
            {"signal_id": row["id"]}
        )

        bot.send_message(
            user_id,
            "🗳 Choose your prediction:",
            reply_markup=kb([
                ["👍 UP", "👎 DOWN"],
                ["⏭ SKIP"],
                ["🔙 Back"]
            ])
        )
        return

    if text in ("👍 UP", "👎 DOWN"):

        state = get_state(user_id)

        if not state or state["action"] != "VOTE_CHOICE":
            return

        vote = "UP" if text == "👍 UP" else "DOWN"

        db_execute(
            """
            INSERT OR IGNORE INTO signal_votes(
                signal_id,telegram_id,vote,created_at
            )
            VALUES(?,?,?,?)
            """,
            (
                state["data"]["signal_id"],
                user_id,
                vote,
                now_str()
            )
        )

        clear_state(user_id)

        bot.send_message(
            user_id,
            "✅ Vote recorded.",
            reply_markup=user_keyboard(user_id)
        )
        return

    if text == "⏭ SKIP":

        state = get_state(user_id)

        if state and state["action"] == "VOTE_CHOICE":
            db_execute(
                """
                INSERT OR IGNORE INTO signal_votes(
                    signal_id,telegram_id,vote,created_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    state["data"]["signal_id"],
                    user_id,
                    "SKIP",
                    now_str()
                )
            )

            clear_state(user_id)

            bot.send_message(
                user_id,
                "⏭ Vote skipped.",
                reply_markup=user_keyboard(user_id)
            )
        return

    if text == "📜 Signal History":

        rows = db_execute(
            """
            SELECT s.signal_date,s.signal_time,s.pair,
                   s.direction,r.result
            FROM signals s
            JOIN signal_access a
              ON a.signal_id=s.id
            LEFT JOIN signal_results r
              ON r.signal_id=s.id
             AND r.telegram_id=?
            WHERE a.telegram_id=?
            ORDER BY s.id DESC
            LIMIT 15
            """,
            (user_id, user_id),
            fetchall=True
        )

        if not rows:
            output = "📭 No signal history."
        else:
            lines = ["📜 <b>Signal History</b>\n"]

            for r in rows:
                result = r["result"] or "Pending"

                lines.append(
                    f"📅 {r['signal_date']} "
                    f"{r['signal_time']}\n"
                    f"💱 {r['pair']} | "
                    f"{r['direction']} | "
                    f"<b>{result}</b>\n"
                )

            output = "\n".join(lines)

        bot.send_message(
            user_id,
            output,
            reply_markup=user_keyboard(user_id)
        )
        return

    if text == "📖 Trading Rules":

        bot.send_message(
            user_id,
            get_setting("rules_text").replace(
                "{rules}",
                get_setting("trading_rules")
            ),
            reply_markup=user_keyboard(user_id)
        )
        return

    if text == "🔔 Notifications":

        u = user_row(user_id)

        new_value = 0 if u["notification_on"] else 1

        db_execute(
            """
            UPDATE users
            SET notification_on=?
            WHERE telegram_id=?
            """,
            (new_value, user_id)
        )

        bot.send_message(
            user_id,
            get_setting(
                "notification_on"
                if new_value
                else "notification_off"
            ),
            reply_markup=user_keyboard(user_id)
        )
        return

    if text == "❓ Help":

        bot.send_message(
            user_id,
            (
                "❓ <b>Help</b>\n\n"
                "📡 Future Signals — scheduled signals\n"
                "⚡ Live Signals — VIP live session\n"
                "💰 Money Management — trading settings\n"
                "💼 Wallet — balance and transactions\n"
                "💸 Withdraw — withdrawal request\n"
                "⭐ VIP / UID — VIP and Quotex UID\n"
                "👥 Referral — referral system\n"
            ),
            reply_markup=user_keyboard(user_id)
        )
        return

    if text == "📜 Wallet History":

        rows = db_execute(
            """
            SELECT type,amount_cents,description,created_at
            FROM wallet_transactions
            WHERE telegram_id=?
            ORDER BY id DESC LIMIT 15
            """,
            (user_id,),
            fetchall=True
        )

        if not rows:
            output = "📭 No wallet transactions."
        else:
            lines = ["📜 <b>Wallet History</b>\n"]

            for r in rows:
                sign = "+" if r["amount_cents"] >= 0 else ""

                lines.append(
                    f"{r['created_at']} | "
                    f"{r['type']} | "
                    f"{sign}${money(r['amount_cents'])}\n"
                    f"{r['description']}"
                )

            output = "\n".join(lines)

        bot.send_message(
            user_id,
            output,
            reply_markup=user_keyboard(user_id)
        )
        return

    # ========================================================
    # ADMIN MENU
    # ========================================================

    if is_admin(user_id) or admin_row(user_id):

        # ADMIN DASHBOARD
        if text == "📊 Admin Dashboard":
            if has_permission(user_id, "p_users"):
                admin_dashboard(user_id)
            return

        # USERS
        if text == "👥 Users":

            if not has_permission(user_id, "p_users"):
                return

            users = db_execute(
                """
                SELECT telegram_id,first_name,username,
                       balance_cents,vip_until,uid
                FROM users
                ORDER BY id DESC
                LIMIT 30
                """,
                fetchall=True
            )

            lines = ["👥 <b>Users</b>\n"]

            for u in users:
                lines.append(
                    f"ID: <code>{u['telegram_id']}</code>\n"
                    f"Name: {u['first_name']}\n"
                    f"Balance: ${money(u['balance_cents'])}\n"
                    f"VIP: {'YES' if is_vip(u['telegram_id']) else 'NO'}\n"
                    f"UID: {u['uid'] or '-'}\n"
                )

            bot.send_message(
                user_id,
                "\n".join(lines),
                reply_markup=admin_keyboard()
            )
            return

        # FUTURE SIGNAL ADMIN
        if text == "📡 Future Signals":

            if not has_permission(user_id, "p_signals"):
                return

            bot.send_message(
                user_id,
                "📡 <b>Future Signal Manager</b>",
                reply_markup=signal_keyboard()
            )
            return

        if text == "➕ Add Future Signals":

            set_state(user_id, "SIGNAL_DATE")

            bot.send_message(
                user_id,
                "📅 Enter date in Bangladesh time.\n\n"
                "Example: <code>21-09-2026</code>",
                reply_markup=back_keyboard()
            )
            return

        if text == "📋 Future Signal List":

            rows = db_execute(
                """
                SELECT * FROM signals
                WHERE signal_date>=?
                ORDER BY signal_date,signal_time
                LIMIT 50
                """,
                (now_bd().date().isoformat(),),
                fetchall=True
            )

            if not rows:
                bot.send_message(
                    user_id,
                    "📭 No future signals.",
                    reply_markup=signal_keyboard()
                )
                return

            lines = ["📋 <b>Future Signals</b>\n"]

            for s in rows:
                lines.append(
                    f"#{s['id']} | {s['signal_date']} "
                    f"{s['signal_time']}\n"
                    f"{s['pair']} | {s['direction']} | "
                    f"{s['confidence']} | {s['status']}"
                )

            bot.send_message(
                user_id,
                "\n".join(lines),
                reply_markup=signal_keyboard()
            )
            return

        if text == "🗑 Delete Future Signal":

            set_state(user_id, "DELETE_SIGNAL")

            bot.send_message(
                user_id,
                "🗑 Enter signal ID to delete:",
                reply_markup=back_keyboard()
            )
            return

        # LIVE
        if text == "⚡ Live Session":

            if not has_permission(user_id, "p_live"):
                return

            session = active_live_session()

            if session:
                bot.send_message(
                    user_id,
                    "⚡ Live Session is already active.",
                    reply_markup=kb([
                        ["📌 Pair", "⏰ Time"],
                        ["⬆️ UP", "⬇️ DOWN"],
                        ["📤 Send Live Signal"],
                        ["✏️ Send Live Text"],
                        ["⛔ End Live Mode"],
                        ["🔙 Back"]
                    ])
                )
            else:
                start_live_session(user_id)

                bot.send_message(
                    user_id,
                    get_setting("live_started"),
                    reply_markup=kb([
                        ["📌 Pair", "⏰ Time"],
                        ["⬆️ UP", "⬇️ DOWN"],
                        ["📤 Send Live Signal"],
                        ["✏️ Send Live Text"],
                        ["⛔ End Live Mode"],
                        ["🔙 Back"]
                    ])
                )

            return

        if text == "📌 Pair":

            set_state(user_id, "LIVE_PAIR")

            bot.send_message(
                user_id,
                "📌 Enter pair:",
                reply_markup=back_keyboard()
            )
            return

        if text == "⏰ Time":

            set_state(user_id, "LIVE_TIME")

            bot.send_message(
                user_id,
                "⏰ Enter live signal time:",
                reply_markup=back_keyboard()
            )
            return

        if text in ("⬆️ UP", "⬇️ DOWN"):

            LIVE_DRAFT.setdefault(user_id, {})

            LIVE_DRAFT[user_id]["direction"] = (
                "UP" if text == "⬆️ UP" else "DOWN"
            )

            bot.send_message(
                user_id,
                f"✅ Direction: <b>{LIVE_DRAFT[user_id]['direction']}</b>",
                reply_markup=kb([
                    ["📌 Pair", "⏰ Time"],
                    ["⬆️ UP", "⬇️ DOWN"],
                    ["📤 Send Live Signal"],
                    ["✏️ Send Live Text"],
                    ["⛔ End Live Mode"],
                    ["🔙 Back"]
                ])
            )
            return

        if text == "📤 Send Live Signal":

            draft = LIVE_DRAFT.get(user_id, {})

            if not draft.get("pair"):
                set_state(user_id, "LIVE_PAIR")

                bot.send_message(
                    user_id,
                    "📌 Enter pair first:",
                    reply_markup=back_keyboard()
                )
                return

            if not draft.get("time"):
                set_state(user_id, "LIVE_TIME")

                bot.send_message(
                    user_id,
                    "⏰ Enter time first:",
                    reply_markup=back_keyboard()
                )
                return

            if not draft.get("direction"):
                bot.send_message(
                    user_id,
                    "⬆️ Choose UP or DOWN first.",
                    reply_markup=kb([
                        ["⬆️ UP", "⬇️ DOWN"],
                        ["📤 Send Live Signal"],
                        ["✏️ Send Live Text"],
                        ["⛔ End Live Mode"]
                    ])
                )
                return

            content = (
                f"📅 <b>{now_bd().date()}</b>\n"
                f"💱 <b>{draft['pair']}</b>\n"
                f"⏰ <b>{draft['time']}</b>\n"
                f"{direction_text(draft['direction'])}"
            )

            sent = send_live_to_vips(
                content,
                user_id
            )

            LIVE_DRAFT.pop(user_id, None)

            bot.send_message(
                user_id,
                f"⚡ Live signal sent to <b>{sent}</b> VIP user(s).",
                reply_markup=admin_keyboard()
            )
            return

        if text == "✏️ Send Live Text":

            if not active_live_session():
                start_live_session(user_id)

            set_state(user_id, "LIVE_TEXT")

            bot.send_message(
                user_id,
                "✏️ Enter any live signal/text.\n"
                "It will be sent to VIP users with Live Signal ON.",
                reply_markup=back_keyboard()
            )
            return

        if text == "⛔ End Live Mode":

            end_live_session()
            LIVE_DRAFT.pop(user_id, None)
            clear_state(user_id)

            bot.send_message(
                user_id,
                get_setting("live_ended"),
                reply_markup=admin_keyboard()
            )
            return

        # VIP
        if text == "⭐ VIP Management":

            if not has_permission(user_id, "p_vip"):
                return

            bot.send_message(
                user_id,
                "⭐ <b>VIP Management</b>",
                reply_markup=vip_keyboard()
            )
            return

        if text == "➕ Add VIP":

            set_state(user_id, "VIP_USER")

            bot.send_message(
                user_id,
                "👤 Enter Telegram User ID:",
                reply_markup=back_keyboard()
            )
            return

        if text == "🔄 Renew VIP":

            set_state(user_id, "VIP_USER")

            bot.send_message(
                user_id,
                "👤 Enter Telegram User ID to renew:",
                reply_markup=back_keyboard()
            )
            return

        if text == "❌ Remove VIP":

            set_state(user_id, "REMOVE_VIP")

            bot.send_message(
                user_id,
                "👤 Enter Telegram User ID:",
                reply_markup=back_keyboard()
            )
            return

        if text == "📋 VIP List":

            vip_users = [
                u for u in db_execute(
                    "SELECT telegram_id,first_name,vip_until FROM users",
                    fetchall=True
                )
                if is_vip(u["telegram_id"])
            ]

            if not vip_users:
                output = "📭 No active VIP users."
            else:
                output = "⭐ <b>VIP Users</b>\n\n"

                for u in vip_users:
                    output += (
                        f"<code>{u['telegram_id']}</code> | "
                        f"{u['first_name']}\n"
                        f"Until: {u['vip_until']}\n\n"
                    )

            bot.send_message(
                user_id,
                output,
                reply_markup=vip_keyboard()
            )
            return

        # UID
        if text == "🆔 UID Requests":

            if not has_permission(user_id, "p_users"):
                return

            rows = db_execute(
                """
                SELECT * FROM uid_submissions
                WHERE status='PENDING'
                ORDER BY id
                LIMIT 30
                """,
                fetchall=True
            )

            if not rows:
                bot.send_message(
                    user_id,
                    "📭 No pending UID requests.",
                    reply_markup=admin_keyboard()
                )
                return

            lines = ["🆔 <b>Pending UID Requests</b>\n"]

            for r in rows:
                lines.append(
                    f"Request #{r['id']}\n"
                    f"User: <code>{r['telegram_id']}</code>\n"
                    f"UID: <code>{r['uid']}</code>\n"
                )

            bot.send_message(
                user_id,
                "\n".join(lines),
                reply_markup=kb([
                    ["✅ Approve UID", "❌ Reject UID"],
                    ["🔙 Back", "🏠 Main Menu"]
                ])
            )
            return

        if text == "✅ Approve UID":

            set_state(user_id, "APPROVE_UID")

            bot.send_message(
                user_id,
                "Enter UID Request ID:",
                reply_markup=back_keyboard()
            )
            return

        if text == "❌ Reject UID":

            set_state(user_id, "REJECT_UID")

            bot.send_message(
                user_id,
                "Enter UID Request ID:",
                reply_markup=back_keyboard()
            )
            return

        # WALLET ADMIN
        if text == "💰 Wallet / Withdraw":

            if not has_permission(user_id, "p_wallet"):
                return

            bot.send_message(
                user_id,
                "💰 <b>Wallet / Withdraw</b>",
                reply_markup=wallet_admin_keyboard()
            )
            return

        if text == "💸 Pending Withdrawals":

            rows = db_execute(
                """
                SELECT * FROM withdrawals
                WHERE status='PENDING'
                ORDER BY id
                LIMIT 30
                """,
                fetchall=True
            )

            if not rows:
                output = "📭 No pending withdrawals."
            else:
                lines = ["💸 <b>Pending Withdrawals</b>\n"]

                for r in rows:
                    lines.append(
                        f"#{r['id']} | User: <code>{r['telegram_id']}</code>\n"
                        f"Amount: ${money(r['amount_cents'])}\n"
                        f"Method: {r['method']}\n"
                        f"Account: <code>{r['account']}</code>\n"
                    )

                output = "\n".join(lines)

            bot.send_message(
                user_id,
                output,
                reply_markup=kb([
                    ["✅ Approve Withdrawal"],
                    ["❌ Reject Withdrawal"],
                    ["🔙 Back", "🏠 Main Menu"]
                ])
            )
            return

        if text == "✅ Approve Withdrawal":

            set_state(user_id, "APPROVE_WITHDRAW")

            bot.send_message(
                user_id,
                "Enter withdrawal ID:",
                reply_markup=back_keyboard()
            )
            return

        if text == "❌ Reject Withdrawal":

            set_state(user_id, "REJECT_WITHDRAW")

            bot.send_message(
                user_id,
                "Enter withdrawal ID:",
                reply_markup=back_keyboard()
            )
            return

        if text == "💰 Add Balance":

            set_state(user_id, "ADMIN_BALANCE_USER")

            bot.send_message(
                user_id,
                "Enter Telegram User ID:",
                reply_markup=back_keyboard()
            )
            return

        if text == "➖ Remove Balance":

            set_state(user_id, "ADMIN_REMOVE_USER")

            bot.send_message(
                user_id,
                "Enter Telegram User ID:",
                reply_markup=back_keyboard()
            )
            return

        if text == "⚙️ Withdrawal Settings":

            bot.send_message(
                user_id,
                (
                    "⚙️ <b>Withdrawal Settings</b>\n\n"
                    f"Status: <b>{'ON' if get_setting('withdrawals') == '1' else 'OFF'}</b>\n"
                    f"Minimum: <b>${get_setting('min_withdraw')}</b>\n"
                    f"Hold: <b>{get_setting('withdraw_hold_hours')}h</b>"
                ),
                reply_markup=kb([
                    ["💸 Toggle Withdrawals"],
                    ["💵 Minimum Withdraw"],
                    ["⏳ Hold Hours"],
                    ["🎁 Referral Bonus"],
                    ["🔙 Back"]
                ])
            )
            return

        if text == "💸 Toggle Withdrawals":

            new = "0" if get_setting("withdrawals") == "1" else "1"
            set_setting("withdrawals", new)

            bot.send_message(
                user_id,
                f"💸 Withdrawals: <b>{'ON' if new == '1' else 'OFF'}</b>",
                reply_markup=wallet_admin_keyboard()
            )
            return

        if text == "💵 Minimum Withdraw":

            set_state(user_id, "MIN_WITHDRAW")

            bot.send_message(
                user_id,
                "Enter minimum withdrawal in USD:",
                reply_markup=back_keyboard()
            )
            return

        if text == "🎁 Referral Bonus":

            set_state(user_id, "REFERRAL_BONUS")

            bot.send_message(
                user_id,
                "Enter referral bonus in USD:",
                reply_markup=back_keyboard()
            )
            return

        # BROADCAST
        if text == "📢 Broadcast":

            if not has_permission(user_id, "p_broadcast"):
                return

            set_state(user_id, "BROADCAST_TARGET")

            bot.send_message(
                user_id,
                "Choose broadcast audience:",
                reply_markup=kb([
                    ["👥 All Users"],
                    ["⭐ VIP Users"],
                    ["🔙 Back"]
                ])
            )
            return

        # TEXT EDITOR
        if text == "📝 Bot Text Editor":

            if not has_permission(user_id, "p_text"):
                return

            bot.send_message(
                user_id,
                "📝 <b>Bot Text Editor</b>\n\nChoose a text:",
                reply_markup=text_editor_keyboard()
            )
            return

        if text in TEXT_KEYS:

            key = TEXT_KEYS[text]

            set_state(
                user_id,
                "EDIT_TEXT",
                {"key": key}
            )

            bot.send_message(
                user_id,
                "✏️ Send the new text.\n\n"
                "Supported placeholders for signal template:\n"
                "<code>{date}</code>\n"
                "<code>{time}</code>\n"
                "<code>{pair}</code>\n"
                "<code>{direction}</code>\n"
                "<code>{confidence}</code>\n"
                "<code>{trade_amount}</code>\n"
                "<code>{stage}</code>\n"
                "<code>{remaining_signals}</code>",
                reply_markup=back_keyboard()
            )
            return

        # SETTINGS
        if text == "⚙️ Settings":

            if not has_permission(user_id, "p_settings"):
                return

            bot.send_message(
                user_id,
                "⚙️ <b>Bot Settings</b>",
                reply_markup=kb([
                    ["🛠 Maintenance"],
                    ["📢 Notice"],
                    ["📖 Trading Rules"],
                    ["🔢 Free Signal Limit"],
                    ["🎁 Referral Bonus"],
                    ["🔔 VIP Reminder"],
                    ["🗳 Vote Reveal"],
                    ["🔙 Back", "🏠 Main Menu"]
                ])
            )
            return

        if text == "🛠 Maintenance":

            new = "0" if get_setting("maintenance") == "1" else "1"

            set_setting("maintenance", new)

            bot.send_message(
                user_id,
                f"🛠 Maintenance: <b>{'ON' if new == '1' else 'OFF'}</b>",
                reply_markup=admin_keyboard()
            )
            return

        if text == "📢 Notice":

            set_state(user_id, "NOTICE_INPUT")

            bot.send_message(
                user_id,
                "📢 Enter new notice:",
                reply_markup=back_keyboard()
            )
            return

        if text == "📖 Trading Rules":

            set_state(user_id, "EDIT_TEXT", {"key": "trading_rules"})

            bot.send_message(
                user_id,
                "📖 Enter new trading rules:",
                reply_markup=back_keyboard()
            )
            return

        if text == "🔢 Free Signal Limit":

            set_state(user_id, "FREE_LIMIT")

            bot.send_message(
                user_id,
                "Enter maximum free signals per 2-day cycle:",
                reply_markup=back_keyboard()
            )
            return

        if text == "🗳 Vote Reveal":

            new = "0" if get_setting("vote_reveal") == "1" else "1"
            set_setting("vote_reveal", new)

            bot.send_message(
                user_id,
                f"🗳 Vote reveal: <b>{'ON' if new == '1' else 'OFF'}</b>",
                reply_markup=admin_keyboard()
            )
            return

        # SUB ADMINS
        if text == "👨‍💼 Sub-Admins":

            if not is_admin(user_id):
                return

            bot.send_message(
                user_id,
                "👨‍💼 <b>Sub-Admin Manager</b>",
                reply_markup=kb([
                    ["➕ Add Sub-Admin"],
                    ["📋 Sub-Admin List"],
                    ["❌ Remove Sub-Admin"],
                    ["🔙 Back"]
                ])
            )
            return

        if text == "➕ Add Sub-Admin":

            set_state(user_id, "SUBADMIN_ID")

            bot.send_message(
                user_id,
                "Enter Telegram User ID:",
                reply_markup=back_keyboard()
            )
            return

        if text == "📋 Sub-Admin List":

            rows = db_execute(
                """
                SELECT * FROM admins
                WHERE telegram_id != ?
                """,
                (ADMIN_ID,),
                fetchall=True
            )

            if not rows:
                output = "📭 No sub-admins."
            else:
                output = "👨‍💼 <b>Sub-Admins</b>\n\n"

                for r in rows:
                    output += (
                        f"<code>{r['telegram_id']}</code> | "
                        f"{r['role']}\n"
                    )

            bot.send_message(
                user_id,
                output,
                reply_markup=admin_keyboard()
            )
            return

        if text == "❌ Remove Sub-Admin":

            set_state(user_id, "REMOVE_SUBADMIN")

            bot.send_message(
                user_id,
                "Enter Telegram User ID:",
                reply_markup=back_keyboard()
            )
            return

        # VOTE RESULTS
        if text == "📊 Vote Results":

            if not has_permission(user_id, "p_signals"):
                return

            rows = db_execute(
                """
                SELECT * FROM signals
                ORDER BY id DESC
                LIMIT 10
                """,
                fetchall=True
            )

            if not rows:
                bot.send_message(
                    user_id,
                    "📭 No signals.",
                    reply_markup=admin_keyboard()
                )
                return

            lines = ["📊 <b>Vote Results</b>\n"]

            for s in rows:

                votes = db_execute(
                    """
                    SELECT vote,COUNT(*) AS c
                    FROM signal_votes
                    WHERE signal_id=?
                    GROUP BY vote
                    """,
                    (s["id"],),
                    fetchall=True
                )

                counts = {
                    "UP": 0,
                    "DOWN": 0,
                    "SKIP": 0
                }

                for v in votes:
                    counts[v["vote"]] = v["c"]

                lines.append(
                    f"#{s['id']} {s['pair']} "
                    f"{s['signal_date']} {s['signal_time']}\n"
                    f"👍 UP: {counts['UP']} | "
                    f"👎 DOWN: {counts['DOWN']} | "
                    f"⏭ SKIP: {counts['SKIP']}\n"
                )

            bot.send_message(
                user_id,
                "\n".join(lines),
                reply_markup=admin_keyboard()
            )
            return

        if text == "📋 Signal History":

            rows = db_execute(
                """
                SELECT * FROM signals
                ORDER BY id DESC
                LIMIT 30
                """,
                fetchall=True
            )

            lines = ["📋 <b>Signal History</b>\n"]

            for s in rows:
                lines.append(
                    f"#{s['id']} | {s['signal_date']} "
                    f"{s['signal_time']} | "
                    f"{s['pair']} | "
                    f"{s['direction']} | "
                    f"{s['status']}"
                )

            bot.send_message(
                user_id,
                "\n".join(lines) if rows else "📭 Empty.",
                reply_markup=admin_keyboard()
            )
            return

    # --------------------------------------------------------
    # FALLBACK
    # --------------------------------------------------------

    bot.send_message(
        user_id,
        get_setting("invalid_text"),
        reply_markup=(
            admin_keyboard()
            if is_admin(user_id) or admin_row(user_id)
            else user_keyboard(user_id)
        )
    )


# ============================================================
# SCHEDULER
# ============================================================

def eligible_users_for_signal(signal):

    users = db_execute(
        "SELECT telegram_id FROM users",
        fetchall=True
    )

    result = []

    for u in users:
        user_id = u["telegram_id"]

        if signal["audience"] == "VIP":
            if not is_vip(user_id):
                continue

        elif signal["audience"] == "SELECTED":
            selected = db_execute(
                """
                SELECT 1
                FROM selected_signal_users
                WHERE signal_id=? AND telegram_id=?
                """,
                (signal["id"], user_id),
                fetchone=True
            )

            if not selected:
                continue

        else:
            if not is_vip(user_id):
                if free_remaining(user_id) <= 0:
                    continue

        result.append(user_id)

    return result


def deliver_signal(signal):

    users = eligible_users_for_signal(signal)

    for user_id in users:

        already = db_execute(
            """
            SELECT id FROM signal_access
            WHERE signal_id=? AND telegram_id=?
            """,
            (signal["id"], user_id),
            fetchone=True
        )

        if already:
            continue

        consume = (
            1
            if signal["audience"] == "ALL"
            and not is_vip(user_id)
            else 0
        )

        try:
            user = user_row(user_id)

            if user and not user["notification_on"]:
                continue

            bot.send_message(
                user_id,
                signal_message(signal, user_id)
            )

            db_execute(
                """
                INSERT OR IGNORE INTO signal_access(
                    signal_id,telegram_id,delivered_at,consumed_quota
                )
                VALUES(?,?,?,?)
                """,
                (
                    signal["id"],
                    user_id,
                    now_str(),
                    consume
                )
            )

            process_referral_bonus(user_id)

        except Exception:
            logger.exception(
                "Could not deliver signal %s to %s",
                signal["id"],
                user_id
            )


def signal_scheduler():

    while True:

        try:
            now = now_bd()

            date = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M")

            rows = db_execute(
                """
                SELECT * FROM signals
                WHERE signal_date=?
                  AND signal_time<=?
                  AND status='PENDING'
                ORDER BY signal_date,signal_time,id
                """,
                (date, current_time),
                fetchall=True
            )

            for signal in rows:

                deliver_signal(signal)

                db_execute(
                    """
                    UPDATE signals
                    SET status='SENT',sent_at=?
                    WHERE id=?
                    """,
                    (now_str(), signal["id"])
                )

            check_vip_expiry_reminders()

        except Exception:
            logger.exception("Scheduler error")

        time.sleep(5)


def check_vip_expiry_reminders():

    reminder_days = int(
        get_setting("vip_reminder_days", "3")
    )

    users = db_execute(
        """
        SELECT telegram_id,vip_until
        FROM users
        WHERE vip_until IS NOT NULL
        """,
        fetchall=True
    )

    now = now_bd()

    for u in users:

        try:
            expiry = datetime.fromisoformat(u["vip_until"])

            if expiry <= now:
                continue

            remaining = expiry - now

            if remaining.days > reminder_days:
                continue

            expiry_date = expiry.date().isoformat()
            reminder_date = now.date().isoformat()

            exists = db_execute(
                """
                SELECT 1 FROM vip_reminders
                WHERE telegram_id=?
                  AND expiry_date=?
                  AND reminder_date=?
                """,
                (
                    u["telegram_id"],
                    expiry_date,
                    reminder_date
                ),
                fetchone=True
            )

            if exists:
                continue

            bot.send_message(
                u["telegram_id"],
                f"⚠️ Your VIP expires on <b>{expiry_date}</b>."
            )

            db_execute(
                """
                INSERT OR IGNORE INTO vip_reminders(
                    telegram_id,expiry_date,reminder_date
                )
                VALUES(?,?,?)
                """,
                (
                    u["telegram_id"],
                    expiry_date,
                    reminder_date
                )
            )

        except Exception:
            pass


# ============================================================
# BACKUP
# ============================================================

def backup_database():

    os.makedirs(BACKUP_DIR, exist_ok=True)

    stamp = now_bd().strftime("%Y%m%d_%H%M%S")

    destination = os.path.join(
        BACKUP_DIR,
        f"sm_quatex_{stamp}.db"
    )

    with DB_LOCK:
        source = get_db()
        backup = sqlite3.connect(destination)

        try:
            source.backup(backup)
        finally:
            backup.close()
            source.close()

    files = sorted(
        [
            os.path.join(BACKUP_DIR, x)
            for x in os.listdir(BACKUP_DIR)
            if x.endswith(".db")
        ]
    )

    while len(files) > 10:
        os.remove(files.pop(0))


def backup_scheduler():

    while True:

        try:
            backup_database()
        except Exception:
            logger.exception("Backup error")

        time.sleep(6 * 60 * 60)


# ============================================================
# ERROR HANDLER
# ============================================================

def safe_notify_error(chat_id):

    try:
        bot.send_message(
            chat_id,
            get_setting("error_text")
        )
    except Exception:
        pass


# ============================================================
# RUN
# ============================================================

def start_background_threads():

    scheduler = threading.Thread(
        target=signal_scheduler,
        daemon=True
    )

    scheduler.start()

    backup = threading.Thread(
        target=backup_scheduler,
        daemon=True
    )

    backup.start()


if __name__ == "__main__":

    logger.info("Starting SM QUATEX SURE SHORT...")

    start_background_threads()

    logger.info("Bot polling started.")

    while True:
        try:
            bot.infinity_polling(
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30
            )

        except Exception:
            logger.exception("Polling crashed. Restarting...")
            time.sleep(5)
