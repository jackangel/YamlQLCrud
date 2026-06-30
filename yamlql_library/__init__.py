from pathlib import Path
import pandas as pd
from .loader import YamlLoader
from .transformer import DataTransformer
from .database import Database

class YamlQL:
    """
    The main class for querying YAML files with SQL.
    """
    def __init__(self, file_path: str, max_depth: int = 5, strategy: str = "depth", mode: str = "r"):
        """
        Initializes the YamlQL instance.

        This loads the YAML file, transforms the data, and sets up the
        in-memory database.

        Args:
            file_path: The path to the YAML file.
            max_depth: Maximum nesting depth for transformation.
            strategy: Transformation strategy ("depth" or "adaptive").
            mode: File mode - "r" for read-only (default), "rw" or "w" for read-write.
        """
        # Validate mode parameter
        if mode not in ['r', 'rw', 'w']:
            raise ValueError(f"Invalid mode: {mode}. Must be 'r', 'rw', or 'w'")
        
        # Store initialization parameters for reload
        self.mode = mode
        self.max_depth = max_depth
        self.strategy = strategy
        self.file_path = str(Path(file_path).resolve())  # Store as absolute path
        
        path = Path(file_path)
        
        # 1. Load the data
        loader = YamlLoader(path)
        data = loader.load()
        
        # Store original data for CRUD operations (needed by handlers)
        self.original_data = data

        # 2. Transform the data
        transformer = DataTransformer(data, max_depth=max_depth, strategy=strategy)
        self.tables = transformer.transform()
        
        # Store column name mapping for reverse transformation (Phase 3)
        self.column_name_map = transformer.column_name_map
        
        # 3. Setup the database (pass mode and file_path for write operations)
        self._db = Database(mode=self.mode, file_path=self.file_path)
        self._db.create_tables(self.tables)
        
        # 4. Initialize CRUD handlers if in write mode
        if self.mode in ['rw', 'w']:
            self._db.initialize_crud_handlers(
                column_name_map=self.column_name_map,
                original_data=self.original_data
            )

    def query(self, sql_query: str):
        """
        Executes a SQL query against the loaded YAML data.

        Args:
            sql_query: The SQL query to run.

        Returns:
            A pandas DataFrame with the results (for SELECT queries).
            A dict with success status and message (for INSERT/UPDATE/DELETE queries).
        """
        result = self._db.query(sql_query)
        
        # If it was a successful write operation, reload from file to sync database
        if isinstance(result, dict) and result.get('success'):
            self._reload_from_file()
        
        return result

    def close(self):
        """Closes the database connection."""
        self._db.close()
    
    def _reload_from_file(self):
        """
        Internal method to reload the YAML file and resynchronize the database.
        Called automatically after successful write operations.
        """
        path = Path(self.file_path)
        
        # 1. Load the data from file
        loader = YamlLoader(path)
        data = loader.load()
        self.original_data = data

        # 2. Transform the data
        transformer = DataTransformer(data, max_depth=self.max_depth, strategy=self.strategy)
        self.tables = transformer.transform()
        self.column_name_map = transformer.column_name_map
        
        # 3. Recreate the database with fresh data
        self._db.close()
        self._db = Database(mode=self.mode, file_path=self.file_path)
        self._db.create_tables(self.tables)
        
        # 4. Reinitialize CRUD handlers if in write mode
        if self.mode in ['rw', 'w']:
            self._db.initialize_crud_handlers(
                column_name_map=self.column_name_map,
                original_data=self.original_data
            )

    def list_tables(self):
        """Lists the tables that were created from the YAML file."""
        return [name for name, _ in self.tables]
    
    @property
    def writable(self) -> bool:
        """
        Returns True if write operations are allowed.
        
        Returns:
            bool: True if mode is 'rw' or 'w', False if mode is 'r'.
        """
        return self.mode in ['rw', 'w'] 