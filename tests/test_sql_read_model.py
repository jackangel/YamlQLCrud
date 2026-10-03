"""Characterize the legacy flattened read model and specify mapping columns."""

import math

import pytest

from tests.fidelity_utils import assert_bytes_equal, read_bytes, to_crlf, to_lf, write_bytes
from yamlql_library import YamlQL
from yamlql_library.loader import YamlLoader
from yamlql_library.transformer import DataTransformer


STRATEGIES = ("depth", "adaptive")


READ_SOURCE = (
    "users:\n"
    "  - id: 1\n"
    "    name: first\n"
    "    details:\n"
    "      region: us\n"
    "      enabled: true\n"
    "  - id: 2\n"
    "    name: second\n"
    "    details:\n"
    "      region: eu\n"
    "      enabled: false\n"
)


G1_SOURCE = (
    "users:\n"
    "  - id: 1\n"
    "    name: mapping\n"
    "    details:\n"
    "      region: us\n"
    "      enabled: true\n"
    "  - id: 2\n"
    "    name: scalar\n"
    "    details: legacy-scalar\n"
    "  - id: 3\n"
    "    name: null\n"
    "    details: null\n"
)


def _path(tmp_path, name, source, encoder=to_lf):
    return write_bytes(tmp_path / name, encoder(source))


def _query(path, strategy, sql, mode="r", expose_mapping_columns=False):
    yamlql = YamlQL(
        str(path),
        strategy=strategy,
        mode=mode,
        expose_mapping_columns=expose_mapping_columns,
    )
    try:
        return yamlql.query(sql)
    finally:
        yamlql.close()


