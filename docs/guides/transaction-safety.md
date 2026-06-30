# Transaction Safety Guide

YamlQL provides multiple layers of safety to ensure your YAML files remain consistent and recoverable, even when write operations fail.

## How Transactions Work

YamlQL uses a **three-phase commit** approach for all write operations:

```
1. BEGIN: Create backup of original file
2. MODIFY: Make changes to in-memory copy
3. COMMIT: Atomically write changes to disk
```

If any step fails, the original file remains unchanged.

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
3. Validate YAML syntax
4. Atomic rename: `config.yaml.tmp` → `config.yaml`
5. Cleanup backup on success

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

# NOW the file is updated atomically
```

**Benefits:**
- All operations succeed or all fail together
- File is only touched once
- Validation happens before any writes
- Easy to review changes before committing

## Rollback Mechanisms

### Automatic Rollback

If any error occurs during a transaction, changes are automatically rolled back:

```python
from yamlql_library import YamlQL

yql = YamlQL("config.yaml", mode="rw")

try:
    # Start multiple operations
    yql.query("INSERT INTO users VALUES ('Alice', 30)")
    yql.query("INSERT INTO users VALUES ('Bob', 'invalid_age')")  # This will fail
except Exception:
    # Original file is untouched
    print("❌ Transaction failed, file unchanged")
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

YamlQL preserves your YAML file's formatting:

**Before:**
```yaml
# Production Configuration
services:
  # Main API server
  - name: api
    port: 8080
    # Enable for debugging
    debug: false
```

**After:** `UPDATE services SET port = 8081 WHERE name = 'api'`
```yaml
# Production Configuration
services:
  # Main API server
  - name: api
    port: 8081  # <-- Only this changed
    # Enable for debugging
    debug: false
```

**Preserved:**
- ✅ Comments
- ✅ Indentation
- ✅ Blank lines
- ✅ Key order
- ✅ Multi-line strings

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

### 1. Always Use Transactions for Batch Operations

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
# File is written once atomically
```

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

**YamlQL Transaction Safety Guarantees:**

✅ Atomic operations - all or nothing  
✅ Automatic backups before changes  
✅ Format preservation  
✅ Type preservation  
✅ Validation before commit  
✅ Automatic rollback on errors  
✅ Opt-in write mode  
✅ Warning for dangerous operations  

**What You Should Do:**

1. Use version control (Git)
2. Test on copies first
3. Use transactions for batch operations
4. Validate after writes
5. Keep backups

## Next Steps

- [CRUD Operations Guide](crud-operations.md) - Learn INSERT/UPDATE/DELETE syntax
- [Quick Start Guide](../getting-started/quick-start.md) - Get started quickly
- [API Reference](../api/yamlql.md) - Full API documentation
