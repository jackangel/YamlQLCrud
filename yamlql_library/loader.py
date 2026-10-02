import yaml
from pathlib import Path
from typing import Any, Dict
import re


class _ApplicationTagSafeLoader(yaml.SafeLoader):
    """SafeLoader variant that accepts application-defined YAML tags."""


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
        with open(self.file_path, 'r', encoding='utf-8') as f:
            content = f.read()

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
        documents = list(yaml.load_all(processed_content, Loader=_ApplicationTagSafeLoader))

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

def load_yaml(file_path: str) -> Dict[str, Any]:
    """
    A convenience function to load a YAML file.

    Args:
        file_path: The path to the YAML file as a string.

    Returns:
        A dictionary representing the YAML content.
    """
    return YamlLoader(Path(file_path)).load() 