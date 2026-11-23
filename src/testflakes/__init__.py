"""Rank flaky tests from a history of JUnit XML runs.

Every CI run leaves a JUnit report behind. Read a folder of them and the pattern
appears: the same three tests pass, fail, pass without a code change in between.

    >>> from testflakes import parse_run_text, flakiness
    >>> run = parse_run_text('<testsuite><testcase classname="t" name="a"/></testsuite>')
    >>> flakiness([run, run])[0].node_id
    't::a'

A flaky test is measured by how often it *flips* between passing and failing
compared with the previous run in which it appeared. A test that fails every time is
broken, not flaky, and a test that is skipped is excluded from the sequence rather
than counted as a flip.

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Case",
    "Run",
    "Stat",
    "flakiness",
    "newly_failing",
    "parse_run",
    "parse_run_text",
    "parse_runs",
]

__version__ = "0.1.1"

FAILING = ("failure", "error")


@dataclass(frozen=True)
class Case:
    """One test result."""

    node_id: str
    status: str
    time: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def failed(self) -> bool:
        return self.status in FAILING


@dataclass
class Run:
    """The results of one CI run, keyed by test."""

    source: str
    cases: dict[str, Case]

    def __len__(self) -> int:
        return len(self.cases)

    @property
    def failing(self) -> set[str]:
        return {node_id for node_id, case in self.cases.items() if case.failed}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _case_from(element: ET.Element) -> Case:
    node_id = element.get("classname", "")
    name = element.get("name", "")
    node_id = f"{node_id}::{name}" if node_id else name
    status = "passed"
    for child in element:
        tag = _local(child.tag)
        if tag in FAILING:
            status = tag
            break
        if tag == "skipped":
            status = "skipped"
            break
    try:
        duration = float(element.get("time") or 0.0)
    except ValueError:
        duration = 0.0
    return Case(node_id, status, duration)


def parse_run_text(text: str | bytes, source: str = "<string>") -> Run:
    """Parse one JUnit document. A repeated test keeps its first result."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"{source} is not valid JUnit XML: {exc}") from None
    if _local(root.tag) not in ("testsuite", "testsuites"):
        raise ValueError(f"{source} is not a JUnit report: root element is <{root.tag}>")
    cases: dict[str, Case] = {}
    for element in root.iter():
        if _local(element.tag) != "testcase":
            continue
        case = _case_from(element)
        cases.setdefault(case.node_id, case)
    return Run(source, cases)


def parse_run(path: str | Path) -> Run:
    """Parse a JUnit XML file."""
    path = Path(path)
    return parse_run_text(path.read_bytes(), source=str(path))


def parse_runs(paths: Sequence[str | Path]) -> list[Run]:
    """Parse several files, in the order given."""
    return [parse_run(path) for path in paths]


@dataclass(frozen=True)
class Stat:
    """How one test behaved across a history."""

    node_id: str
    runs: int
    passes: int
    failures: int
    errors: int
    skips: int
    flips: int

    @property
    def score(self) -> float:
        """Flips divided by the number of comparable transitions, in ``0.0..1.0``."""
        comparable = self.runs - self.skips
        return round(self.flips / (comparable - 1), 4) if comparable > 1 else 0.0

    @property
    def failed(self) -> bool:
        return bool(self.failures or self.errors)


def flakiness(runs: Sequence[Run], *, min_runs: int = 2) -> list[Stat]:
    """Rank the tests that appear in at least ``min_runs`` runs, flakiest first.

    Ties are broken by the number of failures, then by name, so the order is stable.
    """
    if min_runs < 1:
        raise ValueError("min_runs must be at least 1")
    order: list[str] = []
    history: dict[str, list[str]] = {}
    for run in runs:
        for node_id, case in run.cases.items():
            if node_id not in history:
                history[node_id] = []
                order.append(node_id)
            history[node_id].append(case.status)

    stats: list[Stat] = []
    for node_id in order:
        statuses = history[node_id]
        comparable = [status == "passed" for status in statuses if status != "skipped"]
        # A flip is a change of outcome between two runs in which the test appeared.
        flips = sum(
            1 for previous, current in zip(comparable, comparable[1:]) if previous != current
        )
        stats.append(
            Stat(
                node_id=node_id,
                runs=len(statuses),
                passes=statuses.count("passed"),
                failures=statuses.count("failure"),
                errors=statuses.count("error"),
                skips=statuses.count("skipped"),
                flips=flips,
            )
        )
    ranked = [stat for stat in stats if stat.runs >= min_runs]
    ranked.sort(key=lambda stat: (-stat.score, -stat.failures, -stat.errors, stat.node_id))
    return ranked


def newly_failing(before: Run, after: Run) -> list[str]:
    """Tests failing in ``after`` that were not failing in ``before``.

    A test that is new in ``after`` counts: it has no earlier pass to excuse it.
    """
    return sorted(
        node_id
        for node_id in after.failing
        if node_id not in before.cases or not before.cases[node_id].failed
    )
