#!/usr/bin/env python3
"""
TopstepX practice automation runner for the James Level 1 signal proxy.

Defaults are intentionally conservative:
- practice/sim data (`TOPSTEPX_LIVE=false`)
- dry run on (`BOT_DRY_RUN=true`)
- 1 MNQ contract
- max 1 open position
- native stop-loss/take-profit brackets on every submitted market entry
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from live_signal_proxy import SignalConfig, SignalState, detect_signal, normalize_bars
from topstepx_client import TopstepXClient, TopstepXConfig, TopstepXError


ORDER_TYPE_LIMIT = 1
ORDER_TYPE_MARKET = 2
ORDER_TYPE_STOP = 4
SIDE_BUY = 0
SIDE_SELL = 1


def main() -> None:
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    parser = argparse.ArgumentParser(description="Run James proxy automation on TopstepX practice/sim.")
    parser.add_argument("--probe", action="store_true", help="Authenticate, print account/contract, then exit")
    parser.add_argument("--once", action="store_true", help="Run one data poll and signal check, then exit")
    parser.add_argument("--execute", action="store_true", help="Allow real practice orders if BOT_DRY_RUN=false")
    parser.add_argument("--poll-seconds", type=int, default=int(os.getenv("BOT_POLL_SECONDS", "20")))
    parser.add_argument("--lookback-minutes", type=int, default=int(os.getenv("BOT_LOOKBACK_MINUTES", "2880")))
    args = parser.parse_args()

    client = TopstepXClient(TopstepXConfig.from_env())
    client.authenticate()
    live = env_bool("TOPSTEPX_LIVE", False)
    account = resolve_account(client)
    contract = resolve_contract(client, os.getenv("TOPSTEPX_SYMBOL", "MNQ"), live)

    print(f"Connected to TopstepX. account={account.get('name', account.get('id'))} contract={contract.get('name')} live={live}")
    print(f"contract_id={contract['id']} tick_size={contract.get('tickSize')} tick_value={contract.get('tickValue')}")
    if args.probe:
        return

    dry_run = env_bool("BOT_DRY_RUN", True)
    if not dry_run and not args.execute:
        raise SystemExit("BOT_DRY_RUN=false requires --execute so accidental live submissions are harder.")

    state = SignalState()
    config = SignalConfig(
        level_tolerance=float(os.getenv("BOT_LEVEL_TOLERANCE", "20")),
        stop_points=float(os.getenv("BOT_STOP_POINTS", "23")),
        trim1_points=float(os.getenv("BOT_TAKE_PROFIT_POINTS", os.getenv("BOT_TRIM1_POINTS", "40"))),
        trim2_points=float(os.getenv("BOT_TRIM2_POINTS", "70")),
        runner_points=float(os.getenv("BOT_RUNNER_POINTS", "175")),
        first_signal_pressure=float(os.getenv("BOT_FIRST_SIGNAL_PRESSURE", "0.45")),
        normal_pressure=float(os.getenv("BOT_NORMAL_PRESSURE", "0.30")),
        max_trades_per_day=int(os.getenv("BOT_MAX_TRADES_PER_DAY", "3")),
        min_bars_between_signals=int(os.getenv("BOT_MIN_BARS_BETWEEN_SIGNALS", "20")),
        skip_shorts_before_0800=env_bool("BOT_SKIP_SHORTS_BEFORE_0800", True),
        first_trade_longs_only=env_bool("BOT_FIRST_TRADE_LONGS_ONLY", False),
        use_round_levels=env_bool("BOT_USE_ROUND_LEVELS", False),
    )

    print(f"mode={'DRY RUN' if dry_run else 'PRACTICE ORDER EXECUTION'}")
    while True:
        try:
            signal = poll_signal(client, contract["id"], live, args.lookback_minutes, state, config)
            if signal:
                handle_signal(client, account, contract, signal, dry_run)
            else:
                print(f"{datetime.now().isoformat(timespec='seconds')} no signal")
        except Exception as exc:
            print(f"ERROR: {exc}")

        if args.once:
            break
        time.sleep(args.poll_seconds)


def poll_signal(
    client: TopstepXClient,
    contract_id: str,
    live: bool,
    lookback_minutes: int,
    state: SignalState,
    config: SignalConfig,
) -> dict[str, Any] | None:
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(minutes=lookback_minutes)
    raw_bars = client.retrieve_bars(
        contract_id=contract_id,
        start_time=start.isoformat().replace("+00:00", "Z"),
        end_time=end.isoformat().replace("+00:00", "Z"),
        unit=2,
        unit_number=1,
        limit=min(20000, lookback_minutes),
        live=live,
        include_partial_bar=False,
    )
    bars = normalize_bars(raw_bars)
    return detect_signal(bars, state, config)


def handle_signal(
    client: TopstepXClient,
    account: dict[str, Any],
    contract: dict[str, Any],
    signal: dict[str, Any],
    dry_run: bool,
) -> None:
    account_id = int(account["id"])
    contract_id = str(contract["id"])
    tick_size = float(contract.get("tickSize") or os.getenv("TOPSTEPX_TICK_SIZE", "0.25"))
    size = int(os.getenv("TOPSTEPX_CONTRACTS", "1"))
    side = SIDE_BUY if signal["direction"] == "long" else SIDE_SELL
    stop_ticks = points_to_ticks(float(os.getenv("BOT_STOP_POINTS", "23")), tick_size)
    target_ticks = points_to_ticks(float(os.getenv("BOT_TAKE_PROFIT_POINTS", "40")), tick_size)

    open_positions = client.search_open_positions(account_id)
    if open_positions:
        print(f"Signal ignored because an open position already exists: {open_positions}")
        return

    print("SIGNAL", json.dumps(signal, sort_keys=True))
    if dry_run:
        print(
            "DRY RUN order:",
            json.dumps(
                {
                    "accountId": account_id,
                    "contractId": contract_id,
                    "type": ORDER_TYPE_MARKET,
                    "side": side,
                    "size": size,
                    "stopLossBracket": {"ticks": stop_ticks, "type": ORDER_TYPE_STOP},
                    "takeProfitBracket": {"ticks": target_ticks, "type": ORDER_TYPE_LIMIT},
                },
                sort_keys=True,
            ),
        )
        return

    response = client.place_order(
        account_id=account_id,
        contract_id=contract_id,
        side=side,
        size=size,
        order_type=ORDER_TYPE_MARKET,
        stop_loss_ticks=stop_ticks,
        take_profit_ticks=target_ticks,
        custom_tag=f"james-{int(time.time())}",
    )
    print("ORDER RESPONSE", json.dumps(response, sort_keys=True))


def resolve_account(client: TopstepXClient) -> dict[str, Any]:
    requested = os.getenv("TOPSTEPX_ACCOUNT_ID", "").strip()
    accounts = client.search_accounts(only_active=True)
    if requested:
        for account in accounts:
            if str(account.get("id")) == requested:
                return account
        raise TopstepXError(f"TOPSTEPX_ACCOUNT_ID={requested} was not found in active accounts")
    tradable = [a for a in accounts if a.get("canTrade", True) and a.get("isVisible", True)]
    if not tradable:
        raise TopstepXError(f"No tradable active accounts found: {accounts}")
    return tradable[0]


def resolve_contract(client: TopstepXClient, symbol: str, live: bool) -> dict[str, Any]:
    requested = os.getenv("TOPSTEPX_CONTRACT_ID", "").strip()
    if requested:
        return {"id": requested, "name": requested, "tickSize": float(os.getenv("TOPSTEPX_TICK_SIZE", "0.25"))}

    contracts = client.search_contracts(symbol, live=live)
    preferred_symbol_id = "F.US.MNQ" if symbol.upper().startswith("MNQ") else "F.US.ENQ"
    matches = [
        c for c in contracts
        if c.get("activeContract", True)
        and (
            str(c.get("symbolId", "")).upper() == preferred_symbol_id
            or str(c.get("name", "")).upper().startswith(symbol.upper())
        )
    ]
    if not matches:
        raise TopstepXError(f"No active {symbol} contract found. Response: {contracts}")
    return matches[0]


def points_to_ticks(points: float, tick_size: float) -> int:
    return max(1, int(round(points / tick_size)))


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    main()
