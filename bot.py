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
REFERRAL_BONUS_CENTS = 100       # $1.00
MIN_WITHDRAW_CENTS = 500         # $5.00

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

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            INSERT OR IGNORE INTO settings(key, value)
                VALUES('maintenance', '0');

            INSERT OR IGNORE INTO settings(key, value)
                VALUES('live_mode', '1');
            """)
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
            """, (REFERRAL_BONUS_CENTS, referrer))

            new_balance = conn.execute(
                "SELECT wallet_cents FROM users WHERE user_id=?",
                (referrer,)
            ).fetchone()["wallet_cents"]

            conn.execute("""
                INSERT INTO wallet_transactions(
                    user_id,type,amount_cents,balance_after_cents,note,created_at
                ) VALUES(?,?,?,?,?,?)
            """, (
                referrer, "REFERRAL_BONUS", REFERRAL_BONUS_CENTS,
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
                    f"আপনার wallet-এ <b>${REFERRAL_BONUS_CENTS/100:.2f}</b> "
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

    m = re.match(
        r"^(\d{1,2}):(\d{2})\s*[-|]\s*(.+)$",
        line
    )
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            return None

        target = datetime.combine(
            now_bd().date(),
            dt_time(hour, minute),
            tzinfo=BD_TZ
        )
        if target <= now_bd():
            target += timedelta(days=1)

        return m.group(3).strip(), target

    m = re.match(
        r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s*[-|]\s*(.+)$",
        line
    )
    if m:
        try:
            target = datetime.strptime(
                f"{m.group(1)} {int(m.group(2)):02d}:{int(m.group(3)):02d}",
                "%Y-%m-%d %H:%M"
            ).replace(tzinfo=BD_TZ)
        except ValueError:
            return None

        return m.group(4).strip(), target

    return None


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

                if used >= FREE_SIGNALS_PER_CYCLE:
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
    kb.row("📊 Future Signals", "⚡ Live Signals")
    kb.row("🆔 Submit Quotex UID", "👤 My Status")
    kb.row("💰 Wallet", "👥 Referral Link")
    kb.row("📖 VIP Rules")
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
        types.InlineKeyboardButton("🗑 Clear Future", callback_data="adm_clear")
    )
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
            f"🎟️ Non-VIP: প্রতি ২ দিনে সর্বোচ্চ "
            f"<b>{FREE_SIGNALS_PER_CYCLE}টি</b> free signal.\n"
            "⭐ VIP: এই limit নেই।\n\n"
            "⚠️ Signals informational only; কোনো profit guarantee নেই।",
            reply_markup=main_keyboard(message.from_user.id)
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
        reply_markup=main_keyboard(message.from_user.id)
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

    if user["status"] != "VIP" and user["free_used"] >= FREE_SIGNALS_PER_CYCLE:
        bot.send_message(
            message.chat.id,
            "⛔ <b>Free signal quota শেষ</b>\n\n"
            f"এই ২ দিনের cycle-এ আপনার {FREE_SIGNALS_PER_CYCLE}টি signal শেষ হয়েছে।\n"
            "পরবর্তী cycle শুরু হলে quota আবার reset হবে।\n\n"
            "⭐ VIP হলে এই limit থাকবে না।"
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
        remaining = max(0, FREE_SIGNALS_PER_CYCLE - after["free_used"])
        quota = (
            f"🎟️ Remaining: <b>{remaining}/"
            f"{FREE_SIGNALS_PER_CYCLE}</b>"
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
        f"🕐 BD Time: <b>{t.strftime('%d-%m-%Y %I:%M %p')}</b>\n"
        f"📌 <b>{escape(signal['signal_text'])}</b>\n\n"
        f"{quota}\n\n"
        "⚠️ Informational only. No guaranteed profit.",
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
        quota = f"{max(0, FREE_SIGNALS_PER_CYCLE-u['free_used'])}/{FREE_SIGNALS_PER_CYCLE}"

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


@bot.message_handler(func=lambda m: m.text == "📖 VIP Rules")
def vip_rules(message):
    bot.send_message(
        message.chat.id,
        "⭐ <b>VIP</b>\n\n"
        "VIP user-এর Future Signal limit থাকবে না。\n\n"
        f"👤 Non-VIP: প্রতি ২ দিনে {FREE_SIGNALS_PER_CYCLE}টি free signal.\n\n"
        "UID verification admin-এর মাধ্যমে করা হবে।\n\n"
        f"🔗 Referral/registration link:\n{escape(QUOTEX_REF_LINK)}\n\n"
        "⚠️ কোনো guaranteed profit claim করা হচ্ছে না।"
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
            f"Successful referral bonus: <b>${REFERRAL_BONUS_CENTS/100:.2f}</b> "
            "once per referred user."
        )
    except Exception:
        bot.send_message(message.chat.id, "❌ Referral link তৈরি করা যায়নি।")


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
        bot.send_message(message.chat.id, "⭐ আপনি ইতিমধ্যে VIP।")
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
                    WHERE id=? AND status='PENDING'
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
        f"Minimum withdraw: <b>${MIN_WITHDRAW_CENTS/100:.2f}</b>",
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda c: c.data == "user_withdraw")
def withdraw_start(call):
    if maintenance_blocked(call.from_user.id):
        bot.answer_callback_query(call.id, "Maintenance mode.")
        return

    if wallet_balance(call.from_user.id) < MIN_WITHDRAW_CENTS:
        bot.answer_callback_query(
            call.id,
            f"Minimum ${MIN_WITHDRAW_CENTS/100:.2f} required."
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

            cur = conn.execute("""
                INSERT INTO withdrawals(
                    user_id,amount_cents,method,account,status,created_at
                ) VALUES(?,?,?,?,'PENDING',?)
            """, (
                user_id, amount_cents, method, account,
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

        if cents < MIN_WITHDRAW_CENTS:
            bot.send_message(
                message.chat.id,
                f"❌ Minimum ${MIN_WITHDRAW_CENTS/100:.2f}."
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
                f"#{r['id']} | {t.strftime('%d-%m %I:%M %p')} | "
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
                WHERE status='PENDING'
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

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "➕ Add/Update Sub-admin", callback_data="sub_add"
        )
    )
    kb.add(
        types.InlineKeyboardButton(
            "🗑 Remove Sub-admin", callback_data="sub_remove"
        )
    )

    with db_lock:
        conn = db()
        try:
            rows = conn.execute(
                "SELECT user_id,permissions FROM admins ORDER BY user_id"
            ).fetchall()
        finally:
            conn.close()

    if rows:
        text = "🛡 <b>SUB-ADMINS</b>\n\n" + "\n".join(
            f"<code>{r['user_id']}</code> → {escape(r['permissions'])}"
            for r in rows
        )
    else:
        text = "🛡 <b>SUB-ADMINS</b>\n\nNone."

    bot.send_message(call.message.chat.id, text, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "sub_add")
def sub_add(call):
    if not is_master(call.from_user.id):
        return

    states[call.from_user.id] = {"action": "sub_add"}
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "Format:\n"
        "<code>USER_ID signals,uid,users,wallet,withdraw,broadcast,analytics</code>\n\n"
        "Example:\n"
        "<code>123456789 signals,uid,analytics</code>"
    )


def save_subadmin(message):
    try:
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError

        uid = int(parts[0])
        permissions = {
            p.strip() for p in parts[1].split(",")
            if p.strip() in ALL_PERMISSIONS
        }

        if not permissions:
            raise ValueError

        add_subadmin(uid, permissions)
        states.pop(message.from_user.id, None)

        bot.send_message(
            message.chat.id,
            "✅ Sub-admin saved.\n"
            f"User: <code>{uid}</code>\n"
            f"Permissions: <b>{escape(', '.join(sorted(permissions)))}</b>",
            reply_markup=admin_keyboard()
        )
    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Format ভুল। আবার চেষ্টা করো।"
        )


@bot.callback_query_handler(func=lambda c: c.data == "sub_remove")
def sub_remove(call):
    if not is_master(call.from_user.id):
        return

    states[call.from_user.id] = {"action": "sub_remove"}
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "Remove করতে Sub-admin-এর Telegram ID পাঠাও।"
    )


def save_sub_remove(message):
    try:
        uid = int(message.text.strip())
        remove_subadmin(uid)
        states.pop(message.from_user.id, None)
        bot.send_message(
            message.chat.id,
            f"✅ Sub-admin <code>{uid}</code> removed.",
            reply_markup=admin_keyboard()
        )
    except Exception:
        bot.send_message(message.chat.id, "❌ Invalid user ID.")


# ============================================================
# SETTINGS
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == "adm_settings")
def adm_settings(call):
    if not is_master(call.from_user.id):
        bot.answer_callback_query(call.id, "Master Admin only.")
        return

    maintenance = get_setting("maintenance", "0") == "1"
    live = get_setting("live_mode", "1") == "1"

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(
            f"Maintenance {'ON' if maintenance else 'OFF'}",
            callback_data="set_maintenance"
        ),
        types.InlineKeyboardButton(
            f"Live {'ON' if live else 'OFF'}",
            callback_data="set_live"
        )
    )
    kb.add(
        types.InlineKeyboardButton(
            "⬅️ Admin Panel", callback_data="adm_home"
        )
    )

    bot.edit_message_text(
        "⚙️ <b>SETTINGS</b>\n\n"
        f"Maintenance: <b>{'ON' if maintenance else 'OFF'}</b>\n"
        f"Live mode: <b>{'ON' if live else 'OFF'}</b>",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda c: c.data == "set_maintenance")