def _schema(query_result):
    return list(zip(query_result["column_name"], query_result["column_type"]))


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_mapping_rows_pin_existing_flattened_prefix_and_explicit_queries(tmp_path, strategy):
    """Q1 may append columns, but it must not alter this legacy prefix."""
    path = _path(tmp_path, "read-prefix.yaml", READ_SOURCE)

    yamlql = YamlQL(str(path), strategy=strategy)
    try:
        schema = _schema(yamlql.query("DESCRIBE users"))
        expected_prefix = (
            [("id", "BIGINT"), ("name", "VARCHAR")]
            + (
                [("_yaml_path", "VARCHAR")]
                if strategy == "depth"
                else []
            )
            + [("details_region", "VARCHAR"), ("details_enabled", "BOOLEAN")]
            + (
                []
                if strategy == "depth"
                else [("_yaml_path", "VARCHAR")]
            )
        )
        assert schema[: len(expected_prefix)] == expected_prefix

        records = yamlql.query("SELECT * FROM users ORDER BY id").to_dict("records")
        assert records == [
            {
                "id": 1,
                "name": "first",
                **(
                    {"_yaml_path": "root.users.0"}
                    if strategy == "depth"
                    else {}
                ),
                "details_region": "us",
                "details_enabled": True,
                **(
                    {}
                    if strategy == "depth"
                    else {"_yaml_path": "root.users.0"}
                ),
            },
            {
                "id": 2,
                "name": "second",
                **(
                    {"_yaml_path": "root.users.1"}
                    if strategy == "depth"
                    else {}
                ),
                "details_region": "eu",
                "details_enabled": False,
                **(
                    {}
                    if strategy == "depth"
                    else {"_yaml_path": "root.users.1"}
                ),
            },
        ]
        assert yamlql.query(
            "SELECT id, details_region FROM users ORDER BY id"
        ).to_dict("records") == [
            {"id": 1, "details_region": "us"},
            {"id": 2, "details_region": "eu"},
        ]
    finally:
        yamlql.close()


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_g1_positional_and_named_insert_keep_today_schema_shape(tmp_path, strategy):
    """G1 tripwire: today this heterogeneous table has six SQL columns.

    Task 5-2 must preserve positional INSERT compatibility even when it adds
    mapping columns for mapping-valued rows.
    """
    path = _path(tmp_path, "g1.yaml", G1_SOURCE)
    yamlql = YamlQL(str(path), strategy=strategy, mode="rw")
    try:
        schema = _schema(yamlql.query("DESCRIBE users"))
        assert len(schema) == 6  # Recorded 2026-10-02 for both depth/adaptive.
        assert {name for name, _ in schema} == {
            "id", "name", "details", "details_region", "details_enabled", "_yaml_path"
        }

        # `_yaml_path` is synthetic metadata, so today's positional DML shape
        # contains the five writable columns even though DESCRIBE has six.
        positional = "INSERT INTO users VALUES (4, 'positional', 'ap', false, 'raw')"
        assert yamlql.query(positional)["success"]
        assert yamlql.query(
            "INSERT INTO users (id, name, details) VALUES (5, 'named', 'named-raw')"
        )["success"]
    finally:
        yamlql.close()

    reloaded = YamlQL(str(path), strategy=strategy)
    try:
        rows = reloaded.query("SELECT id, name, details FROM users WHERE id >= 4 ORDER BY id")
        assert rows.to_dict("records") == [
            {"id": 4, "name": "positional", "details": "raw"},
            {"id": 5, "name": "named", "details": "named-raw"},
        ]
    finally:
        reloaded.close()


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_mapping_column_is_appended_json_read_view_with_native_value_rules(tmp_path, strategy):
    path = _path(
        tmp_path,
        "mapping-read.yaml",
        "users:\n"
        "  - id: 1\n"
        "    details:\n"
        "      region: us\n"
        "      enabled: true\n"
        "      born: 2026-10-02\n"
        "      flags: !!set {beta: null, alpha: null}\n"
        "      bytes: !!binary aGk=\n"
        "      custom: !Example plain\n"
        "  - id: 2\n"
        "    details: null\n"
        "  - id: 3\n"
        "    name: details-absent\n",
    )
    yamlql = YamlQL(str(path), strategy=strategy, expose_mapping_columns=True)
    try:
        schema = _schema(yamlql.query("DESCRIBE users"))
        legacy_prefix = [item for item in schema if item[0] != "details"]
        assert schema[: len(legacy_prefix)] == legacy_prefix
        assert schema[-1] == ("details", "VARCHAR")
        assert yamlql.query("SELECT id, details FROM users ORDER BY id").to_dict("records") == [
            {
                "id": 1,
                "details": (
                    '{"born":"2026-10-02","bytes":"aGk=","custom":"plain",'
                    '"enabled":true,"flags":["alpha","beta"],"region":"us"}'
                ),
            },
            {"id": 2, "details": None},
            {"id": 3, "details": None},
        ]
    finally:
        yamlql.close()


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_heterogeneous_mapping_scalar_collision_keeps_legacy_details_column(tmp_path, strategy):
    """A scalar `details` column wins over the additive mapping read view."""
    path = _path(
        tmp_path,
        "mapping-scalar-collision.yaml",
        "users:\n"
        "  - id: 1\n"
        "    details:\n"
        "      region: us\n"
        "  - id: 2\n"
        "    details: legacy-scalar\n",
    )
    yamlql = YamlQL(str(path), strategy=strategy)
    try:
        schema = _schema(yamlql.query("DESCRIBE users"))
        assert schema.count(("details", "VARCHAR")) == 1
        records = yamlql.query("SELECT id, details FROM users ORDER BY id").to_dict("records")
        assert records[0]["id"] == 1
        assert math.isnan(records[0]["details"])
        assert records[1] == {"id": 2, "details": "legacy-scalar"}
    finally:
        yamlql.close()


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_heterogeneous_mapping_scalar_collision_rejects_mapping_row_update(tmp_path, strategy):
    source = to_lf(
        "users:\n"
        "  - id: 1\n"
        "    details:\n"
        "      region: us\n"
        "  - id: 2\n"
        "    details: legacy-scalar\n"
    )
    path = write_bytes(tmp_path / "mapping-scalar-update.yaml", source)
    yamlql = YamlQL(str(path), strategy=strategy, mode="rw")
    try:
        with pytest.raises(Exception, match="UPDATE failed:.*details.*mapping.*scalar"):
            yamlql.query(
                "UPDATE users SET details = '{\"region\": \"eu\"}' WHERE id = 1"
            )
    finally:
        yamlql.close()
    assert_bytes_equal(read_bytes(path), source)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_mapping_column_metadata_uses_original_paths_and_reports_ambiguity(tmp_path, strategy):
    path = _path(
        tmp_path,
        "mapping-metadata.yaml",
        "users:\n"
        "  - id: 1\n"
        "    details:\n"
        "      geo-data: {lat: 1}\n"
        "      geo_data: {lat: 2}\n",
    )
    # The loader requires a Path, not a string. This keeps the observation
    # independent of facade lifecycle details.
    transformer = DataTransformer(
        YamlLoader(path).load(), strategy=strategy, expose_mapping_columns=True
    )
    tables = dict(transformer.transform())
    assert "details" in tables["users"].columns
    assert transformer.mapping_column_paths["users"]["details"] == ("details",)
    assert transformer.mapping_column_paths["users"]["details_geo_data"] == (
        "details",
        "geo-data",
    )
    assert "details_geo_data" in transformer.mapping_column_warnings[-1]
    assert "ambiguous" in transformer.mapping_column_warnings[-1].lower()


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_mapping_column_skips_flattened_name_collision_and_supports_nested_paths(tmp_path, strategy):
    path = _path(
        tmp_path,
        "mapping-collision.yaml",
        "users:\n"
        "  - id: 1\n"
        "    details: literal-wins\n"
        "    payload:\n"
        "      geo:\n"
        "        lat: 1\n",
    )
    yamlql = YamlQL(str(path), strategy=strategy, expose_mapping_columns=True)
    try:
        schema = _schema(yamlql.query("DESCRIBE users"))
        assert schema.count(("details", "VARCHAR")) == 1
        # Nested mappings are direct columns too only when their sanitized name
        # does not collide with the preserved flattened prefix.
        assert ("payload_geo", "VARCHAR") in schema
        assert ("payload_geo_lat", "BIGINT") in schema
    finally:
        yamlql.close()


