"""Phase 4 tests: backtest engine, stats, and report generation (no network).

Signal generation itself is unit-tested in `test_signals.py`; here the
engine's `generate_buy_signal`/`should_exit_long` calls are monkeypatched
so trade simulation (SL/TP hit detection, early signal exits, equity
tracking) can be tested deterministically without needing synthetic OHLC
that happens to satisfy the full confluence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.backtesting import backtest_engine
from src.backtesting.backtest_engine import run_backtest
from src.backtesting.backtest_reports import export_trades_csv, generate_html_report
from src.backtesting.backtest_stats import calculate_stats, validate_strategy, BacktestResult as _BR
from src.data.models import Candle, ExitSignal, Signal, SignalSide


def _flat_candles(n: int, symbol="TEST", tf="1h", price=100.0, spread=1.0) -> list[Candle]:
    base = datetime.now(timezone.utc)
    return [
        Candle(
            symbol=symbol, timeframe=tf, timestamp=base + timedelta(hours=i),
            open=price, high=price + spread, low=price - spread, close=price,
            volume=1000.0,
        )
        for i in range(n)
    ]


def _fire_once(value):
    """Returns a fake `generate_buy_signal`/`should_exit_long` that fires
    (returns `value`) exactly once (first call), then always returns None."""
    state = {"fired": False}

    def _fn(snapshot):
        if state["fired"]:
            return None
        state["fired"] = True
        return value

    return _fn


def _never(snapshot):
    return None


# -- run_backtest ---------------------------------------------------------

def test_run_backtest_take_profit_hit(monkeypatch):
    candles = _flat_candles(15)
    candles[10].high = 111.0  # breaches TP before anything breaches SL

    signal = Signal(
        symbol="TEST", timeframe="1h", side=SignalSide.BUY,
        entry=100.0, sl=95.0, tp=110.0, risk_reward=2.0, confidence=1.0,
    )
    monkeypatch.setattr(backtest_engine, "generate_buy_signal", _fire_once(signal))
    monkeypatch.setattr(backtest_engine, "should_exit_long", _never)

    result = run_backtest(candles, min_history=5)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "tp"
    assert trade.exit_price == pytest.approx(110.0)
    assert trade.pnl_pct == pytest.approx((110.0 - 100.0) / 100.0)
    assert result.equity_curve[-1][1] == pytest.approx(1 + trade.pnl_pct)


def test_run_backtest_stop_loss_hit(monkeypatch):
    candles = _flat_candles(15)
    candles[8].low = 90.0  # breaches SL

    signal = Signal(
        symbol="TEST", timeframe="1h", side=SignalSide.BUY,
        entry=100.0, sl=95.0, tp=110.0, risk_reward=2.0, confidence=1.0,
    )
    monkeypatch.setattr(backtest_engine, "generate_buy_signal", _fire_once(signal))
    monkeypatch.setattr(backtest_engine, "should_exit_long", _never)

    result = run_backtest(candles, min_history=5)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "sl"
    assert trade.exit_price == pytest.approx(95.0)
    assert trade.pnl_pct == pytest.approx((95.0 - 100.0) / 100.0)


def test_run_backtest_closes_at_end_of_data_if_never_hit(monkeypatch):
    candles = _flat_candles(15)  # spread +/-1 around 100, never reaches 95 or 110

    signal = Signal(
        symbol="TEST", timeframe="1h", side=SignalSide.BUY,
        entry=100.0, sl=95.0, tp=110.0, risk_reward=2.0, confidence=1.0,
    )
    monkeypatch.setattr(backtest_engine, "generate_buy_signal", _fire_once(signal))
    monkeypatch.setattr(backtest_engine, "should_exit_long", _never)

    result = run_backtest(candles, min_history=5)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "end_of_data"
    assert trade.exit_index == len(candles) - 1


def test_run_backtest_signal_exit_closes_position_early(monkeypatch):
    # Flat candles never breach SL(95)/TP(110) — the only way this position
    # closes is via `should_exit_long` firing.
    candles = _flat_candles(15)

    signal = Signal(
        symbol="TEST", timeframe="1h", side=SignalSide.BUY,
        entry=100.0, sl=95.0, tp=110.0, risk_reward=2.0, confidence=1.0,
    )
    exit_signal = ExitSignal(reasons=["rsi>70"], confidence=1.0)
    monkeypatch.setattr(backtest_engine, "generate_buy_signal", _fire_once(signal))
    monkeypatch.setattr(backtest_engine, "should_exit_long", _fire_once(exit_signal))

    result = run_backtest(candles, min_history=5)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "signal_exit"
    assert trade.exit_price == pytest.approx(100.0)  # flat candles, closes at entry
    assert trade.pnl_pct == pytest.approx(0.0)


def test_run_backtest_sl_tp_take_priority_over_signal_exit(monkeypatch):
    # Both a TP breach and a should_exit_long firing are available on the
    # same bar — the precise SL/TP level should win (see run_backtest's
    # docstring: "a precise price level takes priority").
    candles = _flat_candles(15)
    candles[6].high = 111.0  # TP breach on the very first bar after entry

    signal = Signal(
        symbol="TEST", timeframe="1h", side=SignalSide.BUY,
        entry=100.0, sl=95.0, tp=110.0, risk_reward=2.0, confidence=1.0,
    )
    exit_signal = ExitSignal(reasons=["rsi>70"], confidence=1.0)
    monkeypatch.setattr(backtest_engine, "generate_buy_signal", _fire_once(signal))
    # Always-firing exit signal: still should lose to the TP hit below.
    monkeypatch.setattr(backtest_engine, "should_exit_long", lambda snapshot: exit_signal)

    result = run_backtest(candles, min_history=5)

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "tp"


def test_run_backtest_no_signals_produces_no_trades(monkeypatch):
    candles = _flat_candles(15)
    monkeypatch.setattr(backtest_engine, "generate_buy_signal", _never)
    monkeypatch.setattr(backtest_engine, "should_exit_long", _never)

    result = run_backtest(candles, min_history=5)
    assert result.trades == []
    assert result.equity_curve == []


def test_run_backtest_empty_input():
    result = run_backtest([], min_history=0)
    assert result.trades == []
    assert result.symbol == ""


# -- backtest_stats ---------------------------------------------------------

def _stub_trade(pnl_pct: float):
    from types import SimpleNamespace
    return SimpleNamespace(pnl_pct=pnl_pct)


def test_calculate_stats_win_rate_and_profit_factor():
    # 2 wins (+10%, +20%), 1 loss (-10%)
    result = _BR(symbol="TEST", timeframe="1h", trades=[
        _stub_trade(0.10), _stub_trade(0.20), _stub_trade(-0.10),
    ])
    stats = calculate_stats(result)
    assert stats.total_trades == 3
    assert stats.win_rate == pytest.approx(2 / 3)
    assert stats.profit_factor == pytest.approx(0.30 / 0.10)  # 3.0
    assert stats.total_return_pct == pytest.approx(1.10 * 1.20 * 0.90 - 1.0)


def test_calculate_stats_no_losses_gives_infinite_profit_factor():
    result = _BR(symbol="TEST", timeframe="1h", trades=[_stub_trade(0.05)])
    stats = calculate_stats(result)
    assert stats.profit_factor == float("inf")


def test_calculate_stats_empty_result():
    result = _BR(symbol="TEST", timeframe="1h", trades=[])
    stats = calculate_stats(result)
    assert stats.total_trades == 0
    assert stats.win_rate == 0.0
    assert stats.profit_factor == 0.0


def test_calculate_stats_max_drawdown():
    # equity path: 1.0 -> 1.20 (win) -> 0.90 (big loss) -> drawdown from peak 1.20
    result = _BR(symbol="TEST", timeframe="1h", trades=[
        _stub_trade(0.20), _stub_trade(-0.25),
    ])
    stats = calculate_stats(result)
    peak = 1.20
    trough = 1.20 * 0.75
    expected_dd = (peak - trough) / peak
    assert stats.max_drawdown_pct == pytest.approx(expected_dd)


# -- validate_strategy ---------------------------------------------------------

def test_validate_strategy_passes_when_all_thresholds_met():
    result = _BR(symbol="TEST", timeframe="1h", trades=[
        _stub_trade(0.10), _stub_trade(0.10), _stub_trade(-0.05),
    ])
    stats = calculate_stats(result)
    validation = validate_strategy(stats, min_win_rate=0.5, min_profit_factor=1.5, max_drawdown_pct=0.20)
    assert validation.passed
    assert all(validation.checks.values())


def test_validate_strategy_fails_low_win_rate():
    result = _BR(symbol="TEST", timeframe="1h", trades=[
        _stub_trade(-0.05), _stub_trade(-0.05), _stub_trade(0.10),
    ])
    stats = calculate_stats(result)
    validation = validate_strategy(stats, min_win_rate=0.5)
    assert not validation.passed


# -- backtest_reports ---------------------------------------------------------

def test_export_trades_csv(tmp_path, monkeypatch):
    candles = _flat_candles(15)
    candles[10].high = 111.0
    signal = Signal(symbol="TEST", timeframe="1h", side=SignalSide.BUY,
                     entry=100.0, sl=95.0, tp=110.0, risk_reward=2.0, confidence=1.0)
    monkeypatch.setattr(backtest_engine, "generate_buy_signal", _fire_once(signal))
    monkeypatch.setattr(backtest_engine, "should_exit_long", _never)
    result = run_backtest(candles, min_history=5)

    out = tmp_path / "trades.csv"
    export_trades_csv(result, out)

    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "symbol" in content.splitlines()[0]
    assert "TEST" in content
    assert "tp" in content


def test_generate_html_report(tmp_path, monkeypatch):
    candles = _flat_candles(15)
    candles[10].high = 111.0
    signal = Signal(symbol="TEST", timeframe="1h", side=SignalSide.BUY,
                     entry=100.0, sl=95.0, tp=110.0, risk_reward=2.0, confidence=1.0)
    monkeypatch.setattr(backtest_engine, "generate_buy_signal", _fire_once(signal))
    monkeypatch.setattr(backtest_engine, "should_exit_long", _never)
    result = run_backtest(candles, min_history=5)
    stats = calculate_stats(result)
    validation = validate_strategy(stats)

    out = tmp_path / "report.html"
    generate_html_report(result, stats, validation, out)

    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "TEST" in html
    assert ("PASSED" in html) or ("FAILED" in html)


def test_generate_html_report_handles_zero_trades(tmp_path):
    result = _BR(symbol="TEST", timeframe="1h", trades=[])
    stats = calculate_stats(result)
    validation = validate_strategy(stats)

    out = tmp_path / "empty_report.html"
    generate_html_report(result, stats, validation, out)

    assert out.exists()
    assert "Not enough trades" in out.read_text(encoding="utf-8")
