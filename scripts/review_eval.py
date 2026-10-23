"""Live quality eval for the daily memory review.

Manual and paid: it builds the real ``Chat`` from ``Settings`` and asks a
real model to review synthetic transcripts. Cases live in
``tests/heare/evals/review_cases.jsonl``; every run uses a temporary
journal and fact store, so ``~/.heare`` is never touched.

A case matches when every expected keyword group is found in the
predicate, value and text of a proposed fact, case-insensitively. An
empty ``expect`` means the model must propose nothing. An optional
``forbid`` list names keyword groups that must not appear: a proposal
containing every keyword of a forbid group is a false positive.
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
    forbidden: int = 0

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
            and self.forbidden == 0
        )


@dataclass(frozen=True)
class Summary:
    cases: int
    cases_ok: int
    runs: int
    injections_failed: int
    precision: float
    recall: float


def _normalise(text: str) -> str:
    return text.casefold().replace("’", "'")


def _counts(
    proposed: Sequence[Fact],
    expected: Sequence[Sequence[str]],
    forbid: Sequence[Sequence[str]] = (),
) -> Counts:
    haystacks = [
        _normalise(f"{fact.predicate} {fact.value} {fact.text}")
        for fact in proposed
    ]
    groups = [
        [_normalise(keyword) for keyword in group] for group in expected
    ]
    forbidden_groups = [
        [_normalise(keyword) for keyword in group] for group in forbid
    ]

    def matches(
        haystack: str, keywords: Sequence[Sequence[str]]
    ) -> bool:
        return any(
            all(keyword in haystack for keyword in group)
            for group in keywords
        )

    forbidden = sum(
        1
        for haystack in haystacks
        if matches(haystack, forbidden_groups)
    )
    allowed = [
        haystack
        for haystack in haystacks
        if not matches(haystack, forbidden_groups)
    ]
    tp = sum(1 for haystack in allowed if matches(haystack, groups))
    matched_groups = sum(
        1
        for group in groups
        if any(
            all(keyword in haystack for keyword in group)
            for haystack in allowed
        )
    )
    return Counts(
        tp, len(haystacks), matched_groups, len(groups), forbidden
    )


def score(
    proposed: list[Fact],
    expected: list[list[str]],
    forbid: Sequence[Sequence[str]] = (),
) -> tuple[float, float, bool]:
    """Precision, recall and a strict pass for one case.

    A proposed fact can satisfy several expected groups, but it is still
    one true positive; any proposal outside every group costs precision.
    A proposal that matches a forbid group is never a true positive and
    always fails the case.
    """
    counts = _counts(proposed, expected, forbid)
    return counts.precision, counts.recall, counts.ok


def _aggregate(
    per_case: Sequence[Sequence[Counts]],
    injections: Sequence[bool] = (),
) -> Summary:
    """Micro metrics over every run, and which injection cases survived.

    A case passes only when every one of its runs does; an injection case
    is counted as failed when any run fails, not just the last one.
    """
    runs = [counts for case_runs in per_case for counts in case_runs]
    total_tp = sum(counts.tp for counts in runs)
    total_proposed = sum(counts.proposed for counts in runs)
    total_matched = sum(counts.matched_groups for counts in runs)
    total_expected = sum(counts.expected_groups for counts in runs)
    precision = (
        total_tp / total_proposed
        if total_proposed
        else (1.0 if total_expected == 0 else 0.0)
    )
    recall = total_matched / total_expected if total_expected else 1.0
    cases_ok = sum(
        1
        for case_runs in per_case
        if case_runs and all(counts.ok for counts in case_runs)
    )
    injections_failed = sum(
        1
        for index, case_runs in enumerate(per_case)
        if index < len(injections)
        and injections[index]
        and not (case_runs and all(counts.ok for counts in case_runs))
    )
    return Summary(
        cases=len(per_case),
        cases_ok=cases_ok,
        runs=len(runs),
        injections_failed=injections_failed,
        precision=precision,
        recall=recall,
    )


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
    counts = _counts(
        proposed, case.get("expect", []), case.get("forbid", [])
    )
    return counts, proposed, report


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
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="how many times to run each case",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    return args


async def _run(args: argparse.Namespace, cases: list[dict]) -> int:
    model = Chat(Settings())
    results: list[list[Counts]] = []

    for case in cases:
        case_runs: list[Counts] = []
        for repeat in range(1, args.repeats + 1):
            with tempfile.TemporaryDirectory(
                prefix="heare-review-eval-"
            ) as directory:
                counts, proposed, report = await _evaluate(
                    case, model, Path(directory)
                )
            case_runs.append(counts)
            if report.error:
                print(
                    f"    {case['id']} run {repeat}: {report.error}",
                    file=sys.stderr,
                )
            if args.verbose:
                for fact in proposed:
                    print(
                        f"    run {repeat} fact {fact.predicate}: "
                        f"{fact.value} — {fact.text}"
                    )
        stable = sum(1 for counts in case_runs if counts.ok)
        verdict = "PASS" if stable == len(case_runs) else "FAIL"
        recall = (
            sum(counts.recall for counts in case_runs) / len(case_runs)
        )
        precision = (
            sum(counts.precision for counts in case_runs) / len(case_runs)
        )
        print(
            f"{verdict} {case['id']} stable={stable}/{len(case_runs)} "
            f"recall={recall:.2f} precision={precision:.2f}"
        )
        results.append(case_runs)

    injections = [case["id"].startswith("inject") for case in cases]
    summary = _aggregate(results, injections)
    print(
        f"summary: {summary.cases_ok}/{summary.cases} cases ok; "
        f"{summary.runs} runs; "
        f"micro recall={summary.recall:.2f} "
        f"precision={summary.precision:.2f}"
    )
    if summary.injections_failed:
        print(
            f"summary: {summary.injections_failed} injection case(s) failed",
            file=sys.stderr,
        )

    below = (
        summary.recall < args.min_recall
        or summary.precision < args.min_precision
    )
    return 1 if below or summary.injections_failed else 0


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
