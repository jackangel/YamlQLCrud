"""Demonstration script for Task 1-4: SqlInterceptor integration into Database class

This script shows the behavior of the modified Database.query() method:
- SELECT queries execute normally
- INSERT/UPDATE/DELETE queries raise NotImplementedError with helpful message
"""

import sys
import pandas as pd
sys.path.insert(0, '.')

from yamlql_library.database import Database

# Initialize database
db = Database()

# Create sample table
sample_data = pd.DataFrame({
    'id': [1, 2, 3],
    'name': ['Alice', 'Bob', 'Charlie'],
    'age': [25, 30, 35]
})
db.create_tables([('users', sample_data)])

print("=" * 60)
print("Task 1-4 Integration Demonstration")
print("=" * 60)

# Test 1: SELECT query (should work)
print("\n✓ Test 1: SELECT query (existing behavior)")
print("SQL: SELECT * FROM users WHERE age > 25")
try:
    result = db.query("SELECT * FROM users WHERE age > 25")
    print("Result:")
    print(result)
    print("Status: SUCCESS ✓")
except Exception as e:
    print(f"Status: FAILED ✗ - {e}")

# Test 2: INSERT query (should raise NotImplementedError)
print("\n✓ Test 2: INSERT query (new interception)")
print("SQL: INSERT INTO users (id, name, age) VALUES (4, 'Dave', 40)")
try:
    result = db.query("INSERT INTO users (id, name, age) VALUES (4, 'Dave', 40)")
    print("Status: FAILED ✗ - Should have raised NotImplementedError")
except NotImplementedError as e:
    print(f"Status: BLOCKED (as expected) ✓")
    print(f"Error message: {e}")
except Exception as e:
    print(f"Status: UNEXPECTED ERROR ✗ - {e}")

# Test 3: UPDATE query (should raise NotImplementedError)
print("\n✓ Test 3: UPDATE query (new interception)")
print("SQL: UPDATE users SET age = 26 WHERE id = 1")
try:
    result = db.query("UPDATE users SET age = 26 WHERE id = 1")
    print("Status: FAILED ✗ - Should have raised NotImplementedError")
except NotImplementedError as e:
    print(f"Status: BLOCKED (as expected) ✓")
    print(f"Error message: {e}")
except Exception as e:
    print(f"Status: UNEXPECTED ERROR ✗ - {e}")

# Test 4: DELETE query (should raise NotImplementedError)
print("\n✓ Test 4: DELETE query (new interception)")
print("SQL: DELETE FROM users WHERE id = 3")
try:
    result = db.query("DELETE FROM users WHERE id = 3")
    print("Status: FAILED ✗ - Should have raised NotImplementedError")
except NotImplementedError as e:
    print(f"Status: BLOCKED (as expected) ✓")
    print(f"Error message: {e}")
except Exception as e:
    print(f"Status: UNEXPECTED ERROR ✗ - {e}")

# Test 5: DDL query (should work - DDL is not CRUD)
print("\n✓ Test 5: DDL query (should pass through)")
print("SQL: CREATE TABLE test_table (id INTEGER, value TEXT)")
try:
    result = db.query("CREATE TABLE test_table (id INTEGER, value TEXT)")
    print("Status: SUCCESS ✓ (DDL queries pass through)")
except Exception as e:
    print(f"Status: FAILED ✗ - {e}")

print("\n" + "=" * 60)
print("Integration Summary:")
print("=" * 60)
print("✓ SELECT queries: Execute normally in DuckDB")
print("✓ INSERT/UPDATE/DELETE: Blocked with helpful error")
print("✓ DDL queries: Pass through to DuckDB")
print("✓ No breaking changes to existing API")
print("=" * 60)

# Cleanup
db.close()
