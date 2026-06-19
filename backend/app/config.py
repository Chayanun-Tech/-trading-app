"""ตั้งค่าระบบและเลือก data provider จาก environment variables."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# โหลด .env จากโฟลเดอร์ backend/ เสมอ ไม่ว่าจะสตาร์ทเซิร์ฟเวอร์จาก cwd ไหน
# (เดิมใช้ load_dotenv() เฉย ๆ ซึ่งหาไฟล์จาก cwd → ถ้าไม่ได้รันจาก backend/ จะตกเป็น mock + ไม่มีคีย์ LLM)
_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_ENV_PATH if _ENV_PATH.exists() else None)


class Settings:
    data_provider: str = os.getenv("DATA_PROVIDER", "mock").lower()
    finnhub_api_key: str = os.getenv("FINNHUB_API_KEY", "")
    # Financial Modeling Prep — แหล่งปัจจัยพื้นฐานหุ้นไทย/ต่างประเทศบนคลาวด์ (Yahoo บล็อก IP ดาต้าเซ็นเตอร์)
    fmp_api_key: str = os.getenv("FMP_API_KEY", "")
    # OANDA (ทอง/เงิน/forex ตรง TradingView OANDA เป๊ะ). ตั้ง token แล้วระบบ route ให้เอง
    oanda_api_token: str = os.getenv("OANDA_API_TOKEN", "")
    oanda_account_id: str = os.getenv("OANDA_ACCOUNT_ID", "")  # เว้นว่างได้ ระบบดึงอัตโนมัติ
    oanda_env: str = os.getenv("OANDA_ENV", "practice").lower()  # practice | live

    # ---------- ผู้ให้บริการ AI (เลือกได้: auto/anthropic/gemini/groq/openai) ----------
    llm_provider: str = os.getenv("LLM_PROVIDER", "auto").lower()
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")  # 2.0-flash โควตาฟรี = 0
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    tradingview_webhook_secret: str = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "change-me")

    # Gmail สำหรับส่งแจ้งเตือน (ตั้ง App Password ใน Google Account → Security → App Passwords)
    gmail_user: str = os.getenv("GMAIL_USER", "")
    gmail_app_password: str = os.getenv("GMAIL_APP_PASSWORD", "")
    alert_notify_email: str = os.getenv("ALERT_NOTIFY_EMAIL", "")
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

    # สัญลักษณ์ทั้งหมด พร้อม type: growth | value | dividend | cyclical | defensive | crypto | commodity | index
    sample_symbols = [
        # ── US Growth ──────────────────────────────────────────────────
        {"symbol": "AAPL",  "name": "Apple Inc.",           "market": "US",        "type": "growth",    "sector": "Technology"},
        {"symbol": "MSFT",  "name": "Microsoft",            "market": "US",        "type": "growth",    "sector": "Technology"},
        {"symbol": "NVDA",  "name": "NVIDIA",               "market": "US",        "type": "growth",    "sector": "Semiconductors"},
        {"symbol": "TSLA",  "name": "Tesla",                "market": "US",        "type": "growth",    "sector": "EV / Energy"},
        {"symbol": "META",  "name": "Meta Platforms",       "market": "US",        "type": "growth",    "sector": "Social Media"},
        {"symbol": "AMZN",  "name": "Amazon.com",           "market": "US",        "type": "growth",    "sector": "E-Commerce / Cloud"},
        {"symbol": "GOOGL", "name": "Alphabet Class A",     "market": "US",        "type": "growth",    "sector": "Search / Cloud"},
        {"symbol": "AMD",   "name": "Advanced Micro Devices","market": "US",       "type": "growth",    "sector": "Semiconductors"},
        {"symbol": "NFLX",  "name": "Netflix",              "market": "US",        "type": "growth",    "sector": "Streaming"},
        {"symbol": "PLTR",  "name": "Palantir Technologies","market": "US",        "type": "growth",    "sector": "AI / Data"},
        {"symbol": "COIN",  "name": "Coinbase Global",      "market": "US",        "type": "growth",    "sector": "Crypto Exchange"},
        # ── US Value ────────────────────────────────────────────────
        {"symbol": "JPM",   "name": "JPMorgan Chase",       "market": "US",        "type": "value",     "sector": "Banking"},
        {"symbol": "BAC",   "name": "Bank of America",      "market": "US",        "type": "value",     "sector": "Banking"},
        {"symbol": "V",     "name": "Visa",                 "market": "US",        "type": "value",     "sector": "Payments"},
        {"symbol": "MA",    "name": "Mastercard",           "market": "US",        "type": "value",     "sector": "Payments"},
        {"symbol": "WMT",   "name": "Walmart",              "market": "US",        "type": "value",     "sector": "Retail"},
        {"symbol": "COST",  "name": "Costco Wholesale",     "market": "US",        "type": "value",     "sector": "Retail"},
        # ── US Defensive / Dividend ────────────────────────────────
        {"symbol": "KO",    "name": "The Coca-Cola Company","market": "US",        "type": "dividend",  "sector": "Beverages"},
        {"symbol": "PEP",   "name": "PepsiCo",              "market": "US",        "type": "dividend",  "sector": "Beverages"},
        {"symbol": "MCD",   "name": "McDonald's",           "market": "US",        "type": "dividend",  "sector": "Fast Food"},
        {"symbol": "NKE",   "name": "Nike",                 "market": "US",        "type": "defensive", "sector": "Consumer"},
        {"symbol": "DIS",   "name": "Walt Disney",          "market": "US",        "type": "defensive", "sector": "Entertainment"},
        # ── US Cyclical ────────────────────────────────────────────
        {"symbol": "XOM",   "name": "Exxon Mobil",          "market": "US",        "type": "cyclical",  "sector": "Energy"},
        {"symbol": "CVX",   "name": "Chevron",              "market": "US",        "type": "cyclical",  "sector": "Energy"},
        # ── TH Growth ────────────────────────────────────────────
        {"symbol": "CPALL.BK",  "name": "CP All",           "market": "TH",        "type": "growth",    "sector": "Retail"},
        {"symbol": "ADVANC.BK", "name": "Advanced Info Service","market": "TH",    "type": "growth",    "sector": "Telecom"},
        {"symbol": "DELTA.BK",  "name": "Delta Electronics Thailand","market": "TH","type": "growth",   "sector": "Electronics"},
        {"symbol": "AOT.BK",    "name": "Airports of Thailand","market": "TH",     "type": "growth",    "sector": "Transport"},
        {"symbol": "BDMS.BK",   "name": "Bangkok Dusit Medical","market": "TH",    "type": "growth",    "sector": "Healthcare"},
        {"symbol": "TRUE.BK",   "name": "True Corporation", "market": "TH",        "type": "growth",    "sector": "Telecom"},
        {"symbol": "GULF.BK",   "name": "Gulf Energy Development","market": "TH",  "type": "growth",    "sector": "Energy"},
        {"symbol": "CPN.BK",    "name": "Central Pattana",  "market": "TH",        "type": "growth",    "sector": "Property"},
        # ── TH Value / Dividend ───────────────────────────────────
        {"symbol": "PTT.BK",    "name": "PTT",              "market": "TH",        "type": "value",     "sector": "Energy"},
        {"symbol": "KBANK.BK",  "name": "Kasikornbank",     "market": "TH",        "type": "dividend",  "sector": "Banking"},
        {"symbol": "SCB.BK",    "name": "SCB X",            "market": "TH",        "type": "dividend",  "sector": "Banking"},
        {"symbol": "BBL.BK",    "name": "Bangkok Bank",     "market": "TH",        "type": "dividend",  "sector": "Banking"},
        {"symbol": "KTB.BK",    "name": "Krung Thai Bank",  "market": "TH",        "type": "dividend",  "sector": "Banking"},
        # ── Crypto ───────────────────────────────────────────────
        {"symbol": "BTC-USD",  "name": "Bitcoin USD",       "market": "CRYPTO",    "type": "crypto",    "sector": "Store of Value"},
        {"symbol": "ETH-USD",  "name": "Ethereum USD",      "market": "CRYPTO",    "type": "crypto",    "sector": "Smart Contract"},
        {"symbol": "SOL-USD",  "name": "Solana USD",        "market": "CRYPTO",    "type": "crypto",    "sector": "Smart Contract"},
        {"symbol": "BNB-USD",  "name": "BNB USD",           "market": "CRYPTO",    "type": "crypto",    "sector": "Exchange"},
        {"symbol": "XRP-USD",  "name": "XRP USD",           "market": "CRYPTO",    "type": "crypto",    "sector": "Payments"},
        {"symbol": "DOGE-USD", "name": "Dogecoin USD",      "market": "CRYPTO",    "type": "crypto",    "sector": "Meme"},
        {"symbol": "ADA-USD",  "name": "Cardano USD",       "market": "CRYPTO",    "type": "crypto",    "sector": "Smart Contract"},
        # ── Commodity / FX / Index ───────────────────────────────
        {"symbol": "GC=F",    "name": "Gold Futures",       "market": "COMMODITY", "type": "commodity", "sector": "Precious Metal"},
        {"symbol": "XAUUSD=X","name": "Gold Spot USD",      "market": "FX",        "type": "commodity", "sector": "Precious Metal"},
        {"symbol": "SI=F",    "name": "Silver Futures",     "market": "COMMODITY", "type": "commodity", "sector": "Precious Metal"},
        {"symbol": "CL=F",    "name": "Crude Oil Futures",  "market": "COMMODITY", "type": "cyclical",  "sector": "Energy"},
        {"symbol": "^GSPC",   "name": "S&P 500",            "market": "INDEX",     "type": "index",     "sector": "US Broad"},
        {"symbol": "^IXIC",   "name": "Nasdaq Composite",   "market": "INDEX",     "type": "index",     "sector": "US Tech"},
        {"symbol": "^DJI",    "name": "Dow Jones Industrial Average","market": "INDEX","type": "index",  "sector": "US Blue Chip"},
        {"symbol": "^RUT",    "name": "Russell 2000",       "market": "INDEX",     "type": "index",     "sector": "US Small Cap"},
        {"symbol": "^SET.BK", "name": "SET Index",          "market": "TH",        "type": "index",     "sector": "TH Broad"},
    ]

    bitkub_pairs = [
        {"symbol": "BTC_THB", "name": "Bitcoin THB", "market": "BITKUB"},
        {"symbol": "ETH_THB", "name": "Ethereum THB", "market": "BITKUB"},
        {"symbol": "SOL_THB", "name": "Solana THB", "market": "BITKUB"},
        {"symbol": "XRP_THB", "name": "XRP THB", "market": "BITKUB"},
        {"symbol": "BNB_THB", "name": "BNB THB", "market": "BITKUB"},
        {"symbol": "ADA_THB", "name": "Cardano THB", "market": "BITKUB"},
        {"symbol": "DOGE_THB", "name": "Dogecoin THB", "market": "BITKUB"},
        {"symbol": "LINK_THB", "name": "Chainlink THB", "market": "BITKUB"},
        {"symbol": "DOT_THB", "name": "Polkadot THB", "market": "BITKUB"},
        {"symbol": "AVAX_THB", "name": "Avalanche THB", "market": "BITKUB"},
        {"symbol": "ATOM_THB", "name": "Cosmos THB", "market": "BITKUB"},
        {"symbol": "ARB_THB", "name": "Arbitrum THB", "market": "BITKUB"},
        {"symbol": "OP_THB", "name": "Optimism THB", "market": "BITKUB"},
        {"symbol": "NEAR_THB", "name": "NEAR THB", "market": "BITKUB"},
        {"symbol": "UNI_THB", "name": "Uniswap THB", "market": "BITKUB"},
        {"symbol": "AAVE_THB", "name": "Aave THB", "market": "BITKUB"},
        {"symbol": "USDT_THB", "name": "Tether THB", "market": "BITKUB"},
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
