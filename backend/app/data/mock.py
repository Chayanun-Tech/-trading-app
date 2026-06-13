"""Provider จำลองข้อมูล (random walk) — ทำให้แอปรันได้ทันทีโดยไม่ต้องมี API key.

ข้อมูลเป็นการสังเคราะห์ ไม่ใช่ราคาจริง ใช้สำหรับสาธิตระบบเท่านั้น.
seed อิงจากชื่อสัญลักษณ์ เพื่อให้กราฟของแต่ละตัวคงที่พอประมาณระหว่างการเรียก.
"""
from __future__ import annotations

import math
import random
import time

from app.config import get_settings
from app.data.base import DataProvider
from app.schemas import Candle, Quote


class MockProvider(DataProvider):
    name = "mock"

    def _base_price(self, symbol: str) -> float:
        # ราคาเริ่มต้นอิงจาก hash ของชื่อ ให้แต่ละตัวต่างกัน
        h = sum(ord(c) for c in symbol)
        return 50 + (h % 250)

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        settings = get_settings()
        step = settings.timeframes.get(timeframe, 3600)
        rng = random.Random(f"{symbol}:{timeframe}")
        price = self._base_price(symbol)
        now = int(time.time())
        start = now - step * limit
        candles: list[Candle] = []
        for i in range(limit):
            t = start + i * step
            # แนวโน้มเบา ๆ + คลื่น + สุ่ม
            drift = math.sin(i / 14.0) * price * 0.01
            shock = rng.uniform(-1, 1) * price * 0.012
            o = price
            c = max(1.0, price + drift + shock)
            hi = max(o, c) * (1 + abs(rng.uniform(0, 0.006)))
            lo = min(o, c) * (1 - abs(rng.uniform(0, 0.006)))
            vol = abs(rng.gauss(1_000_000, 250_000))
            candles.append(
                Candle(time=t, open=round(o, 2), high=round(hi, 2),
                       low=round(lo, 2), close=round(c, 2), volume=round(vol))
            )
            price = c
        return candles

    async def get_quote(self, symbol: str) -> Quote:
        candles = await self.get_candles(symbol, "1m", 2)
        last = candles[-1]
        prev = candles[-2].close if len(candles) > 1 else last.open
        change = last.close - prev
        pct = (change / prev * 100) if prev else 0.0
        return Quote(symbol=symbol, price=last.close, time=last.time,
                     change=round(change, 2), change_percent=round(pct, 2))
