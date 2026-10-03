"""Contract tests for the ruamel.yaml private-API compatibility guard."""

# Task 2-3 compatibility seams: ``_compatibility_cache`` stores only a
# successful outcome so the probe runs at most once; ``_installed_version``
# and ``_capability_probe`` simulate failures. Failed checks must raise
# ``YamlWriterCompatibilityError`` without caching success, and their message
# must include ``ruamel.yaml``, the installed version, and
# ``ruamel.yaml>=0.18.0,<0.20``.

from importlib.metadata import version
from pathlib import Path
import re

import pytest

from tests.fidelity_utils import assert_bytes_equal, read_bytes, write_bytes
from yamlql_library import YamlQL
from yamlql_library.writer import YamlWriter


CERTIFIED_REQUIREMENT = "ruamel.yaml>=0.18.0,<0.20"


def _assert_compatibility_error(error, installed_version):
    message = str(error.value)
    assert isinstance(error.value, RuntimeError)
    assert "ruamel.yaml" in message
    assert installed_version in message
    assert CERTIFIED_REQUIREMENT in message


def test_installed_ruamel_version_passes_real_capability_probe():
    from yamlql_library import ruamel_compat

    assert ruamel_compat.CERTIFIED_RANGE == ((0, 18, 0), (0, 20, 0))
    assert ruamel_compat.check_ruamel_compatibility() is None


def test_outside_certified_version_raises_descriptive_compatibility_error(monkeypatch):
    from yamlql_library import ruamel_compat

    monkeypatch.setattr(ruamel_compat, "_compatibility_cache", None)
    monkeypatch.setattr(ruamel_compat, "_installed_version", lambda: "0.20.0")

    with pytest.raises(ruamel_compat.YamlWriterCompatibilityError) as error:
        ruamel_compat.check_ruamel_compatibility()

    _assert_compatibility_error(error, "0.20.0")


def test_capability_probe_failure_raises_descriptive_compatibility_error(monkeypatch):
    from yamlql_library import ruamel_compat

    installed_version = version("ruamel.yaml")
    monkeypatch.setattr(ruamel_compat, "_compatibility_cache", None)

    def missing_line_column_metadata():
        raise AttributeError("round-trip node has no lc metadata")

    monkeypatch.setattr(
        ruamel_compat,
        "_capability_probe",
        missing_line_column_metadata,
    )

    with pytest.raises(ruamel_compat.YamlWriterCompatibilityError) as error:
        ruamel_compat.check_ruamel_compatibility()

    _assert_compatibility_error(error, installed_version)


def test_guard_fails_before_writer_or_sql_update_changes_file(monkeypatch, tmp_path):
    from yamlql_library import ruamel_compat

    yaml_path = write_bytes(
        tmp_path / "users.yaml",
        b"users:\n  - id: 1\n    name: original\n",
    )
    original_bytes = read_bytes(yaml_path)

    monkeypatch.setattr(ruamel_compat, "_compatibility_cache", None)
    monkeypatch.setattr(ruamel_compat, "_installed_version", lambda: "0.20.0")

    with pytest.raises(ruamel_compat.YamlWriterCompatibilityError) as writer_error:
        YamlWriter(yaml_path).load()
    _assert_compatibility_error(writer_error, "0.20.0")
    assert_bytes_equal(read_bytes(yaml_path), original_bytes)

    yamlql = YamlQL(str(yaml_path), mode="rw")
    try:
        with pytest.raises(Exception) as sql_error:
            yamlql.query("UPDATE users SET name = 'changed' WHERE id = 1")
        sql_message = str(sql_error.value)
        assert "UPDATE failed:" in sql_message
        assert "ruamel.yaml" in sql_message
        assert "0.20.0" in sql_message
        assert CERTIFIED_REQUIREMENT in sql_message
    finally:
        yamlql.close()

    assert_bytes_equal(read_bytes(yaml_path), original_bytes)


def test_compatibility_probe_is_cached_across_guard_calls_and_writers(monkeypatch, tmp_path):
    from yamlql_library import ruamel_compat

    yaml_path = write_bytes(tmp_path / "cache.yaml", b"settings:\n  enabled: true\n")
    probe_calls = 0

    def count_probe_calls():
        nonlocal probe_calls
        probe_calls += 1

    monkeypatch.setattr(ruamel_compat, "_compatibility_cache", None)
    monkeypatch.setattr(ruamel_compat, "_capability_probe", count_probe_calls)

    ruamel_compat.check_ruamel_compatibility()
    ruamel_compat.check_ruamel_compatibility()
    YamlWriter(yaml_path).load()
    YamlWriter(yaml_path).load()

    assert probe_calls == 1


def test_pyproject_ruamel_requirement_matches_certified_range():
    from yamlql_library import ruamel_compat

    pyproject_text = (Path(__file__).parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    requirement_match = re.search(
        r'^\s*"(ruamel\.yaml[^"\n]+)"\s*,?\s*$',
        pyproject_text,
        flags=re.MULTILINE,
    )

    assert requirement_match is not None
    assert requirement_match.group(1) == CERTIFIED_REQUIREMENT
    assert ruamel_compat.CERTIFIED_RANGE == ((0, 18, 0), (0, 20, 0))
