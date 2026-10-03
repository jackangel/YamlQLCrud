from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Iterator

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from pa_harness import FIXTURE, FIXTURE_SHA256, Recorder, copy_fixture


def fixture_hash_matches(path: Path, expected_hash: str) -> bool:
    return hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash


@pytest.fixture(scope="session", autouse=True)
def pristine_guard() -> Iterator[None]:
    assert fixture_hash_matches(FIXTURE, FIXTURE_SHA256), "fixture changed before test session"
    yield
    assert fixture_hash_matches(FIXTURE, FIXTURE_SHA256), "fixture changed during test session"


@pytest.fixture
def work(tmp_path: Path) -> tuple[Path, Path]:
    return copy_fixture(tmp_path), tmp_path


@pytest.fixture(scope="session")
def matrix_recorder() -> Iterator[Recorder]:
    recorder = Recorder()
    yield recorder
    recorder.write()


@pytest.fixture
def record(matrix_recorder: Recorder, request: pytest.FixtureRequest) -> Recorder:
    scenario = request.node.get_closest_marker("scenario")
    request.node._pa_scenario_id = scenario.args[0] if scenario and scenario.args else "unscoped"
    return matrix_recorder


def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[object]) -> None:
    if call.when == "call":
        scenario = item.get_closest_marker("scenario")
        item._pa_scenario_id = scenario.args[0] if scenario and scenario.args else "unscoped"


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    editor_root = TESTS_DIR.parent
    fixture_parent = FIXTURE.parent
    forbidden = []
    for pattern in ("*.ddbak", "*.backup", "*.tmp"):
        forbidden.extend(path for path in fixture_parent.glob(pattern) if path != FIXTURE)
        forbidden.extend(path for path in editor_root.rglob(pattern) if "results" not in path.parts)
    if forbidden:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        session.config.warn("C1", "stray harness artifacts: {0}".format(", ".join(str(path) for path in forbidden)))
