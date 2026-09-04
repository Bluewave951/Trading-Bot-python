"""Phase 3 tests: signal logic with synthetic indicator data (no network)."""
from __future__ import annotations

import pytest

from src.data.models import IndicatorSnapshot, SignalSide, SupportResistanceLevels
from src.signals.entry_signals import (
    _macd_turning_down,
    _macd_turning_up,
    generate_buy_signal,
    should_exit_long,
)
from src.signals.exit_signals import calculate_exit_levels
from src.signals.risk_manager import calculate_position_size, meets_risk_reward_threshold
from src.signals.signal_aggregator import aggregate_signals


def _sr(
    symbol="AAPL",
    tf="1h",
    support: list[float] | None = None,
    resistance: list[float] | None = None,
) -> SupportResistanceLevels:
    return SupportResistanceLevels(
        symbol=symbol,
        timeframe=tf,
        support_levels=support or [],
        resistance_levels=resistance or [],
    )


def _snapshot(
    symbol="AAPL",
    tf="1h",
    close=100.0,
    rsi=40.0,
    ema_20=101.0,
    ema_50=99.0,
    ema_200=95.0,
    macd=0.5,
    macd_signal=0.2,
    macd_histogram=0.3,
    macd_histogram_prev=float("nan"),
    volume=2000.0,
    volume_avg_20=1500.0,
    sr_levels: SupportResistanceLevels | None = None,
) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        symbol=symbol,
        timeframe=tf,
        close=close,
        rsi=rsi,
        ema_20=ema_20,
        ema_50=ema_50,
        ema_200=ema_200,
        macd=macd,
        macd_signal=macd_signal,
        macd_histogram=macd_histogram,
        macd_histogram_prev=macd_histogram_prev,
        volume=volume,
        volume_avg_20=volume_avg_20,
        sr_levels=sr_levels if sr_levels is not None else _sr(symbol, tf),
    )


# -- exit_signals ---------------------------------------------------------

def test_calculate_exit_levels_buy_uses_nearest_levels():
    sr = _sr(support=[90.0, 95.0], resistance=[103.0, 110.0])
    sl, tp = calculate_exit_levels(entry=100.0, sr_levels=sr, side="buy")
    assert sl == pytest.approx(95.0)  # nearest support below entry
    assert tp == pytest.approx(103.0)  # capped at nearest resistance above


def test_calculate_exit_levels_buy_fallback_when_no_support():
    sr = _sr(support=[], resistance=[])
    sl, tp = calculate_exit_levels(entry=100.0, sr_levels=sr, side="buy")
    assert sl == pytest.approx(98.0)  # entry * (1 - default_sl_pct=0.02)
    assert tp > 100.0


def test_calculate_exit_levels_sell_uses_nearest_resistance_as_sl():
    sr = _sr(support=[90.0, 95.0], resistance=[103.0, 110.0])
    sl, tp = calculate_exit_levels(entry=100.0, sr_levels=sr, side="sell")
    assert sl == pytest.approx(103.0)  # nearest resistance above entry
    # risk=3 * ratio 1.5 = 4.5 -> tp=95.5, which is above the 95.0 support
    # cap, so the raw risk/reward target wins here (not yet capped).
    assert tp == pytest.approx(95.5)


def test_calculate_exit_levels_sell_caps_tp_at_nearest_support():
    # Wide SL (risk=10) pushes the raw 1.5x target (85.0) past the nearest
    # support (90.0), so the cap should kick in.
    sr = _sr(support=[90.0], resistance=[110.0])
    sl, tp = calculate_exit_levels(entry=100.0, sr_levels=sr, side="sell")
    assert sl == pytest.approx(110.0)
    assert tp == pytest.approx(90.0)


def test_calculate_exit_levels_rejects_bad_side():
    with pytest.raises(ValueError):
        calculate_exit_levels(entry=100.0, sr_levels=_sr(), side="hold")


# -- momentum-turning helpers ---------------------------------------------------------

