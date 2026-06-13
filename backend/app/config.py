"""ตั้งค่าระบบและเลือก data provider จาก environment variables."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    data_provider: str = os.getenv("DATA_PROVIDER", "mock").lower()
    finnhub_api_key: str = os.getenv("FINNHUB_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
    tradingview_webhook_secret: str = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "change-me")

    # timeframe ที่รองรับ (ป้ายชื่อ -> วินาทีต่อแท่ง สำหรับ provider จำลอง)
    timeframes = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }

    # สัญลักษณ์ตัวอย่าง (หุ้นสหรัฐ + ตัวอย่างหุ้นไทยแบบ .BK)
    sample_symbols = [
        {"symbol": "AAPL", "name": "Apple Inc.", "market": "US"},
        {"symbol": "MSFT", "name": "Microsoft", "market": "US"},
        {"symbol": "NVDA", "name": "NVIDIA", "market": "US"},
        {"symbol": "TSLA", "name": "Tesla", "market": "US"},
        {"symbol": "PTT.BK", "name": "PTT (ตัวอย่าง)", "market": "TH"},
        {"symbol": "KBANK.BK", "name": "Kasikornbank (ตัวอย่าง)", "market": "TH"},
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
