import pandas as pd
import copy
from typing import Any, Dict, List, Tuple

class DataTransformer:
    """Transforms nested dictionary data into relational tables."""

    def __init__(self, data: Dict[str, Any], max_depth: int = 5, strategy: str = "depth"):
        """Initializes the DataTransformer with the data to be transformed."""
        self.data = data
        self.max_depth = max_depth
        self.strategy = strategy
        self.min_dict_size_for_table = 2
        # Bidirectional column name mapping: {table_name: {sanitized_col: original_col, ...}}
        self.column_name_map = {}
        self.column_name_reverse_map = {}  # {table_name: {original_col: sanitized_col, ...}}

    def _sanitize_column_name(self, original_name: str) -> str:
        """Sanitizes a column name by replacing special characters with underscores."""
        return original_name.replace(' ', '_').replace('.', '_').replace('-', '_')

    def _track_column_mapping(self, table_name: str, original_columns: List[str], sanitized_columns: List[str]):
        """Tracks the mapping between original and sanitized column names for a table."""
        if table_name not in self.column_name_map:
            self.column_name_map[table_name] = {}
            self.column_name_reverse_map[table_name] = {}
        
        for original, sanitized in zip(original_columns, sanitized_columns):
            if original != sanitized:  # Only track if there was an actual change
                self.column_name_map[table_name][sanitized] = original
                self.column_name_reverse_map[table_name][original] = sanitized

    def _sanitize_and_track_columns(self, table_name: str, columns: List[str]) -> List[str]:
        """Sanitizes column names and tracks the mapping."""
        original_columns = list(columns)
        sanitized_columns = [self._sanitize_column_name(col) for col in columns]
        self._track_column_mapping(table_name, original_columns, sanitized_columns)
        return sanitized_columns

    def get_column_map(self) -> Dict[str, Dict[str, str]]:
        """Returns the bidirectional column name mapping."""
        return self.column_name_map

    def get_reverse_column_map(self) -> Dict[str, Dict[str, str]]:
        """Returns the reverse column name mapping (original -> sanitized)."""
        return self.column_name_reverse_map

    def _find_and_extract_nested_lists(self, records: List[Dict], parent_table_name: str, parent_path: str = "root") -> Tuple[List[Dict], List[Tuple[str, pd.DataFrame]]]:
        """
        Finds lists of objects within a list of records, extracts them into new tables,
        and returns the original records with the extracted lists removed.
        """
        if not records:
            return records, []

        new_tables = []
        # Find all paths to nested lists of objects in the first record as a template
        paths_to_extract = []
        
        def find_paths(d, path=[]):
            for k, v in d.items():
                current_path = path + [k]
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    paths_to_extract.append(current_path)
                elif isinstance(v, dict):
                    find_paths(v, current_path)
        
        find_paths(records[0])

        # For each path, create a new table from the nested lists across all records
        for path in paths_to_extract:
            nested_table_name = f"{parent_table_name}_{'_'.join(path)}".replace('-', '_')
            
            # Extract parent metadata for joining (include _yaml_path if present)
            meta_cols = [k for k, v in records[0].items() if not isinstance(v, (dict, list))]
            if '_yaml_path' in records[0]:
                if '_yaml_path' not in meta_cols:
                    meta_cols.append('_yaml_path')
            
            child_df = pd.json_normalize(
                records,
                record_path=path,
                meta=meta_cols,
                sep='_',
                errors='ignore',
                meta_prefix=f"{parent_table_name}_"
            )
            original_columns = list(child_df.columns)
            child_df.columns = self._sanitize_and_track_columns(nested_table_name, original_columns)
            
            # Build child paths: parent_path.nested_path.index
            nested_path_str = '.'.join(path)
            parent_path_col = f"{parent_table_name}__yaml_path"
            if parent_path_col in child_df.columns:
                # Group by parent path and assign indices within each group
                child_paths = []
                for parent_path_val in child_df[parent_path_col]:
                    count = sum(1 for p in child_paths if p.startswith(f"{parent_path_val}.{nested_path_str}."))
                    child_paths.append(f"{parent_path_val}.{nested_path_str}.{count}")
                child_df['_yaml_path'] = child_paths
            else:
                # Fallback: use sequential indices
                child_df['_yaml_path'] = [f"{parent_path}.{nested_path_str}.{i}" for i in range(len(child_df))]
            
            new_tables.append((nested_table_name, child_df))

        # Create a deepcopy of the records to modify them by removing extracted lists
        records_copy = copy.deepcopy(records)
        for record in records_copy:
            for path in paths_to_extract:
                d = record
                for key in path[:-1]:
                    d = d.get(key, {})
                # Pop the list from the dictionary
                if path[-1] in d:
                    d.pop(path[-1])
                    
        return records_copy, new_tables

    def _stringify_scalar_lists(self, data: Any) -> Any:
        """Recursively traverses data to convert all lists of scalars into lists of strings."""
        if isinstance(data, dict):
            return {k: self._stringify_scalar_lists(v) for k, v in data.items()}
        if isinstance(data, list):
            is_scalar_list = all(not isinstance(item, (dict, list)) for item in data)
            if is_scalar_list:
                return [str(item) for item in data]
            else:
                return [self._stringify_scalar_lists(item) for item in data]
        return data

    def _normalize_records(self, table_name: str, records: List[Dict], current_path: str = "root", is_actual_list: bool = True) -> List[Tuple[str, pd.DataFrame]]:
        """
        Normalizes a list of records into a primary DataFrame and extracts nested
        lists of objects into their own separate tables.
        
        Args:
            table_name: Name for the resulting table
            records: List of dictionary records to normalize
            current_path: Current YAML path (e.g., "root.users")
            is_actual_list: True if records came from an actual YAML list, False if from a wrapped dict
        """
        if not records:
            return []

        # Ensure all scalar lists are stringified before any processing
        records = self._stringify_scalar_lists(records)
        
        # Add _yaml_path to each record before processing
        # For actual lists: Always include numeric index (e.g., "root.users.0", "root.users.1")
        # For wrapped dicts: Use current_path as-is (e.g., "root.metadata")
        for i, record in enumerate(records):
            if is_actual_list:
                # List items always get indexed, even single-item lists
                # This is required for UPDATE/DELETE operations to correctly locate list items
                record['_yaml_path'] = f"{current_path}.{i}"
            else:
                # Dict with scalars was wrapped in a list for processing
                # Use the path as-is without index
                record['_yaml_path'] = current_path
        
        # Extract nested lists of objects into their own tables first
        records_without_nested_lists, extracted_tables = self._find_and_extract_nested_lists(records, table_name, current_path)
        
        # Flatten the remaining records (which now contain only scalars, dicts, and scalar lists)
        parent_df = pd.json_normalize(records_without_nested_lists, sep='_')
        original_columns = list(parent_df.columns)
        parent_df.columns = self._sanitize_and_track_columns(table_name, original_columns)
        
        all_tables = []
        if not parent_df.empty:
            all_tables.append((table_name, parent_df))
        
        all_tables.extend(extracted_tables)
        return all_tables

    def _process_node(self, node_value: Any, table_name: str, tables_list: List, depth: int = 0, current_path: str = "root"):
        """Recursively processes a node to create tables with universal heuristics."""
        table_name = str(table_name).replace('-', '_')
        
        # Universal configuration - no domain-specific keywords
        MAX_DEPTH = 5  # Reasonable depth limit to handle nesting without going overboard
        MIN_DICT_SIZE_FOR_TABLE = 2  # Only create separate tables for dictionaries with 2+ keys
        
        # Check if we should stop recursing based on universal criteria
        should_stop_recursing = depth >= self.max_depth

        if isinstance(node_value, list):
            if not node_value:
                # Create empty table for empty lists to support INSERT operations
                df = pd.DataFrame({'_yaml_path': []})
                tables_list.append((table_name, df))
                return
            if all(isinstance(item, dict) for item in node_value):
                # Always use _normalize_records for lists of objects to get proper flattening
                tables_list.extend(self._normalize_records(table_name, node_value, current_path, is_actual_list=True))
            elif all(not isinstance(item, (dict, list)) for item in node_value):
                df = pd.DataFrame({
                    'value': [str(x) for x in node_value],
                    '_yaml_path': [f"{current_path}.{i}" for i in range(len(node_value))]
                })
                tables_list.append((table_name, df))
        
        elif isinstance(node_value, dict):
            scalar_data = {}
            nested_dicts = {}

            for key, value in node_value.items():
                if isinstance(value, dict):
                    # Only create separate tables for dictionaries that are large enough
                    # and we haven't hit the depth limit
                    if len(value) >= MIN_DICT_SIZE_FOR_TABLE and not should_stop_recursing:
                        nested_dicts[key] = value
                    # ALWAYS add a flattened version to preserve all data
                    for sub_key, sub_value in value.items():
                        if not isinstance(sub_value, (dict, list)):
                            scalar_data[f"{key}_{sub_key}"] = sub_value
                        elif isinstance(sub_value, list) and all(not isinstance(item, (dict, list)) for item in sub_value):
                            # Flatten scalar lists
                            scalar_data[f"{key}_{sub_key}"] = sub_value
                elif isinstance(value, list):
                    # Handle lists within dictionaries
                    self._process_node(value, f"{table_name}_{key}", tables_list, depth + 1, current_path=f"{current_path}.{key}")
                else:
                    scalar_data[key] = value

            if scalar_data:
                tables_list.extend(self._normalize_records(table_name, [scalar_data], current_path, is_actual_list=False))

            # Process nested dictionaries that qualified for their own tables
            for child_name, child_value in nested_dicts.items():
                new_table_name = f"{table_name}_{child_name}"
                self._process_node(child_value, new_table_name, tables_list, depth + 1, current_path=f"{current_path}.{child_name}")

    def transform(self) -> List[Tuple[str, pd.DataFrame]]:
        """Transforms the YAML data into a list of relational tables."""
        if self.strategy == "adaptive":
            return self._transform_adaptive()
        else:
            return self._transform_depth()

    def _transform_depth(self) -> List[Tuple[str, pd.DataFrame]]:
        """Transforms the data using the depth-based strategy."""
        all_tables = []
        data_copy = copy.deepcopy(self.data)

        # Handle root-level list first
        if isinstance(data_copy, list):
            self._process_node(data_copy, 'root', all_tables, depth=0, current_path="root")
            return all_tables

        # Add at the top of transform() to handle root scalars
        root_data = {k: v for k, v in self.data.items() if not isinstance(v, (dict, list))}
        if root_data:
            root_df = pd.json_normalize(root_data)
            original_columns = list(root_df.columns)
            root_df.columns = self._sanitize_and_track_columns('root', original_columns)
            root_df['_yaml_path'] = 'root'
            all_tables.append(('root', root_df))

        # Heuristic: If there is only one top-level key and its value is a dictionary
        # (e.g., a single document wrapper), step inside it for a more intuitive schema.
        top_level_keys = list(data_copy.keys())
        if len(top_level_keys) == 1 and isinstance(data_copy[top_level_keys[0]], dict):
            unwrapped_key = top_level_keys[0]
            source_data = data_copy[unwrapped_key]
            # CRITICAL: Preserve the unwrapped key in the path for correct _yaml_path generation
            base_path = f"root.{unwrapped_key}"
        else:
            source_data = data_copy
            base_path = "root"

        if isinstance(source_data, dict):
            # Check if this is a "record-like" dictionary (only scalar values)
            # If so, treat it as a single record for a table
            has_only_scalars = all(not isinstance(v, (dict, list)) for v in source_data.values())
            
            if has_only_scalars:
                # This is a single record - create a table with one row
                all_tables.extend(self._normalize_records('data', [source_data], base_path, is_actual_list=False))
            else:
                # This has nested structures - process each item separately
                for table_name, value in source_data.items():
                    self._process_node(value, table_name, all_tables, depth=0, current_path=f"{base_path}.{table_name}")
        elif isinstance(source_data, list):
            self._process_node(source_data, 'root', all_tables, depth=0, current_path="root")

        return all_tables 

    def _transform_adaptive(self) -> List[Tuple[str, pd.DataFrame]]:
        """Transforms the data using the adaptive strategy."""
        all_tables = []
        root_scalar_data = {}
        top_level_objects = {}

        for key, value in self.data.items():
            if not isinstance(value, (dict, list)):
                root_scalar_data[key] = value
            else:
                top_level_objects[key] = value
                
        if root_scalar_data:
            df = pd.DataFrame([root_scalar_data])
            original_columns = list(df.columns)
            df.columns = self._sanitize_and_track_columns("root", original_columns)
            df['_yaml_path'] = 'root'
            all_tables.append(("root", df))

        for table_name, node_value in top_level_objects.items():
            self._process_node_adaptive(node_value, table_name, all_tables, f"root.{table_name}")

        return all_tables
    
    def _process_node_adaptive(self, node_value: Any, table_name: str, tables_list: List, current_path: str = "root"):
        """Recursively processes a node using the adaptive strategy."""
        table_name = str(table_name).replace('-', '_')
        
        if isinstance(node_value, dict):
            scalar_data = {}
            # Separate scalars from dicts
            for key, value in node_value.items():
                if isinstance(value, dict):
                    self._process_node_adaptive(value, f"{table_name}_{key}", tables_list, f"{current_path}.{key}")
                elif isinstance(value, list):
                    # Process lists as separate tables
                    self._process_list_adaptive(value, f"{table_name}_{key}", tables_list, f"{current_path}.{key}")
                else:
                    scalar_data[key] = value
            
            if scalar_data:
                df = pd.DataFrame([scalar_data])
                original_columns = list(df.columns)
                df.columns = self._sanitize_and_track_columns(table_name, original_columns)
                df['_yaml_path'] = current_path
                tables_list.append((table_name, df))
        elif isinstance(node_value, list):
            self._process_list_adaptive(node_value, table_name, tables_list, current_path)

    def _process_list_adaptive(self, list_value: List, table_name: str, tables_list: List, current_path: str = "root"):
        """Processes a list using the adaptive strategy."""
        if not list_value:
            return
        
        # Check if it's a list of scalars or objects
        if all(not isinstance(item, (dict, list)) for item in list_value):
            df = pd.DataFrame({
                'value': [str(x) for x in list_value],
                '_yaml_path': [f"{current_path}.{i}" for i in range(len(list_value))]
            })
            tables_list.append((table_name, df))
        elif all(isinstance(item, dict) for item in list_value):
            # Normalize list of objects into a single table
            df = pd.json_normalize(list_value, sep='_')
            original_columns = list(df.columns)
            df.columns = self._sanitize_and_track_columns(table_name, original_columns)
            df['_yaml_path'] = [f"{current_path}.{i}" for i in range(len(df))]
            tables_list.append((table_name, df)) 