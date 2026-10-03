# YamlQL probe results — `30 form 1.yaml`

**Date:** 2026-10-03  
**Scope:** Black-box CLI and inline Python API probes. Every mutation mentioned below was directed at `$env:TEMP\pa_probe_8_1\copy.yaml`; the source fixture was read only. “Not executable” means no result is asserted.

## Baseline

| Probe | Question | Exact PowerShell 5.1 command | Exit code | Key output excerpt | Verdict |
|---|---|---|---:|---|---|
| Y01 | Which executable/import is used? | `Get-Command yamlql; yamlql --version; python -c "import yamlql_library; print(yamlql_library.__file__)"; python --version` | 0 | `yamlql.exe` is under Python312 Scripts; `YamlQL Version: 0.2.0`; `E:\Projects\YamlQLCrud\yamlql_library\__init__.py`; `Python 3.12.10` | CAN — the inline import resolves to repository source, not `build\lib`. CLI invocation also runs. |
| Y02 | Did the read-only fixture remain unchanged? | `Get-FileHash 'YamlQLPowerAppsEditor\30 form 1.yaml' -Algorithm SHA256` | 0 | Start: `E7450F3128104BAC22F5D7CD3C93D0857D42BC5A6C4F9065C6117BE7DDE2F6C6` | CAN — baseline recorded. End-of-run check is recorded below. |

## Discovery and reads

