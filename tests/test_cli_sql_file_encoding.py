"""Regression tests for explicit UTF-8 decoding of ``yamlql sql --sql-file``."""

import pytest
from typer.testing import CliRunner

from tests.fidelity_utils import assert_bytes_equal, read_bytes, write_bytes
from yamlql_library.cli import app


runner = CliRunner()
def test_cli_sql_file_utf8_bom_ascii_select(tmp_path):
    yaml_file = write_bytes(
        tmp_path / "users.yaml",
        b"users:\n  - id: 1\n    name: bom-user\n",
    )
    sql_file = write_bytes(
        tmp_path / "select-with-bom.sql",
        b"\xef\xbb\xbfSELECT name FROM users WHERE id = 1",
    )

    result = runner.invoke(app, ["sql", "-f", str(yaml_file), "--sql-file", str(sql_file)])

    assert result.exit_code == 0
    assert "bom-user" in result.stdout


def test_cli_sql_file_utf8_bom_chinese_insert(tmp_path):
    yaml_file = write_bytes(tmp_path / "users.yaml", b"users: []\n")
    sql_file = write_bytes(
        tmp_path / "insert-with-bom.sql",
        "\ufeffINSERT INTO users (id, name) VALUES (1, '你好世界')".encode("utf-8"),
    )

    result = runner.invoke(
        app,
        ["sql", "-f", str(yaml_file), "--sql-file", str(sql_file), "--writable"],
    )

    assert result.exit_code == 0
    assert "你好世界".encode("utf-8") in read_bytes(yaml_file)


def test_cli_sql_file_invalid_utf8_reports_clean_error_without_writing(tmp_path):
    original_yaml = b"users:\n  - id: 1\n    name: unchanged\n"
    yaml_file = write_bytes(tmp_path / "users.yaml", original_yaml)
    sql_file = write_bytes(
        tmp_path / "invalid-utf8.sql",
        b"UPDATE users SET name = 'changed' WHERE id = 1\xff",
    )

    result = runner.invoke(
        app,
        ["sql", "-f", str(yaml_file), "--sql-file", str(sql_file), "--writable"],
    )

    assert result.exit_code != 0
    assert "".join(str(sql_file).split()) in "".join(result.stdout.split())
    assert "UTF-8" in result.stdout
    assert "Traceback" not in result.stdout
    assert_bytes_equal(read_bytes(yaml_file), original_yaml)


def test_cli_sql_file_ascii_crlf_select(tmp_path):
    yaml_file = write_bytes(
        tmp_path / "users.yaml",
        b"users:\n  - id: 1\n    name: crlf-user\n",
    )
    sql_file = write_bytes(
        tmp_path / "select-crlf.sql",
        b"SELECT name FROM users WHERE id = 1\r\n",
    )

    result = runner.invoke(app, ["sql", "-f", str(yaml_file), "--sql-file", str(sql_file)])

    assert result.exit_code == 0
    assert "crlf-user" in result.stdout


def test_cli_sql_file_utf8_chinese_literal_select(tmp_path):
    yaml_file = write_bytes(tmp_path / "users.yaml", b"users: []\n")
    sql_file = write_bytes(
        tmp_path / "select-chinese.sql",
        "SELECT '你好世界' AS greeting".encode("utf-8"),
    )

    result = runner.invoke(app, ["sql", "-f", str(yaml_file), "--sql-file", str(sql_file)])

    assert result.exit_code == 0
    assert "你好世界" in result.stdout
