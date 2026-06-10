#!/usr/bin/env python3
"""
james_strategy.py — James Berry ICT/orderflow backtest (Level 1 data only).

PRIMARY SIGNAL: Price touches a key level (PDH/PDL/pre-market H/L/round number)
CONFIRMATION:  Delta flips in trade direction at that level (buy/sell ratio ≥ 3:1)
ENTRY:         First 5-min candle that closes above/below the key level
STOP:          23 NQ points from entry (beyond key level structure)
EXITS:         Per-level trim distances, runner trailing

This is a backtest-only implementation. No live trading, dashboard, or web server.

Run:
    python3 james_strategy.py --file <csv_path> [args]
"""

import argparse, json, os, sys
import pandas as pd
import numpy as np
import logging

from data_feeds.pdvpoc_levels import compute_pdvpoc
from data_feeds.four_hour_levels import (
    compute_4hr_levels,
    compute_1hr_levels,
    dedup_against_existing,
)
from data_feeds.order_blocks import compute_order_blocks

logging.getLogger().setLevel(logging.ERROR)

parser = argparse.ArgumentParser()
parser.add_argument("--file",         default="data/processed/NQ_5m_cvd.csv")
parser.add_argument("--stop_pts",     type=float, default=23.0,  help="Stop distance in NQ points")
parser.add_argument("--trim1_pts",    type=float, default=40.0,  help="First trim target")
parser.add_argument("--trim2_pts",    type=float, default=70.0,  help="Second trim target")
parser.add_argument("--target_pts",   type=float, default=100.0, help="Main target")
parser.add_argument("--runner_pts",   type=float, default=175.0, help="Runner target")
parser.add_argument("--trim1_pct",    type=float, default=0.33,  help="Fraction to exit at trim1")
parser.add_argument("--trim2_pct",    type=float, default=0.33,  help="Fraction to exit at trim2")
# ── Per-level trim distance overrides (override global --trim1_pts/--trim2_pts) ─
parser.add_argument("--eqh_trim1",     type=float, default=None, help="T1 override for EQH")
parser.add_argument("--eqh_trim2",     type=float, default=None, help="T2 override for EQH")
parser.add_argument("--ob_high_trim1", type=float, default=None, help="T1 override for OB_HIGH")
parser.add_argument("--ob_high_trim2", type=float, default=None, help="T2 override for OB_HIGH")
parser.add_argument("--ob_low_trim1",  type=float, default=None, help="T1 override for OB_LOW")
parser.add_argument("--ob_low_trim2",  type=float, default=None, help="T2 override for OB_LOW")
parser.add_argument("--pml_trim1",     type=float, default=None, help="T1 override for PML")
parser.add_argument("--pml_trim2",     type=float, default=None, help="T2 override for PML")
parser.add_argument("--four_hr_low_trim1", type=float, default=None, help="T1 override for 4H_LOW")
parser.add_argument("--four_hr_low_trim2", type=float, default=None, help="T2 override for 4H_LOW")
parser.add_argument("--max_per_day", "--max_trades_day", dest="max_per_day", type=int, default=99)
parser.add_argument("--level_tol",    type=float, default=20.0,  help="Pts from key level to count as 'touch'")
parser.add_argument("--dynamic_tol", action="store_true", default=False,
                    help="Scale level_tol with ATR (base_tol * atr_ratio, min 10, max 40)")
parser.add_argument("--min_ratio",    type=float, default=3.0,   help="Min buy/sell ratio at POC (global fallback)")
# ── Per-level ratios (override global --min_ratio per level type) ──────────────
parser.add_argument("--or5l_ratio",    type=float, default=3.0,  help="POC ratio required for OR5L entries (default 3.0)")
parser.add_argument("--eqh_ratio",     type=float, default=3.0,  help="POC ratio required for EQH entries (default 3.0)")
parser.add_argument("--eql_ratio",     type=float, default=3.0,  help="POC ratio required for EQL entries (default 3.0)")
parser.add_argument("--pdvpoc_ratio",  type=float, default=2.5,  help="POC ratio required for PDVPOC entries (default 2.5)")
parser.add_argument("--pdh_pdl_ratio", type=float, default=2.0,  help="POC ratio required for PDH/PDL/PMH entries (default 2.0)")
parser.add_argument("--four_hr_ratio", type=float, default=2.0,  help="POC ratio required for 4H_HIGH/4H_LOW entries (default 2.0)")
parser.add_argument("--hourly_ratio",  type=float, default=2.0,  help="POC ratio required for 1H_HIGH/1H_LOW entries (default 2.0)")
parser.add_argument("--pml_ratio",     type=float, default=2.0,  help="POC ratio required for PML entries (default 2.0)")
parser.add_argument("--commission",   type=float, default=0.25)
parser.add_argument("--slippage",     type=float, default=1.0)
parser.add_argument("--port",         type=int,   default=8766)
parser.add_argument("--min_large_prints", type=int, default=0,
                    help="Large prints filter (0=off")
parser.add_argument("--trend_lookback", type=int, default=2,
                    help="Lookback days for trend (0=off)")
parser.add_argument("--no_browser",   action="store_true")
parser.add_argument("--primary_only", action="store_true",
                    help="Only trade 9:30-9:45am (first 3 bars)")
parser.add_argument("--session_end_mins", type=int, default=210,
                    help="Session end in minutes after 9:30 (default 210 = 1pm)")
parser.add_argument("--afternoon_start_mins", type=int, default=270,
                    help="Afternoon session start in mins after 9:30 (default 270 = 2pm, set 0 to disable)")
parser.add_argument("--afternoon_end_mins", type=int, default=390,
                    help="Afternoon session end in mins after 9:30 (default 330 = 3pm)")
parser.add_argument("--afternoon_contracts", type=int, default=6,
                    help="Contract count for afternoon session (default 6)")
parser.add_argument("--power_hour_contracts", type=int, default=10,
                    help="Override contract count for 10-11am ET (0=use --contracts, default 10)")
parser.add_argument("--skip_news",    action=argparse.BooleanOptionalAction,
                    default=True,     help="Skip FOMC/CPI/NFP/PPI days (default True)")
parser.add_argument("--primary_levels_only", action="store_true", default=False,
                    help="Only trade PDH/PDL/PM-H/PM-L — skip all round numbers")
parser.add_argument("--pdh_pdl_only", action="store_true", default=False,
                    help="Only trade PDH/PDL — skip PM-H/PM-L and round numbers")
parser.add_argument("--liquidity_only", action="store_true", default=False,
                    help="Only trade EQH/EQL/OR5H/OR5L (liquidity pools + opening range)")
parser.add_argument("--eqh_eql_orl_only", action="store_true", default=False,
                    help="Only trade EQH/EQL/OR5L — production config (drops OR5H)")
parser.add_argument("--start_date", type=str, default=None,
                    help="Only trade from this date inclusive (YYYY-MM-DD)")
parser.add_argument("--end_date", type=str, default=None,
                    help="Only trade up to this date inclusive (YYYY-MM-DD)")
parser.add_argument("--session_start_mins", type=int, default=-120,
                    help="Skip signals before this many minutes after 9:30 (e.g. -120 = start at 7:30am, 10 = start at 9:40)")
parser.add_argument("--require_absorption", action="store_true", default=False,
                    help="Only enter if signal bar has absorption=1 (high vol, minimal price move)")
parser.add_argument("--require_judas", action="store_true", default=False,
                    help="Only enter after a Judas swing (fake move >= judas_min_pts in opposite direction)")
parser.add_argument("--judas_min_pts", type=float, default=15.0,
                    help="Minimum pts for a qualifying Judas swing (default 15)")
parser.add_argument("--require_correlation", action="store_true", default=False,
                    help="Only enter when NVDA and SPY prior-day direction aligns with signal")
parser.add_argument("--skip_high_vol", action=argparse.BooleanOptionalAction, default=True,
                    help="Skip days where prior session range > 2x 20-day average (default True)")
parser.add_argument("--fomc_buffer_before", type=int, default=0,
                    help="Extra trading days to black out BEFORE each FOMC date (default 0)")
parser.add_argument("--fomc_buffer_after",  type=int, default=0,
                    help="Extra trading days to black out AFTER each FOMC date (default 0)")
parser.add_argument("--contracts",  type=int,   default=5,   help="Number of contracts (default 5 MNQ)")
parser.add_argument("--tick_value", type=float, default=2.0, help="$ per point per contract (default $2 MNQ)")
# ── Loss-control rules ────────────────────────────────────────────────────────
parser.add_argument("--max_consec_losses", type=int, default=0,
                    help="Skip next trading day after N consecutive losses (0=off)")
parser.add_argument("--intraday_stop_after", type=int, default=1,
                    help="Stop trading for the rest of the session after N consecutive losses within a day (default 1)")
parser.add_argument("--skip_two_stop_day", action="store_true", default=False,
                    help="If both daily trades stop out, skip the next trading day")
parser.add_argument("--max_weekly_loss_pts", type=float, default=0.0,
                    help="Stop trading for the rest of the week once down this many pts (0=off)")
# ── OR5L Trailing Stop ────────────────────────────────────────────────────────
parser.add_argument("--or5l_trail",       action="store_true", default=False,
                    help="Runner uses trailing stop instead of fixed target (all trade types)")
parser.add_argument("--or5l_trail_after", type=float, default=100.0,
                    help="Activate trail once runner unrealized profit hits this many pts (default 100)")
parser.add_argument("--or5l_trail_stop",  type=float, default=15.0,
                    help="Trailing stop distance in pts (default 15)")
# ── Features 1–5 ──────────────────────────────────────────────────────────────
parser.add_argument("--break_retest",    action="store_true", default=False,
                    help="Break-and-retest entry: wait for level break (strong body), then retest + POC confirmation")
parser.add_argument("--min_body_pct",    type=float, default=0.0,
                    help="Wick filter: signal/break candle body must be >= this fraction of bar range (0=off, default 0; set 0.60 to enable)")
parser.add_argument("--include_pdvpoc",  action="store_true", default=False,
                    help="Add Prior Day Volume Profile POC as a key level (PDVPOC)")
parser.add_argument("--pdvpoc",  dest="pdvpoc", action="store_true",  default=True,
                    help="Add PDVPOC as a key level (default: on)")
