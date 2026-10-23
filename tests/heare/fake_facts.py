from __future__ import annotations

import time
from dataclasses import replace

from heare.memory.contracts import CONFIRMED, REJECTED, Fact


class FakeFacts:
    def __init__(self) -> None:
        self._by_id: dict[int, Fact] = {}
        self._by_source: dict[str, int] = {}
        self._next = 1

    def add(self, fact: Fact) -> int:
        for source in fact.source_ids:
            if source in self._by_source:
                return self._by_source[source]
        stored = replace(fact, id=self._next)
        self._by_id[self._next] = stored
        for source in stored.source_ids:
            self._by_source[source] = self._next
        self._next += 1
        return stored.id

    def has_source(self, source_id: str) -> bool:
        return source_id in self._by_source

    def get(self, fact_id: int) -> Fact | None:
        return self._by_id.get(fact_id)

    def current(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        now: float | None = None,
        include_pending: bool = False,
    ) -> list[Fact]:
        now = time.time() if now is None else now
        return [
            fact for fact in self._by_id.values()
            if (include_pending or fact.status == CONFIRMED)
            and fact.valid_from <= now
            and (fact.valid_to is None or now < fact.valid_to)
            and (subject is None or fact.subject == subject)
            and (predicate is None or fact.predicate == predicate)
        ]

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        now: float | None = None,
        include_pending: bool = False,
    ) -> list[Fact]:
        needle = query.casefold()
        found = [
            fact for fact in self.current(
                now=now, include_pending=include_pending
            )
            if needle in fact.text.casefold()
            or needle in fact.value.casefold()
            or needle in fact.predicate.casefold()
        ]
        return found[:limit]

    def confirm(self, fact_id: int) -> bool:
        fact = self._by_id.get(fact_id)
        if fact is None or fact.status == REJECTED:
            return False
        self._by_id[fact_id] = replace(fact, status=CONFIRMED)
        return True

    def reject(self, fact_id: int) -> bool:
        if fact_id not in self._by_id:
            return False
        self._by_id[fact_id] = replace(
            self._by_id[fact_id], status=REJECTED
        )
        return True

    def supersede(self, old_id: int, new: Fact) -> int:
        old = self._by_id.get(old_id)
        if old is not None:
            self._by_id[old_id] = replace(old, valid_to=new.valid_from)
        return self.add(replace(new, supersedes=old_id))

    def forget(self, fact_id: int) -> bool:
        fact = self._by_id.get(fact_id)
        if fact is None:
            return False
        self._by_id[fact_id] = replace(
            fact, status=REJECTED, valid_to=time.time()
        )
        return True
