"""Golden-byte tests for scalar and collection styles in YAML writes."""

import yaml
import pytest

from tests.fidelity_utils import (
    assert_bytes_equal,
    assert_minimal_diff,
    noop_roundtrip,
    read_bytes,
    run_sql,
    to_crlf,
    to_lf,
    write_bytes,
)
from yamlql_library.writer import YamlWriter


@pytest.mark.parametrize(
    ("style_name", "source_value", "expected_value"),
    [
        ("double", '"Alice"', '"Bob"'),
        ("single", "'Alice'", "'Bob'"),
        ("plain", "Alice", "Bob"),
    ],
)
@pytest.mark.parametrize("operation", ["writer", "sql"])
def test_update_keeps_scalar_quote_style_and_changes_one_line(
    tmp_path, style_name, source_value, expected_value, operation
):
    """Writer and SQL UPDATE retain the existing quote convention."""
    source = to_lf("users:\n  - name: {}\n    role: admin\n".format(source_value))
    expected = to_lf("users:\n  - name: {}\n    role: admin\n".format(expected_value))
    path = write_bytes(tmp_path / (operation + "-" + style_name + ".yaml"), source)

    if operation == "writer":
        writer = YamlWriter(path)
        writer.load()
        writer.set_value("users.0.name", "Bob")
        writer.write()
    else:
        result = run_sql(path, "UPDATE users SET name = 'Bob' WHERE role = 'admin'")
        assert result["success"]

    output = read_bytes(path)
    assert_bytes_equal(output, expected)
    assert_minimal_diff(source, output, max_changed_lines=2)
    assert yaml.safe_load(output) == {"users": [{"name": "Bob", "role": "admin"}]}


@pytest.mark.parametrize("encode", [to_lf, to_crlf], ids=["lf", "crlf"])
@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        (
            "new first\nnew second",
            "literal: |\n"
            "  new first\n"
            "  new second\n"
            "strip: |-\n"
            "  new first\n"
            "  new second\n"
            "keep: |+\n"
            "  new first\n"
            "  new second\n"
            "\n"
            "folded: >-\n"
            "  new first\n"
            "\n"
            "  new second\n",
        ),
        (
            "single line",
            "literal: |\n"
            "  single line\n"
            "strip: |-\n"
            "  single line\n"
            "keep: |+\n"
            "  single line\n"
            "\n"
            "folded: >-\n"
            "  single line\n",
        ),
    ],
)
def test_block_scalar_updates_preserve_indicator_chomping_and_eol(tmp_path, encode, replacement, expected):
    """Literal/folded scalar updates retain their style, chomping, and EOL."""
    source = encode(
        "literal: |\n"
        "  old literal\n"
        "strip: |-\n"
        "  old strip\n"
        "keep: |+\n"
        "  old keep\n"
        "\n"
        "folded: >-\n"
        "  old folded\n"
    )
    path = write_bytes(tmp_path / "blocks.yaml", source)
    writer = YamlWriter(path)
    writer.load()
    for key in ("literal", "strip", "keep", "folded"):
        writer.set_value(key, replacement)
    writer.write()

    output = read_bytes(path)
    assert_bytes_equal(output, encode(expected))
    assert yaml.safe_load(output)["literal"].startswith("new first" if "new" in replacement else "single")


def test_scalar_type_changes_drop_string_style_and_plain_string_stays_plain(tmp_path):
    """Replacing quoted strings with non-strings removes only scalar styling."""
    source = to_lf("text: 'old'\nnumber: 1\nflag: true\nmissing: null\n")
    expected = to_lf("text: 42\nnumber: new\nflag: false\nmissing: null\n")
    path = write_bytes(tmp_path / "type-changes.yaml", source)

    writer = YamlWriter(path)
    writer.load()
    writer.set_value("text", 42)
    writer.set_value("number", "new")
    writer.set_value("flag", False)
    writer.set_value("missing", None)
    writer.write()

    output = read_bytes(path)
    assert_bytes_equal(output, expected)
    assert yaml.safe_load(output) == {"text": 42, "number": "new", "flag": False, "missing": None}


@pytest.mark.parametrize(
    ("string_value", "typed_value"),
    [("123", 123), ("true", True), ("null", None), ("1.5", 1.5)],
)
def test_type_looking_values_keep_intended_safe_load_types(tmp_path, string_value, typed_value):
    """Quoted strings remain strings while plain replacements retain native types."""
    source = to_lf('quoted: "old"\nplain: old\n')
    expected = to_lf('quoted: "{}"\nplain:{}\n'.format(string_value, _yaml_null_literal(typed_value)))
    path = write_bytes(tmp_path / (string_value + ".yaml"), source)

    writer = YamlWriter(path)
    writer.load()
    writer.set_value("quoted", string_value)
    writer.set_value("plain", typed_value)
    writer.write()

    output = read_bytes(path)
    assert_bytes_equal(output, expected)
    assert yaml.safe_load(output) == {"quoted": string_value, "plain": typed_value}


