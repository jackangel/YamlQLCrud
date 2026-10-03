"""Contract tests for document-aware YAML SQL projection and qualified writes."""

import json
from pathlib import Path

import pytest
import pandas as pd
from yaml.composer import ComposerError

from tests.fidelity_utils import assert_bytes_equal, read_bytes, run_sql, write_bytes
from yamlql_library import YamlQL
from yamlql_library.loader import YamlLoader
from yamlql_library.transformer import DataTransformer
from yamlql_library.writer import YamlWriter


LF = "lf"
CRLF = "crlf"


def _load_stream(path):
    """Exercise the planned ordered loader stream API without fallback behavior."""
    return YamlLoader(path).load_stream()


def _writer_document_count(path):
    writer = YamlWriter(path)
    writer.load()
    return writer.doc_count


def _table_names(path):
    yamlql = YamlQL(str(path))
    try:
        return yamlql.list_tables()
    finally:
        yamlql.close()


def _query_records(path, sql):
    yamlql = YamlQL(str(path))
    try:
        return yamlql.query(sql).to_dict("records")
    finally:
        yamlql.close()


def _document_tables(path):
    """Project the loader's one parsed stream directly for Task 6-3."""
    loader = YamlLoader(path)
    transformer = DataTransformer(loader.load(), stream=loader.load_stream())
    return dict(transformer.transform())


def _assert_no_transaction_artifacts(path):
    assert not path.with_suffix(path.suffix + ".backup").exists()
    assert not list(path.parent.glob(path.stem + ".*.tmp"))


def test_load_keeps_legacy_merged_mapping_contract_and_compose_rules(tmp_path):
    """`load()` remains last-document-wins while `load_stream()` is additive."""
    source = (
        b"\xef\xbb\xbffirst: &same !app tagged\n"
        b"alias: *same\n"
        b"users:\n"
        b"  - id: 1\n"
        b"---\n"
        b"users:\n"
        b"  - id: 2\n"
        b"second: &same second\n"
        b"again: *same\n"
    )
    path = write_bytes(tmp_path / "legacy.yaml", source)

    assert YamlLoader(path).load() == {
        "first": "tagged",
        "alias": "tagged",
        "users": [{"id": 2}],
        "second": "second",
        "again": "second",
    }

    undefined = write_bytes(tmp_path / "undefined.yaml", b"value: *missing\n")
    with pytest.raises(ComposerError, match="undefined alias"):
        YamlLoader(undefined).load()


def test_single_document_schema_remains_the_existing_unqualified_schema(tmp_path):
    """A single document must not acquire qualified or metadata relations."""
    source = b"users:\n  - id: 1\n    name: Ada\n"
    path = write_bytes(tmp_path / "single.yaml", source)

    assert _table_names(path) == ["users"]
    assert _query_records(path, "SELECT id, name FROM users") == [{"id": 1, "name": "Ada"}]
    assert not [name for name in _table_names(path) if name.startswith("doc")]
    assert "_yamlql_documents" not in _table_names(path)


def test_loader_stream_lists_every_document_with_kind_and_zero_based_index(tmp_path):
    path = write_bytes(
        tmp_path / "stream.yaml",
        b"one: 1\n---\n- two\n---\ntext\n---\n# comment-only\n---\n",
    )

    assert _load_stream(path) == [
        (0, "mapping", {"one": 1}),
        (1, "list", ["two"]),
        (2, "scalar", "text"),
        (3, "null", None),
        (4, "null", None),
    ]


def test_mapping_documents_expose_merged_and_document_qualified_tables(tmp_path):
    path = write_bytes(
        tmp_path / "mapping-documents.yaml",
        b"users:\n  - id: 1\n    name: first\n---\nusers:\n  - id: 2\n    name: second\n",
    )

    tables = _document_tables(path)
    assert list(tables) == ["users", "doc0_users", "doc1_users", "_yamlql_documents"]
    assert tables["users"][["id", "name"]].to_dict("records") == [{"id": 2, "name": "second"}]
    assert tables["doc0_users"][["id", "name"]].to_dict("records") == [{"id": 1, "name": "first"}]
    assert tables["doc1_users"][["id", "name"]].to_dict("records") == [{"id": 2, "name": "second"}]


