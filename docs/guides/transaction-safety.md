# Transaction Safety Guide

YamlQL protects each individual SQL write statement with a file transaction.
It does not provide a multi-statement SQL transaction.

## How Transactions Work

YamlQL uses a **three-phase commit** approach for all write operations:

```
1. BEGIN: Create backup of original file
2. MODIFY: Make changes to in-memory copy
3. COMMIT: Atomically write changes to disk
```

For one SQL write statement, a failure before replacement leaves the source
unchanged. Tested rejected writer and SQL statements leave the file
byte-identical.

## Atomic File Operations

### Single Operation Mode

Every write operation in YamlQL is atomic by default:

```python
from yamlql_library import YamlQL

yql = YamlQL("config.yaml", mode="rw")

# This is atomic - either fully succeeds or fully fails
result = yql.query("INSERT INTO users VALUES ('Alice', 30)")

if result['success']:
    print("✅ File updated successfully")
else:
    print(f"❌ No changes made: {result['message']}")
```

**What happens internally:**
1. Backup created: `config.yaml.backup`
2. Changes written to: `config.yaml.tmp`
3. Validate every document in the YAML stream and retain the document count
4. Atomic rename: `config.yaml.tmp` → `config.yaml`
5. Cleanup backup on success

For multi-document streams, the writer renders the changed document and keeps
the source bytes of untouched documents in the tested cases.

### Transaction Mode (Interactive)

For batch operations, use explicit transaction control:

```bash
$ yamlql sql config.yaml --interactive --writable

YamlQL> begin
✅ Transaction started

YamlQL [TXN:0]> INSERT INTO services VALUES ('api', 8080);
YamlQL [TXN:1]> INSERT INTO services VALUES ('web', 3000);
YamlQL [TXN:2]> UPDATE services SET status = 'active';

# All operations are queued, no file changes yet

YamlQL [TXN:3]> commit
Committing 3 operations...
✅ Transaction committed (3 operations executed)

# Each queued statement now executes as its own file transaction
```

`BEGIN` queues SQL text, and `COMMIT` executes the queued statements one at a
time. `ROLLBACK` discards queued work before commit, but `COMMIT` is not one
atomic file transaction: if statement $n$ fails, statements $1..n-1$ can
already be committed.

## Rollback Mechanisms

### Automatic Rollback

If a single statement raises while its file transaction is active, its pending
write is rolled back. This does not roll back an earlier, separately committed
`yql.query()` call:

```python
from yamlql_library import YamlQL

yql = YamlQL("config.yaml", mode="rw")

try:
    # This successful statement commits before the next call begins.
    yql.query("INSERT INTO users VALUES ('Alice', 30)")
    yql.query("INSERT INTO users VALUES ('Bob', 'invalid_age')")
except Exception:
    print("❌ The failing statement was not committed; inspect earlier writes")
```

### Manual Rollback

In interactive mode, explicitly rollback if you change your mind:

```bash
YamlQL> begin
YamlQL [TXN:0]> DELETE FROM users WHERE age > 65;
YamlQL [TXN:1]> # Wait, I didn't mean to do that!
YamlQL [TXN:1]> rollback
✅ Transaction rolled back (1 operations discarded)
```

## Safety Checks

### 1. Dangerous Operation Warnings

YamlQL warns you before executing potentially destructive operations:

```bash
YamlQL> DELETE FROM users;
⚠ WARNING: DELETE without WHERE will remove ALL rows!
Are you sure you want to proceed? (y/n): 
```

### 2. Permission Checks

Write operations are blocked by default:

```bash
$ yamlql sql config.yaml "INSERT INTO users VALUES ('Alice', 30)"
❌ Error: INSERT operations require write mode. Use --writable flag.
```

**Required flags:**
- CLI: `--writable` or `-w`
- Python: `mode='rw'`

### 3. Schema Validation

Operations are validated before execution:

```python
result = yql.query("INSERT INTO nonexistent_table VALUES (1, 2)")
# Returns: {'success': False, 'message': "Table 'nonexistent_table' does not exist"}
```

### 4. Type Preservation

YamlQL maintains data types to prevent corruption:

