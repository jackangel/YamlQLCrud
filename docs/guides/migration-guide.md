# Migration Guide: Upgrading to YamlQL with CRUD Support

This guide helps existing YamlQL users understand the changes and upgrade to the CRUD-enabled version.

## Summary of Changes

**Good News**: YamlQL remains **100% backward compatible** with existing read-only usage!

**What's New**:
- ✅ Full CRUD operations (INSERT, UPDATE, DELETE)
- ✅ Write mode with explicit opt-in (`--writable` flag)
- ✅ Transaction support with BEGIN/COMMIT/ROLLBACK
- ✅ Format-preserving writes (comments, indentation maintained)
- ✅ Type preservation (int, bool, string, null)

**What Hasn't Changed**:
- Default behavior is read-only (same as before)
- All existing SELECT queries work exactly as before
- API remains the same for read operations
- No changes to transformation strategies

## Do You Need to Change Anything?

### If You Only Use SELECT Queries: **NO CHANGES NEEDED**

Your existing code and scripts will continue to work exactly as before:

```bash
# This works exactly as before - no changes needed
yamlql sql -f config.yaml "SELECT * FROM services"
yamlql discover -f config.yaml
yamlql ai -f config.yaml "What services are defined?"
```

```python
# This works exactly as before - no changes needed
from yamlql_library import YamlQL

yql = YamlQL("config.yaml")
data = yql.query("SELECT * FROM services")
print(data)
yql.close()
```

### If You Want to Use Write Operations: **EXPLICIT OPT-IN**

To use the new CRUD features, you must explicitly enable write mode:

**CLI:**
```bash
# Add the --writable flag
yamlql sql -f config.yaml --writable "INSERT INTO services VALUES ('api', 8080)"
```

**Python API:**
```python
# Add mode='rw' parameter
yql = YamlQL("config.yaml", mode="rw")
yql.query("INSERT INTO services VALUES ('api', 8080)")
yql.close()
```

## Upgrading Your Installation

### Via pip

```bash
# Upgrade to the latest version
pip install --upgrade yamlql

# Verify installation
yamlql --version
```

### Via Poetry

```bash
poetry update yamlql
```

### Via requirements.txt

Update your `requirements.txt`:
```
yamlql>=1.0.0  # or whatever the CRUD-enabled version is
```

Then:
```bash
pip install -r requirements.txt --upgrade
```

## New Dependencies

The CRUD-enabled version adds two new dependencies:

- `sqlglot>=23.0.0` - SQL parsing and classification
- `ruamel.yaml>=0.18.0,<0.20` - Format-preserving YAML writes

These are automatically installed when you upgrade YamlQL via pip.

The writer is certified on ruamel.yaml 0.18.0, 0.18.17, 0.19.0, and 0.19.1.
At `YamlWriter` construction, a version guard and round-trip capability probe
run before any write. Install a version in the certified range if the guard
reports an incompatible runtime.

## API Changes (Optional)

### New Parameters

#### YamlQL Constructor

```python
# Before (still works)
yql = YamlQL("config.yaml")

# New optional parameter
yql = YamlQL("config.yaml", mode="r")   # Read-only (default)
yql = YamlQL("config.yaml", mode="rw")  # Read-write
yql = YamlQL("config.yaml", mode="w")   # Write-only (rarely used)
```

#### CLI Commands

```bash
# Before (still works)
yamlql sql -f config.yaml "SELECT * FROM services"

# New optional flag
yamlql sql -f config.yaml --writable "INSERT INTO services VALUES (...)"
yamlql sql -f config.yaml -w "UPDATE services SET ..."  # Short flag
```

### New Return Format for Write Operations

Write operations return a dictionary with status information:

```python
yql = YamlQL("config.yaml", mode="rw")

result = yql.query("INSERT INTO services VALUES ('api', 8080)")
print(result)
# Output:
# {
#   'success': True,
#   'message': '✅ 1 row inserted',
#   'rows_affected': 1
# }
```

Read operations still return a DataFrame as before:

```python
result = yql.query("SELECT * FROM services")
print(type(result))  # <class 'pandas.core.frame.DataFrame'>
```

## Behavioral Changes

### 1. Enhanced DataFrames (Invisible to Most Users)

DataFrames now include an additional `_yaml_path` column:

```python
yql = YamlQL("config.yaml")
data = yql.query("SELECT * FROM services")
print(data.columns)
# Before: ['name', 'port']
# After:  ['name', 'port', '_yaml_path']
```

**Impact**: Low — You can ignore this column or filter it out if needed:

```python
# Exclude _yaml_path from results
data = data[[col for col in data.columns if col != '_yaml_path']]
```

### 2. Permission Errors for Write Operations (Without --writable)

