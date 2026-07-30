"""13F Superinvestors — "เซียน VI ระดับตำนานถือหุ้นตัวนี้ไหม" (coattail investing).

กองทุนใหญ่ (>$100M) ต้องยื่น 13F เปิดพอร์ตทุกไตรมาสต่อ SEC — รวม Berkshire ของ Buffett เอง.
โมดูลนี้ดึง 13F ล่าสุดของเซียน VI ที่คัดไว้ แล้ว reverse-lookup ว่าใครถือหุ้นที่เราสนใจบ้าง.
CIK ยืนยันกับ SEC แล้ว. cache ในหน่วยความจำ (13F ออกไตรมาสละครั้ง).
"""
from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET

import httpx

from app import edgar

_HEADERS = edgar._HEADERS

# เซียน VI/มูลค่า ที่ติดตาม (ชื่อ, CIK) — ยืนยันชื่อกับ SEC submissions แล้ว
SUPERINVESTORS = [
    ("Warren Buffett — Berkshire Hathaway", "0001067983"),
    ("Bill Gates Foundation Trust", "0001166559"),
    ("Bill Ackman — Pershing Square", "0001336528"),
    ("Seth Klarman — Baupost Group", "0001061768"),
    ("Li Lu — Himalaya Capital", "0001709323"),
    ("Michael Burry — Scion Asset Mgmt", "0001649339"),
    ("Dan Loeb — Third Point", "0001040273"),
    ("Stanley Druckenmiller — Duquesne", "0001536411"),
    ("David Tepper — Appaloosa", "0001006438"),
    ("Mario Gabelli — GAMCO", "0000807249"),
]

_CACHE_TTL = 24 * 3600
_cache: dict[str, tuple[float, list[dict]]] = {}   # cik -> (ts, holdings)
_SUFFIXES = ("INCORPORATED", "CORPORATION", "COMPANY", "HOLDINGS", "HLDGS", "HLDG", "GROUP", "GRP",
             "INC", "CORP", "PLC", "LTD", "LLC", "THE", "COM", "CL", "CLASS", "NEW", "DEL", "DE",
             "SHS", "ADR", "SPON", "SPONSORED", "OF", "AND", "A", "B", "C")


def _norm_name(s: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper())
    toks = [t for t in s.split() if t not in _SUFFIXES]
    return " ".join(toks)


def _ln(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _parse_infotable(xml: str) -> list[dict]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for node in root.iter():
        if _ln(node) != "infoTable":
            continue
        rec = {"name": None, "cusip": None, "value": None, "shares": None}
        for el in node.iter():
            tag = _ln(el)
            if tag == "nameOfIssuer" and el.text:
                rec["name"] = el.text.strip()
            elif tag == "cusip" and el.text:
                rec["cusip"] = el.text.strip()
            elif tag == "value" and el.text and rec["value"] is None:
                try:
                    rec["value"] = float(el.text.replace(",", ""))
                except ValueError:
                    pass
            elif tag == "sshPrnamt" and el.text and rec["shares"] is None:
                try:
                    rec["shares"] = float(el.text.replace(",", ""))
                except ValueError:
                    pass
        if rec["name"]:
            out.append(rec)
    return out


async def _fund_holdings(client: httpx.AsyncClient, cik: str) -> list[dict]:
    now = time.time()
    cached = _cache.get(cik)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]
    try:
        subs = (await client.get(f"https://data.sec.gov/submissions/CIK{cik}.json")).json()
        rec = subs.get("filings", {}).get("recent", {})
        forms, accns, dates = rec.get("form", []), rec.get("accessionNumber", []), rec.get("filingDate", [])
        i = next((i for i, f in enumerate(forms) if f == "13F-HR"), None)
        if i is None:
            return []
        accn = accns[i].replace("-", "")
        as_of = dates[i]
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}"
        idx = (await client.get(f"{base}/index.json")).json()
        info = [it.get("name", "") for it in idx.get("directory", {}).get("item", [])
                if it.get("name", "").endswith(".xml") and not it["name"].startswith("primary_doc")]
        holdings = []
        for n in info:
            xml = (await client.get(f"{base}/{n}")).text
            if "nameOfIssuer" in xml or "infoTable" in xml:
                holdings = _parse_infotable(xml)
                break
        for h in holdings:
            h["as_of"] = as_of
        _cache[cik] = (now, holdings)
        return holdings
    except Exception:  # noqa: BLE001
        return []


