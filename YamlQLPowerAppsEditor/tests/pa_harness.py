from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Pattern, Tuple, TypeVar

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "YamlQLPowerAppsEditor" / "30 form 1.yaml"
FIXTURE_SHA256 = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()

T = TypeVar("T")
_OUTPUT_FAILURE_PATTERN = re.compile(
    r"unexpected error occurred|UPDATE failed|INSERT failed|DELETE failed|Traceback|Error:",
    re.IGNORECASE,
)


@dataclass
class Result:
    exit: int
    stdout: str
    stderr: str
    seconds: float
    failed_in_output: bool = False

    @property
    def ok(self) -> bool:
        """Return true only when the process and its reported operation succeeded."""
        return self.exit == 0 and not self.failed_in_output


def copy_fixture(dest_dir: Path, name: str = "30 form 1.yaml") -> Path:
    """Copy the read-only Power Apps fixture into an OS-temp test directory."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    destination = dest_dir / name
    shutil.copy2(FIXTURE, destination)
    return destination


def run_native(
    args: Sequence[str],
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    timeout: int = 120,
) -> Result:
    """Run one external process, retrying it once after a timeout.

    Probes Y07/Y08 and P-Y02--P-Y09 were interrupted during pandas/native
    imports.  Calls are deliberately single-process and receive a generous
    timeout; an exhausted retry is recorded as an unmeasured result instead of
    being converted into a fabricated outcome by callers.
    """
    command = [str(part) for part in args]
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    # P-Y06 requires UTF-8 transport for yamlpath Unicode probes.  The Task 9-1
    # write probe found that yamlql/Rich crashes on its warning banner under
    # captured cp1252 output, so both variables are forced for every child.
    if command and (Path(command[0]).name.lower().startswith("yaml-") or command[0].lower() == "yamlql"):
        process_env["PYTHONUTF8"] = "1"
        process_env["PYTHONIOENCODING"] = "utf-8"
    started = time.perf_counter()
    for attempt in range(2):
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd) if cwd else None,
                env=process_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            return Result(
                completed.returncode,
                stdout,
                stderr,
                time.perf_counter() - started,
                bool(_OUTPUT_FAILURE_PATTERN.search(stdout) or _OUTPUT_FAILURE_PATTERN.search(stderr)),
            )
        except subprocess.TimeoutExpired as error:
            if attempt:
                stdout = error.stdout or ""
                stderr = error.stderr or ""
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", "replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", "replace")
                return Result(124, stdout, stderr + "\nTIMEOUT: unmeasured", time.perf_counter() - started, True)
    raise AssertionError("unreachable")


def yamlql_sql(
    file: Path,
    sql: str,
    writable: bool = False,
    output: str = "list",
    max_depth: int = 30,
    strategy: str = "depth",
) -> Result:
    args = [
        "yamlql", "sql", "-f", str(file), "--output", output,
        "--max-depth", str(max_depth), "--strategy", strategy,
    ]
    if writable:
        args.append("--writable")
    args.append(sql)
    return run_native(args, cwd=REPO_ROOT)


def yamlql_discover(file: Path, max_depth: int = 30, strategy: str = "depth") -> Result:
    # Y01/Y04: the one authoritative yamlql invocation recipe.
    return run_native(
        ["yamlql", "discover", "-f", str(file), "--max-depth", str(max_depth), "--strategy", strategy],
        cwd=REPO_ROOT,
    )


def _yamlpath(command: str, args: Sequence[str]) -> Result:
    return run_native([command] + [str(arg) for arg in args], cwd=REPO_ROOT)


def yamlpath_set(args: Sequence[str]) -> Result:
    return _yamlpath("yaml-set", args)


def yamlpath_get(args: Sequence[str]) -> Result:
    return _yamlpath("yaml-get", args)


def yamlpath_paths(args: Sequence[str]) -> Result:
    return _yamlpath("yaml-paths", args)


def yamlpath_validate(args: Sequence[str]) -> Result:
    return _yamlpath("yaml-validate", args)


def run_ps51(command: str, cwd: Path) -> Result:
    """Execute a command in Windows PowerShell 5.1 without shell quoting loss."""
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    result = run_native(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded,
        ],
        cwd=cwd,
    )
    return result


def load_yaml(path: Path) -> Any:
    """Load a test oracle with ruamel round-trip semantics and PyYAML validation."""
    content = path.read_text(encoding="utf-8")
    ruamel = YAML(typ="rt")
    document = ruamel.load(content)
    yaml.safe_load(content)
    return document


def walk(node: Any, path: Tuple[Any, ...] = ()) -> Iterator[Tuple[Tuple[Any, ...], Any, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield path + (key,), key, value
            yield from walk(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield path + (index,), index, value
            yield from walk(value, path + (index,))


def find_controls(doc: Any, control_type: Optional[str] = None) -> list[tuple[Tuple[Any, ...], str, Any]]:
    found = []
    for path, key, value in walk(doc):
        if key != "Control" or not isinstance(value, str):
            continue
        if control_type is None or value == control_type:
            name = str(path[-2]) if len(path) >= 2 else ""
            found.append((path[:-1], name, value))
    return found


def count_property(doc: Any, property_name: str) -> int:
    return sum(1 for _, key, _ in walk(doc) if key == property_name)


def count_value_regex(doc: Any, pattern: Any) -> int:
    compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
    return sum(1 for _, _, value in walk(doc) if isinstance(value, str) and compiled.search(value))


def count_contains(doc: Any, text: str) -> int:
    return sum(1 for _, _, value in walk(doc) if isinstance(value, str) and text in value)


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def structural_diff(before: Any, after: Any) -> list[tuple[Tuple[Any, ...], Any, Any]]:
    differences = []

    def compare(left: Any, right: Any, path: Tuple[Any, ...]) -> None:
        left = _plain(left)
        right = _plain(right)
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right), key=str):
                compare(left.get(key), right.get(key), path + (key,))
        elif isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                compare(left[index] if index < len(left) else None, right[index] if index < len(right) else None, path + (index,))
        elif left != right:
            differences.append((path, left, right))

    compare(before, after, ())
    return differences


def literal_block_paths(path: Path) -> list[tuple[Tuple[Any, ...], str]]:
    document = load_yaml(path)
    source = path.read_text(encoding="utf-8")
    styles = ["|-" if "|-" in line else "|" for line in source.splitlines() if re.search(r":\s*\|[-+]?\s*(?:#.*)?$", line)]
    literal_paths = [item_path for item_path, _, value in walk(document) if isinstance(value, LiteralScalarString)]
    return [(item_path, styles[index] if index < len(styles) else "|") for index, item_path in enumerate(literal_paths)]


def byte_stats(a: Path, b: Path) -> dict[str, int]:
    before = a.read_text(encoding="utf-8").splitlines()
    after = b.read_text(encoding="utf-8").splitlines()
    changed = sum(1 for line in difflib.ndiff(before, after) if line.startswith("+ ") or line.startswith("- "))
    return {"size_delta": b.stat().st_size - a.stat().st_size, "changed_lines": changed}


def assert_only_changes(diff: Sequence[tuple[Tuple[Any, ...], Any, Any]], expected_paths: Sequence[Tuple[Any, ...]]) -> None:
    actual = {path for path, _, _ in diff}
    expected = set(expected_paths)
    assert actual <= expected, "unexpected paths changed: {0}".format(sorted(actual - expected, key=str))


def timed(fn: Callable[[], T]) -> tuple[T, float]:
    started = time.perf_counter()
    return fn(), time.perf_counter() - started


def ps_quote_single(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Recorder:
    def __init__(self, destination: Optional[Path] = None) -> None:
        self.destination = destination or Path(__file__).resolve().parent / "results" / "matrix_results.json"
        self.entries: list[dict[str, Any]] = []

    def add(
        self,
        scenario_id: str,
        tool: str,
        status: str,
        expected: Any,
        measured: Any,
        seconds: float = 0.0,
        notes: str = "",
    ) -> None:
        if status not in {"pass", "fail", "gap", "skip", "unmeasured"}:
            raise ValueError("unsupported matrix status: {0}".format(status))
        self.entries.append({
            "scenario_id": scenario_id,
            "tool": tool,
            "status": status,
            "expected": expected,
            "measured": measured,
            "seconds": seconds,
            "notes": notes,
        })

    def write(self) -> None:
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.destination.write_text(json.dumps(self.entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
