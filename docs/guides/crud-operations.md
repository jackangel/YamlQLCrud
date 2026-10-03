# CRUD Operations Guide

YamlQL now supports full CRUD (Create, Read, Update, Delete) operations on YAML files through SQL syntax.

## Overview

By default, YamlQL operates in **read-only mode** for safety. To enable write operations, you must explicitly use the `--writable` flag (CLI) or `mode='rw'` parameter (Python API).

## Enabling Write Mode

### Command Line (CLI)

```bash
# Read-only mode (default)
yamlql sql data.yaml "SELECT * FROM users"

# Write mode - use --writable or -w flag
yamlql sql data.yaml --writable "INSERT INTO users VALUES ('Alice', 30)"
yamlql sql data.yaml -w "UPDATE users SET age = 31 WHERE name = 'Alice'"
```

### Python API

```python
from yamlql_library import YamlQL

# Read-only mode (default)
yql = YamlQL("data.yaml", mode="r")
result = yql.query("SELECT * FROM users")

# Read-write mode
yql = YamlQL("data.yaml", mode="rw")
result = yql.query("INSERT INTO users VALUES ('Bob', 25)")
yql.close()
```

## INSERT Operations

### Basic INSERT

```sql
-- Insert a single row
INSERT INTO users VALUES ('Alice', 30)

-- Insert with explicit column names
INSERT INTO users (name, age) VALUES ('Bob', 25)

-- Insert multiple rows
INSERT INTO users (name, age) VALUES 
    ('Charlie', 35),
    ('Diana', 28)
```

### NULL Values

```sql
-- Insert with NULL values
INSERT INTO users (name, email, age) VALUES ('Eve', NULL, 40)
```

**Result in YAML:**
```yaml
users:
- name: Alice
  age: 30
- name: Bob
  age: 25
- name: Eve
  email:  # NULL written as empty/null
  age: 40
```

### Empty Lists

YamlQL automatically creates tables for empty lists:

```yaml
# Starting with empty list
users: []
```

```sql
-- This works!
INSERT INTO users (name, age) VALUES ('First User', 25)
```

**Result:**
```yaml
users:
- name: First User
  age: 25
```

## UPDATE Operations

### Basic UPDATE

```sql
-- Update single column
UPDATE users SET age = 31 WHERE name = 'Alice'

-- Update multiple columns
UPDATE users SET age = 31, email = 'alice@example.com' WHERE name = 'Alice'

-- Update all rows (use with caution!)
UPDATE users SET status = 'active'
```

### Expression-Based Updates

```sql
-- Increment age by 1
UPDATE users SET age = age + 1 WHERE name = 'Alice'

-- Arithmetic operations
UPDATE products SET price = price * 1.1 WHERE category = 'electronics'
```

### Complex WHERE Clauses

```sql
-- Multiple conditions
UPDATE users SET status = 'inactive' WHERE age > 65 AND last_login < '2020-01-01'

-- IN clause
UPDATE users SET tier = 'premium' WHERE name IN ('Alice', 'Bob', 'Charlie')

-- BETWEEN clause
UPDATE products SET discount = 0.2 WHERE price BETWEEN 100 AND 500
```

### Structural-change guard

An SQL `UPDATE` cannot replace a mapping or sequence with a scalar (including
`NULL`), or replace a scalar with a mapping or sequence. The statement fails
before the file is committed. SQL reports the writer's kind error inside an
`UPDATE failed:` message, for example:

```text
UPDATE failed: Cannot update table '<table>', row '<path>', column '<column>': Refusing to change node at '<path>' from <kind> to <kind> (<PythonType>); pass allow_kind_change=True to override
```

There is no SQL opt-in for this. Python users of `YamlWriter` can explicitly
pass `allow_kind_change=True`; SQL users must make a shape-compatible update.

## DELETE Operations

### Basic DELETE

```sql
-- Delete with WHERE clause
DELETE FROM users WHERE age < 18

-- Delete multiple rows
DELETE FROM users WHERE status = 'inactive'
```

### Delete All Rows

```sql
-- ⚠️ WARNING: This removes ALL rows
DELETE FROM users

-- In interactive mode, you'll be prompted to confirm
```

### Pattern Matching

```sql
-- Using LIKE
DELETE FROM users WHERE email LIKE '%@temp.com'

-- Using NOT
DELETE FROM users WHERE NOT (age BETWEEN 18 AND 65)
```

## Data Types

YamlQL preserves data types:

```sql
-- Integer
INSERT INTO config VALUES ('max_connections', 100)

-- Float
INSERT INTO config VALUES ('timeout', 30.5)

-- Boolean
INSERT INTO config VALUES ('debug', true)

-- String
INSERT INTO config VALUES ('log_level', 'info')

-- NULL
INSERT INTO config (key, value) VALUES ('optional_setting', NULL)
```

