"""OANDA v20 provider — ทอง/เงิน/forex ตรง TradingView (OANDA) เป๊ะ + เรียลไทม์.

ใช้เมื่อมี OANDA_API_TOKEN (สมัครบัญชี practice ฟรีที่ oanda.com → Manage API Access → Generate token).
account id ดึงอัตโนมัติถ้าไม่ได้ตั้ง OANDA_ACCOUNT_ID.

`RouterProvider` รวม OANDA (ทอง/เงิน/forex) + Yahoo (หุ้น/คริปโต) ในตัวเดียว — route ตามสัญลักษณ์.
"""
from __future__ import annotations

import time

import httpx

from app.data.base import DataProvider
from app.schemas import Candle, Quote

# tf ของแอป -> granularity ของ OANDA
_GRAN = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}

# โลหะมีค่า
_METAL = {
    "XAUUSD=X": "XAU_USD", "XAUUSD": "XAU_USD", "XAU/USD": "XAU_USD", "XAU=X": "XAU_USD",
    "GOLD": "XAU_USD", "GOLD=X": "XAU_USD", "GC=F": "XAU_USD",
    "XAGUSD=X": "XAG_USD", "XAGUSD": "XAG_USD", "XAG/USD": "XAG_USD", "XAG=X": "XAG_USD",
    "SILVER": "XAG_USD", "SI=F": "XAG_USD",
    "XPTUSD=X": "XPT_USD", "XPDUSD=X": "XPD_USD",
}

# สกุลเงินที่ OANDA รองรับ (ใช้ตรวจว่า pair นี้เป็น forex ของ OANDA ไหม)
_CCY = {"AUD", "CAD", "CHF", "CNH", "CZK", "DKK", "EUR", "GBP", "HKD", "HUF", "JPY",
        "MXN", "NOK", "NZD", "PLN", "SEK", "SGD", "THB", "TRY", "USD", "ZAR"}


def to_oanda_instrument(symbol: str) -> str | None:
    """แปลงสัญลักษณ์ของแอป -> instrument ของ OANDA. คืน None ถ้าไม่ใช่ของ OANDA (เช่น หุ้น/คริปโต)."""
    if not symbol:
        return None
    s = symbol.strip().upper()
    if s in _METAL:
        return _METAL[s]
    # รูปแบบ instrument ของ OANDA อยู่แล้ว เช่น EUR_USD, XAU_USD
    if "_" in s:
        a, _, b = s.partition("_")
        if a in _CCY | {"XAU", "XAG", "XPT", "XPD"} and b in _CCY:
            return f"{a}_{b}"
        return None
    # forex แบบ yahoo: EURUSD=X / USDJPY=X / EURUSD
    core = s[:-2] if s.endswith("=X") else s
    if len(core) == 6 and core.isalpha():
        a, b = core[:3], core[3:]
        if a in _CCY and b in _CCY:
            return f"{a}_{b}"
    return None


