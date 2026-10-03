"""Compatibility guard for the ruamel.yaml private APIs used by ``YamlWriter``."""

from __future__ import annotations

from importlib import metadata
import re
from typing import Tuple


CERTIFIED_RANGE = ((0, 18, 0), (0, 20, 0))
_CERTIFIED_REQUIREMENT = "ruamel.yaml>=0.18.0,<0.20"
_compatibility_cache = None


class YamlWriterCompatibilityError(RuntimeError):
    """Raised when ruamel.yaml cannot safely support ``YamlWriter``."""


def _installed_version() -> str:
    """Return the installed ruamel.yaml distribution version."""
    try:
        return metadata.version("ruamel.yaml")
    except metadata.PackageNotFoundError:
        from ruamel.yaml import __version__

        return __version__


def _capability_probe() -> None:
    """Exercise the round-trip metadata APIs required by ``YamlWriter``."""
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    yaml.width = 2 ** 31 - 1
    yaml.indent(mapping=2, sequence=2, offset=0)

    document = yaml.load(
        "defaults: &defaults\n"
        "  # preserved comment\n"
        "  name: example # inline comment\n"
        "  values:\n"
        "    - one\n"
        "item:\n"
        "  <<: *defaults\n"
        "  name: override\n"
        "flow: { enabled: true }\n"
    )
    defaults = document["defaults"]
    values = defaults["values"]
    item = document["item"]
    flow = document["flow"]

    if not defaults.ca.items["name"][2].value:
        raise AttributeError("round-trip node has no inline comment metadata")
    if document.lc.key("defaults") is None or document.lc.value("defaults") is None:
        raise AttributeError("round-trip mapping has no key/value line-column metadata")
    if defaults.lc.key("name") is None or defaults.lc.value("name") is None:
        raise AttributeError("round-trip nested mapping has no line-column metadata")
    if values.lc.item(0) is None:
        raise AttributeError("round-trip sequence has no item line-column metadata")
    if defaults.anchor.value != "defaults":
        raise AttributeError("round-trip node has no anchor metadata")
    merge = item.merge
    merge_entries = getattr(merge, "value", merge)
    if not merge_entries:
        raise AttributeError("round-trip mapping has no merge metadata")
    if not flow.fa.flow_style():
        raise AttributeError("round-trip mapping has no flow-style metadata")


def _parse_version(version: str) -> Tuple[int, int, int]:
    """Parse the leading numeric components of a ruamel.yaml version."""
    match = re.match(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?", version)
    if match is None:
        raise ValueError("version has no leading numeric components")
    return tuple(int(component or 0) for component in match.groups())


def _compatibility_error(installed_version: str, reason: str) -> YamlWriterCompatibilityError:
    return YamlWriterCompatibilityError(
        f"ruamel.yaml compatibility check failed for installed version "
        f"{installed_version!r}: {reason}. YamlWriter requires "
        f"{_CERTIFIED_REQUIREMENT}; install a certified ruamel.yaml version "
        "and retry."
    )


def check_ruamel_compatibility() -> None:
    """Validate the installed ruamel.yaml version and required APIs once."""
    global _compatibility_cache

    if _compatibility_cache is not None:
        return

    installed_version = _installed_version()
    try:
        parsed_version = _parse_version(installed_version)
    except (TypeError, ValueError) as error:
        raise _compatibility_error(
            str(installed_version), f"the version string is not parseable ({error})"
        ) from error

    minimum, maximum = CERTIFIED_RANGE
    if not minimum <= parsed_version < maximum:
        raise _compatibility_error(
            installed_version,
            f"the version is outside the certified range {minimum} through {maximum}",
        )

    try:
        _capability_probe()
    except Exception as error:
        raise _compatibility_error(
            installed_version, f"the required round-trip capability probe failed ({error})"
        ) from error

    _compatibility_cache = True
