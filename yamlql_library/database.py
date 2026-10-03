import duckdb
import pandas as pd
from typing import List, Tuple, Dict, Any, Optional
from .sql_interceptor import SqlInterceptor
from .crud_handlers import InsertHandler, UpdateHandler, DeleteHandler

class Database:
    """Manages an in-memory DuckDB database."""

    def __init__(self, mode: str = 'r', file_path: str = None):
        """Initializes a new in-memory DuckDB connection.
        
        Args:
            mode: File mode ('r' for read-only, 'rw' or 'w' for read-write).
            file_path: Path to the YAML file (for write operations).
        """
        self.con = duckdb.connect(database=':memory:')
        self.interceptor = SqlInterceptor()
        self.mode = mode
        self.file_path = file_path
        self.mapping_column_paths: Dict[str, Dict[str, tuple]] = {}
        self.table_doc_map: Dict[str, Any] = {}
        
        # CRUD handlers (initialized after transformation via initialize_crud_handlers)
        self.insert_handler: Optional[InsertHandler] = None
        self.update_handler: Optional[UpdateHandler] = None
        self.delete_handler: Optional[DeleteHandler] = None

    def create_tables(self, tables: List[Tuple[str, pd.DataFrame]]):
        """
        Registers a list of pandas DataFrames as tables in DuckDB.

        Args:
            tables: A list of tuples, where each tuple contains a table name
                    and the corresponding DataFrame.
        """
        registered_names = []
        try:
            for name, df in tables:
                self.con.register(name, df)
                registered_names.append(name)
        except Exception:
            for name in registered_names:
                self.con.unregister(name)
            raise
    
    def initialize_crud_handlers(
        self,
        column_name_map: Dict[str, Dict[str, str]],
        mapping_column_paths: Dict[str, Dict[str, tuple]],
        original_data: dict,
        table_doc_map: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Initialize CRUD handlers with transformation metadata.
        
        This method should be called after create_tables() to enable write operations.
        It initializes the INSERT, UPDATE, and DELETE handlers with the necessary
        context to perform bidirectional transformation between SQL and YAML.
        
        Args:
            column_name_map: Bidirectional column mapping from DataTransformer.
                            Format: {table_name: {sanitized_col: original_col, ...}}
            original_data: Original YAML structure loaded from file (before transformation)
        """
        # Only initialize handlers if in write mode
        if self.mode not in ['rw', 'w']:
            return

        self.table_doc_map = table_doc_map or self.table_doc_map
        
        # Initialize all three CRUD handlers
        self.insert_handler = InsertHandler(
            file_path=self.file_path,
            column_name_map=column_name_map,
            mapping_column_paths=mapping_column_paths,
            original_data=original_data,
            db=self
        )
        
        self.update_handler = UpdateHandler(
            file_path=self.file_path,
            column_name_map=column_name_map,
            mapping_column_paths=mapping_column_paths,
            original_data=original_data,
            db=self
        )
        
        self.delete_handler = DeleteHandler(
            file_path=self.file_path,
            column_name_map=column_name_map,
            mapping_column_paths=mapping_column_paths,
            original_data=original_data,
            db=self
        )
        for handler in (self.insert_handler, self.update_handler, self.delete_handler):
            handler.table_doc_map = self.table_doc_map

    def query(self, sql_query: str) -> pd.DataFrame:
        """
        Executes a SQL query against the database.

        Args:
            sql_query: The SQL query to execute.

        Returns:
            A pandas DataFrame containing the query results.
            
        Raises:
            PermissionError: If attempting write operations in read-only mode.
            NotImplementedError: If CRUD handlers are not initialized.
        """
        # Classify the SQL statement
        classification = self.interceptor.classify(sql_query)
        
        if classification['needs_crud']:
            # Check write mode permission
            if self.mode not in ['rw', 'w']:
                raise PermissionError(
                    f"{classification['type']} operations require write mode. "
                    f"Initialize YamlQL with mode='rw' or mode='w', "
                    f"or use --writable flag in CLI."
                )
            
            # Check if handlers are initialized
            if self.insert_handler is None or self.update_handler is None or self.delete_handler is None:
                raise NotImplementedError(
                    f"{classification['type']} operations require CRUD handlers to be initialized. "
                    f"Call initialize_crud_handlers() after create_tables()."
                )
            
            # Route to appropriate CRUD handler
            if classification['type'] == 'INSERT':
                return self._handle_insert(classification['parsed'])
            
            elif classification['type'] == 'UPDATE':
                return self._handle_update(classification['parsed'])
            
            elif classification['type'] == 'DELETE':
                return self._handle_delete(classification['parsed'])
            
            else:
                raise NotImplementedError(
                    f"{classification['type']} operations are not supported. "
                    f"Only INSERT, UPDATE, and DELETE are currently implemented."
                )
        
        # Execute SELECT/DDL queries in DuckDB (existing behavior)
        result = self.con.execute(sql_query).fetchdf()
        mapping_columns = {
            column
            for columns in self.mapping_column_paths.values()
            for column in columns
        }
        for column in mapping_columns.intersection(result.columns):
            result[column] = result[column].astype(object).where(result[column].notna(), None)
        return result
    
    def _handle_insert(self, parsed_sql) -> Dict[str, Any]:
        """
        Handle INSERT operation via InsertHandler.
        
        Args:
            parsed_sql: Parsed INSERT statement from SqlInterceptor
            
        Returns:
            Dict containing operation result
            
        Raises:
            Exception: If the INSERT operation fails
        """
        result = self.insert_handler.handle(parsed_sql)
        
        # If operation failed, raise an exception
        if not result.get('success', False):
            raise Exception(result.get('message', 'INSERT operation failed'))
        
        return result
    
    def _handle_update(self, parsed_sql) -> Dict[str, Any]:
        """
        Handle UPDATE operation via UpdateHandler.
        
        Args:
            parsed_sql: Parsed UPDATE statement from SqlInterceptor
            
        Returns:
            Dict containing operation result
            
        Raises:
            Exception: If the UPDATE operation fails
        """
        result = self.update_handler.handle(parsed_sql)
        
        # If operation failed, raise an exception
        if not result.get('success', False):
            raise Exception(result.get('message', 'UPDATE operation failed'))
        
        return result
    
    def _handle_delete(self, parsed_sql) -> Dict[str, Any]:
        """
        Handle DELETE operation via DeleteHandler.
        
        Args:
            parsed_sql: Parsed DELETE statement from SqlInterceptor
            
        Returns:
            Dict containing operation result
            
        Raises:
            Exception: If the DELETE operation fails
        """
        result = self.delete_handler.handle(parsed_sql)
        
        # If operation failed, raise an exception
        if not result.get('success', False):
            raise Exception(result.get('message', 'DELETE operation failed'))
        
        return result

    def close(self):
        """Closes the database connection."""
        self.con.close() 