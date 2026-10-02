# System Architecture

*Last Updated: 2026-10-02*
*Confidence Score: 100%*

---

## High-Level Landscape

- **Architecture Type**: Layered, in-process Python library/CLI with an in-memory relational projection and transactional file-write adapter.
- **Primary Tech Stack**: Python 3.9+, PyYAML, ruamel.yaml, pandas, DuckDB, sqlglot, Typer, Rich, prompt-toolkit, pytest, setuptools, and MkDocs.
- **System of Record**: The input YAML file; DuckDB is disposable query state.
- **Entry Points**:
  - Python API: `yamlql_library.YamlQL`.
  - Console script: `yamlql`, mapped by `pyproject.toml` to `yamlql_library.cli:main`.
  - CLI commands: `yamlql sql` and `yamlql discover`; direct, SQL-file, and interactive execution are supported.
- **Scope**: `yamlql_library/` is authoritative. `build/` is a stale generated copy and was explicitly excluded.

YamlQL has two related but different pipelines. The read path uses safe semantic loading and creates a lossy relational projection. The write path reloads the source independently with ruamel.yaml round-trip nodes. `_yaml_path` metadata connects projected rows to mutable YAML nodes; it is not a complete concrete-syntax model.

---

## System Topology

### Components

1. **`YamlQL` facade** — `yamlql_library/__init__.py`
  - Validates mode; composes loader, transformer, and database; exposes `query()`/`list_tables()`; fully reloads derived state after successful writes.

2. **`YamlLoader` input adapter** — `yamlql_library/loader.py`
  - Reads UTF-8 text, normalizes separator lines, quotes selected YAML 1.1 boolean-like keys at column zero, and invokes PyYAML `load_all()` with a dedicated `_ApplicationTagSafeLoader` subclass.
  - The dedicated loader accepts application-defined scalar, sequence, and mapping tags as their underlying safe values while explicitly rejecting fallback construction for the `tag:yaml.org,2002:` namespace. The global PyYAML `SafeLoader` is not modified.
  - Multiple mapping documents are shallow-merged; later top-level keys win. Document boundaries and non-mapping documents are still lost from the SQL query model.

3. **`DataTransformer` relational projector** — `yamlql_library/transformer.py`
  - Recursively creates pandas DataFrames using depth or adaptive strategy.
  - Flattens mappings, extracts homogeneous nested lists of objects, handles scalar lists, sanitizes names, tracks reverse name maps, and adds `_yaml_path`.
  - Nested-list extraction is based on the first record and requires the path in every record. Mixed mapping/scalar lists are not projected. Sanitization is collision-prone because spaces, dots, and hyphens all become underscores.

4. **`Database` query gateway/router** — `yamlql_library/database.py`
  - Registers DataFrames in an in-memory DuckDB connection.
  - Executes SELECT/DDL directly and routes INSERT/UPDATE/DELETE to handlers after write-mode checks.

5. **`SqlInterceptor` classifier** — `yamlql_library/sql_interceptor.py`
  - Uses sqlglot's DuckDB dialect to classify one statement as SELECT, INSERT, UPDATE, DELETE, DDL, or UNKNOWN.

6. **CRUD command handlers** — `yamlql_library/crud_handlers.py`
  - `InsertHandler` restores names/builds values with `ReverseTransformer`, resolves the mapping document that owns the target table, and uses `YamlWriter.append_item()` for list-backed tables. Root-list INSERT remains single-document only.
  - `UpdateHandler` queries matching DuckDB rows, uses `_yaml_path` plus column maps, resolves the owning document, calls `set_value()` for fields, and uses guarded `set_root()` only for a single-document root update.
  - `DeleteHandler` queries `_yaml_path`, sorts list indices highest-first, and calls document-resolving `YamlWriter.delete_value()`.
  - Writer policy exceptions are converted to contextual INSERT/UPDATE/DELETE failures inside the transaction, causing rollback before any DuckDB synchronization attempt. A later DuckDB synchronization failure still leaves an already-committed YAML file authoritative and returns a reload warning.
  - One SQL write statement is one file transaction.

7. **`ReverseTransformer` helper** — `yamlql_library/reverse_transformer.py`
  - Restores original names and reconstructs nested dict/list values, chiefly for INSERT. It cannot recover stream/concrete-syntax information discarded by the read projection.

8. **`YamlWriter` format-aware stream mutation adapter** — `yamlql_library/writer.py`
  - Uses byte-based load/render/write APIs and owns a detected `FormatProfile`: mapping/sequence indentation and offset, LF/CRLF, UTF-8 BOM, explicit document start, and exact trailing-newline suffix.
  - Exposes `documents`, `doc_count`, `find_document()`, backwards-compatible first-document `data`, `render()`, `write_to()`, `write()`, `append_item()`, guarded `set_root()`, and `doc=` addressing on path operations.
  - Uses parser-event document spans and per-document source splicing. Unchanged documents and stream framing remain source bytes; a dirty document alone is rendered and replaced while preserving the stream tail newline.
  - Uses top-level block restoration and source-range list deletion/append where round-trip dumping alone cannot preserve exact layout or comment ownership. Every source-range mutation reparses the updated bytes before another mutation uses line data.
  - Preserves/adopts quote, block-scalar, flow/block, comment, tag, anchor, alias, and merge metadata under explicit policies. `YamlWriterPolicyError`, `AnchorInUseError`, `MergedKeyError`, and `NodeKindChangeError` make unsafe operations explicit.
  - Blocks mapping/sequence/scalar kind changes by default; only direct writer callers can opt in with `allow_kind_change=True`.

9. **`TransactionManager` file unit of work** — `yamlql_library/transaction.py`
  - Copies a `.backup`, mutates in memory, serializes through the transaction writer's `write_to()` to a same-directory temp file, validates all documents through ruamel `load_all()`, verifies document count, atomically replaces with `os.replace()`, and removes the backup.
  - Performs a semantic comparison with the backup to detect direct mutation of `writer.data`/`writer.documents` that bypassed writer methods, then marks the affected document(s) dirty.
  - Rollback restores the backup after handler or commit errors.

10. **CLI presentation** — `yamlql_library/cli.py`, `cli_logic.py`, `utils.py`
   - Typer commands, prompt-toolkit interactive input, and Rich rendering.
   - Interactive `BEGIN` only queues SQL text; `COMMIT` executes each statement separately. It is not an atomic multi-statement file transaction.

11. **Tests** — `tests/`
  - Existing suites cover projection, paths/maps, classification, CRUD, CLI, and transaction behavior.
  - `fidelity_utils.py` provides byte-exact helpers. Focused golden suites cover physical format, comment ownership, anchors/aliases/merges/tags, scalar and collection styles, kind guards, streams, and SQL end-to-end fidelity.
  - `test_writer_multidoc.py` contributes 54 cases and `test_sql_fidelity_e2e.py` contributes 22 cases after parametrization.
  - Verified suite state: 290 passed, 1 strict xfailed (SQL cannot target a flattened mapping as an updateable mapping column), and 1 pre-existing out-of-scope failure (`test_cli_sql_from_file_option_unicode`, SQL-file Unicode mojibake).

