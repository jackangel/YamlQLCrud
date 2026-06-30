"""Debug DELETE operation."""
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
print(f"Row count: {len(data)}")

print("\n=== Running DELETE WHERE name = 'Bob' ===")
result = yql.query("DELETE FROM users WHERE name = 'Bob'")
print(f"DELETE result: {result}")

print("\n=== YAML content after DELETE ===")
print(Path(temp_path).read_text())

print("\n=== Data after DELETE (same instance) ===")
data = yql.query("SELECT * FROM users")
print(data)
print(f"Row count: {len(data)}")

print("\n=== Data after DELETE (new instance) ===")
yql2 = YamlQL(temp_path)
data2 = yql2.query("SELECT * FROM users")
print(data2)
print(f"Row count: {len(data2)}")

# Cleanup
yql.close()
yql2.close()
Path(temp_path).unlink()
