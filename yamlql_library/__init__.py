from pathlib import Path
from .loader import YamlLoader
from .transformer import DataTransformer
from .database import Database

class YamlQL:
    """
    The main class for querying YAML files with SQL.
    """
    def __init__(
        self,
        file_path: str,
        max_depth: int = 5,
        strategy: str = "depth",
        mode: str = "r",
        expose_mapping_columns: bool = False,
    ):
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
        self.expose_mapping_columns = expose_mapping_columns
        self.file_path = str(Path(file_path).resolve())  # Store as absolute path
        
        self._load_from_path(Path(file_path))
        self._db = self._create_database()

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
        previous_db = self._db
        try:
            self._load_from_path(Path(self.file_path))
            refreshed_db = self._create_database()
        except Exception:
            # YAML is authoritative after a completed write. Do not retain a
            # previous DuckDB projection whose document relations are stale.
            previous_db.close()
            self._db = Database(mode=self.mode, file_path=self.file_path)
            raise
        previous_db.close()
        self._db = refreshed_db

    def _load_from_path(self, path: Path) -> None:
        """Build the facade state from one parsed semantic document stream."""
        loader = YamlLoader(path)
        stream = loader.load_stream()
        if len(stream) == 1:
            data = stream[0][2]
        else:
            data = {}
            for _, kind, document in stream:
                if kind == "mapping":
                    data.update(document)

        self.original_data = data
        transformer = DataTransformer(
            data,
            max_depth=self.max_depth,
            strategy=self.strategy,
            expose_mapping_columns=self.expose_mapping_columns,
            stream=stream if self._has_document_collections(stream) else None,
        )
        self.tables = transformer.transform()
        self.column_name_map = transformer.column_name_map
        self.mapping_column_paths = transformer.mapping_column_paths
        self.table_doc_map = transformer.table_doc_map
        self._warnings = list(transformer.warnings)

    @staticmethod
    def _has_document_collections(stream) -> bool:
        """Return whether stream-aware relations add a queryable collection."""
        for _, kind, document in stream:
            if kind == "list":
                return True
            if kind == "mapping" and any(
                isinstance(value, list) for value in document.values()
            ):
                return True
        return False

    def _create_database(self) -> Database:
        """Create a fully initialized disposable DuckDB projection."""
        database = Database(mode=self.mode, file_path=self.file_path)
        database.mapping_column_paths = self.mapping_column_paths
        database.table_doc_map = self.table_doc_map
        try:
            database.create_tables(self.tables)
            if self.mode in ['rw', 'w']:
                database.initialize_crud_handlers(
                    column_name_map=self.column_name_map,
                    mapping_column_paths=self.mapping_column_paths,
                    original_data=self.original_data,
                    table_doc_map=self.table_doc_map,
                )
        except Exception:
            database.close()
            raise
        return database

    def list_tables(self):
        """Lists the tables that were created from the YAML file."""
        return [name for name, _ in self.tables]

    @property
    def warnings(self):
        """Return collision and derivation warnings without exposing internal state."""
        return list(self._warnings)
    
    @property
    def writable(self) -> bool:
        """
        Returns True if write operations are allowed.
        
        Returns:
            bool: True if mode is 'rw' or 'w', False if mode is 'r'.
        """
        return self.mode in ['rw', 'w'] 