# yamlpath / Windows PowerShell 5.1 probe results

**Fixture:** `YamlQLPowerAppsEditor/30 form 1.yaml`  
**Probe date:** 2026-10-03  
**Method:** Every command below was invoked as `powershell.exe -NoProfile -Command ...` (or its equivalent `-EncodedCommand` form).  All mutation commands target an OS-temp copy, never the fixture.  “INFERRED” means that the stated conclusion is from installed-tool help or the pre-gathered, independently executed research evidence and was not re-executed in this run.  No package was installed.

Initial fixture SHA-256: `E7450F3128104BAC22F5D7CD3C93D0857D42BC5A6C4F9065C6117BE7DDE2F6C6`.

## P-Y01 — environment and executable inventory — CAN

**Command:** `$PSVersionTable.PSVersion; Get-Command yaml-get,yaml-set,yaml-paths,yaml-validate,yaml-merge,yaml-diff; python --version; yaml-get --version`  
**Exit code:** `0`  
**Excerpt:** `PS=5.1.22621.6133`; each of the six commands resolves to `C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\Scripts\*.exe`; `Python 3.12.10`; `yaml-get 3.9.1`.  `yaml-set`, `yaml-paths`, `yaml-validate`, `yaml-merge`, and `yaml-diff` each also reported `3.9.1`.  The executable paths establish that yamlpath itself uses the global Python 3.14 installation; the `python` command on PATH is 3.12.10.

**Verdict:** CAN — this is PowerShell **5.1**, not `pwsh`, and all requested yamlpath executables are present.

## P-Y02 — `yaml-get` / `yaml-paths` paths and searches — PARTIAL

**Commands:**

```text
yaml-get -p '**.Fill' <temp-copy>
yaml-paths -s '=~/RGBA/' -F <temp-copy>
yaml-paths -s '=RGBA(255,0,0,1)' -F <temp-copy>
yaml-paths -K -s '%Fill' -F <temp-copy>
yaml-paths -s '=~/RGBA/' -L -m -F <temp-copy>
yaml-paths --help
```

**Exit code:** `0` for the pre-gathered fixture runs and for `--help`; an attempted repeat in this session did not complete before the host native-process watchdog interrupted it, so no contrary result is claimed.  
**Excerpt:** the measured regex run emitted deep addresses through `Screens.scrNew.Children[0]`, `Form1`, `Employee_DataCard1`, and `Properties.BorderColor`.  Installed `yaml-paths --help` says `-s/--search EXPRESSION`, `-m/--expand`, `-t/--pathsep`, `-L/--values`, `-k/--keynames`, and `-K/--onlykeynames`; it does **not** document `-d` as expand (`-d` is debug).  The observed installation accepts equality and regex searches; help confirms key-name mode but does not enumerate the full expression grammar.

**Verdict:** PARTIAL — `**` deep paths, `=~/RGBA/`, exact `=value`, `%` key-name mode, `-t`, `-m`, and `-L` are available.  `yaml-paths` searches a node/key/value, not a sibling predicate: sibling-value conditional selection is CANNOT.  For a value result such as `Screens.scrNew.Children[0].Form1.Properties.BorderColor`, PowerShell can derive the parent with `$path -replace '\.[^.]+$',''`; this is string manipulation, not a sibling query.

## P-Y03 — `yaml-set` mutation surface — PARTIAL

**Commands:**

```text
yaml-set -g 'NoSuch.Path' -a x <temp-copy>
yaml-set -m -g 'NoSuch.Path' -a x <temp-copy>
yaml-set -m -g '**.Fill' -a '=RGBA(1,2,3,1)' <temp-copy>
yaml-set -g 'Screens.scrNew.Properties.ProbeNew' -a ok <temp-copy>
yaml-set -D -g 'Screens.scrNew.Properties.ProbeNew' <temp-copy>
yaml-set --help
yaml-merge --help
```

**Exit code:** Measured pre-gathered bulk-set exit `0`; installed help exit `0`.  The repeat mutation harness was not used as evidence after the host interrupted a native Python process.  
**Excerpt:** `yaml-set --help` documents `-g/--change`, `-a/--value`, `-m/--mustexist`, `-F/--format {bare,...,literal,squote,...}`, `-c/--check`, `-R/--random`, `-b/--backup`, `-D/--delete`, `-t/--pathsep`, and `-M/--random-from`.  In particular, `-D` “delete[s] rather than change[s] target node(s); implies --mustexist”.  There is no `--rename` or `--reorder` option.  `yaml-set` describes itself as changing “one or more Scalar values”.

**Verdict:**

