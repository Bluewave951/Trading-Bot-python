"""Position sizing and risk/reward ratio calculations."""
from __future__ import annotations

from dataclasses import dataclass

from src.config import settings


@dataclass
class PositionSize:
    """Result of sizing a position to a fixed account-risk budget."""
    quantity: float
    risk_amount: float   # account currency at risk if SL is hit
    risk_pct: float       # fraction of account_balance that represents


def calculate_position_size(
    account_balance: float,
    entry: float,
    sl: float,
    max_risk_pct: float | None = None,
) -> PositionSize:
    """Size a position so a stop-out loses at most `max_risk_pct` of the account.

    quantity = (account_balance * max_risk_pct) / abs(entry - sl)
    """
    if account_balance <= 0:
        raise ValueError("account_balance must be positive")
    max_risk_pct = (
        max_risk_pct if max_risk_pct is not None else settings.risk.max_position_risk_pct
    )
    per_unit_risk = abs(entry - sl)
    if per_unit_risk <= 0:
        raise ValueError("entry and sl must differ to size a position")

    risk_amount = account_balance * max_risk_pct
    quantity = risk_amount / per_unit_risk
    return PositionSize(quantity=quantity, risk_amount=risk_amount, risk_pct=max_risk_pct)


def meets_risk_reward_threshold(
    entry: float, sl: float, tp: float, min_ratio: float | None = None
) -> bool:
    """True if the trade's reward/risk ratio is at least `min_ratio`
    (defaults to `settings.risk.risk_reward_ratio`)."""
    min_ratio = min_ratio if min_ratio is not None else settings.risk.risk_reward_ratio
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return False
    return (reward / risk) >= min_ratio
