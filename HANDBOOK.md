# 📘 คู่มือระบบฉบับสมบูรณ์ — AI Trade Assistant

> เอกสารนี้สรุป **ทุกอย่างของแอป** ไว้ที่เดียว: แอปคืออะไร, โครงสร้าง, บัญชีที่ใช้, ลิงก์สำคัญ,
> รหัสผ่าน/คีย์เก็บที่ไหน, วิธีรัน, วิธีอัปเดต/แก้บั๊ก เผื่อกลับมาดูในอนาคตแล้วเข้าใจได้ทันที
> (แม้จะลืมไปแล้ว หรือคนอื่นมาอ่านก็เข้าใจ)
>
> อัปเดตล่าสุด: 2026-06-15

---

## 1. แอปนี้คืออะไร (สรุป 30 วินาที)

**AI Trade Assistant** = แอปช่วยเทรดแบบ TradingView ที่:
- ดึงกราฟราคา หุ้น/คริปโต/ทอง/forex แบบเรียลไทม์
- วิเคราะห์กราฟ **แยกตาม 20 ศาสตร์** (Price Action, แท่งเทียน, Elliott, Fibonacci, Wyckoff, SMC, MMC ฯลฯ) แล้วสรุปเป็น % โอกาสขึ้น/ลง
- **Backtest** กลยุทธ์ย้อนหลังได้ + มี preset สำเร็จรูป (ชนะบ่อย/สไนเปอร์/ทอง MMC)
- **Go Live** แสดงสัญญาณเข้าออเดอร์ (Entry/TP/SL) บนกราฟ
- **Auto Trade Bot** (Bitkub) โหมด paper (จำลอง) — โหมดเงินจริงล็อกไว้แน่นหนา

**ขอบเขต:** วิเคราะห์ + ช่วยตัดสินใจ (โหมดเทรดจริงต้องเปิดเองหลายชั้น)

---

## 2. โครงสร้างระบบ (Architecture)

แอปมี 3 ส่วนแยกกัน อยู่คนละที่:

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  ผู้ใช้ (เบราว์เซอร์) │ ──▶ │  Hugging Face Spaces  │ ──▶ │   Supabase      │
│  มือถือ/คอม/แท็บ   │ ◀── │  (FastAPI + frontend) │ ◀── │  (Postgres DB)  │
└─────────────────┘     └──────────────────────┘     └─────────────────┘
                              │         ▲
              ราคาคริปโต ◀────┘         └──── โค้ดต้นทางจาก GitHub
              Binance WebSocket
              ราคาหุ้น/ทอง: Yahoo Finance
