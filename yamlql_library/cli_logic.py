# This file contains the core implementation logic for the CLI commands
# to avoid circular dependencies and overly complex CLI files.

import rich
import os
import typer
from . import YamlQL
from .llm_providers import get_llm_provider
from .utils import OutputFormat, _render_list, _render_table, _get_required_table_width
from rich.console import Console
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

def run_query(sql_query: str, file: str, output: OutputFormat, max_depth: int = 5, strategy: str = "depth", mode: str = "r"):
    """Core logic for the 'query' command."""
    yql = None
    try:
        yql = YamlQL(file_path=file, max_depth=max_depth, strategy=strategy, mode=mode)
        results = yql.query(sql_query)

        # Handle DataFrame results (SELECT queries)
        if hasattr(results, 'empty'):
            if results.empty:
                rich.print("[yellow]Query returned no results.[/yellow]")
                return
            
            console = Console()
            use_list_view = (
                output == OutputFormat.LIST or
                (output == OutputFormat.AUTO and _get_required_table_width(results) > console.width)
            )

            if use_list_view:
                _render_list(results)
            else:
                _render_table(results)
        # Handle dict results (INSERT/UPDATE/DELETE)
        elif isinstance(results, dict):
            if results.get('success'):
                message = results.get('message', 'Operation completed')
                rich.print(f"[green]✓ {message}[/green]")
            else:
                error = results.get('error', 'Unknown error')
                rich.print(f"[red]✗ {error}[/red]")

    except FileNotFoundError as e:
        rich.print(f"[bold red]Error:[/bold red] {e}")
    except Exception as e:
        rich.print(f"[bold red]An unexpected error occurred:[/bold red] {e}")
    finally:
        if yql:
            yql.close()

