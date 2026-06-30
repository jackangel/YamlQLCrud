"""Debug INSERT with full error trace."""
from yamlql_library import YamlQL
import tempfile
from pathlib import Path
import traceback

# Create temp file with empty list
with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
    f.write("users: []")
    temp_path = f.name

print(f"Temp file: {temp_path}")

yql = YamlQL(temp_path, mode='rw')

try:
    insert_result = yql.query("INSERT INTO users (name, age) VALUES ('Alice', 30)")
    print(f"INSERT result: {insert_result}")
except Exception as e:
    print(f"\nFull error trace:")
    traceback.print_exc()
    print(f"\nError: {e}")

print(f"\n=== YAML content after INSERT ===")
print(Path(temp_path).read_text())

yql.close()
Path(temp_path).unlink()
