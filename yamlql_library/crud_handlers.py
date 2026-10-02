"""
CRUD Handlers for YamlQL Write Operations

This module provides handlers for INSERT, UPDATE, and DELETE operations
that modify YAML files while maintaining format preservation and atomicity.

The CRUD handlers bridge the gap between SQL write operations and YAML persistence:
1. Parse SQL statement (via SqlInterceptor)
2. Validate against database schema
3. Convert to YAML paths (via ReverseTransformer)
4. Write atomically to file (via TransactionManager)
5. Update in-memory database to maintain consistency
"""

from typing import Dict, Any, List, Optional, TYPE_CHECKING
import pandas as pd
import sqlglot
from sqlglot.expressions import Insert, Update, Delete, Tuple as SqlTuple, Values

if TYPE_CHECKING:
    from .database import Database

from .reverse_transformer import ReverseTransformer
from .transaction import TransactionManager
from .writer import YamlWriterPolicyError


class InsertHandler:
    """
    Handles INSERT operations for YamlQL.
    
    Converts SQL INSERT statements into YAML modifications and persists
    them to disk while maintaining format preservation and atomicity.
    
    Example usage:
        >>> handler = InsertHandler(
        ...     file_path="config.yaml",
        ...     column_name_map={"users": {"user_name": "user-name"}},
        ...     original_data={"users": []},
        ...     db=database_instance
        ... )
        >>> result = handler.handle(parsed_insert)
        >>> print(result)
        {'success': True, 'rows_inserted': 1, 'message': '1 row inserted'}
    """
    
    def __init__(
        self,
        file_path: str,
        column_name_map: Dict[str, Dict[str, str]],
        original_data: dict,
        db: "Database"
    ):
        """
        Initialize the INSERT handler.
        
        Args:
            file_path: Path to the YAML file to modify
            column_name_map: Bidirectional column mapping from transformer.
                            Format: {table_name: {sanitized_col: original_col, ...}}
            original_data: Current YAML structure (before modifications)
            db: Database instance for schema validation and in-memory updates
        """
        self.file_path = file_path
        self.column_name_map = column_name_map
        self.original_data = original_data
        self.db = db
        self.reverse_transformer = ReverseTransformer(column_name_map, original_data)
    
    def handle(self, parsed_sql: Insert) -> Dict[str, Any]:
        """
        Execute an INSERT statement.
        
        This method:
        1. Parses the INSERT to extract table, columns, and values
        2. Validates the table and columns exist
        3. Converts SQL values to YAML path→value pairs
        4. Writes changes atomically to the YAML file
        5. Updates the in-memory database to maintain consistency
        
        Args:
            parsed_sql: Parsed INSERT statement from sqlglot
        
        Returns:
            dict with keys:
            - 'success': bool - True if operation succeeded
            - 'rows_inserted': int - Number of rows inserted
            - 'message': str - Human-readable result message
            
        Raises:
            ValueError: If table/columns don't exist or values are invalid
            IOError: If file write fails
            
        Examples:
            INSERT INTO users VALUES ('Alice', 30)
            INSERT INTO users (name, age) VALUES ('Bob', 25)
            INSERT INTO users (name, age) VALUES ('Carol', 28), ('Dave', 32)
        """
        try:
            # Step 1: Extract INSERT components
            table_name = self._extract_table_name(parsed_sql)
            columns = self._extract_columns(parsed_sql, table_name)
            rows = self._extract_values(parsed_sql, columns)
            
            # Step 2: Validate schema
            self._validate_table_exists(table_name)
            self._validate_columns_exist(table_name, columns)
            
            # Step 3: Convert to YAML paths and write atomically
            rows_inserted = self._insert_rows(table_name, columns, rows)
            
            # Step 4: Update in-memory database
            try:
                self._update_database(table_name, columns, rows)
            except RuntimeError as db_error:
                # Database sync failed, but YAML write succeeded
                # Return success with warning to reload
                import warnings
                warnings.warn(str(db_error))
                return {
                    'success': True,
                    'rows_inserted': rows_inserted,
                    'message': (
                        f"{rows_inserted} row{'s' if rows_inserted != 1 else ''} inserted into {table_name}. "
                        f"Warning: In-memory database out of sync. Create a new YamlQL instance to reload."
                    )
                }
            
            return {
                'success': True,
                'rows_inserted': rows_inserted,
                'message': f"{rows_inserted} row{'s' if rows_inserted != 1 else ''} inserted into {table_name}"
            }
            
        except Exception as e:
            return {
                'success': False,
                'rows_inserted': 0,
                'message': f"INSERT failed: {str(e)}"
            }
    
    def _extract_table_name(self, parsed_sql: Insert) -> str:
        """
        Extract the target table name from INSERT statement.
        
        Args:
            parsed_sql: Parsed INSERT expression
            
        Returns:
            Table name as string
            
        Raises:
            ValueError: If table name cannot be extracted
        """
        if not parsed_sql.this:
            raise ValueError("INSERT statement missing target table")
        
        # Get table name from the 'this' attribute
        # In sqlglot, INSERT has: this=Schema(this=Table(this=Identifier(this='name')))
        schema_expr = parsed_sql.this
        
        # Navigate: Schema.this -> Table
        if hasattr(schema_expr, 'this'):
            table_expr = schema_expr.this
            
            # Navigate: Table.this -> Identifier
            if hasattr(table_expr, 'this'):
                identifier = table_expr.this
                
                # Get name from Identifier
                if hasattr(identifier, 'this'):
                    table_name = str(identifier.this)
                elif hasattr(identifier, 'name'):
                    table_name = identifier.name
                else:
                    table_name = str(identifier).strip('"').strip("'").strip('`')
            elif hasattr(table_expr, 'name'):
                table_name = table_expr.name
            else:
                table_name = str(table_expr).strip('"').strip("'").strip('`')
        elif hasattr(schema_expr, 'name'):
            table_name = schema_expr.name
        else:
            # Fallback to string representation
            table_name = str(schema_expr).strip('"').strip("'").strip('`')
            # Extract just the table name if it has a schema prefix
            if ' ' in table_name:
                table_name = table_name.split()[0]
        
        if not table_name:
            raise ValueError("Could not extract table name from INSERT statement")
        
        return table_name
    
    def _extract_columns(self, parsed_sql: Insert, table_name: str) -> List[str]:
        """
        Extract column names from INSERT statement.
        
        For INSERT INTO table (col1, col2) VALUES (...), extracts [col1, col2].
        For INSERT INTO table VALUES (...), infers columns from table schema.
        
        Args:
            parsed_sql: Parsed INSERT expression
            table_name: Table name for schema lookup
            
        Returns:
            List of column names in order
            
        Raises:
            ValueError: If columns cannot be determined
        """
        # Check if columns are explicitly specified
        # In sqlglot INSERT: this=Schema(this=Table, expressions=[col1, col2, ...])
        if parsed_sql.this and hasattr(parsed_sql.this, 'expressions') and parsed_sql.this.expressions:
            # Explicit columns: INSERT INTO table (col1, col2) VALUES (...)
            columns = []
            for col in parsed_sql.this.expressions:
                if hasattr(col, 'this'):
                    # It's an Identifier with 'this' attribute
                    columns.append(str(col.this))
                elif hasattr(col, 'name'):
                    columns.append(col.name)
                else:
                    columns.append(str(col).strip('"').strip("'"))
            return columns
        
        # No explicit columns - infer from table schema
        return self._get_table_columns(table_name)
    
    def _extract_values(self, parsed_sql: Insert, columns: List[str]) -> List[Dict[str, Any]]:
        """
        Extract values from INSERT statement.
        
        Handles:
        - Single row: INSERT INTO table VALUES (1, 2)
        - Multiple rows: INSERT INTO table VALUES (1, 2), (3, 4)
        
        Args:
            parsed_sql: Parsed INSERT expression
            columns: List of column names (for mapping positional values)
            
        Returns:
            List of dictionaries, each representing a row: [{col1: val1, col2: val2}, ...]
            
        Raises:
            ValueError: If value count doesn't match column count
        """
        rows = []
        
        # Get the VALUES clause
        if not parsed_sql.expression:
            raise ValueError("INSERT statement missing VALUES clause")
        
        values_expr = parsed_sql.expression
        
        # Check if it's a Values expression (can contain multiple tuples)
        if isinstance(values_expr, Values):
            # Multiple rows: VALUES (1, 2), (3, 4)
            for tuple_expr in values_expr.expressions:
                row_values = self._parse_tuple(tuple_expr)
                rows.append(self._map_values_to_columns(columns, row_values))
        
        elif isinstance(values_expr, SqlTuple):
            # Single row: VALUES (1, 2)
            row_values = self._parse_tuple(values_expr)
            rows.append(self._map_values_to_columns(columns, row_values))
        
        else:
            # Try to parse as a single tuple directly
            try:
                row_values = self._parse_tuple(values_expr)
                rows.append(self._map_values_to_columns(columns, row_values))
            except Exception:
                raise ValueError(f"Unsupported VALUES clause format: {type(values_expr).__name__}")
        
        return rows
    
    def _parse_tuple(self, tuple_expr: Any) -> List[Any]:
        """
        Parse a tuple of values from SQL expression.
        
        Args:
            tuple_expr: SQL tuple expression containing values
            
        Returns:
            List of Python values (int, float, str, bool, None)
        """
        values = []
        
        if isinstance(tuple_expr, SqlTuple):
            # Standard tuple with expressions
            for expr in tuple_expr.expressions:
                values.append(self._extract_literal_value(expr))
        else:
            # Try to extract expressions directly
            if hasattr(tuple_expr, 'expressions'):
                for expr in tuple_expr.expressions:
                    values.append(self._extract_literal_value(expr))
            else:
                # Single value
                values.append(self._extract_literal_value(tuple_expr))
        
        return values
    
    def _extract_literal_value(self, expr: Any) -> Any:
        """
        Extract Python value from SQL literal expression.
        
        Args:
            expr: SQL expression (Literal, Null, Boolean, etc.)
            
        Returns:
            Python value (int, float, str, bool, None)
        """
        from sqlglot.expressions import Literal, Null, Boolean
        
        if isinstance(expr, Null):
            return None
        
        if isinstance(expr, Boolean):
            return expr.this
        
        if isinstance(expr, Literal):
            # Get the literal value and its type
            value_str = expr.this
            
            # Try to infer type from the literal
            if expr.is_int:
                return int(value_str)
            elif expr.is_number:
                return float(value_str)
            elif expr.is_string:
                return value_str
            else:
                # Fallback to string
                return value_str
        
        # Fallback: convert to string
        return str(expr)
    
    def _map_values_to_columns(self, columns: List[str], values: List[Any]) -> Dict[str, Any]:
        """
        Map positional values to column names.
        
        Args:
            columns: List of column names
            values: List of values in same order
            
        Returns:
            Dictionary mapping column names to values
            
        Raises:
            ValueError: If lengths don't match
        """
        if len(columns) != len(values):
            raise ValueError(
                f"Column count ({len(columns)}) does not match value count ({len(values)})"
            )
        
        return dict(zip(columns, values))
    
    def _validate_table_exists(self, table_name: str) -> None:
        """
        Validate that the table exists in the database.
        
        Args:
            table_name: Table name to validate
            
        Raises:
            ValueError: If table does not exist
        """
        try:
            # Try to describe the table (will fail if table doesn't exist)
            self.db.query(f"DESCRIBE {table_name}")
        except Exception:
            raise ValueError(f"Table '{table_name}' does not exist")
    
    def _validate_columns_exist(self, table_name: str, columns: List[str]) -> None:
        """
        Validate that all columns exist in the table.
        
        For INSERT operations on empty tables (tables with only _yaml_path column),
        this validation is skipped since we're adding new data dynamically.
        
        Args:
            table_name: Table name
            columns: List of column names to validate
            
        Raises:
            ValueError: If any column does not exist (unless it's an empty table)
        """
        # Get table schema
        schema = self.db.query(f"DESCRIBE {table_name}")
        existing_columns = set(schema['column_name'].tolist())
        
        # Skip validation for empty tables (only have _yaml_path column)
        # This allows INSERT to add new columns dynamically
        if existing_columns == {'_yaml_path'}:
            return
        
        # Check each column
        for col in columns:
            if col not in existing_columns and col != '_yaml_path':
                raise ValueError(
                    f"Column '{col}' does not exist in table '{table_name}'. "
                    f"Available columns: {', '.join(sorted(existing_columns))}"
                )
    
    def _get_table_columns(self, table_name: str) -> List[str]:
        """
        Get all column names for a table from its schema.
        
        Args:
            table_name: Table name
            
        Returns:
            List of column names in schema order
            
        Raises:
            ValueError: If table doesn't exist
        """
        try:
            schema = self.db.query(f"DESCRIBE {table_name}")
            # Filter out metadata columns
            columns = [
                col for col in schema['column_name'].tolist()
                if not col.startswith('_')
            ]
            return columns
        except Exception:
            raise ValueError(f"Could not retrieve schema for table '{table_name}'")
    
    def _insert_rows(self, table_name: str, columns: List[str], rows: List[Dict[str, Any]]) -> int:
        """
        Insert rows into the YAML file using transaction manager.
        
        Args:
            table_name: Table name (YAML section)
            columns: Column names
            rows: List of row dictionaries
            
        Returns:
            Number of rows inserted
            
        Raises:
            IOError: If write fails
        """
        with TransactionManager(self.file_path) as txn:
            writer = txn.get_writer()
            writer.load()

            # The read model omits non-mapping documents, so SQL tables can
            # address only mapping documents in a YAML document stream.
            document_index = writer.find_document(table_name)
            if writer.doc_count == 1 and isinstance(writer.data, list):
                document_index = 0
            yaml_data = (
                writer.documents[document_index]
                if document_index is not None
                else None
            )
            
            # Convert each row to YAML paths
            for row_index, row in enumerate(rows):
                # Use reverse transformer to get path→value pairs
                path_value_pairs = self.reverse_transformer.row_to_yaml_path(table_name, row)
                
                # Determine insertion strategy based on YAML structure
                # For list-based tables (most common), append to the list
                # For dict-based tables, merge keys
                
                try:
                    if self._is_list_table(
                        table_name,
                        yaml_data,
                        allow_root_list=writer.doc_count == 1,
                    ):
                        # Append new item to list
                        self._append_to_list_table(
                            table_name,
                            path_value_pairs,
                            writer,
                            document_index,
                        )
                    else:
                        # Merge into dict structure
                        self._merge_into_dict_table(table_name, path_value_pairs, writer)
                except YamlWriterPolicyError as error:
                    raise ValueError(
                        f"Cannot insert into table '{table_name}', row {row_index}: {error}"
                    ) from error
        
        return len(rows)
    
    def _is_list_table(
        self,
        table_name: str,
        yaml_data: Any,
        allow_root_list: bool = True,
    ) -> bool:
        """
        Determine if a table represents a YAML list.
        
        Args:
            table_name: Table name
            yaml_data: Loaded YAML data structure
            
        Returns:
            True if the table corresponds to a list, False otherwise
        """
        # Check if the table name exists as a top-level key pointing to a list
        if isinstance(yaml_data, dict) and table_name in yaml_data:
            return isinstance(yaml_data[table_name], list)
        
        # Root-level lists are meaningful only for single-document files.
        if allow_root_list and isinstance(yaml_data, list):
            return True
        
        return False
    
    def _append_to_list_table(
        self,
        table_name: str,
        path_value_pairs: Dict[str, Any],
        writer: Any,
        document_index: Optional[int],
    ) -> None:
        """
        Append a new item to a list-based table.
        
        Args:
            table_name: Table name
            path_value_pairs: Dictionary of path→value to insert
            writer: YamlWriter instance
        """
        # Build the item from path→value pairs
        item = self.reverse_transformer.build_nested_dict(path_value_pairs)
        
        # Determine the list path
        yaml_data = (
            writer.documents[document_index]
            if document_index is not None
            else None
        )
        if isinstance(yaml_data, list):
            # Root-level list
            list_path = None
        elif isinstance(yaml_data, dict) and table_name in yaml_data:
            # Named list
            list_path = table_name
        else:
            raise ValueError(f"Cannot find list for table '{table_name}'")
        
        if list_path is not None:
            current_list = yaml_data[list_path]
            if not isinstance(current_list, list):
                raise ValueError(f"Expected list at '{list_path}', found {type(current_list).__name__}")

        writer.append_item(list_path, item, doc=document_index)
    
    def _merge_into_dict_table(
        self,
        table_name: str,
        path_value_pairs: Dict[str, Any],
        writer: Any
    ) -> None:
        """
        Merge values into a dict-based table.
        
        Args:
            table_name: Table name
            path_value_pairs: Dictionary of path→value to insert
            writer: YamlWriter instance
        """
        # For dict tables, insert each path as a new key
        for path, value in path_value_pairs.items():
            # Construct full path with table name if needed
            if table_name and not path.startswith(table_name + '.'):
                full_path = f"{table_name}.{path}"
            else:
                full_path = path
            
            try:
                writer.insert_value(full_path, value)
            except YamlWriterPolicyError:
                raise
            except ValueError:
                # Key already exists - this is an error for INSERT
                raise ValueError(f"Key '{full_path}' already exists. Use UPDATE to modify existing data.")
    
    def _update_database(self, table_name: str, columns: List[str], rows: List[Dict[str, Any]]) -> None:
        """
        Update the in-memory database with newly inserted rows.
        
        This ensures the database state matches the YAML file state after INSERT.
        If new columns are being added, recreates the DuckDB table with new schema.
        
        Args:
            table_name: Table name
            columns: Column names
            rows: List of row dictionaries to insert
        """
        # Get existing columns in DuckDB
        try:
            schema = self.db.con.execute(f"DESCRIBE {table_name}").fetchdf()
            existing_cols = set(schema['column_name'].tolist())
        except Exception:
            existing_cols = set()
        
        # Check if we need to add columns
        new_cols = [col for col in columns if col not in existing_cols and col != '_yaml_path']
        
        if new_cols:
            # Need to recreate table with new schema
            # First, get existing data
            try:
                existing_data = self.db.con.execute(f"SELECT * FROM {table_name}").fetchdf()
            except Exception:
                existing_data = None
            
            # Drop and recreate table with new schema
            try:
                self.db.con.execute(f"DROP TABLE IF EXISTS {table_name}")
                
                # Build CREATE TABLE statement with all columns
                all_cols = ['_yaml_path VARCHAR'] + [
                    f"{col} {self._infer_duckdb_type(next((row.get(col) for row in rows if col in row), None))}"
                    for col in columns if col != '_yaml_path'
                ]
                create_sql = f"CREATE TABLE {table_name} ({', '.join(all_cols)})"
                self.db.con.execute(create_sql)
                
                # Re-insert existing data if any
                if existing_data is not None and len(existing_data) > 0:
                    # Only insert columns that exist in both old and new schema
                    common_cols = [col for col in existing_data.columns if col in columns or col == '_yaml_path']
                    if common_cols:
                        cols_str = ", ".join(common_cols)
                        values_str = ", ".join([
                            "(" + ", ".join([
                                self._format_sql_value(row[col]) for col in common_cols
                            ]) + ")"
                            for _, row in existing_data.iterrows()
                        ])
                        reinsert_sql = f"INSERT INTO {table_name} ({cols_str}) VALUES {values_str}"
                        self.db.con.execute(reinsert_sql)
                
            except Exception as e:
                raise RuntimeError(
                    f"Failed to recreate DuckDB table '{table_name}' with new columns: {e}"
                ) from e
        
        # Build INSERT statement for DuckDB
        values_str = ", ".join([
            "(" + ", ".join([
                self._format_sql_value(row.get(col)) for col in columns
            ]) + ")"
            for row in rows
        ])
        
        columns_str = ", ".join(columns)
        insert_sql = f"INSERT INTO {table_name} ({columns_str}) VALUES {values_str}"
        
        # Execute directly on DuckDB connection (bypass interceptor)
        try:
            self.db.con.execute(insert_sql)
        except Exception as e:
            # Database sync failed - this is critical, must fail the operation
            raise RuntimeError(
                f"YAML file updated successfully, but failed to synchronize in-memory database: {e}. "
                f"Reload the YamlQL instance to re-sync from file."
            ) from e
    
    def _infer_duckdb_type(self, value: Any) -> str:
        """
        Infer DuckDB column type from a Python value.
        
        Args:
            value: Sample Python value
            
        Returns:
            DuckDB type string (VARCHAR, INTEGER, DOUBLE, BOOLEAN, etc.)
        """
        if value is None:
            return "VARCHAR"  # Default to VARCHAR for NULL
        elif isinstance(value, bool):
            return "BOOLEAN"
        elif isinstance(value, int):
            return "INTEGER"
        elif isinstance(value, float):
            return "DOUBLE"
        else:
            return "VARCHAR"
    
    def _format_sql_value(self, value: Any) -> str:
        """
        Format a Python value for SQL insertion.
        
        Args:
            value: Python value
            
        Returns:
            SQL-formatted string representation
        """
        if value is None:
            return "NULL"
        elif isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            # Escape single quotes
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        else:
            # Fallback: convert to string
            escaped = str(value).replace("'", "''")
            return f"'{escaped}'"


