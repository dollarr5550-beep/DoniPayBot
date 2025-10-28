\
# donipay_bot.py
import os
import logging
import sqlite3
from decimal import Decimal
from datetime import datetime

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# Import the withdraw helper from card2card module
from donipay_card2card import withdraw_command_handler, init_db as init_card_db

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
DB_PATH = os.getenv('DONIPAY_DB', 'donipay.db')

if not TELEGRAM_TOKEN:
    raise RuntimeError("Set TELEGRAM_TOKEN environment variable")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('donipay_bot')

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    con = get_conn()
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS wallets (user_id INTEGER PRIMARY KEY, balance TEXT DEFAULT '0.00')")
    con.commit()
    con.close()

def ensure_user(user_id: int, username: str, full_name: str):
    con = get_conn()
    cur = con.cursor()
    cur.execute('SELECT id FROM users WHERE id = ?', (user_id,))
    if cur.fetchone() is None:
        cur.execute('INSERT INTO users (id, username, full_name, created_at) VALUES (?, ?, ?, ?)',
                    (user_id, username, full_name, datetime.utcnow().isoformat()))
        cur.execute('INSERT OR IGNORE INTO wallets (user_id, balance) VALUES (?, ?)', (user_id, '0.00'))
        con.commit()
    else:
        cur.execute('UPDATE users SET username = ?, full_name = ? WHERE id = ?', (username, full_name, user_id))
        con.commit()
    con.close()

def get_balance(user_id: int):
    con = get_conn()
    cur = con.cursor()
    cur.execute('SELECT balance FROM wallets WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    con.close()
    return Decimal(row[0]) if row else Decimal('0.00')

def set_balance(user_id: int, new_balance: Decimal):
    con = get_conn()
    cur = con.cursor()
    cur.execute('INSERT OR IGNORE INTO wallets (user_id, balance) VALUES (?, ?)', (user_id, str(new_balance)))
    cur.execute('UPDATE wallets SET balance = ? WHERE user_id = ?', (str(new_balance), user_id))
    con.commit()
    con.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username or '', f"{user.first_name or ''} {user.last_name or ''}".strip())
    await update.message.reply_text(f"Salom, {user.first_name}! DoniPay botga xush kelibsiz.\nBuyruqlar:\n/balance\n/topup <miqdor>\n/withdraw <karta> <miqdor>")

async def balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username or '', f"{user.first_name or ''} {user.last_name or ''}".strip())
    bal = get_balance(user.id)
    await update.message.reply_text(f"Sizning balansingiz: {bal} UZS")

async def topup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username or '', f"{user.first_name or ''} {user.last_name or ''}".strip())
    args = context.args
    if not args:
        await update.message.reply_text("Iltimos summa kiriting. Misol: /topup 10000")
        return
    try:
        amount = Decimal(args[0])
        if amount <= 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("Noto'g'ri summa.")
        return
    bal = get_balance(user.id)
    new_bal = bal + amount
    set_balance(user.id, new_bal)
    await update.message.reply_text(f"✅ {amount} UZS muvaffaqiyatli to'ldirildi. Yangi bal: {new_bal} UZS")

async def withdraw_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username or '', f"{user.first_name or ''} {user.last_name or ''}".strip())
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Foydalanish: /withdraw <karta> <miqdor>")
        return
    to_card = args[0]
    try:
        amount = Decimal(args[1])
        if amount <= 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("Noto'g'ri summa.")
        return

    bal = get_balance(user.id)
    if bal < amount:
        await update.message.reply_text(f"Balansingiz yetarli emas: {bal} UZS")
        return

    set_balance(user.id, bal - amount)

    def bot_send(chat_id, text):
        try:
            application = context.application
            application.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.exception("Failed to send message: %s", e)

    res = withdraw_command_handler(bot_send, user.id, to_card, amount)
    if not res.get('ok'):
        set_balance(user.id, get_balance(user.id) + amount)
        await update.message.reply_text(f"O'tkazma xatolik: {res.get('msg')}")
    else:
        await update.message.reply_text(f"O'tkazma qabul qilindi. Holat: {res.get('status')}")

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

def main():
    init_db()
    init_card_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_handler))
    app.add_handler(CommandHandler('balance', balance_handler))
    app.add_handler(CommandHandler('topup', topup_handler))
    app.add_handler(CommandHandler('withdraw', withdraw_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, help_handler))
    logger.info("DoniPay bot started (polling)...")
    app.run_polling()

if __name__ == '__main__':
    main()