def test_root_list_document_has_value_column_and_child_tables(tmp_path):
    path = write_bytes(
        tmp_path / "root-list.yaml",
        b"- red\n- blue\n---\n- id: 1\n  children:\n    - code: A\n",
    )

    tables = _document_tables(path)
    assert list(tables) == ["doc0", "doc1", "doc1_children", "_yamlql_documents"]
    assert tables["doc0"][["value"]].to_dict("records") == [
        {"value": "red"},
        {"value": "blue"},
    ]
    assert tables["doc1"][["id"]].to_dict("records") == [{"id": 1}]
    assert tables["doc1_children"][["code"]].to_dict("records") == [{"code": "A"}]


def test_document_metadata_pins_creation_order_and_only_created_tables(tmp_path):
    path = write_bytes(
        tmp_path / "metadata.yaml",
        b"users:\n"
        b"  - id: 1\n"
        b"    pets:\n"
        b"      - name: cat\n"
        b"settings:\n"
        b"  enabled: true\n"
        b"  retries: 3\n"
        b"---\n"
        b"- id: 2\n"
        b"  pets:\n"
        b"    - name: dog\n"
        b"---\n"
        b"hello\n"
        b"---\n",
    )

    records = _document_tables(path)["_yamlql_documents"].to_dict("records")
    assert records == [
        {
            "doc_index": 0,
            "kind": "mapping",
            "keys": json.dumps(["users", "settings"], separators=(",", ":")),
            "row_tables": json.dumps(["doc0_users", "doc0_users_pets", "doc0_settings"], separators=(",", ":")),
        },
        {
            "doc_index": 1,
            "kind": "list",
            "keys": "[]",
            "row_tables": json.dumps(["doc1", "doc1_pets"], separators=(",", ":")),
        },
        {"doc_index": 2, "kind": "scalar", "keys": "[]", "row_tables": "[]"},
        {"doc_index": 3, "kind": "null", "keys": "[]", "row_tables": "[]"},
    ]


@pytest.mark.parametrize(
    "name, source, expected_length",
    [
        ("empty", b"", 0),
        ("whitespace-comment", b" \n# only a comment\n\t\n", 1),
        ("trailing-separator", b"a: 1\n---\n", 2),
        ("comment-between-separators", b"a: 1\n---\n# middle\n---\nb: 2\n", 3),
        ("explicit-end-and-directive", b"%YAML 1.2\n---\na: 1\n...\n---\nb: 2\n...\n", 2),
        ("crlf-markers", b"a: 1\r\n---\r\nb: 2\r\n", 2),
    ],
)
def test_stream_edge_cases_have_pinned_loader_writer_alignment(tmp_path, name, source, expected_length):
    path = write_bytes(tmp_path / (name + ".yaml"), source)

    stream = _load_stream(path)
    assert len(stream) == expected_length
    assert len(stream) == _writer_document_count(path)
    assert not [table for table in _table_names(path) if table.startswith("doc")]
    assert "_yamlql_documents" not in _table_names(path)


def test_parser_count_disagreement_is_a_clean_qualified_write_failure(tmp_path, monkeypatch):
    source = b"first:\n  - id: 1\n---\n# trailing comment\n"
    path = write_bytes(tmp_path / "disagreement.yaml", source)
    monkeypatch.setattr(YamlWriter, "doc_count", property(lambda self: 1))

    with pytest.raises(Exception, match="document.*count.*disagree"):
        run_sql(path, "UPDATE doc0_first SET id = 2 WHERE id = 1")
    assert_bytes_equal(read_bytes(path), source)
    _assert_no_transaction_artifacts(path)


def test_document_table_collisions_preserve_user_table_and_surface_warnings(tmp_path):
    path = write_bytes(
        tmp_path / "collisions.yaml",
        b"doc0_users:\n  - id: 10\n_yamlql_documents:\n  - id: 11\nusers:\n  - id: 1\n---\nusers:\n  - id: 2\n",
    )
    loader = YamlLoader(path)
    transformer = DataTransformer(loader.load(), stream=loader.load_stream())
    tables = dict(transformer.transform())
    assert tables["doc0_users"][["id"]].to_dict("records") == [{"id": 10}]
    assert transformer.warnings == [
        "Skipped derived table doc0_users because user table doc0_users already exists",
        "Skipped metadata table _yamlql_documents because user table _yamlql_documents already exists",
    ]


