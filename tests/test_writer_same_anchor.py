"""Golden-byte policy tests for YAML anchor-name redefinition."""

import pytest

from tests.fidelity_utils import assert_bytes_equal, read_bytes, run_sql, to_lf, write_bytes
from yamlql_library.writer import AnchorInUseError, YamlWriter, YamlWriterPolicyError


CORE_SOURCE = to_lf(
    "first: &same one\n"
    "second: *same\n"
    "third: &same two\n"
    "fourth: *same\n"
)


def _loaded_writer(path):
    writer = YamlWriter(path)
    writer.load()
    return writer


def test_update_second_same_name_definition_preserves_its_anchor_binding(tmp_path):
    """The second definition must reconnect only its nearest following alias."""
    path = write_bytes(tmp_path / "second-definition.yaml", CORE_SOURCE)
    writer = _loaded_writer(path)

    writer.set_value("third", "THREE")
    writer.write()

    assert_bytes_equal(
        read_bytes(path),
        to_lf(
            "first: &same one\n"
            "second: *same\n"
            "third: &same THREE\n"
            "fourth: *same\n"
        ),
    )


def test_update_first_same_name_definition_does_not_cross_bind_second_group(tmp_path):
    """The established definition-update policy applies per definition identity."""
    path = write_bytes(tmp_path / "first-definition.yaml", CORE_SOURCE)
    writer = _loaded_writer(path)

    writer.set_value("first", "ONE")
    writer.write()

    assert_bytes_equal(
        read_bytes(path),
        to_lf(
            "first: &same ONE\n"
            "second: *same\n"
            "third: &same two\n"
            "fourth: *same\n"
        ),
    )


@pytest.mark.parametrize(
    ("path", "replacement", "expected"),
    [
        (
            "second",
            "literal-second",
            to_lf(
                "first: &same one\n"
                "second: literal-second\n"
                "third: &same two\n"
                "fourth: *same\n"
            ),
        ),
        (
            "fourth",
            "literal-fourth",
            to_lf(
                "first: &same one\n"
                "second: *same\n"
                "third: &same two\n"
                "fourth: literal-fourth\n"
            ),
        ),
    ],
    ids=["first-binding-alias", "second-binding-alias"],
)
def test_update_same_name_alias_changes_only_its_targeted_binding(
    tmp_path, path, replacement, expected
):
    """Alias updates retain the existing literal-replacement policy per group."""
    fixture = write_bytes(tmp_path / "alias-update.yaml", CORE_SOURCE)
    writer = _loaded_writer(fixture)

    writer.set_value(path, replacement)
    writer.write()

    assert_bytes_equal(read_bytes(fixture), expected)


@pytest.mark.parametrize("path", ["first", "third"], ids=["first-definition", "second-definition"])
def test_delete_referenced_same_name_definition_is_rejected_without_file_change(tmp_path, path):
    fixture = write_bytes(tmp_path / "referenced-definition.yaml", CORE_SOURCE)
    writer = _loaded_writer(fixture)

    with pytest.raises(AnchorInUseError, match="same") as error:
        writer.delete_value(path)

    assert isinstance(error.value, YamlWriterPolicyError)
    assert_bytes_equal(read_bytes(fixture), CORE_SOURCE)


def test_delete_unreferenced_same_name_definition_leaves_other_group_intact(tmp_path):
    source = to_lf(
        "first: &same one\n"
        "second: *same\n"
        "third: &same two\n"
        "fourth: independent\n"
    )
    fixture = write_bytes(tmp_path / "unreferenced-definition.yaml", source)
    writer = _loaded_writer(fixture)

    writer.delete_value("third")
    writer.write()

    assert_bytes_equal(
        read_bytes(fixture),
        to_lf("first: &same one\nsecond: *same\nfourth: independent\n"),
    )


def test_same_anchor_name_is_scoped_to_its_document_for_definition_update(tmp_path):
    source = to_lf("---\na: &x 1\nb: *x\n---\nc: &x 2\nd: *x\n")
    fixture = write_bytes(tmp_path / "document-scoped.yaml", source)
    writer = _loaded_writer(fixture)

    writer.set_value("c", 20, doc=1)
    writer.write()

    assert_bytes_equal(
        read_bytes(fixture),
        to_lf("---\na: &x 1\nb: *x\n---\nc: &x 20\nd: *x\n"),
    )


def test_sql_update_redefined_anchor_row_matches_writer_definition_policy(tmp_path):
    source = to_lf(
        "users:\n"
        "  - id: 1\n"
        "    name: &same one\n"
        "  - id: 2\n"
        "    name: *same\n"
        "  - id: 3\n"
        "    name: &same two\n"
        "  - id: 4\n"
        "    name: *same\n"
    )
    fixture = write_bytes(tmp_path / "sql-redefined-anchor.yaml", source)

    result = run_sql(fixture, "UPDATE users SET name = 'z' WHERE id = 3")

    assert result["success"]
    assert_bytes_equal(
        read_bytes(fixture),
        to_lf(
            "users:\n"
            "  - id: 1\n"
            "    name: &same one\n"
            "  - id: 2\n"
            "    name: *same\n"
            "  - id: 3\n"
            "    name: &same z\n"
            "  - id: 4\n"
            "    name: *same\n"
        ),
    )


def test_unresolved_anchor_identity_is_rejected_without_file_change(tmp_path, monkeypatch):
    """A missing source identity is rejected before the writer mutates data."""
    fixture = write_bytes(tmp_path / "unresolved-anchor.yaml", CORE_SOURCE)
    writer = _loaded_writer(fixture)

    monkeypatch.setattr(
        writer,
        "_anchor_event_bindings",
        lambda anchor_name, document_index: None,
    )

    with pytest.raises(
        YamlWriterPolicyError,
        match=r"anchor 'same'.*document 0",
    ):
        writer.set_value("first", "ONE")

    assert_bytes_equal(read_bytes(fixture), CORE_SOURCE)
