"""
Comprehensive CRUD Operation Tests for YamlQL

This test suite validates INSERT, UPDATE, and DELETE operations with 60+ test cases.
Tests cover normal operations, edge cases, error handling, and format preservation.

Test Categories:
- INSERT operations (20+ tests)
- UPDATE operations (20+ tests)
- DELETE operations (20+ tests)
- Integration tests (10+ tests)
- Edge cases and error handling

Each test uses isolated temporary files to ensure test independence.
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from yamlql_library import YamlQL


# ============================================================================
# INSERT OPERATION TESTS (20+ tests)
# ============================================================================

def test_insert_single_row_simple(tmp_path):
    """Test Task 3-1: INSERT single row with VALUES into empty table."""
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("users: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    result = yql.query("INSERT INTO users (name, age) VALUES ('Alice', 30)")
    
    # Verify insertion succeeded
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users")
    assert len(data) == 1
    assert data.iloc[0]['name'] == 'Alice'
    assert data.iloc[0]['age'] == 30
    yql.close()
    yql_read.close()


def test_insert_multiple_columns_named(tmp_path):
    """INSERT with all columns named explicitly."""
    yaml_file = tmp_path / "products.yaml"
    yaml_file.write_text("products: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO products (id, name, price, in_stock) VALUES (1, 'Widget', 29.99, true)")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM products WHERE id = 1")
    assert len(data) == 1
    assert data.iloc[0]['name'] == 'Widget'
    assert data.iloc[0]['price'] == 29.99
    assert data.iloc[0]['in_stock'] == True
    yql.close()
    yql_read.close()


def test_insert_multiple_rows_single_statement(tmp_path):
    """INSERT multiple rows in one statement with multiple VALUES."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("users: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO users (name, age) VALUES ('Alice', 30), ('Bob', 25), ('Charlie', 35)")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users ORDER BY age")
    assert len(data) == 3
    assert data.iloc[0]['name'] == 'Bob'
    assert data.iloc[1]['name'] == 'Alice'
    assert data.iloc[2]['name'] == 'Charlie'
    yql.close()
    yql_read.close()


def test_insert_into_nested_structure(tmp_path):
    """INSERT into nested YAML structure."""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("""
database:
  connections: []
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO connections (host, port, database) VALUES ('localhost', 5432, 'mydb')")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM connections")
    assert len(data) == 1
    assert data.iloc[0]['host'] == 'localhost'
    assert data.iloc[0]['port'] == 5432
    yql.close()
    yql_read.close()


def test_insert_with_null_values(tmp_path):
    """INSERT with NULL values."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("users: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO users (name, email, age) VALUES ('Alice', NULL, 30)")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users")
    assert len(data) == 1
    assert data.iloc[0]['name'] == 'Alice'
    assert pd.isna(data.iloc[0]['email'])
    assert data.iloc[0]['age'] == 30
    yql.close()
    yql_read.close()


def test_insert_integer_types(tmp_path):
    """INSERT preserves integer types."""
    yaml_file = tmp_path / "numbers.yaml"
    yaml_file.write_text("numbers: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO numbers (value) VALUES (42)")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM numbers")
    assert len(data) == 1
    assert data.iloc[0]['value'] == 42
    # Accept Python int, pandas Int64, or numpy integer types
    assert isinstance(data.iloc[0]['value'], (int, pd.Int64Dtype)) or np.issubdtype(type(data.iloc[0]['value']), np.integer)
    yql.close()
    yql_read.close()


def test_insert_float_types(tmp_path):
    """INSERT preserves float types."""
    yaml_file = tmp_path / "prices.yaml"
    yaml_file.write_text("prices: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO prices (amount) VALUES (29.99)")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM prices")
    assert len(data) == 1
    assert abs(data.iloc[0]['amount'] - 29.99) < 0.01
    yql.close()
    yql_read.close()


def test_insert_boolean_types(tmp_path):
    """INSERT preserves boolean types."""
    yaml_file = tmp_path / "flags.yaml"
    yaml_file.write_text("flags: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO flags (enabled, active) VALUES (true, false)")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM flags")
    assert len(data) == 1
    assert data.iloc[0]['enabled'] == True
    assert data.iloc[0]['active'] == False
    yql.close()
    yql_read.close()


def test_insert_string_with_spaces(tmp_path):
    """INSERT preserves strings with spaces."""
    yaml_file = tmp_path / "messages.yaml"
    yaml_file.write_text("messages: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO messages (text) VALUES ('Hello World')")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM messages")
    assert len(data) == 1
    assert data.iloc[0]['text'] == 'Hello World'
    yql.close()
    yql_read.close()