def test_failed_qualified_write_preserves_all_bytes_and_creates_no_artifacts(tmp_path, monkeypatch):
    source = b"users:\n  - id: 1\n    name: one\n---\nusers:\n  - id: 2\n    name: two\n"
    path = write_bytes(tmp_path / "failed-qualified.yaml", source)

    def fail_write_to(self, destination):
        raise OSError("injected writer error")

    monkeypatch.setattr(YamlWriter, "write_to", fail_write_to)
    with pytest.raises(Exception, match="injected writer error"):
        run_sql(path, "UPDATE doc1_users SET name = 'TWO' WHERE id = 2")
    assert_bytes_equal(read_bytes(path), source)
    _assert_no_transaction_artifacts(path)


_WRITE_MATRIX = [
    pytest.param(
        "mapping-update-lf",
        b"# first\nusers:\n  - id: 1\n    name: one\n---\n# second\nusers:\n  - id: 2\n    name: two\n...\n",
        "UPDATE doc0_users SET name = 'ONE' WHERE id = 1",
        b"# first\nusers:\n  - id: 1\n    name: ONE\n---\n# second\nusers:\n  - id: 2\n    name: two\n...\n",
        b"# second\nusers:\n  - id: 2\n    name: two\n...\n",
        id="mapping-update-lf",
    ),
    pytest.param(
        "mapping-insert-crlf",
        b"# first\r\nusers:\r\n  - id: 1\r\n    name: one\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    name: two\r\n...\r\n",
        "INSERT INTO doc0_users (id, name) VALUES (3, 'three')",
        b"# first\r\nusers:\r\n  - id: 1\r\n    name: one\r\n  - id: 3\r\n    name: three\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    name: two\r\n...\r\n",
        b"# second\r\nusers:\r\n  - id: 2\r\n    name: two\r\n...\r\n",
        id="mapping-insert-crlf",
    ),
    pytest.param(
        "mapping-delete-lf",
        b"users:\n  - id: 1\n    name: one\n---\n# second\nusers:\n  - id: 2\n    name: two\n",
        "DELETE FROM doc0_users WHERE id = 1",
        b"users: []\n---\n# second\nusers:\n  - id: 2\n    name: two\n",
        b"# second\nusers:\n  - id: 2\n    name: two\n",
        id="mapping-delete-lf",
    ),
    pytest.param(
        "root-list-update-crlf",
        b"# first\r\n- &item !tag one\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n",
        "UPDATE doc0 SET value = 'ONE' WHERE value = 'one'",
        b"# first\r\n- !tag ONE\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n",
        b"# second\r\nusers:\r\n  - id: 2\r\n",
        id="root-list-update-crlf",
    ),
    pytest.param(
        "root-list-insert-lf",
        b"# first\n- one\n---\n# second\nusers:\n  - id: 2\n",
        "INSERT INTO doc0 (value) VALUES ('three')",
        b"# first\n- one\n- three\n---\n# second\nusers:\n  - id: 2\n",
        b"# second\nusers:\n  - id: 2\n",
        id="root-list-insert-lf",
    ),
    pytest.param(
        "root-list-delete-crlf",
        b"# first\r\n- one\r\n- two\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n",
        "DELETE FROM doc0 WHERE value = 'one'",
        b"# first\r\n- two\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n",
        b"# second\r\nusers:\r\n  - id: 2\r\n",
        id="root-list-delete-crlf",
    ),
    pytest.param(
        "child-update-lf",
        b"users:\n  - id: 1\n    pets:\n      - name: cat\n---\n# second\nusers:\n  - id: 2\n    pets:\n      - name: dog\n...\n",
        "UPDATE doc0_users_pets SET name = 'CAT' WHERE name = 'cat'",
        b"users:\n  - id: 1\n    pets:\n      - name: CAT\n---\n# second\nusers:\n  - id: 2\n    pets:\n      - name: dog\n...\n",
        b"# second\nusers:\n  - id: 2\n    pets:\n      - name: dog\n...\n",
        id="child-update-lf",
    ),
    pytest.param(
        "child-insert-crlf",
        b"users:\r\n  - id: 1\r\n    pets:\r\n      - name: cat\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    pets:\r\n      - name: dog\r\n",
        "INSERT INTO doc0_users_pets (name) VALUES ('bird')",
        b"users:\r\n  - id: 1\r\n    pets:\r\n      - name: cat\r\n      - name: bird\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    pets:\r\n      - name: dog\r\n",
        b"# second\r\nusers:\r\n  - id: 2\r\n    pets:\r\n      - name: dog\r\n",
        id="child-insert-crlf",
    ),
    pytest.param(
        "child-delete-lf",
        b"users:\n  - id: 1\n    pets:\n      - name: cat\n      - name: bird\n---\n# second\nusers:\n  - id: 2\n    pets:\n      - name: dog\n",
        "DELETE FROM doc0_users_pets WHERE name = 'cat'",
        b"users:\n  - id: 1\n    pets:\n      - name: bird\n---\n# second\nusers:\n  - id: 2\n    pets:\n      - name: dog\n",
        b"# second\nusers:\n  - id: 2\n    pets:\n      - name: dog\n",
        id="child-delete-lf",
    ),
    pytest.param(
        "mapping-update-crlf",
        b"# first\r\nusers:\r\n  - id: 1\r\n    name: one\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    name: two\r\n...\r\n",
        "UPDATE doc0_users SET name = 'ONE' WHERE id = 1",
        b"# first\r\nusers:\r\n  - id: 1\r\n    name: ONE\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    name: two\r\n...\r\n",
        b"# second\r\nusers:\r\n  - id: 2\r\n    name: two\r\n...\r\n",
        id="mapping-update-crlf",
    ),
    pytest.param(
        "mapping-insert-lf",
        b"# first\nusers:\n  - id: 1\n    name: one\n---\n# second\nusers:\n  - id: 2\n    name: two\n...\n",
        "INSERT INTO doc0_users (id, name) VALUES (3, 'three')",
        b"# first\nusers:\n  - id: 1\n    name: one\n  - id: 3\n    name: three\n---\n# second\nusers:\n  - id: 2\n    name: two\n...\n",
        b"# second\nusers:\n  - id: 2\n    name: two\n...\n",
        id="mapping-insert-lf",
    ),
    pytest.param(
        "mapping-delete-crlf",
        b"users:\r\n  - id: 1\r\n    name: one\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    name: two\r\n",
        "DELETE FROM doc0_users WHERE id = 1",
        b"users: []\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    name: two\r\n",
        b"# second\r\nusers:\r\n  - id: 2\r\n    name: two\r\n",
        id="mapping-delete-crlf",
    ),
    pytest.param(
        "root-list-update-lf",
        b"# first\n- &item !tag one\n---\n# second\nusers:\n  - id: 2\n",
        "UPDATE doc0 SET value = 'ONE' WHERE value = 'one'",
        b"# first\n- !tag ONE\n---\n# second\nusers:\n  - id: 2\n",
        b"# second\nusers:\n  - id: 2\n",
        id="root-list-update-lf",
    ),
    pytest.param(
        "root-list-insert-crlf",
        b"# first\r\n- one\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n",
        "INSERT INTO doc0 (value) VALUES ('three')",
        b"# first\r\n- one\r\n- three\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n",
        b"# second\r\nusers:\r\n  - id: 2\r\n",
        id="root-list-insert-crlf",
    ),
    pytest.param(
        "root-list-delete-lf",
        b"# first\n- one\n- two\n---\n# second\nusers:\n  - id: 2\n",
        "DELETE FROM doc0 WHERE value = 'one'",
        b"# first\n- two\n---\n# second\nusers:\n  - id: 2\n",
        b"# second\nusers:\n  - id: 2\n",
        id="root-list-delete-lf",
    ),
    pytest.param(
        "child-update-crlf",
        b"users:\r\n  - id: 1\r\n    pets:\r\n      - name: cat\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    pets:\r\n      - name: dog\r\n...\r\n",
        "UPDATE doc0_users_pets SET name = 'CAT' WHERE name = 'cat'",
        b"users:\r\n  - id: 1\r\n    pets:\r\n      - name: CAT\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    pets:\r\n      - name: dog\r\n...\r\n",
        b"# second\r\nusers:\r\n  - id: 2\r\n    pets:\r\n      - name: dog\r\n...\r\n",
        id="child-update-crlf",
    ),
    pytest.param(
        "child-insert-lf",
        b"users:\n  - id: 1\n    pets:\n      - name: cat\n---\n# second\nusers:\n  - id: 2\n    pets:\n      - name: dog\n",
        "INSERT INTO doc0_users_pets (name) VALUES ('bird')",
        b"users:\n  - id: 1\n    pets:\n      - name: cat\n      - name: bird\n---\n# second\nusers:\n  - id: 2\n    pets:\n      - name: dog\n",
        b"# second\nusers:\n  - id: 2\n    pets:\n      - name: dog\n",
        id="child-insert-lf",
    ),
    pytest.param(
        "child-delete-crlf",
        b"users:\r\n  - id: 1\r\n    pets:\r\n      - name: cat\r\n      - name: bird\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    pets:\r\n      - name: dog\r\n",
        "DELETE FROM doc0_users_pets WHERE name = 'cat'",
        b"users:\r\n  - id: 1\r\n    pets:\r\n      - name: bird\r\n---\r\n# second\r\nusers:\r\n  - id: 2\r\n    pets:\r\n      - name: dog\r\n",
        b"# second\r\nusers:\r\n  - id: 2\r\n    pets:\r\n      - name: dog\r\n",
        id="child-delete-crlf",
    ),
]