```yaml
# Original
users:
  - name: Alice
    age: 30  # Integer

# After UPDATE users SET age = 31
users:
  - name: Alice
    age: 31  # Still integer, not string "31"
```

## Format Preservation

The writer's fidelity guarantees are limited to the golden-tested cases in the
[CRUD Operations Guide](crud-operations.md): detected indentation; LF/CRLF,
BOM, and trailing-newline handling; quote, block-scalar, and flow styles;
defined comment ownership; anchors, merge keys, tags, and document streams.
Numeric and boolean literal spelling is not guaranteed after an update.

## Structural and concurrency limits

- A SQL `UPDATE` cannot change a mapping or sequence to a scalar, including
    `NULL`, or the reverse. It is rejected before commit; only the writer's
    Python API offers `allow_kind_change=True`.
- Atomic replacement prevents torn files, not lost updates. YamlQL has no
    lock, source hash, or other concurrency control.
- In multi-document streams, use `doc{N}_{table}` for a mapping table or
    `doc{N}` for a root-list table when a statement must target one document.
    `_yamlql_documents` is read-only; scalar and null documents cannot be
    written through SQL. Unqualified tables continue to use later-definition-
    wins behavior.
- The writer is certified for `ruamel.yaml>=0.18.0,<0.20` (0.18.0, 0.18.17,
    0.19.0, and 0.19.1). A version guard and capability probe run before any
    write.

## Batch statements and timing

Compatible multi-row INSERT and DELETE statements for one list in one document
use one batch writer call; incompatible groups use the existing grouped or
sequential path. Batch errors fail the whole statement before commit. Recorded
single-row INSERT timings were 0.002100 s / 2.169384 s / 1.752429 s at 500
items and 0.002551 s / 7.750668 s / 4.805445 s at 1,000 items (writer edit /
first refresh / whole statement). The refresh was super-linear in this run,
and no 5,000-item timing was recorded. Whole-statement latency is parse-bound,
not sub-second.

## Recovery Strategies

### Strategy 1: Git Integration

The safest approach is to use version control:

```bash
# Before making changes
$ git add config.yaml
$ git commit -m "Snapshot before YamlQL changes"

# Make changes with YamlQL
$ yamlql sql config.yaml -w "UPDATE services SET port = 8081"

# Review changes
$ git diff config.yaml

# If something went wrong
$ git checkout config.yaml  # Revert to previous version
```

### Strategy 2: Manual Backup

Create backups before batch operations:

```bash
# Create backup
$ cp config.yaml config.yaml.backup

# Make changes
$ yamlql sql config.yaml -w "DELETE FROM old_services"

# If needed, restore
$ cp config.yaml.backup config.yaml
```

### Strategy 3: Dry-Run Testing

Test on a copy first:

```bash
# Copy to test file
$ cp production.yaml test.yaml

# Test changes
$ yamlql sql test.yaml -w "UPDATE services SET replicas = 5"

# Verify results
$ yamlql sql test.yaml "SELECT * FROM services"

# Apply to production
$ yamlql sql production.yaml -w "UPDATE services SET replicas = 5"
```

## Error Scenarios

### Scenario 1: Disk Full

```python
result = yql.query("INSERT INTO large_table VALUES (...)")
# Returns: {'success': False, 'message': 'Failed to commit transaction: No space left on device'}
# Original file is untouched
```

### Scenario 2: Permission Denied

```bash
$ yamlql sql /etc/readonly.yaml -w "UPDATE config SET value = 'new'"
❌ Error: Permission denied: /etc/readonly.yaml
# Original file is untouched
```

### Scenario 3: Invalid YAML After Modification

```python
# YamlQL validates before writing
result = yql.query("INSERT INTO users VALUES ('Invalid\tYAML', 30)")
# Internal validation catches this before writing
```

### Scenario 4: Concurrent Modifications

**⚠️ Not Protected:** YamlQL doesn't currently lock files. If two processes modify the same file simultaneously, the last write wins.

**Workaround:** Use file locking externally:

```python
import fcntl
from yamlql_library import YamlQL

with open("config.yaml", "r+") as f:
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock
    
    yql = YamlQL("config.yaml", mode="rw")
    yql.query("INSERT INTO users VALUES ('Alice', 30)")
    yql.close()
    
    # Lock released automatically
```

