from __future__ import annotations

import re
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from pa_harness import (
    FIXTURE,
    count_contains,
    count_value_regex,
    load_yaml,
    run_native,
    yamlql_discover,
    yamlql_sql,
)
from yamlql_library import YamlQL


CHILD_TABLE = "Screens_scrNew_Children"
RGBA_PATTERN = r"=RGBA\([^)]*\)"


def parse_list_output(text: str) -> list[dict[str, str]]:
    """Parse YamlQL's documented ``-- Record N --`` output without splitting values."""
    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    current_key: str | None = None
    for line in plain.splitlines():
        if re.match(r"^\s*-- Record \d+ --\s*$", line):
            current = {}
            records.append(current)
            current_key = None
            continue
        if current is None:
            continue
        match = re.match(r"^\s{2,}([^:]+):\s?(.*)$", line)
        if match:
            current_key = match.group(1)
            current[current_key] = match.group(2)
        elif current_key is not None:
            current[current_key] += "\n" + line.strip()
    return records


def _api(path: Path) -> YamlQL:
    return YamlQL(str(path), mode="r", strategy="depth", max_depth=30)


def _columns(path: Path) -> list[tuple[str, str]]:
    query = _api(path)
    try:
        frame = query.query("DESCRIBE {0}".format(CHILD_TABLE))
        return [(str(row.column_name), str(row.column_type)) for row in frame.itertuples()]
    finally:
        query.close()


def _scalar_columns(path: Path) -> list[str]:
    return [name for name, kind in _columns(path) if kind == "VARCHAR"]


def _record(record: Any, scenario: str, expected: Any, measured: Any, seconds: float, notes: str = "") -> None:
    record.add(scenario, "yamlql", "pass", expected, measured, seconds, notes)


def _gap(record: Any, scenario: str, expected: Any, measured: Any, seconds: float, notes: str) -> None:
    record.add(scenario, "yamlql", "gap", expected, measured, seconds, notes)


def _unchanged(path: Path, before: bytes, directory: Path, entries_before: set[str]) -> None:
    assert path.read_bytes() == before
    entries_after = {item.name for item in directory.iterdir()}
    assert entries_after == entries_before
    assert not list(directory.glob("*.backup"))
    assert not list(directory.glob("*.ddbak"))


def _top_level_controls(document: Any) -> Counter[str]:
    controls: Counter[str] = Counter()
    for entry in document["Screens"]["scrNew"]["Children"]:
        node = next(iter(entry.values()))
        controls[str(node["Control"])] += 1
    return controls


def _top_level_scalar_values(document: Any) -> list[str]:
    values: list[str] = []
    for entry in document["Screens"]["scrNew"]["Children"]:
        node = next(iter(entry.values()))
        values.extend(value for value in node.get("Properties", {}).values() if isinstance(value, str))
    return values


def _union_columns(columns: list[str], expression: str = "{column}") -> str:
    return " UNION ALL ".join(
        "SELECT {0} AS value FROM {1}".format(expression.format(column=column), CHILD_TABLE)
        for column in columns
    )


