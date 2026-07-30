"""Insider transactions (SEC Form 4) — สัญญาณ "ผู้บริหารกินข้าวหม้อเดียวกับผู้ถือหุ้นไหม".

Buffett: ผู้บริหารซื้อหุ้นตัวเองด้วยเงินสด (open-market purchase, code P) = เชื่อมั่นจริง (สัญญาณบวกแรง).
การขาย (S) เป็น noise มากกว่า (อาจแค่จ่ายภาษี/กระจายความเสี่ยง). การได้หุ้นจาก options/grant (M/A/G)
เป็นค่าตอบแทน ไม่ใช่ความเชื่อมั่น. โมดูลนี้ดึง Form 4 ล่าสุดจาก SEC แล้วสรุปการซื้อ-ขายแบบ open-market.
"""
from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import httpx

from app import edgar

_HEADERS = edgar._HEADERS
_CACHE_TTL = 6 * 3600
_cache: dict[str, tuple[float, dict]] = {}


def _txt(node, tag: str) -> str | None:
    """หาค่า element ลูกชื่อ tag (local-name) — Form 4 ห่อค่าจริงใน <value> อีกชั้นบ่อยครั้ง."""
    for el in node.iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            v = None
            for child in el.iter():
                if child.tag.rsplit("}", 1)[-1] == "value" and child.text:
                    v = child.text.strip()
                    break
            return v if v is not None else (el.text.strip() if el.text else None)
    return None


def _fnum(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _parse_form4(xml: str) -> dict | None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    owner = _txt(root, "rptOwnerName")
    is_dir = (_txt(root, "isDirector") or "").strip() in ("1", "true")
    is_off = (_txt(root, "isOfficer") or "").strip() in ("1", "true")
    title = _txt(root, "officerTitle") or ("Director" if is_dir else "")
    role = title or ("Officer" if is_off else ("Director" if is_dir else "Insider"))

    txns = []
    for node in root.iter():
        name = node.tag.rsplit("}", 1)[-1]
        if name != "nonDerivativeTransaction":
            continue
        code = _txt(node, "transactionCode")
        shares = _fnum(_txt(node, "transactionShares"))
        price = _fnum(_txt(node, "transactionPricePerShare"))
        ad = _txt(node, "transactionAcquiredDisposedCode")
        tdate = _txt(node, "transactionDate")
        if code and shares:
            txns.append({"code": code, "shares": shares, "price": price,
                         "acquired": ad == "A", "date": tdate})
    if not txns:
        return None
    return {"owner": owner, "role": role, "transactions": txns}


async def _fetch_form4(client: httpx.AsyncClient, cik: str, accn: str) -> dict | None:
    compact = accn.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}"
    try:
        # ownership XML แทบทุกฉบับชื่อ form4.xml — ลองดึงตรงก่อน (ประหยัด 1 request/ฉบับ)
        r = await client.get(f"{base}/form4.xml")
        if r.status_code == 200 and "ownershipDocument" in r.text:
            return _parse_form4(r.text)
        # fallback: อ่าน index หา XML ตัวจริง (กรณีตั้งชื่อไฟล์แปลก)
        idx = (await client.get(f"{base}/index.json")).json()
        raw = [it.get("name", "") for it in idx.get("directory", {}).get("item", [])
               if it.get("name", "").endswith(".xml") and not it["name"].lower().startswith("xsl")]
        if not raw:
            return None
        return _parse_form4((await client.get(f"{base}/{raw[0]}")).text)
    except Exception:  # noqa: BLE001
        return None


async def get_insider_activity(symbol: str, *, months: int = 12, max_filings: int = 40) -> dict:
    """สรุปการซื้อ-ขายของผู้บริหารจาก Form 4 ล่าสุด (ภายใน `months` เดือน, สูงสุด `max_filings` ฉบับ)."""
    ckey = f"{symbol.upper()}:{months}:{max_filings}"
    hit = _cache.get(ckey)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    data = await edgar.get_submissions(symbol)
    cik = str(data.get("cik") or "").zfill(10)
    rec = data.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    dates = rec.get("filingDate", [])
    accns = rec.get("accessionNumber", [])
    cutoff = (date.today() - timedelta(days=months * 31)).isoformat()

    picks = []
    for i, f in enumerate(forms):
        if f in ("4", "4/A") and i < len(dates) and dates[i] >= cutoff:
            picks.append(accns[i])
        if len(picks) >= max_filings:
            break
    if not picks:
        return {"symbol": symbol.upper(), "filings": 0, "note": "ไม่พบ Form 4 ในช่วงเวลานี้",
                "buy_shares": 0, "sell_shares": 0, "recent": [], "signal": "ไม่มีข้อมูล"}

    sem = asyncio.Semaphore(9)   # SEC จำกัด ~10 req/วินาที
    async with httpx.AsyncClient(headers=_HEADERS, timeout=30) as client:
        async def worker(accn):
            async with sem:
                return await _fetch_form4(client, cik, accn)
        results = [r for r in await asyncio.gather(*(worker(a) for a in picks)) if r]

    buy_sh = buy_val = sell_sh = sell_val = 0.0
    buyers, sellers = set(), set()
    recent = []
    for r in results:
        for t in r["transactions"]:
            code, sh, pr = t["code"], t["shares"], t["price"]
            val = (sh * pr) if pr else None
            if code == "P":       # open-market purchase = เงินจริง = สัญญาณเชื่อมั่น
                buy_sh += sh; buy_val += val or 0; buyers.add(r["owner"])
                recent.append({**t, "owner": r["owner"], "role": r["role"], "type": "ซื้อ (open market)"})
            elif code == "S":     # open-market sale
                sell_sh += sh; sell_val += val or 0; sellers.add(r["owner"])
                recent.append({**t, "owner": r["owner"], "role": r["role"], "type": "ขาย (open market)"})
    recent.sort(key=lambda x: x.get("date") or "", reverse=True)

    net_val = buy_val - sell_val
    if buy_sh > 0 and buy_sh >= sell_sh:
        signal = "🟢 ผู้บริหารซื้อสุทธิ (สัญญาณเชื่อมั่น)"
    elif sell_sh > buy_sh * 3 and sell_sh > 0:
        signal = "🔴 ผู้บริหารขายหนัก (เฝ้าระวัง)"
    elif buy_sh == 0 and sell_sh == 0:
        signal = "⚪ ไม่มีการซื้อ-ขายแบบ open-market (มีแต่ options/หุ้นรางวัล)"
    else:
        signal = "🟡 มีทั้งซื้อและขาย"

    out = {
        "symbol": symbol.upper(), "months": months, "filings": len(results),
        "buy_shares": round(buy_sh), "buy_value": round(buy_val),
        "sell_shares": round(sell_sh), "sell_value": round(sell_val),
        "net_value": round(net_val), "num_buyers": len(buyers), "num_sellers": len(sellers),
        "signal": signal, "recent": recent[:15],
        "note": "นับเฉพาะรายการ open-market: ซื้อ (P) = เงินจริง = สัญญาณเชื่อมั่น · "
                "ขาย (S) = noise (อาจแค่จ่ายภาษี/กระจายเสี่ยง) · ไม่นับ options/หุ้นรางวัล",
    }
    _cache[ckey] = (time.time(), out)
    return out