def test_macd_turning_up_true_when_already_positive():
    snap = _snapshot(macd_histogram=0.1, macd_histogram_prev=0.2)  # falling but still >0
    assert _macd_turning_up(snap) is True


def test_macd_turning_up_true_when_rising_but_still_negative():
    snap = _snapshot(macd_histogram=-0.1, macd_histogram_prev=-0.3)  # rising toward zero
    assert _macd_turning_up(snap) is True


def test_macd_turning_up_false_when_falling_and_negative():
    snap = _snapshot(macd_histogram=-0.3, macd_histogram_prev=-0.1)
    assert _macd_turning_up(snap) is False


def test_macd_turning_up_false_without_prior_bar():
    snap = _snapshot(macd_histogram=-0.1, macd_histogram_prev=float("nan"))
    assert _macd_turning_up(snap) is False


def test_macd_turning_down_true_when_falling_but_still_positive():
    snap = _snapshot(macd_histogram=0.1, macd_histogram_prev=0.3)  # falling toward zero
    assert _macd_turning_down(snap) is True


def test_generate_buy_signal_fires_on_turning_momentum_before_zero_cross():
    # macd_histogram is still negative but rising -> should count as bullish
    # momentum per _macd_turning_up, even though the old `>0` check wouldn't.
    sr = _sr(support=[99.7], resistance=[105.0])
    snap = _snapshot(close=100.0, rsi=40.0, ema_20=101.0, ema_50=99.0,
                      ema_200=95.0, macd_histogram=-0.05, macd_histogram_prev=-0.2,
                      sr_levels=sr)
    signal = generate_buy_signal(snap)
    assert signal is not None


# -- entry_signals: buy ---------------------------------------------------------

def test_generate_buy_signal_fires_when_all_conditions_align():
    sr = _sr(support=[99.7], resistance=[105.0])
    snap = _snapshot(close=100.0, rsi=40.0, ema_20=101.0, ema_50=99.0,
                      ema_200=95.0, macd_histogram=0.3, volume=2000.0,
                      volume_avg_20=1500.0, sr_levels=sr)
    signal = generate_buy_signal(snap)
    assert signal is not None
    assert signal.side == SignalSide.BUY
    assert signal.entry == pytest.approx(100.0)
    assert signal.confidence == pytest.approx(1.0)
    assert signal.risk_reward > 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"rsi": 60.0},                # not < 50
        {"ema_20": 98.0, "ema_50": 99.0},  # not in uptrend
        {"macd_histogram": -0.1},     # bearish momentum
        {"close": 90.0, "ema_200": 95.0},  # below EMA200
        {"volume": 1000.0, "volume_avg_20": 1500.0},  # no buying pressure
    ],
)
def test_generate_buy_signal_none_if_any_condition_fails(overrides):
    sr = _sr(support=[99.7], resistance=[105.0])
    snap = _snapshot(close=100.0, rsi=40.0, ema_20=101.0, ema_50=99.0,
                      ema_200=95.0, macd_histogram=0.3, volume=2000.0,
                      volume_avg_20=1500.0, sr_levels=sr)
    for key, value in overrides.items():
        setattr(snap, key, value)
    assert generate_buy_signal(snap) is None


def test_generate_buy_signal_none_when_price_not_near_support():
    sr = _sr(support=[50.0], resistance=[105.0])  # support far below close
    snap = _snapshot(close=100.0, rsi=40.0, ema_20=101.0, ema_50=99.0,
                      ema_200=95.0, macd_histogram=0.3, sr_levels=sr)
    assert generate_buy_signal(snap) is None


# -- entry_signals: should_exit_long (long-only; SELL redefined as an exit trigger) ------

def test_should_exit_long_fires_at_resistance_with_confirmation():
    # All three bearish confirmations must hold (mirrors BUY's strictness).
    sr = _sr(support=[90.0], resistance=[100.5])
    snap = _snapshot(close=100.0, rsi=55.0, ema_20=98.0, ema_50=99.0,
                      ema_200=101.0, macd_histogram=-0.2, sr_levels=sr)
    exit_signal = should_exit_long(snap)
    assert exit_signal is not None
    assert "price_near_resistance" in exit_signal.reasons


