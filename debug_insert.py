"""Debug INSERT operation to see what's happening."""
from yamlql_library import YamlQL
import tempfile
from pathlib import Path

# Create temp file with empty list
with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
    f.write("users: []")
    temp_path = f.name

print(f"Temp file: {temp_path}")

# Read initial state
print("\n=== Initial YAML content ===")
print(Path(temp_path).read_text())

# Create YamlQL instance and run INSERT
yql = YamlQL(temp_path, mode='rw')
print("\n=== Tables in database ===")
result = yql.query("SHOW TABLES")
print(result)

print("\n=== Running INSERT ===")
insert_result = yql.query("INSERT INTO users (name, age) VALUES ('Alice', 30)")
print(f"INSERT result: {insert_result}")

print("\n=== YAML content after INSERT ===")
print(Path(temp_path).read_text())

print("\n=== Reading back with new instance ===")
yql2 = YamlQL(temp_path)
data = yql2.query("SELECT * FROM users")
print(f"Data: {data}")
print(f"Length: {len(data)}")

# Cleanup
yql.close()
yql2.close()
Path(temp_path).unlink()
