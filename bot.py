=========

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

        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            status TEXT DEFAULT 'FREE',
            vip_until TEXT,
            wallet_cents INTEGER DEFAULT 0,
            referred_by INTEGER,
            referral_paid INTEGER DEFAULT 0,
            refs_count INTEGER DEFAULT 0,
            notifications INTEGER DEFAULT 1,
            free_cycle TEXT,
            free_used INTEGER DEFAULT 0,
            created_at TEXT,
            last_seen TEXT,
            FOREIGN KEY(referred_by) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS admins(
            user_id INTEGER PRIMARY KEY,
            permissions TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS signals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_date TEXT NOT NULL,
            signal_time TEXT NOT NULL,
            pair TEXT NOT NULL,
            direction TEXT NOT NULL,
            confidence TEXT DEFAULT '',
            audience TEXT DEFAULT 'ALL',
            selected_users TEXT DEFAULT '',
            sent INTEGER DEFAULT 0,
            vote_revealed INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS signal_deliveries(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            delivered_at TEXT,
            quota_used INTEGER DEFAULT 0,
            UNIQUE(user_id, signal_id)
        );

        CREATE TABLE IF NOT EXISTS signal_votes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            vote TEXT NOT NULL,
            created_at TEXT,
            UNIQUE(user_id, signal_id)
        );

        CREATE TABLE IF NOT EXISTS signal_results(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            trade_amount_cents INTEGER DEFAULT 0,
            created_at TEXT,
            UNIQUE(user_id, signal_id)
        );

        CREATE TABLE IF NOT EXISTS uid_submissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            uid TEXT NOT NULL,
            status TEXT DEFAULT 'PENDING',
            submitted_at TEXT,
            reviewed_at TEXT,
            reviewed_by INTEGER
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_uid_unique
        ON uid_submissions(uid)
        WHERE status IN ('PENDING','APPROVED');

        CREATE TABLE IF NOT EXISTS wallet_transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT,
            amount_cents INTEGER,
            balance_after_cents INTEGER,
            note TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS withdrawals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount_cents INTEGER,
            method TEXT,
            account TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT,
            reviewed_at TEXT,
            reviewed_by INTEGER
        );

        CREATE TABLE IF NOT EXISTS mm_profiles(
            user_id INTEGER PRIMARY KEY,
            balance_cents INTEGER DEFAULT 0,
            profit_target_cents INTEGER DEFAULT 1000,
            loss_limit_cents INTEGER DEFAULT 500,
            base_trade_cents INTEGER DEFAULT 100,
            m1_trade_cents INTEGER DEFAULT 200,
            max_trades INTEGER DEFAULT 20,
            daily_start_balance_cents INTEGER DEFAULT 0,
            daily_pl_cents INTEGER DEFAULT 0,
            trades_today INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            stage TEXT DEFAULT 'BASE',
            stopped INTEGER DEFAULT 0,
            balance_confirmed INTEGER DEFAULT 0,
            reset_date TEXT
        );

        CREATE TABLE IF NOT EXISTS live_sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT,
            ended_at TEXT,
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS live_signals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            content TEXT,
            confidence TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS user_limits(
            user_id INTEGER PRIMARY KEY,
            free_limit INTEGER
        );
        """)

        conn.execute(
            "INSERT OR IGNORE INTO admins(user_id,permissions) VALUES(?,?)",
            (ADMIN_ID, "ALL")
        )

        defaults = {
            "free_signal_limit": "4",
            "referral_bonus_cents": "100",
            "min_withdraw_cents": "500",
            "withdraw_enabled": "1",
            "maintenance": "0",
            "confidence": "95–99%",
            "max_m1_cents": "500",
            "max_daily_loss_cents": "5000",
            "default_profit_target": "1000",
            "default_loss_limit": "500",
            "default_base_trade": "100",
            "default_m1_trade": "200",
            "default_max_trades": "20",
        }

        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                (key, value)
            )

        for key, value in DEFAULT_TEXTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                (f"text.{key}", value)
            )

        conn.commit()
        conn.close()


def get_setting(key, default=""):
    with DB_LOCK:
        conn = db()
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,)
        ).fetchone()
        conn.close()
        return row["value"] if row else default


def set_setting(key, value):
    with DB_LOCK:
        conn = db()
        conn.execute("""
            INSERT INTO settings(key,value)
            VALUES(?,?)
            ON CONFLICT(key)
            DO UPDATE SET value=excluded.value
        """, (key, str(value)))
        conn.commit()
        conn.close()


def T(key):
    return get_setting(
        f"text.{key}",
        DEFAULT_TEXTS.get(key, key)
    )


def setting_int(key, default):
    try:
        return int(get_setting(key, str(default)))
    except Exception:
        return default


# ============================================================
# HELPERS
# ============================================================

def now_bd():
    return datetime.now(BD_TZ)


def now_utc():
    return datetime.now(UTC)


def money(cents):
    return f"{cents / 100:.2f}"


def parse_money(value):
    value = value.strip().replace("$", "").replace(",", "")
    amount = float(value)
    if amount < 0:
        raise ValueError
    return int(round(amount * 100))


def current_cycle():
    today = now_bd().date()
    epoch = datetime(2026, 1, 1, tzinfo=BD_TZ).date()
    days = (today - epoch).days
    start = (days // 2) * 2
    return (epoch + timedelta(days=start)).isoformat()


def is_admin(user_id):
    if user_id == ADMIN_ID:
        return True

    with DB_LOCK:
        conn = db()
        row = conn.execute(
            "SELECT user_id FROM admins WHERE user_id=?", and stage == "BASE":
            next_stage = "M1"
        else:
            next_stage = "BASE"

        trades = m["trades_today"]

        if result in ("WIN", "LOSS"):
            trades += 1

        stopped = m["stopped"]

        if new_pl >= m["profit_target_cents"]:
            stopped = 1

        if new_pl <= -m["loss_limit_cents"]:
            stopped = 1

        if trades >= m["max_trades"]:
            stopped = 1

        conn.execute("""
            INSERT INTO signal_results(
                user_id,signal_id,result,trade_amount_cents,created_at
            )
            VALUES(?,?,?,?,?)
        """, (
            user_id,
            signal_id,
            result,
            amount,
            now_utc().isoformat()
        ))

        conn.execute("""
            UPDATE mm_profiles
            SET balance_cents=?,
                daily_pl_cents=?,
                trades_today=?,
                wins=wins+?,
                losses=losses+?,
                stage=?,
                stopped=?
            WHERE user_id=?
        """, (
            new_balance,
            new_pl,
            trades,
            1 if result == "WIN" else 0,
            1 if result == "LOSS" else 0,
            next_stage,
            stopped,
            user_id
        ))

        conn.commit()
        conn.close()

    return True


# ============================================================
# SIGNAL FORMAT
# ============================================================

def direction_display(direction):
    direction = direction.upper()

    if direction in ("UP", "BUY", "CALL"):
        return "🟢 ⬆️ <b>UP / BUY</b>"

    return "🔴 ⬇️ <b>DOWN / SELL</b>"


def parse_signal_datetime(date_text, time_text):
    date_text = date_text.strip()
    time_text = time_text.strip().upper()

    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M %p",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y %I:%M %p"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                f"{date_text} {time_text}",
                fmt
            ).replace(tzinfo=BD_TZ)
        except ValueError:
            continue

    raise ValueError


def signal_due_datetime(row):
    return datetime.strptime(
        f"{row['signal_date']} {row['signal_time']}",
        "%Y-%m-%d %H:%M"
    ).replace(tzinfo=BD_TZ)


def user_can_receive_signal(user_id, signal):
    audience = signal["audience"]

    if audience == "ALL":
        if vip_active(user_id):
            return True

        reset_cycle(user_id)
        u = user_row(user_id)

        return u["free_used"] < free_limit(user_id)

    if audience == "VIP":
        return vip_active(user_id)

    if audience == "SELECTED":
        ids = [
            x.strip()
            for x in (signal["selected_users"] or "").split(",")
            if x.strip()
        ]

        return str(user_id) in ids

    return False


def next_future_signal(user_id):
    with DB_LOCK:
        conn = db()
        rows = conn.execute("""
            SELECT *
            FROM signals
            WHERE sent=0
            ORDER BY signal_date ASC,signal_time ASC
        """).fetchall()
        conn.close()

    for signal in rows:
        if user_can_receive_signal(user_id, signal):
            return signal

    return None


def send_signal_to_user(user_id, signal, quota=False):
    with DB_LOCK:
        conn = db()

        exists = conn.execute("""
            SELECT id FROM signal_deliveries
            WHERE user_id=? AND signal_id=?
        """, (
            user_id,
            signal["id"]
        )).fetchone()

        if exists:
            conn.close()
            return False

        conn.execute("""
            INSERT INTO signal_deliveries(
                user_id,signal_id,delivered_at,quota_used
            )
            VALUES(?,?,?,?)
        """, (
            user_id,
            signal["id"],
            now_utc().isoformat(),
            1 if quota else 0
        ))

        if quota:
            conn.execute("""
                UPDATE users
                SET free_used=free_used+1
                WHERE user_id=?
            """, (user_id,))

        conn.commit()
        conn.close()

    trade, stage = current_trade(user_id)

    u = user_row(user_id)

    if vip_active(user_id):
        remaining = "UNLIMITED"
    else:
        remaining = str(
            max(
                0,
                free_limit(user_id) - u["free_used"]
            )
        )

    message = T("signal").format(
        date=signal["signal_date"],
        time=signal["signal_time"],
        pair=escape(signal["pair"]),
        direction=direction_display(signal["direction"]),
        confidence=escape(
            signal["confidence"] or
            get_setting("confidence", "95–99%")
        ),
        trade_amount=money(trade),
        stage=stage,
        remaining=remaining
    )

    try:
        bot.send_message(
            user_id,
            message,
            reply_markup=main_menu(user_id)
        )
        return True
    except Exception:
        logger.exception("Signal send failed")
        return False


# ============================================================
# START
# ============================================================

@bot.message_handler(commands=["start"])
def start_handler(message):
    try:
        referral = None

        parts = message.text.split(maxsplit=1)

        if len(parts) == 2 and parts[1].startswith("ref_"):
            try:
                referral = int(parts[1][4:])
            except Exception:
                referral = None

        register_user(message.from_user, referral)

        bot.send_message(
            message.chat.id,
            T("welcome"),
            reply_markup=main_menu(message.from_user.id)
        )

    except Exception:
        logger.exception("Start error")
        bot.send_message(
            message.chat.id,
            T("error"),
            reply_markup=main_menu(message.from_user.id)
        )


# ============================================================
# CANCEL
# ============================================================

@bot.message_handler(commands=["cancel"])
def cancel_handler(message):
    STATES.pop(message.from_user.id, None)

    bot.send_message(
        message.chat.id,
        T("cancelled"),
        reply_markup=main_menu(message.from_user.id)
    )


# ============================================================
# USER ACTIONS
# ============================================================

def handle_user_button(message):

    uid = message.from_user.id
    text = message.text

    register_user(message.from_user)

    if maintenance_blocked(uid):
        bot.send_message(
            message.chat.id,
            T("maintenance"),
            reply_markup=main_menu(uid)
        )
        return

    # Notice
    if text == T("btn_notice"):
        notice = get_setting("notice", "").strip()

        bot.send_message(
            message.chat.id,
            notice if notice else T("notice_empty"),
            reply_markup=main_menu(uid)
        )
        return

    # Rules
    if text == T("btn_rules"):
        bot.send_message(
            message.chat.id,
            get_setting("rules_text", T("rules")),
            reply_markup=main_menu(uid)
        )
        return

    # Help
    if text == T("btn_help"):
        bot.send_message(
            message.chat.id,
            T("help"),
            reply_markup=main_menu(uid)
        )
        return

    # Future signal
    if text == T("btn_future"):
        reset_mm_day(uid)

        if not ensure_balance_before_signal(uid):
            bot.send_message(
                message.chat.id,
                T("trade_stopped") +
                "\n\n" +
                mm_text(uid),
                reply_markup=money_menu()
            )
            return

        signal = next_future_signal(uid)

        if not signal:
            bot.send_message(
                message.chat.id,
                T("future_empty"),
                reply_markup=main_menu(uid)
            )
            return

        quota = (
            not vip_active(uid)
            and signal["audience"] == "ALL"
        )

        if quota:
            if user_row(uid)["free_used"] >= free_limit(uid):
                bot.send_message(
                    message.chat.id,
                    T("quota_end"),
                    reply_markup=main_menu(uid)
                )
                return

        send_signal_to_user(
            uid,
            signal,
            quota=quota
        )

        return

    # Money management
    if text == T("btn_money"):
        show_mm(message)
        return

    if text == T("btn_set_balance"):
        mm_set_value(
            message,
            "balance_cents",
            T("balance_prompt")
        )
        return

    if text == T("btn_set_target"):
        mm_set_value(
            message,
            "profit_target_cents",
            T("target_prompt")
        )
        return

    if text == T("btn_set_loss"):
        mm_set_value(
            message,
            "loss_limit_cents",
            T("loss_prompt")
        )
        return

    if text == T("btn_set_base"):
        mm_set_value(
            message,
            "base_trade_cents",
            T("base_prompt")
        )
        return

    if text == T("btn_set_m1"):
        mm_set_value(
            message,
            "m1_trade_cents",
            T("m1_prompt")
        )
        return

    if text == T("btn_set_max"):
        STATES[uid] = {
            "action": "mm_int",
            "field": "max_trades"
        }

        bot.send_message(
            message.chat.id,
            T("max_prompt"),
            reply_markup=back_keyboard()
        )
        return

    if text == T("btn_mm_status"):
        show_mm(message)
        return

    if text == T("btn_stop"):
        update_mm_value(uid, "stopped", 1)
        bot.send_message(
            message.chat.id,
            T("trade_stopped"),
            reply_markup=money_menu()
        )
        return

    if text == T("btn_resume"):
        update_mm_value(uid, "stopped", 0)
        bot.send_message(
            message.chat.id,
            "▶️ Trading resumed.",
            reply_markup=money_menu()
        )
        return

    # Wallet
    if text == T("btn_wallet"):
        u = user_row(uid)

        bot.send_message(
            message.chat.id,
            T("wallet").format(
                balance=money(u["wallet_cents"]),
                refs=u["refs_count"]
            ),
            reply_markup=main_menu(uid)
        )
        return

    # Referral
    if text == T("btn_referral"):
        try:
            me = bot.get_me()
            link = f"https://t.me/{me.username}?start=ref_{uid}"

            bot.send_message(
                message.chat.id,
                T("referral").format(
                    link=escape(link),
                    bonus=money(
                        setting_int(
                            "referral_bonus_cents",
                            100
                        )
                    )
                ),
                reply_markup=main_menu(uid)
            )
        except Exception:
            bot.send_message(
                message.chat.id,
                T("error"),
                reply_markup=main_menu(uid)
            )
        return

    # VIP
    if text == T("btn_vip"):
        u = user_row(uid)

        expiry = u["vip_until"] or "No expiry"

        bot.send_message(
            message.chat.id,
            T("vip_message") +
            f"\n\n⏳ Expiry: <b>{escape(expiry)}</b>",
            reply_markup=main_menu(uid)
        )
        return

    # UID
    if text == T("btn_uid"):
        if vip_active(uid):
            bot.send_message(
                message.chat.id,
                T("vip_message"),
                reply_markup=main_menu(uid)
            )
            return

        with DB_LOCK:
            conn = db()

            pending = conn.execute("""
                SELECT id FROM uid_submissions
                WHERE user_id=? AND status='PENDING'
                LIMIT 1
            """, (uid,)).fetchone()

            approved = conn.execute("""
                SELECT id FROM uid_submissions
                WHERE user_id=? AND status='APPROVED'
                LIMIT 1
            """, (uid,)).fetchone()

            conn.close()

        if pending or approved:
            bot.send_message(
                message.chat.id,
                T("uid_pending"),
                reply_markup=main_menu(uid)
            )
            return

        STATES[uid] = {"action": "uid"}

        bot.send_message(
            message.chat.id,
            T("uid_prompt"),
            reply_markup=back_keyboard()
        )
        return

    # Status
    if text == T("btn_status"):
        u = user_row(uid)
        reset_cycle(uid)
        u = user_row(uid)

        remaining = (
            "UNLIMITED"
            if vip_active(uid)
            else str(
                max(
                    0,
                    free_limit(uid) - u["free_used"]
                )
            )
        )

        bot.send_message(
            message.chat.id,
            (
                "👤 <b>MY STATUS</b>\n\n"
                f"🆔 ID: <code>{uid}</code>\n"
                f"⭐ Status: <b>{u['status']}</b>\n"
                f"💰 Wallet: <b>${money(u['wallet_cents'])}</b>\n"
                f"👥 Referrals: <b>{u['refs_count']}</b>\n"
                f"🎟️ Remaining Signals: <b>{remaining}</b>"
            ),
            reply_markup=main_menu(uid)
        )
        return

    # History
    if text == T("btn_history"):
        with DB_LOCK:
            conn = db()
            rows = conn.execute("""
                SELECT s.*,r.result
                FROM signal_deliveries d
                JOIN signals s ON s.id=d.signal_id
                LEFT JOIN signal_results r
                    ON r.signal_id=s.id
                    AND r.user_id=?
                WHERE d.user_id=?
                ORDER BY d.id DESC
                LIMIT 10
            """, (uid, uid)).fetchall()
            conn.close()

        if not rows:
            msg = "📜 কোনো signal history নেই।"
        else:
            lines = ["📜 <b>SIGNAL HISTORY</b>\n"]

            for r in rows:
                result = r["result"] or "PENDING"

                lines.append(
                    f"#{r['id']} | "
                    f"{r['signal_date']} "
                    f"{r['signal_time']} | "
                    f"{escape(r['pair'])} | "
                    f"<b>{result}</b>"
                )

            msg = "\n".join(lines)

        bot.send_message(
            message.chat.id,
            msg,
            reply_markup=main_menu(uid)
        )
        return

    # Vote
    if text == T("btn_vote"):
        with DB_LOCK:
            conn = db()
            row = conn.execute("""
                SELECT s.*
                FROM signal_deliveries d
                JOIN signals s ON s.id=d.signal_id
                LEFT JOIN signal_votes v
                    ON v.signal_id=s.id
                    AND v.user_id=?
                WHERE d.user_id=?
                AND v.id IS NULL
                ORDER BY d.id DESC
                LIMIT 1
            """, (uid, uid)).fetchone()
            conn.close()

        if not row:
            bot.send_message(
                message.chat.id,
                "🗳 কোনো pending vote নেই।",
                reply_markup=main_menu(uid)
            )
            return

        STATES[uid] = {
            "action": "vote",
            "signal_id": row["id"]
        }

        bot.send_message(
            message.chat.id,
            T("vote_prompt"),
            reply_markup=kb([
                [T("btn_up"), T("btn_down")],
                [T("btn_vote_skip")],
                [T("btn_back")]
            ])
        )
        return

    # Result
    if text == T("btn_result"):
        with DB_LOCK:
            conn = db()
            row = conn.execute("""
                SELECT s.*
                FROM signal_deliveries d
                JOIN signals s ON s.id=d.signal_id
                LEFT JOIN signal_results r
                    ON r.signal_id=s.id
                    AND r.user_id=?
                WHERE d.user_id=?
                AND r.id IS NULL
                ORDER BY d.id DESC
                LIMIT 1
            """, (uid, uid)).fetchone()
            conn.close()

        if not row:
            bot.send_message(
                message.chat.id,
                "📈 কোনো pending signal result নেই।",
                reply_markup=main_menu(uid)
            )
            return

        STATES[uid] = {
            "action": "result",
            "signal_id": row["id"]
        }

        bot.send_message(
            message.chat.id,
            "📈 এই signal-এর result নির্বাচন করুন।",
            reply_markup=kb([
                [T("btn_win"), T("btn_loss")],
                [T("btn_skip")],
                [T("btn_back")]
            ])
        )
        return

    # Notifications
    if text == T("btn_notification"):
        u = user_row(uid)
        new_value = 0 if u["notifications"] else 1

        with DB_LOCK:
            conn = db()
            conn.execute(
                "UPDATE users SET notifications=? WHERE user_id=?",
                (new_value, uid)
            )
            conn.commit()
            conn.close()

        bot.send_message(
            message.chat.id,
            T("notification_on")
            if new_value
            else T("notification_off"),
            reply_markup=main_menu(uid)
        )
        return

    # Live user
    if text == T("btn_live"):
        bot.send_message(
            message.chat.id,
            "⚡ Live signal session বর্তমানে admin-controlled।",
            reply_markup=main_menu(uid)
        )
        return

    # Back/home
    if text == T("btn_home"):
        bot.send_message(
            message.chat.id,
            "🏠 Main Menu",
            reply_markup=main_menu(uid)
        )
        return


# ============================================================
# ADMIN MENU
# ============================================================

def admin_action(message):

    uid = message.from_user.id
    text = message.text

    if not is_admin(uid):
        bot.send_message(
            message.chat.id,
            T("admin_denied"),
            reply_markup=main_menu(uid)
        )
        return

    # Add signal
    if text == T("btn_add_signal"):
        STATES[uid] = {
            "action": "add_signal_date"
        }

        bot.send_message(
            uid,
            "📅 Signal date লিখুন:\n\nExample: 2026-09-25",
            reply_markup=back_keyboard()
        )
        return

    # Signal manager
    if text == T("btn_signals"):
        with DB_LOCK:
            conn = db()
            rows = conn.execute("""
                SELECT *
                FROM signals
                ORDER BY id DESC
                LIMIT 15
            """).fetchall()
            conn.close()

        if not rows:
            msg = "📊 কোনো signal নেই।"
        else:
            lines = ["📊 <b>SIGNAL MANAGER</b>\n"]

            for r in rows:
                lines.append(
                    f"#{r['id']} | "
                    f"{r['signal_date']} "
                    f"{r['signal_time']} | "
                    f"{escape(r['pair'])} | "
                    f"{r['direction']} | "
                    f"{r['audience']}"
                )

            msg = "\n".join(lines)

        bot.send_message(
            uid,
            msg,
            reply_markup=admin_menu()
        )
        return

    # Live session
    if text == T("btn_live_session"):
        with DB_LOCK:
            conn = db()

            active = conn.execute("""
                SELECT id FROM live_sessions
                WHERE active=1
                ORDER BY id DESC
                LIMIT 1
            """).fetchone()

            if not active:
                cur = conn.execute("""
                    INSERT INTO live_sessions(started_at,active)
                    VALUES(?,1)
                """, (now_utc().isoformat(),))

                session_id = cur.lastrowid
                conn.commit()
            else:
                session_id = active["id"]

            conn.close()

        STATES[uid] = {
            "action": "live_signal",
            "session_id": session_id
        }

        bot.send_message(
            uid,
            T("live_prompt"),
            reply_markup=back_keyboard()
        )
        return

    # Users
    if text == T("btn_users"):
        with DB_LOCK:
            conn = db()

            total = conn.execute(
                "SELECT COUNT(*) c FROM users"
            ).fetchone()["c"]

            vip = conn.execute(
                "SELECT COUNT(*) c FROM users WHERE status='VIP'"
            ).fetchone()["c"]

            conn.close()

        bot.send_message(
            uid,
            (
                "👥 <b>USERS</b>\n\n"
                f"Total: <b>{total}</b>\n"
                f"VIP: <b>{vip}</b>"
            ),
            reply_markup=admin_menu()
        )
        return

    # Pending UID
    if text == T("btn_uid_pending"):
        if not can(uid, "uid"):
            bot.send_message(uid, T("admin_denied"))
            return

        with DB_LOCK:
            conn = db()
            rows = conn.execute("""
                SELECT * FROM uid_submissions
                WHERE status='PENDING'
                ORDER BY id DESC
                LIMIT 20
            """).fetchall()
            conn.close()

        if not rows:
            msg = "🆔 কোনো pending UID নেই।"
        else:
            lines = ["🆔 <b>PENDING UID</b>\n"]

            for r in rows:
                lines.append(
                    f"ID: {r['id']}\n"
                    f"User: <code>{r['user_id']}</code>\n"
                    f"UID: <code>{escape(r['uid'])}</code>\n"
                    f"Approve করতে: <code>/approve_uid {r['id']}</code>\n"
                    f"Reject করতে: <code>/reject_uid {r['id']}</code>\n"
                )

            msg = "\n".join(lines)

        bot.send_message(
            uid,
            msg,
            reply_markup=admin_menu()
        )
        return

    # VIP manager
    if text == T("btn_vip_manage"):
        STATES[uid] = {
            "action": "vip_manage"
        }

        bot.send_message(
            uid,
            (
                "⭐ VIP Manager\n\n"
                "Format:\n"
                "<code>USER_ID DAYS</code>\n\n"
                "Example:\n"
                "<code>123456789 30</code>"
            ),
            reply_markup=back_keyboard()
        )
        return

    # Wallet manager
    if text == T("btn_wallet_manage"):
        STATES[uid] = {
            "action": "wallet_adjust"
        }

        bot.send_message(
            uid,
            (
                "💳 Wallet Adjust\n\n"
                "Format:\n"
                "<code>USER_ID AMOUNT</code>\n\n"
                "Example:\n"
                "<code>123456789 10</code>\n\n"
                "Negative amount দিয়ে deduct করা যাবে।"
            ),
            reply_markup=back_keyboard()
        )
        return

    # Withdrawals
    if text == T("btn_withdraw"):
        if not can(uid, "withdraw"):
            bot.send_message(uid, T("admin_denied"))
            return

        with DB_LOCK:
            conn = db()
            rows = conn.execute("""
                SELECT * FROM withdrawals
                WHERE status='PENDING'
                ORDER BY id DESC
                LIMIT 20
            """).fetchall()
            conn.close()

        if not rows:
            msg = "💸 কোনো pending withdrawal নেই।"
        else:
            lines = ["💸 <b>PENDING WITHDRAWALS</b>\n"]

            for r in rows:
                lines.append(
                    f"#{r['id']} | "
                    f"User {r['user_id']} | "
                    f"${money(r['amount_cents'])}\n"
                    f"{r['method']} | {escape(r['account'])}\n"
                    f"Approve: /approve_withdraw {r['id']}\n"
                    f"Reject: /reject_withdraw {r['id']}\n"
                )

            msg = "\n".join(lines)

        bot.send_message(
            uid,
            msg,
            reply_markup=admin_menu()
        )
        return

    # Broadcast
    if text == T("btn_broadcast"):
        STATES[uid] = {
            "action": "broadcast"
        }

        bot.send_message(
            uid,
            T("broadcast_prompt"),
            reply_markup=back_keyboard()
        )
        return

    # Text editor
    if text == T("btn_text_editor"):
        show_text_editor(uid)
        return

    # Notice edit
    if text == T("btn_notice_edit"):
        STATES[uid] = {
            "action": "edit_notice"
        }

        bot.send_message(
            uid,
            "📢 নতুন Notice text পাঠান।",
            reply_markup=back_keyboard()
        )
        return

    # Rules edit
    if text == T("btn_rules_edit"):
        STATES[uid] = {
            "action": "edit_rules"
        }

        bot.send_message(
            uid,
            "📖 নতুন Trading Rules text পাঠান।",
            reply_markup=back_keyboard()
        )
        return

    # Sub admins
    if text == T("btn_subadmins"):
        if not can(uid, "ALL"):
            bot.send_message(uid, T("admin_denied"))
            return

        STATES[uid] = {
            "action": "subadmin"
        }

        bot.send_message(
            uid,
            (
                "🛡 <b>SUB-ADMIN</b>\n\n"
                "Format:\n"
                "<code>USER_ID permission1,permission2</code>\n\n"
                "Permissions:\n"
                "signals, uid, users, wallet, withdraw, "
                "broadcast, settings, analytics"
            ),
            reply_markup=back_keyboard()
        )
        return

    # Settings
    if text == T("btn_settings"):
        STATES[uid] = {
            "action": "setting"
        }

        bot.send_message(
            uid,
            (
                "⚙️ Settings\n\n"
                "Format:\n"
                "<code>KEY VALUE</code>\n\n"
                "Example:\n"
                "<code>free_signal_limit 6</code>"
            ),
            reply_markup=back_keyboard()
        )
        return

    # Analytics
    if text == T("btn_analytics"):
        show_analytics(uid)
        return

    # Maintenance
    if text == T("btn_maintenance"):
        current = get_setting("maintenance", "0")
        new = "0" if current == "1" else "1"
        set_setting("maintenance", new)

        bot.send_message(
            uid,
            f"🔧 Maintenance: <b>{'ON' if new == '1' else 'OFF'}</b>",
            reply_markup=admin_menu()
        )
        return

    # Backup
    if text == T("btn_backup"):
        create_backup()

        bot.send_message(
            uid,
            T("backup_done"),
            reply_markup=admin_menu()
        )
        return

    # User manage
    if text == T("btn_user_manage"):
        STATES[uid] = {
            "action": "user_manage"
        }

        bot.send_message(
            uid,
            (
                "👤 User Manage\n\n"
                "Format:\n"
                "<code>USER_ID</code>"
            ),
            reply_markup=back_keyboard()
        )
        return

    # Back
    if text == T("btn_back"):
        bot.send_message(
            uid,
            "👑 Admin Panel",
            reply_markup=admin_menu()
        )
        return

    if text == T("btn_home"):
        bot.send_message(
            uid,
            "🏠 Main Menu",
            reply_markup=main_menu(uid)
        )


# ============================================================
# TEXT EDITOR
# ============================================================

TEXT_EDITOR_KEYS = [
    "welcome",
    "maintenance",
    "notice_empty",
    "rules",
    "help",
    "error",
    "cancelled",
    "invalid",
    "uid_prompt",
    "uid_pending",
    "uid_success",
    "uid_duplicate",
    "vip_message",
    "wallet",
    "referral",
    "future_empty",
    "quota_end",
    "signal",
    "live_signal",
    "vote_prompt",
    "vote_saved",
    "vote_result",
    "result_saved",
    "mm",
    "balance_prompt",
    "target_prompt",
    "loss_prompt",
    "base_prompt",
    "m1_prompt",
    "max_prompt",
    "trade_stopped",
    "withdraw_off",
    "withdraw_amount",
    "withdraw_method",
    "withdraw_account",
    "withdraw_success",
    "withdraw_min",
    "admin_denied",
    "broadcast_prompt",
    "broadcast_done",
    "live_prompt",
    "live_ended",
    "text_saved",
    "backup_done",
    "notification_on",
    "notification_off"
]


def show_text_editor(user_id):
    rows = []

    for key in TEXT_EDITOR_KEYS:
        label = key.replace("_", " ").title()
        rows.append([label])

    rows.append([T("btn_back"), T("btn_home")])

    bot.send_message(
        user_id,
        "📝 <b>BOT TEXT EDITOR</b>\n\n"
        "যে text পরিবর্তন করতে চান সেটিতে press করুন।",
        reply_markup=kb(rows)
    )

    STATES[user_id] = {
        "action": "choose_text"
    }


# ============================================================
# WITHDRAW USER
# ============================================================

def start_withdraw(message):
    uid = message.from_user.id

    if get_setting("withdraw_enabled", "1") != "1":
        bot.send_message(
            message.chat.id,
            T("withdraw_off"),
            reply_markup=main_menu(uid)
        )
        return

    STATES[uid] = {
        "action": "withdraw_amount"
    }

    bot.send_message(
        message.chat.id,
        T("withdraw_amount"),
        reply_markup=back_keyboard()
    )


# ============================================================
# GENERIC STATE HANDLER
# ============================================================

@bot.message_handler(content_types=["text"])
def all_text_handler(message):

    uid = message.from_user.id
    text = message.text.strip()

    register_user(message.from_user)

    # Admin menu has priority
    if is_admin(uid):
        if text in [
            T("btn_admin"),
            T("btn_add_signal"),
            T("btn_signals"),
            T("btn_live_session"),
            T("btn_users"),
            T("btn_uid_pending"),
            T("btn_vip_manage"),
            T("btn_wallet_manage"),
            T("btn_withdraw"),
            T("btn_broadcast"),
            T("btn_text_editor"),
            T("btn_notice_edit"),
            T("btn_rules_edit"),
            T("btn_subadmins"),
            T("btn_settings"),
            T("btn_analytics"),
            T("btn_maintenance"),
            T("btn_backup"),
            T("btn_user_manage")
        ]:
            if text == T("btn_admin"):
                bot.send_message(
                    uid,
                    "👑 <b>ADMIN PANEL</b>",
                    reply_markup=admin_menu()
                )
            else:
                admin_action(message)
            return

    # State handling
    state = STATES.get(uid)

    if state:
        action = state.get("action")

        # Back
        if text == T("btn_back"):
            STATES.pop(uid, None)

            if is_admin(uid):
                bot.send_message(
                    uid,
                    "👑 Admin Panel",
                    reply_markup=admin_menu()
                )
            else:
                bot.send_message(
                    uid,
                    "🏠 Main Menu",
                    reply_markup=main_menu(uid)
                )
            return

        # Home
        if text == T("btn_home"):
            STATES.pop(uid, None)

            bot.send_message(
                uid,
                "🏠 Main Menu",
                reply_markup=main_menu(uid)
            )
            return

        # ---------- UID ----------
        if action == "uid":
            value = text

            if not 3 <= len(value) <= 100:
                bot.send_message(
                    uid,
                    T("invalid")
                )
                return

            with DB_LOCK:
                conn = db()

                duplicate = conn.execute("""
                    SELECT id FROM uid_submissions
                    WHERE uid=?
                    AND status IN ('PENDING','APPROVED')
                """, (value,)).fetchone()

                if duplicate:
                    conn.close()

                    bot.send_message(
                        uid,
                        T("uid_duplicate"),
                        reply_markup=main_menu(uid)
                    )

                    STATES.pop(uid, None)
                    return

                existing = conn.execute("""
                    SELECT id FROM uid_submissions
                    WHERE user_id=?
                    AND status IN ('PENDING','APPROVED')
                """, (uid,)).fetchone()

                if existing:
                    conn.close()

                    bot.send_message(
                        uid,
                        T("uid_pending"),
                        reply_markup=main_menu(uid)
                    )

                    STATES.pop(uid, None)
                    return

                conn.execute("""
                    INSERT INTO uid_submissions(
                        user_id,uid,status,submitted_at
                    )
                    VALUES(?,?,?,?)
                """, (
                    uid,
                    value,
                    "PENDING",
                    now_utc().isoformat()
                ))

                conn.commit()
                conn.close()

            STATES.pop(uid, None)

            bot.send_message(
                uid,
                T("uid_success"),
                reply_markup=main_menu(uid)
            )

            bot.send_message(
                ADMIN_ID,
                (
                    "🆔 <b>NEW UID</b>\n\n"
                    f"User: <code>{uid}</code>\n"
                    f"UID: <code>{escape(value)}</code>\n\n"
                    f"/approve_uid {uid}\n"
                    f"/reject_uid {uid}"
                )
            )

            return

        # ---------- MM money ----------
        if action == "mm_value":
            field = state["field"]

            try:
                amount = parse_money(text)
            except Exception:
                bot.send_message(uid, T("invalid"))
                return

            if amount <= 0:
                bot.send_message(uid, T("invalid"))
                return

            if field == "m1_trade_cents":
                max_m1 = setting_int(
                    "max_m1_cents",
                    500
                )

                if amount > max_m1:
                    bot.send_message(
                        uid,
                        f"❌ Maximum M1 amount: ${money(max_m1)}"
                    )
                    return

            update_mm_value(uid, field, amount)

            if field == "balance_cents":
                update_mm_value(
                    uid,
                    "daily_start_balance_cents",
                    amount
                )
                update_mm_value(
                    uid,
                    "balance_confirmed",
                    1
                )

            STATES.pop(uid, None)

            bot.send_message(
                uid,
                "✅ Value saved.",
                reply_markup=money_menu()
            )
            return

        # ---------- MM integer ----------
        if action == "mm_int":
            try:
                value = int(text)
                if value <= 0:
                    raise ValueError
            except Exception:
                bot.send_message(uid, T("invalid"))
                return

            update_mm_value(
                uid,
                state["field"],
                value
            )

            STATES.pop(uid, None)

            bot.send_message(
                uid,
                "✅ Value saved.",
                reply_markup=money_menu()
            )
            return

        # ---------- Vote ----------
        if action == "vote":
            vote = None

            if text == T("btn_up"):
                vote = "UP"

            elif text == T("btn_down"):
                vote = "DOWN"

            elif text == T("btn_vote_skip"):
                vote = "SKIP"

            if vote:
                try:
                    with DB_LOCK:
                        conn = db()
                        conn.execute("""
                            INSERT INTO signal_votes(
                                user_id,signal_id,vote,created_at
                            )
                            VALUES(?,?,?,?)
                        """, (
                            uid,
                            state["signal_id"],
                            vote,
                            now_utc().isoformat()
                        ))
                        conn.commit()
                        conn.close()

                    STATES.pop(uid, None)

                    bot.send_message(
                        uid,
                        T("vote_saved"),
                        reply_markup=main_menu(uid)
                    )

                except sqlite3.IntegrityError:
                    STATES.pop(uid, None)

                    bot.send_message(
                        uid,
                        "⚠️ আপনি এই signal-এ already vote দিয়েছেন।",
                        reply_markup=main_menu(uid)
                    )

                return

        # ---------- Result ----------
        if action == "result":
            result = None

            if text == T("btn_win"):
                result = "WIN"

            elif text == T("btn_loss"):
                result = "LOSS"

            elif text == T("btn_skip"):
                result = "SKIP"

            if result:
                ok = apply_trade_result(
                    uid,
                    state["signal_id"],
                    result
                )

                STATES.pop(uid, None)

                if ok:
                    bot.send_message(
                        uid,
                        T("result_saved") +
                        "\n\n" +
                        mm_text(uid),
                        reply_markup=main_menu(uid)
                    )
                else:
                    bot.send_message(
                        uid,
                        "⚠️ এই signal-এর result already saved.",
                        reply_markup=main_menu(uid)
                    )

                return

        # ---------- Withdraw amount ----------
        if action == "withdraw_amount":
            try:
                amount = parse_money(text)
            except Exception:
                bot.send_message(uid, T("invalid"))
                return

            minimum = setting_int(
                "min_withdraw_cents",
                500
            )

            if amount < minimum:
                bot.send_message(
                    uid,
                    T("withdraw_min").format(
                        amount=money(minimum)
                    )
                )
                return

            u = user_row(uid)

            if amount > u["wallet_cents"]:
                bot.send_message(
                    uid,
                    "❌ Wallet balance insufficient."
                )
                return

            STATES[uid] = {
                "action": "withdraw_method",
                "amount": amount
            }

            bot.send_message(
                uid,
                T("withdraw_method"),
                reply_markup=back_keyboard()
            )
            return

        # ---------- Withdraw method ----------
        if action == "withdraw_method":
            STATES[uid]["method"] = text
            STATES[uid]["action"] = "withdraw_account"

            bot.send_message(
                uid,
                T("withdraw_account"),
                reply_markup=back_keyboard()
            )
            return

        # ---------- Withdraw account ----------
        if action == "withdraw_account":

            amount = STATES[uid]["amount"]
            method = STATES[uid]["method"]

            with DB_LOCK:
                conn = db()

                balance = conn.execute(
                    "SELECT wallet_cents FROM users WHERE user_id=?",
                    (uid,)
                ).fetchone()["wallet_cents"]

                if balance < amount:
                    conn.close()

                    STATES.pop(uid, None)

                    bot.send_message(
                        uid,
                        "❌ Wallet balance insufficient.",
                        reply_markup=main_menu(uid)
                    )
                    return

                new_balance = balance - amount

                conn.execute("""
                    UPDATE users
                    SET wallet_cents=?
                    WHERE user_id=?
                """, (
                    new_balance,
                    uid
                ))

                conn.execute("""
                    INSERT INTO wallet_transactions(
                        user_id,type,amount_cents,
                        balance_after_cents,note,created_at
                    )
                    VALUES(?,?,?,?,?,?)
                """, (
                    uid,
                    "WITHDRAW_HOLD",
                    -amount,
                    new_balance,
                    "Withdrawal pending",
                    now_utc().isoformat()
                ))

                conn.execute("""
                    INSERT INTO withdrawals(
                        user_id,amount_cents,method,
                        account,status,created_at
                    )
                    VALUES(?,?,?,?,?,?)
                """, (
                    uid,
                    amount,
                    method,
                    text,
                    "PENDING",
                    now_utc().isoformat()
                ))

                conn.commit()
                conn.close()

            STATES.pop(uid, None)

            bot.send_message(
                uid,
                T("withdraw_success"),
                reply_markup=main_menu(uid)
            )

            bot.send_message(
                ADMIN_ID,
                (
                    "💸 <b>NEW WITHDRAWAL</b>\n\n"
                    f"User: <code>{uid}</code>\n"
                    f"Amount: <b>${money(amount)}</b>\n"
                    f"Method: {escape(method)}\n"
                    f"Account: <code>{escape(text)}</code>"
                )
            )

            return

        # ---------- Text editor selection ----------
        if action == "choose_text":

            selected = None

            for key in TEXT_EDITOR_KEYS:
                if text == key.replace("_", " ").title():
                    selected = key
                    break

            if selected:
                STATES[uid] = {
                    "action": "edit_text",
                    "key": selected
                }

                current = T(selected)

                bot.send_message(
                    uid,
                    (
                        "📝 <b>EDIT TEXT</b>\n\n"
                        f"<b>Key:</b> {selected}\n\n"
                        "<b>Current:</b>\n"
                        f"{escape(current)}\n\n"
                        "এখন নতুন text পাঠান।"
                    ),
                    reply_markup=back_keyboard()
                )
                return

        # ---------- Text editor save ----------
        if action == "edit_text":

            key = state["key"]

            set_setting(
                f"text.{key}",
                text
            )

            STATES.pop(uid, None)

            bot.send_message(
                uid,
                T("text_saved"),
                reply_markup=admin_menu()
            )
            return

        # ---------- Notice ----------
        if action == "edit_notice":
            set_setting("notice", text)
            STATES.pop(uid, None)

            bot.send_message(
                uid,
                T("text_saved"),
                reply_markup=admin_menu()
            )
            return

        # ---------- Rules ----------
        if action == "edit_rules":
            set_setting("rules_text", text)
            STATES.pop(uid, None)

            bot.send_message(
                uid,
                T("text_saved"),
                reply_markup=admin_menu()
            )
            return

        # ---------- Broadcast ----------
        if action == "broadcast":
            STATES.pop(uid, None)

            sent = 0

            with DB_LOCK:
                conn = db()
                users = conn.execute(
                    "SELECT user_id FROM users WHERE blocked=0"
                ).fetchall()
                conn.close()

            for u in users:
                try:
                    bot.send_message(
                        u["user_id"],
                        text
                    )
                    sent += 1
                    time.sleep(0.03)
                except Exception:
                    pass

            bot.send_message(
                uid,
                f"{T('broadcast_done')}\nSent: <b>{sent}</b>",
                reply_markup=admin_menu()
            )
            return

        # ---------- Add signal ----------
        if action == "add_signal_date":
            STATES[uid] = {
                "action": "add_signal_time",
                "date": text
            }

            bot.send_message(
                uid,
                "🕐 Signal time লিখুন।\n\nExample: 21:30",
                reply_markup=back_keyboard()
            )
            return

        if action == "add_signal_time":
            STATES[uid]["time"] = text
            STATES[uid]["action"] = "add_signal_pair"

            bot.send_message(
                uid,
                "📌 Pair লিখুন।\n\nExample: EUR/USD",
                reply_markup=back_keyboard()
            )
            return

        if action == "add_signal_pair":
            STATES[uid]["pair"] = text
            STATES[uid]["action"] = "add_signal_direction"

            bot.send_message(
                uid,
                "Direction নির্বাচন করুন:",
                reply_markup=kb([
                    ["UP", "DOWN"],
                    [T("btn_back")]
                ])
            )
            return

        if action == "add_signal_direction":
            if text not in ("UP", "DOWN"):
                bot.send_message(uid, T("invalid"))
                return

            STATES[uid]["direction"] = text
            STATES[uid]["action"] = "add_signal_confidence"

            bot.send_message(
                uid,
                "🎯 Confidence লিখুন।\n\nExample: 95–99%",
                reply_markup=back_keyboard()
            )
            return

        if action == "add_signal_confidence":
            STATES[uid]["confidence"] = text
            STATES[uid]["action"] = "add_signal_audience"

            bot.send_message(
                uid,
                "Audience নির্বাচন করুন:",
                reply_markup=kb([
                    ["ALL", "VIP"],
                    ["SELECTED"],
                    [T("btn_back")]
                ])
            )
            return

        if action == "add_signal_audience":

            if text not in ("ALL", "VIP", "SELECTED"):
                bot.send_message(uid, T("invalid"))
                return

            STATES[uid]["audience"] = text

            if text == "SELECTED":
                STATES[uid]["action"] = "add_signal_users"

                bot.send_message(
                    uid,
                    (
                        "Selected Telegram user IDs comma দিয়ে লিখুন।\n\n"
                        "Example:\n"
                        "<code>123,456,789</code>"
                    ),
                    reply_markup=back_keyboard()
                )
                return

            save_future_signal(uid, "")

            return

        if action == "add_signal_users":
            save_future_signal(uid, text)
            return

        # ---------- Live signal ----------
        if action == "live_signal":

            session_id = state["session_id"]

            with DB_LOCK:
                conn = db()

                conn.execute("""
                    INSERT INTO live_signals(
                        session_id,content,confidence,created_at
                    )
                    VALUES(?,?,?,?)
                """, (
                    session_id,
                    text,
                    get_setting("confidence", "95–99%"),
                    now_utc().isoformat()
                ))

                conn.commit()

                users = conn.execute("""
                    SELECT user_id
                    FROM users
                    WHERE notifications=1
                """).fetchall()

                conn.close()

            for u in users:
                try:
                    bot.send_message(
                        u["user_id"],
                        T("live_signal").format(
                            content=escape(text),
                            confidence=escape(
                                get_setting(
                                    "confidence",
                                    "95–99%"
                                )
                            )
                        )
                    )
                except Exception:
                    pass

            bot.send_message(
                uid,
                "⚡ Live signal sent.",
                reply_markup=admin_menu()
            )

            STATES.pop(uid, None)
            return

        # ---------- VIP manager ----------
        if action == "vip_manage":
            parts = text.split()

            if len(parts) != 2:
                bot.send_message(uid, T("invalid"))
                return

            try:
                target = int(parts[0])
                days = int(parts[1])
                if days <= 0:
                    raise ValueError
            except Exception:
                bot.send_message(uid, T("invalid"))
                return

            until = now_utc() + timedelta(days=days)

            with DB_LOCK:
                conn = db()
                conn.execute("""
                    UPDATE users
                    SET status='VIP',vip_until=?
                    WHERE user_id=?
                """, (
                    until.isoformat(),
                    target
                ))
                conn.commit()
                conn.close()

            STATES.pop(uid, None)

            try:
                bot.send_message(
                    target,
                    f"⭐ <b>VIP Activated</b>\n\n"
                    f"Expiry: <b>{until.isoformat()}</b>"
                )
            except Exception:
                pass

            bot.send_message(
                uid,
                "✅ VIP updated.",
                reply_markup=admin_menu()
            )
            return

        # ---------- Wallet adjust ----------
        if action == "wallet_adjust":
            parts = text.split()

            if len(parts) != 2:
                bot.send_message(uid, T("invalid"))
                return

            try:
                target = int(parts[0])
                amount = parse_money(parts[1])
            except Exception:
                bot.send_message(uid, T("invalid"))
                return

            if parts[1].startswith("-"):
                amount = -amount

            with DB_LOCK:
                conn = db()

                row = conn.execute(
                    "SELECT wallet_cents FROM users WHERE user_id=?",
                    (target,)
                ).fetchone()

                if not row:
                    conn.close()
                    bot.send_message(uid, "❌ User not found.")
                    return

                new_balance = row["wallet_cents"] + amount

                if new_balance < 0:
                    conn.close()
                    bot.send_message(uid, "❌ Insufficient wallet.")
                    return

                conn.execute("""
                    UPDATE users
                    SET wallet_cents=?
                    WHERE user_id=?
                """, (
                    new_balance,
                    target
                ))

                conn.execute("""
                    INSERT INTO wallet_transactions(
                        user_id,type,amount_cents,
                        balance_after_cents,note,created_at
                    )
                    VALUES(?,?,?,?,?,?)
                """, (
                    target,
                    "ADMIN_ADJUST",
                    amount,
                    new_balance,
                    f"Admin adjustment by {uid}",
                    now_utc().isoformat()
                ))

                conn.commit()
                conn.close()

            STATES.pop(uid, None)

            bot.send_message(
                uid,
                f"✅ Wallet updated: ${money(new_balance)}",
                reply_markup=admin_menu()
            )
            return

        # ---------- Subadmin ----------
        if action == "subadmin":
            parts = text.split(maxsplit=1)

            if len(parts) != 2:
                bot.send_message(uid, T("invalid"))
                return

            try:
                target = int(parts[0])
            except Exception:
                bot.send_message(uid, T("invalid"))
                return

            permissions = {
                x.strip()
                for x in parts[1].split(",")
                if x.strip()
            }

            valid = {
                "signals",
                "uid",
                "users",
                "wallet",
                "withdraw",
                "broadcast",
                "settings",
                "analytics"
            }

            permissions &= valid

            with DB_LOCK:
                conn = db()
                conn.execute("""
                    INSERT INTO admins(user_id,permissions)
                    VALUES(?,?)
                    ON CONFLICT(user_id)
                    DO UPDATE SET permissions=excluded.permissions
                """, (
                    target,
                    ",".join(sorted(permissions))
                ))
                conn.commit()
                conn.close()

            STATES.pop(uid, None)

            bot.send_message(
                uid,
                "✅ Sub-admin permissions updated.",
                reply_markup=admin_menu()
            )
            return

        # ---------- Setting ----------
        if action == "setting":
            parts = text.split(maxsplit=1)

            if len(parts) != 2:
                bot.send_message(uid, T("invalid"))
                return

            key, value = parts

            set_setting(key, value)

            STATES.pop(uid, None)

            bot.send_message(
                uid,
                "✅ Setting saved.",
                reply_markup=admin_menu()
            )
            return

        # ---------- User manage ----------
        if action == "user_manage":
            try:
                target = int(text)
            except Exception:
                bot.send_message(uid, T("invalid"))
                return

            target_user = user_row(target)

            if not target_user:
                bot.send_message(
                    uid,
                    "❌ User not found.",
                    reply_markup=admin_menu()
                )
                STATES.pop(uid, None)
                return

            STATES.pop(uid, None)

            bot.send_message(
                uid,
                (
                    "👤 <b>USER</b>\n\n"
                    f"ID: <code>{target}</code>\n"
                    f"Name: {escape(target_user['first_name'] or '')}\n"
                    f"Status: <b>{target_user['status']}</b>\n"
                    f"Wallet: <b>${money(target_user['wallet_cents'])}</b>\n"
                    f"VIP Until: {escape(target_user['vip_until'] or 'None')}\n"
                    f"Referrals: {target_user['refs_count']}\n\n"
                    f"/vip {target} 30\n"
                    f"/free_limit {target} 10\n"
                    f"/wallet {target} 5"
                ),
                reply_markup=admin_menu()
            )
            return

    # No active state -> route menus
    if is_admin(uid):
        if text == T("btn_admin"):
            bot.send_message(
                uid,
                "👑 <b>ADMIN PANEL</b>",
                reply_markup=admin_menu()
            )
            return

        admin_buttons = [
            T("btn_add_signal"),
            T("btn_signals"),
            T("btn_live_session"),
            T("btn_users"),
            T("btn_uid_pending"),
            T("btn_vip_manage"),
            T("btn_wallet_manage"),
            T("btn_withdraw"),
            T("btn_broadcast"),
            T("btn_text_editor"),
            T("btn_notice_edit"),
            T("btn_rules_edit"),
            T("btn_subadmins"),
            T("btn_settings"),
            T("btn_analytics"),
            T("btn_maintenance"),
            T("btn_backup"),
            T("btn_user_manage")
        ]

        if text in admin_buttons:
            admin_action(message)
            return

    # User menu
    if text == T("btn_withdraw"):
        start_withdraw(message)
        return

    handle_user_button(message)


# ============================================================
# SAVE FUTURE SIGNAL
# ============================================================

def save_future_signal(admin_id, selected_users):

    state = STATES[admin_id]

    try:
        target = parse_signal_datetime(
            state["date"],
            state["time"]
        )
    except Exception:
        bot.send_message(
            admin_id,
            "❌ Date/time format ভুল। Signal বাতিল করা হয়েছে।",
            reply_markup=admin_menu()
        )
        STATES.pop(admin_id, None)
        return

    if target <= now_bd():
        bot.send_message(
            admin_id,
            "❌ Signal time future হতে হবে।",
            reply_markup=admin_menu()
        )
        STATES.pop(admin_id, None)
        return

    with DB_LOCK:
        conn = db()

        conn.execute("""
            INSERT INTO signals(
                signal_date,
                signal_time,
                pair,
                direction,
                confidence,
                audience,
                selected_users,
                created_at
            )
            VALUES(?,?,?,?,?,?,?,?)
        """, (
            target.strftime("%Y-%m-%d"),
            target.strftime("%H:%M"),
            state["pair"],
            state["direction"],
            state["confidence"],
            state["audience"],
            selected_users,
            now_utc().isoformat()
        ))

        conn.commit()
        conn.close()

    STATES.pop(admin_id, None)

    bot.send_message(
        admin_id,
        (
            "✅ <b>Future Signal Added</b>\n\n"
            f"📅 {target.strftime('%Y-%m-%d')}\n"
            f"🕐 {target.strftime('%H:%M')}\n"
            f"📌 {escape(state['pair'])}\n"
            f"📊 {state['direction']}\n"
            f"🎯 {escape(state['confidence'])}\n"
            f"👥 {state['audience']}"
        ),
        reply_markup=admin_menu()
    )


# ============================================================
# ADMIN COMMANDS
# ============================================================

@bot.message_handler(commands=["approve_uid"])
def approve_uid(message):

    if not can(message.from_user.id, "uid"):
        return

    parts = message.text.split()

    if len(parts) != 2:
        return

    try:
        target_user = int(parts[1])
    except Exception:
        return

    with DB_LOCK:
        conn = db()

        row = conn.execute("""
            SELECT * FROM uid_submissions
            WHERE user_id=? AND status='PENDING'
            ORDER BY id DESC
            LIMIT 1
        """, (target_user,)).fetchone()

        if not row:
            conn.close()
            bot.send_message(
                message.chat.id,
                "❌ Pending UID not found."
            )
            return

        duplicate = conn.execute("""
            SELECT id FROM uid_submissions
            WHERE uid=?
            AND status='APPROVED'
            AND user_id!=?
        """, (
            row["uid"],
            target_user
        )).fetchone()

        if duplicate:
            conn.close()

            bot.send_message(
                message.chat.id,
                T("uid_duplicate")
            )
            return

        conn.execute("""
            UPDATE uid_submissions
            SET status='APPROVED',
                reviewed_at=?,
                reviewed_by=?
            WHERE id=?
        """, (
            now_utc().isoformat(),
            message.from_user.id,
            row["id"]
        ))

        conn.execute("""
            UPDATE users
            SET status='VIP'
            WHERE user_id=?
        """, (target_user,))

        conn.commit()
        conn.close()

    bot.send_message(
        target_user,
        "⭐ <b>VIP Activated</b>\n\nYour UID has been approved."
    )

    bot.send_message(
        message.chat.id,
        "✅ UID approved."
    )


@bot.message_handler(commands=["reject_uid"])
def reject_uid(message):

    if not can(message.from_user.id, "uid"):
        return

    parts = message.text.split()

    if len(parts) != 2:
        return

    try:
        target_user = int(parts[1])
    except Exception:
        return

    with DB_LOCK:
        conn = db()

        conn.execute("""
            UPDATE uid_submissions
            SET status='REJECTED',
                reviewed_at=?,
                reviewed_by=?
            WHERE user_id=?
            AND status='PENDING'
        """, (
            now_utc().isoformat(),
            message.from_user.id,
            target_user
        ))

        conn.commit()
        conn.close()

    try:
        bot.send_message(
            target_user,
            "❌ আপনার UID rejected হয়েছে। আবার submit করতে পারেন।"
        )
    except Exception:
        pass

    bot.send_message(
        message.chat.id,
        "✅ UID rejected."
    )


@bot.message_handler(commands=["approve_withdraw"])
def approve_withdraw(message):

    if not can(message.from_user.id, "withdraw"):
        return

    parts = message.text.split()

    if len(parts) != 2:
        return

    try:
        wid = int(parts[1])
    except Exception:
        return

    with DB_LOCK:
        conn = db()

        row = conn.execute("""
            SELECT * FROM withdrawals
            WHERE id=? AND status='PENDING'
        """, (wid,)).fetchone()

        if not row:
            conn.close()
            bot.send_message(
                message.chat.id,
                "❌ Withdrawal not found."
            )
            return

        conn.execute("""
            UPDATE withdrawals
            SET status='APPROVED',
                reviewed_at=?,
                reviewed_by=?
            WHERE id=?
        """, (
            now_utc().isoformat(),
            message.from_user.id,
            wid
        ))

        conn.execute("""
            INSERT INTO wallet_transactions(
                user_id,type,amount_cents,
                balance_after_cents,note,created_at
            )
            SELECT
                user_id,
                'WITHDRAW_APPROVED',
                -amount_cents,
                wallet_cents,
                'Withdrawal approved',
                ?
            FROM users
            WHERE user_id=?
        """, (
            now_utc().isoformat(),
            row["user_id"]
        ))

        conn.commit()
        conn.close()

    try:
        bot.send_message(
            row["user_id"],
            f"✅ Withdrawal approved: ${money(row['amount_cents'])}"
        )
    except Exception:
        pass

    bot.send_message(
        message.chat.id,
        "✅ Withdrawal approved."
    )


@bot.message_handler(commands=["reject_withdraw"])
def reject_withdraw(message):

    if not can(message.from_user.id, "withdraw"):
        return

    parts = message.text.split()

    if len(parts) != 2:
        return

    try:
        wid = int(parts[1])
    except Exception:
        return

    with DB_LOCK:
        conn = db()

        row = conn.execute("""
            SELECT * FROM withdrawals
            WHERE id=? AND status='PENDING'
        """, (wid,)).fetchone()

        if not row:
            conn.close()
            return

        current = conn.execute(
            "SELECT wallet_cents FROM users WHERE user_id=?",
            (row["user_id"],)
        ).fetchone()["wallet_cents"]

        new_balance = current + row["amount_cents"]

        conn.execute("""
            UPDATE users
            SET wallet_cents=?
            WHERE user_id=?
        """, (
            new_balance,
            row["user_id"]
        ))

        conn.execute("""
            UPDATE withdrawals
            SET status='REJECTED',
                reviewed_at=?,
                reviewed_by=?
            WHERE id=?
        """, (
            now_utc().isoformat(),
            message.from_user.id,
            wid
        ))

        conn.execute("""
            INSERT INTO wallet_transactions(
                user_id,type,amount_cents,
                balance_after_cents,note,created_at
            )
            VALUES(?,?,?,?,?,?)
        """, (
            row["user_id"],
            "WITHDRAW_REFUND",
            row["amount_cents"],
            new_balance,
            "Withdrawal rejected/refunded",
            now_utc().isoformat()
        ))

        conn.commit()
        conn.close()

    try:
        bot.send_message(
            row["user_id"],
            f"❌ Withdrawal rejected.\n"
            f"${money(row['amount_cents'])} refunded."
        )
    except Exception:
        pass

    bot.send_message(
        message.chat.id,
        "✅ Withdrawal rejected/refunded."
    )


@bot.message_handler(commands=["vip"])
def vip_command(message):

    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()

    if len(parts) != 3:
        return

    try:
        uid = int(parts[1])
        days = int(parts[2])
    except Exception:
        return

    until = now_utc() + timedelta(days=days)

    with DB_LOCK:
        conn = db()
        conn.execute("""
            UPDATE users
            SET status='VIP',vip_until=?
            WHERE user_id=?
        """, (
            until.isoformat(),
            uid
        ))
        conn.commit()
        conn.close()

    bot.send_message(
        message.chat.id,
        "✅ VIP updated."
    )


@bot.message_handler(commands=["free_limit"])
def free_limit_command(message):

    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()

    if len(parts) != 3:
        return

    try:
        uid = int(parts[1])
        limit = int(parts[2])
    except Exception:
        return

    with DB_LOCK:
        conn = db()

        conn.execute("""
            INSERT INTO user_limits(user_id,free_limit)
            VALUES(?,?)
            ON CONFLICT(user_id)
            DO UPDATE SET free_limit=excluded.free_limit
        """, (
            uid,
            max(0, limit)
        ))

        conn.commit()
        conn.close()

    bot.send_message(
        message.chat.id,
        "✅ User free-signal limit updated."
    )


@bot.message_handler(commands=["wallet"])
def wallet_command(message):

    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()

    if len(parts) != 3:
        return

    try:
        uid = int(parts[1])
        amount = parse_money(parts[2])
    except Exception:
        return

    with DB_LOCK:
        conn = db()

        row = conn.execute(
            "SELECT wallet_cents FROM users WHERE user_id=?",
            (uid,)
        ).fetchone()

        if not row:
            conn.close()
            return

        new_balance = row["wallet_cents"] + amount

        conn.execute("""
            UPDATE users
            SET wallet_cents=?
            WHERE user_id=?
        """, (
            new_balance,
            uid
        ))

        conn.execute("""
            INSERT INTO wallet_transactions(
                user_id,type,amount_cents,
                balance_after_cents,note,created_at
            )
            VALUES(?,?,?,?,?,?)
        """, (
            uid,
            "ADMIN_COMMAND",
            amount,
            new_balance,
            "Admin wallet command",
            now_utc().isoformat()
        ))

        conn.commit()
        conn.close()

    bot.send_message(
        message.chat.id,
        f"✅ Wallet: ${money(new_balance)}"
    )


# ============================================================
# ANALYTICS
# ============================================================

def show_analytics(admin_id):

    with DB_LOCK:
        conn = db()

        users = conn.execute(
            "SELECT COUNT(*) c FROM users"
        ).fetchone()["c"]

        vip = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE status='VIP'"
        ).fetchone()["c"]

        signals = conn.execute(
            "SELECT COUNT(*) c FROM signals"
        ).fetchone()["c"]

        deliveries = conn.execute(
            "SELECT COUNT(*) c FROM signal_deliveries"
        ).fetchone()["c"]

        votes = conn.execute(
            "SELECT COUNT(*) c FROM signal_votes"
        ).fetchone()["c"]

        withdrawals = conn.execute(
            "SELECT COUNT(*) c FROM withdrawals"
        ).fetchone()["c"]

        pending = conn.execute("""
            SELECT COUNT(*) c
            FROM withdrawals
            WHERE status='PENDING'
        """).fetchone()["c"]

        conn.close()

    bot.send_message(
        admin_id,
        (
            "📈 <b>ANALYTICS</b>\n\n"
            f"👥 Users: <b>{users}</b>\n"
            f"⭐ VIP: <b>{vip}</b>\n"
            f"📊 Signals: <b>{signals}</b>\n"
            f"📨 Deliveries: <b>{deliveries}</b>\n"
            f"🗳 Votes: <b>{votes}</b>\n"
            f"💸 Withdrawals: <b>{withdrawals}</b>\n"
            f"⏳ Pending Withdrawals: <b>{pending}</b>"
        ),
        reply_markup=admin_menu()
    )


# ============================================================
# BACKUP
# ============================================================

def create_backup():

    try:
        filename = os.path.join(
            BACKUP_DIR,
            f"bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        )

        with DB_LOCK:
            source = sqlite3.connect(DB_FILE)
            target = sqlite3.connect(filename)

            source.backup(target)

            target.close()
            source.close()

        files = sorted(
            [
                os.path.join(BACKUP_DIR, x)
                for x in os.listdir(BACKUP_DIR)
                if x.endswith(".db")
            ],
            key=os.path.getmtime,
            reverse=True
        )

        for old in files[10:]:
            try:
                os.remove(old)
            except Exception:
                pass

        logger.info("Backup created: %s", filename)

    except Exception:
        logger.exception("Backup failed")


# ============================================================
# SCHEDULER
# ============================================================

def scheduler_loop():

    last_backup = 0

    while True:

        try:

            # ------------------------------
            # Future signals
            # ------------------------------

            now = now_bd()

            with DB_LOCK:
                conn = db()

                signals = conn.execute("""
                    SELECT *
                    FROM signals
                    WHERE sent=0
                    ORDER BY id ASC
                """).fetchall()

                users = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE blocked=0
                """).fetchall()

                conn.close()

            for signal in signals:

                try:
                    due = signal_due_datetime(signal)
                except Exception:
                    continue

                if due > now:
                    continue

                for u in users:

                    uid = u["user_id"]

                    if not user_can_receive_signal(uid, signal):
                        continue

                    if not u["notifications"]:
                        continue

                    quota = (
                        signal["audience"] == "ALL"
                        and not vip_active(uid)
                    )

                    send_signal_to_user(
                        uid,
                        signal,
                        =quota
                    )

                with DB_LOCK:
                    conn = db()

                    conn.execute("""
                        UPDATE signals
                        SET sent=1
                        WHERE id=?
                    """, (signal["id"],))

                    conn.commit()
                    conn.close()

            # ------------------------------
            # VIP expiry
            # ------------------------------

            expiry_limit = now_utc() + timedelta(days=3)

            with DB_LOCK:
                conn = db()

                expiring = conn.execute("""
                    SELECT user_id,vip_until
                    FROM users
                    WHERE status='VIP'
                    AND vip_until IS NOT NULL
                """).fetchall()

                conn.close()

            for u in expiring:

                try:
                    expiry = datetime.fromisoformat(
                        u["vip_until"]
                    )

                    if now_utc() < expiry <= expiry_limit:
                        bot.send_message(
                            u["user_id"],
                            (
                                "⏳ <b>VIP Expiry Reminder</b>\n\n"
                                f"Your VIP expires on:\n"
                                f"<b>{escape(u['vip_until'])}</b>"
                            )
                        )

                except Exception:
                    pass

            # ------------------------------
            # Backup every 6 hours
            # ------------------------------

            if time.time() - last_backup >= 21600:

                create_backup()

                last_backup = time.time()

        except Exception:
            logger.exception("Scheduler error")

        time.sleep(5)


# ============================================================
# ERROR HANDLER
# ============================================================

@bot.message_handler(func=lambda m: False)
def unused_handler(message):
    pass


# ============================================================
# START DATABASE + SCHEDULER
# IMPORTANT: polling is LAST
# ============================================================

init_db()

scheduler_thread = threading.Thread(
    target=scheduler_loop,
    daemon=True
)

scheduler_thread.start()

logger.info("SM QUATEX SURE SHORT started.")

while True:
    try:
        bot.infinity_polling(
            skip_pending=True,
            timeout=30,
            long_polling_timeout=30
        )

    except Exception:
        logger.exception("Polling crashed. Restarting in 5 seconds.")
        time.sleep(5)
