"""Performance, parse-boundary, and batch-contract tests for list editing.

The batch assertions deliberately describe the public API planned for Task 4-2.
Their expected YAML bytes are literals: sequential single-item calls are only a
secondary proof that the hand-authored output remains achievable today.
"""

import os
import sys
import time
from collections import Counter
from pathlib import Path

import pytest
import pandas as pd
from sqlglot import parse_one
from ruamel.yaml import YAML

from tests.fidelity_utils import assert_bytes_equal, read_bytes, to_crlf, to_lf, write_bytes
from yamlql_library import YamlQL
from yamlql_library.crud_handlers import DeleteHandler, InsertHandler
from yamlql_library.transaction import TransactionManager
from yamlql_library.writer import AnchorInUseError, YamlWriter, YamlWriterPolicyError


ENCODERS = (to_lf, to_crlf)
PERF_RECORD = pytest.mark.skipif(
    os.environ.get("YAMLQL_PERF_RECORD") != "1",
    reason="set YAMLQL_PERF_RECORD=1 to collect non-asserting scale timings",
)


def _writer(path):
    writer = YamlWriter(path)
    writer.load()
    return writer


def _items(count):
    return "items:\n" + "".join(
        "  - id: {0}\n    value: old-{0}\n".format(index)
        for index in range(count)
    )


def _append_expected(encode):
    return encode(
        "items:\n"
        "  - id: 1\n"
        "    name: one\n"
        "  - id: 2\n"
        "    name: two\n"
        "  - id: 3\n"
        "    name: three\n"
    )


def _counter(monkeypatch):
    """Count public ruamel entry points by the direct calling library module."""
    counts = Counter()
    originals = {name: getattr(YAML, name) for name in ("load_all", "load", "parse")}

    def make_wrapper(name, original):
        def counted(self, *args, **kwargs):
            caller = sys._getframe(1)
            module = caller.f_globals.get("__name__", "")
            if module == "yamlql_library.writer":
                if name == "load_all":
                    counts["writer_load_all"] += 1
                elif name == "parse":
                    counts["writer_event_parse"] += 1
            elif module == "yamlql_library.transaction":
                if caller.f_code.co_name == "_validate_yaml":
                    counts["txn_validation_parse"] += 1
                elif caller.f_code.co_name == "commit":
                    counts["txn_backup_parse"] += 1
            return original(self, *args, **kwargs)

        return counted

    for name, original in originals.items():
        monkeypatch.setattr(YAML, name, make_wrapper(name, original))
    return counts


def _commit_no_edit(path):
    transaction = TransactionManager(path)
    transaction.begin()
    writer = transaction.get_writer()
    writer.load()
    transaction.commit()


