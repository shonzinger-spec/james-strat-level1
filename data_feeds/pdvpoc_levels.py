# data_feeds/pdvpoc_levels.py
# Prior Day Volume Point of Control (PDVPOC) level calculator
#
# BACKTESTABLE — uses only historical volume bar data (already in CSV)
# Add --pdvpoc flag to james_strategy.py to enable

import pandas as pd
import numpy as np
from typing import Optional


def compute_pdvpoc(df: pd.DataFrame, date: str, bucket_pts: float = 10.0) -> Optional[float]:
    """
    Computes the Prior Day Volume Point of Control for a given date.

    PDVPOC = the price level with the highest volume from the previous
    trading day. Acts as a magnet/key level — price tends to revisit it.
    James Berry uses this in premarket prep alongside 4-hr levels.

    Args:
        df:          Full dataframe with columns: date, high, low, close, volume
        date:        Current trading date (string 'YYYY-MM-DD')
        bucket_pts:  Price bucket size in points (default 10pts for NQ)

    Returns:
        PDVPOC price level (float) or None if no prior day data
    """
    all_dates = sorted(df["date"].unique())

    if date not in all_dates:
        return None

    idx = all_dates.index(date)
    if idx == 0:
        return None  # No prior day

    prior_date = all_dates[idx - 1]
    prior_bars = df[df["date"] == prior_date].copy()

    if prior_bars.empty:
        return None

    # Typical price as volume proxy per bar
    prior_bars["typ_px"] = (
        prior_bars["high"] + prior_bars["low"] + prior_bars["close"]
    ) / 3.0

    # Bucket to nearest N points
    prior_bars["px_bucket"] = (
        (prior_bars["typ_px"] / bucket_pts).round() * bucket_pts
    )

    vol_profile = prior_bars.groupby("px_bucket")["volume"].sum()

    if vol_profile.empty:
        return None

    poc = float(vol_profile.idxmax())
    return poc


def compute_pdvpoc_range(
    df: pd.DataFrame,
    date: str,
    bucket_pts: float = 10.0,
    top_n: int = 3,
) -> list:
    """
    Returns top N highest-volume price levels from prior day.
    Useful for identifying a 'value area' not just a single POC.

    Returns list of (price, volume) tuples sorted by volume desc.
    """
    all_dates = sorted(df["date"].unique())

    if date not in all_dates:
        return []

    idx = all_dates.index(date)
    if idx == 0:
        return []

    prior_date = all_dates[idx - 1]
    prior_bars = df[df["date"] == prior_date].copy()

    if prior_bars.empty:
        return []

    prior_bars["typ_px"] = (
        prior_bars["high"] + prior_bars["low"] + prior_bars["close"]
    ) / 3.0
    prior_bars["px_bucket"] = (
        (prior_bars["typ_px"] / bucket_pts).round() * bucket_pts
    )

    vol_profile = (
        prior_bars.groupby("px_bucket")["volume"]
        .sum()
        .sort_values(ascending=False)
        .head(top_n)
    )

    return [(float(px), int(vol)) for px, vol in vol_profile.items()]


# ── Offline test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Minimal synthetic dataframe test
    dates = ["2026-01-02"] * 390 + ["2026-01-03"] * 390
    np.random.seed(42)
    base = 21000.0
    prices = base + np.random.randn(780) * 50

    df_test = pd.DataFrame({
        "date":   dates,
        "high":   prices + 5,
        "low":    prices - 5,
        "close":  prices,
        "volume": np.random.randint(500, 5000, 780),
    })

    # Inject a clear POC at 21050 on day 1
    mask = df_test["date"] == "2026-01-02"
    idx_high = df_test[mask].sample(20, random_state=1).index
    df_test.loc[idx_high, "close"] = 21050.0
    df_test.loc[idx_high, "high"]  = 21055.0
    df_test.loc[idx_high, "low"]   = 21045.0
    df_test.loc[idx_high, "volume"] = 50000

    poc = compute_pdvpoc(df_test, "2026-01-03", bucket_pts=10.0)
    print(f"PDVPOC for 2026-01-03: {poc}")  # Should be near 21050

    top = compute_pdvpoc_range(df_test, "2026-01-03", top_n=3)
    print(f"Top 3 levels: {top}")
