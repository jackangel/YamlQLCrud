"""SQL Statement Interceptor/Classifier

This module provides SQL statement classification to route queries appropriately:
- SELECT queries → DuckDB execution (read operations)
- INSERT/UPDATE/DELETE → CRUD handlers (write operations with YAML persistence)
- DDL statements → DuckDB execution (schema operations)
"""

from typing import Dict, Any, Optional
import sqlglot
from sqlglot import parse_one, ParseError
from sqlglot.expressions import (
    Select, Insert, Update, Delete, 
    Create, Drop, Alter, TruncateTable,
    Expression
)


class SqlInterceptor:
    """Intercepts and classifies SQL statements for routing decisions.
    
    This class parses SQL statements and determines:
    1. Statement type (SELECT, INSERT, UPDATE, DELETE, DDL, UNKNOWN)
    2. Target table name (for DML operations)
    3. Whether the statement requires CRUD handling (write operations)
    
    Uses sqlglot for robust SQL parsing with DuckDB dialect support.
    """
    
    def __init__(self, dialect: str = 'duckdb'):
        """Initialize the SQL interceptor.
        
        Args:
            dialect: SQL dialect to use for parsing (default: 'duckdb')
        """
        self.dialect = dialect
    
    def classify(self, sql_query: str) -> Dict[str, Any]:
        """Classifies a SQL statement and extracts key components.
        
        Args:
            sql_query: SQL statement to classify (single statement)
            
        Returns:
            Dictionary with keys:
            - 'type': Statement type ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'DDL', 'UNKNOWN')
            - 'table': Target table name (str or None if not applicable)
            - 'parsed': Parsed sqlglot Expression object (or None if parse failed)
            - 'needs_crud': Boolean indicating if CRUD handler should process this
            
        Raises:
            ValueError: If SQL is empty or contains multiple statements
            
        Examples:
            >>> interceptor = SqlInterceptor()
            >>> result = interceptor.classify("SELECT * FROM users")
            >>> result['type']
            'SELECT'
            >>> result['needs_crud']
            False
            
            >>> result = interceptor.classify("INSERT INTO users (name) VALUES ('Alice')")
            >>> result['type']
            'INSERT'
            >>> result['table']
            'users'
            >>> result['needs_crud']
            True
        """
        # Validate input
        sql_query = sql_query.strip()
        if not sql_query:
            raise ValueError("SQL query cannot be empty")
        
        # Check for multiple statements (semicolon-separated)
        # Note: This is a simple check; sqlglot.parse() can handle multiple statements
        # but for classification we want single statements
        if sql_query.count(';') > 1 or (sql_query.count(';') == 1 and not sql_query.endswith(';')):
            raise ValueError(
                "Multi-statement SQL not supported in classify(). "
                "Split statements and classify individually."
            )
        
        # Remove trailing semicolon if present
        sql_query = sql_query.rstrip(';').strip()
        
        # Parse SQL
        try:
            parsed = parse_one(sql_query, dialect=self.dialect)
        except ParseError as e:
            # Parse failed - return UNKNOWN
            return {
                'type': 'UNKNOWN',
                'table': None,
                'parsed': None,
                'needs_crud': False,
                'error': f"Parse error: {str(e)}"
            }
        except Exception as e:
            # Unexpected error
            return {
                'type': 'UNKNOWN',
                'table': None,
                'parsed': None,
                'needs_crud': False,
                'error': f"Unexpected error during parsing: {str(e)}"
            }
        
        # Classify based on expression type
        return self._classify_expression(parsed)
    
    def _classify_expression(self, parsed: Expression) -> Dict[str, Any]:
        """Classify a parsed SQL expression.
        
        Args:
            parsed: sqlglot Expression object
            
        Returns:
            Classification dictionary
        """
        # SELECT queries - read operations
        if isinstance(parsed, Select):
            return {
                'type': 'SELECT',
                'table': self._extract_primary_table(parsed),
                'parsed': parsed,
                'needs_crud': False
            }
        
        # INSERT queries - write operations
        elif isinstance(parsed, Insert):
            return {
                'type': 'INSERT',
                'table': self._extract_table_name(parsed),
                'parsed': parsed,
                'needs_crud': True
            }
        
        # UPDATE queries - write operations
        elif isinstance(parsed, Update):
            return {
                'type': 'UPDATE',
                'table': self._extract_table_name(parsed),
                'parsed': parsed,
                'needs_crud': True
            }
        
        # DELETE queries - write operations
        elif isinstance(parsed, Delete):
            return {
                'type': 'DELETE',
                'table': self._extract_table_name(parsed),
                'parsed': parsed,
                'needs_crud': True
            }
        
        # DDL statements (CREATE, DROP, ALTER, TRUNCATE) - schema operations
        elif isinstance(parsed, (Create, Drop, Alter, TruncateTable)):
            return {
                'type': 'DDL',
                'table': self._extract_table_name(parsed),
                'parsed': parsed,
                'needs_crud': False  # DDL goes to DuckDB for temp tables
            }
        
        # Unknown/unsupported statement type
        else:
            return {
                'type': 'UNKNOWN',
                'table': None,
                'parsed': parsed,
                'needs_crud': False,
                'error': f"Unsupported statement type: {type(parsed).__name__}"
            }
    
    def _extract_table_name(self, parsed: Expression) -> Optional[str]:
        """Extract table name from DML/DDL statement.
        
        Args:
            parsed: sqlglot Expression object
            
        Returns:
            Table name as string, or None if not found
        """
        try:
            # For INSERT, UPDATE, DELETE, the table is typically in the 'this' attribute
            if hasattr(parsed, 'this') and parsed.this:
                # Get the table expression
                table_expr = parsed.this
                
                # If it's a table reference, get its name
                if hasattr(table_expr, 'name'):
                    return table_expr.name
                
                # Try to get string representation and extract table name
                table_str = str(table_expr)
                # Remove quotes if present
                table_str = table_str.strip('"').strip("'").strip('`')
                return table_str if table_str else None
            
            return None
        except Exception:
            return None
    
    def _extract_primary_table(self, parsed: Select) -> Optional[str]:
        """Extract primary table name from SELECT statement.
        
        For SELECT statements, extracts the first/primary table from the FROM clause.
        
        Args:
            parsed: sqlglot Select expression
            
        Returns:
            Primary table name, or None if not found
        """
        try:
            # Get FROM clause
            from_clause = parsed.args.get('from')
            if not from_clause:
                return None
            
            # Get the table expression
            table_expr = from_clause.this if hasattr(from_clause, 'this') else from_clause
            
            # Extract table name
            if hasattr(table_expr, 'name'):
                return table_expr.name
            
            # Fallback: string representation
            table_str = str(table_expr)
            table_str = table_str.strip('"').strip("'").strip('`')
            return table_str if table_str else None
            
        except Exception:
            return None
