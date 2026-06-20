"""Thai SEC iDISC client for Form 56-1 / 56-1 One Report.

The SEC publishes one consolidated HTML table containing company name, report
year, received date, and the official download URL.  The table is cached on
disk because it is large and the SEC site occasionally resets connections.
"""
from __future__ import annotations

import asyncio
import html
import io
import json
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import httpx

_INDEX_URL = "https://market.sec.or.th/public/idisc/en/ViewMore/fs-r561"
_SOURCE_URL = "https://market.sec.or.th/public/idisc/en/Viewmore/fs-r561"
_CACHE_DIR = Path(__file__).resolve().parents[1].parent / "data" / "thai_sec"
_CACHE_FILE = _CACHE_DIR / "fs-r561-en.html"
_CACHE_TTL = 12 * 3600
_DOCUMENT_TTL = 7 * 24 * 3600
_MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
_MAX_PDF_BYTES = 70 * 1024 * 1024
_MAX_CONTEXT_CHARS = 48_000
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def is_thai_symbol(symbol: str) -> bool:
    return (symbol or "").upper().strip().endswith(".BK")


class _ReportsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.cells: list[str] = []
        self.cell_parts: list[str] = []
        self.href: str | None = None
        self.rows: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "table" and attr.get("id") == "gPP06T05":
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.in_row = True
            self.cells = []
            self.href = None
        elif self.in_row and tag in ("td", "th"):
            self.in_cell = True
            self.cell_parts = []
        elif self.in_cell and tag == "a" and attr.get("href"):
            self.href = urljoin(_INDEX_URL, attr["href"])

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_cell and tag in ("td", "th"):
            self.cells.append(re.sub(r"\s+", " ", "".join(self.cell_parts)).strip())
            self.in_cell = False
        elif self.in_row and tag == "tr":
            if len(self.cells) >= 3 and self.href and self.cells[1].strip()[:4].isdigit():
                self.rows.append({
                    "company_name": html.unescape(self.cells[0]).strip(),
                    "year_label": html.unescape(self.cells[1]).strip(),
                    "received_date": self.cells[2].strip(),
                    "url": self.href,
                })
            self.in_row = False
        elif self.in_table and tag == "table":
            self.in_table = False


def parse_reports_html(text: str) -> list[dict]:
    parser = _ReportsParser()
    parser.feed(text)
    return parser.rows


def _normalize_company_name(value: str) -> str:
    text = html.unescape(value or "").upper()
    text = text.replace("&", " AND ")
    text = re.sub(r"\bTHE\b", " ", text)
    text = re.sub(r"\bPUBLIC\s+COMPANY\s+LIMITED\b", " ", text)
    text = re.sub(r"\bCOMPANY\s+LIMITED\b", " ", text)
    text = re.sub(r"\bPUBLIC\s+CO\.?\s*,?\s*LTD\.?\b", " ", text)
    text = re.sub(r"\bCO\.?\s*,?\s*LTD\.?\b", " ", text)
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def _company_match_score(wanted: str, candidate: str) -> float:
    left = _normalize_company_name(wanted)
    right = _normalize_company_name(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_tokens, right_tokens = set(left.split()), set(right.split())
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))
    sequence = SequenceMatcher(None, left, right).ratio()
    return max(overlap, sequence)


def _curl_fetch() -> str:
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if not curl:
        raise RuntimeError("ไม่พบ curl สำหรับเชื่อมต่อ SEC Thailand")
    result = subprocess.run(
        [
            curl, "-L", "--http1.1", "-sS", "--fail",
            "--max-time", "60", "-A", _HEADERS["User-Agent"],
            "-H", f"Accept: {_HEADERS['Accept']}",
            "-H", f"Accept-Language: {_HEADERS['Accept-Language']}",
            _INDEX_URL,
        ],
        capture_output=True,
        timeout=70,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"SEC Thailand ไม่ตอบกลับ: {detail[:180]}")
    return result.stdout.decode("utf-8", errors="replace")


async def _download_index() -> str:
    error: Exception | None = None
    for attempt in range(3):
        try:
            timeout = httpx.Timeout(60, connect=15)
            async with httpx.AsyncClient(
                timeout=timeout, headers=_HEADERS, follow_redirects=True,
            ) as client:
                response = await client.get(_INDEX_URL)
                response.raise_for_status()
                if len(response.content) < 100_000:
                    raise RuntimeError("SEC Thailand ส่งข้อมูลกลับมาไม่ครบ")
                return response.content.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            error = exc
            await asyncio.sleep(attempt + 1)
    try:
        return await asyncio.to_thread(_curl_fetch)
    except Exception as curl_exc:  # noqa: BLE001
        raise RuntimeError(f"ดึงดัชนี One Report ไม่สำเร็จ: {curl_exc}") from error


