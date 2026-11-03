"""Order store + tracker.

Events are published on a tiny in-process bus (`EventBus`).  The interface mirrors
a Kafka producer/consumer (topic + payload), so swapping in Kafka later is a
one-class change; for the prototype it removes an operational failure point.
Stage dates are computed from typical_install_days so the tracker is deterministic.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Callable

from .config import TODAY
from .data_store import get_store
from .privacy import get_logger

log = get_logger("tracker")


class EventBus:
    def __init__(self) -> None:
        self.subs: dict[str, list[Callable]] = defaultdict(list)
        self.history: list[dict] = []

    def subscribe(self, topic: str, fn: Callable) -> None:
        self.subs[topic].append(fn)

    def publish(self, topic: str, payload: dict) -> None:
        self.history.append({"topic": topic, "payload": payload})
        for fn in self.subs[topic]:
            fn(payload)


bus = EventBus()
ORDERS: dict[str, dict] = {}


def _stages(order: dict) -> list[dict]:
    store = get_store()
    install = max((store.products[l["product_id"]].install_days for l in order["lines"] if l["product_id"] in store.products), default=7)
    placed = date.fromisoformat(order["placed_on"])
    plan = [
        ("Order placed", 0, "We received your order."),
        ("KYC verified", 0, "Your documents were verified."),
        ("Site survey / dispatch", max(1, round(install * 0.25)), "Our team confirms the installation slot."),
        ("Installation in progress", max(2, round(install * 0.7)), "Equipment is being installed."),
        ("Activated", install, "Your services are live. One bill, one support number."),
    ]
    return [{"name": n, "expected_date": (placed + timedelta(days=d)).isoformat(), "note": note} for n, d, note in plan]


def place_order(lines: list[dict], totals: dict, contact: dict, kyc_verdict: str) -> dict:
    oid = "ORD-" + uuid.uuid4().hex[:8].upper()
    order = {"order_id": oid, "placed_on": TODAY.isoformat(), "lines": lines, "totals": totals,
             "contact_name": contact.get("name", ""), "kyc": kyc_verdict}
    order["stages"] = _stages(order)
    order["expected_activation"] = order["stages"][-1]["expected_date"]
    ORDERS[oid] = order
    bus.publish("order.placed", {"order_id": oid})
    log.info("Order %s placed, expected activation %s", oid, order["expected_activation"])
    return order


def status(order_id: str, as_of: date | None = None) -> dict | None:
    """Current stage, derived from `as_of` (default: the frozen today). Demo can pass ?as_of= to fast-forward."""
    o = ORDERS.get(order_id)
    if not o:
        return None
    as_of = as_of or TODAY
    stages = []
    current = 0
    for i, s in enumerate(o["stages"]):
        done = date.fromisoformat(s["expected_date"]) <= as_of
        stages.append({**s, "done": done})
        if done:
            current = i
    return {"order_id": order_id, "as_of": as_of.isoformat(), "current_stage": o["stages"][current]["name"],
            "stages": stages, "expected_activation": o["expected_activation"],
            "days_remaining": max((date.fromisoformat(o["expected_activation"]) - as_of).days, 0),
            "activated": current == len(o["stages"]) - 1}
