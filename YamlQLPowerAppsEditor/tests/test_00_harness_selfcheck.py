from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from conftest import fixture_hash_matches
from pa_harness import (
    FIXTURE,
    FIXTURE_SHA256,
    assert_only_changes,
    copy_fixture,
    count_property,
    find_controls,
    load_yaml,
    ps_quote_single,
    run_ps51,
    sql_quote,
    structural_diff,
    yamlpath_get,
    yamlpath_paths,
    yamlpath_set,
    yamlpath_validate,
    yamlql_discover,
    yamlql_sql,
)

COMMITTED_FIXTURE_SHA256 = "E7450F3128104BAC22F5D7CD3C93D0857D42BC5A6C4F9065C6117BE7DDE2F6C6"


def test_fixture_hash_matches_committed_baseline() -> None:
    assert FIXTURE_SHA256.upper() == COMMITTED_FIXTURE_SHA256
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest().upper() == COMMITTED_FIXTURE_SHA256


def test_copy_fixture_is_independent_and_guard_detects_alteration(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    assert copied.read_bytes() == FIXTURE.read_bytes()
    copied.write_text("changed\n", encoding="utf-8")
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest().upper() == COMMITTED_FIXTURE_SHA256
    assert not fixture_hash_matches(copied, COMMITTED_FIXTURE_SHA256)


@pytest.mark.yamlql
@pytest.mark.slow
def test_yamlql_discover_runs() -> None:
    result = yamlql_discover(FIXTURE)
    assert result.ok, result.stderr or result.stdout


@pytest.mark.yamlpath
@pytest.mark.parametrize("wrapper", [yamlpath_set, yamlpath_get, yamlpath_paths, yamlpath_validate])
def test_yamlpath_wrappers_report_versions(wrapper: object) -> None:
    result = wrapper(["--version"])
    assert result.exit == 0, result.stderr


def test_run_ps51_is_windows_powershell_51() -> None:
    result = run_ps51("$PSVersionTable.PSVersion.Major", FIXTURE.parent)
    assert result.exit == 0, result.stderr
    assert result.stdout.strip() == "5"


def test_structural_diff_is_semantic_and_precise() -> None:
    before = {"nested": {"value": "one"}}
    assert structural_diff(before, {"nested": {"value": "one"}}) == []
    diff = structural_diff(before, {"nested": {"value": "two"}})
    assert diff == [(("nested", "value"), "one", "two")]
    assert_only_changes(diff, [("nested", "value")])


@pytest.mark.yamlql
@pytest.mark.yamlpath
@pytest.mark.slow
def test_quotes_round_trip_through_real_tools(tmp_path: Path) -> None:
    target = tmp_path / "synthetic.yaml"
    nasty = "=Set(varO'Hare, $x, `tick)"
    # Discovery of this fixture-shaped document reports a writable
    # `scrNew_Properties` table only when the Properties mapping has multiple
    # values; do not infer fixture table names here.
    target.write_text(
        "Screens:\n  scrNew:\n    Properties:\n      LoadingSpinnerColor: =RGBA(1, 2, 3, 1)\n      OnVisible: old\n    Children:\n      - cnt_body:\n          Control: GroupContainer@1.5.0\n          Properties:\n            Fill: =RGBA(2, 3, 4, 1)\n",
        encoding="utf-8",
    )

    sql = "UPDATE scrNew_Properties SET OnVisible = {0}".format(sql_quote(nasty))
    updated = yamlql_sql(target, sql, writable=True)
    # YamlQL saves this update but then emits a benign "Binder Error" while
    # synchronizing its in-memory view. `ok` intentionally remains conservative
    # because the required failure detector treats `Error:` as a failure signal.
    assert updated.exit == 0, updated.stderr or updated.stdout
    assert load_yaml(target)["Screens"]["scrNew"]["Properties"]["OnVisible"] == nasty

    replacement = "=Set(varO'Hare, $y, `again)"
    # P-Y06 recommends one native argument per token; the wrapper preserves it.
    changed = yamlpath_set(["-m", "-g", "Screens.scrNew.Properties.OnVisible", "-a", replacement, str(target)])
    assert changed.exit == 0, changed.stderr
    read_back = yamlpath_get(["-p", "Screens.scrNew.Properties.OnVisible", str(target)])
    assert read_back.exit == 0, read_back.stderr
    assert replacement in read_back.stdout
    assert ps_quote_single(nasty) == "'=Set(varO''Hare, $x, `tick)'"


@pytest.mark.yamlql
def test_yamlql_output_failure_is_not_treated_as_success(tmp_path: Path) -> None:
    target = tmp_path / "synthetic.yaml"
    target.write_text(
        "Screens:\n  scrNew:\n    Properties:\n      LoadingSpinnerColor: =RGBA(1, 2, 3, 1)\n      OnVisible: old\n    Children:\n      - cnt_body:\n          Control: GroupContainer@1.5.0\n          Properties:\n            Fill: =RGBA(2, 3, 4, 1)\n",
        encoding="utf-8",
    )
    before = target.read_bytes()

    result = yamlql_sql(
        target,
        "UPDATE Screens_scrNew_Properties_Missing SET OnVisible = 'new'",
        writable=True,
    )

    # Task 9-1 correction: YamlQL reports this failed write with exit 0.
    assert result.exit == 0, result.stderr or result.stdout
    assert result.failed_in_output
    assert not result.ok
    assert target.read_bytes() == before


def test_fixture_ground_truth_matches_probe_p_y05() -> None:
    document = load_yaml(FIXTURE)
    assert len(FIXTURE.read_text(encoding="utf-8").splitlines()) == 4236
    controls = find_controls(document)
    assert len(controls) == 194
    assert len(find_controls(document, "Label@2.5.1")) == 104
    assert len(find_controls(document, "TypedDataCard@1.0.7")) == 33
    assert len(find_controls(document, "Classic/ComboBox@2.4.0")) == 24
    assert len(find_controls(document, "Classic/TextInput@2.3.2")) == 8
    assert count_property(document, "Fill") == 48
    assert count_property(document, "BorderColor") == 186
    assert count_property(document, "Visible") == 74
