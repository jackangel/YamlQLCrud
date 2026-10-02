"""Regression tests for the strict YAML node-kind change policy."""

from copy import deepcopy

import pytest

from tests.fidelity_utils import assert_bytes_equal, read_bytes, to_crlf, to_lf, write_bytes
from yamlql_library import YamlQL
from yamlql_library.transaction import TransactionManager
from yamlql_library.writer import (
    AnchorInUseError,
    MergedKeyError,
    NodeKindChangeError,
    YamlWriter,
    YamlWriterPolicyError,
)


def _loaded_writer(path):
    writer = YamlWriter(path)
    writer.load()
    return writer


def _assert_kind_change_is_blocked(writer, path, replacement):
    data_before = deepcopy(writer.data)
    with pytest.raises(NodeKindChangeError) as error:
        writer.set_value(path, replacement)

    assert isinstance(error.value, YamlWriterPolicyError)
    assert isinstance(error.value, ValueError)
    message = str(error.value)
    assert "'{}'".format(path) in message
    assert "from" in message
    assert "to" in message
    assert writer.data == data_before


@pytest.mark.parametrize(
    "source,path,replacement,expected_kinds",
    [
        ("value:\n  child: original\n", "value", "scalar", ("mapping", "scalar")),
        ("value:\n  - original\n", "value", "scalar", ("sequence", "scalar")),
        ("value: original\n", "value", ["replacement"], ("scalar", "sequence")),
        ("value: original\n", "value", {"child": "replacement"}, ("scalar", "mapping")),
        ("value:\n  - original\n", "value", None, ("sequence", "scalar")),
        ("value:\n  child: original\n", "value", None, ("mapping", "scalar")),
    ],
)
def test_set_value_blocks_kind_changes_without_mutating_data(
    tmp_path, source, path, replacement, expected_kinds
):
    source_bytes = to_lf(source)
    yaml_path = write_bytes(tmp_path / "kind-guard.yaml", source_bytes)
    writer = _loaded_writer(yaml_path)

    _assert_kind_change_is_blocked(writer, path, replacement)

    message = str(
        pytest.raises(NodeKindChangeError, writer.set_value, path, replacement).value
    )
    for kind in expected_kinds:
        assert kind in message
    assert_bytes_equal(read_bytes(yaml_path), source_bytes)


def test_set_value_allows_replacements_with_the_same_kind(tmp_path):
    yaml_path = write_bytes(
        tmp_path / "same-kind.yaml",
        to_lf(
            "scalar: before\n"
            "none_value: null\n"
            "mapping:\n"
            "  child: before\n"
            "sequence:\n"
            "  - before\n"
        ),
    )
    writer = _loaded_writer(yaml_path)

    writer.set_value("scalar", "after")
    writer.set_value("none_value", "after")
    writer.set_value("scalar", None)
    writer.set_value("mapping", {"child": "after"})
    writer.set_value("sequence", ["after"])

    assert writer.get_value("scalar") is None
    assert writer.get_value("none_value") == "after"
    assert writer.get_value("mapping") == {"child": "after"}
    assert writer.get_value("sequence") == ["after"]


def test_allow_kind_change_and_set_root_override_the_guard(tmp_path):
    yaml_path = write_bytes(tmp_path / "override.yaml", to_lf("value: original\n"))
    writer = _loaded_writer(yaml_path)

    writer.set_value("value", ["replacement"], allow_kind_change=True)
    assert writer.get_value("value") == ["replacement"]

    writer.set_root("replacement", allow_kind_change=True)
    assert writer.data == "replacement"


def test_set_root_has_the_same_kind_change_guard(tmp_path):
    yaml_path = write_bytes(tmp_path / "root-guard.yaml", to_lf("value: original\n"))
    writer = _loaded_writer(yaml_path)
    data_before = deepcopy(writer.data)

    with pytest.raises(NodeKindChangeError, match="root.*mapping.*scalar") as error:
        writer.set_root("replacement")

    assert isinstance(error.value, YamlWriterPolicyError)
    assert isinstance(error.value, ValueError)
    assert writer.data == data_before


def test_sql_update_rejects_list_to_scalar_and_null_without_file_changes(tmp_path):
    source = to_lf(
        "users:\n"
        "  - id: 1\n"
        "    name: first\n"
        "    tags:\n"
        "      - alpha\n"
        "      - beta\n"
    )
    yaml_path = write_bytes(tmp_path / "list-column.yaml", source)
    yamlql = YamlQL(str(yaml_path), mode="rw")
    try:
        for statement in (
            "UPDATE users SET tags = 'replacement' WHERE id = 1",
            "UPDATE users SET tags = NULL WHERE id = 1",
        ):
            with pytest.raises(Exception, match="UPDATE failed:.*tags.*sequence.*scalar"):
                yamlql.query(statement)
            assert_bytes_equal(read_bytes(yaml_path), source)
            selected = yamlql.query("SELECT tags FROM users WHERE id = 1")
            assert list(selected.iloc[0]["tags"]) == ["alpha", "beta"]
    finally:
        yamlql.close()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The projection flattens mapping fields and does not expose an updateable "
        "mapping column, so SQL cannot reach the writer kind guard."
    ),
)
def test_sql_update_rejects_mapping_to_scalar_without_file_changes(tmp_path):
    source = to_lf(
        "users:\n"
        "  - id: 1\n"
        "    name: first\n"
        "    details:\n"
        "      region: west\n"
        "      enabled: true\n"
    )
    yaml_path = write_bytes(tmp_path / "mapping-column.yaml", source)
    yamlql = YamlQL(str(yaml_path), mode="rw")
    try:
        with pytest.raises(Exception) as error:
            yamlql.query("UPDATE users SET details = 'replacement' WHERE id = 1")
        assert_bytes_equal(read_bytes(yaml_path), source)
        selected = yamlql.query("SELECT details_region, details_enabled FROM users WHERE id = 1")
        assert selected.iloc[0]["details_region"] == "west"
        assert selected.iloc[0]["details_enabled"]
        assert "Column 'details' does not exist" in str(error.value)
        pytest.fail(
            "The SQL projection does not expose mapping field 'details', so the "
            "writer kind guard is unreachable through UPDATE."
        )
    finally:
        yamlql.close()