**Result in YAML:**
```yaml
config:
- key: max_connections
  value: 100          # Integer preserved
- key: timeout
  value: 30.5         # Float preserved
- key: debug
  value: true         # Boolean preserved
- key: log_level
  value: info         # String
- key: optional_setting
  value:              # NULL/empty
```

## Transaction Mode (Interactive)

Use the interactive mode with transactions for batch operations:

```bash
$ yamlql sql data.yaml --interactive --writable
YamlQL> begin
✅ Transaction started

YamlQL [TXN:0]> INSERT INTO users VALUES ('Alice', 30);
📝 Operation queued (transaction has 1 operations)

YamlQL [TXN:1]> INSERT INTO users VALUES ('Bob', 25);
📝 Operation queued (transaction has 2 operations)

YamlQL [TXN:2]> show pending
Transaction has 2 pending operations:
  1. INSERT INTO users VALUES ('Alice', 30)
  2. INSERT INTO users VALUES ('Bob', 25)

YamlQL [TXN:2]> commit
✅ Transaction committed (2 operations executed)

YamlQL> exit
```

### Transaction Commands

- `begin` - Start a transaction
- `commit` - Execute all queued operations
- `rollback` - Discard all queued operations
- `show pending` - List queued operations
- `status` - Show connection and transaction state

`BEGIN` queues statements, but `COMMIT` executes them one at a time. It is not
one atomic multi-statement file transaction: an earlier statement can remain
committed if a later statement fails.

## Safety Features

### 1. Opt-In Model

Write operations are **disabled by default**. You must explicitly enable them:

```bash
# This will fail
$ yamlql sql data.yaml "INSERT INTO users VALUES ('Alice', 30)"
Error: INSERT operations require write mode. Use --writable flag.

# This works
$ yamlql sql data.yaml --writable "INSERT INTO users VALUES ('Alice', 30)"
✅ 1 row inserted
```

### 2. Atomic Operations

All write operations are atomic - either they fully succeed or fully fail with no partial changes.

### 4. Backup on Errors

Each SQL write statement uses a backup, a same-directory temporary file,
stream validation, and atomic replacement. The tested rejection cases leave
the source bytes unchanged.

## Tested write fidelity and edit policies

These guarantees apply to the covered writer and SQL CRUD cases; they are not
a claim that arbitrary YAML source syntax is preserved unchanged.

| Area | Tested behavior |
| --- | --- |
| Physical layout | Edits retain LF or CRLF, UTF-8 BOM, trailing-newline suffixes, detected indentation, and the dominant indentation style in a mixed file. |
| Scalars and collections | Existing single/double quotes and block-scalar indicator/chomping are retained on string updates. Existing flow collections stay flow; new values adopt tested local sibling/row styles. Numeric and boolean literal spelling is not guaranteed after an update. |
| Comments | A comment block directly above an item with no blank line, and that item's end-of-line comment, are deleted with the item. Blank-line-separated blocks and comments before the next top-level key remain. |
| Anchors and aliases | Updating an anchored definition retains its anchor and updates aliases. Updating an alias occurrence replaces only that occurrence with a literal. Deleting an anchor still referenced by aliases is rejected. |
| Merge keys and tags | Setting an inherited merge-only key adds an own-key override; deleting that merge-only key is rejected. Tested custom tags, including `!Ref`, remain readable on the SQL read path and remain intact when another node is updated. |
| Document streams | Writer edits preserve untouched documents. An unqualified path targets the last mapping document defining its top-level key. Qualified tables target one document; scalar and null documents are preserved but are not writable through SQL. |

No rename, move, or set-comment SQL/Python editing APIs are provided.

### Reused anchor names

Within a document, aliases with a reused anchor name bind to the nearest
preceding definition of that name. Anchor identity is document-scoped. A
statement is rejected rather than guessed when identity cannot be established:

```text
Cannot resolve anchor '<name>' identity in document <N>
```

The transaction then leaves the file unchanged. One accepted limitation is a
multi-row `UPDATE` that first turns an alias in one same-name-anchor group into
a literal and then edits a definition in another group: it is rejected with
that policy error rather than attempting to infer the changed group.

## Mapping-valued columns

Mapping-valued fields retain the existing flattened columns by default. To add
direct mapping columns, construct the Python API with
`YamlQL(path, expose_mapping_columns=True)`. Each added column contains a
canonical JSON object: keys are sorted, separators are compact, non-ASCII text
is kept, dates and times use ISO-8601, sets become sorted arrays, and bytes
become base64 text. A value without a JSON form is `NULL`. A mapping column is
skipped when its name collides with an existing scalar or flattened column.

