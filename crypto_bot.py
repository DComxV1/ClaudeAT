#!/usr/bin/env python3
"""
====================================================
  Crypto RSI Trading Bot
  Strategy: RSI Oversold Detection
  Starting Capital: $100 (Paper Trading Mode)
  Exchange: Coinbase (Advanced Trade API)
====================================================

HOW IT WORKS:
  1. Every hour, scans top coins on CoinGecko for free market data
  2. Calculates RSI (14-period) for each coin
  3. BUYS coins with RSI < 35 (oversold / dipped too far down)
  4. SELLS when RSI > 65 (recovered), profit > 15%, or loss > 10% (stop-loss)
  5. Tracks a virtual $100 portfolio in paper_portfolio.json

SETUP:
  1. pip install requests coinbase-advanced-py
  2. For live trading: set COINBASE_API_KEY and COINBASE_API_SECRET in your environment
  3. Run: python crypto_bot.py
  4. Schedule hourly: python scheduler.py
"""

import os
import json
import time
import logging
import requests
from datetime import datetime
from pathlib import Path

# ============================================================
# CONFIGURATION  — tweak these to adjust behavior
# ============================================================
CONFIG = {
    # Capital settings
    "starting_capital": 100.0,      # Starting cash in USD

    # Mode
    "paper_trading": True,           # True = simulate only, False = real Coinbase trades

    # RSI thresholds
    "rsi_period": 14,                # Standard RSI period
    "rsi_oversold": 35,              # BUY signal: RSI below this
    "rsi_overbought": 65,            # SELL signal: RSI above this

    # Position sizing
    "max_positions": 5,              # Hold at most 5 coins at once
    "position_size_pct": 0.20,       # Use up to 20% of cash per trade
    "min_trade_usd": 5.0,            # Minimum trade size in USD

    # Exit rules
    "take_profit_pct": 15.0,         # Sell if +15% profit
    "stop_loss_pct": -10.0,          # Sell if -10% loss

    # Coin filters (avoids garbage coins)
    "min_market_cap":   50_000_000,  # At least $50M market cap
    "max_price_usd":    10.0,        # Under $10 per coin (affordable & volatile)
    "min_volume_24h":    1_000_000,  # At least $1M daily volume

    # Coinbase API credentials (set as environment variables — never hardcode!)
    "coinbase_api_key":    os.getenv("COINBASE_API_KEY", ""),
    "coinbase_api_secret": os.getenv("COINBASE_API_SECRET", ""),
}

STABLECOINS = {"usdt", "usdc", "busd", "dai", "tusd", "usdp", "frax", "usdd", "gusd", "lusd"}

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("crypto_bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("CryptoBot")

# ============================================================
# PORTFOLIO — persisted to JSON between runs
# ============================================================
PORTFOLIO_FILE = "paper_portfolio.json"


def load_portfolio() -> dict:
    """Load or initialize the paper trading portfolio."""
    if Path(PORTFOLIO_FILE).exists():
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    portfolio = {
        "cash": CONFIG["starting_capital"],
        "holdings": {},   # { "BTC": { "id": "bitcoin", "quantity": 0.001, "avg_cost": 30000 } }
        "trades": [],
        "created": datetime.now().isoformat(),
    }
    save_portfolio(portfolio)
    log.info(f"📂 New portfolio created with ${CONFIG['starting_capital']:.2f}")
    return portfolio


def save_portfolio(portfolio: dict):
    """Persist portfolio state to disk."""
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)


def portfolio_summary(portfolio: dict, live_prices: dict = None) -> str:
    """Return a human-readable summary of the current portfolio."""
    cash = portfolio["cash"]
    holdings_value = 0.0
    lines = [f"  💵 Cash: ${cash:.2f}"]

    for symbol, h in portfolio["holdings"].items():
        cost_basis = h["quantity"] * h["avg_cost"]
        if live_prices and symbol in live_prices:
            current_val = h["quantity"] * live_prices[symbol]
            pnl = current_val - cost_basis
            pnl_pct = (pnl / cost_basis) * 100 if cost_basis else 0
            holdings_value += current_val
            lines.append(
                f"  📦 {symbol}: {h['quantity']:.4f} coins | Cost ${cost_basis:.2f} | "
                f"Now ${current_val:.2f} | P&L ${pnl:+.2f} ({pnl_pct:+.1f}%)"
            )
        else:
            holdings_value += cost_basis
            lines.append(f"  📦 {symbol}: {h['quantity']:.4f} coins | Cost ${cost_basis:.2f}")

    total = cash + holdings_value
    start = CONFIG["starting_capital"]
    total_pnl = total - start
    total_pnl_pct = (total_pnl / start) * 100
    lines.append(f"  ────────────────────────────────")
    lines.append(f"  💰 Total: ${total:.2f} | P&L ${total_pnl:+.2f} ({total_pnl_pct:+.1f}%)")
    return "\n".join(lines)