def test_sql_update_is_atomic_when_a_later_row_would_change_kind(tmp_path):
    source = to_lf(
        "users:\n"
        "  - id: 1\n"
        "    tags: plain\n"
        "  - id: 2\n"
        "    tags:\n"
        "      - protected\n"
    )
    yaml_path = write_bytes(tmp_path / "atomic-kind-guard.yaml", source)
    yamlql = YamlQL(str(yaml_path), mode="rw")
    try:
        with pytest.raises(Exception, match="UPDATE failed:.*tags.*sequence.*scalar"):
            yamlql.query("UPDATE users SET tags = 'replacement'")
        assert_bytes_equal(read_bytes(yaml_path), source)
        selected = yamlql.query("SELECT id, tags FROM users ORDER BY id")
        assert selected.iloc[0]["tags"] == "plain"
        assert selected.iloc[1]["tags"] == "['protected']"
    finally:
        yamlql.close()


def test_sql_scalar_updates_to_scalar_and_null_still_succeed(tmp_path):
    yaml_path = write_bytes(
        tmp_path / "scalar-updates.yaml",
        to_lf("users:\n  - id: 1\n    name: before\n    note: null\n"),
    )
    yamlql = YamlQL(str(yaml_path), mode="rw")
    try:
        result = yamlql.query("UPDATE users SET name = 'after', note = 'created' WHERE id = 1")
        assert result["success"]
        assert result["rows_updated"] == 1
        result = yamlql.query("UPDATE users SET name = NULL WHERE id = 1")
        assert result["success"]
        selected = yamlql.query("SELECT name, note FROM users WHERE id = 1")
        assert selected.iloc[0]["name"] is None or str(selected.iloc[0]["name"]) == "<NA>"
        assert selected.iloc[0]["note"] == "created"
    finally:
        yamlql.close()


@pytest.mark.parametrize("encoder", [to_lf, to_crlf], ids=["lf", "crlf"])
def test_sql_multi_row_insert_then_delete_preserves_comment_bytes_and_parses(encoder, tmp_path):
    source = encoder(
        "records:\n"
        "  # comment for first record\n"
        "  - id: 1\n"
        "    name: first\n"
        "  # comment for second record\n"
        "  - id: 2\n"
        "    name: second\n"
        "\n"
        "# trailing comment section\n"
        "metadata:\n"
        "  owner: preserved\n"
    )
    yaml_path = write_bytes(tmp_path / "records.yaml", source)
    yamlql = YamlQL(str(yaml_path), mode="rw")
    try:
        insert_result = yamlql.query(
            "INSERT INTO records (id, name) VALUES (3, 'third'), (4, 'fourth')"
        )
        assert insert_result["success"]
        delete_result = yamlql.query("DELETE FROM records WHERE id IN (3, 4)")
        assert delete_result["success"]
    finally:
        yamlql.close()

    assert_bytes_equal(read_bytes(yaml_path), source)
    reloaded = YamlQL(str(yaml_path))
    try:
        records = reloaded.query("SELECT id, name FROM records ORDER BY id")
        assert records.to_dict("records") == [
            {"id": 1, "name": "first"},
            {"id": 2, "name": "second"},
        ]
    finally:
        reloaded.close()


def test_writer_level_mapping_and_sequence_failures_leave_transaction_file_unchanged(tmp_path):
    source = to_lf("mapping:\n  child: original\nsequence:\n  - original\n")
    yaml_path = write_bytes(tmp_path / "transaction-guard.yaml", source)
    transaction = TransactionManager(yaml_path)
    transaction.begin()
    writer = transaction.get_writer()
    writer.load()

    _assert_kind_change_is_blocked(writer, "mapping", "replacement")
    _assert_kind_change_is_blocked(writer, "sequence", None)
    assert_bytes_equal(read_bytes(yaml_path), source)

    transaction.rollback()
    assert_bytes_equal(read_bytes(yaml_path), source)


@pytest.mark.parametrize(
    "source,path,error_type",
    [
        (
            "base: &base original\nconsumer: *base\n",
            "base",
            AnchorInUseError,
        ),
        (
            "base: &base\n  region: west\nservice:\n  <<: *base\n",
            "service.region",
            MergedKeyError,
        ),
    ],
    ids=["anchored-node", "merge-only-key"],
)
def test_transaction_level_delete_policy_failures_leave_file_bytes_unchanged(
    tmp_path, source, path, error_type
):
    source_bytes = to_lf(source)
    yaml_path = write_bytes(tmp_path / "protected-delete.yaml", source_bytes)
    transaction = TransactionManager(yaml_path)
    transaction.begin()
    writer = transaction.get_writer()
    writer.load()

    with pytest.raises(error_type) as error:
        writer.delete_value(path)

    assert isinstance(error.value, YamlWriterPolicyError)
    assert isinstance(error.value, ValueError)
    assert_bytes_equal(read_bytes(yaml_path), source_bytes)
    transaction.rollback()
    assert_bytes_equal(read_bytes(yaml_path), source_bytes)
