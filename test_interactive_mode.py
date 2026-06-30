#!/usr/bin/env python
"""Test interactive transaction mode."""
import subprocess
import time

# Create a test YAML file
with open("test_interactive.yaml", "w") as f:
    f.write("""services:
  - name: database
    port: 5432
""")

# Test commands to send to interactive mode
commands = [
    "status",
    "listtables",
    "begin",
    "INSERT INTO services VALUES ('api', 8080);",
    "INSERT INTO services VALUES ('web', 3000);",
    "show pending",
    "commit",
    "SELECT * FROM services;",
    "exit"
]

print("Testing interactive transaction mode...")
print("=" * 60)

# Note: This is a manual test script. Run interactively:
print("\nTo test manually, run:")
print("  python -m yamlql_library.cli sql --file test_interactive.yaml --interactive --writable")
print("\nThen try these commands:")
for cmd in commands:
    print(f"  {cmd}")
