"""Tests for testflakes."""

import json

import pytest

from testflakes import (
    Case,
    Stat,
    flakiness,
    newly_failing,
    parse_run,
    parse_run_text,
    parse_runs,
)


def doc(*cases):
    return '<testsuite name="t">' + "".join(cases) + "</testsuite>"


def case(name, status="passed", classname="mod"):
    if status == "passed":
        return f'<testcase classname="{classname}" name="{name}"/>'
    if status == "skipped":
        return f'<testcase classname="{classname}" name="{name}"><skipped/></testcase>'
    return f'<testcase classname="{classname}" name="{name}"><{status} message="m"/></testcase>'


def run(status="passed", classname="mod", name="test"):
    """One run in which a single test has the given status."""
    return parse_run_text(doc(case(name, status, classname)))


def history(*statuses, name="test"):
    return [run(status, name=name) for status in statuses]


class TestParse:
    def test_reads_a_testcase(self):
        parsed = parse_run_text(doc(case("a")))
        assert list(parsed.cases) == ["mod::a"]

    def test_cases_without_a_classname_keep_the_name(self):
        parsed = parse_run_text(doc('<testcase name="a"/>'))
        assert list(parsed.cases) == ["a"]

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("", "passed"),
            ("<skipped/>", "skipped"),
            ("<failure message='m'/>", "failure"),
            ("<error message='m'/>", "error"),
        ],
    )
    def test_status_is_read(self, body, expected):
        parsed = parse_run_text(doc(f'<testcase classname="m" name="a">{body}</testcase>'))
        assert parsed.cases["m::a"].status == expected

    def test_time_is_read(self):
        parsed = parse_run_text(doc('<testcase classname="m" name="a" time="1.5"/>'))
        assert parsed.cases["m::a"].time == 1.5

    def test_invalid_time_is_zero(self):
        parsed = parse_run_text(doc('<testcase classname="m" name="a" time="slow"/>'))
        assert parsed.cases["m::a"].time == 0.0

    def test_a_testsuites_wrapper_is_accepted(self):
        parsed = parse_run_text("<testsuites><testsuite>" + case("a") + "</testsuite></testsuites>")
        assert len(parsed) == 1

    def test_a_repeated_test_keeps_the_first_result(self):
        parsed = parse_run_text(doc(case("a"), case("a", "failure")))
        assert parsed.cases["mod::a"].status == "passed"

    def test_namespaced_documents_work(self):
        parsed = parse_run_text(
            '<testsuite xmlns="urn:junit"><testcase classname="m" name="a"/></testsuite>'
        )
        assert parsed.cases["m::a"].status == "passed"

    def test_invalid_xml_raises_value_error(self):
        with pytest.raises(ValueError, match="not valid JUnit XML"):
            parse_run_text("<testsuite>")

    def test_wrong_root_raises_value_error(self):
        with pytest.raises(ValueError, match="not a JUnit report"):
            parse_run_text("<report/>")

    def test_parse_run_uses_the_file_name_as_source(self, tmp_path):
        path = tmp_path / "run.xml"
        path.write_text(doc(case("a")), encoding="utf-8")
        assert parse_run(path).source == str(path)

    def test_parse_runs_keeps_the_order(self, tmp_path):
        first = tmp_path / "1.xml"
        second = tmp_path / "2.xml"
        first.write_text(doc(case("a")), encoding="utf-8")
        second.write_text(doc(case("b")), encoding="utf-8")
        assert [len(run_) for run_ in parse_runs([first, second])] == [1, 1]


class TestCaseAndRun:
    def test_case_helpers(self):
        assert Case("a", "passed").passed
        assert not Case("a", "passed").failed
        assert Case("a", "failure").failed
        assert Case("a", "error").failed

    def test_run_exposes_its_failing_set(self):
        parsed = parse_run_text(doc(case("a"), case("b", "failure")))
        assert parsed.failing == {"mod::b"}