@pytest.mark.parametrize("encoder", (to_lf, to_crlf), ids=("lf", "crlf"))
@pytest.mark.parametrize(
    "source, expected",
    [
        (
            "users:\n  - id: 1\n    details:\n      region: us\n      child: old\nother: untouched\n",
            "users:\n  - id: 1\n    details:\n      region: eu\nother: untouched\n",
        ),
        # The mapping anchor and key comment survive; old children do not.
        (
            "users:\n  - id: 1\n    details: &detail # key comment\n      region: us\nother: untouched\n",
            "users:\n  - id: 1\n    details: &detail # key comment\n      region: eu\nother: untouched\n",
        ),
        # Child comments are deliberately not retained after full replacement.
        (
            "users:\n  - id: 1\n    details:\n      # child comment\n      region: us\nother: untouched\n",
            "users:\n  - id: 1\n    details:\n      region: eu\nother: untouched\n",
        ),
        # A merge key is a policy error; source bytes must remain unchanged.
        (
            "base: &base {region: us}\nusers:\n  - id: 1\n    details:\n      <<: *base\n",
            "base: &base {region: us}\nusers:\n  - id: 1\n    details:\n      <<: *base\n",
        ),
        (
            "users:\n  - id: 1\n    details: !Keep # key comment\n      region: us\n      child: old\nother: untouched\n",
            "users:\n  - id: 1\n    details: !Keep # key comment\n      region: eu\nother: untouched\n",
        ),
        # Replacement does not retain child quotes or block-scalar style.
        (
            "users:\n  - id: 1\n    details:\n      region: 'us'\n      note: |-\n        old\nother: untouched\n",
            "users:\n  - id: 1\n    details:\n      region: eu\nother: untouched\n",
        ),
        # An aliased descendant cannot be discarded by a whole-map replace.
        (
            "users:\n  - id: 1\n    details:\n      region: &region us\nother: *region\n",
            "users:\n  - id: 1\n    details:\n      region: &region us\nother: *region\n",
        ),
    ],
    ids=("plain", "anchored", "child_comment", "merge", "tagged", "quoted_block", "aliased_child"),
)
def test_mapping_json_update_has_hand_authored_goldens(tmp_path, encoder, source, expected):
    path = _path(tmp_path, "mapping-update.yaml", source, encoder)
    if "<<:" in source:
        with pytest.raises(Exception, match="UPDATE failed:.*details.*merge"):
            _query(
                path,
                "depth",
                "UPDATE users SET details = '{\"region\": \"eu\"}' WHERE id = 1",
                mode="rw",
                expose_mapping_columns=True,
            )
    elif "other: *region" in source:
        with pytest.raises(Exception, match="UPDATE failed:.*details.*anchor"):
            _query(
                path,
                "depth",
                "UPDATE users SET details = '{\"region\": \"eu\"}' WHERE id = 1",
                mode="rw",
                expose_mapping_columns=True,
            )
    else:
        result = _query(
            path,
            "depth",
            "UPDATE users SET details = '{\"region\": \"eu\"}' WHERE id = 1",
            mode="rw",
            expose_mapping_columns=True,
        )
        assert result["success"]
        assert _query(path, "depth", "SELECT details_region FROM users WHERE id = 1").iloc[0, 0] == "eu"
    assert_bytes_equal(read_bytes(path), encoder(expected))


