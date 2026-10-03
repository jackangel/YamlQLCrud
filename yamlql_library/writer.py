"""
Format-preserving YAML writer for YamlQL.

This module provides a YAML round-trip writer with detected physical-format
settings for a single YAML document. Uses ruamel.yaml for serialization.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import re
from typing import Any, List, Optional, Set, Tuple, Union
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq, TaggedScalar
from ruamel.yaml.events import AliasEvent, DocumentEndEvent, DocumentStartEvent
from ruamel.yaml.scalarstring import (
    DoubleQuotedScalarString,
    FoldedScalarString,
    LiteralScalarString,
    PlainScalarString,
    SingleQuotedScalarString,
)
from .ruamel_compat import check_ruamel_compatibility


class YamlWriterPolicyError(ValueError):
    """Base class for explicit YAML writer safety-policy violations."""


class AnchorInUseError(YamlWriterPolicyError):
    """Raised when deletion would leave an anchor's aliases dangling."""


class MergedKeyError(YamlWriterPolicyError):
    """Raised when deletion targets a key inherited through ``<<``."""


class NodeKindChangeError(YamlWriterPolicyError):
    """Raised when an edit would replace a node with a different kind."""


@dataclass
class FormatProfile:
    """Physical formatting detected from a YAML source file."""

    mapping_indent: int = 2
    sequence_indent: int = 2
    sequence_offset: int = 0
    line_ending: str = "\n"
    trailing_newline_suffix: str = ""
    has_bom: bool = False
    explicit_start: bool = False


