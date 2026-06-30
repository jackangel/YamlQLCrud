"""
Format-preserving YAML writer for YamlQL.

This module provides a YamlWriter class that can modify YAML files while
preserving formatting, comments, indentation, and overall structure.
Uses ruamel.yaml for round-trip serialization.
"""

from pathlib import Path
from typing import Any, List, Union
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


class YamlWriter:
    """
    Format-preserving YAML file writer.
    
    Allows modification of YAML files while maintaining:
    - Comments (inline and block)
    - Indentation and spacing
    - Quote styles
    - Key ordering
    - Multi-document structure
    
    Example:
        >>> writer = YamlWriter("config.yaml")
        >>> writer.load()
        >>> writer.set_value("metadata.name", "newapp")
        >>> writer.insert_value("spec.replicas", 3)
        >>> writer.delete_value("metadata.labels.old-label")
        >>> writer.write()
    """
    
    def __init__(self, file_path: Union[str, Path]):
        """
        Initialize the YAML writer.
        
        Args:
            file_path: Path to the YAML file to modify
            
        Raises:
            FileNotFoundError: If the file does not exist
        """
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"YAML file not found: {self.file_path}")
        
        # Configure ruamel.yaml for format preservation
        self.yaml = YAML()
        self.yaml.preserve_quotes = True
        self.yaml.default_flow_style = False
        self.yaml.indent(mapping=2, sequence=2, offset=0)
        
        self.data = None
        self._is_loaded = False
    
    def load(self) -> Any:
        """
        Load the YAML file for modification.
        
        Returns:
            The loaded YAML data structure (dict or list)
            
        Raises:
            IOError: If the file cannot be read
        """
        with open(self.file_path, 'r', encoding='utf-8') as f:
            self.data = self.yaml.load(f)
        
        self._is_loaded = True
        return self.data
    
    def set_value(self, path: str, value: Any) -> None:
        """
        Set a value at a specific YAML path.
        
        Updates an existing value. Use insert_value() to create new keys.
        
        Args:
            path: Dot-notation path like "metadata.labels.app"
            value: The value to set
            
        Raises:
            ValueError: If the file hasn't been loaded
            KeyError: If the path doesn't exist
            
        Example:
            >>> writer.set_value("spec.replicas", 5)
            >>> writer.set_value("items.0.name", "newname")  # List index
        """
        if not self._is_loaded:
            raise ValueError("Call load() before modifying data")
        
        parts = self._parse_path(path)
        target = self._navigate_to_parent(parts[:-1])
        final_key = parts[-1]
        
        # Handle list index
        if isinstance(target, list):
            index = self._parse_index(final_key)
            if index >= len(target):
                raise KeyError(f"List index {index} out of range at path: {path}")
            target[index] = value
        else:
            # Handle dict key
            if final_key not in target:
                raise KeyError(f"Key '{final_key}' does not exist at path: {path}")
            target[final_key] = value
    
    def insert_value(self, path: str, value: Any) -> None:
        """
        Insert a new value, creating parent structure if needed.
        
        Creates intermediate dictionaries as needed. Does not overwrite
        existing values - use set_value() for that.
        
        Args:
            path: Dot-notation path like "metadata.annotations.owner"
            value: The value to insert
            
        Raises:
            ValueError: If the file hasn't been loaded or if path exists
            TypeError: If attempting to traverse through non-dict/list values
            
        Example:
            >>> writer.insert_value("metadata.labels.new-label", "value")
            >>> writer.insert_value("items.0.metadata.id", "123")
        """
        if not self._is_loaded:
            raise ValueError("Call load() before modifying data")
        
        parts = self._parse_path(path)
        
        # Navigate/create path to parent
        current = self.data
        for i, part in enumerate(parts[:-1]):
            if isinstance(current, list):
                # Handle list traversal
                index = self._parse_index(part)
                if index >= len(current):
                    raise ValueError(f"List index {index} out of range at: {'.'.join(parts[:i+1])}")
                current = current[index]
            elif isinstance(current, dict):
                # Create intermediate dict if needed
                if part not in current:
                    current[part] = CommentedMap()
                current = current[part]
            else:
                raise TypeError(f"Cannot traverse through non-dict/list value at: {'.'.join(parts[:i+1])}")
        
        # Insert the final value
        final_key = parts[-1]
        if isinstance(current, list):
            index = self._parse_index(final_key)
            if index > len(current):
                raise ValueError(f"List index {index} out of range for insertion")
            current.insert(index, value)
        elif isinstance(current, dict):
            if final_key in current:
                raise ValueError(f"Key '{final_key}' already exists at path: {path}. Use set_value() to update.")
            current[final_key] = value
        else:
            raise TypeError(f"Cannot insert into non-dict/list value at: {'.'.join(parts[:-1])}")
    
    def delete_value(self, path: str) -> None:
        """
        Delete a value at a specific path.
        
        Args:
            path: Dot-notation path to delete
            
        Raises:
            ValueError: If the file hasn't been loaded
            KeyError: If the path doesn't exist
            
        Example:
            >>> writer.delete_value("metadata.labels.deprecated")
            >>> writer.delete_value("items.2")  # Delete list item
        """
        if not self._is_loaded:
            raise ValueError("Call load() before modifying data")
        
        parts = self._parse_path(path)
        target = self._navigate_to_parent(parts[:-1])
        final_key = parts[-1]
        
        # Handle deletion
        if isinstance(target, list):
            index = self._parse_index(final_key)
            if index >= len(target):
                raise KeyError(f"List index {index} out of range at path: {path}")
            del target[index]
        elif isinstance(target, dict):
            if final_key not in target:
                raise KeyError(f"Key '{final_key}' does not exist at path: {path}")
            del target[final_key]
        else:
            raise TypeError(f"Cannot delete from non-dict/list value")
    
    def write(self) -> None:
        """
        Write the modified data back to the file.
        
        Preserves comments, formatting, and indentation.
        
        Raises:
            ValueError: If the file hasn't been loaded
            IOError: If the file cannot be written
        """
        if not self._is_loaded:
            raise ValueError("Call load() before writing data")
        
        with open(self.file_path, 'w', encoding='utf-8') as f:
            self.yaml.dump(self.data, f)
    
    def _parse_path(self, path: str) -> List[str]:
        """
        Parse a dot-notation path into parts.
        
        Args:
            path: Dot-notation path like "metadata.labels.app"
            
        Returns:
            List of path parts
            
        Raises:
            ValueError: If path is empty or malformed
            
        Example:
            >>> self._parse_path("metadata.labels.app")
            ['metadata', 'labels', 'app']
        """
        if not path:
            raise ValueError("Path cannot be empty")
        
        # Validate path doesn't have consecutive dots or leading/trailing dots
        if '..' in path:
            raise ValueError(f"Invalid path: consecutive dots not allowed: {path}")
        if path.startswith('.') or path.endswith('.'):
            raise ValueError(f"Invalid path: cannot start or end with dot: {path}")
        
        parts = path.split('.')
        
        # Validate each part is non-empty (redundant with consecutive dots check, but explicit)
        if any(not part for part in parts):
            raise ValueError(f"Invalid path: empty component found: {path}")
        
        return parts
    
    def _parse_index(self, key: str) -> int:
        """
        Parse a key as a list index.
        
        Args:
            key: String that should be a numeric index
            
        Returns:
            Integer index
            
        Raises:
            ValueError: If key is not a valid integer
        """
        try:
            return int(key)
        except ValueError:
            raise ValueError(f"Expected numeric list index, got: {key}")
    
    def _navigate_to_parent(self, parts: List[str]) -> Union[dict, list]:
        """
        Navigate to the parent container for a path.
        
        Args:
            parts: Path parts (excluding the final key)
            
        Returns:
            The parent container (dict or list)
            
        Raises:
            KeyError: If path doesn't exist
            TypeError: If path traverses through non-dict/list values
        """
        current = self.data
        
        for i, part in enumerate(parts):
            if isinstance(current, list):
                index = self._parse_index(part)
                if index >= len(current):
                    raise KeyError(f"List index {index} out of range at: {'.'.join(parts[:i+1])}")
                current = current[index]
            elif isinstance(current, dict):
                if part not in current:
                    raise KeyError(f"Key '{part}' does not exist at: {'.'.join(parts[:i+1])}")
                current = current[part]
            else:
                raise TypeError(f"Cannot traverse through non-dict/list value at: {'.'.join(parts[:i])}")
        
        return current
    
    def get_value(self, path: str) -> Any:
        """
        Get a value at a specific path (read-only helper).
        
        Args:
            path: Dot-notation path
            
        Returns:
            The value at the path
            
        Raises:
            ValueError: If the file hasn't been loaded
            KeyError: If the path doesn't exist
        """
        if not self._is_loaded:
            raise ValueError("Call load() before reading data")
        
        parts = self._parse_path(path)
        current = self.data
        
        for i, part in enumerate(parts):
            if isinstance(current, list):
                index = self._parse_index(part)
                if index >= len(current):
                    raise KeyError(f"List index {index} out of range at: {'.'.join(parts[:i+1])}")
                current = current[index]
            elif isinstance(current, dict):
                if part not in current:
                    raise KeyError(f"Key '{part}' does not exist at: {'.'.join(parts[:i+1])}")
                current = current[part]
            else:
                raise TypeError(f"Cannot traverse through non-dict/list value at: {'.'.join(parts[:i])}")
        
        return current