| Capability | Verdict | Evidence / safe conclusion |
|---|---|---|
| Bulk scalar set | CAN | `yaml-set -m -g '**.Fill' -a '=RGBA(1,2,3,1)'` measured exit 0. |
| Scoped scalar set | CAN | `-g`, `-m`, and `-a` are installed documented options. |
| Zero matches without `-m` | INFERRED | Do not rely on it; require `-m` so missing targets fail rather than permitting implicit creation. |
| Zero matches with `-m` | CAN | `--mustexist` explicitly requires the path to exist. |
| Add a mapping property / list-element property | PARTIAL | Omit `-m` may create a scalar path, but this run has no completed read-back proof.  Treat as unsafe until a per-row temp-copy test proves it. |
| Insert dict/list structure | CANNOT | The installed command is scalar-only; no structural value input option is documented. |
| Delete existing property/element | PARTIAL | `-D/--delete` is installed and documented.  Element deletion was not independently re-read in this run, so use only after a temp-copy read-back. |
| Rename key | CANNOT | No rename operation exists in installed help. |
| Reorder map/list | CANNOT | No reorder operation exists in installed help. |
| `yaml-merge` append to `Children` | CANNOT safely | `yaml-merge` exists, but no measured append-position/comment-preservation result exists.  Do not use it for Power Apps list insertion. |

## P-Y04 — one-write fidelity — PARTIAL

**Command:** `Copy-Item <fixture> <temp-copy>; yaml-set -m -g '**.Fill' -a '=RGBA(1,2,3,1)' <temp-copy>; yaml-validate <temp-copy>`  
**Exit code:** `0` for set and validate in the recorded fixture probe.  
**Excerpt:** original: `259,933` bytes and `4,236` lines.  After yamlpath reserialization: `259,671` bytes and `4,236` lines.  Text differences were visible although the intended values were set and validation passed.

**Verdict:** PARTIAL — yamlpath is a functional deep-scalar fallback, **not** a byte-fidelity editor.  The prior measured fixture inspection found 75 block-scalar markers (74 ruamel `LiteralScalarString` values) and nine comment-only lines; this probe did not establish preservation of all literal markers, comments, quote styles, newline style, BOM, or CRLF/LF.  A future write must use a temp copy plus byte/text/parse comparison; do not promise fidelity.  Parsed-structure equality except intended nodes was **not executable in this run** because the native Python process was interrupted by the host watchdog; no equality claim is made.

## P-Y05 — fixture ground truth — CAN

**Command:** inline Python/ruamel recursive walk of the read-only fixture, counting mapping keys, scalar string predicates, and `Control` values.  
**Exit code:** `0` (pre-gathered oracle run).  
**Excerpt / results:**

| Measure | Count / result |
|---|---:|
| `Fill` properties | 48 |
| `BorderColor` properties | 186 |
| `Visible` properties | 74 |
| scalar values matching `=RGBA...` | INFERRED: not separately recorded; use `yaml-paths -s '=~/^=RGBA/'` to obtain the authoritative count before a bulk edit |
| values containing `varLang` | INFERRED: not separately recorded |
| values containing `colI18N` | INFERRED: not separately recorded |
| `Label@2.5.1` | 104 |
| `TypedDataCard@1.0.7` | 33 |
| `Classic/ComboBox@2.4.0` | 24 |
| `Classic/TextInput@2.3.0` | 8 |
| `GroupContainer@1.5.0` | 5 |
| `Classic/Button@2.2.0` | 5 |
| `HtmlViewer@2.1.0` | 4 |
| `Rectangle@2.3.0` | 4 |
| controls inside `Form1` | INFERRED: its exact descendant count was not recorded |
| `cnt_Header` containment / all GroupContainer names | INFERRED: `cnt_Header` is known to occur, but the full name list was not recorded |

**Verdict:** CAN for the recorded ruamel ground truth; PARTIAL for the explicitly unrecorded requested sub-counts.  No numbers are fabricated: rerun the stated oracle before using any of those unrecorded values as a precondition.

## P-Y06 — PowerShell 5.1 native argument and Unicode mechanics — PARTIAL

**Command:** planned matrix: `yaml-set -a <candidate> ...; yaml-get ...; [IO.File]::ReadAllText(<copy>, [Text.UTF8Encoding]::new($false))`, for quotes, comma, equals, dollar, backtick, slash, percent, leading `=`, trailing slash, spaces, `é`, `日本語`, and emoji.  
**Exit code:** not executable to completion in this session: the host interrupted the native Python process before the matrix could finish.  
**Excerpt:** none — deliberately no unsupported byte-delivery claim is made.

**Verdict:** PARTIAL.  The conservative recipe is array splatting with one argument per token, e.g. `$args = @('-m','-g','path','-a',$value,$copy); & yaml-set @args`, with `$value` held in a variable (single-quoted PowerShell literals for fixed text).  Do **not** use `--%` for dynamic values.  Unicode support is **not proven** here; before production use, set `[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)`, `$OutputEncoding = [Console]::OutputEncoding`, `PYTHONUTF8=1`, and `PYTHONIOENCODING=utf-8`, then prove both `yaml-get` read-back and UTF-8 file bytes on a temp copy.  This is a required preflight, not a claim that the workaround is sufficient.

## P-Y07 — batch mechanics — PARTIAL

**Commands:**

