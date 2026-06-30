"""Debug SQL parsing."""
import sqlglot
from sqlglot.dialects import duckdb

sql = "INSERT INTO users (name, age) VALUES ('Alice', 30)"
parsed = sqlglot.parse_one(sql, dialect='duckdb')

print(f"Parsed type: {type(parsed)}")
print(f"Parsed: {parsed}")
print(f"\nAttributes:")
for attr in dir(parsed):
    if not attr.startswith('_'):
        try:
            val = getattr(parsed, attr)
            if not callable(val):
                print(f"  {attr}: {val}")
        except:
            pass

print(f"\nthis: {parsed.this}")
print(f"this type: {type(parsed.this)}")
if parsed.this:
    print(f"this.name: {getattr(parsed.this, 'name', 'NO NAME ATTR')}")

print(f"\nexpression: {parsed.expression}")
print(f"expression type: {type(parsed.expression)}")