class TestFlakiness:
    def test_a_stable_pass_is_not_flaky(self):
        stats = flakiness(history("passed", "passed", "passed"))
        assert stats[0].score == 0.0
        assert stats[0].flips == 0

    def test_a_consistent_failure_is_not_flaky(self):
        stats = flakiness(history("failure", "failure", "failure"))
        assert stats[0].score == 0.0
        assert stats[0].failed

    def test_alternating_outcomes_score_one(self):
        stats = flakiness(history("passed", "failure", "passed", "failure"))
        assert stats[0].flips == 3
        assert stats[0].score == 1.0

    def test_one_flip_in_four_runs(self):
        stats = flakiness(history("passed", "passed", "passed", "failure"))
        assert stats[0].flips == 1
        assert stats[0].score == pytest.approx(1 / 3, abs=1e-3)

    def test_skips_are_excluded_from_the_sequence(self):
        stats = flakiness(history("passed", "skipped", "passed"))
        assert stats[0].skips == 1
        assert stats[0].flips == 0
        assert stats[0].score == 0.0

    def test_error_to_failure_is_not_a_flip(self):
        stats = flakiness(history("failure", "error"))
        assert stats[0].flips == 0

    def test_counts_are_kept(self):
        stats = flakiness(history("passed", "failure", "skipped", "error"))[0]
        assert (stats.runs, stats.passes, stats.failures, stats.errors, stats.skips) == (
            4,
            1,
            1,
            1,
            1,
        )

    def test_flakiest_test_comes_first(self):
        runs = [
            parse_run_text(doc(case("flaky", "passed"), case("steady", "passed"))),
            parse_run_text(doc(case("flaky", "failure"), case("steady", "passed"))),
            parse_run_text(doc(case("flaky", "passed"), case("steady", "passed"))),
        ]
        assert [stat.node_id for stat in flakiness(runs)] == ["mod::flaky", "mod::steady"]

    def test_min_runs_filters_short_histories(self):
        assert flakiness(history("passed"), min_runs=2) == []
        assert len(flakiness(history("passed"), min_runs=1)) == 1

    def test_min_runs_must_be_positive(self):
        with pytest.raises(ValueError, match="at least 1"):
            flakiness([], min_runs=0)

    def test_no_runs_gives_no_stats(self):
        assert flakiness([]) == []

    def test_a_test_missing_from_a_run_does_not_count_as_a_transition(self):
        runs = [
            parse_run_text(doc(case("a", "passed"), case("b"))),
            parse_run_text(doc(case("b"))),
            parse_run_text(doc(case("a", "failure"), case("b"))),
        ]
        stat = next(s for s in flakiness(runs) if s.node_id == "mod::a")
        assert stat.runs == 2
        assert stat.flips == 1

    def test_ordering_is_stable_for_equal_scores(self):
        runs = [
            parse_run_text(doc(case("z"), case("a"))),
            parse_run_text(doc(case("z"), case("a"))),
        ]
        assert [stat.node_id for stat in flakiness(runs)] == ["mod::a", "mod::z"]

    def test_more_failures_break_a_score_tie(self):
        runs = [
            parse_run_text(doc(case("x", "passed"), case("y", "passed"))),
            parse_run_text(doc(case("x", "failure"), case("y", "passed"))),
            parse_run_text(doc(case("x", "failure"), case("y", "passed"))),
            parse_run_text(doc(case("x", "passed"), case("y", "failure"))),
            parse_run_text(doc(case("x", "passed"), case("y", "passed"))),
        ]
        ranked = flakiness(runs)
        assert [stat.failures for stat in ranked] == [2, 1]


class TestNewlyFailing:
    def test_reports_a_regression(self):
        before = parse_run_text(doc(case("a"), case("b")))
        after = parse_run_text(doc(case("a"), case("b", "failure")))
        assert newly_failing(before, after) == ["mod::b"]

    def test_a_still_failing_test_is_not_new(self):
        before = parse_run_text(doc(case("a", "failure")))
        after = parse_run_text(doc(case("a", "failure")))
        assert newly_failing(before, after) == []

    def test_a_fixed_test_is_not_reported(self):
        before = parse_run_text(doc(case("a", "failure")))
        after = parse_run_text(doc(case("a")))
        assert newly_failing(before, after) == []

    def test_a_brand_new_failing_test_counts(self):
        before = parse_run_text(doc(case("a")))
        after = parse_run_text(doc(case("a"), case("fresh", "error")))
        assert newly_failing(before, after) == ["mod::fresh"]

    def test_results_are_sorted(self):
        before = parse_run_text(doc())
        after = parse_run_text(doc(case("z", "failure"), case("a", "failure")))
        assert newly_failing(before, after) == ["mod::a", "mod::z"]


class TestStat:
    def test_score_of_a_single_comparable_run_is_zero(self):
        assert Stat("a", runs=1, passes=1, failures=0, errors=0, skips=0, flips=0).score == 0.0

    def test_score_of_only_skips_is_zero(self):
        assert Stat("a", runs=2, passes=0, failures=0, errors=0, skips=2, flips=0).score == 0.0

    def test_failed_reports_errors(self):
        assert Stat("a", 2, 1, 0, 1, 0, 0).failed