class UpdateHandler:
    """
    Handles UPDATE operations for YamlQL.
    
    Converts SQL UPDATE statements into YAML modifications and persists
    them to disk while maintaining format preservation and atomicity.
    
    UPDATE operations are more complex than INSERT because they require:
    1. Row identification via WHERE clause
    2. Using _yaml_path to locate exact YAML nodes to modify
    3. Precise targeting to avoid updating wrong data
    
    Example usage:
        >>> handler = UpdateHandler(
        ...     file_path="config.yaml",
        ...     column_name_map={"users": {"user_name": "user-name"}},
        ...     original_data={"users": []},
        ...     db=database_instance
        ... )
        >>> result = handler.handle(parsed_update)
        >>> print(result)
        {'success': True, 'rows_updated': 1, 'message': '1 row updated in users'}
    """
    
    def __init__(
        self,
        file_path: str,
        column_name_map: Dict[str, Dict[str, str]],
        original_data: dict,
        db: "Database"
    ):
        """
        Initialize the UPDATE handler.
        
        Args:
            file_path: Path to the YAML file to modify
            column_name_map: Bidirectional column mapping from transformer.
                            Format: {table_name: {sanitized_col: original_col, ...}}
            original_data: Current YAML structure (before modifications)
            db: Database instance for schema validation and row identification
        """
        self.file_path = file_path
        self.column_name_map = column_name_map
        self.original_data = original_data
        self.db = db
        self.reverse_transformer = ReverseTransformer(column_name_map, original_data)
    
    def handle(self, parsed_sql: Update) -> Dict[str, Any]:
        """
        Execute an UPDATE statement.
        
        This method:
        1. Parses the UPDATE to extract table, SET clauses, and WHERE condition
        2. Queries the database to find matching rows (including _yaml_path)
        3. For each matching row, locates its YAML node via _yaml_path
        4. Converts SET clause updates to YAML paths using reverse transformer
        5. Writes changes atomically to the YAML file
        6. Updates the in-memory database to maintain consistency
        
        Args:
            parsed_sql: Parsed UPDATE statement from sqlglot
        
        Returns:
            dict with keys:
            - 'success': bool - True if operation succeeded
            - 'rows_updated': int - Number of rows updated
            - 'message': str - Human-readable result message
            
        Raises:
            ValueError: If table/columns don't exist or WHERE clause is invalid
            IOError: If file write fails
            
        Examples:
            UPDATE users SET age = 31 WHERE name = 'Alice'
            UPDATE users SET age = age + 1, status = 'active' WHERE age < 30
            UPDATE users SET status = 'inactive'  -- Updates all rows
        """
        try:
            # Step 1: Extract UPDATE components
            table_name = self._extract_table_name(parsed_sql)
            set_clauses = self._extract_set_clauses(parsed_sql)
            where_clause = self._extract_where_clause(parsed_sql)
            
            # Step 2: Validate schema
            self._validate_table_exists(table_name)
            self._validate_columns_exist(table_name, list(set_clauses.keys()))
            
            # Step 3: Find matching rows using WHERE clause
            matching_rows = self._find_matching_rows(table_name, where_clause)
            
            if len(matching_rows) == 0:
                return {
                    'success': True,
                    'rows_updated': 0,
                    'message': f"0 rows updated in {table_name} (no rows matched WHERE clause)"
                }
            
            # Step 4: Update rows in YAML file atomically
            rows_updated = self._update_rows(table_name, matching_rows, set_clauses)
            
            # Step 5: Update in-memory database
            try:
                self._update_database(table_name, set_clauses, where_clause)
            except RuntimeError as db_error:
                # Database sync failed, but YAML write succeeded
                # Return success with warning to reload
                import warnings
                warnings.warn(str(db_error))
                return {
                    'success': True,
                    'rows_updated': rows_updated,
                    'message': (
                        f"{rows_updated} row{'s' if rows_updated != 1 else ''} updated in {table_name}. "
                        f"Warning: In-memory database out of sync. Create a new YamlQL instance to reload."
                    )
                }
            
            return {
                'success': True,
                'rows_updated': rows_updated,
                'message': f"{rows_updated} row{'s' if rows_updated != 1 else ''} updated in {table_name}"
            }
            
        except Exception as e:
            return {
                'success': False,
                'rows_updated': 0,
                'message': f"UPDATE failed: {str(e)}"
            }
    
    def _extract_table_name(self, parsed_sql: Update) -> str:
        """
        Extract the target table name from UPDATE statement.
        
        Args:
            parsed_sql: Parsed UPDATE expression
            
        Returns:
            Table name as string
            
        Raises:
            ValueError: If table name cannot be extracted
        """
        if not parsed_sql.this:
            raise ValueError("UPDATE statement missing target table")
        
        # Get table name from the 'this' attribute
        table_expr = parsed_sql.this
        
        if hasattr(table_expr, 'name'):
            table_name = table_expr.name
        else:
            # Fallback to string representation
            table_name = str(table_expr).strip('"').strip("'").strip('`')
        
        if not table_name:
            raise ValueError("Could not extract table name from UPDATE statement")
        
        return table_name
    
    def _extract_set_clauses(self, parsed_sql: Update) -> Dict[str, Any]:
        """
        Extract SET clause column→value mappings from UPDATE statement.
        
        For UPDATE users SET age = 31, status = 'active', extracts:
        {'age': 31, 'status': 'active'}
        
        Args:
            parsed_sql: Parsed UPDATE expression
            
        Returns:
            Dictionary mapping column names to new values
            
        Raises:
            ValueError: If SET clause cannot be parsed
        """
        set_clauses = {}
        
        # Get SET expressions
        if not parsed_sql.expressions:
            raise ValueError("UPDATE statement missing SET clause")
        
        # Parse each SET expression (column = value)
        for expr in parsed_sql.expressions:
            # Each expression is an EQ (equality) with left=column, right=value
            if hasattr(expr, 'this') and hasattr(expr, 'expression'):
                # expr.this is the column (left side)
                # expr.expression is the value (right side)
                column_expr = expr.this
                value_expr = expr.expression
                
                # Extract column name
                if hasattr(column_expr, 'name'):
                    column_name = column_expr.name
                else:
                    column_name = str(column_expr).strip('"').strip("'")
                
                # Extract value (handle literals, expressions, etc.)
                value = self._extract_value(value_expr)
                
                set_clauses[column_name] = value
            else:
                raise ValueError(f"Unsupported SET clause format: {expr}")
        
        if not set_clauses:
            raise ValueError("No valid SET clauses found in UPDATE statement")
        
        return set_clauses
    
    def _extract_value(self, expr: Any) -> Any:
        """
        Extract Python value from SQL expression.
        
        Handles literals, NULL, booleans, and simple expressions.
        For complex expressions (e.g., age + 1), delegates to DuckDB evaluation.
        
        Args:
            expr: SQL expression
            
        Returns:
            Python value (int, float, str, bool, None, or SQL string for expressions)
        """
        from sqlglot.expressions import Literal, Null, Boolean, Column
        
        if isinstance(expr, Null):
            return None
        
        if isinstance(expr, Boolean):
            return expr.this
        
        if isinstance(expr, Literal):
            # Get the literal value
            value_str = expr.this
            
            # Try to infer type
            if expr.is_int:
                return int(value_str)
            elif expr.is_number:
                return float(value_str)
            elif expr.is_string:
                return value_str
            else:
                return value_str
        
        if isinstance(expr, Column):
            # This is a column reference in an expression like "age + 1"
            # We can't evaluate this here - need to let DuckDB handle it
            # Return the SQL representation for later evaluation
            return f"__EXPR__{str(expr)}"
        
        # For complex expressions, return SQL string for DuckDB evaluation
        expr_str = str(expr)
        if any(op in expr_str for op in ['+', '-', '*', '/', '||']):
            return f"__EXPR__{expr_str}"
        
        # Fallback: try to treat as literal string
        return str(expr)
    
    def _extract_where_clause(self, parsed_sql: Update) -> Optional[str]:
        """
        Extract WHERE clause from UPDATE statement.
        
        Args:
            parsed_sql: Parsed UPDATE expression
            
        Returns:
            WHERE clause as SQL string (without "WHERE" keyword), or None if no WHERE
        """
        if not parsed_sql.args.get('where'):
            return None
        
        where_expr = parsed_sql.args['where']
        # Get the condition expression (without WHERE keyword)
        # where_expr is a Where object, where_expr.this is the actual condition
        condition_expr = where_expr.this
        return str(condition_expr)
    
    def _validate_table_exists(self, table_name: str) -> None:
        """
        Validate that the table exists in the database.
        
        Args:
            table_name: Table name to validate
            
        Raises:
            ValueError: If table does not exist
        """
        try:
            # Try to describe the table (will fail if table doesn't exist)
            self.db.query(f"DESCRIBE {table_name}")
        except Exception:
            raise ValueError(f"Table '{table_name}' does not exist")
    
    def _validate_columns_exist(self, table_name: str, columns: List[str]) -> None:
        """
        Validate that all columns exist in the table.
        
        Args:
            table_name: Table name
            columns: List of column names to validate
            
        Raises:
            ValueError: If any column does not exist
        """
        # Get table schema
        schema = self.db.query(f"DESCRIBE {table_name}")
        existing_columns = set(schema['column_name'].tolist())
        
        # Check each column
        for col in columns:
            if col not in existing_columns:
                raise ValueError(
                    f"Column '{col}' does not exist in table '{table_name}'. "
                    f"Available columns: {', '.join(sorted(existing_columns))}"
                )
    
    def _find_matching_rows(self, table_name: str, where_clause: Optional[str]) -> pd.DataFrame:
        """
        Find rows matching the WHERE clause.
        
        Executes a SELECT query with WHERE clause to identify which rows to update.
        CRITICAL: Must include _yaml_path column to locate YAML nodes.
        
        Args:
            table_name: Table name
            where_clause: WHERE clause SQL (without "WHERE" keyword), or None for all rows
            
        Returns:
            DataFrame with matching rows, including _yaml_path column
            
        Raises:
            ValueError: If WHERE clause is invalid or _yaml_path missing
        """
        # Build SELECT query to find matching rows
        if where_clause:
            select_sql = f"SELECT * FROM {table_name} WHERE {where_clause}"
        else:
            select_sql = f"SELECT * FROM {table_name}"
        
        # Execute query
        try:
            result = self.db.query(select_sql)
        except Exception as e:
            raise ValueError(f"Failed to evaluate WHERE clause: {str(e)}")
        
        # Verify _yaml_path column exists
        if '_yaml_path' not in result.columns:
            raise ValueError(
                f"Table '{table_name}' missing _yaml_path column. "
                "Cannot identify YAML nodes for UPDATE operation."
            )
        
        return result
    
    def _update_rows(
        self,
        table_name: str,
        matching_rows: pd.DataFrame,
        set_clauses: Dict[str, Any]
    ) -> int:
        """
        Update rows in the YAML file using transaction manager.
        
        For each matching row:
        1. Get its _yaml_path to locate the YAML node
        2. Convert SET clause columns to YAML paths using reverse transformer
        3. Use writer.set_value() to update each path
        
        Args:
            table_name: Table name
            matching_rows: DataFrame of rows to update (with _yaml_path)
            set_clauses: Dictionary of column→new_value mappings
            
        Returns:
            Number of rows updated
            
        Raises:
            IOError: If write fails
        """
        with TransactionManager(self.file_path) as txn:
            writer = txn.get_writer()
            writer.load()
            
            # Update each matching row
            for _, row in matching_rows.iterrows():
                yaml_path = row['_yaml_path']
                writer_path_prefix = self._strip_root_prefix(yaml_path)
                if writer_path_prefix:
                    document_key = writer_path_prefix.split('.', 1)[0]
                    document_index = writer.find_document(document_key)
                elif writer.doc_count == 1:
                    document_index = 0
                else:
                    raise ValueError(
                        "Cannot update a root-level list in a multi-document YAML stream"
                    )

                if document_index is None:
                    document_index = writer.find_document(table_name)
                if document_index is None:
                    raise ValueError(
                        f"Cannot find a mapping document for table '{table_name}'"
                    )
                yaml_data = writer.documents[document_index]
                
                # For each SET clause, update the corresponding YAML path
                for col_name, new_value in set_clauses.items():
                    # Handle expressions (e.g., age = age + 1)
                    if isinstance(new_value, str) and new_value.startswith('__EXPR__'):
                        # Extract expression from marker
                        expr = new_value[8:]  # Remove '__EXPR__' prefix
                        
                        # Evaluate expression via DuckDB using current row values
                        # Create a temporary single-row query to evaluate the expression
                        eval_query = f"SELECT {expr} AS result FROM (VALUES ({', '.join([self._format_sql_value(row[c]) for c in row.index if c != '_yaml_path'])})) AS t({', '.join([c for c in row.index if c != '_yaml_path'])})"
                        try:
                            result_df = self.db.con.execute(eval_query).fetchdf()
                            new_value = result_df.iloc[0]['result']
                        except Exception as eval_error:
                            raise ValueError(
                                f"Failed to evaluate expression '{expr}' for column '{col_name}': {eval_error}"
                            ) from eval_error
                    
                    # Convert column name to YAML path using reverse transformer
                    target_path = self._resolve_yaml_path(
                        table_name, col_name, yaml_path, yaml_data
                    )
                    
                    # Strip "root." prefix for writer (same as DELETE handler)
                    writer_path = self._strip_root_prefix(target_path)
                    
                    # Convert numpy types to Python native types for YAML serialization
                    new_value = self._convert_numpy_types(new_value)
                    
                    # Update the value in YAML
                    try:
                        if writer_path:
                            writer.set_value(writer_path, new_value)
                        else:
                            # Updating root itself (rare case)
                            if writer.doc_count != 1:
                                raise ValueError(
                                    "Cannot update a root document in a multi-document YAML stream"
                                )
                            writer.set_root(new_value)
                    except YamlWriterPolicyError as error:
                        raise ValueError(
                            f"Cannot update table '{table_name}', row '{yaml_path}', "
                            f"column '{col_name}': {error}"
                        ) from error
        
        return len(matching_rows)
    
    def _resolve_yaml_path(
        self,
        table_name: str,
        column_name: str,
        row_yaml_path: str,
        yaml_data: Any
    ) -> str:
        """
        Resolve the full YAML path for a column update.
        
        Combines the row's _yaml_path with the column's original name to produce
        the full path to update in the YAML structure.
        
        Args:
            table_name: Table name
            column_name: Column name (sanitized)
            row_yaml_path: The _yaml_path from the row (e.g., "root.0")
            yaml_data: Current YAML data structure
            
        Returns:
            Full YAML path (e.g., "root.0.age")
            
        Examples:
            table_name="users", column_name="age", row_yaml_path="root.0"
            → "root.0.age"
            
            table_name="users", column_name="user_name", row_yaml_path="root.1"
            → "root.1.user-name" (if original was hyphenated)
        """
        # Get column mapping for this table
        table_map = self.column_name_map.get(table_name, {})
        
        # Look up original column name
        if column_name in table_map:
            original_col = table_map[column_name]
        else:
            original_col = column_name
        
        # Construct full path
        # row_yaml_path points to the object (e.g., "root.0")
        # We need to append the field name
        
        # Handle root-level list vs named list
        if row_yaml_path == 'root':
            # Single object at root
            return original_col
        elif row_yaml_path.startswith('root.'):
            # List item (e.g., "root.0", "root.1")
            # Check if this is a root-level list or named list
            parts = row_yaml_path.split('.')
            
            if len(parts) == 2 and parts[1].isdigit():
                # Root-level list: root.0 → append column name
                return f"{row_yaml_path}.{original_col}"
            else:
                # Named list: root.users.0 → append column name
                return f"{row_yaml_path}.{original_col}"
        else:
            # Custom path - append column name
            return f"{row_yaml_path}.{original_col}"
    
    def _strip_root_prefix(self, yaml_path: str) -> str:
        """
        Strip 'root.' prefix from yaml_path for writer operations.
        
        The _yaml_path column tracks paths like "root.users.0", but YamlWriter
        expects paths relative to data root like "users.0".
        
        Args:
            yaml_path: Full yaml_path with root prefix
            
        Returns:
            Path without root prefix, or empty string if path is exactly "root"
            
        Examples:
            "root.users.0" → "users.0"
            "root.0" → "0"
            "root" → ""
        """
        if yaml_path == "root":
            return ""
        elif yaml_path.startswith("root."):
            return yaml_path[5:]  # Strip "root."
        else:
            # Path doesn't start with root (shouldn't happen, but handle gracefully)
            return yaml_path
    
    def _convert_numpy_types(self, value: Any) -> Any:
        """
        Convert numpy types to Python native types for YAML serialization.
        
        ruamel.yaml cannot serialize numpy types (np.int32, np.float64, etc.),
        which are returned by DuckDB when evaluating expressions.
        
        Args:
            value: Value to convert (may be numpy type or Python native)
            
        Returns:
            Python native type equivalent
            
        Examples:
            np.int32(42) → 42
            np.float64(3.14) → 3.14
            "string" → "string" (unchanged)
        """
        # Check if value has numpy item() method (indicates numpy scalar)
        if hasattr(value, 'item'):
            return value.item()  # Converts np.int32(42) → 42
        return value
    
    def _update_database(
        self,
        table_name: str,
        set_clauses: Dict[str, Any],
        where_clause: Optional[str]
    ) -> None:
        """
        Update the in-memory database to match the YAML file state.
        
        Executes the UPDATE statement in DuckDB to keep in-memory state synchronized.
        
        Args:
            table_name: Table name
            set_clauses: Dictionary of column→value mappings
            where_clause: WHERE clause SQL (without "WHERE" keyword), or None
        """
        # Build UPDATE statement for DuckDB
        set_parts = []
        for col, value in set_clauses.items():
            # Handle expressions
            if isinstance(value, str) and value.startswith('__EXPR__'):
                # Remove __EXPR__ prefix for DuckDB
                expr = value[8:]  # Remove "__EXPR__"
                set_parts.append(f"{col} = {expr}")
            else:
                formatted_value = self._format_sql_value(value)
                set_parts.append(f"{col} = {formatted_value}")
        
        set_clause_str = ", ".join(set_parts)
        
        if where_clause:
            update_sql = f"UPDATE {table_name} SET {set_clause_str} WHERE {where_clause}"
        else:
            update_sql = f"UPDATE {table_name} SET {set_clause_str}"
        
        # Execute directly on DuckDB connection (bypass interceptor)
        try:
            self.db.con.execute(update_sql)
        except Exception as e:
            # Database sync failed - this is critical, must fail the operation
            raise RuntimeError(
                f"YAML file updated successfully, but failed to synchronize in-memory database: {e}. "
                f"Reload the YamlQL instance to re-sync from file."
            ) from e
    
    def _format_sql_value(self, value: Any) -> str:
        """
        Format a Python value for SQL UPDATE.
        
        Args:
            value: Python value
            
        Returns:
            SQL-formatted string representation
        """
        if value is None:
            return "NULL"
        elif isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            # Escape single quotes
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        else:
            # Fallback: convert to string
            escaped = str(value).replace("'", "''")
            return f"'{escaped}'"


