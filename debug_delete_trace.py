"""Debug DELETE with full traceback."""
from yamlql_library import YamlQL
import tempfile
from pathlib import Path
import traceback

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

yql = YamlQL(temp_path, mode='rw')

print("\n=== Initial data ===")
data = yql.query("SELECT * FROM users")
print(data)

try:
    print("\n=== Running DELETE WHERE name = 'Bob' ===")
    result = yql.query("DELETE FROM users WHERE name = 'Bob'")
    print(f"DELETE result: {result}")
except Exception as e:
    print(f"\nFull error trace:")
    traceback.print_exc()
    print(f"\nError: {e}")

print("\n=== YAML content after DELETE ===")
print(Path(temp_path).read_text())

yql.close()
Path(temp_path).unlink()
