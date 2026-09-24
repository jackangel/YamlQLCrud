# Task Intake — UTF-8 / Non-ASCII (Chinese) Character Handling Bug

## Objective

Fix `yamlql_library` so that YAML files and SQL inputs containing non-ASCII
characters (e.g. Chinese text such as "世界") are read, queried, written, and
displayed by the CLI without silent corruption (mojibake), unhandled
exceptions, or lossy `\uXXXX` escaping — across all code paths: loading
existing YAML files, CRUD round-trips (INSERT/UPDATE/DELETE → write-back →
reload), the `--sql-file` CLI option, and interactive/console output. "Fixed"
means every Acceptance Criterion below passes on Windows, Linux, and macOS
without requiring the user to change locale/console configuration.

## Definition of Done

- Root cause(s) are fixed in source with minimal, targeted changes (no
  unrelated refactors).
- A repro fixture/test demonstrating the originally reported failure mode is
  added and passes.
- Full existing test suite (`tests/test_crud.py`, `tests/test_yamlql.py`)
  passes unchanged (no regressions to ASCII-only behavior).
- No new required runtime dependencies are introduced.
- No public API signatures change unless unavoidable.

## Acceptance Criteria

- **AC-1**: Loading a pre-existing YAML file on disk that already contains
  non-ASCII/Chinese character data (not created via SQL INSERT, but as a
  static UTF-8-encoded fixture file) via `YamlLoader`/`YamlQL` and querying it
  returns the exact original characters in query results, with no
  `UnicodeDecodeError` and no mojibake.
- **AC-2**: Performing INSERT/UPDATE with non-ASCII/Chinese string values and
  then writing/round-tripping the YAML file back to disk preserves the exact
  characters without `\uXXXX` escaping or corruption (regression guard for
  currently-passing behavior; must not regress).
