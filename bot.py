    )

    STATE[message.from_user.id] = "SETTINGS_ADMIN"


# =======================================================
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