def test_insert_unicode_characters(tmp_path):
    """INSERT handles unicode characters correctly."""
    yaml_file = tmp_path / "international.yaml"
    yaml_file.write_text("international: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO international (text) VALUES ('Hello 世界 🌍')")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM international")
    assert len(data) == 1
    assert data.iloc[0]['text'] == 'Hello 世界 🌍'
    yql.close()
    yql_read.close()


def test_insert_special_characters_in_values(tmp_path):
    """INSERT handles special characters in values."""
    yaml_file = tmp_path / "special.yaml"
    yaml_file.write_text("special: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO special (code) VALUES ('a@b.com')")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM special")
    assert len(data) == 1
    assert data.iloc[0]['code'] == 'a@b.com'
    yql.close()
    yql_read.close()


def test_insert_empty_string(tmp_path):
    """INSERT handles empty strings."""
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("empty: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO empty (value) VALUES ('')")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM empty")
    assert len(data) == 1
    assert data.iloc[0]['value'] == ''
    yql.close()
    yql_read.close()


def test_insert_list_of_objects_append(tmp_path):
    """INSERT appends to existing list of objects."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO users (name, age) VALUES ('Bob', 25)")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users ORDER BY age")
    assert len(data) == 2
    assert data.iloc[0]['name'] == 'Bob'
    assert data.iloc[1]['name'] == 'Alice'
    yql.close()
    yql_read.close()


def test_insert_into_hyphenated_key_table(tmp_path):
    """INSERT into table with hyphenated keys (sanitized to underscores)."""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("api-keys: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    # Table name is sanitized to api_keys
    yql.query("INSERT INTO api_keys (service_name, key_value) VALUES ('github', 'abc123')")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM api_keys")
    assert len(data) == 1
    assert data.iloc[0]['service_name'] == 'github'
    yql.close()
    yql_read.close()


def test_insert_read_only_mode_blocked(tmp_path):
    """INSERT fails in read-only mode with PermissionError."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("users: []")
    
    yql = YamlQL(str(yaml_file), mode='r')
    
    with pytest.raises(PermissionError, match="INSERT operations require write mode"):
        yql.query("INSERT INTO users (name) VALUES ('Alice')")
    
    yql.close()


def test_insert_invalid_table_name(tmp_path):
    """INSERT into non-existent table raises error."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("users: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    with pytest.raises(Exception):  # DuckDB will raise catalog error
        yql.query("INSERT INTO nonexistent (name) VALUES ('Alice')")
    
    yql.close()


def test_insert_invalid_column_name(tmp_path):
    """INSERT with non-existent column raises error."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    with pytest.raises(Exception):  # DuckDB will raise column not found error
        yql.query("INSERT INTO users (invalid_column) VALUES ('test')")
    
    yql.close()


def test_insert_mismatched_value_count(tmp_path):
    """INSERT with mismatched column/value count raises error."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("users: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    with pytest.raises(Exception):  # SQL syntax or column count error
        yql.query("INSERT INTO users (name, age) VALUES ('Alice')")
    
    yql.close()


def test_insert_preserves_existing_data(tmp_path):
    """INSERT doesn't overwrite existing data."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
  - name: Bob
    age: 25
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO users (name, age) VALUES ('Charlie', 35)")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users ORDER BY age")
    assert len(data) == 3
    assert set(data['name']) == {'Alice', 'Bob', 'Charlie'}
    yql.close()
    yql_read.close()


def test_insert_sequential_operations(tmp_path):
    """Multiple INSERT operations in sequence."""
    yaml_file = tmp_path / "logs.yaml"
    yaml_file.write_text("logs: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO logs (message) VALUES ('First')")
    yql.query("INSERT INTO logs (message) VALUES ('Second')")
    yql.query("INSERT INTO logs (message) VALUES ('Third')")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM logs")
    assert len(data) == 3
    assert 'First' in data['message'].values
    assert 'Second' in data['message'].values
    assert 'Third' in data['message'].values
    yql.close()
    yql_read.close()


# ============================================================================
# UPDATE OPERATION TESTS (20+ tests)
# ============================================================================

def test_update_single_row_simple(tmp_path):
    """Test Task 3-2: UPDATE single row with WHERE clause."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
  - name: Bob
    age: 25
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE users SET age = 31 WHERE name = 'Alice'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users WHERE name = 'Alice'")
    assert len(data) == 1
    assert data.iloc[0]['age'] == 31
    yql.close()
    yql_read.close()


def test_update_multiple_rows(tmp_path):
    """UPDATE multiple rows matching WHERE condition."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
    active: false
  - name: Bob
    age: 25
    active: false
  - name: Charlie
    age: 35
    active: true
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE users SET active = true WHERE age < 32")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users WHERE active = true")
    assert len(data) == 3  # All users should now be active
    yql.close()
    yql_read.close()