parser.add_argument("--no_pdvpoc", dest="pdvpoc", action="store_false",
                    help="Disable PDVPOC key level")
parser.add_argument("--four_hr_levels",    dest="four_hr_levels", action="store_true",  default=True,
                    help="Add 4-hour swing highs/lows as key levels (default: on)")
parser.add_argument("--no_four_hr_levels", dest="four_hr_levels", action="store_false",
                    help="Disable 4-hour swing H/L key levels")
parser.add_argument("--hourly_levels", dest="hourly_levels", action="store_true", default=False,
                    help="Add prior-day 1-hour swing highs/lows as key levels")
parser.add_argument("--no_hourly_levels", dest="hourly_levels", action="store_false",
                    help="Disable 1-hour swing H/L key levels")
parser.add_argument("--order_blocks", dest="order_blocks", action="store_true", default=True,
                    help="Add prior 3-day order block high/low key levels (default: on)")
parser.add_argument("--no_order_blocks", dest="order_blocks", action="store_false",
                    help="Disable order block key levels")
parser.add_argument("--pdh_pdl",  dest="pdh_pdl", action="store_true", default=True,
                    help="Add previous day high/low as key levels (default: on)")
parser.add_argument("--no_pdh_pdl", dest="pdh_pdl", action="store_false",
                    help="Disable previous day high/low key levels")
parser.add_argument("--premarket_levels",  dest="premarket_levels", action="store_true", default=True,
                    help="Add pre-market high/low as key levels (default: on)")
parser.add_argument("--no_premarket_levels", dest="premarket_levels", action="store_false",
                    help="Disable pre-market high/low key levels")
parser.add_argument("--poc_position_gate", dest="poc_position_gate", action="store_true", default=True,
                    help="Require POC to form on the correct side of the touched level (default: on)")
parser.add_argument("--no_poc_position_gate", dest="poc_position_gate", action="store_false",
                    help="Disable POC position gate")
parser.add_argument("--min_bar_volume", type=float, default=0.0,
                    help="Minimum 5-min confirmation bar volume; 0 disables")
parser.add_argument("--min_bar_delta", type=float, default=0.0,
                    help="Minimum absolute 5-min confirmation bar delta; 0 disables")
parser.add_argument("--qqq_vol_csv",     type=str,   default=None,
                    help="CSV with columns date,qqq_volume for vol-mode day classification")
parser.add_argument("--vol_mode_override", type=str, default=None,
                    choices=["SKIP", "LOW_VOL", "NORMAL", "HIGH_VOL"],
                    help="Force QQQ vol mode for all days (overrides CSV/NQ approximation)")
parser.add_argument("--br_window_minutes", type=int,   default=60,
                    help="Session window for B&R entries in minutes past 9:30 (default 60)")
parser.add_argument("--br_skip_or5l",      action="store_true", default=False,
                    help="OR5L uses first-touch; all other levels use B&R")
parser.add_argument("--br_only",           action="store_true", default=False,
                    help="B&R entries only — skip first-touch path entirely (requires --break_retest)")
parser.add_argument("--retest_tol",        type=float, default=10.0,
                    help="Pts within level to count as B&R retest (default 10)")
parser.add_argument("--csv_out",           type=str,   default=None,
                    help="If set, save full trade log to this CSV path")
# ── Direction bias ────────────────────────────────────────────────────────────
parser.add_argument("--long_bias",    action="store_true", default=False,
                    help="Skip short signals when trend is 'neutral' or 'long' (only short when trend=short)")
parser.add_argument("--longs_only",   action="store_true", default=False,
                    help="Skip ALL short signals unconditionally")
# ── Early timeout exit ────────────────────────────────────────────────────────
parser.add_argument("--min_timeout_pts", type=float, default=0.0,
                    help="At halfway through max_hold window, exit if unrealized < this threshold (0=off)")
# ── Real delta ────────────────────────────────────────────────────────────────
parser.add_argument("--real_delta", action="store_true", default=False,
                    help="Use real_buy_vol/real_sell_vol/real_delta from rebuild_footprint_real_delta.py "
                         "instead of proxy delta columns. Requires NQ_1m_footprint_real_delta.csv.")
args = parser.parse_args()

def contracts_for(mins_from_open=0, session_tag=None):
    """Return contract count for a trade based on time window and session."""
    if args.power_hour_contracts > 0 and 30 <= mins_from_open <= 90:
        return args.power_hour_contracts
    if args.afternoon_start_mins > 0 and (session_tag == 'pm' or mins_from_open >= args.afternoon_start_mins):
        return args.afternoon_contracts
    return args.contracts

# ── Per-level ratio lookup ────────────────────────────────────────────────────
# Each level type gets its own minimum POC ratio. Falls back to args.min_ratio
# for any level not explicitly listed (e.g. round numbers, OR5H, WK-O).
_LEVEL_RATIOS: dict = {
    'OR5L':    args.or5l_ratio,
    'EQH':     args.eqh_ratio,
    'EQL':     args.eql_ratio,
    'PDVPOC':  args.pdvpoc_ratio,
    'PDH':     args.pdh_pdl_ratio,
    'PDL':     args.pdh_pdl_ratio,
    'PMH':     args.pdh_pdl_ratio,
    'PML':     args.pml_ratio,
    '4H_HIGH': args.four_hr_ratio,
    '4H_LOW':  args.four_hr_ratio,
    '1H_HIGH': args.hourly_ratio,
    '1H_LOW':  args.hourly_ratio,
    'OB_HIGH': args.four_hr_ratio,
    'OB_LOW':  args.four_hr_ratio,
}

def get_level_ratio(level_name: str) -> float:
    """Return the required POC ratio for this level type, falling back to --min_ratio."""
    return _LEVEL_RATIOS.get(level_name, args.min_ratio)

_TRIM1_OVERRIDES = {
    'EQH': args.eqh_trim1, 'EQL': args.eqh_trim1,
    'OB_HIGH': args.ob_high_trim1, 'OB_LOW': args.ob_low_trim1,
    'PML': args.pml_trim1, 'PMH': args.pml_trim1,
    '4H_LOW': args.four_hr_low_trim1, '4H_HIGH': args.four_hr_low_trim1,
}
_TRIM2_OVERRIDES = {
    'EQH': args.eqh_trim2, 'EQL': args.eqh_trim2,
    'OB_HIGH': args.ob_high_trim2, 'OB_LOW': args.ob_low_trim2,
    'PML': args.pml_trim2, 'PMH': args.pml_trim2,
    '4H_LOW': args.four_hr_low_trim2, '4H_HIGH': args.four_hr_low_trim2,
}

def get_trim_pts(level_name, trim1_pts, trim2_pts):
    t1 = _TRIM1_OVERRIDES.get(level_name) or trim1_pts
    t2 = _TRIM2_OVERRIDES.get(level_name) or trim2_pts
    return t1, t2

# ── News Blackout ─────────────────────────────────────────────────────────────
# Nov 2025 – May 2026 high-impact macro dates (no trades allowed on these days)
# FOMC: rate decisions  CPI/PPI: BLS inflation  NFP: BLS jobs report
import datetime as _dt

_FOMC_DATES = [
    _dt.date(2025, 11,  7),
    _dt.date(2025, 12, 18),
    _dt.date(2026,  1, 29),
    _dt.date(2026,  3, 19),
    _dt.date(2026,  5,  7),
]

_OTHER_NEWS_DATES = {
    _dt.date(d[0], d[1], d[2]) for d in [
        # CPI releases
        (2025, 11, 13),
        (2025, 12, 11),
        (2026,  1, 15),
        (2026,  2, 12),
        (2026,  3, 12),
        (2026,  4, 10),
        (2026,  5, 13),
        # PPI releases (day after CPI)
        (2025, 11, 14),
        (2025, 12, 12),
        (2026,  1, 16),
        (2026,  2, 13),
        (2026,  3, 13),
        (2026,  4,  9),  # PPI shifted — day before CPI in Apr 2026
        (2026,  5, 14),
        # NFP (first Friday of month)
        (2025, 11,  7),  # same as FOMC Nov — counted once
        (2025, 12,  6),
        (2026,  1,  9),
        (2026,  2,  6),
        (2026,  3,  6),
        (2026,  4,  3),
        (2026,  5,  2),
    ]
}

def _trading_days_offset(date, n, direction):
    """Return n weekdays before (direction=-1) or after (+1) a given date."""
    result = []
    d = date
    while len(result) < n:
        d += _dt.timedelta(days=direction)
        if d.weekday() < 5:
            result.append(d)
    return result

_fomc_blackout = set(_FOMC_DATES)
for _fd in _FOMC_DATES:
    for _bd in _trading_days_offset(_fd, args.fomc_buffer_before, -1):
        _fomc_blackout.add(_bd)
    for _bd in _trading_days_offset(_fd, args.fomc_buffer_after, +1):
        _fomc_blackout.add(_bd)

NEWS_BLACKOUT_DATES = _fomc_blackout | _OTHER_NEWS_DATES

COSTS = args.commission + args.slippage


# ── Load ─────────────────────────────────────────────────────────────────────


def get_trend_bias(date, dates_list, sess_hl, lookback=2):
    """2 lower session closes in a row = short bias. 2 higher = long bias."""
    try: i = dates_list.index(date)
    except ValueError: return 'neutral'
    if i < lookback: return 'neutral'
    closes = [float(sess_hl.loc[dates_list[j],'sc'])
              for j in range(i-lookback, i)
              if dates_list[j] in sess_hl.index]
    if len(closes) < lookback: return 'neutral'
    if all(closes[k] > closes[k+1] for k in range(len(closes)-1)): return 'short'
    if all(closes[k] < closes[k+1] for k in range(len(closes)-1)): return 'long'
    return 'neutral'

