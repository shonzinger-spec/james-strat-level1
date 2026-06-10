#!/usr/bin/env python3
"""
Small ProjectX/TopstepX REST client used by the live James runner.

Endpoint paths are centralized here so they can be adjusted from environment
variables if ProjectX changes a route name. The defaults match the public
ProjectX Gateway API shape used by TopstepX API access.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class TopstepXError(RuntimeError):
    """Raised when the TopstepX API returns an error or unexpected payload."""


@dataclass
class TopstepXConfig:
    username: str
    api_key: str
    base_url: str = "https://api.topstepx.com"
    timeout_seconds: int = 20

    @classmethod
    def from_env(cls) -> "TopstepXConfig":
        username = os.getenv("TOPSTEPX_USERNAME", "").strip()
        api_key = os.getenv("TOPSTEPX_API_KEY", "").strip()
        if not username or not api_key:
            raise TopstepXError("TOPSTEPX_USERNAME and TOPSTEPX_API_KEY are required")
        return cls(
            username=username,
            api_key=api_key,
            base_url=os.getenv("TOPSTEPX_BASE_URL", "https://api.topstepx.com").rstrip("/"),
            timeout_seconds=int(os.getenv("TOPSTEPX_TIMEOUT_SECONDS", "20")),
        )


class TopstepXClient:
    def __init__(self, config: TopstepXConfig):
        self.config = config
        self.token: str | None = None

    def authenticate(self) -> str:
        data = self._request(
            os.getenv("TOPSTEPX_AUTH_PATH", "/api/Auth/loginKey"),
            {
                "userName": self.config.username,
                "apiKey": self.config.api_key,
            },
            auth=False,
        )
        token = data.get("token") or data.get("accessToken") or data.get("jwt")
        if not token:
            raise TopstepXError(f"Authentication succeeded but no token was returned: {data}")
        self.token = str(token)
        return self.token

    def search_accounts(self, only_active: bool = True) -> list[dict[str, Any]]:
        payload = {"onlyActiveAccounts": only_active}
        data = self._request(os.getenv("TOPSTEPX_ACCOUNT_SEARCH_PATH", "/api/Account/search"), payload)
        return self._extract_list(data, "accounts")

    def search_contracts(self, query: str, live: bool = False) -> list[dict[str, Any]]:
        payload = {"searchText": query, "live": live}
        data = self._request(os.getenv("TOPSTEPX_CONTRACT_SEARCH_PATH", "/api/Contract/search"), payload)
        return self._extract_list(data, "contracts")

    def available_contracts(self, live: bool = False) -> list[dict[str, Any]]:
        data = self._request(
            os.getenv("TOPSTEPX_CONTRACT_AVAILABLE_PATH", "/api/Contract/available"),
            {"live": live},
        )
        return self._extract_list(data, "contracts")

    def retrieve_bars(
        self,
        contract_id: str,
        start_time: str,
        end_time: str,
        unit: int = 2,
        unit_number: int = 1,
        limit: int = 500,
        live: bool = False,
        include_partial_bar: bool = False,
    ) -> list[dict[str, Any]]:
        payload = {
            "contractId": contract_id,
            "live": live,
            "startTime": start_time,
            "endTime": end_time,
            "unit": unit,
            "unitNumber": unit_number,
            "limit": limit,
            "includePartialBar": include_partial_bar,
        }
        data = self._request(os.getenv("TOPSTEPX_BARS_PATH", "/api/History/retrieveBars"), payload)
        return self._extract_list(data, "bars")

    def search_open_positions(self, account_id: int) -> list[dict[str, Any]]:
        data = self._request(
            os.getenv("TOPSTEPX_POSITION_SEARCH_PATH", "/api/Position/searchOpen"),
            {"accountId": account_id},
        )
        return self._extract_list(data, "positions")

    def place_order(
        self,
        account_id: int,
        contract_id: str,
        side: int,
        size: int,
        order_type: int,
        limit_price: float | None = None,
        stop_price: float | None = None,
        trail_price: float | None = None,
        stop_loss_ticks: int | None = None,
        take_profit_ticks: int | None = None,
        custom_tag: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "accountId": account_id,
            "contractId": contract_id,
            "type": order_type,
            "side": side,
            "size": size,
        }
        if limit_price is not None:
            payload["limitPrice"] = limit_price
        if stop_price is not None:
            payload["stopPrice"] = stop_price
        if trail_price is not None:
            payload["trailPrice"] = trail_price
        if stop_loss_ticks is not None:
            payload["stopLossBracket"] = {"ticks": stop_loss_ticks, "type": 4}
        if take_profit_ticks is not None:
            payload["takeProfitBracket"] = {"ticks": take_profit_ticks, "type": 1}
        if custom_tag:
            payload["customTag"] = custom_tag
        return self._request(os.getenv("TOPSTEPX_ORDER_PLACE_PATH", "/api/Order/place"), payload)

    def close_position(self, account_id: int, contract_id: str) -> dict[str, Any]:
        return self._request(
            os.getenv("TOPSTEPX_POSITION_CLOSE_PATH", "/api/Position/closeContract"),
            {"accountId": account_id, "contractId": contract_id},
        )

    def _request(self, path: str, payload: dict[str, Any], auth: bool = True) -> dict[str, Any]:
        if auth and not self.token:
            self.authenticate()

        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        url = urllib.parse.urljoin(f"{self.config.base_url}/", path.lstrip("/"))
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise TopstepXError(f"HTTP {exc.code} from {url}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise TopstepXError(f"Could not reach {url}: {exc}") from exc

        if not raw.strip():
            return {}
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("success") is False:
            raise TopstepXError(f"API rejected request to {path}: {data}")
        if not isinstance(data, dict):
            raise TopstepXError(f"Unexpected response from {path}: {data!r}")
        return data

    @staticmethod
    def _extract_list(data: dict[str, Any], preferred_key: str) -> list[dict[str, Any]]:
        value = data.get(preferred_key)
        if value is None:
            value = data.get("data")
        if value is None:
            value = data.get("result")
        if isinstance(value, dict):
            for nested in ("items", preferred_key, "data"):
                if isinstance(value.get(nested), list):
                    value = value[nested]
                    break
        if not isinstance(value, list):
            raise TopstepXError(f"Expected list payload in {preferred_key}: {data}")
        return [item for item in value if isinstance(item, dict)]