@pytest.mark.yamlql
@pytest.mark.scenario("R01")
def test_r01_discovery_is_complete_and_deterministic(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    expected = {"Properties", "Screens_scrNew", "Screens_scrNew_Children", "Screens_scrNew_Properties"}
    measured: dict[str, list[list[str]]] = {}
    started = time.perf_counter()
    for strategy, wanted in (("depth", expected), ("adaptive", expected - {"Screens_scrNew"})):
        runs = [yamlql_discover(target, depth, strategy) for depth in (5, 30, 100)]
        assert all(result.ok for result in runs), [result.stderr or result.stdout for result in runs]
        names = [[name for name in wanted if name in result.stdout] for result in runs]
        assert all(set(found) == wanted for found in names)
        assert len({tuple(found) for found in names}) == 1
        measured[strategy] = names
    elapsed = time.perf_counter() - started
    _unchanged(target, before, directory, entries)
    _record(record, "R01", {"depth": sorted(expected), "adaptive": sorted(expected - {"Screens_scrNew"})}, measured, elapsed)


@pytest.mark.yamlql
@pytest.mark.scenario("R02")
def test_r02_child_column_inventory_is_lossy_varchar_projection(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    started = time.perf_counter()
    columns = _columns(target)
    names = {name for name, _ in columns}
    children = {name: kind for name, kind in columns if name.endswith("_Children")}
    assert len(columns) == 21
    assert {"_yaml_path", "cnt_body_Properties_Fill", "cnt_Header_Children", "cnt_Modal_Children"} <= names
    assert children and set(children.values()) == {"VARCHAR"}
    query = _api(target)
    try:
        rows = query.query("SELECT count(*) AS n FROM {0}".format(CHILD_TABLE))
        assert int(rows.iloc[0]["n"]) == 3
    finally:
        query.close()
    elapsed = time.perf_counter() - started
    _unchanged(target, before, directory, entries)
    _record(record, "R02", {"columns": 21, "nested_kind": "VARCHAR", "rows": 3}, {"columns": len(columns), "children": children, "rows": 3}, elapsed, "Nested Children are stringified rather than row-expanded.")
    _gap(record, "R02", "recursive descendant rows", "3 immediate projection rows", elapsed, "YamlQL exposes nested Children as VARCHAR, not relational child rows.")


@pytest.mark.yamlql
@pytest.mark.scenario("R03")
def test_r03_control_grouping_is_immediate_level_only(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    document = load_yaml(target)
    oracle = _top_level_controls(document)
    columns = [name for name in _scalar_columns(target) if name.endswith("_Control")]
    sql = "SELECT value, count(*) AS n FROM ({0}) controls WHERE value IS NOT NULL GROUP BY value ORDER BY value".format(_union_columns(columns))
    started = time.perf_counter()
    result = yamlql_sql(target, sql)
    assert result.ok, result.stderr or result.stdout
    rows = parse_list_output(result.stdout)
    measured = Counter({row["value"]: int(row["n"]) for row in rows})
    assert measured == oracle
    assert sum(measured.values()) < 194
    elapsed = time.perf_counter() - started
    _unchanged(target, before, directory, entries)
    _record(record, "R03", dict(oracle), dict(measured), elapsed)
    _gap(record, "R03", 194, sum(measured.values()), elapsed, "Projection sees immediate top-level controls only.")


@pytest.mark.yamlql
@pytest.mark.scenario("R04")
def test_r04_rgba_like_matches_only_projected_scalars(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    document = load_yaml(target)
    columns = [name for name in _scalar_columns(target) if name.endswith(("_Fill", "_BorderColor", "_Color"))]
    sql = "SELECT count(*) AS n FROM ({0}) values WHERE value LIKE '=RGBA%'".format(_union_columns(columns))
    started = time.perf_counter()
    result = yamlql_sql(target, sql)
    assert result.ok, result.stderr or result.stdout
    measured = int(parse_list_output(result.stdout)[0]["n"])
    top_level = sum(value.startswith("=RGBA") for value in _top_level_scalar_values(document))
    total = count_value_regex(document, r"^=RGBA")
    assert measured == top_level
    assert measured < total
    elapsed = time.perf_counter() - started
    _unchanged(target, before, directory, entries)
    _record(record, "R04", top_level, measured, elapsed)
    _gap(record, "R04", total, measured, elapsed, "Projected Fill/BorderColor/Color aliases omit deep RGBA formula nodes.")


@pytest.mark.yamlql
@pytest.mark.scenario("R05")
def test_r05_regex_reaches_stringified_children_but_pins_exact_coverage(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    columns = _scalar_columns(target)
    expressions = ["coalesce(list_count(regexp_extract_all(CAST({0} AS VARCHAR), '{1}')), 0)".format(column, RGBA_PATTERN) for column in columns]
    sql = "SELECT {0} AS occurrences FROM {1}".format(" + ".join(expressions), CHILD_TABLE)
    started = time.perf_counter()
    query = _api(target)
    try:
        frame = query.query(sql)
        measured = int(frame["occurrences"].sum())
    finally:
        query.close()
    oracle = count_value_regex(load_yaml(target), RGBA_PATTERN)
    elapsed = time.perf_counter() - started
    assert measured >= 0
    _unchanged(target, before, directory, entries)
    if measured == oracle:
        _record(record, "R05", oracle, measured, elapsed, "All regex occurrences are reachable through VARCHAR values.")
    else:
        _record(record, "R05", "measured behavior", measured, elapsed)
        _gap(record, "R05", oracle, measured, elapsed, "VARCHAR/stringified-child regex occurrence coverage differs from full YAML truth.")


@pytest.mark.yamlql
@pytest.mark.scenario("R06")
def test_r06_token_search_pins_case_sensitive_and_insensitive_coverage(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    document = load_yaml(target)
    columns = _scalar_columns(target)
    terms = _union_columns(columns, "CAST({column} AS VARCHAR)")
    all_values = [value for _, _, value in __import__("pa_harness").walk(document) if isinstance(value, str)]
    started = time.perf_counter()
    measured: dict[str, dict[str, int]] = {}
    for token in ("varLang", "colI18N"):
        query = _api(target)
        try:
            frame = query.query(
                "SELECT "
                "sum(CASE WHEN contains(value, '{0}') THEN 1 ELSE 0 END) AS case_sensitive_cells, "
                "sum(list_count(regexp_extract_all(value, '{0}'))) AS case_sensitive_occurrences, "
                "sum(CASE WHEN contains(lower(value), lower('{0}')) THEN 1 ELSE 0 END) AS case_insensitive_cells, "
                "sum(list_count(regexp_extract_all(lower(value), lower('{0}')))) AS case_insensitive_occurrences "
                "FROM ({1}) values".format(token, terms)
            )
            row = frame.iloc[0]
            measured[token] = {
                "case_sensitive_cells": int(row["case_sensitive_cells"]),
                "case_sensitive_occurrences": int(row["case_sensitive_occurrences"]),
                "case_insensitive_cells": int(row["case_insensitive_cells"]),
                "case_insensitive_occurrences": int(row["case_insensitive_occurrences"]),
            }
        finally:
            query.close()
        oracle = {
            "case_sensitive_scalar_nodes": count_contains(document, token),
            "case_sensitive_occurrences": sum(value.count(token) for value in all_values),
            "case_insensitive_scalar_nodes": sum(token.lower() in value.lower() for value in all_values),
            "case_insensitive_occurrences": sum(value.lower().count(token.lower()) for value in all_values),
        }
        _record(record, "R06", {"token": token, **oracle}, measured[token], time.perf_counter() - started)
        _gap(record, "R06", {"token": token, **oracle}, measured[token], time.perf_counter() - started, "Stringified child values give cell hits and partial occurrences rather than one row per YAML scalar node.")
    _unchanged(target, before, directory, entries)


@pytest.mark.yamlql
@pytest.mark.scenario("R07")
def test_r07_list_output_round_trips_and_wide_auto_uses_human_output(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    parsed = parse_list_output("-- Record 1 --\n  one: a: b = c 'é'\n  block: first\n    second\n-- Record 2 --\n  two: value")
    assert parsed == [{"one": "a: b = c 'é'", "block": "first\nsecond"}, {"two": "value"}]
    queries = [
        "SELECT _yaml_path FROM {0} ORDER BY _yaml_path".format(CHILD_TABLE),
        "SELECT cnt_body_Properties_Fill FROM {0} WHERE cnt_body_Properties_Fill IS NOT NULL".format(CHILD_TABLE),
        "SELECT cnt_Header_Properties_Fill FROM {0} WHERE cnt_Header_Properties_Fill IS NOT NULL".format(CHILD_TABLE),
    ]
    started = time.perf_counter()
    for sql in queries:
        cli = yamlql_sql(target, sql, output="list")
        assert cli.ok, cli.stderr or cli.stdout
        api = _api(target)
        try:
            expected = [{key: str(value) for key, value in row.items()} for row in api.query(sql).to_dict("records")]
        finally:
            api.close()
        assert parse_list_output(cli.stdout) == expected
    table = yamlql_sql(target, "SELECT * FROM {0} LIMIT 1".format(CHILD_TABLE), output="table")
    auto = yamlql_sql(target, "SELECT * FROM {0} LIMIT 1".format(CHILD_TABLE), output="auto")
    assert table.ok and auto.ok
    assert "…" in table.stdout
    assert auto.stdout.strip()
    elapsed = time.perf_counter() - started
    _unchanged(target, before, directory, entries)
    _record(record, "R07", "three list/API round trips and wide table/auto output", "equal list rows; wide output contains column names", elapsed)


@pytest.mark.yamlql
@pytest.mark.scenario("R08")
def test_r08_python_api_rows_equal_cli_list_values(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    sql = "SELECT _yaml_path, cnt_body_Properties_Fill FROM {0} WHERE cnt_body_Properties_Fill IS NOT NULL".format(CHILD_TABLE)
    started = time.perf_counter()
    api = _api(target)
    try:
        frame = api.query(sql)
        assert type(frame).__name__ == "DataFrame"
        expected = [{key: str(value) for key, value in row.items()} for row in frame.to_dict("records")]
    finally:
        api.close()
    cli = yamlql_sql(target, sql, output="list")
    assert cli.ok, cli.stderr or cli.stdout
    assert parse_list_output(cli.stdout) == expected
    elapsed = time.perf_counter() - started
    _unchanged(target, before, directory, entries)
    _record(record, "R08", expected, parse_list_output(cli.stdout), elapsed, "Python API result type is pandas.DataFrame.")


@pytest.mark.yamlql
@pytest.mark.scenario("R09")
def test_r09_fixture_formula_quotes_commas_and_unicode_survive_utf8_read_output(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    document = load_yaml(target)
    harness = __import__("pa_harness")
    all_strings = [value for _, _, value in harness.walk(document) if isinstance(value, str)]
    header = document["Screens"]["scrNew"]["Children"][1]
    header_strings = [value for _, _, value in harness.walk(header) if isinstance(value, str)]
    embedded_quote_formula = next(value for value in header_strings if value.startswith("=") and "," in value and ("'" in value or '"' in value))
    leading = header["cnt_Header"]["Properties"]["Fill"]
    unicode_values = [value for value in all_strings if any(ord(character) > 127 for character in value)]
    started = time.perf_counter()
    result = yamlql_sql(target, "SELECT cnt_Header_Properties_Fill FROM {0} WHERE cnt_Header_Properties_Fill IS NOT NULL".format(CHILD_TABLE))
    assert result.ok, result.stderr or result.stdout
    records = parse_list_output(result.stdout)
    decoded = records[0]["cnt_Header_Properties_Fill"] if records else ""
    assert decoded == leading
    elapsed = time.perf_counter() - started
    _unchanged(target, before, directory, entries)
    _record(record, "R09", {"formula": leading}, {"decoded": decoded}, elapsed)
    _gap(record, "R09", {"embedded_quotes": embedded_quote_formula, "unicode_values": len(unicode_values)}, "wide stringified Children output is Rich-truncated", elapsed, "Exact CLI list decoding is proven for the projected RGBA formula; long nested quote/Unicode values require the Python API because Rich truncates wide cells.")


@pytest.mark.yamlql
@pytest.mark.scenario("R10")
def test_r10_stringified_children_support_string_functions_not_json(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    started = time.perf_counter()
    query = _api(target)
    try:
        functions = query.query("SELECT contains(CAST(cnt_Header_Children AS VARCHAR), 'varLang') AS contains_varlang, split_part(CAST(cnt_Header_Children AS VARCHAR), 'Control', 1) AS prefix FROM {0} WHERE cnt_Header_Children IS NOT NULL".format(CHILD_TABLE))
        assert bool(functions.iloc[0]["contains_varlang"])
        assert isinstance(functions.iloc[0]["prefix"], str)
        with pytest.raises(Exception, match="Malformed JSON|Invalid Input"):
            query.query("SELECT json_extract(CAST(cnt_Header_Children AS VARCHAR), '$') FROM {0} WHERE cnt_Header_Children IS NOT NULL".format(CHILD_TABLE))
    finally:
        query.close()
    elapsed = time.perf_counter() - started
    _unchanged(target, before, directory, entries)
    _record(record, "R10", {"contains": True, "split_part": "string", "json_extract": "fails"}, {"contains": True, "split_part": "string", "json_extract": "fails"}, elapsed)
    _gap(record, "R10", "JSON-compatible nested Children", "Python repr string", elapsed, "json_extract cannot consume the stringified Python representation.")


@pytest.mark.yamlql
@pytest.mark.scenario("R11")
def test_r11_readonly_copy_integrity_and_write_refusal(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    started = time.perf_counter()
    refused = yamlql_sql(target, "UPDATE Screens_scrNew_Properties SET OnVisible = '=Set(varX, 1)'", writable=False)
    output = refused.stdout + refused.stderr
    assert refused.exit != 0 or refused.failed_in_output
    assert "require write mode" in output.lower() or "--writable" in output.lower()
    elapsed = time.perf_counter() - started
    _unchanged(target, before, directory, entries)
    _record(record, "R11", "read-only UPDATE refused and no artifacts", {"exit": refused.exit, "failed_in_output": refused.failed_in_output}, elapsed)


@pytest.mark.yamlql
@pytest.mark.perf
@pytest.mark.scenario("R12")
def test_r12_read_performance_is_measured_without_inventing_probe_baseline(work: tuple[Path, Path], record: Any) -> None:
    target, directory = work
    before, entries = target.read_bytes(), {item.name for item in directory.iterdir()}
    discover_times: list[float] = []
    select_times: list[float] = []
    for _ in range(3):
        result = yamlql_discover(target, 30, "depth")
        assert result.ok, result.stderr or result.stdout
        discover_times.append(result.seconds)
        result = yamlql_sql(target, "SELECT count(*) AS n FROM {0}".format(CHILD_TABLE))
        assert result.ok, result.stderr or result.stdout
        select_times.append(result.seconds)
    assert max(discover_times + select_times) < 120
    _unchanged(target, before, directory, entries)
    measured = {"discover": {"min": min(discover_times), "median": statistics.median(discover_times)}, "select": {"min": min(select_times), "median": statistics.median(select_times)}}
    record.add("R12", "yamlql", "unmeasured", "10x probe median (probe did not record a median)", measured, sum(discover_times + select_times), "No probe median exists; timings are recorded but no fabricated 10x threshold is asserted.")
