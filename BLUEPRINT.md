# พิมพ์เขียวระบบ: AI Trade Assistant (วิเคราะห์ + แจ้งเตือนแบบ realtime)

> เอกสารออกแบบระบบสำหรับแอปช่วยเทรดที่ใช้ AI วิเคราะห์กราฟตามเทมเพลตนักวิเคราะห์มืออาชีพ
> **ขอบเขตเวอร์ชันแรก:** วิเคราะห์ + แจ้งเตือนเท่านั้น (ไม่ส่งคำสั่งเทรดอัตโนมัติ)
> **ตลาดหลัก:** หุ้น (ไทย / สหรัฐ) — ออกแบบ provider เป็น adapter เพื่อขยายไป crypto/forex ได้

---

## 1. เป้าหมายและหลักการ

1. ดึงข้อมูลราคา (OHLCV) จากแหล่งจริง → คำนวณอินดิเคเตอร์เอง → ให้ Claude วิเคราะห์ตามเทมเพลต 8 หัวข้อ
2. ไม่พึ่ง "API ผลวิเคราะห์ของ TradingView" (ไม่มีจริง) — แต่รองรับ **TradingView Webhook Alert** เป็นแหล่งสัญญาณเสริม
3. เน้น **Risk Management** และแยก "ข้อเท็จจริงจากกราฟ" ออกจาก "สมมติฐานการวิเคราะห์"
4. ใส่ disclaimer ทุกครั้ง: เป็นข้อมูลประกอบการตัดสินใจ ไม่ใช่คำแนะนำลงทุน

---

## 2. ความจริงเรื่องการเชื่อม TradingView

TradingView **ไม่มี** Public API สำหรับดึงราคา/ผลวิเคราะห์มาใช้ในแอปอื่นอย่างเป็นทางการ สิ่งที่ใช้ได้:

| ช่องทาง | ใช้ทำอะไร | ข้อจำกัด |
|---|---|---|
| Lightweight Charts (ฟรี, โอเพนซอร์ส) | แสดงกราฟในแอปเรา | เราป้อนข้อมูลเอง |
| Charting Library / Advanced Charts | กราฟเต็มรูปแบบ + อินดิเคเตอร์ | ต้องขออนุมัติ + ทำ datafeed UDF เอง |
| **Webhook Alerts** (วิธีอัตโนมัติที่ถูกต้อง) | Pine Script Alert → ยิง webhook เข้า backend | ต้องเป็นแพ็กเกจ Pro+ |
| Widgets | ฝังวิดเจ็ต | ดูได้อย่างเดียว |

> ❌ **อย่าใช้** scraping (`tradingview-ta` ฯลฯ) ใน production — ผิด ToS และพังได้ทุกเมื่อ
> ✅ **ทางที่ยั่งยืน:** ดึง OHLCV จาก data provider จริง แล้วคำนวณอินดิเคเตอร์ + วิเคราะห์ด้วย Claude เอง

---

## 3. แหล่งข้อมูล (Data Providers)

ออกแบบเป็น **adapter pattern** — สลับ provider ได้โดยไม่แก้โค้ดส่วนอื่น

| ตลาด | Provider แนะนำ | Realtime | หมายเหตุ |
|---|---|---|---|
| หุ้นสหรัฐ | **Finnhub** (free tier), Polygon, Alpaca | ✅ (มี websocket) | MVP นี้รองรับ Finnhub REST |
| หุ้นไทย (SET) | ผ่านโบรก/พันธมิตรข้อมูล (Settrade), ข้อมูลดีเลย์จาก provider ทั่วไป | ⚠️ จำกัด | ต้องเป็น partner; ใส่เป็น adapter ภายหลัง |
| (ทดสอบ) | **MockProvider** (สังเคราะห์ random-walk) | ✅ | รันได้ทันทีโดยไม่ต้องมี key |

> หุ้นไทย realtime ต้องมีสัญญาข้อมูลกับโบรก/ตลาด — ใน MVP ใช้ Mock/Finnhub ไปก่อน แล้วเพิ่ม `SetProvider` เมื่อมีสิทธิ์เข้าถึง

---

## 4. สถาปัตยกรรมระบบ

```
┌──────────────────────────────────────────────────────────┐
│  FRONTEND (เว็บ)                                          │
│  Lightweight Charts + แดชบอร์ดสัญญาณ + รายงาน AI          │
└───────────────▲───────────────────────▲──────────────────┘
                │ REST / WebSocket        │
┌───────────────┴───────────────────────┴──────────────────┐
│  BACKEND (FastAPI)                                        │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐  │
│  │ Data Ingest  │ │ Indicator    │ │ AI Analysis      │  │
│  │ (providers)  │→│ Engine       │→│ (Claude API)     │  │
│  └──────────────┘ └──────────────┘ └──────────────────┘  │
│  ┌──────────────┐ ┌──────────────────────────────────┐   │
│  │ Alert Engine │ │ TradingView Webhook Receiver     │   │
│  └──────────────┘ └──────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
                │
        ┌───────┴────────┐
        │ DB (ภายหลัง)   │  TimescaleDB (ราคา) + Postgres (ผู้ใช้/alert/journal)
        └────────────────┘
```

### องค์ประกอบ
- **Data Ingestion** — ดึง OHLCV + quote จาก provider ที่เลือก
- **Indicator Engine** — คำนวณ EMA, SMA, RSI, MACD, swing structure, S/R (pure Python)
- **AI Analysis** — ส่งราคา+อินดิเคเตอร์เข้า Claude (`claude-opus-4-8`) พร้อม system prompt = persona นักวิเคราะห์ → ได้รายงาน 8 หัวข้อ
- **Alert Engine** — กฎแจ้งเตือน (ราคาทะลุแนว, RSI overbought/oversold ฯลฯ) ตรวจเป็นรอบ
- **Webhook Receiver** — รับ Alert จาก TradingView Pine Script

