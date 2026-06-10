"""
Order block level detection from 1-minute footprint bars.

An order block is treated as a high-volume candle range that either rejects
strongly from one side or accepts strongly before reversing. Levels are computed
from prior trading days only so they are backtest-safe for the current session.
"""

import datetime
from typing import List, Tuple

import numpy as np
import pandas as pd


_ROUND_LEVEL_PTS = 100.0
_ROUND_BONUS_TOL = 15.0
_DEDUP_PTS = 10.0


def compute_order_blocks(
    df: pd.DataFrame,
    date,
    lookback_days: int = 3,
) -> List[Tuple[str, float]]:
    """
    Compute order block levels from the prior `lookback_days` trading days.

    Order block candle:
      - volume > 1.5x average lookback volume, and
      - rejection wick > 50% of range, or
      - body > 70% of range followed by reversal.

    Returns:
      [('OB_HIGH', px), ('OB_LOW', px), ...]

    Round 100 alignment is represented as a score bonus before deduplication:
    levels within 15pts of a round 100 are kept ahead of nearby non-round OBs.
    """
    date = _to_date(date)
    if '_date' not in df.columns:
        return []

    all_dates = sorted(df['_date'].unique())
    prior_dates = [d for d in all_dates if d < date]
    if not prior_dates:
        return []

    window_dates = set(prior_dates[-lookback_days:])
    bars = df[df['_date'].isin(window_dates)].copy()
    if bars.empty:
        return []

    bars = _clean_bars(bars)
    if bars.empty or 'volume' not in bars.columns:
        return []

    avg_volume = float(bars['volume'].replace([np.inf, -np.inf], np.nan).dropna().mean() or 0)
    if avg_volume <= 0:
        return []

    if '_est' in bars.columns:
        bars = bars.sort_values('_est')
    elif 'datetime' in bars.columns:
        bars = bars.sort_values('datetime')
    else:
        bars = bars.sort_index()
    candidates = []
    records = list(bars.to_dict('records'))

    for i, bar in enumerate(records):
        high = float(bar.get('high', 0) or 0)
        low = float(bar.get('low', 0) or 0)
        open_px = float(bar.get('open', 0) or 0)
        close = float(bar.get('close', 0) or 0)
        volume = float(bar.get('volume', 0) or 0)
        rng = high - low
        if rng <= 0 or volume <= avg_volume * 1.5:
            continue

        body = abs(close - open_px)
        upper_wick = high - max(open_px, close)
        lower_wick = min(open_px, close) - low
        vol_score = volume / avg_volume

        if upper_wick / rng > 0.50:
            _add_candidate(candidates, 'OB_HIGH', high, i, vol_score)
        if lower_wick / rng > 0.50:
            _add_candidate(candidates, 'OB_LOW', low, i, vol_score)

        if body / rng > 0.70 and i + 1 < len(records):
            next_close = float(records[i + 1].get('close', 0) or 0)
            if close > open_px and next_close < close:
                _add_candidate(candidates, 'OB_HIGH', high, i, vol_score)
            elif close < open_px and next_close > close:
                _add_candidate(candidates, 'OB_LOW', low, i, vol_score)

    candidates.sort(key=lambda x: (-x[3], -x[2]))
    return _dedup([(ltype, price) for ltype, price, _, _ in candidates], _DEDUP_PTS)


def _add_candidate(candidates, level_type: str, price: float, idx: int, vol_score: float) -> None:
    round_bonus = 1.0 if _near_round_100(price) else 0.0
    candidates.append((level_type, float(price), idx, vol_score + round_bonus))


def _near_round_100(price: float) -> bool:
    nearest = round(price / _ROUND_LEVEL_PTS) * _ROUND_LEVEL_PTS
    return abs(price - nearest) <= _ROUND_BONUS_TOL


def _to_date(d):
    if isinstance(d, datetime.datetime):
        return d.date()
    if isinstance(d, str):
        return datetime.date.fromisoformat(d)
    return d


def _clean_bars(bars: pd.DataFrame) -> pd.DataFrame:
    med = bars['close'].median()
    if not np.isfinite(med) or med <= 0:
        return bars.iloc[0:0]
    return bars[
        (bars['low'] >= med * 0.5) &
        (bars['high'] <= med * 2.0)
    ]


def _dedup(levels: List[Tuple[str, float]], tol: float) -> List[Tuple[str, float]]:
    kept: List[Tuple[str, float]] = []
    for ltype, price in levels:
        if any(abs(price - kept_price) <= tol for _, kept_price in kept):
            continue
        kept.append((ltype, price))
    return kept
