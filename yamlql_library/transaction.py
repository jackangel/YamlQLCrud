"""
Transaction manager for atomic YAML file writes.

This module provides a TransactionManager class that ensures atomic write operations
to YAML files. Changes are written to a temporary file first, then atomically
replaced, ensuring the original file is never corrupted even if the process crashes.

Key features:
- Atomic write operations (temp file + atomic rename)
- Backup and rollback capability
- Transaction state management
- Context manager support for automatic commit/rollback
"""

import os
import shutil
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from .writer import YamlWriter


class TransactionState(Enum):
    """Transaction lifecycle states."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


class TransactionManager:
    """
    Manages atomic write transactions for YAML files.
    
    Ensures data integrity by:
    1. Creating a backup of the original file on begin()
    2. Tracking all modifications via YamlWriter
    3. Writing to a temporary file on commit()
    4. Atomically replacing the original file
    5. Rolling back from backup if errors occur
    
    Example (explicit transaction):
        >>> from yamlql_library.transaction import TransactionManager
        >>> 
        >>> txn = TransactionManager("config.yaml")
        >>> txn.begin()
        >>> writer = txn.get_writer()
        >>> writer.load()
        >>> writer.set_value("spec.replicas", 5)
        >>> txn.commit()  # Atomic write
    
    Example (context manager - recommended):
        >>> with TransactionManager("config.yaml") as txn:
        ...     writer = txn.get_writer()
        ...     writer.load()
        ...     writer.set_value("spec.replicas", 5)
        ...     # Auto-commit on success, auto-rollback on exception
    """
    
    def __init__(self, file_path: str):
        """
        Initialize transaction manager.
        
        Args:
            file_path: Path to the YAML file to manage
            
        Raises:
            FileNotFoundError: If the file does not exist
        """
        self.file_path = Path(file_path)
        
        if not self.file_path.exists():
            raise FileNotFoundError(f"YAML file not found: {self.file_path}")
        
        self.backup_path = self.file_path.with_suffix(self.file_path.suffix + '.backup')
        self.temp_path = None
        self.writer = None
        self.state = TransactionState.NOT_STARTED
        self._pending_changes = {}
    
    def begin(self) -> None:
        """
        Start a transaction.
        
        Creates a backup of the original file and initializes the YamlWriter
        for tracking changes.
        
        Raises:
            RuntimeError: If transaction is already in progress
            IOError: If backup creation fails
        """
        if self.state == TransactionState.IN_PROGRESS:
            raise RuntimeError("Transaction already in progress")
        
        if self.state in (TransactionState.COMMITTED, TransactionState.ROLLED_BACK):
            raise RuntimeError(f"Cannot begin transaction in state: {self.state.value}")
        
        try:
            # Create backup with metadata preservation
            shutil.copy2(str(self.file_path), str(self.backup_path))
            
            # Initialize writer on original file (changes not yet persisted)
            self.writer = YamlWriter(str(self.file_path))
            
            self.state = TransactionState.IN_PROGRESS
            
        except Exception as e:
            # Clean up backup if it was created
            if self.backup_path.exists():
                try:
                    self.backup_path.unlink()
                except Exception:
                    pass
            raise IOError(f"Failed to begin transaction: {e}")
    
    def get_writer(self) -> YamlWriter:
        """
        Get the YamlWriter instance for this transaction.
        
        Returns:
            YamlWriter instance for making modifications
            
        Raises:
            RuntimeError: If transaction has not been started
        """
        if self.state != TransactionState.IN_PROGRESS:
            raise RuntimeError(f"Cannot get writer in state: {self.state.value}. Call begin() first.")
        
        if self.writer is None:
            raise RuntimeError("Writer not initialized")
        
        return self.writer
    
    def commit(self) -> None:
        """
        Commit the transaction.
        
        Writes changes to a temporary file, validates the YAML, then atomically
        replaces the original file. The backup is cleaned up on success.
        
        The atomic write sequence:
        1. Write changes to temp file (config.yaml.tmp)
        2. Validate temp file is valid YAML
        3. Atomically rename temp file to original (os.replace)
        4. Clean up backup file
        
        Raises:
            RuntimeError: If transaction is not in progress
            IOError: If write or validation fails
        """
        if self.state != TransactionState.IN_PROGRESS:
            raise RuntimeError(f"Cannot commit in state: {self.state.value}")
        
        if self.writer is None:
            raise RuntimeError("Writer not initialized")
        
        temp_file = None
        
        try:
            # Create a temporary file in the same directory as the target
            # This ensures atomic rename will work (same filesystem)
            fd, temp_path = tempfile.mkstemp(
                suffix='.tmp',
                prefix=self.file_path.stem + '.',
                dir=self.file_path.parent,
                text=True
            )
            os.close(fd)  # Close the file descriptor, we'll use the path
            self.temp_path = Path(temp_path)
            
            # Write changes to temp file via YamlWriter
            temp_writer = YamlWriter(str(self.temp_path))
            temp_writer.data = self.writer.data
            temp_writer._is_loaded = True
            temp_writer.write()
            
            # Validate temp file is valid YAML by attempting to load it
            self._validate_yaml(self.temp_path)
            
            # Atomic replace: os.replace works on both POSIX and Windows
            # On Windows, this requires the destination to not be open by another process
            os.replace(str(self.temp_path), str(self.file_path))
            
            # Clean up backup after successful commit
            if self.backup_path.exists():
                self.backup_path.unlink()
            
            self.state = TransactionState.COMMITTED
            self.temp_path = None
            
        except Exception as e:
            # Clean up temp file if it exists
            if self.temp_path and self.temp_path.exists():
                try:
                    self.temp_path.unlink()
                except Exception:
                    pass
            
            raise IOError(f"Failed to commit transaction: {e}")
    
    def rollback(self) -> None:
        """
        Rollback the transaction.
        
        Discards all changes and restores the original file from backup.
        
        Raises:
            RuntimeError: If transaction is not in progress
            IOError: If restore from backup fails
        """
        if self.state != TransactionState.IN_PROGRESS:
            raise RuntimeError(f"Cannot rollback in state: {self.state.value}")
        
        try:
            # Restore from backup
            if self.backup_path.exists():
                shutil.copy2(str(self.backup_path), str(self.file_path))
                self.backup_path.unlink()
            
            # Clean up temp file if it exists
            if self.temp_path and self.temp_path.exists():
                self.temp_path.unlink()
            
            self.state = TransactionState.ROLLED_BACK
            self.writer = None
            self._pending_changes = {}
            
        except Exception as e:
            raise IOError(f"Failed to rollback transaction: {e}")
    
    def get_pending_changes(self) -> dict:
        """
        Get a summary of pending changes (not yet committed).
        
        Returns:
            Dictionary with transaction metadata and state
            
        Note:
            This provides transaction metadata. To inspect actual YAML data,
            access writer.data directly after calling writer.load().
        """
        return {
            'state': self.state.value,
            'file_path': str(self.file_path),
            'has_backup': self.backup_path.exists() if self.backup_path else False,
            'has_writer': self.writer is not None,
            'writer_loaded': self.writer._is_loaded if self.writer else False,
        }
    
    def _validate_yaml(self, file_path: Path) -> None:
        """
        Validate that a file contains valid YAML.
        
        Args:
            file_path: Path to the file to validate
            
        Raises:
            ValueError: If the file is not valid YAML
        """
        from ruamel.yaml import YAML
        
        try:
            yaml = YAML()
            with open(file_path, 'r', encoding='utf-8') as f:
                yaml.load(f)
        except Exception as e:
            raise ValueError(f"Invalid YAML in temp file: {e}")
    
    def __enter__(self):
        """
        Context manager entry.
        
        Automatically starts the transaction.
        
        Returns:
            Self for use in with statement
        """
        self.begin()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Context manager exit.
        
        Automatically commits if no exception occurred, otherwise rolls back.
        
        Args:
            exc_type: Exception type (None if no exception)
            exc_val: Exception value
            exc_tb: Exception traceback
            
        Returns:
            False to propagate exceptions
        """
        if exc_type is None:
            # No exception, commit the transaction
            try:
                self.commit()
            except Exception as e:
                # If commit fails, attempt rollback
                try:
                    self.rollback()
                except Exception as rollback_error:
                    # Log rollback failure (in production, use proper logging)
                    import warnings
                    warnings.warn(f"Rollback failed after commit error: {rollback_error}")
                raise e
        else:
            # Exception occurred, rollback
            try:
                self.rollback()
            except Exception as rollback_error:
                # Log rollback failure but don't mask original exception
                import warnings
                warnings.warn(f"Rollback failed after exception: {rollback_error}")
        
        # Return False to propagate the original exception
        return False