# ============================================================
# MARKET DATA — CoinGecko (free, no API key required)
# ============================================================
COINGECKO = "https://api.coingecko.com/api/v3"


def get_top_coins(limit: int = 250) -> list:
    """Fetch top N coins by market cap from CoinGecko."""
    url = f"{COINGECKO}/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": min(limit, 250),
        "page": 1,
        "sparkline": False,
        "price_change_percentage": "24h,7d",
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_price_history(coin_id: str, days: int = 30) -> list:
    """Get daily closing prices for the past N days."""
    url = f"{COINGECKO}/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": days, "interval": "daily"}
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return [p[1] for p in resp.json()["prices"]]


# ============================================================
# RSI CALCULATION
# ============================================================
def calculate_rsi(prices: list, period: int = 14):
    """
    Wilder's RSI calculation.
    Returns RSI value 0–100, or None if not enough data.
    """
    if len(prices) < period + 1:
        return None

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [abs(d) if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


# ============================================================
# SCANNER — Find RSI Oversold Opportunities
# ============================================================
def scan_opportunities() -> list:
    """
    Scan top 250 coins, filter by quality metrics, calculate RSI,
    and return coins that are oversold (RSI < rsi_oversold threshold).
    """
    log.info("🔍 Scanning market for oversold opportunities...")
    coins = get_top_coins(250)
    opportunities = []

    for coin in coins:
        symbol = coin.get("symbol", "").lower()

        # Skip stablecoins
        if symbol in STABLECOINS:
            continue

        # Quality filters
        if coin.get("market_cap", 0) < CONFIG["min_market_cap"]:
            continue
        if coin.get("current_price", 9999) > CONFIG["max_price_usd"]:
            continue
        if coin.get("total_volume", 0) < CONFIG["min_volume_24h"]:
            continue

        # Get RSI (with rate-limit pause)
        try:
            prices = get_price_history(coin["id"], days=30)
            rsi = calculate_rsi(prices, CONFIG["rsi_period"])
            time.sleep(0.6)  # Respect CoinGecko free tier rate limit
        except Exception as e:
            log.debug(f"Skipping {coin['id']}: {e}")
            continue

        if rsi is None:
            continue

        if rsi < CONFIG["rsi_oversold"]:
            opportunities.append({
                "id":         coin["id"],
                "symbol":     coin["symbol"].upper(),
                "name":       coin["name"],
                "price":      coin["current_price"],
                "rsi":        rsi,
                "change_24h": round(coin.get("price_change_percentage_24h") or 0, 2),
                "change_7d":  round(coin.get("price_change_percentage_7d_in_currency") or 0, 2),
                "market_cap": coin["market_cap"],
                "volume_24h": coin["total_volume"],
                "score":      round(CONFIG["rsi_oversold"] - rsi, 2),  # Higher = more oversold
            })

    opportunities.sort(key=lambda x: x["score"], reverse=True)
    log.info(f"✅ Found {len(opportunities)} oversold coin(s).")
    return opportunities


# ============================================================
# PAPER TRADING — Execute Simulated Trades
# ============================================================
def paper_buy(portfolio: dict, coin: dict, amount_usd: float) -> bool:
    """Simulate buying a coin with USD from the paper portfolio."""
    symbol = coin["symbol"]
    price = coin["price"]

    if portfolio["cash"] < amount_usd:
        log.warning(f"⚠️  Not enough cash to buy {symbol}. Have ${portfolio['cash']:.2f}, need ${amount_usd:.2f}")
        return False

    quantity = amount_usd / price
    portfolio["cash"] = round(portfolio["cash"] - amount_usd, 6)

    if symbol in portfolio["holdings"]:
        existing = portfolio["holdings"][symbol]
        total_qty = existing["quantity"] + quantity
        total_cost = (existing["quantity"] * existing["avg_cost"]) + amount_usd
        portfolio["holdings"][symbol]["quantity"] = total_qty
        portfolio["holdings"][symbol]["avg_cost"] = round(total_cost / total_qty, 8)
    else:
        portfolio["holdings"][symbol] = {
            "id": coin["id"],
            "quantity": quantity,
            "avg_cost": price,
        }

    trade_record = {
        "time":     datetime.now().isoformat(),
        "action":   "BUY",
        "symbol":   symbol,
        "price":    price,
        "quantity": quantity,
        "usd":      amount_usd,
        "rsi":      coin.get("rsi"),
    }
    portfolio["trades"].append(trade_record)
    log.info(f"  ✅ BUY  {quantity:.4f} {symbol} @ ${price:.6f} = ${amount_usd:.2f} | RSI: {coin.get('rsi')}")
    return True


def paper_sell(portfolio: dict, symbol: str, price: float, rsi: float = None, reason: str = "") -> bool:
    """Simulate selling all of a coin position."""
    if symbol not in portfolio["holdings"]:
        return False

    holding = portfolio["holdings"][symbol]
    quantity = holding["quantity"]
    proceeds = quantity * price
    cost = quantity * holding["avg_cost"]
    pnl = proceeds - cost
    pnl_pct = (pnl / cost) * 100 if cost else 0

    portfolio["cash"] = round(portfolio["cash"] + proceeds, 6)
    del portfolio["holdings"][symbol]

    trade_record = {
        "time":     datetime.now().isoformat(),
        "action":   "SELL",
        "symbol":   symbol,
        "price":    price,
        "quantity": quantity,
        "usd":      round(proceeds, 4),
        "pnl":      round(pnl, 4),
        "pnl_pct":  round(pnl_pct, 2),
        "rsi":      rsi,
        "reason":   reason,
    }
    portfolio["trades"].append(trade_record)
    emoji = "📈" if pnl >= 0 else "📉"
    log.info(
        f"  {emoji} SELL {quantity:.4f} {symbol} @ ${price:.6f} = ${proceeds:.2f} | "
        f"P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%) | Reason: {reason}"
    )
    return True


# ============================================================
# LIVE TRADING — Coinbase Advanced Trade API
# ============================================================
def coinbase_buy(symbol: str, amount_usd: float) -> bool:
    """Place a real market buy order on Coinbase."""
    if not CONFIG["coinbase_api_key"] or not CONFIG["coinbase_api_secret"]:
        log.error("❌ Coinbase API credentials not set. Switch to paper trading or set COINBASE_API_KEY / COINBASE_API_SECRET.")
        return False
    try:
        from coinbase.rest import RESTClient  # coinbase-advanced-py
        client = RESTClient(
            api_key=CONFIG["coinbase_api_key"],
            api_secret=CONFIG["coinbase_api_secret"],
        )
        product_id = f"{symbol}-USD"
        import uuid
        order = client.market_order_buy(
            client_order_id=str(uuid.uuid4()),
            product_id=product_id,
            quote_size=str(round(amount_usd, 2)),
        )
        log.info(f"  ✅ LIVE BUY {symbol}: ${amount_usd:.2f} | Order: {order}")
        return True
    except Exception as e:
        log.error(f"  ❌ Coinbase buy failed for {symbol}: {e}")
        return False


def coinbase_sell(symbol: str, quantity: float) -> bool:
    """Place a real market sell order on Coinbase."""
    if not CONFIG["coinbase_api_key"] or not CONFIG["coinbase_api_secret"]:
        log.error("❌ Coinbase API credentials not set.")
        return False
    try:
        from coinbase.rest import RESTClient
        client = RESTClient(
            api_key=CONFIG["coinbase_api_key"],
            api_secret=CONFIG["coinbase_api_secret"],
        )
        product_id = f"{symbol}-USD"
        import uuid
        order = client.market_order_sell(
            client_order_id=str(uuid.uuid4()),
            product_id=product_id,
            base_size=str(round(quantity, 8)),
        )
        log.info(f"  ✅ LIVE SELL {symbol}: {quantity:.6f} | Order: {order}")
        return True
    except Exception as e:
        log.error(f"  ❌ Coinbase sell failed for {symbol}: {e}")
        return False


# ============================================================
# MAIN TRADING CYCLE
# ============================================================
def run_cycle():
    """
    One full trading cycle:
      1. Load portfolio
      2. Check existing holdings for SELL signals
      3. Scan for new BUY opportunities
      4. Execute trades (paper or live)
      5. Save portfolio and print summary
    """
    log.info("=" * 55)
    log.info(f"🚀 TRADING CYCLE  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"   Mode: {'📝 PAPER TRADING' if CONFIG['paper_trading'] else '💵 LIVE TRADING'}")
    log.info("=" * 55)

    portfolio = load_portfolio()

    # ── Step 1: Evaluate existing holdings ──────────────────
    live_prices = {}
    if portfolio["holdings"]:
        log.info("📊 Checking existing holdings...")
        all_coins_data = get_top_coins(250)
        coin_lookup = {c["symbol"].upper(): c for c in all_coins_data}

        for symbol in list(portfolio["holdings"].keys()):
            coin_data = coin_lookup.get(symbol)
            if not coin_data:
                log.warning(f"  ⚠️  Could not find market data for {symbol}")
                continue

            current_price = coin_data["current_price"]
            live_prices[symbol] = current_price
            holding = portfolio["holdings"][symbol]
            avg_cost = holding["avg_cost"]
            pnl_pct = ((current_price - avg_cost) / avg_cost) * 100

            # Get RSI for sell decision
            try:
                prices = get_price_history(holding["id"], days=30)
                rsi = calculate_rsi(prices, CONFIG["rsi_period"])
                time.sleep(0.6)
            except Exception:
                rsi = None

            log.info(f"  {symbol}: ${current_price:.6f} | P&L {pnl_pct:+.1f}% | RSI: {rsi}")

            sell_reason = None
            if rsi and rsi > CONFIG["rsi_overbought"]:
                sell_reason = f"RSI overbought ({rsi})"
            elif pnl_pct >= CONFIG["take_profit_pct"]:
                sell_reason = f"Take profit ({pnl_pct:.1f}%)"
            elif pnl_pct <= CONFIG["stop_loss_pct"]:
                sell_reason = f"Stop loss ({pnl_pct:.1f}%)"

            if sell_reason:
                if CONFIG["paper_trading"]:
                    paper_sell(portfolio, symbol, current_price, rsi=rsi, reason=sell_reason)
                else:
                    quantity = holding["quantity"]
                    if coinbase_sell(symbol, quantity):
                        paper_sell(portfolio, symbol, current_price, rsi=rsi, reason=sell_reason)

    # ── Step 2: Scan for new opportunities ──────────────────
    num_holdings = len(portfolio["holdings"])
    slots_available = CONFIG["max_positions"] - num_holdings

    if slots_available > 0:
        opportunities = scan_opportunities()

        if opportunities:
            log.info(f"\n🎯 Top Opportunities:")
            for opp in opportunities[:5]:
                log.info(
                    f"  {opp['symbol']:<8} RSI={opp['rsi']:<6} "
                    f"Price=${opp['price']:.6f}  24h={opp['change_24h']:+.1f}%  "
                    f"7d={opp['change_7d']:+.1f}%  Score={opp['score']}"
                )

            log.info(f"\n💸 Executing buys (up to {slots_available} position slots)...")
            bought = 0
            for opp in opportunities:
                if bought >= slots_available:
                    break
                if opp["symbol"] in portfolio["holdings"]:
                    continue  # Already holding this coin

                amount_usd = round(
                    min(
                        portfolio["cash"] * CONFIG["position_size_pct"],
                        portfolio["cash"] * 0.95,
                    ),
                    2,
                )

                if amount_usd < CONFIG["min_trade_usd"]:
                    log.info(f"  💸 Insufficient cash for minimum trade. Stopping buys.")
                    break

                if CONFIG["paper_trading"]:
                    success = paper_buy(portfolio, opp, amount_usd)
                else:
                    success = coinbase_buy(opp["symbol"], amount_usd)
                    if success:
                        paper_buy(portfolio, opp, amount_usd)  # Track in portfolio

                if success:
                    bought += 1
        else:
            log.info("😴 No oversold opportunities found this cycle. Holding cash.")
    else:
        log.info(f"📦 Portfolio full ({CONFIG['max_positions']} positions). Skipping new buys.")

    # ── Step 3: Save & Summarize ─────────────────────────────
    save_portfolio(portfolio)
    log.info("\n📊 PORTFOLIO SUMMARY:")
    log.info(portfolio_summary(portfolio, live_prices))
    log.info("=" * 55 + "\n")


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    run_cycle()