Previously, if someone accidentally tried a write operation in read-only mode, DuckDB might have accepted it (but changes wouldn't persist).

Now, write operations without explicit permission raise an error:

```bash
$ yamlql sql -f config.yaml "INSERT INTO services VALUES ('api', 8080)"
❌ Error: INSERT operations require write mode. Use --writable flag.
```

**Impact**: Low — This is a safety improvement. Simply add `--writable` if you intend to write.

## Testing Your Upgrade

### Step 1: Test Read Operations

Ensure all your existing queries still work:

```bash
# Test your existing queries
yamlql sql -f your-file.yaml "SELECT * FROM your_table"
yamlql discover -f your-file.yaml
```

Expected: Identical output as before.

### Step 2: Test Write Operations (Optional)

Create a **copy** of your YAML file and test CRUD operations:

```bash
# Copy your file first
cp production.yaml test.yaml

# Test INSERT
yamlql sql -f test.yaml --writable "INSERT INTO services VALUES ('test', 9999)"

# Verify
yamlql sql -f test.yaml "SELECT * FROM services WHERE name = 'test'"

# Check the YAML file
cat test.yaml
```

Expected: New data appears in both SQL query and YAML file, formatting preserved.

## Common Migration Scenarios

### Scenario 1: CI/CD Pipeline (Read-Only)

**No changes needed.** Your pipeline continues to work:

```yaml
# .github/workflows/analyze.yml
- name: Analyze Kubernetes manifests
  run: |
    yamlql sql -f k8s/*.yaml "SELECT name, image FROM containers" > report.txt
```

### Scenario 2: Automated Configuration Updates (New Feature)

Add write mode to enable automated updates:

```bash
# Before: Manual YAML editing
yq eval '.services.api.replicas = 5' config.yaml

# After: Use YamlQL with SQL
yamlql sql -f config.yaml --writable \
  "UPDATE services SET replicas = 5 WHERE name = 'api'"
```

### Scenario 3: Python Scripts for Data Analysis

**No changes needed.** Your scripts continue to work:

```python
from yamlql_library import YamlQL
import pandas as pd

yql = YamlQL("data.yaml")
users = yql.query("SELECT * FROM users WHERE age > 18")
pd.DataFrame(users).to_csv("adults.csv")
yql.close()
```

### Scenario 4: Python Scripts for Configuration Management (New)

Enable write mode for automated config management:

```python
from yamlql_library import YamlQL

# Read-write mode
yql = YamlQL("config.yaml", mode="rw")

# Update configurations
yql.query("UPDATE services SET replicas = 5 WHERE environment = 'production'")
yql.query("DELETE FROM services WHERE status = 'deprecated'")

yql.close()
```

## Rollback Plan

If you encounter issues with the CRUD-enabled version:

### Option 1: Use Read-Only Mode Only

Simply avoid using `--writable` or `mode='rw'`. The system behaves exactly as the old version.

### Option 2: Downgrade (Not Recommended)

```bash
# Downgrade to the last read-only version (example version number)
pip install yamlql==0.9.0
```

**Note**: Check your installation history for the exact version number:
```bash
pip show yamlql
```

### Option 3: Pin Your Version

In `requirements.txt`:
```
yamlql==1.0.0  # Pin to specific version to prevent auto-upgrades
```

## Troubleshooting

### Issue: "ImportError: cannot import name 'ReverseTransformer'"

**Cause**: Incomplete upgrade or cached bytecode.

**Solution**:
```bash
# Clear cache and reinstall
pip cache purge
pip uninstall yamlql
pip install yamlql
```

### Issue: "PermissionError: INSERT operations require write mode"

**Cause**: Attempting write operation without `--writable` flag.

**Solution**: Add the flag:
```bash
yamlql sql -f config.yaml --writable "INSERT INTO ..."
```

### Issue: "Extra `_yaml_path` column appearing in results"

**Cause**: New metadata column added for CRUD support.

**Solution**: Filter it out if needed:
```python
data = data[[col for col in data.columns if col != '_yaml_path']]
```

Or exclude in SQL:
```sql
SELECT name, port FROM services  -- Don't use SELECT *
```

### Issue: "Format changes after write operation"

**Cause**: This should not happen with `ruamel.yaml`, but if it does...

**Solution**: Report as a bug and include:
- Original YAML file
- SQL query executed
- Expected vs actual output

## Getting Help

- **Documentation**: [docs.yamlql.com](https://docs.yamlql.com)
- **CRUD Operations Guide**: [docs/guides/crud-operations.md](crud-operations.md)
- **Transaction Safety Guide**: [docs/guides/transaction-safety.md](transaction-safety.md)
- **GitHub Issues**: Report bugs or ask questions

## Best Practices for Migration

1. **Test on Copies First**
   - Always test CRUD operations on copies of production files
   - Verify formatting preservation and data correctness

2. **Use Version Control**
   - Commit your YAML files before making changes
   - Review diffs after CRUD operations
   - Easy rollback if needed

3. **Start with INSERT**
   - INSERT is the safest write operation (purely additive)
   - Use it to validate the write pipeline before trying UPDATE/DELETE

4. **Leverage Transaction Mode**
   - Use interactive mode with transactions for complex changes
   - Review pending operations before committing

5. **Monitor for Regressions**
   - Run your existing test suite after upgrading
   - Verify all read-only queries produce identical results

## Summary Checklist

- [ ] Read this migration guide completely
- [ ] Upgrade YamlQL via pip/poetry
- [ ] Test all existing read-only queries (should work identically)
- [ ] If using CRUD: Test write operations on **copies** of files
- [ ] Review [CRUD Operations Guide](crud-operations.md) for syntax
- [ ] Review [Transaction Safety Guide](transaction-safety.md) for best practices
- [ ] Update CI/CD pipelines if needed (add `--writable` where appropriate)
- [ ] Commit working state before deploying to production

## What's Next?

- Explore new CRUD capabilities: [CRUD Operations Guide](crud-operations.md)
- Learn about transaction safety: [Transaction Safety Guide](transaction-safety.md)
- Review updated examples: [Quick Start Guide](../getting-started/quick-start.md)

**Welcome to the full-featured YamlQL! 🎉**