def run_interactive_sql(file: str, output: OutputFormat, max_depth: int = 5, strategy: str = "depth", mode: str = "r"):
    """Starts an interactive SQL prompt with optional transaction support."""
    yql = None
    in_transaction = False
    pending_operations = []
    
    try:
        yql = YamlQL(file_path=file, max_depth=max_depth, strategy=strategy, mode=mode)
        rich.print(f"[bold green]Connected to {file}.[/bold green]")
        if mode == "rw":
            rich.print("[yellow]Write mode enabled. Use BEGIN to start transactions.[/yellow]")
        rich.print("Enter SQL commands (end with a semicolon) or interactive commands.")
        rich.print("Type [bold cyan]help[/bold cyan] for available commands, [bold cyan]exit[/bold cyan] to quit.")
        
        session = PromptSession(
            history=FileHistory(os.path.expanduser("~/.yamlql_history")),
            auto_suggest=AutoSuggestFromHistory()
        )
        
        multiline_buffer = []

        while True:
            # Show transaction status in prompt
            if in_transaction:
                prompt_text = f"YamlQL [TXN:{len(pending_operations)}]> " if not multiline_buffer else "              ...> "
            else:
                prompt_text = "YamlQL> " if not multiline_buffer else "    ...> "
            
            line = session.prompt(prompt_text)
            command = line.strip().lower()

            # Exit command
            if command in ('exit', 'quit'):
                if in_transaction and len(pending_operations) > 0:
                    rich.print("[yellow]⚠ You have uncommitted changes. Use COMMIT or ROLLBACK first.[/yellow]")
                    confirm = input("Exit anyway? (y/n): ").strip().lower()
                    if confirm != 'y':
                        continue
                break

            # Help command
            if command in ('help', '?'):
                console = Console()
                console.print("""
[bold]Available Commands:[/bold]

[bold cyan]Query Commands:[/bold cyan]
  listtables              List all tables in the YAML file
  listfields <table>      Show columns for a specific table
  status                  Show current connection and transaction state

[bold cyan]Transaction Commands (write mode only):[/bold cyan]
  begin                   Start a transaction
  commit                  Commit pending operations
  rollback                Discard pending operations
  show pending            List queued operations

[bold cyan]General:[/bold cyan]
  help / ?                Show this help message
  exit / quit             Exit interactive mode

[bold cyan]SQL Statements (end with semicolon):[/bold cyan]
  SELECT * FROM table;    Query data
  INSERT INTO table ...;  Insert data (queued in transaction mode)
  UPDATE table SET ...;   Update data (queued in transaction mode)
  DELETE FROM table ...;  Delete data (queued in transaction mode)
                """)
                continue

            # Status command
            if command == 'status':
                console = Console()
                mode_display = "[green]Read-Write[/green]" if mode == "rw" else "[blue]Read-Only[/blue]"
                txn_display = "[yellow]Active[/yellow]" if in_transaction else "[gray]None[/gray]"
                console.print(f"Mode: {mode_display}")
                console.print(f"File: {file}")
                console.print(f"Transaction: {txn_display}")
                if in_transaction:
                    console.print(f"Pending operations: {len(pending_operations)}")
                try:
                    tables = yql._db.con.execute("SHOW ALL TABLES;").fetchall()
                    table_names = [t[2] for t in tables]
                    console.print(f"Tables: {', '.join(table_names) if table_names else '(none)'}")
                except:
                    pass
                continue

            # Transaction: BEGIN
            if command == 'begin':
                if mode != "rw":
                    rich.print("[red]Transactions require write mode. Use --writable flag.[/red]")
                elif in_transaction:
                    rich.print("[yellow]Transaction already in progress.[/yellow]")
                else:
                    in_transaction = True
                    pending_operations = []
                    rich.print("[green]✅ Transaction started. Use COMMIT to save or ROLLBACK to discard.[/green]")
                continue

            # Transaction: COMMIT
            if command == 'commit':
                if not in_transaction:
                    rich.print("[yellow]No transaction in progress. Use BEGIN to start one.[/yellow]")
                elif len(pending_operations) == 0:
                    rich.print("[yellow]No operations to commit.[/yellow]")
                    in_transaction = False
                else:
                    # Show pending operations
                    console = Console()
                    console.print(f"\n[bold]Committing {len(pending_operations)} operations:[/bold]")
                    for i, op in enumerate(pending_operations, 1):
                        truncated = op[:80] + "..." if len(op) > 80 else op
                        console.print(f"  {i}. {truncated}")
                    
                    # Execute all operations
                    try:
                        for op in pending_operations:
                            result = yql.query(op)
                            # Check if operation was successful
                            if isinstance(result, dict) and 'success' in result and not result['success']:
                                raise Exception(result.get('message', 'Operation failed'))
                        
                        rich.print(f"[green]✅ Transaction committed ({len(pending_operations)} operations executed).[/green]")
                        in_transaction = False
                        pending_operations = []
                    except Exception as e:
                        rich.print(f"[red]❌ Transaction failed: {e}[/red]")
                        rich.print("[yellow]Use ROLLBACK to discard changes or fix the error and try again.[/yellow]")
                continue

            # Transaction: ROLLBACK
            if command == 'rollback':
                if not in_transaction:
                    rich.print("[yellow]No transaction in progress.[/yellow]")
                else:
                    count = len(pending_operations)
                    in_transaction = False
                    pending_operations = []
                    rich.print(f"[green]✅ Transaction rolled back ({count} operations discarded).[/green]")
                continue

            # Transaction: SHOW PENDING
            if command == 'show pending':
                if not in_transaction:
                    rich.print("[yellow]No transaction in progress.[/yellow]")
                elif len(pending_operations) == 0:
                    rich.print("[yellow]Transaction is active but no operations queued yet.[/yellow]")
                else:
                    console = Console()
                    console.print(f"\n[bold]Transaction has {len(pending_operations)} pending operations:[/bold]")
                    for i, op in enumerate(pending_operations, 1):
                        console.print(f"  {i}. {op}")
                continue

            # List tables
            if command == 'listtables':
                tables = yql._db.con.execute("SHOW ALL TABLES;").fetchall()
                if tables:
                    rich.print("[bold green]Available tables:[/bold green]")
                    for table in tables:
                        rich.print(f"- {table[2]}")
                else:
                    rich.print("[yellow]No tables found.[/yellow]")
                continue

            # List fields
            if command.startswith('listfields'):
                parts = line.strip().split()
                if len(parts) < 2:
                    rich.print("[bold red]Error:[/bold red] Please provide a table name. Usage: listfields <table_name>")
                    continue
                
                table_name = parts[1]
                try:
                    columns = yql._db.con.execute(f"PRAGMA table_info('{table_name}');").fetchall()
                    if columns:
                        rich.print(f"[bold green]Fields for table '{table_name}':[/bold green]")
                        for col in columns:
                            rich.print(f"- [bold]{col[1]}[/bold]: {col[2]}")
                    else:
                        rich.print(f"[bold red]Error:[/bold red] Table '{table_name}' not found or has no columns.")
                except Exception as e:
                    rich.print(f"[bold red]Error:[/bold red] {e}")
                continue

            # SQL query handling
            multiline_buffer.append(line)
            
            # If the line ends with a semicolon, it's time to execute
            if line.strip().endswith(';'):
                full_query = " ".join(multiline_buffer).strip()
                # Remove the trailing semicolon for DuckDB
                full_query = full_query[:-1]
                
                # Detect write operations
                is_write = _is_write_operation(full_query)
                
                # Safety check for DELETE without WHERE
                if is_write and full_query.strip().upper().startswith('DELETE'):
                    if not _has_where_clause(full_query):
                        rich.print("[red]⚠ WARNING: DELETE without WHERE will remove ALL rows![/red]")
                        if not in_transaction:
                            confirm = input("Are you sure you want to proceed? (y/n): ").strip().lower()
                            if confirm != 'y':
                                rich.print("[yellow]Operation cancelled.[/yellow]")
                                multiline_buffer = []
                                continue
                
                try:
                    # In transaction mode, queue write operations
                    if in_transaction and is_write:
                        pending_operations.append(full_query)
                        rich.print(f"[yellow]📝 Operation queued (transaction has {len(pending_operations)} operations)[/yellow]")
                        rich.print("   Use COMMIT to execute or ROLLBACK to discard")
                    else:
                        # Execute immediately (auto-commit mode or read operation)
                        results = yql.query(full_query)
                        
                        # Handle DataFrame results (SELECT queries)
                        if hasattr(results, 'empty'):
                            if results.empty:
                                rich.print("[yellow]Query returned no results.[/yellow]")
                            else:
                                console = Console()
                                use_list_view = (
                                    output == OutputFormat.LIST or
                                    (output == OutputFormat.AUTO and _get_required_table_width(results) > console.width)
                                )
                                if use_list_view:
                                    _render_list(results)
                                else:
                                    _render_table(results)
                        # Handle dict results (INSERT/UPDATE/DELETE)
                        elif isinstance(results, dict):
                            if results.get('success'):
                                message = results.get('message', 'Operation completed')
                                rich.print(f"[green]✅ {message}[/green]")
                            else:
                                message = results.get('message', 'Operation failed')
                                rich.print(f"[red]❌ {message}[/red]")
                        else:
                            # Unexpected result type
                            rich.print(results)
                            
                except Exception as e:
                    rich.print(f"[bold red]Query Error:[/bold red] {e}")

                multiline_buffer = [] # Reset for the next query

    except FileNotFoundError as e:
        rich.print(f"[bold red]Error:[/bold red] {e}")
    except Exception as e:
        import traceback
        rich.print(f"[bold red]An unexpected error occurred:[/bold red] {e}")
        rich.print(f"[dim]{traceback.format_exc()}[/dim]")
    finally:
        if yql:
            yql.close()
        rich.print("[bold]Exiting YamlQL.[/bold]")


