# 90C
Automated trading bot for Polymarket 15-min crypto prediction markets (BTC, ETH, SOL, XRP). Buys shares at 98-99¢, profits at resolution. Features real-time WebSocket feeds, multi-strategy signals, auto-claiming, live web dashboard for P&L tracking, and a competitive leaderboard.

---

## Features

- **Automated Trading** — Monitors Polymarket 15-minute prediction markets 24/7 and executes trades automatically
- **Buy Once Strategy** — Buys when price dips to 98-99¢, sells at resolution for consistent small gains
- **Real-Time WebSocket** — Live price and orderbook data from Polymarket
- **Auto-Claim** — Automatically redeems winning positions every 15 minutes
- **Web Dashboard** — Live P&L, trade history, positions, and manual claim button at `http://localhost:5052`
- **Leaderboard** — Compete with other traders on the global ranking
- **Risk Management** — Stop-loss, trailing stops, position sizing, and stale order cancellation

---

## Quick Start

1. Install **Python 3.8+**

2. Download the .rar or clone the repo

3. Install dependencies:
```bash
cd 90cent
pip install -r requirements.txt
```

4. Configure `.env.example` with your keys and rename it to `.env`

5. Rename `config.example.py` to `config.py`. Default settings work out of the box. Change `LEADERBOARD_USERNAME` on line 241 to set your name on the ranking.

6. Run the bot:
```bash
python trading_bot.py
```

The dashboard starts automatically at **http://localhost:5052**

---

## Configuration

### .env (Required)

| Variable | Description | Required |
|---|---|---|
| `POLYMARKET_PRIVATE_KEY` | Your wallet private key 
| `POLYMARKET_API_KEY` | API credential 
| `POLYMARKET_API_SECRET` | API secret 
| `POLYMARKET_API_PASSPHRASE` | API passphrase 
| `LEADERBOARD_USERNAME` | Your name on the leaderboard 

### config.py (Optional)

| Setting | Default | Description |
|---|---|---|
| `order_size` | 140.0 | Shares per trade |
| `min_price` | 0.98 | Buy when price drops to 98¢ |
| `max_price` | 0.99 | Don't buy above 99¢ |
| `stop_loss_price` | 0.92 | Exit if price drops to 92¢ |
| `trailing_stop_distance` | 0.05 | Lock in 5¢ gains |

See `Documentation/GUIA.md` for full configuration details.

---

## Project Structure

```
90cent/
├── trading_bot.py           # Main bot
├── dashboard.py             # Web dashboard (Flask)
├── polymarket_client.py     # Polymarket API client
├── order_manager.py         # Order execution & risk
├── position_tracker.py      # Position & P&L tracking
├── claim_utils.py           # Auto-claim winnings
├── config.example.py        # Config template
├── .env.example             # Environment template
├── requirements.txt         # Dependencies
├── strategies/              # Trading strategies
│   ├── momentum_strategy.py
│   ├── technical_indicators.py
│   └── ai_predictor.py
└── Documentation/
    ├── GUIA.md              # Detailed setup guide
    └── RESUMEN.md           # Quick start (Spanish)
```

---

## Dashboard

The web dashboard starts automatically with the bot at **http://localhost:5052**

- Real-time P&L and win rate
- Trade history with buy/sell matching
- Open positions tracker
- Manual claim button for winning positions
- P&L reset option

---

## Security

Your private keys are **never** committed to git. The `.gitignore` blocks `.env` and `config.py` from being tracked. Only the templates (`.env.example`, `config.example.py`) are included in the repo.

---

## Documentation

- [GUIA.md](90cent/Documentation/GUIA.md) — Full setup and configuration guide
- [RESUMEN.md](90cent/Documentation/RESUMEN.md) — Quick start summary

---

## Disclaimer

This bot is provided as-is for educational purposes. Trading on prediction markets involves risk. Use at your own discretion and only trade with funds you can afford to lose.
