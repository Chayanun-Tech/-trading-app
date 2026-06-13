"""Provider หุ้นสหรัฐผ่าน Finnhub REST API (free tier).

ขอ API key ฟรีที่ https://finnhub.io
หมายเหตุ: free tier รองรับหุ้นสหรัฐเป็นหลัก; ข้อมูลแท่งเทียนย้อนหลังบางช่วงอาจจำกัด.
สำหรับหุ้นไทย realtime ต้องทำ provider แยกที่ต่อกับโบรก/ผู้ให้ข้อมูลที่มีสิทธิ์.
"""
from __future__ import annotations

import time

import httpx

from app.config import get_settings
from app.data.base import DataProvider
from app.schemas import Candle, Quote

# map timeframe -> resolution ของ Finnhub
_RESOLUTION = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "60", "1d": "D"}


class FinnhubProvider(DataProvider):
    name = "finnhub"
    BASE = "https://finnhub.io/api/v1"

    def __init__(self) -> None:
        self.key = get_settings().finnhub_api_key
        if not self.key:
            raise RuntimeError("ต้องตั้งค่า FINNHUB_API_KEY เมื่อ DATA_PROVIDER=finnhub")

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        settings = get_settings()
        step = settings.timeframes.get(timeframe, 3600)
        resolution = _RESOLUTION.get(timeframe, "60")
        now = int(time.time())
        # เผื่อช่วงเวลาให้ครอบคลุมจำนวนแท่งที่ต้องการ (x3 กันวันหยุด/ช่วงปิดตลาด)
        frm = now - step * limit * 3
        params = {"symbol": symbol, "resolution": resolution,
                  "from": frm, "to": now, "token": self.key}
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self.BASE}/stock/candle", params=params)
            r.raise_for_status()
            data = r.json()
        if data.get("s") != "ok":
            return []
        out: list[Candle] = []
        for t, o, h, low, c, v in zip(
            data["t"], data["o"], data["h"], data["l"], data["c"], data["v"]
        ):
            out.append(Candle(time=int(t), open=o, high=h, low=low, close=c, volume=v))
        return out[-limit:]

    async def get_quote(self, symbol: str) -> Quote:
        params = {"symbol": symbol, "token": self.key}
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self.BASE}/quote", params=params)
            r.raise_for_status()
            d = r.json()
        # c=current, pc=previous close, dp=percent change
        return Quote(
            symbol=symbol,
            price=d.get("c", 0.0),
            time=int(d.get("t", time.time())),
            change=round(d.get("c", 0) - d.get("pc", 0), 2),
            change_percent=round(d.get("dp", 0.0), 2),
        )
