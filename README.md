---
title: AI Trade Assistant
emoji: 📈
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# AI Trade Assistant — วิเคราะห์กราฟหลายสำนัก

แอปช่วยเทรดที่ประเมินกราฟ **แยกตามทุกศาสตร์การวิเคราะห์** (Price Action, แท่งเทียน, Chart Patterns,
Elliott Wave, Fibonacci, Harmonic, Wyckoff, SMC/ICT, Gann, อินดิเคเตอร์, แนวรับ-แนวต้าน, Volume, จิตวิทยาตลาด)
แล้วสรุปเป็น **ตารางความน่าจะเป็น** ว่าแต่ละสำนักมองกราฟจะไปทางไหน + โอกาสขึ้น/ลงรวม โดยยึด **จิตวิทยากราฟ** เป็นแกน
ขอบเขต: **วิเคราะห์ + ช่วยตัดสินใจเท่านั้น** (ไม่ส่งคำสั่งเทรดอัตโนมัติ)

> 📐 ดูพิมพ์เขียวระบบ ความเป็นไปได้ และ roadmap ที่ [`BLUEPRINT.md`](./BLUEPRINT.md)

## ✨ คุณสมบัติ
- **ฐานความรู้วิชาเทรดทุกศาสตร์** เป็น dataset (`backend/app/knowledge/*.json`) — ปรับ/เพิ่มได้
- **ประเมินหลายสำนักพร้อมกัน** → ตาราง: ศาสตร์ | มองขึ้น/ลง | สัญญาณ | ความเชื่อมั่น % | เหตุผล
- **ถ่วงน้ำหนักรวมเป็นโอกาสขึ้น vs ลง** + ระดับความเป็นฉันทามติ + สรุปจิตวิทยากราฟ
- **2 โหมดอินพุต:** (1) ใส่ symbol+timeframe ดึง OHLCV คำนวณเอง  (2) **อัปโหลดภาพ screenshot กราฟ** ให้ Claude vision อ่าน
- ศาสตร์เชิงตัวเลข (EMA/RSI/MACD/Bollinger/Stochastic/S-R/Volume/Dow/Divergence) คำนวณด้วย Python; ศาสตร์เชิง pattern ใช้ Claude ground ด้วยฐานความรู้
- กราฟแท่งเทียน + EMA ด้วย **TradingView Lightweight Charts** + ระบบ **แจ้งเตือน** + รับ **TradingView Webhook**
- **รันได้ทันทีโดยไม่ต้องมี API key** (provider จำลอง + ศาสตร์เชิงสูตรทำงานได้; ศาสตร์ AI จะแสดงเป็นกลางจนกว่าจะตั้งคีย์)

## 🚀 วิธีรัน

**Windows (ง่ายสุด):** ดับเบิลคลิก **`run.bat`** — ครั้งแรกติดตั้งให้อัตโนมัติ แล้วเปิด http://localhost:8000
> รันในเครื่องแล้ว **หุ้นไทย (.BK) ใช้งานได้เต็ม** ผ่าน Yahoo (บนเว็ปคลาวด์ Yahoo บล็อก IP ดาต้าเซ็นเตอร์ จึงได้แต่หุ้น US)

**หรือรันเอง:**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # (ทางเลือก) ใส่คีย์จริงในไฟล์ .env
uvicorn app.main:app --reload --port 8000
```

เปิดเบราว์เซอร์ที่ **http://localhost:8000** — เอกสาร API ที่ **http://localhost:8000/docs**

## 🔑 การตั้งค่า (ไฟล์ `.env`)

| ตัวแปร | ค่า | ผล |
|---|---|---|
| `DATA_PROVIDER` | `yahoo` (แนะนำ) / `mock` / `finnhub` | แหล่งข้อมูลราคา |
| `FINNHUB_API_KEY` | คีย์จาก finnhub.io | เปิดข้อมูลหุ้นสหรัฐจริง (เฉพาะ provider finnhub) |
| `FMP_API_KEY` | คีย์ฟรีจาก financialmodelingprep.com | ปัจจัยพื้นฐาน**หุ้นไทย/ต่างประเทศ**บนคลาวด์ (Yahoo บล็อก IP ดาต้าเซ็นเตอร์); หุ้น US ใช้ SEC EDGAR อยู่แล้ว |
| `ANTHROPIC_API_KEY` | คีย์จาก console.anthropic.com | เปิดการวิเคราะห์ด้วย Claude เต็มรูปแบบ |
| `ANTHROPIC_MODEL` | `claude-opus-4-8` | รุ่นโมเดล |
| `TRADINGVIEW_WEBHOOK_SECRET` | ค่าสุ่ม | ยืนยัน webhook จาก TradingView |

> ถ้าไม่ใส่ `ANTHROPIC_API_KEY` ระบบจะใช้สรุปแบบกฎ (rule-based) แทน และแจ้งให้ตั้งค่า

## 🔗 เชื่อม TradingView (สัญญาณอัตโนมัติ)
TradingView ไม่มี API ดึงผลวิเคราะห์ — ใช้ **Webhook Alert** แทน:
ใน Pine Script ตั้ง Alert → URL = `http://<server>/webhook/tradingview` → message เป็น JSON เช่น
```json
{"secret":"<TRADINGVIEW_WEBHOOK_SECRET>","symbol":"AAPL","signal":"buy"}
```

## ⚠️ ข้อจำกัด
- ข้อมูล `mock` เป็นการสังเคราะห์ ไม่ใช่ราคาจริง — ใช้สาธิตระบบเท่านั้น
- หุ้นไทย realtime ต้องเพิ่ม provider ที่ต่อกับโบรก/ผู้ให้ข้อมูลที่มีสิทธิ์
- Alert เก็บใน memory (หายเมื่อรีสตาร์ท) — โปรดักชันต้องใช้ DB
- ไม่มีระบบ auth — อย่านำขึ้นเซิร์ฟเวอร์สาธารณะโดยไม่เพิ่มระบบยืนยันตัวตน
- **รายงานเป็นข้อมูลประกอบการตัดสินใจ ไม่ใช่คำแนะนำลงทุน ไม่การันตีกำไร**