def _assert_clean_after_failed_batch(path, before, operation):
    writer = _writer(path)
    with pytest.raises((AnchorInUseError, IndexError, KeyError, TypeError, ValueError, RuntimeError)):
        operation(writer)
    assert_bytes_equal(writer.render(), before)
    assert_bytes_equal(read_bytes(path), before)
    fresh = _writer(path)
    assert fresh.documents == writer.documents


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_sequential_append_golden_is_hand_authored_and_byte_exact(tmp_path, encode):
    """Green pin for the batch append golden; the literal is the oracle."""
    source = encode("items:\n  - id: 1\n    name: one\n")
    path = write_bytes(tmp_path / "block.yaml", source)
    writer = _writer(path)
    writer.append_item("items", {"id": 2, "name": "two"})
    writer.append_item("items", {"id": 3, "name": "three"})
    writer.write()
    assert_bytes_equal(read_bytes(path), _append_expected(encode))


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_sequential_delete_golden_is_hand_authored_and_byte_exact(tmp_path, encode):
    """Green pin for deletion goldens, including the writer's index refresh."""
    source = encode("items:\n  - one\n  - two\n  - three\n")
    path = write_bytes(tmp_path / "sequential-delete.yaml", source)
    writer = _writer(path)
    writer.delete_value("items.1")
    writer.write()
    assert_bytes_equal(read_bytes(path), encode("items:\n  - one\n  - three\n"))


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
@pytest.mark.parametrize(
    "source, list_path, doc, values, expected",
    [
        (
            "items:\n  - id: 1\n    name: one\n",
            "items",
            None,
            [{"id": 2, "name": "two"}, {"id": 3, "name": "three"}],
            "items:\n  - id: 1\n    name: one\n  - id: 2\n    name: two\n  - id: 3\n    name: three\n",
        ),
        (
            "items: [one, two]\n",
            "items",
            None,
            ["three", "four"],
            "items: [one, two, three, four]\n",
        ),
        (
            "items:\n  # between items\n  - one\n  - two\n",
            "items",
            None,
            ["three"],
            "items:\n  # between items\n  - one\n  - two\n  - three\n",
        ),
        (
            "---\nignored: true\n---\nitems:\n  - one\n",
            "items",
            1,
            ["two"],
            "---\nignored: true\n---\nitems:\n  - one\n  - two\n",
        ),
        (
            "- one\n",
            None,
            None,
            ["two"],
            "- one\n- two\n",
        ),
    ],
)
def test_append_items_contract_has_hand_authored_goldens(
    tmp_path, encode, source, list_path, doc, values, expected
):
    path = write_bytes(tmp_path / "append.yaml", encode(source))
    writer = _writer(path)
    writer.append_items(list_path, values, doc=doc)
    writer.write()
    assert_bytes_equal(read_bytes(path), encode(expected))


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
@pytest.mark.parametrize(
    "source, list_path, doc, indexes, expected",
    [
        (
            "items:\n  - one\n  - two\n  - three\n",
            "items",
            None,
            [1],
            "items:\n  - one\n  - three\n",
        ),
        ("items: [one, two, three]\n", "items", None, [1], "items: [one, three]\n"),
        (
            "---\nignored: true\n---\nitems:\n  - one\n  - two\n",
            "items",
            1,
            [0],
            "---\nignored: true\n---\nitems:\n  - two\n",
        ),
        ("- one\n- two\n", "", None, [0], "- two\n"),
    ],
)
def test_delete_values_contract_has_hand_authored_goldens(
    tmp_path, encode, source, list_path, doc, indexes, expected
):
    path = write_bytes(tmp_path / "delete.yaml", encode(source))
    writer = _writer(path)
    writer.delete_values(list_path, indexes, doc=doc)
    writer.write()
    assert_bytes_equal(read_bytes(path), encode(expected))


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_stale_state_sequence_single_item_green_pin(tmp_path, encode):
    source = encode("items:\n  # owned by one\n  - one\n  - two\n")
    path = write_bytes(tmp_path / "stale.yaml", source)
    writer = _writer(path)
    writer.append_item("items", "three")
    writer.delete_value("items.0")
    writer.delete_value("items.1")
    writer.write()
    assert_bytes_equal(read_bytes(path), encode("items:\n  - two\n"))


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_stale_state_sequence_batch_contract(tmp_path, encode):
    path = write_bytes(tmp_path / "stale-batch.yaml", encode("items: [one, two]\n"))
    writer = _writer(path)
    writer.append_items("items", ["three"])
    writer.delete_values("items", [0, 2])
    writer.write()
    assert_bytes_equal(read_bytes(path), encode("items: [two]\n"))


def test_same_name_anchor_deletion_remains_rejected_and_unchanged(tmp_path):
    source = (
        b"items:\n"
        b"  - &same one\n"
        b"  - *same\n"
        b"  - &same two\n"
        b"  - *same\n"
    )
    path = write_bytes(tmp_path / "same-anchor.yaml", source)
    writer = _writer(path)
    with pytest.raises((AnchorInUseError, ValueError)):
        writer.delete_value("items.0")
    assert_bytes_equal(read_bytes(path), source)


