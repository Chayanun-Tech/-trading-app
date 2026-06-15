"""ตั้งค่าระบบและเลือก data provider จาก environment variables."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    data_provider: str = os.getenv("DATA_PROVIDER", "mock").lower()
    finnhub_api_key: str = os.getenv("FINNHUB_API_KEY", "")
    # OANDA (ทอง/เงิน/forex ตรง TradingView OANDA เป๊ะ). ตั้ง token แล้วระบบ route ให้เอง
    oanda_api_token: str = os.getenv("OANDA_API_TOKEN", "")
    oanda_account_id: str = os.getenv("OANDA_ACCOUNT_ID", "")  # เว้นว่างได้ ระบบดึงอัตโนมัติ
    oanda_env: str = os.getenv("OANDA_ENV", "practice").lower()  # practice | live

    # ---------- ผู้ให้บริการ AI (เลือกได้: auto/anthropic/gemini/groq/openai) ----------
    llm_provider: str = os.getenv("LLM_PROVIDER", "auto").lower()
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    tradingview_webhook_secret: str = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "change-me")
    bitkub_api_key: str = os.getenv("BITKUB_API_KEY", "")
    bitkub_api_secret: str = os.getenv("BITKUB_API_SECRET", "")
    bitkub_real_trading_enabled: bool = os.getenv("BITKUB_REAL_TRADING_ENABLED", "false").lower() == "true"
    supabase_url: str = os.getenv("SUPABASE_URL", "https://xiblqetehrnprycbkwyp.supabase.co")
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", "")
    database_url: str = os.getenv("DATABASE_URL", "")

    # ข้อมูลของแต่ละ provider: (มีคีย์?, model, base_url, รองรับการอ่านภาพ?)
    def _provider_info(self, name: str) -> dict | None:
        table = {
            "anthropic": (self.anthropic_api_key, self.anthropic_model, None, True),
            "gemini": (self.gemini_api_key, self.gemini_model,
                       "https://generativelanguage.googleapis.com/v1beta/openai/", True),
            "groq": (self.groq_api_key, self.groq_model,
                     "https://api.groq.com/openai/v1", False),
            "openai": (self.openai_api_key, self.openai_model, None, True),
        }
        if name not in table:
            return None
        key, model, base_url, vision = table[name]
        return {"provider": name, "api_key": key, "model": model,
                "base_url": base_url, "vision": vision,
                "kind": "anthropic" if name == "anthropic" else "openai"}

    def resolve_llm(self, exclude: set | None = None) -> dict:
        """เลือก provider ที่จะใช้จริง — ตาม LLM_PROVIDER หรือ auto (เจ้าแรกที่มีคีย์).

        exclude: ชุด provider ที่จะข้าม (ถ้า provider ไหนล้ม quota ให้ส่ง exclude={'gemini'} มาลอง groq)
        """
        exclude = exclude or set()
        if self.llm_provider != "auto":
            if self.llm_provider not in exclude:
                info = self._provider_info(self.llm_provider)
                if info:
                    return info
        # auto: ไล่ตามลำดับความเหมาะ (gemini สำรองด้วย groq)
        for name in ("anthropic", "gemini", "groq", "openai"):
            if name in exclude:
                continue
            info = self._provider_info(name)
            if info and info["api_key"]:
                return info
        # ไม่มีคีย์เลย
        return {"provider": "none", "api_key": "", "model": "rule-based",
                "base_url": None, "vision": False, "kind": "none"}

    def llm_enabled(self) -> bool:
        return bool(self.resolve_llm()["api_key"])

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
        {"symbol": "META", "name": "Meta Platforms", "market": "US"},
        {"symbol": "AMZN", "name": "Amazon.com", "market": "US"},
        {"symbol": "GOOGL", "name": "Alphabet Class A", "market": "US"},
        {"symbol": "AMD", "name": "Advanced Micro Devices", "market": "US"},
        {"symbol": "KO", "name": "The Coca-Cola Company", "market": "US"},
        {"symbol": "PEP", "name": "PepsiCo", "market": "US"},
        {"symbol": "MCD", "name": "McDonald's", "market": "US"},
        {"symbol": "NKE", "name": "Nike", "market": "US"},
        {"symbol": "DIS", "name": "Walt Disney", "market": "US"},
        {"symbol": "NFLX", "name": "Netflix", "market": "US"},
        {"symbol": "JPM", "name": "JPMorgan Chase", "market": "US"},
        {"symbol": "BAC", "name": "Bank of America", "market": "US"},
        {"symbol": "V", "name": "Visa", "market": "US"},
        {"symbol": "MA", "name": "Mastercard", "market": "US"},
        {"symbol": "WMT", "name": "Walmart", "market": "US"},
        {"symbol": "COST", "name": "Costco Wholesale", "market": "US"},
        {"symbol": "XOM", "name": "Exxon Mobil", "market": "US"},
        {"symbol": "CVX", "name": "Chevron", "market": "US"},
        {"symbol": "PLTR", "name": "Palantir Technologies", "market": "US"},
        {"symbol": "COIN", "name": "Coinbase Global", "market": "US"},
        {"symbol": "PTT.BK", "name": "PTT (ตัวอย่าง)", "market": "TH"},
        {"symbol": "KBANK.BK", "name": "Kasikornbank (ตัวอย่าง)", "market": "TH"},
        {"symbol": "CPALL.BK", "name": "CP All", "market": "TH"},
        {"symbol": "ADVANC.BK", "name": "Advanced Info Service", "market": "TH"},
        {"symbol": "DELTA.BK", "name": "Delta Electronics Thailand", "market": "TH"},
        {"symbol": "AOT.BK", "name": "Airports of Thailand", "market": "TH"},
        {"symbol": "BDMS.BK", "name": "Bangkok Dusit Medical Services", "market": "TH"},
        {"symbol": "SCB.BK", "name": "SCB X", "market": "TH"},
        {"symbol": "BBL.BK", "name": "Bangkok Bank", "market": "TH"},
        {"symbol": "KTB.BK", "name": "Krung Thai Bank", "market": "TH"},
        {"symbol": "TRUE.BK", "name": "True Corporation", "market": "TH"},
        {"symbol": "GULF.BK", "name": "Gulf Energy Development", "market": "TH"},
        {"symbol": "CPN.BK", "name": "Central Pattana", "market": "TH"},
        {"symbol": "BTC-USD", "name": "Bitcoin USD", "market": "CRYPTO"},
        {"symbol": "ETH-USD", "name": "Ethereum USD", "market": "CRYPTO"},
        {"symbol": "SOL-USD", "name": "Solana USD", "market": "CRYPTO"},
        {"symbol": "BNB-USD", "name": "BNB USD", "market": "CRYPTO"},
        {"symbol": "XRP-USD", "name": "XRP USD", "market": "CRYPTO"},
        {"symbol": "DOGE-USD", "name": "Dogecoin USD", "market": "CRYPTO"},
        {"symbol": "ADA-USD", "name": "Cardano USD", "market": "CRYPTO"},
        {"symbol": "GC=F", "name": "Gold Futures", "market": "COMMODITY"},
        {"symbol": "XAUUSD=X", "name": "Gold Spot USD", "market": "FX"},
        {"symbol": "SI=F", "name": "Silver Futures", "market": "COMMODITY"},
        {"symbol": "CL=F", "name": "Crude Oil Futures", "market": "COMMODITY"},
        {"symbol": "^GSPC", "name": "S&P 500", "market": "INDEX"},
        {"symbol": "^IXIC", "name": "Nasdaq Composite", "market": "INDEX"},
        {"symbol": "^DJI", "name": "Dow Jones Industrial Average", "market": "INDEX"},
        {"symbol": "^RUT", "name": "Russell 2000", "market": "INDEX"},
        {"symbol": "^SET.BK", "name": "SET Index", "market": "TH"},
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