def set_maintenance(call):
    if not is_master(call.from_user.id):
        return

    old = get_setting("maintenance", "0")
    set_setting("maintenance", "0" if old == "1" else "1")
    adm_settings(call)


@bot.callback_query_handler(func=lambda c: c.data == "set_live")
def set_live(call):
    if not is_master(call.from_user.id):
        return

    old = get_setting("live_mode", "1")
    set_setting("live_mode", "0" if old == "1" else "1")
    adm_settings(call)


# ============================================================
# PERSISTENT INTERACTIVE STATE HANDLER
# ============================================================

@bot.message_handler(content_types=["text"], func=lambda m: m.from_user.id in states)
def state_handler(message):
    action = states.get(message.from_user.id, {}).get("action")

    if action == "uid":
        save_uid(message)
    elif action == "withdraw_amount" or action == "withdraw_method" or action == "withdraw_account":
        finish_withdraw(message)
    elif action == "add_signal":
        admin_save_signal(message)
    elif action == "wallet_adjust":
        save_wallet_adjust(message)
    elif action == "broadcast":
        do_broadcast(message)
    elif action == "sub_add":
        save_subadmin(message)
    elif action == "sub_remove":
        save_sub_remove(message)


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
        reply_markup=main_keyboard(message.from_user.id)
    )


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


if __name__ == "__main__":
    main()