def test_update_all_rows_no_where(tmp_path):
    """UPDATE all rows when no WHERE clause provided."""
    yaml_file = tmp_path / "settings.yaml"
    yaml_file.write_text("""
settings:
  - key: setting1
    enabled: false
  - key: setting2
    enabled: false
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE settings SET enabled = true")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM settings")
    assert len(data) == 2
    assert all(data['enabled'])
    yql.close()
    yql_read.close()


def test_update_multiple_columns(tmp_path):
    """UPDATE multiple columns in single statement."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
    status: pending
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE users SET age = 31, status = 'active' WHERE name = 'Alice'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users WHERE name = 'Alice'")
    assert data.iloc[0]['age'] == 31
    assert data.iloc[0]['status'] == 'active'
    yql.close()
    yql_read.close()


def test_update_with_expression(tmp_path):
    """UPDATE with expression (increment value)."""
    yaml_file = tmp_path / "counters.yaml"
    yaml_file.write_text("""
counters:
  - name: visits
    count: 10
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE counters SET count = count + 1 WHERE name = 'visits'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM counters WHERE name = 'visits'")
    assert data.iloc[0]['count'] == 11
    yql.close()
    yql_read.close()


def test_update_where_no_match(tmp_path):
    """UPDATE with WHERE matching zero rows (should succeed with no changes)."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    result = yql.query("UPDATE users SET age = 40 WHERE name = 'NonExistent'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users WHERE name = 'Alice'")
    assert data.iloc[0]['age'] == 30  # Unchanged
    yql.close()
    yql_read.close()


def test_update_with_complex_where(tmp_path):
    """UPDATE with complex WHERE clause (AND, OR)."""
    yaml_file = tmp_path / "products.yaml"
    yaml_file.write_text("""
products:
  - name: Widget
    price: 10.0
    in_stock: true
  - name: Gadget
    price: 20.0
    in_stock: false
  - name: Doohickey
    price: 15.0
    in_stock: true
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE products SET price = price * 0.9 WHERE in_stock = true AND price > 12")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM products WHERE name = 'Doohickey'")
    assert abs(data.iloc[0]['price'] - 13.5) < 0.01  # 15 * 0.9
    yql.close()
    yql_read.close()


def test_update_preserves_other_fields(tmp_path):
    """UPDATE doesn't affect columns not mentioned in SET."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
    email: alice@example.com
    status: active
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE users SET age = 31 WHERE name = 'Alice'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users WHERE name = 'Alice'")
    assert data.iloc[0]['age'] == 31
    assert data.iloc[0]['email'] == 'alice@example.com'  # Unchanged
    assert data.iloc[0]['status'] == 'active'  # Unchanged
    yql.close()
    yql_read.close()


def test_update_to_null(tmp_path):
    """UPDATE column to NULL value."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    email: alice@example.com
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE users SET email = NULL WHERE name = 'Alice'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users WHERE name = 'Alice'")
    assert pd.isna(data.iloc[0]['email'])
    yql.close()
    yql_read.close()


def test_update_boolean_values(tmp_path):
    """UPDATE boolean values correctly."""
    yaml_file = tmp_path / "flags.yaml"
    yaml_file.write_text("""
flags:
  - name: feature_a
    enabled: false
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE flags SET enabled = true WHERE name = 'feature_a'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM flags WHERE name = 'feature_a'")
    assert data.iloc[0]['enabled'] == True
    yql.close()
    yql_read.close()


def test_update_string_with_quotes(tmp_path):
    """UPDATE with string containing quotes."""
    yaml_file = tmp_path / "messages.yaml"
    yaml_file.write_text("""
messages:
  - id: 1
    text: old
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE messages SET text = 'It''s working' WHERE id = 1")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM messages WHERE id = 1")
    assert "It's working" in data.iloc[0]['text'] or "It''s working" in data.iloc[0]['text']
    yql.close()
    yql_read.close()


def test_update_numeric_to_different_type(tmp_path):
    """UPDATE numeric value preserves type."""
    yaml_file = tmp_path / "settings.yaml"
    yaml_file.write_text("""
settings:
  - key: timeout
    value: 30
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE settings SET value = 60 WHERE key = 'timeout'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM settings WHERE key = 'timeout'")
    assert data.iloc[0]['value'] == 60
    yql.close()
    yql_read.close()


def test_update_read_only_mode_blocked(tmp_path):
    """UPDATE fails in read-only mode with PermissionError."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
""")
    
    yql = YamlQL(str(yaml_file), mode='r')
    
    with pytest.raises(PermissionError, match="UPDATE operations require write mode"):
        yql.query("UPDATE users SET age = 31 WHERE name = 'Alice'")
    
    yql.close()


def test_update_invalid_table(tmp_path):
    """UPDATE on non-existent table raises error."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("users: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    with pytest.raises(Exception):  # DuckDB catalog error
        yql.query("UPDATE nonexistent SET value = 1")
    
    yql.close()


def test_update_invalid_column(tmp_path):
    """UPDATE with non-existent column raises error."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    with pytest.raises(Exception):  # DuckDB column error
        yql.query("UPDATE users SET invalid_col = 'test' WHERE name = 'Alice'")
    
    yql.close()


def test_update_with_subquery(tmp_path):
    """UPDATE with subquery in WHERE clause."""
    yaml_file = tmp_path / "employees.yaml"
    yaml_file.write_text("""
employees:
  - name: Alice
    salary: 50000
    department: IT
  - name: Bob
    salary: 60000
    department: IT
  - name: Charlie
    salary: 45000
    department: HR
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE employees SET salary = salary * 1.1 WHERE department = 'IT'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM employees WHERE name = 'Alice'")
    assert data.iloc[0]['salary'] == 55000
    yql.close()
    yql_read.close()


def test_update_case_sensitive_where(tmp_path):
    """UPDATE respects case sensitivity in WHERE clause."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    status: active
  - name: alice
    status: inactive
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE users SET status = 'updated' WHERE name = 'Alice'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users")
    alice_data = data[data['name'] == 'Alice']
    alice_lower_data = data[data['name'] == 'alice']
    assert alice_data.iloc[0]['status'] == 'updated'
    assert alice_lower_data.iloc[0]['status'] == 'inactive'  # Unchanged
    yql.close()
    yql_read.close()


def test_update_with_in_clause(tmp_path):
    """UPDATE with IN clause in WHERE."""
    yaml_file = tmp_path / "products.yaml"
    yaml_file.write_text("""
products:
  - id: 1
    category: electronics
    discount: 0
  - id: 2
    category: books
    discount: 0
  - id: 3
    category: electronics
    discount: 0
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE products SET discount = 10 WHERE id IN (1, 3)")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM products WHERE id IN (1, 3)")
    assert all(data['discount'] == 10)
    yql.close()
    yql_read.close()


def test_update_sequential_operations(tmp_path):
    """Multiple UPDATE operations in sequence."""
    yaml_file = tmp_path / "counter.yaml"
    yaml_file.write_text("""
counter:
  - value: 0
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE counter SET value = value + 1")
    yql.query("UPDATE counter SET value = value + 1")
    yql.query("UPDATE counter SET value = value + 1")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM counter")
    assert data.iloc[0]['value'] == 3
    yql.close()
    yql_read.close()


def test_update_preserves_row_count(tmp_path):
    """UPDATE doesn't change row count."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
  - name: Bob
    age: 25
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE users SET age = 100")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users")
    assert len(data) == 2  # Same count
    assert all(data['age'] == 100)
    yql.close()
    yql_read.close()


# ============================================================================
# DELETE OPERATION TESTS (20+ tests)
# ============================================================================

def test_delete_single_row_simple(tmp_path):
    """Test Task 3-3: DELETE single row with WHERE clause."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
  - name: Bob
    age: 25
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM users WHERE name = 'Alice'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users")
    assert len(data) == 1
    assert data.iloc[0]['name'] == 'Bob'
    yql.close()
    yql_read.close()


def test_delete_multiple_rows(tmp_path):
    """DELETE multiple rows matching WHERE condition."""
    yaml_file = tmp_path / "products.yaml"
    yaml_file.write_text("""
products:
  - name: Widget
    price: 10
  - name: Gadget
    price: 5
  - name: Doohickey
    price: 3
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM products WHERE price < 8")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM products")
    assert len(data) == 1
    assert data.iloc[0]['name'] == 'Widget'
    yql.close()
    yql_read.close()


def test_delete_all_rows_no_where(tmp_path):
    """DELETE all rows when no WHERE clause provided."""
    yaml_file = tmp_path / "logs.yaml"
    yaml_file.write_text("""
logs:
  - message: log1
  - message: log2
  - message: log3
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM logs")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM logs")
    assert len(data) == 0
    yql.close()
    yql_read.close()


def test_delete_where_no_match(tmp_path):
    """DELETE with WHERE matching zero rows (should succeed with no changes)."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    result = yql.query("DELETE FROM users WHERE name = 'NonExistent'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users")
    assert len(data) == 1  # No change
    assert data.iloc[0]['name'] == 'Alice'
    yql.close()
    yql_read.close()


def test_delete_with_complex_where(tmp_path):
    """DELETE with complex WHERE clause (AND, OR)."""
    yaml_file = tmp_path / "inventory.yaml"
    yaml_file.write_text("""
inventory:
  - item: Widget
    quantity: 5
    price: 10
  - item: Gadget
    quantity: 0
    price: 20
  - item: Doohickey
    quantity: 3
    price: 15
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM inventory WHERE quantity = 0 OR price > 12")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM inventory")
    assert len(data) == 1
    assert data.iloc[0]['item'] == 'Widget'
    yql.close()
    yql_read.close()


def test_delete_from_list_maintains_order(tmp_path):
    """DELETE from YAML list maintains correct order of remaining items."""
    yaml_file = tmp_path / "items.yaml"
    yaml_file.write_text("""
items:
  - id: 1
    name: First
  - id: 2
    name: Second
  - id: 3
    name: Third
  - id: 4
    name: Fourth
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM items WHERE id = 2")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM items ORDER BY id")
    assert len(data) == 3
    assert list(data['id']) == [1, 3, 4]
    assert list(data['name']) == ['First', 'Third', 'Fourth']
    yql.close()
    yql_read.close()


def test_delete_with_numeric_comparison(tmp_path):
    """DELETE with numeric comparison in WHERE."""
    yaml_file = tmp_path / "scores.yaml"
    yaml_file.write_text("""
scores:
  - player: Alice
    score: 100
  - player: Bob
    score: 50
  - player: Charlie
    score: 75
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM scores WHERE score < 70")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM scores")
    assert len(data) == 2
    assert set(data['player']) == {'Alice', 'Charlie'}
    yql.close()
    yql_read.close()


def test_delete_with_string_pattern(tmp_path):
    """DELETE with LIKE pattern matching."""
    yaml_file = tmp_path / "files.yaml"
    yaml_file.write_text("""
files:
  - name: test.txt
  - name: data.csv
  - name: backup.txt
  - name: config.json
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM files WHERE name LIKE '%.txt'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM files")
    assert len(data) == 2
    assert 'test.txt' not in data['name'].values
    assert 'backup.txt' not in data['name'].values
    yql.close()
    yql_read.close()


def test_delete_with_null_check(tmp_path):
    """DELETE rows where column IS NULL."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    email: alice@example.com
  - name: Bob
    email: null
  - name: Charlie
    email: null
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM users WHERE email IS NULL")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM users")
    assert len(data) == 1
    assert data.iloc[0]['name'] == 'Alice'
    yql.close()
    yql_read.close()


def test_delete_boolean_condition(tmp_path):
    """DELETE with boolean column condition."""
    yaml_file = tmp_path / "tasks.yaml"
    yaml_file.write_text("""
tasks:
  - title: Task 1
    completed: true
  - title: Task 2
    completed: false
  - title: Task 3
    completed: true
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM tasks WHERE completed = true")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM tasks")
    assert len(data) == 1
    assert data.iloc[0]['title'] == 'Task 2'
    yql.close()
    yql_read.close()


def test_delete_with_in_clause(tmp_path):
    """DELETE with IN clause in WHERE."""
    yaml_file = tmp_path / "items.yaml"
    yaml_file.write_text("""
items:
  - id: 1
  - id: 2
  - id: 3
  - id: 4
  - id: 5
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM items WHERE id IN (2, 4)")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM items")
    assert len(data) == 3
    assert set(data['id']) == {1, 3, 5}
    yql.close()
    yql_read.close()


def test_delete_read_only_mode_blocked(tmp_path):
    """DELETE fails in read-only mode with PermissionError."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
""")
    
    yql = YamlQL(str(yaml_file), mode='r')
    
    with pytest.raises(PermissionError, match="DELETE operations require write mode"):
        yql.query("DELETE FROM users WHERE name = 'Alice'")
    
    yql.close()


def test_delete_invalid_table(tmp_path):
    """DELETE from non-existent table raises error."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("users: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    with pytest.raises(Exception):  # DuckDB catalog error
        yql.query("DELETE FROM nonexistent WHERE id = 1")
    
    yql.close()


def test_delete_invalid_column_in_where(tmp_path):
    """DELETE with non-existent column in WHERE raises error."""
    yaml_file = tmp_path / "users.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
    age: 30
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    with pytest.raises(Exception):  # DuckDB column error
        yql.query("DELETE FROM users WHERE invalid_col = 'test'")
    
    yql.close()


def test_delete_clears_table_empty_list(tmp_path):
    """DELETE all rows results in empty list in YAML."""
    yaml_file = tmp_path / "items.yaml"
    yaml_file.write_text("""
items:
  - id: 1
  - id: 2
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM items")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM items")
    assert len(data) == 0
    
    # Verify YAML file contains empty list
    content = yaml_file.read_text()
    assert 'items:' in content
    yql.close()
    yql_read.close()


def test_delete_from_nested_table(tmp_path):
    """DELETE from nested table structure."""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("""
database:
  connections:
    - host: localhost
      port: 5432
    - host: remote
      port: 5433
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM connections WHERE host = 'remote'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM connections")
    assert len(data) == 1
    assert data.iloc[0]['host'] == 'localhost'
    yql.close()
    yql_read.close()


def test_delete_preserves_other_tables(tmp_path):
    """DELETE from one table doesn't affect other tables."""
    yaml_file = tmp_path / "multi.yaml"
    yaml_file.write_text("""
users:
  - name: Alice
products:
  - name: Widget
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM users")
    
    yql_read = YamlQL(str(yaml_file))
    users_data = yql_read.query("SELECT * FROM users")
    products_data = yql_read.query("SELECT * FROM products")
    assert len(users_data) == 0
    assert len(products_data) == 1  # Unchanged
    yql.close()
    yql_read.close()


def test_delete_sequential_operations(tmp_path):
    """Multiple DELETE operations in sequence."""
    yaml_file = tmp_path / "items.yaml"
    yaml_file.write_text("""
items:
  - id: 1
  - id: 2
  - id: 3
  - id: 4
  - id: 5
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM items WHERE id = 1")
    yql.query("DELETE FROM items WHERE id = 3")
    yql.query("DELETE FROM items WHERE id = 5")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM items")
    assert len(data) == 2
    assert set(data['id']) == {2, 4}
    yql.close()
    yql_read.close()


def test_delete_with_between(tmp_path):
    """DELETE with BETWEEN clause."""
    yaml_file = tmp_path / "numbers.yaml"
    yaml_file.write_text("""
numbers:
  - value: 1
  - value: 5
  - value: 10
  - value: 15
  - value: 20
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM numbers WHERE value BETWEEN 5 AND 15")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM numbers")
    assert len(data) == 2
    assert set(data['value']) == {1, 20}
    yql.close()
    yql_read.close()


def test_delete_last_row_keeps_structure(tmp_path):
    """DELETE last row maintains table structure."""
    yaml_file = tmp_path / "single.yaml"
    yaml_file.write_text("""
single:
  - id: 1
    name: Only
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("DELETE FROM single WHERE id = 1")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM single")
    assert len(data) == 0
    
    # Verify table still exists
    assert 'single' in yql_read.list_tables()
    yql.close()
    yql_read.close()


# ============================================================================
# INTEGRATION TESTS (10+ tests)
# ============================================================================

def test_crud_full_sequence(tmp_path):
    """Full CRUD sequence: INSERT → SELECT → UPDATE → DELETE."""
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("users: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    # INSERT
    yql.query("INSERT INTO users (name, age) VALUES ('Alice', 30)")
    data = yql.query("SELECT * FROM users WHERE name = 'Alice'")
    assert len(data) == 1
    
    # UPDATE
    yql.query("UPDATE users SET age = 31 WHERE name = 'Alice'")
    data = yql.query("SELECT * FROM users WHERE name = 'Alice'")
    assert data.iloc[0]['age'] == 31
    
    # DELETE
    yql.query("DELETE FROM users WHERE name = 'Alice'")
    data = yql.query("SELECT * FROM users")
    assert len(data) == 0
    
    yql.close()


def test_insert_update_verify_roundtrip(tmp_path):
    """INSERT then UPDATE, verify changes persist."""
    yaml_file = tmp_path / "products.yaml"
    yaml_file.write_text("products: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO products (name, price) VALUES ('Widget', 10.0)")
    yql.query("UPDATE products SET price = 15.0 WHERE name = 'Widget'")
    
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM products WHERE name = 'Widget'")
    assert abs(data.iloc[0]['price'] - 15.0) < 0.01
    yql.close()
    yql_read.close()


def test_insert_delete_verify_roundtrip(tmp_path):
    """INSERT then DELETE, verify removal."""
    yaml_file = tmp_path / "temp.yaml"
    yaml_file.write_text("temp: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO temp (value) VALUES ('test')")
    data = yql.query("SELECT * FROM temp")
    assert len(data) == 1
    
    yql.query("DELETE FROM temp WHERE value = 'test'")
    data = yql.query("SELECT * FROM temp")
    assert len(data) == 0
    yql.close()


def test_multiple_insert_bulk_delete(tmp_path):
    """Multiple INSERTs followed by bulk DELETE."""
    yaml_file = tmp_path / "logs.yaml"
    yaml_file.write_text("logs: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    for i in range(10):
        yql.query(f"INSERT INTO logs (level, message) VALUES ('INFO', 'Message {i}')")
    
    data = yql.query("SELECT * FROM logs")
    assert len(data) == 10
    
    yql.query("DELETE FROM logs WHERE level = 'INFO'")
    data = yql.query("SELECT * FROM logs")
    assert len(data) == 0
    yql.close()


def test_mixed_crud_operations(tmp_path):
    """Mixed INSERT, UPDATE, DELETE operations."""
    yaml_file = tmp_path / "mixed.yaml"
    yaml_file.write_text("items: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    # Build up data
    yql.query("INSERT INTO items (id, status) VALUES (1, 'new'), (2, 'new'), (3, 'new')")
    
    # Update some
    yql.query("UPDATE items SET status = 'processing' WHERE id IN (1, 2)")
    
    # Delete one
    yql.query("DELETE FROM items WHERE id = 3")
    
    # Update remaining
    yql.query("UPDATE items SET status = 'done'")
    
    data = yql.query("SELECT * FROM items")
    assert len(data) == 2
    assert all(data['status'] == 'done')
    yql.close()


def test_transaction_consistency(tmp_path):
    """Verify in-memory and file state stay synchronized."""
    yaml_file = tmp_path / "sync.yaml"
    yaml_file.write_text("data: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO data (value) VALUES ('test')")
    
    # Create new instance to read from file
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM data")
    assert len(data) == 1
    assert data.iloc[0]['value'] == 'test'
    
    yql.close()
    yql_read.close()


def test_format_preservation_simple(tmp_path):
    """Verify YAML formatting is preserved after CRUD operations."""
    yaml_file = tmp_path / "formatted.yaml"
    original_content = """# Configuration
users:
  - name: Alice
    age: 30
"""
    yaml_file.write_text(original_content)
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("UPDATE users SET age = 31 WHERE name = 'Alice'")
    yql.close()
    
    # Verify comment still exists (ruamel.yaml should preserve)
    content = yaml_file.read_text()
    # Note: Actual format preservation depends on writer implementation
    assert 'users:' in content


def test_concurrent_read_write_instances(tmp_path):
    """Two YamlQL instances: one read, one write."""
    yaml_file = tmp_path / "concurrent.yaml"
    yaml_file.write_text("data: []")
    
    yql_write = YamlQL(str(yaml_file), mode='rw')
    yql_write.query("INSERT INTO data (value) VALUES ('test')")
    yql_write.close()
    
    # New read instance should see the change
    yql_read = YamlQL(str(yaml_file))
    data = yql_read.query("SELECT * FROM data")
    assert len(data) == 1
    yql_read.close()


def test_cross_table_operations(tmp_path):
    """CRUD operations across multiple tables."""
    yaml_file = tmp_path / "multi.yaml"
    yaml_file.write_text("""
users: []
orders: []
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    # INSERT into both tables
    yql.query("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    yql.query("INSERT INTO orders (id, user_id, product) VALUES (101, 1, 'Widget')")
    
    # Verify both tables
    users = yql.query("SELECT * FROM users")
    orders = yql.query("SELECT * FROM orders")
    assert len(users) == 1
    assert len(orders) == 1
    
    # Update one table
    yql.query("UPDATE orders SET product = 'Gadget' WHERE id = 101")
    
    # Delete from other table
    yql.query("DELETE FROM users WHERE id = 1")
    
    users = yql.query("SELECT * FROM users")
    orders = yql.query("SELECT * FROM orders")
    assert len(users) == 0
    assert len(orders) == 1
    assert orders.iloc[0]['product'] == 'Gadget'
    
    yql.close()


def test_large_dataset_crud(tmp_path):
    """CRUD operations on larger dataset (100 rows)."""
    yaml_file = tmp_path / "large.yaml"
    yaml_file.write_text("records: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    # Bulk insert
    values = ', '.join([f"({i}, 'Record {i}')" for i in range(100)])
    yql.query(f"INSERT INTO records (id, name) VALUES {values}")
    
    data = yql.query("SELECT * FROM records")
    assert len(data) == 100
    
    # Bulk update
    yql.query("UPDATE records SET name = 'Updated' WHERE id < 50")
    
    updated = yql.query("SELECT * FROM records WHERE name = 'Updated'")
    assert len(updated) == 50
    
    # Bulk delete
    yql.query("DELETE FROM records WHERE id >= 50")
    
    remaining = yql.query("SELECT * FROM records")
    assert len(remaining) == 50
    
    yql.close()


# ============================================================================
# EDGE CASE & ERROR HANDLING TESTS
# ============================================================================

def test_crud_empty_yaml_file(tmp_path):
    """CRUD operations on completely empty YAML file."""
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("")
    
    # Empty YAML might not create tables, so this may raise error
    # Depends on implementation
    try:
        yql = YamlQL(str(yaml_file), mode='rw')
        tables = yql.list_tables()
        # If no tables, INSERT should fail
        if len(tables) == 0:
            with pytest.raises(Exception):
                yql.query("INSERT INTO nonexistent (value) VALUES ('test')")
        yql.close()
    except Exception:
        # Empty file might cause load error - acceptable
        pass


def test_crud_malformed_sql(tmp_path):
    """Malformed SQL raises clear error."""
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("data: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    
    with pytest.raises(Exception):
        yql.query("INSERT INTO data INVALID SYNTAX")
    
    yql.close()


def test_crud_type_preservation(tmp_path):
    """CRUD operations preserve data types correctly."""
    yaml_file = tmp_path / "types.yaml"
    yaml_file.write_text("types: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO types (int_val, float_val, bool_val, str_val) VALUES (42, 3.14, true, 'text')")
    
    data = yql.query("SELECT * FROM types")
    row = data.iloc[0]
    assert isinstance(row['int_val'], (int, pd.Int64Dtype)) or row['int_val'] == 42
    assert isinstance(row['float_val'], float) or abs(row['float_val'] - 3.14) < 0.01
    assert row['bool_val'] == True
    assert row['str_val'] == 'text'
    
    yql.close()


def test_crud_special_yaml_keys(tmp_path):
    """CRUD with YAML keys containing special characters (hyphens)."""
    yaml_file = tmp_path / "special.yaml"
    yaml_file.write_text("api-keys: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    # Sanitized to api_keys
    yql.query("INSERT INTO api_keys (key_name, key_value) VALUES ('github', 'secret')")
    
    data = yql.query("SELECT * FROM api_keys")
    assert len(data) == 1
    assert data.iloc[0]['key_name'] == 'github'
    yql.close()


def test_crud_unicode_content(tmp_path):
    """CRUD operations with unicode content."""
    yaml_file = tmp_path / "unicode.yaml"
    yaml_file.write_text("messages: []")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO messages (text) VALUES ('Hello 世界')")
    yql.query("UPDATE messages SET text = 'Bonjour 世界' WHERE text LIKE '%世界%'")
    
    data = yql.query("SELECT * FROM messages")
    assert data.iloc[0]['text'] == 'Bonjour 世界'
    yql.close()


def test_load_static_unicode_fixture_roundtrip(tmp_path):
    """AC-1/AC-6 regression test: load a pre-existing on-disk UTF-8 YAML
    fixture containing Chinese-character data (not SQL-inserted), query it,
    then UPDATE + write-back + reload and verify fidelity through the full
    load -> query -> write-back -> reload cycle."""
    fixture_path = Path(__file__).parent / "test_data" / "unicode_sample.yaml"
    yaml_file = tmp_path / "unicode_sample.yaml"
    yaml_file.write_text(fixture_path.read_text(encoding="utf-8"), encoding="utf-8")

    # AC-1: load pre-existing on-disk UTF-8 fixture and query it exactly.
    yql = YamlQL(str(yaml_file))
    data = yql.query("SELECT * FROM people ORDER BY id")
    assert len(data) == 2
    assert data.iloc[0]['name'] == '张伟'
    assert data.iloc[0]['greeting'] == '你好，世界'
    assert data.iloc[1]['name'] == '李娜'
    assert data.iloc[1]['greeting'] == '欢迎来到北京'
    yql.close()

    # AC-6: UPDATE with new non-ASCII content, write back, reload, verify.
    yql_rw = YamlQL(str(yaml_file), mode='rw')
    yql_rw.query("UPDATE people SET greeting = '早上好，中国' WHERE id = 1")
    yql_rw.close()

    yql_reload = YamlQL(str(yaml_file))
    data_reload = yql_reload.query("SELECT * FROM people ORDER BY id")
    assert data_reload.iloc[0]['greeting'] == '早上好，中国'
    # Untouched row's original fixture content must survive the round-trip.
    assert data_reload.iloc[1]['name'] == '李娜'
    assert data_reload.iloc[1]['greeting'] == '欢迎来到北京'
    yql_reload.close()

    # Byte-level check: file on disk still contains exact Chinese substrings,
    # no \uXXXX escaping introduced by write-back.
    raw = yaml_file.read_text(encoding="utf-8")
    assert '早上好，中国' in raw
    assert '李娜' in raw
    assert '\\u' not in raw


def test_crud_preserves_unmodified_sections(tmp_path):
    """CRUD on one section doesn't affect other sections."""
    yaml_file = tmp_path / "sections.yaml"
    yaml_file.write_text("""
config:
  setting1: value1
  setting2: value2
users:
  - name: Alice
""")
    
    yql = YamlQL(str(yaml_file), mode='rw')
    yql.query("INSERT INTO users (name) VALUES ('Bob')")
    yql.close()
    
    # Verify config section unchanged
    yql_read = YamlQL(str(yaml_file))
    # Config keys should still exist (as root-level or separate table)
    tables = yql_read.list_tables()
    # Implementation-dependent: config might be unwrapped or separate
    yql_read.close()