def test_should_exit_long_fires_on_overbought_rsi():
    sr = _sr(support=[90.0], resistance=[])
    snap = _snapshot(close=100.0, rsi=75.0, ema_20=98.0, ema_50=99.0,
                      ema_200=101.0, macd_histogram=-0.2, sr_levels=sr)
    exit_signal = should_exit_long(snap)
    assert exit_signal is not None
    assert "rsi>70" in exit_signal.reasons


def test_should_exit_long_none_with_only_partial_confirmation():
    # 2-of-3 confirmations (close<ema200 fails) should not be enough.
    sr = _sr(support=[90.0], resistance=[100.5])
    snap = _snapshot(close=100.0, rsi=55.0, ema_20=98.0, ema_50=99.0,
                      ema_200=97.0, macd_histogram=-0.2, sr_levels=sr)
    assert should_exit_long(snap) is None


def test_should_exit_long_none_without_trigger():
    sr = _sr(support=[90.0], resistance=[])  # no resistance nearby, rsi normal
    snap = _snapshot(close=100.0, rsi=50.0, sr_levels=sr)
    assert should_exit_long(snap) is None


def test_should_exit_long_none_without_bearish_confirmation():
    # At resistance, but momentum still fully bullish -> no confirmation.
    sr = _sr(support=[90.0], resistance=[100.5])
    snap = _snapshot(close=100.0, rsi=55.0, ema_20=101.0, ema_50=99.0,
                      ema_200=95.0, macd_histogram=0.3, sr_levels=sr)
    assert should_exit_long(snap) is None


# -- risk_manager ---------------------------------------------------------

def test_calculate_position_size():
    size = calculate_position_size(account_balance=10_000.0, entry=100.0, sl=98.0, max_risk_pct=0.02)
    # risk_amount = 10000 * 0.02 = 200; per-unit risk = 2 -> quantity = 100
    assert size.risk_amount == pytest.approx(200.0)
    assert size.quantity == pytest.approx(100.0)


def test_calculate_position_size_rejects_zero_risk():
    with pytest.raises(ValueError):
        calculate_position_size(account_balance=10_000.0, entry=100.0, sl=100.0)


def test_meets_risk_reward_threshold():
    assert meets_risk_reward_threshold(entry=100.0, sl=98.0, tp=103.0, min_ratio=1.5)
    assert not meets_risk_reward_threshold(entry=100.0, sl=98.0, tp=101.0, min_ratio=1.5)


# -- signal_aggregator ---------------------------------------------------------

def test_aggregate_signals_confirms_when_timeframes_agree():
    sr = _sr(support=[99.7], resistance=[105.0])
    buy_snap = _snapshot(tf="1h", close=100.0, rsi=40.0, ema_20=101.0, ema_50=99.0,
                          ema_200=95.0, macd_histogram=0.3, sr_levels=sr)
    agreeing_snap = _snapshot(tf="4h", close=100.0, rsi=40.0, ema_20=101.0, ema_50=99.0,
                               ema_200=95.0, macd_histogram=0.3, sr_levels=sr)
    result = aggregate_signals({"1h": buy_snap, "4h": agreeing_snap})
    assert result is not None
    assert result.side == SignalSide.BUY
    assert result.confidence > 1.0 * 0.99  # nudged toward/at the 1.0 cap
    assert any(reason.startswith("confluence:") for reason in result.reasons)


def test_aggregate_signals_none_when_primary_timeframe_has_no_signal():
    flat_snap = _snapshot(tf="1h", close=100.0, rsi=60.0)  # rsi>=50 -> no buy
    result = aggregate_signals({"1h": flat_snap})
    assert result is None


def test_aggregate_signals_empty_input():
    assert aggregate_signals({}) is None
