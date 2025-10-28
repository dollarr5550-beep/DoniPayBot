\
# donipay_card2card.py
# Card2Card integration module (Flask) - simplified for prototype
import os
import sqlite3
import logging
import hmac
import hashlib
import json
import time
from decimal import Decimal
from typing import Optional

import requests
from flask import Flask, request, jsonify, abort

DB_PATH = os.getenv('DONIPAY_DB', 'donipay.db')
API_BASE = os.getenv('CARD2CARD_API_URL', 'https://api.bank.example/v1')
MERCHANT_ID = os.getenv('CARD2CARD_MERCHANT_ID', 'REPLACE_ME')
SECRET = os.getenv('CARD2CARD_SECRET', 'REPLACE_ME')
CALLBACK_SECRET = os.getenv('CARD2CARD_CALLBACK_SECRET', 'REPLACE_ME')

HTTP_TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAY = 2

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('donipay_card2card')

app = Flask(__name__)

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS payouts (id INTEGER PRIMARY KEY AUTOINCREMENT, ext_id TEXT UNIQUE, user_id INTEGER, to_card_masked TEXT, amount TEXT, currency TEXT DEFAULT 'UZS', status TEXT, bank_tx_id TEXT, error TEXT, created_at INTEGER, updated_at INTEGER)")
    cur.execute("CREATE TABLE IF NOT EXISTS callbacks (id INTEGER PRIMARY KEY AUTOINCREMENT, payout_ext_id TEXT, payload TEXT, verified INTEGER, received_at INTEGER)")
    con.commit()
    con.close()

def get_conn():
    return sqlite3.connect(DB_PATH)

def mask_card(card_pan: str) -> str:
    if not card_pan or len(card_pan) < 10:
        return '****'
    return card_pan[:6] + ('*' * (len(card_pan) - 10)) + card_pan[-4:]

def now_ts() -> int:
    return int(time.time())

def sign_payload(payload: dict, secret: str = SECRET) -> str:
    serialized = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    mac = hmac.new(secret.encode(), serialized.encode(), hashlib.sha256).hexdigest()
    return mac

def post_to_bank(endpoint: str, payload: dict, headers: Optional[dict] = None) -> dict:
    url = API_BASE.rstrip('/') + '/' + endpoint.lstrip('/')
    headers = headers or {}
    headers.update({'Content-Type': 'application/json', 'X-Merchant-Id': MERCHANT_ID})
    headers['X-Signature'] = sign_payload(payload)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(url, json=payload, timeout=HTTP_TIMEOUT, headers=headers)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            logger.warning('Bank request failed (attempt %s/%s): %s', attempt, MAX_RETRIES, e)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_DELAY * (2 ** (attempt - 1)))