Writing one of these columns requires a JSON object. It replaces the YAML
mapping in the writer's local style. Child comments, child quote styles, and
child tags are not retained by this replacement. Invalid JSON, JSON of another
kind, text `null`, and SQL `NULL` reach the mapping-to-non-mapping kind guard
inside `UPDATE failed:`. Replacements containing a merge key, replacements
that would discard an aliased descendant, and parent/child column conflicts
are rejected before a write.

## Document-qualified tables

For a multi-document stream, mapping tables are also available as
`doc{N}_{table}`. A root-list document is `doc{N}` and can also expose child
tables. `_yamlql_documents` lists document metadata and is read-only. Qualified
writes modify only the selected document. Existing unqualified tables retain
their merged, later-definition-wins behavior.

## Common Patterns

### 1. Configuration Management

```sql
-- Add new service
INSERT INTO services VALUES ('api', 8080, 'active')

-- Update service port
UPDATE services SET port = 8081 WHERE name = 'api'

-- Remove old services
DELETE FROM services WHERE status = 'deprecated'
```

### 2. Data Migration

```python
from yamlql_library import YamlQL

# Migrate data from one YAML to another
source = YamlQL("old_config.yaml")
dest = YamlQL("new_config.yaml", mode="rw")

# Read from source
users = source.query("SELECT * FROM users WHERE active = true")

# Write to destination
for _, user in users.iterrows():
    dest.query(f"INSERT INTO users VALUES ('{user['name']}', {user['age']})")

source.close()
dest.close()
```

### 3. Batch Updates

```sql
-- Use transactions for batch operations
BEGIN

INSERT INTO logs VALUES ('2024-01-01', 'System started')
INSERT INTO logs VALUES ('2024-01-02', 'User logged in')
INSERT INTO logs VALUES ('2024-01-03', 'Data updated')

COMMIT
```

## Limits of the SQL model

- Mapping columns are opt-in and replace a complete mapping only; they do not
  preserve child-level presentation metadata during replacement.
- SQL column names can collide after sanitization. Dots and numeric segments
  in YAML keys are ambiguous in dot paths.
- `_yamlql_documents` is read-only, and scalar or null stream documents cannot
  be written through SQL.
- There is no concurrency control. Atomic replacement prevents torn files,
  not lost updates between writers.
- The writer requires `ruamel.yaml>=0.18.0,<0.20` and checks both that range
  and required round-trip capabilities before it writes.

## Batch list operations and measured scale

Compatible multi-row `INSERT` and `DELETE` operations for the same list in the
same document use one batch writer call. Cross-document, cross-list, mixed,
and unsupported shapes use grouped or sequential handling. Batch insertion and
deletion fail fast: an error rolls back the entire statement. For a rejected
batch list insert, the policy message is:

```text
Cannot insert into table '<t>': <err>
```

The following recorded single-row INSERT measurements separate the in-memory
writer edit from the deferred refresh and the complete statement. No 5,000-item
timing was recorded.

| Items | Writer edit | First post-edit refresh | Whole statement |
| ---: | ---: | ---: | ---: |
| 500 | 0.002100 s | 2.169384 s | 1.752429 s |
| 1,000 | 0.002551 s | 7.750668 s | 4.805445 s |

Refresh is super-linear in this measurement and is not included in the edit
step. Whole-statement latency remains parse-bound and is not sub-second. In a
separate whole-INSERT comparison (HEAD versus this implementation), 500 items
took 2.87 s versus 1.89 s for one row and 145.35 s versus 2.03 s for 100 rows;
1,000 items took 8.72 s versus 5.46 s for one row and 483.68 s versus 5.56 s
for 100 rows.

## Error Handling

```python
from yamlql_library import YamlQL

yql = YamlQL("data.yaml", mode="rw")

try:
    result = yql.query("INSERT INTO users VALUES ('Alice', 30)")
    if result.get('success'):
        print(f"✅ {result['message']}")
    else:
        print(f"❌ {result['message']}")
except Exception as e:
    print(f"Error: {e}")
finally:
    yql.close()
```

## Best Practices

1. **Always use --writable explicitly** - Never set write mode as default
2. **Use interactive transactions only to queue/review work** - They are not an atomic batch write
3. **Test on copies first** - Try operations on a copy of your YAML file
4. **Add WHERE clauses to DELETE** - Avoid accidental full deletes
5. **Validate after writes** - Query to confirm changes applied correctly
6. **Keep backups** - YamlQL doesn't automatically version files

## Next Steps

- [Transaction Safety Guide](transaction-safety.md) - Learn about atomic operations
- [Quick Start Guide](../getting-started/quick-start.md) - Basic usage examples
- [API Reference](../api/yamlql.md) - Full API documentation
