"""Risk limit configuration and constants."""

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class RiskLimits:
    """Global risk limits — every trade must pass all checks.

    Tightened on 2026-05-30 after forensic audit:
      • max_position_size_pct: 10 → 7 (forces dry powder; one bad day with 5 SLs
        is now ~10% portfolio loss instead of 20%)
      • max_open_positions:    10 → 7 (caps fan-out of correlated bets)
      • max_gross_exposure_pct: NEW (70%) — explicit portfolio cap so the system
        can't deploy 100% via the per-position × count product
    """

    max_position_size_pct: Decimal = Decimal("7.0")  # % of agent's capital
    max_daily_loss_pct: Decimal = Decimal("3.0")  # % of total capital
    max_drawdown_pct: Decimal = Decimal("15.0")  # from equity peak
    min_confidence: float = 0.60
    max_open_positions: int = 7  # per agent
    max_sector_exposure_pct: Decimal = Decimal("30.0")  # % of capital in one sector
    max_gross_exposure_pct: Decimal = Decimal("70.0")  # NEW: portfolio-level cap
    max_order_rate_per_sec: int = 10  # SEBI compliance
    max_single_order_value: Decimal = Decimal("200000")  # ₹2,00,000 safety limit
    require_stop_loss: bool = True

    def to_dict(self) -> dict:
        return {
            "max_position_size_pct": float(self.max_position_size_pct),
            "max_daily_loss_pct": float(self.max_daily_loss_pct),
            "max_drawdown_pct": float(self.max_drawdown_pct),
            "min_confidence": self.min_confidence,
            "max_open_positions": self.max_open_positions,
            "max_sector_exposure_pct": float(self.max_sector_exposure_pct),
            "max_gross_exposure_pct": float(self.max_gross_exposure_pct),
            "max_order_rate_per_sec": self.max_order_rate_per_sec,
            "max_single_order_value": float(self.max_single_order_value),
            "require_stop_loss": self.require_stop_loss,
        }


@dataclass
class RiskCheckResult:
    """Result of running a trade proposal through all risk checks."""

    approved: bool
    checks: dict[str, bool] = field(default_factory=dict)
    rejections: list[str] = field(default_factory=list)
    position_size_pct: float = 0.0
    stop_loss: float = 0.0

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "checks": self.checks,
            "rejections": self.rejections,
            "position_size_pct": self.position_size_pct,
            "stop_loss": self.stop_loss,
        }
