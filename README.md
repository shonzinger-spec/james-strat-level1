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
- No intraday stop after loss (`--intraday_stop_after 99` — changed from 1)
- 3 consecutive losses across days -> skip next day (`--max_consec_losses 3`)

### Bias
Long bias by default (only shorts when trend is clearly down via `--long_bias`)

## Performance

Stress test results (Nov 2025 – May 2026 on MNQ):

| Metric | Value |
|--------|-------|
| Trades | 526 |
| Win Rate | 84.6% |
| Profit Factor | 29.66 |
| Total P&L | +$488,000 |
| Per Month | +$81,000 |
| Max Drawdown | $1,736 |

Of 526 trades, a **133-trade "clean prior-bar confirmable" subset** achieves
**94% WR** — representing the highest-confidence signals.

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
    --level_tol 20 --min_ratio 3.0 --eqh_eql_orl_only \
    --trend_lookback 2 --skip_news --skip_high_vol \
    --max_consec_losses 3 --or5l_trail --or5l_trail_after 100 \
    --or5l_trail_stop 15 --long_bias --poc_position_gate \
    --pdvpoc --pdh_pdl --premarket_levels --four_hr_levels \
    --session_start_mins -120 --session_end_mins 210 \
    --afternoon_start_mins 270 --afternoon_end_mins 330 \
    --afternoon_contracts 6 --power_hour_contracts 10 \
    --intraday_stop_after 99 \
    --eqh_min_hour 8 \
    --level_scale \
    --eqh_trim1=30 --eqh_trim2=55 \
    --ob_high_trim1=45 --ob_high_trim2=85 \
    --ob_low_trim1=45 --ob_low_trim2=85 \
    --pml_trim1=35 --pml_trim2=60 \
    --four_hr_low_trim1=45 --four_hr_low_trim2=85
```

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