def test_batch_anchor_protected_item_is_atomic(tmp_path):
    source = b"items:\n  - &saved one\n  - *saved\n  - two\n"
    path = write_bytes(tmp_path / "anchor-batch.yaml", source)
    _assert_clean_after_failed_batch(path, source, lambda writer: writer.delete_values("items", [2, 0]))


def test_batch_out_of_range_delete_is_atomic(tmp_path):
    source = b"items:\n  - one\n  - two\n"
    path = write_bytes(tmp_path / "atomic-delete.yaml", source)
    _assert_clean_after_failed_batch(path, source, lambda writer: writer.delete_values("items", [0, 8]))


def test_empty_batch_is_a_byte_exact_no_op(tmp_path):
    source = b"items:\n  - one\n"
    path = write_bytes(tmp_path / "empty.yaml", source)
    writer = _writer(path)
    writer.append_items("items", [])
    writer.delete_values("items", [])
    writer.write()
    assert_bytes_equal(read_bytes(path), source)


def test_append_batch_rejection_is_atomic(tmp_path):
    source = b"items:\n  - one\n"
    path = write_bytes(tmp_path / "atomic-append.yaml", source)
    _assert_clean_after_failed_batch(
        path,
        source,
        lambda writer: writer.append_items("items", ["two", object()]),
    )


def test_batch_failure_injection_is_atomic_for_source_and_flow_paths(tmp_path, monkeypatch):
    source = b"block:\n  - one\n  - two\nflow: [one, two]\n"
    original_parse = YAML.parse

    def fail_second_parse(self, *args, **kwargs):
        parse_calls["count"] += 1
        if parse_calls["count"] == 2:
            raise RuntimeError("injected parse failure")
        return original_parse(self, *args, **kwargs)

    source_path = write_bytes(tmp_path / "injected-source.yaml", source)
    with monkeypatch.context() as patch:
        parse_calls = {"count": 0}
        patch.setattr(YAML, "parse", fail_second_parse)
        _assert_clean_after_failed_batch(
            source_path,
            source,
            lambda writer: writer.append_items("block", ["three", "four"]),
        )

    flow_path = write_bytes(tmp_path / "injected-flow.yaml", source)
    original_dump = YAML.dump

    def fail_second_dump(self, *args, **kwargs):
        dump_calls["count"] += 1
        if dump_calls["count"] == 2:
            raise RuntimeError("injected dump failure")
        return original_dump(self, *args, **kwargs)

    # Flow-style append uses the in-memory fallback, which does not parse.
    # Fail preparation of the second item so the first was already prepared.
    with monkeypatch.context() as patch:
        dump_calls = {"count": 0}
        patch.setattr(YAML, "dump", fail_second_dump)
        _assert_clean_after_failed_batch(
            flow_path,
            source,
            lambda writer: writer.append_items("flow", ["three", "four"]),
        )


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
@pytest.mark.parametrize(
    "source, list_path, appended_index, doc",
    [
        ("items:\n  - one\n  - two\n", "items", 2, None),
        (
            "items:\n  - id: 1\n    nested:\n      value: one\n",
            "items",
            1,
            None,
        ),
        ("items:\n  - one\n  # owned by two\n  - two\n", "items", 2, None),
        ("items:\n  - one\n  # trailing comment\n", "items", 1, None),
        ("items:\n  - one", "items", 1, None),
        ("outer:\n  items:\n    - one\n", "outer.items", 1, None),
        ("---\nignored: true\n---\nitems:\n  - one\n", "items", 1, 1),
        ("items:\n  - &last one\n", "items", 1, None),
        ("items: [one, two]\n", "items", 2, None),
        ("items: []\n", "items", 0, None),
    ],
)
def test_append_then_delete_appended_item_restores_hand_authored_bytes(
    tmp_path, encode, source, list_path, appended_index, doc
):
    """The stale append-delete path must restore the literal original bytes."""
    expected = encode(source)
    path = write_bytes(tmp_path / "stale-append-delete.yaml", expected)
    writer = _writer(path)
    writer.append_item(list_path, {"id": 2, "nested": {"value": "new"}} if "nested:" in source else "new", doc=doc)
    writer.delete_value("%s.%s" % (list_path, appended_index), doc=doc)
    assert_bytes_equal(writer.render(), expected)
    writer.write()
    assert_bytes_equal(read_bytes(path), expected)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_multiple_stale_appends_and_deletions_restore_hand_authored_bytes(tmp_path, encode):
    source = "items:\n  - one\n  - two\n"
    expected = encode(source)
    path = write_bytes(tmp_path / "stale-two-appends.yaml", expected)
    writer = _writer(path)
    writer.append_item("items", "three")
    writer.append_item("items", "four")
    writer.delete_value("items.3")
    writer.delete_value("items.2")
    assert_bytes_equal(writer.render(), expected)
    writer.write()
    assert_bytes_equal(read_bytes(path), expected)