async def get_reports_index(*, force_refresh: bool = False) -> list[dict]:
    if (
        not force_refresh
        and _CACHE_FILE.exists()
        and time.time() - _CACHE_FILE.stat().st_mtime < _CACHE_TTL
    ):
        text = _CACHE_FILE.read_text(encoding="utf-8", errors="replace")
    else:
        text = await _download_index()
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(text, encoding="utf-8")
    rows = parse_reports_html(text)
    if not rows:
        raise RuntimeError("อ่านตาราง 56-1 One Report จาก SEC Thailand ไม่สำเร็จ")
    return rows


def _iso_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except (TypeError, ValueError):
        return value


async def recent_one_reports(
    symbol: str,
    company_name: str,
    limit: int = 20,
    *,
    force_refresh: bool = False,
) -> list[dict]:
    if not is_thai_symbol(symbol):
        raise ValueError("รองรับเฉพาะสัญลักษณ์หุ้นไทยที่ลงท้าย .BK")
    if not company_name:
        raise ValueError(f"ไม่พบชื่อบริษัทของ {symbol}")

    rows = await get_reports_index(force_refresh=force_refresh)
    scored = [(_company_match_score(company_name, row["company_name"]), row) for row in rows]
    best = max((score for score, _ in scored), default=0.0)
    if best < 0.82:
        raise ValueError(f"ไม่พบ 56-1 One Report ของ {symbol} ใน SEC Thailand")

    # Keep only the same matched issuer.  A small tolerance handles minor name
    # spelling changes across old filings without mixing similarly named firms.
    matched = [row for score, row in scored if score >= max(0.82, best - 0.03)]
    output: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in matched:
        year_match = re.search(r"\d{4}", row["year_label"])
        year = int(year_match.group()) if year_match else None
        key = (row["year_label"], row["url"])
        if key in seen:
            continue
        seen.add(key)
        output.append({
            "form": "56-1 One Report",
            "date": _iso_date(row["received_date"]),
            "year": year,
            "year_label": row["year_label"],
            "company_name": row["company_name"],
            "items": "",
            "doc": "",
            "url": row["url"],
        })

    output.sort(key=lambda item: (item.get("year") or 0, item.get("date") or ""), reverse=True)
    return output[:limit]


def source_url() -> str:
    return _SOURCE_URL


def _document_cache_path(symbol: str) -> Path:
    safe = re.sub(r"[^A-Z0-9_-]+", "_", (symbol or "").upper().strip())
    return _CACHE_DIR / f"one_report_{safe}.json"


def _select_pdf_member(members: list[zipfile.ZipInfo]) -> zipfile.ZipInfo:
    pdfs = [
        item for item in members
        if not item.is_dir() and item.filename.lower().endswith(".pdf")
        and 0 < item.file_size <= _MAX_PDF_BYTES
    ]
    if not pdfs:
        raise ValueError("ไม่พบไฟล์ PDF ในชุด 56-1 One Report")

    def rank(item: zipfile.ZipInfo) -> tuple[int, int]:
        name = item.filename.upper()
        score = 0
        if "ONEREPORT" in name or "ONE_REPORT" in name:
            score += 100
        if "56-1" in name or "56_1" in name:
            score += 40
        if "STRUCTURE" in name:
            score -= 100
        return score, item.file_size

    return max(pdfs, key=rank)


