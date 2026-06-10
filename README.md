# James Berry ICT/Orderflow Strategy — Backtest (Level 1 Data)

A backtest implementation of James Berry's ICT/orderflow trading strategy.
Requires only **Level 1** tick data with aggressor flags (buy_vol / sell_vol) —
no Level 2 market depth or order book data needed.

The repo also includes a conservative TopstepX practice automation runner. See
`TOPSTEPX_AUTOMATION.md`. The live runner uses a 1-minute OHLCV proxy because
TopstepX bars do not expose the footprint POC fields used by the research
backtest.

## Strategy

### Sessions
- **Primary**: 7:30am–1:00pm ET (8 MNQ contracts, 10 MNQ 10–11am power hour)
- **Afternoon**: 2:00–4:00pm ET (6 MNQ contracts)

### Levels
EQH / EQL / OR5L / OB_HIGH / OB_LOW / PML / PDVPOC / 4H_LOW
(configurable with `--eqh_eql_orl_only` and per-level flags)

### Entry
1. Price touches a key level (`--level_tol 20` pts tolerance)
2. POC buy/sell ratio >= 3.0 at the touched level (per-level ratios configurable)
3. POC position gate ensures POC forms on the correct side of the level

### Exits
- Per-level trim distances (EQH T1/T2 = 30/55, OB_HIGH/OB_LOW/4H_LOW = 45/85,
  PML = 35/60, rest = 40/70)
- Runner trailing: activates at 100 pts unrealized, 15 pt trailing stop
- Hard stop: 23 pts
- No trade cap (`--max_per_day 99`), stop after 1st loss ends the day

### Loss Control
- 1 intraday loss -> end session (`--intraday_stop_after 1`)
- 4 consecutive losses across days -> skip the next qualified setup and stand down for that day (`--max_consec_losses 4`)

### Default High-Risk Discretion Mode
By default, the bot trades high-impact news days, high prior-range days, and
cross-day loss-control cooldown days instead of skipping them. Those formerly
skipped days are now treated as **high-risk discretion days** and use tighter
rules:

- `--skipped_day_mode`: compatibility flag for high-risk discretion mode; enabled by default
- `--no-skipped_day_mode`: old pure-skip baseline, where those days are not traded
- `--skipped_day_skip_first_signal`: enabled by default; ignore the first qualifying high-risk-day signal
- `--skipped_day_longs_only`: enabled by default; only take long setups on high-risk days
- `--skipped_day_no_shorts_before 08:00`: block early high-risk-day shorts by default
- `--skipped_day_size_multiplier 1.0`: full-size high-risk-day trades by default
- `--news_slippage_multiplier 1.0`: normal modeled slippage by default
- `--research_target_report`: print the `$716k` post-filter research target

### First-Signal Quality Gates

The first trade of each day is stricter by default:

- `--first_signal_min_ratio 4.0`: enabled by default; require stronger POC imbalance for the first trade
- `--first_signal_longs_only`: block first-trade shorts
- `--first_signal_no_shorts_before 08:00`: block first-trade shorts before 8:00am ET

### Bias
Long bias by default (only shorts when trend is clearly down via `--long_bias`)

## Performance

Default verified backtest (Nov 2025 – May 2026 on MNQ, 8/6 contract schedule):

| Metric | Value |
|--------|-------|
| Trades | 526 |
| Win Rate | 84.8% |
| Profit Factor | 29.43 |
| Total P&L | +$727,223 |
| Max Drawdown | $1,552 |

### Legacy Pure-Skip Baseline

To reproduce the old baseline that truly skips news/high-vol/cooldown days, add
`--no-skipped_day_mode`:

| Metric | Value |
|--------|-------|
| Trades | 516 |
| Win Rate | 84.3% |
| Profit Factor | 26.92 |
| Total P&L | +$682,442 |
| Per Month | +$113,517 |
| Max Drawdown | $1,736 |

Half-size high-risk discretion mode (`--skipped_day_size_multiplier 0.5`):

| Metric | Value |
|--------|-------|
| Total P&L | +$695,937 |
| Per Month | +$115,761 |
| Max Drawdown | $1,790 |

### Research Note

The earlier `$716,551 / 85.2% WR / PF 31.33 / $1,348 MaxDD` result came from a
post-hoc research filter on an already-generated trade log:

- remove high-risk-day shorts before 8:00am ET
- remove high-risk-day first trades

That exact legacy number remains a historical post-hoc trade-log result. The
repo now includes `--research_target_report` so the same idea can be measured
against a fresh engine pass. On the current engine, running an all-skipped-days
source pass and applying that report prints:

