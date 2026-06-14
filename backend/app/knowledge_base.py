"""ตัวโหลดฐานความรู้วิชาเทรด (ไฟล์ JSON ในโฟลเดอร์ knowledge/).

ใช้โดย:
- engine.py / schools.py — อ่านน้ำหนัก (weight) และรายชื่อศาสตร์จาก _index.json
- analysis.py / vision.py — ดึงเนื้อหาความรู้ของศาสตร์เชิง pattern ไป ground ให้ Claude
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"


@lru_cache
def get_index() -> dict:
    with open(KNOWLEDGE_DIR / "_index.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def _load_file(filename: str) -> dict:
    with open(KNOWLEDGE_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


def schools() -> list[dict]:
    """รายการ school ทั้งหมดจาก registry."""
    return get_index()["schools"]


def schools_by_evaluator(kind: str) -> list[dict]:
    return [s for s in schools() if s["evaluator"] == kind]


def get_knowledge(school_id: str) -> dict | None:
    """เนื้อหาความรู้เต็มของศาสตร์หนึ่ง (อาจอยู่ไฟล์รวม เช่น indicators.json)."""
    for s in schools():
        if s["id"] == school_id:
            return _load_file(s["file"])
    return None


def claude_knowledge_bundle() -> str:
    """รวมความรู้ของศาสตร์ที่ใช้ evaluator='claude' เป็นข้อความ ground ให้โมเดล.

    ส่งเฉพาะศาสตร์เชิง pattern/discretionary ที่ Python ประเมินเองไม่ได้.
    """
    seen: set[str] = set()
    parts: list[str] = []
    for s in schools_by_evaluator("claude"):
        fname = s["file"]
        if fname in seen:
            continue
        seen.add(fname)
        data = _load_file(fname)
        parts.append(json.dumps(data, ensure_ascii=False))
    return "\n\n".join(parts)


def claude_school_list() -> list[dict]:
    """รายการ id/ชื่อของศาสตร์ที่ Claude ต้องประเมิน (ใช้บังคับ schema output)."""
    return [
        {"id": s["id"], "name": s["display_name"]}
        for s in schools_by_evaluator("claude")
    ]
