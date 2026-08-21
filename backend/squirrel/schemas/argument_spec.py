from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArgumentSpec:
    """
    Declares one accepted keyword argument for an inspection strategy.

    This is the single source of truth for argument metadata — the catalog,
    prompt renderer, and runtime validator all read from here.
    """
    name: str
    type: str                          # human-readable type label, e.g. "str", "float", "list[str]"
    required: bool
    default: Any
    description: str
    possible_values: str               # free-text range/enum description
    value_descriptions: dict[str, str] # concrete value → consequence mapping
    condition: str = ""                # e.g. "only used when method == 'zscore'"