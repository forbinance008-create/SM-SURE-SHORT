import os
import re
import sqlite3
import threading
import time
import traceback
import shutil
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import telebot
from telebot import types


# =========================================================
# BASIC CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6470135702"))

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing. Add BOT_TOKEN in Railway Variables."
    )

DB_PATH = os.getenv("DB_PATH", "bot.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")

BD_TZ = ZoneInfo("Asia/Dhaka")

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML"
)

DB_LOCK = threading.RLock()

# Temporary conversation states.
# Important business data is stored in SQLite.
STATE = {}


# =========================================================
# DEFAULT EDITABLE TEXT
# =========================================================

DEFAULTS = {
    "welcome":
        "👋 Welcome, <b>{user_name}</b>!\n\n"
        "🌐 <b>SM QUATEX SURE SHORT</b>\n\n"
        "Choose an option from the buttons below.",

    "maintenance":
        "🛠 <b>Maintenance Mode</b>\n\n"
        "The bot is temporarily unavailable. Please try again later.",

    "free_limit": "4",

    "withdraw_enabled": "1",

    "withdraw_min": "5.00",

    "withdraw_hold": "0",

    "ref_bonus": "0.50",

    "reminder_days": "3",

    "channel_username": "",

    "signal_template":
        "📅 <b>{date}</b>\n\n"
        "💱 <b>{pair}</b>\n"
        "⏰ <b>{time}</b>\n"
        "{arrow} <b>{direction}</b>\n"
        "🎯 Confidence: <b>{confidence}%</b>\n\n"
        "🆔 Signal #{signal_id}",

    "notice":
        "📢 <b>Notice</b>\n\n"
        "No notice has been added yet.",

    "trading_rules":
        "📖 <b>Trading Rules</b>\n\n"
        "1. Check your balance before trading.\n"
        "2. Follow the scheduled signal time.\n"
        "3. Use your own risk settings.\n"
        "4. Record WIN / LOSS correctly.\n"
        "5. Recovery calculations are not guarantees.",

    "help":
        "❓ <b>Help</b>\n\n"
        "📡 Future Signals = scheduled signals.\n"
        "⚡ Live Signals = VIP live-session messages.\n"
        "💰 Money Management = balance, target, loss limit and trade amount tracker.\n"
        "📈 Signal Result = record WIN / LOSS.\n"
        "🗳 Vote Signal = vote on the latest delivered signal.",

    "wallet":
        "💼 <b>Wallet</b>\n\n"
        "Balance: <b>${balance}</b>",

    "vip":
        "👤 <b>VIP / UID</b>\n\n"
        "⭐ VIP: {vip_status}\n"
        "🆔 UID: {uid}",

    "referral":
        "👥 <b>Referral</b>\n\n"
        "Your referral link:\n"
        "{ref_link}\n\n"
        "Successful referrals: {count}\n"
        "Referral bonus: ${bonus}",

    "notifications":
        "🔔 Notifications: <b>{status}</b>",

    "live_vip_only":
        "⭐ <b>Live Signals are VIP-only.</b>",

    "invalid":
        "❌ Invalid input.\n\n"
        "Please use the buttons or enter the requested value.",

    "saved":
        "✅ Saved successfully.",

    "admin_only":
        "⛔ Admin access only.",

    "mm_intro":
        "💰 <b>Money Management</b>\n\n"
        "এটা খুব সহজভাবে আপনার trading money হিসাব রাখবে:\n\n"
        "💵 Balance = আপনার trading balance\n"
        "🎯 Profit Target = দিনে কত USD profit হলে stop করবেন\n"
        "🛑 Loss Limit = দিনে কত USD loss হলে stop করবেন\n"
        "💲 Base Trade = সাধারণ trade amount\n\n"
        "⚡ Quick Setup চাপলে শুধু ৪টা তথ্য দিলেই setup শেষ।"
}


# =========================================================
# MENUS
# =========================================================

USER_MENU = [
    "📡 Future Signals",
    "⚡ Live Signals",
    "💰 Money Management",
    "💼 Wallet",
    "💸 Withdraw",
    "👤 VIP / UID",
    "👥 Referral",
    "📊 Dashboard",
    "🗳 Vote Signal",
    "📈 Signal Result",
    "📜 Signal History",
    "📖 Trading Rules",
    "🔔 Notifications",
    "❓ Help"
]


ADMIN_MENU = [
    "📡 Future Signals",
    "⚡ Live Signals",
    "💰 Money Management",
    "💼 Wallet",
    "💸 Withdraw",
    "👤 VIP / UID",
    "👥 Referral",
    "📊 Dashboard",
    "🗳 Vote Signal",
    "📈 Signal Result",
    "📜 Signal History",
    "📖 Trading Rules",
    "🔔 Notifications",
    "❓ Help",
    "🛠 Admin Panel"
]


ADMIN_PANEL = [
    "➕ Add Future Signals",
    "📋 Future Signal List",
    "⚡ Live Session",
    "📢 Broadcast",
    "⭐ VIP Management",
    "🆔 UID Requests",
    "💸 Withdraw Requests",
    "👥 Sub-admins",
    "⚙️ Bot Settings",
    "📝 Bot Text Editor",
    "📊 Vote Results",
    "📣 Notice",
    "🟢 Maintenance ON",
    "🔴 Maintenance OFF",
    "🔙 Back",
    "🏠 Main Menu"
]


MM_MENU = [
    "⚡ Quick Setup",
    "💵 Set Balance",
    "🎯 Profit Target",
    "🛑 Loss Limit",
    "💲 Base Trade",
    "🔁 M1 Trade",
    "📈 M2 Recovery",
    "🔢 Max Trades/Day",
    "▶️ Start/Stop Trading",
    "📋 MM Status",
    "🔙 Back",
    "🏠 Main Menu"
]


VIP_MENU = [
    "🆔 Set Quotex UID",
    "⭐ VIP Status",
    "🔙 Back",
    "🏠 Main Menu"
]


# =========================================================
# DATABASE
# =========================================================

def db():
    c = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )
    c.row_factory = sqlite3.Row
    return c