def create_payout(ext_id: str, user_id: int, to_card_pan: str, amount: Decimal, currency: str = 'UZS') -> dict:
    con = get_conn()
    cur = con.cursor()
    created = now_ts()
    try:
        cur.execute('INSERT INTO payouts (ext_id, user_id, to_card_masked, amount, currency, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (ext_id, user_id, mask_card(to_card_pan), str(amount), currency, 'pending', created, created))
        con.commit()
    except sqlite3.IntegrityError:
        cur.execute('SELECT id, status, bank_tx_id, error FROM payouts WHERE ext_id = ?', (ext_id,))
        row = cur.fetchone()
        con.close()
        return {'status': row[1], 'bank_tx_id': row[2], 'error': row[3]}

    payload = {
        'merchant_id': MERCHANT_ID,
        'ext_id': ext_id,
        'to_pan': to_card_pan,
        'amount': str(amount),
        'currency': currency
    }

    logger.info('Sending payout to bank ext_id=%s user=%s amount=%s', ext_id, user_id, amount)
    resp = None
    try:
        resp = post_to_bank('/card2card/transfer', payload)
    except Exception as e:
        cur.execute('UPDATE payouts SET status = ?, error = ?, updated_at = ? WHERE ext_id = ?', ('failed', str(e), now_ts(), ext_id))
        con.commit()
        con.close()
        logger.error('Payout request failed for ext_id=%s: %s', ext_id, e)
        raise

    bank_status = resp.get('status') or resp.get('result') or 'unknown'
    bank_tx_id = resp.get('tx_id') or resp.get('bank_tx_id')
    cur.execute('UPDATE payouts SET status = ?, bank_tx_id = ?, updated_at = ? WHERE ext_id = ?', (bank_status, bank_tx_id, now_ts(), ext_id))
    con.commit()
    con.close()
    return {'status': bank_status, 'bank_tx_id': bank_tx_id, 'raw': resp}

def get_payout_status(ext_id: str) -> Optional[dict]:
    con = get_conn()
    cur = con.cursor()
    cur.execute('SELECT ext_id, status, bank_tx_id, error, amount FROM payouts WHERE ext_id = ?', (ext_id,))
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    return {'ext_id': row[0], 'status': row[1], 'bank_tx_id': row[2], 'error': row[3], 'amount': row[4]}

@app.route('/webhook/card2card', methods=['POST'])
def card2card_webhook():
    payload = request.get_data(as_text=True)
    try:
        data = request.get_json(force=True)
    except Exception:
        logger.warning('Invalid JSON in webhook')
        abort(400)

    sig_header = request.headers.get('X-Signature') or request.headers.get('X-Hmac')
    verified = False
    if sig_header and CALLBACK_SECRET:
        mac = hmac.new(CALLBACK_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(mac, sig_header):
            verified = True
    else:
        verified = True

    con = get_conn()
    cur = con.cursor()
    cur.execute('INSERT INTO callbacks (payout_ext_id, payload, verified, received_at) VALUES (?, ?, ?, ?)',
                (data.get('ext_id'), json.dumps(data, ensure_ascii=False), int(verified), now_ts()))
    con.commit()

    ext_id = data.get('ext_id') or data.get('merchant_ext_id')
    new_status = data.get('status') or data.get('result')
    bank_tx = data.get('tx_id') or data.get('bank_tx_id')
    error = data.get('error')

    if ext_id:
        cur.execute('SELECT id, status FROM payouts WHERE ext_id = ?', (ext_id,))
        row = cur.fetchone()
        if row:
            local_status = row[1]
            if local_status != new_status:
                cur.execute('UPDATE payouts SET status = ?, bank_tx_id = ?, error = ?, updated_at = ? WHERE ext_id = ?',
                            (new_status, bank_tx, error, now_ts(), ext_id))
                con.commit()
                logger.info('Payout ext_id=%s updated -> %s', ext_id, new_status)
        else:
            logger.warning('Webhook ext_id not found locally: %s', ext_id)
    con.close()
    return jsonify({'result': 'ok'})

def withdraw_command_handler(bot_context_send_func, user_id: int, to_card: str, amount: Decimal) -> dict:
    if amount <= 0:
        return {'ok': False, 'msg': 'Noto`g`ri summa'}

    ext_id = f'DONIPAY-PAYOUT-{user_id}-{int(time.time())}'
    try:
        resp = create_payout(ext_id=ext_id, user_id=user_id, to_card_pan=to_card, amount=amount)
    except Exception as e:
        logger.exception('Withdraw failed')
        return {'ok': False, 'msg': f'O`tish muvaffaqiyatsiz: {e}'}

    status = resp.get('status')
    if status in ('ok', 'success', 'completed'):
        bot_context_send_func(user_id, f'✅ {amount} UZS muvaffaqiyatli o‘tkazildi. Tx: {resp.get("bank_tx_id")}')
        return {'ok': True, 'msg': 'ok', 'status': status}
    else:
        bot_context_send_func(user_id, f'ℹ️ O`tkazma qabul qilindi, holati: {status}. Sizga xabar beramiz.')
        return {'ok': True, 'msg': 'processing', 'status': status}

if __name__ == '__main__':
    init_db()
    logger.info('Card2Card module running as webhook server on http://0.0.0.0:8080')
    app.run(host='0.0.0.0', port=8080)
