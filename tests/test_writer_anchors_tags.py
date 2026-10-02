"""Golden-byte tests for writer anchor, merge-key, and tag policy."""

from copy import deepcopy
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
from yamlql_library.transaction import TransactionManager
from yamlql_library.writer import (
    AnchorInUseError,
    MergedKeyError,
    YamlWriter,
    YamlWriterPolicyError,
)


def _write_with_writer(path, operation):
    writer = YamlWriter(path)
    writer.load()
    operation(writer)
    writer.write()
    return writer, read_bytes(path)


def test_noop_roundtrip_preserves_anchor_alias_merge_tag_and_crlf_bytes(tmp_path):
    source = (
        "used: &used value\n"
        "alias: *used\n"
        "unused: &unused retained\n"
        "base: &base\n"
        "  host: localhost\n"
        "service:\n"
        "  <<: *base\n"
        "  name: api\n"
        "items: &items\n"
        "  - one\n"
        "  - two\n"
        "custom: !Custom value\n"
        "tagged: !Tagged\n"
        "  child: unchanged\n"
    )

    lf_path = write_bytes(tmp_path / "anchors-tags.yaml", to_lf(source))
    crlf_path = write_bytes(tmp_path / "anchors-tags-crlf.yaml", to_crlf(source))

    assert_bytes_equal(noop_roundtrip(lf_path), to_lf(source))
    assert_bytes_equal(noop_roundtrip(crlf_path), to_crlf(source))


def test_noop_roundtrip_preserves_standard_set_and_omap_when_supported(tmp_path):
    source = (
        "values: !!set\n"
        "  alpha: null\n"
        "  beta: null\n"
        "ordered: !!omap\n"
        "  - first: one\n"
        "  - second: two\n"
    )
    path = write_bytes(tmp_path / "standard-tags.yaml", to_lf(source))

    try:
        actual = noop_roundtrip(path)
    except Exception as error:
        pytest.skip(
            "Installed ruamel.yaml cannot round-trip !!set/!!omap fixtures: {}"
            .format(error)
        )

    assert_bytes_equal(actual, to_lf(source))


def test_update_anchored_scalar_preserves_anchor_aliases_and_inline_comment(tmp_path):
    path = write_bytes(
        tmp_path / "anchored-scalar.yaml",
        to_lf("base: &base original  # retain comment\nfirst: *base\nsecond: *base\n"),
    )

    writer, actual = _write_with_writer(
        path, lambda candidate: candidate.set_value("base", "updated")
    )

    assert writer.get_value("base") == "updated"
    assert writer.get_value("first") == "updated"
    assert writer.get_value("second") == "updated"
    assert_bytes_equal(
        actual,
        to_lf("base: &base updated  # retain comment\nfirst: *base\nsecond: *base\n"),
    )


def test_update_alias_replaces_only_that_occurrence_with_a_literal(tmp_path):
    path = write_bytes(
        tmp_path / "alias-update.yaml",
        to_lf("base: &base original\nfirst: *base\nsecond: *base\n"),
    )

    writer, actual = _write_with_writer(
        path, lambda candidate: candidate.set_value("first", "literal")
    )

    assert writer.get_value("base") == "original"
    assert writer.get_value("first") == "literal"
    assert writer.get_value("second") == "original"
    assert_bytes_equal(
        actual,
        to_lf("base: &base original\nfirst: literal\nsecond: *base\n"),
    )


def test_delete_referenced_anchor_raises_and_transaction_preserves_file(tmp_path):
    source = to_lf("base: &base original\nconsumer: *base\n")
    path = write_bytes(tmp_path / "referenced-anchor.yaml", source)
    transaction = TransactionManager(path)

    transaction.begin()
    writer = transaction.get_writer()
    writer.load()
    data_before = deepcopy(writer.data)
    with pytest.raises(AnchorInUseError) as error:
        writer.delete_value("base")

    assert isinstance(error.value, YamlWriterPolicyError)
    assert isinstance(error.value, ValueError)
    assert writer.data == data_before
    assert writer.get_value("base") == "original"
    assert writer.get_value("consumer") == "original"
    assert_bytes_equal(read_bytes(path), source)

    transaction.rollback()
    assert_bytes_equal(read_bytes(path), source)