@pytest.mark.parametrize("name, source, sql, expected, untouched_document", _WRITE_MATRIX)
def test_qualified_write_untouched_document_byte_identity_matrix(
    tmp_path, name, source, sql, expected, untouched_document
):
    path = write_bytes(tmp_path / (name + ".yaml"), source)

    result = run_sql(path, sql)
    assert result["success"]
    assert_bytes_equal(read_bytes(path), expected)
    assert untouched_document in read_bytes(path)


@pytest.mark.parametrize("root", [b"plain scalar\n", b"null\n"], ids=["scalar", "null"])
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO doc0 (value) VALUES ('new')",
        "UPDATE doc0 SET value = 'new'",
        "DELETE FROM doc0",
    ],
)
def test_scalar_and_null_document_dml_fails_before_transaction(tmp_path, root, statement):
    path = write_bytes(tmp_path / "scalar-null.yaml", root + b"---\nusers:\n  - id: 1\n")
    before = read_bytes(path)

    with pytest.raises(Exception, match="(?i)(scalar|null|document|write)"):
        run_sql(path, statement)
    assert_bytes_equal(read_bytes(path), before)
    _assert_no_transaction_artifacts(path)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO _yamlql_documents (doc_index, kind) VALUES (0, 'mapping')",
        "UPDATE _yamlql_documents SET kind = 'list' WHERE doc_index = 0",
        "DELETE FROM _yamlql_documents WHERE doc_index = 0",
    ],
)
def test_document_metadata_relation_is_read_only_and_has_no_yaml_path(tmp_path, statement):
    source = b"users:\n  - id: 1\n---\nusers:\n  - id: 2\n"
    path = write_bytes(tmp_path / "metadata-read-only.yaml", source)

    records = _query_records(path, "SELECT * FROM _yamlql_documents ORDER BY doc_index")
    assert all("_yaml_path" not in record for record in records)
    with pytest.raises(Exception, match="(?i)(read.only|metadata|_yamlql_documents)"):
        run_sql(path, statement)
    assert_bytes_equal(read_bytes(path), source)
    _assert_no_transaction_artifacts(path)


def test_qualified_multirow_update_g4_rejection_is_wrapped_and_unchanged(
    tmp_path, monkeypatch
):
    """A same-name anchor identity ambiguity is never guessed mid-statement."""
    source = (
        b"- &same one\n"
        b"- *same\n"
        b"- &same two\n"
        b"- *same\n"
        b"---\n"
        b"users:\n"
        b"  - id: 1\n"
    )
    path = write_bytes(tmp_path / "qualified-g4.yaml", source)
    yamlql = YamlQL(str(path), mode="rw")
    try:
        monkeypatch.setattr(
            yamlql._db.update_handler,
            "_find_matching_rows",
            lambda table_name, where_clause: pd.DataFrame(
                {"value": ["one", "two"], "_yaml_path": ["root.1", "root.2"]}
            ),
        )
        with pytest.raises(
            Exception, match="Cannot resolve anchor 'same' identity in document 0"
        ):
            yamlql.query("UPDATE doc0 SET value = 'literal' WHERE value IN ('one', 'two')")
    finally:
        yamlql.close()

    assert_bytes_equal(read_bytes(path), source)
    _assert_no_transaction_artifacts(path)
