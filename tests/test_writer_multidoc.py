"""Golden byte tests for YAML document-stream editing and transactions."""

from pathlib import Path

import pytest

from tests.fidelity_utils import (
    assert_bytes_equal,
    noop_roundtrip,
    read_bytes,
    to_crlf,
    to_lf,
    write_bytes,
)
from yamlql_library import YamlQL
from yamlql_library.transaction import TransactionManager
from yamlql_library.writer import YamlWriter


ENCODERS = (to_lf, to_crlf)


def _writer(path):
    writer = YamlWriter(path)
    writer.load()
    return writer


def _stream(encode, suffix="\n"):
    """Return a three-mapping-document fixture with one editable list table."""
    return encode(
        "# first document\n"
        "first: one\n"
        "---\n"
        "# middle document\n"
        "users:\n"
        "  - id: 1\n"
        "    name: alpha\n"
        "---\n"
        "# last document\n"
        "last: three" + suffix
    )


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
@pytest.mark.parametrize(
    "source",
    [
        "%YAML 1.2\n---\nfirst: one\n# between documents\n---\n- list item\n...",
        "first: one\n---\n\n---\n- list item\n# between documents\n---\nlast: three",
        "---\n# first comment\nfirst: one\n...\n---\nsecond: two\n...",
    ],
    ids=["directive-list-marker", "implicit-empty-list-comments", "explicit-markers"],
)
def test_multidocument_noop_writer_and_transaction_are_byte_identical(tmp_path, encode, source):
    """Every stream framing form remains byte-identical when no value changes."""
    source_bytes = encode(source)
    writer_path = write_bytes(tmp_path / "writer.yaml", source_bytes)
    transaction_path = write_bytes(tmp_path / "transaction.yaml", source_bytes)

    assert_bytes_equal(noop_roundtrip(writer_path), source_bytes)

    transaction = TransactionManager(transaction_path)
    transaction.begin()
    transaction.get_writer().load()
    transaction.commit()
    assert_bytes_equal(read_bytes(transaction_path), source_bytes)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_multidocument_noop_preserves_bom_and_no_final_newline(tmp_path, encode):
    source = b"\xef\xbb\xbf" + encode("---\nfirst: one\n---\nsecond: two")
    path = write_bytes(tmp_path / "bom-stream.yaml", source)

    assert_bytes_equal(noop_roundtrip(path), source)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_edits_are_isolated_to_the_selected_document(tmp_path, encode):
    """Set, insert, delete, and append render only their owning document."""
    source = _stream(encode)
    path = write_bytes(tmp_path / "isolation.yaml", source)
    writer = _writer(path)

    writer.set_value("first", "changed", doc=0)
    writer.set_value("users.0.name", "bravo", doc=1)
    writer.insert_value("created", "last-only", doc=2)
    writer.append_item("users", {"id": 2, "name": "charlie"}, doc=1)
    writer.delete_value("users.0", doc=1)
    writer.write()

    expected = encode(
        "# first document\n"
        "first: changed\n"
        "---\n"
        "# middle document\n"
        "users:\n"
        "  - id: 2\n"
        "    name: charlie\n"
        "---\n"
        "# last document\n"
        "last: three\n"
        "created: last-only\n"
    )
    assert_bytes_equal(read_bytes(path), expected)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_addressing_uses_last_definer_and_explicit_document_override(tmp_path, encode):
    source = encode(
        "shared: first\n"
        "only_first: yes\n"
        "---\n"
        "middle: ignored\n"
        "---\n"
        "shared: last\n"
        "only_last: yes\n"
    )
    path = write_bytes(tmp_path / "addressing.yaml", source)
    writer = _writer(path)

    assert writer.find_document("shared") == 2
    assert writer.find_document("only_first") == 0
    assert writer.find_document("missing") is None
    writer.set_value("shared", "default-last")
    writer.set_value("shared", "explicit-first", doc=0)
    writer.insert_value("created", "in-last-map")
    with pytest.raises(ValueError, match="Document index 3 out of range"):
        writer.set_value("shared", "nope", doc=3)
    writer.write()

    expected = encode(
        "shared: explicit-first\n"
        "only_first: yes\n"
        "---\n"
        "middle: ignored\n"
        "---\n"
        "shared: default-last\n"
        "only_last: yes\n"
        "created: in-last-map\n"
    )
    assert_bytes_equal(read_bytes(path), expected)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
