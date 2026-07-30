"""บันทึกวิทยานิพนธ์การลงทุน (Investment Thesis Journal) — ชั้น 'วินัย' ของนักลงทุน VI.

Buffett/Munger: จดเหตุผลที่ซื้อไว้เป็นลายลักษณ์ แล้ว "ขายเมื่อเหตุผลเปลี่ยน ไม่ใช่เมื่อราคาตก".
เก็บเป็น JSON ในเครื่อง (แพตเทิร์นเดียวกับ offline_fundamentals.json) — CRUD + คำนวณสถานะสด
(ราคาปัจจุบันเข้าเขตซื้อตาม margin of safety หรือยัง) + คำแนะนำน้ำหนักพอร์ตตามระดับความเชื่อมั่น.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "data" / "thesis_journal.json"

# ฟิลด์ที่ผู้ใช้แก้ได้ (กันฟิลด์แปลกปลอม)
_FIELDS = ("symbol", "entity_name", "moat_summary", "reasons", "target_price",
           "required_mos", "conviction", "notes", "buffett_score", "status")


def _load() -> list[dict]:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def _clean(payload: dict) -> dict:
    out = {k: payload.get(k) for k in _FIELDS if payload.get(k) is not None}
    if out.get("symbol"):
        out["symbol"] = str(out["symbol"]).strip().upper()
    for k in ("target_price", "required_mos", "buffett_score", "conviction"):
        if k in out:
            try:
                out[k] = float(out[k])
            except (TypeError, ValueError):
                out.pop(k, None)
    return out


def suggested_weight_pct(conviction: float | None) -> float | None:
    """น้ำหนักพอร์ตที่แนะนำ (%) ตามระดับความเชื่อมั่น 1–5 — สไตล์ Buffett/Munger ที่ 'กระจุกในของดี'
    (ความเชื่อมั่นสูง = น้ำหนักมาก) แต่คุมเพดานไม่ให้เสี่ยงเกินไป. conviction 1→5% ... 5→25%."""
    if not isinstance(conviction, (int, float)):
        return None
    c = max(1.0, min(5.0, conviction))
    return round(c * 5.0, 1)


def list_theses() -> list[dict]:
    return _load()


def add_thesis(payload: dict) -> dict:
    items = _load()
    entry = _clean(payload)
    if not entry.get("symbol"):
        raise ValueError("ต้องระบุสัญลักษณ์หุ้น (symbol)")
    now = int(time.time())
    entry.update(id=uuid.uuid4().hex[:12], created_at=now, updated_at=now)
    entry.setdefault("status", "watching")
    items.append(entry)
    _save(items)
    return entry


def update_thesis(thesis_id: str, patch: dict) -> dict:
    items = _load()
    for e in items:
        if e.get("id") == thesis_id:
            e.update(_clean(patch))
            e["updated_at"] = int(time.time())
            _save(items)
            return e
    raise ValueError("ไม่พบวิทยานิพนธ์ที่ระบุ")


def delete_thesis(thesis_id: str) -> bool:
    items = _load()
    new = [e for e in items if e.get("id") != thesis_id]
    if len(new) == len(items):
        return False
    _save(new)
    return True


def annotate_live(entry: dict, price: float | None) -> dict:
    """เติมสถานะสด: ราคาปัจจุบัน, ส่วนลดจากราคาเป้าหมาย, เข้าเขตซื้อหรือยัง (ตาม margin of safety),
    และน้ำหนักพอร์ตที่แนะนำ — คืน dict ใหม่ (ไม่แก้ต้นฉบับ)."""
    out = {**entry, "current_price": price, "suggested_weight_pct": suggested_weight_pct(entry.get("conviction"))}
    target = entry.get("target_price")
    if isinstance(price, (int, float)) and isinstance(target, (int, float)) and target > 0:
        out["discount_to_target_pct"] = round((target - price) / target * 100, 1)
        out["in_buy_zone"] = price <= target
    return out
