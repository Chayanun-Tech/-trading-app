"""Thai SEC iDISC client for Form 56-1 / 56-1 One Report.

The SEC publishes one consolidated HTML table containing company name, report
year, received date, and the official download URL.  The table is cached on
disk because it is large and the SEC site occasionally resets connections.
"""
from __future__ import annotations

import asyncio
import html
import re
import shutil
import subprocess
import time
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