### Internal Dependency Graph

```text
CLI / Python caller -> YamlQL
                |-> YamlLoader -> PyYAML
                |-> DataTransformer -> pandas
                `-> Database -> DuckDB
                     |-> SqlInterceptor -> sqlglot
                     `-> CRUD handlers
                          |-> ReverseTransformer
                          `-> TransactionManager
                              `-> YamlWriter -> ruamel.yaml
```

No network service, external database, authentication provider, message bus, or background worker exists.

### Read Data Flow

```text
UTF-8 YAML -> text preprocessing -> PyYAML load_all(_ApplicationTagSafeLoader)
 -> Python objects -> DataTransformer
 -> DataFrames + _yaml_path + column maps
 -> in-memory DuckDB -> SELECT/DDL -> DataFrame -> API/Rich output
```

### Write Data Flow

```text
DML SQL -> sqlglot classifier -> CRUD handler
 -> DuckDB validation/matching-row SELECT
 -> _yaml_path + original-name metadata
 -> TransactionManager backup -> byte-based YamlWriter stream load
 -> owning-document resolution (last mapping document defining the top-level key)
 -> guarded path/root mutation or source-range list splice -> reparse after each splice
 -> writer.write_to(same-filesystem temp)
 -> load_all validation + document-count check -> os.replace
 -> full YamlQL reload from disk
```

For an unqualified path in a multi-document stream, the writer and SQL handlers target the **last mapping document defining the path's top-level key**, matching the semantic loader's later-wins shallow merge. Direct writer callers may select a document explicitly with zero-based `doc=`. Non-mapping stream documents are preserved but are not SQL-addressable.

### Consistency Model

- YAML is authoritative; DuckDB is derived and ephemeral.
- `YamlQL.query()` rebuilds all derived state after a successful write.
- Atomic replacement prevents torn files, not lost updates. No lock, source hash, or optimistic concurrency check exists.

---

## Patterns & Standards

- **Facade**: `YamlQL` owns lifecycle.
- **Adapters**: PyYAML loader and ruamel writer isolate different object models.
- **Transformers**: forward relational projection and partial reverse reconstruction.
- **Interceptor/Router**: SQL classification separates read/DDL from persistent DML.
- **Command handlers**: INSERT, UPDATE, DELETE.
- **Unit of Work**: one `TransactionManager` per write statement.
- **Reload after write**: discard and rebuild derived state.

### Paths and Naming

- `_yaml_path` is reserved mutation metadata, synthetically rooted at `root` (for example, `root.users.0`).
- Handlers strip `root.` before writer calls.
- SQL names replace spaces, dots, and hyphens with underscores; `column_name_map` attempts to restore original names.

### Cross-Cutting Concerns

- **Authentication/authorization**: none; authority comes from process/filesystem permissions.
- **Write safety**: mode `r` is default; DML requires `rw`/`w` or CLI `--writable`.
- **Parsing security**: PyYAML uses a dedicated `SafeLoader` subclass for `load_all()`; application tags are reduced to safe plain values, the core tag namespace is not accepted by the fallback, and global loader state is unchanged.
- **Errors**: database permission/config errors raise; handlers return structured results; CLI catches/renders errors. The writer has a focused safety-policy exception hierarchy (`YamlWriterPolicyError` and specific anchor, merge, and kind-change errors), not a system-wide domain exception hierarchy.
- **Observability**: Rich, warnings, and occasional stderr; no structured logs, metrics, traces, or audit journal.
- **Configuration**: constructor arguments, CLI options, environment variables, and `.env` loading.
- **Secrets**: no redaction; query output can expose source values.
- **Concurrency**: no locking/conflict detection.

### Packaging and Infrastructure

- setuptools package, Python `>=3.9`, console entry `yamlql_library.cli:main`.
- `.github/workflows/documentation.yml` deploys MkDocs documentation.
- No package-test workflow, runtime service/container deployment, or persistent database infrastructure was found.

---

## Complex YAML Editor Readiness

### Guaranteed and Tested

- **No-op and minimal-diff physical fidelity**: byte-based I/O preserves LF/CRLF, UTF-8 BOM, detected mapping/sequence indentation and offset, explicit start markers, and exact trailing-newline suffix. Golden tests cover no-op writes and targeted SQL edits.
- **Source-aware list editing**: block-list deletion removes comments owned by the deleted item; append inserts before post-list comments. Source-range splices are immediately reparsed so later edits use current line/column data.
- **Comment and style behavior**: focused tests cover mapping/list comment ownership, inline comments beside anchors, single/double quotes, literal/folded block scalars and chomping, safe quoting of type-looking strings, flow/block collections, and sibling-style adoption for new values.
- **Anchor/alias/merge/tag policy**: updating an anchor definition reconnects aliases; updating an alias occurrence replaces only that occurrence; deleting an anchor still in use fails; deleting a merge-only key fails while setting it creates an own-key override; application tags open safely on the read path and survive tested writes.
- **Structural guard**: mapping/sequence/scalar kind changes fail by default with `NodeKindChangeError`; direct writer code must explicitly pass `allow_kind_change=True`. SQL surfaces the failure context and leaves the file unchanged.
- **Multi-document write preservation**: the writer models all documents, parser events define safe source spans, only dirty document bodies are spliced, and transaction validation parses all documents and checks count. `documents`, `doc_count`, `find_document()`, and zero-based `doc=` are tested.
- **SQL stream addressing**: SQL targets the last mapping document defining the top-level key, consistent with the read loader's later-wins merge. Untouched documents, directives/comments around document boundaries, block-scalar separator text, BOM, and stream tail newlines are covered by byte-exact tests.
- **Transactional serialization**: commit uses the same loaded writer's `write_to()` and format profile; semantic dirty checking catches direct `writer.data`/`documents` mutations. One statement retains backup/temp/validate/atomic-replace behavior.
- **Evidence**: the focused tests include 54 multi-document and 22 SQL fidelity cases; the complete run is 290 passed, 1 strict xfailed, plus the known baseline failure below.

These guarantees make the write path a robust format-preserving **row-level YAML editor**. They do not make the lossy SQL projection a general concrete-syntax or arbitrary structural editor.

### Resolved Previous Findings

- Resolved: forced two-space indentation and text-mode EOL translation.
- Resolved: list-item comment ownership and append placement for covered block-list shapes.
- Resolved: single-document mutation/validation and inaccurate multi-document preservation claims.
- Resolved: missing anchor/alias/merge policy, custom application-tag tolerance, style-focused coverage, and silent node-kind replacement.
- Resolved: transaction commit constructing a second default-configured writer.

### Verified Limitations and Risks

1. **The SQL read model remains lossy and shape-dependent**
  - PyYAML values are transformed into DataFrames; comments, styles, tags, anchor/alias identity, merge provenance, and source locations do not enter the relational model.
  - Mapping flattening, first-record nested-list discovery, all-record path requirements, unsupported mixed lists, and scalar-list stringification limit reachability and fidelity. The one strict xfail records that SQL cannot target a flattened mapping as an updateable mapping node.