def test_delete_alias_is_allowed_and_leaves_anchor_intact(tmp_path):
    path = write_bytes(
        tmp_path / "delete-alias.yaml",
        to_lf("base: &base original\nconsumer: *base\n"),
    )

    writer, actual = _write_with_writer(path, lambda candidate: candidate.delete_value("consumer"))

    assert writer.get_value("base") == "original"
    assert_bytes_equal(actual, to_lf("base: &base original\n"))


def test_delete_anchor_after_all_aliases_removed_is_allowed(tmp_path):
    path = write_bytes(
        tmp_path / "delete-unreferenced-anchor.yaml",
        to_lf("base: &base original\nconsumer: *base\n"),
    )

    writer, actual = _write_with_writer(
        path,
        lambda candidate: (
            candidate.delete_value("consumer"),
            candidate.delete_value("base"),
        ),
    )

    assert writer.data == {}
    assert_bytes_equal(actual, to_lf("{}\n"))


def test_merge_key_set_adds_own_override_and_keeps_source_unchanged(tmp_path):
    path = write_bytes(
        tmp_path / "merge-set.yaml",
        to_lf(
            "base: &base\n"
            "  host: localhost\n"
            "service:\n"
            "  <<: *base\n"
            "  name: api\n"
        ),
    )

    writer, actual = _write_with_writer(
        path, lambda candidate: candidate.set_value("service.host", "override")
    )

    assert writer.get_value("base.host") == "localhost"
    assert writer.get_value("service.host") == "override"
    assert_bytes_equal(
        actual,
        to_lf(
            "base: &base\n"
            "  host: localhost\n"
            "service:\n"
            "  <<: *base\n"
            "  name: api\n"
            "  host: override\n"
        ),
    )


def test_delete_merge_only_key_raises_without_changing_document_or_file(tmp_path):
    source = to_lf(
        "base: &base\n"
        "  host: localhost\n"
        "service:\n"
        "  <<: *base\n"
        "  name: api\n"
    )
    path = write_bytes(tmp_path / "merge-delete.yaml", source)
    writer = YamlWriter(path)
    writer.load()
    data_before = deepcopy(writer.data)

    with pytest.raises(MergedKeyError) as error:
        writer.delete_value("service.host")

    assert isinstance(error.value, YamlWriterPolicyError)
    assert isinstance(error.value, ValueError)
    assert writer.data == data_before
    assert writer.get_value("service.host") == "localhost"
    assert_bytes_equal(read_bytes(path), source)


def test_delete_owned_key_from_mapping_with_merge_is_allowed(tmp_path):
    path = write_bytes(
        tmp_path / "merge-own-delete.yaml",
        to_lf(
            "base: &base\n"
            "  host: localhost\n"
            "service:\n"
            "  <<: *base\n"
            "  name: api\n"
        ),
    )

    writer, actual = _write_with_writer(path, lambda candidate: candidate.delete_value("service.name"))

    assert writer.get_value("service.host") == "localhost"
    assert_bytes_equal(
        actual,
        to_lf("base: &base\n  host: localhost\nservice:\n  <<: *base\n"),
    )


def test_tagged_scalar_update_preserves_tag(tmp_path):
    path = write_bytes(tmp_path / "tagged-scalar.yaml", to_lf("value: !Custom original\n"))

    writer, actual = _write_with_writer(
        path, lambda candidate: candidate.set_value("value", "updated")
    )

    assert str(writer.get_value("value")) == "updated"
    assert_bytes_equal(actual, to_lf("value: !Custom updated\n"))


def test_tagged_mapping_child_update_preserves_mapping_tag(tmp_path):
    path = write_bytes(
        tmp_path / "tagged-mapping.yaml",
        to_lf("value: !Custom\n  child: original\n  untouched: preserved\n"),
    )

    writer, actual = _write_with_writer(
        path, lambda candidate: candidate.set_value("value.child", "updated")
    )

    assert writer.get_value("value.child") == "updated"
    assert_bytes_equal(
        actual,
        to_lf("value: !Custom\n  child: updated\n  untouched: preserved\n"),
    )


def test_edge_cases_fixture_noop_roundtrip_is_byte_identical(tmp_path):
    fixture_path = Path(__file__).parent / "test_data" / "edge_cases.yml"
    expected = fixture_path.read_bytes()
    path = write_bytes(tmp_path / "edge_cases.yml", expected)

    assert_bytes_equal(noop_roundtrip(path), expected)
