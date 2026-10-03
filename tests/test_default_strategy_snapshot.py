"""Freeze legacy depth/adaptive projections.

Set ``YAMLQL_REGEN_GOLDEN=1`` only after an explicit human decision that a
legacy default-projection change is intentional.  This test otherwise compares
the checked-in, before-change golden snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from yamlql_library import YamlQL


DATA_DIR = Path(__file__).parent / "test_data"
GOLDEN_PATH = DATA_DIR / "default_projection_golden.json"
STRATEGIES = ("depth", "adaptive")
MAX_DEPTHS = (5, 30)


def _json_value(value):
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return str(value)


def _capture() -> dict:
    snapshot = {}
    for yaml_path in sorted((*DATA_DIR.glob("*.yaml"), *DATA_DIR.glob("*.yml"))):
        fixture = {}
        for strategy in STRATEGIES:
            for max_depth in MAX_DEPTHS:
                yql = YamlQL(str(yaml_path), strategy=strategy, max_depth=max_depth)
                try:
                    tables = []
                    for name, dataframe in yql.tables:
                        payload = dataframe.to_json(
                            orient="split", date_format="iso", default_handler=str
                        )
                        tables.append(
                            {
                                "name": name,
                                "columns": list(dataframe.columns),
                                "dtypes": {column: str(dtype) for column, dtype in dataframe.dtypes.items()},
                                "row_count": len(dataframe),
                                "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                            }
                        )
                    fixture["{0}:{1}".format(strategy, max_depth)] = {
                        "tables": tables,
                        "column_name_map_keys": sorted(yql.column_name_map),
                        "table_doc_map": {
                            key: _json_value(value)
                            for key, value in sorted(yql.table_doc_map.items())
                        },
                    }
                finally:
                    yql.close()
        snapshot[yaml_path.name] = fixture
    return snapshot


def test_default_strategy_projection_matches_golden():
    """Legacy default strategies remain deterministic and byte-for-byte frozen."""
    first = _capture()
    assert first == _capture()
    if os.environ.get("YAMLQL_REGEN_GOLDEN") == "1":
        GOLDEN_PATH.write_text(
            json.dumps(first, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert first == expected
