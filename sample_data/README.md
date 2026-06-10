# Sample Data

Place your Level 1 tick data CSV in this directory.

## Required CSV columns

| Column | Description |
|--------|-------------|
| `datetime` | Bar timestamp (UTC, ISO 8601) |
| `open` | Open price |
| `high` | High price |
| `low` | Low price |
| `close` | Close price |
| `volume` | Total volume |
| `buy_vol` | Buy-initiated volume (aggressor-side) |
| `sell_vol` | Sell-initiated volume (aggressor-side) |
| `delta` | buy_vol - sell_vol |
| `poc_buy_ratio` | POC-level buy ratio (buy_vol / sell_vol at POC price) |
| `poc_sell_ratio` | POC-level sell ratio (sell_vol / buy_vol at POC price) |
| `poc_price` | Volume Point of Control price for the bar |

## Data source

These columns are produced by footprint tick processors (e.g., Sierra Chart,
bookmap, or custom feedhub scripts). No Level 2 / order book data is required.

## Example usage

```bash
python3 james_strategy.py --file sample_data/NQ_1m_footprint.csv
```