class YamlWriter:
    """
    YAML document-stream writer with detected source formatting.
    
    Allows modification of YAML files while maintaining:
    - Comments (inline and block)
    - Quote styles
    - Key ordering
    - Detected indentation, line endings, UTF-8 BOM, and trailing newlines
    - Multi-document streams, including untouched document source bytes

    On mixed-format files, detection chooses the dominant style. Exact source
    bytes are retained when data has not been modified through this writer.
    Paths without ``doc=`` resolve to the last mapping document defining their
    first segment; new top-level keys use the last mapping document. Documents
    with non-mapping roots are preserved but cannot be addressed by SQL paths.

    Dirty documents are rendered independently and spliced into the original
    stream, so an edit never changes bytes owned by another document. This is
    used instead of ``dump_all`` because ruamel normalizes directives and
    inter-document comments during an otherwise no-op stream round trip.
    
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
        check_ruamel_compatibility()

        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"YAML file not found: {self.file_path}")
        
        # Configure ruamel.yaml for format preservation
        self.yaml = YAML()
        self.yaml.preserve_quotes = True
        self.yaml.default_flow_style = False
        self.yaml.width = 2 ** 31 - 1
        self._apply_format_profile(FormatProfile())
        
        self._documents: List[Any] = []
        self._document_count = 0
        self._tree_is_stale = False
        self._anchor_event_binding_cache = {}
        self._is_loaded = False
        self._format_profile: Optional[FormatProfile] = None
        self._source_bytes: Optional[bytes] = None
        self._source_top_level_blocks = {}
        self._top_level_snapshots = {}
        self._sequence_lengths = {}
        self._is_dirty = False
        self._source_matches_data = False
        self._document_spans: List[Tuple[int, int, int, int]] = []
        self._source_spans_current = False
        self._dirty_documents: Set[int] = set()

    @property
    def data(self) -> Any:
        """Return the first document for backwards-compatible callers."""
        return self.documents[0] if self.documents else None

    @property
    def documents(self) -> List[Any]:
        """Return the round-trip stream, refreshing it after a source splice."""
        self._ensure_fresh_tree()
        return self._documents

    @documents.setter
    def documents(self, value: List[Any]) -> None:
        self._documents = value

    @data.setter
    def data(self, value: Any) -> None:
        """Replace the first document while retaining stream compatibility."""
        if self.documents:
            self.documents[0] = value
        else:
            self.documents = [value]
        if self._is_loaded:
            self._is_dirty = True
            self._source_matches_data = False
            self._dirty_documents.add(0)

    @property
    def doc_count(self) -> int:
        """Return the number of documents in the loaded YAML stream."""
        return self._document_count
    
    def load(self) -> Any:
        """
        Load the YAML file for modification.
        
        Returns:
            The loaded YAML data structure (dict or list)
            
        Raises:
            IOError: If the file cannot be read
        """
        source_bytes = self.file_path.read_bytes()
        has_bom = source_bytes.startswith(b'\xef\xbb\xbf')
        source_text = source_bytes[3:].decode('utf-8') if has_bom else source_bytes.decode('utf-8')

        self._format_profile = self._detect_format_profile(source_text, has_bom)
        self._apply_format_profile(self._format_profile)
        if not any(
            line.strip() and not line.lstrip().startswith("#")
            for line in source_text.splitlines()
        ):
            self.documents = [] if not source_text else [None]
        else:
            self.documents = list(self.yaml.load_all(source_text))
        for document in self.documents:
            self._mark_anchors_for_round_trip(document)
        self._source_bytes = source_bytes
        self._capture_source_structure(source_text)
        self._document_spans = self._document_source_spans(source_text)
        self._source_spans_current = True
        self._document_count = len(self._documents)
        self._tree_is_stale = False
        self._anchor_event_binding_cache = {}
        self._dirty_documents = set()
        self._is_dirty = False
        self._source_matches_data = True
        
        self._is_loaded = True
        return self.data

    def _ensure_fresh_tree(self) -> None:
        """Synchronously rebuild derived state after a source-only splice.

        STALE-STATE INVARIANT: a source splice makes ``_source_bytes``
        authoritative and leaves the parsed tree, locations, source spans,
        and captured structure stale until a tree-aware operation needs them.
        """
        if not self._tree_is_stale:
            return
        source_text = self._source_text()
        if source_text is None:
            raise ValueError("Cannot refresh writer state without source text")
        normalized = source_text.replace("\r\n", "\n").replace("\r", "\n")
        self._documents = list(self.yaml.load_all(normalized))
        for document in self._documents:
            self._mark_anchors_for_round_trip(document)
        if not self._source_spans_current:
            self._document_spans = self._document_source_spans(source_text)
            self._source_spans_current = True
        self._tree_is_stale = False
        self._capture_source_structure(source_text)
        self._document_count = len(self._documents)
        self._anchor_event_binding_cache = {}
    
    def set_value(
        self,
        path: str,
        value: Any,
        allow_kind_change: bool = False,
        doc: Optional[int] = None,
    ) -> None:
        """
        Set a value at a specific YAML path.
        
        Updates an existing value. Use insert_value() to create new keys.
        
        Args:
            path: Dot-notation path like "metadata.labels.app"
            value: The value to set
            allow_kind_change: Allow replacing a mapping, sequence, or scalar
                with a different kind. Defaults to ``False`` to prevent
                accidental structural data loss.
            
        Raises:
            ValueError: If the file hasn't been loaded
            KeyError: If the path doesn't exist
            
        Example:
            >>> writer.set_value("spec.replicas", 5)
            >>> writer.set_value("items.0.name", "newname")  # List index
        """
        if not self._is_loaded:
            raise ValueError("Call load() before modifying data")
        self._source_matches_data = False
        
        parts = self._parse_path(path)
        document_index = self._resolve_document(parts, doc)
        target = self._navigate_to_parent(parts[:-1], self.documents[document_index])
        final_key = parts[-1]
        
        # Handle list index
        if isinstance(target, list):
            index = self._parse_index(final_key)
            if index >= len(target):
                raise KeyError(f"List index {index} out of range at path: {path}")
            current = target[index]
            self._assert_kind_change_allowed(
                target,
                index,
                current,
                value,
                path,
                allow_kind_change,
                document_index,
            )
            preserve_metadata = not self._is_alias_target(
                target, index, current, document_index
            )
            replacement = self._prepare_replacement(
                current,
                value,
                preserve_metadata=preserve_metadata,
                preserve_anchor=self._should_preserve_anchor(
                    current, preserve_metadata, document_index, target, index
                ),
            )
            self._adjust_sequence_eol_comment_column(target, index, replacement)
            self._replace_anchor_definition(
                target,
                index,
                current,
                replacement,
                preserve_metadata,
                document_index,
            )
        else:
            # Handle dict key
            if final_key not in target:
                raise KeyError(f"Key '{final_key}' does not exist at path: {path}")
            current = target[final_key]
            if isinstance(current, dict) and isinstance(value, dict):
                self._clear_replaced_mapping_child_comments(target, final_key)
            self._assert_kind_change_allowed(
                target,
                final_key,
                current,
                value,
                path,
                allow_kind_change,
                document_index,
            )
            preserve_metadata = not self._is_alias_target(
                target, final_key, current, document_index
            )
            replacement = self._prepare_replacement(
                current,
                value,
                preserve_metadata=preserve_metadata,
                preserve_anchor=self._should_preserve_anchor(
                    current, preserve_metadata, document_index, target, final_key
                ),
            )
            self._adjust_mapping_eol_comment_column(target, final_key, replacement)
            self._replace_anchor_definition(
                target,
                final_key,
                current,
                replacement,
                preserve_metadata,
                document_index,
            )
        self._is_dirty = True
        self._dirty_documents.add(document_index)

    def set_root(
        self,
        value: Any,
        allow_kind_change: bool = False,
        doc: Optional[int] = None,
    ) -> None:
        """Replace the root document while enforcing the node-kind policy.

        Args:
            value: The new root value.
            allow_kind_change: Allow replacing the root mapping, sequence, or
                scalar with a different kind. Defaults to ``False`` to prevent
                accidental structural data loss.

        Raises:
            ValueError: If the file has not been loaded.
            NodeKindChangeError: If the root kind would change without an
                explicit override.
            AnchorInUseError: If an allowed kind change would orphan aliases
                of an anchored root.
        """
        if not self._is_loaded:
            raise ValueError("Call load() before modifying data")
        self._source_matches_data = False

        document_index = self._resolve_document([], doc)
        current = self.documents[document_index]
        current_kind = self._node_kind(current)
        new_kind = self._node_kind(value)
        if current_kind != new_kind:
            if not allow_kind_change:
                raise self._node_kind_change_error("root", current, value)
            self._assert_root_kind_change_anchor_safe(current, document_index)

        self.documents[document_index] = self._prepare_replacement(current, value)
        self._is_dirty = True
        self._dirty_documents.add(document_index)
    
    def insert_value(self, path: str, value: Any, doc: Optional[int] = None) -> None:
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
        document_index = self._resolve_document(parts, doc, for_insert=True)
        
        # Navigate/create path to parent
        current = self.documents[document_index]
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
                    self._source_matches_data = False
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
            if index == len(current):
                self._append_to_sequence(current, value)
            else:
                self._source_matches_data = False
                current.insert(index, self._prepare_new_sequence_value(current, value))
        elif isinstance(current, dict):
            if final_key in current:
                raise ValueError(f"Key '{final_key}' already exists at path: {path}. Use set_value() to update.")
            self._source_matches_data = False
            current[final_key] = self._prepare_new_mapping_value(current, final_key, value)
        else:
            raise TypeError(f"Cannot insert into non-dict/list value at: {'.'.join(parts[:-1])}")
        self._is_dirty = True
        self._dirty_documents.add(document_index)

    def _delete_stale_appended_item(self, path: str, doc: Optional[int]) -> bool:
        """Undo a final source append without rebuilding its now-stale tree."""
        if not self._tree_is_stale or not self._source_matches_data:
            return False
        # Appending a final item adds a physical line terminator when the
        # original source had none. Let the normal refreshed-tree path handle
        # that shape so it can restore the exact no-final-newline source.
        if not (self._format_profile or FormatProfile()).trailing_newline_suffix:
            return False
        try:
            parts = self._parse_path(path)
            if not parts:
                return False
            document_index = doc if doc is not None else (0 if len(self._documents) == 1 else None)
            if document_index is None or document_index < 0 or document_index >= len(self._documents):
                return False
            parent = self._navigate_to_parent(parts[:-1], self._documents[document_index])
            index = self._parse_index(parts[-1])
        except (KeyError, TypeError, ValueError):
            return False
        if not isinstance(parent, CommentedSeq) or index != len(parent):
            return False
        source_text = self._source_text()
        if source_text is None or not self._sequence_source_locations_are_trustworthy(parent, source_text):
            return False
        lines = source_text.replace('\r\n', '\n').replace('\r', '\n').splitlines(keepends=True)
        last_line = parent.lc.item(len(parent) - 1)[0]
        dash_column = lines[last_line].index('-')
        start_line = last_line + 1
        while start_line < len(lines):
            line = lines[start_line]
            if not line.strip() or line.lstrip().startswith('#') or len(line) - len(line.lstrip()) <= dash_column:
                break
            start_line += 1
        end_line = start_line + 1
        while end_line < len(lines):
            line = lines[end_line]
            if not line.strip() or line.lstrip().startswith('#') or len(line) - len(line.lstrip()) <= dash_column:
                break
            end_line += 1
        if start_line >= len(lines) or not lines[start_line].lstrip().startswith('-'):
            return False
        del lines[start_line:end_line]
        updated = ''.join(lines)
        if (self._format_profile or FormatProfile()).line_ending == '\r\n':
            updated = updated.replace('\n', '\r\n')
        self._replace_source_text(updated)
        return True

    def append_item(
        self,
        list_path: Optional[str],
        value: Any,
        doc: Optional[int] = None,
    ) -> None:
        """Append ``value`` to the list at ``list_path`` or the root list.

        Args:
            list_path: Dot-notation path to a list, or ``None`` for a root list.
            value: Value to append.

        Raises:
            ValueError: If the file has not been loaded.
            KeyError: If ``list_path`` does not exist.
            TypeError: If the selected value is not a list.
        """
        if not self._is_loaded:
            raise ValueError("Call load() before modifying data")

        try:
            if list_path is None:
                document_index = self._resolve_document([], doc)
                target = self.documents[document_index]
            else:
                parts = self._parse_path(list_path)
                document_index = self._resolve_document(parts, doc)
                target = self._get_value_from_document(parts, document_index)
        except ValueError as error:
            if list_path is not None and str(error).startswith("Expected numeric list index"):
                raise KeyError(f"Path does not exist: {list_path}") from error
            raise
        if not isinstance(target, list):
            location = "root document" if list_path is None else f"path: {list_path}"
            raise TypeError(f"Cannot append to non-list value at {location}")

        self._append_to_sequence(target, value)
        self._is_dirty = True
        self._dirty_documents.add(document_index)
    
    def delete_value(self, path: str, doc: Optional[int] = None) -> None:
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

        if self._delete_stale_appended_item(path, doc):
            return
        
        parts = self._parse_path(path)
        document_index = self._resolve_document(parts, doc)
        target = self._navigate_to_parent(parts[:-1], self.documents[document_index])
        final_key = parts[-1]
        
        # Handle deletion
        if isinstance(target, list):
            index = self._parse_index(final_key)
            if index >= len(target):
                raise KeyError(f"List index {index} out of range at path: {path}")
            self._assert_deletable_anchor(
                target, index, target[index], path, document_index
            )
            if self._delete_sequence_item_from_source(target, index):
                self._dirty_documents.add(document_index)
                return
            self._source_matches_data = False
            self._delete_sequence_item(target, index)
        elif isinstance(target, dict):
            if final_key == "<<" and self._has_merge_entry(target):
                self._source_matches_data = False
                self._delete_merge_entry(target)
                self._is_dirty = True
                self._dirty_documents.add(document_index)
                return
            if final_key not in target:
                raise KeyError(f"Key '{final_key}' does not exist at path: {path}")
            if self._is_merge_only_key(target, final_key):
                raise MergedKeyError(
                    f"Cannot delete merged key '{final_key}' at path '{path}'; "
                    "delete the '<<' entry or add an own-key override first"
                )
            self._assert_deletable_anchor(
                target, final_key, target[final_key], path, document_index
            )
            self._source_matches_data = False
            self._delete_mapping_key(target, final_key)
        else:
            raise TypeError(f"Cannot delete from non-dict/list value")
        self._is_dirty = True
        self._dirty_documents.add(document_index)

    def append_items(self, list_path: Optional[str], items: List[Any], doc: Optional[int] = None) -> None:
        """Append all items as one atomic transformation of the selected list."""
        if not self._is_loaded:
            raise ValueError("Call load() before modifying data")
        if not items:
            return
        target, document_index = self.resolve_list_target(list_path, doc)
        prepared = self._prepare_batch_items(target, items)
        if self._append_items_to_source(target, prepared):
            return
        snapshot = deepcopy(target)
        try:
            target.extend(prepared)
        except Exception:
            target[:] = snapshot
            raise
        self._source_matches_data = False
        self._is_dirty = True
        self._dirty_documents.add(document_index)

    def delete_values(self, list_path: Optional[str], indexes: List[int], doc: Optional[int] = None) -> None:
        """Delete original-list indexes as one atomic transformation."""
        if not self._is_loaded:
            raise ValueError("Call load() before modifying data")
        if not indexes:
            return
        target, document_index = self.resolve_list_target(list_path, doc)
        self._validate_batch_indexes(target, indexes)
        self._assert_batch_deletable_anchors(target, indexes, list_path, document_index)
        if self._delete_sequence_items_from_source(target, indexes):
            return
        snapshot = deepcopy(target)
        try:
            for index in sorted(indexes, reverse=True):
                self._delete_sequence_item(target, index)
        except Exception:
            target[:] = snapshot
            raise
        self._source_matches_data = False
        self._is_dirty = True
        self._dirty_documents.add(document_index)

    def resolve_list_target(
        self,
        list_path: Optional[str],
        doc: Optional[int] = None,
    ) -> Tuple[list, int]:
        """Resolve a list path and its document for a coordinated batch edit.

        This exposes only target validation/address resolution; callers must
        use :meth:`append_items` or :meth:`delete_values` to mutate the list.
        """
        try:
            if not list_path:
                document_index = self._resolve_document([], doc)
                target = self.documents[document_index]
            else:
                parts = self._parse_path(list_path)
                document_index = self._resolve_document(parts, doc)
                target = self._get_value_from_document(parts, document_index)
        except ValueError as error:
            if list_path and str(error).startswith("Expected numeric list index"):
                raise KeyError(f"Path does not exist: {list_path}") from error
            raise
        if not isinstance(target, list):
            location = "root document" if not list_path else f"path: {list_path}"
            raise TypeError(f"Cannot append to non-list value at {location}")
        return target, document_index

    def _prepare_batch_items(self, target: list, items: List[Any]) -> List[Any]:
        prepared = []
        prototype = target[-1] if target else None
        validator = YAML()
        validator.preserve_quotes = True
        for item in items:
            candidate = self._prepare_new_sequence_value(target, item, prototype)
            try:
                validator.dump(candidate, StringIO())
            except Exception as error:
                raise TypeError("Cannot safely render batch list item") from error
            prepared.append(candidate)
            prototype = candidate
        return prepared

    @staticmethod
    def _validate_batch_indexes(target: list, indexes: List[int]) -> None:
        if any(isinstance(index, bool) or not isinstance(index, int) for index in indexes):
            raise TypeError("Batch list indexes must be integers")
        if len(set(indexes)) != len(indexes):
            raise ValueError("Batch list indexes must not contain duplicates")
        for index in indexes:
            if index < 0 or index >= len(target):
                raise IndexError(f"List index {index} out of range")

    def _assert_batch_deletable_anchors(self, target: list, indexes: List[int], list_path: Optional[str], document_index: int) -> None:
        selected = set(indexes)
        for index in indexes:
            anchor_name = getattr(getattr(target[index], "anchor", None), "value", None)
            if not anchor_name:
                continue
            definition, occurrences = self._resolve_anchor_group(anchor_name, document_index, target, index)
            if definition[0] is target and definition[1] == index and any(parent is not target or key not in selected for parent, key, _ in occurrences):
                path = "root" if not list_path else list_path
                raise AnchorInUseError(f"Cannot delete anchored node '{anchor_name}' at path '{path}.{index}': {len(occurrences) - 1} alias occurrence(s) still reference it")

    @staticmethod
    def _prepare_replacement(
        current: Any,
        replacement: Any,
        preserve_metadata: bool = True,
        preserve_anchor: bool = True,
    ) -> Any:
        """Preserve anchors and tags while replacing one direct occurrence.

        ruamel.yaml 0.19.1 represents aliases as multiple parent references to
        one Python object. Assigning through one parent deliberately replaces
        only that occurrence. The small use of ``anchor``/``tag`` and their
        setters here is verified against that installed version.
        """
        anchor = getattr(current, "anchor", None)
        anchor_name = getattr(anchor, "value", None)
        tag = getattr(current, "tag", None)

        replacement = YamlWriter._preserve_scalar_style(current, replacement)

        if not preserve_metadata:
            return replacement
        if isinstance(current, TaggedScalar):
            replacement = TaggedScalar(replacement)
        elif isinstance(current, CommentedMap) and isinstance(replacement, dict):
            replacement = CommentedMap(replacement)
        elif isinstance(current, CommentedSeq) and isinstance(replacement, list):
            replacement = CommentedSeq(replacement)
        elif anchor_name and preserve_anchor and not hasattr(replacement, "yaml_set_anchor"):
            replacement = YamlWriter._wrap_anchored_scalar(current, replacement)

        if tag is not None and hasattr(replacement, "yaml_set_ctag"):
            replacement.yaml_set_ctag(tag)
        if anchor_name and preserve_anchor and hasattr(replacement, "yaml_set_anchor"):
            replacement.yaml_set_anchor(anchor_name, always_dump=True)
        return replacement

    def _should_preserve_anchor(
        self,
        current: Any,
        preserve_metadata: bool,
        document_index: int,
        parent: Any,
        key: Any,
    ) -> bool:
        """Apply the root-list replacement anchor policy without weakening maps."""
        anchor_name = getattr(getattr(current, "anchor", None), "value", None)
        if not preserve_metadata or not anchor_name:
            return False
        # Direct writer and mapping replacements retain an unreferenced anchor
        # for backward-compatible round trips. Qualified root-list scalar SQL
        # replacement intentionally emits its new literal without that stale
        # definition, while retaining a custom tag.
        if not self._is_document_root_sequence(parent):
            return True
        _, occurrences = self._resolve_anchor_group(
            anchor_name, document_index, parent, key
        )
        return len(occurrences) > 1

    @staticmethod
    def _node_kind(value: Any) -> str:
        """Classify values for structural replacement safety checks."""
        if isinstance(value, dict):
            return "mapping"
        if isinstance(value, list):
            return "sequence"
        return "scalar"

    @classmethod
    def _node_kind_change_error(
        cls,
        path: str,
        current: Any,
        replacement: Any,
    ) -> NodeKindChangeError:
        """Build the explicit error emitted for a blocked structure change."""
        current_kind = cls._node_kind(current)
        replacement_kind = cls._node_kind(replacement)
        return NodeKindChangeError(
            f"Refusing to change node at '{path}' from {current_kind} to "
            f"{replacement_kind} ({type(replacement).__name__}); pass "
            "allow_kind_change=True to override"
        )

    def _assert_kind_change_allowed(
        self,
        parent: Any,
        key: Any,
        current: Any,
        replacement: Any,
        path: str,
        allow_kind_change: bool,
        document_index: int,
    ) -> None:
        """Reject unsafe kind changes before mutating the selected node."""
        if self._node_kind(current) == self._node_kind(replacement):
            return
        if not allow_kind_change:
            raise self._node_kind_change_error(path, current, replacement)
        self._assert_deletable_anchor(parent, key, current, path, document_index)

    def _assert_root_kind_change_anchor_safe(self, current: Any, document_index: int) -> None:
        """Reject root replacement when aliases retain its anchor definition."""
        anchor_name = getattr(getattr(current, "anchor", None), "value", None)
        if not anchor_name:
            return
        alias_count = len(self._anchor_occurrences(anchor_name, document_index))
        if alias_count:
            raise AnchorInUseError(
                f"Cannot replace anchored root node '{anchor_name}': "
                f"{alias_count} alias occurrence(s) still reference it"
            )

    @staticmethod
    def _preserve_scalar_style(current: Any, replacement: Any) -> Any:
        """Keep a replaced string's quote/block style and block chomping."""
        if not isinstance(replacement, str):
            return replacement

        scalar_type = None
        if isinstance(current, SingleQuotedScalarString):
            scalar_type = SingleQuotedScalarString
        elif isinstance(current, DoubleQuotedScalarString):
            scalar_type = DoubleQuotedScalarString
        elif isinstance(current, LiteralScalarString):
            scalar_type = LiteralScalarString
            replacement = YamlWriter._match_block_chomping(current, replacement)
        elif isinstance(current, FoldedScalarString):
            scalar_type = FoldedScalarString
            replacement = YamlWriter._match_block_chomping(current, replacement)

        return scalar_type(replacement) if scalar_type is not None else replacement

    @staticmethod
    def _match_block_chomping(current: Any, replacement: str) -> str:
        """Match clip, strip, or keep behavior encoded by a loaded scalar."""
        original_newlines = len(str(current)) - len(str(current).rstrip("\n"))
        return replacement.rstrip("\n") + ("\n" * original_newlines)

    @staticmethod
    def _quote_style(value: Any) -> Optional[type]:
        """Return a quote-style scalar class, excluding block/plain scalars."""
        if isinstance(value, SingleQuotedScalarString):
            return SingleQuotedScalarString
        if isinstance(value, DoubleQuotedScalarString):
            return DoubleQuotedScalarString
        return None

    @classmethod
    def _uniform_sibling_quote_style(cls, mapping: Any) -> Optional[type]:
        """Find the one shared quote style among a mapping's string values."""
        if not isinstance(mapping, dict):
            return None
        styles = [cls._quote_style(item) for item in mapping.values() if isinstance(item, str)]
        if not styles or any(style is None for style in styles):
            return None
        return styles[0] if all(style is styles[0] for style in styles) else None

    @classmethod
    def _style_new_string(cls, value: str, prototype: Any, siblings: Any) -> Any:
        """Adopt an explicit prototype or uniform quote style for a string."""
        style = cls._quote_style(prototype) or cls._uniform_sibling_quote_style(siblings)
        if style is not None:
            return style(value)
        # PyYAML safe_load() recognizes YAML 1.1 boolean/null spellings that
        # ruamel's YAML 1.2 emitter may leave plain. Quote those values so a
        # new string never changes type when consumed by the read pipeline.
        if value.lower() in {"yes", "no", "true", "false", "on", "off", "null", "~"}:
            return DoubleQuotedScalarString(value)
        return value

    @classmethod
    def _prepare_new_mapping_value(
        cls,
        mapping: Any,
        key: Any,
        value: Any,
        prototype: Any = None,
        sibling_mapping: Any = None,
    ) -> Any:
        """Prepare an inserted mapping value using local row/sibling style."""
        if isinstance(value, str):
            siblings = sibling_mapping if isinstance(sibling_mapping, dict) else mapping
            return cls._style_new_string(value, prototype, siblings)
        if isinstance(value, dict):
            result = CommentedMap()
            if (
                isinstance(prototype, CommentedMap)
                and prototype.fa.flow_style()
                and not isinstance(value, CommentedMap)
            ):
                result.fa.set_flow_style()
            for child_key, child_value in value.items():
                child_prototype = prototype.get(child_key) if isinstance(prototype, dict) else None
                result[child_key] = cls._prepare_new_mapping_value(
                    value, child_key, child_value, child_prototype
                )
            return result
        if isinstance(value, list):
            result = CommentedSeq()
            if isinstance(prototype, CommentedSeq) and prototype.fa.flow_style():
                result.fa.set_flow_style()
            for item in value:
                result.append(cls._prepare_new_sequence_value(result, item, prototype))
            return result
        return value

    @classmethod
    def _prepare_new_sequence_value(
        cls,
        sequence: Any,
        value: Any,
        prototype: Any = None,
    ) -> Any:
        """Prepare a sequence item, including mapping-row style conventions."""
        if prototype is None and sequence:
            prototype = sequence[-1]
        if isinstance(value, str):
            return cls._style_new_string(value, prototype, sequence)
        if isinstance(value, dict):
            result = CommentedMap()
            if isinstance(prototype, CommentedMap) and prototype.fa.flow_style():
                result.fa.set_flow_style()
            for key, child_value in value.items():
                child_prototype = prototype.get(key) if isinstance(prototype, dict) else None
                result[key] = cls._prepare_new_mapping_value(
                    value, key, child_value, child_prototype, prototype
                )
            return result
        if isinstance(value, list):
            result = CommentedSeq()
            if isinstance(prototype, CommentedSeq) and prototype.fa.flow_style():
                result.fa.set_flow_style()
            for item in value:
                result.append(cls._prepare_new_sequence_value(result, item, prototype))
            return result
        return value

    def _is_alias_target(
        self,
        parent: Any,
        key: Any,
        value: Any,
        document_index: int,
    ) -> bool:
        """Return whether a direct target slot is an alias occurrence."""
        anchor_name = getattr(getattr(value, "anchor", None), "value", None)
        if not anchor_name:
            return False
        definition, _ = self._resolve_anchor_group(
            anchor_name, document_index, parent, key
        )
        return parent is not definition[0] or key != definition[1]

    def _replace_anchor_definition(
        self,
        parent: Any,
        key: Any,
        current: Any,
        replacement: Any,
        preserve_metadata: bool,
        document_index: int,
    ) -> None:
        """Replace an anchor definition and reconnect all of its aliases.

        ruamel.yaml 0.19.1 normally shares aliased scalar objects, but scalar
        aliases can also appear as distinct anchor-bearing instances. Assigning
        one shared replacement to every occurrence makes the emitter retain
        ``*anchor`` syntax instead of serializing stale scalar literals.
        """
        anchor_name = getattr(getattr(current, "anchor", None), "value", None)
        if preserve_metadata and anchor_name:
            _, occurrences = self._resolve_anchor_group(
                anchor_name, document_index, parent, key
            )
            if occurrences:
                for occurrence_parent, occurrence_key, _ in occurrences:
                    occurrence_parent[occurrence_key] = replacement
                return
        parent[key] = replacement

    @staticmethod
    def _wrap_anchored_scalar(current: Any, replacement: Any) -> Any:
        """Wrap a scalar using its ruamel type so its anchor remains valid."""
        try:
            return type(current)(replacement)
        except (TypeError, ValueError):
            if isinstance(replacement, str):
                return PlainScalarString(replacement)
            raise YamlWriterPolicyError(
                "Cannot safely retain an anchor while replacing this scalar"
            )

    @staticmethod
    def _has_merge_entry(mapping: Any) -> bool:
        """Return whether a ruamel 0.19.1 mapping retains a ``<<`` entry."""
        return isinstance(mapping, CommentedMap) and bool(getattr(mapping, "merge", None))

    @staticmethod
    def _is_merge_only_key(mapping: Any, key: Any) -> bool:
        """Identify a key resolved from ``<<`` rather than owned locally."""
        return (
            isinstance(mapping, CommentedMap)
            and key in mapping
            and key not in getattr(mapping, "_ok", set())
        )

    @staticmethod
    def _delete_merge_entry(mapping: CommentedMap) -> None:
        """Remove ``<<`` without flattening inherited keys on ruamel 0.19.1."""
        merge_only_keys = [
            key for key in list(mapping)
            if key not in getattr(mapping, "_ok", set())
        ]
        for key in merge_only_keys:
            del mapping[key]
        mapping.merge.value[:] = []

    def _assert_deletable_anchor(
        self,
        parent: Any,
        key: Any,
        value: Any,
        path: str,
        document_index: int,
    ) -> None:
        """Reject deletion of an anchor definition still used by aliases."""
        anchor_name = getattr(getattr(value, "anchor", None), "value", None)
        if not anchor_name:
            return
        definition, occurrences = self._resolve_anchor_group(
            anchor_name, document_index, parent, key
        )
        if len(occurrences) < 2 or parent is not definition[0] or key != definition[1]:
            return
        raise AnchorInUseError(
            f"Cannot delete anchored node '{anchor_name}' at path '{path}': "
            f"{len(occurrences) - 1} alias occurrence(s) still reference it"
        )

    def _anchor_occurrences(
        self,
        anchor_name: str,
        document_index: int,
    ) -> List[Tuple[Any, Any, Optional[Tuple[int, int]]]]:
        """Find direct anchor-bearing slots inside one source document."""
        return self._find_occurrences(
            lambda child: getattr(getattr(child, "anchor", None), "value", None)
            == anchor_name,
            document_index,
        )

    def _find_occurrences(
        self,
        predicate: Any,
        document_index: int,
    ) -> List[Tuple[Any, Any, Optional[Tuple[int, int]]]]:
        """Find direct parent slots whose values match ``predicate`` in one document."""
        occurrences = []

        def visit(value: Any, ancestors: set) -> None:
            if id(value) in ancestors:
                return
            next_ancestors = ancestors | {id(value)}
            if isinstance(value, dict):
                for child_key, child in value.items():
                    location = value.lc.key(child_key) if hasattr(value, "lc") else None
                    if predicate(child):
                        occurrences.append((value, child_key, location))
                    visit(child, next_ancestors)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    location = value.lc.item(index) if hasattr(value, "lc") else None
                    if predicate(child):
                        occurrences.append((value, index, location))
                    visit(child, next_ancestors)

        visit(self.documents[document_index], set())
        return occurrences

    def _anchor_event_bindings(
        self,
        anchor_name: str,
        document_index: int,
    ) -> Optional[List[bool]]:
        """Return source-order definition flags for one anchor in one document.

        ``AliasEvent`` is public ruamel parser API. The only parser state this
        method relies on is the emitted event sequence; node locations come
        from the round-trip tree's public ``lc`` metadata.
        """
        cache_key = (anchor_name, document_index)
        if cache_key in self._anchor_event_binding_cache:
            return self._anchor_event_binding_cache[cache_key]
        source_text = self._source_text()
        if source_text is None:
            return None
        try:
            events = list(YAML(typ="rt").parse(source_text))
        except Exception:
            return None
        current_document = -1
        bindings = []
        for event in events:
            if isinstance(event, DocumentStartEvent):
                current_document += 1
            if current_document != document_index:
                continue
            if getattr(event, "anchor", None) != anchor_name:
                continue
            bindings.append(not isinstance(event, AliasEvent))
        self._anchor_event_binding_cache[cache_key] = bindings
        return bindings

    def _resolve_anchor_group(
        self,
        anchor_name: str,
        document_index: int,
        target_parent: Any,
        target_key: Any,
    ) -> Tuple[
        Tuple[Any, Any, Optional[Tuple[int, int]]],
        List[Tuple[Any, Any, Optional[Tuple[int, int]]]],
    ]:
        """Resolve the target occurrence to its document-local definition group.

        Reused names are intentionally resolved by source occurrence, not
        ruamel object identity: each alias belongs to the nearest preceding
        definition event of the same name in its own document.
        """
        occurrences = self._anchor_occurrences(anchor_name, document_index)
        bindings = self._anchor_event_bindings(anchor_name, document_index)
        ordered = sorted(
            occurrences,
            key=lambda occurrence: occurrence[2] if occurrence[2] is not None else (10 ** 9, 10 ** 9),
        )
        target_index = next(
            (
                index
                for index, occurrence in enumerate(ordered)
                if occurrence[0] is target_parent and occurrence[1] == target_key
            ),
            None,
        )
        definition_count = (
            sum(1 for is_definition in bindings if is_definition)
            if bindings is not None
            else 0
        )
        # A prior alias-to-literal edit removes that alias from the live
        # round-trip tree while it remains in the immutable source event list.
        # One definition has only one possible group, so the remaining live
        # occurrence is still unambiguous. Reused names are rejected below
        # rather than guessing which source occurrence disappeared.
        if (
            bindings is not None
            and len(bindings) != len(ordered)
            and definition_count == 1
            and target_index is not None
            and all(occurrence[2] is not None for occurrence in ordered)
        ):
            return ordered[0], ordered
        if (
            bindings is None
            or target_index is None
            or len(bindings) != len(ordered)
            or any(occurrence[2] is None for occurrence in ordered)
        ):
            raise YamlWriterPolicyError(
                f"Cannot resolve anchor '{anchor_name}' identity in document {document_index}"
            )

        definition_indexes = [
            index for index, is_definition in enumerate(bindings) if is_definition
        ]
        definition_index = max(
            (index for index in definition_indexes if index <= target_index),
            default=None,
        )
        if definition_index is None:
            raise YamlWriterPolicyError(
                f"Cannot resolve anchor '{anchor_name}' identity in document {document_index}"
            )
        next_definition = next(
            (index for index in definition_indexes if index > definition_index),
            len(ordered),
        )
        return ordered[definition_index], ordered[definition_index:next_definition]

    @staticmethod
    def _mark_anchors_for_round_trip(value: Any, visited: Optional[set] = None) -> None:
        """Keep unreferenced anchors when ruamel re-emits a changed document."""
        if visited is None:
            visited = set()
        if id(value) in visited:
            return
        visited.add(id(value))
        anchor = getattr(value, "anchor", None)
        anchor_name = getattr(anchor, "value", None)
        if anchor_name and hasattr(value, "yaml_set_anchor"):
            value.yaml_set_anchor(anchor_name, always_dump=True)
        if isinstance(value, dict):
            for item in value.values():
                YamlWriter._mark_anchors_for_round_trip(item, visited)
        elif isinstance(value, list):
            for item in value:
                YamlWriter._mark_anchors_for_round_trip(item, visited)

    def _append_to_sequence(self, sequence: list, value: Any) -> None:
        """Append while retaining comments that belong after a sequence."""
        value = self._prepare_new_sequence_value(sequence, value)
        if self._append_sequence_item_to_source(sequence, value):
            return

        self._source_matches_data = False
        original_length = len(sequence)
        if isinstance(sequence, CommentedSeq) and original_length == 0:
            if sequence.fa.flow_style():
                sequence.fa.set_block_style()

        sequence.append(value)
        if isinstance(sequence, CommentedSeq) and original_length:
            self._move_sequence_trailing_comments(
                sequence,
                original_length - 1,
                original_length,
            )

    def _source_text(self) -> Optional[str]:
        """Return the loaded source without a BOM, if direct edits are possible."""
        if self._source_bytes is None:
            return None
        source = self._source_bytes
        if source.startswith(b'\xef\xbb\xbf'):
            source = source[3:]
        return source.decode('utf-8')

    @staticmethod
    def _line_start(lines: List[str], line_number: int) -> int:
        """Return the character offset where a zero-based source line starts."""
        return sum(len(line) for line in lines[:line_number])

    @staticmethod
    def _owned_item_start(lines: List[str], item_line: int) -> int:
        """Find comments directly above an item that belong to that item."""
        start = item_line
        while start > 0:
            previous = lines[start - 1]
            if not previous.strip():
                break
            if previous.lstrip().startswith('#'):
                start -= 1
                continue
            break
        return start

    def _replace_source_text(
        self,
        source_text: str,
        document_spans: Optional[List[Tuple[int, int, int, int]]] = None,
    ) -> None:
        """Install a source splice and defer rebuilding its derived state."""
        profile = self._format_profile or FormatProfile()
        encoded = source_text.encode('utf-8')
        self._source_bytes = (b'\xef\xbb\xbf' if profile.has_bom else b'') + encoded
        if document_spans is not None:
            self._document_spans = document_spans
        self._source_spans_current = document_spans is not None

        self._dirty_documents = set()
        self._is_dirty = True
        self._source_matches_data = True
        self._tree_is_stale = True
        self._anchor_event_binding_cache = {}

    def _append_items_to_source(self, sequence: list, values: List[Any]) -> bool:
        """Splice a complete block-list append exactly once."""
        if not isinstance(sequence, CommentedSeq) or not sequence:
            return False
        source_text = self._source_text()
        if not self._source_matches_data or source_text is None or sequence.fa.flow_style() or not self._sequence_source_locations_are_trustworthy(sequence, source_text):
            return False
        lines = source_text.replace('\r\n', '\n').replace('\r', '\n').splitlines(keepends=True)
        last_line = sequence.lc.item(len(sequence) - 1)[0]
        dash_column = lines[last_line].index('-')
        end_line = last_line + 1
        while end_line < len(lines):
            line = lines[end_line]
            if not line.strip() or line.lstrip().startswith('#') or len(line) - len(line.lstrip()) <= dash_column:
                break
            end_line += 1
        insertion = []
        for value in values:
            fragment = StringIO()
            renderer = YAML()
            renderer.preserve_quotes = True
            renderer.default_flow_style = False
            renderer.width = self.yaml.width
            renderer.indent(mapping=2, sequence=2, offset=0)
            renderer.dump(CommentedSeq([value]), fragment)
            insertion.extend(' ' * dash_column + (line.lstrip() if line_index == 0 else line) + '\n' for line_index, line in enumerate(fragment.getvalue().replace('\r\n', '\n').splitlines()))
        if end_line and not lines[end_line - 1].endswith('\n'):
            lines[end_line - 1] += '\n'
        lines[end_line:end_line] = insertion
        updated = ''.join(lines)
        if (self._format_profile or FormatProfile()).line_ending == '\r\n':
            updated = updated.replace('\n', '\r\n')
        # G3 source-splice fallback: one parser-backed validation per batch.
        # It occurs before replacing the authoritative source, so a parser
        # failure leaves bytes, tree, dirty flags, and spans unchanged.
        document_spans = self._document_source_spans(updated)
        if not document_spans:
            raise ValueError("Cannot safely validate batch source splice")
        self._replace_source_text(updated, document_spans)
        return True

    def _delete_sequence_items_from_source(self, sequence: list, indexes: List[int]) -> bool:
        """Delete several original block-list items by one source splice."""
        if not isinstance(sequence, CommentedSeq) or len(sequence) <= len(indexes):
            return False
        source_text = self._source_text()
        if not self._source_matches_data or source_text is None or sequence.fa.flow_style() or not self._sequence_source_locations_are_trustworthy(sequence, source_text):
            return False
        lines = source_text.replace('\r\n', '\n').replace('\r', '\n').splitlines(keepends=True)
        ranges = []
        for index in sorted(indexes, reverse=True):
            item_line = sequence.lc.item(index)[0]
            dash_column = len(lines[item_line]) - len(lines[item_line].lstrip())
            if index + 1 < len(sequence):
                end_line = self._owned_item_start(lines, sequence.lc.item(index + 1)[0])
            else:
                end_line = item_line + 1
                while end_line < len(lines):
                    line = lines[end_line]
                    if not line.strip() or line.lstrip().startswith('#') or len(line) - len(line.lstrip()) <= dash_column:
                        break
                    end_line += 1
            start_line = item_line
            if not (index == 0 and self._is_document_root_sequence(sequence)):
                start_line = self._owned_item_start(lines, item_line)
            ranges.append((start_line, end_line))
        if any(ranges[index][0] < ranges[index + 1][1] for index in range(len(ranges) - 1)):
            return False
        for start, end in ranges:
            del lines[start:end]
        updated = ''.join(lines)
        if (self._format_profile or FormatProfile()).line_ending == '\r\n':
            updated = updated.replace('\n', '\r\n')
        document_spans = self._document_source_spans(updated)
        if not document_spans:
            raise ValueError("Cannot safely validate batch source splice")
        self._replace_source_text(updated, document_spans)
        return True

    def _delete_sequence_item_from_source(self, sequence: list, index: int) -> bool:
        """Delete a block-list item by its original source-line range.

        ruamel.yaml 0.19.1 stores leading comments in predecessor comment
        tokens, which cannot express Q7 ownership reliably. Flow collections,
        programmatically assigned data, and nodes without source locations use
        the established ruamel-token fallback below.
        """
        if not isinstance(sequence, CommentedSeq) or len(sequence) == 1:
            return False
        if not (self._format_profile or FormatProfile()).trailing_newline_suffix:
            # A prior append necessarily introduced a terminator. The direct
            # source range deletion cannot distinguish that synthetic suffix
            # from an original one, while the render fallback can restore it.
            return False
        source_text = self._source_text()
        if (
            not self._source_matches_data
            or source_text is None
            or sequence.fa.flow_style()
            or not self._sequence_source_locations_are_trustworthy(sequence, source_text)
        ):
            return False
        item_location = sequence.lc.item(index)
        if item_location is None:
            return False

        lines = source_text.replace('\r\n', '\n').replace('\r', '\n').splitlines(keepends=True)
        item_line = item_location[0]
        if item_line >= len(lines):
            return False
        dash_column = len(lines[item_line]) - len(lines[item_line].lstrip())
        if index + 1 < len(sequence):
            next_location = sequence.lc.item(index + 1)
            if next_location is None:
                return False
            end_line = self._owned_item_start(lines, next_location[0])
        else:
            end_line = item_line + 1
            while end_line < len(lines):
                line = lines[end_line]
                if not line.strip() or line.lstrip().startswith('#'):
                    break
                if len(line) - len(line.lstrip()) <= dash_column:
                    break
                end_line += 1

        start_line = item_line
        if not (index == 0 and self._is_document_root_sequence(sequence)):
            start_line = self._owned_item_start(lines, item_line)
        del lines[start_line:end_line]
        updated = ''.join(lines)
        profile = self._format_profile or FormatProfile()
        if profile.line_ending == '\r\n':
            updated = updated.replace('\n', '\r\n')
        self._replace_source_text(updated)
        return True

    def _is_document_root_sequence(self, sequence: Any) -> bool:
        """Return whether a sequence owns a document's leading comments."""
        return any(sequence is document for document in self.documents)

    def _append_sequence_item_to_source(self, sequence: list, value: Any) -> bool:
        """Append a block-list item directly before its post-list section.

        Re-parsing after the splice is essential: the next source-range edit
        must use line/column information for this newly appended item, rather
        than the locations from the pre-append tree.
        """
        if not isinstance(sequence, CommentedSeq) or not sequence:
            return False
        source_text = self._source_text()
        if (
            not self._source_matches_data
            or source_text is None
            or sequence.fa.flow_style()
            or not self._sequence_source_locations_are_trustworthy(sequence, source_text)
        ):
            return False
        last_location = sequence.lc.item(len(sequence) - 1)
        if last_location is None:
            return False

        lines = source_text.replace('\r\n', '\n').replace('\r', '\n').splitlines(keepends=True)
        last_line = last_location[0]
        if last_line >= len(lines):
            return False
        dash_column = lines[last_line].index('-')
        end_line = last_line + 1
        while end_line < len(lines):
            line = lines[end_line]
            if not line.strip() or line.lstrip().startswith('#'):
                break
            if len(line) - len(line.lstrip()) <= dash_column:
                break
            end_line += 1

        fragment = StringIO()
        renderer = YAML()
        renderer.preserve_quotes = True
        renderer.default_flow_style = False
        renderer.width = self.yaml.width
        # Render a root-level fragment, then shift every emitted line to the
        # target dash column. Applying the document profile here would make
        # continuation mapping lines absolute rather than dash-relative.
        renderer.indent(mapping=2, sequence=2, offset=0)
        renderer.dump(CommentedSeq([value]), fragment)
        rendered_lines = fragment.getvalue().replace('\r\n', '\n').splitlines()
        indent = ' ' * dash_column
        insertion = [
            indent + (line.lstrip() if line_index == 0 else line) + '\n'
            for line_index, line in enumerate(rendered_lines)
        ]
        if end_line and not lines[end_line - 1].endswith('\n'):
            lines[end_line - 1] += '\n'
        lines[end_line:end_line] = insertion
        updated = ''.join(lines)
        profile = self._format_profile or FormatProfile()
        if profile.line_ending == '\r\n':
            updated = updated.replace('\n', '\r\n')
        self._replace_source_text(updated)
        return True

    @staticmethod
    def _sequence_source_locations_are_trustworthy(
        sequence: CommentedSeq,
        source_text: str,
    ) -> bool:
        """Check that all current list items have matching ruamel locations.

        A direct in-memory append has no ``lc`` entry. In that state source
        offsets describe an older tree and are unsafe for deletion. This check
        is deliberately conservative: callers use ruamel's safe container
        mutation when a source splice cannot be proven correct.
        """
        lines = source_text.replace('\r\n', '\n').replace('\r', '\n').splitlines()
        previous_line = -1
        for index in range(len(sequence)):
            try:
                location = sequence.lc.item(index)
            except (AttributeError, IndexError, KeyError, TypeError):
                return False
            if (
                location is None
                or len(location) != 2
                or location[0] <= previous_line
                or location[0] >= len(lines)
                or location[1] < 0
                or not lines[location[0]].lstrip().startswith('-')
            ):
                return False
            previous_line = location[0]
        return True

    def _delete_sequence_item(self, sequence: list, index: int) -> None:
        """Delete a sequence item and migrate only its successor's comments."""
        if not isinstance(sequence, CommentedSeq):
            del sequence[index]
            return

        # ruamel.yaml 0.19.1 stores leading comments in the terminal token of
        # the preceding rendered item (including nested item mappings).
        self._remove_sequence_item_leading_comments(sequence, index)
        successor_comments = self._take_sequence_following_comments(sequence, index)
        del sequence[index]
        if successor_comments:
            if index < len(sequence):
                self._attach_leading_sequence_comments(
                    sequence,
                    index,
                    successor_comments,
                )
                if index == 0:
                    self._set_parent_child_comment(sequence, successor_comments)
            elif sequence:
                if successor_comments.value.startswith("\n"):
                    successor_comments.value = "\n" + successor_comments.value
                self._append_terminal_comment(sequence[-1], successor_comments)

    def _delete_mapping_key(self, mapping: dict, key: Any) -> None:
        """Delete a mapping key without dropping the next key's comments.

        Verified against ruamel.yaml 0.19.1: an inline mapping-value token
        also contains comment lines immediately before the following key.
        """
        if not isinstance(mapping, CommentedMap):
            del mapping[key]
            return

        keys = list(mapping)
        key_index = keys.index(key)
        successor_key = keys[key_index + 1] if key_index + 1 < len(keys) else None
        comment_parts = mapping.ca.items.get(key)
        trailing_comment = comment_parts[2] if comment_parts and len(comment_parts) > 2 else None
        if successor_key is not None and trailing_comment is not None:
            newline_index = trailing_comment.value.find("\n")
            if newline_index != -1:
                following_value = trailing_comment.value[newline_index + 1:]
                if following_value.strip():
                    if key_index:
                        predecessor_key = keys[key_index - 1]
                        predecessor_parts = mapping.ca.items.setdefault(
                            predecessor_key,
                            [None, None, None, None],
                        )
                        predecessor_comment = predecessor_parts[2]
                        if predecessor_comment is not None:
                            predecessor_comment.value += following_value
                        else:
                            predecessor_parts[2] = type(trailing_comment)(
                                following_value,
                                trailing_comment.start_mark,
                            )
                        self._top_level_snapshots.pop(predecessor_key, None)
                    else:
                        start_mark = deepcopy(trailing_comment.start_mark)
                        start_mark.column = mapping.lc.key(successor_key)[1]
                        mapping.ca.comment = [None, [type(trailing_comment)(
                            following_value.lstrip("\r\n "),
                            start_mark,
                        )]]
                        self._clear_parent_sequence_comment(mapping)
                        self._set_parent_child_comment(mapping, mapping.ca.comment[1][0])
                    # The emitted comment token has changed even though the
                    # successor's semantic value has not. Do not replace its
                    # source block during format restoration.
                    self._top_level_snapshots.pop(successor_key, None)
        del mapping[key]

    def _move_sequence_trailing_comments(
        self,
        sequence: CommentedSeq,
        old_index: int,
        new_index: int,
    ) -> None:
        """Move post-list comments from an old final item to a new final item."""
        following_comments = self._take_sequence_following_comments(sequence, old_index)
        if following_comments:
            self._append_sequence_comments(sequence, new_index, following_comments)

    def _take_sequence_following_comments(self, sequence: CommentedSeq, index: int) -> Optional[Any]:
        """Detach leading/post-list lines after a sequence item's EOL token.

        Verified against ruamel.yaml 0.19.1. The final key in a mapping list
        item carries both its EOL comment and the lines before the next item.
        """
        token = self._terminal_comment_token(sequence[index])
        parts = None
        if token is None:
            parts = sequence.ca.items.get(index)
            token = parts[0] if parts else None
        if token is None:
            return None
        newline_index = token.value.find("\n")
        if newline_index == -1:
            return None
        # A token beginning with a newline has no EOL component; it is wholly
        # a post-list/next-key comment and must retain that leading newline.
        following_value = token.value if newline_index == 0 else token.value[newline_index + 1:]
        if not following_value.strip():
            return None
        if newline_index == 0 and parts:
            parts[0] = None
        else:
            token.value = token.value[:newline_index + 1]
        return type(token)(following_value, token.start_mark)

    def _remove_sequence_item_leading_comments(self, sequence: CommentedSeq, index: int) -> None:
        """Remove a deleted item's no-blank-line leading comments."""
        if index == 0:
            # ruamel 0.19.1 stores first-item comments on the sequence.
            if sequence.ca.comment and len(sequence.ca.comment) > 1:
                sequence.ca.comment[1] = None
            self._set_parent_child_comment(sequence, None)
            return
        token = self._terminal_comment_token(sequence[index - 1])
        if token is None:
            parts = sequence.ca.items.get(index - 1)
            token = parts[0] if parts else None
        if token is None:
            return
        newline_index = token.value.find("\n")
        if newline_index == -1:
            return
        following_value = token.value[newline_index + 1:]
        if following_value.strip() and not re.match(r'^[\r\n]+\s*#', following_value):
            token.value = token.value[:newline_index + 1]

    @staticmethod
    def _terminal_comment_token(value: Any) -> Optional[Any]:
        """Return a list item's final emitted comment token, if any."""
        if isinstance(value, CommentedMap) and value:
            key = next(reversed(value))
            parts = value.ca.items.get(key)
            return parts[2] if parts and len(parts) > 2 else None
        return None

    @staticmethod
    def _append_terminal_comment(value: Any, comment: Any) -> None:
        """Append a post-item token to a mapping item's final rendered key."""
        if isinstance(value, CommentedMap) and value:
            key = next(reversed(value))
            parts = value.ca.items.setdefault(key, [None, None, None, None])
            if parts[2] is None:
                parts[2] = comment
            else:
                parts[2].value += comment.value

    def _clear_parent_sequence_comment(self, sequence: Any) -> None:
        """Clear a parent mapping's duplicate first-child comment token."""
        def visit(value: Any) -> bool:
            if isinstance(value, CommentedMap):
                for key, child in value.items():
                    if child is sequence:
                        parts = value.ca.items.get(key)
                        if parts and len(parts) > 3:
                            parts[3] = None
                        return True
                    if visit(child):
                        return True
            elif isinstance(value, list):
                for child in value:
                    if visit(child):
                        return True
            return False

        visit(self.data)

    def _set_parent_child_comment(self, child: Any, comment: Optional[Any]) -> None:
        """Mirror first-child comments in ruamel's parent mapping slot."""
        def visit(value: Any) -> bool:
            if isinstance(value, CommentedMap):
                for key, nested in value.items():
                    if nested is child:
                        parts = value.ca.items.setdefault(key, [None, None, None, None])
                        parts[3] = [comment] if comment is not None else None
                        return True
                    if visit(nested):
                        return True
            elif isinstance(value, list):
                for nested in value:
                    if visit(nested):
                        return True
            return False

        visit(self.data)

    @staticmethod
    def _attach_leading_sequence_comments(
        sequence: CommentedSeq,
        index: int,
        comment: Any,
    ) -> None:
        """Attach comments before an item using ruamel's predecessor token."""
        if index:
            YamlWriter._append_terminal_comment(sequence[index - 1], comment)
            return

        # ruamel.yaml 0.19.1 stores comments before the first item here rather
        # than in ``ca.items`` because the sequence has no predecessor item.
        comment.value = comment.value.lstrip()
        existing_comment = sequence.ca.comment
        if existing_comment and len(existing_comment) > 1 and existing_comment[1]:
            existing_comment[1][-1].value += comment.value
        else:
            sequence.ca.comment = [None, [comment]]

    @staticmethod
    def _append_sequence_comments(
        sequence: CommentedSeq,
        index: int,
        comment: Any,
    ) -> None:
        """Attach comments after the indexed item without turning them into EOL text."""
        comment_parts = sequence.ca.items.setdefault(index, [None, None, None, None])
        existing = comment_parts[0]
        if existing is None:
            comment_parts[0] = comment
            return
        existing.value += comment.value
    
    def write(self) -> None:
        """
        Write the modified data back to the file.
        
        Preserves comments, formatting, and indentation.
        
        Raises:
            ValueError: If the file hasn't been loaded
            IOError: If the file cannot be written
        """
        self.write_to(self.file_path)

    def render(self) -> bytes:
        """Render the loaded YAML data using its detected physical format."""
        if not self._is_loaded:
            raise ValueError("Call load() before writing data")

        if self._source_bytes is not None and (
            not self._is_dirty or self._source_matches_data
        ):
            return self._source_bytes

        profile = self._format_profile or FormatProfile(trailing_newline_suffix="\n")
        if len(self.documents) > 1:
            return self._render_document_stream(profile)

        self._apply_format_profile(profile)
        self._move_appended_sequence_trailing_newlines()
        output = StringIO()
        self.yaml.dump(self.data, output)
        text = self._restore_unchanged_top_level_blocks(output.getvalue())
        text = self._normalize_line_endings(text, profile.line_ending)
        text = self._strip_trailing_newlines(text) + profile.trailing_newline_suffix
        encoded = text.encode('utf-8')
        return (b'\xef\xbb\xbf' if profile.has_bom else b'') + encoded

    def _render_document_stream(self, profile: FormatProfile) -> bytes:
        """Splice individually rendered dirty documents into their source spans."""
        if self._source_bytes is None or len(self._document_spans) != len(self.documents):
            raise ValueError(
                "Cannot safely render YAML stream: source-span count "
                f"({len(self._document_spans)}) does not match loaded document "
                f"count ({len(self.documents)})"
            )

        source_text = self._source_text()
        if source_text is None:
            raise ValueError("Cannot safely render a YAML stream without source text")

        rendered = []
        cursor = 0
        for index, (start, end, content_start, content_end) in enumerate(self._document_spans):
            rendered.append(source_text[cursor:start])
            if index not in self._dirty_documents:
                rendered.append(source_text[start:end])
            else:
                rendered.append(source_text[start:content_start])
                output = StringIO()
                explicit_start = self.yaml.explicit_start
                self._apply_format_profile(profile)
                if isinstance(self.documents[index], list):
                    self.yaml.indent(mapping=profile.mapping_indent, sequence=2, offset=0)
                self.yaml.explicit_start = False
                self.yaml.dump(self.documents[index], output)
                self.yaml.explicit_start = explicit_start
                body = output.getvalue()
                original_body = source_text[content_start:content_end]
                trailing_suffix = self._document_body_trailing_suffix(original_body)
                normalized_body = self._normalize_line_endings(body, profile.line_ending)
                rendered_body = self._strip_trailing_newlines(normalized_body)
                marker_needs_line_break = (
                    not original_body
                    and rendered_body
                    and content_start > start
                    and not source_text[start:content_start].endswith(("\n", "\r"))
                )
                if marker_needs_line_break:
                    rendered_body = profile.line_ending + rendered_body
                rendered.append(rendered_body + trailing_suffix)
                rendered.append(source_text[content_end:end])
            cursor = end
        rendered.append(source_text[cursor:])
        text = ''.join(rendered)
        return (b'\xef\xbb\xbf' if profile.has_bom else b'') + text.encode('utf-8')

    def write_to(self, path: Union[str, Path]) -> None:
        """Write :meth:`render` output to ``path`` without newline translation."""
        if not self._is_loaded:
            raise ValueError("Call load() before writing data")

        Path(path).write_bytes(self.render())

    def _apply_format_profile(self, profile: FormatProfile) -> None:
        """Configure ruamel's public formatting APIs from a profile."""
        self.yaml.indent(
            mapping=profile.mapping_indent,
            sequence=profile.sequence_indent,
            offset=profile.sequence_offset,
        )
        self.yaml.line_break = profile.line_ending
        self.yaml.explicit_start = profile.explicit_start

    @staticmethod
    def _strip_trailing_newlines(text: str) -> str:
        return re.sub(r'(?:\r\n|\n)+\Z', '', text)

    @staticmethod
    def _document_body_trailing_suffix(text: str) -> str:
        """Return a document body's exact trailing line-terminator suffix."""
        match = re.search(r'(?:\r\n|\n)+\Z', text)
        return match.group(0) if match else ''

    @staticmethod
    def _normalize_line_endings(text: str, line_ending: str) -> str:
        """Apply the detected EOL to emitted text and retained comment tokens."""
        return re.sub(r'\r\r\n|\r\n|\r|\n', line_ending, text)

    @staticmethod
    def _adjust_sequence_eol_comment_column(sequence: list, index: int, replacement: Any) -> None:
        """Keep an anchored sequence scalar's EOL comment at its source column."""
        if not isinstance(sequence, CommentedSeq):
            return
        parts = sequence.ca.items.get(index)
        token = parts[0] if parts else None
        if token is None or not token.value.startswith('#'):
            return
        location = sequence.lc.item(index)
        if location is None:
            return
        anchor_name = getattr(getattr(replacement, "anchor", None), "value", None)
        width = 2 + (len(anchor_name) + 2 if anchor_name else 0) + len(str(replacement))
        token.column = max(token.column, location[1] - 2 + width + 2)

    @staticmethod
    def _adjust_mapping_eol_comment_column(mapping: dict, key: Any, replacement: Any) -> None:
        """Keep two spaces before an anchored mapping scalar's EOL comment."""
        if not isinstance(mapping, CommentedMap):
            return
        parts = mapping.ca.items.get(key)
        token = parts[2] if parts and len(parts) > 2 else None
        if token is None or not token.value.startswith('#'):
            return
        location = mapping.lc.value(key)
        if location is None:
            return
        anchor_name = getattr(getattr(replacement, "anchor", None), "value", None)
        tag = getattr(replacement, "tag", None)
        if anchor_name:
            if isinstance(replacement, dict):
                token.column = location[1] + len(anchor_name) + 2
                return
            token.column = location[1] + len(anchor_name) + 2 + len(str(replacement)) + 2
            return
        if tag is not None:
            token.column = location[1] + len(str(tag)) + 1

    @staticmethod
    def _clear_replaced_mapping_child_comments(mapping: dict, key: Any) -> None:
        """Discard comments owned by children when replacing an entire mapping."""
        if not isinstance(mapping, CommentedMap):
            return
        parts = mapping.ca.items.get(key)
        token = parts[3] if parts and len(parts) > 3 else None
        if isinstance(token, list):
            token = token[0] if token else None
        key_location = mapping.lc.key(key)
        if (
            token is not None
            and key_location is not None
            and token.start_mark.line == key_location[0]
        ):
            parts[2] = token
            parts[3] = None
        elif (
            token is not None
            and key_location is not None
            and token.start_mark.line != key_location[0]
        ):
            parts[3] = None

    def _capture_source_structure(self, source_text: str) -> None:
        """Keep minimal source metadata needed when ruamel must re-emit a mapping."""
        self._source_top_level_blocks = {}
        self._top_level_snapshots = {}
        self._sequence_lengths = {}
        self._capture_sequence_lengths(self.data)

        if not isinstance(self.data, CommentedMap):
            return

        source_lines = source_text.splitlines(keepends=True)
        entries = []
        for key in self.data:
            location = self.data.lc.key(key)
            if location is not None:
                entries.append((location[0], key))

        for index, (start, key) in enumerate(entries):
            end = entries[index + 1][0] if index + 1 < len(entries) else len(source_lines)
            self._source_top_level_blocks[key] = ''.join(source_lines[start:end])
            self._top_level_snapshots[key] = deepcopy(self.data[key])

    @staticmethod
    def _document_source_spans(source_text: str) -> List[Tuple[int, int, int, int]]:
        """Return parser-backed source spans and renderable document bodies.

        A separator-looking line can occur inside a block scalar or quoted
        scalar, so textual separator matching cannot safely model a stream.
        Ruamel's document events supply one ``DocumentStartEvent`` per value
        produced by ``load_all()``, including an implicit first document and
        empty/trailing documents.  Source around explicit start/end markers is
        retained outside the renderable body, keeping directives, comments,
        and stream framing byte-for-byte intact when another document changes.
        """
        parser = YAML(typ="rt")
        try:
            events = list(parser.parse(source_text))
        except Exception:
            # ``load_all`` already raises syntax errors during ``load``. This
            # defensive fallback lets ``render`` refuse unsafe splicing rather
            # than guessing should parser state ever be unavailable.
            return []

        starts = [event for event in events if isinstance(event, DocumentStartEvent)]
        ends = [event for event in events if isinstance(event, DocumentEndEvent)]
        if len(starts) != len(ends):
            return []

        spans = []
        source_length = len(source_text)
        for index, start_event in enumerate(starts):
            start_mark = start_event.start_mark.index
            end = starts[index + 1].start_mark.index if index + 1 < len(starts) else source_length
            start = 0 if index == 0 else start_mark
            content_start = start

            if start_event.explicit:
                # Keep an explicit marker (and any marker-line comment) in
                # the source prefix. ``end_mark`` is immediately after `---`,
                # so advance through its physical line for both LF and CRLF.
                newline = source_text.find("\n", start_event.end_mark.index)
                content_start = source_length if newline == -1 else newline + 1

            content_end = end
            end_event = ends[index]
            if end_event.explicit:
                content_end = end_event.start_mark.index

            if not (start <= content_start <= content_end <= end):
                return []
            spans.append((start, end, content_start, content_end))
        return spans

    def _capture_sequence_lengths(self, value: Any) -> None:
        """Record loaded sequences so appended final items can own file suffixes."""
        if isinstance(value, CommentedSeq):
            self._sequence_lengths[id(value)] = len(value)
            for item in value:
                self._capture_sequence_lengths(item)
        elif isinstance(value, dict):
            for item in value.values():
                self._capture_sequence_lengths(item)

    def _move_appended_sequence_trailing_newlines(self) -> None:
        """Detach a file-ending blank-line token from an item no longer last.

        ruamel 0.19.1 stores a trailing blank line on the last scalar of a
        sequence item. When callers append directly to that sequence, the
        token is otherwise emitted between the old and new items. The writer
        owns the file suffix, so remove newline-only tokens here and append the
        captured suffix after serializing the complete document.
        """
        self._remove_displaced_sequence_suffix(self.data)

    def _remove_displaced_sequence_suffix(self, value: Any) -> None:
        if isinstance(value, CommentedSeq):
            original_length = self._sequence_lengths.get(id(value))
            if original_length and len(value) > original_length:
                self._remove_newline_only_comments(value[original_length - 1])
            for item in value:
                self._remove_displaced_sequence_suffix(item)
        elif isinstance(value, dict):
            for item in value.values():
                self._remove_displaced_sequence_suffix(item)

    @staticmethod
    def _remove_newline_only_comments(value: Any) -> None:
        """Clear ruamel comment tokens that contain only physical line breaks."""
        if not isinstance(value, CommentedMap):
            return
        for comment_parts in value.ca.items.values():
            for index, comment in enumerate(comment_parts):
                if comment is not None and re.fullmatch(r'(?:\r\n|\n)+', comment.value):
                    comment_parts[index] = None

    def _restore_unchanged_top_level_blocks(self, text: str) -> str:
        """Retain original blocks that ruamel would reindent after another edit."""
        if not isinstance(self.data, CommentedMap) or not self._source_top_level_blocks:
            return text

        block_starts = []
        search_from = 0
        for key in self.data:
            pattern = re.compile(r'^' + re.escape(str(key)) + r'\s*:', re.MULTILINE)
            match = pattern.search(text, search_from)
            if match is None:
                return text
            block_starts.append((match.start(), key))
            search_from = match.end()

        replacements = []
        for index, (start, key) in enumerate(block_starts):
            source_block = self._source_top_level_blocks.get(key)
            if source_block is None:
                continue
            if key not in self.data or self.data[key] != self._top_level_snapshots.get(key):
                continue
            end = block_starts[index + 1][0] if index + 1 < len(block_starts) else len(text)
            if end < len(text) and not re.search(r'(?:\r\n|\n)\Z', source_block):
                source_block += (self._format_profile or FormatProfile()).line_ending
            replacements.append((start, end, source_block))

        for start, end, source_block in reversed(replacements):
            text = text[:start] + source_block + text[end:]
        return text

    @classmethod
    def _detect_format_profile(cls, source_text: str, has_bom: bool) -> FormatProfile:
        """Detect a profile; common layouts win when a source mixes styles."""
        crlf_count = source_text.count('\r\n')
        bare_lf_count = len(re.findall(r'(?<!\r)\n', source_text))
        line_ending = '\r\n' if crlf_count > bare_lf_count else '\n'
        trailing_match = re.search(r'(?:\r\n|\n)+\Z', source_text)
        trailing_suffix = trailing_match.group(0) if trailing_match else ''

        mapping_indent = cls._detect_mapping_indent(source_text)
        sequence_indent, sequence_offset = cls._detect_sequence_indent(source_text)
        explicit_start = cls._has_explicit_start(source_text)
        return FormatProfile(
            mapping_indent=mapping_indent,
            sequence_indent=sequence_indent,
            sequence_offset=sequence_offset,
            line_ending=line_ending,
            trailing_newline_suffix=trailing_suffix,
            has_bom=has_bom,
            explicit_start=explicit_start,
        )

    @staticmethod
    def _most_common(values: List[int], default: int) -> int:
        return Counter(values).most_common(1)[0][0] if values else default

    @classmethod
    def _detect_mapping_indent(cls, source_text: str) -> int:
        lines = source_text.splitlines()
        steps = []
        parent_pattern = re.compile(r'^( *)(?:[^\s#][^:]*):\s*(?:#.*)?$')
        child_pattern = re.compile(r'^( *)(?:[^\s#][^:]*):')
        for index, line in enumerate(lines):
            parent = parent_pattern.match(line)
            if not parent:
                continue
            parent_column = len(parent.group(1))
            for child_line in lines[index + 1:]:
                if not child_line.strip() or child_line.lstrip().startswith('#'):
                    continue
                child = child_pattern.match(child_line)
                if child and len(child.group(1)) > parent_column:
                    steps.append(len(child.group(1)) - parent_column)
                break
        return cls._most_common(steps, 2)

    @classmethod
    def _detect_sequence_indent(cls, source_text: str) -> tuple:
        lines = source_text.splitlines()
        layouts = []
        parent_pattern = re.compile(r'^( *)(?:[^\s#][^:]*):\s*(?:#.*)?$')
        sequence_pattern = re.compile(r'^( *)(-)\s+')
        for index, line in enumerate(lines):
            parent = parent_pattern.match(line)
            if not parent:
                continue
            parent_column = len(parent.group(1))
            for item_line in lines[index + 1:]:
                if not item_line.strip() or item_line.lstrip().startswith('#'):
                    continue
                item = sequence_pattern.match(item_line)
                if item:
                    dash_column = len(item.group(1))
                    content_column = dash_column + 2
                    if dash_column >= parent_column:
                        layouts.append((content_column - parent_column, dash_column - parent_column))
                break
        if not layouts:
            return 2, 0
        sequence_indent, sequence_offset = Counter(layouts).most_common(1)[0][0]
        if sequence_indent < sequence_offset + 2:
            return 2, 0
        return sequence_indent, sequence_offset

    @staticmethod
    def _has_explicit_start(source_text: str) -> bool:
        for line in source_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            return stripped == '---'
        return False
    
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
    
    def _resolve_document(
        self,
        parts: List[str],
        doc: Optional[int],
        for_insert: bool = False,
    ) -> int:
        """Select an explicit or default document for a path operation."""
        if doc is not None:
            if doc < 0 or doc >= len(self.documents):
                maximum = len(self.documents) - 1
                raise ValueError(f"Document index {doc} out of range (0..{maximum})")
            return doc
        if len(self.documents) <= 1:
            return 0
        if parts:
            key = parts[0]
            for index in range(len(self.documents) - 1, -1, -1):
                document = self.documents[index]
                if isinstance(document, dict) and key in document:
                    return index
        if for_insert or not parts:
            for index in range(len(self.documents) - 1, -1, -1):
                if isinstance(self.documents[index], dict):
                    return index
        raise KeyError(f"No mapping document defines path: {'.'.join(parts)}")

    def find_document(self, key: str) -> Optional[int]:
        """Return the last mapping document that defines top-level ``key``."""
        if not self._is_loaded:
            raise ValueError("Call load() before reading data")
        for index in range(len(self.documents) - 1, -1, -1):
            document = self.documents[index]
            if isinstance(document, dict) and key in document:
                return index
        return None

    def _navigate_to_parent(
        self,
        parts: List[str],
        root: Optional[Any] = None,
    ) -> Union[dict, list]:
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
        current = self.data if root is None else root
        
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
    
    def _get_value_from_document(self, parts: List[str], document_index: int) -> Any:
        """Traverse ``parts`` in one already-selected document."""
        current = self.documents[document_index]
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

    def get_value(self, path: str, doc: Optional[int] = None) -> Any:
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
        document_index = self._resolve_document(parts, doc)
        return self._get_value_from_document(parts, document_index)
