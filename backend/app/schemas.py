"""Pydantic models สำหรับ request/response."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Candle(BaseModel):
    time: int  # unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float


class Quote(BaseModel):
    symbol: str
    price: float
    time: int
    change: float = 0.0
    change_percent: float = 0.0


class CandlesResponse(BaseModel):
    symbol: str
    timeframe: str
    candles: list[Candle]
    indicators: dict


class AnalyzeRequest(BaseModel):
    symbol: str
    timeframe: str = "1h"
    note: Optional[str] = Field(default=None, description="ข้อมูล/บริบทเพิ่มเติมจากผู้ใช้")


class AnalyzeResponse(BaseModel):
    symbol: str
    timeframe: str
    report: str
    source: Literal["claude", "rule-based"]
    disclaimer: str


class AlertRule(BaseModel):
    id: Optional[str] = None
    symbol: str
    kind: Literal["price_above", "price_below", "rsi_above", "rsi_below"]
    value: float
    timeframe: str = "1h"
    note: Optional[str] = None


class TriggeredAlert(BaseModel):
    rule_id: str
    symbol: str
    kind: str
    value: float
    observed: float
    time: int
    message: str
