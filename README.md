# Crypto RSI Trading Bot

An RSI-based cryptocurrency trading bot that scans for oversold opportunities and executes paper or live trades via Coinbase.

## How It Works

1. Every hour, scans top coins on CoinGecko for free market data
2. Calculates RSI (14-period) for each coin
3. **Buys** coins with RSI < 35 (oversold)
4. **Sells** when RSI > 65 (recovered), profit > 15%, or loss > 10% (stop-loss)
5. Tracks a virtual $100 portfolio in `paper_portfolio.json`

## Setup

```bash
pip3 install -r requirements.txt
```

### Paper Trading (default)

```bash
python3 crypto_bot.py
```

### Live Trading (Coinbase)

Set your API credentials as environment variables:

```bash
export COINBASE_API_KEY="your-key"
export COINBASE_API_SECRET="your-secret"
```

Then set `paper_trading` to `False` in `crypto_bot.py` and run.

### Scheduled Runs

```bash
python3 scheduler.py               # Run every 60 minutes
python3 scheduler.py --interval 30 # Run every 30 minutes
python3 scheduler.py --once        # Run once and exit
```

## Configuration

Key settings in `crypto_bot.py`:

- `starting_capital` — Initial USD balance ($100 default)
- `rsi_oversold` / `rsi_overbought` — Buy/sell RSI thresholds (35/65)
- `max_positions` — Maximum concurrent holdings (5)
- `position_size_pct` — Fraction of cash per trade (20%)
- `take_profit_pct` / `stop_loss_pct` — Exit rules (+15% / -10%)
- `min_market_cap`, `max_price_usd`, `min_volume_24h` — Coin quality filters

## Testing

```bash
pip3 install -r requirements-dev.txt
python3 -m pytest test_crypto_bot.py -v
```

## Disclaimer

This bot is for educational purposes. Use live trading at your own risk.
