"""Debug UPDATE operation."""
from yamlql_library import YamlQL
import tempfile
from pathlib import Path

# Create temp file with test data
with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
    f.write("""users:
  - name: Alice
    age: 30
  - name: Bob
    age: 25
  - name: Charlie
    age: 35
""")
    temp_path = f.name

print(f"Temp file: {temp_path}")

# Read initial state
print("\n=== Initial YAML content ===")
print(Path(temp_path).read_text())

yql = YamlQL(temp_path, mode='rw')

print("\n=== Initial data ===")
data = yql.query("SELECT * FROM users")
print(data)

print("\n=== Running UPDATE SET age = 31 WHERE name = 'Alice' ===")
result = yql.query("UPDATE users SET age = 31 WHERE name = 'Alice'")
print(f"UPDATE result: {result}")

print("\n=== YAML content after UPDATE ===")
print(Path(temp_path).read_text())

print("\n=== Data after UPDATE (same instance) ===")
data = yql.query("SELECT * FROM users")
print(data)

print("\n=== Data after UPDATE (new instance) ===")
yql2 = YamlQL(temp_path)
data2 = yql2.query("SELECT * FROM users")
print(data2)

# Cleanup
yql.close()
yql2.close()
Path(temp_path).unlink()