| Metric | Value |
|--------|-------|
| Trades | 549 |
| Win Rate | 84.0% |
| Profit Factor | 27.29 |
| Total P&L | +$730,235 |
| Max Drawdown | $1,736 |

The executable high-risk discretion mode above is still the honest command-line
trading implementation.

### Previous Default Comparison

To reproduce the previous high-risk discretion default without the first-trade ratio
gate, add `--first_signal_min_ratio 0`:

| Metric | Value |
|--------|-------|
| Trades | 540 |
| Win Rate | 83.1% |
| Profit Factor | 25.49 |
| Total P&L | +$713,476 |
| Max Drawdown | $1,984 |

The first-trade ratio gate improved profit, win rate, profit factor, and
drawdown on the verified data, so it is now the default.

News slippage stress on high-risk discretion mode (`--news_slippage_multiplier 2`):

| Metric | Value |
|--------|-------|
| Trades | 540 |
| Win Rate | 83.1% |
| Profit Factor | 25.41 |
| Total P&L | +$713,164 |
| Max Drawdown | $1,984 |

## Data Format

### Required CSV columns

| Column | Description |
|--------|-------------|
| `datetime` | Bar timestamp in UTC (ISO 8601) |
| `open`, `high`, `low`, `close` | OHLC prices |
| `volume` | Total volume |
| `buy_vol` | Buy-initiated volume (aggressor) |
| `sell_vol` | Sell-initiated volume (aggressor) |
| `delta` | Net aggressor delta (buy_vol - sell_vol) |
| `poc_buy_ratio`, `poc_sell_ratio` | POC-level buy/sell ratios |
| `poc_price` | Volume POC price |

No Level 2 data required.

## Usage

```bash
# Install dependencies
pip install -r requirements.txt

# Run default high-risk discretion backtest
python3 james_strategy.py
```

The default command expects `sample_data/NQ_1m_footprint.csv` and uses the
verified 8 MNQ / 10 MNQ power-hour / 6 MNQ afternoon setup.

To run the old pure-skip baseline, add `--no-skipped_day_mode`.

For the lower-drawdown variant, add:

```bash
--skipped_day_size_multiplier 0.5
```

Research-target report:

```bash
--research_target_report
```

News slippage stress:

```bash
--news_slippage_multiplier 2
```

First-signal quality gate overrides:

```bash
--first_signal_min_ratio 0
--first_signal_longs_only
--first_signal_no_shorts_before 08:00
```

Walk-forward diagnostics:

```bash
--walk_forward_report
```

## TopstepX Practice Automation

Create local config:

```bash
cp .env.example .env
```

Edit `.env` with your TopstepX username/API key. Keep `BOT_DRY_RUN=true` first.

Probe the API connection and auto-detected practice contract:

```bash
python3 live_topstepx.py --probe
```

Run one dry poll:

```bash
python3 live_topstepx.py --once
```

Run continuously in dry-run mode:

```bash
python3 live_topstepx.py
```

After dry-run output is correct, practice execution requires both:

```bash
BOT_DRY_RUN=false
python3 live_topstepx.py --execute
```

The practice runner submits one market entry with native TopstepX brackets:
`BOT_STOP_POINTS` for stop loss and `BOT_TAKE_PROFIT_POINTS` for take profit.
It starts with `MNQ`, `1` contract, max `3` trades/day, and refuses a new entry
when an open position already exists.

## Further Improvement Ideas

- **Engine-native research target:** convert the post-filter target into a full
  two-pass execution mode if the diagnostic remains stable.
- **Level-specific first-trade rules:** test whether early shorts are weak only
  on specific level families instead of blocking them globally.
- **Live-feasible filter comparison:** compare POC ratio, session, volatility,
  and direction gates against Level 1-only data available in real time.
- **Out-of-sample expansion:** rerun the same commands on additional NQ months
  before treating any new filter as production-grade.

## Structure

```
james-strat-level1/
  james_strategy.py     — Main backtest engine
  data_feeds/
    __init__.py
    four_hour_levels.py — 4-hour swing H/L detector
    order_blocks.py     — Order block detection
    pdvpoc_levels.py    — PDVPOC computation
  requirements.txt
  sample_data/
    README.md           — Data format description
```

## Notes

- This is a **backtest-only** implementation. No live trading, web dashboard, or
  Discord/propfarm emitters.
- All level computation is look-ahead safe (uses only prior-day data).
- The `load_correlation_data()` function uses yfinance for NVDA/SPY correlation
  and requires an internet connection (optional, via `--require_correlation`).
