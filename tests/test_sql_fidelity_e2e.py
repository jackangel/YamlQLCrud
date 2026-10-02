"""End-to-end byte-golden coverage for SQL edits to a realistic YAML stream."""

import pytest

from tests.fidelity_utils import (
    assert_bytes_equal,
    assert_minimal_diff,
    read_bytes,
    to_crlf,
    to_lf,
    write_bytes,
)
from yamlql_library import YamlQL


ENCODERS = (to_lf, to_crlf)


FIXTURE = (
    "# deployment configuration\n"
    "# these defaults are intentionally shared by several services\n"
    "defaults: &service_defaults\n"
    '  image: "nginx:1.25" # default image\n'
    "  restart: always\n"
    "unused: &unused\n"
    "  region: east\n"
    "services:\n"
    "  # web owns this comment\n"
    '  - name: "web" # public service\n'
    "    <<: *service_defaults\n"
    "    ports: [80, 443]\n"
    "    script: |-\n"
    '      echo "start"\n'
    "      echo web\n"
    "  # api owns this comment\n"
    "  - name: api\n"
    "    <<: *service_defaults\n"
    "    ports: [8080]\n"
    "    script: |-\n"
    "      echo api\n"
    "reference: !Ref SharedResource\n"
    "---\n"
    "# audit document\n"
    "audit:\n"
    "  - event: deployed\n"
    "    actor: system\n"
)


INSERTED_WORKER = (
    "  - name: worker\n"
    '    image: "nginx:1.25"\n'
    "    restart: always\n"
    "    ports: '[9000]'\n"
    "    script: echo worker\n"
)


INSERTED_MULTI_ROWS = (
    "  - name: worker-a\n"
    '    image: "nginx:1.25"\n'
    "    restart: always\n"
    "  - name: worker-b\n"
    '    image: "nginx:1.25"\n'
    "    restart: always\n"
)


def _fixture_path(tmp_path, encode):
    return write_bytes(tmp_path / "complex.yaml", encode(FIXTURE))


def _query(path, sql, mode="rw"):
    yamlql = YamlQL(str(path), mode=mode)
    try:
        return yamlql.query(sql)
    finally:
        yamlql.close()


def _select_records(path, sql):
    """Run SELECT through a fresh facade instance to prove on-disk persistence."""
    result = _query(path, sql, mode="r")
    return result.to_dict("records")


