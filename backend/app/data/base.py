"""Interface กลางของ data provider — สลับแหล่งข้อมูลได้โดยไม่แก้โค้ดส่วนอื่น."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas import Candle, Quote


class DataProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        """คืนรายการแท่งเทียนเรียงจากเก่า -> ใหม่."""

    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote:
        """คืนราคาล่าสุดของสัญลักษณ์."""
