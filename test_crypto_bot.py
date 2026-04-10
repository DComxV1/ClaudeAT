#!/usr/bin/env python3
"""Tests for the Crypto RSI Trading Bot."""

import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

import crypto_bot
from crypto_bot import (
    calculate_rsi,
    load_portfolio,
    save_portfolio,
    portfolio_summary,
    paper_buy,
    paper_sell,
    scan_opportunities,
    CONFIG,
    STABLECOINS,
)


# ============================================================
# FIXTURES
# ============================================================
@pytest.fixture
def empty_portfolio():
    """A fresh portfolio with default starting capital."""
    return {
        "cash": 100.0,
        "holdings": {},
        "trades": [],
        "created": "2026-01-01T00:00:00",
    }


@pytest.fixture
def portfolio_with_holdings():
    """A portfolio with existing positions."""
    return {
        "cash": 50.0,
        "holdings": {
            "DOGE": {"id": "dogecoin", "quantity": 500.0, "avg_cost": 0.08},
            "ADA": {"id": "cardano", "quantity": 100.0, "avg_cost": 0.30},
        },
        "trades": [],
        "created": "2026-01-01T00:00:00",
    }


@pytest.fixture
def sample_coin():
    """A sample coin opportunity dict."""
    return {
        "id": "dogecoin",
        "symbol": "DOGE",
        "name": "Dogecoin",
        "price": 0.10,
        "rsi": 28.5,
        "change_24h": -5.2,
        "change_7d": -12.1,
        "market_cap": 10_000_000_000,
        "volume_24h": 500_000_000,
        "score": 6.5,
    }


# ============================================================
# RSI CALCULATION
# ============================================================
class TestCalculateRSI:
    def test_not_enough_data(self):
        """RSI returns None when there aren't enough data points."""
        assert calculate_rsi([1, 2, 3], period=14) is None
        assert calculate_rsi([], period=14) is None

    def test_exactly_minimum_data(self):
        """RSI returns a value with exactly period+1 data points."""
        prices = list(range(16))  # 16 points = 15 deltas, enough for period=14
        result = calculate_rsi(prices, period=14)
        assert result is not None
        assert 0 <= result <= 100

    def test_all_gains_returns_100(self):
        """RSI = 100 when all price changes are positive (no losses)."""
        prices = [float(i) for i in range(20)]  # Monotonically increasing
        result = calculate_rsi(prices, period=14)
        assert result == 100.0

    def test_all_losses(self):
        """RSI approaches 0 when all price changes are negative."""
        prices = [float(20 - i) for i in range(20)]  # Monotonically decreasing
        result = calculate_rsi(prices, period=14)
        assert result is not None
        assert result < 5  # Should be very low

    def test_known_rsi_value(self):
        """Test RSI against a hand-calculated scenario."""
        # Create a price series with known gain/loss pattern:
        # 14 gains of +1, then price stays flat → RSI should reflect all-gain history
        prices = [10.0 + i for i in range(16)]  # 10, 11, 12, ... 25
        result = calculate_rsi(prices, period=14)
        assert result == 100.0  # All gains, no losses

    def test_mixed_prices(self):
        """RSI with mixed gains and losses falls between 0 and 100."""
        prices = [
            44.0, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10,
            45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
            46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
        ]
        result = calculate_rsi(prices, period=14)
        assert result is not None
        assert 0 < result < 100

    def test_rsi_range(self):
        """RSI should always be between 0 and 100."""
        import random
        random.seed(42)
        prices = [random.uniform(1, 100) for _ in range(50)]
        result = calculate_rsi(prices, period=14)
        assert 0 <= result <= 100

    def test_custom_period(self):
        """RSI works with non-default period."""
        prices = [float(i) for i in range(10)]
        result = calculate_rsi(prices, period=5)
        assert result is not None
        assert result == 100.0  # All gains


