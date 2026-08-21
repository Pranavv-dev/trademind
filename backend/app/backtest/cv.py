"""Cross-validation primitives for path-dependent financial labels.

Pattern #3 from STRATEGY_RESEARCH.md. Standard k-fold CV LEAKS information when
labels are path-dependent (triple-barrier labels overlap multiple bars; holding-
period labels touch the future of in-sample bars). Lopez de Prado (Advances in
Financial Machine Learning, Ch. 7) developed two fixes:

  1. PURGED K-FOLD: remove training samples whose label horizon overlaps a test
     fold from the training set.
  2. EMBARGO: additionally remove training samples within `embargo` bars AFTER
     the end of any test fold (to prevent leakage via serial-correlation).

This module also provides a walk-forward harness — the gold standard for
strategy validation in live-mimicking conditions. It runs:

    [train_window]                                                 → test_window
                  [train_window]                            → test_window
                                [train_window]     → test_window
                                              ...

with the train window sliding forward by `step` after each cycle. Use this
to estimate out-of-sample edge that's robust to regime changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator


@dataclass
class CVSplit:
    """One train/test split with absolute date ranges."""

    train_start: date
    train_end: date
    test_start: date
    test_end: date
    purged_count: int = 0
    embargoed_count: int = 0


def purged_kfold(
    dates: list[date],
    n_splits: int = 5,
    label_horizon_days: int = 10,
    embargo_days: int = 5,
) -> Iterator[CVSplit]:
    """Generate purged k-fold splits over a list of trading dates.

    Each test fold is a contiguous slice. Training samples within
    [test_start - label_horizon_days, test_end + embargo_days] are removed
    (purged). The remaining dates form the training set.

    Args:
        dates: sorted list of trading dates covering the full period
        n_splits: number of folds
        label_horizon_days: longest possible label horizon — bars within this
            distance of any test sample are purged from training
        embargo_days: extra bars after a test fold to embargo

    Yields:
        CVSplit with the relevant date ranges and counts of purged/embargoed bars.

    Notes:
        Use `label_horizon_days` ≥ your strategy's max holding period.
        For a 10-day swing strategy, set ≥ 10. For 5m intraday, set ≥ 1.
    """
    if not dates or n_splits < 2:
        return

    n = len(dates)
    fold_size = n // n_splits
    if fold_size < 1:
        return

    for fold_idx in range(n_splits):
        test_start_idx = fold_idx * fold_size
        test_end_idx = (fold_idx + 1) * fold_size if fold_idx < n_splits - 1 else n
        test_start = dates[test_start_idx]
        test_end = dates[test_end_idx - 1]

        purge_lower = test_start - timedelta(days=label_horizon_days)
        purge_upper = test_end + timedelta(days=label_horizon_days + embargo_days)

        train_dates_kept = []
        purged = 0
        embargoed = 0
        for i, d in enumerate(dates):
            if test_start_idx <= i < test_end_idx:
                continue  # this is the test fold
            if purge_lower <= d <= test_end:
                purged += 1
                continue
            if test_end < d <= purge_upper:
                embargoed += 1
                continue
            train_dates_kept.append(d)

        if not train_dates_kept:
            continue

        yield CVSplit(
            train_start=train_dates_kept[0],
            train_end=train_dates_kept[-1],
            test_start=test_start,
            test_end=test_end,
            purged_count=purged,
            embargoed_count=embargoed,
        )


@dataclass
class WalkForwardWindow:
    """One walk-forward train/test window."""

    train_start: date
    train_end: date
    test_start: date
    test_end: date


def walk_forward_windows(
    start: date,
    end: date,
    train_months: int = 12,
    test_months: int = 3,
    step_months: int = 1,
) -> Iterator[WalkForwardWindow]:
    """Generate walk-forward windows.

    Default: 12-month train / 3-month test, step forward 1 month at a time.
    This matches Pardo's recommended starting parameters for daily-bar strategies.

    For a 5-year historical window:
      training set spans months [0–11], tests on months [12–14]
      training set spans months [1–12], tests on months [13–15]
      ...

    Yields ~52 windows for a 5-year backtest with default params.
    """

    def add_months(d: date, n: int) -> date:
        m = d.month + n
        y = d.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        # Cap day at month end
        try:
            return d.replace(year=y, month=m)
        except ValueError:
            # Handle month-end edge: e.g., Jan 31 → Feb 28
            from calendar import monthrange

            last_day = monthrange(y, m)[1]
            return d.replace(year=y, month=m, day=min(d.day, last_day))

    cursor = start
    while True:
        train_start = cursor
        train_end = add_months(train_start, train_months)
        test_start = train_end
        test_end = add_months(test_start, test_months)
        if test_end > end:
            break
        yield WalkForwardWindow(
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
        )
        cursor = add_months(cursor, step_months)


def deflated_sharpe_threshold(
    n_strategies_tested: int,
    confidence: float = 0.95,
) -> float:
    """Multiple-testing adjustment for Sharpe ratio (Bailey-Lopez de Prado 2014).

    When you've tested N strategies and pick the best, you need a higher Sharpe
    just to clear the multiple-testing bar at the same statistical confidence.

    Returns the minimum observed Sharpe (over N tests) that's needed to claim
    "real" edge at the given confidence. Use to gate deployment decisions.

    Reference: "The Deflated Sharpe Ratio: Correcting for Selection Bias,
    Backtest Overfitting, and Non-Normality" — Bailey & Lopez de Prado, 2014.
    """
    import math

    # Inverse-normal-ish approximation; the exact derivation uses Gaussian extreme
    # value theory. This is a good practical approximation for N up to ~10000.
    z = _inverse_normal_cdf(confidence)
    # Maximum-Sharpe correction
    expected_max = (1 - 0.5772156649) * _inverse_normal_cdf(
        1 - 1.0 / max(n_strategies_tested, 1)
    ) + 0.5772156649 * _inverse_normal_cdf(1 - 1.0 / (n_strategies_tested * math.e))
    return float(expected_max + z * math.sqrt(1.0 / max(n_strategies_tested, 1)))


def _inverse_normal_cdf(p: float) -> float:
    """Quick rational approximation to the inverse normal CDF.

    Acceptable for our practical multiple-testing thresholds (4 sig figs).
    Beats pulling in scipy for this single use.
    """
    if p <= 0 or p >= 1:
        raise ValueError("p must be in (0, 1)")
    # Acklam's approximation
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]

    plow = 0.02425
    phigh = 1 - plow
    import math

    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(
            (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
            / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )
