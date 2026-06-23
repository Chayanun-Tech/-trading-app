"""แถบ 'เรดาร์ดักจับเทรนด์โลก' (Trend Radar) — สแกนสัญญาณ 'ต้นน้ำ' ของเทรนด์ใหม่
ก่อนที่มันจะกลายเป็นกระแสหลัก แล้วให้ AI จัดกลุ่ม + วิเคราะห์ว่าเทรนด์ไหนกำลังโผล่
มีศักยภาพดิสรัปอุตสาหกรรมเดิม (แบบกล้องดิจิทัลฆ่าฟิล์ม Kodak) ใครคือผู้ชนะ-ผู้แพ้
และนักลงทุนควรจับตาอะไร เพื่อให้ 'เป็นต้นเทรนด์ ไม่ใช่ผู้ตามกลางเทรนด์'.

แนวคิดหลัก: ข่าวกระแสหลัก = สัญญาณช้า (เทรนด์มาถึงกลางทางแล้ว) จึงไปดักที่ต้นน้ำ
ที่เทรนด์โผล่ก่อน:
- งานวิจัย (arXiv)      → เทคโนโลยีเกิดในแล็บก่อนออกตลาดหลายปี
- นักพัฒนา (Hacker News) → ของใหม่ถูกสร้าง/ถกเถียงก่อนนักลงทุนเห็น
- ผู้บริโภคหัวก้าว (Reddit) → adoption รุ่นแรก
- สื่อ (Google News RSS) → ใช้ 'ยืนยัน' ว่าเริ่มเข้ากระแสหลักหรือยัง (ตัวบอกระยะเทรนด์)

ทุกแหล่งฟรี ไม่ต้องใช้ API key. ดึงล้มเหลวบางแหล่ง = ข้ามไป ไม่ทำให้ทั้งคำขอพัง.
cache ผลลัพธ์ลงดิสก์ (TTL ~12 ชม.) เพราะสัญญาณเทรนด์ไวต่อเวลา.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

from app import llm
from app.config import get_settings

_CACHE_DIR = Path(__file__).resolve().parents[1].parent / "data" / "trends"
_TTL = 12 * 3600
_UA = {"User-Agent": "ChayanunTradingApp/1.0 (trend radar; contact chayanun250841@gmail.com)"}

# หมวดสแกนเริ่มต้น (โหมดกวาดกว้าง) — ครอบเทรนด์ที่มักดิสรัปตลาดทุน
_DEFAULT_TOPICS = [
    "artificial intelligence breakthrough", "biotech gene therapy",
    "energy storage battery", "robotics automation", "quantum computing",
    "space economy", "new materials", "fintech disruption",
    "climate technology", "semiconductor",
]
_ARXIV_CATS = ["cs.AI", "cs.LG", "cs.RO", "q-bio", "cond-mat.mtrl-sci", "quant-ph"]


# ---------------------------------------------------------------- harvesters --
async def _hn_signals(client: httpx.AsyncClient, query: str = "") -> list[dict]:
    """Hacker News (Algolia) — สตอรี่ใหม่ที่คะแนนพุ่ง = สิ่งที่ผู้สร้างกำลังตื่นเต้น.

    ถ้ามี query: ค้นตามคำ (เกณฑ์คะแนนต่ำลงเพราะธีมเจาะจงมีของน้อยกว่า)."""
    cutoff = int(time.time()) - 30 * 24 * 3600
    if query:
        params = httpx.QueryParams({
            "query": query, "tags": "story", "hitsPerPage": "50",
            "numericFilters": f"created_at_i>{cutoff},points>10"})
        url = f"https://hn.algolia.com/api/v1/search?{params}"
    else:
        cutoff = int(time.time()) - 14 * 24 * 3600
        url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story"
               f"&numericFilters=created_at_i>{cutoff},points>80&hitsPerPage=60")
    r = await client.get(url, headers=_UA)
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        title = (h.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title, "source": "Hacker News",
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            "ts": int(h.get("created_at_i") or 0),
            "score": int(h.get("points") or 0),
        })
    return out


async def _arxiv_signals(client: httpx.AsyncClient, query: str = "") -> list[dict]:
    """arXiv — เปเปอร์ล่าสุด = สัญญาณต้นน้ำที่สุด. ถ้ามี query: ค้นทุกหมวดตามคำ."""
    if query:
        params = httpx.QueryParams({
            "search_query": f"all:{query}", "sortBy": "relevance",
            "sortOrder": "descending", "max_results": "50"})
        url = f"http://export.arxiv.org/api/query?{params}"
    else:
        cats = "+OR+".join(f"cat:{c}" for c in _ARXIV_CATS)
        url = ("http://export.arxiv.org/api/query?search_query=" + cats +
               "&sortBy=submittedDate&sortOrder=descending&max_results=50")
    r = await client.get(url, headers=_UA)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.text)
    out = []
    for e in root.findall("a:entry", ns):
        title = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
        title = re.sub(r"\s+", " ", title)
        if not title:
            continue
        pub = e.findtext("a:published", default="", namespaces=ns) or ""
        out.append({
            "title": title, "source": "arXiv (งานวิจัย)",
            "url": e.findtext("a:id", default="", namespaces=ns),
            "ts": _parse_iso(pub), "score": 0,
        })
    return out


async def _reddit_signals(client: httpx.AsyncClient, query: str = "") -> list[dict]:
    """Reddit — โพสต์ที่กำลังมาแรง = adoption รุ่นแรก.

    ถ้ามี query: ค้นทั้ง Reddit ตามคำ; ไม่งั้นดึง top ของซับเทคโนโลยี/อนาคต."""
    if query:
        endpoints = [(f"https://www.reddit.com/search.json?q={httpx.QueryParams({'q': query})['q']}"
                      "&sort=top&t=month&limit=30", "Reddit")]
    else:
        subs = ["technology", "Futurology", "artificial", "singularity"]
        endpoints = [(f"https://www.reddit.com/r/{s}/top.json?t=week&limit=20",
                      f"Reddit r/{s}") for s in subs]
    out: list[dict] = []
    for url, label in endpoints:
        try:
            r = await client.get(url, headers=_UA)
            r.raise_for_status()
            for c in r.json().get("data", {}).get("children", []):
                d = c.get("data", {})
                title = (d.get("title") or "").strip()
                if not title:
                    continue
                sub = d.get("subreddit")
                out.append({
                    "title": title,
                    "source": f"Reddit r/{sub}" if query and sub else label,
                    "url": "https://www.reddit.com" + (d.get("permalink") or ""),
                    "ts": int(d.get("created_utc") or 0),
                    "score": int(d.get("score") or 0),
                })
        except Exception:  # noqa: BLE001 — แหล่งใดล้มก็ข้าม
            continue
    return out


async def _gnews_signals(client: httpx.AsyncClient, topics: list[str]) -> list[dict]:
    """Google News RSS — ใช้ 'ยืนยัน' ว่าธีมเริ่มเข้ากระแสหลักหรือยัง (ตัวชี้ระยะเทรนด์)."""
    out: list[dict] = []
    for topic in topics:
        try:
            q = httpx.QueryParams({"q": f"{topic} when:14d", "hl": "en-US",
                                   "gl": "US", "ceid": "US:en"})
            r = await client.get(f"https://news.google.com/rss/search?{q}", headers=_UA)
            r.raise_for_status()
            root = ET.fromstring(r.text)
            for item in list(root.iterfind(".//item"))[:8]:
                title = (item.findtext("title") or "").strip()
                if not title:
                    continue
                out.append({
                    "title": html.unescape(title), "source": "Google News",
                    "url": item.findtext("link") or "",
                    "ts": _parse_rss_date(item.findtext("pubDate") or ""),
                    "score": 0, "topic": topic,
                })
        except Exception:  # noqa: BLE001
            continue
    return out


def _parse_iso(s: str) -> int:
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError):
        return 0


def _parse_rss_date(s: str) -> int:
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return 0


# ------------------------------------------------------------------- LLM ------
SYSTEM_PROMPT = """คุณคือนักวิเคราะห์การลงทุนเชิงเทรนด์ (thematic / disruption investor)
สไตล์ผสม Cathie Wood (เทคโนโลยีดิสรัปต์) + Clayton Christensen (Innovator's Dilemma)
หน้าที่: รับ 'สัญญาณดิบ' จากต้นน้ำของโลก (งานวิจัย นักพัฒนา ชุมชนหัวก้าวหน้า ข่าว)
แล้วมองทะลุว่าเบื้องหลังสัญญาณกระจัดกระจายเหล่านี้ 'เทรนด์ใหญ่อะไรกำลังก่อตัว'
ก่อนที่มันจะกลายเป็นกระแสหลักที่ทุกคนรู้

โจทย์สำคัญของผู้ใช้: เขาไม่อยากเป็น 'ผู้ตาม' ที่รู้ตอนเทรนด์ถึงกลางทางแล้ว
เขาอยากจับ 'ต้นเทรนด์' และเข้าใจว่าเทรนด์ใหม่จะ 'ดิสรัป' อุตสาหกรรม/บริษัทเดิมตัวไหน
(เหมือนกล้องดิจิทัลฆ่าฟิล์ม Kodak, สตรีมมิงฆ่าร้านเช่าวิดีโอ)

หลักการวิเคราะห์:
- จัดกลุ่มสัญญาณที่ปรากฏข้าม 'หลายแหล่งอิสระ' และ 'เพิ่งเกิด/กำลังเร่งตัว' = เทรนด์ที่กำลังโผล่จริง
  (สัญญาณที่มาจากแหล่งเดียวหรือเป็นข่าวเก่า = น้ำหนักน้อย)
- ประเมิน 'ระยะของเทรนด์' ตามสัญญาณ: ถ้าเห็นแต่ในงานวิจัย/นักพัฒนา = ต้นเทรนด์ (โอกาสสูงสุด)
  ถ้าข่าวกระแสหลักพูดกันทั่วแล้ว = กลาง/ปลายเทรนด์ (มาช้าไปแล้ว)
- ทุกเทรนด์ต้องตอบให้ได้ว่า 'ใครได้ ใครเสีย': อุตสาหกรรม/บริษัทเดิมที่ถูกดิสรัป (ผู้แพ้)
  และผู้ได้ประโยชน์ (ผู้ชนะ) รวมถึง 'ผู้ขายจอบเสียม' (picks & shovels — คนขายโครงสร้างพื้นฐานให้เทรนด์)
- ระบุหุ้น/บริษัทที่จับต้องได้จริง (ใส่ ticker ถ้ารู้) แต่ถ้าไม่มั่นใจอย่าเดามั่ว ใส่ประเภทธุรกิจแทน

หลักความซื่อสัตย์:
- คุณคือ 'การวิเคราะห์/สมมติฐาน' ไม่ใช่คำทำนายที่แน่นอน เป็นกลาง ไม่เชียร์ซื้อขาย
- ถ้าสัญญาณยังบางเบาให้บอกตรง ๆ ว่าเป็นเทรนด์ที่ยังไม่ชัด อย่าปั้นเทรนด์ปลอม

กฎภาษา (สำคัญสูงสุด):
- ทุกข้อความตอบเป็นภาษาไทยที่อ่านรู้เรื่อง
- ศัพท์เทคนิค/ชื่อเฉพาะ/ticker ภาษาอังกฤษ เก็บไว้ได้ แต่ถ้าเป็นวลีอังกฤษให้ใส่คำแปลไทยในวงเล็บต่อท้าย

ข้อบังคับ: ตอบเป็น JSON เท่านั้น ห้ามมีข้อความนอก JSON"""

_OUTPUT_CONTRACT = """รูปแบบ JSON ที่ต้องคืน (เท่านั้น):
{
  "overview": "<ภาพรวม 2-4 ประโยค: ตอนนี้โลกกำลังหมุนไปทางไหน ธีมใหญ่อะไรกำลังก่อตัวจากสัญญาณ>",
  "trends": [
    {
      "name": "<ชื่อเทรนด์สั้น กระชับ เช่น 'AI Agents เข้าแทนแรงงานความรู้'>",
      "stage": "<ต้นเทรนด์ | กลางเทรนด์ | ปลายเทรนด์>",
      "stage_reason": "<ทำไมจัดให้อยู่ระยะนี้ อ้างอิงว่าสัญญาณมาจากชั้นไหน (วิจัย/นักพัฒนา/ข่าว) 1-2 ประโยค>",
      "momentum": <โมเมนตัม/ความเร่ง 1-5 (5=เร่งแรงสุด)>,
      "conviction": <ความมั่นใจว่าเป็นเทรนด์จริง 1-5 (ดูจากจำนวนแหล่งอิสระที่ยืนยัน)>,
      "horizon": "<กรอบเวลาที่คาดว่าจะส่งผลชัด เช่น '6-18 เดือน', '2-5 ปี'>",
      "summary": "<เทรนด์นี้คืออะไร กำลังเกิดอะไรขึ้น 2-4 ประโยค>",
      "why_now": "<ทำไมเทรนด์นี้ถึงเร่งตัว 'ตอนนี้' มีอะไรปลดล็อก (ต้นทุนลด/เทคโนโลยีสุก/กฎหมายเปลี่ยน) 1-3 ประโยค>",
      "disruption": {
        "incumbents_at_risk": [
          {"who": "<อุตสาหกรรม/ประเภทบริษัท/บริษัทเดิมที่เสี่ยงถูกดิสรัป (ใส่ ticker ถ้ารู้)>",
           "why": "<ถูกดิสรัปอย่างไร เหมือน Kodak ตรงไหน 1-2 ประโยค>"}
        ],
        "analogy": "<เทียบกับเหตุการณ์ดิสรัปในอดีตที่คล้ายกัน เช่น 'ดิจิทัลฆ่าฟิล์ม', 'สมาร์ตโฟนฆ่ากล้องคอมแพ็กต์'>"
      },
      "winners": [
        {"who": "<บริษัท/กลุ่มที่ได้ประโยชน์ (ใส่ ticker ถ้ารู้)>",
         "role": "<ผู้นำเทรนด์ | ผู้ขายจอบเสียม (picks&shovels) | ผู้ปรับตัวได้>",
         "why": "<ได้ประโยชน์อย่างไร 1 ประโยค>"}
      ],
      "watch_for": ["<สัญญาณ/เหตุการณ์ที่ถ้าเกิดแปลว่าเทรนด์นี้ของจริงและเร่งขึ้น 2-4 ข้อสั้น ๆ>"],
      "risks": "<อะไรที่อาจทำให้เทรนด์นี้ไม่เกิด/ช้า/ตายกลางทาง 1-2 ประโยค>",
      "evidence": ["<อ้างหัวข้อสัญญาณดิบจริงที่ทำให้สรุปเทรนด์นี้ 2-4 ข้อ ลอกหัวข้อมาสั้น ๆ>"]
    }
  ],
  "wildcards": [
    {"name": "<สัญญาณแปลก/เทรนด์เล็กที่ยังไม่ชัดแต่ถ้าจริงจะพลิกเกม>",
     "note": "<ทำไมน่าจับตา 1-2 ประโยค>"}
  ],
  "bottom_line": "<สรุปปิดท้าย 2-4 ประโยค: ถ้าจะเป็น 'ต้นเทรนด์' ตอนนี้ควรโฟกัสธีมไหนมากสุด และเริ่มศึกษาอะไรก่อน>"
}

ข้อกำหนด (สำคัญ — เพื่อไม่ให้คำตอบยาวเกินจนถูกตัด):
- คืน trends 'ไม่เกิน 5 อัน' เรียงจาก (momentum × conviction) มากไปน้อย — เลือกเฉพาะเทรนด์ที่ชัดสุด
  ให้น้ำหนักเทรนด์ 'ต้นเทรนด์' ที่มีหลายแหล่งยืนยัน
- แต่ละเทรนด์: incumbents_at_risk 1-2 อัน, winners 1-3 อัน, watch_for/evidence อย่างละ 2-3 ข้อ
- เขียนกระชับ ตรงประเด็น ทุกฟิลด์คำอธิบายไม่เกิน 2 ประโยค
- evidence ต้องอ้างจากหัวข้อสัญญาณดิบที่ให้มาเท่านั้น ห้ามแต่งหัวข้อที่ไม่มี
- wildcards ไม่เกิน 3 อัน; ถ้าสัญญาณไม่พอจะสรุปเป็นเทรนด์ ให้ใส่ใน wildcards แทน อย่าปั้นเป็น trend เต็ม
- ตอบ JSON ให้ 'ครบและปิดวงเล็บสมบูรณ์' เสมอ ถ้าจะยาวเกินให้ลดจำนวน trends ลง"""


def _safe_topic(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9ก-๙ ]+", "", s).strip()[:80]


def _cache_path(topic: str) -> Path:
    key = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_") or "global"
    return _CACHE_DIR / f"radar_{key}.json"


def _extract_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        t = t.lstrip("json").strip("` \n")
    start, end = t.find("{"), t.rfind("}")
    if start == -1:
        raise ValueError("ไม่พบ JSON ในคำตอบของโมเดล")
    body = t[start:end + 1] if end > start else t[start:]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return _salvage_json(t[start:])


def _salvage_json(s: str) -> dict:
    """กู้ JSON ที่ถูกตัดกลางคัน: ตัดถึงตัวอักษรที่ปลอดภัยล่าสุด แล้วปิดวงเล็บที่ค้างอยู่."""
    depth, in_str, esc, last_safe = 0, False, False, -1
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth <= 2:
            last_safe = i  # ขอบเขตระหว่างรายการระดับบน — ตัดตรงนี้ได้ปลอดภัย
    if last_safe == -1:
        raise ValueError("กู้ JSON ที่ถูกตัดไม่สำเร็จ")
    frag = s[:last_safe]
    # ปิดสตริง/วงเล็บที่ค้าง (ตามชนิดที่เปิดไว้)
    stack: list[str] = []
    in_str, esc = False, False
    for ch in frag:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack:
            stack.pop()
    if in_str:
        frag += '"'
    frag += "".join(reversed(stack))
    return json.loads(frag)


def _num(v, lo, hi):
    try:
        n = round(float(v), 1)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, n))


def _s(v, n):
    return str(v)[:n] if v is not None else None


def _list(d, k):
    v = (d or {}).get(k)
    return v if isinstance(v, list) else []


def _clean(payload: dict) -> dict:
    trends = []
    for t in _list(payload, "trends")[:8]:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        stage = str(t.get("stage", "")).strip()
        stage = ("ต้นเทรนด์" if "ต้น" in stage or "early" in stage.lower() else
                 "ปลายเทรนด์" if "ปลาย" in stage or "late" in stage.lower() else
                 "กลางเทรนด์" if stage else None)
        dis = t.get("disruption") or {}
        incumbents = [{"who": _s(x.get("who"), 120), "why": _s(x.get("why"), 280)}
                      for x in _list(dis, "incumbents_at_risk")[:6]
                      if isinstance(x, dict) and x.get("who")]
        winners = []
        for w in _list(t, "winners")[:8]:
            if not isinstance(w, dict) or not w.get("who"):
                continue
            winners.append({"who": _s(w.get("who"), 120),
                            "role": _s(w.get("role"), 60),
                            "why": _s(w.get("why"), 240)})
        trends.append({
            "name": _s(t.get("name"), 120),
            "stage": stage,
            "stage_reason": _s(t.get("stage_reason"), 280),
            "momentum": _num(t.get("momentum"), 1, 5),
            "conviction": _num(t.get("conviction"), 1, 5),
            "horizon": _s(t.get("horizon"), 60),
            "summary": _s(t.get("summary"), 600),
            "why_now": _s(t.get("why_now"), 400),
            "disruption": {
                "incumbents_at_risk": incumbents,
                "analogy": _s(dis.get("analogy"), 200),
            },
            "winners": winners,
            "watch_for": [_s(x, 160) for x in _list(t, "watch_for")[:5] if x],
            "risks": _s(t.get("risks"), 300),
            "evidence": [_s(x, 200) for x in _list(t, "evidence")[:4] if x],
        })

    wildcards = [{"name": _s(w.get("name"), 120), "note": _s(w.get("note"), 280)}
                 for w in _list(payload, "wildcards")[:6]
                 if isinstance(w, dict) and w.get("name")]

    return {
        "overview": _s(payload.get("overview"), 800),
        "trends": trends,
        "wildcards": wildcards,
        "bottom_line": _s(payload.get("bottom_line"), 1000),
    }


def _signals_digest(signals: list[dict], limit: int = 130) -> str:
    """ทำสัญญาณดิบเป็นข้อความให้ LLM อ่าน: [แหล่ง · อายุ · คะแนน] หัวข้อ."""
    now = time.time()
    lines = []
    for s in signals[:limit]:
        age_days = int((now - s.get("ts", now)) / 86400) if s.get("ts") else "?"
        score = f" ·{s['score']}pts" if s.get("score") else ""
        lines.append(f"- [{s['source']} · {age_days}d{score}] {s['title']}")
    return "\n".join(lines)


_ASCII_RE = re.compile(r"^[\x00-\x7f]+$")


async def _topic_to_keywords(topic: str, exclude: set) -> dict:
    """แปลงธีม (ไทย/อังกฤษ) เป็นคำค้นภาษาอังกฤษ เพราะแหล่งต้นน้ำเป็นภาษาอังกฤษ.

    คืน {'search': '<คำค้นรวมสำหรับ arXiv/HN/Reddit>',
         'news': ['<วลีข่าว 1>', '<วลีข่าว 2>', ...]}.
    ถ้าธีมเป็นอังกฤษอยู่แล้วและแปลไม่ได้ ใช้ธีมเดิมเป็น fallback."""
    fallback = {"search": topic, "news": [topic, f"{topic} startup", f"{topic} breakthrough"]}
    sys = ("คุณเป็นผู้ช่วยทำคำค้น (search query) ภาษาอังกฤษสำหรับค้นงานวิจัยและข่าวเทคโนโลยี "
           "รับ 'ธีม' ที่ผู้ใช้สนใจ (อาจเป็นไทยหรืออังกฤษ) แล้วคืนคำค้นภาษาอังกฤษที่ดีที่สุด "
           "ตอบเป็น JSON เท่านั้น")
    um = (f"ธีมที่ผู้ใช้สนใจ: \"{topic}\"\n"
          "คืน JSON รูปแบบนี้เท่านั้น:\n"
          '{"search": "<คำค้นภาษาอังกฤษ 2-5 คำ คั่นด้วยช่องว่าง ครอบคลุมแก่นของธีมนี้ '
          'เช่น space → space exploration satellite launch>", '
          '"news": ["<วลีข่าวอังกฤษ 1>", "<วลีข่าวอังกฤษ 2>", "<วลีข่าวอังกฤษ 3>"]}')
    try:
        txt = await llm.complete(sys, um, exclude=exclude)
        data = _extract_json(txt)
        search = str(data.get("search") or "").strip()
        news = [str(x).strip() for x in (data.get("news") or []) if str(x).strip()]
        if search and news:
            return {"search": search[:120], "news": news[:4]}
    except Exception:  # noqa: BLE001 — แปลไม่ได้ก็ใช้ธีมเดิม
        pass
    return fallback


async def get_trend_radar(topic: str = "", *, refresh: bool = False) -> dict:
    """สแกนสัญญาณต้นน้ำ + ให้ AI วิเคราะห์เทรนด์ที่กำลังโผล่. อ่าน cache ก่อน เว้นแต่ refresh.

    topic ว่าง = โหมดกวาดกว้างทั้งโลก; ใส่ topic = เจาะลึกธีมที่สนใจ (เช่น 'หุ่นยนต์', 'AI agent')."""
    topic = _safe_topic(topic)
    cache = _cache_path(topic or "global")
    if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < _TTL:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    settings = get_settings()
    if not settings.llm_enabled():
        raise ValueError("ต้องตั้งค่าคีย์ AI (เช่น Gemini ฟรี) เพื่อให้ AI วิเคราะห์เทรนด์")

    # โหมดเจาะธีม: แปลงเป็นคำค้นภาษาอังกฤษก่อน (แหล่งต้นน้ำเป็นภาษาอังกฤษ)
    # แล้วสั่งให้ 'ทุกแหล่ง' ค้นตามธีม ไม่ใช่ดึงข่าวทั่วไป
    search_q, news_topics, kw = "", _DEFAULT_TOPICS, None
    if topic:
        kw = await _topic_to_keywords(topic, set())
        search_q = kw["search"]
        news_topics = kw["news"]
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        harvested = await asyncio.gather(
            _hn_signals(client, search_q), _arxiv_signals(client, search_q),
            _reddit_signals(client, search_q), _gnews_signals(client, news_topics),
            return_exceptions=True,
        )
    signals: list[dict] = []
    sources_ok: list[str] = []
    labels = ["Hacker News", "arXiv", "Reddit", "Google News"]
    for label, res in zip(labels, harvested):
        if isinstance(res, list) and res:
            signals.extend(res)
            sources_ok.append(label)
    # ตัดซ้ำ + เรียงใหม่สุดก่อน
    seen, dedup = set(), []
    for s in sorted(signals, key=lambda x: x.get("ts", 0), reverse=True):
        k = re.sub(r"[^a-z0-9]+", "", (s["title"] or "").lower())[:60]
        if k and k not in seen:
            seen.add(k)
            dedup.append(s)

    min_needed = 4 if topic else 8
    if len(dedup) < min_needed:
        extra = (f" สำหรับธีม '{topic}'" if topic else "")
        raise ValueError(
            f"ดึงสัญญาณต้นน้ำได้น้อยเกินไป{extra} "
            "(ธีมอาจเฉพาะเกินไป หรือแหล่งข้อมูลถูกบล็อก/เน็ตมีปัญหา) — "
            "ลองใช้คำที่กว้างขึ้น หรือเว้นว่างเพื่อกวาดทั้งโลก")

    digest = _signals_digest(dedup)
    focus = (f"ผู้ใช้สนใจเจาะลึกธีม: '{topic}'\n" if topic
             else "โหมดกวาดกว้าง: หาเทรนด์ใหญ่ของโลกจากทุกหมวด\n")
    user_msg = (
        focus +
        "ด้านล่างคือ 'สัญญาณดิบ' ที่สแกนจากต้นน้ำของโลกในช่วง ~2 สัปดาห์ล่าสุด "
        "(รูปแบบ: [แหล่ง · อายุวัน · คะแนน] หัวข้อ)\n"
        "จงมองทะลุสัญญาณกระจัดกระจายเหล่านี้ จัดกลุ่มเป็นเทรนด์ที่กำลังก่อตัว "
        "แล้ววิเคราะห์ตามรูปแบบ JSON ที่กำหนด เน้นจับ 'ต้นเทรนด์' และระบุผู้ถูกดิสรัป-ผู้ได้ประโยชน์\n\n"
        f"=== สัญญาณดิบ ({len(dedup)} รายการ จากแหล่ง: {', '.join(sources_ok)}) ===\n" + digest
    )
    system = SYSTEM_PROMPT + "\n\n" + _OUTPUT_CONTRACT

    exclude: set = set()
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            text = await llm.complete(system, user_msg, exclude=exclude)
            data = _clean(_extract_json(text))
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            reason = str(exc).lower()
            is_quota = "429" in reason or "quota" in reason or "resource_exhausted" in reason
            cur = settings.resolve_llm(exclude=exclude)["provider"]
            if is_quota and attempt == 0 and cur not in ("none", ""):
                exclude.add(cur)
                continue
            raise ValueError(f"AI วิเคราะห์เทรนด์ไม่สำเร็จ: {exc}") from exc
    else:  # pragma: no cover
        raise ValueError(f"AI วิเคราะห์เทรนด์ไม่สำเร็จ: {last_err}")

    result = {
        "topic": topic or None,
        "search_terms": (kw["search"] if kw else None),
        "scope": "เจาะลึกธีม" if topic else "กวาดกว้างทั้งโลก",
        "sources": sources_ok,
        "signal_count": len(dedup),
        "top_signals": [{"title": s["title"], "source": s["source"], "url": s.get("url")}
                        for s in dedup[:30]],
        "generated_at": int(time.time()),
        **data,
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result