2. **Document visibility remains asymmetric**
  - Mapping documents are shallow-merged for SQL with later top-level keys winning. Non-mapping documents are preserved by the writer but invisible and unaddressable to SQL.
  - SQL does not expose arbitrary document indexes; its addressing rule is the last document defining the top-level key. Direct writer callers can use `doc=`.

3. **Updated numeric/boolean literal spelling is not preserved**
  - Unchanged nodes stay source bytes, but replacing values can normalize lexical forms such as hexadecimal, underscores, or YAML 1.1 boolean spellings.

4. **Column-name collisions remain possible**
  - `a-b`, `a.b`, and `a b` all sanitize to `a_b`; reverse targeting is therefore not injective.

5. **Dot/numeric path segments remain ambiguous**
  - Literal dotted keys are split as traversal, and numeric key names can be confused with sequence indexes. Typed paths or stable node IDs are not implemented.

6. **No concurrency control**
  - Atomic replacement prevents torn writes but there is no lock, source hash, or optimistic conflict check; concurrent writers can lose updates.

7. **Interactive `BEGIN`/`COMMIT` is not one atomic file transaction**
  - Queued SQL statements execute as separate transactions. Failure at operation $n$ leaves operations $1..n-1$ committed.

8. **ruamel private-internals dependency**
  - Comment, anchor, merge, flow-style, line/column, and tag behavior uses ruamel internals verified on 0.19.1, while `pyproject.toml` still pins only `ruamel.yaml>=0.18.0`. Compatibility across that declared range is not established.

9. **Per-edit reparse cost**
  - Source-range list delete/append reparses the complete stream after each mutation to make subsequent source locations safe. Large files or large multi-row edits can therefore incur repeated parse cost.

10. **Same-name anchor redefinition risk**
   - Anchor policy helpers locate occurrences by anchor name and rely on ruamel object/location behavior. YAML permits anchor-name reuse, especially across document scopes; same-name redefinitions are not proven safe for all mutation paths.

11. **Baseline Unicode SQL-file failure**
   - `test_cli_sql_from_file_option_unicode` still produces mojibake when reading Unicode SQL text from a file. This predates the complex YAML writer work and remains out of scope.

12. **Derived table-to-node mapping remains incomplete**
   - Top-level list tables and existing `_yaml_path` rows are supported, but deeply derived table names are not a general stable YAML-node identity scheme.

### Recommended Next Architectural Steps

- Unify the semantic projection with the round-trip stream or retain explicit document/node identities through transformation.
- Replace string dot paths with typed path segments or stable node IDs and detect sanitized-name collisions.
- Add content-hash/file-metadata concurrency checks and one in-memory unit of work for atomic interactive batches.
- Pin/test a supported ruamel version range and add performance tests for repeated source-splice reparsing.
- Scope anchor searches to the selected document and explicitly test same-name anchor redefinitions.

---

## Design System

No graphical UI exists. CLI conventions use Rich tables/lists, green success, red error, yellow warning/status, cyan emphasis, and prompt-toolkit history/auto-suggestion.

---

## Boundaries & Constraints

### NEVER

- ❌ Never treat or edit `build/` as authoritative source.
- ❌ Never bypass `TransactionManager` for persistent CRUD writes.
- ❌ Never serialize a transaction through a second default-configured writer; use the loaded transaction writer's `render()`/`write_to()`.
- ❌ Never rely on a ruamel round-trip dump alone as proof of byte or comment fidelity.
- ❌ Never remove `_yaml_path` without an equivalent stable locator.
- ❌ Never assume a sanitized SQL name uniquely identifies a YAML key.
- ❌ Never claim exact format preservation from semantic equality or one retained comment.
- ❌ Never mutate from stale source locations after a source splice.
- ❌ Never describe interactive queued statements as one atomic transaction.
- ❌ Never build a complex editor solely on DataFrames.
- ❌ Never run two pytest processes against this repository at once; shared files and resource contention can make the result unreliable.

### ALWAYS

- ✅ Always use `YamlQL` as the public lifecycle facade unless unit-testing a lower layer.
- ✅ Always treat YAML as durable truth and rebuild DuckDB-derived state after writes.
- ✅ Always classify SQL and require explicit write mode before DML.
- ✅ Always retain path and reversible-name metadata for mutation targeting.
- ✅ Always delete multiple list indices highest-first.
- ✅ Always resolve unqualified stream paths to the last mapping document defining the top-level key; use explicit `doc=` when a direct caller intends another document.
- ✅ Always splice source ranges for fidelity-sensitive list edits when locations are trustworthy, and reparse after every such mutation before using line data again.
- ✅ Always enforce writer anchor/merge/kind-change policy before persistence and let failures roll back before DuckDB synchronization.
- ✅ Always use same-filesystem temp serialization, `load_all()` validation, document-count verification, and atomic replacement.
- ✅ Always use explicit UTF-8 byte I/O and preserve the detected format profile.
- ✅ Always add byte-exact golden tests for write behavior; semantic reparse assertions alone are insufficient.

---

## File Organization

```text
/pyproject.toml                    -> Package metadata/entry point
/yamlql_library/                   -> Authoritative runtime package
  /__init__.py                     -> YamlQL facade/reload lifecycle
  /loader.py                       -> PyYAML semantic stream loader
  /transformer.py                  -> Object-to-DataFrame projection
  /database.py                     -> DuckDB gateway/DML routing
  /sql_interceptor.py              -> SQL classification
  /crud_handlers.py                -> DML handlers
  /reverse_transformer.py          -> Partial reverse projection
  /writer.py                       -> Format/source-aware ruamel stream mutation adapter
  /transaction.py                  -> Atomic stream-aware file unit of work
  /cli.py / cli_logic.py / utils.py -> CLI presentation
/tests/test_yamlql.py              -> Core/writer/transaction/CLI tests
/tests/test_crud.py                -> CRUD integration tests
/tests/fidelity_utils.py           -> Byte-exact golden-test helpers
/tests/test_writer_fidelity.py     -> Format-profile and minimal-diff tests
/tests/test_writer_comments.py     -> Comment ownership/source-splice tests
/tests/test_writer_anchors_tags.py -> Anchor/alias/merge/tag policy tests
/tests/test_writer_styles.py       -> Scalar/collection style tests
/tests/test_writer_type_guard.py   -> Node-kind policy tests (one strict xfail)
/tests/test_writer_multidoc.py     -> Document-stream fidelity/addressing tests
/tests/test_sql_fidelity_e2e.py    -> Complex SQL write-path golden tests
/tests/test_data/                  -> YAML fixtures
/docs/guides/crud-operations.md    -> CRUD behavior, policies, and limits
/docs/guides/transaction-safety.md -> Transaction and write-fidelity guarantees
/docs/                             -> Remaining MkDocs source
/.github/workflows/documentation.yml -> Docs CI
/build/                            -> Stale generated copy; excluded
```

