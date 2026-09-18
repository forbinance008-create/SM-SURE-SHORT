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
REFERRAL_BONUS_CENTS = 100       # default $1.00
MIN_WITHDRAW_CENTS = 500         # default $5.00
VIP_DEPOSIT_CENTS = 1500        # default $15.00

# Optional: keep your own referral URL here if you use one.
QUOTEX_REF_LINK = os.getenv(
    "QUOTEX_REF_LINK",
    "https://broker-qx.pro/sign-up/?lid=2350796"
)

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing. Set BOT_TOKEN as an environment variable."
    )

os.makedirs(BACKUP_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("telegram_signal_bot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
db_lock = threading.RLock()

# Runtime conversation state. Normal user data is persisted in SQLite;
# this dictionary only tracks the current interactive admin/user prompt.
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
    start_days = (days // 2) * 2
    return (CYCLE_EPOCH + timedelta(days=start_days)).isoformat()


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
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
                status TEXT NOT NULL DEFAULT 'FREE',
                wallet_cents INTEGER NOT NULL DEFAULT 0,
                referred_by INTEGER,
                refs_count INTEGER NOT NULL DEFAULT 0,
                referral_bonus_paid INTEGER NOT NULL DEFAULT 0,
                free_cycle_key TEXT,
                free_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                blocked INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(referred_by) REFERENCES users(user_id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_text TEXT NOT NULL,
                signal_at_utc TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(signal_text, signal_at_utc)
            );

            CREATE TABLE IF NOT EXISTS signal_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                accessed_at TEXT NOT NULL,
                UNIQUE(user_id, signal_id),
                FOREIGN KEY(user_id) REFERENCES users(user_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(signal_id) REFERENCES signals(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS signal_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                vote TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, signal_id),
                FOREIGN KEY(user_id) REFERENCES users(user_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(signal_id) REFERENCES signals(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS uid_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                uid TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                submitted_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
                    ON DELETE CASCADE
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
                reviewed_by INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
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
                FOREIGN KEY(user_id) REFERENCES users(user_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                permissions TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS signal_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                sent_at TEXT NOT NULL,
                UNIQUE(user_id, signal_id),
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE
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
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            INSERT OR IGNORE INTO settings(key, value)
                VALUES('maintenance', '0');

            INSERT OR IGNORE INTO settings(key, value)
                VALUES('live_mode', '1');

            INSERT OR IGNORE INTO settings(key, value) VALUES('free_signal_limit', '4');
            INSERT OR IGNORE INTO settings(key, value) VALUES('referral_bonus_cents', '100');
            INSERT OR IGNORE INTO settings(key, value) VALUES('min_withdraw_cents', '500');
            INSERT OR IGNORE INTO settings(key, value) VALUES('vip_deposit_cents', '1500');
            INSERT OR IGNORE INTO settings(key, value) VALUES('withdraw_enabled', '1');
            INSERT OR IGNORE INTO settings(key, value) VALUES('withdraw_hold', '0');
            INSERT OR IGNORE INTO settings(key, value) VALUES('notice', '');
            INSERT OR IGNORE INTO settings(key, value) VALUES('trading_rules', 'Trade responsibly. Use proper money management and do not risk money you cannot afford to lose.');
            INSERT OR IGNORE INTO settings(key, value) VALUES('notifications_enabled', '1');
            INSERT OR IGNORE INTO settings(key, value) VALUES('signal_confidence', '95–99%');
            """)
            # Safe migration for databases created by older versions.
            try:
                conn.execute("ALTER TABLE users ADD COLUMN notifications_enabled INTEGER NOT NULL DEFAULT 1")
            except sqlite3.OperationalError:
                pass
            conn.commit()
        finally:
            conn.close()


def get_setting(key, default=None):
    with db_lock:
        conn = db()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default
        finally:
            conn.close()


def get_int_setting(key, default):
    try:
        return int(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


def free_signal_limit():
    return max(0, get_int_setting("free_signal_limit", FREE_SIGNALS_PER_CYCLE))


def free_signal_limit_for(user_id):
    with db_lock:
        conn = db()
        try:
            row = conn.execute("SELECT free_limit FROM user_limits WHERE user_id=?", (user_id,)).fetchone()
            return max(0, int(row["free_limit"])) if row else free_signal_limit()
        finally:
            conn.close()


def set_user_free_limit(user_id, limit):
    with db_lock:
        conn = db()
        try:
            if not conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone():
                return False
            if limit is None:
                conn.execute("DELETE FROM user_limits WHERE user_id=?", (user_id,))
            else:
                conn.execute("INSERT INTO user_limits(user_id,free_limit) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET free_limit=excluded.free_limit", (user_id, max(0, int(limit))))
            conn.commit()
            return True
        finally:
            conn.close()


def referral_bonus_cents():
    return max(0, get_int_setting("referral_bonus_cents", REFERRAL_BONUS_CENTS))


def min_withdraw_cents():
    return max(0, get_int_setting("min_withdraw_cents", 500))


def vip_deposit_cents():
    return max(0, get_int_setting("vip_deposit_cents", VIP_DEPOSIT_CENTS))


def withdraw_enabled():
    return get_setting("withdraw_enabled", "1") == "1"


def withdraw_hold():
    return get_setting("withdraw_hold", "0") == "1"


def set_setting(key, value):
    with db_lock:
        conn = db()
        try:
            conn.execute("""
                INSERT INTO settings(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (key, str(value)))
            conn.commit()
        finally:
            conn.close()


def audit(admin_id, action, target_user_id=None, details=''):
    try:
        with db_lock:
            conn = db()
            try:
                conn.execute("INSERT INTO admin_audit(admin_id,action,target_user_id,details,created_at) VALUES(?,?,?,?,?)",
                             (admin_id, action, target_user_id, str(details)[:500], utc_iso(now_utc())))
                conn.commit()
            finally:
                conn.close()
    except Exception:
        logger.exception("audit failed")


def notifications_enabled_global():
    return get_setting('notifications_enabled', '1') == '1'


def user_notifications_enabled(user_id):
    u = get_user(user_id)
    return bool(u and u['notifications_enabled'] == 1)


def signal_confidence():
    return get_setting('signal_confidence', '95–99%')


# ============================================================
# USER
# ============================================================

def register_user(tg_user, referred_by=None):
    uid = tg_user.id
    now = utc_iso(now_utc())

    with db_lock:
        conn = db()
        try:
            existing = conn.execute(
                "SELECT user_id FROM users WHERE user_id=?", (uid,)
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE users
                    SET username=?, first_name=?, last_seen=?, blocked=0
                    WHERE user_id=?
                """, (
                    tg_user.username, tg_user.first_name, now, uid
                ))
                conn.commit()
                return False

            if referred_by == uid:
                referred_by = None

            if referred_by:
                ok = conn.execute(
                    "SELECT user_id FROM users WHERE user_id=?",
                    (referred_by,)
                ).fetchone()
                if not ok:
                    referred_by = None

            conn.execute("""
                INSERT INTO users(
                    user_id,username,first_name,status,wallet_cents,
                    referred_by,refs_count,referral_bonus_paid,
                    free_cycle_key,free_used,created_at,last_seen,blocked
                )
                VALUES(?,?,?,'FREE',0,?,0,0,?,0,?,?,0)
            """, (
                uid, tg_user.username, tg_user.first_name,
                referred_by, cycle_key(), now, now
            ))
            conn.commit()
            return True
        finally:
            conn.close()


def get_user(user_id):
    with db_lock:
        conn = db()
        try:
            return conn.execute(
                "SELECT * FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
        finally:
            conn.close()


def ensure_cycle(user_id):
    ck = cycle_key()
    with db_lock:
        conn = db()
        try:
            row = conn.execute(
                "SELECT free_cycle_key FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()
            if row and row["free_cycle_key"] != ck:
                conn.execute("""
                    UPDATE users SET free_cycle_key=?, free_used=0
                    WHERE user_id=?
                """, (ck, user_id))
                conn.commit()
        finally:
            conn.close()


def set_vip(user_id, is_vip=True):
    with db_lock:
        conn = db()
        try:
            conn.execute(
                "UPDATE users SET status=? WHERE user_id=?",
                ("VIP" if is_vip else "FREE", user_id)
            )
            conn.commit()
        finally:
            conn.close()


def wallet_balance(user_id):
    row = get_user(user_id)
    return row["wallet_cents"] if row else 0


def wallet_adjust(user_id, delta_cents, note, tx_type="ADJUSTMENT"):
    with db_lock:
        conn = db()
        try:
            row = conn.execute(
                "SELECT wallet_cents FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()
            if not row:
                return False, 0

            new_balance = row["wallet_cents"] + delta_cents
            if new_balance < 0:
                return False, row["wallet_cents"]

            conn.execute(
                "UPDATE users SET wallet_cents=? WHERE user_id=?",
                (new_balance, user_id)
            )
            conn.execute("""
                INSERT INTO wallet_transactions(
                    user_id,type,amount_cents,balance_after_cents,note,created_at
                ) VALUES(?,?,?,?,?,?)
            """, (
                user_id, tx_type, delta_cents, new_balance,
                note, utc_iso(now_utc())
            ))
            conn.commit()
            return True, new_balance
        finally:
            conn.close()


# ============================================================
# REFERRAL
# ============================================================

def process_referral_bonus(user_id):
    with db_lock:
        conn = db()
        try:
            user = conn.execute("""
                SELECT referred_by,referral_bonus_paid
                FROM users WHERE user_id=?
            """, (user_id,)).fetchone()

            if not user or not user["referred_by"] or user["referral_bonus_paid"]:
                return False

            referrer = user["referred_by"]

            conn.execute("""
                UPDATE users
                SET wallet_cents=wallet_cents+?,
                    refs_count=refs_count+1
                WHERE user_id=?
            """, (referral_bonus_cents(), referrer))

            new_balance = conn.execute(
                "SELECT wallet_cents FROM users WHERE user_id=?",
                (referrer,)
            ).fetchone()["wallet_cents"]

            conn.execute("""
                INSERT INTO wallet_transactions(
                    user_id,type,amount_cents,balance_after_cents,note,created_at
                ) VALUES(?,?,?,?,?,?)
            """, (
                referrer, "REFERRAL_BONUS", referral_bonus_cents(),
                new_balance, f"Referral bonus from user {user_id}",
                utc_iso(now_utc())
            ))

            conn.execute("""
                UPDATE users SET referral_bonus_paid=1 WHERE user_id=?
            """, (user_id,))
            conn.commit()

            try:
                bot.send_message(
                    referrer,
                    "🎉 <b>Referral Bonus</b>\n\n"
                    f"আপনার wallet-এ <b>${referral_bonus_cents()/100:.2f}</b> "
                    "referral bonus যোগ হয়েছে।"
                )
            except Exception:
                pass

            return True
        finally:
            conn.close()


# ============================================================
# SIGNALS
# ============================================================

def parse_signal_line(line):
    line = line.strip()
    if not line:
        return None

    # Supports 24h: 13:27 - EUR/USD - UP
    # Supports 12h: 9:27 AM - EUR/USD - UP
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)?\s*[-|]\s*(.+)$", line, re.I)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        ap = (m.group(3) or "").upper()
        if minute > 59:
            return None
        if ap:
            if hour < 1 or hour > 12:
                return None
            if ap == "AM" and hour == 12:
                hour = 0
            elif ap == "PM" and hour != 12:
                hour += 12
        elif hour > 23:
            return None
        target = datetime.combine(now_bd().date(), dt_time(hour, minute), tzinfo=BD_TZ)
        if target <= now_bd():
            target += timedelta(days=1)
        return m.group(4).strip(), target

    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s*(AM|PM)?\s*[-|]\s*(.+)$", line, re.I)
    if m:
        hour, minute = int(m.group(2)), int(m.group(3))
        ap = (m.group(4) or "").upper()
        if minute > 59:
            return None
        if ap:
            if hour < 1 or hour > 12:
                return None
            if ap == "AM" and hour == 12:
                hour = 0
            elif ap == "PM" and hour != 12:
                hour += 12
        elif hour > 23:
            return None
        try:
            target = datetime.strptime(m.group(1), "%Y-%m-%d").replace(
                hour=hour, minute=minute, tzinfo=BD_TZ
            )
        except ValueError:
            return None
        return m.group(5).strip(), target

    return None


def format_signal_time(dt):
    # AM is shown in normal 12-hour form; PM is shown in 24-hour form.
    if dt.hour < 12:
        return dt.strftime("%I:%M AM").lstrip("0")
    return dt.strftime("%H:%M")


def format_signal_text(raw):
    raw = raw.strip()
    m = re.match(r"^(.+?)\s*[-|]\s*(UP|BUY|CALL|DOWN|SELL|PUT)\s*$", raw, re.I)
    if m:
        pair = m.group(1).strip().upper()
        direction = m.group(2).upper()
        if direction in {"UP", "BUY", "CALL"}:
            return f"📌 <b>{escape(pair)}</b>\n🟢 ⬆️ <b>UP / BUY</b>"
        return f"📌 <b>{escape(pair)}</b>\n🔴 ⬇️ <b>DOWN / SELL</b>"
    return f"📌 <b>{escape(raw)}</b>"


def add_signals(text):
    added = duplicate = invalid = 0
    with db_lock:
        conn = db()
        try:
            for line in text.splitlines():
                parsed = parse_signal_line(line)
                if not parsed:
                    invalid += 1
                    continue

                signal_text, target = parsed

                try:
                    conn.execute("""
                        INSERT INTO signals(
                            signal_text,signal_at_utc,created_at
                        ) VALUES(?,?,?)
                    """, (
                        signal_text, utc_iso(target), utc_iso(now_utc())
                    ))
                    added += 1
                except sqlite3.IntegrityError:
                    duplicate += 1

            conn.commit()
        finally:
            conn.close()
    return added, duplicate, invalid


def next_signal_for(user_id):
    current = utc_iso(now_utc())
    with db_lock:
        conn = db()
        try:
            return conn.execute("""
                SELECT s.*
                FROM signals s
                WHERE s.signal_at_utc > ?
                AND NOT EXISTS(
                    SELECT 1 FROM signal_access a
                    WHERE a.user_id=? AND a.signal_id=s.id
                )
                ORDER BY s.signal_at_utc ASC
                LIMIT 1
            """, (current, user_id)).fetchone()
        finally:
            conn.close()


def deliver_signal(user_id, signal_id):
    with db_lock:
        conn = db()
        try:
            user = conn.execute(
                "SELECT * FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            signal = conn.execute(
                "SELECT * FROM signals WHERE id=?", (signal_id,)
            ).fetchone()

            if not user or not signal:
                return False, "not_found"

            exists = conn.execute("""
                SELECT id FROM signal_access
                WHERE user_id=? AND signal_id=?
            """, (user_id, signal_id)).fetchone()

            if exists:
                return False, "already"

            if user["status"] != "VIP":
                ck = cycle_key()
                used = user["free_used"]

                if user["free_cycle_key"] != ck:
                    used = 0
                    conn.execute("""
                        UPDATE users SET free_cycle_key=?,free_used=0
                        WHERE user_id=?
                    """, (ck, user_id))

                if used >= free_signal_limit_for(user_id):
                    conn.commit()
                    return False, "limit"

                conn.execute("""
                    UPDATE users SET free_used=free_used+1
                    WHERE user_id=?
                """, (user_id,))

            conn.execute("""
                INSERT INTO signal_access(user_id,signal_id,accessed_at)
                VALUES(?,?,?)
            """, (user_id, signal_id, utc_iso(now_utc())))

            conn.commit()
            return True, "ok"
        finally:
            conn.close()


# ============================================================
# ADMIN / SUBADMIN
# ============================================================

ALL_PERMISSIONS = {
    "signals", "uid", "users", "wallet",
    "withdraw", "broadcast", "settings", "analytics"
}


def is_master(user_id):
    return user_id == ADMIN_ID


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
            return {x.strip() for x in row["permissions"].split(",") if x.strip()}
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
            conn.execute("""
                INSERT INTO admins(user_id,permissions)
                VALUES(?,?)
                ON CONFLICT(user_id)
                DO UPDATE SET permissions=excluded.permissions
            """, (user_id, ",".join(sorted(permissions))))
            conn.commit()
        finally:
            conn.close()


def remove_subadmin(user_id):
    with db_lock:
        conn = db()
        try:
            conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
            conn.commit()
        finally:
            conn.close()


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard(user_id):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📢 Notice", "📊 Future Signals")
    kb.row("⚡ Live Signals", "👤 My Status")
    kb.row("💰 Wallet", "👥 Referral Link")
    kb.row("⭐ VIP Rules", "📖 Trading Rules")
    kb.row("🔔 Notifications")
    u = get_user(user_id)
    if not u or u["status"] != "VIP":
        kb.row("🆔 Submit Quotex UID")
    if is_admin(user_id):
        kb.row("👑 Admin Control")
    return kb


def admin_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Add Signal", callback_data="adm_add_signal"),
        types.InlineKeyboardButton("📊 Signals", callback_data="adm_signals")
    )
    kb.add(
        types.InlineKeyboardButton("🆔 Pending UID", callback_data="adm_uids"),
        types.InlineKeyboardButton("💸 Withdrawals", callback_data="adm_withdrawals")
    )
    kb.add(
        types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast"),
        types.InlineKeyboardButton("👥 Users", callback_data="adm_users")
    )
    kb.add(
        types.InlineKeyboardButton("💳 Wallet Adjust", callback_data="adm_wallet"),
        types.InlineKeyboardButton("📈 Analytics", callback_data="adm_analytics")
    )
    kb.add(
        types.InlineKeyboardButton("🛡 Sub-admins", callback_data="adm_subadmins"),
        types.InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")
    )
    kb.add(
        types.InlineKeyboardButton("👤 Manage User", callback_data="adm_manage_user"),
        types.InlineKeyboardButton("🗑 Clear Future", callback_data="adm_clear")
    )
    kb.add(types.InlineKeyboardButton("🔔 Notification Settings", callback_data="adm_notifications"))
    kb.add(types.InlineKeyboardButton("📢 Send Notice Now", callback_data="adm_notice_send"))
    return kb


def back_admin_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Admin Panel", callback_data="adm_home"))
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
            except ValueError:
                pass

        register_user(message.from_user, referred_by)

        bot.send_message(
            message.chat.id,
            "🎉 <b>Welcome!</b>\n\n"
            "আপনার account তৈরি হয়েছে।\n\n"
            f"🎟️ Free: প্রতি ২ দিনে সর্বোচ্চ <b>{free_signal_limit()}টি</b> signal.\n"
            f"⭐ VIP: <b>${vip_deposit_cents()/100:.2f}</b> deposit করে join করা যাবে.\n\n"
            f"🎯 Stated signal confidence: <b>{escape(signal_confidence())}</b>\n\n"
            "📌 Confidence is a stated estimate, not a guaranteed result.",
            reply_markup=extra_main_keyboard(message.from_user.id)
        )
    except Exception:
        logger.exception("start error")
        bot.send_message(message.chat.id, "❌ Error. আবার /start দিন।")


@bot.message_handler(commands=["cancel"])
def cancel_cmd(message):
    states.pop(message.from_user.id, None)
    bot.send_message(
        message.chat.id,
        "❌ Operation cancelled.",
        reply_markup=extra_main_keyboard(message.from_user.id)
    )


# ============================================================
# USER MENU
# ============================================================

def maintenance_blocked(user_id):
    return get_setting("maintenance", "0") == "1" and not is_admin(user_id)


@bot.message_handler(func=lambda m: m.text == "📊 Future Signals")
def future_signal_cmd(message):
    uid = message.from_user.id
    register_user(message.from_user)

    if maintenance_blocked(uid):
        bot.send_message(message.chat.id, "🛠️ Bot maintenance mode-এ আছে।")
        return

    ensure_cycle(uid)
    user = get_user(uid)
    trade_amount, stage = mm_trade(uid)
    if trade_amount <= 0:
        bot.send_message(message.chat.id,"⚠️ <b>আজকের Trading Balance আগে set করো</b>\n\n💰 Money Management → Set Daily Balance",reply_markup=extra_main_keyboard(uid))
        return

    if user["status"] != "VIP" and user["free_used"] >= free_signal_limit_for(uid):
        bot.send_message(
            message.chat.id,
            "⛔ <b>Free signal quota শেষ</b>\n\n"
            f"এই ২ দিনের cycle-এ আপনার {free_signal_limit()}টি signal শেষ হয়েছে।\n"
            "পরবর্তী cycle শুরু হলে quota আবার reset হবে।\n\n"
            "⭐ VIP হলে এই limit থাকবে না."
        )
        return

    signal = next_signal_for(uid)
    if not signal:
        bot.send_message(message.chat.id, "📭 কোনো upcoming signal নেই।")
        return

    ok, reason = deliver_signal(uid, signal["id"])

    if not ok:
        bot.send_message(
            message.chat.id,
            "⛔ Free quota শেষ বা signal পাওয়া যায়নি।"
        )
        return

    process_referral_bonus(uid)

    t = bd_from_iso(signal["signal_at_utc"])
    after = get_user(uid)

    if after["status"] == "VIP":
        quota = "♾️ VIP Unlimited"
    else:
        remaining = max(0, free_signal_limit_for(uid) - after["free_used"])
        quota = (
            f"🎟️ Remaining: <b>{remaining}/"
            f"{free_signal_limit_for(uid)}</b>"
        )

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(
            "✅ WIN", callback_data=f"vote_win_{signal['id']}"
        ),
        types.InlineKeyboardButton(
            "❌ LOSS", callback_data=f"vote_loss_{signal['id']}"
        )
    )

    bot.send_message(
        message.chat.id,
        "📊 <b>FUTURE SIGNAL</b>\n\n"
        f"🕐 BD Time: <b>{t.strftime('%d-%m-%Y')}</b> <b>{format_signal_time(t)}</b>\n"
        f"{format_signal_text(signal['signal_text'])}\n\n"
        f"{quota}\n"
        f"💵 Trade: <b>${trade_amount/100:.2f}</b> | <b>{stage}</b>\n\n"
        f"🎯 Stated confidence: <b>{escape(signal_confidence())}</b>\n"
        "📌 Confidence is a stated estimate, not a guarantee.",
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("vote_"))
def vote_callback(call):
    try:
        _, vote, sid = call.data.split("_")
        sid = int(sid)

        with db_lock:
            conn = db()
            try:
                exists = conn.execute("""
                    SELECT id FROM signal_votes
                    WHERE user_id=? AND signal_id=?
                """, (call.from_user.id, sid)).fetchone()

                if exists:
                    bot.answer_callback_query(call.id, "আপনি আগে vote দিয়েছেন।")
                    return

                conn.execute("""
                    INSERT INTO signal_votes(
                        user_id,signal_id,vote,created_at
                    ) VALUES(?,?,?,?)
                """, (
                    call.from_user.id, sid, vote.upper(), utc_iso(now_utc())
                ))
                conn.commit()
            finally:
                conn.close()

        bot.answer_callback_query(call.id, "Vote saved ✅")
    except Exception:
        logger.exception("vote error")
        bot.answer_callback_query(call.id, "Vote save করা যায়নি।")


@bot.message_handler(func=lambda m: m.text == "⚡ Live Signals")
def live_cmd(message):
    live = get_setting("live_mode", "1")
    bot.send_message(
        message.chat.id,
        "⚡ <b>LIVE SIGNAL MODE</b>\n\n"
        + ("🟢 Active" if live == "1" else "🔴 Disabled")
    )


@bot.message_handler(func=lambda m: m.text == "👤 My Status")
def status_cmd(message):
    register_user(message.from_user)
    ensure_cycle(message.from_user.id)
    u = get_user(message.from_user.id)

    if u["status"] == "VIP":
        quota = "♾️ Unlimited"
    else:
        quota = f"{max(0, free_signal_limit_for(message.from_user.id)-u['free_used'])}/{free_signal_limit_for(message.from_user.id)}"

    bot.send_message(
        message.chat.id,
        "👤 <b>MY STATUS</b>\n\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n"
        f"⭐ Status: <b>{u['status']}</b>\n"
        f"💰 Wallet: <b>${u['wallet_cents']/100:.2f}</b>\n"
        f"👥 Referrals: <b>{u['refs_count']}</b>\n"
        f"🎟️ Free Signal Remaining: <b>{quota}</b>\n"
        f"🇧🇩 BD Time: {now_bd().strftime('%d-%m-%Y %I:%M %p')}"
    )


@bot.message_handler(func=lambda m: m.text == "🔔 Notifications")
def user_notification_settings(message):
    uid = message.from_user.id
    u = get_user(uid)
    if not u:
        register_user(message.from_user)
        u = get_user(uid)
    enabled = bool(u['notifications_enabled'])
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(
        f"{'🔕 Turn OFF' if enabled else '🔔 Turn ON'} notifications",
        callback_data="user_notify_toggle"
    ))
    bot.send_message(message.chat.id,
        "🔔 <b>Notifications</b>\n\n"
        f"Status: <b>{'ON' if enabled else 'OFF'}</b>\n"
        "ON থাকলে scheduled signal আপনার কাছে automatically আসবে।",
        reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "user_notify_toggle")
def user_notify_toggle(call):
    uid = call.from_user.id
    with db_lock:
        conn = db()
        try:
            row = conn.execute("SELECT notifications_enabled FROM users WHERE user_id=?", (uid,)).fetchone()
            if not row:
                bot.answer_callback_query(call.id, "User not found.")
                return
            new_value = 0 if row['notifications_enabled'] else 1
            conn.execute("UPDATE users SET notifications_enabled=? WHERE user_id=?", (new_value, uid))
            conn.commit()
        finally:
            conn.close()
    bot.answer_callback_query(call.id, "Updated")
    user_notification_settings(types.SimpleNamespace(chat=call.message.chat, from_user=call.from_user))


@bot.message_handler(func=lambda m: m.text == "⭐ VIP Rules")
def vip_rules(message):
    deposit = vip_deposit_cents() / 100
    limit = free_signal_limit()
    bot.send_message(
        message.chat.id,
        "⭐ <b>VIP RULES</b>\n\n"
        f"💵 VIP join/deposit: <b>${deposit:.2f}</b>\n"
        f"🎟️ Non-VIP: প্রতি ২ দিনে <b>{limit}টি</b> free signal.\n"
        "♾️ VIP: Future Signal limit নেই.\n"
        "🆔 UID verification admin-এর মাধ্যমে হবে.\n\n"
        "📌 VIP join করার জন্য admin-এর নির্দেশনা অনুসরণ করুন.\n"
        f"🎯 Stated signal confidence: <b>{escape(signal_confidence())}</b>\n"
        "📌 Confidence is a stated estimate, not a guarantee."
    )


@bot.message_handler(func=lambda m: m.text == "👥 Referral Link")
def referral_cmd(message):
    try:
        me = bot.get_me()
        link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
        bot.send_message(
            message.chat.id,
            "👥 <b>Your Referral Link</b>\n\n"
            f"<code>{link}</code>\n\n"
            f"Successful referral bonus: <b>${referral_bonus_cents()/100:.2f}</b> "
            "once per referred user."
        )
    except Exception:
        bot.send_message(message.chat.id, "❌ Referral link তৈরি করা যায়নি।")


# ============================================================
# NOTICE / TRADING RULES
# ============================================================

@bot.message_handler(func=lambda m: m.text == "📢 Notice")
def notice_cmd(message):
    notice = get_setting("notice", "").strip()
    if not notice:
        bot.send_message(message.chat.id, "📢 <b>NOTICE</b>\n\nকোনো নতুন notice নেই।")
        return
    bot.send_message(message.chat.id, "📢 <b>NOTICE</b>\n\n" + escape(notice))


@bot.message_handler(func=lambda m: m.text == "📖 Trading Rules")
def trading_rules_cmd(message):
    rules = get_setting("trading_rules", "Trade responsibly.").strip()
    bot.send_message(message.chat.id, "📖 <b>TRADING RULES</b>\n\n" + escape(rules))


# ============================================================
# UID
# ============================================================

@bot.message_handler(func=lambda m: m.text == "🆔 Submit Quotex UID")
def uid_start(message):
    uid = message.from_user.id

    if maintenance_blocked(uid):
        bot.send_message(message.chat.id, "🛠️ Maintenance mode.")
        return

    u = get_user(uid)
    if u and u["status"] == "VIP":
        bot.send_message(message.chat.id, "⭐ আপনি ইতিমধ্যে VIP। UID আবার submit করার প্রয়োজন নেই।")
        return
    with db_lock:
        conn = db()
        try:
            pending = conn.execute(
                "SELECT id FROM uid_submissions WHERE user_id=? AND status='PENDING' LIMIT 1", (uid,)
            ).fetchone()
        finally:
            conn.close()
    if pending:
        bot.send_message(message.chat.id, "⏳ আপনার UID already pending আছে। Admin review শেষ হওয়া পর্যন্ত আবার submit করা যাবে না।")
        return

    states[uid] = {"action": "uid"}
    bot.send_message(
        message.chat.id,
        "🆔 আপনার Quotex UID পাঠান।\n\n"
        "শুধু UID number/text পাঠাও।\n"
        "/cancel দিয়ে বাতিল করতে পারো।"
    )


def save_uid(user_message):
    user_id = user_message.from_user.id
    value = user_message.text.strip()

    if not (3 <= len(value) <= 100):
        bot.send_message(user_message.chat.id, "❌ UID format সঠিক নয়। আবার পাঠাও।")
        return

    with db_lock:
        conn = db()
        try:
            # Only one active pending submission at a time.
            conn.execute("""
                UPDATE uid_submissions
                SET status='REPLACED', reviewed_at=?
                WHERE user_id=? AND status='PENDING'
            """, (utc_iso(now_utc()), user_id))

            cur = conn.execute("""
                INSERT INTO uid_submissions(
                    user_id,uid,status,submitted_at
                ) VALUES(?,?,'PENDING',?)
            """, (user_id, value, utc_iso(now_utc())))
            submission_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

    states.pop(user_id, None)

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton(
            "✅ Approve VIP", callback_data=f"uid_ok_{submission_id}"
        ),
        types.InlineKeyboardButton(
            "❌ Reject", callback_data=f"uid_no_{submission_id}"
        )
    )

    try:
        bot.send_message(
            ADMIN_ID,
            "🆔 <b>New UID Submission</b>\n\n"
            f"User: <code>{user_id}</code>\n"
            f"UID: <code>{escape(value)}</code>",
            reply_markup=kb
        )
    except Exception:
        pass

    bot.send_message(
        user_message.chat.id,
        "✅ UID submitted.\n\nAdmin verification-এর জন্য অপেক্ষা করুন।"
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("uid_"))
def uid_review(call):
    if not can(call.from_user.id, "uid"):
        bot.answer_callback_query(call.id, "Access denied.")
        return

    try:
        _, decision, sid = call.data.split("_")
        sid = int(sid)

        with db_lock:
            conn = db()
            try:
                row = conn.execute("""
                    SELECT * FROM uid_submissions
                    WHERE id=? AND status IN ('PENDING','HOLD')
                """, (sid,)).fetchone()

                if not row:
                    bot.answer_callback_query(call.id, "Already processed.")
                    return

                new_status = "APPROVED" if decision == "ok" else "REJECTED"

                conn.execute("""
                    UPDATE uid_submissions
                    SET status=?,reviewed_at=?,reviewed_by=?
                    WHERE id=?
                """, (
                    new_status, utc_iso(now_utc()),
                    call.from_user.id, sid
                ))

                if new_status == "APPROVED":
                    conn.execute(
                        "UPDATE users SET status='VIP' WHERE user_id=?",
                        (row["user_id"],)
                    )

                conn.commit()
            finally:
                conn.close()

        if decision == "ok":
            text = "⭐ <b>VIP Approved!</b>\n\nআপনার account এখন VIP।"
        else:
            text = "❌ <b>UID Rejected.</b>\n\nপ্রয়োজনে নতুন UID submit করুন।"

        try:
            bot.send_message(row["user_id"], text)
        except Exception:
            pass

        bot.answer_callback_query(call.id, "Done.")
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
        )
    except Exception:
        logger.exception("uid review error")


# ============================================================
# WALLET / WITHDRAW
# ============================================================

@bot.message_handler(func=lambda m: m.text == "💰 Wallet")
def wallet_cmd(message):
    u = get_user(message.from_user.id)
    balance = u["wallet_cents"] / 100 if u else 0

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(
        "💸 Request Withdraw", callback_data="user_withdraw"
    ))

    bot.send_message(
        message.chat.id,
        f"💰 <b>Wallet</b>\n\n"
        f"Balance: <b>${balance:.2f}</b>\n"
        f"Minimum withdraw: <b>${min_withdraw_cents()/100:.2f}</b>",
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda c: c.data == "user_withdraw")
def withdraw_start(call):
    if maintenance_blocked(call.from_user.id):
        bot.answer_callback_query(call.id, "Maintenance mode.")
        return

    if not withdraw_enabled():
        bot.answer_callback_query(call.id, "Withdraw is currently disabled.")
        return

    if withdraw_hold():
        bot.answer_callback_query(call.id, "Withdrawals are currently on hold.")
        return

    if wallet_balance(call.from_user.id) < min_withdraw_cents():
        bot.answer_callback_query(
            call.id,
            f"Minimum ${min_withdraw_cents()/100:.2f} required."
        )
        return

    states[call.from_user.id] = {"action": "withdraw_amount"}
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "💸 কত USD withdraw করতে চান?\n\nExample: <code>5</code>"
    )


def create_withdraw(user_id, amount_cents, method, account):
    with db_lock:
        conn = db()
        try:
            row = conn.execute(
                "SELECT wallet_cents FROM users WHERE user_id=?",
                (user_id,)
            ).fetchone()

            if not row or row["wallet_cents"] < amount_cents:
                return False

            new_balance = row["wallet_cents"] - amount_cents

            conn.execute(
                "UPDATE users SET wallet_cents=? WHERE user_id=?",
                (new_balance, user_id)
            )
            conn.execute("""
                INSERT INTO wallet_transactions(
                    user_id,type,amount_cents,balance_after_cents,note,created_at
                ) VALUES(?,?,?,?,?,?)
            """, (
                user_id, "WITHDRAW_HOLD", -amount_cents,
                new_balance, "Pending withdrawal hold",
                utc_iso(now_utc())
            ))

            withdrawal_status = "HOLD" if withdraw_hold() else "PENDING"
            cur = conn.execute("""
                INSERT INTO withdrawals(
                    user_id,amount_cents,method,account,status,created_at
                ) VALUES(?,?,?,?,?,?)
            """, (
                user_id, amount_cents, method, account, withdrawal_status,
                utc_iso(now_utc())
            ))

            wid = cur.lastrowid
            conn.commit()
            return wid
        finally:
            conn.close()


def finish_withdraw(message):
    user_id = message.from_user.id
    state = states.get(user_id)

    if not state:
        return

    if state["action"] == "withdraw_amount":
        try:
            amount = float(message.text.strip())
            cents = int(round(amount * 100))
        except ValueError:
            bot.send_message(message.chat.id, "❌ শুধু amount দাও। Example: 5")
            return

        if cents < min_withdraw_cents():
            bot.send_message(
                message.chat.id,
                f"❌ Minimum ${min_withdraw_cents()/100:.2f}."
            )
            return

        if wallet_balance(user_id) < cents:
            bot.send_message(message.chat.id, "❌ Wallet balance যথেষ্ট নয়।")
            return

        state["amount_cents"] = cents
        state["action"] = "withdraw_method"
        bot.send_message(
            message.chat.id,
            "💳 Payment method পাঠাও।\nExample: bKash / Bank / অন্য method"
        )
        return

    if state["action"] == "withdraw_method":
        state["method"] = message.text.strip()[:100]
        state["action"] = "withdraw_account"
        bot.send_message(
            message.chat.id,
            "📱 Payment account/number পাঠাও।"
        )
        return

    if state["action"] == "withdraw_account":
        account = message.text.strip()[:200]
        amount_cents = state["amount_cents"]
        method = state["method"]

        wid = create_withdraw(
            user_id, amount_cents, method, account
        )

        states.pop(user_id, None)

        if not wid:
            bot.send_message(message.chat.id, "❌ Withdraw request তৈরি করা যায়নি।")
            return

        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(
                "✅ Approve", callback_data=f"wd_ok_{wid}"
            ),
            types.InlineKeyboardButton(
                "❌ Reject", callback_data=f"wd_no_{wid}"
            )
        )

        try:
            bot.send_message(
                ADMIN_ID,
                "💸 <b>New Withdrawal</b>\n\n"
                f"Request: <code>#{wid}</code>\n"
                f"User: <code>{user_id}</code>\n"
                f"Amount: <b>${amount_cents/100:.2f}</b>\n"
                f"Method: <b>{escape(method)}</b>\n"
                f"Account: <code>{escape(account)}</code>",
                reply_markup=kb
            )
        except Exception:
            pass

        bot.send_message(
            message.chat.id,
            "✅ Withdraw request submitted.\n"
            "Admin review-এর জন্য অপেক্ষা করুন।"
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("wd_"))
def withdraw_review(call):
    if not can(call.from_user.id, "withdraw"):
        bot.answer_callback_query(call.id, "Access denied.")
        return

    try:
        _, decision, wid = call.data.split("_")
        wid = int(wid)

        with db_lock:
            conn = db()
            try:
                row = conn.execute("""
                    SELECT * FROM withdrawals
                    WHERE id=? AND status='PENDING'
                """, (wid,)).fetchone()

                if not row:
                    bot.answer_callback_query(call.id, "Already processed.")
                    return

                if decision == "ok":
                    new_status = "APPROVED"
                else:
                    new_status = "REJECTED"

                conn.execute("""
                    UPDATE withdrawals
                    SET status=?,reviewed_at=?,reviewed_by=?
                    WHERE id=?
                """, (
                    new_status, utc_iso(now_utc()),
                    call.from_user.id, wid
                ))

                if new_status == "REJECTED":
                    cur = conn.execute(
                        "SELECT wallet_cents FROM users WHERE user_id=?",
                        (row["user_id"],)
                    ).fetchone()
                    new_balance = cur["wallet_cents"] + row["amount_cents"]

                    conn.execute("""
                        UPDATE users SET wallet_cents=? WHERE user_id=?
                    """, (new_balance, row["user_id"]))

                    conn.execute("""
                        INSERT INTO wallet_transactions(
                            user_id,type,amount_cents,balance_after_cents,note,created_at
                        ) VALUES(?,?,?,?,?,?)
                    """, (
                        row["user_id"], "WITHDRAW_REFUND",
                        row["amount_cents"], new_balance,
                        f"Withdrawal #{wid} rejected",
                        utc_iso(now_utc())
                    ))

                conn.commit()
            finally:
                conn.close()

        if decision == "ok":
            msg = (
                f"✅ Withdrawal #{wid} approved.\n"
                f"Amount: ${row['amount_cents']/100:.2f}"
            )
        else:
            msg = (
                f"❌ Withdrawal #{wid} rejected.\n"
                "Amount wallet-এ ফেরত দেওয়া হয়েছে।"
            )

        try:
            bot.send_message(row["user_id"], msg)
        except Exception:
            pass

        bot.answer_callback_query(call.id, "Done.")
        bot.edit_message_reply_markup(
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
        )
    except Exception:
        logger.exception("withdraw review error")


# ============================================================
# ADMIN PANEL
# ============================================================

@bot.message_handler(func=lambda m: m.text == "👑 Admin Control")
def admin_cmd(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Access denied.")
        return
    bot.send_message(
        message.chat.id,
        "👑 <b>ADMIN CONTROL</b>",
        reply_markup=admin_keyboard()
    )


@bot.callback_query_handler(func=lambda c: c.data == "adm_home")
def adm_home(call):
    if not is_admin(call.from_user.id):
        return
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "👑 <b>ADMIN CONTROL</b>",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=admin_keyboard()
    )


@bot.callback_query_handler(func=lambda c: c.data == "adm_add_signal")
def adm_add_signal(call):
    if not can(call.from_user.id, "signals"):
        bot.answer_callback_query(call.id, "Access denied.")
        return

    states[call.from_user.id] = {"action": "add_signal"}
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "➕ Signal পাঠাও।\n\n"
        "<code>18:30 - EUR/USD - CALL</code>\n"
        "অথবা\n"
        "<code>2026-09-17 18:30 - EUR/USD - CALL</code>\n\n"
        "একসাথে multiple line দেওয়া যাবে।"
    )


def admin_save_signal(message):
    added, dup, bad = add_signals(message.text or "")
    states.pop(message.from_user.id, None)

    bot.send_message(
        message.chat.id,
        "✅ <b>Signal Import</b>\n\n"
        f"➕ Added: {added}\n"
        f"♻️ Duplicate: {dup}\n"
        f"❌ Invalid: {bad}",
        reply_markup=admin_keyboard()
    )


@bot.callback_query_handler(func=lambda c: c.data == "adm_signals")
def adm_signals(call):
    if not can(call.from_user.id, "signals"):
        return

    current = utc_iso(now_utc())

    with db_lock:
        conn = db()
        try:
            rows = conn.execute("""
                SELECT * FROM signals
                WHERE signal_at_utc > ?
                ORDER BY signal_at_utc ASC
                LIMIT 30
            """, (current,)).fetchall()
        finally:
            conn.close()

    if not rows:
        text = "📭 No upcoming signals."
    else:
        lines = ["📊 <b>UPCOMING SIGNALS</b>\n"]
        for r in rows:
            t = bd_from_iso(r["signal_at_utc"])
            lines.append(
                f"#{r['id']} | {t.strftime('%d-%m')} {format_signal_time(t)} | "
                f"{escape(r['signal_text'])}"
            )
        text = "\n".join(lines)

    bot.send_message(
        call.message.chat.id, text, reply_markup=back_admin_keyboard()
    )


@bot.callback_query_handler(func=lambda c: c.data == "adm_clear")
def adm_clear(call):
    if not is_master(call.from_user.id):
        bot.answer_callback_query(call.id, "Master Admin only.")
        return

    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ CLEAR", callback_data="adm_clear_yes"),
        types.InlineKeyboardButton("❌ CANCEL", callback_data="adm_home")
    )
    bot.edit_message_text(
        "⚠️ সব upcoming signal clear করবে?",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda c: c.data == "adm_clear_yes")
def adm_clear_yes(call):
    if not is_master(call.from_user.id):
        return

    with db_lock:
        conn = db()
        try:
            conn.execute(
                "DELETE FROM signals WHERE signal_at_utc > ?",
                (utc_iso(now_utc()),)
            )
            conn.commit()
        finally:
            conn.close()

    bot.edit_message_text(
        "🗑️ Upcoming signals cleared.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=admin_keyboard()
    )


@bot.callback_query_handler(func=lambda c: c.data == "adm_uids")
def adm_uids(call):
    if not can(call.from_user.id, "uid"):
        return

    with db_lock:
        conn = db()
        try:
            rows = conn.execute("""
                SELECT u.id AS submission_id,u.uid,u.user_id,u.submitted_at
                FROM uid_submissions u
                WHERE u.status='PENDING'
                ORDER BY u.id ASC
                LIMIT 20
            """).fetchall()
        finally:
            conn.close()

    if not rows:
        bot.send_message(call.message.chat.id, "📭 No pending UID.")
        return

    for r in rows:
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(
                "✅ Approve", callback_data=f"uid_ok_{r['submission_id']}"
            ),
            types.InlineKeyboardButton(
                "❌ Reject", callback_data=f"uid_no_{r['submission_id']}"
            )
        )
        bot.send_message(
            call.message.chat.id,
            f"🆔 Request #{r['submission_id']}\n"
            f"User: <code>{r['user_id']}</code>\n"
            f"UID: <code>{escape(r['uid'])}</code>",
            reply_markup=kb
        )


@bot.callback_query_handler(func=lambda c: c.data == "adm_withdrawals")
def adm_withdrawals(call):
    if not can(call.from_user.id, "withdraw"):
        return

    with db_lock:
        conn = db()
        try:
            rows = conn.execute("""
                SELECT * FROM withdrawals
                WHERE status IN ('PENDING','HOLD')
                ORDER BY id ASC
                LIMIT 20
            """).fetchall()
        finally:
            conn.close()

    if not rows:
        bot.send_message(call.message.chat.id, "📭 No pending withdrawal.")
        return

    for r in rows:
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton(
                "✅ Approve", callback_data=f"wd_ok_{r['id']}"
            ),
            types.InlineKeyboardButton(
                "❌ Reject", callback_data=f"wd_no_{r['id']}"
            )
        )
        bot.send_message(
            call.message.chat.id,
            f"💸 <b>#{r['id']}</b>\n"
            f"User: <code>{r['user_id']}</code>\n"
            f"Amount: <b>${r['amount_cents']/100:.2f}</b>\n"
            f"Method: {escape(r['method'])}\n"
            f"Account: <code>{escape(r['account'])}</code>",
            reply_markup=kb
        )


@bot.callback_query_handler(func=lambda c: c.data == "adm_users")
def adm_users(call):
    if not can(call.from_user.id, "users"):
        return

    with db_lock:
        conn = db()
        try:
            rows = conn.execute("""
                SELECT user_id,username,status,wallet_cents,refs_count
                FROM users ORDER BY created_at DESC LIMIT 30
            """).fetchall()
        finally:
            conn.close()

    lines = ["👥 <b>Latest Users</b>\n"]
    for r in rows:
        name = f"@{r['username']}" if r["username"] else "-"
        lines.append(
            f"<code>{r['user_id']}</code> | {escape(name)} | "
            f"{r['status']} | ${r['wallet_cents']/100:.2f} | "
            f"Refs:{r['refs_count']}"
        )

    bot.send_message(
        call.message.chat.id,
        "\n".join(lines) if rows else "No users.",
        reply_markup=back_admin_keyboard()
    )



@bot.callback_query_handler(func=lambda c: c.data == "adm_manage_user")
def adm_manage_user(call):
    if not can(call.from_user.id, "users"):
        bot.answer_callback_query(call.id, "Access denied.")
        return
    states[call.from_user.id] = {"action": "manage_user"}
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "👤 যে user manage করবে তার Telegram ID পাঠাও।")


def manage_user_menu(chat_id, user_id):
    u = get_user(user_id)
    if not u:
        bot.send_message(chat_id, "❌ User পাওয়া যায়নি।", reply_markup=admin_keyboard())
        return
    limit = free_signal_limit_for(user_id)
    custom = limit != free_signal_limit()
    kb = types.InlineKeyboardMarkup(row_width=2)
    if u["status"] == "VIP":
        kb.add(types.InlineKeyboardButton("❌ Remove VIP", callback_data=f"user_vip_no_{user_id}"))
    else:
        kb.add(types.InlineKeyboardButton("⭐ Make VIP", callback_data=f"user_vip_yes_{user_id}"))
    kb.add(types.InlineKeyboardButton("🎟️ Set Free Limit", callback_data=f"user_limit_{user_id}"))
    kb.add(types.InlineKeyboardButton("♻️ Global Default", callback_data=f"user_default_{user_id}"))
    bot.send_message(chat_id,
        f"👤 <b>USER</b>\n\nID: <code>{user_id}</code>\n"
        f"Status: <b>{u['status']}</b>\n"
        f"Wallet: <b>${u['wallet_cents']/100:.2f}</b>\n"
        f"Free limit: <b>{limit}</b> / 2 days {'(custom)' if custom else '(global)'}",
        reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_vip_"))
def user_vip_toggle(call):
    if not can(call.from_user.id, "users"):
        return
    try:
        _, _, decision, uid = call.data.split("_")
        uid = int(uid)
        set_vip(uid, decision == "yes")
        audit(call.from_user.id, "VIP_STATUS", uid, decision)
        bot.answer_callback_query(call.id, "Updated.")
        manage_user_menu(call.message.chat.id, uid)
        try:
            bot.send_message(uid, "⭐ আপনার VIP status update করা হয়েছে: " + ("VIP Active" if decision == "yes" else "VIP Removed"), reply_markup=extra_main_keyboard(uid))
        except Exception:
            pass
    except Exception:
        bot.answer_callback_query(call.id, "Update failed.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_limit_"))
def user_limit_prompt(call):
    if not can(call.from_user.id, "users"): return
    uid = int(call.data.rsplit("_", 1)[1])
    states[call.from_user.id] = {"action": "set_user_limit_direct", "target_user": uid}
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, f"🎟️ User <code>{uid}</code>-এর free signal limit কত হবে? শুধু number পাঠাও।")


@bot.callback_query_handler(func=lambda c: c.data.startswith("user_default_"))
def user_default(call):
    if not can(call.from_user.id, "users"): return
    uid = int(call.data.rsplit("_", 1)[1])
    if set_user_free_limit(uid, None):
        bot.answer_callback_query(call.id, "Global default applied.")
        manage_user_menu(call.message.chat.id, uid)
    else:
        bot.answer_callback_query(call.id, "User not found.")


def save_manage_user_state(message):
    action = states.get(message.from_user.id, {}).get("action")
    if action == "manage_user":
        try:
            uid = int(message.text.strip())
            states.pop(message.from_user.id, None)
            manage_user_menu(message.chat.id, uid)
        except Exception:
            bot.send_message(message.chat.id, "❌ Invalid Telegram ID.")
        return True
    if action == "set_user_limit_direct":
        try:
            uid = int(states[message.from_user.id]["target_user"])
            limit = int(message.text.strip())
            if limit < 0: raise ValueError
            if set_user_free_limit(uid, limit):
                states.pop(message.from_user.id, None)
                manage_user_menu(message.chat.id, uid)
            else:
                bot.send_message(message.chat.id, "❌ User not found.")
        except Exception:
            bot.send_message(message.chat.id, "❌ শুধু 0 বা তার বেশি number দাও।")
        return True
    return False


@bot.callback_query_handler(func=lambda c: c.data == "adm_analytics")
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
                "SELECT COUNT(*) c FROM users WHERE status='VIP'"
            ).fetchone()["c"]
            pending_uid = conn.execute(
                "SELECT COUNT(*) c FROM uid_submissions WHERE status='PENDING'"
            ).fetchone()["c"]
            pending_wd = conn.execute(
                "SELECT COUNT(*) c FROM withdrawals WHERE status='PENDING'"
            ).fetchone()["c"]
            signals = conn.execute("""
                SELECT COUNT(*) c FROM signals
                WHERE signal_at_utc > ?
            """, (utc_iso(now_utc()),)).fetchone()["c"]
            views = conn.execute(
                "SELECT COUNT(*) c FROM signal_access"
            ).fetchone()["c"]
        finally:
            conn.close()

    bot.send_message(
        call.message.chat.id,
        "📈 <b>ANALYTICS</b>\n\n"
        f"👥 Users: <b>{total}</b>\n"
        f"⭐ VIP: <b>{vip}</b>\n"
        f"📊 Upcoming signals: <b>{signals}</b>\n"
        f"👁 Signal deliveries: <b>{views}</b>\n"
        f"🆔 Pending UID: <b>{pending_uid}</b>\n"
        f"💸 Pending withdrawals: <b>{pending_wd}</b>",
        reply_markup=back_admin_keyboard()
    )


# ============================================================
# ADMIN WALLET ADJUST
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == "adm_wallet")
def adm_wallet(call):
    if not can(call.from_user.id, "wallet"):
        return

    states[call.from_user.id] = {"action": "wallet_adjust"}
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "💳 Format:\n"
        "<code>USER_ID AMOUNT</code>\n\n"
        "Positive = add\n"
        "Negative = deduct\n\n"
        "Example:\n"
        "<code>123456789 5</code>\n"
        "<code>123456789 -2</code>"
    )


def save_wallet_adjust(message):
    try:
        parts = message.text.strip().split()
        if len(parts) < 2:
            raise ValueError

        user_id = int(parts[0])
        amount = float(parts[1])
        cents = int(round(amount * 100))

        ok, balance = wallet_adjust(
            user_id,
            cents,
            f"Admin adjustment by {message.from_user.id}"
        )

        states.pop(message.from_user.id, None)

        if not ok:
            bot.send_message(message.chat.id, "❌ User নেই অথবা balance negative হবে।")
            return

        bot.send_message(
            message.chat.id,
            f"✅ Wallet updated.\n"
            f"User: <code>{user_id}</code>\n"
            f"New balance: <b>${balance/100:.2f}</b>",
            reply_markup=admin_keyboard()
        )

        try:
            bot.send_message(
                user_id,
                f"💰 আপনার wallet update হয়েছে।\n"
                f"New balance: <b>${balance/100:.2f}</b>"
            )
        except Exception:
            pass

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Format ভুল। Example: <code>123456789 5</code>"
        )


# ============================================================
# BROADCAST
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == "adm_broadcast")
def adm_broadcast(call):
    if not can(call.from_user.id, "broadcast"):
        return

    states[call.from_user.id] = {"action": "broadcast"}
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "📢 Broadcast message পাঠাও।\n\n"
        "এই message সব registered users-এর কাছে যাবে।"
    )


def do_broadcast(message):
    text = message.text or ""
    states.pop(message.from_user.id, None)

    with db_lock:
        conn = db()
        try:
            rows = conn.execute(
                "SELECT user_id FROM users WHERE blocked=0"
            ).fetchall()
        finally:
            conn.close()

    sent = failed = 0

    for r in rows:
        try:
            bot.send_message(r["user_id"], text)
            sent += 1
            time.sleep(0.05)
        except Exception as e:
            failed += 1
            msg = str(e).lower()
            if "blocked" in msg or "chat not found" in msg or "user is deactivated" in msg:
                with db_lock:
                    conn = db()
                    try:
                        conn.execute(
                            "UPDATE users SET blocked=1 WHERE user_id=?",
                            (r["user_id"],)
                        )
                        conn.commit()
                    finally:
                        conn.close()

    bot.send_message(
        message.chat.id,
        f"📢 Broadcast finished.\n\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}",
        reply_markup=admin_keyboard()
    )


# ============================================================
# SUBADMINS
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == "adm_subadmins")
def adm_subadmins(call):
    if not is_master(call.from_user.id):
        bot.answer_callback_query(call.id, "Master Admin only.")
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("➕ Add Sub-admin", callback_data="sub_add"))
    kb.add(types.InlineKeyboardButton("🗑 Remove Sub-admin", callback_data="sub_remove"))
    kb.add(types.InlineKeyboardButton("📋 Sub-admin List", callback_data="sub_list"))
    kb.add(types.InlineKeyboardButton("⬅️ Admin Panel", callback_data="adm_home"))
    bot.edit_message_text("🛡 <b>SUB-ADMIN MANAGEMENT</b>\n\nChoose an action:", call.message.chat.id, call.message.message_id, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "sub_list")
def sub_list(call):
    if not is_master(call.from_user.id):
        return
    with db_lock:
        conn = db()
        try:
            rows = conn.execute("SELECT user_id,permissions FROM admins ORDER BY user_id").fetchall()
        finally:
            conn.close()
    if rows:
        text = "🛡 <b>SUB-ADMIN LIST</b>\n\n" + "\n".join(
            f"👤 <code>{r['user_id']}</code>\n🔐 {escape(r['permissions'] or 'none')}" for r in rows
        )
    else:
        text = "🛡 <b>SUB-ADMIN LIST</b>\n\nকোনো Sub-admin নেই।"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=back_admin_keyboard())


@bot.callback_query_handler(func=lambda c: c.data == "sub_add")
def sub_add(call):
    if not is_master(call.from_user.id):
        return
    states[call.from_user.id] = {"action": "sub_add_id"}
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "➕ যে user-কে Sub-admin করবে তার Telegram ID পাঠাও।\n\nExample: <code>123456789</code>\n/cancel দিয়ে বাতিল করতে পারো।")


def show_sub_permissions(chat_id, admin_id, selected=None):
    selected = set(selected or get_permissions(admin_id))
    # Master is never stored as sub-admin.
    labels = [
        ("signals", "📊 Signals"), ("uid", "🆔 UID"),
        ("users", "👥 Users"), ("wallet", "💳 Wallet"),
        ("withdraw", "💸 Withdraw"), ("broadcast", "📢 Broadcast"),
        ("analytics", "📈 Analytics")
    ]
    kb = types.InlineKeyboardMarkup(row_width=2)
    for key, label in labels:
        mark = "✅" if key in selected else "⬜"
        kb.add(types.InlineKeyboardButton(f"{mark} {label}", callback_data=f"sp_{key}_{admin_id}"))
    kb.add(types.InlineKeyboardButton("💾 Save Sub-admin", callback_data=f"sp_save_{admin_id}"))
    kb.add(types.InlineKeyboardButton("❌ Cancel", callback_data="adm_subadmins"))
    bot.send_message(chat_id, f"🛡 <b>Permissions for {admin_id}</b>\n\nযে কাজগুলো করতে পারবে সেগুলো ON করো:", reply_markup=kb)
    states[chat_id] = {"action": "sub_permissions", "target_user": admin_id, "permissions": selected}


@bot.callback_query_handler(func=lambda c: c.data.startswith("sp_"))
def sub_permission_callback(call):
    if not is_master(call.from_user.id):
        return
    parts = call.data.split("_")
    if len(parts) < 3:
        return
    action = parts[1]
    try:
        uid = int(parts[-1])
    except ValueError:
        return
    st = states.get(call.from_user.id, {})
    if st.get("action") != "sub_permissions" or st.get("target_user") != uid:
        bot.answer_callback_query(call.id, "Session expired. Start again.")
        return
    perms = set(st.get("permissions", set()))
    if action == "save":
        if not perms:
            bot.answer_callback_query(call.id, "কমপক্ষে ১টি permission নির্বাচন করো।")
            return
        add_subadmin(uid, perms)
        audit(call.from_user.id, "ADD_OR_UPDATE_SUBADMIN", uid, ",".join(sorted(perms)))
        states.pop(call.from_user.id, None)
        bot.answer_callback_query(call.id, "Saved")
        bot.edit_message_text(f"✅ <b>Sub-admin saved</b>\n\nUser: <code>{uid}</code>\nPermissions: <b>{escape(', '.join(sorted(perms)))}</b>", call.message.chat.id, call.message.message_id, reply_markup=back_admin_keyboard())
        return
    if action not in ALL_PERMISSIONS or action == "settings":
        return
    if action in perms:
        perms.remove(action)
    else:
        perms.add(action)
    st["permissions"] = perms
    states[call.from_user.id] = st
    bot.answer_callback_query(call.id)
    # Replace current permission message with fresh buttons.
    labels = [("signals","📊 Signals"),("uid","🆔 UID"),("users","👥 Users"),("wallet","💳 Wallet"),("withdraw","💸 Withdraw"),("broadcast","📢 Broadcast"),("analytics","📈 Analytics")]
    kb = types.InlineKeyboardMarkup(row_width=2)
    for key,label in labels:
        kb.add(types.InlineKeyboardButton(("✅ " if key in perms else "⬜ ")+label, callback_data=f"sp_{key}_{uid}"))
    kb.add(types.InlineKeyboardButton("💾 Save Sub-admin", callback_data=f"sp_save_{uid}"))
    kb.add(types.InlineKeyboardButton("❌ Cancel", callback_data="adm_subadmins"))
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "sub_remove")
def sub_remove(call):
    if not is_master(call.from_user.id):
        return
    with db_lock:
        conn = db()
        try:
            rows = conn.execute("SELECT user_id FROM admins ORDER BY user_id").fetchall()
        finally:
            conn.close()
    if not rows:
        bot.answer_callback_query(call.id, "No sub-admins.")
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for r in rows:
        kb.add(types.InlineKeyboardButton(f"🗑 Remove {r['user_id']}", callback_data=f"sub_del_{r['user_id']}"))
    kb.add(types.InlineKeyboardButton("⬅️ Back", callback_data="adm_subadmins"))
    bot.edit_message_text("🗑 <b>REMOVE SUB-ADMIN</b>\n\nযাকে remove করবে তাকে চাপো:", call.message.chat.id, call.message.message_id, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("sub_del_"))
def sub_delete_callback(call):
    if not is_master(call.from_user.id):
        return
    try:
        uid = int(call.data.rsplit("_",1)[1])
        remove_subadmin(uid)
        audit(call.from_user.id, "REMOVE_SUBADMIN", uid)
        bot.answer_callback_query(call.id, "Removed")
        adm_subadmins(call)
    except Exception:
        bot.answer_callback_query(call.id, "Failed")


# ============================================================

# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == "adm_settings")
def adm_settings(call):
    if not is_master(call.from_user.id):
        bot.answer_callback_query(call.id, "Master Admin only.")
        return
    maintenance = get_setting("maintenance", "0") == "1"
    live = get_setting("live_mode", "1") == "1"
    wd = withdraw_enabled()
    hold = withdraw_hold()
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(f"Maintenance {'ON' if maintenance else 'OFF'}", callback_data="set_maintenance"),
        types.InlineKeyboardButton(f"Live {'ON' if live else 'OFF'}", callback_data="set_live")
    )
    kb.add(
        types.InlineKeyboardButton(f"Withdraw {'ON' if wd else 'OFF'}", callback_data="set_withdraw"),
        types.InlineKeyboardButton(f"Hold {'ON' if hold else 'OFF'}", callback_data="set_hold")
    )
    kb.add(
        types.InlineKeyboardButton("🎟️ Free Limit", callback_data="set_free_limit"),
        types.InlineKeyboardButton("🎁 Referral Bonus", callback_data="set_ref_bonus")
    )
    kb.add(
        types.InlineKeyboardButton("💵 Min Withdraw", callback_data="set_min_withdraw"),
        types.InlineKeyboardButton("⭐ VIP Deposit", callback_data="set_vip_deposit")
    )
    kb.add(
        types.InlineKeyboardButton("📢 Edit Notice", callback_data="set_notice"),
        types.InlineKeyboardButton("📖 Edit Trading Rules", callback_data="set_rules")
    )
    kb.add(types.InlineKeyboardButton("👤 User Free Limit", callback_data="set_user_limit"))
    kb.add(types.InlineKeyboardButton(f"🔔 Notifications {'ON' if notifications_enabled_global() else 'OFF'}", callback_data="set_notifications"))
    kb.add(types.InlineKeyboardButton("🎯 Confidence Label", callback_data="set_confidence"))
    kb.add(types.InlineKeyboardButton("⬅️ Admin Panel", callback_data="adm_home"))
    bot.edit_message_text(
        "⚙️ <b>SETTINGS</b>\n\n"
        f"🎟️ Global free signal: <b>{free_signal_limit()}</b> / 2 days\n"
        f"🎁 Referral bonus: <b>${referral_bonus_cents()/100:.2f}</b>\n"
        f"💵 Minimum withdraw: <b>${min_withdraw_cents()/100:.2f}</b>\n"
        f"⭐ VIP deposit: <b>${vip_deposit_cents()/100:.2f}</b>\n"
        f"💸 Withdraw: <b>{'ON' if wd else 'OFF'}</b> | Hold: <b>{'ON' if hold else 'OFF'}</b>\n"
        f"🛠️ Maintenance: <b>{'ON' if maintenance else 'OFF'}</b> | Live: <b>{'ON' if live else 'OFF'}</b>",
        call.message.chat.id, call.message.message_id, reply_markup=kb
    )


def setting_prompt(call, action, text):
    if not is_master(call.from_user.id):
        return
    states[call.from_user.id] = {"action": action}
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text + "\n\n/cancel দিয়ে বাতিল করতে পারো।")


@bot.callback_query_handler(func=lambda c: c.data == "set_withdraw")
def set_withdraw(call):
    if not is_master(call.from_user.id): return
    set_setting("withdraw_enabled", "0" if withdraw_enabled() else "1")
    adm_settings(call)


@bot.callback_query_handler(func=lambda c: c.data == "set_hold")
def set_hold(call):
    if not is_master(call.from_user.id): return
    set_setting("withdraw_hold", "0" if withdraw_hold() else "1")
    adm_settings(call)


@bot.callback_query_handler(func=lambda c: c.data == "set_free_limit")
def set_free_limit(call):
    setting_prompt(call, "set_free_limit", "🎟️ Global free signal limit কত হবে? শুধু number পাঠাও।\nExample: 6")


@bot.callback_query_handler(func=lambda c: c.data == "set_ref_bonus")
def set_ref_bonus(call):
    setting_prompt(call, "set_ref_bonus", "🎁 Referral bonus কত USD হবে?\nExample: 1 or 2.50")


@bot.callback_query_handler(func=lambda c: c.data == "set_min_withdraw")
def set_min_withdraw(call):
    setting_prompt(call, "set_min_withdraw", "💵 Minimum withdraw কত USD হবে?\nExample: 5 or 10")


@bot.callback_query_handler(func=lambda c: c.data == "set_vip_deposit")
def set_vip_deposit(call):
    setting_prompt(call, "set_vip_deposit", "⭐ VIP deposit কত USD হবে?\nExample: 15")


@bot.callback_query_handler(func=lambda c: c.data == "set_notice")
def set_notice(call):
    setting_prompt(call, "set_notice", "📢 নতুন Notice text পাঠাও।")


@bot.callback_query_handler(func=lambda c: c.data == "set_rules")
def set_rules(call):
    setting_prompt(call, "set_rules", "📖 নতুন Trading Rules text পাঠাও।")


@bot.callback_query_handler(func=lambda c: c.data == "set_user_limit")
def set_user_limit(call):
    setting_prompt(call, "set_user_limit", "👤 Format: USER_ID LIMIT\nExample: <code>123456789 10</code>\nFree signal বন্ধ করতে LIMIT হিসেবে <code>0</code> দাও। Global limit-এ ফেরত দিতে <code>123456789 default</code> পাঠাও।")


def save_setting_state(message):
    action = states.get(message.from_user.id, {}).get("action")
    value = (message.text or "").strip()
    try:
        if action == "set_free_limit":
            set_setting("free_signal_limit", str(max(0, int(value))))
            result = f"🎟️ Global free limit এখন {free_signal_limit()} / 2 days"
        elif action == "set_ref_bonus":
            cents = int(round(float(value) * 100))
            if cents < 0: raise ValueError
            set_setting("referral_bonus_cents", cents)
            result = f"🎁 Referral bonus এখন ${cents/100:.2f}"
        elif action == "set_min_withdraw":
            cents = int(round(float(value) * 100))
            if cents < 0: raise ValueError
            set_setting("min_withdraw_cents", cents)
            result = f"💵 Minimum withdraw এখন ${cents/100:.2f}"
        elif action == "set_vip_deposit":
            cents = int(round(float(value) * 100))
            if cents < 0: raise ValueError
            set_setting("vip_deposit_cents", cents)
            result = f"⭐ VIP deposit এখন ${cents/100:.2f}"
        elif action == "set_notice":
            set_setting("notice", value[:4000])
            result = "📢 Notice updated."
        elif action == "set_confidence":
            if len(value) > 50: raise ValueError
            set_setting("signal_confidence", value)
            result = f"🎯 Confidence label: {escape(value)}"
        elif action == "set_rules":
            set_setting("trading_rules", value)
            result = "📖 Trading Rules updated."
        elif action == "set_user_limit":
            parts = value.split(maxsplit=1)
            if len(parts) != 2: raise ValueError
            uid = int(parts[0])
            if parts[1].strip().lower() == "default":
                ok = set_user_free_limit(uid, None)
                result = "👤 User limit global default-এ ফিরেছে." if ok else "❌ User not found."
            else:
                limit = int(parts[1])
                ok = set_user_free_limit(uid, limit)
                result = f"👤 User {uid} free limit = {limit} / 2 days" if ok else "❌ User not found."
        else:
            return False
        states.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, "✅ " + result, reply_markup=admin_keyboard())
        return True
    except Exception:
        bot.send_message(message.chat.id, "❌ Format ভুল। আবার চেষ্টা করো।")
        return True


@bot.callback_query_handler(func=lambda c: c.data == "set_notifications")
def set_notifications(call):
    if not is_master(call.from_user.id):
        return
    set_setting("notifications_enabled", "0" if notifications_enabled_global() else "1")
    audit(call.from_user.id, "TOGGLE_GLOBAL_NOTIFICATIONS", None, get_setting("notifications_enabled"))
    adm_settings(call)


@bot.callback_query_handler(func=lambda c: c.data == "set_confidence")
def set_confidence(call):
    setting_prompt(call, "set_confidence", "🎯 Signal confidence label কী হবে?\nExample: 95–99%\nএটি guarantee নয়; শুধু stated confidence label হিসেবে দেখানো হবে।")


# ============================================================
# NOTIFICATION ADMIN
# ============================================================

def broadcast_notice_to_users(admin_chat_id):
    text = get_setting("notice", "").strip()
    if not text:
        bot.send_message(admin_chat_id, "📢 আগে Notice সেট করো।")
        return
    with db_lock:
        conn = db()
        try:
            rows = conn.execute("SELECT user_id FROM users WHERE blocked=0").fetchall()
        finally:
            conn.close()
    sent = failed = 0
    for r in rows:
        try:
            bot.send_message(r["user_id"], "📢 <b>NOTICE</b>\n\n" + escape(text))
            sent += 1
            time.sleep(0.05)
        except Exception as e:
            failed += 1
            if any(x in str(e).lower() for x in ("blocked", "chat not found", "deactivated")):
                with db_lock:
                    conn=db()
                    try:
                        conn.execute("UPDATE users SET blocked=1 WHERE user_id=?", (r["user_id"],)); conn.commit()
                    finally: conn.close()
    bot.send_message(admin_chat_id, f"📢 Notice sent.\n\n✅ Sent: {sent}\n❌ Failed: {failed}", reply_markup=admin_keyboard())


@bot.callback_query_handler(func=lambda c: c.data == "adm_notifications")
def adm_notifications(call):
    if not is_master(call.from_user.id):
        bot.answer_callback_query(call.id, "Master Admin only.")
        return
    bot.edit_message_text(
        "🔔 <b>NOTIFICATION SETTINGS</b>\n\n"
        f"Global scheduled notifications: <b>{'ON' if notifications_enabled_global() else 'OFF'}</b>\n"
        "Users can individually turn notifications ON/OFF from their menu.",
        call.message.chat.id, call.message.message_id, reply_markup=back_admin_keyboard())


@bot.callback_query_handler(func=lambda c: c.data == "adm_notice_send")
def adm_notice_send(call):
    if not is_master(call.from_user.id):
        bot.answer_callback_query(call.id, "Master Admin only.")
        return
    bot.answer_callback_query(call.id, "Sending...")
    broadcast_notice_to_users(call.message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "set_maintenance")
def set_maintenance(call):
    if not is_master(call.from_user.id): return
    old = get_setting("maintenance", "0")
    set_setting("maintenance", "0" if old == "1" else "1")
    adm_settings(call)


@bot.callback_query_handler(func=lambda c: c.data == "set_live")
def set_live(call):
    if not is_master(call.from_user.id): return
    old = get_setting("live_mode", "1")
    set_setting("live_mode", "0" if old == "1" else "1")
    adm_settings(call)


# ============================================================
# PERSISTENT INTERACTIVE STATE HANDLER
# ============================================================

@bot.message_handler(content_types=["text"], func=lambda m: m.from_user.id in states)
def state_handler(message):
    action = states.get(message.from_user.id, {}).get("action")

    if save_manage_user_state(message):
        return

    if action == "extra_daily_balance":
        try:
            cents=int(round(float((message.text or "").strip())*100))
            if cents<=0: raise ValueError
            mm_set_balance(message.from_user.id,cents)
            states.pop(message.from_user.id,None)
            r=mm_get(message.from_user.id)
            bot.send_message(message.chat.id,f"✅ আজকের MM set হয়েছে.\n\n💵 Balance: <b>${cents/100:.2f}</b>\n💰 Base Trade: <b>${r['base_cents']/100:.2f}</b>\n🎯 Target: <b>{r['target_percent']:.2f}%</b>\n🛑 Max Daily Loss: <b>${r['max_daily_loss_cents']/100:.2f}</b>",reply_markup=extra_main_keyboard(message.from_user.id))
        except Exception:
            bot.send_message(message.chat.id,"❌ সঠিক USD amount দাও। Example: 100")
        return

    if action in {"set_free_limit", "set_ref_bonus", "set_min_withdraw", "set_vip_deposit", "set_notice", "set_rules", "set_user_limit", "set_confidence"}:
        save_setting_state(message)
    elif action == "uid":
        save_uid(message)
    elif action == "withdraw_amount" or action == "withdraw_method" or action == "withdraw_account":
        finish_withdraw(message)
    elif action == "add_signal":
        admin_save_signal(message)
    elif action == "wallet_adjust":
        save_wallet_adjust(message)
    elif action == "broadcast":
        do_broadcast(message)
    elif action == "sub_add_id":
        try:
            uid = int((message.text or '').strip())
            if uid == ADMIN_ID: raise ValueError
            if not get_user(uid):
                bot.send_message(message.chat.id, "❌ আগে ওই user-কে Bot-এ /start দিতে হবে।")
                return
            states.pop(message.from_user.id, None)
            show_sub_permissions(message.chat.id, uid, set())
        except Exception:
            bot.send_message(message.chat.id, "❌ সঠিক Telegram ID দাও।")


# ============================================================
# FALLBACK
# ============================================================

@bot.message_handler(content_types=["text"])
def fallback(message):
    if message.text.startswith("/"):
        return

    bot.send_message(
        message.chat.id,
        "আমি এই option বুঝতে পারিনি। নিচের menu ব্যবহার করুন।",
        reply_markup=extra_main_keyboard(message.from_user.id)
    )


# ============================================================
# AUTOMATIC SIGNAL NOTIFICATIONS
# ============================================================

def send_due_signal_notifications():
    if not notifications_enabled_global():
        return
    current = utc_iso(now_utc())
    with db_lock:
        conn = db()
        try:
            signals = conn.execute("SELECT * FROM signals WHERE signal_at_utc <= ? ORDER BY signal_at_utc ASC LIMIT 20", (current,)).fetchall()
            users = conn.execute("SELECT user_id FROM users WHERE blocked=0 AND notifications_enabled=1").fetchall()
        finally:
            conn.close()
    for signal in signals:
        for u in users:
            uid = u["user_id"]
            with db_lock:
                conn = db()
                try:
                    already = conn.execute("SELECT 1 FROM signal_notifications WHERE user_id=? AND signal_id=?", (uid, signal["id"])).fetchone()
                finally:
                    conn.close()
            if already:
                continue
            ok, reason = deliver_signal(uid, signal["id"])
            if not ok:
                if reason in ("limit", "already"):
                    if reason == "already":
                        with db_lock:
                            conn=db()
                            try:
                                conn.execute("INSERT OR IGNORE INTO signal_notifications(user_id,signal_id,sent_at) VALUES(?,?,?)", (uid, signal["id"], utc_iso(now_utc()))); conn.commit()
                            finally: conn.close()
                continue
            process_referral_bonus(uid)
            t = bd_from_iso(signal["signal_at_utc"])
            user = get_user(uid)
            quota = "♾️ VIP Unlimited" if user and user["status"] == "VIP" else f"🎟️ Remaining: <b>{max(0, free_signal_limit_for(uid)-user['free_used'])}/{free_signal_limit_for(uid)}</b>"
            msg = (
                "🔔 <b>SIGNAL ALERT</b>\n\n"
                f"🕐 BD Time: <b>{t.strftime('%d-%m-%Y')}</b> <b>{format_signal_time(t)}</b>\n"
                f"{format_signal_text(signal['signal_text'])}\n\n"
                f"{quota}\n"
                f"🎯 Stated confidence: <b>{escape(signal_confidence())}</b>\n"
                "📌 Confidence is a stated estimate, not a guarantee."
            )
            try:
                bot.send_message(uid, msg)
                with db_lock:
                    conn=db()
                    try:
                        conn.execute("INSERT OR IGNORE INTO signal_notifications(user_id,signal_id,sent_at) VALUES(?,?,?)", (uid, signal["id"], utc_iso(now_utc()))); conn.commit()
                    finally: conn.close()
            except Exception as e:
                low = str(e).lower()
                if "blocked" in low or "chat not found" in low or "deactivated" in low:
                    with db_lock:
                        conn=db()
                        try:
                            conn.execute("UPDATE users SET blocked=1 WHERE user_id=?", (uid,)); conn.commit()
                        finally: conn.close()


def notification_loop():
    while True:
        try:
            with db_lock:
                c=db(); rows=c.execute("SELECT user_id,expires_at_utc,last_reminder_date FROM vip_memberships").fetchall(); c.close()
            today=now_bd().date().isoformat()
            for r in rows:
                try:
                    exp=datetime.fromisoformat(r["expires_at_utc"]); days=(exp-now_utc()).total_seconds()/86400
                    if 0 < days <= int(get_setting("vip_reminder_days","3")) and r["last_reminder_date"]!=today:
                        bot.send_message(r["user_id"],f"⏳ <b>VIP Reminder</b>\n\nআপনার VIP প্রায় <b>{max(1,int(days))} দিন</b>-এর মধ্যে expire হবে।")
                        with db_lock:
                            c=db(); c.execute("UPDATE vip_memberships SET last_reminder_date=? WHERE user_id=?",(today,r["user_id"])); c.commit(); c.close()
                    elif days <= 0:
                        with db_lock:
                            c=db(); c.execute("UPDATE users SET status='FREE' WHERE user_id=?",(r["user_id"],)); c.commit(); c.close()
                except Exception: pass
            send_due_signal_notifications()
        except Exception:
            logger.exception("notification loop error")
        time.sleep(10)


# ============================================================
# BACKUP
# ============================================================

def backup_database():
    try:
        if not os.path.exists(DB_FILE):
            return

        stamp = now_bd().strftime("%Y%m%d_%H%M%S")
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

        # Keep newest 7 backups
        files = sorted(
            [
                os.path.join(BACKUP_DIR, f)
                for f in os.listdir(BACKUP_DIR)
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

        logger.info("Database backup created: %s", path)

    except Exception:
        logger.exception("Backup failed")


def backup_loop():
    while True:
        time.sleep(6 * 60 * 60)
        backup_database()


# ============================================================
# STARTUP / POLLING
# ============================================================

def main():
    init_db()
    backup_database()

    threading.Thread(
        target=backup_loop,
        daemon=True
    ).start()

    threading.Thread(
        target=notification_loop,
        daemon=True
    ).start()

    logger.info("Bot is starting...")

    while True:
        try:
            bot.infinity_polling(
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30
            )
        except Exception:
            logger.exception("Polling crashed; restarting in 5 seconds.")
            time.sleep(5)



# ============================================================
# EXTRA FEATURES: VIP TIERS/REMINDERS, 1-STEP M1 MM, NAVIGATION
# ============================================================

def init_extra_db():
    with db_lock:
        conn = db()
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS vip_memberships (
                user_id INTEGER PRIMARY KEY,
                expires_at_utc TEXT,
                last_reminder_date TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS mm_daily (
                user_id INTEGER PRIMARY KEY,
                day_key TEXT NOT NULL,
                balance_cents INTEGER NOT NULL DEFAULT 0,
                base_cents INTEGER NOT NULL DEFAULT 0,
                current_cents INTEGER NOT NULL DEFAULT 0,
                stage TEXT NOT NULL DEFAULT 'BASE',
                stopped INTEGER NOT NULL DEFAULT 0,
                max_daily_loss_cents INTEGER NOT NULL DEFAULT 0,
                daily_loss_cents INTEGER NOT NULL DEFAULT 0,
                daily_profit_cents INTEGER NOT NULL DEFAULT 0,
                trades INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                target_percent REAL NOT NULL DEFAULT 1.85,
                max_trades INTEGER NOT NULL DEFAULT 20,
                UNIQUE(user_id)
            );
            CREATE TABLE IF NOT EXISTS mm_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                result TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                pnl_cents INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, signal_id),
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE
            );
            """)
            conn.commit()
        finally:
            conn.close()

_orig_init_db = init_db
def init_db():
    _orig_init_db()
    init_extra_db()


def vip_expiry(user_id):
    with db_lock:
        conn=db()
        try:
            r=conn.execute("SELECT expires_at_utc FROM vip_memberships WHERE user_id=?",(user_id,)).fetchone()
            return r["expires_at_utc"] if r else None
        finally: conn.close()


def vip_is_active(user_id):
    u=get_user(user_id)
    if not u or u["status"]!="VIP": return False
    exp=vip_expiry(user_id)
    if not exp: return True
    if datetime.fromisoformat(exp) <= now_utc():
        with db_lock:
            c=db(); c.execute("UPDATE users SET status='FREE' WHERE user_id=?",(user_id,)); c.commit(); c.close()
        return False
    return True


def set_vip_days_extra(user_id, days):
    exp=now_utc()+timedelta(days=days)
    with db_lock:
        c=db()
        c.execute("INSERT INTO vip_memberships(user_id,expires_at_utc,last_reminder_date) VALUES(?,?,NULL) ON CONFLICT(user_id) DO UPDATE SET expires_at_utc=excluded.expires_at_utc,last_reminder_date=NULL",(user_id,utc_iso(exp)))
        c.execute("UPDATE users SET status='VIP' WHERE user_id=?",(user_id,))
        c.commit(); c.close()


def mm_reset_if_new_day(user_id):
    day=now_bd().date().isoformat()
    with db_lock:
        c=db(); r=c.execute("SELECT day_key FROM mm_daily WHERE user_id=?",(user_id,)).fetchone()
        if not r:
            c.execute("INSERT INTO mm_daily(user_id,day_key,target_percent,max_trades) VALUES(?,?,?,?)",(user_id,day,float(get_setting('profit_target_percent','1.85')),int(get_setting('max_trades_per_day','20'))))
        elif r["day_key"]!=day:
            c.execute("UPDATE mm_daily SET day_key=?,balance_cents=0,base_cents=0,current_cents=0,stage='BASE',stopped=0,max_daily_loss_cents=0,daily_loss_cents=0,daily_profit_cents=0,trades=0,wins=0,losses=0,target_percent=?,max_trades=? WHERE user_id=?",(day,float(get_setting('profit_target_percent','1.85')),int(get_setting('max_trades_per_day','20')),user_id))
        c.commit(); c.close()


def mm_get(user_id):
    mm_reset_if_new_day(user_id)
    with db_lock:
        c=db(); r=c.execute("SELECT * FROM mm_daily WHERE user_id=?",(user_id,)).fetchone(); c.close(); return r


def mm_set_balance(user_id, balance_cents):
    mm_reset_if_new_day(user_id)
    base=max(1,int(round(balance_cents*float(get_setting('base_trade_percent','2'))/100)))
    m1=max(base,int(round(balance_cents*float(get_setting('max_m1_percent','4'))/100)))
    maxloss=max(0,int(round(balance_cents*float(get_setting('max_daily_loss_percent','5'))/100)))
    maxtr=max(0,int(get_setting('max_trades_per_day','20')))
    with db_lock:
        c=db(); c.execute("UPDATE mm_daily SET balance_cents=?,base_cents=?,current_cents=?,stage='BASE',stopped=0,max_daily_loss_cents=?,max_trades=?,target_percent=? WHERE user_id=?",(balance_cents,base,base,maxloss,maxtr,float(get_setting('profit_target_percent','1.85')),user_id)); c.commit(); c.close()


def mm_trade(user_id):
    r=mm_get(user_id)
    if not r or r["balance_cents"]<=0 or r["stopped"]: return 0,"STOP"
    if r["max_daily_loss_cents"] and r["daily_loss_cents"]>=r["max_daily_loss_cents"]: return 0,"STOP"
    if r["max_trades"] and r["trades"]>=r["max_trades"]: return 0,"STOP"
    if r["daily_profit_cents"]>=int(round(r["balance_cents"]*r["target_percent"]/100)): return 0,"STOP"
    return r["current_cents"] or r["base_cents"],r["stage"]


def mm_record(user_id, signal_id, result):
    result=result.upper()
    if result not in ("WIN","LOSS","SKIP"): return False,"bad"
    r=mm_get(user_id)
    if not r or r["balance_cents"]<=0: return False,"balance"
    with db_lock:
        c=db()
        if c.execute("SELECT 1 FROM mm_results WHERE user_id=? AND signal_id=?",(user_id,signal_id)).fetchone(): c.close(); return False,"already"
        amount=r["current_cents"] or r["base_cents"]
        if result=="SKIP":
            next_amount,next_stage=amount,r["stage"]; pnl=0; trades=0
        elif result=="WIN":
            next_amount,next_stage=r["base_cents"],"BASE"; pnl=int(round(amount*0.85)); trades=1
        else:
            if r["stage"]=="BASE":
                next_amount=min(max(r["base_cents"]*2,r["base_cents"]),max(r["base_cents"],int(round(r["balance_cents"]*float(get_setting('max_m1_percent','4'))/100)))); next_stage="M1"
            else:
                next_amount,next_stage=r["base_cents"],"BASE"
            pnl=-amount; trades=1
        loss=r["daily_loss_cents"]+(amount if result=="LOSS" else 0)
        profit=r["daily_profit_cents"]+pnl
        tr=r["trades"]+trades
        stop=1 if (r["max_daily_loss_cents"] and loss>=r["max_daily_loss_cents"]) or (r["max_trades"] and tr>=r["max_trades"]) or profit>=int(round(r["balance_cents"]*r["target_percent"]/100)) else 0
        c.execute("INSERT INTO mm_results(user_id,signal_id,result,amount_cents,pnl_cents,created_at) VALUES(?,?,?,?,?,?)",(user_id,signal_id,result,amount,pnl,utc_iso(now_utc())))
        c.execute("UPDATE mm_daily SET current_cents=?,stage=?,stopped=?,daily_loss_cents=?,daily_profit_cents=?,trades=?,wins=wins+?,losses=losses+? WHERE user_id=?",(next_amount,next_stage,stop,loss,profit,tr,1 if result=="WIN" else 0,1 if result=="LOSS" else 0,user_id))
        c.commit(); c.close()
    return True,(next_amount,next_stage,stop)


def mm_status_text(user_id):
    r=mm_get(user_id)
    if not r or r["balance_cents"]<=0: return "⚠️ আজকের Trading Balance এখনো set করা হয়নি।"
    return (f"💰 Balance: <b>${r['balance_cents']/100:.2f}</b>\n"
            f"🎯 Target: <b>{r['target_percent']:.2f}%</b>\n"
            f"📌 Current: <b>${(r['current_cents'] or r['base_cents'])/100:.2f}</b> <b>{r['stage']}</b>\n"
            f"📊 WIN/LOSS: <b>{r['wins']}/{r['losses']}</b>\n"
            f"📈 P/L: <b>${r['daily_profit_cents']/100:.2f}</b>\n"
            f"🛑 Loss: <b>${r['daily_loss_cents']/100:.2f}</b> /${r['max_daily_loss_cents']/100:.2f}\n"
            f"🔢 Trades: <b>{r['trades']}/{r['max_trades'] or '∞'}</b>\n"
            f"{'🛑 STOPPED' if r['stopped'] else '🟢 ACTIVE'}")


def extra_main_keyboard(user_id):
    kb=types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📢 Notice","📊 Future Signals")
    kb.row("⚡ Live Signals","👤 My Status")
    kb.row("💰 Wallet","👥 Referral Link")
    kb.row("⭐ VIP Rules",[span_2](start_span)[span_3](start_span)"[span_2](end_span)[span_3](end_span)

