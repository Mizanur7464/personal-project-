# Crypto Trading Assistant Telegram Bot

Telegram bot for crypto traders: market summary, alerts, watchlist, news, AI journal, and paid Pro/Elite plans.

---

## Features

| Plan | Features |
|------|----------|
| **Free** | Daily summary, volatility alert, risk reminder, crypto news |
| **Pro** | EMA/RSI, watchlist, price & technical alerts |
| **Elite** | AI trade journal, weekly AI summary |
| **Admin** | Full access, stats, backup, tier management |

**Payments:** NOWPayments (crypto) or Cryptomus — monthly subscription with auto-expiry.

---

## Setup (Windows PowerShell)

```powershell
cd "d:\New project momin"
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env with your tokens
python main.py
```

---

## Environment Variables

See `.env.example` for full list. Minimum:

```env
TELEGRAM_BOT_TOKEN=your_token
ADMIN_TELEGRAM_ID=123456789
```

**Payments (NOWPayments):**
```env
NOWPAYMENTS_API_KEY=...
NOWPAYMENTS_IPN_SECRET=...
NOWPAYMENTS_IPN_CALLBACK_URL=https://your-domain.com/webhook/nowpayments
WEBHOOK_PORT=8080
```

**Or Cryptomus:**
```env
PAYMENT_PROVIDER=cryptomus
CRYPTOMUS_MERCHANT_UUID=...
CRYPTOMUS_API_KEY=...
```

---

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Menu |
| `/summary` | Top coins summary |
| `/volatility` | High volatility coins |
| `/news` | Crypto news |
| `/watchlist` | Your watchlist (Pro/Elite) |
| `/watch_add BTC` | Add to watchlist |
| `/technical BTC 1h` | EMA/RSI (Pro/Elite) |
| `/add_alert BTC 65000 above` | Price alert |
| `/tech_alert BTC rsi_above 1h` | Technical alert |
| `/journal` | AI journal (Elite) |
| `/weekly_summary` | Weekly AI report (Elite) |
| `/referral` | Referral code |
| `/pay pro` / `/pay elite` | Payment |
| `/check_payment` | Check payment status |
| `/upgrade` | Plan info |

**Admin:** `/admin_stats` `/admin_backup` `/admin_set_tier USER_ID tier`

---

## Docker

```bash
docker build -t crypto-bot .
docker run -d --env-file .env -p 8080:8080 -v bot-data:/app/data crypto-bot
```

---

## Tests

```powershell
pytest tests/ -v
```

---

## Project Structure

```text
.
├── bot/main.py           # Telegram bot
├── bot/extra_handlers.py # Watchlist, news, admin, etc.
├── core/db.py            # SQLite database
├── core/config.py        # Settings
├── services/             # Binance, payments, AI, news
├── webhooks/ipn_server.py # NOWPayments webhook
├── main.py               # Entry point
└── Dockerfile
```
