"""Builds ``components/schemas`` from project DTO classes.

Handles generic substitution (``GridResponse<R>`` with ``R = ProductDTO``),
nested DTOs, collections, maps and enums. Types that cannot be resolved degrade
to ``type: object`` and are recorded so the report can name them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from . import javatypes
from .javaindex import JavaClass, JavaIndex, TypeRef

_NAME_SAFE = re.compile(r"[^A-Za-z0-9._-]")

#: Field modifiers that keep a field out of the JSON payload.
_SKIPPED_MODIFIERS = {"static", "transient"}


@dataclass
class UnresolvedType:
    type_name: str
    context: str
    reason: str


@dataclass
class SchemaRegistry:
    index: JavaIndex
    schemas: Dict[str, Dict[str, object]] = field(default_factory=dict)
    unresolved: List[UnresolvedType] = field(default_factory=list)
    opaque: List[UnresolvedType] = field(default_factory=list)
    _building: Set[str] = field(default_factory=set)
    _origin: Dict[str, str] = field(default_factory=dict)

    # -- public API --------------------------------------------------------- #

    def schema_for(
        self,
        type_ref: Optional[TypeRef],
        context: Optional[JavaClass],
        origin: str,
        subst: Optional[Dict[str, TypeRef]] = None,
    ) -> Dict[str, object]:
        """Return an OpenAPI schema (possibly a ``$ref``) for ``type_ref``."""
        if type_ref is None:
            return {"type": "object"}
        resolved = type_ref.substitute(subst or {})

        if javatypes.is_optional(resolved):
            inner = javatypes.element_type(resolved)
            return self.schema_for(inner, context, origin, subst)

        if javatypes.is_collection(resolved):
            inner = javatypes.element_type(resolved)
            if inner is None:
                self._record_unresolved(resolved.render(), origin, "raw collection without type argument")
                return {"type": "array", "items": {"type": "object"}}
            return {"type": "array", "items": self.schema_for(inner, context, origin, subst)}

        if javatypes.is_map(resolved):
            value = javatypes.map_value_type(resolved)
            schema: Dict[str, object] = {"type": "object"}
            if value is not None:
                value_schema = self.schema_for(value, context, origin, subst)
                if value_schema != {"type": "object"}:
                    schema["additionalProperties"] = value_schema
                else:
                    schema["additionalProperties"] = True
            else:
                schema["additionalProperties"] = True
            return schema

        if resolved.name in javatypes.OPAQUE_TYPES:
            # Structurally unknowable by design: free-form JSON containers,
            # java.lang.Object, and qcadoo's dynamic Entity model.
            self._record(
                self.opaque,
                resolved.render(),
                origin,
                "type carries no static structure; serialised shape is free-form",
            )
            return {"type": "object"}

        leaf = javatypes.leaf_schema(resolved)
        if leaf is not None:
            return dict(leaf)

        target = self.index.resolve(resolved.name, context)
        if target is None:
            ambiguous = self.index.resolve_ambiguity(resolved.name)
            reason = (
                f"ambiguous simple name ({len(ambiguous)} candidate files)"
                if len(ambiguous) > 1
                else "class not found in source roots (external or JDK type)"
            )
            self._record_unresolved(resolved.render(), origin, reason)
            return {"type": "object"}

        if target.kind == "enum":
            name = self._register_enum(target)
            return {"$ref": f"#/components/schemas/{name}"}

        name = self._register_class(target, resolved, context, origin)
        return {"$ref": f"#/components/schemas/{name}"}

    # -- internals ---------------------------------------------------------- #

    @staticmethod
    def _record(bucket: List[UnresolvedType], type_name: str, origin: str, reason: str) -> None:
        for existing in bucket:
            if existing.type_name == type_name and existing.context == origin:
                return
        bucket.append(UnresolvedType(type_name, origin, reason))

    def _record_unresolved(self, type_name: str, origin: str, reason: str) -> None:
        self._record(self.unresolved, type_name, origin, reason)

    def _register_enum(self, target: JavaClass) -> str:
        name = _NAME_SAFE.sub("", target.simple_name)
        if name not in self.schemas:
            self.schemas[name] = {
                "type": "string",
                "description": f"Java enum {target.fqn}",
                "enum": list(target.enum_constants),
            }
            self._origin[name] = target.fqn
        return name

    def _schema_name(self, target: JavaClass, type_ref: TypeRef) -> str:
        parts = [target.simple_name]
        for arg in type_ref.args:
            parts.append(arg.name if arg is not None else "Any")
        return _NAME_SAFE.sub("", "".join(parts))

    def _register_class(
        self,
        target: JavaClass,
        type_ref: TypeRef,
        context: Optional[JavaClass],
        origin: str,
    ) -> str:
        name = self._schema_name(target, type_ref)
        if name in self.schemas or name in self._building:
            return name

        self._building.add(name)
        try:
            subst: Dict[str, TypeRef] = {}
            for position, param in enumerate(target.type_params):
                if position < len(type_ref.args) and type_ref.args[position] is not None:
                    subst[param] = type_ref.args[position]

            properties: Dict[str, Dict[str, object]] = {}
            for owner, member in self._all_fields(target):
                if member.modifiers & _SKIPPED_MODIFIERS:
                    continue
                if member.name in properties:
                    continue
                properties[member.name] = self.schema_for(
                    member.type, owner, f"{target.simple_name}.{member.name}", subst
                )

            schema: Dict[str, object] = {
                "type": "object",
                "description": f"Generated from {target.fqn}",
            }
            if properties:
                schema["properties"] = properties
            else:
                schema["description"] += (
                    " (no serialisable fields found; shape is opaque to static analysis)"
                )
                self._record_unresolved(
                    target.simple_name, origin, "no fields declared (interface or getter-only DTO)"
                )
            self.schemas[name] = schema
            self._origin[name] = target.fqn
        finally:
            self._building.discard(name)
        return name

    def _all_fields(self, target: JavaClass):
        """Fields of ``target`` plus those inherited from project superclasses."""
        seen: Set[str] = set()
        current: Optional[JavaClass] = target
        depth = 0
        while current is not None and depth < 8:
            for member in current.fields:
                if member.name not in seen:
                    seen.add(member.name)
                    yield current, member
            if current.extends is None:
                break
            parent = self.index.resolve(current.extends.name, current)
            if parent is None or parent.fqn == current.fqn:
                break
            current = parent
            depth += 1

    def sorted_schemas(self) -> Dict[str, Dict[str, object]]:
        return {key: self.schemas[key] for key in sorted(self.schemas)}