def load(path):
    raw_path = path
    # Auto-use clean CSV if available
    _clean_path = raw_path.replace('.csv', '_clean.csv')
    if raw_path != _clean_path and os.path.exists(_clean_path):
        path = _clean_path
        print(f"  Using pre-cleaned data: {_clean_path}")
    if not os.path.exists(path):
        print(f"  Not found: {path}")
        sys.exit(1)
    df = pd.read_csv(path)
    df.columns = [c.lower().strip() for c in df.columns]
    if 'datetime' in df.columns:
        df['datetime'] = pd.to_datetime(df['datetime'], utc=True, errors='coerce')
    df = df.reset_index(drop=True)

    # ── Price corruption fix (Issue 1) ──────────────────────────────────────
    # Some bars have prices like 256.95 or 655.25 missing leading "25" (~25,256).
    # Fix by median of surrounding 5 bars (±2), then save clean CSV.
    if '_clean' not in str(path):
        _price_cols = [c for c in ['open', 'high', 'low', 'close'] if c in df.columns]
        _total = 0
        _corrections = []
        for _col in _price_cols:
            _ref = df['close'] if _col != 'close' else df['open']
            _mask = (df[_col] < 1000) & (_ref > 10000)
            _bad = df.index[_mask].tolist()
            for _i in _bad:
                _window = [_i-2, _i-1, _i+1, _i+2]
                _neighbors = [df.loc[j, _col] for j in _window
                              if j in df.index and df.loc[j, _col] > 1000]
                _old = float(df.loc[_i, _col])
                if _neighbors:
                    _new = float(np.median(_neighbors))
                else:
                    _new = _old + 25000.0
                df.loc[_i, _col] = _new
                _total += 1
                _corrections.append((str(df.loc[_i, 'datetime'])[:16], _col, _old, _new))
        if _total:
            print(f"  Price fix [{path}]: {_total:,} corrupted bars corrected")
            for dt, col, old, new in _corrections[:10]:
                print(f"    {dt}  {col:<6}  {old:>8.2f} → {new:>8.2f}")
            if len(_corrections) > 10:
                print(f"    ... and {len(_corrections)-10:,} more")
            # Save clean CSV alongside original
            df.to_csv(_clean_path, index=False)
            print(f"  Saved -> {_clean_path}")
    return df


def add_est(df):
    if 'dt_est' in df.columns:
        df['_est'] = pd.to_datetime(df['dt_est'], utc=True, errors='coerce')
    else:
        df['_est'] = df['datetime'].dt.tz_convert('US/Eastern')
    df['_date'] = df['_est'].dt.date
    df['_hour'] = df['_est'].dt.hour
    df['_min']  = df['_est'].dt.minute
    df['_mins_from_open'] = (df['_hour'] - 9) * 60 + df['_min'] - 30
    return df


# ── Key Levels ────────────────────────────────────────────────────────────────

def compute_key_levels(df):
    """
    For each trading day, compute static key levels:
      PDH/PDL    — prior session H/L
      PDO        — prior day open (9:30am ET)
      PM-H/PM-L  — today's pre-market H/L
      ONH/ONL    — overnight H/L (prior AH + today PM combined)
      WK-O       — weekly open (Monday's 9:30am open)
      PD-VWAP    — prior day closing VWAP
      OR5H/OR5L  — 5-min opening range H/L (valid after 9:35)
      OR15H/OR15L — 15-min opening range H/L (valid after 9:45)
      EQH/EQL    — equal highs/lows from prior session (liquidity pools)
      ROUND      — nearest 100-pt and 50-pt round numbers
    Session VWAP is dynamic (computed bar-by-bar in run_backtest()).
    Returns (levels_by_day dict, sess_hl DataFrame)
    """
    import datetime as _dt2
    levels_by_day = {}
    dates = sorted(df['_date'].unique())

    # Session OHLC per day
    session = df[(df['_mins_from_open'] >= 0) & (df['_mins_from_open'] <= 390)]
    session_hl = session.groupby('_date').agg(
        s_high=('high', 'max'), s_low=('low', 'min'),
        s_open=('open', 'first'), s_close=('close', 'last')
    )
    # Robust session range: strip impossible prices first, then use 10th/90th percentile
    def _robust_range(x):
        clean = x[(x > 1000) & (x < 50000)]
        if len(clean) < 5:
            return float('nan')
        return float(np.percentile(clean, 90) - np.percentile(clean, 10))
    _sess_range = session.groupby('_date')['close'].agg(_robust_range).rename('s_range')
    sess_hl = session_hl[['s_close']].rename(columns={'s_close': 'sc'}).join(_sess_range)

    # Pre-market bars (before 9:30am ET)
    pm = df[df['_mins_from_open'] < 0]
    pm_hl = pm.groupby('_date').agg(pm_high=('high', 'max'), pm_low=('low', 'min'))

    # After-hours bars (after 4pm ET, mins_from_open > 390)
    ah = df[df['_mins_from_open'] > 390]
    ah_hl = ah.groupby('_date').agg(ah_high=('high', 'max'), ah_low=('low', 'min'))

    # Opening range aggregations
    or5  = df[(df['_mins_from_open'] >= 0) & (df['_mins_from_open'] < 5)]
    or5_hl  = or5.groupby('_date').agg(or5_high=('high', 'max'),  or5_low=('low', 'min'))
    or15 = df[(df['_mins_from_open'] >= 0) & (df['_mins_from_open'] < 15)]
    or15_hl = or15.groupby('_date').agg(or15_high=('high', 'max'), or15_low=('low', 'min'))

    # Prior day VWAP — typical_price × volume summed over session
    vol_col = 'volume' if 'volume' in session.columns else None
    if vol_col:
        s2 = session.copy()
        s2['_tp']  = (s2['high'] + s2['low'] + s2['close']) / 3
        s2['_tpv'] = s2['_tp'] * s2[vol_col]
        vwap_per_day = s2.groupby('_date').apply(
            lambda g: g['_tpv'].sum() / max(g[vol_col].sum(), 1)
        ).astype(float)
    else:
        vwap_per_day = pd.Series(dtype=float)

    # Group prior-session bars by date for EQH/EQL scanning (avoid repeated filtering)
    sess_groups = {d: grp for d, grp in session.groupby('_date')}

    for i, date in enumerate(dates):
        lvls = []

        if i > 0:
            prev = dates[i - 1]
            if prev in session_hl.index:
                # PDH / PDL
                lvls.append((float(session_hl.loc[prev, 's_high']), 'PDH'))
                lvls.append((float(session_hl.loc[prev, 's_low']),  'PDL'))

                # PDO — Prior Day Open
                lvls.append((float(session_hl.loc[prev, 's_open']), 'PDO'))

                # PD-VWAP
                if prev in vwap_per_day.index:
                    lvls.append((float(vwap_per_day[prev]), 'PD-VWAP'))

            # ONH/ONL — prior AH + today PM combined
            pm_h = float(pm_hl.loc[date, 'pm_high']) if date in pm_hl.index else None
            pm_l = float(pm_hl.loc[date, 'pm_low'])  if date in pm_hl.index else None
            ah_h = float(ah_hl.loc[prev, 'ah_high']) if prev in ah_hl.index else None
            ah_l = float(ah_hl.loc[prev, 'ah_low'])  if prev in ah_hl.index else None
            hs   = [v for v in [pm_h, ah_h] if v is not None]
            ls   = [v for v in [pm_l, ah_l] if v is not None]
            if hs: lvls.append((max(hs), 'ONH'))
            if ls: lvls.append((min(ls), 'ONL'))

            # EQH/EQL — equal highs/lows from prior session (2+ tests within 5pts)
            if prev in sess_groups:
                prev_bars = sess_groups[prev]
                for vals, label in [(prev_bars['high'].values, 'EQH'),
                                    (prev_bars['low'].values,  'EQL')]:
                    used = np.zeros(len(vals), dtype=bool)
                    for j, v in enumerate(vals):
                        if used[j]: continue
                        used[j] = True
                        near = np.where(~used & (np.abs(vals - v) <= 5.0))[0]
                        if len(near) >= 1:  # j + 1 others = 2+ total tests
                            group = np.append(vals[near], v)
                            used[near] = True
                            lvls.append((float(np.mean(group)), label))

                # PDVPOC — Prior Day Volume Profile POC (price with highest buy+sell vol)
                if getattr(args, 'include_pdvpoc', False):
                    if 'buy_vol' in prev_bars.columns and 'sell_vol' in prev_bars.columns:
                        tot_vol = prev_bars['buy_vol'].fillna(0) + prev_bars['sell_vol'].fillna(0)
                        if tot_vol.sum() > 0:
                            poc_bar = prev_bars.loc[tot_vol.idxmax()]
                            poc_px  = float((poc_bar['high'] + poc_bar['low'] + poc_bar['close']) / 3)
                            lvls.append((poc_px, 'PDVPOC'))

        # Pre-market H/L
        if date in pm_hl.index:
            lvls.append((float(pm_hl.loc[date, 'pm_high']), 'PM-H'))
            lvls.append((float(pm_hl.loc[date, 'pm_low']),  'PM-L'))

        # Round numbers (100-pt and 50-pt) near today's open
        if date in session_hl.index:
            open_px = float(session_hl.loc[date, 's_open'])
            for step in [100, 50]:
                base = round(open_px / step) * step
                for offset in range(-5, 6):
                    lvl = base + offset * step
                    if abs(lvl - open_px) <= 400:
                        lvls.append((lvl, f"R{int(step)}:{int(lvl)}"))

        # Weekly Open — Monday's 9:30am open
        if date in session_hl.index:
            weekday = date.weekday()  # 0=Mon
            if weekday == 0:
                lvls.append((float(session_hl.loc[date, 's_open']), 'WK-O'))
            else:
                monday = date - _dt2.timedelta(days=weekday)
                if monday in session_hl.index:
                    lvls.append((float(session_hl.loc[monday, 's_open']), 'WK-O'))

        # Opening Range (valid only after OR period — enforced in find_key_level_touch)
        if date in or5_hl.index:
            lvls.append((float(or5_hl.loc[date, 'or5_high']), 'OR5H'))
            lvls.append((float(or5_hl.loc[date, 'or5_low']),  'OR5L'))
        if date in or15_hl.index:
            lvls.append((float(or15_hl.loc[date, 'or15_high']), 'OR15H'))
            lvls.append((float(or15_hl.loc[date, 'or15_low']),  'OR15L'))

        if lvls:
            levels_by_day[date] = lvls

    return levels_by_day, sess_hl


def _clean_bars(bars):
    """Filter out corrupt rows where low is implausibly small vs median close."""
    if bars.empty:
        return bars
    med = bars['close'].median()
    return bars[bars['low'] > med * 0.5]