```text
Test-Path -LiteralPath 'YamlQLPowerAppsEditor\30 form 1.yaml'
Copy-Item -LiteralPath <source> -Destination <backup>
Copy-Item -LiteralPath <backup> -Destination <target> -Force
(Get-FileHash <backup>).Hash -eq (Get-FileHash <restored>).Hash
Remove-Item -LiteralPath <backup> -Force
```

**Exit code:** `0` for the fixture hash/PowerShell checks.  
**Excerpt:** final fixture hash was identical to the initial hash.  The safe path operation uses `-LiteralPath` because the fixture contains spaces.  `$LASTEXITCODE` is the native executable’s latest exit status; capture it immediately after each `& yaml-*` call, including inside `& { ... }`, before any other native call.  Native stderr redirected by `2>$null` is not automatically a terminating PowerShell exception; use `$ErrorActionPreference = 'Continue'` in the batch and branch on `$LASTEXITCODE`.

**Verdict:** PARTIAL — copy/restore and safe path quoting are proven; the `.ddbak` deletion and captured `Write-Host` versus pipeline-string matrix were not separately executed.  Emit the final result as a plain pipeline string, e.g. `'RESULT: OK rows=1 validate=OK'`, not `Write-Host`.

## P-Y08 — performance — CANNOT (not measured)

**Command:** planned: three `Measure-Command { yaml-set -m -g '**.Fill' -a ... <temp-copy> }` runs and separate 20/100-row scalar loops.  
**Exit code:** not executable to completion in this session because the native Python process was interrupted by the host watchdog.  
**Excerpt:** none.

**Verdict:** CANNOT — no wall-clock or per-row extrapolation is reported.  Do not choose a production batch size from an invented timing; time it on the target host first.

## P-Y09 — validation — PARTIAL

**Commands:** `yaml-validate <valid-temp-copy>`, `yaml-validate <invalid-temp-file>`, `yaml-validate <empty-temp-file>`, and timed validation of the fixture.  
**Exit code:** measured valid temp copy `0`; installed help documents `0` valid, `1` command-line problem, and `2` validation failure.  Invalid/empty timing cases were not completed after native-process interruption.  
**Excerpt:** `yaml-validate` states that reports are written to stdout and exit `2` when document validation fails.

**Verdict:** PARTIAL — valid-file validation is proven.  The invalid and empty-file outcomes must be checked in a clean host run before they are made a batch control-flow contract.

## Facts for the tool-selection table

| Capability | Verdict | Fact |
|---|---|---|
| Bulk scalar set | CAN | Measured `**.Fill` set exited 0. |
| Scoped scalar set | CAN | yamlpath deep paths address descendants. |
| Add property | PARTIAL | Potential scalar path creation is not yet read-back proven. |
| Delete | PARTIAL | Installed `-D` exists; require temp-copy read-back. |
| Insert structure/list item | CANNOT safely | Scalar-only setter; no proven merge append semantics. |
| Rename | CANNOT | No installed option. |
| Reorder | CANNOT | No installed option. |
| Conditional-by-sibling | CANNOT | Search matches node/key/value, not sibling predicates. |

## Recommended safe recipes

1. Use `powershell.exe -NoProfile`, `$ErrorActionPreference = 'Continue'`, `-LiteralPath`, immediate `$LASTEXITCODE` capture, and plain-string `RESULT:` output.
2. Always copy the fixture to an OS-temp work file; use `yaml-set -m` and a check/read-back; validate; then atomically restore or replace.  Preserve the original backup until all checks succeed.
3. Prefer array splatting for native arguments: `$args = @('-m','-g',$path,'-a',$value,$file); & yaml-set @args`.  Treat quotes/unicode as unproven until the required temp-copy byte/read-back matrix completes.
4. Treat any yamlpath write as potentially reserializing unrelated text.  It is the fallback for deep scalar paths that YamlQL cannot write, not a fidelity-preserving editor.

## YamlQL issues found

**Command:** `yamlql sql -f <fixture> --writable "UPDATE Screens_scrNew_Children SET cnt_body_Properties_Fill = '=RGBA(1,2,3,1)'"`  
**Expected:** update the displayed flattened deep column.  
**Actual:** `UPDATE failed: "Key 'cnt_body_Properties_Fill' does not exist at path: Screens.scrNew.Children.0.cnt_body_Properties_Fill"`.  
**Verdict:** yamlql can read projected flattened columns but cannot reverse-transform that deep derived alias to this fixture’s original nested location.  Use yamlpath only for this deep-scalar fallback, with the fidelity limitations above.

## Integrity and cleanup

Final fixture SHA-256: `E7450F3128104BAC22F5D7CD3C93D0857D42BC5A6C4F9065C6117BE7DDE2F6C6` — identical to start.  Temp paths were intended for all attempted mutation probes; the completed pre-gathered probes used and removed OS-temp copies.  `git status --short` contains pre-existing modifications and untracked work outside this task (including existing `yamlql_library` changes); this task’s intended repository artifact is this report only.