## Best Practices

### 1. Review Interactive Batches Before Commit

❌ **Bad:**
```bash
$ yamlql sql config.yaml -w "INSERT INTO users VALUES ('Alice', 30)"
$ yamlql sql config.yaml -w "INSERT INTO users VALUES ('Bob', 25)"
$ yamlql sql config.yaml -w "INSERT INTO users VALUES ('Charlie', 35)"
# File is written 3 times - inefficient and risky
```

✅ **Good:**
```bash
$ yamlql sql config.yaml --interactive -w
YamlQL> begin
YamlQL> INSERT INTO users VALUES ('Alice', 30);
YamlQL> INSERT INTO users VALUES ('Bob', 25);
YamlQL> INSERT INTO users VALUES ('Charlie', 35);
YamlQL> commit
# Each queued statement is written through its own file transaction
```

This improves reviewability, but does not make the batch atomic.

### 2. Validate After Write

```python
yql = YamlQL("config.yaml", mode="rw")
result = yql.query("UPDATE services SET replicas = 5")

# Verify the change
yql_verify = YamlQL("config.yaml")  # Fresh instance
data = yql_verify.query("SELECT replicas FROM services")
assert all(data['replicas'] == 5), "Update didn't apply correctly"
```

### 3. Use Version Control

```bash
# Always commit working state before bulk changes
$ git commit -am "Working state before migration"

# Make changes
$ yamlql sql data.yaml -w "..."

# Review and commit
$ git diff
$ git commit -am "Applied YamlQL migrations"
```

### 4. Test in Non-Production First

```bash
# Copy production to staging
$ cp production.yaml staging.yaml

# Test changes on staging
$ yamlql sql staging.yaml -w "DELETE FROM deprecated_services"

# Verify results
$ yamlql sql staging.yaml "SELECT * FROM services"

# Apply to production after verification
$ yamlql sql production.yaml -w "DELETE FROM deprecated_services"
```

### 5. Keep Operation Logs

```bash
# Log all write operations
$ yamlql sql config.yaml -w "INSERT INTO users VALUES ('Alice', 30)" 2>&1 | tee -a yamlql-operations.log
```

## Transaction State Machine

YamlQL transactions follow this state machine:

```
NOT_STARTED
    ↓ begin()
IN_PROGRESS
    ↓ commit() → COMMITTED
    ↓ rollback() → ROLLED_BACK
    ↓ exception → AUTO_ROLLBACK
```

**State guarantees:**
- `NOT_STARTED`: No file modifications possible
- `IN_PROGRESS`: Changes in memory only, file untouched
- `COMMITTED`: Changes successfully written to file
- `ROLLED_BACK`: All changes discarded, file untouched

## Advanced: Custom Transaction Handling

For programmatic control:

```python
from yamlql_library.transaction import TransactionManager

with TransactionManager("config.yaml") as txn:
    writer = txn.get_writer()
    writer.load()
    
    # Make multiple modifications
    writer.set_value("services.0.port", 8081)
    writer.set_value("services.1.port", 3001)
    
    # Commit happens automatically on context exit
    # Rollback happens automatically on exception
```

## Monitoring Transaction Health

```bash
# Check transaction status
YamlQL> status
Mode: Read-Write
File: config.yaml
Transaction: Active
Pending operations: 3
```

## Summary

**Per-statement safety guarantees:**

- Backup before mutation, temporary-file serialization, stream validation, and
    atomic replacement for one SQL write statement.
- Rollback of that statement when its write fails before replacement.
- The documented, golden-tested fidelity behaviors in the CRUD guide; not a
    general or byte-for-byte formatting guarantee for arbitrary edits.
- Opt-in write mode and dangerous-operation warnings.

**What You Should Do:**

1. Use version control (Git)
2. Test on copies first
3. Treat interactive batches as sequential statements, not an atomic transaction
4. Validate after writes
5. Keep backups

## Next Steps

- [CRUD Operations Guide](crud-operations.md) - Learn INSERT/UPDATE/DELETE syntax
- [Quick Start Guide](../getting-started/quick-start.md) - Get started quickly
- [API Reference](../api/yamlql.md) - Full API documentation
