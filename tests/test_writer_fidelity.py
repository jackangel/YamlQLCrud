"""Golden byte-fidelity tests for the YAML write path."""

import re

import pytest

from tests.fidelity_utils import (
    assert_bytes_equal,
    assert_minimal_diff,
    changed_lines,
    noop_roundtrip,
    read_bytes,
    run_sql,
    to_crlf,
    to_lf,
    write_bytes,
)
from yamlql_library.transaction import TransactionManager
from yamlql_library.writer import YamlWriter


@pytest.mark.parametrize(
    "name, source",
    [
        (
            "lf_without_trailing_newline",
            to_lf("settings:\n  enabled: true"),
        ),
        (
            "crlf_with_one_trailing_newline",
            to_crlf("settings:\n  enabled: true\n"),
        ),
        (
            "lf_with_two_trailing_newlines",
            to_lf("settings:\n  enabled: true\n\n"),
        ),
        (
            "four_space_layout",
            to_lf("settings:\n    nested:\n        value: preserved\n"),
        ),
        (
            "indented_dash_layout",
            to_lf("items:\n  - name: first\n    value: 1\n"),
        ),
        (
            "utf8_bom_unicode_and_long_scalar",
            b"\xef\xbb\xbf" + to_lf(
                "title: 配置\n"
                "description: "
                "this plain scalar deliberately exceeds eighty columns without being wrapped "
                "by the round-trip writer\n"
            ),
        ),
        (
            "explicit_document_start_and_comments",
            to_crlf(
                "# header comment\n"
                "---\n"
                "settings:\n"
                "  enabled: true  # inline comment\n"
                "\n"
                "  # between keys\n"
                "  name: service\n"
            ),
        ),
    ],
)
def test_noop_roundtrip_is_byte_identical_for_writer_and_transaction(tmp_path, name, source):
    """No-op writer and transaction round trips retain every source byte."""
    writer_path = write_bytes(tmp_path / (name + "-writer.yaml"), source)
    transaction_path = write_bytes(tmp_path / (name + "-transaction.yaml"), source)

    assert_bytes_equal(noop_roundtrip(writer_path), source)

    transaction = TransactionManager(str(transaction_path))
    transaction.begin()
    transaction.get_writer().load()
    transaction.commit()
    assert_bytes_equal(read_bytes(transaction_path), source)


@pytest.mark.parametrize("line_ending, encode", [("LF", to_lf), ("CRLF", to_crlf)])
def test_sql_update_changes_only_the_target_line_and_preserves_eol(tmp_path, line_ending, encode):
    """A scalar SQL update has a one-line logical diff and keeps file EOLs."""
    source = encode(
        "users:\n"
        "    - name: Alice\n"
        "      age: 30\n"
        "    - name: Bob\n"
        "      age: 25\n"
    )
    path = write_bytes(tmp_path / ("update-" + line_ending + ".yaml"), source)

    run_sql(path, "UPDATE users SET age = 31 WHERE name = 'Alice'")
    output = read_bytes(path)

    assert_minimal_diff(source, output, max_changed_lines=2)
    assert len(changed_lines(source, output)) == 2
    if line_ending == "CRLF":
        assert re.search(br"(?<!\r)\n", output) is None
    else:
        assert b"\r" not in output


@pytest.mark.parametrize("encode", [to_lf, to_crlf], ids=["lf", "crlf"])
def test_sql_insert_matches_four_space_golden_bytes_and_preserves_trailing_newlines(tmp_path, encode):
    """INSERT uses the source list layout, EOL, and two-newline file suffix."""
    source = encode(
        "users:\n"
        "    - name: Alice\n"
        "      age: 30\n"
        "\n"
    )
    expected = encode(
        "users:\n"
        "    - name: Alice\n"
        "      age: 30\n"
        "    - name: Bob\n"
        "      age: 25\n"
        "\n"
    )
    path = write_bytes(tmp_path / "insert.yaml", source)

    run_sql(path, "INSERT INTO users (name, age) VALUES ('Bob', 25)")

    assert_bytes_equal(read_bytes(path), expected)


def test_sql_delete_matches_original_minus_deleted_item_lines(tmp_path):
    """DELETE removes only the selected list item's lines."""
    source = to_crlf(
        "users:\n"
        "    - name: Alice\n"
        "      age: 30\n"
        "    - name: Bob\n"
        "      age: 25\n"
    )
    expected = to_crlf("users:\n    - name: Bob\n      age: 25\n")
    path = write_bytes(tmp_path / "delete.yaml", source)

    run_sql(path, "DELETE FROM users WHERE name = 'Alice'")

    assert_bytes_equal(read_bytes(path), expected)


def test_mixed_indentation_uses_dominant_style_without_changing_untouched_lines(tmp_path):
    """New nested content follows the dominant four-space indentation style."""
    source = to_lf(
        "primary:\n"
        "    nested:\n"
        "        value: first\n"
        "secondary:\n"
        "  nested:\n"
        "    value: second\n"
    )
    expected = to_lf(
        "primary:\n"
        "    nested:\n"
        "        value: first\n"
        "        added: dominant\n"
        "secondary:\n"
        "  nested:\n"
        "    value: second\n"
    )
    path = write_bytes(tmp_path / "mixed.yaml", source)

    writer = YamlWriter(path)
    writer.load()
    writer.insert_value("primary.nested.added", "dominant")
    writer.write()

    assert_bytes_equal(read_bytes(path), expected)


def test_fallback_defaults_for_flat_mapping_and_emptyish_file(tmp_path):
    """Files without nesting use 2/2/0 for new nested content and no-op safely."""
    flat_path = write_bytes(tmp_path / "flat.yaml", to_lf("name: config\n"))
    writer = YamlWriter(flat_path)
    writer.load()
    writer.insert_value("metadata.owner", "platform")
    writer.write()
    assert_bytes_equal(
        read_bytes(flat_path),
        to_lf("name: config\nmetadata:\n  owner: platform\n"),
    )

    emptyish_source = to_lf("# intentionally empty\n\n")
    emptyish_path = write_bytes(tmp_path / "emptyish.yaml", emptyish_source)
    assert_bytes_equal(noop_roundtrip(emptyish_path), emptyish_source)


def test_writer_render_write_to_and_unloaded_write_contract(tmp_path):
    """The writer API renders the exact bytes used by both write methods."""
    source = to_crlf("settings:\n    enabled: false\n")
    original_path = write_bytes(tmp_path / "original.yaml", source)
    destination_path = tmp_path / "copy.yaml"

    unloaded_writer = YamlWriter(original_path)
    with pytest.raises(ValueError):
        unloaded_writer.write()

    writer = YamlWriter(original_path)
    writer.load()
    writer.set_value("settings.enabled", True)
    rendered = writer.render()
    writer.write_to(destination_path)

    assert_bytes_equal(read_bytes(destination_path), rendered)
    assert_bytes_equal(read_bytes(original_path), source)
    writer.write()
    assert_bytes_equal(read_bytes(original_path), rendered)


def test_lf_write_never_translates_to_crlf_on_windows_or_other_platforms(tmp_path):
    """A regression guard for text-mode newline translation during writes."""
    path = write_bytes(tmp_path / "lf-only.yaml", to_lf("settings:\n  enabled: false\n"))

    writer = YamlWriter(path)
    writer.load()
    writer.set_value("settings.enabled", True)
    writer.write()

    output = read_bytes(path)
    assert b"\r" not in output
    assert b"\n" in output
