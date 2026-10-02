"""Golden-byte regression tests for YAML writer comment ownership."""

import pytest

from tests.fidelity_utils import assert_bytes_equal, read_bytes, run_sql, to_crlf, to_lf, write_bytes
from yamlql_library.writer import YamlWriter


def _write_with_writer(path, operation):
    writer = YamlWriter(path)
    writer.load()
    operation(writer)
    writer.write()
    return read_bytes(path)


def _delete_with_sql(path, predicate):
    result = run_sql(path, "DELETE FROM items WHERE name = '{}'".format(predicate))
    assert result["success"]
    return read_bytes(path)


def test_delete_middle_item_removes_its_owned_comments_writer(tmp_path):
    path = write_bytes(
        tmp_path / "middle.yaml",
        to_lf(
            "items:\n"
            "  - name: first  # first EOL\n"
            "  # middle leading\n"
            "  - name: middle  # middle EOL\n"
            "  # last leading\n"
            "  - name: last  # last EOL\n"
            "next: unchanged\n"
        ),
    )

    actual = _write_with_writer(path, lambda writer: writer.delete_value("items.1"))

    assert_bytes_equal(
        actual,
        to_lf(
            "items:\n"
            "  - name: first  # first EOL\n"
            "  # last leading\n"
            "  - name: last  # last EOL\n"
            "next: unchanged\n"
        ),
    )


def test_delete_middle_item_removes_its_owned_comments_sql(tmp_path):
    path = write_bytes(
        tmp_path / "middle-sql.yaml",
        to_lf(
            "items:\n"
            "  - name: first  # first EOL\n"
            "  # middle leading\n"
            "  - name: middle  # middle EOL\n"
            "  # last leading\n"
            "  - name: last  # last EOL\n"
            "next: unchanged\n"
        ),
    )

    actual = _delete_with_sql(path, "middle")

    assert_bytes_equal(
        actual,
        to_lf(
            "items:\n"
            "  - name: first  # first EOL\n"
            "  # last leading\n"
            "  - name: last  # last EOL\n"
            "next: unchanged\n"
        ),
    )


def test_delete_first_item_removes_comment_between_parent_and_item_writer(tmp_path):
    path = write_bytes(
        tmp_path / "first.yaml",
        to_lf(
            "items:\n"
            "  # first leading\n"
            "  - name: first  # first EOL\n"
            "  # second leading\n"
            "  - name: second  # second EOL\n"
        ),
    )

    actual = _write_with_writer(path, lambda writer: writer.delete_value("items.0"))

    assert_bytes_equal(
        actual,
        to_lf(
            "items:\n"
            "  # second leading\n"
            "  - name: second  # second EOL\n"
        ),
    )


def test_delete_first_item_removes_comment_between_parent_and_item_sql(tmp_path):
    path = write_bytes(
        tmp_path / "first-sql.yaml",
        to_lf(
            "items:\n"
            "  # first leading\n"
            "  - name: first  # first EOL\n"
            "  # second leading\n"
            "  - name: second  # second EOL\n"
        ),
    )

    actual = _delete_with_sql(path, "first")

    assert_bytes_equal(
        actual,
        to_lf(
            "items:\n"
            "  # second leading\n"
            "  - name: second  # second EOL\n"
        ),
    )


def test_delete_last_item_keeps_comment_before_next_top_level_key_writer(tmp_path):
    path = write_bytes(
        tmp_path / "last.yaml",
        to_lf(
            "items:\n"
            "  - name: first\n"
            "  - name: last  # last EOL\n"
            "\n"
            "# next section\n"
            "next:\n"
            "  value: unchanged\n"
        ),
    )

    actual = _write_with_writer(path, lambda writer: writer.delete_value("items.1"))

    assert_bytes_equal(
        actual,
        to_lf(
            "items:\n"
            "  - name: first\n"
            "\n"
            "# next section\n"
            "next:\n"
            "  value: unchanged\n"
        ),
    )


def test_delete_last_item_keeps_comment_before_next_top_level_key_sql(tmp_path):
    path = write_bytes(
        tmp_path / "last-sql.yaml",
        to_lf(
            "items:\n"
            "  - name: first\n"
            "  - name: last  # last EOL\n"
            "\n"
            "# next section\n"
            "next:\n"
            "  value: unchanged\n"
        ),
    )

    actual = _delete_with_sql(path, "last")

    assert_bytes_equal(
        actual,
        to_lf(
            "items:\n"
            "  - name: first\n"
            "\n"
            "# next section\n"
            "next:\n"
            "  value: unchanged\n"
        ),
    )


def test_delete_multiple_items_highest_index_first(tmp_path):
    path = write_bytes(
        tmp_path / "multiple.yaml",
        to_lf(
            "items:\n"
            "  - name: first\n"
            "  # second leading\n"
            "  - name: second  # second EOL\n"
            "  # third leading\n"
            "  - name: third  # third EOL\n"
            "  - name: fourth\n"
        ),
    )

    def delete_highest_first(writer):
        writer.delete_value("items.2")
        writer.delete_value("items.1")

    actual = _write_with_writer(path, delete_highest_first)

    assert_bytes_equal(
        actual,
        to_lf("items:\n  - name: first\n  - name: fourth\n"),
    )