@pytest.mark.parametrize("size", [10, 100, 500])
def test_writer_batch_parse_delta_is_constant_in_n(tmp_path, monkeypatch, size):
    """One future batch must cause no more than one tree/event reparse."""
    counts = _counter(monkeypatch)
    path = write_bytes(tmp_path / ("batch-%s.yaml" % size), _items(size).encode("utf-8"))
    writer = _writer(path)
    baseline = Counter(counts)
    writer.append_items("items", [{"id": size + index, "value": "new"} for index in range(size)])
    delta = Counter(counts)
    delta.subtract(baseline)
    assert delta["writer_load_all"] <= 1
    assert delta["writer_event_parse"] <= 1


@pytest.mark.parametrize("size", [10, 100, 500])
def test_sql_multirow_insert_writer_parse_delta_is_constant_in_n(tmp_path, monkeypatch, size):
    counts = _counter(monkeypatch)
    path = write_bytes(tmp_path / ("sql-%s.yaml" % size), _items(size).encode("utf-8"))
    yamlql = YamlQL(str(path), mode="rw")
    try:
        no_edit_baseline = Counter(counts)
        _commit_no_edit(path)
        no_edit_delta = Counter(counts)
        no_edit_delta.subtract(no_edit_baseline)
        baseline = Counter(counts)
        values = ", ".join("(%s, 'new-%s')" % (size + index, index) for index in range(size))
        yamlql.query("INSERT INTO items (id, value) VALUES " + values)
        delta = Counter(counts)
        delta.subtract(baseline)
        assert delta["writer_load_all"] <= 1
        assert delta["writer_event_parse"] - no_edit_delta["writer_event_parse"] <= 1
        assert delta["txn_validation_parse"] == no_edit_delta["txn_validation_parse"] == 1
    finally:
        yamlql.close()


@pytest.mark.parametrize("size", [10, 100, 500])
def test_sql_multirow_delete_writer_parse_delta_is_constant_in_n(tmp_path, monkeypatch, size):
    """A 100-row DELETE has one validation parse and at most one extra event parse.

    One extra writer event parse over a no-edit transaction is permitted because
    the batch source splice validates the completed candidate YAML stream once.
    """
    counts = _counter(monkeypatch)
    path = write_bytes(tmp_path / ("sql-delete-%s.yaml" % size), _items(1000).encode("utf-8"))
    yamlql = YamlQL(str(path), mode="rw")
    try:
        no_edit_baseline = Counter(counts)
        _commit_no_edit(path)
        no_edit_delta = Counter(counts)
        no_edit_delta.subtract(no_edit_baseline)
        baseline = Counter(counts)
        ids = ", ".join(str(index) for index in range(100))
        result = yamlql.query("DELETE FROM items WHERE id IN (" + ids + ")")
        assert result["success"]
        delta = Counter(counts)
        delta.subtract(baseline)
        assert delta["writer_load_all"] <= 1
        assert delta["writer_event_parse"] - no_edit_delta["writer_event_parse"] <= 1
        assert delta["txn_validation_parse"] == no_edit_delta["txn_validation_parse"] == 1
    finally:
        yamlql.close()