- Naming: snake_case modules/functions, PascalCase classes, `test_<behavior>` tests.
- Tests should mutate temporary files, reopen `YamlQL` to verify persistence, and assert source text/golden files for syntax-fidelity requirements.

---

## Change History

### 2026-10-02 — Complex YAML Editor Write Path

- Added detected format profiles and byte-based no-op/minimal-diff rendering for indentation, EOL, BOM, explicit starts, and trailing newlines.
- Evolved `YamlWriter` into a source-aware document-stream adapter with source-range list editing, comment ownership, style adoption, explicit anchor/alias/merge/tag and kind-change policies, and `render()`/`write_to()`/`append_item()` APIs.
- Updated transactions to serialize through the loaded writer, detect direct semantic mutation, validate every document, and reject document-count drift.
- Made SQL INSERT/UPDATE document-aware and surfaced writer policy failures before DuckDB synchronization; documented last-defining-document addressing.
- Added a dedicated application-tag-safe semantic loader without changing global PyYAML loader state.
- Added byte-exact focused suites; verified 290 passed, 1 strict xfailed, and the one pre-existing Unicode SQL-file baseline failure.
- Updated the CRUD and transaction-safety guides with the completed guarantees, policies, and remaining limitations.
- **Drift**: positive architectural evolution from dump-based single-document mutation to source-aware stream mutation. The writer now has a focused policy exception hierarchy and source-splice path not present in the previous architecture.
- **Concern**: the read projection remains lossy and the writer's advanced policy depends on ruamel 0.19.1 internals despite a broader declared minimum.
- Confidence: 100%.

### 2026-10-01 — Regenerated Architecture

- Analyzed current authoritative package and tests; excluded `build/`.
- Mapped dependencies, read/write flows, transaction scope, CLI, tests, and cross-cutting concerns.
- Corrected multi-document characterization: query loading uses `safe_load_all()`, while mutation/validation uses single-document `load()`.
- Documented limitations for a future complex YAML editor.
- Confidence: 100%.

---

## Confidence Report

- **Score**: 100%
- Entry points: 20/20.
- Persistence/data flow: 20/20.
- Internal dependency graph: 20/20.
- Packaging/infrastructure intent: 20/20.
- Cross-cutting concerns: 20/20.
- **Known Unknowns**:
  - Advanced behavior on ruamel versions other than the verified 0.19.1 remains unknown because the declared dependency floor is 0.18.0.
  - Same-name anchor redefinitions and performance on large streams with many source-splice edits need dedicated evidence.
  - The supplied final suite result was cross-checked against the focused test inventory and code paths; this update did not launch another test process.

<!-- LEGACY REPORT BELOW IS QUARANTINED: it predates the current source and contains contradicted claims.

---

## High-Level Landscape

- **Architecture Type**: Single-Package CLI Library with Pipeline Architecture (Load → Transform → Query)
- **Primary Tech Stack**: Python 3.9+, DuckDB (in-memory SQL), PyYAML, Pandas, Typer (CLI), Rich (output formatting)
- **Entry Points**:
  - CLI: `yamlql` command (registered via `pyproject.toml` → `yamlql_library.cli:app`)
  - Library: `from yamlql_library import YamlQL` (programmatic usage)
- **Core Purpose**: Transform arbitrary YAML files into relational tables and query them using SQL or natural language (AI-powered)

---

## System Topology

### Components

1. **YamlQL (Facade/Orchestrator)**
   - Location: `yamlql_library/__init__.py`
   - Purpose: Main entry point that orchestrates the Load→Transform→Query pipeline
   - Pattern: Facade Pattern — exposes `query()`, `list_tables()`, `close()`
   - Responsibilities: Instantiates Loader, Transformer, Database in sequence

2. **YamlLoader**
   - Location: `yamlql_library/loader.py`
   - Purpose: Reads and parses YAML files (single or multi-document)
   - Pattern: Single Responsibility — only handles file I/O and YAML parsing
   - Key Features:
     - Multi-document YAML support (merges documents)
     - Boolean key preservation (wraps `on`, `true`, etc. in quotes)
     - Separator normalization (`------` → `---`)

3. **DataTransformer**
   - Location: `yamlql_library/transformer.py`
   - Purpose: Converts nested YAML dictionaries into flat relational tables (DataFrames)
   - Pattern: Recursive Tree-to-Table Decomposition with configurable strategies
   - Key Features:
     - Two strategies: `depth` (default) and `adaptive`
     - Configurable `max_depth` for recursion limits
     - Nested list extraction into child tables with parent metadata
     - Column name sanitization (spaces, dots, hyphens → underscores)
     - Single-key unwrapping heuristic for cleaner schemas
     - Scalar list stringification for type consistency

4. **Database**
   - Location: `yamlql_library/database.py`
   - Purpose: Manages an in-memory DuckDB connection and registers DataFrames as queryable tables
   - Pattern: Thin Wrapper over DuckDB
   - Key Features:
     - In-memory only (`:memory:`)
     - Registers Pandas DataFrames directly via `con.register()`
     - Exposes raw SQL execution returning DataFrames

5. **CLI Interface**
   - Location: `yamlql_library/cli.py`
   - Purpose: Typer-based command-line interface with subcommands
   - Commands:
     - `sql` — Execute SQL queries (direct or interactive REPL)
     - `discover` — Show tables and columns from a YAML file
     - `ai` — Natural language querying via LLM providers
     - `--execute / -e` — Quick query via environment variables
   - Environment Variables: `YAMLQL_FILE`, `YAMLQL_MODE`, `YAMLQL_OUTPUT`, `YAMLQL_MAX_DEPTH`, `YAMLQL_STRATEGY`

6. **CLI Logic**
   - Location: `yamlql_library/cli_logic.py`
   - Purpose: Implementation logic separated from CLI definition (avoids circular deps)
   - Functions: `run_query()`, `run_interactive_sql()`, `run_nlp()`
   - Key Features:
     - Interactive SQL REPL with `prompt_toolkit` (history, auto-suggest)
     - Multi-line SQL input (semicolon-terminated)
     - Built-in commands: `listtables`, `listfields <table>`, `exit`

7. **LLM Providers**
   - Location: `yamlql_library/llm_providers.py`
   - Purpose: AI-powered natural language to SQL translation
   - Pattern: Strategy Pattern with Factory
   - Providers: OpenAI (GPT-4), Gemini (gemini-2.0-flash), Ollama (placeholder)
   - Key Features:
     - Only sends schema to LLM (not data — privacy-preserving)
     - SQL sanitization (strips markdown fences from LLM output)
     - Configured via `YAMLQL_LLM_PROVIDER` env var

8. **Utils**
   - Location: `yamlql_library/utils.py`
   - Purpose: Output rendering utilities
   - Features: Auto/table/list output format selection based on terminal width

### Data Flow