def compute_pdh_pdl(df, date):
    """Return previous trading day's session high/low for date, or (None, None)."""
    session = df[(df['_mins_from_open'] >= 0) & (df['_mins_from_open'] <= 390)]
    prior_dates = sorted(d for d in session['_date'].unique() if d < date)
    if not prior_dates:
        return None, None

    prev = prior_dates[-1]
    prev_bars = _clean_bars(session[session['_date'] == prev])
    if prev_bars.empty:
        return None, None
    return float(prev_bars['high'].max()), float(prev_bars['low'].min())


def compute_premarket_levels(df, date):
    """Return current day's 04:00-09:29 ET high/low, or (None, None)."""
    mins_of_day = df['_hour'] * 60 + df['_min']
    pm = _clean_bars(df[
        (df['_date'] == date) &
        (mins_of_day >= 4 * 60) &
        (mins_of_day < 9 * 60 + 30)
    ])
    if pm.empty:
        return None, None
    return float(pm['high'].max()), float(pm['low'].min())


def add_level_if_unique(levels, level_name, level_px, duplicate_tol=10.0):
    """Append a level unless another level is already within duplicate_tol points."""
    if level_px is None:
        return levels
    px = float(level_px)
    if any(abs(float(existing_px) - px) <= duplicate_tol for existing_px, _ in levels):
        return levels
    levels.append((px, level_name))
    return levels


# ── Signal ────────────────────────────────────────────────────────────────────

def find_key_level_touch(bar, levels, tol, trend='neutral'):
    """
    Returns (level_price, level_name, direction) if bar touches a key level,
    else None. Direction: 'long' if approaching from below, 'short' from above.
    """
    lo, hi, cl = bar['low'], bar['high'], bar['close']
    mins = int(bar.get('_mins_from_open', 0) or 0)
    for lvl, name in levels:
        if args.pdh_pdl_only and name not in ('PDH', 'PDL'):
            continue
        if args.eqh_eql_orl_only:
            _eqh_ok = {'EQH', 'EQL', 'OR5L'}
            if getattr(args, 'include_pdvpoc', False) or getattr(args, 'pdvpoc', False): _eqh_ok.add('PDVPOC')
            if getattr(args, 'four_hr_levels', False): _eqh_ok.update({'4H_HIGH', '4H_LOW'})
            if getattr(args, 'hourly_levels', False): _eqh_ok.update({'1H_HIGH', '1H_LOW'})
            if getattr(args, 'order_blocks', False): _eqh_ok.update({'OB_HIGH', 'OB_LOW'})
            if getattr(args, 'pdh_pdl', False): _eqh_ok.update({'PDH', 'PDL'})
            if getattr(args, 'premarket_levels', False): _eqh_ok.update({'PMH', 'PML'})
            if name not in _eqh_ok: continue
        if args.liquidity_only and name not in ('EQH', 'EQL', 'OR5H', 'OR5L'):
            continue
        if args.primary_levels_only and name.startswith('R'):
            continue
        # Opening range levels only valid after the OR period ends (no look-ahead)
        if name in ('OR5H', 'OR5L') and mins < 5:
            continue
        if name in ('OR15H', 'OR15L') and mins < 15:
            continue
        touching = lo <= lvl + tol and hi >= lvl - tol
        if touching:
            direction = 'long' if cl > lvl else 'short'
            if trend != 'neutral' and direction != trend: continue
            return lvl, name, direction
    return None


def find_break_bar(bar, levels, tol, trend='neutral', min_body_pct=0.60):
    """
    Detect a bar that BREAKS THROUGH a key level with a strong body.
    Break = bar's range straddles the level (lo < lvl < hi) AND
            close is clearly on the far side (>1pt beyond level) AND
            body_pct >= min_body_pct (wick filter).
    Returns (level_price, level_name, direction) or None.
    """
    lo, hi = float(bar['low']), float(bar['high'])
    op, cl = float(bar['open']), float(bar['close'])
    rng = hi - lo
    if rng < 0.5:
        return None
    body     = abs(cl - op)
    body_pct = body / rng
    if body_pct < min_body_pct:
        return None

    mins = int(bar.get('_mins_from_open', 0) or 0)

    for lvl, name in levels:
        if args.pdh_pdl_only and name not in ('PDH', 'PDL'):
            continue
        if args.eqh_eql_orl_only:
            _eqh_ok = {'EQH', 'EQL', 'OR5L'}
            if getattr(args, 'include_pdvpoc', False) or getattr(args, 'pdvpoc', False): _eqh_ok.add('PDVPOC')
            if getattr(args, 'four_hr_levels', False): _eqh_ok.update({'4H_HIGH', '4H_LOW'})
            if getattr(args, 'hourly_levels', False): _eqh_ok.update({'1H_HIGH', '1H_LOW'})
            if getattr(args, 'order_blocks', False): _eqh_ok.update({'OB_HIGH', 'OB_LOW'})
            if getattr(args, 'pdh_pdl', False): _eqh_ok.update({'PDH', 'PDL'})
            if getattr(args, 'premarket_levels', False): _eqh_ok.update({'PMH', 'PML'})
            if name not in _eqh_ok: continue
        if args.liquidity_only and name not in ('EQH', 'EQL', 'OR5H', 'OR5L'):
            continue
        if args.primary_levels_only and name.startswith('R'):
            continue
        if name in ('OR5H', 'OR5L')   and mins < 5:  continue
        if name in ('OR15H', 'OR15L') and mins < 15: continue

        # Level must be strictly inside bar range
        if not (lo < lvl < hi):
            continue

        if cl > lvl + 1.0:   # broke UP — long setup
            direction = 'long'
        elif cl < lvl - 1.0: # broke DOWN — short setup
            direction = 'short'
        else:
            continue          # closed right at level, not a clean break

        if trend != 'neutral' and direction != trend:
            continue
        return lvl, name, direction

    return None


def check_delta_confirmation(bar, direction, min_ratio, level_name=''):
    """
    POC ratio check: buy_vol / sell_vol (or vice versa) must be >= min_ratio.
    Also checks delta sign.
    """
    buy  = float(bar.get('buy_vol', 0) or 0)
    sell = float(bar.get('sell_vol', 0) or 0)
    delta = float(bar.get('delta', 0) or 0)

    if level_name in ('4H_HIGH', '4H_LOW', 'OB_HIGH', 'OB_LOW'):
        poc_buy = float(bar.get('poc_buy_ratio', 0) or 0)
        poc_sell = float(bar.get('poc_sell_ratio', 0) or 0)

        if direction == 'long':
            poc_ratio = poc_buy / poc_sell if poc_sell > 0 else 0
            bar_ratio = buy / sell if sell > 0 else 0
            poc_ratio_ok = poc_ratio >= min_ratio
            bar_ratio_ok = bar_ratio >= min_ratio and delta > 0
        else:
            poc_ratio = poc_sell / poc_buy if poc_buy > 0 else 0
            bar_ratio = sell / buy if buy > 0 else 0
            poc_ratio_ok = poc_ratio >= min_ratio
            bar_ratio_ok = bar_ratio >= min_ratio and delta < 0

        ratio = poc_ratio if poc_ratio_ok else bar_ratio
        return poc_ratio_ok or bar_ratio_ok, round(ratio, 1)

    if direction == 'long':
        if delta <= 0: return False, 0
        ratio = buy / max(sell, 1)
    else:
        if delta >= 0: return False, 0
        ratio = sell / max(buy, 1)

    return ratio >= min_ratio, round(ratio, 1)


def check_poc_position(direction, poc_price, level_px, entry_px):
    """Return (ok, reason) for James's POC location gate."""
    try:
        poc = float(poc_price)
        lvl = float(level_px)
        entry = float(entry_px)
    except (TypeError, ValueError):
        return True, ""
    if not np.isfinite(poc) or poc <= 0:
        return True, ""

    if direction == "long":
        if poc < lvl - 5:
            return False, "POC below level"
        if poc > entry + 10:
            return False, "POC above entry resistance"
    elif direction == "short":
        if poc > lvl + 5:
            return False, "POC above level"
        if poc < entry - 10:
            return False, "POC below entry support"
    return True, ""


def check_bar_volume_delta(bar, min_volume, min_delta):
    """Return True when the 5-min confirmation proxy meets absolute flow floors."""
    if min_volume <= 0 and min_delta <= 0:
        return True

    vol = float(bar.get('_confirm_volume', bar.get('volume', 0)) or 0)
    delta = float(bar.get('_confirm_delta', bar.get('delta', 0)) or 0)
    if min_volume > 0 and vol < min_volume:
        return False
    if min_delta > 0 and abs(delta) < min_delta:
        return False
    return True


# ── Correlation Data ─────────────────────────────────────────────────────────

def load_correlation_data(start='2025-11-01', end='2026-05-14'):
    """
    Download NVDA and SPY daily OHLCV via yfinance.
    Returns dict: date -> {'nvda': 1/-1/0, 'spy': 1/-1/0}
    where 1 = bullish day (close > open), -1 = bearish, 0 = flat/unknown.
    """
    import yfinance as yf
    print("  Downloading NVDA + SPY daily bars (yfinance)...")
    result = {}
    try:
        raw = yf.download(['NVDA', 'SPY'], start=start, end=end,
                          auto_adjust=True, progress=False)
        # yfinance multi-ticker returns MultiIndex columns: (field, ticker)
        nvda_open  = raw[('Open',  'NVDA')]
        nvda_close = raw[('Close', 'NVDA')]
        spy_open   = raw[('Open',  'SPY')]
        spy_close  = raw[('Close', 'SPY')]
        for dt in nvda_open.index:
            d = dt.date()
            no, nc = nvda_open[dt], nvda_close[dt]
            so, sc = spy_open[dt],  spy_close[dt]
            if any(v != v for v in (no, nc, so, sc)):  # NaN check
                continue
            result[d] = {
                'nvda': 1 if nc > no else (-1 if nc < no else 0),
                'spy':  1 if sc > so else (-1 if sc < so else 0),
            }
        print(f"  Correlation data: {len(result)} trading days loaded")
    except Exception as e:
        print(f"  Warning: yfinance download failed: {e}. --require_correlation will be skipped.")
    return result