class TestCommandLine:
    def run(self, argv):
        from testflakes.__main__ import main

        return main(argv)

    def reports(self, tmp_path, *status_sequences):
        paths = []
        for index, status in enumerate(status_sequences, start=1):
            path = tmp_path / f"run{index}.xml"
            path.write_text(doc(case("test", status)), encoding="utf-8")
            paths.append(str(path))
        return paths

    def test_table_output(self, tmp_path, capsys):
        paths = self.reports(tmp_path, "passed", "failure", "passed")
        assert self.run(paths) == 0
        out = capsys.readouterr().out
        assert "mod::test" in out
        assert "score" in out

    def test_json_output(self, tmp_path, capsys):
        paths = self.reports(tmp_path, "passed", "failure")
        assert self.run([*paths, "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["node_id"] == "mod::test"
        assert payload[0]["flips"] == 1

    def test_top_limits_the_table(self, tmp_path, capsys):
        first = tmp_path / "1.xml"
        second = tmp_path / "2.xml"
        first.write_text(doc(case("a"), case("b")), encoding="utf-8")
        second.write_text(doc(case("a"), case("b")), encoding="utf-8")
        assert self.run([str(first), str(second), "--top", "1"]) == 0
        assert len(capsys.readouterr().out.strip().splitlines()) == 2

    def test_new_lists_regressions_and_exits_one(self, tmp_path, capsys):
        before = tmp_path / "before.xml"
        after = tmp_path / "after.xml"
        before.write_text(doc(case("a")), encoding="utf-8")
        after.write_text(doc(case("a", "failure")), encoding="utf-8")
        assert self.run([str(before), str(after), "--new"]) == 1
        assert "mod::a" in capsys.readouterr().out

    def test_new_without_regressions_exits_zero(self, tmp_path):
        before = tmp_path / "before.xml"
        after = tmp_path / "after.xml"
        for path in (before, after):
            path.write_text(doc(case("a")), encoding="utf-8")
        assert self.run([str(before), str(after), "--new"]) == 0

    def test_missing_file_exits_two(self, tmp_path, capsys):
        assert self.run([str(tmp_path / "nope.xml")]) == 2
        assert "no such file" in capsys.readouterr().err

    def test_bad_xml_exits_two(self, tmp_path, capsys):
        path = tmp_path / "bad.xml"
        path.write_text("<testsuite>", encoding="utf-8")
        assert self.run([str(path)]) == 2
        assert "not valid JUnit XML" in capsys.readouterr().err

    def test_min_runs_warns_when_nothing_matches(self, tmp_path, capsys):
        path = tmp_path / "one.xml"
        path.write_text(doc(case("a")), encoding="utf-8")
        assert self.run([str(path), "--min-runs", "3"]) == 0
        assert "no test appears in 3 reports" in capsys.readouterr().out


class TestRigour:
    """The --new path, its argument checking and its JSON shape."""

    def write(self, tmp_path, name, cases):
        path = tmp_path / name
        path.write_text(doc(*cases), encoding="utf-8")
        return str(path)

    def test_new_needs_exactly_two_reports(self, tmp_path, capsys):
        from testflakes.__main__ import main

        path = self.write(tmp_path, "a.xml", [case("a")])
        assert main([path, "--new"]) == 2
        assert "BEFORE AFTER" in capsys.readouterr().err

    def test_new_lists_the_failures(self, tmp_path, capsys):
        from testflakes.__main__ import main

        before = self.write(tmp_path, "a.xml", [case("a"), case("b")])
        after = self.write(tmp_path, "b.xml", [case("a", "failure"), case("b")])
        assert main([before, after, "--new"]) == 1
        out = capsys.readouterr().out
        assert "mod::a" in out
        assert "1 newly failing" in out

    def test_new_as_json(self, tmp_path, capsys):
        from testflakes.__main__ import main

        before = self.write(tmp_path, "a.xml", [case("a")])
        after = self.write(tmp_path, "b.xml", [case("a", "error")])
        assert main([before, after, "--new", "--json"]) == 1
        assert json.loads(capsys.readouterr().out) == {"newly_failing": ["mod::a"]}

    def test_a_malformed_report_exits_two(self, tmp_path, capsys):
        from testflakes.__main__ import main

        path = tmp_path / "bad.xml"
        path.write_text("<testsuite>", encoding="utf-8")
        assert main([str(path)]) == 2
        assert "error" in capsys.readouterr().err