def _is_write_operation(query: str) -> bool:
    """Check if a SQL query is a write operation (INSERT/UPDATE/DELETE)."""
    query_upper = query.strip().upper()
    return query_upper.startswith(('INSERT', 'UPDATE', 'DELETE'))


def _has_where_clause(query: str) -> bool:
    """Check if a DELETE/UPDATE query has a WHERE clause."""
    query_upper = query.strip().upper()
    return ' WHERE ' in query_upper

def run_nlp(question: str, file: str, output: OutputFormat, mode: str = "r"):
    """Core logic for the 'ai' command."""
    yql = None
    try:
        yql = YamlQL(file_path=file, mode=mode)
        
        schema_lines = []
        for row in yql._db.con.execute("SHOW ALL TABLES;").fetchall():
            table_name = row[2]
            schema_lines.append(f"\n-- Table: {table_name}")
            for col_info in yql._db.con.execute(f"PRAGMA table_info('{table_name}');").fetchall():
                schema_lines.append(f"  - {col_info[1]}: {col_info[2]}")
        schema = "\n".join(schema_lines)

        provider_name = os.getenv("YAMLQL_LLM_PROVIDER")
        llm_provider = get_llm_provider(provider_name)

        rich.print("[yellow]Generating SQL query from your question...[/yellow]")
        sql_query = llm_provider.get_sql_query(schema, question)
        rich.print(f"[bold green]Generated SQL:[/bold green] [cyan]{sql_query}[/cyan]")

        results = yql.query(sql_query)

        # Handle DataFrame results (SELECT queries)
        if hasattr(results, 'empty'):
            if results.empty:
                rich.print("[yellow]Query executed successfully and returned no results.[/yellow]")
                return

            rich.print("\n[bold magenta]Query Results:[/bold magenta]")
            console = Console()
            use_list_view = (
                output == OutputFormat.LIST or
                (output == OutputFormat.AUTO and _get_required_table_width(results) > console.width)
            )

            if use_list_view:
                _render_list(results)
            else:
                _render_table(results)
        # Handle dict results (INSERT/UPDATE/DELETE)
        elif isinstance(results, dict):
            if results.get('success'):
                message = results.get('message', 'Operation completed')
                rich.print(f"[green]✓ {message}[/green]")
            else:
                error = results.get('error', 'Unknown error')
                rich.print(f"[red]✗ {error}[/red]")

    except (ValueError, NotImplementedError) as e:
        rich.print(f"[bold red]Configuration Error:[/bold red] {e}")
    except FileNotFoundError as e:
        rich.print(f"[bold red]Error:[/bold red] {e}")
    except Exception as e:
        import traceback
        rich.print(f"[bold red]An unexpected error occurred:[/bold red] {e}")
        rich.print(f"[dim]{traceback.format_exc()}[/dim]")
    finally:
        if yql:
            yql.close() 