def check_correlation(corr_data, signal_date, direction):
    """
    Look up prior trading day's NVDA/SPY direction.
    Returns True if both confirm the signal direction, False otherwise.
    """
    if not corr_data:
        return True  # graceful degradation if download failed
    # Find the most recent date before signal_date
    prior = max((d for d in corr_data if d < signal_date), default=None)
    if prior is None:
        return True
    c = corr_data[prior]
    if direction == 'long':
        return c['nvda'] == 1 and c['spy'] == 1
    else:
        return c['nvda'] == -1 and c['spy'] == -1


# ── QQQ Volume Mode (Feature 2+3) ────────────────────────────────────────────

def load_qqq_vol(path=None):
    """Load QQQ first-bar volume from CSV (date,qqq_volume). Returns {date: float}."""
    if not path or not os.path.exists(path):
        return {}
    try:
        df_q = pd.read_csv(path)
        df_q.columns = [c.lower().strip() for c in df_q.columns]
        out = {}
        for _, row in df_q.iterrows():
            try:
                d = _dt.date.fromisoformat(str(row['date'])[:10])
                out[d] = float(row['qqq_volume'])
            except Exception:
                pass
        return out
    except Exception as e:
        print(f"  Warning: Could not load QQQ vol CSV: {e}")
        return {}


def get_vol_mode(date, qqq_vol_data, df_full):
    """
    Return 'SKIP', 'LOW_VOL', 'NORMAL', or 'HIGH_VOL' for the given date.
    Priority: vol_mode_override > qqq_vol_csv > NQ first-bar vol x 0.85 proxy.
    Thresholds (QQQ shares):  <1.2M=SKIP  1.2-1.5M=LOW_VOL  1.5-1.87M=NORMAL  >=1.87M=HIGH_VOL
    """
    if args.vol_mode_override:
        return args.vol_mode_override

    vol = None
    if qqq_vol_data and date in qqq_vol_data:
        vol = qqq_vol_data[date]

    if vol is None:
        return 'NORMAL'   # no QQQ CSV — don't filter
    if vol < 282_000:
        return 'SKIP'
    elif vol < 321_000:
        return 'LOW_VOL'
    elif vol < 451_000:
        return 'NORMAL'
    else:
        return 'HIGH_VOL'


# ── Trade Simulator ───────────────────────────────────────────────────────────

def _trade_result(pts, reason, trim1=False, trim2=False):
    net = pts - COSTS
    return {'net_pts': round(net, 2), 'exit_reason': reason,
            'trim1_hit': trim1, 'trim2_hit': trim2,
            'realized_rr': round(net / args.stop_pts, 2)}