async def _download_document(url: str) -> bytes:
    error: Exception | None = None
    for attempt in range(3):
        try:
            timeout = httpx.Timeout(150, connect=20)
            async with httpx.AsyncClient(
                timeout=timeout, headers=_HEADERS, follow_redirects=True,
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    stated = int(response.headers.get("content-length") or 0)
                    if stated > _MAX_DOWNLOAD_BYTES:
                        raise ValueError("ไฟล์ One Report มีขนาดใหญ่เกินขีดจำกัด")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > _MAX_DOWNLOAD_BYTES:
                            raise ValueError("ไฟล์ One Report มีขนาดใหญ่เกินขีดจำกัด")
                        chunks.append(chunk)
                    return b"".join(chunks)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            error = exc
            await asyncio.sleep(attempt + 1)
    raise RuntimeError(f"ดาวน์โหลด One Report ไม่สำเร็จ: {error}") from error


def _pdf_bytes(payload: bytes) -> tuple[bytes, str]:
    source = io.BytesIO(payload)
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            member = _select_pdf_member(archive.infolist())
            if member.compress_size and member.file_size / member.compress_size > 200:
                raise ValueError("ไฟล์ One Report มีอัตราการบีบอัดผิดปกติ")
            return archive.read(member), member.filename
    if payload[:5] == b"%PDF-":
        if len(payload) > _MAX_PDF_BYTES:
            raise ValueError("ไฟล์ PDF One Report มีขนาดใหญ่เกินขีดจำกัด")
        return payload, "one-report.pdf"
    raise ValueError("ไฟล์จาก SEC Thailand ไม่ใช่ PDF หรือ ZIP ที่รองรับ")


_PAGE_KEYWORDS = {
    "business overview": 12,
    "nature of business": 12,
    "revenue structure": 12,
    "business operation": 8,
    "products and services": 8,
    "hospital network": 7,
    "management discussion": 10,
    "management analysis": 10,
    "operating results": 8,
    "segment information": 10,
    "revenue by": 8,
    "source of revenue": 8,
    "customer": 3,
    "competition": 4,
    "competitive": 4,
    "risk factor": 5,
    "บริษัทประกอบธุรกิจ": 12,
    "ลักษณะการประกอบธุรกิจ": 12,
    "โครงสร้างรายได้": 12,
    "ผลิตภัณฑ์และบริการ": 8,
    "การวิเคราะห์และคำอธิบายของฝ่ายจัดการ": 10,
    "ปัจจัยความเสี่ยง": 5,
}


def _select_relevant_pages(pages: list[str], max_chars: int = _MAX_CONTEXT_CHARS) -> str:
    scored: list[tuple[int, int]] = []
    for index, text in enumerate(pages):
        low = text.lower()
        score = sum(weight * low.count(term) for term, weight in _PAGE_KEYWORDS.items())
        if score:
            scored.append((score, index))

    chosen: set[int] = {0}
    for _, index in sorted(scored, reverse=True)[:28]:
        chosen.update(i for i in (index - 1, index, index + 1) if 0 <= i < len(pages))

    output: list[str] = []
    total = 0
    for index in sorted(chosen):
        text = pages[index].strip()
        if not text:
            continue
        block = f"\n\n--- PAGE {index + 1} ---\n{text}"
        if total + len(block) > max_chars:
            remaining = max_chars - total
            if remaining > 500:
                output.append(block[:remaining])
            break
        output.append(block)
        total += len(block)
    return "".join(output).strip()


def _extract_pdf_context(pdf: bytes) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("PDF One Report ถูกเข้ารหัสและไม่สามารถอ่านได้") from exc

    pages: list[str] = []
    for page in reader.pages[:800]:
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        pages.append(re.sub(r"\s+", " ", text).strip())
    context = _select_relevant_pages(pages)
    if len(context) < 1_000:
        raise ValueError("อ่านข้อความจาก PDF One Report ได้น้อยเกินไป")
    return context, len(reader.pages)


async def get_one_report_context(
    symbol: str,
    company_name: str,
    *,
    force_refresh: bool = False,
) -> dict:
    """Download the latest Thai 56-1 One Report and return AI-ready excerpts."""
    cache = _document_cache_path(symbol)
    if (
        not force_refresh
        and cache.exists()
        and time.time() - cache.stat().st_mtime < _DOCUMENT_TTL
    ):
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    reports = await recent_one_reports(
        symbol, company_name, 1, force_refresh=force_refresh,
    )
    if not reports:
        raise ValueError(f"ไม่พบ 56-1 One Report ของ {symbol}")
    report = reports[0]
    payload = await _download_document(report["url"])
    pdf, filename = _pdf_bytes(payload)
    context, page_count = await asyncio.to_thread(_extract_pdf_context, pdf)
    result = {
        "url": report["url"],
        "filing_date": report["date"],
        "report_year": report["year"],
        "company_name": report["company_name"],
        "document_name": filename,
        "page_count": page_count,
        "business": context,
        "mda": context,
        "source": "SEC Thailand 56-1 One Report",
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result