def test_row_insert_adopts_last_row_column_style_and_leaves_mixed_siblings_plain(tmp_path):
    """New rows inherit known column styles without styling non-string values."""
    source = to_lf(
        "users:\n"
        "  - name: \"Alice\"\n"
        "    title: 'engineer'\n"
        "    active: true\n"
        "    age: 30\n"
    )
    expected = to_lf(
        "users:\n"
        "  - name: \"Alice\"\n"
        "    title: 'engineer'\n"
        "    active: true\n"
        "    age: 30\n"
        "  - name: \"Bob\"\n"
        "    title: 'manager'\n"
        "    department: operations\n"
        "    active: false\n"
        "    age: 31\n"
    )
    path = write_bytes(tmp_path / "row-style.yaml", source)

    writer = YamlWriter(path)
    writer.load()
    writer.append_item(
        "users",
        {"name": "Bob", "title": "manager", "department": "operations", "active": False, "age": 31},
    )
    writer.write()

    output = read_bytes(path)
    assert_bytes_equal(output, expected)
    assert yaml.safe_load(output)["users"][-1] == {
        "name": "Bob", "title": "manager", "department": "operations", "active": False, "age": 31,
    }


def test_sql_row_insert_adopts_last_row_quote_style(tmp_path):
    """SQL INSERT reaches the same row-style adoption path as writer appends."""
    source = to_lf("users:\n  - name: 'Alice'\n    age: 30\n")
    expected = to_lf("users:\n  - name: 'Alice'\n    age: 30\n  - name: 'Bob'\n    age: 31\n")
    path = write_bytes(tmp_path / "sql-row-style.yaml", source)

    result = run_sql(path, "INSERT INTO users (name, age) VALUES ('Bob', 31)")

    assert result["success"]
    assert_bytes_equal(read_bytes(path), expected)


@pytest.mark.parametrize("encode", [to_lf, to_crlf], ids=["lf", "crlf"])
def test_flow_collections_stay_flow_and_new_nested_collection_adopts_row_style(tmp_path, encode):
    """Flow mappings/lists survive edits and appended values keep local flow style."""
    source = encode(
        "users: [{name: Alice, tags: [one]}]\n"
        "values: [a, b]\n"
        "block_users:\n"
        "  - name: Carol\n"
        "    tags:\n"
        "      - one\n"
    )
    expected = encode(
        "users: [{name: Bob, tags: [one]}, {name: Dana, tags: [two, three]}]\n"
        "values: [a, b, c]\n"
        "block_users:\n"
        "  - name: Carol\n"
        "    tags:\n"
        "      - one\n"
        "  - name: Erin\n"
        "    tags:\n"
        "      - two\n"
    )
    path = write_bytes(tmp_path / "flow.yaml", source)

    writer = YamlWriter(path)
    writer.load()
    writer.set_value("users.0.name", "Bob")
    writer.append_item("users", {"name": "Dana", "tags": ["two", "three"]})
    writer.append_item("values", "c")
    writer.append_item("block_users", {"name": "Erin", "tags": ["two"]})
    writer.write()

    output = read_bytes(path)
    assert_bytes_equal(output, expected)
    assert yaml.safe_load(output)["values"] == ["a", "b", "c"]


def test_insert_value_adopts_uniform_single_quote_style_or_plain_for_mixed_mapping(tmp_path):
    """New mapping values use uniform single quotes, but mixed siblings stay plain."""
    source = to_lf(
        "uniform:\n"
        "  first: 'one'\n"
        "  second: 'two'\n"
        "mixed:\n"
        "  first: 'one'\n"
        "  second: \"two\"\n"
    )
    expected = to_lf(
        "uniform:\n"
        "  first: 'one'\n"
        "  second: 'two'\n"
        "  third: 'three'\n"
        "mixed:\n"
        "  first: 'one'\n"
        "  second: \"two\"\n"
        "  third: three\n"
    )
    path = write_bytes(tmp_path / "mapping-style.yaml", source)

    writer = YamlWriter(path)
    writer.load()
    writer.insert_value("uniform.third", "three")
    writer.insert_value("mixed.third", "three")
    writer.write()

    assert_bytes_equal(read_bytes(path), expected)


def test_noop_roundtrip_is_byte_identical_for_every_covered_style(tmp_path):
    """A file combining quote, block, flow, and plain styles is byte-stable."""
    source = to_crlf(
        "quoted: \"Bob\"\n"
        "single: 'Alice'\n"
        "plain: Carol\n"
        "literal: |-\n"
        "  line one\n"
        "  line two\n"
        "folded: >-\n"
        "  a folded value\n"
        "flow: [{name: Dana, tags: [one, two]}]\n"
        "block:\n"
        "  - name: Erin\n"
        "    active: true\n"
    )
    path = write_bytes(tmp_path / "noop-all-styles.yaml", source)

    assert_bytes_equal(noop_roundtrip(path), source)


def _yaml_null_literal(value):
    if value is None:
        return ""
    if value is True:
        return " true"
    if value is False:
        return " false"
    return " " + str(value)