def sim_trade(df, entry_bar_idx, direction, entry_px, vol_mode='NORMAL', level_name=''):
    h, l, c = df['high'].values, df['low'].values, df['close'].values
    n = len(df)

    # LOW_VOL mode: single flat 40-pt target, 20-pt stop, no scaling
    if vol_mode == 'LOW_VOL':
        if direction == 'long':
            stop_px, tgt_px = entry_px - args.stop_pts, entry_px + 40.0
        else:
            stop_px, tgt_px = entry_px + args.stop_pts, entry_px - 40.0
        for k in range(entry_bar_idx, min(entry_bar_idx + 24, n)):
            bh, bl = h[k], l[k]
            if direction == 'long':
                if bl <= stop_px: return _trade_result(stop_px - entry_px, 'stop')
                if bh >= tgt_px:  return _trade_result(tgt_px  - entry_px, 'target')
            else:
                if bh >= stop_px: return _trade_result(entry_px - stop_px, 'stop')
                if bl <= tgt_px:  return _trade_result(entry_px - tgt_px,  'target')
        ep = c[min(entry_bar_idx + 23, n - 1)]
        pts = (ep - entry_px) if direction == 'long' else (entry_px - ep)
        return _trade_result(pts, 'timeout')

    # OR5L trailing stop: replaces fixed runner target for OR5L trades only
    use_trail  = args.or5l_trail  # trail runner portion for all trade types
    max_hold   = 24   # trail does NOT extend hold — same window as baseline

    t1_pts, t2_pts = get_trim_pts(level_name, args.trim1_pts, args.trim2_pts)
    if direction == 'long':
        stop   = entry_px - args.stop_pts
        trim1  = entry_px + t1_pts
        trim2  = entry_px + t2_pts
        target = entry_px + args.target_pts
        runner = entry_px + args.runner_pts
    else:
        stop   = entry_px + args.stop_pts
        trim1  = entry_px - t1_pts
        trim2  = entry_px - t2_pts
        target = entry_px - args.target_pts
        runner = entry_px - args.runner_pts

    remaining    = 1.0
    realized     = 0.0
    trim1_done   = False
    trim2_done   = False
    peak_profit  = 0.0    # highest unrealized profit on runner portion (pts from entry)
    trail_active = False  # True once peak_profit >= or5l_trail_after

    _half = entry_bar_idx + max_hold // 2

    for k in range(entry_bar_idx, min(entry_bar_idx + max_hold, n)):
        bar_h, bar_l = h[k], l[k]

        # Early timeout exit: at halfway point, exit if unrealized < threshold
        if args.min_timeout_pts > 0 and k == _half:
            _close_k = c[k]
            if direction == 'long':
                _unreal = (_close_k - entry_px)
            else:
                _unreal = (entry_px - _close_k)
            _total_unreal = realized + remaining * _unreal
            if _total_unreal < args.min_timeout_pts:
                realized += remaining * _unreal
                remaining = 0
                exit_reason = 'timeout'
                break

        if direction == 'long':
            # Stop
            if bar_l <= stop:
                realized += remaining * (stop - entry_px)
                remaining = 0; exit_reason = 'stop'; break
            # Trim 1
            if not trim1_done and bar_h >= trim1:
                realized += args.trim1_pct * (trim1 - entry_px)
                remaining -= args.trim1_pct
                trim1_done = True
            # Trim 2
            if trim1_done and not trim2_done and bar_h >= trim2:
                realized += args.trim2_pct * (trim2 - entry_px)
                remaining -= args.trim2_pct
                trim2_done = True
            # Runner exit
            if trim2_done:
                if use_trail:
                    unreal = bar_h - entry_px
                    if 0 < unreal < 500:  # guard: 500pt single-bar move impossible on NQ
                        if unreal > peak_profit:
                            peak_profit = unreal
                    if not trail_active and peak_profit >= args.or5l_trail_after:
                        trail_active = True
                    if trail_active:
                        trail_px = entry_px + peak_profit - args.or5l_trail_stop
                        if bar_l <= trail_px:
                            realized += remaining * (trail_px - entry_px)
                            remaining = 0; exit_reason = 'runner'; break
                else:
                    if bar_h >= runner:
                        realized += remaining * (runner - entry_px)
                        remaining = 0; exit_reason = 'runner'; break
                    elif trim1_done and bar_h >= target:
                        realized += remaining * (target - entry_px)
                        remaining = 0; exit_reason = 'target'; break
        else:
            if bar_h >= stop:
                realized += remaining * (entry_px - stop)
                remaining = 0; exit_reason = 'stop'; break
            if not trim1_done and bar_l <= trim1:
                realized += args.trim1_pct * (entry_px - trim1)
                remaining -= args.trim1_pct
                trim1_done = True
            if trim1_done and not trim2_done and bar_l <= trim2:
                realized += args.trim2_pct * (entry_px - trim2)
                remaining -= args.trim2_pct
                trim2_done = True
            if trim2_done:
                if use_trail:
                    unreal = entry_px - bar_l
                    if 0 < unreal < 500:  # guard: 500pt single-bar move impossible on NQ
                        if unreal > peak_profit:
                            peak_profit = unreal
                    if not trail_active and peak_profit >= args.or5l_trail_after:
                        trail_active = True
                    if trail_active:
                        trail_px = entry_px - peak_profit + args.or5l_trail_stop
                        if bar_h >= trail_px:
                            realized += remaining * (entry_px - trail_px)
                            remaining = 0; exit_reason = 'runner'; break
                else:
                    if bar_l <= runner:
                        realized += remaining * (entry_px - runner)
                        remaining = 0; exit_reason = 'runner'; break
                    elif trim1_done and bar_l <= target:
                        realized += remaining * (entry_px - target)
                        remaining = 0; exit_reason = 'target'; break
    else:
        # Timeout — exit at last close
        exit_price = c[min(entry_bar_idx + max_hold - 1, n - 1)]
        if direction == 'long':
            realized += remaining * (exit_price - entry_px)
        else:
            realized += remaining * (entry_px - exit_price)
        exit_reason = 'timeout'

    net = realized - COSTS
    result = {
        'net_pts':     round(net, 2),
        'exit_reason': exit_reason,
        'trim1_hit':   trim1_done,
        'trim2_hit':   trim2_done,
        'realized_rr': round(net / args.stop_pts, 2),
    }
    if use_trail:
        result['runner_peak_pts'] = round(peak_profit, 2)
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def run_backtest():
    print(f"Loading {args.file}...")
    df = load(args.file)
    df = add_est(df)

    if 'volume' in df.columns:
        print("  CSV volume scale:")
        print(df['volume'].describe().to_string())

    # ── Real delta column swap ────────────────────────────────────────────────
    if args.real_delta:
        missing = [c for c in ("real_buy_vol", "real_sell_vol", "real_delta")
                   if c not in df.columns]
        if missing:
            print(f"  --real_delta requested but columns missing: {missing}")
            print(f"   Run: python3 scripts/rebuild_footprint_real_delta.py first.")
            sys.exit(1)
        df["buy_vol"]  = df["real_buy_vol"]
        df["sell_vol"] = df["real_sell_vol"]
        df["delta"]    = (df["real_delta"] *
                          (df["real_buy_vol"] + df["real_sell_vol"])).round(0)
        print(f"  [real_delta] Swapped buy_vol/sell_vol/delta <- real tick data  "
              f"(coverage: {(df['real_buy_vol']+df['real_sell_vol']>0).mean()*100:.1f}%)")

    df['_confirm_volume'] = df.get('volume', 0).rolling(5, min_periods=1).sum()
    df['_confirm_delta'] = df.get('delta', 0).rolling(5, min_periods=1).sum()
    if args.min_bar_volume > 0 or args.min_bar_delta > 0:
        print("  5-min confirmation proxy scale:")
        print(df[['_confirm_volume', '_confirm_delta']].describe().to_string())

    print(f"  Bars: {len(df):,}")

    # ── ATR for dynamic level tolerance (Task B) ─────────────────────────────
    if args.dynamic_tol:
        _tr = pd.Series(index=df.index, dtype=float)
        _prev_close = df['close'].shift(1)
        _tr = pd.concat([
            (df['high'] - df['low']).abs(),
            (df['high'] - _prev_close).abs(),
            (df['low'] - _prev_close).abs(),
        ], axis=1).max(axis=1)
        df['_atr'] = _tr.rolling(14, min_periods=14).mean()
        df['_atr_ma20'] = df['_atr'].rolling(20, min_periods=20).mean()
        print(f"  Dynamic tolerance ON: ATR(14) range [{df['_atr'].min():.1f}-{df['_atr'].max():.1f}]")

    corr_data = load_correlation_data() if args.require_correlation else {}
    print(f"  Computing key levels...")
    levels_by_day, sess_hl = compute_key_levels(df)
    c_dates = sorted(levels_by_day.keys())
    print(f"  Trading days with levels: {len(levels_by_day)}")

    # Precompute PDVPOC for each trading day (needs df["date"] as string)
    if args.pdvpoc:
        _df_poc = df[['_date', 'high', 'low', 'close', 'volume']].copy()
        _df_poc.rename(columns={'_date': 'date'}, inplace=True)
        _df_poc['date'] = _df_poc['date'].astype(str)
        pdvpoc_by_date = {d: compute_pdvpoc(_df_poc, str(d)) for d in levels_by_day}
    else:
        pdvpoc_by_date = {}

    # Precompute 4-hour swing levels for each trading day
    if args.four_hr_levels:
        print(f"  Computing 4-hour swing levels...")
        four_hr_by_date = {d: compute_4hr_levels(df, d) for d in levels_by_day}
        total_4h = sum(len(v) for v in four_hr_by_date.values())
        avg_4h   = total_4h / max(len(four_hr_by_date), 1)
        print(f"  4H levels: {total_4h} total across {len(four_hr_by_date)} days (avg {avg_4h:.1f}/day)")
    else:
        four_hr_by_date = {}

    # Precompute prior-day 1-hour swing levels for each trading day
    if args.hourly_levels:
        print(f"  Computing 1-hour swing levels...")
        hourly_by_date = {d: compute_1hr_levels(df, d) for d in levels_by_day}
        total_1h = sum(len(v) for v in hourly_by_date.values())
        avg_1h = total_1h / max(len(hourly_by_date), 1)
        print(f"  1H levels: {total_1h} total across {len(hourly_by_date)} days (avg {avg_1h:.1f}/day)")
    else:
        hourly_by_date = {}

    # Precompute order block levels for each trading day
    if args.order_blocks:
        print(f"  Computing order block levels...")
        order_blocks_by_date = {d: compute_order_blocks(df, d) for d in levels_by_day}
        total_ob = sum(len(v) for v in order_blocks_by_date.values())
        avg_ob = total_ob / max(len(order_blocks_by_date), 1)
        print(f"  OB levels: {total_ob} total across {len(order_blocks_by_date)} days (avg {avg_ob:.1f}/day)")
    else:
        order_blocks_by_date = {}

    print(f"  Stop: {args.stop_pts}pts | T1: {args.trim1_pts} | T2: {args.trim2_pts} | Target: {args.target_pts} | Runner: {args.runner_pts}")
    start_label_h  = 9 + args.session_start_mins // 60
    start_label_m  = 30 + args.session_start_mins % 60
    start_label = f"{start_label_h}:{start_label_m:02d}"
    end_mins = 45 if args.primary_only else args.session_end_mins
    total_min = 9 * 60 + 30 + end_mins
    end_hour  = total_min // 60
    end_min   = total_min % 60
    end_label = f"{end_hour}:{end_min:02d}"
    if args.afternoon_start_mins > 0:
        pm_total_min = 9 * 60 + 30 + args.afternoon_end_mins
        pm_end_hour  = pm_total_min // 60
        pm_end_min   = pm_total_min % 60
        pm_start_min = 9 * 60 + 30 + args.afternoon_start_mins
        pm_start_hour = pm_start_min // 60
        pm_start_min2 = pm_start_min % 60
        print(f"  Session: {start_label}-{end_label} + {pm_start_hour}:{pm_start_min2:02d}-{pm_end_hour}:{pm_end_min:02d} ({args.afternoon_contracts} MNQ) | Max/day: {args.max_per_day}")
    else:
        print(f"  Session: {start_label}-{end_label} | Max/day: {args.max_per_day}")
    if args.skip_news:
        skipped = [d for d in levels_by_day if d in NEWS_BLACKOUT_DATES]
        fomc_buf = f" [FOMC +/-{args.fomc_buffer_before}b/{args.fomc_buffer_after}a]" if (args.fomc_buffer_before or args.fomc_buffer_after) else ""
        print(f"  News blackout{fomc_buf}: {len(skipped)} days skipped ({', '.join(str(d) for d in sorted(skipped))})")
    if args.br_only:
        entry_mode = 'break-and-retest'
    elif args.break_retest:
        entry_mode = 'first-touch+B&R'
    else:
        entry_mode = 'first-touch'
    print(f"  Entry mode: {entry_mode} | Wick filter: {args.min_body_pct:.0%} body"
          f" | PDVPOC: {'on' if (args.pdvpoc or args.include_pdvpoc) else 'off'}"
          f" | 4H levels: {'on' if args.four_hr_levels else 'off'}"
          f" | 1H levels: {'on' if args.hourly_levels else 'off'}"
          f" | OB levels: {'on' if args.order_blocks else 'off'}"
          f" | PDH/PDL: {'on' if args.pdh_pdl else 'off'}"
          f" | PMH/PML: {'on' if args.premarket_levels else 'off'}"
          f" | POC position: {'on' if args.poc_position_gate else 'off'}"
          f" | Abs vol/delta: {args.min_bar_volume:g}/{args.min_bar_delta:g}"
          f" | Vol mode: {args.vol_mode_override or ('QQQ CSV' if args.qqq_vol_csv else 'NQ proxy (no override)')}")
    print()

    # QQQ volume data (Feature 2+3)
    qqq_vol_data   = load_qqq_vol(args.qqq_vol_csv)
    vol_mode_counts = {'SKIP': 0, 'LOW_VOL': 0, 'NORMAL': 0, 'HIGH_VOL': 0}

    # ── Local trade tracking ──────────────────────────────────────────────────
    trades = []
    total_signals = 0
    absorbed_signals = 0

    # Session range
    session_max    = 15 if args.primary_only else args.session_end_mins  # mins from open for first-touch
    br_session_max = args.br_window_minutes           # may be wider for B&R entries

    h_arr = df['high'].values
    l_arr = df['low'].values
    c_arr = df['close'].values
    vol_col = 'volume' if 'volume' in df.columns else None

    days_with_setup  = 0
    level_type_hits  = {}
    level_type_wins  = {}
    level_type_losses = {}

    # ── Loss-control state ────────────────────────────────────────────────────
    consec_losses_count = 0          # running consecutive loss counter
    skip_next_day       = False      # set True to sit out the next trading day
    week_pts            = 0.0        # running P&L for current ISO week
    week_key            = None       # (year, week_number) of current day

    for day_idx, date in enumerate(sorted(levels_by_day.keys())):
        levels = [
            lvl for lvl in levels_by_day[date]
            if lvl[1] not in ('PDH', 'PDL', 'PM-H', 'PM-L', 'PMH', 'PML')
        ]
        lb = getattr(args,"trend_lookback",2)
        trend = get_trend_bias(date,c_dates,sess_hl,lb) if lb>0 else "neutral"

        # Inject PDVPOC as a key level for this day
        if args.pdvpoc:
            _poc = pdvpoc_by_date.get(date)
            if _poc is not None:
                levels = levels + [(_poc, 'PDVPOC')]

        # Inject 4-hour swing levels (deduped against existing levels)
        if args.four_hr_levels:
            _4h = four_hr_by_date.get(date, [])
            _4h_filtered = dedup_against_existing(_4h, levels, tol=10.0)
            levels = levels + [(_p, _n) for _n, _p in _4h_filtered]

        if args.eqh_eql_orl_only:
            _ok_set = {'EQH', 'EQL', 'OR5L', 'PDVPOC'}
            if args.four_hr_levels: _ok_set.update({'4H_HIGH', '4H_LOW'})
            if args.hourly_levels: _ok_set.update({'1H_HIGH', '1H_LOW'})
            if args.order_blocks: _ok_set.update({'OB_HIGH', 'OB_LOW'})
            levels = [lvl for lvl in levels if lvl[1] in _ok_set]

        if args.pdh_pdl:
            pdh, pdl = compute_pdh_pdl(df, date)
            add_level_if_unique(levels, 'PDH', pdh)
            add_level_if_unique(levels, 'PDL', pdl)
        if args.premarket_levels:
            pmh, pml = compute_premarket_levels(df, date)
            add_level_if_unique(levels, 'PMH', pmh)
            add_level_if_unique(levels, 'PML', pml)

        # Inject prior-day 1-hour swing levels after all existing levels exist.
        if args.hourly_levels:
            _1h = hourly_by_date.get(date, [])
            _1h_filtered = dedup_against_existing(_1h, levels, tol=10.0)
            levels = levels + [(_p, _n) for _n, _p in _1h_filtered]

        # Inject order block levels after all existing levels are present.
        if args.order_blocks:
            _ob = order_blocks_by_date.get(date, [])
            _ob_filtered = dedup_against_existing(_ob, levels, tol=10.0)
            levels = levels + [(_p, _n) for _n, _p in _ob_filtered]

        if args.start_date and date < _dt.date.fromisoformat(args.start_date):
            continue
        if args.end_date and date > _dt.date.fromisoformat(args.end_date):
            continue

        if args.skip_news and date in NEWS_BLACKOUT_DATES:
            print(f"  {date}  — SKIPPED (news blackout)")
            continue

        # QQQ vol mode for this day (Features 2+3)
        vol_mode = get_vol_mode(date, qqq_vol_data, df)
        if vol_mode == 'SKIP':
            vol_mode_counts['SKIP'] += 1
            print(f"  {date}  — SKIPPED (QQQ vol too low)")
            continue
        vol_mode_counts[vol_mode] = vol_mode_counts.get(vol_mode, 0) + 1

        # ── Loss-control checks ───────────────────────────────────────────────
        # Reset weekly P&L counter when week rolls over
        iso = date.isocalendar()
        this_week = (iso[0], iso[1])
        if this_week != week_key:
            week_pts  = 0.0
            week_key  = this_week

        # Max consecutive losses -> skip this day
        if skip_next_day:
            skip_next_day = False
            print(f"  {date}  — SKIPPED (loss-control cooldown)")
            continue

        # Weekly loss cap -> skip rest of week
        if args.max_weekly_loss_pts > 0 and week_pts <= -args.max_weekly_loss_pts:
            print(f"  {date}  — SKIPPED (weekly loss cap: {week_pts:.1f}pts)")
            continue

        if args.skip_high_vol and day_idx >= 1:
            prior_dates = c_dates[max(0, day_idx - 20):day_idx]
            vol_ranges = [float(sess_hl.loc[d, 's_range'])
                          for d in prior_dates if d in sess_hl.index and not pd.isna(sess_hl.loc[d, 's_range'])]
            if len(vol_ranges) >= 5:
                avg_range  = sum(vol_ranges) / len(vol_ranges)
                prev_date  = c_dates[day_idx - 1]
                if prev_date in sess_hl.index and not pd.isna(sess_hl.loc[prev_date, 's_range']):
                    yrange = float(sess_hl.loc[prev_date, 's_range'])
                    if yrange > 2 * avg_range:
                        print(f"  {date}  HIGH VOL SKIPPED (range={yrange:.0f} vs 20d-avg={avg_range:.0f})")
                        continue

        # All session bars from exact open (for Judas swing tracking + VWAP)
        open_mask = (df['_date'] == date) & (df['_mins_from_open'] >= 0)
        open_bars  = df[open_mask].sort_index()
        sess_open  = float(open_bars['open'].iloc[0]) if len(open_bars) else 0.0

        # Build running session VWAP indexed by bar position
        vwap_by_idx = {}
        if vol_col and len(open_bars) > 0:
            cum_pv = 0.0; cum_v = 0.0
            for idx2 in open_bars.index:
                b2 = df.loc[idx2]
                v  = float(b2.get(vol_col, 0) or 0)
                tp = (float(b2['high']) + float(b2['low']) + float(b2['close'])) / 3
                cum_pv += tp * v; cum_v += v
                if cum_v > 0:
                    vwap_by_idx[idx2] = cum_pv / cum_v

        use_break_retest = args.break_retest or args.br_only or vol_mode == 'LOW_VOL'
        _effective_max = br_session_max if use_break_retest else session_max
        day_mask = (df['_date'] == date) & (
            ((df['_mins_from_open'] >= args.session_start_mins) & (df['_mins_from_open'] <= _effective_max)) |
            ((args.afternoon_start_mins > 0) &
             (df['_mins_from_open'] >= args.afternoon_start_mins) &
             (df['_mins_from_open'] <= args.afternoon_end_mins))
        )
        day_bars = df[day_mask]

        # Build index -> (running_high, running_low) from session open up to each bar
        if args.require_judas:
            run_high = sess_open; run_low = sess_open
            judas_state = {}  # idx -> (run_high, run_low) BEFORE this bar
            for i in open_bars.index:
                judas_state[i] = (run_high, run_low)
                run_high = max(run_high, open_bars.loc[i, 'high'])
                run_low  = min(run_low,  open_bars.loc[i, 'low'])

        trades_today = 0
        day_consec_losses = 0
        signals_seen = 0
        signals_absorbed = 0
        day_had_setup = False
        # Break-and-retest state: active pending breaks this day
        # key=(round_lvl, level_name), val={level_px, level_name, direction, age}
        pending_breaks: dict = {}

        for idx in day_bars.index:
            if trades_today >= args.max_per_day:
                break

            bar = df.loc[idx]

            # Dynamic level tolerance (Task B)
            if args.dynamic_tol:
                _bar_atr    = bar.get('_atr', 0) or 0
                _bar_atr_ma = bar.get('_atr_ma20', 0) or 0
                if _bar_atr > 0 and _bar_atr_ma > 0:
                    _tol = args.level_tol * (_bar_atr / _bar_atr_ma)
                    _tol = max(10.0, min(40.0, _tol))
                else:
                    _tol = args.level_tol
            else:
                _tol = args.level_tol

            # Add live session VWAP to levels for this bar
            bar_levels = levels
            vwap_val = vwap_by_idx.get(idx)
            if vwap_val is not None:
                bar_levels = levels + [(vwap_val, 'VWAP')]

            # ── Break-and-Retest path (Feature 1 + Features 2/3 LOW_VOL) ────
            if use_break_retest:
                # Age / invalidate stale pending breaks
                for bkey in list(pending_breaks.keys()):
                    bst = pending_breaks[bkey]
                    bst['age'] += 1
                    cl_ = float(bar['close'])
                    invalidated = (
                        (bst['direction'] == 'long'  and cl_ < bst['level_px'] - 5) or
                        (bst['direction'] == 'short' and cl_ > bst['level_px'] + 5) or
                        bst['age'] > 10
                    )
                    if invalidated:
                        del pending_breaks[bkey]

                # Check retests of pending break levels
                for bkey, bst in list(pending_breaks.items()):
                    lo_, hi_  = float(bar['low']), float(bar['high'])
                    lvl_px    = bst['level_px']
                    dirn      = bst['direction']
                    is_retest = ((dirn == 'long'  and lo_ <= lvl_px + args.retest_tol) or
                                 (dirn == 'short' and hi_ >= lvl_px - args.retest_tol))
                    if not is_retest:
                        continue
                    ok_delta, ratio = check_delta_confirmation(bar, dirn, get_level_ratio(bst['level_name']), bst['level_name'])
                    if not ok_delta:
                        continue
                    if not check_bar_volume_delta(bar, args.min_bar_volume, args.min_bar_delta):
                        continue
                    # Direction bias (Issue 2)
                    if args.longs_only and dirn == 'short':
                        continue
                    if args.long_bias and dirn == 'short' and trend != 'short':
                        continue
                    # Retest confirmed — enter
                    entry_px = float(bar['close'])
                    lname    = bst['level_name']
                    if args.poc_position_gate:
                        poc_ok, _ = check_poc_position(
                            dirn, bar.get('poc_price', 0), lvl_px, entry_px)
                        if not poc_ok:
                            continue
                    result   = sim_trade(df, idx, dirn, entry_px, vol_mode=vol_mode, level_name=lname)
                    dt_str   = str(bar['_est'])[:16]
                    trade = {
                        'n':          len(trades) + 1,
                        'date':       str(date), 'datetime': dt_str,
                        'direction':  dirn,
                        'level_name': lname,
                        'level_px':   round(lvl_px, 2),
                        'entry_px':   round(entry_px, 2),
                        'ratio':      ratio,
                        'trade_number': trades_today + 1,
                        'setup_type': 'retest',
                        'mins_from_open': int(bar['_mins_from_open']),
                        'session':    'pm' if args.afternoon_start_mins > 0 and bar['_mins_from_open'] >= args.afternoon_start_mins else 'am',
                        **result,
                    }
                    trades.append(trade)
                    trades_today += 1
                    day_had_setup = True
                    week_pts += result['net_pts']
                    level_type_hits[lname]  = level_type_hits.get(lname, 0) + 1
                    if result['net_pts'] > 0:
                        level_type_wins[lname]   = level_type_wins.get(lname, 0) + 1
                        consec_losses_count = 0
                        day_consec_losses = 0
                    else:
                        level_type_losses[lname] = level_type_losses.get(lname, 0) + 1
                        consec_losses_count += 1
                        if args.max_consec_losses > 0 and consec_losses_count >= args.max_consec_losses:
                            skip_next_day = True
                        day_consec_losses += 1
                        if args.intraday_stop_after > 0 and day_consec_losses >= args.intraday_stop_after:
                            trades_today = args.max_per_day
                    icon = '+' if result['net_pts'] > 0 else '-'
                    dval = result['net_pts'] * contracts_for(bar['_mins_from_open']) * args.tick_value
                    print(f"  {date}  {dirn:<5}  {lname:<10}  ratio={ratio:.1f}:1  "
                          f"[RETEST/{vol_mode}]  {icon}  "
                          f"{result['net_pts']:+6.2f}pts ({dval:+,.0f}$)  exit={result['exit_reason']}")
                    del pending_breaks[bkey]
                    break  # one trade entry per bar

                if trades_today >= args.max_per_day:
                    break

                # Detect new break bars for future retest entries (Feature 1 + Feature 5)
                break_touch = find_break_bar(bar, bar_levels, _tol, trend, args.min_body_pct)
                if break_touch:
                    lvl_px_, lname_, dirn_ = break_touch
                    if not (args.br_skip_or5l and lname_ == 'OR5L'):
                        bkey_ = (round(lvl_px_), lname_)
                        pending_breaks[bkey_] = {
                            'level_px': lvl_px_, 'level_name': lname_,
                            'direction': dirn_,  'age': 0,
                        }
                # Fall-through control:
                #   --br_only            -> skip first-touch entirely (B&R signals only)
                #   --break_retest alone -> both B&R and first-touch run on same bar
                #   --br_skip_or5l       -> OR5L still uses first-touch, others skip
                if args.br_skip_or5l:
                    _or5l_touch = find_key_level_touch(
                        bar, [(px, n) for px, n in bar_levels if n == 'OR5L'],
                        _tol, trend)
                    if _or5l_touch is not None:
                        pass  # fall through to first-touch path below
                    else:
                        continue  # non-OR5L bar in B&R mode — skip first-touch
                elif args.br_only:
                    continue  # do NOT fall through to first-touch path
                # else (--break_retest without --br_only): fall through to first-touch

            # ── First-Touch path (default / --first_touch) ───────────────────
            touch = find_key_level_touch(bar, bar_levels, _tol, trend)
            if touch is None:
                continue

            level_px, level_name, direction = touch

            # Direction bias filters
            if args.longs_only and direction == 'short':
                continue
            if args.long_bias and direction == 'short' and trend != 'short':
                continue

            # Wick filter — Feature 5 (applied in both modes; here for first-touch)
            rng_b  = float(bar['high']) - float(bar['low'])
            body_b = abs(float(bar['close']) - float(bar['open']))
            bpct_b = body_b / rng_b if rng_b > 0.5 else 0.0
            if bpct_b < args.min_body_pct:
                continue

            # Delta flip confirmation + POC ratio
            ok_delta, ratio = check_delta_confirmation(bar, direction, get_level_ratio(level_name), level_name)
            if not ok_delta:
                continue
            if not check_bar_volume_delta(bar, args.min_bar_volume, args.min_bar_delta):
                continue

            signals_seen += 1
            # poc_absorption: POC-level wall proxy (preferred over bar-level 'absorption')
            absorbed = int(bar.get('poc_absorption', 0) or 0)
            if absorbed:
                signals_absorbed += 1

            # Absorption filter
            if args.require_absorption and not absorbed:
                continue

            # Correlation filter (NVDA + SPY prior-day direction)
            if args.require_correlation and not check_correlation(corr_data, date, direction):
                continue

            # Judas swing filter
            if args.require_judas:
                rh, rl = judas_state.get(idx, (sess_open, sess_open))
                up_move   = rh - sess_open
                down_move = sess_open - rl
                judas_up   = up_move   >= args.judas_min_pts  # fake move was UP -> signal must be SHORT
                judas_down = down_move >= args.judas_min_pts  # fake move was DOWN -> signal must be LONG
                valid = (judas_up and direction == 'short') or (judas_down and direction == 'long')
                if not valid:
                    continue

            # Entry at close of confirming bar
            entry_px = bar['close']
            if args.poc_position_gate:
                poc_ok, _ = check_poc_position(
                    direction, bar.get('poc_price', 0), level_px, entry_px)
                if not poc_ok:
                    continue
            result = sim_trade(df, idx, direction, entry_px, vol_mode=vol_mode, level_name=level_name)

            dt_str = str(bar['_est'])[:16]
            trade = {
                'n':           len(trades) + 1,
                'date':        str(date),
                'datetime':    dt_str,
                'direction':   direction,
                'level_name':  level_name,
                'level_px':    round(level_px, 2),
                'entry_px':    round(float(entry_px), 2),
                'ratio':       ratio,
                'trade_number': trades_today + 1,
                'setup_type':  'first_touch',
                'mins_from_open': int(bar['_mins_from_open']),
                'session':     'pm' if args.afternoon_start_mins > 0 and bar['_mins_from_open'] >= args.afternoon_start_mins else 'am',
                **result,
            }
            trades.append(trade)
            trades_today += 1
            day_had_setup = True
            week_pts += result['net_pts']
            level_type_hits[level_name] = level_type_hits.get(level_name, 0) + 1
            if result['net_pts'] > 0:
                level_type_wins[level_name] = level_type_wins.get(level_name, 0) + 1
                consec_losses_count = 0
                day_consec_losses = 0
            else:
                level_type_losses[level_name] = level_type_losses.get(level_name, 0) + 1
                consec_losses_count += 1
                if args.max_consec_losses > 0 and consec_losses_count >= args.max_consec_losses:
                    skip_next_day = True
                day_consec_losses += 1
                if args.intraday_stop_after > 0 and day_consec_losses >= args.intraday_stop_after:
                    trades_today = args.max_per_day

            icon = '+' if result['net_pts'] > 0 else '-'
            dval = result['net_pts'] * contracts_for(bar['_mins_from_open']) * args.tick_value
            print(f"  {date}  {direction:<5}  {level_name:<10}  ratio={ratio:.1f}:1  "
                  f"{icon}  {result['net_pts']:+6.2f}pts ({dval:+,.0f}$)  exit={result['exit_reason']}")

        total_signals += signals_seen
        absorbed_signals += signals_absorbed
        if day_had_setup:
            days_with_setup += 1
        elif trades_today == 0:
            print(f"  {date}  — no qualifying setup (key level + delta confirmation)")

        # skip_two_stop_day: if both trades today were full stops, sit out tomorrow
        if args.skip_two_stop_day and trades_today > 0:
            day_trades = trades[-trades_today:]
            if all(t['exit_reason'] == 'stop' for t in day_trades):
                skip_next_day = True

    nets = [t['net_pts'] for t in trades]
    wins = [n for n in nets if n > 0]
    rrs  = [t['realized_rr'] for t in trades if t['net_pts'] > 0]

    pt_val     = args.contracts * args.tick_value
    total_pts  = sum(nets)
    def _contracts(t):
        return contracts_for(t.get('mins_from_open', 0), t.get('session'))
    total_dol  = sum(t['net_pts'] * _contracts(t) * args.tick_value for t in trades)
    wr_pct     = len(wins) / max(1, len(nets)) * 100

    if trades:
        d0 = _dt.date.fromisoformat(trades[0]['date'])
        d1 = _dt.date.fromisoformat(trades[-1]['date'])
        months_spanned = max(1.0, (d1 - d0).days / 30.44)
    else:
        months_spanned = 1.0
    per_month_dol = total_dol / months_spanned

    gross_win  = sum(n for n in nets if n > 0)
    gross_loss = abs(sum(n for n in nets if n < 0))
    pf = gross_win / gross_loss if gross_loss else float('inf')

    equity = 0.0; peak = 0.0; max_dd_dol = 0.0
    for t in trades:
        c = contracts_for(t.get('mins_from_open', 0), t.get('session'))
        dol = t['net_pts'] * c * args.tick_value
        equity += dol
        peak = max(peak, equity)
        max_dd_dol = max(max_dd_dol, peak - equity)
    dd_flag    = 'TOPSTEP SAFE' if max_dd_dol <= 2000 else 'EXCEEDS $2K LIMIT'

    best_pts  = max(nets) if nets else 0
    worst_pts = min(nets) if nets else 0

    print(f"\n{'='*60}")
    label = f"{args.contracts} MNQ"
    if args.power_hour_contracts > 0:
        label += f" (PH:{args.power_hour_contracts})"
    if args.afternoon_start_mins > 0:
        label += f" + PM:{args.afternoon_contracts}"
    print(f"  JAMES BERRY STRATEGY — FINAL RESULTS  ({label}, ${args.tick_value:.0f}/pt)")
    print(f"{'='*60}")
    print(f"  Trades:           {len(nets)}")
    print(f"  Win Rate:         {wr_pct:.1f}%")
    print(f"  Profit Factor:    {pf:.2f}")
    print(f"  Total pts:        {total_pts:+,.1f}")
    print(f"  Total $ ({args.contracts} MNQ):  {total_dol:+,.0f}$")
    print(f"  Per month:        {per_month_dol:+,.0f}$")
    print(f"  Max DD ($):       ${max_dd_dol:,.0f}  {dd_flag}")
    print(f"  Best trade:       {best_pts:+.2f}pts (${best_pts*pt_val:+,.0f})")
    print(f"  Worst trade:      {worst_pts:+.2f}pts (${worst_pts*pt_val:+,.0f})")
    print(f"  Avg RR (wins):    {sum(rrs)/max(1,len(rrs)):.2f}x")

    total_days = len([d for d in sorted(levels_by_day.keys())
                      if not (args.skip_news and d in NEWS_BLACKOUT_DATES)])
    print(f"  Days with setup:  {days_with_setup}/{total_days}")

    if level_type_hits:
        print(f"\n  Level type breakdown (trades / WR / PF):")
        for lname, count in sorted(level_type_hits.items(), key=lambda x: -x[1]):
            w = level_type_wins.get(lname, 0)
            l = level_type_losses.get(lname, 0)
            wr_l = f"{w/count*100:.0f}%" if count else "—"
            gw = sum(t['net_pts'] for t in trades if t['level_name']==lname and t['net_pts']>0)
            gl = abs(sum(t['net_pts'] for t in trades if t['level_name']==lname and t['net_pts']<=0))
            pf_l = f"{gw/gl:.2f}" if gl else "inf"
            print(f"    {lname:<12}  {count:3d} trades  WR {wr_l:<6} PF {pf_l}")

    if total_signals:
        print(f"  POC absorption rate:  {absorbed_signals}/{total_signals} signals ({absorbed_signals/total_signals*100:.1f}%) had poc_absorption=1")

    if any(vol_mode_counts.values()):
        total_vol_days = sum(vol_mode_counts.values())
        parts = [f"{k}:{v}" for k, v in vol_mode_counts.items() if v]
        print(f"  Vol mode days:        {total_vol_days} total — {', '.join(parts)}")
    if args.require_correlation and not corr_data:
        print(f"\n  Note: Nvidia/SPY filter NOT applied (no data).")
        print(f"  Live trading would add that filter — expect fewer but better trades.")
    print()

    if args.csv_out:
        import csv as _csv
        pt_val_csv = args.contracts * args.tick_value
        rows = []
        fieldnames = [
            'trade_n', 'trade_number', 'date', 'datetime',
            'setup_type', 'direction', 'level_name', 'level_px', 'entry_px',
            'session', 'poc_ratio', 'net_pts', 'net_dollars', 'exit_reason', 'trim1_hit',
            'trim2_hit', 'realized_rr', 'result', 'runner_peak_pts',
        ]
        def _csv_contracts(t):
            return contracts_for(t.get('mins_from_open', 0), t.get('session'))
        for t in trades:
            rows.append({
                'trade_n':      t['n'],
                'trade_number': t.get('trade_number', ''),
                'date':         t['date'],
                'datetime':     t['datetime'],
                'setup_type':   t.get('setup_type', ''),
                'direction':    t['direction'],
                'level_name':   t['level_name'],
                'level_px':     t['level_px'],
                'entry_px':     t['entry_px'],
                'session':      t.get('session', 'am'),
                'poc_ratio':    t['ratio'],
                'net_pts':      t['net_pts'],
                'net_dollars':  round(t['net_pts'] * _csv_contracts(t) * args.tick_value, 2),
                'exit_reason':  t['exit_reason'],
                'trim1_hit':    t['trim1_hit'],
                'trim2_hit':    t['trim2_hit'],
                'realized_rr':  t['realized_rr'],
                'result':       'WIN' if t['net_pts'] > 0 else 'LOSS',
                'runner_peak_pts': t.get('runner_peak_pts', ''),
            })
        with open(args.csv_out, 'w', newline='') as fh:
            writer = _csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"  Trade log saved -> {args.csv_out}  ({len(rows)} trades)")


if __name__ == "__main__":
    run_backtest()