```

| ส่วน | ทำหน้าที่ | บริการที่ใช้ |
|------|-----------|--------------|
| **Frontend** | หน้าเว็บ (กราฟ, ปุ่ม, ตาราง) — ไฟล์เดียว `frontend/index.html` | เสิร์ฟจาก backend เดียวกัน |
| **Backend** | API, คำนวณอินดิเคเตอร์, วิเคราะห์, backtest, bot | FastAPI (Python) บน Hugging Face Spaces (Docker) |
| **Database** | เก็บข้อมูลถาวร (commands, orders, bot events, memory) | Supabase (Postgres) |
| **โค้ด** | เก็บ source code | GitHub |

---

## 3. 🔗 ลิงก์สำคัญทั้งหมด

### เว็บแอปจริง (เปิดใช้งาน)
- **แอป:** https://chayanun2541-ai-trade-assistant.hf.space

### Dashboard สำหรับจัดการ (ต้อง login)
| บริการ | ลิงก์ | ใช้ทำอะไร |
|--------|-------|-----------|
| Hugging Face Space | https://huggingface.co/spaces/Chayanun2541/ai-trade-assistant | ดู Logs, restart, ใส่ secret, ดู build |
| HF Settings (ของ Space) | Space → แท็บ Settings → Variables and secrets | จัดการคีย์/ค่า config |
| Supabase | https://supabase.com/dashboard/project/xiblqetehrnprycbkwyp | จัดการ DB, ดูตาราง, รัน SQL |
| GitHub repo | https://github.com/Chayanun-Tech/-trading-app | source code |

### หน้าจัดการ/ดูคีย์ (เว็บออกคีย์)
| คีย์ | เว็บออก/ดู/รีเซ็ตคีย์ |
|------|----------------------|
| Gemini API | https://aistudio.google.com/apikey |
| Groq API | https://console.groq.com/keys |
| HF access token (สำหรับ push) | https://huggingface.co/settings/tokens |
| Supabase DB connection string | Supabase dashboard → Connect → Session pooler |

---

## 4. 🔑 บัญชีที่ใช้ (ใครเป็นเจ้าของอะไร)

> ⚠️ **ไม่เก็บรหัสผ่านจริงในไฟล์นี้** (อันตราย เพราะไฟล์อยู่บน GitHub สาธารณะ)
> รหัสผ่านบัญชีให้จำเอง/เก็บใน password manager ส่วนตัว

| บริการ | บัญชี/เจ้าของ | หมายเหตุ |
|--------|--------------|----------|
| GitHub (เจ้าของ repo) | org **Chayanun-Tech** (เจ้าของหลัก = `chayanunju@scphpl.ac.th`) | repo ชื่อ `-trading-app` (มีขีดนำหน้า!) |
| GitHub (เครื่องนี้ login) | `chayanun250841` | เป็น collaborator สิทธิ์ Write บน repo; ใช้กับโปรเจกต์อื่นด้วย |
| Hugging Face | `Chayanun2541` | เจ้าของ Space |
| Supabase | (บัญชีที่สร้าง project xiblqetehrnprycbkwyp) | |
| Gemini / Google AI | บัญชี Google ส่วนตัว | |
| Groq | บัญชี Groq ส่วนตัว | |

---

## 5. 🔐 รหัสผ่าน/คีย์ — เก็บที่ไหน, เอามาจากไหน

แอปต้องใช้คีย์เหล่านี้ **เก็บไว้ 2 ที่:**
1. **ในเครื่อง (รันโลคอล):** ไฟล์ `backend/.env` — ไฟล์นี้ถูก gitignore ไม่ขึ้น GitHub
2. **บน production (HF):** Space → Settings → Variables and secrets

| ชื่อคีย์ | คืออะไร | เอามาจากไหน | จำเป็น? |
|---------|---------|-------------|---------|
| `DATA_PROVIDER` | แหล่งข้อมูลราคา | ตั้งเป็น `yahoo` (ถ้าไม่ตั้ง = `mock` ข้อมูลปลอม!) | ✅ ต้องมี |
| `GEMINI_API_KEY` | คีย์ AI หลัก | https://aistudio.google.com/apikey | ✅ |
| `GEMINI_MODEL` | รุ่น Gemini | ค่า: `gemini-2.5-flash-lite` (หรือ gemini-2.0-flash) | optional |
| `GROQ_API_KEY` | คีย์ AI สำรอง (เมื่อ Gemini โควต้าหมด) | https://console.groq.com/keys | optional |
| `GROQ_MODEL` | รุ่น Groq | ค่า: `llama-3.3-70b-versatile` | optional |
| `DATABASE_URL` | connection string ฐานข้อมูล Supabase | Supabase → Connect → **Session pooler** (port 5432) | optional* |
| `SUPABASE_URL` | URL โปรเจกต์ (ไม่ลับ) | `https://xiblqetehrnprycbkwyp.supabase.co` | optional |
| `OANDA_API_TOKEN` | ทอง/เงิน/forex ตรง TradingView OANDA เป๊ะ | oanda.com → practice ฟรี → Manage API Access | แนะนำ (ทอง) |
| `OANDA_ENV` | `practice` (demo) หรือ `live` | ตั้ง `practice` | optional |
| `BITKUB_API_KEY` / `_SECRET` | สำหรับ auto trade เงินจริง | Bitkub account | เฉพาะเทรดจริง |
| `FINNHUB_API_KEY` | (อนาคต) หุ้นเรียลไทม์ | https://finnhub.io | ยังไม่ใช้ |

> 💡 ใส่ `OANDA_API_TOKEN` แล้ว ทอง (XAUUSD=X) + forex จะ route ไป OANDA อัตโนมัติ (ตรง TradingView), หุ้นยังใช้ Yahoo, คริปโตยังใช้ Binance — ไม่ต้องตั้งอย่างอื่นเพิ่ม

\* `DATABASE_URL` ไม่ใส่ก็ได้ — แอปทำงานปกติ แต่จะ**ไม่บันทึกข้อมูลลง Supabase** (in-memory เท่านั้น)

