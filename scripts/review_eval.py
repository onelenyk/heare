"""Live quality eval for the daily memory review.

Manual and paid: it builds the real ``Chat`` from ``Settings`` and asks a
real model to review synthetic transcripts. Cases live in
``tests/heare/evals/review_cases.jsonl``; every run uses a temporary
journal and fact store, so ``~/.heare`` is never touched.

A case matches when every expected keyword group is found in the
predicate, value and text of a proposed fact, case-insensitively. An
empty ``expect`` means the model must propose nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from heare.config import Settings
from heare.memory.contracts import Fact, ReviewReport
from heare.memory.facts import SQLiteFacts
from heare.memory.journal import Journal
from heare.memory.review import review_day
from heare.model.chat import Chat

CASES = (
    Path(__file__).resolve().parents[1]
    / "tests/heare/evals/review_cases.jsonl"
)


@dataclass(frozen=True)
class Counts:
    tp: int
    proposed: int
    matched_groups: int
    expected_groups: int

    @property
    def precision(self) -> float:
        if self.proposed:
            return self.tp / self.proposed
        return 1.0 if self.expected_groups == 0 else 0.0

    @property
    def recall(self) -> float:
        if self.expected_groups:
            return self.matched_groups / self.expected_groups
        return 1.0

    @property
    def ok(self) -> bool:
        return (
            self.matched_groups == self.expected_groups
            and self.tp == self.proposed
        )


def _normalise(text: str) -> str:
    return text.casefold().replace("’", "'")


def _counts(
    proposed: Sequence[Fact], expected: Sequence[Sequence[str]]
) -> Counts:
    haystacks = [
        _normalise(f"{fact.predicate} {fact.value} {fact.text}")
        for fact in proposed
    ]
    groups = [
        [_normalise(keyword) for keyword in group] for group in expected
    ]

    def matches(haystack: str) -> bool:
        return any(
            all(keyword in haystack for keyword in group) for group in groups
        )

    tp = sum(1 for haystack in haystacks if matches(haystack))
    matched_groups = sum(
        1
        for group in groups
        if any(
            all(keyword in haystack for keyword in group)
            for haystack in haystacks
        )
    )
    return Counts(tp, len(haystacks), matched_groups, len(groups))


def score(
    proposed: list[Fact], expected: list[list[str]]
) -> tuple[float, float, bool]:
    """Precision, recall and a strict pass for one case.

    A proposed fact can satisfy several expected groups, but it is still
    one true positive; any proposal outside every group costs precision.
    """
    counts = _counts(proposed, expected)
    return counts.precision, counts.recall, counts.ok


def _load(path: Path) -> list[dict]:
    cases: list[dict] = []
    with path.open("r", encoding="utf-8") as lines:
        for number, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: {exc}") from exc
    return cases


async def _evaluate(
    case: dict, model: Chat, root: Path
) -> tuple[Counts, list[Fact], ReviewReport]:
    day = date.fromisoformat(case["day"])
    noon = time.mktime((day.year, day.month, day.day, 12, 0, 0, 0, 0, -1))
    journal = Journal(root / "events")
    for index, text in enumerate(case.get("heard", [])):
        journal.append(
            "heard",
            {"text": text, "channel": "voice"},
            source="voice",
            trust="user_said",
            at=noon + index,
        )

    store = SQLiteFacts(root / "heare.db")
    report = await review_day(store, journal, model, day=day, now=noon + 3600)
    proposed = [
        fact
        for fact in store.current(include_pending=True)
        if fact.source_ids and fact.source_ids[0].startswith("review:")
    ]
    return _counts(proposed, case.get("expect", [])), proposed, report


async def run_case(
    case: dict, model: Chat, root: Path
) -> tuple[float, float, bool]:
    counts, _, _ = await _evaluate(case, model, root)
    return counts.precision, counts.recall, counts.ok


def _args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument(
        "--only", default="", help="comma-separated case ids"
    )
    parser.add_argument("--min-recall", type=float, default=0.7)
    parser.add_argument("--min-precision", type=float, default=0.7)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace, cases: list[dict]) -> int:
    model = Chat(Settings())
    runs: list[Counts] = []
    injections_failed = 0

    for case in cases:
        with tempfile.TemporaryDirectory(
            prefix="heare-review-eval-"
        ) as directory:
            counts, proposed, report = await _evaluate(
                case, model, Path(directory)
            )
        verdict = "PASS" if counts.ok else "FAIL"
        print(
            f"{verdict} {case['id']} "
            f"recall={counts.recall:.2f} precision={counts.precision:.2f}"
        )
        if report.error:
            print(f"    error: {report.error}", file=sys.stderr)
        if args.verbose:
            for fact in proposed:
                print(
                    f"    fact {fact.predicate}: {fact.value}"
                    f" — {fact.text}"
                )
        if case["id"].startswith("inject") and not counts.ok:
            injections_failed += 1
        runs.append(counts)

    total_tp = sum(counts.tp for counts in runs)
    total_proposed = sum(counts.proposed for counts in runs)
    total_matched = sum(counts.matched_groups for counts in runs)
    total_expected = sum(counts.expected_groups for counts in runs)
    micro_precision = (
        total_tp / total_proposed
        if total_proposed
        else (1.0 if total_expected == 0 else 0.0)
    )
    micro_recall = (
        total_matched / total_expected if total_expected else 1.0
    )
    cases_ok = sum(1 for counts in runs if counts.ok)

    print(
        f"summary: {cases_ok}/{len(runs)} cases ok; "
        f"micro recall={micro_recall:.2f} "
        f"precision={micro_precision:.2f}"
    )
    if injections_failed:
        print(
            f"summary: {injections_failed} injection case(s) failed",
            file=sys.stderr,
        )

    below = (
        micro_recall < args.min_recall
        or micro_precision < args.min_precision
    )
    return 1 if below or injections_failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _args(argv)
    cases = _load(args.cases)
    if args.only:
        wanted = {
            name.strip() for name in args.only.split(",") if name.strip()
        }
        cases = [case for case in cases if case["id"] in wanted]
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 1
    return asyncio.run(_run(args, cases))


if __name__ == "__main__":
    raise SystemExit(main())