@pytest.mark.parametrize(
    "source, statement, expected_message",
    [
        (
            "users:\n  - id: 1\n    details: {region: us}\n",
            "UPDATE users SET details = '[1,2]' WHERE id = 1",
            "UPDATE failed:.*details.*mapping.*sequence",
        ),
        (
            "users:\n  - id: 1\n    details: {region: us}\n",
            "UPDATE users SET details = '3' WHERE id = 1",
            "UPDATE failed:.*details.*mapping.*scalar",
        ),
        (
            "users:\n  - id: 1\n    details: {region: us}\n",
            "UPDATE users SET details = 'true' WHERE id = 1",
            "UPDATE failed:.*details.*mapping.*scalar",
        ),
        (
            "users:\n  - id: 1\n    details: {region: us}\n",
            "UPDATE users SET details = '\"x\"' WHERE id = 1",
            "UPDATE failed:.*details.*mapping.*scalar",
        ),
        (
            "users:\n  - id: 1\n    details: {region: us}\n",
            "UPDATE users SET details = '{bad' WHERE id = 1",
            "UPDATE failed:.*details.*mapping.*scalar",
        ),
        (
            "users:\n  - id: 1\n    details: {region: us}\n",
            "UPDATE users SET details = 'null' WHERE id = 1",
            "UPDATE failed:.*details.*mapping.*scalar",
        ),
        (
            "users:\n  - id: 1\n    details: {region: us}\n",
            "UPDATE users SET details = 'replacement' WHERE id = 1",
            "UPDATE failed:.*details.*mapping.*scalar",
        ),
        (
            "users:\n  - id: 1\n    details: {region: us}\n",
            "UPDATE users SET details = NULL WHERE id = 1",
            "UPDATE failed:.*details.*mapping.*scalar",
        ),
    ],
    ids=(
        "non_object_json",
        "json_number",
        "json_boolean",
        "json_string",
        "invalid_json_syntax",
        "json_null",
        "invalid_json",
        "sql_null",
    ),
)
def test_mapping_wrong_kind_updates_are_wrapped_and_byte_identical(
    tmp_path, source, statement, expected_message
):
    original = to_lf(source)
    path = write_bytes(tmp_path / "mapping-guard.yaml", original)
    yamlql = YamlQL(str(path), mode="rw", expose_mapping_columns=True)
    try:
        with pytest.raises(Exception, match=expected_message):
            yamlql.query(statement)
    finally:
        yamlql.close()
    assert_bytes_equal(read_bytes(path), original)


def test_mapping_parent_child_conflicts_fail_before_a_transaction_or_file_change(tmp_path):
    source = to_lf(
        "users:\n"
        "  - id: 1\n"
        "    details:\n"
        "      region: us\n"
        "  - id: 2\n"
        "    details:\n"
        "      region: us\n"
    )
    path = write_bytes(tmp_path / "mapping-conflict.yaml", source)
    yamlql = YamlQL(str(path), mode="rw", expose_mapping_columns=True)
    try:
        with pytest.raises(Exception, match="UPDATE failed:.*details.*details_region.*conflict"):
            yamlql.query(
                "UPDATE users SET details = '{\"region\": \"eu\"}', "
                "details_region = 'ap'"
            )
    finally:
        yamlql.close()
    assert_bytes_equal(read_bytes(path), source)
    assert not list(tmp_path.glob("mapping-conflict.yaml.backup"))
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "source, statement, error_prefix, fields",
    [
        (
            "users:\n  - id: 1\n    details:\n      region: us\n",
            "INSERT INTO users (id, details, details_region) "
            "VALUES (2, '{\"region\": \"eu\"}', 'ap')",
            "INSERT failed:",
            ("details", "details_region"),
        ),
        (
            "users:\n  - id: 1\n    details:\n      geo:\n        lat: 1\n",
            "UPDATE users SET details = '{\"geo\": {\"lat\": 2}}', "
            "details_geo = '{\"lat\": 2}', details_geo_lat = 2 WHERE id = 1",
            "UPDATE failed:",
            ("details", "details_geo", "details_geo_lat"),
        ),
        (
            "users:\n  - id: 1\n    geo-data:\n      lat: 1\n    geo_data:\n      lat: 2\n",
            "UPDATE users SET geo_data = '{\"lat\": 3}', "
            "geo_data_lat = 3 WHERE id = 1",
            "UPDATE failed:",
            ("geo_data", "geo_data_lat"),
        ),
    ],
    ids=("insert_parent_child", "nested_update", "sanitized_collision"),
)
def test_mapping_conflicts_fail_before_a_transaction_or_file_change(
    tmp_path, source, statement, error_prefix, fields
):
    original = to_lf(source)
    path = write_bytes(tmp_path / "mapping-conflict-variants.yaml", original)
    yamlql = YamlQL(str(path), mode="rw", expose_mapping_columns=True)
    try:
        pattern = error_prefix + ".*" + ".*".join(fields) + ".*conflict"
        with pytest.raises(Exception, match=pattern):
            yamlql.query(statement)
    finally:
        yamlql.close()
    assert_bytes_equal(read_bytes(path), original)
    assert not list(tmp_path.glob("mapping-conflict-variants.yaml.backup"))
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_named_mapping_insert_writes_object_without_unspecified_null_mapping(tmp_path, strategy):
    source = to_lf(
        "users:\n"
        "  - id: 0\n"
        "    details: {region: us}\n"
        "    profile: {name: existing}\n"
    )
    expected = to_lf(
        "users:\n"
        "  - id: 0\n"
        "    details: {region: us}\n"
        "    profile: {name: existing}\n"
        "  - id: 1\n"
        "    details:\n"
        "      region: eu\n"
        "      n: 1\n"
        "      ok: true\n"
    )
    path = write_bytes(tmp_path / "mapping-insert.yaml", source)
    result = _query(
        path,
        strategy,
        "INSERT INTO users (id, details) VALUES "
        "(1, '{\"region\": \"eu\", \"n\": 1, \"ok\": true}')",
        mode="rw",
        expose_mapping_columns=True,
    )
    assert result["success"]
    assert_bytes_equal(read_bytes(path), expected)
    assert b"profile:" not in read_bytes(path).split(b"  - id: 1\n", 1)[1]


