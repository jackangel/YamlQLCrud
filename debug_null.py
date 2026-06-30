"""Debug NULL value handling."""
from yamlql_library import YamlQL
import tempfile
from pathlib import Path

with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
    f.write("users: []")
    temp_path = f.name

print(f"Temp file: {temp_path}")

yql = YamlQL(temp_path, mode='rw')
print("\n=== Running INSERT with NULL ===")
result = yql.query("INSERT INTO users (name, email, age) VALUES ('Alice', NULL, 30)")
print(f"Result: {result}")

print("\n=== YAML content after INSERT ===")
print(Path(temp_path).read_text())

print("\n=== Data after INSERT ===")
yql2 = YamlQL(temp_path)
data = yql2.query("SELECT * FROM users")
print(data)
print(f"Columns: {list(data.columns)}")

yql.close()
yql2.close()
Path(temp_path).unlink()
