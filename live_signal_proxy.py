#!/usr/bin/env python3
"""
Live OHLCV signal proxy for the James strategy.

The research backtest uses footprint POC fields that are not available from
ordinary TopstepX minute bars. This module mirrors the TradingView proxy:
key-level touch + OHLCV pressure confirmation + conservative daily throttles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")


@dataclass
class SignalConfig:
    level_tolerance: float = 20.0
    stop_points: float = 23.0
    trim1_points: float = 40.0
    trim2_points: float = 70.0
    runner_points: float = 175.0
    first_signal_pressure: float = 0.45
    normal_pressure: float = 0.30
    max_trades_per_day: int = 3
    min_bars_between_signals: int = 20
    skip_shorts_before_0800: bool = True
    first_trade_longs_only: bool = False
    use_round_levels: bool = False


@dataclass
class SignalState:
    trades_today: int = 0
    last_signal_index: int | None = None
    current_day: str | None = None

    def roll_day(self, day: str) -> None:
        if self.current_day != day:
            self.current_day = day
            self.trades_today = 0
            self.last_signal_index = None


def normalize_bars(api_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bars = []
    for raw in api_bars:
        ts = raw.get("t") or raw.get("time") or raw.get("timestamp")
        if not ts:
            continue
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(NY)
        bars.append(
            {
                "time": dt,
                "open": float(raw.get("o", raw.get("open"))),
                "high": float(raw.get("h", raw.get("high"))),
                "low": float(raw.get("l", raw.get("low"))),
                "close": float(raw.get("c", raw.get("close"))),
                "volume": float(raw.get("v", raw.get("volume", 0)) or 0),
            }
        )
    return sorted(bars, key=lambda b: b["time"])


def detect_signal(
    bars: list[dict[str, Any]],
    state: SignalState,
    config: SignalConfig | None = None,
) -> dict[str, Any] | None:
    config = config or SignalConfig()
    if len(bars) < 60:
        return None

    bar = bars[-1]
    day = bar["time"].date().isoformat()
    state.roll_day(day)

    idx = len(bars) - 1
    if state.trades_today >= config.max_trades_per_day:
        return None
    if state.last_signal_index is not None and idx - state.last_signal_index < config.min_bars_between_signals:
        return None

    levels = build_levels(bars, config)
    if not levels:
        return None

    pressure = body_pressure(bar)
    min_pressure = config.first_signal_pressure if state.trades_today == 0 else config.normal_pressure
    if pressure < min_pressure:
        return None

    touched = find_touched_level(bar, levels, config.level_tolerance)
    if not touched:
        return None

    level_name, level_price = touched
    direction = "long" if bar["close"] > level_price else "short"
    if direction == "long" and bar["close"] <= bar["open"]:
        return None
    if direction == "short" and bar["close"] >= bar["open"]:
        return None
    if direction == "short" and config.first_trade_longs_only and state.trades_today == 0:
        return None
    if direction == "short" and config.skip_shorts_before_0800 and bar["time"].hour < 8:
        return None

    state.trades_today += 1
    state.last_signal_index = idx
    entry = bar["close"]
    return {
        "time": bar["time"].isoformat(),
        "direction": direction,
        "level_name": level_name,
        "level_price": round(level_price, 2),
        "entry_price": round(entry, 2),
        "stop_price": round(entry - config.stop_points if direction == "long" else entry + config.stop_points, 2),
        "t1_price": round(entry + config.trim1_points if direction == "long" else entry - config.trim1_points, 2),
        "t2_price": round(entry + config.trim2_points if direction == "long" else entry - config.trim2_points, 2),
        "runner_price": round(entry + config.runner_points if direction == "long" else entry - config.runner_points, 2),
        "pressure": round(pressure, 3),
        "trade_number": state.trades_today,
    }


def build_levels(bars: list[dict[str, Any]], config: SignalConfig) -> list[tuple[str, float]]:
    last = bars[-1]
    today = last["time"].date()
    prev_days = [b for b in bars if b["time"].date() < today]
    today_bars = [b for b in bars if b["time"].date() == today]
    levels: list[tuple[str, float]] = []

    prev_rth = [b for b in prev_days if 9 <= b["time"].hour <= 16]
    if prev_rth:
        levels.append(("PDH", max(b["high"] for b in prev_rth)))
        levels.append(("PDL", min(b["low"] for b in prev_rth)))

    premarket = [b for b in today_bars if b["time"].hour >= 4 and (b["time"].hour, b["time"].minute) < (9, 30)]
    if premarket:
        levels.append(("PMH", max(b["high"] for b in premarket)))
        levels.append(("PML", min(b["low"] for b in premarket)))

    or5 = [b for b in today_bars if (b["time"].hour, b["time"].minute) >= (9, 30) and (b["time"].hour, b["time"].minute) < (9, 35)]
    if or5:
        levels.append(("OR5H", max(b["high"] for b in or5)))
        levels.append(("OR5L", min(b["low"] for b in or5)))

    recent = bars[-120:]
    highs = [b["high"] for b in recent]
    lows = [b["low"] for b in recent]
    if len(highs) >= 20:
        levels.append(("EQH_PROXY", max(highs[-20:])))
        levels.append(("EQL_PROXY", min(lows[-20:])))

    if config.use_round_levels:
        close = last["close"]
        levels.append(("ROUND_50", round(close / 50.0) * 50.0))
        levels.append(("ROUND_100", round(close / 100.0) * 100.0))

    return levels


def find_touched_level(
    bar: dict[str, Any],
    levels: list[tuple[str, float]],
    tolerance: float,
) -> tuple[str, float] | None:
    matches = [
        (name, level, abs(bar["close"] - level))
        for name, level in levels
        if bar["low"] <= level + tolerance and bar["high"] >= level - tolerance
    ]
    if not matches:
        return None
    name, level, _ = min(matches, key=lambda item: item[2])
    return name, level


def body_pressure(bar: dict[str, Any]) -> float:
    rng = max(bar["high"] - bar["low"], 0.25)
    return abs(bar["close"] - bar["open"]) / rng
