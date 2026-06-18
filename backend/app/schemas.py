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
    indicator_params: Optional[dict] = Field(default=None, description="ปรับ period อินดิเคเตอร์")
    enabled_schools: Optional[list[str]] = Field(default=None, description="id ศาสตร์ที่ให้ร่วมประเมิน (None=ทั้งหมด)")
    weights: Optional[dict] = Field(default=None, description="น้ำหนักแต่ละศาสตร์ {id: weight}")


class BacktestRequest(BaseModel):
    """พารามิเตอร์ของท่าไม้ตาย (Signature Strategy) สำหรับ backtest ย้อนหลัง."""
    symbol: str
    timeframe: str = "1d"
    entry_threshold: int = Field(default=65, ge=51, le=95, description="คะแนนฉันทามติขั้นต่ำเพื่อเข้า (0-100)")
    rr_ratio: float = Field(default=1.8, ge=0.5, le=10, description="Risk:Reward (target = risk * นี้)")
    atr_mult: float = Field(default=1.5, ge=0.2, le=10, description="ระยะ stop = ATR * นี้")
    direction: Literal["both", "long", "short"] = "both"
    use_trend_filter: bool = Field(default=True, description="เข้า long เฉพาะเหนือ SMA200 / short เฉพาะใต้")
    min_directional_weight: float = Field(default=2.0, ge=0.5, le=12,
                                          description="น้ำหนักรวมขั้นต่ำของศาสตร์ที่เห็นพ้อง (สูง=ต้องหลายศาสตร์เห็นตรงกัน=เทรดน้อยลงแต่คัดกว่า)")
    max_hold_bars: int = Field(default=60, ge=3, le=500)
    indicator_params: Optional[dict] = None
    enabled_schools: Optional[list[str]] = None
    weights: Optional[dict] = None


class LiveSignalRequest(BaseModel):
    """สัญญาณ ณ ปัจจุบันของกลยุทธ์ (ใช้กฎเดียวกับ backtest) + คำนวณขนาดโพสิชัน."""
    symbol: str
    timeframe: str = "1d"
    entry_threshold: int = Field(default=65, ge=51, le=95)
    rr_ratio: float = Field(default=1.8, ge=0.5, le=10)
    atr_mult: float = Field(default=1.5, ge=0.2, le=10)
    direction: Literal["both", "long", "short"] = "both"
    use_trend_filter: bool = True
    min_directional_weight: float = Field(default=2.0, ge=0.5, le=12)
    indicator_params: Optional[dict] = None
    enabled_schools: Optional[list[str]] = None
    weights: Optional[dict] = None
    account_size: float = Field(default=1000.0, gt=0, description="ขนาดพอร์ต (ใช้คำนวณ position size)")
    risk_pct: float = Field(default=1.0, gt=0, le=20, description="ความเสี่ยงต่อไม้ (% ของพอร์ต)")


class AnalyzeResponse(BaseModel):
    symbol: str
    timeframe: str
    report: str
    source: Literal["claude", "rule-based"]
    disclaimer: str


class SchoolVerdict(BaseModel):
    """ผลประเมินของศาสตร์หนึ่ง = หนึ่งแถวในตารางความน่าจะเป็น."""
    id: str
    name: str
    category: str
    view: Literal["up", "down", "neutral"]
    signal: str  # buy / sell / wait / ...
    confidence: int = Field(ge=0, le=100, description="ความเชื่อมั่นของศาสตร์นี้ต่อมุมมอง (0-100)")
    rationale: str
    evaluator: Literal["python", "claude"]


class MultiSchoolReport(BaseModel):
    """ผลรวมการประเมินทุกศาสตร์ + ถ่วงน้ำหนักเป็นโอกาสขึ้น/ลง."""
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    input_mode: Literal["data", "image"]
    verdicts: list[SchoolVerdict]
    up_probability: int = Field(ge=0, le=100)
    down_probability: int = Field(ge=0, le=100)
    bias: str                       # Bullish / Bearish / Neutral (+ ความแรง)
    consensus_strength: str         # ฉันทามติแข็งแรง/ขัดแย้ง
    psychology_summary: str         # สรุปเชิงจิตวิทยากราฟ (แกนหลัก)
    suggested_plan: Optional[str] = None
    ai_enabled: bool
    disclaimer: str


class AnalyzeFundamentalsRequest(BaseModel):
    """คำขอวิเคราะห์สาย VI (ปัจจัยพื้นฐาน) ของหุ้นหนึ่งตัว."""
    symbol: str
    note: Optional[str] = Field(default=None, description="ข้อมูล/บริบทเพิ่มเติมจากผู้ใช้")
    enabled_schools: Optional[list[str]] = Field(default=None, description="id ด้านที่ให้ร่วมประเมิน (None=ทั้งหมด)")
    weights: Optional[dict] = Field(default=None, description="น้ำหนักแต่ละด้าน {id: weight}")


class ValueVerdict(BaseModel):
    """ผลประเมินเชิงคุณค่าของด้านหนึ่ง = หนึ่งแถวในตาราง VI."""
    id: str
    name: str
    category: str
    view: Literal["good", "fair", "poor"]
    signal: str  # undervalued / strong / weak / ...
    confidence: int = Field(ge=0, le=100, description="ความเชื่อมั่นของด้านนี้ต่อมุมมอง (0-100)")
    metric_value: Optional[str] = Field(default=None, description="ค่าเมตริกที่ใช้ตัดสิน")
    rationale: str
    evaluator: Literal["python", "claude"]


class ValueReport(BaseModel):
    """ผลรวมการประเมินสาย VI ทุกด้าน + ถ่วงน้ำหนักเป็นคะแนนคุณค่า/เกรด."""
    symbol: Optional[str] = None
    long_name: Optional[str] = None
    sector: Optional[str] = None
    verdicts: list[ValueVerdict]
    value_score: int = Field(ge=0, le=100, description="คะแนนคุณค่า/คุณภาพรวม (0-100)")
    quality_grade: str  # A / B / C / D / F
    consensus_strength: str
    summary: str
    key_metrics: dict
    ai_enabled: bool
    disclaimer: str


class ImageAnalyzeMeta(BaseModel):
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    note: Optional[str] = None


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