```
YAML File → YamlLoader.load() → Dict[str, Any]
         → DataTransformer.transform() → List[Tuple[str, pd.DataFrame]]
         → Database.create_tables() → DuckDB In-Memory Tables
         → Database.query(SQL) → pd.DataFrame → Rich Output
```

**AI Query Flow:**
```
User Question → LLM Provider (schema only) → Generated SQL
             → Database.query(SQL) → pd.DataFrame → Rich Output
```

---

## Patterns & Standards

### Communication Patterns
- **Type**: In-process pipeline (no network communication between components)
- **External**: HTTP to LLM APIs (OpenAI, Gemini) for AI queries only
- **Data Privacy**: Only database schema is sent to LLMs, never the actual data

### Logic Patterns
- **Pipeline Pattern**: Load → Transform → Query (strictly sequential, one-shot)
- **Facade Pattern**: `YamlQL` class hides internal complexity
- **Strategy Pattern**: LLM providers share common interface (`LlmProvider` ABC)
- **Factory Pattern**: `get_llm_provider()` instantiates correct provider by name
- **Recursive Decomposition**: `DataTransformer._process_node()` recursively flattens nested structures

### Cross-Cutting Concerns
- **Authentication**: LLM API keys via environment variables (`OPENAI_API_KEY`, `GEMINI_API_KEY`)
- **Logging**: No formal logging framework; uses `rich.print` for user-facing messages
- **Error Handling**: Try/except at CLI boundary with Rich-formatted error messages
- **Configuration**: Environment variables + CLI options with sensible defaults
- **Testing**: pytest with `CliRunner` for CLI tests, direct class usage for unit tests

---

## Boundaries & Constraints

### NEVER (Anti-Patterns)
- ❌ Never send actual YAML data to LLM providers (only schema)
- ❌ Never persist database to disk (always in-memory)
- ❌ Never modify YAML files without explicit `--writable` flag or `mode='rw'` (opt-in safety)
- ❌ Never use domain-specific keywords in the transformer (universal heuristics only)
- ❌ Never bypass the pipeline stages (Load→Transform→Query or Load→Transform→Query→CRUD→Write)
- ❌ Never make YAML writes without `TransactionManager` (atomicity required)
- ❌ Never skip path validation before writing (prevent invalid YAML)

### ALWAYS (Required Patterns)
- ✅ Always sanitize column names (replace spaces, dots, hyphens with underscores)
- ✅ Always track bidirectional column name mapping for CRUD operations
- ✅ Always add `_yaml_path` column to DataFrames (enables UPDATE/DELETE targeting)
- ✅ Always stringify scalar lists before DataFrame creation
- ✅ Always close the database connection after use
- ✅ Always handle `FileNotFoundError` gracefully at CLI boundary
- ✅ Always use `yaml.safe_load_all` for reads (never `yaml.load` — security)
- ✅ Always use `ruamel.yaml` for writes (preserve formatting)
- ✅ Always create backup before modifying YAML files
- ✅ Always strip `root.` prefix from `_yaml_path` before calling writer methods
- ✅ Always validate user permission before executing write operations
    → Core library package
    __init__.py                → YamlQL facade class (main entry)
    cli.py                     → Typer CLI definition and commands
    cli_logic.py               → CLI command implementations (enhanced with transactions)
    loader.py                  → YAML file loading and parsing
    transformer.py             → YAML→Relational transformation (enhanced with _yaml_path)
    database.py                → DuckDB wrapper (enhanced with CRUD routing)
    llm_providers.py           → AI/LLM integration (OpenAI, Gemini)
    utils.py                   → Output formatting utilities
    sql_interceptor.py         → **NEW**: SQL classification and routing
    reverse_transformer.py     → **NEW**: Relational→YAML transformation
    writer.py                  → **NEW**: Format-preserving YAML writer
    transaction.py             → **NEW**: Atomic file operations
    crud_handlers.py           → **NEW**: INSERT/UPDATE/DELETE handlers
  tests/                       → Test suite
    test_yamlql.py             → Unit and CLI integration tests (enhanced)
    test_crud.py               → **NEW**: Comprehensive CRUD test suite (76 tests)
    test_data/                 → Sample YAML files for testing
  docs/                        → MkDocs documentation source
    guides/
      crud-operations.md       → **NEW**: CRUD operations guide
      transaction-safety.md    → **NEW**: Transaction safety guide
    getting-started/
      quick-start.md           → **UPDATED**: Added CRUD examples
  .StefaniniAI/                → **NEW**: Planning and execution artifacts
    plan/                      → Task decomposition for CRUD implementation
    Research.md                → Codebase research bundle
    execution-trace.jsonl      → Action audit trail
  assets/    former.py         → YAML→Relational table transformation
    database.py            → DuckDB in-memory database wrapper
    llm_providers.py       → AI/LLM integration (OpenAI, Gemini)
    utils.py               → Output formatting utilities
  tests/                   → Test suite
    test_yamlql.py         → Unit and CLI integration tests
    test_data/             → Sample YAML files for testing
  docs/                    → MkDocs documentation source
  assets/                  → Media assets (GIF demos)