def _expected_inserted_worker():
    return FIXTURE.replace("reference: !Ref SharedResource\n", INSERTED_WORKER + "reference: !Ref SharedResource\n")


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_select_only_is_byte_identical_for_complex_stream(tmp_path, encode):
    path = _fixture_path(tmp_path, encode)
    original = read_bytes(path)

    assert _select_records(path, "SELECT name, image FROM services ORDER BY name") == [
        {"name": "api", "image": "nginx:1.25"},
        {"name": "web", "image": "nginx:1.25"},
    ]

    assert_bytes_equal(read_bytes(path), original)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_sql_update_scalar_preserves_quote_and_inline_comment(tmp_path, encode):
    path = _fixture_path(tmp_path, encode)
    expected = encode(FIXTURE.replace('  - name: "web" # public service\n', '  - name: "frontend" # public service\n'))

    result = _query(path, "UPDATE services SET name = 'frontend' WHERE name = 'web'")

    assert result["success"]
    assert_bytes_equal(read_bytes(path), expected)
    assert _select_records(path, "SELECT name FROM services ORDER BY name") == [
        {"name": "api"},
        {"name": "frontend"},
    ]


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_sql_update_block_scalar_keeps_literal_style_and_chomping(tmp_path, encode):
    path = _fixture_path(tmp_path, encode)
    expected = encode(
        FIXTURE.replace(
            '    script: |-\n      echo "start"\n      echo web\n',
            "    script: |-\n      echo frontend\n",
            1,
        )
    )

    result = _query(path, "UPDATE services SET script = 'echo frontend' WHERE name = 'web'")

    assert result["success"]
    assert_bytes_equal(read_bytes(path), expected)
    assert _select_records(path, "SELECT script FROM services WHERE name = 'web'") == [
        {"script": "echo frontend"}
    ]


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_sql_insert_into_commented_list_has_golden_bytes(tmp_path, encode):
    path = _fixture_path(tmp_path, encode)
    expected = encode(_expected_inserted_worker())

    result = _query(
        path,
        "INSERT INTO services (name, image, restart, ports, script) "
        "VALUES ('worker', 'nginx:1.25', 'always', '[9000]', 'echo worker')",
    )

    assert result["success"]
    assert_bytes_equal(read_bytes(path), expected)
    assert _select_records(path, "SELECT name, image FROM services WHERE name = 'worker'") == [
        {"name": "worker", "image": "nginx:1.25"}
    ]


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_sql_delete_middle_commented_row_removes_its_owned_comment_only(tmp_path, encode):
    path = _fixture_path(tmp_path, encode)
    removed_item = (
        "  # api owns this comment\n"
        "  - name: api\n"
        "    <<: *service_defaults\n"
        "    ports: [8080]\n"
        "    script: |-\n"
        "      echo api\n"
    )
    expected = encode(FIXTURE.replace(removed_item, ""))

    result = _query(path, "DELETE FROM services WHERE name = 'api'")

    assert result["success"]
    assert_bytes_equal(read_bytes(path), expected)
    assert _select_records(path, "SELECT name FROM services") == [{"name": "web"}]


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_rejected_sql_statements_leave_complex_file_byte_identical(tmp_path, encode):
    path = _fixture_path(tmp_path, encode)
    original = read_bytes(path)

    with pytest.raises(Exception, match="UPDATE failed:.*ports.*sequence.*scalar"):
        _query(path, "UPDATE services SET ports = 'replacement' WHERE name = 'web'")
    assert_bytes_equal(read_bytes(path), original)

    with pytest.raises(Exception, match="DELETE failed"):
        _query(path, "DELETE FROM defaults WHERE restart = 'always'")
    assert_bytes_equal(read_bytes(path), original)


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_sql_sequence_on_one_instance_has_only_the_three_intended_changes(tmp_path, encode):
    path = _fixture_path(tmp_path, encode)
    original = read_bytes(path)
    yamlql = YamlQL(str(path), mode="rw")
    try:
        inserted = yamlql.query(
            "INSERT INTO services (name, image, restart, ports, script) "
            "VALUES ('worker', 'nginx:1.25', 'always', '[9000]', 'echo worker')"
        )
        assert inserted["success"]
        assert yamlql.query("SELECT name FROM services WHERE name = 'worker'").to_dict("records") == [
            {"name": "worker"}
        ]

        updated = yamlql.query("UPDATE services SET name = 'frontend' WHERE name = 'web'")
        assert updated["success"]
        assert yamlql.query("SELECT name FROM services WHERE name = 'frontend'").to_dict("records") == [
            {"name": "frontend"}
        ]

        deleted = yamlql.query("DELETE FROM services WHERE name = 'api'")
        assert deleted["success"]
        assert yamlql.query("SELECT name FROM services ORDER BY name").to_dict("records") == [
            {"name": "frontend"},
            {"name": "worker"},
        ]
    finally:
        yamlql.close()

    removed_api = (
        "  # api owns this comment\n"
        "  - name: api\n"
        "    <<: *service_defaults\n"
        "    ports: [8080]\n"
        "    script: |-\n"
        "      echo api\n"
    )
    expected_text = _expected_inserted_worker().replace(
        '  - name: "web" # public service\n', '  - name: "frontend" # public service\n'
    ).replace(removed_api, "")
    expected = encode(expected_text)
    assert_bytes_equal(read_bytes(path), expected)
    assert_minimal_diff(original, read_bytes(path), max_changed_lines=14)
    assert _select_records(path, "SELECT name FROM services ORDER BY name") == [
        {"name": "frontend"},
        {"name": "worker"},
    ]


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_sql_multirow_insert_then_multirow_delete_on_one_instance(tmp_path, encode):
    path = _fixture_path(tmp_path, encode)
    yamlql = YamlQL(str(path), mode="rw")
    try:
        inserted = yamlql.query(
            "INSERT INTO services (name, image, restart) VALUES "
            "('worker-a', 'nginx:1.25', 'always'), "
            "('worker-b', 'nginx:1.25', 'always')"
        )
        assert inserted["success"]
        assert_bytes_equal(
            read_bytes(path),
            encode(FIXTURE.replace("reference: !Ref SharedResource\n", INSERTED_MULTI_ROWS + "reference: !Ref SharedResource\n")),
        )
        assert yamlql.query("SELECT name FROM services WHERE name LIKE 'worker-%' ORDER BY name").to_dict("records") == [
            {"name": "worker-a"},
            {"name": "worker-b"},
        ]

        deleted = yamlql.query("DELETE FROM services WHERE name IN ('worker-a', 'worker-b')")
        assert deleted["success"]
        assert_bytes_equal(read_bytes(path), encode(FIXTURE))
    finally:
        yamlql.close()

    assert _select_records(path, "SELECT name FROM services ORDER BY name") == [
        {"name": "api"},
        {"name": "web"},
    ]


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_sql_edits_are_isolated_between_documents(tmp_path, encode):
    path = _fixture_path(tmp_path, encode)
    second_document = encode("---\n# audit document\naudit:\n  - event: deployed\n    actor: system\n")
    expected_after_services = encode(
        FIXTURE.replace('  - name: "web" # public service\n', '  - name: "frontend" # public service\n')
    )

    assert _query(path, "UPDATE services SET name = 'frontend' WHERE name = 'web'")["success"]
    assert_bytes_equal(read_bytes(path), expected_after_services)
    assert read_bytes(path).endswith(second_document)

    expected_after_audit = encode(
        FIXTURE.replace('  - name: "web" # public service\n', '  - name: "frontend" # public service\n').replace(
            "  - event: deployed\n", "  - event: verified\n"
        )
    )
    assert _query(path, "UPDATE audit SET event = 'verified' WHERE actor = 'system'")["success"]
    assert_bytes_equal(read_bytes(path), expected_after_audit)
    assert read_bytes(path).startswith(expected_after_services[: -len(second_document)])
    assert _select_records(path, "SELECT event FROM audit") == [{"event": "verified"}]


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_custom_tag_opens_and_is_untouched_by_untagged_sql_update(tmp_path, encode):
    path = _fixture_path(tmp_path, encode)
    expected = encode(FIXTURE.replace('  - name: "web" # public service\n', '  - name: "frontend" # public service\n'))

    assert _select_records(path, "SELECT reference FROM root") == [{"reference": "SharedResource"}]
    assert _query(path, "UPDATE services SET name = 'frontend' WHERE name = 'web'")["success"]
    assert_bytes_equal(read_bytes(path), expected)
    assert b"reference: !Ref SharedResource" in read_bytes(path)
    assert _select_records(path, "SELECT reference FROM root") == [{"reference": "SharedResource"}]


@pytest.mark.parametrize("encode", ENCODERS, ids=["lf", "crlf"])
def test_anchored_default_update_propagates_to_merged_aliases_and_delete_is_rejected(tmp_path, encode):
    path = _fixture_path(tmp_path, encode)
    expected = encode(FIXTURE.replace('  image: "nginx:1.25" # default image\n', '  image: "nginx:1.26" # default image\n'))

    assert _query(path, "UPDATE defaults SET image = 'nginx:1.26' WHERE restart = 'always'")["success"]
    assert_bytes_equal(read_bytes(path), expected)
    assert _select_records(path, "SELECT name, image FROM services ORDER BY name") == [
        {"name": "api", "image": "nginx:1.26"},
        {"name": "web", "image": "nginx:1.26"},
    ]

    before_delete = read_bytes(path)
    with pytest.raises(Exception, match="DELETE failed"):
        _query(path, "DELETE FROM defaults WHERE restart = 'always'")
    assert_bytes_equal(read_bytes(path), before_delete)