class DeleteHandler:
    """
    Handles DELETE operations for YamlQL.
    
    Converts SQL DELETE statements into YAML modifications and persists
    them to disk while maintaining format preservation and atomicity.
    
    DELETE operations are destructive and require:
    1. Row identification via WHERE clause
    2. Using _yaml_path to locate exact YAML nodes to remove
    3. Careful handling of list reindexing after deletions
    4. Optional referential integrity checks for child tables
    
    Example usage:
        >>> handler = DeleteHandler(
        ...     file_path="config.yaml",
        ...     column_name_map={"users": {"user_name": "user-name"}},
        ...     original_data={"users": []},
        ...     db=database_instance
        ... )
        >>> result = handler.handle(parsed_delete)
        >>> print(result)
        {'success': True, 'rows_deleted': 1, 'message': '1 row deleted from users'}
    """
    
    def __init__(
        self,
        file_path: str,
        column_name_map: Dict[str, Dict[str, str]],
        original_data: dict,
        db: "Database"
    ):
        """
        Initialize the DELETE handler.
        
        Args:
            file_path: Path to the YAML file to modify
            column_name_map: Bidirectional column mapping from transformer.
                            Format: {table_name: {sanitized_col: original_col, ...}}
            original_data: Current YAML structure (before modifications)
            db: Database instance for schema validation and row identification
        """
        self.file_path = file_path
        self.column_name_map = column_name_map
        self.original_data = original_data
        self.db = db
        self.reverse_transformer = ReverseTransformer(column_name_map, original_data)
    
    def handle(self, parsed_sql: Delete) -> Dict[str, Any]:
        """
        Execute a DELETE statement.
        
        This method:
        1. Parses the DELETE to extract table and WHERE condition
        2. Queries the database to find matching rows (including _yaml_path)
        3. For each matching row, locates its YAML node via _yaml_path
        4. Deletes the YAML nodes using writer.delete_value()
        5. Writes changes atomically to the YAML file
        6. Updates the in-memory database to maintain consistency
        
        Args:
            parsed_sql: Parsed DELETE statement from sqlglot
        
        Returns:
            dict with keys:
            - 'success': bool - True if operation succeeded
            - 'rows_deleted': int - Number of rows deleted
            - 'message': str - Human-readable result message
            
        Raises:
            ValueError: If table doesn't exist or WHERE clause is invalid
            IOError: If file write fails
            
        Examples:
            DELETE FROM users WHERE age < 25
            DELETE FROM users WHERE name = 'Alice'
            DELETE FROM users  -- Deletes all rows (dangerous!)
        """
        try:
            # Step 1: Extract DELETE components
            table_name = self._extract_table_name(parsed_sql)
            where_clause = self._extract_where_clause(parsed_sql)
            
            # Step 2: Validate schema
            self._validate_table_exists(table_name)
            
            # Step 3: Find matching rows using WHERE clause
            matching_rows = self._find_matching_rows(table_name, where_clause)
            
            if len(matching_rows) == 0:
                return {
                    'success': True,
                    'rows_deleted': 0,
                    'message': f"0 rows deleted from {table_name} (no rows matched WHERE clause)"
                }
            
            # Step 4: Delete rows from YAML file atomically
            rows_deleted = self._delete_rows(table_name, matching_rows)
            
            # Step 5: Update in-memory database
            try:
                self._update_database(table_name, where_clause)
            except RuntimeError as db_error:
                # Database sync failed, but YAML write succeeded
                # Return success with warning to reload
                import warnings
                warnings.warn(str(db_error))
                return {
                    'success': True,
                    'rows_deleted': rows_deleted,
                    'message': (
                        f"{rows_deleted} row{'s' if rows_deleted != 1 else ''} deleted from {table_name}. "
                        f"Warning: In-memory database out of sync. Create a new YamlQL instance to reload."
                    )
                }
            
            return {
                'success': True,
                'rows_deleted': rows_deleted,
                'message': f"{rows_deleted} row{'s' if rows_deleted != 1 else ''} deleted from {table_name}"
            }
            
        except Exception as e:
            return {
                'success': False,
                'rows_deleted': 0,
                'message': f"DELETE failed: {str(e)}"
            }
    
    def _extract_table_name(self, parsed_sql: Delete) -> str:
        """
        Extract the target table name from DELETE statement.
        
        Args:
            parsed_sql: Parsed DELETE expression
            
        Returns:
            Table name as string
            
        Raises:
            ValueError: If table name cannot be extracted
        """
        if not parsed_sql.this:
            raise ValueError("DELETE statement missing target table")
        
        # Get table name from the 'this' attribute
        table_expr = parsed_sql.this
        
        if hasattr(table_expr, 'name'):
            table_name = table_expr.name
        else:
            # Fallback to string representation
            table_name = str(table_expr).strip('"').strip("'").strip('`')
        
        if not table_name:
            raise ValueError("Could not extract table name from DELETE statement")
        
        return table_name
    
    def _extract_where_clause(self, parsed_sql: Delete) -> Optional[str]:
        """
        Extract WHERE clause from DELETE statement.
        
        Args:
            parsed_sql: Parsed DELETE expression
            
        Returns:
            WHERE clause as SQL string (without "WHERE" keyword), or None if no WHERE
        """
        if not parsed_sql.args.get('where'):
            return None
        
        where_expr = parsed_sql.args['where']
        # Get the condition expression (without WHERE keyword)
        # where_expr is a Where object, where_expr.this is the actual condition
        condition_expr = where_expr.this
        return str(condition_expr)
    
    def _validate_table_exists(self, table_name: str) -> None:
        """
        Validate that the table exists in the database.
        
        Args:
            table_name: Table name to validate
            
        Raises:
            ValueError: If table does not exist
        """
        try:
            # Try to describe the table (will fail if table doesn't exist)
            self.db.query(f"DESCRIBE {table_name}")
        except Exception:
            raise ValueError(f"Table '{table_name}' does not exist")
    
    def _find_matching_rows(self, table_name: str, where_clause: Optional[str]) -> pd.DataFrame:
        """
        Find rows matching the WHERE clause.
        
        Executes a SELECT query with WHERE clause to identify which rows to delete.
        CRITICAL: Must include _yaml_path column to locate YAML nodes.
        
        Args:
            table_name: Table name
            where_clause: WHERE clause SQL (without "WHERE" keyword), or None for all rows
            
        Returns:
            DataFrame with matching rows, including _yaml_path column
            
        Raises:
            ValueError: If WHERE clause is invalid or _yaml_path missing
        """
        # Build SELECT query to find matching rows
        if where_clause:
            select_sql = f"SELECT * FROM {table_name} WHERE {where_clause}"
        else:
            select_sql = f"SELECT * FROM {table_name}"
        
        # Execute query
        try:
            result = self.db.query(select_sql)
        except Exception as e:
            raise ValueError(f"Failed to evaluate WHERE clause: {str(e)}")
        
        # Verify _yaml_path column exists
        if '_yaml_path' not in result.columns:
            raise ValueError(
                f"Table '{table_name}' missing _yaml_path column. "
                "Cannot identify YAML nodes for DELETE operation."
            )
        
        return result
    
    def _delete_rows(
        self,
        table_name: str,
        matching_rows: pd.DataFrame
    ) -> int:
        """
        Delete rows from the YAML file using transaction manager.
        
        For each matching row:
        1. Get its _yaml_path to locate the YAML node
        2. Use writer.delete_value() to remove the node
        
        IMPORTANT: Delete in reverse order (highest index first) for list-based
        tables to avoid index shifting issues during deletion.
        
        Args:
            table_name: Table name
            matching_rows: DataFrame of rows to delete (with _yaml_path)
            
        Returns:
            Number of rows deleted
            
        Raises:
            IOError: If write fails
        """
        with TransactionManager(self.file_path) as txn:
            writer = txn.get_writer()
            writer.load()
            
            # Extract _yaml_path values and sort in reverse order
            # This ensures we delete from highest index to lowest for lists
            yaml_paths = matching_rows['_yaml_path'].tolist()
            yaml_paths_sorted = self._sort_paths_for_deletion(yaml_paths)
            
            # Delete each YAML node
            for yaml_path in yaml_paths_sorted:
                try:
                    # Strip "root." prefix if present - the writer works with paths relative to data root
                    # _yaml_path format: "root.users.0" or "root.0" or "root"
                    writer_path = self._strip_root_prefix(yaml_path)
                    
                    if writer_path:
                        writer.delete_value(writer_path)
                    else:
                        # Special case: deleting root itself (should rarely happen)
                        import sys
                        print(f"Warning: Cannot delete root path '{yaml_path}'", file=sys.stderr)
                except YamlWriterPolicyError as error:
                    raise ValueError(
                        f"Cannot delete from table '{table_name}', row '{yaml_path}': {error}"
                    ) from error
                except ValueError as e:
                    # Log warning but continue with other deletions
                    import sys
                    print(f"Warning: Failed to delete path '{yaml_path}': {e}", file=sys.stderr)
        
        return len(matching_rows)
    
    def _strip_root_prefix(self, yaml_path: str) -> str:
        """
        Strip 'root.' prefix from yaml_path for writer operations.
        
        The _yaml_path column tracks paths like "root.users.0", but YamlWriter
        expects paths relative to data root like "users.0".
        
        Args:
            yaml_path: Full yaml_path with root prefix
            
        Returns:
            Path without root prefix, or empty string if path is exactly "root"
            
        Examples:
            "root.users.0" → "users.0"
            "root.0" → "0"
            "root" → ""
        """
        if yaml_path == "root":
            return ""
        elif yaml_path.startswith("root."):
            return yaml_path[5:]  # Strip "root."
        else:
            # Path doesn't start with root (shouldn't happen, but handle gracefully)
            return yaml_path
    
    def _sort_paths_for_deletion(self, yaml_paths: List[str]) -> List[str]:
        """
        Sort YAML paths for safe deletion order.
        
        For list-based paths (e.g., "root.0", "root.1", "root.2"), sort in
        descending order by index to avoid index shifting during deletion.
        
        For dict-based paths, order doesn't matter.
        
        Args:
            yaml_paths: List of YAML paths to delete
            
        Returns:
            Sorted list of paths (highest index first for lists)
            
        Examples:
            ["root.0", "root.2", "root.1"] → ["root.2", "root.1", "root.0"]
            ["root.users.0", "root.users.2"] → ["root.users.2", "root.users.0"]
        """
        def path_sort_key(path: str) -> tuple:
            """
            Generate sort key for a path.
            
            Returns tuple: (path_prefix, -index) where index is negated
            to sort in descending order.
            """
            parts = path.split('.')
            
            # Find the last numeric part (list index)
            for i in range(len(parts) - 1, -1, -1):
                if parts[i].isdigit():
                    # Found numeric index
                    prefix = '.'.join(parts[:i])
                    index = int(parts[i])
                    # Return negated index for descending sort
                    return (prefix, -index, path)
            
            # No numeric index found - just return path
            return ('', 0, path)
        
        # Sort by the key function
        return sorted(yaml_paths, key=path_sort_key)
    
    def _update_database(
        self,
        table_name: str,
        where_clause: Optional[str]
    ) -> None:
        """
        Update the in-memory database to match the YAML file state.
        
        Executes the DELETE statement in DuckDB to keep in-memory state synchronized.
        
        Args:
            table_name: Table name
            where_clause: WHERE clause SQL (without "WHERE" keyword), or None
        """
        # Build DELETE statement for DuckDB
        if where_clause:
            delete_sql = f"DELETE FROM {table_name} WHERE {where_clause}"
        else:
            delete_sql = f"DELETE FROM {table_name}"
        
        # Execute directly on DuckDB connection (bypass interceptor)
        try:
            self.db.con.execute(delete_sql)
        except Exception as e:
            # Database sync failed - this is critical, must fail the operation
            raise RuntimeError(
                f"YAML file updated successfully, but failed to synchronize in-memory database: {e}. "
                f"Reload the YamlQL instance to re-sync from file."
            ) from e