def test_sql_multirow_delete_anchor_failure_is_wrapped_atomic_and_cleans_transaction_files(tmp_path, monkeypatch):
    source = b"items:\n  - &saved one\n  - *saved\n  - two\n"
    path = write_bytes(tmp_path / "delete-anchor-failure.yaml", source)
    yamlql = YamlQL(str(path), mode="rw")
    try:
        monkeypatch.setattr(
            yamlql._db.delete_handler,
            "_find_matching_rows",
            lambda table_name, where_clause: pd.DataFrame(
                {"_yaml_path": ["root.items.0", "root.items.2"]}
            ),
        )
        result = yamlql._db.delete_handler.handle(
            parse_one("DELETE FROM items WHERE value IN ('one', 'two')", dialect="duckdb")
        )
    finally:
        yamlql.close()
    assert not result["success"]
    assert result["message"].startswith("DELETE failed:")
    assert "Cannot delete anchored node 'saved'" in result["message"]
    assert_bytes_equal(read_bytes(path), source)
    assert not path.with_suffix(path.suffix + ".backup").exists()
    assert not list(tmp_path.glob(path.stem + ".*.tmp"))


def test_sql_multirow_insert_writer_failure_is_wrapped_atomic_and_cleans_transaction_files(tmp_path, monkeypatch):
    source = b"items:\n  - id: 1\n    value: old\n"
    path = write_bytes(tmp_path / "insert-failure.yaml", source)

    def injected_failure(self, list_path, items, doc=None):
        raise RuntimeError("injected append failure")

    monkeypatch.setattr(YamlWriter, "append_items", injected_failure)
    yamlql = YamlQL(str(path), mode="rw")
    try:
        result = yamlql._db.insert_handler.handle(
            parse_one(
                "INSERT INTO items (id, value) VALUES (2, 'new'), (3, 'later')",
                dialect="duckdb",
            )
        )
    finally:
        yamlql.close()
    assert not result["success"]
    assert result["message"].startswith("INSERT failed:")
    assert "injected append failure" in result["message"]
    assert_bytes_equal(read_bytes(path), source)
    assert not path.with_suffix(path.suffix + ".backup").exists()
    assert not list(tmp_path.glob(path.stem + ".*.tmp"))


def test_sql_multirow_insert_policy_error_uses_rowless_batch_message(tmp_path, monkeypatch):
    source = b"items:\n  - id: 1\n    value: old\n"
    path = write_bytes(tmp_path / "insert-policy-message.yaml", source)
    monkeypatch.setattr(
        YamlWriter,
        "append_items",
        lambda self, list_path, items, doc=None: (_ for _ in ()).throw(
            YamlWriterPolicyError("batch policy rejection")
        ),
    )
    yamlql = YamlQL(str(path), mode="rw")
    try:
        result = yamlql._db.insert_handler.handle(
            parse_one(
                "INSERT INTO items (id, value) VALUES (2, 'new'), (3, 'later')",
                dialect="duckdb",
            )
        )
    finally:
        yamlql.close()
    assert result["message"] == "INSERT failed: Cannot insert into table 'items': batch policy rejection"
    assert_bytes_equal(read_bytes(path), source)


def test_insert_handler_cross_document_fallback_keeps_hand_authored_bytes(tmp_path):
    source = b"---\nfirst:\n  - id: 1\n---\nsecond:\n  - id: 2\n"
    expected = (
        b"---\nfirst:\n  - id: 1\n  - id: 3\n"
        b"---\nsecond:\n  - id: 2\n  - id: 4\n"
    )
    path = write_bytes(tmp_path / "cross-document-insert.yaml", source)
    handler = InsertHandler(str(path), {}, {}, None)
    transaction = TransactionManager(path)
    transaction.begin()
    writer = transaction.get_writer()
    writer.load()
    handler._append_rows_to_list_table("first", [{"id": 3}], writer, 0)
    handler._append_rows_to_list_table("second", [{"id": 4}], writer, 1)
    transaction.commit()
    assert_bytes_equal(read_bytes(path), expected)