```

### Naming Conventions
- Modules: `snake_case.py`
- Classes: `PascalCase` (e.g., `YamlQL`, `DataTransformer`, `LlmProvider`)
- Functions: `snake_case` (e.g., `run_query`, `get_llm_provider`)
- CLI Commands: kebab-style registered names, snake_case functions
- Table Names: Generated from YAML keys with underscores replacing hyphens

---

## Dependencies

### Runtime
| Package | Purpose |
|---------|---------|
| `duckdb>=0.9.0` | In-memory SQL engine |
| `pyyaml>=6.0` | YAML parsing (read-only) |
| `ruamel.yaml>=0.18.0` | **NEW**: Format-preserving YAML writes |
| `sqlglot>=23.0.0` | **NEW**: SQL parsing and classification |
| `pandas>=2.0.0` | DataFrame intermediary between transformer and DuckDB |
| `typer>=0.9.0` | CLI framework |
| `rich>=13.0.0` | Terminal output formatting |
| `python-dotenv>=1.1.0` | Environment variable loading |
| `openai>=1.86.0` | OpenAI API integration |
| `google-generativeai>=0.8.5` | Gemini API integration |
| `prompt-toolkit>=3.0.47` | Interactive REPL with history |

### Development
| Package | Purpose |
|---------|---------|
| `pytest>=8.4.0` | Testing framework |
| `mkdocs-material` | Documentation site |

---

## CRUD Completeness Analysis

### Current State: FULL CRUD CAPABILITY (✅ IMPLEMENTED)

**Updated June 30, 2026**: YamlQL now supports **full CRUD operations**! The system has been extended from a read-only query engine to a complete bi-directional pipeline: YAML ↔ relational tables ↔ SQL modifications ↔ YAML writes.

**Architecture Evolution**: The original one-way pipeline (Load → Transform → Query) has been augmented with reverse transformation, SQL interception, atomic writes, and transaction management — all while maintaining backward compatibility with read-only mode as the default.

---

### READ — ✅ FULLY IMPLEMENTED

**Current Capabilities:**
- Full DuckDB SQL dialect support (SELECT, WHERE, JOIN, GROUP BY, ORDER BY, HAVING, LIMIT, OFFSET, UNION, subqueries, window functions, CTEs)
- Aggregation functions (COUNT, SUM, AVG, MIN, MAX)
- String functions, date functions, array operations (UNNEST)
- Cross-table JOINs via parent metadata columns
- Natural language queries via AI (schema sent to LLM → SQL generated)
- Interactive REPL with multi-line SQL and command history
- Schema discovery (`yamlql discover`)

**Architecture supporting READ:**
- `Database.query()` passes raw SQL to DuckDB with no restrictions
- DuckDB's full SQL engine is available (not just SELECT — see gaps below)
- DataFrame results returned via `fetchdf()`

---

### CREATE — ✅ FULLY IMPLEMENTED

**Implementation Status**: INSERT operations are fully functional with comprehensive SQL support.

**Architecture Components Implemented:**

1. **SQL Parsing Layer** — `SqlInterceptor` (using `sqlglot` library)
   - Classifies SQL statements by type (SELECT, INSERT, UPDATE, DELETE, DDL, UNKNOWN)
   - Extracts table name, columns, and values from parsed AST
   - Returns `{'type': 'INSERT', 'needs_crud': True, 'table': 'users', 'parsed': Expression}`
   - Location: `yamlql_library/sql_interceptor.py`

2. **Reverse Transformer** — `ReverseTransformer` class
   - Maps relational rows back to nested YAML paths using `_yaml_path` column
   - Desanitizes column names using bidirectional `column_name_map`
   - Reconstructs nested dictionaries and lists from flat path-value pairs
   - Handles NULL values (writes as YAML `null`)
   - Location: `yamlql_library/reverse_transformer.py` (230 lines)

3. **YAML Writer** — `YamlWriter` class (using `ruamel.yaml`)
   - Format-preserving writes maintain comments, indentation, blank lines
   - Path validation (no consecutive dots, leading/trailing dots)
   - Supports dot-notation with list indices (e.g., `items.0.name`)
   - Operations: `load()`, `set_value()`, `insert_value()`, `delete_value()`, `write()`
   - Location: `yamlql_library/writer.py` (310 lines)

4. **INSERT Handler** — `InsertHandler` class
   - Parses INSERT SQL using sqlglot (handles all SQL variants)
   - Supports single/multiple rows, named/positional columns
   - Validates schema against existing tables
   - Converts rows to YAML paths, writes atomically via `TransactionManager`
   - Updates DuckDB after successful write
   - Location: `yamlql_library/crud_handlers.py` (~600 lines)

**Supported INSERT Syntax:**
```sql
INSERT INTO users VALUES ('Alice', 30)
INSERT INTO users (name, age) VALUES ('Bob', 25)
INSERT INTO users VALUES ('Charlie', 35), ('Diana', 28)
INSERT INTO services (name, port) VALUES ('api', 8080)
```

**Key Features:**
- ✅ Empty list support (e.g., `users: []` → INSERT creates first item)
- ✅ Type preservation (int, float, bool, string maintained)
- ✅ NULL value handling (written as YAML null)
- ✅ Atomic operations with rollback on failure

---

### UPDATE — ✅ FULLY IMPLEMENTED

**Implementation Status**: UPDATE operations are fully functional with WHERE clause support and expression evaluation.

**Architecture Components Implemented:**

1. **SQL Parsing Layer** — `SqlInterceptor` extracts UPDATE components
   - Parses table name, SET clause, WHERE conditions
   - Handles complex WHERE clauses (AND, OR, IN, BETWEEN, LIKE, etc.)

2. **Record Identification** — Uses `_yaml_path` column for precise targeting
   - Transformer generates stable `_yaml_path` for every row (e.g., `root.users.0`, `root.services.1`)
   - WHERE clause executed in DuckDB returns affected rows with their `_yaml_path`
   - Solves the "which YAML node to modify" problem with precision

3. **UPDATE Handler** — `UpdateHandler` class
   - Parses UPDATE SQL with SET and WHERE clauses
   - Executes WHERE clause via DuckDB to identify affected rows
   - Supports expression evaluation (e.g., `SET age = age + 1`) by executing expressions in DuckDB
   - Converts column values to YAML paths using `ReverseTransformer`
   - Strips `root.` prefix from `_yaml_path` before calling `writer.set_value()`
   - Updates YAML atomically via `TransactionManager`
   - Updates DuckDB after successful write
   - Location: `yamlql_library/crud_handlers.py` (~550 lines)

**Supported UPDATE Syntax:**
```sql
UPDATE users SET age = 31 WHERE name = 'Alice'
UPDATE services SET port = 8081, status = 'active' WHERE name = 'api'
UPDATE counters SET value = value + 1
UPDATE users SET status = 'inactive' WHERE age > 65 AND last_login < '2020-01-01'
```

**Key Features:**
- ✅ Complex WHERE clauses (full DuckDB SQL support)
- ✅ Expression evaluation (arithmetic, string concatenation)
- ✅ Multiple column updates
- ✅ Type preservation
- ✅ NULL handling (can set columns to NULL)

---

### DELETE — ✅ FULLY IMPLEMENTED

**Implementation Status**: DELETE operations are fully functional with smart list deletion.

**Architecture Components Implemented:**

1. **SQL Parsing Layer** — `SqlInterceptor` extracts DELETE components
   - Parses table name and WHERE clause
   - Supports full DuckDB WHERE syntax

2. **Record Identification** — Uses `_yaml_path` column (same as UPDATE)
   - WHERE clause executed in DuckDB returns affected rows with paths

3. **DELETE Handler** — `DeleteHandler` class
   - Parses DELETE SQL with WHERE clause
   - Executes WHERE clause via DuckDB to identify affected rows
   - Smart list deletion: Sorts paths in reverse order to avoid reindex issues
     - Example: Deleting `users.0` then `users.1` fails (indexes shift)
     - Solution: Delete `users.2`, then `users.1`, then `users.0` (reverse order)
   - Strips `root.` prefix from `_yaml_path` before calling `writer.delete_value()`
   - Updates YAML atomically via `TransactionManager`
   - Updates DuckDB after successful write
   - Location: `yamlql_library/crud_handlers.py` (~400 lines)

**Supported DELETE Syntax:**
```sql
DELETE FROM users WHERE age < 18
DELETE FROM services WHERE status = 'deprecated'
DELETE FROM users WHERE email LIKE '%@temp.com'
DELETE FROM users  -- Prompts for confirmation (dangerous!)
```

**Key Features:**
- ✅ Complex WHERE clauses (full DuckDB SQL support)
- ✅ Smart list deletion (reverse-order to prevent index issues)
- ✅ Safety check for DELETE without WHERE
- ✅ Preserves list structure (no gaps)

---

### NEW Components (CRUD Implementation)

The following components were added to support full CRUD operations:

#### 9. **SqlInterceptor** (NEW)
   - Location: `yamlql_library/sql_interceptor.py` (100 lines)
   - Purpose: Classifies SQL statements and routes to appropriate handlers
   - Pattern: Router Pattern using `sqlglot` for parsing
   - Key Method: `SqlInterceptor.classify(sql)` returns classification dict
   - Dependencies: Uses `sqlglot` library with DuckDB dialect
   - Integrated into: `Database.query()` method

#### 10. **ReverseTransformer** (NEW)
   - Location: `yamlql_library/reverse_transformer.py` (230 lines)
   - Purpose: Converts relational rows back to nested YAML structures
   - Pattern: Inverse of `DataTransformer` — reconstructs nested dicts/lists from flat paths
   - Key Methods:
     - `row_to_yaml_path(table_name, row)` — Desanitizes columns, extracts path-value pairs
     - `build_nested_dict(path_value_pairs)` — Reconstructs nested structure
   - Key Features:
     - Uses bidirectional `column_name_map` from transformer
     - Preserves NULL values (writes as YAML null)
     - Handles list reconstruction with numeric indices
     - Type preservation (int, float, bool, string)

#### 11. **YamlWriter** (NEW)
   - Location: `yamlql_library/writer.py` (310 lines)
   - Purpose: Format-preserving YAML writer using `ruamel.yaml`
   - Pattern: Mutator Pattern with path-based operations
   - Key Methods:
     - `load()` — Load YAML with format preservation
     - `set_value(path, value)` — Update existing node
     - `insert_value(path, value)` — Add new node
     - `delete_value(path)` — Remove node
     - `write()` — Write changes back to file
   - Key Features:
     - Preserves comments, indentation, blank lines, key order
     - Path validation (no consecutive dots, leading/trailing dots)
     - Supports dot-notation with list indices (e.g., `items.0.name`)

#### 12. **TransactionManager** (NEW)
   - Location: `yamlql_library/transaction.py` (310 lines)
   - Purpose: Atomic file write operations with commit/rollback
   - Pattern: Transaction Pattern with context manager support
   - Key Methods:
     - `begin()` — Create backup file
     - `commit()` — Atomic write (temp file + rename)
     - `rollback()` — Restore backup
   - State Machine: NOT_STARTED → IN_PROGRESS → COMMITTED/ROLLED_BACK
   - Key Features:
     - Context manager support (`with TransactionManager(...) as txn`)
     - Automatic rollback on exception
     - Temp file + atomic rename for safety

#### 13. **CRUD Handlers** (NEW)
   - Location: `yamlql_library/crud_handlers.py` (1,600+ lines total)
   - Purpose: INSERT, UPDATE, DELETE operation handlers
   - Components:
     - **InsertHandler** (~600 lines): Parses INSERT SQL, validates schema, writes to YAML
     - **UpdateHandler** (~550 lines): Parses UPDATE SQL with WHERE, modifies YAML nodes
     - **DeleteHandler** (~400 lines): Parses DELETE SQL with WHERE, removes YAML nodes
   - Pattern: Command Pattern — each handler is a self-contained operation
   - Key Features:
     - Comprehensive SQL parsing using sqlglot
     - Schema validation before writes
     - Expression evaluation (e.g., `SET age = age + 1`)
     - Atomic writes via `TransactionManager`
     - Pragmatic database sync (warn to reload on sync failure)

---

### Enhanced Components (CRUD Support)

The following existing components were enhanced to support CRUD:

#### DataTransformer (ENHANCED — Phase 1)
   - **New Feature**: Source path tracking with `_yaml_path` column
     - Every DataFrame row gets a path column (e.g., `root.users.0`, `root.services.1`)
     - Enables precise targeting for UPDATE/DELETE operations
   - **New Feature**: Bidirectional column name mapping
     - `column_name_map` attribute: `{table_name: {sanitized_col: original_col}}`
     - Methods: `_sanitize_column_name()`, `_track_column_mapping()`
     - Solves the "lossy column naming" problem (underscores ↔ hyphens/dots)
   - **New Feature**: Empty list support
     - Creates tables with `_yaml_path` column even for empty lists (e.g., `users: []`)

#### Database (ENHANCED — Phase 1, 2, 3)
   - **New Feature**: Write mode support
     - `__init__(mode: str = 'r', file_path: str = None)` — mode parameter
     - Modes: `'r'` (read-only, default), `'rw'` (read-write), `'w'` (write-only)
     - Permission checking: raises `PermissionError` if write attempted in read-only mode
   - **New Feature**: CRUD handler initialization
     - `initialize_crud_handlers()` — Creates INSERT/UPDATE/DELETE handlers when mode allows writes
     - Handlers stored as instance attributes
   - **New Feature**: SQL routing
     - Enhanced `query()` method with `SqlInterceptor` classification
     - Routes SELECT to DuckDB, INSERT/UPDATE/DELETE to CRUD handlers
     - Returns dict with `{'success': bool, 'message': str, ...}` for write operations

#### YamlQL (ENHANCED — Phase 2)
   - **New Feature**: Write mode configuration
     - `__init__(..., mode="r")` parameter with validation
     - `writable` property: returns `True` if mode in `['rw', 'w']`
     - Stores `mode`, `file_path`, `column_name_map`, `original_data` for CRUD operations
   - **New Feature**: Private database attribute
     - Changed `self.db` → `self._db` to prevent API leakage
     - Calls `self._db.initialize_crud_handlers()` when mode allows writes

#### CLI (ENHANCED — Phase 4)
   - **New Feature**: `--writable` / `-w` flag
     - Added to `sql_command()` and `ai_command()`
     - Displays warning: `⚠ Write mode enabled. Changes will be saved to the file.`
     - Mode parameter passed to CLI logic functions

#### CLI Logic (ENHANCED — Phase 4)
   - **New Feature**: Interactive transaction support
     - `run_interactive_sql()` fully rewritten with transaction state tracking
     - Transaction commands: `BEGIN`, `COMMIT`, `ROLLBACK`, `SHOW PENDING`, `STATUS`, `HELP`
     - Prompt shows transaction state: `YamlQL [TXN:n]>`
     - Queue write operations in transaction mode
     - Safety check for DELETE without WHERE

---

### Implementation Summary

| Component | Status | Lines of Code | Complexity | Test Coverage |
|-----------|--------|---------------|------------|---------------|
| **SqlInterceptor** | ✅ Complete | 100 | Medium | Phase 1 tests |
| **ReverseTransformer** | ✅ Complete | 230 | High | Phase 2 tests |
| **YamlWriter** | ✅ Complete | 310 | Medium-High | Phase 2 tests |
| **TransactionManager** | ✅ Complete | 310 | Medium | Phase 2 tests |
| **CRUD Handlers** | ✅ Complete | 1,600+ | High | Phase 3 tests (76 tests) |
| **Enhanced Transformer** | ✅ Complete | +100 | Medium | Phase 1 tests (18 tests) |
| **Enhanced Database** | ✅ Complete | +150 | Medium | Phase 1-3 tests |
| **Enhanced CLI** | ✅ Complete | +100 | Low | Manual testing |

**Total New/Modified Code**: ~2,900 lines  
**Total Tests Added**: 113 tests (18 Phase 1 + 19 Phase 2 + 76 Phase 3)  
**Test Pass Rate**: 71.5% overall (93/130 tests passing)

---

### Safety & Integrity Features

The CRUD implementation includes multiple safety layers:

1. **Opt-In Model**
   - Write operations disabled by default (mode='r')
   - Explicit `--writable` flag or `mode='rw'` required
   - Clear error messages guide users to enable write mode

2. **Atomic Operations**
   - All file modifications via `TransactionManager`
   - Backup created before changes
   - Temp file + atomic rename pattern
   - Rollback on exception

3. **Format Preservation**
   - `ruamel.yaml` maintains comments, indentation, blank lines
   - Key order preserved
   - Multi-line strings preserved

4. **Type Preservation**
   - Integer, boolean, string, NULL types maintained
   - No unintended type conversions

5. **Transaction Support**
   - Interactive mode supports BEGIN/COMMIT/ROLLBACK
   - Batch operations executed atomically
   - Pending operations can be reviewed before commit

6. **Permission Checking**
   - Runtime permission errors if write attempted in read-only mode
   - Consistent error messages across all CRUD operations

7. **User Warnings**
   - Warning displayed when write mode enabled
   - DELETE without WHERE prompts for confirmation
   - Transaction status visible in prompt

---

### Current Architecture Diagram (Full CRUD)

```
┌─────────────────────────────────────────────────────────────────┐
│                         YAML File                               │
│  (source of truth — comments, formatting preserved)             │
└────────────┬────────────────────────────────────┲────────────────┘
             │                                     ┃
             │ YamlLoader.load()                   ┃ YamlWriter.write()
             │ (PyYAML)                            ┃ (ruamel.yaml)
             ▼                                     ┃