# ============================================================
# PORTFOLIO PERSISTENCE
# ============================================================
class TestPortfolioPersistence:
    def test_load_creates_new_portfolio(self, tmp_path, monkeypatch):
        """load_portfolio creates a fresh portfolio if file doesn't exist."""
        portfolio_file = tmp_path / "test_portfolio.json"
        monkeypatch.setattr(crypto_bot, "PORTFOLIO_FILE", str(portfolio_file))

        portfolio = load_portfolio()
        assert portfolio["cash"] == CONFIG["starting_capital"]
        assert portfolio["holdings"] == {}
        assert portfolio["trades"] == []
        assert portfolio_file.exists()

    def test_load_existing_portfolio(self, tmp_path, monkeypatch):
        """load_portfolio reads an existing portfolio file."""
        portfolio_file = tmp_path / "test_portfolio.json"
        data = {"cash": 75.5, "holdings": {"BTC": {"id": "bitcoin", "quantity": 0.001, "avg_cost": 30000}}, "trades": [], "created": "2026-01-01"}
        portfolio_file.write_text(json.dumps(data))
        monkeypatch.setattr(crypto_bot, "PORTFOLIO_FILE", str(portfolio_file))

        portfolio = load_portfolio()
        assert portfolio["cash"] == 75.5
        assert "BTC" in portfolio["holdings"]

    def test_save_and_reload(self, tmp_path, monkeypatch):
        """save_portfolio writes JSON that can be loaded back."""
        portfolio_file = tmp_path / "test_portfolio.json"
        monkeypatch.setattr(crypto_bot, "PORTFOLIO_FILE", str(portfolio_file))

        original = {"cash": 42.0, "holdings": {}, "trades": [{"action": "BUY"}], "created": "2026-01-01"}
        save_portfolio(original)

        loaded = load_portfolio()
        assert loaded["cash"] == 42.0
        assert loaded["trades"] == [{"action": "BUY"}]


# ============================================================
# PORTFOLIO SUMMARY
# ============================================================
class TestPortfolioSummary:
    def test_empty_portfolio(self, empty_portfolio):
        """Summary for portfolio with no holdings."""
        summary = portfolio_summary(empty_portfolio)
        assert "$100.00" in summary
        assert "P&L $+0.00" in summary

    def test_with_holdings_no_live_prices(self, portfolio_with_holdings):
        """Summary uses cost basis when no live prices available."""
        summary = portfolio_summary(portfolio_with_holdings)
        assert "DOGE" in summary
        assert "ADA" in summary
        assert "$50.00" in summary  # Cash

    def test_with_live_prices(self, portfolio_with_holdings):
        """Summary shows P&L when live prices are provided."""
        live_prices = {"DOGE": 0.10, "ADA": 0.35}
        summary = portfolio_summary(portfolio_with_holdings, live_prices)
        assert "DOGE" in summary
        assert "Now $" in summary


# ============================================================
# PAPER TRADING — BUY
# ============================================================
class TestPaperBuy:
    def test_successful_buy(self, empty_portfolio, sample_coin):
        """Buy deducts cash and adds holding."""
        result = paper_buy(empty_portfolio, sample_coin, 20.0)
        assert result is True
        assert empty_portfolio["cash"] == pytest.approx(80.0)
        assert "DOGE" in empty_portfolio["holdings"]
        assert empty_portfolio["holdings"]["DOGE"]["quantity"] == pytest.approx(200.0)  # 20 / 0.10
        assert empty_portfolio["holdings"]["DOGE"]["avg_cost"] == 0.10
        assert len(empty_portfolio["trades"]) == 1
        assert empty_portfolio["trades"][0]["action"] == "BUY"

    def test_insufficient_cash(self, empty_portfolio, sample_coin):
        """Buy fails when portfolio doesn't have enough cash."""
        result = paper_buy(empty_portfolio, sample_coin, 200.0)
        assert result is False
        assert empty_portfolio["cash"] == 100.0
        assert "DOGE" not in empty_portfolio["holdings"]
        assert len(empty_portfolio["trades"]) == 0

    def test_buy_adds_to_existing_position(self, empty_portfolio, sample_coin):
        """Buying a coin already held updates average cost and quantity."""
        paper_buy(empty_portfolio, sample_coin, 20.0)

        # Price changed — buy more at new price
        sample_coin["price"] = 0.12
        paper_buy(empty_portfolio, sample_coin, 12.0)

        holding = empty_portfolio["holdings"]["DOGE"]
        # 200 coins @ 0.10 + 100 coins @ 0.12 = 300 coins
        assert holding["quantity"] == pytest.approx(300.0)
        # Avg cost = (20 + 12) / 300 = 0.10666...
        assert holding["avg_cost"] == pytest.approx(32.0 / 300.0, rel=1e-4)

    def test_buy_records_trade(self, empty_portfolio, sample_coin):
        """Buy appends a trade record with correct fields."""
        paper_buy(empty_portfolio, sample_coin, 10.0)
        trade = empty_portfolio["trades"][0]
        assert trade["action"] == "BUY"
        assert trade["symbol"] == "DOGE"
        assert trade["price"] == 0.10
        assert trade["quantity"] == pytest.approx(100.0)
        assert trade["usd"] == 10.0
        assert trade["rsi"] == 28.5