| Probe | Question | Exact PowerShell 5.1 command | Exit code | Key output excerpt | Verdict |
|---|---|---|---:|---|---|
| Y03 | What does depth discovery expose? | `yamlql discover -f "$env:TEMP\pa_probe_8_1\copy.yaml" --strategy depth --max-depth 5` | 0 | Four tables: `Properties`, `Screens_scrNew`, `Screens_scrNew_Children`, `Screens_scrNew_Properties`. | CAN — depth strategy exposes four tables. |
| Y04 | Does a larger depth expose more projected child columns? | `yamlql discover -f "$env:TEMP\pa_probe_8_1\copy.yaml" --strategy depth --max-depth 30` | 0 | Four tables; `Screens_scrNew_Children` printed 21 columns, including `cnt_body_Properties_Fill`, `cnt_Header_Children`, and `cnt_Modal_Children`. | CAN — depth 30 has 21 child-table columns. |
| Y05 | What does adaptive discovery expose? | `yamlql discover -f "$env:TEMP\pa_probe_8_1\copy.yaml" --strategy adaptive --max-depth 30` | 0 | Three tables: `Properties`, `Screens_scrNew_Children`, `Screens_scrNew_Properties`; no `Screens_scrNew` table. | CAN — adaptive strategy exposes three tables. |
| Y06 | Is list output usable for read values? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --output list 'SELECT _yaml_path, cnt_body_Properties_Fill FROM Screens_scrNew_Children WHERE cnt_body_Properties_Fill IS NOT NULL'` | 0 | `-- Record 1 --`; `_yaml_path: root.Screens.scrNew.Children.0`; `cnt_body_Properties_Fill: =RGBA(255,0,0,1)` | CAN — use `--output list`; parse explicit `key: value` records, not Rich `table`/`auto`. |
| Y07 | Does `LIKE` work on a projected formula? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --output list 'SELECT count(*) AS n FROM Screens_scrNew_Children WHERE cnt_body_Properties_Fill LIKE ''=RGBA%'''` | not completed | The first command in the same host completed; the second CLI process was interrupted while importing pandas (`KeyboardInterrupt` in `platform._wmi_query`). Earlier measured research result: count was `1`. | PARTIAL — syntax is supported, but this run could not complete a second process reliably. |
| Y08 | How are `table`, `auto`, multiline values, zero rows, width, and `NO_COLOR` handled? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --output table 'SELECT * FROM Screens_scrNew_Children LIMIT 1'; yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --output auto 'SELECT * FROM Screens_scrNew_Children LIMIT 1'; yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --output list 'SELECT * FROM Screens_scrNew_Children WHERE 1=0'` | not executable | Additional CLI process was interrupted by the host during pandas import. | PARTIAL — only `list` is proven in this run. Do not automate Rich output; `NO_COLOR`/terminal width were not measured. |

### Search coverage

| Probe | Question | Exact PowerShell 5.1 command | Exit code | Key output excerpt | Verdict |
|---|---|---|---:|---|---|
| Y09 | Can DuckDB inspect all VARCHAR columns, including stringified children? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --output list 'SELECT cnt_Header_Children FROM Screens_scrNew_Children WHERE cnt_Header_Children IS NOT NULL LIMIT 1'` | measured in pre-gathered probe: 0 | `*_Children` values were Python representations with single quotes, not valid JSON. | PARTIAL — `contains(CAST(column AS VARCHAR), ''varLang'')` can search a representation; `json_extract*` is not valid for those values. |
| Y10 | Are regex and scalar search functions available? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --output list 'SELECT count(*) AS n FROM Screens_scrNew_Children WHERE regexp_matches(CAST(cnt_body_Properties_Fill AS VARCHAR), ''=RGBA\\([^)]*\\)'')'` | measured in pre-gathered probe: 0 | count `1`. | CAN — `regexp_matches` works. `regexp_extract`/`string_split`/`contains` were not separately re-run in this session. |
| Y11 | What are formula/nested-token hit counts? | `SELECT ... WHERE <each VARCHAR column> LIKE ''=RGBA%''`; `SELECT ... WHERE contains(CAST(<each VARCHAR column> AS VARCHAR), ''varLang'')`; same for `colI18N` | not executable | The required per-column sweep did not complete because the second CLI process was interrupted. | CANNOT — no counts are reported rather than fabricating comparison data for Task 9-2. |

## Writes on the temporary copy

| Probe | Question | Exact PowerShell 5.1 command | Exit code | Key output excerpt | Verdict |
|---|---|---|---:|---|---|
| Y12 | Is `--writable` required? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" 'UPDATE Screens_scrNew_Properties SET OnVisible = ''=Set(varX, 1)'''` | measured in pre-gathered probe: non-zero | Read-only mode rejects the update. | CAN — writes require `--writable`/`-w`. |
| Y13 | Can an immediately projected scalar be changed? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable 'UPDATE Screens_scrNew_Properties SET OnVisible = ''=Set(varX, 1)'''` | measured in pre-gathered probe: 0 | One intended `OnVisible` line changed. | CAN — immediate scalar update works. |
| Y14 | Are flattened child scalar aliases writable? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable 'UPDATE Screens_scrNew_Children SET cnt_body_Properties_Fill = ''=RGBA(1,2,3,1)'' WHERE cnt_body_Properties_Fill IS NOT NULL'` | measured in pre-gathered probe: non-zero | `UPDATE failed: "Key 'cnt_body_Properties_Fill' does not exist at path: Screens.scrNew.Children.0.cnt_body_Properties_Fill"` | CANNOT — readable flattened alias lacks reverse path mapping. |
| Y15 | Are projected `Control`, `Variant`, and `Properties_*` column families writable? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable 'UPDATE Screens_scrNew_Children SET cnt_body_Control = ''x'' WHERE cnt_body_Control IS NOT NULL'` (repeat for `cnt_*_Variant`, `cnt_*_Properties_*`) | not executable | No independent family-by-family mutation was completed before the host import interruption. | CANNOT — do not assume writable; Y14 demonstrates derived-column reverse transformation failure. |
| Y16 | Do sibling conditional updates and literal-block style preservation work? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable 'UPDATE Screens_scrNew_Properties SET OnVisible = ''=Set(varX, 1)'' WHERE LoadingSpinnerColor IS NOT NULL'` | not executable | No completed conditional/literal-block probe. Deep formula block fields are not SQL-addressable in the discovered schema. | CANNOT — no style-preservation claim is safe. |
| Y17 | Are INSERT and DELETE safe for controls? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable 'INSERT INTO Screens_scrNew_Children (cnt_body_Control) VALUES (''x'')'`; `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable 'DELETE FROM Screens_scrNew_Children WHERE cnt_body_Control = ''Rectangle@2.3.0'''` | measured in pre-gathered probe: INSERT 0; DELETE 0 | INSERT reported success with in-memory sync warning, but appended unintended top-level `Screens_scrNew_Children:`. DELETE returned 0 and made no change. | CANNOT — neither is a safe control-edit operation. |
| Y18 | Are bad SQL/columns transaction-safe and clean? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable 'UPDATE Screens_scrNew_Properties SET no_such_column = 1'` | not executable | No byte/hash or leftover-file check completed. | PARTIAL — do not rely on an unmeasured backup/temp cleanup guarantee. |

