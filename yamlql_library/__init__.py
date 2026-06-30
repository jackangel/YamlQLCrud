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
        
        # Store mode and file path for write operations
        self.mode = mode
        self.file_path = str(Path(file_path).resolve())  # Store as absolute path
        
        path = Path(file_path)
        
        # 1. Load the data
        loader = YamlLoader(path)
        data = loader.load()

        # 2. Transform the data
        transformer = DataTransformer(data, max_depth=max_depth, strategy=strategy)
        self.tables = transformer.transform()
        
        # Store column name mapping for reverse transformation (Phase 3)
        self.column_name_map = transformer.column_name_map
        
        # 3. Setup the database (pass mode and file_path for write operations)
        self._db = Database(mode=self.mode, file_path=self.file_path)
        self._db.create_tables(self.tables)

    def query(self, sql_query: str) -> pd.DataFrame:
        """
        Executes a SQL query against the loaded YAML data.

        Args:
            sql_query: The SQL query to run.

        Returns:
            A pandas DataFrame with the results.
        """
        return self._db.query(sql_query)

    def close(self):
        """Closes the database connection."""
        self._db.close()

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