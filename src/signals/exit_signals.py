"""Dynamic SL/TP calculator.

See TRADING_BOT_PLAN.md "Key Algorithms > C. Dynamic SL/TP Calculator":

    def calculate_exit_levels(entry: float, sr_levels: dict) -> tuple:
        # SL = closest support below entry (or entry * 0.98)
        # TP = closest resistance above entry (or entry + 1.5x risk)
"""
from __future__ import annotations

from src.config import settings
from src.data.models import SupportResistanceLevels


def calculate_exit_levels(
    entry: float, sr_levels: SupportResistanceLevels, side: str = "buy"
) -> tuple[float, float]:
    """Compute (stop_loss, take_profit) for a position opened at `entry`.

    BUY: SL = nearest support below entry (falls back to
    `entry * (1 - default_sl_pct)` if there is none); TP = risk *
    risk_reward_ratio above entry, capped at the nearest resistance above
    entry if one exists closer than that.
    SELL is the mirror image.
    """
    cfg = settings.risk
    side = side.lower()
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    if side == "buy":
        supports_below = sorted(s for s in sr_levels.support_levels if s < entry)
        resistances_above = sorted(r for r in sr_levels.resistance_levels if r > entry)

        sl = supports_below[-1] if supports_below else entry * (1 - cfg.default_sl_pct)
        risk = entry - sl
        tp = entry + risk * cfg.risk_reward_ratio
        if resistances_above:
            tp = min(tp, resistances_above[0])
        return sl, tp

    # side == "sell"
    resistances_above = sorted(r for r in sr_levels.resistance_levels if r > entry)
    supports_below = sorted(s for s in sr_levels.support_levels if s < entry)

    sl = resistances_above[0] if resistances_above else entry * (1 + cfg.default_sl_pct)
    risk = sl - entry
    tp = entry - risk * cfg.risk_reward_ratio
    if supports_below:
        tp = max(tp, supports_below[-1])
    return sl, tp