> **สำคัญ:** ถ้าคีย์หลุด → ไป "เว็บออกคีย์" (ข้อ 3) กด regenerate ตัวใหม่ แล้วอัปเดตทั้งใน `.env` และ HF secret

---

## 6. 📊 แหล่งข้อมูลราคา (ทำไมกราฟเรียลไทม์/ไม่เรียลไทม์)

| สินทรัพย์ | แท่งกราฟย้อนหลัง | ราคาเรียลไทม์ (tick) | เรียลไทม์แท้? |
|-----------|------------------|----------------------|---------------|
| **คริปโต** (BTC/ETH/SOL…) | Yahoo Finance | **Binance WebSocket** | ✅ ใช่ |
| **หุ้น** (AAPL/NVDA/PTT.BK…) | Yahoo Finance | Yahoo poll ทุก 1 วิ | ⚠️ ไม่ (polling) |
| **ทอง/forex** (GC=F, XAUUSD=X) | Yahoo Finance | Yahoo poll ทุก 1 วิ | ⚠️ ไม่ (polling) |

- ทอง `XAUUSD=X` map อัตโนมัติเป็น `GC=F` (Yahoo ไม่มี XAUUSD ตรงๆ)
- **อยากให้หุ้น/ทองเรียลไทม์แท้:** ต้องต่อ **Finnhub** (หุ้น US) หรือ **Twelve Data** (ครบทุก asset) — ยังไม่ได้ทำ

---

## 7. 🗂️ โครงสร้างไฟล์

```
trading-app/
├── HANDBOOK.md          ← ไฟล์นี้ (คู่มือรวม)
├── BLUEPRINT.md         ← พิมพ์เขียวออกแบบระบบเดิม
├── DEPLOYMENT.md        ← ขั้นตอน deploy (Render เดิม)
├── memory.md            ← log การทำงาน/handoff ทุกครั้ง (อัปเดตเสมอ)
├── Dockerfile           ← สำหรับ build บน Hugging Face
├── render.yaml          ← config Render (สำรอง ไม่ได้ใช้แล้ว)
├── frontend/
│   ├── index.html       ← ทั้งแอปหน้าบ้านอยู่ไฟล์เดียวนี้ (กราฟ/ปุ่ม/JS)
│   └── config.js        ← ตั้ง API_BASE_URL (ว่าง = same-origin)
└── backend/
    ├── requirements.txt ← Python dependencies
    ├── .env             ← คีย์ลับ (ไม่ขึ้น git!)
    ├── app/
    │   ├── main.py          ← FastAPI routes ทั้งหมด + เสิร์ฟ frontend
    │   ├── config.py        ← อ่าน env/settings
    │   ├── analysis.py      ← วิเคราะห์ด้วย AI
    │   ├── engine.py        ← รวมผลหลายศาสตร์เป็นคะแนน
    │   ├── schools.py       ← ศาสตร์เชิงกฎ (Python)
    │   ├── backtest.py      ← engine backtest + live signal
    │   ├── autotrade.py     ← บอทเทรด Bitkub (paper/real)
    │   ├── bitkub.py        ← client Bitkub
    │   ├── indicators.py    ← คำนวณ EMA/RSI/MACD/BB ฯลฯ
    │   ├── vision.py        ← อ่านภาพกราฟด้วย AI
    │   ├── db.py            ← เชื่อม Supabase (optional)
    │   ├── llm.py           ← เรียก AI (Gemini/Groq/Claude/OpenAI)
    │   ├── data/            ← provider: yahoo.py, finnhub.py, mock.py
    │   └── knowledge/       ← ฐานความรู้ 17+ ศาสตร์ (.json)
    └── db/
        ├── schema.sql       ← สร้างตาราง Supabase (9 ตาราง)
        └── SUPABASE_SETUP.md
```

---

## 8. ▶️ วิธีรันในเครื่อง (local)

```powershell
cd "G:\Other computers\My Laptop\งานของชยานันต์\AI\ChayanunOperating\trading-app\backend"
.\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
เปิด http://127.0.0.1:8000/

- **แก้ backend (Python) → ต้องรีสตาร์ท uvicorn เสมอ** (ไม่มี --reload)
- **แก้ frontend (index.html) → กด Ctrl+Shift+R ในเบราว์เซอร์** (เคลียร์ cache)

---

## 9. 🚀 วิธีอัปเดตแอป (เมื่อแก้โค้ดแล้วอยากให้เว็บจริงเปลี่ยน)

โค้ดอยู่ 2 remote: `origin` (GitHub) และ `hf` (Hugging Face — ตัวนี้คือที่รันเว็บจริง)

```powershell
cd "G:\Other computers\My Laptop\งานของชยานันต์\AI\ChayanunOperating\trading-app"
git add <ไฟล์ที่แก้>
git commit -m "อธิบายว่าแก้อะไร"