class OandaProvider(DataProvider):
    name = "oanda"

    def __init__(self, token: str, env: str = "practice", account_id: str = ""):
        self.token = token
        self.base = ("https://api-fxtrade.oanda.com" if env == "live"
                     else "https://api-fxpractice.oanda.com")
        self._account_id = account_id
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept-Datetime-Format": "UNIX",  # time กลับมาเป็น epoch seconds
        }

    async def _account(self, client: httpx.AsyncClient) -> str:
        if self._account_id:
            return self._account_id
        r = await client.get(f"{self.base}/v3/accounts")
        r.raise_for_status()
        accts = r.json().get("accounts") or []
        if not accts:
            raise RuntimeError("OANDA: ไม่พบบัญชี (token ผิด หรือยังไม่มี account)")
        self._account_id = accts[0]["id"]
        return self._account_id

    def _parse_candles(self, raw: list[dict]) -> list[Candle]:
        out: list[Candle] = []
        for c in raw:
            if not c.get("complete", True) and len(out) and False:
                continue
            m = c.get("mid") or {}
            try:
                out.append(Candle(
                    time=int(float(c["time"])),
                    open=float(m["o"]), high=float(m["h"]),
                    low=float(m["l"]), close=float(m["c"]),
                    volume=float(c.get("volume", 0) or 0),
                ))
            except (KeyError, ValueError, TypeError):
                continue
        return out

    async def _candles(self, instrument: str, granularity: str, count: int) -> list[Candle]:
        params = {"granularity": granularity, "count": str(min(count, 5000)), "price": "M"}
        async with httpx.AsyncClient(timeout=20.0, headers=self._headers) as client:
            r = await client.get(f"{self.base}/v3/instruments/{instrument}/candles", params=params)
            r.raise_for_status()
            return self._parse_candles(r.json().get("candles") or [])

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        inst = to_oanda_instrument(symbol)
        if not inst:
            return []
        return await self._candles(inst, _GRAN.get(timeframe, "H1"), limit)

    async def get_history(self, symbol: str, timeframe: str, max_bars: int = 8000) -> list[Candle]:
        inst = to_oanda_instrument(symbol)
        if not inst:
            return []
        return await self._candles(inst, _GRAN.get(timeframe, "D"), min(max_bars, 5000))

    async def get_quote(self, symbol: str) -> Quote:
        inst = to_oanda_instrument(symbol)
        if not inst:
            return Quote(symbol=symbol, price=0.0, time=int(time.time()))
        async with httpx.AsyncClient(timeout=10.0, headers=self._headers) as client:
            acct = await self._account(client)
            r = await client.get(f"{self.base}/v3/accounts/{acct}/pricing",
                                  params={"instruments": inst})
            r.raise_for_status()
            prices = r.json().get("prices") or []
            if not prices:
                return Quote(symbol=symbol, price=0.0, time=int(time.time()))
            p = prices[0]
            bid = float(p.get("closeoutBid") or 0)
            ask = float(p.get("closeoutAsk") or 0)
            price = round((bid + ask) / 2, 4) if bid and ask else (bid or ask)
            t = int(float(p.get("time") or time.time()))
        # หา % เปลี่ยนจากแท่งวันก่อนหน้า (เทียบ daily close)
        change = pct = 0.0
        try:
            daily = await self._candles(inst, "D", 2)
            if len(daily) >= 2 and daily[-2].close:
                change = round(price - daily[-2].close, 4)
                pct = round(change / daily[-2].close * 100, 2)
        except Exception:
            pass
        return Quote(symbol=symbol, price=price, time=t, change=change, change_percent=pct)

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        # bulk pricing ในครั้งเดียว
        mapping = {s: to_oanda_instrument(s) for s in symbols}
        insts = [i for i in mapping.values() if i]
        if not insts:
            return []
        async with httpx.AsyncClient(timeout=10.0, headers=self._headers) as client:
            acct = await self._account(client)
            r = await client.get(f"{self.base}/v3/accounts/{acct}/pricing",
                                  params={"instruments": ",".join(dict.fromkeys(insts))})
            r.raise_for_status()
            by_inst = {p["instrument"]: p for p in (r.json().get("prices") or [])}
        out: list[Quote] = []
        for sym, inst in mapping.items():
            p = by_inst.get(inst)
            if not p:
                continue
            bid = float(p.get("closeoutBid") or 0)
            ask = float(p.get("closeoutAsk") or 0)
            price = round((bid + ask) / 2, 4) if bid and ask else (bid or ask)
            out.append(Quote(symbol=sym, price=price,
                             time=int(float(p.get("time") or time.time()))))
        return out


class RouterProvider(DataProvider):
    """รวม OANDA (ทอง/เงิน/forex) + Yahoo (หุ้น/คริปโต) — route อัตโนมัติตามสัญลักษณ์.

    name = "yahoo" เพื่อให้ฟีเจอร์ค้นหา (เฉพาะ yahoo) ยังทำงาน.
    """
    name = "yahoo"

    def __init__(self, oanda: OandaProvider, yahoo: DataProvider):
        self.oanda = oanda
        self.yahoo = yahoo

    def _pick(self, symbol: str) -> DataProvider:
        return self.oanda if to_oanda_instrument(symbol) else self.yahoo

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        return await self._pick(symbol).get_candles(symbol, timeframe, limit)

    async def get_quote(self, symbol: str) -> Quote:
        return await self._pick(symbol).get_quote(symbol)

    async def get_history(self, symbol: str, timeframe: str, max_bars: int = 8000) -> list[Candle]:
        return await self._pick(symbol).get_history(symbol, timeframe, max_bars)

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        oa = [s for s in symbols if to_oanda_instrument(s)]
        ya = [s for s in symbols if not to_oanda_instrument(s)]
        merged: dict[str, Quote] = {}
        if oa:
            for q in await self.oanda.get_quotes(oa):
                merged[q.symbol] = q
        if ya:
            for q in await self.yahoo.get_quotes(ya):
                merged[q.symbol] = q
        return [merged[s] for s in symbols if s in merged]

    async def search_symbols(self, query: str, limit: int = 50) -> list[dict]:
        return await self.yahoo.search_symbols(query, limit)