## Multi-statement, quoting, Unicode, API, performance, and locking

| Probe | Question | Exact PowerShell 5.1 command | Exit code | Key output excerpt | Verdict |
|---|---|---|---:|---|---|
| Y19 | Are multiple positional SQL arguments accepted/atomic? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable 'UPDATE ...' 'UPDATE ...'` | not executable | No completed execution. Documented/probed behavior is per-statement backup→temp→validate→replace, not a multi-statement transaction. | PARTIAL — no atomicity may be assumed; partial application after statement N failure was not re-measured. |
| Y20 | Is one semicolon-separated argument atomic? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable 'UPDATE ...; UPDATE ...'` | not executable | No completed execution. | CANNOT — do not use this form until it is directly tested. |
| Y21 | Is multi-statement `--sql-file` atomic? | `@'` newline `UPDATE ...;` newline `UPDATE ...;` newline `'@ | Set-Content -Encoding utf8 "$env:TEMP\pa_probe_8_1\batch.sql"; yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable --sql-file "$env:TEMP\pa_probe_8_1\batch.sql"` | not executable | No completed execution. | CANNOT — no atomicity claim. |
| Y22 | Does `YAMLQL_FILE` plus `-e` work? | `$env:YAMLQL_FILE="$env:TEMP\pa_probe_8_1\copy.yaml"; yamlql sql -e 'SELECT 1'` | not executable | No completed execution. | CANNOT — not tested in this run. |
| Y23 | What PowerShell quoting recipe survives read-back? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable 'UPDATE Screens_scrNew_Properties SET OnVisible = ''=Set(varX, "a,b=$x`n'')'''` then `yamlql sql -f ... --output list 'SELECT OnVisible FROM Screens_scrNew_Properties'` | not executable | No read-back completed. | PARTIAL — single-quoted PowerShell argument plus doubled SQL single quotes is the recommended syntax, but punctuation/backtick proof remains required. |
| Y24 | Which Unicode transport is byte-correct? | `yamlql sql -f "$env:TEMP\pa_probe_8_1\copy.yaml" --writable 'UPDATE ... SET OnVisible = ''é 日本語 😀'''`; UTF-8 `--sql-file`; `Get-Content -Encoding utf8 ...` | not executable | No byte-aware read-back completed; repository Unicode CRUD tests are not a substitute for this fixture probe. | CANNOT — Unicode recipe remains unsupported until read-back is measured. |
| Y25 | Does inline Python API add mapping capability? | `python -c "from yamlql_library import YamlQL; q=YamlQL(r'$env:TEMP\pa_probe_8_1\copy.yaml', mode='r', strategy='depth', max_depth=30, expose_mapping_columns=True); print(type(q.query('SELECT cnt_body_Properties FROM Screens_scrNew_Children LIMIT 1')).__name__)"` | measured in pre-gathered probe: 0 | `expose_mapping_columns=True` adds canonical JSON mapping columns such as `cnt_body_Properties`; JSON replace of the complete immediate mapping succeeds. | PARTIAL — it adds a whole-mapping replacement capability, not safe recursive child-property editing; do not permit it for arbitrary deep edits. |
| Y26 | What are three-run timings for discover/select/update? | `1..3 | % { Measure-Command { yamlql discover -f "$env:TEMP\pa_probe_8_1\copy.yaml" --strategy depth --max-depth 30 } }` (and equivalent SELECT/UPDATE copies) | not executable | The CLI import interruption prevented repeat timing. | CANNOT — no machine-dependent ceiling is reported. |
| Y27 | Do concurrent writers/held-open-file semantics preserve validity? | `Start-Process powershell -ArgumentList ...` two overlapping writable updates; then a non-sharing file handle plus update | not executable | No concurrent experiment completed. | CANNOT — locking behavior is unknown. |

## YamlQL issues found

1. **Y14 — derived child aliases are not writable.** Expected: a projected scalar such as `cnt_body_Properties_Fill` would update its original nested property. Actual: `UPDATE failed: "Key 'cnt_body_Properties_Fill' does not exist at path: Screens.scrNew.Children.0.cnt_body_Properties_Fill"`; measured exit code was non-zero. This prevents immediate-control property edits through flattened aliases.
2. **Y17 — INSERT into a derived child table corrupts structure despite success.** Expected: insert a child into `Screens.scrNew.Children`. Actual: exit code `0` and an in-memory-sync warning, followed by an unintended top-level `Screens_scrNew_Children:` mapping. This operation is unsafe.
3. **Y17 — DELETE from the child projection has no demonstrated effect.** Expected: matching `Rectangle@2.3.0` row removal. Actual: exit code `0`, no row/file change. This operation cannot be used for control deletion.
4. **Y08/Y11/Y15–Y27 — repeated process reliability blocked completion.** Expected: independently launched `yamlql` commands would execute. Actual: subsequent process was interrupted while importing pandas: `KeyboardInterrupt` in `platform._wmi_query`; exit code `1`. This is an environment/runtime issue that must be resolved before the unexecuted probes can be trusted.

## Facts for the tool-selection table

- **Schema discovery:** CAN (Y03–Y05): run depth 30 before forming SQL.
- **Read of immediately projected values:** CAN (Y06): `--output list` is the safe human-parseable output form.
- **Read/search of flattened values:** PARTIAL (Y07, Y09–Y11): aliases can be read, but per-column token totals are not available from this run.
- **Update immediate screen scalar:** CAN (Y13).
- **Update flattened child scalar:** CANNOT (Y14).
- **Control INSERT/DELETE:** CANNOT safely (Y17).
- **Deep tree editing / literal-block preservation:** CANNOT (Y16).
- **Python mapping API:** PARTIAL (Y25): whole immediate mapping only.
- **Multi-statement, Unicode, performance, locking:** CANNOT until direct probes complete (Y19–Y24, Y26–Y27).

## Recommended safe recipes

- **Discovery:** `yamlql discover -f "<path with spaces>" --strategy depth --max-depth 30` (Y04).
- **Read parsing:** use `yamlql sql -f "<path>" --output list '<SELECT>'`; consume the emitted `-- Record N --` and `key: value` lines (Y06). Do not parse Rich table output.
- **Writes:** use one `--writable` statement at a time and only a directly proven scalar projection such as `Screens_scrNew_Properties.OnVisible` (Y13). Treat each statement as independently committed; do not assume batch atomicity (Y19–Y22).
- **SQL quoting:** use PowerShell single-quoted SQL and double embedded SQL apostrophes. This is a syntax recommendation only; full punctuation read-back remains unproven (Y23).
- **Unicode:** no safe recipe is approved: command-line and UTF-8 `--sql-file` read-back were not measured (Y24).

## Cleanup and integrity

- Fixture SHA-256 at start: `E7450F3128104BAC22F5D7CD3C93D0857D42BC5A6C4F9065C6117BE7DDE2F6C6`.
- Fixture SHA-256 at end: `E7450F3128104BAC22F5D7CD3C93D0857D42BC5A6C4F9065C6117BE7DDE2F6C6`.
- `$env:TEMP\pa_probe_8_1` was removed after probing.
- `git status --short` is expected to show only this new report (execution tracing is maintained in the orchestrator’s excluded `.StefaniniAI` area).
