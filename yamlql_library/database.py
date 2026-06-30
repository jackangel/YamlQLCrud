import duckdb
import pandas as pd
from typing import List, Tuple
from .sql_interceptor import SqlInterceptor

class Database:
    """Manages an in-memory DuckDB database."""

    def __init__(self):
        """Initializes a new in-memory DuckDB connection."""
        self.con = duckdb.connect(database=':memory:')
        self.interceptor = SqlInterceptor()

    def create_tables(self, tables: List[Tuple[str, pd.DataFrame]]):
        """
        Registers a list of pandas DataFrames as tables in DuckDB.

        Args:
            tables: A list of tuples, where each tuple contains a table name
                    and the corresponding DataFrame.
        """
        for name, df in tables:
            self.con.register(name, df)

    def query(self, sql_query: str) -> pd.DataFrame:
        """
        Executes a SQL query against the database.

        Args:
            sql_query: The SQL query to execute.

        Returns:
            A pandas DataFrame containing the query results.
            
        Raises:
            NotImplementedError: If the query is a write operation (INSERT/UPDATE/DELETE).
        """
        # Classify the SQL statement
        classification = self.interceptor.classify(sql_query)
        
        if classification['needs_crud']:
            # Block write operations with informative error
            # (CRUD handlers will be implemented in Phase 3)
            raise NotImplementedError(
                f"{classification['type']} operations are not yet supported. "
                f"This feature requires write mode (--writable flag). "
                f"Write operations will be available in a future release."
            )
        
        # Execute SELECT/DDL queries in DuckDB (existing behavior)
        return self.con.execute(sql_query).fetchdf()

    def close(self):
        """Closes the database connection."""
        self.con.close() 