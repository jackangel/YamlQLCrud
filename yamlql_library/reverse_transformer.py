"""
Reverse Transformer Module

Converts relational data (table rows) back into nested YAML structures.
This is the inverse operation of DataTransformer - it reconstructs nested
dictionaries and lists from flat DataFrame rows.
"""

from typing import Any, Dict, List, Optional, Tuple
import pandas as pd


class ReverseTransformer:
    """
    Transforms flat relational rows back into nested YAML structures.
    
    This class performs the inverse operation of DataTransformer:
    - Takes rows with sanitized column names and converts them back to original names
    - Uses _yaml_path metadata to reconstruct the correct nesting level
    - Rebuilds nested dictionaries and lists from flat structures
    """
    
    def __init__(self, column_name_map: dict, original_data: dict = None, mapping_column_paths: Optional[Dict[str, Dict[str, Tuple[str, ...]]]] = None):
        """
        Initialize the reverse transformer.
        
        Args:
            column_name_map: Bidirectional mapping from DataTransformer.
                            Format: {table_name: {sanitized_col: original_col, ...}}
            original_data: Optional - the original YAML structure for reference
        """
        self.column_name_map = column_name_map
        self.original_data = original_data or {}
        self.mapping_column_paths = mapping_column_paths or {}
    
    def row_to_yaml_path(self, table_name: str, row: dict) -> dict:
        """
        Converts a single row back to YAML path→value pairs.
        
        This method:
        1. Extracts the _yaml_path from the row to determine nesting level
        2. Converts each column name back to its original form using column_name_map
        3. Constructs full paths by combining _yaml_path with column paths
        
        Args:
            table_name: The table this row came from
            row: Dictionary of column_name → value (from DataFrame.to_dict('records'))
        
        Returns:
            Dictionary of yaml_path → value
            Example: {
                "metadata.labels.app": "myapp",
                "spec.replicas": 3
            }
        """
        path_value_pairs = {}
        
        # Get the base path from _yaml_path column (if present)
        base_path = row.get('_yaml_path', 'root')
        
        mapping_paths = self.mapping_column_paths.get(table_name, {})

        # Get column mapping for this table
        table_map = self.column_name_map.get(table_name, {})
        
        # Process each column in the row
        for col_name, value in row.items():
            # Skip metadata columns
            if col_name == '_yaml_path':
                continue
            
            # NOTE: We DO NOT skip null values - they should be written as YAML null
            # to preserve schema and allow SELECT to return null columns
            
            if col_name in mapping_paths:
                path_value_pairs['.'.join(mapping_paths[col_name])] = value
                continue

            # Look up original column name from the mapping
            # The mapping tells us the exact original key before sanitization
            if col_name in table_map:
                # Column was sanitized, use the original mapped value
                original_col = table_map[col_name]
            else:
                # Column wasn't sanitized (no special chars), use as-is
                original_col = col_name
            
            # The original_col now contains the proper nested path with dots
            # (e.g., "metadata.name" not "metadata_name")
            yaml_path = original_col
            
            # Store the path→value pair (including None/NaN values)
            path_value_pairs[yaml_path] = value
        
        return path_value_pairs
    
    def build_nested_dict(self, path_value_pairs: dict) -> dict:
        """
        Converts flat path→value pairs into nested dictionary.
        
        This method reconstructs nested structures by:
        1. Splitting each path into components (e.g., "a.b.c" → ["a", "b", "c"])
        2. Detecting list indices (numeric components like "items.0.name")
        3. Building the nested structure bottom-up
        4. Handling non-contiguous list indices by filling gaps with None
        
        Args:
            path_value_pairs: Dictionary like {"a.b.c": 1, "a.b.d": 2}
        
        Returns:
            Nested dictionary like {"a": {"b": {"c": 1, "d": 2}}}
        
        Examples:
            >>> rt = ReverseTransformer({})
            >>> rt.build_nested_dict({"metadata.name": "app", "metadata.labels.tier": "frontend"})
            {"metadata": {"name": "app", "labels": {"tier": "frontend"}}}
            
            >>> rt.build_nested_dict({"items.0.name": "first", "items.1.name": "second"})
            {"items": [{"name": "first"}, {"name": "second"}]}
        """
        if not path_value_pairs:
            return {}
        
        result = {}
        
        # Process each path→value pair
        for path, value in path_value_pairs.items():
            # Split path into components
            components = path.split('.')
            
            # Navigate/create nested structure
            current = result
            
            for i, component in enumerate(components[:-1]):
                # Check if this component is a list index (numeric)
                if self._is_numeric(component):
                    # Parent should be a list
                    # This shouldn't happen for well-formed paths, but handle gracefully
                    index = int(component)
                    
                    # Ensure current is a list
                    if not isinstance(current, list):
                        # Convert to list if needed (shouldn't happen in normal flow)
                        current = []
                    
                    # Extend list if necessary
                    while len(current) <= index:
                        current.append({})
                    
                    current = current[index]
                else:
                    # Regular dict key
                    # Check if next component is numeric (indicates this should be a list)
                    next_component = components[i + 1] if i + 1 < len(components) else None
                    
                    if next_component and self._is_numeric(next_component):
                        # This key should point to a list
                        if component not in current:
                            current[component] = []
                        current = current[component]
                    else:
                        # This key should point to a dict
                        if component not in current:
                            current[component] = {}
                        current = current[component]
            
            # Set the final value
            last_component = components[-1]
            
            if self._is_numeric(last_component):
                # Setting a list element
                index = int(last_component)
                if not isinstance(current, list):
                    # Shouldn't happen, but handle gracefully
                    current = []
                
                while len(current) <= index:
                    current.append(None)
                
                current[index] = self._preserve_type(value)
            else:
                # Setting a dict key
                if isinstance(current, dict):
                    current[last_component] = self._preserve_type(value)
        
        return result
    
    def _is_numeric(self, s: str) -> bool:
        """
        Check if a string represents a numeric list index.
        
        Args:
            s: String to check
        
        Returns:
            True if the string is a valid non-negative integer
        """
        try:
            num = int(s)
            return num >= 0
        except (ValueError, TypeError):
            return False
    
    def _preserve_type(self, value: Any) -> Any:
        """
        Preserve the original type of a value from DataFrame.
        
        Args:
            value: Value from DataFrame (may be numpy types, pandas types, etc.)
        
        Returns:
            Value converted to standard Python types (int, float, bool, str, list)
        """
        # Handle pandas/numpy types
        if pd.isna(value):
            return None
        
        # Check for numpy/pandas integer types
        if hasattr(value, 'item'):  # numpy/pandas scalar
            return value.item()
        
        # Handle string representations of lists (from stringified scalar lists)
        if isinstance(value, str) and value.startswith('[') and value.endswith(']'):
            # This was a stringified list - keep it as string for now
            # Could be enhanced to parse back to list if needed
            return value
        
        # Handle boolean explicitly (before int, since bool is subclass of int)
        if isinstance(value, bool):
            return value
        
        # Return as-is for standard Python types
        return value
    
    def rows_to_yaml_structure(self, table_name: str, rows: List[dict]) -> Any:
        """
        Converts multiple rows back to a YAML structure.
        
        This is a convenience method that:
        1. Converts each row to path→value pairs
        2. Merges all pairs
        3. Builds the final nested structure
        
        Args:
            table_name: The table these rows came from
            rows: List of row dictionaries (from DataFrame.to_dict('records'))
        
        Returns:
            Reconstructed YAML structure (dict or list)
        """
        if not rows:
            return {}
        
        # For a single row, process directly
        if len(rows) == 1:
            paths = self.row_to_yaml_path(table_name, rows[0])
            return self.build_nested_dict(paths)
        
        # For multiple rows, they likely represent a list
        # Check if _yaml_path indicates list structure (e.g., "root.0", "root.1")
        yaml_paths = [row.get('_yaml_path', '') for row in rows]
        
        # If all paths are like "root.0", "root.1", this is a list at root level
        if all(path.startswith('root.') and self._is_numeric(path.split('.')[-1]) for path in yaml_paths):
            # Reconstruct as list
            result = []
            for row in rows:
                paths = self.row_to_yaml_path(table_name, row)
                # Remove the root.N prefix from paths
                adjusted_paths = {}
                for path, value in paths.items():
                    # Paths are relative to this item in the list
                    adjusted_paths[path] = value
                item = self.build_nested_dict(adjusted_paths)
                result.append(item)
            return result
        else:
            # Otherwise, merge all rows into a single dict
            all_paths = {}
            for row in rows:
                paths = self.row_to_yaml_path(table_name, row)
                all_paths.update(paths)
            return self.build_nested_dict(all_paths)