def test_non_json_native_mapping_values_warn_and_read_as_null(tmp_path):
    path = _path(
        tmp_path,
        "unsafe-json.yaml",
        "users:\n"
        "  - id: 1\n"
        "    details: {finite: .inf}\n"
        "  - id: 2\n"
        "    details: {1: one}\n",
    )
    transformer = DataTransformer(YamlLoader(path).load(), expose_mapping_columns=True)
    transformer.transform()
    assert hasattr(transformer, "mapping_column_warnings"), (
        "DataTransformer must expose a mapping-column warnings list"
    )
    assert transformer.mapping_column_warnings == [
        "users.details row 0 cannot be represented as canonical JSON",
        "users.details row 1 cannot be represented as canonical JSON",
    ]
    assert all(math.isnan(value) for value in dict(transformer.transform())["users"]["details"])


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_positional_insert_legacy_value_count_omits_appended_mapping_columns(tmp_path, strategy):
    source = to_lf(
        "users:\n"
        "  - id: 1\n"
        "    details:\n"
        "      region: us\n"
        "      enabled: true\n"
    )
    path = write_bytes(tmp_path / "positional-legacy.yaml", source)
    yamlql = YamlQL(
        str(path), strategy=strategy, mode="rw", expose_mapping_columns=True
    )
    try:
        result = yamlql.query("INSERT INTO users VALUES (2, 'ap', false)")
        assert result["success"]
    finally:
        yamlql.close()

    reloaded = YamlQL(str(path), strategy=strategy, expose_mapping_columns=True)
    try:
        assert reloaded.query(
            "SELECT id, details_region, details_enabled, details FROM users ORDER BY id"
        ).to_dict("records") == [
            {
                "id": 1,
                "details_region": "us",
                "details_enabled": True,
                "details": '{"enabled":true,"region":"us"}',
            },
            {
                "id": 2,
                "details_region": "ap",
                "details_enabled": False,
                "details": None,
            },
        ]
    finally:
        reloaded.close()


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize(
    "statement, prefix",
    [
        ("INSERT INTO users (id, details) VALUES (2, '{\"region\": \"eu\"}')", "INSERT failed:"),
        ("UPDATE users SET details = '{\"region\": \"eu\"}' WHERE id = 1", "UPDATE failed:"),
    ],
)
def test_named_mapping_columns_write_json_objects_transactionally(
    tmp_path, strategy, statement, prefix
):
    original = to_lf("users:\n  - id: 1\n    details: {region: us}\n")
    path = write_bytes(tmp_path / "mapping-guard-temp.yaml", original)
    yamlql = YamlQL(
        str(path), strategy=strategy, mode="rw", expose_mapping_columns=True
    )
    try:
        assert yamlql.query(statement)["success"]
    finally:
        yamlql.close()
    assert read_bytes(path) != original
    assert not list(tmp_path.glob("mapping-guard-temp.yaml.backup"))
    assert not list(tmp_path.glob("*.tmp"))
