# data_feeds/four_hour_levels.py
# 4-Hour swing high/low level detector — James Berry's primary level type
#
# James identifies 4-hour swing H/L in pre-market each day.
# These are the turning points where institutional orders accumulate.
# This module automates that detection from 1-min bar data.
#
# Public API
# ──────────
#   levels = compute_4hr_levels(df, date)
#   → [('4H_HIGH', 21450.0), ('4H_LOW', 21200.0), ...]
#   levels = compute_1hr_levels(df, date)
#   → [('1H_HIGH', 21450.0), ('1H_LOW', 21200.0), ...]
#
# df must have columns: _est (tz-aware datetime, US/Eastern), _date (date),
#                       high, low, close, open, volume
# date: datetime.date — the CURRENT trading day (levels come from prior days)

import datetime
import pandas as pd
import numpy as np
from typing import List, Tuple, Optional


# ── Constants ──────────────────────────────────────────────────────────────────
_DEDUP_PTS   = 15.0    # collapse 4H levels within this many points (keep newest)
_DEDUP_1H_PTS = 10.0   # collapse 1H levels within this many points (keep newest)
_MIN_LEVELS  = 2       # minimum levels to return (relaxes swing_bars if too few)


def compute_4hr_levels(
    df: pd.DataFrame,
    date,
    lookback_days: int = 5,
    swing_bars: int = 3,
) -> List[Tuple[str, float]]:
    """
    Compute 4-hour swing highs and lows from the prior N trading days.

    Method:
      1. Filter df to the prior `lookback_days` trading days before `date`
      2. Resample 1-min bars to 4-hour OHLC bars (aligned to midnight ET)
      3. Detect swing highs/lows: bar[i].high is a local max if it is strictly
         greater than all bars in [i-swing_bars … i+swing_bars] (exclusive)
      4. Deduplicate levels within _DEDUP_PTS of each other (keep most recent)
      5. If swing_bars=3 yields < _MIN_LEVELS total, retry with swing_bars=2

    Args:
        df:            Full dataframe with _est, _date, high, low columns
        date:          Current trading date (datetime.date) — levels are PRIOR to this
        lookback_days: Number of prior trading days to include
        swing_bars:    Half-width of the swing detection window (default 3)

    Returns:
        List of (level_type, price) tuples:
          [('4H_HIGH', 21450.0), ('4H_LOW', 21200.0), ...]
        Ordered most-recent first within each type.
    """
    date = _to_date(date)

    # ── 1. Filter to prior lookback_days trading days ──────────────────────────
    all_dates = sorted(df['_date'].unique())
    prior_dates = [d for d in all_dates if d < date]
    if not prior_dates:
        return []
    window_dates = set(prior_dates[-lookback_days:])
    prior_df = df[df['_date'].isin(window_dates)].copy()
    if prior_df.empty:
        return []

    # ── 1b. Sanity-filter bad ticks (occasional CSV artefacts with extreme lows) ─
    med_high = prior_df['high'].median()
    if med_high > 0:
        prior_df = prior_df[
            (prior_df['low']  >= med_high * 0.5) &
            (prior_df['high'] <= med_high * 2.0)
        ]
    if prior_df.empty:
        return []

    # ── 2. Resample to 4-hour OHLC ────────────────────────────────────────────
    bars_4h = _resample_4h(prior_df)
    if bars_4h is None or len(bars_4h) < 3:
        return []

    # ── 3. Swing detection (with fallback to swing_bars=2 if too few results) ──
    levels = _detect_swings(bars_4h, swing_bars)
    if len(levels) < _MIN_LEVELS and swing_bars > 2:
        levels = _detect_swings(bars_4h, 2)

    # ── 4. Deduplicate within _DEDUP_PTS (most recent wins) ───────────────────
    levels = _dedup(levels, _DEDUP_PTS)

    return levels


def compute_1hr_levels(
    df: pd.DataFrame,
    date,
    swing_bars: int = 2,
) -> List[Tuple[str, float]]:
    """
    Compute 1-hour swing highs and lows from the prior trading day.

    Method mirrors compute_4hr_levels, but uses the immediately prior trading
    day only and resamples to 1-hour OHLC bars.

    Returns:
        [('1H_HIGH', price), ('1H_LOW', price), ...]
        Ordered most-recent first within each type.
    """
    date = _to_date(date)

    all_dates = sorted(df['_date'].unique())
    prior_dates = [d for d in all_dates if d < date]
    if not prior_dates:
        return []
    prior_df = df[df['_date'] == prior_dates[-1]].copy()
    if prior_df.empty:
        return []

    med_high = prior_df['high'].median()
    if med_high > 0:
        prior_df = prior_df[
            (prior_df['low'] >= med_high * 0.5) &
            (prior_df['high'] <= med_high * 2.0)
        ]
    if prior_df.empty:
        return []

    bars_1h = _resample_ohlc(prior_df, '1h')
    if bars_1h is None or len(bars_1h) < 3:
        return []

    levels = _detect_swings(bars_1h, swing_bars, '1H_HIGH', '1H_LOW')
    if len(levels) < _MIN_LEVELS and swing_bars > 1:
        levels = _detect_swings(bars_1h, 1, '1H_HIGH', '1H_LOW')

    return _dedup(levels, _DEDUP_1H_PTS)


