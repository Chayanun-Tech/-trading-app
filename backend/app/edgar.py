"""SEC EDGAR client — ดึงงบการเงินย้อนหลังลึก (companyfacts XBRL) ของหุ้น US.

ฟรี ไม่ใช้คีย์ แต่ SEC บังคับ:
- ตั้ง header `User-Agent` (ชื่อแอป + อีเมลติดต่อ) ไม่งั้นคืน 403
- จำกัด ≤10 req/วินาที (เราเรียกแค่ไม่กี่ครั้งต่อหุ้น + cache)

ใช้ httpx (ssl ของ Python จัดการ path ภาษาไทยได้ ไม่เจอปัญหา libcurl เหมือน yfinance).
cache companyfacts ลงดิสก์ (TTL 7 วัน เพราะงบออกไม่บ่อย).
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import httpx

# SEC ขอให้ระบุชื่อแอป + อีเมลจริงใน User-Agent
_HEADERS = {
    "User-Agent": "ChayanunOperating-trading-app chayanun250841@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

_CACHE_DIR = Path(__file__).resolve().parents[1].parent / "data" / "financials"
_FACTS_TTL = 7 * 24 * 3600       # 7 วัน
_TICKERS_TTL = 30 * 24 * 3600    # 30 วัน

# ออฟไลน์ก่อน (offline-first): ถ้ามีไฟล์งบในฐานข้อมูลออฟไลน์แล้ว → เสิร์ฟทันทีไม่สนอายุ cache
# (อ่านเร็ว/ไม่กลัวเน็ตล่ม/ไม่โดน SEC บล็อก). จะดึงสดก็ต่อเมื่อกดปุ่มอัปเดต (force_refresh)
# หรือยังไม่เคยมีไฟล์เลย. ปิดได้ด้วย env EDGAR_OFFLINE_FIRST=0 (กลับไปใช้ TTL 7 วัน).
import os as _os
_OFFLINE_FIRST = _os.getenv("EDGAR_OFFLINE_FIRST", "1").strip().lower() not in ("0", "false", "no")

_ticker_map: dict[str, str] | None = None


async def _load_ticker_map() -> dict[str, str]:
    """โหลดตาราง ticker→CIK (cache memory + disk)."""
    global _ticker_map
    if _ticker_map is not None:
        return _ticker_map
    cache = _CACHE_DIR / "company_tickers.json"
    data = None
    if cache.exists() and time.time() - cache.stat().st_mtime < _TICKERS_TTL:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None:
        async with httpx.AsyncClient(timeout=30, headers=_HEADERS) as client:
            res = await client.get(_TICKERS_URL)
            res.raise_for_status()
            data = res.json()
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    m: dict[str, str] = {}
    for row in data.values():
        m[str(row["ticker"]).upper()] = str(row["cik_str"]).zfill(10)
    _ticker_map = m
    return m


async def get_cik(symbol: str) -> str:
    m = await _load_ticker_map()
    cik = m.get((symbol or "").upper().strip())
    if not cik:
        raise ValueError(f"ไม่พบ CIK ของ {symbol} ใน SEC (อาจไม่ใช่หุ้นจดทะเบียนในสหรัฐ)")
    return cik


async def get_company_facts(symbol: str, *, force_refresh: bool = False) -> dict:
    """ดึง companyfacts (XBRL ทั้งหมด) ของหุ้น — cache ดิสก์ TTL 7 วัน."""
    cik = await get_cik(symbol)
    cache = _CACHE_DIR / f"facts_{cik}.json"
    # offline-first: มีไฟล์ในฐานออฟไลน์ → ใช้เลย (ไม่สน TTL) เว้นแต่สั่ง force_refresh
    fresh = cache.exists() and time.time() - cache.stat().st_mtime < _FACTS_TTL
    if not force_refresh and cache.exists() and (_OFFLINE_FIRST or fresh):
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    async with httpx.AsyncClient(timeout=60, headers=_HEADERS) as client:
        res = await client.get(_FACTS_URL.format(cik=cik))
        res.raise_for_status()
        data = res.json()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


# ---------- ฐานข้อมูลออฟไลน์ (offline DB) — รู้ว่าตัวไหนดาวน์โหลดเก็บไว้แล้ว ----------
def facts_file(cik: str) -> Path:
    return _CACHE_DIR / f"facts_{str(cik).zfill(10)}.json"


def has_offline_cik(cik: str) -> bool:
    """มีไฟล์งบ (companyfacts) ของ CIK นี้ในฐานออฟไลน์หรือยัง."""
    return facts_file(cik).exists()


async def has_offline(symbol: str) -> bool:
    """หุ้นตัวนี้มีงบเก็บไว้ในฐานออฟไลน์แล้วหรือยัง (ใช้ให้ frontend รู้ว่าต้องกดอัปเดตไหม)."""
    try:
        return has_offline_cik(await get_cik(symbol))
    except ValueError:
        return False


def downloaded_count() -> int:
    """จำนวนหุ้นที่ดาวน์โหลดงบเก็บไว้แล้วในฐานออฟไลน์."""
    try:
        return sum(1 for _ in _CACHE_DIR.glob("facts_*.json"))
    except OSError:
        return 0


async def offline_status() -> dict:
    """สรุปสถานะฐานข้อมูลออฟไลน์: ดาวน์โหลดแล้วกี่ตัว จากทั้งหมดกี่ตัวที่ SEC มี."""
    have = downloaded_count()
    total = None
    try:
        total = len(await _load_ticker_map())
    except Exception:  # noqa: BLE001
        pass
    return {
        "downloaded": have,
        "total_us_tickers": total,
        "pct": round(have / total * 100, 1) if total else None,
        "cache_dir": str(_CACHE_DIR),
        "offline_first": _OFFLINE_FIRST,
    }


_SUBS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FRAME_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/{unit}/{period}.json"
_SUBS_TTL = 12 * 3600       # ประวัติการยื่นเปลี่ยนบ่อยกว่า → 12 ชม.
_FRAME_TTL = 7 * 24 * 3600  # frame ทั้งตลาดใหญ่ + เปลี่ยนช้า → 7 วัน


async def _cached_json(url: str, cache_name: str, ttl: int, timeout: float = 60) -> dict:
    cache = _CACHE_DIR / cache_name
    if cache.exists() and time.time() - cache.stat().st_mtime < ttl:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS) as client:
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


async def get_submissions(symbol: str) -> dict:
    """ประวัติการยื่นเอกสารทั้งหมดของบริษัท (data.sec.gov/submissions)."""
    cik = await get_cik(symbol)
    return await _cached_json(_SUBS_URL.format(cik=cik), f"subs_{cik}.json", _SUBS_TTL)


async def _cached_text(url: str, cache_name: str, ttl: int, timeout: float = 60) -> str:
    cache = _CACHE_DIR / cache_name
    if cache.exists() and time.time() - cache.stat().st_mtime < ttl:
        try:
            return cache.read_text(encoding="utf-8")
        except OSError:
            pass
    async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS) as client:
        res = await client.get(url)
        res.raise_for_status()
        text = res.text
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


async def get_latest_filing_metrics(symbol: str) -> dict:
    """อ่าน XBRL instance ของ 10-K ล่าสุดเพื่อเติม metrics ที่ companyfacts ทำหล่น.

    SEC companyfacts ตัด facts ที่มี dimension ออกหลายกรณี เช่น Visa แยกหุ้น Class A/B/C
    ทำให้ EPS และ weighted-average shares หาย แม้ข้อมูลอยู่ใน 10-K จริง ฟังก์ชันนี้อ่าน
    instance XML โดยตรงและเลือก context ของ class หลัก/จำนวนหุ้น diluted ที่มากที่สุด.
    """
    cik = await get_cik(symbol)
    submissions = await get_submissions(symbol)
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    index = next((i for i, form in enumerate(forms) if form == "10-K"), None)
    if index is None:
        return {}
    accession = recent.get("accessionNumber", [])[index]
    compact = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}"
    listing = await _cached_json(f"{base}/index.json", f"filing_index_{compact}.json", _FACTS_TTL)
    items = listing.get("directory", {}).get("item", [])
    instance_name = next(
        (item.get("name") for item in items
         if str(item.get("name", "")).endswith("_htm.xml")),
        None,
    )
    if not instance_name:
        return {}
    xml = await _cached_text(f"{base}/{instance_name}", f"filing_instance_{compact}.xml", _FACTS_TTL)
    root = ET.fromstring(xml)

    contexts: dict[str, dict] = {}
    for node in root.iter():
        if _local_name(node.tag) != "context":
            continue
        info = {"start": None, "end": None, "instant": None, "members": []}
        for child in node.iter():
            name = _local_name(child.tag)
            if name in ("startDate", "endDate", "instant"):
                info[{"startDate": "start", "endDate": "end", "instant": "instant"}[name]] = child.text
            elif name in ("explicitMember", "typedMember"):
                info["members"].append(child.text or "")
        contexts[node.attrib.get("id", "")] = info

    def annual_context(info: dict) -> bool:
        try:
            if not info.get("start") or not info.get("end"):
                return False
            days = (date.fromisoformat(info["end"]) - date.fromisoformat(info["start"])).days
            return 300 <= days <= 400
        except (TypeError, ValueError):
            return False

    def numeric_facts(concepts: tuple[str, ...]) -> list[dict]:
        out = []
        seen = set()
        for node in root.iter():
            if _local_name(node.tag) not in concepts or not node.text:
                continue
            try:
                value = float(node.text.strip().replace(",", ""))
            except ValueError:
                continue
            context_id = node.attrib.get("contextRef", "")
            key = (context_id, _local_name(node.tag), value)
            if key in seen:
                continue
            seen.add(key)
            out.append({"concept": _local_name(node.tag), "value": value,
                        "context_id": context_id, "context": contexts.get(context_id, {})})
        return out

    share_concepts = (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    )
    shares_facts = [fact for fact in numeric_facts(share_concepts)
                    if fact["value"] > 0 and annual_context(fact["context"])]
    if not shares_facts:
        return {}
    latest_end = max(fact["context"]["end"] for fact in shares_facts)
    latest_shares = [fact for fact in shares_facts if fact["context"]["end"] == latest_end]
    diluted = [fact for fact in latest_shares if "Diluted" in fact["concept"]]
    share_fact = max(diluted or latest_shares, key=lambda fact: fact["value"])
    shares = share_fact["value"]

    eps_concepts = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",
                    "EarningsPerShareBasic")
    eps_facts = [fact for fact in numeric_facts(eps_concepts)
                 if fact["value"] > 0 and fact["context"].get("end") == latest_end]
    same_context = [fact for fact in eps_facts if fact["context_id"] == share_fact["context_id"]]
    same_context_diluted = [fact for fact in same_context if "Diluted" in fact["concept"]]
    diluted_eps = [fact for fact in eps_facts if "Diluted" in fact["concept"]]
    eps_fact = (same_context_diluted or same_context or diluted_eps or eps_facts)
    eps = eps_fact[0]["value"] if eps_fact else None

    equity_concepts = ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                       "StockholdersEquity")
    equity_facts = [fact for fact in numeric_facts(equity_concepts)
                    if fact["value"] > 0 and fact["context"].get("instant") == latest_end
                    and not fact["context"].get("members")]
    equity = max((fact["value"] for fact in equity_facts), default=None)
    return {
        "shares": shares,
        "eps": eps,
        "total_equity": equity,
        "bvps": (equity / shares if equity and shares else None),
        "_filing_period": latest_end,
    }


def _doc_url(cik: str, accession: str, primary_doc: str) -> str | None:
    if not primary_doc:
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_doc}"


async def recent_filings(symbol: str, limit: int = 25) -> list[dict]:
    """รายการยื่นล่าสุด (form/วันที่/รหัสเหตุการณ์ 8-K/ลิงก์เอกสารจริง)."""
    data = await get_submissions(symbol)
    cik = str(data.get("cik") or "").zfill(10)
    rec = data.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    out: list[dict] = []
    for i in range(min(limit, len(forms))):
        out.append({
            "form": forms[i],
            "date": rec.get("filingDate", [None] * len(forms))[i],
            "items": (rec.get("items") or [""] * len(forms))[i],
            "doc": (rec.get("primaryDocument") or [""] * len(forms))[i],
            "url": _doc_url(cik, rec.get("accessionNumber", [""] * len(forms))[i],
                            (rec.get("primaryDocument") or [""] * len(forms))[i]),
        })
    return out


async def latest_filing_url(symbol: str, form: str = "10-K") -> tuple[str | None, str | None]:
    """คืน (URL เอกสารหลัก, วันที่ยื่น) ของเอกสารฟอร์มล่าสุด."""
    data = await get_submissions(symbol)
    cik = str(data.get("cik") or "").zfill(10)
    rec = data.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    for i, f in enumerate(forms):
        if f == form:
            url = _doc_url(cik, rec["accessionNumber"][i], rec.get("primaryDocument", [""])[i])
            return url, rec.get("filingDate", [None])[i]
    return None, None


async def latest_annual_filing_url(symbol: str) -> tuple[str | None, str | None, str | None]:
    """Return the latest annual filing for US and foreign SEC registrants.

    Domestic issuers normally file 10-K, foreign private issuers file 20-F,
    and Canadian MJDS issuers may file 40-F.
    """
    data = await get_submissions(symbol)
    cik = str(data.get("cik") or "").zfill(10)
    rec = data.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    annual_forms = {"10-K", "20-F", "40-F"}
    for i, filing_form in enumerate(forms):
        if filing_form not in annual_forms:
            continue
        primary_docs = rec.get("primaryDocument") or []
        accessions = rec.get("accessionNumber") or []
        if i >= len(primary_docs) or i >= len(accessions):
            continue
        url = _doc_url(cik, accessions[i], primary_docs[i])
        filing_dates = rec.get("filingDate") or []
        filing_date = filing_dates[i] if i < len(filing_dates) else None
        return url, filing_date, filing_form

    # Newly listed companies may not have filed their first annual report yet.
    # Their final prospectus/registration statement still contains a detailed
    # audited business description and is preferable to returning no data.
    registration_forms = {"424B4", "S-1", "S-1/A", "F-1", "F-1/A"}
    for i, filing_form in enumerate(forms):
        if filing_form not in registration_forms:
            continue
        primary_docs = rec.get("primaryDocument") or []
        accessions = rec.get("accessionNumber") or []
        if i >= len(primary_docs) or i >= len(accessions):
            continue
        url = _doc_url(cik, accessions[i], primary_docs[i])
        filing_dates = rec.get("filingDate") or []
        filing_date = filing_dates[i] if i < len(filing_dates) else None
        return url, filing_date, filing_form
    return None, None, None


async def fetch_document_text(url: str, max_chars: int = 4_000_000) -> str:
    """ดึงเอกสาร (HTML) จาก EDGAR Archives แล้วถอดเป็นข้อความล้วน."""
    async with httpx.AsyncClient(timeout=60, headers=_HEADERS, follow_redirects=True) as client:
        res = await client.get(url)
        res.raise_for_status()
        # Do not truncate the HTML before parsing. Large inline-XBRL annual
        # reports can exceed 20 MB and place Item 1/Item 4 after the first 4 MB.
        html = res.text
    try:
        from bs4 import BeautifulSoup
        text = BeautifulSoup(html, "html.parser").get_text(" ")
    except Exception:  # noqa: BLE001
        import re
        text = re.sub(r"<[^>]+>", " ", html)
    import html as _html
    import re
    # SEC HTML มัก double-escape (&amp;#160;) → unescape สองชั้นให้ entity เช่น &#160; (nbsp),
    # &#8221; (”) กลายเป็นอักขระจริง ไม่งั้นหัวข้อ "Item 1.&#160;&#160;Business" จะ match ไม่ติด
    text = _html.unescape(_html.unescape(text))
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


async def get_frame(concept: str, unit: str, period: str) -> dict:
    """ค่าหนึ่ง concept ของ 'ทุกบริษัท' ในงวดเดียว (xbrl/frames) — ใช้จัดอันดับเทียบตลาด."""
    return await _cached_json(_FRAME_URL.format(concept=concept, unit=unit, period=period),
                              f"frame_{concept}_{unit}_{period}.json", _FRAME_TTL)


def _section(text: str, start_re: str, end_re: str, cap: int = 8000) -> str | None:
    """ดึง section จริงจาก 10-K โดยเลือก 'ช่วง start→end ที่กว้างที่สุด' (real section คือเนื้อ
    ที่ยาวสุดระหว่างหัวข้อ; สารบัญ=ช่วงสั้น, การอ้างอิง=ไม่มี end ตามมา). กันสารบัญด้วยความถี่ 'Item N'.
    """
    import re
    low = text.lower()
    best, best_span = None, 0
    for m in re.finditer(start_re, low):
        si = m.start()
        # Ignore prose references such as “Item 7—Management's Discussion and
        # Analysis” and keep actual headings, whose title is followed by body text.
        after_heading = text[m.end():m.end() + 3].lstrip()
        if after_heading[:1] in ('"', "'", "”", "’", "»"):
            continue
        if len(re.findall(r"item\s+\d", low[si:si + 1500])) >= 5:  # หัวสารบัญ
            continue
        end = si + cap
        for em in re.finditer(end_re, low[si + 50:]):
            candidate_end = si + 50 + em.start()
            after_end_heading = text[si + 50 + em.end():si + 50 + em.end() + 3].lstrip()
            # Skip cross-references such as “Item 8. Financial Statements” of
            # this report; continue until the real next section heading.
            if after_end_heading[:1] in ('"', "'", "”", "’", "»"):
                continue
            end = candidate_end
            break
        span = end - si
        if span > best_span:
            best, best_span = text[si:min(end, si + cap)], span
    return best.strip() if best and len(best.split()) >= 80 else None


def _business_section(text: str, cap: int = 32000) -> str | None:
    """ดึง Item 1 (Business) จริง: หัวข้อจริงคือ 'ครั้งแรก' ที่เจอ (ไม่ใช่การอ้างอิงกลางประโยค
    แบบ 'Item 1. Business” of this report') แล้วตัดถึง 'Item 1A. Risk Factors' ตัวแรกถัดมา.
    widest-span ใช้ไม่ได้กับ Business เพราะการอ้างอิงในส่วน Risk Factors ไม่มี 1A ตามมาใกล้ ๆ
    ทำให้ span ยาวเกินจริงและถูกเลือกผิด.
    """
    import re
    low = text.lower()
    starts = []
    for m in re.finditer(
        r"items?\s*1\b(?:\s+and\s+2\b)?[^a-z0-9]{1,12}"
        r"b\s*u\s*s\s*i\s*n\s*e\s*s\s*s\b"
        r"(?:\s+and\s+properties\b)?"
        r"(?:\s+description\b)?",
        low,
    ):
        nxt = text[m.end():m.end() + 3].lstrip()[:1]
        if nxt in ("”", '"', "’", "'", "»"):   # การอ้างอิง เช่น 'Item 1. Business” of this report'
            continue
        if len(re.findall(r"item\s+\d", low[m.start():m.start() + 1500])) >= 5:  # หัวสารบัญ
            continue
        starts.append(m.start())
    if not starts:
        return None
    si = min(starts)
    end = si + cap
    for em in re.finditer(
        r"item\s*1a\b[^a-z0-9]{0,12}r\s*i\s*s\s*k\s+f\s*a\s*c\s*t\s*o\s*r\s*s\b",
        low[si + 50:],
    ):
        candidate_end = si + 50 + em.start()
        after_end_heading = text[si + 50 + em.end():si + 50 + em.end() + 3].lstrip()
        if after_end_heading[:1] in ('"', "'", "”", "’", "»"):
            continue
        end = candidate_end
        break
    section = text[si:min(end, si + cap)].strip()
    return section if len(section.split()) >= 80 else None


def extract_filing_sections(text: str) -> dict:
    """แยกส่วน Business (Item 1), Risk Factors (Item 1A) และ MD&A (Item 7) จากข้อความ 10-K."""
    business = _business_section(text)
    # SEC filings use many heading separators, including em/en dashes. Restrict
    # separators to non-alphanumeric characters so Item 10 cannot match Item 1.
    risk = _section(
        text,
        r"item\s*1a\b[^a-z0-9]{0,12}r\s*i\s*s\s*k\s+f\s*a\s*c\s*t\s*o\s*r\s*s\b",
        r"item\s*1b\b[^a-z0-9]{0,12}u\s*n\s*r\s*e\s*s\s*o\s*l\s*v\s*e\s*d"
        r"\s+staff\s+comments\b",
    )
    mda = _section(
        text,
        r"item\s*7\b[^a-z0-9]{1,12}management(?:\s*['’]\s*s)?\s+discussion\s+and\s+analysis"
        r"\s+of\s+financial\s+condition\s+and\s+results\s+of\s+operations\b",
        r"item\s*7a\b[^a-z0-9]{0,12}quantitative\s+and\s+qualitative\s+disclosures"
        r"|item\s*8\b[^a-z0-9]{0,12}financial\s+statements\b",
    )
    return {"business": business, "risk_factors": risk, "mda": mda}


def extract_20f_sections(text: str) -> dict:
    """Extract the equivalent business/risk/operating sections from Form 20-F."""
    business = _section(
        text,
        r"item\s*4\b[^a-z0-9]{1,12}information\s+on\s+the\s+company\b",
        r"item\s*4a\b[^a-z0-9]{0,12}unresolved\s+staff\s+comments\b"
        r"|item\s*5\b[^a-z0-9]{0,12}operating\s+and\s+financial\s+reviews?",
        cap=32000,
    )
    risk = _section(
        text,
        r"item\s*3\b[^a-z0-9]{1,12}key\s+information\b",
        r"item\s*4\b[^a-z0-9]{0,12}information\s+on\s+the\s+company\b",
        cap=12000,
    )
    mda = _section(
        text,
        r"item\s*5\b[^a-z0-9]{1,12}operating\s+and\s+financial\s+reviews?\s+and\s+prospects\b",
        r"item\s*6\b[^a-z0-9]{0,12}directors\b",
        cap=12000,
    )
    return {"business": business, "risk_factors": risk, "mda": mda}


def _generic_business_section(text: str, cap: int = 32000) -> str | None:
    """Fallback for annual reports whose headings are not SEC item headings."""
    import re
    low = text.lower()
    patterns = (
        r"\bour\s+b\s*u\s*s\s*i\s*n\s*e\s*s\s*s\b",
        r"\bb\s*u\s*s\s*i\s*n\s*e\s*s\s*s\s+overview\b",
        r"\bb\s*u\s*s\s*i\s*n\s*e\s*s\s*s\s+and\s+properties\b",
        r"\bdescription\s+of\s+(?:our\s+)?b\s*u\s*s\s*i\s*n\s*e\s*s\s*s\b",
    )
    candidates = []
    for pattern in patterns:
        candidates.extend(m.start() for m in re.finditer(pattern, low))
    for start in sorted(set(candidates)):
        excerpt = text[start:start + cap].strip()
        if len(excerpt.split()) >= 120:
            return excerpt
    return None


def _generic_named_section(text: str, patterns: tuple[str, ...], cap: int) -> str | None:
    """Return a useful excerpt after a non-standard annual-report heading."""
    import re
    low = text.lower()
    candidates = []
    for pattern in patterns:
        candidates.extend((m.start(), m.end()) for m in re.finditer(pattern, low))
    for start, _ in sorted(set(candidates)):
        # Table-of-contents entries usually contain many nearby numbered items.
        if len(re.findall(r"\bitem\s+\d", low[start:start + 1200])) >= 5:
            continue
        excerpt = text[start:start + cap].strip()
        if len(excerpt.split()) >= 100:
            return excerpt
    return None


def extract_annual_filing_sections(text: str, filing_form: str) -> dict:
    if filing_form == "10-K":
        sections = extract_filing_sections(text)
    elif filing_form == "20-F":
        sections = extract_20f_sections(text)
    else:
        sections = {"business": None, "risk_factors": None, "mda": None}
    if not sections.get("business"):
        sections["business"] = _generic_business_section(text)
    if not sections.get("risk_factors"):
        sections["risk_factors"] = _generic_named_section(
            text,
            (r"\brisk\s+factors\b", r"\bprincipal\s+risks\b", r"\brisk\s+review\b"),
            8000,
        )
    if not sections.get("mda"):
        sections["mda"] = _generic_named_section(
            text,
            (
                r"\bmanagement(?:\s*['’]\s*s)?\s+discussion\s+and\s+analysis\b",
                r"\boperating\s+and\s+financial\s+reviews?\b",
                r"\bresults\s+of\s+operations\b",
                r"\bfinancial\s+performance\b",
            ),
            12000,
        )
    if not sections.get("business") and filing_form in {"S-1", "S-1/A", "F-1", "F-1/A", "424B4"}:
        sections["business"] = _generic_named_section(
            text,
            (
                r"\bprospectus\s+summary\b",
                r"\bthe\s+company\b",
                r"\bcompany\s+overview\b",
                r"\boverview\b",
            ),
            32000,
        )
    if not sections.get("business") and filing_form == "40-F":
        sections["business"] = _generic_named_section(
            text,
            (
                r"\boverview\s+of\s+the\s+trust\b",
                r"\bthe\s+trust\b",
                r"\binvestment\s+objective\b",
                r"\bdescription\s+of\s+the\s+fund\b",
            ),
            32000,
        )
    return sections


async def _fetch_annual_filing_text(
    symbol: str, primary_url: str, filing_form: str,
) -> str:
    text = await fetch_document_text(primary_url)
    if filing_form != "40-F":
        return text

    # A 40-F is often only a cover form. The Canadian annual information form,
    # MD&A, and financial statements live in separate HTML exhibits.
    try:
        base = primary_url.rsplit("/", 1)[0]
        cik = await get_cik(symbol)
        listing = await _cached_json(
            f"{base}/index.json", f"annual_index_{cik}.json", _FACTS_TTL,
        )
        primary_name = primary_url.rsplit("/", 1)[-1].lower()
        candidates = []
        for item in listing.get("directory", {}).get("item", []):
            name = str(item.get("name") or "")
            low_name = name.lower()
            if low_name == primary_name or not low_name.endswith((".htm", ".html")):
                continue
            if low_name.startswith("r") and low_name[1:-4].isdigit():
                continue
            if "_htm." in low_name:
                continue
            size = int(item.get("size") or 0)
            if size >= 100_000:
                candidates.append((size, name))
        # The relevant documents are normally exhibits. Read several because
        # issuers split the AIF, MD&A, and audited statements differently.
        for _, name in sorted(candidates, reverse=True)[:4]:
            exhibit_text = await fetch_document_text(f"{base}/{name}", max_chars=2_000_000)
            text += "\n\n" + exhibit_text
    except Exception:  # noqa: BLE001
        pass
    return text[:6_000_000]


async def get_10k_context(symbol: str, *, force_refresh: bool = False) -> dict | None:
    """Fetch/cache business context from the latest 10-K, 20-F, or 40-F."""
    url, date, filing_form = await latest_annual_filing_url(symbol)
    if not url:
        return None
    cik = (await get_cik(symbol))
    cache = _CACHE_DIR / f"tenk_{cik}.json"
    if not force_refresh and cache.exists() and time.time() - cache.stat().st_mtime < _FRAME_TTL:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            # Never preserve a failed extraction. Older parser versions cached
            # null sections, which made every retry fail until the TTL expired.
            if (
                cached.get("url") == url
                and cached.get("business")
                and cached.get("report_type")
            ):
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    text = await _fetch_annual_filing_text(symbol, url, filing_form or "10-K")
    sections = extract_annual_filing_sections(text, filing_form or "10-K")
    out = {"url": url, "filing_date": date, "report_type": filing_form, **sections}
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out
