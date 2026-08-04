"""Derives operationId, summary and tag names for generated operations.

SpecFormula matches operations by their ``summary`` string, so summaries must be
unique and stable. Nothing in the Java source carries a human summary, therefore
every summary is either

  * curated - taken from ``summaries.yml`` keyed by ``ClassName.methodName``, or
  * generated - ``"<Humanised method name> (<ControllerName>)"``

Generated summaries are marked with ``x-summary-source: generated`` in the spec
so a human can find everything still awaiting curation.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

CAMEL_TOKEN = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+")

#: Method-name prefixes rendered as a nicer leading verb.
VERB_WORDS = {
    "get": "Get",
    "find": "Find",
    "save": "Save",
    "create": "Create",
    "update": "Update",
    "delete": "Delete",
    "generate": "Generate",
    "export": "Export",
    "validate": "Validate",
}


def humanise(identifier: str) -> str:
    tokens = CAMEL_TOKEN.findall(identifier)
    if not tokens:
        return identifier
    words = [tokens[0]]
    words.extend(token if token.isupper() else token.lower() for token in tokens[1:])
    first = words[0]
    words[0] = VERB_WORDS.get(first.lower(), first[:1].upper() + first[1:])
    return " ".join(words)


def tag_for_module(module: str) -> str:
    tokens = re.split(r"[-_]", module)
    return "".join(token[:1].upper() + token[1:] for token in tokens if token)


def generated_summary(controller_name: str, method_name: str) -> str:
    return f"{humanise(method_name)} ({controller_name})"


def build_operation_ids(entries: List[Tuple[str, str, str]]) -> Dict[str, str]:
    """Map endpoint keys to unique operationIds.

    ``entries`` is a list of ``(endpoint_key, controller_name, method_name)``.
    A bare method name is preferred; collisions fall back to
    ``controllerNameMethodName``, then to a numeric suffix.
    """
    by_method: Dict[str, List[Tuple[str, str]]] = {}
    for key, controller, method_name in entries:
        by_method.setdefault(method_name, []).append((key, controller))

    assigned: Dict[str, str] = {}
    used: set = set()
    for method_name, group in sorted(by_method.items()):
        if len(group) == 1 and method_name not in used:
            assigned[group[0][0]] = method_name
            used.add(method_name)
            continue
        for key, controller in group:
            candidate = (
                controller[:1].lower() + controller[1:] + method_name[:1].upper() + method_name[1:]
            )
            if candidate in used:
                suffix = 2
                while f"{candidate}{suffix}" in used:
                    suffix += 1
                candidate = f"{candidate}{suffix}"
            assigned[key] = candidate
            used.add(candidate)
    return assigned


def ensure_unique_summaries(summaries: Dict[str, str]) -> Dict[str, str]:
    """Disambiguate any summary collision by appending the source key."""
    counts: Dict[str, int] = {}
    for value in summaries.values():
        counts[value] = counts.get(value, 0) + 1
    result: Dict[str, str] = {}
    seen: Dict[str, int] = {}
    for key, value in summaries.items():
        if counts[value] == 1:
            result[key] = value
            continue
        seen[value] = seen.get(value, 0) + 1
        result[key] = f"{value} #{seen[value]}"
    return result