def init_db():

    with DB_LOCK:

        c = db()

        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance_cents INTEGER DEFAULT 0,
                vip_until TEXT,
                uid TEXT UNIQUE,
                referred_by INTEGER,
                referral_paid INTEGER DEFAULT 0,
                notify INTEGER DEFAULT 1,
                live_notify INTEGER DEFAULT 1,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_date TEXT,
                signal_time TEXT,
                pair TEXT,
                direction TEXT,
                confidence TEXT,
                audience TEXT DEFAULT 'ALL',
                selected_users TEXT DEFAULT '',
                sent INTEGER DEFAULT 0,
                sent_at TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS signal_access(
                user_id INTEGER,
                signal_id INTEGER,
                delivered_at TEXT,
                quota_used INTEGER DEFAULT 0,
                PRIMARY KEY(user_id, signal_id)
            );

            CREATE TABLE IF NOT EXISTS signal_results(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                signal_id INTEGER,
                result TEXT,
                amount_cents INTEGER,
                created_at TEXT,
                UNIQUE(user_id, signal_id)
            );

            CREATE TABLE IF NOT EXISTS signal_votes(
                user_id INTEGER,
                signal_id INTEGER,
                vote TEXT,
                created_at TEXT,
                PRIMARY KEY(user_id, signal_id)
            );

            CREATE TABLE IF NOT EXISTS uid_requests(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                uid TEXT UNIQUE,
                status TEXT DEFAULT 'PENDING',
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS withdrawals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount_cents INTEGER,
                status TEXT DEFAULT 'PENDING',
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS wallet_tx(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount_cents INTEGER,
                kind TEXT,
                note TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS admins(
                user_id INTEGER PRIMARY KEY,
                role TEXT DEFAULT 'SUBADMIN',
                p_signals INTEGER DEFAULT 0,
                p_users INTEGER DEFAULT 0,
                p_money INTEGER DEFAULT 0,
                p_broadcast INTEGER DEFAULT 0,
                p_settings INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS settings(
                k TEXT PRIMARY KEY,
                v TEXT
            );

            CREATE TABLE IF NOT EXISTS mm_profiles(
                user_id INTEGER PRIMARY KEY,
                balance_cents INTEGER DEFAULT 0,
                profit_target_cents INTEGER DEFAULT 1000,
                loss_limit_cents INTEGER DEFAULT 500,
                base_trade_cents INTEGER DEFAULT 200,
                m1_trade_cents INTEGER DEFAULT 300,
                m2_trade_cents INTEGER DEFAULT 500,
                max_trades INTEGER DEFAULT 10,
                trades_today INTEGER DEFAULT 0,
                daily_pl_cents INTEGER DEFAULT 0,
                day TEXT,
                stage TEXT DEFAULT 'BASE',
                session_loss_cents INTEGER DEFAULT 0,
                recovery_bank_cents INTEGER DEFAULT 0,
                stop_trading INTEGER DEFAULT 0,
                balance_confirmed INTEGER DEFAULT 0,
                payout_pct REAL DEFAULT 80.0
            );

            CREATE TABLE IF NOT EXISTS live_sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT,
                ended_at TEXT,
                status TEXT
            );

            CREATE TABLE IF NOT EXISTS live_signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                pair TEXT,
                signal_time TEXT,
                direction TEXT,
                confidence TEXT,
                message TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS reminders(
                user_id INTEGER,
                reminder_date TEXT,
                kind TEXT,
                PRIMARY KEY(user_id, reminder_date, kind)
            );

            CREATE TABLE IF NOT EXISTS selected_signal_users(
                signal_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY(signal_id, user_id)
            );
            """
        )

        for k, v in DEFAULTS.items():

            c.execute(
                "INSERT OR IGNORE INTO settings(k,v) VALUES(?,?)",
                (k, v)
            )

        c.commit()
        c.close()


# =========================================================
# HELPERS
# =========================================================

def get_setting(key):

    with DB_LOCK:

        c = db()

        r = c.execute(
            "SELECT v FROM settings WHERE k=?",
            (key,)
        ).fetchone()

        c.close()

    return r["v"] if r else DEFAULTS.get(key, "")


def set_setting(key, value):

    with DB_LOCK:

        c = db()

        c.execute(
            """
            INSERT INTO settings(k,v)
            VALUES(?,?)
            ON CONFLICT(k)
            DO UPDATE SET v=excluded.v
            """,
            (key, str(value))
        )

        c.commit()
        c.close()


def money(c):
    return f"{c / 100:.2f}"


def cents(value):

    value = str(value)
    value = value.replace("$", "")
    value = value.replace(",", "")
    value = value.strip()

    return int(round(float(value) * 100))


def now():
    return datetime.now(BD_TZ)


def iso():
    return now().isoformat()


def get_user(uid):

    with DB_LOCK:

        c = db()

        r = c.execute(
            "SELECT * FROM users WHERE id=?",
            (uid,)
        ).fetchone()

        c.close()

    return r


def ensure_user(m):

    uid = m.from_user.id

    with DB_LOCK:

        c = db()

        r = c.execute(
            "SELECT id FROM users WHERE id=?",
            (uid,)
        ).fetchone()

        if not r:

            c.execute(
                """
                INSERT INTO users(
                    id,
                    username,
                    first_name,
                    created_at
                )
                VALUES(?,?,?,?,?)
                """.replace("VALUES(?,?,?,?,?)", "VALUES(?,?,?,?)"),
                (
                    uid,
                    m.from_user.username or "",
                    m.from_user.first_name or "",
                    iso()
                )
            )

        else:

            c.execute(
                """
                UPDATE users
                SET username=?, first_name=?
                WHERE id=?
                """,
                (
                    m.from_user.username or "",
                    m.from_user.first_name or "",
                    uid
                )
            )

        c.execute(
            """
            INSERT OR IGNORE INTO mm_profiles(
                user_id,
                day
            )
            VALUES(?,?)
            """,
            (
                uid,
                now().date().isoformat()
            )
        )

        c.commit()
        c.close()


def is_admin(uid):

    if uid == ADMIN_ID:
        return True

    with DB_LOCK:

        c = db()

        r = c.execute(
            "SELECT 1 FROM admins WHERE user_id=?",
            (uid,)
        ).fetchone()

        c.close()

    return bool(r)


def money_format(c):
    return f"{c / 100:.2f}"


def user_keyboard(admin=False):

    values = ADMIN_MENU if admin else USER_MENU

    return make_keyboard(values, 2)


def make_keyboard(values, width=2):

    k = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=width
    )

    for i in range(0, len(values), width):

        row = values[i:i + width]

        k.row(*row)

    return k


def back_keyboard(values):

    return make_keyboard(
        values + [
            "🔙 Back",
            "🏠 Main Menu"
        ],
        2
    )


def send(uid, text, keyboard=None):

    try:

        bot.send_message(
            uid,
            text,
            reply_markup=keyboard
        )

    except Exception:

        pass


def require_input(uid, action):

    STATE[uid] = {
        "action": action
    }


def clear_state(uid):

    STATE.pop(uid, None)


def vip_active(user):

    if not user:
        return False

    if not user["vip_until"]:
        return False

    try:

        return (
            datetime.fromisoformat(
                user["vip_until"]
            ) > now()
        )

    except Exception:

        return False


# =========================================================
# QUOTA
# =========================================================

def quota_cycle():

    days = (
        date.today() -
        date(1970, 1, 1)
    ).days

    return days // 2


def quota_used(uid):

    cycle_start = (
        date(1970, 1, 1) +
        timedelta(
            days=quota_cycle() * 2
        )
    ).isoformat()

    with DB_LOCK:

        c = db()

        r = c.execute(
            """
            SELECT COUNT(*) AS n
            FROM signal_access
            WHERE user_id=?
            AND quota_used=1
            AND substr(delivered_at,1,10)>=?
            """,
            (
                uid,
                cycle_start
            )
        ).fetchone()

        c.close()

    return r["n"]


def free_remaining(uid):

    user = get_user(uid)

    if vip_active(user):
        return "Unlimited"

    limit = int(
        get_setting("free_limit") or 4
    )

    return str(
        max(
            0,
            limit - quota_used(uid)
        )
    )


# =========================================================
# MONEY MANAGEMENT
# =========================================================

def mm_reset(uid):

    with DB_LOCK:

        c = db()

        row = c.execute(
            """
            SELECT *
            FROM mm_profiles
            WHERE user_id=?
            """,
            (uid,)
        ).fetchone()

        today = now().date().isoformat()

        if not row:

            c.execute(
                """
                INSERT INTO mm_profiles(
                    user_id,
                    day
                )
                VALUES(?,?)
                """,
                (uid, today)
            )

            c.commit()
            c.close()

            return

        if row["day"] != today:

            c.execute(
                """
                UPDATE mm_profiles
                SET
                    day=?,
                    trades_today=0,
                    daily_pl_cents=0,
                    stop_trading=0,
                    balance_confirmed=0,
                    stage='BASE',
                    session_loss_cents=0
                WHERE user_id=?
                """,
                (
                    today,
                    uid
                )
            )

            c.commit()

        c.close()


def mm_row(uid):

    mm_reset(uid)

    with DB_LOCK:

        c = db()

        r = c.execute(
            """
            SELECT *
            FROM mm_profiles
            WHERE user_id=?
            """,
            (uid,)
        ).fetchone()

        c.close()

    return r


def next_trade_amount(uid):

    r = mm_row(uid)

    if r["stage"] == "M1":

        return r["m1_trade_cents"]

    if r["stage"] == "M2":

        needed = (
            r["session_loss_cents"] +
            r["base_trade_cents"]
        )

        payout = max(
            r["payout_pct"] / 100,
            0.01
        )

        calculated = int(
            needed / payout + 0.9999
        )

        return max(
            r["m2_trade_cents"],
            calculated
        )

    if r["recovery_bank_cents"] > 0:

        needed = (
            r["recovery_bank_cents"] +
            r["base_trade_cents"]
        )

        payout = max(
            r["payout_pct"] / 100,
            0.01
        )

        return int(
            needed / payout + 0.9999
        )

    return r["base_trade_cents"]


def mm_status(uid):

    r = mm_row(uid)

    amount = next_trade_amount(uid)

    return (
        "💰 <b>Money Management Status</b>\n\n"

        f"💵 Balance: <b>${money(r['balance_cents'])}</b>\n"

        f"🎯 Daily Profit Target: "
        f"<b>+${money(r['profit_target_cents'])}</b>\n"

        f"🛑 Daily Loss Limit: "
        f"<b>-${money(r['loss_limit_cents'])}</b>\n"

        f"💲 Next Trade Amount: "
        f"<b>${money(amount)}</b>\n"

        f"🔹 Current Stage: <b>{r['stage']}</b>\n"

        f"📈 Today's P/L: "
        f"<b>${money(r['daily_pl_cents'])}</b>\n"

        f"🔢 Trades: "
        f"<b>{r['trades_today']}/{r['max_trades']}</b>\n"

        f"⛔ Stop Trading: "
        f"<b>{'ON' if r['stop_trading'] else 'OFF'}</b>\n\n"

        "ℹ️ <b>Recovery:</b>\n"
        "Base LOSS → M1\n"
        "M1 LOSS → M2\n"
        "WIN → recovery reset"
    )


# =========================================================
# SIGNAL FORMAT
# =========================================================

def format_signal(row):

    direction = row["direction"].upper()

    arrow = (
        "🟢⬆️"
        if direction in ("UP", "BUY")
        else
        "🔴⬇️"
    )

    template = get_setting(
        "signal_template"
    )

    return template.format(
        date=row["signal_date"],
        time=row["signal_time"],
        pair=row["pair"],
        direction=direction,
        confidence=row["confidence"] or "—",
        signal_id=row["id"],
        arrow=arrow,
        trade_amount="",
        stage="",
        remaining_signals=""
    )


# =========================================================
# MAIN MENU
# =========================================================

def main_menu(uid):

    send(
        uid,
        "🏠 <b>Main Menu</b>\n\nChoose an option:",
        user_keyboard(
            is_admin(uid)
        )
    )


def admin_panel(uid):

    send(
        uid,
        "🛠 <b>ADMIN PANEL</b>\n\n"
        "সব Admin কাজ নিচের button দিয়েই করা যাবে:",
        make_keyboard(
            ADMIN_PANEL,
            2
        )
    )


# =========================================================
# /START
# =========================================================

@bot.message_handler(commands=["start"])
def start_handler(message):

    ensure_user(message)

    uid = message.from_user.id

    # Referral
    if message.text and " " in message.text:

        payload = message.text.split(
            " ",
            1
        )[1].strip()

        if payload.startswith("ref_"):

            ref_id = payload[4:]

            if ref_id.isdigit():

                ref_id = int(ref_id)

                if ref_id != uid:

                    with DB_LOCK:

                        c = db()

                        u = c.execute(
                            """
                            SELECT referred_by
                            FROM users
                            WHERE id=?
                            """,
                            (uid,)
                        ).fetchone()

                        if (
                            u and
                            not u["referred_by"]
                        ):

                            c.execute(
                                """
                                UPDATE users
                                SET referred_by=?
                                WHERE id=?
                                """,
                                (
                                    ref_id,
                                    uid
                                )
                            )

                            c.commit()

                        c.close()

    if (
        get_setting("maintenance") == "1"
        and not is_admin(uid)
    ):

        send(
            uid,
            get_setting("maintenance")
        )

        return

    user = get_user(uid)

    text = get_setting(
        "welcome"
    ).format(
        user_name=(
            message.from_user.first_name
            or "there"
        ),
        balance=money(
            user["balance_cents"]
        )
    )

    send(
        uid,
        text,
        user_keyboard(
            is_admin(uid)
        )
    )


# =========================================================
# /ADMIN
# =========================================================

@bot.message_handler(commands=["admin"])
def admin_command(message):

    ensure_user(message)

    uid = message.from_user.id

    if is_admin(uid):

        admin_panel(uid)

    else:

        send(
            uid,
            get_setting("admin_only")
        )


# =========================================================
# USER FEATURES
# =========================================================

def future_menu(message):

    uid = message.from_user.id

    send(
        uid,
        "📡 <b>Future Signals</b>\n\n"
        f"📊 Free signals remaining: "
        f"<b>{free_remaining(uid)}</b>\n\n"
        "Choose:",
        make_keyboard(
            [
                "📅 Today's Signals",
                "📜 Signal History",
                "🔙 Back",
                "🏠 Main Menu"
            ],
            2
        )
    )


def todays_signals(uid):

    today = now().strftime("%d-%m-%Y")

    with DB_LOCK:

        c = db()

        rows = c.execute(
            """
            SELECT *
            FROM signals
            WHERE signal_date=?
            ORDER BY signal_time
            """,
            (today,)
        ).fetchall()

        c.close()

    if not rows:

        send(
            uid,
            "📅 <b>Today's Signals</b>\n\n"
            "📭 No signal available for today.",
            user_keyboard(
                is_admin(uid)
            )
        )

        return

    text = "📅 <b>Today's Signals</b>\n\n"

    for row in rows:

        arrow = (
            "🟢⬆️"
            if row["direction"] == "UP"
            else
            "🔴⬇️"
        )

        text += (
            f"#{row['id']} "
            f"⏰ {row['signal_time']} "
            f"💱 {row['pair']} "
            f"{arrow} "
            f"{row['direction']} "
            f"🎯 {row['confidence']}%\n"
        )

    send(
        uid,
        text,
        user_keyboard(
            is_admin(uid)
        )
    )


def live_menu_user(message):

    uid = message.from_user.id

    user = get_user(uid)

    if not vip_active(user):

        send(
            uid,
            get_setting("live_vip_only"),
            user_keyboard(
                is_admin(uid)
            )
        )

        return

    send(
        uid,
        "⚡ <b>Live Signals</b>\n\n"
        "VIP live signals will appear here.\n\n"
        "You can control your Live notification:",
        make_keyboard(
            [
                "🔔 Live ON",
                "🔕 Live OFF",
                "🔙 Back",
                "🏠 Main Menu"
            ],
            2
        )
    )


def wallet_menu(message):

    uid = message.from_user.id

    user = get_user(uid)

    send(
        uid,
        get_setting(
            "wallet"
        ).format(
            balance=money(
                user["balance_cents"]
            )
        ),
        make_keyboard(
            [
                "📜 Wallet History",
                "🔙 Back",
                "🏠 Main Menu"
            ],
            2
        )
    )


def withdraw_menu(message):

    uid = message.from_user.id

    if get_setting(
        "withdraw_enabled"
    ) != "1":

        send(
            uid,
            "💸 <b>Withdraw is currently OFF.</b>",
            user_keyboard(
                is_admin(uid)
            )
        )

        return

    send(
        uid,
        "💸 <b>Withdraw</b>\n\n"
        f"Minimum withdrawal: "
        f"<b>${get_setting('withdraw_min')}</b>\n\n"
        "Press the button and enter your USD amount.",
        make_keyboard(
            [
                "💵 Withdraw Amount",
                "📜 Withdraw History",
                "🔙 Back",
                "🏠 Main Menu"
            ],
            2
        )
    )


def vip_menu(message):

    uid = message.from_user.id

    user = get_user(uid)

    status = (
        "Active until "
        + user["vip_until"][:10]
        if vip_active(user)
        else
        "Not Active"
    )

    send(
        uid,
        get_setting("vip").format(
            vip_status=status,
            uid=user["uid"] or "Not set"
        ),
        back_keyboard(VIP_MENU)
    )


def referral_menu(message):

    uid = message.from_user.id

    user = get_user(uid)

    with DB_LOCK:

        c = db()

        r = c.execute(
            """
            SELECT COUNT(*) AS n
            FROM users
            WHERE referred_by=?
            """,
            (uid,)
        ).fetchone()

        c.close()

    try:
        username = bot.get_me().username
    except Exception:
        username = "YOUR_BOT"

    link = (
        f"https://t.me/{username}"
        f"?start=ref_{uid}"
    )

    send(
        uid,
        get_setting(
            "referral"
        ).format(
            ref_link=link,
            count=r["n"],
            bonus=get_setting("ref_bonus")
        ),
        user_keyboard(
            is_admin(uid)
        )
    )


def dashboard(uid):

    user = get_user(uid)

    with DB_LOCK:

        c = db()

        win = c.execute(
            """
            SELECT COUNT(*) AS n
            FROM signal_results
            WHERE user_id=?
            AND result='WIN'
            """,
            (uid,)
        ).fetchone()["n"]

        loss = c.execute(
            """
            SELECT COUNT(*) AS n
            FROM signal_results
            WHERE user_id=?
            AND result='LOSS'
            """,
            (uid,)
        ).fetchone()["n"]

        c.close()

    return (
        "📊 <b>Dashboard</b>\n\n"
        f"💰 Wallet: ${money(user['balance_cents'])}\n"
        f"⭐ VIP: {'Active' if vip_active(user) else 'No'}\n"
        f"📈 WIN: {win}\n"
        f"📉 LOSS: {loss}\n"
        f"📡 Free signals: {free_remaining(uid)}"
    )


def history_menu(message):

    uid = message.from_user.id

    with DB_LOCK:

        c = db()

        rows = c.execute(
            """
            SELECT
                s.*,
                x.result
            FROM signal_access a
            JOIN signals s
                ON s.id=a.signal_id
            LEFT JOIN signal_results x
                ON x.signal_id=s.id
                AND x.user_id=?
            WHERE a.user_id=?
            ORDER BY s.id DESC
            LIMIT 15
            """,
            (
                uid,
                uid
            )
        ).fetchall()

        c.close()

    if not rows:

        send(
            uid,
            "📜 No signal history yet.",
            user_keyboard(
                is_admin(uid)
            )
        )

        return

    text = "📜 <b>Signal History</b>\n\n"

    for r in rows:

        text += (
            f"#{r['id']} "
            f"{r['signal_date']} "
            f"{r['signal_time']} "
            f"{r['pair']} "
            f"{r['direction']} — "
            f"{r['result'] or 'Pending'}\n"
        )

    send(
        uid,
        text,
        user_keyboard(
            is_admin(uid)
        )
    )


def notification_menu(message):

    uid = message.from_user.id

    user = get_user(uid)

    send(
        uid,
        get_setting(
            "notifications"
        ).format(
            status=(
                "ON"
                if user["notify"]
                else
                "OFF"
            )
        ),
        make_keyboard(
            [
                "🔔 ON",
                "🔕 OFF",
                "🔙 Back",
                "🏠 Main Menu"
            ],
            2
        )
    )


# =========================================================
# MONEY MANAGEMENT MENU
# =========================================================

def money_management_menu(message):

    uid = message.from_user.id

    send(
        uid,
        get_setting("mm_intro")
        + "\n\n"
        "👉 নতুন হলে শুধু "
        "<b>⚡ Quick Setup</b> চাপুন.",
        back_keyboard(MM_MENU)
    )


def mm_action(message, text):

    uid = message.from_user.id

    if text == "⚡ Quick Setup":

        STATE[uid] = {
            "action": "mm_quick_balance",
            "quick": {}
        }

        send(
            uid,
            "⚡ <b>Quick Setup — Step 1/4</b>\n\n"
            "আপনার current trading balance কত USD?\n\n"
            "Example: <b>100</b>"
        )

        return

    if text == "💵 Set Balance":

        require_input(
            uid,
            "mm_balance"
        )

        send(
            uid,
            "💵 আপনার current trading balance USD-তে দিন.\n\n"
            "Example: <b>100</b>"
        )

        return

    if text == "🎯 Profit Target":

        require_input(
            uid,
            "mm_target"
        )

        send(
            uid,
            "🎯 প্রতিদিন কত USD profit হলে trading stop করতে চান?\n\n"
            "Example: <b>10</b>"
        )

        return

    if text == "🛑 Loss Limit":

        require_input(
            uid,
            "mm_loss"
        )

        send(
            uid,
            "🛑 প্রতিদিন কত USD loss হলে trading stop করতে চান?\n\n"
            "Example: <b>5</b>"
        )

        return

    if text == "💲 Base Trade":

        require_input(
            uid,
            "mm_base"
        )

        send(
            uid,
            "💲 আপনার normal/base trade amount কত USD?\n\n"
            "Example: <b>2</b>"
        )

        return

    if text == "🔁 M1 Trade":

        require_input(
            uid,
            "mm_m1"
        )

        send(
            uid,
            "🔁 Base loss হলে M1 কত USD হবে?\n\n"
            "Example: <b>3</b>"
        )

        return

    if text == "📈 M2 Recovery":

        require_input(
            uid,
            "mm_m2"
        )

        send(
            uid,
            "📈 M2-এর minimum amount USD-তে দিন.\n\n"
            "Session-এর accumulated loss অনুযায়ী bot recovery amount calculate করবে."
        )

        return

    if text == "🔢 Max Trades/Day":

        require_input(
            uid,
            "mm_max"
        )

        send(
            uid,
            "🔢 দিনে সর্বোচ্চ কতটি trade করতে চান?\n\n"
            "Example: <b>10</b>"
        )

        return

    if text == "▶️ Start/Stop Trading":

        with DB_LOCK:

            c = db()

            r = c.execute(
                """
                SELECT stop_trading
                FROM mm_profiles
                WHERE user_id=?
                """,
                (uid,)
            ).fetchone()

            new_value = (
                0
                if r["stop_trading"]
                else
                1
            )

            c.execute(
                """
                UPDATE mm_profiles
                SET stop_trading=?
                WHERE user_id=?
                """,
                (
                    new_value,
                    uid
                )
            )

            c.commit()
            c.close()

        send(
            uid,
            mm_status(uid),
            back_keyboard(MM_MENU)
        )

        return

    if text == "📋 MM Status":

        send(
            uid,
            mm_status(uid),
            back_keyboard(MM_MENU)
        )


# =========================================================
# VIP / UID
# =========================================================

def vip_action(message, text):

    uid = message.from_user.id

    if text == "🆔 Set Quotex UID":

        require_input(
            uid,
            "uid"
        )

        send(
            uid,
            "🆔 আপনার Quotex UID পাঠান.\n\n"
            "একটি UID শুধুমাত্র একটি Telegram account-এর সাথে link করা যাবে."
        )

        return

    if text == "⭐ VIP Status":

        vip_menu(message)


# =========================================================
# VOTE
# =========================================================

def vote_menu(message):

    uid = message.from_user.id

    with DB_LOCK:

        c = db()

        row = c.execute(
            """
            SELECT s.*
            FROM signals s
            JOIN signal_access a
                ON a.signal_id=s.id
            WHERE a.user_id=?
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (uid,)
        ).fetchone()

        c.close()

    if not row:

        send(
            uid,
            "🗳 No delivered signal to vote on.",
            user_keyboard(
                is_admin(uid)
            )
        )

        return

    with DB_LOCK:

        c = db()

        voted = c.execute(
            """
            SELECT 1
            FROM signal_votes
            WHERE user_id=?
            AND signal_id=?
            """,
            (
                uid,
                row["id"]
            )
        ).fetchone()

        c.close()

    if voted:

        send(
            uid,
            "✅ You already voted on the latest signal.",
            user_keyboard(
                is_admin(uid)
            )
        )

        return

    STATE[uid] = {
        "action": "vote",
        "signal_id": row["id"]
    }

    send(
        uid,
        "🗳 <b>Vote Signal</b>\n\n"
        "আপনার prediction দিন:",
        make_keyboard(
            [
                "👍 UP",
                "👎 DOWN",
                "⏭ SKIP",
                "🔙 Back"
            ],
            2
        )
    )


def save_vote(message, text):

    uid = message.from_user.id

    state = STATE.get(
        uid,
        {}
    )

    signal_id = state.get(
        "signal_id"
    )

    if not signal_id:
        main_menu(uid)
        return

    vote = {
        "👍 UP": "UP",
        "👎 DOWN": "DOWN",
        "⏭ SKIP": "SKIP"
    }.get(text)

    if not vote:
        return

    with DB_LOCK:

        c = db()

        c.execute(
            """
            INSERT OR IGNORE INTO signal_votes(
                user_id,
                signal_id,
                vote,
                created_at
            )
            VALUES(?,?,?,?)
            """,
            (
                uid,
                signal_id,
                vote,
                iso()
            )
        )

        c.commit()
        c.close()

    clear_state(uid)

    send(
        uid,
        "✅ Vote recorded.\n\n"
        "📊 Vote result is visible to admin.",
        user_keyboard(
            is_admin(uid)
        )
    )


# =========================================================
# SIGNAL RESULT
# =========================================================

def result_menu(message):

    uid = message.from_user.id

    with DB_LOCK:

        c = db()

        row = c.execute(
            """
            SELECT s.*
            FROM signals s
            JOIN signal_access a
                ON a.signal_id=s.id
            LEFT JOIN signal_results x
                ON x.signal_id=s.id
                AND x.user_id=?
            WHERE a.user_id=?
            AND x.id IS NULL
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (
                uid,
                uid
            )
        ).fetchone()

        c.close()

    if not row:

        send(
            uid,
            "📈 No pending signal result.",
            user_keyboard(
                is_admin(uid)
            )
        )

        return

    mm = mm_row(uid)

    amount = next_trade_amount(uid)

    STATE[uid] = {
        "action": "result",
        "signal_id": row["id"]
    }

    send(
        uid,
        f"📈 <b>Signal #{row['id']}</b>\n\n"
        f"💱 Pair: {row['pair']}\n"
        f"⏰ Time: {row['signal_time']}\n"
        f"🔹 Stage: {mm['stage']}\n"
        f"💲 Trade Amount: ${money(amount)}\n\n"
        "Trade result কী হয়েছে?",
        make_keyboard(
            [
                "✅ WIN",
                "❌ LOSS",
                "⏭ SKIP",
                "🔙 Back"
            ],
            2
        )
    )


def save_result(message, text):

    uid = message.from_user.id

    state = STATE.get(
        uid,
        {}
    )

    signal_id = state.get(
        "signal_id"
    )

    if not signal_id:
        main_menu(uid)
        return

    result = {
        "✅ WIN": "WIN",
        "❌ LOSS": "LOSS",
        "⏭ SKIP": "SKIP"
    }.get(text)

    if not result:
        return

    mm = mm_row(uid)

    amount = next_trade_amount(uid)

    with DB_LOCK:

        c = db()

        c.execute(
            """
            INSERT OR IGNORE INTO signal_results(
                user_id,
                signal_id,
                result,
                amount_cents,
                created_at
            )
            VALUES(?,?,?,?,?)
            """,
            (
                uid,
                signal_id,
                result,
                amount,
                iso()
            )
        )

        if result in ("WIN", "LOSS"):

            delta = (
                amount
                if result == "WIN"
                else
                -amount
            )

            c.execute(
                """
                UPDATE mm_profiles
                SET
                    balance_cents=balance_cents+?,
                    daily_pl_cents=daily_pl_cents+?,
                    trades_today=trades_today+1
                WHERE user_id=?
                """,
                (
                    delta,
                    delta,
                    uid
                )
            )

            if result == "LOSS":

                c.execute(
                    """
                    UPDATE mm_profiles
                    SET
                        session_loss_cents=
                            session_loss_cents+?,
                        recovery_bank_cents=
                            recovery_bank_cents+?,
                        stage=
                            CASE
                                WHEN stage='BASE'
                                    THEN 'M1'
                                WHEN stage='M1'
                                    THEN 'M2'
                                ELSE
                                    'BASE'
                            END
                    WHERE user_id=?
                    """,
                    (
                        amount,
                        amount,
                        uid
                    )
                )

            else:

                c.execute(
                    """
                    UPDATE mm_profiles
                    SET
                        stage='BASE',
                        session_loss_cents=0,
                        recovery_bank_cents=0
                    WHERE user_id=?
                    """,
                    (uid,)
                )

            r = c.execute(
                """
                SELECT *
                FROM mm_profiles
                WHERE user_id=?
                """,
                (uid,)
            ).fetchone()

            if (
                r["daily_pl_cents"]
                >= r["profit_target_cents"]
            ):

                c.execute(
                    """
                    UPDATE mm_profiles
                    SET stop_trading=1
                    WHERE user_id=?
                    """,
                    (uid,)
                )

            elif (
                r["daily_pl_cents"]
                <= -r["loss_limit_cents"]
            ):

                c.execute(
                    """
                    UPDATE mm_profiles
                    SET stop_trading=1
                    WHERE user_id=?
                    """,
                    (uid,)
                )

            elif (
                r["trades_today"]
                >= r["max_trades"]
            ):

                c.execute(
                    """
                    UPDATE mm_profiles
                    SET stop_trading=1
                    WHERE user_id=?
                    """,
                    (uid,)
                )

        c.commit()
        c.close()

    clear_state(uid)

    send(
        uid,
        f"✅ <b>{result}</b> saved.\n\n"
        + mm_status(uid),
        user_keyboard(
            is_admin(uid)
        )
    )


# =========================================================
# NOTIFICATIONS
# =========================================================

def set_notification(message, text):

    uid = message.from_user.id

    value = (
        1
        if text == "🔔 ON"
        else
        0
    )

    with DB_LOCK:

        c = db()

        c.execute(
            """
            UPDATE users
            SET notify=?
            WHERE id=?
            """,
            (
                value,
                uid
            )
        )

        c.commit()
        c.close()

    notification_menu(message)


# =========================================================
# ADMIN - FUTURE SIGNAL PARSER
# =========================================================

def parse_signals(text, default_date):

    result = []

    for raw in text.splitlines():

        line = raw.strip()

        if not line:
            continue

        # Format:
        # 12:30 EURUSD UP 95

        match = re.match(
            r"""
            ^
            (\d{1,2}:\d{2})
            [|,\s]+
            ([^|,\s]+)
            [|,\s]+
            (UP|DOWN|BUY|SELL)
            (?:[|,\s]+(\d+(?:\.\d+)?))?
            $
            """,
            line,
            re.I | re.X
        )

        if not match:
            continue

        tm, pair, direction, confidence = (
            match.groups()
        )

        try:

            datetime.strptime(
                f"{default_date} {tm}",
                "%d-%m-%Y %H:%M"
            )

        except Exception:

            continue

        direction = direction.upper()

        if direction == "BUY":
            direction = "UP"

        if direction == "SELL":
            direction = "DOWN"

        result.append(
            (
                default_date,
                tm,
                pair.upper(),
                direction,
                confidence or ""
            )
        )

    return result


# =========================================================
# SIGNAL DELIVERY
# =========================================================

def audience_allowed(signal, uid):

    user = get_user(uid)

    if signal["audience"] == "VIP":

        return vip_active(user)

    if signal["audience"] == "SELECTED":

        with DB_LOCK:

            c = db()

            r = c.execute(
                """
                SELECT 1
                FROM selected_signal_users
                WHERE signal_id=?
                AND user_id=?
                """,
                (
                    signal["id"],
                    uid
                )
            ).fetchone()

            c.close()

        return bool(r)

    return True


def deliver_signal(signal):

    with DB_LOCK:

        c = db()

        users = c.execute(
            "SELECT * FROM users"
        ).fetchall()

        c.close()

    for user in users:

        uid = user["id"]

        if not user["notify"]:
            continue

        if not audience_allowed(
            signal,
            uid
        ):
            continue

        with DB_LOCK:

            c = db()

            already = c.execute(
                """
                SELECT 1
                FROM signal_access
                WHERE user_id=?
                AND signal_id=?
                """,
                (
                    uid,
                    signal["id"]
                )
            ).fetchone()

            if already:

                c.close()

                continue

            quota_used_value = 0

            if (
                signal["audience"] == "ALL"
                and not vip_active(user)
            ):

                limit = int(
                    get_setting("free_limit")
                    or 4
                )

                if quota_used(uid) >= limit:

                    c.close()

                    continue

                quota_used_value = 1

            c.execute(
                """
                INSERT INTO signal_access(
                    user_id,
                    signal_id,
                    delivered_at,
                    quota_used
                )
                VALUES(?,?,?,?)
                """,
                (
                    uid,
                    signal["id"],
                    iso(),
                    quota_used_value
                )
            )

            c.commit()
            c.close()

        send(
            uid,
            format_signal(signal),
            user_keyboard(
                is_admin(uid)
            )
        )

        # Referral bonus after first actual delivered signal.
        if (
            quota_used_value == 1
            and user["referred_by"]
            and not user["referral_paid"]
        ):

            bonus = cents(
                get_setting("ref_bonus")
                or "0"
            )

            if bonus > 0:

                with DB_LOCK:

                    c = db()

                    c.execute(
                        """
                        UPDATE users
                        SET balance_cents=
                            balance_cents+?
                        WHERE id=?
                        """,
                        (
                            bonus,
                            user["referred_by"]
                        )
                    )

                    c.execute(
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
                            user["referred_by"],
                            bonus,
                            "REFERRAL",
                            "Referral bonus",
                            iso()
                        )
                    )

                    c.execute(
                        """
                        UPDATE users
                        SET referral_paid=1
                        WHERE id=?
                        """,
                        (uid,)
                    )

                    c.commit()
                    c.close()


# =========================================================
# ADMIN PANEL
# =========================================================

def admin_action(message, text):

    uid = message.from_user.id

    # ---------------------------------------------
    # ADD FUTURE SIGNALS
    # ---------------------------------------------

    if text == "➕ Add Future Signals":

        require_input(
            uid,
            "future_date"
        )

        send(
            uid,
            "📅 <b>Future Signal Date</b>\n\n"
            "একবার date দিন:\n\n"
            "<b>DD-MM-YYYY</b>\n\n"
            "Example:\n"
            "<b>21-09-2026</b>"
        )

        return

    # ---------------------------------------------
    # FUTURE LIST
    # ---------------------------------------------

    if text == "📋 Future Signal List":

        with DB_LOCK:

            c = db()

            rows = c.execute(
                """
                SELECT *
                FROM signals
                WHERE sent=0
                ORDER BY signal_date, signal_time
                LIMIT 50
                """
            ).fetchall()

            c.close()

        if not rows:

            send(
                uid,
                "📋 <b>Future Signal List</b>\n\n"
                "No upcoming signals.",
                make_keyboard(
                    ADMIN_PANEL,
                    2
                )
            )

            return

        body = "📋 <b>Upcoming Signals</b>\n\n"

        for r in rows:

            body += (
                f"#{r['id']} | "
                f"{r['signal_date']} | "
                f"{r['signal_time']} | "
                f"{r['pair']} | "
                f"{r['direction']} | "
                f"{r['confidence']}%\n"
            )

        send(
            uid,
            body,
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    # ---------------------------------------------
    # LIVE SESSION
    # ---------------------------------------------

    if text == "⚡ Live Session":

        start_live_session(uid)

        return

    # ---------------------------------------------
    # BROADCAST
    # ---------------------------------------------

    if text == "📢 Broadcast":

        require_input(
            uid,
            "broadcast"
        )

        send(
            uid,
            "📢 আপনার broadcast message পাঠান."
        )

        return

    # ---------------------------------------------
    # VIP
    # ---------------------------------------------

    if text == "⭐ VIP Management":

        send(
            uid,
            "⭐ <b>VIP Management</b>\n\n"
            "Choose:",
            make_keyboard(
                [
                    "➕ Add/Renew VIP",
                    "📋 VIP List",
                    "🔙 Back",
                    "🏠 Main Menu"
                ],
                2
            )
        )

        return

    if text == "➕ Add/Renew VIP":

        require_input(
            uid,
            "vip_user"
        )

        send(
            uid,
            "⭐ যে user-কে VIP দিতে চান তার Telegram ID দিন."
        )

        return

    if text == "📋 VIP List":

        with DB_LOCK:

            c = db()

            rows = c.execute(
                """
                SELECT id,uid,vip_until
                FROM users
                WHERE vip_until IS NOT NULL
                ORDER BY vip_until DESC
                LIMIT 50
                """
            ).fetchall()

            c.close()

        if not rows:

            body = "⭐ VIP List\n\nEmpty."

        else:

            body = "⭐ <b>VIP List</b>\n\n"

            for r in rows:

                body += (
                    f"👤 {r['id']} | "
                    f"{r['vip_until'][:10]} | "
                    f"UID: {r['uid'] or '-'}\n"
                )

        send(
            uid,
            body,
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    # ---------------------------------------------
    # UID REQUEST
    # ---------------------------------------------

    if text == "🆔 UID Requests":

        uid_requests_admin(uid)

        return

    # ---------------------------------------------
    # WITHDRAW REQUEST
    # ---------------------------------------------

    if text == "💸 Withdraw Requests":

        withdraw_requests_admin(uid)

        return

    # ---------------------------------------------
    # SUB ADMIN
    # ---------------------------------------------

    if text == "👥 Sub-admins":

        require_input(
            uid,
            "subadmin_id"
        )

        send(
            uid,
            "👥 Sub-admin-এর Telegram numeric ID দিন."
        )

        return

    # ---------------------------------------------
    # BOT SETTINGS
    # ---------------------------------------------

    if text == "⚙️ Bot Settings":

        settings_menu(uid)

        return

    # ---------------------------------------------
    # TEXT EDITOR
    # ---------------------------------------------

    if text == "📝 Bot Text Editor":

        text_editor(uid)

        return

    # ---------------------------------------------
    # VOTE RESULTS
    # ---------------------------------------------

    if text == "📊 Vote Results":

        vote_results_admin(uid)

        return

    # ---------------------------------------------
    # NOTICE
    # ---------------------------------------------

    if text == "📣 Notice":

        require_input(
            uid,
            "notice"
        )

        send(
            uid,
            "📣 নতুন notice text পাঠান."
        )

        return

    # ---------------------------------------------
    # MAINTENANCE
    # ---------------------------------------------

    if text == "🟢 Maintenance ON":

        set_setting(
            "maintenance",
            "1"
        )

        send(
            uid,
            "🟢 Maintenance Mode ON.",
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    if text == "🔴 Maintenance OFF":

        set_setting(
            "maintenance",
            "0"
        )

        send(
            uid,
            "🔴 Maintenance Mode OFF.",
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    # ---------------------------------------------
    # SETTINGS SUBMENU
    # ---------------------------------------------

    if text == "🔢 Free Limit":

        require_input(
            uid,
            "set_free_limit"
        )

        send(
            uid,
            "🔢 2-day cycle-এ non-VIP user কতটি free signal পাবে?\n\n"
            "Example: 4"
        )

        return

    if text == "💸 Withdraw ON/OFF":

        current = get_setting(
            "withdraw_enabled"
        )

        set_setting(
            "withdraw_enabled",
            "0" if current == "1" else "1"
        )

        settings_menu(uid)

        return

    if text == "💵 Minimum Withdraw":

        require_input(
            uid,
            "set_withdraw_min"
        )

        send(
            uid,
            "💵 Minimum withdrawal USD amount দিন."
        )

        return

    if text == "👥 Referral Bonus":

        require_input(
            uid,
            "set_ref_bonus"
        )

        send(
            uid,
            "👥 Referral bonus USD amount দিন."
        )

        return


# =========================================================
# ADMIN SETTINGS
# =========================================================

def settings_menu(uid):

    send(
        uid,
        "⚙️ <b>Bot Settings</b>\n\n"
        f"📊 Free Limit: {get_setting('free_limit')}\n"
        f"💸 Withdraw: {get_setting('withdraw_enabled')}\n"
        f"💵 Minimum Withdraw: ${get_setting('withdraw_min')}\n"
        f"👥 Referral Bonus: ${get_setting('ref_bonus')}",
        make_keyboard(
            [
                "🔢 Free Limit",
                "💸 Withdraw ON/OFF",
                "💵 Minimum Withdraw",
                "👥 Referral Bonus",
                "🔙 Back",
                "🏠 Main Menu"
            ],
            2
        )
    )


# =========================================================
# BOT TEXT EDITOR
# =========================================================

def text_editor(uid):

    keys = [
        "welcome",
        "maintenance",
        "signal_template",
        "notice",
        "trading_rules",
        "help",
        "mm_intro",
        "wallet",
        "vip",
        "referral",
        "notifications",
        "live_vip_only",
        "invalid",
        "saved",
        "admin_only"
    ]

    labels = [
        f"📝 {x}"
        for x in keys
    ]

    STATE[uid] = {
        "action": "text_choose",
        "keys": keys
    }

    send(
        uid,
        "📝 <b>Bot Text Editor</b>\n\n"
        "যে text পরিবর্তন করতে চান সেই button চাপুন.",
        make_keyboard(
            labels + [
                "🔙 Back",
                "🏠 Main Menu"
            ],
            2
        )
    )


# =========================================================
# UID ADMIN
# =========================================================

def uid_requests_admin(uid):

    with DB_LOCK:

        c = db()

        rows = c.execute(
            """
            SELECT *
            FROM uid_requests
            WHERE status='PENDING'
            ORDER BY id
            """
        ).fetchall()

        c.close()

    if not rows:

        send(
            uid,
            "🆔 No pending UID requests.",
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    row = rows[0]

    STATE[uid] = {
        "action": "uid_decision",
        "req": row["id"]
    }

    send(
        uid,
        f"🆔 <b>UID Request #{row['id']}</b>\n\n"
        f"👤 User: {row['user_id']}\n"
        f"🆔 UID: {row['uid']}\n\n"
        "Choose:",
        make_keyboard(
            [
                "✅ Approve UID",
                "❌ Reject UID",
                "🔙 Back"
            ],
            2
        )
    )


# =========================================================
# WITHDRAW ADMIN
# =========================================================

def withdraw_requests_admin(uid):

    with DB_LOCK:

        c = db()

        rows = c.execute(
            """
            SELECT *
            FROM withdrawals
            WHERE status='PENDING'
            ORDER BY id
            """
        ).fetchall()

        c.close()

    if not rows:

        send(
            uid,
            "💸 No pending withdrawal requests.",
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    row = rows[0]

    STATE[uid] = {
        "action": "withdraw_decision",
        "req": row["id"]
    }

    send(
        uid,
        f"💸 <b>Withdrawal #{row['id']}</b>\n\n"
        f"👤 User: {row['user_id']}\n"
        f"💵 Amount: ${money(row['amount_cents'])}\n\n"
        "Choose:",
        make_keyboard(
            [
                "✅ Approve Withdraw",
                "❌ Reject Withdraw",
                "🔙 Back"
            ],
            2
        )
    )


# =========================================================
# VOTE ADMIN
# =========================================================

def vote_results_admin(uid):

    with DB_LOCK:

        c = db()

        rows = c.execute(
            """
            SELECT *
            FROM signals
            ORDER BY id DESC
            LIMIT 15
            """
        ).fetchall()

        c.close()

    if not rows:

        send(
            uid,
            "📊 No signals yet.",
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    text = "📊 <b>Vote Results</b>\n\n"

    for row in rows:

        with DB_LOCK:

            c = db()

            votes = c.execute(
                """
                SELECT vote, COUNT(*) AS n
                FROM signal_votes
                WHERE signal_id=?
                GROUP BY vote
                """,
                (row["id"],)
            ).fetchall()

            c.close()

        parts = []

        for v in votes:

            parts.append(
                f"{v['vote']}={v['n']}"
            )

        text += (
            f"#{row['id']} "
            f"{row['pair']} "
            f"{row['direction']} : "
            + (
                ", ".join(parts)
                if parts
                else
                "No votes"
            )
            + "\n"
        )

    send(
        uid,
        text,
        make_keyboard(
            ADMIN_PANEL,
            2
        )
    )


# =========================================================
# LIVE SESSION
# =========================================================

def start_live_session(uid):

    with DB_LOCK:

        c = db()

        c.execute(
            """
            UPDATE live_sessions
            SET
                status='ENDED',
                ended_at=?
            WHERE status='ACTIVE'
            """,
            (iso(),)
        )

        c.execute(
            """
            INSERT INTO live_sessions(
                started_at,
                status
            )
            VALUES(?,'ACTIVE')
            """,
            (iso(),)
        )

        c.commit()
        c.close()

    STATE[uid] = {
        "action": "live_menu"
    }

    send(
        uid,
        "⚡ <b>LIVE SESSION STARTED</b>\n\n"
        "VIP users with Live ON will receive live messages.\n\n"
        "You can:\n"
        "📌 set Pair\n"
        "⏰ set Time\n"
        "⬆️ / ⬇️ Direction\n"
        "🎯 Confidence\n"
        "📤 Send Live Signal\n"
        "✏️ Send Live Text",
        make_keyboard(
            [
                "📌 Pair",
                "⏰ Time",
                "⬆️ UP",
                "⬇️ DOWN",
                "🎯 Confidence",
                "📤 Send Live Signal",
                "✏️ Send Live Text",
                "⛔ End Live Mode",
                "🏠 Main Menu"
            ],
            2
        )
    )


def live_broadcast(uid, text):

    with DB_LOCK:

        c = db()

        users = c.execute(
            "SELECT * FROM users"
        ).fetchall()

        session = c.execute(
            """
            SELECT id
            FROM live_sessions
            WHERE status='ACTIVE'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

        c.close()

    for user in users:

        if (
            vip_active(user)
            and user["live_notify"]
        ):

            send(
                user["id"],
                text
            )

    if session:

        with DB_LOCK:

            c = db()

            c.execute(
                """
                INSERT INTO live_signals(
                    session_id,
                    message,
                    created_at
                )
                VALUES(?,?,?)
                """,
                (
                    session["id"],
                    text,
                    iso()
                )
            )

            c.commit()
            c.close()


# =========================================================
# SCHEDULER
# =========================================================

def scheduler():

    last_backup = 0

    while True:

        try:

            current = now()

            today = current.strftime(
                "%d-%m-%Y"
            )

            current_time = current.strftime(
                "%H:%M"
            )

            with DB_LOCK:

                c = db()

                rows = c.execute(
                    """
                    SELECT *
                    FROM signals
                    WHERE sent=0
                    AND signal_date=?
                    AND signal_time<=?
                    ORDER BY signal_time
                    """,
                    (
                        today,
                        current_time
                    )
                ).fetchall()

                for row in rows:

                    deliver_signal(row)

                    c.execute(
                        """
                        UPDATE signals
                        SET
                            sent=1,
                            sent_at=?
                        WHERE id=?
                        """,
                        (
                            iso(),
                            row["id"]
                        )
                    )

                c.commit()
                c.close()

            if (
                time.time() -
                last_backup
                > 21600
            ):

                backup_db()

                last_backup = time.time()

        except Exception:

            traceback.print_exc()

        time.sleep(3)


# =========================================================
# DATABASE BACKUP
# =========================================================

def backup_db():

    try:

        os.makedirs(
            BACKUP_DIR,
            exist_ok=True
        )

        filename = (
            "bot_"
            + now().strftime(
                "%Y%m%d_%H%M%S"
            )
            + ".db"
        )

        destination = os.path.join(
            BACKUP_DIR,
            filename
        )

        shutil.copy2(
            DB_PATH,
            destination
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
            ],
            key=os.path.getmtime,
            reverse=True
        )

        for old in files[10:]:

            try:
                os.remove(old)
            except Exception:
                pass

    except Exception:

        traceback.print_exc()


# =========================================================
# STATE HANDLER
# =========================================================

def handle_state(message, action, text):

    uid = message.from_user.id

    # -----------------------------------------------------
    # MONEY MANAGEMENT QUICK SETUP
    # -----------------------------------------------------

    if action == "mm_quick_balance":

        try:

            STATE[uid]["quick"]["balance"] = cents(text)

            STATE[uid]["action"] = (
                "mm_quick_target"
            )

            send(
                uid,
                "⚡ <b>Quick Setup — Step 2/4</b>\n\n"
                "Daily Profit Target কত USD?\n\n"
                "Example: <b>10</b>"
            )

        except Exception:

            send(
                uid,
                "❌ Valid USD amount দিন.\nExample: 100"
            )

        return

    if action == "mm_quick_target":

        try:

            STATE[uid]["quick"]["target"] = cents(text)

            STATE[uid]["action"] = (
                "mm_quick_loss"
            )

            send(
                uid,
                "⚡ <b>Quick Setup — Step 3/4</b>\n\n"
                "Daily Loss Limit কত USD?\n\n"
                "Example: <b>5</b>"
            )

        except Exception:

            send(
                uid,
                "❌ Valid USD amount দিন."
            )

        return

    if action == "mm_quick_loss":

        try:

            STATE[uid]["quick"]["loss"] = cents(text)

            STATE[uid]["action"] = (
                "mm_quick_base"
            )

            send(
                uid,
                "⚡ <b>Quick Setup — Step 4/4</b>\n\n"
                "Normal/Base Trade কত USD?\n\n"
                "Example: <b>2</b>"
            )

        except Exception:

            send(
                uid,
                "❌ Valid USD amount দিন."
            )

        return

    if action == "mm_quick_base":

        try:

            q = STATE[uid]["quick"]

            q["base"] = cents(text)

            with DB_LOCK:

                c = db()

                c.execute(
                    """
                    UPDATE mm_profiles
                    SET
                        balance_cents=?,
                        profit_target_cents=?,
                        loss_limit_cents=?,
                        base_trade_cents=?,
                        balance_confirmed=1
                    WHERE user_id=?
                    """,
                    (
                        q["balance"],
                        q["target"],
                        q["loss"],
                        q["base"],
                        uid
                    )
                )

                c.commit()
                c.close()

            clear_state(uid)

            send(
                uid,
                "✅ <b>Money Management Setup Complete</b>\n\n"
                + mm_status(uid),
                back_keyboard(MM_MENU)
            )

        except Exception:

            send(
                uid,
                "❌ Valid USD amount দিন."
            )

        return

    # -----------------------------------------------------
    # NORMAL MM INPUTS
    # -----------------------------------------------------

    if action.startswith("mm_"):

        try:

            if action == "mm_max":

                value = int(text)

                if value < 1:
                    raise ValueError

                column = "max_trades"

            else:

                value = cents(text)

                if value < 0:
                    raise ValueError

                column = {
                    "mm_balance":
                        "balance_cents",

                    "mm_target":
                        "profit_target_cents",

                    "mm_loss":
                        "loss_limit_cents",

                    "mm_base":
                        "base_trade_cents",

                    "mm_m1":
                        "m1_trade_cents",

                    "mm_m2":
                        "m2_trade_cents"
                }[action]

            with DB_LOCK:

                c = db()

                c.execute(
                    f"""
                    UPDATE mm_profiles
                    SET {column}=?
                    WHERE user_id=?
                    """,
                    (
                        value,
                        uid
                    )
                )

                if action == "mm_balance":

                    c.execute(
                        """
                        UPDATE mm_profiles
                        SET balance_confirmed=1
                        WHERE user_id=?
                        """,
                        (uid,)
                    )

                c.commit()
                c.close()

            clear_state(uid)

            send(
                uid,
                "✅ Saved.\n\n"
                + mm_status(uid),
                back_keyboard(MM_MENU)
            )

        except Exception:

            send(
                uid,
                "❌ Valid number দিন.\n\n"
                "Example: 100 অথবা 2.50"
            )

        return

    # -----------------------------------------------------
    # UID
    # -----------------------------------------------------

    if action == "uid":

        uid_value = text.strip()

        if (
            len(uid_value) < 3
            or len(uid_value) > 40
        ):

            send(
                uid,
                "❌ Invalid UID."
            )

            return

        with DB_LOCK:

            c = db()

            duplicate = c.execute(
                """
                SELECT id
                FROM users
                WHERE uid=?
                AND id!=?
                """,
                (
                    uid_value,
                    uid
                )
            ).fetchone()

            pending = c.execute(
                """
                SELECT id
                FROM uid_requests
                WHERE uid=?
                AND status='PENDING'
                AND user_id!=?
                """,
                (
                    uid_value,
                    uid
                )
            ).fetchone()

            if duplicate or pending:

                c.close()

                send(
                    uid,
                    "❌ এই Quotex UID অন্য account-এর সাথে already linked/pending."
                )

                return

            c.execute(
                """
                INSERT INTO uid_requests(
                    user_id,
                    uid,
                    created_at
                )
                VALUES(?,?,?)
                """,
                (
                    uid,
                    uid_value,
                    iso()
                )
            )

            c.commit()
            c.close()

        clear_state(uid)

        send(
            uid,
            "✅ UID submitted for admin approval.",
            user_keyboard(
                is_admin(uid)
            )
        )

        send(
            ADMIN_ID,
            "🆔 <b>New UID Request</b>\n\n"
            f"👤 User: {uid}\n"
            f"🆔 UID: {uid_value}\n\n"
            "Go to Admin Panel → UID Requests."
        )

        return

    # -----------------------------------------------------
    # WITHDRAW
    # -----------------------------------------------------

    if action == "withdraw":

        try:

            amount = cents(text)

        except Exception:

            send(
                uid,
                "❌ Valid USD amount দিন."
            )

            return

        minimum = cents(
            get_setting(
                "withdraw_min"
            )
        )

        user = get_user(uid)

        if amount < minimum:

            send(
                uid,
                f"❌ Minimum withdrawal "
                f"is ${money(minimum)}."
            )

            return

        if amount > user["balance_cents"]:

            send(
                uid,
                "❌ Insufficient wallet balance."
            )

            return

        with DB_LOCK:

            c = db()

            c.execute(
                """
                UPDATE users
                SET balance_cents=
                    balance_cents-?
                WHERE id=?
                """,
                (
                    amount,
                    uid
                )
            )

            c.execute(
                """
                INSERT INTO withdrawals(
                    user_id,
                    amount_cents,
                    created_at
                )
                VALUES(?,?,?)
                """,
                (
                    uid,
                    amount,
                    iso()
                )
            )

            c.execute(
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
                    uid,
                    -amount,
                    "WITHDRAW_HOLD",
                    "Withdrawal request",
                    iso()
                )
            )

            c.commit()
            c.close()

        clear_state(uid)

        send(
            uid,
            f"✅ Withdrawal request created.\n\n"
            f"💵 Amount: ${money(amount)}",
            user_keyboard(
                is_admin(uid)
            )
        )

        send(
            ADMIN_ID,
            "💸 <b>New Withdrawal</b>\n\n"
            f"👤 User: {uid}\n"
            f"💵 Amount: ${money(amount)}\n\n"
            "Go to Admin Panel → Withdraw Requests."
        )

        return

    # -----------------------------------------------------
    # FUTURE SIGNAL DATE
    # -----------------------------------------------------

    if action == "future_date":

        if not re.match(
            r"^\d{1,2}[-/]\d{1,2}[-/]\d{4}$",
            text
        ):

            send(
                uid,
                "❌ Date format must be:\n"
                "<b>DD-MM-YYYY</b>"
            )

            return

        parts = re.split(
            r"[-/]",
            text
        )

        normalized_date = (
            f"{int(parts[0]):02d}-"
            f"{int(parts[1]):02d}-"
            f"{int(parts[2]):04d}"
        )

        try:

            datetime.strptime(
                normalized_date,
                "%d-%m-%Y"
            )

        except Exception:

            send(
                uid,
                "❌ Invalid date."
            )

            return

        STATE[uid] = {
            "action": "future_audience",
            "date": normalized_date
        }

        send(
            uid,
            "🎯 <b>Who should receive these signals?</b>",
            make_keyboard(
                [
                    "🌐 ALL",
                    "⭐ VIP",
                    "🎯 SELECTED",
                    "🔙 Back"
                ],
                2
            )
        )

        return

    # -----------------------------------------------------
    # FUTURE SIGNAL AUDIENCE
    # -----------------------------------------------------

    if action == "future_audience":

        audience = {
            "🌐 ALL": "ALL",
            "⭐ VIP": "VIP",
            "🎯 SELECTED": "SELECTED"
        }.get(text)

        if not audience:

            send(
                uid,
                "Choose one of the buttons."
            )

            return

        date_value = STATE[uid]["date"]

        STATE[uid] = {
            "action": "future_bulk",
            "date": date_value,
            "audience": audience
        }

        send(
            uid,
            "📋 <b>এখন সব Future Signal একসাথে paste করুন.</b>\n\n"
            "Example:\n\n"
            "<code>12:30 EURUSD UP 95</code>\n"
            "<code>12:35 GBPUSD DOWN 97</code>\n"
            "<code>12:40 USDJPY UP 96</code>\n\n"
            "প্রতিটি signal নতুন line-এ থাকবে."
        )

        return

    # -----------------------------------------------------
    # FUTURE BULK
    # -----------------------------------------------------

    if action == "future_bulk":

        default_date = STATE[uid]["date"]
        audience = STATE[uid]["audience"]

        rows = parse_signals(
            text,
            default_date
        )

        if not rows:

            send(
                uid,
                "❌ কোনো valid signal পাওয়া যায়নি.\n\n"
                "Correct example:\n"
                "<code>12:30 EURUSD UP 95</code>\n"
                "<code>12:35 GBPUSD DOWN 97</code>"
            )

            return

        with DB_LOCK:

            c = db()

            for row in rows:

                c.execute(
                    """
                    INSERT INTO signals(
                        signal_date,
                        signal_time,
                        pair,
                        direction,
                        confidence,
                        audience,
                        created_at
                    )
                    VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        audience,
                        iso()
                    )
                )

            c.commit()
            c.close()

        clear_state(uid)

        send(
            uid,
            f"✅ <b>{len(rows)} Future Signals added.</b>\n\n"
            "📅 Date: "
            f"{default_date}\n"
            "🎯 Audience: "
            f"{audience}\n\n"
            "⏰ প্রতিটি signal তার নিজের Bangladesh time অনুযায়ী automatically send হবে.",
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    # -----------------------------------------------------
    # TEXT EDITOR
    # -----------------------------------------------------

    if action == "text_choose":

        keys = STATE[uid].get(
            "keys",
            []
        )

        key = (
            text[2:]
            if text.startswith("📝 ")
            else ""
        )

        if key not in keys:

            send(
                uid,
                "Choose a text button."
            )

            return

        STATE[uid] = {
            "action": "edit_text",
            "key": key
        }

        send(
            uid,
            f"📝 <b>Editing:</b> {key}\n\n"
            "Current text:\n\n"
            f"{get_setting(key)}\n\n"
            "এখন নতুন text পাঠান."
        )

        return

    if action == "edit_text":

        key = STATE[uid]["key"]

        set_setting(
            key,
            text
        )

        clear_state(uid)

        send(
            uid,
            f"✅ <b>{key}</b> updated.",
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    # -----------------------------------------------------
    # NOTICE
    # -----------------------------------------------------

    if action == "notice":

        set_setting(
            "notice",
            text
        )

        clear_state(uid)

        send(
            uid,
            "✅ Notice updated.",
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    # -----------------------------------------------------
    # BROADCAST
    # -----------------------------------------------------

    if action == "broadcast":

        STATE[uid] = {
            "action": "broadcast_target",
            "body": text
        }

        send(
            uid,
            "📢 Broadcast audience choose করুন:",
            make_keyboard(
                [
                    "🌐 ALL",
                    "⭐ VIP",
                    "🔙 Back"
                ],
                2
            )
        )

        return

    if action == "broadcast_target":

        body = STATE[uid].get(
            "body",
            ""
        )

        if text not in (
            "🌐 ALL",
            "⭐ VIP"
        ):

            send(
                uid,
                "Choose an audience button."
            )

            return

        target_vip = (
            text == "⭐ VIP"
        )

        with DB_LOCK:

            c = db()

            users = c.execute(
                "SELECT * FROM users"
            ).fetchall()

            c.close()

        count = 0

        for user in users:

            if (
                target_vip
                and not vip_active(user)
            ):
                continue

            send(
                user["id"],
                body
            )

            count += 1

        clear_state(uid)

        send(
            uid,
            f"✅ Broadcast sent to "
            f"<b>{count}</b> users.",
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    # -----------------------------------------------------
    # VIP
    # -----------------------------------------------------

    if action == "vip_user":

        if not text.isdigit():

            send(
                uid,
                "❌ Enter numeric Telegram ID."
            )

            return

        target = int(text)

        if not get_user(target):

            send(
                uid,
                "❌ User not found."
            )

            return

        STATE[uid] = {
            "action": "vip_days",
            "target": target
        }

        send(
            uid,
            "⭐ কত দিনের VIP দিতে চান?\n\n"
            "Example: <b>20</b>"
        )

        return

    if action == "vip_days":

        try:

            days = int(text)

            if days < 1:
                raise ValueError

        except Exception:

            send(
                uid,
                "❌ Enter whole days."
            )

            return

        target = STATE[uid]["target"]

        until = (
            now() +
            timedelta(days=days)
        ).isoformat()

        with DB_LOCK:

            c = db()

            c.execute(
                """
                UPDATE users
                SET vip_until=?
                WHERE id=?
                """,
                (
                    until,
                    target
                )
            )

            c.commit()
            c.close()

        clear_state(uid)

        send(
            uid,
            "✅ VIP updated.",
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        send(
            target,
            f"⭐ <b>VIP activated for {days} days.</b>",
            user_keyboard(False)
        )

        return

    # -----------------------------------------------------
    # UID DECISION
    # -----------------------------------------------------

    if action == "uid_decision":

        req_id = STATE[uid]["req"]

        approve = (
            text == "✅ Approve UID"
        )

        with DB_LOCK:

            c = db()

            request = c.execute(
                """
                SELECT *
                FROM uid_requests
                WHERE id=?
                """,
                (req_id,)
            ).fetchone()

            if not request:

                c.close()

                clear_state(uid)

                send(
                    uid,
                    "❌ Request not found."
                )

                return

            target_uid = request["user_id"]

            if approve:

                duplicate = c.execute(
                    """
                    SELECT id
                    FROM users
                    WHERE uid=?
                    AND id!=?
                    """,
                    (
                        request["uid"],
                        target_uid
                    )
                ).fetchone()

                if duplicate:

                    c.execute(
                        """
                        UPDATE uid_requests
                        SET status='REJECTED'
                        WHERE id=?
                        """,
                        (req_id,)
                    )

                    result = (
                        "❌ UID duplicate; rejected."
                    )

                else:

                    c.execute(
                        """
                        UPDATE uid_requests
                        SET status='APPROVED'
                        WHERE id=?
                        """,
                        (req_id,)
                    )

                    c.execute(
                        """
                        UPDATE users
                        SET uid=?
                        WHERE id=?
                        """,
                        (
                            request["uid"],
                            target_uid
                        )
                    )

                    result = (
                        "✅ UID approved."
                    )

            else:

                c.execute(
                    """
                    UPDATE uid_requests
                    SET status='REJECTED'
                    WHERE id=?
                    """,
                    (req_id,)
                )

                result = (
                    "❌ UID rejected."
                )

            c.commit()
            c.close()

        clear_state(uid)

        send(
            uid,
            result,
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        send(
            target_uid,
            result,
            user_keyboard(False)
        )

        return

    # -----------------------------------------------------
    # WITHDRAW DECISION
    # -----------------------------------------------------

    if action == "withdraw_decision":

        req_id = STATE[uid]["req"]

        approve = (
            text == "✅ Approve Withdraw"
        )

        with DB_LOCK:

            c = db()

            request = c.execute(
                """
                SELECT *
                FROM withdrawals
                WHERE id=?
                """,
                (req_id,)
            ).fetchone()

            if not request:

                c.close()

                clear_state(uid)

                send(
                    uid,
                    "❌ Request not found."
                )

                return

            target_uid = request["user_id"]

            if approve:

                c.execute(
                    """
                    UPDATE withdrawals
                    SET status='APPROVED'
                    WHERE id=?
                    """,
                    (req_id,)
                )

                result = (
                    "✅ Withdrawal approved."
                )

            else:

                c.execute(
                    """
                    UPDATE withdrawals
                    SET status='REJECTED'
                    WHERE id=?
                    """,
                    (req_id,)
                )

                c.execute(
                    """
                    UPDATE users
                    SET balance_cents=
                        balance_cents+?
                    WHERE id=?
                    """,
                    (
                        request["amount_cents"],
                        target_uid
                    )
                )

                c.execute(
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
                        target_uid,
                        request["amount_cents"],
                        "WITHDRAW_REFUND",
                        "Rejected withdrawal refund",
                        iso()
                    )
                )

                result = (
                    "❌ Withdrawal rejected and refunded."
                )

            c.commit()
            c.close()

        clear_state(uid)

        send(
            uid,
            result,
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        send(
            target_uid,
            result,
            user_keyboard(False)
        )

        return

    # -----------------------------------------------------
    # SUB ADMIN
    # -----------------------------------------------------

    if action == "subadmin_id":

        if not text.isdigit():

            send(
                uid,
                "❌ Enter Telegram numeric ID."
            )

            return

        STATE[uid] = {
            "action": "subadmin_perms",
            "target": int(text)
        }

        send(
            uid,
            "👥 Permissions লিখুন.\n\n"
            "Example:\n"
            "<code>signals,users,money,broadcast,settings</code>\n\n"
            "সব permission দিতে:\n"
            "<code>ALL</code>"
        )

        return

    if action == "subadmin_perms":

        target = STATE[uid]["target"]

        values = [
            "signals",
            "users",
            "money",
            "broadcast",
            "settings"
        ]

        entered = (
            text.lower()
            .replace(" ", "")
            .split(",")
        )

        all_permission = (
            "all" in entered
        )

        flags = [
            int(
                all_permission
                or x in entered
            )
            for x in values
        ]

        with DB_LOCK:

            c = db()

            c.execute(
                """
                INSERT INTO admins(
                    user_id,
                    role,
                    p_signals,
                    p_users,
                    p_money,
                    p_broadcast,
                    p_settings
                )
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(user_id)
                DO UPDATE SET
                    p_signals=excluded.p_signals,
                    p_users=excluded.p_users,
                    p_money=excluded.p_money,
                    p_broadcast=excluded.p_broadcast,
                    p_settings=excluded.p_settings
                """,
                (
                    target,
                    "SUBADMIN",
                    *flags
                )
            )

            c.commit()
            c.close()

        clear_state(uid)

        send(
            uid,
            "✅ Sub-admin saved.",
            make_keyboard(
                ADMIN_PANEL,
                2
            )
        )

        return

    # -----------------------------------------------------
    # SETTINGS
    # -----------------------------------------------------

    if action == "set_free_limit":

        try:

            value = int(text)

            if value < 0:
                raise ValueError

            set_setting(
                "free_limit",
                value
            )

            clear_state(uid)

            settings_menu(uid)

        except Exception:

            send(
                uid,
                "❌ Enter a whole number."
            )

        return

    if action == "set_withdraw_min":

        try:

            cents(text)

            set_setting(
                "withdraw_min",
                text
            )

            clear_state(uid)

            settings_menu(uid)

        except Exception:

            send(
                uid,
                "❌ Enter valid amount."
            )

        return

    if action == "set_ref_bonus":

        try:

            cents(text)

            set_setting(
                "ref_bonus",
                text
            )

            clear_state(uid)

            settings_menu(uid)

        except Exception:

            send(
                uid,
                "❌ Enter valid amount."
            )

        return

    # -----------------------------------------------------
    # LIVE MENU
    # -----------------------------------------------------

    if action == "live_pair":

        STATE[uid]["pair"] = text.upper()

        STATE[uid]["action"] = (
            "live_menu"
        )

        send(
            uid,
            f"📌 Pair saved: "
            f"<b>{text.upper()}</b>",
            make_keyboard(
                [
                    "📌 Pair",
                    "⏰ Time",
                    "⬆️ UP",
                    "⬇️ DOWN",
                    "🎯 Confidence",
                    "📤 Send Live Signal",
                    "✏️ Send Live Text",
                    "⛔ End Live Mode",
                    "🏠 Main Menu"
                ],
                2
            )
        )

        return

    if action == "live_time":

        STATE[uid]["time"] = text

        STATE[uid]["action"] = (
            "live_menu"
        )

        send(
            uid,
            f"⏰ Time saved: "
            f"<b>{text}</b>",
            make_keyboard(
                [
                    "📌 Pair",
                    "⏰ Time",
                    "⬆️ UP",
                    "⬇️ DOWN",
                    "🎯 Confidence",
                    "📤 Send Live Signal",
                    "✏️ Send Live Text",
                    "⛔ End Live Mode",
                    "🏠 Main Menu"
                ],
                2
            )
        )

        return

    if action == "live_confidence":

        STATE[uid]["confidence"] = text

        STATE[uid]["action"] = (
            "live_menu"
        )

        send(
            uid,
            f"🎯 Confidence saved: "
            f"<b>{text}%</b>",
            make_keyboard(
                [
                    "📌 Pair",
                    "⏰ Time",
                    "⬆️ UP",
                    "⬇️ DOWN",
                    "🎯 Confidence",
                    "📤 Send Live Signal",
                    "✏️ Send Live Text",
                    "⛔ End Live Mode",
                    "🏠 Main Menu"
                ],
                2
            )
        )

        return

    if action == "live_text":

        live_broadcast(
            uid,
            text
        )

        STATE[uid]["action"] = (
            "live_menu"
        )

        send(
            uid,
            "✅ Live message sent."
        )

        return


# =========================================================
# MAIN TEXT ROUTER
# =========================================================

@bot.message_handler(
    content_types=["text"]
)
def router(message):

    ensure_user(message)

    uid = message.from_user.id

    text = (
        message.text or ""
    ).strip()

    if text in (
        "/start",
        "/admin"
    ):
        return

    # Maintenance
    if (
        get_setting("maintenance") == "1"
        and not is_admin(uid)
    ):

        send(
            uid,
            get_setting("maintenance")
        )

        return

    # State first.
    state = STATE.get(
        uid,
        {}
    )

    action = state.get(
        "action"
    )

    if action:

        # Vote
        if action == "vote":

            save_vote(
                message,
                text
            )

            return

        # Result
        if action == "result":

            save_result(
                message,
                text
            )

            return

        # Live menu buttons
        if action == "live_menu":

            if text == "📌 Pair":

                STATE[uid]["action"] = (
                    "live_pair"
                )

                send(
                    uid,
                    "📌 Pair লিখুন.\nExample: EURUSD"
                )

                return

            if text == "⏰ Time":

                STATE[uid]["action"] = (
                    "live_time"
                )

                send(
                    uid,
                    "⏰ Time লিখুন.\nExample: 13:30"
                )

                return

            if text in (
                "⬆️ UP",
                "⬇️ DOWN"
            ):

                STATE[uid]["direction"] = (
                    "UP"
                    if text == "⬆️ UP"
                    else
                    "DOWN"
                )

                send(
                    uid,
                    "✅ Direction saved."
                )

                return

            if text == "🎯 Confidence":

                STATE[uid]["action"] = (
                    "live_confidence"
                )

                send(
                    uid,
                    "🎯 Confidence লিখুন.\nExample: 95"
                )

                return

            if text == "✏️ Send Live Text":

                STATE[uid]["action"] = (
                    "live_text"
                )

                send(
                    uid,
                    "✏️ Live message পাঠান."
                )

                return

            if text == "📤 Send Live Signal":

                st = STATE[uid]

                pair = st.get(
                    "pair"
                )

                direction = st.get(
                    "direction"
                )

                signal_time = st.get(
                    "time",
                    "NOW"
                )

                confidence = st.get(
                    "confidence",
                    ""
                )

                if not pair:

                    send(
                        uid,
                        "❌ আগে Pair দিন."
                    )

                    return

                if not direction:

                    send(
                        uid,
                        "❌ আগে UP অথবা DOWN select করুন."
                    )

                    return

                arrow = (
                    "🟢⬆️"
                    if direction == "UP"
                    else
                    "🔴⬇️"
                )

                live_message = (
                    "⚡ <b>LIVE SIGNAL</b>\n\n"
                    f"💱 <b>{pair}</b>\n"
                    f"⏰ <b>{signal_time}</b>\n"
                    f"{arrow} <b>{direction}</b>\n"
                    f"🎯 Confidence: "
                    f"<b>{confidence or '—'}%</b>"
                )

                live_broadcast(
                    uid,
                    live_message
                )

                send(
                    uid,
                    "✅ Live Signal sent."
                )

                return

            if text == "⛔ End Live Mode":

                with DB_LOCK:

                    c = db()

                    c.execute(
                        """
                        UPDATE live_sessions
                        SET
                            status='ENDED',
                            ended_at=?
                        WHERE status='ACTIVE'
                        """,
                        (iso(),)
                    )

                    c.commit()
                    c.close()

                clear_state(uid)

                send(
                    uid,
                    "⛔ Live Session ended.",
                    make_keyboard(
                        ADMIN_PANEL,
                        2
                    )
                )

                return

        # Text editor
        if action == "text_choose":

            keys = state.get(
                "keys",
                []
            )

            key = (
                text[2:]
                if text.startswith("📝 ")
                else ""
            )

            if key in keys:

                STATE[uid] = {
                    "action": "edit_text",
                    "key": key
                }

                send(
                    uid,
                    f"📝 <b>Editing:</b> {key}\n\n"
                    "Current text:\n\n"
                    f"{get_setting(key)}\n\n"
                    "নতুন text পাঠান."
                )

            return

        # Everything else
        handle_state(
            message,
            action,
            text
        )

        return

    # -----------------------------------------------------
    # NAVIGATION
    # -----------------------------------------------------

    if text == "🏠 Main Menu":

        main_menu(uid)

        return

    if text == "🔙 Back":

        main_menu(uid)

        return

    # -----------------------------------------------------
    # USER MENU
    # -----------------------------------------------------

    if text == "📡 Future Signals":

        future_menu(message)

        return

    if text == "📅 Today's Signals":

        todays_signals(uid)

        return

    if text == "⚡ Live Signals":

        live_menu_user(message)

        return

    if text == "💰 Money Management":

        money_management_menu(message)

        return

    if text in MM_MENU:

        mm_action(
            message,
            text
        )

        return

    if text == "💼 Wallet":

        wallet_menu(message)

        return

    if text == "💸 Withdraw":

        withdraw_menu(message)

        return

    if text == "💵 Withdraw Amount":

        require_input(
            uid,
            "withdraw"
        )

        send(
            uid,
            "💵 কত USD withdraw করতে চান?\n\n"
            "Example: <b>10</b>"
        )

        return

    if text == "👤 VIP / UID":

        vip_menu(message)

        return

    if text in VIP_MENU:

        vip_action(
            message,
            text
        )

        return

    if text == "👥 Referral":

        referral_menu(message)

        return

    if text == "📊 Dashboard":

        send(
            uid,
            dashboard(uid),
            user_keyboard(
                is_admin(uid)
            )
        )

        return

    if text == "🗳 Vote Signal":

        vote_menu(message)

        return

    if text == "📈 Signal Result":

        result_menu(message)

        return

    if text == "📜 Signal History":

        history_menu(message)

        return

    if text == "📖 Trading Rules":

        send(
            uid,
            get_setting(
                "trading_rules"
            ),
            user_keyboard(
                is_admin(uid)
            )
        )

        return

    if text == "🔔 Notifications":

        notification_menu(message)

        return

    if text == "🔔 ON":

        set_notification(
            message,
            text
        )

        return

    if text == "🔕 OFF":

        set_notification(
            message,
            text
        )

        return

    if text == "🔔 Live ON":

        with DB_LOCK:

            c = db()

            c.execute(
                """
                UPDATE users
                SET live_notify=1
                WHERE id=?
                """,
                (uid,)
            )

            c.commit()
            c.close()

        live_menu_user(message)

        return

    if text == "🔕 Live OFF":

        with DB_LOCK:

            c = db()

            c.execute(
                """
                UPDATE users
                SET live_notify=0
                WHERE id=?
                """,
                (uid,)
            )

            c.commit()
            c.close()

        live_menu_user(message)

        return

    if text == "❓ Help":

        send(
            uid,
            get_setting(
                "help"
            ),
            user_keyboard(
                is_admin(uid)
            )
        )

        return

    # -----------------------------------------------------
    # ADMIN
    # -----------------------------------------------------

    if (
        is_admin(uid)
        and text == "🛠 Admin Panel"
    ):

        admin_panel(uid)

        return

    if (
        is_admin(uid)
        and text in ADMIN_PANEL
    ):

        if text == "🔙 Back":

            main_menu(uid)

        elif text == "🏠 Main Menu":

            main_menu(uid)

        else:

            admin_action(
                message,
                text
            )

        return

    # Admin submenu buttons
    if is_admin(uid):

        admin_submenus = [
            "➕ Add/Renew VIP",
            "📋 VIP List",
            "🔢 Free Limit",
            "💸 Withdraw ON/OFF",
            "💵 Minimum Withdraw",
            "👥 Referral Bonus"
        ]

        if text in admin_submenus:

            admin_action(
                message,
                text
            )

            return

    # -----------------------------------------------------
    # FALLBACK
    # -----------------------------------------------------

    send(
        uid,
        get_setting("invalid"),
        user_keyboard(
            is_admin(uid)
        )
    )


# =========================================================
# START BOT
# =========================================================

init_db()


def main():

    threading.Thread(
        target=scheduler,
        daemon=True
    ).start()

    print(
        "SM QUATEX SURE SHORT BOT STARTED"
    )

    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30
    )


if __name__ == "__main__":

    main()