@pytest.mark.parametrize(
    "suffix",
    ["", "\n", "\n\n", "\n...", "\n...\n", "\n# final comment", "\n---\n"],
    ids=[
        "no-final-newline",
        "one-final-newline",
        "two-final-newlines",
        "final-end-marker-without-newline",
        "final-end-marker-with-newline",
        "final-comment-without-newline",
        "trailing-empty-document",
    ],
)
@pytest.mark.parametrize("document_index", [0, 1], ids=["first", "last"])
def test_tail_newline_matrix_preserves_stream_suffix_when_editing_each_document(
    tmp_path, encode, suffix, document_index
):
    source = encode("first: one\n---\nlast: two" + suffix)
    path = write_bytes(tmp_path / "tail.yaml", source)
    writer = _writer(path)

    writer.set_value("first" if document_index == 0 else "last", "changed", doc=document_index)
    writer.write()

    expected = encode(
        ("first: changed" if document_index == 0 else "first: one")
        + "\n---\n"
        + ("last: changed" if document_index == 1 else "last: two")
        + suffix
    )
    assert_bytes_equal(read_bytes(path), expected)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_block_scalar_separator_does_not_split_document_or_touch_other_bytes(tmp_path, encode):
    source = encode(
        "notes: |-\n"
        "  an apparent separator follows\n"
        "  ---\n"
        "  and remains scalar text\n"
        "---\n"
        "target: before\n"
    )
    path = write_bytes(tmp_path / "block-scalar.yaml", source)
    writer = _writer(path)

    assert writer.doc_count == 2
    writer.set_value("target", "after", doc=1)
    writer.write()

    expected = encode(
        "notes: |-\n"
        "  an apparent separator follows\n"
        "  ---\n"
        "  and remains scalar text\n"
        "---\n"
        "target: after\n"
    )
    assert_bytes_equal(read_bytes(path), expected)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_transaction_commit_and_direct_data_mutation_preserve_stream_bytes(tmp_path, encode):
    source = _stream(encode)
    path = write_bytes(tmp_path / "commit.yaml", source)

    transaction = TransactionManager(path)
    transaction.begin()
    writer = transaction.get_writer()
    writer.load()
    writer.data["first"] = "mutated-directly"
    transaction.commit()

    expected = encode(
        "# first document\n"
        "first: mutated-directly\n"
        "---\n"
        "# middle document\n"
        "users:\n"
        "  - id: 1\n"
        "    name: alpha\n"
        "---\n"
        "# last document\n"
        "last: three\n"
    )
    assert_bytes_equal(read_bytes(path), expected)
    assert not path.with_suffix(path.suffix + ".backup").exists()
    assert not list(tmp_path.glob(path.stem + ".*.tmp"))


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_transaction_rejects_lost_or_invalid_later_document_and_rolls_back(tmp_path, encode, monkeypatch):
    source = _stream(encode)
    path = write_bytes(tmp_path / "rollback.yaml", source)

    for replacement in (encode("first: replacement\n"), encode("first: replacement\n---\nbroken: [\n")):
        transaction = TransactionManager(path)
        transaction.begin()
        writer = transaction.get_writer()
        writer.load()
        writer.set_value("first", "changed", doc=0)
        monkeypatch.setattr(writer, "write_to", lambda destination, body=replacement: Path(destination).write_bytes(body))

        with pytest.raises(IOError, match="Failed to commit transaction"):
            transaction.commit()
        transaction.rollback()
        assert_bytes_equal(read_bytes(path), source)
        assert not path.with_suffix(path.suffix + ".backup").exists()
        assert not list(tmp_path.glob(path.stem + ".*.tmp"))


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_sql_crud_targets_tables_in_first_middle_and_last_documents(tmp_path, encode):
    source = encode(
        "first_users:\n"
        "  - id: 1\n"
        "    name: one\n"
        "---\n"
        "middle_users:\n"
        "  - id: 2\n"
        "    name: two\n"
        "---\n"
        "last_users:\n"
        "  - id: 3\n"
        "    name: three\n"
    )
    path = write_bytes(tmp_path / "sql-documents.yaml", source)
    yamlql = YamlQL(str(path), mode="rw")
    try:
        update = yamlql.query("UPDATE first_users SET name = 'ONE' WHERE id = 1")
        insert = yamlql.query("INSERT INTO middle_users (id, name) VALUES (4, 'four')")
        delete = yamlql.query("DELETE FROM last_users WHERE id = 3")
        assert update["success"] and insert["success"] and delete["success"]
        selected = yamlql.query("SELECT id, name FROM middle_users ORDER BY id")
        assert selected.to_dict("records") == [{"id": 2, "name": "two"}, {"id": 4, "name": "four"}]
    finally:
        yamlql.close()

    expected = encode(
        "first_users:\n"
        "  - id: 1\n"
        "    name: ONE\n"
        "---\n"
        "middle_users:\n"
        "  - id: 2\n"
        "    name: two\n"
        "  - id: 4\n"
        "    name: four\n"
        "---\n"
        "last_users: []\n"
    )
    assert_bytes_equal(read_bytes(path), expected)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_sql_multirow_insert_then_delete_and_last_definer_win(tmp_path, encode):
    source = encode(
        "users:\n"
        "  - id: 1\n"
        "    name: first\n"
        "---\n"
        "users:\n"
        "  - id: 9\n"
        "    name: last\n"
    )
    path = write_bytes(tmp_path / "sql-last-definer.yaml", source)
    yamlql = YamlQL(str(path), mode="rw")
    try:
        yamlql.query("UPDATE users SET name = 'LAST' WHERE id = 9")
        yamlql.query("INSERT INTO users (id, name) VALUES (10, 'ten'), (11, 'eleven')")
        yamlql.query("DELETE FROM users WHERE id IN (9, 10)")
        selected = yamlql.query("SELECT id, name FROM users")
        assert selected.to_dict("records") == [{"id": 11, "name": "eleven"}]
    finally:
        yamlql.close()

    expected = encode(
        "users:\n"
        "  - id: 1\n"
        "    name: first\n"
        "---\n"
        "users:\n"
        "  - id: 11\n"
        "    name: eleven\n"
    )
    assert_bytes_equal(read_bytes(path), expected)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_root_list_multidocument_sql_write_fails_without_changing_file(tmp_path, encode):
    source = encode("- root list item\n---\nusers:\n  - id: 1\n    name: safe\n")
    path = write_bytes(tmp_path / "root-list.yaml", source)

    with pytest.raises(Exception, match="(?i)(root|list|table|update)"):
        yamlql = YamlQL(str(path), mode="rw")
        try:
            yamlql.query("UPDATE root SET name = 'unsafe'")
        finally:
            yamlql.close()
    assert_bytes_equal(read_bytes(path), source)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_single_document_controls_remain_byte_exact(tmp_path, encode):
    """Single-document controls cover no-op, direct edit, transaction, and SQL CRUD."""
    source = encode("users:\n  - id: 1\n    name: before\nsettings:\n  enabled: true\n")
    path = write_bytes(tmp_path / "single.yaml", source)

    assert_bytes_equal(noop_roundtrip(path), source)
    writer = _writer(path)
    writer.set_value("settings.enabled", False)
    writer.write()
    expected_after_writer = encode("users:\n  - id: 1\n    name: before\nsettings:\n  enabled: false\n")
    assert_bytes_equal(read_bytes(path), expected_after_writer)

    with TransactionManager(path) as transaction:
        writer = transaction.get_writer()
        writer.load()
        writer.insert_value("settings.owner", "team")
    expected_after_transaction = encode(
        "users:\n  - id: 1\n    name: before\nsettings:\n  enabled: false\n  owner: team\n"
    )
    assert_bytes_equal(read_bytes(path), expected_after_transaction)

    yamlql = YamlQL(str(path), mode="rw")
    try:
        yamlql.query("UPDATE users SET name = 'after' WHERE id = 1")
        yamlql.query("INSERT INTO users (id, name) VALUES (2, 'second')")
        yamlql.query("DELETE FROM users WHERE id = 1")
    finally:
        yamlql.close()
    expected_after_sql = encode(
        "users:\n"
        "  - id: 2\n"
        "    name: second\n"
        "settings:\n"
        "  enabled: false\n"
        "  owner: team\n"
    )
    assert_bytes_equal(read_bytes(path), expected_after_sql)