def test_delete_handler_cross_document_groups_keep_hand_authored_bytes(tmp_path):
    source = (
        b"---\nfirst:\n  - id: 1\n  - id: 2\n"
        b"---\nsecond:\n  - id: 3\n  - id: 4\n"
    )
    expected = b"---\nfirst:\n  - id: 2\n---\nsecond:\n  - id: 4\n"
    path = write_bytes(tmp_path / "cross-document-delete.yaml", source)
    handler = DeleteHandler(str(path), {}, {}, None)
    rows = pd.DataFrame({"_yaml_path": ["root.first.0", "root.second.0"]})
    handler._delete_rows("items", rows)
    assert_bytes_equal(read_bytes(path), expected)


def test_delete_handler_cross_list_and_mixed_paths_keep_hand_authored_bytes(tmp_path):
    source = (
        b"first:\n  - id: 1\n  - id: 2\n"
        b"second:\n  - id: 3\n  - id: 4\n"
        b"settings:\n  enabled: true\n"
    )
    expected = b"first:\n  - id: 2\nsecond:\n  - id: 4\nsettings: {}\n"
    path = write_bytes(tmp_path / "cross-list-mixed-delete.yaml", source)
    handler = DeleteHandler(str(path), {}, {}, None)
    rows = pd.DataFrame(
        {"_yaml_path": ["root.first.0", "root.second.0", "root.settings.enabled"]}
    )
    handler._delete_rows("items", rows)
    assert_bytes_equal(read_bytes(path), expected)


def test_sql_multirow_delete_g4_rejection_is_wrapped_and_unchanged(tmp_path, monkeypatch):
    source = b"items:\n  - &same one\n  - *same\n  - &same two\n  - *same\n"
    path = write_bytes(tmp_path / "g4-delete.yaml", source)
    yamlql = YamlQL(str(path), mode="rw")
    try:
        monkeypatch.setattr(
            yamlql._db.delete_handler,
            "_find_matching_rows",
            lambda table_name, where_clause: pd.DataFrame(
                {"_yaml_path": ["root.items.0", "root.items.2"]}
            ),
        )
        monkeypatch.setattr(
            YamlWriter,
            "delete_values",
            lambda self, list_path, indexes, doc=None: (_ for _ in ()).throw(
                YamlWriterPolicyError(
                    "Cannot resolve anchor 'same' identity in document 0"
                )
            ),
        )
        result = yamlql._db.delete_handler.handle(
            parse_one("DELETE FROM items WHERE value IN ('one', 'two')", dialect="duckdb")
        )
    finally:
        yamlql.close()
    assert not result["success"]
    assert result["message"] == (
        "DELETE failed: Cannot delete from table 'items': "
        "Cannot resolve anchor 'same' identity in document 0"
    )
    assert_bytes_equal(read_bytes(path), source)


@PERF_RECORD
@pytest.mark.perf
def test_batch_statement_is_faster_than_sequential_reference_at_1000_items(tmp_path):
    source = _items(1000).encode("utf-8")
    batch_path = write_bytes(tmp_path / "batch-statement.yaml", source)
    sequential_path = write_bytes(tmp_path / "sequential-statement.yaml", source)
    values = ", ".join("(%s, 'new-%s')" % (1000 + index, index) for index in range(100))

    batch = YamlQL(str(batch_path), mode="rw")
    try:
        started = time.perf_counter()
        assert batch.query("INSERT INTO items (id, value) VALUES " + values)["success"]
        batch_seconds = time.perf_counter() - started
    finally:
        batch.close()

    started = time.perf_counter()
    for index in range(100):
        sequential = YamlQL(str(sequential_path), mode="rw")
        try:
            assert sequential.query(
                "INSERT INTO items (id, value) VALUES (%s, 'new-%s')"
                % (1000 + index, index)
            )["success"]
        finally:
            sequential.close()
    sequential_seconds = time.perf_counter() - started
    print(
        "batch_1000_100_rows_s=%.6f sequential_1000_100_rows_s=%.6f"
        % (batch_seconds, sequential_seconds)
    )
    assert batch_seconds < sequential_seconds


