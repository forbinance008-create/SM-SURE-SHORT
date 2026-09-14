import sqlite3
import time
from datetime import datetime
import telebot
from telebot import types

TOKEN = "8948659386:AAEB3zPLwa6JcwiMLsIT_hZ05SjfEPm6ZpM"
ADMIN_ID = 6470135702
QUOTEX_REF_LINK = "https://broker-qx.pro/sign-up/?lid=2350796" 

bot = telebot.TeleBot(TOKEN)

# --- SQLite Database Setup ---
def init_db():
    conn = sqlite3.connect('bot_database.db', check_same_thread=False)
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            status TEXT,
            last_uid_date TEXT,
            wallet REAL,
            referred_by INTEGER,
            refs_count INTEGER,
            trade_counted INTEGER,
            uid TEXT
        )
    ''')
    
    # Signals table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY,
            text TEXT,
            datetime TEXT
        )
    ''')
    
    # Sub admins table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sub_admins (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    return sqlite3.connect('bot_database.db', check_same_thread=False)

def get_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'status': row[1],
            'last_uid_date': datetime.strptime(row[2], '%Y-%m-%d').date() if row[2] else None,
            'wallet': row[3],
            'referred_by': row[4],
            'refs_count': row[5],
            'trade_counted': bool(row[6]),
            'uid': row[7]
        }
    return None

def save_user(user_id, data):
    conn = get_db_connection()
    cursor = conn.cursor()
    uid_date_str = data['last_uid_date'].strftime('%Y-%m-%d') if data['last_uid_date'] else None
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, status, last_uid_date, wallet, referred_by, refs_count, trade_counted, uid)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, data['status'], uid_date_str, data['wallet'], data['referred_by'], data['refs_count'], int(data['trade_counted']), data['uid']))
    conn.commit()
    conn.close()

def is_admin(user_id):
    if str(user_id) == str(ADMIN_ID):
        return True
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM sub_admins WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

pending_uids = {}
maintenance_mode = False
live_mode = True

@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        user_id = message.from_user.id
        args = message.text.split()
        user = get_user(user_id)

        if not user:
            referred_by = None
            if len(args) > 1 and args[1].startswith('ref_'):
                try:
                    ref_id = int(args[1].split('_')[1])
                    if ref_id != user_id and get_user(ref_id):
                        referred_by = ref_id
                except ValueError:
                    pass

            user = {
                'status': 'Normal',
                'last_uid_date': None,
                'wallet': 0.0,
                'referred_by': referred_by,
                'refs_count': 0,
                'trade_counted': False,
                'uid': None
            }
            save_user(user_id, user)
        else:
            if len(args) > 1 and args[1].startswith('ref_'):
                try:
                    ref_id = int(args[1].split('_')[1])
                    if ref_id != user_id and get_user(ref_id) and user['referred_by'] is None:
                        user['referred_by'] = ref_id
                        save_user(user_id, user)
                except ValueError:
                    pass

        if maintenance_mode and not is_admin(user_id):
            bot.send_message(message.chat.id, "??? ??? ???????? ??????????? ????? ????! ?????????? ???? ?? ??????? ???? ??????")
            return

        show_user_main_menu(message.chat.id, user_id)
    except Exception as e:
        print(f"Error in start: {e}")

def show_user_main_menu(chat_id, user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_signals = types.KeyboardButton('?? Future Signals')
    btn_live = types.KeyboardButton('? Live Signals')

    user = get_user(user_id)
    user_status = user['status'] if user else 'Normal'
    
    if user_status != 'VIP':
        btn_uid = types.KeyboardButton('?? Submit Quotex UID')
        markup.add(btn_signals, btn_live, btn_uid)
    else:
        markup.add(btn_signals, btn_live)

    btn_status = types.KeyboardButton('?? My Status')
    btn_wallet = types.KeyboardButton('?? Wallet & Withdraw')
    btn_rules = types.KeyboardButton('?? VIP Join Rules')
    btn_ref = types.KeyboardButton('?? Referral Link')
    markup.add(btn_status, btn_wallet, btn_rules, btn_ref)

    if is_admin(user_id):
        btn_admin = types.KeyboardButton('?? Admin Master Control')
        markup.add(btn_admin)

    bot.send_message(chat_id, "???????! ???? ???? ????? ????????? ?????? ???? ???:", reply_markup=markup)

@bot.message_handler(func=lambda message: True)
def handle_text_messages(message):
    try:
        user_id = message.from_user.id
        text = message.text
        user = get_user(user_id)

        if not user:
            user = {
                'status': 'Normal', 'last_uid_date': None, 'wallet': 0.0,
                'referred_by': None, 'refs_count': 0, 'trade_counted': False, 'uid': None
            }
            save_user(user_id, user)

        if maintenance_mode and not is_admin(user_id):
            bot.send_message(message.chat.id, "??? ????????????? ??? ???? ???????? ???? ??????? ???? ????")
            return

        if text == '?? My Status':
            bot.send_message(message.chat.id, f"?? Telegram ID: `{user_id}`\n?? Status: **{user['status']}**\n?? Wallet: **${user['wallet']:.2f}**\n?? ??? ?????: **{user['refs_count']} ??**", parse_mode='Markdown')

        elif text == '?? Referral Link':
            bot_info = bot.get_me()
            ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
            bot.send_message(message.chat.id, f"?? **????? ????? ??????? ????:**\n{ref_link}\n\n?? ??? ??? ?????: **{user['refs_count']} ??**\n\n?? ?????? ????? ???????? ???? ????? ????!", parse_mode='Markdown')

        elif text == '?? Wallet & Withdraw':
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton('?? Withdraw Request', callback_data='withdraw_req'))
            bot.send_message(message.chat.id, f"?? ????? ??????? ?????? ?????????: **${user['wallet']:.2f}**\n?????? ???? ????? ????? ????? ?????", reply_markup=markup, parse_