def _tok_match(a: str, b: str) -> bool:
    """คำเดียวกันหรือย่อมาจากกัน — 13F ย่อคำเยอะ (PETE↔PETROLEUM, FINL↔FINANCIAL) ใช้ prefix 3 ตัว."""
    return a == b or (len(a) >= 3 and len(b) >= 3 and a[:3] == b[:3])


def _matches(company_norm: str, issuer_norm: str) -> bool:
    if not company_norm or not issuer_norm:
        return False
    if company_norm == issuer_norm:
        return True
    ct, it = company_norm.split(), issuer_norm.split()
    if not ct or not it or ct[0] != it[0]:
        return False
    # คำแรกตรงแล้ว + ถ้ามีคำที่ 2 ต้องใกล้เคียง (กัน GENERAL MOTORS ปนกับ GENERAL MILLS)
    if len(ct) >= 2 and len(it) >= 2:
        return _tok_match(ct[1], it[1])
    return True


async def holders_of(symbol: str) -> dict:
    """เซียน VI คนไหนถือหุ้นตัวนี้บ้าง (จาก 13F ล่าสุดของแต่ละคน) — match ตามชื่อบริษัท."""
    try:
        subs = await edgar.get_submissions(symbol)
        company = subs.get("name") or symbol
    except ValueError:
        company = symbol
    company_norm = _norm_name(company)

    async with httpx.AsyncClient(headers=_HEADERS, timeout=30) as client:
        results = await asyncio.gather(*(_fund_holdings(client, cik) for _, cik in SUPERINVESTORS))

    from datetime import date, timedelta
    fresh_cutoff = (date.today() - timedelta(days=200)).isoformat()   # 13F ล่าสุดต้องไม่เก่าเกิน ~6 เดือน

    holders = []
    for (name, _cik), holdings in zip(SUPERINVESTORS, results):
        # กองใหญ่ (เช่น Berkshire) แยกโพซิชันเดียวเป็นหลายแถวตาม manager → ต้องรวมทุกแถวที่ตรง
        matched = [h for h in holdings if _matches(company_norm, _norm_name(h["name"]))]
        matched = [h for h in matched if (h.get("as_of") or "") >= fresh_cutoff]  # ตัด 13F เก่าค้าง
        if not matched:
            continue
        tot_val = sum(h.get("value") or 0 for h in matched)
        tot_sh = sum(h.get("shares") or 0 for h in matched)
        holders.append({"investor": name, "value": tot_val, "shares": tot_sh,
                        "as_of": matched[0].get("as_of"), "issuer_name": matched[0].get("name")})
    holders.sort(key=lambda h: h.get("value") or 0, reverse=True)
    return {
        "symbol": symbol.upper(), "company": company, "holders": holders,
        "num_holders": len(holders), "num_tracked": len(SUPERINVESTORS),
        "signal": (f"🟢 มีเซียน VI ถือ {len(holders)} คน" if holders else "⚪ ไม่มีเซียน VI ที่ติดตามถือหุ้นนี้"),
        "note": "จาก 13F ล่าสุด (ยื่นไตรมาสละครั้ง อาจล่าช้า ~45 วัน) · จับคู่ตามชื่อบริษัท · "
                "'coattail investing' — ดูของคนเก่ง แต่ต้องเข้าใจเหตุผลเอง ไม่ลอกตรง ๆ",
    }
