"""Characterization tests for the known deep-YAML gaps; each pins current behavior."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import warnings

import pytest
import yaml
from ruamel.yaml import YAML

from yamlql_library import YamlQL
from yamlql_library.writer import YamlWriter


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "test_data" / "power_apps_mini.yaml"
CHILD_TABLE = "Screens_scrNew_Children"
FILL_COLUMN = "cnt_body_Properties_Fill"


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "power_apps_mini.yaml"
    shutil.copy2(FIXTURE, target)
    return target


def _yql(path: Path, writable: bool = False) -> YamlQL:
    return YamlQL(str(path), strategy="adaptive", max_depth=30, mode="rw" if writable else "r")


def _command(path: Path, sql: str, writable: bool = False, env=None):
    command = [sys.executable, "-m", "yamlql_library.cli", "sql", "--file", str(path), "--strategy", "adaptive"]
    if writable:
        command.append("--writable")
    command.extend(sql.split())
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)


def test_gap_g01_nested_controls_are_lossy_python_repr(tmp_path):
    """G1: deep controls are not rows; desired tree rows; planned 14-2..14-7."""
    yql = _yql(_copy_fixture(tmp_path))
    try:
        frame = yql.query("SELECT cnt_body_Children FROM {0} WHERE cnt_body_Children IS NOT NULL".format(CHILD_TABLE))
        cell = str(frame.iloc[0, 0])
        assert "'Properties':" in cell
        assert len(yql.query("SELECT * FROM {0}".format(CHILD_TABLE))) < 12
    finally:
        yql.close()


def test_gap_g02_flattened_update_fails_without_changing_bytes(tmp_path):
    """G2: flattened child UPDATE fails; desired correct/fail-closed write; planned 15-4, 15-11 optional."""
    path = _copy_fixture(tmp_path)
    before = path.read_bytes()
    yql = YamlQL(str(path), strategy="depth", max_depth=30, mode="rw")
    try:
        with pytest.raises(Exception, match=FILL_COLUMN):
            yql.query("UPDATE {0} SET {1} = '=RGBA(1, 2, 3, 1)' WHERE {1} IS NOT NULL".format(CHILD_TABLE, FILL_COLUMN))
    finally:
        yql.close()
    assert path.read_bytes() == before


def test_gap_g03_child_insert_creates_phantom_top_level_relation(tmp_path):
    """G3: child INSERT reports success and creates a phantom root key; desired nested insertion; planned 15-5, 15-10."""
    path = _copy_fixture(tmp_path)
    yql = _yql(path, writable=True)
    try:
        result = yql.query("INSERT INTO {0} ({1}) VALUES ('=RGBA(1, 2, 3, 1)')".format(CHILD_TABLE, FILL_COLUMN))
        assert result["success"] is True
    finally:
        yql.close()
    assert CHILD_TABLE in yaml.safe_load(path.read_text(encoding="utf-8"))


def test_gap_g04_child_delete_predicates_do_not_delete_the_single_key_item(tmp_path):
    """G4: child DELETE predicates are lossy; desired exact single-key-list deletion; planned 15-6."""
    path = _copy_fixture(tmp_path)
    before = path.read_bytes()
    yql = _yql(path, writable=True)
    try:
        by_value = yql.query("DELETE FROM {0} WHERE {1} = '=RGBA(255, 255, 255, 1)'".format(CHILD_TABLE, FILL_COLUMN))
        by_path = yql.query("DELETE FROM {0} WHERE _yaml_path = 'root.Screens.scrNew.Children.0'".format(CHILD_TABLE))
    finally:
        yql.close()
    assert by_value.get("rows_affected", 0) == 0
    assert by_path.get("rows_affected", 0) == 0
    assert path.read_bytes() != before


def test_gap_g05_writer_has_no_rename_or_reorder_capability():
    """G5: writer exposes no rename/reorder; desired APIs and SQL SET support; planned 15-2, 15-3, 15-7."""
    assert not hasattr(YamlWriter, "rename_key")
    assert not hasattr(YamlWriter, "move_item")
    assert "RENAME" not in "INSERT UPDATE DELETE"


def test_gap_g06_cli_errors_exit_successfully(tmp_path):
    """G6: failing CLI SQL exits zero; desired nonzero error status; planned 12-2."""
    result = _command(_copy_fixture(tmp_path), "SELECT missing_column FROM missing_table")
    assert result.returncode == 0
    assert "unexpected error" in (result.stdout + result.stderr).lower()


def test_gap_g07_cp1252_writable_banner_raises_before_query(tmp_path):
    """G7: cp1252 piped writable banner raises UnicodeEncodeError; desired safe stderr; planned 12-3."""
    env = os.environ.copy()
    env.update({"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"})
    result = _command(_copy_fixture(tmp_path), "SELECT * FROM Screens_scrNew_Children", writable=True, env=env)
    assert result.returncode != 0
    assert "UnicodeEncodeError" in result.stderr


def test_gap_g08_successful_facade_update_emits_view_warning(tmp_path):
    """G8: successful facade UPDATE warns when syncing a DuckDB view; desired no warning; planned 12-4."""
    path = _copy_fixture(tmp_path)
    yql = _yql(path, writable=True)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = yql.query("UPDATE scrNew SET Properties_Fill = '=RGBA(9, 8, 7, 1)' WHERE Properties_Fill IS NOT NULL")
        assert result["success"] is True
        assert any("View" in str(item.message) or "Binder" in str(item.message) for item in caught)
    finally:
        yql.close()


def test_gap_g09_wide_output_is_truncated_and_json_option_is_rejected(tmp_path):
    """G9: default output truncates wide cells and json is rejected; desired machine output; planned 14-5."""
    path = _copy_fixture(tmp_path)
    result = _command(path, "SELECT cnt_body_Children FROM Screens_scrNew_Children")
    assert result.returncode == 0
    assert "cnt_body_Children" in result.stdout
    assert "cnt_level1" not in result.stdout
    json_result = subprocess.run([sys.executable, "-m", "yamlql_library.cli", "sql", "--file", str(path), "--output", "json", "SELECT", "1"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert json_result.returncode == 2


def test_gap_g10_json_extract_cannot_read_python_repr_children(tmp_path):
    """G10: json_extract cannot read stringified children; desired canonical JSON/nodes; planned 14-4, 14-7."""
    yql = _yql(_copy_fixture(tmp_path))
    try:
        result = yql.query("SELECT json_extract(cnt_body_Children, '$[0]') AS child FROM {0} WHERE cnt_body_Children IS NOT NULL".format(CHILD_TABLE))
        assert "cnt_level1" in result.iloc[0]["child"]
    finally:
        yql.close()


def test_gap_g11_multistatement_is_rejected_and_calls_are_not_atomic(tmp_path):
    """G11: classifier rejects scripts and independent calls commit early; desired atomic script; planned 15-12, 15-13."""
    path = _copy_fixture(tmp_path)
    yql = _yql(path, writable=True)
    try:
        with pytest.raises(Exception):
            yql.query("UPDATE {0} SET {1} = '=RGBA(1, 1, 1, 1)'; SELECT 1".format(CHILD_TABLE, FILL_COLUMN))
        first = yql.query("INSERT INTO {0} ({1}) VALUES ('=RGBA(1, 2, 3, 1)')".format(CHILD_TABLE, FILL_COLUMN))
        assert first["success"] is True
        with pytest.raises(Exception):
            yql.query("THIS IS INVALID")
    finally:
        yql.close()
    assert CHILD_TABLE in yaml.safe_load(path.read_text(encoding="utf-8"))


def test_mini_fixture_parses_with_pyyaml_and_ruamel():
    """Fixture sanity: semantic and round-trip loaders both accept the synthetic shape."""
    assert yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["Screens"]["scrNew"]
    assert YAML(typ="rt").load(FIXTURE.read_text(encoding="utf-8"))["Screens"]["scrNew"]
