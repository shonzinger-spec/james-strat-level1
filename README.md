# James Berry ICT/Orderflow Strategy — Backtest (Level 1 Data)

A backtest-only implementation of James Berry's ICT/orderflow trading strategy.
Requires only **Level 1** tick data with aggressor flags (buy_vol / sell_vol) —
no Level 2 market depth or order book data needed.

## Strategy

### Sessions
- **Primary**: 7:30am–1:00pm ET (8 MNQ contracts, 10 MNQ 10–11am power hour)
- **Afternoon**: 2:00–3:00pm ET (6 MNQ contracts)

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
- 3 consecutive losses across days -> skip next day (`--max_consec_losses 3`)

### Optional Skipped-Day Mode
The baseline skips high-impact news days, high prior-range days, and cross-day
loss-control cooldown days. `--skipped_day_mode` reopens those formerly skipped
days, but lets you apply tighter rules only on those days:

- `--skipped_day_skip_first_signal`: ignore the first qualifying skipped-day signal
- `--skipped_day_longs_only`: only take long setups on skipped days
- `--skipped_day_no_shorts_before 08:00`: block early skipped-day shorts
- `--skipped_day_size_multiplier 0.5`: half-size skipped-day trades for more drawdown cushion
- `--news_slippage_multiplier 2`: stress-test news-day trades with heavier slippage
- `--research_target_report`: print the `$716k` post-filter research target

### First-Signal Quality Gates

Use these to make trade #1 of each day stricter without changing later trades:

- `--first_signal_min_ratio 4.0`: require stronger POC imbalance for the first trade
- `--first_signal_longs_only`: block first-trade shorts
- `--first_signal_no_shorts_before 08:00`: block first-trade shorts before 8:00am ET

### Bias
Long bias by default (only shorts when trend is clearly down via `--long_bias`)

## Performance

Verified backtests (Nov 2025 – May 2026 on MNQ, 8/6 contract schedule):

| Metric | Value |
|--------|-------|
| Trades | 516 |
| Win Rate | 84.3% |
| Profit Factor | 26.92 |
| Total P&L | +$682,442 |
| Per Month | +$113,517 |
| Max Drawdown | $1,736 |

### Skipped-Day Mode

Executable skipped-day mode:
`--skipped_day_mode --skipped_day_longs_only --skipped_day_skip_first_signal`

| Metric | Value |
|--------|-------|
| Trades | 540 |
| Win Rate | 83.1% |
| Profit Factor | 25.49 |
| Total P&L | +$713,476 |
| Per Month | +$118,679 |
| Max Drawdown | $1,984 |
| Skipped-day trades | 33 trades across 12 days, +$35,080 |

Half-size skipped-day mode (`--skipped_day_size_multiplier 0.5`):

| Metric | Value |
|--------|-------|
| Total P&L | +$695,937 |
| Per Month | +$115,761 |
| Max Drawdown | $1,790 |

### Research Note

The earlier `$716,551 / 85.2% WR / PF 31.33 / $1,348 MaxDD` result came from a
post-hoc research filter on an already-generated trade log:

- remove skipped-day shorts before 8:00am ET
- remove skipped-day first trades

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

The executable skipped-day mode above is still the honest command-line trading
implementation.

### Optional Conservative Variants

First-trade POC ratio gate (`--first_signal_min_ratio 4.0`) on the baseline:

| Metric | Value |
|--------|-------|
| Trades | 500 |
| Win Rate | 86.0% |
| Profit Factor | 30.97 |
| Total P&L | +$696,549 |
| Max Drawdown | $1,552 |

This improves quality and drawdown but gives up some total P&L, so it is not
the default.

News slippage stress on skipped-day mode (`--news_slippage_multiplier 2`):

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

# Run backtest
python3 james_strategy.py --file sample_data/NQ_1m_footprint.csv \
    --contracts 8 \
    --level_tol 20 --min_ratio 3.0 --eqh_eql_orl_only \
    --trend_lookback 2 --skip_news --skip_high_vol \
    --max_consec_losses 3 --or5l_trail --or5l_trail_after 100 \
    --or5l_trail_stop 15 --long_bias --poc_position_gate \
    --pdvpoc --pdh_pdl --premarket_levels --four_hr_levels \
    --session_start_mins -120 --session_end_mins 210 \
    --afternoon_start_mins 270 --afternoon_end_mins 330 \
    --afternoon_contracts 6 --power_hour_contracts 10 \
    --intraday_stop_after 1 \
    --eqh_trim1=30 --eqh_trim2=55 \
    --ob_high_trim1=45 --ob_high_trim2=85 \
    --ob_low_trim1=45 --ob_low_trim2=85 \
    --pml_trim1=35 --pml_trim2=60 \
    --four_hr_low_trim1=45 --four_hr_low_trim2=85
```

Skipped-day mode:

```bash
python3 james_strategy.py --file sample_data/NQ_1m_footprint.csv \
    --contracts 8 \
    --level_tol 20 --min_ratio 3.0 --eqh_eql_orl_only \
    --trend_lookback 2 --skip_news --skip_high_vol \
    --skipped_day_mode --skipped_day_longs_only \
    --skipped_day_no_shorts_before 08:00 --skipped_day_skip_first_signal \
    --max_consec_losses 3 --or5l_trail --or5l_trail_after 100 \
    --or5l_trail_stop 15 --long_bias --poc_position_gate \
    --pdvpoc --pdh_pdl --premarket_levels --four_hr_levels \
    --session_start_mins -120 --session_end_mins 210 \
    --afternoon_start_mins 270 --afternoon_end_mins 330 \
    --afternoon_contracts 6 --power_hour_contracts 10 \
    --intraday_stop_after 1 \
    --eqh_trim1=30 --eqh_trim2=55 \
    --ob_high_trim1=45 --ob_high_trim2=85 \
    --ob_low_trim1=45 --ob_low_trim2=85 \
    --pml_trim1=35 --pml_trim2=60 \
    --four_hr_low_trim1=45 --four_hr_low_trim2=85
```

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

First-signal quality gate examples:

```bash
--first_signal_min_ratio 4.0
--first_signal_longs_only
--first_signal_no_shorts_before 08:00
```

Walk-forward diagnostics:

```bash
--walk_forward_report
```

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