def test_delete_item_before_blank_line_section_comment_keeps_section(tmp_path):
    path = write_bytes(
        tmp_path / "section.yaml",
        to_lf(
            "items:\n"
            "  - name: first\n"
            "  - name: remove\n"
            "\n"
            "# section after the list\n"
            "next: value\n"
        ),
    )

    actual = _write_with_writer(path, lambda writer: writer.delete_value("items.1"))

    assert_bytes_equal(
        actual,
        to_lf(
            "items:\n"
            "  - name: first\n"
            "\n"
            "# section after the list\n"
            "next: value\n"
        ),
    )


def test_append_item_preserves_comments_and_handles_list_shapes(tmp_path):
    named_path = write_bytes(
        tmp_path / "named.yaml",
        to_lf("items:\n  - old\n# next key comment\nnext: value\n"),
    )
    named_actual = _write_with_writer(
        named_path, lambda writer: writer.append_item("items", "new")
    )
    assert_bytes_equal(
        named_actual,
        to_lf("items:\n  - old\n  - new\n# next key comment\nnext: value\n"),
    )

    root_path = write_bytes(tmp_path / "root.yaml", to_lf("- old\n"))
    root_actual = _write_with_writer(
        root_path, lambda writer: writer.append_item(None, "new")
    )
    assert_bytes_equal(root_actual, to_lf("- old\n- new\n"))

    empty_path = write_bytes(tmp_path / "empty.yaml", to_lf("items: []\n"))
    empty_actual = _write_with_writer(
        empty_path, lambda writer: writer.append_item("items", "new")
    )
    assert_bytes_equal(empty_actual, to_lf("items:\n- new\n"))

    non_list_path = write_bytes(tmp_path / "not-a-list.yaml", to_lf("name: value\n"))
    with pytest.raises(TypeError, match="non-list"):
        _write_with_writer(non_list_path, lambda writer: writer.append_item("name", "new"))
    assert_bytes_equal(read_bytes(non_list_path), to_lf("name: value\n"))


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (
            lambda writer: writer.set_value("items.1", "changed"),
            "base: &b 1  # keep base\nitems:\n  - &x item  # keep item\n  - changed\n",
        ),
        (
            lambda writer: writer.insert_value("items.2", "inserted"),
            "base: &b 1  # keep base\nitems:\n  - &x item  # keep item\n  - sibling\n  - inserted\n",
        ),
        (
            lambda writer: writer.delete_value("items.1"),
            "base: &b 1  # keep base\nitems:\n  - &x item  # keep item\n",
        ),
        (
            lambda writer: writer.set_value("items.0", "updated"),
            "base: &b 1  # keep base\nitems:\n  - &x updated  # keep item\n  - sibling\n",
        ),
    ],
    ids=("edit-sibling", "insert-sibling", "delete-sibling", "update-anchored-scalar"),
)
def test_inline_comments_beside_anchors_survive_mutations(tmp_path, operation, expected):
    path = write_bytes(
        tmp_path / "anchors.yaml",
        to_lf("base: &b 1  # keep base\nitems:\n  - &x item  # keep item\n  - sibling\n"),
    )

    actual = _write_with_writer(path, operation)

    assert_bytes_equal(actual, to_lf(expected))


def test_mapping_key_delete_and_insert_preserve_key_comments(tmp_path):
    delete_path = write_bytes(
        tmp_path / "mapping-delete.yaml",
        to_lf(
            "mapping:\n"
            "  # first leading\n"
            "  first: one  # first EOL\n"
            "  # second leading\n"
            "  second: two  # second EOL\n"
        ),
    )
    delete_actual = _write_with_writer(
        delete_path, lambda writer: writer.delete_value("mapping.first")
    )
    assert_bytes_equal(
        delete_actual,
        to_lf("mapping:\n  # second leading\n  second: two  # second EOL\n"),
    )

    insert_path = write_bytes(
        tmp_path / "mapping-insert.yaml",
        to_lf(
            "mapping:\n"
            "  # first leading\n"
            "  first: one  # first EOL\n"
            "  # second leading\n"
            "  second: two  # second EOL\n"
        ),
    )
    insert_actual = _write_with_writer(
        insert_path, lambda writer: writer.insert_value("mapping.third", "three")
    )
    assert_bytes_equal(
        insert_actual,
        to_lf(
            "mapping:\n"
            "  # first leading\n"
            "  first: one  # first EOL\n"
            "  # second leading\n"
            "  second: two  # second EOL\n"
            "  third: three\n"
        ),
    )


def test_crlf_delete_middle_item_preserves_comment_ownership(tmp_path):
    path = write_bytes(
        tmp_path / "middle-crlf.yaml",
        to_crlf(
            "items:\n"
            "  - name: first  # first EOL\n"
            "  # middle leading\n"
            "  - name: middle  # middle EOL\n"
            "  # last leading\n"
            "  - name: last  # last EOL\n"
            "next: unchanged\n"
        ),
    )

    actual = _write_with_writer(path, lambda writer: writer.delete_value("items.1"))

    assert_bytes_equal(
        actual,
        to_crlf(
            "items:\n"
            "  - name: first  # first EOL\n"
            "  # last leading\n"
            "  - name: last  # last EOL\n"
            "next: unchanged\n"
        ),
    )


def test_crlf_append_item_keeps_comment_before_next_key(tmp_path):
    path = write_bytes(
        tmp_path / "append-crlf.yaml",
        to_crlf("items:\n  - old\n# next key comment\nnext: value\n"),
    )

    actual = _write_with_writer(path, lambda writer: writer.append_item("items", "new"))

    assert_bytes_equal(
        actual,
        to_crlf("items:\n  - old\n  - new\n# next key comment\nnext: value\n"),
    )