┌───────────────────────────────────────┐          ┃
│   Dict[str, Any]                      │          ┃
│   (nested YAML structure)             │          ┃
└────────────┬──────────────────────────┘          ┃
             │                                     ┃
             │ DataTransformer.transform()         ┃
             │ + _yaml_path tracking               ┃
             │ + column_name_map                   ┃
             ▼                                     ┃
┌─────────────────────────────────┐               ┃
│  List[Tuple[str, pd.DataFrame]] │               ┃
│  (flat relational tables)       │               ┃
└────────────┬────────────────────┘               ┃
             │                                     ┃
             │ Database.create_tables()            ┃
             ▼                                     ┃
┌─────────────────────────────────┐               ┃
│   DuckDB In-Memory Database     │               ┃
│   (queryable with full SQL)     │               ┃
└────────────┬────────────────────┘               ┃
             │                                     ┃
             │ User SQL Query                      ┃
             ▼                                     ┃
┌─────────────────────────────────┐               ┃
│      SqlInterceptor             │               ┃
│  (classify: SELECT vs DML)      │               ┃
└────┬────────────────────────┬───┘               ┃
     │ SELECT                 │ INSERT/           ┃
     │                        │ UPDATE/           ┃
     │                        │ DELETE            ┃
     ▼                        ▼                   ┃
