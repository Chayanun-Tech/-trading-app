"""Alert Engine อย่างง่าย — เก็บกฎใน memory และตรวจกับราคา/อินดิเคเตอร์ล่าสุด.

MVP: เก็บใน memory (หายเมื่อรีสตาร์ท). โปรดักชันควรย้ายไป DB + คิวงาน + แจ้งเตือนผ่าน
LINE/Email/Push.
"""
from __future__ import annotations

import time
import uuid
from typing import NamedTuple

from app.schemas import AlertRule, TriggeredAlert

_rules: dict[str, AlertRule] = {}
_triggered: list[TriggeredAlert] = []
_last_fired: dict[str, float] = {}  # rule_id → unix timestamp ที่ยิงล่าสุด
ALERT_COOLDOWN_SECONDS = 1800  # ไม่ยิงซ้ำภายใน 30 นาที


def add_rule(rule: AlertRule) -> AlertRule:
    rule.id = rule.id or uuid.uuid4().hex[:8]
    _rules[rule.id] = rule
    return rule


def list_rules() -> list[AlertRule]:
    return list(_rules.values())


def delete_rule(rule_id: str) -> bool:
    _last_fired.pop(rule_id, None)  # reset cooldown เมื่อลบกฎ
    return _rules.pop(rule_id, None) is not None


def list_triggered() -> list[TriggeredAlert]:
    return list(reversed(_triggered[-100:]))


def symbols_with_rules() -> set[str]:
    return {r.symbol for r in _rules.values()}


class FiredResult(NamedTuple):
    alert: TriggeredAlert
    notify_email: str | None


def evaluate(symbol: str, price: float, rsi: float | None, global_email: str = "") -> list[FiredResult]:
    """ตรวจกฎทั้งหมดของ symbol นี้กับค่าปัจจุบัน คืนรายการที่เพิ่งทริกเกอร์พร้อม email."""
    fired: list[FiredResult] = []
    now = int(time.time())
    for rule in _rules.values():
        if rule.symbol != symbol:
            continue
        hit, observed = False, price
        if rule.kind == "price_above" and price >= rule.value:
            hit = True
        elif rule.kind == "price_below" and price <= rule.value:
            hit = True
        elif rule.kind == "rsi_above" and rsi is not None and rsi >= rule.value:
            hit, observed = True, rsi
        elif rule.kind == "rsi_below" and rsi is not None and rsi <= rule.value:
            hit, observed = True, rsi
        if hit:
            rule_id = rule.id or ""
            last = _last_fired.get(rule_id, 0)
            if now - last < ALERT_COOLDOWN_SECONDS:
                continue  # ยังอยู่ใน cooldown — ข้ามไป
            _last_fired[rule_id] = now
            ta = TriggeredAlert(
                rule_id=rule_id, symbol=symbol, kind=rule.kind,
                value=rule.value, observed=round(observed, 2), time=now,
                message=f"{symbol}: {rule.kind} {rule.value} (ค่าปัจจุบัน {round(observed, 2)})",
            )
            _triggered.append(ta)
            email = rule.notify_email or global_email or None
            fired.append(FiredResult(alert=ta, notify_email=email))
    return fired
