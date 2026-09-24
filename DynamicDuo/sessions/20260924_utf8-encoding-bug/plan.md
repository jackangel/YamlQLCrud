# Plan — UTF-8 / Non-ASCII (Chinese) Character Handling Bug

**STATUS: READY**

Single active plan. No genuine blocking gap was found versus the approved
intake (`task-intake.md` "Material Questions for the User" = None). All
assumptions (A-1..A-5) and unknowns (U-1..U-3) are bounded and carried
forward into steps/validation below rather than requiring upfront
clarification.

## Objective / Scope / Definition of Done (carried from intake)

Fix `yamlql_library` so non-ASCII (e.g. Chinese) content in YAML/SQL inputs
is read, queried, written, and displayed without silent corruption,
unhandled exceptions, or lossy escaping, per AC-1..AC-6, with a minimal
targeted diff, a repro-covering test added, the existing suite passing
unchanged, no new dependencies, and no public API signature changes unless
unavoidable. In scope: `loader.py`, `writer.py`, `cli.py`, `cli_logic.py`,
`transaction.py`, plus new test fixtures/tests. Out of scope:
`transformer.py`, `reverse_transformer.py`, `database.py`,
`crud_handlers.py` (unless implementation-phase evidence proves otherwise —
none found in intake investigation), unrelated formatting changes,
unrelated bugs.

## Acceptance Criteria pass conditions (carried from intake)

- **AC-1**: Loading a pre-existing on-disk UTF-8 YAML fixture (Chinese data,
  not SQL-inserted) via `YamlLoader`/`YamlQL` and querying it returns exact
  original characters; no `UnicodeDecodeError`; no mojibake.
- **AC-2**: INSERT/UPDATE with non-ASCII values, then write-back/round-trip,
  preserves exact characters, no `\uXXXX` escaping (regression guard).
