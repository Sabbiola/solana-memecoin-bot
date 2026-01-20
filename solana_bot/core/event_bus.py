from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict, Iterable


@dataclass
class Event:
    level: str
    root_cause: str
    message: str
    ts: float


class EventBus:
    def __init__(self) -> None:
        self._events: DefaultDict[str, list[Event]] = defaultdict(list)

    def publish(self, mint: str, event: Event) -> None:
        self._events[mint].append(event)

    def get_events(self, mint: str) -> Iterable[Event]:
        return list(self._events.get(mint, []))

    def clear(self, mint: str) -> None:
        if mint in self._events:
            del self._events[mint]

    def should_exit(self, mint: str) -> bool:
        events = self._events.get(mint, [])
        if any(evt.level == "CRITICAL" for evt in events):
            return True
        major_roots = {evt.root_cause for evt in events if evt.level == "MAJOR"}
        return len(major_roots) >= 2