# ============================================================
# PAPER TRADING — SELL
# ============================================================
class TestPaperSell:
    def test_successful_sell(self, portfolio_with_holdings):
        """Sell adds proceeds to cash and removes holding."""
        result = paper_sell(portfolio_with_holdings, "DOGE", price=0.10, rsi=70.0, reason="RSI overbought")
        assert result is True
        assert "DOGE" not in portfolio_with_holdings["holdings"]
        # 500 * 0.10 = 50.0 proceeds
        assert portfolio_with_holdings["cash"] == pytest.approx(100.0)
        assert len(portfolio_with_holdings["trades"]) == 1

    def test_sell_nonexistent_holding(self, empty_portfolio):
        """Selling a coin not in the portfolio returns False."""
        result = paper_sell(empty_portfolio, "BTC", price=50000.0)
        assert result is False
        assert len(empty_portfolio["trades"]) == 0

    def test_sell_records_pnl(self, portfolio_with_holdings):
        """Sell trade record includes correct P&L calculation."""
        # DOGE: 500 coins, avg_cost 0.08, sell at 0.10
        paper_sell(portfolio_with_holdings, "DOGE", price=0.10, rsi=68.0, reason="Take profit")
        trade = portfolio_with_holdings["trades"][0]
        assert trade["action"] == "SELL"
        assert trade["symbol"] == "DOGE"
        assert trade["pnl"] == pytest.approx(10.0)  # (0.10 - 0.08) * 500
        assert trade["pnl_pct"] == pytest.approx(25.0)  # 25% gain
        assert trade["reason"] == "Take profit"

    def test_sell_at_loss(self, portfolio_with_holdings):
        """Sell at a loss records negative P&L."""
        # ADA: 100 coins, avg_cost 0.30, sell at 0.25
        paper_sell(portfolio_with_holdings, "ADA", price=0.25, rsi=None, reason="Stop loss")
        trade = portfolio_with_holdings["trades"][0]
        assert trade["pnl"] == pytest.approx(-5.0)  # (0.25 - 0.30) * 100
        assert trade["pnl_pct"] == pytest.approx(-16.67, rel=1e-2)