def test_sql_multirow_delete_is_byte_exact_against_hand_authored_golden(tmp_path):
    source = (
        b"items:\n"
        b"  - id: 1\n"
        b"    value: one\n"
        b"  - id: 2\n"
        b"    value: two\n"
        b"  - id: 3\n"
        b"    value: three\n"
    )
    expected = b"items:\n  - id: 2\n    value: two\n"
    path = write_bytes(tmp_path / "sql-delete.yaml", source)
    yamlql = YamlQL(str(path), mode="rw")
    try:
        result = yamlql.query("DELETE FROM items WHERE id IN (1, 3)")
        assert result["success"]
    finally:
        yamlql.close()
    assert_bytes_equal(read_bytes(path), expected)


def test_transaction_parse_characterization_and_direct_mutation(tmp_path, monkeypatch):
    counts = _counter(monkeypatch)
    source = b"items:\n  - id: 1\n    value: old\n"
    clean_path = write_bytes(tmp_path / "clean.yaml", source)
    _commit_no_edit(clean_path)
    assert counts["txn_backup_parse"] == 1
    assert counts["txn_validation_parse"] == 1
    assert counts["writer_load_all"] == 1
    assert counts["writer_event_parse"] == 1

    counts.clear()
    dirty_path = write_bytes(tmp_path / "dirty.yaml", source)
    transaction = TransactionManager(dirty_path)
    transaction.begin()
    writer = transaction.get_writer()
    writer.load()
    writer.append_item("items", {"id": 2, "value": "new"})
    transaction.commit()
    assert counts["txn_backup_parse"] == 0
    assert counts["txn_validation_parse"] == 1

    counts.clear()
    direct_path = write_bytes(tmp_path / "direct.yaml", source)
    transaction = TransactionManager(direct_path)
    transaction.begin()
    writer = transaction.get_writer()
    writer.load()
    writer.data["items"][0]["value"] = "direct"
    transaction.commit()
    assert counts["txn_backup_parse"] == 1
    assert counts["txn_validation_parse"] == 1
    assert b"value: direct" in read_bytes(direct_path)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
@pytest.mark.parametrize(
    "operation, source, expected",
    [
        (
            lambda writer: writer.append_items("items", ["three", "four"]),
            "items:\n  - one\n  - two\n",
            "items:\n  - one\n  - two\n  - three\n  - four\n",
        ),
        (
            lambda writer: writer.delete_values("items", [1, 3]),
            "items:\n  - one\n  - two\n  - three\n  - four\n",
            "items:\n  - one\n  - three\n",
        ),
    ],
    ids=["append-items", "delete-values"],
)
def test_transaction_commit_after_lazy_batch_is_byte_exact_and_validates_once(
    tmp_path, monkeypatch, encode, operation, source, expected
):
    """A stale source splice refreshes once, then commits through one temp parse."""
    counts = _counter(monkeypatch)
    path = write_bytes(tmp_path / "lazy-batch.yaml", encode(source))
    transaction = TransactionManager(path)
    transaction.begin()
    writer = transaction.get_writer()
    writer.load()

    operation(writer)
    transaction.commit()

    assert_bytes_equal(read_bytes(path), encode(expected))
    assert counts["txn_backup_parse"] == 0
    assert counts["writer_load_all"] == 1
    assert counts["txn_validation_parse"] == 1


