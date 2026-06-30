"""Debug WHERE clause extraction."""
import sqlglot

sql = "DELETE FROM users WHERE name = 'Bob'"
parsed = sqlglot.parse_one(sql, dialect='duckdb')

print(f"Parsed type: {type(parsed)}")
print(f"Parsed: {parsed}")

print(f"\nargs: {parsed.args}")
print(f"\nwhere: {parsed.args.get('where')}")
print(f"where type: {type(parsed.args.get('where'))}")
print(f"where str: {str(parsed.args.get('where'))}")

where_expr = parsed.args.get('where')
if where_expr:
    print(f"\nwhere_expr attributes:")
    for attr in ['this', 'expression', 'left', 'right']:
        if hasattr(where_expr, attr):
            val = getattr(where_expr, attr)
            print(f"  {attr}: {val} (type: {type(val).__name__})")
    
    # Try sql() method
    if hasattr(where_expr, 'sql'):
        print(f"\nwhere_expr.sql(): {where_expr.sql()}")