- **AC-3**: Running `yamlql sql --file <yaml> --sql-file <path>` where
  `<path>` is a UTF-8-encoded SQL file containing non-ASCII/Chinese
  characters in string literals executes correctly and produces the correct
  (non-corrupted) values — fixes the confirmed silent-corruption bug in
  [yamlql_library/cli.py](yamlql_library/cli.py#L111).
- **AC-4**: CLI commands (`sql`, `discover`, interactive SQL prompt) print
  non-ASCII/Chinese characters correctly to stdout on Windows, Linux, and
  macOS without raising `UnicodeEncodeError`/`UnicodeDecodeError`.
- **AC-5**: The existing ASCII-only test suite
  ([tests/test_crud.py](tests/test_crud.py),
  [tests/test_yamlql.py](tests/test_yamlql.py)) continues to pass unmodified.
- **AC-6**: A new regression test loads a static YAML fixture file (written
  to disk as UTF-8, containing Chinese characters as data, not inserted via
  SQL) and verifies fidelity through load → query → write-back → reload,
  closing the current test-coverage gap (see Unknowns).

## Seven-Dimension Constraints

- **Functional**: Fix must apply uniformly across `loader.py`, `writer.py`,
  `cli.py`, and any other file-I/O path; must not special-case Chinese only —
  general non-ASCII/Unicode correctness is required.
- **Performance**: No material performance regression; changes are limited to
  file-open encoding parameters and possibly console stream configuration —
  negligible overhead expected.
- **Security/Privacy**: No new attack surface. Must not disable YAML
  `safe_load`/safe practices while fixing encoding. Must not read/write files
  outside the paths already specified by the user/CLI arguments.
- **Compatibility/Back-compat**: Must not change on-disk YAML formatting for
  existing ASCII-only files (comments, quote style, indentation preserved via
  `ruamel.yaml` as today). Must not change public function/class signatures
  in `yamlql_library` unless unavoidable. Must support Python ≥3.9 per
  [pyproject.toml](pyproject.toml) (not just the Python 3.14 environment used
  for this investigation).
- **Operational**: No new required dependencies (fix should use only
  already-declared dependencies: `pyyaml`, `ruamel.yaml`, stdlib `open()`
  encoding parameters, stdlib `io`/`sys` stream reconfiguration if needed for
  CLI output).
- **Data/Integrity**: Fix must not alter non-ASCII byte content of unrelated,
  untouched YAML sections/files (write-back must remain scoped to only the
  CRUD-modified paths, consistent with current `YamlWriter` format-preserving
  design).
- **UX/Observability**: Errors, if any remain for genuinely malformed input
  encodings, should raise clear exceptions rather than silently producing
  corrupted data (current `cli.py` bug is a *silent* corruption case, which is
  worse than a raised exception).

## Scope

**In scope:**
- [yamlql_library/loader.py](yamlql_library/loader.py) — YAML file read path.
- [yamlql_library/writer.py](yamlql_library/writer.py) — YAML file write-back
  path (`ruamel.yaml` dump).
- [yamlql_library/cli.py](yamlql_library/cli.py) — `--sql-file` file read
  (confirmed bug, line 111).
- [yamlql_library/cli_logic.py](yamlql_library/cli_logic.py) — CLI
  output/print paths, stdout encoding behavior.
- [yamlql_library/transaction.py](yamlql_library/transaction.py) — temp-file
  YAML validation read path (already uses `encoding='utf-8'`; verify no
  regression).
- New/updated test fixtures and tests under `tests/` covering a static
  UTF-8 YAML fixture with non-ASCII content.

**Out of scope:**
- `yamlql_library/transformer.py` / `reverse_transformer.py` internal string
  handling — investigated, no encode/decode operations found; not touched
  unless the DuckDB/pandas round-trip investigation (see Unknowns) reveals an
  issue here during implementation.
- `yamlql_library/database.py` / `crud_handlers.py` — investigated, no
  encode/decode operations found; not touched unless implementation-phase
  testing reveals an issue.
- Changing YAML formatting/style choices (indentation, quote style) unrelated
  to encoding.
- Non-Unicode-related bugs discovered incidentally.

## Assumptions

- **A-1**: The primary, concretely reproduced bug is
  [yamlql_library/cli.py](yamlql_library/cli.py#L111)'s `open(sql_file, 'r')`
  call lacking `encoding='utf-8'`, which silently mis-decodes UTF-8 SQL files
  as the platform's locale-preferred encoding (confirmed `cp1252` on this
  Windows system) instead of raising an error — producing corrupted
  characters. Assumed fix: add `encoding='utf-8'` to this `open()` call.
- **A-2**: `yamlql_library/loader.py` and `yamlql_library/writer.py` already
  correctly specify `encoding='utf-8'` on all `open()` calls, and
  `ruamel.yaml`'s `YAML()` class used in `writer.py` defaults
  `allow_unicode=True` (verified experimentally on the installed version
  0.19.1) so it does not escape non-ASCII characters. These two files are
  assumed correct for the direct load/write path and are treated as low
  priority / regression-guard targets rather than primary fix targets, unless
  implementation-phase testing (with DuckDB installed) proves otherwise.
  Escalation trigger: if AC-1 or AC-6 fails when actually implemented/tested.
- **A-3**: PyYAML's `safe_load`/`safe_load_all` already strips a UTF-8 BOM
  (`\ufeff`) transparently (verified experimentally), so BOM-prefixed files
  are not a distinct root cause requiring `utf-8-sig`.
- **A-4**: On the target Python versions (≥3.9) and platforms in scope,
  `sys.stdout` defaults to UTF-8 encoding on Windows consoles per PEP 528
  (verified on Python 3.14/Windows in this session: `sys.stdout.encoding ==
  'utf-8'`), so AC-4 is expected to already pass in most environments; it is
  retained as an explicit regression-guarding AC rather than a known-broken
  target.
- **A-5**: "Fixed" does not require adding `PYTHONIOENCODING` or console
  code-page instructions to end users; the tool should work correctly out of
  the box.

## Unknowns

- **U-1**: Whether the full CRUD round-trip pipeline (YAML → pandas →
  DuckDB → pandas → `ReverseTransformer` → `YamlWriter`) preserves non-ASCII
  characters when values originate from *loading* a pre-existing UTF-8 file
  (as opposed to being inserted directly via a SQL string literal, which
  existing tests `test_insert_unicode_characters` and
  `test_crud_unicode_content` in
  [tests/test_crud.py](tests/test_crud.py#L187) already cover). This could
  not be verified in this investigation because `duckdb`/`pandas` could not
  be installed/imported in the available Python 3.14 environment within a
  reasonable time (no prebuilt wheel readily available); this must be
  verified during implementation by actually running the test suite in a
  supported Python environment (3.9–3.13).
- **U-2**: Whether the currently existing unicode tests
  (`test_insert_unicode_characters`, `test_crud_unicode_content`) actually
  pass in CI today, since they could not be executed in this investigation
  environment.
- **U-3**: Whether non-BMP characters (e.g. emoji like 🌍, already present in
  `test_insert_unicode_characters`) or right-to-left/combining scripts behave
  correctly through the same pipeline — not specifically tested here.

## Dependencies

- Depends on being able to add a new UTF-8-encoded test fixture file (e.g.
  `tests/test_data/unicode_sample.yaml`) containing Chinese characters as
  static data, and a corresponding test in `tests/test_crud.py` or
  `tests/test_yamlql.py`.
- Depends on a working Python environment with `duckdb`, `pandas`,
  `ruamel.yaml`, `pyyaml` installed to run the existing/new test suite during
  implementation and validation (not fully available in this investigation's
  environment — see U-1/U-2).
- No external service or approval dependency.

## Risks

- **R-1**: Risk of breaking existing ASCII-only tests if `encoding='utf-8'`
  is added incorrectly or if other read/write parameters are changed
  alongside the encoding fix. Mitigation: minimal, isolated diff limited to
  encoding parameters; run full existing suite before/after.
- **R-2**: Risk that the DuckDB/pandas transit layer (U-1) has its own
  encoding issue not discoverable via static analysis alone; if so, the fix
  scope would need to expand into `database.py`/`crud_handlers.py`/
  `transformer.py`, which are currently out of scope. Mitigation: flagged as
  Unknown; implementer must validate with an actual load-from-disk unicode
  fixture (AC-6) before declaring the fix complete.
- **R-3**: Platform-specific console/code-page risk on Windows terminals
  where `PYTHONUTF8`/console code page is not 65001 in older/non-default
  configurations (e.g. legacy `cmd.exe` with a non-UTF-8 active code page for
  I/O redirected to a file, or older Python 3.9 builds) — could cause
  `UnicodeEncodeError` on `rich.print`/`typer.echo` even if code is otherwise
  correct. Mitigation: AC-4 should be validated on at least one Windows and
  one Linux/macOS environment.
- **R-4**: Risk that the `cli.py` fix (`open(sql_file, 'r')` →
  `encoding='utf-8'`) is too narrow an assumption if some users legitimately
  save SQL files in a different encoding; accepted as a reasonable, common,
  and minimal-risk default consistent with the rest of the codebase's
  `encoding='utf-8'` convention.

## Material Questions for the User

None. No genuinely blocking ambiguity was found. All open items are recorded
above as bounded Assumptions (A-1…A-5) or Unknowns (U-1…U-3) to be resolved
during implementation/validation rather than requiring upfront clarification.
