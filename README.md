DoniPay - Ready Telegram bot (prototype)
=======================================

What is inside:
- donipay_bot.py       -> Telegram bot (polling) with /topup, /balance, /withdraw
- donipay_card2card.py -> Card2Card webhook & payout helper (Flask)
- requirements.txt
- .env.example
- systemd and nginx example files

Quick start (prototype, on Ubuntu):
1. Copy files to server, create virtualenv, install requirements:
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt

2. Create and edit .env with your real credentials (DONIPAY_DB path, TELEGRAM_TOKEN etc.)

3. Initialize DBs (the first run will create sqlite DB):
   python donipay_bot.py
   # in another terminal or service, run the webhook:
   python donipay_card2card.py

4. For production, run donipay_card2card.py under gunicorn and reverse-proxy via nginx.
   See systemd/nginx examples included.

Security notes:
- This is a prototype. Do NOT store PAN/CVV in plaintext.
- Use bank's sandbox for testing.
- For production handle secrets via environment variables or a secrets manager.
