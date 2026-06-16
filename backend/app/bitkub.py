"""Bitkub REST client used by the auto-trade module.

The client keeps public market-data calls separate from secure order calls.
Secure calls are available, but the bot layer must still gate real trading.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.schemas import Candle, Quote


BITKUB_BASE_URL = "https://api.bitkub.com"


def _resolution(timeframe: str) -> str:
    return {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "1h": "60",
        "4h": "240",
        "1d": "1D",
    }.get(timeframe, "60")


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace("/", "_").replace("-", "_")


def _order_symbol(symbol: str) -> str:
    """Bitkub order examples use lower-case symbols in the secure payload."""
    return _normalize_symbol(symbol).lower()


@dataclass
class BitkubClient:
    api_key: str = ""
    api_secret: str = ""
    base_url: str = BITKUB_BASE_URL
    timeout: float = 12.0

    async def server_time(self) -> int:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(f"{self.base_url}/api/v3/servertime")
            res.raise_for_status()
            return int(res.json())

    async def symbols(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(f"{self.base_url}/api/v3/market/symbols")
            res.raise_for_status()
            payload = res.json()
        if isinstance(payload, dict):
            return payload.get("result") or []
        return []

    async def ticker(self, symbol: str | None = None) -> dict:
        params = {}
        if symbol:
            params["sym"] = _normalize_symbol(symbol)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(f"{self.base_url}/api/v3/market/ticker", params=params)
            res.raise_for_status()
            payload = res.json()
        norm = _normalize_symbol(symbol) if symbol else ""
        # v3 ticker คืนเป็น list: [{"symbol":"BTC_THB","last":"...","percent_change":"..."}]
        if isinstance(payload, list):
            if symbol:
                return next((r for r in payload if str(r.get("symbol", "")).upper() == norm.upper()),
                            payload[0] if payload else {})
            return payload
        if symbol and isinstance(payload, dict):
            return payload.get(norm) or payload.get(norm.lower()) or payload
        return payload if isinstance(payload, dict) else {}

    async def quote(self, symbol: str) -> Quote:
        norm = _normalize_symbol(symbol)
        row = await self.ticker(norm)
        if isinstance(row, list):
            row = next((r for r in row if str(r.get("symbol", "")).upper() == norm.upper()),
                       row[0] if row else {})
        last = float(row.get("last") or row.get("last_price") or row.get("close") or 0)
        change = float(row.get("change") or 0)
        change_pct = float(row.get("percent_change") or row.get("percentChange") or row.get("change_percent") or 0)
        return Quote(symbol=norm, price=last, change=change, change_percent=change_pct, time=int(time.time()))

    async def candles(self, symbol: str, timeframe: str = "1h", limit: int = 400) -> list[Candle]:
        now = int(time.time())
        seconds = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }.get(timeframe, 3600)
        # Ask for extra room because exchanges may return sparse arrays around the boundary.
        start = now - seconds * max(limit + 20, 120)
        params = {
            "symbol": _normalize_symbol(symbol),
            "resolution": _resolution(timeframe),
            "from": start,
            "to": now,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(f"{self.base_url}/tradingview/history", params=params)
            res.raise_for_status()
            payload = res.json()
        if payload.get("s") not in {"ok", "no_data"}:
            raise RuntimeError(f"Bitkub history error: {payload}")
        times = payload.get("t") or []
        candles = [
            Candle(
                time=int(t),
                open=float(payload["o"][i]),
                high=float(payload["h"][i]),
                low=float(payload["l"][i]),
                close=float(payload["c"][i]),
                volume=float((payload.get("v") or [0] * len(times))[i] or 0),
            )
            for i, t in enumerate(times)
        ]
        return candles[-limit:]

    def _sign(self, timestamp: int, method: str, path: str, query: str = "", body: str = "") -> str:
        raw = f"{timestamp}{method.upper()}{path}{query}{body}"
        return hmac.new(self.api_secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()

    async def secure_request(self, method: str, path: str, payload: dict | None = None,
                             params: dict | None = None) -> dict:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Bitkub API key/secret are not configured")
        timestamp = await self.server_time()
        query = f"?{urlencode(params)}" if params else ""
        body = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=False) if payload else ""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-BTK-APIKEY": self.api_key,
            "X-BTK-TIMESTAMP": str(timestamp),
            "X-BTK-SIGN": self._sign(timestamp, method, path, query, body),
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.request(method, f"{self.base_url}{path}{query}", content=body or None, headers=headers)
            res.raise_for_status()
            data = res.json()
        if isinstance(data, dict) and data.get("error") not in (None, 0):
            raise RuntimeError(f"Bitkub API error {data.get('error')}: {data}")
        return data

    async def place_bid(self, symbol: str, amount_thb: float, rate: float, order_type: str = "limit") -> dict:
        payload = {"sym": _order_symbol(symbol), "amt": amount_thb, "rat": rate, "typ": order_type}
        return await self.secure_request("POST", "/api/v3/market/place-bid", payload=payload)

    async def place_ask(self, symbol: str, amount_base: float, rate: float, order_type: str = "limit") -> dict:
        payload = {"sym": _order_symbol(symbol), "amt": amount_base, "rat": rate, "typ": order_type}
        return await self.secure_request("POST", "/api/v3/market/place-ask", payload=payload)

    async def open_orders(self, symbol: str) -> dict:
        return await self.secure_request("GET", "/api/v3/market/my-open-orders", params={"sym": _normalize_symbol(symbol)})
