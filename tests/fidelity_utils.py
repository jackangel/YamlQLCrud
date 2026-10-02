"""Byte-exact helpers shared by YAML write-fidelity tests."""

import difflib
from pathlib import Path
from typing import List, Union

from yamlql_library import YamlQL
from yamlql_library.writer import YamlWriter


PathLike = Union[str, Path]


def write_bytes(path: PathLike, data: bytes) -> Path:
    """Write ``data`` without text-mode newline or encoding conversion."""
    target = Path(path)
    target.write_bytes(data)
    return target


def read_bytes(path: PathLike) -> bytes:
    """Read a file without text-mode newline or encoding conversion."""
    return Path(path).read_bytes()


def to_crlf(text: str) -> bytes:
    """Encode text as UTF-8 after normalizing its line endings to CRLF."""
    return _normalize_line_endings(text, "\r\n").encode("utf-8")


def to_lf(text: str) -> bytes:
    """Encode text as UTF-8 after normalizing its line endings to LF."""
    return _normalize_line_endings(text, "\n").encode("utf-8")


def assert_bytes_equal(actual: bytes, expected: bytes) -> None:
    """Assert byte equality, reporting a visible unified diff on mismatch."""
    if actual == expected:
        return

    offset = _first_differing_offset(actual, expected)
    diff = _visible_unified_diff(expected, actual)
    raise AssertionError(
        "Byte content differs at offset {offset}.\n"
        "expected bytes: {expected_bytes!r}\n"
        "actual bytes:   {actual_bytes!r}\n"
        "{diff}".format(
            offset=offset,
            expected_bytes=expected[offset : offset + 16],
            actual_bytes=actual[offset : offset + 16],
            diff=diff,
        )
    )


def changed_lines(before: bytes, after: bytes) -> List[str]:
    """Return added and removed lines from a byte-level line diff."""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    return [
        line
        for line in difflib.ndiff(_visible_lines(before_lines), _visible_lines(after_lines))
        if line.startswith("- ") or line.startswith("+ ")
    ]


def assert_minimal_diff(before: bytes, after: bytes, max_changed_lines: int) -> None:
    """Assert that a change replaces no more than ``max_changed_lines`` lines."""
    changes = changed_lines(before, after)
    if len(changes) <= max_changed_lines:
        return

    raise AssertionError(
        "Changed {actual} lines; expected at most {maximum}.\n{diff}".format(
            actual=len(changes),
            maximum=max_changed_lines,
            diff=_visible_unified_diff(before, after),
        )
    )


def noop_roundtrip(path: PathLike) -> bytes:
    """Load and write a YAML file through the public writer API, then read bytes."""
    writer = YamlWriter(path)
    writer.load()
    writer.write()
    return read_bytes(path)


def run_sql(path: PathLike, sql: str, mode: str = "rw"):
    """Run one SQL statement through ``YamlQL`` and always close its connection."""
    yamlql = YamlQL(str(path), mode=mode)
    try:
        return yamlql.query(sql)
    finally:
        yamlql.close()


def _normalize_line_endings(text: str, line_ending: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", line_ending)


def _first_differing_offset(actual: bytes, expected: bytes) -> int:
    for offset, (actual_byte, expected_byte) in enumerate(zip(actual, expected)):
        if actual_byte != expected_byte:
            return offset
    return min(len(actual), len(expected))


def _visible_unified_diff(expected: bytes, actual: bytes) -> str:
    expected_lines = _visible_lines(expected.splitlines(keepends=True))
    actual_lines = _visible_lines(actual.splitlines(keepends=True))
    return "".join(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile="expected",
            tofile="actual",
            lineterm="\n",
        )
    )


def _visible_lines(lines: List[bytes]) -> List[str]:
    return [repr(line.decode("utf-8", errors="backslashreplace")) + "\n" for line in lines]