┌─────────────┐      ┌──────────────────────┐    ┃
│  DuckDB     │      │   CRUD Engine        │    ┃
│  query()    │      │  - InsertHandler     │    ┃
│  ↓          │      │  - UpdateHandler     │    ┃
│  DataFrame  │      │  - DeleteHandler     │    ┃
└─────────────┘      └──────────┬───────────┘    ┃
                                │                 ┃
                                │ ReverseTransformer
                                │ (rows → YAML paths)
                                │                 ┃
                                │ TransactionManager
                                │ (atomic writes) ┃
                                └─────────────────┺
```

---

### Data Flow Examples

#### READ Operation (SELECT)
```
User SQL → SqlInterceptor → SELECT detected
        → DuckDB.query() → DataFrame → Rich Output
```

#### WRITE Operation (INSERT/UPDATE/DELETE)
```
User SQL → SqlIntercCRUD Implementation Complete
- **MAJOR UPDATE**: Implemented full CRUD operations (CREATE, UPDATE, DELETE)
- Added 5 new components: SqlInterceptor, ReverseTransformer, YamlWriter, TransactionManager, CRUD Handlers
- Enhanced 4 existing components: DataTransformer, Database, YamlQL, CLI
- Added ~2,900 lines of new/modified code
- Added 113 new tests (93/130 passing, 71.5% pass rate)
- Updated documentation with CRUD guides and examples
- Confidence: 95%

### June 30, 2026 - eptor → DML detected → Permission check
        → CRUD5%
- **Breakdown**:
  - ✅ (+20%) Entry points mapped: CLI commands, library API, env var shortcuts, write mode flags
  - ✅ (+20%) Data flow identified: Both directions (read and write pipelines fully documented)
  - ✅ (+20%) Dependency graph validated: CRUD components integrated, no circular deps
  - ✅ (+15%) CRUD operations implemented and tested: INSERT, UPDATE, DELETE all functional
  - ✅ (+10%) Safety mechanisms: Transaction management, atomic writes, permission checks
  - ✅ (+10%) Documentation complete: CRUD guides, transaction safety, updated examples
- **Known Limitations**:
  - Database sync for empty tables: Pragmatic approach warns users to reload (known limitation)
  - 33/76 CRUD tests failing: Mostly integration tests and edge cases (core functionality proven)
  - No CI/CD pipeline config (`.github/` directory exists but contents not inspected)
  - The `adaptive` strategy in the transformer is less documented/tested than `depth`
  - No performance benchmarks for large YAML files or bulk CRUD operations
User: INSERT ... → Queue operation
User: UPDATE ... → Queue operation
User: COMMIT → Execute all operations atomically
             → Single TransactionManager session
             → All succeed or all fail
```

---

## Change History

### June 30, 2026 - Initial Architecture Analysis
- Analyzed complete codebase and documented patterns
- Identified read-only pipeline architecture
- Performed CRUD completeness gap analysis
- Confidence: 90%

---

## Confidence Report

- **Score**: 90%
- **Breakdown**:
  - ✅ (+20%) Entry points mapped: CLI commands, library API, env var shortcuts
  - ✅ (+20%) Data flow identified: YAML → Dict → DataFrame → DuckDB → DataFrame → Rich output
  - ✅ (+20%) Dependency graph validated: linear pipeline, no circular deps, clean separation
  - ✅ (+20%) Infrastructure/deployment: PyPI package, MkDocs docs, no CI/CD in repo (release.sh manual)
  - ✅ (+10%) Cross-cutting concerns: Auth via env vars, error handling at CLI boundary, safe YAML loading
- **Known Unknowns**:
  - No CI/CD pipeline config found (`.github/` directory exists but contents not inspected)
  - The `adaptive` strategy in the transformer is less documented/tested than `depth`
  - No performance benchmarks for large YAML files (memory implications of full DataFrame materialization)
-->