---

## 5. Tech Stack

| ส่วน | เทคโนโลยี | เหตุผล |
|---|---|---|
| Backend | Python + FastAPI | เด่นเรื่อง TA + เชื่อม Claude SDK ง่าย |
| AI | Anthropic SDK (`claude-opus-4-8`, adaptive thinking) | ออกรายงานโครงสร้างตามเทมเพลต |
| Indicators | Pure Python (ไม่งอก dependency) | เบา รันง่าย; โปรดักชันค่อยขยับเป็น pandas-ta/TA-Lib |
| Frontend | HTML + TradingView Lightweight Charts (CDN) | กราฟสวย ฟรี รันได้โดยไม่ต้อง build |
| Realtime | WebSocket (FastAPI) | สตรีม quote |
| DB (เฟสถัดไป) | TimescaleDB + PostgreSQL | time-series + ข้อมูลผู้ใช้ |

> โปรดักชันจริง: ย้าย indicator ไป `pandas-ta`/`TA-Lib`, เพิ่ม Redis pub/sub, แยก ingestion service, ต่อ DB

---

## 6. API (เวอร์ชัน MVP)

| Method | Path | หน้าที่ |
|---|---|---|
| GET | `/api/health` | เช็คสถานะ + provider ที่ใช้ |
| GET | `/api/symbols` | รายชื่อสัญลักษณ์ตัวอย่าง |
| GET | `/api/candles?symbol=&timeframe=&limit=` | OHLCV + อินดิเคเตอร์ |
| GET | `/api/quote?symbol=` | ราคาล่าสุด |
| POST | `/api/analyze` | วิเคราะห์ด้วย AI (body: `{symbol, timeframe}`) |
| GET/POST/DELETE | `/api/alerts` | จัดการกฎแจ้งเตือน |
| GET | `/api/alerts/triggered` | รายการที่ถูกทริกเกอร์ |
| POST | `/webhook/tradingview` | รับ Alert จาก TradingView |
| WS | `/ws/quotes?symbol=` | สตรีมราคา realtime |

---

## 7. ความเป็นไปได้ / ความเสี่ยง

| ด้าน | สถานะ | หมายเหตุ |
|---|---|---|
| เทคนิค | 🟢 ทำได้จริง | MVP นี้รันได้ทันที |
| ต้นทุนข้อมูลหุ้นไทย realtime | 🔴 สูง / ต้องเป็น partner | เริ่มที่ Mock/หุ้นสหรัฐก่อน |
| ต้นทุน AI | 🟡 ตาม token | ควบคุมด้วย caching + เลือกรุ่น |
| กฎหมาย | 🔴 ต้องระวัง | การให้คำแนะนำลงทุนถูกกำกับโดย ก.ล.ต. — ต้องมี disclaimer, ห้ามฟันธง/การันตี |

**หลักความปลอดภัย:** เริ่มที่ "วิเคราะห์ + แจ้งเตือน" (เวอร์ชันนี้) — เลี่ยงความเสี่ยงกฎหมาย/การเงินจาก auto-trade

---

## 8. Roadmap

- **เฟส 1 (MVP — โค้ดในรีโปนี้):** หุ้น 1–หลายตัว → กราฟ → อินดิเคเตอร์ → ปุ่มวิเคราะห์ด้วย AI → กฎแจ้งเตือนพื้นฐาน → รับ TV webhook
- **เฟส 2:** ต่อ DB จริง, trade journal, ระบบ alert แบบ background + แจ้งเตือนผ่าน LINE/Email/Push
- **เฟส 3:** auth + ผู้ใช้หลายคน + portfolio + risk module + provider หุ้นไทย realtime
- **เฟส 4:** ขยาย crypto/forex, vision analysis (อ่านภาพ screenshot), backtest

---

## 9. โครงสร้างโค้ดในรีโปนี้

```
.
├── BLUEPRINT.md            ← เอกสารนี้
├── README.md               ← วิธีรัน
├── backend/
│   ├── requirements.txt
│   ├── .env.example
│   └── app/
│       ├── main.py         ← FastAPI + routes + WebSocket
│       ├── config.py       ← ตั้งค่า/เลือก provider
│       ├── schemas.py      ← Pydantic models
│       ├── indicators.py   ← EMA/SMA/RSI/MACD/structure/S-R
│       ├── analysis.py     ← เชื่อม Claude + persona prompt
│       ├── alerts.py       ← กฎแจ้งเตือน
│       └── data/
│           ├── base.py     ← interface ของ provider
│           ├── mock.py     ← provider จำลอง (รันได้ไม่ต้องมี key)
│           └── finnhub.py  ← provider หุ้นสหรัฐ (REST)
└── frontend/
    └── index.html          ← กราฟ + ปุ่มวิเคราะห์ + แผง alert
```

---

## 10. ข้อจำกัดของ MVP (อ่านก่อนใช้งานจริง)

- ข้อมูล Mock เป็นการ **สังเคราะห์** ไม่ใช่ราคาจริง — ใช้สาธิตระบบเท่านั้น
- Alert engine เก็บใน memory (หายเมื่อรีสตาร์ท) — โปรดักชันต้องใช้ DB + คิว
- ไม่มี auth — อย่านำขึ้นเซิร์ฟเวอร์สาธารณะโดยไม่เพิ่มระบบยืนยันตัวตน
- รายงาน AI เป็น **ข้อมูลประกอบการตัดสินใจ ไม่ใช่คำแนะนำลงทุน**