def _to_date(d):
    """Accept datetime.date, datetime.datetime, or ISO string."""
    if isinstance(d, datetime.datetime):
        return d.date()
    if isinstance(d, str):
        return datetime.date.fromisoformat(d)
    return d


def _resample_4h(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    Resample 1-min df to 4-hour bars.
    Aligns to midnight ET (origin='start_day' with tz-aware index).
    Returns DataFrame with columns: high, low, ts (bar open timestamp).
    """
    return _resample_ohlc(df, '4h')


def _resample_ohlc(df: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
    """Resample 1-min df to OHLC swing bars with columns high, low, ts."""
    try:
        idx_col = '_est' if '_est' in df.columns else 'datetime'
        tmp = df[[idx_col, 'high', 'low']].copy()
        tmp = tmp.set_index(idx_col).sort_index()

        bars = tmp.resample(rule, origin='start_day').agg(
            high=('high', 'max'),
            low=('low', 'min'),
        ).dropna(how='all')

        bars['ts'] = bars.index
        return bars.reset_index(drop=True)
    except Exception:
        return None


def _detect_swings(
    bars: pd.DataFrame,
    swing_bars: int,
    high_label: str = '4H_HIGH',
    low_label: str = '4H_LOW',
) -> List[Tuple[str, float]]:
    """
    Detect swing highs and lows in a 4H bar series.

    A bar at position i is:
      swing high: bars.high[i] > bars.high[j] for all j in [i-n .. i+n], j≠i
      swing low:  bars.low[i]  < bars.low[j]  for all j in [i-n .. i+n], j≠i

    Returns list of (level_type, price, timestamp) sorted newest first.
    """
    n = swing_bars
    highs = bars['high'].values
    lows  = bars['low'].values
    ts    = bars['ts'].values if 'ts' in bars.columns else np.arange(len(bars))

    results = []   # (timestamp_ordinal, level_type, price)

    for i in range(n, len(bars) - n):
        window_h = np.concatenate([highs[i-n:i], highs[i+1:i+n+1]])
        window_l = np.concatenate([lows[i-n:i],  lows[i+1:i+n+1]])

        if highs[i] > window_h.max():
            results.append((i, high_label, float(highs[i])))
        if lows[i]  < window_l.min():
            results.append((i, low_label,  float(lows[i])))

    # Sort newest first (higher index = more recent bar)
    results.sort(key=lambda x: -x[0])
    return [(ltype, price) for _, ltype, price in results]


def _dedup(
    levels: List[Tuple[str, float]],
    tol: float,
) -> List[Tuple[str, float]]:
    """
    Remove duplicate levels within `tol` points.
    Levels are ordered most-recent first; earlier entries are kept, later dropped.
    Dedup is applied across both 4H_HIGH and 4H_LOW (any level type).
    """
    kept: List[Tuple[str, float]] = []
    for ltype, price in levels:
        if any(abs(price - p) <= tol for _, p in kept):
            continue
        kept.append((ltype, price))
    return kept


def dedup_against_existing(
    levels_4h: List[Tuple[str, float]],
    existing_levels: List[Tuple[float, str]],
    tol: float = 10.0,
) -> List[Tuple[str, float]]:
    """
    Filter out any 4H level that is within `tol` points of an already-computed
    level (PDH/PDL/EQH/EQL/OR5L/PDVPOC).

    Args:
        levels_4h:       Output of compute_4hr_levels — list of (type, price)
        existing_levels: james_strategy levels list — list of (price, type)
        tol:             Minimum separation in NQ points (default 10)

    Returns:
        Filtered list of (type, price) tuples.
    """
    existing_prices = [float(p) for p, _ in existing_levels]
    return [
        (ltype, price)
        for ltype, price in levels_4h
        if not any(abs(price - ep) <= tol for ep in existing_prices)
    ]


# ── Offline test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytz
    EST = pytz.timezone('US/Eastern')

    # Build 5 days of synthetic 1-min bars (2026-01-05 through 2026-01-09)
    # with clear swing highs/lows at 4-hour boundaries
    np.random.seed(42)
    dates = pd.date_range('2026-01-05 00:00', '2026-01-10 00:00',
                          freq='1min', tz='US/Eastern')[:-1]

    base = 21000.0
    n = len(dates)
    t = np.arange(n)
    # Sine wave over 4-hour periods to create clear swings
    prices = base + 100 * np.sin(2 * np.pi * t / 240) + np.random.randn(n) * 3

    df_test = pd.DataFrame({
        '_est':   dates,
        '_date':  dates.date,
        'high':   prices + 5,
        'low':    prices - 5,
        'close':  prices,
        'open':   prices,
        'volume': np.random.randint(100, 500, n),
    })

    test_date = datetime.date(2026, 1, 10)
    levels = compute_4hr_levels(df_test, test_date, lookback_days=5, swing_bars=3)

    print(f"4H levels for {test_date} (prior 5 days):")
    for ltype, price in levels:
        print(f"  {ltype:10s} {price:.2f}")
    print(f"Total: {len(levels)} levels")

    highs = [(t, p) for t, p in levels if t == '4H_HIGH']
    lows  = [(t, p) for t, p in levels if t == '4H_LOW']
    print(f"  Highs: {len(highs)}  Lows: {len(lows)}")
    assert len(levels) > 0, "Should detect at least some swing levels"
    print("✅  Offline test passed")