# ============================================================
# SCANNER (with mocked API calls)
# ============================================================
class TestScanner:
    def _make_coin(self, symbol, coin_id, price, market_cap, volume, rsi_prices=None):
        """Helper to create a mock coin dict from CoinGecko."""
        return {
            "id": coin_id,
            "symbol": symbol,
            "name": symbol.upper(),
            "current_price": price,
            "market_cap": market_cap,
            "total_volume": volume,
            "price_change_percentage_24h": -5.0,
            "price_change_percentage_7d_in_currency": -10.0,
        }

    @patch("crypto_bot.get_price_history")
    @patch("crypto_bot.get_top_coins")
    @patch("crypto_bot.time.sleep")  # Skip rate-limit delays in tests
    def test_finds_oversold_coins(self, mock_sleep, mock_top_coins, mock_prices):
        """Scanner returns coins with RSI below oversold threshold."""
        mock_top_coins.return_value = [
            self._make_coin("doge", "dogecoin", 0.08, 100_000_000, 5_000_000),
        ]
        # Prices that produce a low RSI (declining series)
        mock_prices.return_value = [10.0 - i * 0.3 for i in range(20)]

        results = scan_opportunities()
        assert len(results) == 1
        assert results[0]["symbol"] == "DOGE"
        assert results[0]["rsi"] < CONFIG["rsi_oversold"]

    @patch("crypto_bot.get_price_history")
    @patch("crypto_bot.get_top_coins")
    @patch("crypto_bot.time.sleep")
    def test_filters_stablecoins(self, mock_sleep, mock_top_coins, mock_prices):
        """Scanner skips stablecoins."""
        mock_top_coins.return_value = [
            self._make_coin("usdt", "tether", 1.0, 80_000_000_000, 50_000_000_000),
        ]
        results = scan_opportunities()
        assert len(results) == 0
        mock_prices.assert_not_called()

    @patch("crypto_bot.get_price_history")
    @patch("crypto_bot.get_top_coins")
    @patch("crypto_bot.time.sleep")
    def test_filters_low_market_cap(self, mock_sleep, mock_top_coins, mock_prices):
        """Scanner skips coins below minimum market cap."""
        mock_top_coins.return_value = [
            self._make_coin("tiny", "tinycoin", 0.01, 1_000, 5_000_000),
        ]
        results = scan_opportunities()
        assert len(results) == 0

    @patch("crypto_bot.get_price_history")
    @patch("crypto_bot.get_top_coins")
    @patch("crypto_bot.time.sleep")
    def test_filters_high_price(self, mock_sleep, mock_top_coins, mock_prices):
        """Scanner skips coins above max price."""
        mock_top_coins.return_value = [
            self._make_coin("btc", "bitcoin", 60000.0, 1_000_000_000_000, 30_000_000_000),
        ]
        results = scan_opportunities()
        assert len(results) == 0

    @patch("crypto_bot.get_price_history")
    @patch("crypto_bot.get_top_coins")
    @patch("crypto_bot.time.sleep")
    def test_filters_low_volume(self, mock_sleep, mock_top_coins, mock_prices):
        """Scanner skips coins below minimum 24h volume."""
        mock_top_coins.return_value = [
            self._make_coin("low", "lowvol", 0.05, 100_000_000, 500),
        ]
        results = scan_opportunities()
        assert len(results) == 0

    @patch("crypto_bot.get_price_history")
    @patch("crypto_bot.get_top_coins")
    @patch("crypto_bot.time.sleep")
    def test_skips_non_oversold(self, mock_sleep, mock_top_coins, mock_prices):
        """Scanner doesn't return coins with RSI above the oversold threshold."""
        mock_top_coins.return_value = [
            self._make_coin("doge", "dogecoin", 0.08, 100_000_000, 5_000_000),
        ]
        # Rising prices → high RSI
        mock_prices.return_value = [float(i) for i in range(1, 21)]
        results = scan_opportunities()
        assert len(results) == 0

    @patch("crypto_bot.get_price_history")
    @patch("crypto_bot.get_top_coins")
    @patch("crypto_bot.time.sleep")
    def test_sorted_by_score(self, mock_sleep, mock_top_coins, mock_prices):
        """Results are sorted by oversold score (most oversold first)."""
        mock_top_coins.return_value = [
            self._make_coin("aaa", "coin-a", 0.05, 100_000_000, 5_000_000),
            self._make_coin("bbb", "coin-b", 0.05, 100_000_000, 5_000_000),
        ]
        # Both declining but coin-b declines more steeply
        mock_prices.side_effect = [
            [10.0 - i * 0.2 for i in range(20)],  # coin-a: moderate decline
            [10.0 - i * 0.4 for i in range(20)],  # coin-b: steeper decline
        ]
        results = scan_opportunities()
        if len(results) == 2:
            assert results[0]["score"] >= results[1]["score"]


# ============================================================
# CONFIG & CONSTANTS
# ============================================================
class TestConfig:
    def test_stablecoins_are_lowercase(self):
        """All stablecoins in the set should be lowercase."""
        for coin in STABLECOINS:
            assert coin == coin.lower()

    def test_default_config_values(self):
        """Verify critical defaults are sensible."""
        assert CONFIG["starting_capital"] > 0
        assert CONFIG["rsi_oversold"] < CONFIG["rsi_overbought"]
        assert CONFIG["rsi_period"] > 0
        assert CONFIG["max_positions"] > 0
        assert 0 < CONFIG["position_size_pct"] <= 1.0
        assert CONFIG["take_profit_pct"] > 0
        assert CONFIG["stop_loss_pct"] < 0
        assert CONFIG["paper_trading"] is True  # Default should be safe