def test_transaction_clean_writer_direct_data_mutation_persists_and_validates_once(
    tmp_path, monkeypatch
):
    """The backup comparison detects direct public tree mutation on a clean writer."""
    counts = _counter(monkeypatch)
    source = b"items:\n  - id: 1\n    value: old\n"
    expected = b"items:\n  - id: 1\n    value: direct\n"
    path = write_bytes(tmp_path / "direct-mutation.yaml", source)
    transaction = TransactionManager(path)
    transaction.begin()
    writer = transaction.get_writer()
    writer.load()
    writer.data["items"][0]["value"] = "direct"

    transaction.commit()

    assert_bytes_equal(read_bytes(path), expected)
    assert counts["txn_backup_parse"] == 1
    assert counts["txn_validation_parse"] == 1


def test_transaction_validation_failure_rolls_back_and_cleans_files(tmp_path, monkeypatch):
    source = b"items:\n  - id: 1\n    value: old\n"
    path = write_bytes(tmp_path / "validation-failure.yaml", source)
    transaction = TransactionManager(path)
    transaction.begin()
    writer = transaction.get_writer()
    writer.load()
    writer.set_value("items.0.value", "changed")
    monkeypatch.setattr(
        writer,
        "write_to",
        lambda destination: Path(destination).write_bytes(b"items: []\n---\nextra: true\n"),
    )

    with pytest.raises(IOError, match="Failed to commit transaction"):
        transaction.commit()
    transaction.rollback()

    assert_bytes_equal(read_bytes(path), source)
    assert not path.with_suffix(path.suffix + ".backup").exists()
    assert not list(tmp_path.glob(path.stem + ".*.tmp"))


@pytest.mark.perf
def test_writer_single_item_edit_ceiling_on_1000_item_list(tmp_path):
    path = write_bytes(tmp_path / "ceiling.yaml", _items(1000).encode("utf-8"))
    writer = _writer(path)
    started = time.perf_counter()
    writer.append_item("items", {"id": 1000, "value": "new"})
    assert time.perf_counter() - started <= 1.0
    started = time.perf_counter()
    writer.delete_value("items.1000")
    assert time.perf_counter() - started <= 1.0


@pytest.mark.perf
def test_multirow_insert_writer_boundary_ceiling_on_1000_item_list(tmp_path, monkeypatch):
    counts = _counter(monkeypatch)
    path = write_bytes(tmp_path / "statement-ceiling.yaml", _items(1000).encode("utf-8"))
    yamlql = YamlQL(str(path), mode="rw")
    try:
        no_edit_baseline = Counter(counts)
        _commit_no_edit(path)
        no_edit_delta = Counter(counts)
        no_edit_delta.subtract(no_edit_baseline)
        baseline = Counter(counts)
        values = ", ".join("(%s, 'new')" % (1000 + index) for index in range(100))
        yamlql.query("INSERT INTO items (id, value) VALUES " + values)
        delta = Counter(counts)
        delta.subtract(baseline)
        assert delta["writer_load_all"] <= 1
        assert delta["writer_event_parse"] - no_edit_delta["writer_event_parse"] <= 1
        assert delta["txn_validation_parse"] == no_edit_delta["txn_validation_parse"] == 1
    finally:
        yamlql.close()


@PERF_RECORD
@pytest.mark.perf
@pytest.mark.parametrize("size", [500, 1000, 5000])
def test_scale_record_prints_writer_refresh_and_statement_timings(tmp_path, size):
    path = write_bytes(tmp_path / ("record-%s.yaml" % size), _items(size).encode("utf-8"))
    writer = _writer(path)
    started = time.perf_counter()
    writer.append_item("items", {"id": size, "value": "new"})
    edit_seconds = time.perf_counter() - started
    started = time.perf_counter()
    writer.load()
    refresh_seconds = time.perf_counter() - started
    yamlql = YamlQL(str(path), mode="rw")
    try:
        started = time.perf_counter()
        yamlql.query("INSERT INTO items (id, value) VALUES (%s, 'statement')" % (size + 1))
        statement_seconds = time.perf_counter() - started
    finally:
        yamlql.close()
    print(
        "size=%s writer_edit_s=%.6f first_refresh_s=%.6f whole_statement_s=%.6f"
        % (size, edit_seconds, refresh_seconds, statement_seconds)
    )
