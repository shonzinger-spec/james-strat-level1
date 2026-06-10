# TopstepX Practice Automation

This repo now includes a conservative TopstepX practice runner:

- `topstepx_client.py` authenticates and calls the ProjectX/TopstepX REST API.
- `live_signal_proxy.py` creates live signals from 1-minute OHLCV bars.
- `live_topstepx.py` polls TopstepX, auto-detects the active contract, and can submit bracketed practice orders.

## Important

The verified Python research backtest uses footprint fields such as `poc_price`,
`poc_buy_ratio`, and `poc_sell_ratio`. TopstepX minute bars do not provide those
footprint fields. The live automation therefore uses the same OHLCV proxy logic
as the TradingView strategy. Treat it as a practice automation layer, not a
guaranteed clone of the backtest.

TopstepX API access is powered by ProjectX. Their docs show:

- API key login: `POST https://api.topstepx.com/api/Auth/loginKey`
- active account search: `POST https://api.topstepx.com/api/Account/search`
- contract search: `POST https://api.topstepx.com/api/Contract/search`
- retrieve bars: `POST https://api.topstepx.com/api/History/retrieveBars`
- place order: `POST https://api.topstepx.com/api/Order/place`
- bracket fields: `stopLossBracket` and `takeProfitBracket`

Topstep also states there is no sandbox environment. Use your practice account,
start with `BOT_DRY_RUN=true`, and monitor the first sessions.

## Setup

```bash
cd /Users/shonzinger/james-strat-level1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```bash
TOPSTEPX_USERNAME=your_topstepx_username
TOPSTEPX_API_KEY=your_topstepx_api_key
TOPSTEPX_LIVE=false
TOPSTEPX_SYMBOL=MNQ
TOPSTEPX_CONTRACTS=1
BOT_DRY_RUN=true
```

Do not commit `.env`.

## 1. Probe The Connection

This logs in, finds your account, finds the active MNQ contract, and exits.

```bash
python3 live_topstepx.py --probe
```

If you have multiple practice accounts, copy the wanted account id into `.env`:

```bash
TOPSTEPX_ACCOUNT_ID=123456
```

## 2. Dry Run One Poll

This pulls recent 1-minute bars and prints whether a trade would fire.

```bash
python3 live_topstepx.py --once
```

## 3. Dry Run Continuous

```bash
python3 live_topstepx.py
```

Leave this running while TopstepX is open. It will print `no signal` or a JSON
signal plus the exact order payload it would submit.

## 4. Practice Execution

Only after dry-run output looks right:

```bash
# edit .env
BOT_DRY_RUN=false

python3 live_topstepx.py --execute
```

The runner submits a market entry with native ProjectX/TopstepX brackets:

- stop loss: `BOT_STOP_POINTS`, default 23 points
- take profit: `BOT_TAKE_PROFIT_POINTS`, default 40 points

For MNQ, `23` NQ points becomes `92` ticks because MNQ tick size is `0.25`.

## Safety Defaults

- Practice/sim mode: `TOPSTEPX_LIVE=false`
- Dry run: `BOT_DRY_RUN=true`
- Symbol: `MNQ`
- Contracts: `1`
- Max trades/day: `3`
- Minimum spacing: `20` bars
- No new entry if any open position exists
- Short signals before 8:00am ET blocked
- Round-level signals off by default

## Stop The Bot

Press `Ctrl-C` in the terminal.

If an order is open, manage or flatten it inside TopstepX.