- **AC-3**: `yamlql sql --file <yaml> --sql-file <path>` with a UTF-8 SQL
  file containing non-ASCII string literals executes correctly, no
  corruption (fixes [cli.py](../../../yamlql_library/cli.py#L111)).
- **AC-4**: CLI commands (`sql`, `discover`, interactive prompt) print
  non-ASCII correctly to stdout on Windows/Linux/macOS, no
  `UnicodeEncodeError`/`UnicodeDecodeError`.
- **AC-5**: Existing `tests/test_crud.py` + `tests/test_yamlql.py` pass
  unmodified.
- **AC-6**: New regression test loads a static UTF-8 fixture (Chinese data,
  not SQL-inserted) and verifies fidelity through load → query →
  write-back → reload.

## Evidence gathered during planning (task-local, verified)

Grep of `open(` calls across `yamlql_library/` (verified this session):

| File:line | Call | encoding='utf-8' present? |
| --- | --- | --- |
| `cli.py:111` | `open(sql_file, 'r')` | **No — confirmed bug (A-1)** |
| `loader.py:28` | `open(self.file_path, 'r', ...)` | Yes |
| `writer.py:68` | `open(self.file_path, 'r', ...)` | Yes |
| `writer.py:214` | `open(self.file_path, 'w', ...)` | Yes |
| `transaction.py:265` | `open(file_path, 'r', ...)` | Yes |

This confirms A-1 and A-2 exactly as stated in the intake: `cli.py` line 111
is the sole missing-encoding call among the in-scope files; `loader.py`,
`writer.py`, `transaction.py` already specify `encoding='utf-8'`.
`cli_logic.py` uses `rich.print`/`console.print` exclusively for output (no
raw `open()`/stdout reconfiguration calls found) — relevant to AC-4/C-6.

`tests/test_crud.py` already contains `test_insert_unicode_characters` and
(per intake) `test_crud_unicode_content`, both SQL-INSERT-originated —
confirms the AC-6 gap (no test loads a pre-existing on-disk UTF-8 fixture).

## Alternatives considered

| # | Approach | Coverage of AC | Scope/complexity | Safety/reversibility | Rejected because |
| --- | --- | --- | --- | --- | --- |
| A (selected) | Minimal targeted fix: add `encoding='utf-8'` only to `cli.py:111`; verify (not modify) already-correct `loader.py`/`writer.py`/`transaction.py`; add one new UTF-8 fixture + regression test(s) for AC-1/AC-3/AC-6; evidence-gated stdout check for AC-4 (only patch if a real gap is proven); run full suite | Full — maps 1:1 to AC-1..AC-6 | Low — single-line source fix + tests | High — trivially revertible, no unrelated diffs | — |
| B | Broad hardening: proactively reconfigure `sys.stdin`/`sys.stdout`/`sys.stderr` to UTF-8 at CLI startup, add `encoding='utf-8'` defensively everywhere (even where already correct), add `PYTHONUTF8`/codepage guidance | Full, but over-scoped | Higher — touches CLI entrypoint globally, more surface for regressions | Lower — global stream reconfiguration risks unrelated behavior changes (e.g. redirected-output edge cases) | Violates Definition of Done ("minimal, targeted changes... no unrelated refactors") and constraint "must not... unless unavoidable"; A-4 already shows stdout is not broken by default — proactively rewriting it is an unrelated/unverified change (R-3 risk without evidence) |
| C | Bug-only fix: patch `cli.py:111` only, no new fixture/test, rely on existing suite | Fails AC-6 (explicit new-test requirement) and weakens AC-1/AC-3 evidence | Lowest | High | Fails Definition of Done ("repro fixture/test... is added and passes") and AC-6 explicitly requires a new test — not a safe complete option |

**Selection: Approach A.** It is the only option that satisfies every AC and
the Definition of Done without introducing unverified, out-of-scope changes.
It keeps the diff minimal (constraint: "minimal, targeted changes"), treats
AC-4/stdout as evidence-gated rather than pre-emptively "fixed" (avoiding an
unfalsifiable, unrelated change per A-4), and directly closes the AC-6
test-coverage gap identified in the intake.

## Ordered Steps

### Step 1 — Baseline environment & suite check (supports C-5, resolves U-1/U-2)
- **Action**: Before any change, resolve a working Python (≥3.9, ≤3.13 per
  pyproject `requires-python`/tested range) environment with `duckdb`,
  `pandas`, `ruamel.yaml`, `pyyaml` installed (use the project's configured
  interpreter/environment tooling, not an arbitrary system Python), then run
  the full existing suite (`tests/test_crud.py`, `tests/test_yamlql.py`)
  unmodified.
- **Expected result**: Suite executes (imports succeed) and reports current
  pass/fail state, resolving whether `test_insert_unicode_characters` /
  `test_crud_unicode_content` already pass today (U-2) and whether
  duckdb/pandas import cleanly in a real execution environment (U-1,
  partially).
- **Prerequisites**: None (read-only).
- **Serves AC**: Establishes baseline for AC-5; informs AC-1/AC-2/AC-6.
- **Planned commitment**: none new (informs execution of C-5 below).
- **Validation/pass condition**: `pytest` runs to completion (pass or fail
  reported, not an import/collection crash). If it crashes on
  duckdb/pandas import, that is itself a material finding, not a plan
  defect.
- **Risk/mitigation**: Same import difficulty encountered during intake
  investigation (duckdb/pandas not installable in the Python 3.14
  investigation environment). Mitigation: use whatever supported Python
  version (3.9–3.13) is actually available in this workspace's configured
  environment rather than the 3.14 interpreter used for investigation.
- **Recovery if it fails**: If no environment with duckdb/pandas can be
  obtained, do not block the source fix (Steps 2–4 are independent of
  duckdb). Proceed with a standalone repro script covering the file-I/O
  layer only (loader/writer/cli open() calls) and explicitly mark AC-5/AC-6
  as "not fully verified via full suite" in the final report rather than
  claiming a false pass.
- **Reversibility/rollback**: N/A — read-only step, nothing to roll back.
- **Approval**: None required.

### Step 2 — Apply the confirmed fix (C-1)
- **Action**: In [yamlql_library/cli.py](../../../yamlql_library/cli.py#L111),
  change `open(sql_file, 'r')` to `open(sql_file, 'r', encoding='utf-8')`.
  No other parameters/lines touched.
- **Expected result**: A single-line diff in `cli.py`; `--sql-file` reads
  are now explicitly UTF-8 regardless of platform locale.
- **Prerequisites**: Step 1 baseline attempted (not blocking if
  duckdb/pandas unavailable — this step does not depend on them).
- **Serves AC**: AC-3.
- **Planned commitment**: **C-1**.
- **Validation/pass condition**: `git diff` shows exactly one changed line
  in `cli.py`, adding only the `encoding='utf-8'` keyword argument.
- **Risk/mitigation**: Negligible — single keyword argument addition,
  consistent with the existing codebase convention (R-4 accepted as
  reasonable default per intake).
- **Recovery if it fails**: If the diff is larger than expected or breaks
  something, `git checkout -- yamlql_library/cli.py` to revert and retry.
- **Reversibility/rollback**: Fully reversible — `git revert`/`git checkout`
  of this one line.
- **Approval**: None required (low-risk, local, reversible).

### Step 3 — Audit remaining in-scope files (C-2)
- **Action**: Re-verify (not modify, unless a genuine gap is found) that
  `loader.py:28`, `writer.py:68`, `writer.py:214`, `transaction.py:265`
  still specify `encoding='utf-8'` at execution time (guards against drift
  since the planning-phase grep). Also scan `cli_logic.py` for any raw
  `open()`/file-encoding calls (none found in planning; confirm still none).
- **Expected result**: Audit confirms A-2 holds (all four calls already
  correct) with no source change needed; OR, if drift is found, apply the
  same single-keyword-argument fix pattern as Step 2 to only the specific
  affected call.
- **Prerequisites**: Step 2 complete (so the audit reflects the
  post-fix file set).
- **Serves AC**: AC-1, AC-2, AC-5 (regression guard).
- **Planned commitment**: **C-2**.
- **Validation/pass condition**: Audit output matches the planning-phase
  evidence table above (4/4 calls already correct); if not, the delta is
  documented and a minimal fix applied and re-audited.
- **Risk/mitigation**: Low — verification-only in the expected case.
- **Recovery if it fails**: If a real missing-encoding call is discovered,
  fix it with the same minimal pattern as C-1 and re-run Step 1's baseline
  to confirm no regression.
- **Reversibility/rollback**: Fully reversible (same as Step 2, if any edit
  is made at all).
- **Approval**: None required.

### Step 4 — Add static UTF-8 fixture file (C-3)
- **Action**: Create `tests/test_data/unicode_sample.yaml`: a small,
  ASCII-keyed YAML table (list of mapping objects) with at least one
  non-ASCII string value containing Chinese characters (e.g. "世界"), saved
  to disk as UTF-8 (no BOM), representing data that exists on disk
  independent of any SQL INSERT.
- **Expected result**: New fixture file present under `tests/test_data/`,
  parses via `yaml.safe_load`/`ruamel.yaml` without error, and its bytes
  round-trip through `open(path, encoding='utf-8').read()` exactly.
- **Prerequisites**: None.
- **Serves AC**: AC-1, AC-6 (provides the "not SQL-inserted" fixture the
  intake identifies as the current coverage gap).
- **Planned commitment**: **C-3**.
- **Validation/pass condition**: File exists at the target path; manual
  parse check (`yaml.safe_load`) succeeds; content contains the intended
  Chinese substring verbatim (byte-for-byte, checked via a quick read-back).
- **Risk/mitigation**: Keep YAML *keys* ASCII (only *values* non-ASCII) to
  avoid conflating unrelated key-parsing concerns with the encoding fix,
  consistent with scope ("must not special-case Chinese only" — general
  Unicode correctness, but this fixture specifically targets Chinese per
  the reported bug).
- **Recovery if it fails**: If the fixture doesn't parse as intended, adjust
  its structure (e.g. simplify nesting) and re-validate before use in
  Step 5.
- **Reversibility/rollback**: Fully reversible — new file, safe to delete/
  amend without side effects on other tests.
- **Approval**: None required.

### Step 5 — Add regression test(s) for AC-1 / AC-3 / AC-6 (C-4)
- **Action**: In `tests/test_crud.py` (co-located with the existing unicode
  tests for consistency), add a new test (e.g.
  `test_load_static_unicode_fixture_roundtrip`) that: (a) loads the Step 4
  fixture via `YamlQL`/`YamlLoader` and asserts the queried Chinese value
  matches exactly (AC-1); (b) performs an UPDATE or INSERT via SQL against
  the loaded file, writes back, reopens/reloads, and asserts fidelity
  through load→query→write-back→reload (AC-6). Additionally add/extend a
  test that invokes the `--sql-file` CLI path with a UTF-8-encoded SQL file
  containing a Chinese string literal (via Typer's `CliRunner` or direct
  call into `cli_logic`) to directly regression-guard AC-3 against the
  Step 2 fix.
- **Expected result**: New test(s) pass; they exercise exactly the code
  paths fixed/verified in Steps 2–4.
- **Prerequisites**: Step 2 (fix applied), Step 4 (fixture exists).
- **Serves AC**: AC-1, AC-3, AC-6.
- **Planned commitment**: **C-4**.
- **Validation/pass condition**: New test(s) pass under `pytest`. Sanity
  check (reasoned, not necessarily re-executed against pre-fix code): the
  AC-3 test would have failed prior to Step 2 on a non-UTF-8-locale
  platform (e.g. Windows cp1252), confirming it actually exercises the bug.
- **Risk/mitigation**: Cross-platform flakiness from relying on default
  locale — mitigated by having the test itself always write/read fixture
  and SQL-file bytes with explicit `encoding='utf-8'`, independent of the
  fix under test, so only the production code path's behavior is what's
  being asserted.
- **Recovery if it fails**: If a `CliRunner`/subprocess-based AC-3 test
  proves unreliable across platforms in this session, fall back to a more
  direct unit-level call into `cli_logic.run_query()` with a pre-decoded
  UTF-8 string (lower fidelity for true end-to-end `--sql-file` coverage)
  and explicitly note the reduced fidelity in the final report.
- **Reversibility/rollback**: Fully reversible — test-file-only addition.
- **Approval**: None required.

### Step 6 — Evidence-gated CLI stdout verification for AC-4 (C-6)
- **Action**: Using the fixture/tests from Steps 4–5, actually invoke a CLI
  path that prints Chinese characters to stdout (e.g. `sql`/`discover`/
  interactive prompt via `CliRunner` or subprocess) on whichever OS(es) are
  available in this execution session, and inspect captured stdout for
  corruption or `UnicodeEncodeError`/`UnicodeDecodeError`. Do **not**
  pre-emptively add stdout/stdin reconfiguration code — only patch if this
  concrete check surfaces a real failure.
- **Expected result**: Per A-4 (verified in the intake investigation: Python
  3.14/Windows `sys.stdout.encoding == 'utf-8'` via PEP 528), the check is
  expected to pass with no code change needed. If it fails on the available
  environment, apply the smallest possible guarded fix at the CLI
  entrypoint only (e.g. `if hasattr(sys.stdout, "reconfigure"):
  sys.stdout.reconfigure(encoding="utf-8")`), scoped to `cli.py`'s entry
  function, not a global monkeypatch.
- **Prerequisites**: Steps 2 and 5 complete (need real Chinese-bearing CLI
  invocation to test against).
- **Serves AC**: AC-4.
- **Planned commitment**: **C-6**.
- **Validation/pass condition**: Captured stdout contains the exact
  original Chinese characters with no exception, on at least one available
  OS in this session.
- **Risk/mitigation (R-3)**: Only the OS(es) actually available in this
  execution session can be verified directly; other platforms rely on
  documented stdlib behavior (PEP 528) plus A-4's experimental verification
  rather than a live test in this session.
- **Recovery if it fails**: Apply the minimal guarded reconfiguration
  described above, re-run the check, and re-run Step 5's tests to confirm
  no regression.
- **Reversibility/rollback**: If a stdout fix is added, it is a small,
  fully revertible diff scoped to the CLI entrypoint.
- **Approval**: None required (still a low-risk, local, reversible change
  even in the fallback case).

### Step 7 — Full suite run with fix + new tests (C-5)
- **Action**: Run the complete `tests/` suite (existing + new) in the
  environment resolved in Step 1, after Steps 2–6 are applied.
- **Expected result**: All existing tests in `test_crud.py`/`test_yamlql.py`
  still pass unmodified (AC-5); all new tests from Step 5 pass.
- **Prerequisites**: Steps 1–6 complete.
- **Serves AC**: AC-5 primarily; cross-validates AC-1/AC-2/AC-3/AC-6.
- **Planned commitment**: **C-5**.
- **Validation/pass condition**: `pytest` exit code 0 for the full `tests/`
  directory.
- **Risk/mitigation**: Same duckdb/pandas environment risk as Step 1.
  Mitigation: reuse whichever environment resolution succeeded in Step 1;
  if none succeeded there, this step inherits the same fallback (standalone
  repro script for the file-I/O layer) and the gap is documented rather
  than a false pass claimed.
- **Recovery if it fails**: Any failing test is triaged: if it's one of the
  new AC-1/AC-3/AC-6 tests, return to the relevant earlier step (2, 4, 5,
  or 6) to correct; if it's a previously-passing test that now fails, that
  is a regression — revert Steps 2/3/6 diffs via git and re-diagnose before
  reapplying (this would trigger a replan, not a silent workaround).
- **Reversibility/rollback**: Test execution only; any source revert uses
  Step 2/3/6's stated rollback (git revert/checkout).
- **Approval**: None required.

### Step 8 — Final validation & reconciliation (C-7)
- **Action**: Independently re-check each of AC-1..AC-6 against its stated
  pass condition (see Final Validation section below), reconcile against
  Unknowns U-1..U-3 (mark each resolved/still-open with a one-line reason),
  confirm Risks R-1..R-4 did not materialize, and review `git diff --stat`
  to confirm the total change set is limited to: `cli.py` (Step 2, and
  possibly Step 6's guarded stdout addition), any single-line fix from
  Step 3's audit (expected: none), the new fixture file (Step 4), and the
  new/extended test file(s) (Step 5).
- **Expected result**: A clear per-AC pass/fail record and a confirmed
  minimal, targeted diff with no unrelated changes.
- **Prerequisites**: Steps 1–7 complete.
- **Serves AC**: AC-1..AC-6 (closure check).
- **Planned commitment**: **C-7**.
- **Validation/pass condition**: See "Final Validation" section below.
- **Risk/mitigation**: None material — this is a read-only reconciliation
  step.
- **Recovery if it fails**: If any AC does not pass, do not close the task;
  loop back to the specific step that owns that AC (see AC-to-C matrix) for
  a targeted follow-up rather than reopening the whole plan.
- **Reversibility/rollback**: N/A.
- **Approval**: None required.

## Commitment Inventory

| ID | Commitment | Owning step |
| --- | --- | --- |
| C-1 | Add `encoding='utf-8'` to `cli.py`'s `--sql-file` `open()` call (confirmed bug fix) | Step 2 |
| C-2 | Audit `loader.py`/`writer.py`/`transaction.py`/`cli_logic.py`; confirm (or, only if genuinely missing, add) `encoding='utf-8'` | Step 3 |
| C-3 | Add new UTF-8 fixture `tests/test_data/unicode_sample.yaml` with Chinese-character data, not SQL-inserted | Step 4 |
| C-4 | Add new regression test(s) in `tests/test_crud.py` covering AC-1/AC-3/AC-6 (load→query→write-back→reload + `--sql-file` path) | Step 5 |
| C-5 | Run full existing + new test suite; confirm zero regressions (AC-5) | Step 1 (baseline) + Step 7 (final run) |
| C-6 | Verify CLI stdout output path for AC-4; add guarded UTF-8 stdout reconfiguration only if a real gap is proven | Step 6 |
| C-7 | Final validation & reconciliation across AC-1..AC-6, U-1..U-3, R-1..R-4, and diff scope | Step 8 |

## AC-to-Commitment Traceability Matrix

| AC | Implemented/validated by |
| --- | --- |
| AC-1 (load pre-existing UTF-8 fixture, query, no corruption) | C-2 (verify loader.py), C-3 (fixture), C-4 (test), C-7 (final check) |
| AC-2 (INSERT/UPDATE round-trip, no `\uXXXX` regression) | C-2 (verify writer.py), C-5 (existing tests re-run), C-7 |
| AC-3 (`--sql-file` UTF-8 SQL execution, confirmed bug) | C-1 (fix), C-4 (new test), C-7 |
| AC-4 (CLI stdout prints non-ASCII correctly) | C-6 (verify/guard), C-7 |
| AC-5 (existing ASCII-only suite unmodified pass) | C-5 (full suite run), C-7 |
| AC-6 (new regression test: load→query→write-back→reload) | C-3 (fixture), C-4 (test), C-7 |

## Risk / Rollback / Approval Analysis

- **Nature of change**: Local, source-controlled, single-repository code
  fix plus test/fixture additions. No destructive operations (no deletes of
  existing data, no schema/DB migrations, no network/external calls, no
  credentials/secrets touched).
- **Reversibility**: Every change in this plan (C-1, C-2's conditional
  fix, C-6's conditional fix, C-3, C-4) is a normal, small git diff, fully
  reversible via `git revert`/`git checkout -- <file>` on the affected
  file(s). No step produces an irreversible side effect.
- **Rollback trigger**: Any regression detected in Step 7 (a previously
  passing test now fails) or any AC failing at Step 8 reconciliation.
- **Rollback action**: `git revert` (or `git checkout -- <file>`) of the
  specific commit/diff introduced in Steps 2/3/6; re-run Step 7 to confirm
  the repository is back to the pre-change passing baseline.
- **Rollback verification**: Re-run the full suite (Step 1's baseline
  invocation) after rollback and confirm it matches the originally recorded
  baseline pass/fail state.
- **External dependencies/approvals**: None. No external service, no
  schema change, no production data, no security/privacy-sensitive surface
  (confirmed by intake's Security/Privacy constraint: no new attack
  surface, no `safe_load` bypass, no reads/writes outside user-specified
  paths — this plan does not touch any of that).
- **Human approval gate**: **None required.** This is a MEDIUM/LOW-risk,
  local, fully reversible bugfix with explicit test coverage added
  (C-4/C-5), no irreversible or destructive operation, no security/privacy
  impact, and no external/production system touched. The only situation
  that would escalate to requiring a pause/approval is if Step 7 or Step 8
  reveals the bug's scope is materially larger than the intake's assumption
  (i.e., R-2 materializes and `database.py`/`crud_handlers.py`/
  `transformer.py` genuinely need changes) — in that case this plan would
  require a **REPLAN**, not silent scope expansion, per DD_Planner
  adaptation rules.

## Final Validation

Before closure, independently verify all of AC-1..AC-6:

1. **AC-1/AC-6**: Run the new Step 5 test(s) that load the Step 4 fixture
   from disk, query it, mutate it, write it back, and reload it — assert
   the Chinese substring is byte-for-byte identical at every stage.
2. **AC-2**: Confirm `test_insert_unicode_characters` /
   `test_crud_unicode_content` (existing) still pass unmodified as part of
   the full suite run (Step 7).
3. **AC-3**: Run the new `--sql-file` regression test (Step 5) and, if
   feasible in the execution environment, an additional manual invocation
   of `yamlql sql --file <fixture> --sql-file <utf8_sql_file>` to visually
   confirm correct output — this directly exercises the exact reported bug
   location ([cli.py](../../../yamlql_library/cli.py#L111)).
4. **AC-4**: Result of Step 6's stdout capture check on the available
   OS(es) in this session; documented as "verified on <OS>" rather than
   claimed for untested platforms.
5. **AC-5**: `pytest` exit code 0 for the full existing suite as part of
   Step 7, with no test files under `tests/` modified other than the
   additions from Step 5 and (only if needed) fixture from Step 4.
6. **AC-6**: Covered jointly by items 1 and the fixture's "not SQL-inserted,
   pre-existing on disk" property from Step 4 — satisfies the intake's
   explicit distinction from the existing SQL-INSERT-based unicode tests.

**Execution note for DD_Executor**: Attempt the real test suite first via
the project's configured Python environment/tooling (do not hand-roll a
raw `python -m pytest` against an arbitrary interpreter — resolve/configure
the project's environment first, per U-1's documented difficulty in the
intake investigation). Only fall back to a standalone repro script
(exercising `loader.py`/`writer.py`/`cli.py` directly without duckdb/pandas)
if the full suite genuinely cannot be executed in any available
environment, and clearly label any AC verified only via the fallback script
as such in the final report — do not represent fallback-script results as
equivalent to a full-suite pass.

## Unknowns / Risk disposition carried into execution

- **U-1** (DuckDB round-trip from a loaded, not inserted, fixture): Directly
  resolved by Step 5's AC-6 test once run in a working environment (Step 1/
  7). If it reveals a genuine DuckDB/pandas-layer encoding defect, this
  triggers R-2 → REPLAN (scope would expand beyond this plan's file list).
- **U-2** (do existing unicode tests currently pass): Resolved by Step 1's
  baseline run.
- **U-3** (non-BMP/emoji/RTL/combining scripts): Out of scope for this
  plan's mandatory AC set (not required by any AC-1..AC-6); optionally
  extend the Step 4 fixture with an emoji value as a low-cost bonus check,
  but do not block closure on it — it is not a Definition-of-Done item.
- **R-1** (breaking ASCII tests via incorrect encoding changes): Mitigated
  by the single-keyword-argument diff style in C-1/C-2 and the full-suite
  re-run in C-5.
- **R-2** (DuckDB/pandas layer has its own defect): Mitigated by explicit
  AC-6 test (C-4) before declaring completion; if it fires, escalate to
  REPLAN rather than expanding scope silently.
- **R-3** (platform console/code-page risk): Mitigated by evidence-gated
  C-6 rather than an unverified blanket fix; documented per-OS coverage
  limits in Step 8/Final Validation.
- **R-4** (UTF-8-only assumption for SQL files): Accepted per intake as a
  reasonable minimal default; no further mitigation planned (matches
  existing codebase convention).