# อัปเดตเว็บจริง (HF จะ build Docker ใหม่อัตโนมัติ ~2-5 นาที)
git push hf HEAD:main

# อัปเดต GitHub ด้วย (ให้ source ตรงกัน)
git push origin HEAD:main
```
- ตอน push `hf`: username=`Chayanun2541`, password=HF access token (Write) จาก https://huggingface.co/settings/tokens
- ดู build ที่ Space → แท็บ **Logs**

> หมายเหตุ: การ push ต้องรันในเทอร์มินัลปกติ (PowerShell) เอง — เครื่องมือ AI บล็อกการ push ขึ้น repo สาธารณะ

---

## 10. 🛠️ แก้ปัญหาที่เจอบ่อย (Troubleshooting)

| อาการ | สาเหตุ | วิธีแก้ |
|-------|--------|---------|
| มุมซ้ายบนขึ้น `provider: mock` กราฟมั่ว | ไม่ได้ตั้ง `DATA_PROVIDER=yahoo` | เพิ่ม Variable ใน HF Settings → restart |
| มุมซ้ายบนขึ้น `AI: rule-based` | ไม่มีคีย์ AI / โควต้าหมด | ใส่/regenerate `GEMINI_API_KEY` หรือ `GROQ_API_KEY` |
| Analyze ขึ้น error | Gemini โควต้าหมด (ฟรี 20/วัน) | ระบบ fallback ไป Groq อัตโนมัติ; หรือรอโควต้ารีเซ็ต |
| กราฟหุ้น/ทองว่าง บน HF | Yahoo บล็อก IP คลาวด์ | ต่อ provider อื่น (Finnhub/Twelve Data) |
| แก้โค้ดแล้วเว็บไม่เปลี่ยน | ยังไม่ push `hf` / HF ยัง build | push hf + รอ build เสร็จที่ Logs |
| Space หลับ (free tier) | ไม่มีคนเข้า 48 ชม. | เข้าเว็บใหม่ มันจะ wake เอง |

---

## 11. 🔒 โหมดเทรดเงินจริง (Auto Trade) — ความปลอดภัย

บอทเริ่มต้นเป็น **paper mode** (จำลอง ไม่ส่งคำสั่งจริง) เสมอ
การเปิดเทรดเงินจริงต้องผ่าน **3 ชั้น**:
1. ตั้ง env `BITKUB_REAL_TRADING_ENABLED=true`
2. ใส่ `BITKUB_API_KEY` + `BITKUB_API_SECRET`
3. พิมพ์ข้อความยืนยันเป๊ะๆ ใน UI: `I UNDERSTAND REAL ORDERS`

มี guard: max open positions, daily loss limit, order size cap, ไม่เข้าซ้ำแท่งเดิม

> ⚠️ ก่อนเทรดจริง ควรเพิ่ม: persist ลง DB, fee/slippage model, kill switch, เทส order เล็กๆ ก่อน

---

## 12. 📌 สิ่งที่ยังทำต่อได้ (Roadmap)

1. ใส่ `DATABASE_URL` ใน HF → เปิดบันทึกข้อมูลถาวรลง Supabase
2. ต่อ Finnhub/Twelve Data → หุ้น/ทอง เรียลไทม์แท้ระดับ tick
3. ปรับ Space เป็น hardware ที่ไม่หลับ (เสียเงิน) ถ้าต้องการ uptime 100%
4. แยก frontend ออกจาก index.html ไฟล์เดียว (ถ้าโตขึ้น)
5. paper trade → เก็บสถิติ 2-4 สัปดาห์ก่อนพิจารณาเทรดจริง

---

> 📝 **กฎประจำโปรเจกต์:** ทุกครั้งที่ทำงาน/แก้อะไร ให้บันทึก log ลง `memory.md` เสมอ (ลำดับเวลา + ทำอะไร + ไฟล์ไหน + ผลทดสอบ + ที่ค้าง)
