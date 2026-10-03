import yaml
from yaml.composer import ComposerError
from yaml.events import AliasEvent, MappingStartEvent, ScalarEvent, SequenceStartEvent
from pathlib import Path
from typing import Any, Dict, List, Tuple
import re


class _ApplicationTagSafeLoader(yaml.SafeLoader):
    """SafeLoader variant that accepts application-defined YAML tags.

    PyYAML resets ``self.anchors`` for each document. This override keeps that
    behavior and only permits an anchor name to replace its earlier definition,
    allowing subsequent aliases to resolve to the nearest preceding definition.
    """

    def compose_node(self, parent: Any, index: Any) -> yaml.nodes.Node:
        """Compose a node without PyYAML's duplicate-anchor rejection."""
        if self.check_event(AliasEvent):
            event = self.get_event()
            anchor = event.anchor
            if anchor not in self.anchors:
                raise ComposerError(
                    None,
                    None,
                    "found undefined alias %r" % anchor,
                    event.start_mark,
                )
            return self.anchors[anchor]

        event = self.peek_event()
        anchor = event.anchor
        self.descend_resolver(parent, index)
        if self.check_event(ScalarEvent):
            node = self.compose_scalar_node(anchor)
        elif self.check_event(SequenceStartEvent):
            node = self.compose_sequence_node(anchor)
        elif self.check_event(MappingStartEvent):
            node = self.compose_mapping_node(anchor)
        self.ascend_resolver()
        return node


def _construct_application_tag(
    loader: yaml.SafeLoader, tag_suffix: str, node: yaml.nodes.Node
) -> Any:
    """Construct the plain value behind an application-defined tag."""
    if node.tag.startswith("tag:yaml.org,2002:"):
        # Never allow PyYAML's Python-object tag family through this fallback.
        raise yaml.constructor.ConstructorError(
            None,
            None,
            f"could not determine a constructor for the tag {node.tag!r}",
            node.start_mark,
        )

    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)

    raise yaml.constructor.ConstructorError(
        None,
        None,
        f"could not determine a constructor for the tag {node.tag!r}",
        node.start_mark,
    )


_ApplicationTagSafeLoader.add_multi_constructor("", _construct_application_tag)


class YamlLoader:
    """Loads content from a YAML file, with support for multi-document streams."""

    def __init__(self, file_path: Path):
        """
        Initializes the YamlLoader with the path to the YAML file.

        Args:
            file_path: The path to the YAML file.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"The file {file_path} was not found.")
        self.file_path = file_path

    def load(self) -> Dict[str, Any]:
        """
        Loads YAML content from the file. If it's a multi-document file
        (separated by '---'), it merges all documents into a single dictionary.

        Returns:
            A dictionary representing the (potentially merged) YAML content.
        """
        documents = self._load_documents()

        if not documents:
            return {}
        
        if len(documents) == 1:
            return documents[0]
        
        # Merge all documents into one. This assumes keys are unique across documents
        # or that later documents are intended to override earlier ones.
        merged_data = {}
        for doc in documents:
            if isinstance(doc, dict):
                merged_data.update(doc)
        
        return merged_data

    def load_stream(self) -> List[Tuple[int, str, Any]]:
        """Load every YAML document with its zero-based index and root kind."""
        documents = self._load_documents()
        stream = []
        for index, document in enumerate(documents):
            if isinstance(document, dict):
                kind = "mapping"
            elif isinstance(document, list):
                kind = "list"
            elif document is None:
                kind = "null"
            else:
                kind = "scalar"
            stream.append((index, kind, document))
        return stream

    def _load_documents(self) -> List[Any]:
        with open(self.file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # PyYAML rejects a tab-only otherwise-comment-only stream, while the
        # round-trip writer treats it as one empty document. Keep the read and
        # write sides aligned without inventing a queryable relation.
        if not any(
            line.strip() and not line.lstrip().startswith('#')
            for line in content.splitlines()
        ):
            return [] if not content else [None]

        # Pre-process the content to handle common multi-document formatting issues
        # Replace '------' with '---' and ensure separators are on their own lines
        # Wrap boolean-like keys (on, true, etc.) in quotes to preserve them as strings
        processed_content = re.sub(r'^\s*-{3,}\s*$', '---', content, flags=re.MULTILINE)
        boolean_keys = ['y', 'Y', 'yes', 'Yes', 'YES', 'n', 'N', 'no', 'No', 'NO',
                        'true', 'True', 'TRUE', 'false', 'False', 'FALSE',
                        'on', 'On', 'ON', 'off', 'Off', 'OFF']
        for key in boolean_keys:
            # This regex finds keys at the start of a line and wraps them in quotes.
            processed_content = re.sub(f'^{key}:', f'"{key}":', processed_content, flags=re.MULTILINE)

        # Use the dedicated safe loader to handle multi-document YAML files.
        return list(yaml.load_all(processed_content, Loader=_ApplicationTagSafeLoader))

def load_yaml(file_path: str) -> Dict[str, Any]:
    """
    A convenience function to load a YAML file.

    Args:
        file_path: The path to the YAML file as a string.

    Returns:
        A dictionary representing the YAML content.
    """
    return YamlLoader(Path(file_path)).load() 