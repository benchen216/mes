"""Lazy Java source index built on javalang AST parsing.

Only files that are actually needed get parsed: the index first walks the tree
building a cheap ``simple name -> path`` map from filenames, then parses a file
the first time a class in it is requested. Java requires the public top-level
type to match the filename, so filename-based lookup is reliable for the DTO /
controller resolution this tool performs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import javalang

SRC_MARKER = os.path.join("src", "main", "java")


@dataclass(frozen=True)
class TypeRef:
    """A resolved-as-written Java type reference (simple name, not FQN)."""

    name: str
    args: Tuple[Optional["TypeRef"], ...] = ()
    dims: int = 0
    primitive: bool = False

    def substitute(self, mapping: Dict[str, "TypeRef"]) -> "TypeRef":
        """Replace type variables (``T``, ``R``) using ``mapping``."""
        if not mapping:
            return self
        replacement = mapping.get(self.name)
        if replacement is not None and not self.args:
            return TypeRef(
                name=replacement.name,
                args=replacement.args,
                dims=max(self.dims, replacement.dims),
                primitive=replacement.primitive,
            )
        if not self.args:
            return self
        new_args = tuple(a.substitute(mapping) if a is not None else None for a in self.args)
        return TypeRef(name=self.name, args=new_args, dims=self.dims, primitive=self.primitive)

    def render(self) -> str:
        text = self.name
        if self.args:
            inner = ", ".join(a.render() if a is not None else "?" for a in self.args)
            text = f"{text}<{inner}>"
        return text + "[]" * self.dims


@dataclass
class Annotation:
    name: str
    values: Dict[str, object] = field(default_factory=dict)

    def value(self, key: str, default=None):
        return self.values.get(key, default)

    def first_of(self, *keys, default=None):
        for key in keys:
            if key in self.values:
                return self.values[key]
        return default


@dataclass
class JavaParam:
    name: str
    type: Optional[TypeRef]
    annotations: List[Annotation] = field(default_factory=list)

    def annotation(self, name: str) -> Optional[Annotation]:
        return _find_annotation(self.annotations, name)


@dataclass
class JavaMethod:
    name: str
    return_type: Optional[TypeRef]
    params: List[JavaParam] = field(default_factory=list)
    annotations: List[Annotation] = field(default_factory=list)
    modifiers: frozenset = frozenset()
    line: int = 0

    def annotation(self, name: str) -> Optional[Annotation]:
        return _find_annotation(self.annotations, name)


@dataclass
class JavaField:
    name: str
    type: Optional[TypeRef]
    modifiers: frozenset = frozenset()
    annotations: List[Annotation] = field(default_factory=list)

    def annotation(self, name: str) -> Optional[Annotation]:
        return _find_annotation(self.annotations, name)


@dataclass
class JavaClass:
    simple_name: str
    package: str
    file: str
    kind: str  # class | interface | enum
    modifiers: frozenset = frozenset()
    type_params: List[str] = field(default_factory=list)
    extends: Optional[TypeRef] = None
    implements: List[TypeRef] = field(default_factory=list)
    annotations: List[Annotation] = field(default_factory=list)
    fields: List[JavaField] = field(default_factory=list)
    methods: List[JavaMethod] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    wildcard_imports: List[str] = field(default_factory=list)
    enum_constants: List[str] = field(default_factory=list)

    @property
    def fqn(self) -> str:
        return f"{self.package}.{self.simple_name}" if self.package else self.simple_name

    def annotation(self, name: str) -> Optional[Annotation]:
        return _find_annotation(self.annotations, name)


def _find_annotation(annotations: Sequence[Annotation], name: str) -> Optional[Annotation]:
    for ann in annotations:
        if ann.name == name or ann.name.endswith("." + name):
            return ann
    return None


# --------------------------------------------------------------------------- #
# javalang node -> plain python conversion
# --------------------------------------------------------------------------- #


def _annotation_value(node) -> object:
    """Normalise a javalang annotation element into a plain Python value."""
    if node is None:
        return True  # marker annotation element, e.g. @ResponseBody
    cls = type(node).__name__
    if cls == "Literal":
        raw = node.value
        if isinstance(raw, str) and len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
            return raw[1:-1]
        return raw
    if cls == "MemberReference":
        qualifier = getattr(node, "qualifier", None)
        member = getattr(node, "member", None)
        return f"{qualifier}.{member}" if qualifier else member
    if cls == "ElementArrayValue":
        return [_annotation_value(v) for v in (node.values or [])]
    if cls == "Annotation":
        return _annotation_values(node)
    if cls == "ClassReference":
        target = getattr(node, "type", None)
        return getattr(target, "name", None)
    if cls == "BinaryOperation":
        # constant string concatenation: "a" + "b"
        left = _annotation_value(node.operandl)
        right = _annotation_value(node.operandr)
        if isinstance(left, str) and isinstance(right, str) and node.operator == "+":
            return left + right
        return f"{left}{node.operator}{right}"
    return getattr(node, "value", None) or getattr(node, "member", None)


def _annotation_values(ann) -> Dict[str, object]:
    element = ann.element
    if element is None:
        return {}
    if isinstance(element, list):
        return {pair.name: _annotation_value(pair.value) for pair in element}
    return {"value": _annotation_value(element)}


def _to_annotation(ann) -> Annotation:
    return Annotation(name=ann.name, values=_annotation_values(ann))


def _to_type_ref(node) -> Optional[TypeRef]:
    if node is None:
        return None
    cls = type(node).__name__
    if cls == "BasicType":
        return TypeRef(name=node.name, dims=len(node.dimensions or []), primitive=True)
    args: List[Optional[TypeRef]] = []
    for arg in (getattr(node, "arguments", None) or []):
        arg_type = getattr(arg, "type", None)
        if arg_type is None:
            args.append(None)  # bare wildcard '?'
        else:
            # '? extends Foo' / '? super Foo' collapse to Foo. For 'super' this is
            # imprecise, but the serialised payload is still at least Foo-shaped.
            args.append(_to_type_ref(arg_type))
    # inner types (Outer.Inner) keep only the last segment
    name = node.name
    sub = getattr(node, "sub_type", None)
    while sub is not None:
        name = sub.name
        if not args:
            args = [_to_type_ref(a.type) if a.type else None for a in (sub.arguments or [])]
        sub = getattr(sub, "sub_type", None)
    return TypeRef(name=name, args=tuple(args), dims=len(node.dimensions or []))


def _method_of(node) -> JavaMethod:
    params = [
        JavaParam(
            name=p.name,
            type=_to_type_ref(p.type),
            annotations=[_to_annotation(a) for a in (p.annotations or [])],
        )
        for p in (node.parameters or [])
    ]
    position = getattr(node, "position", None)
    return JavaMethod(
        name=node.name,
        return_type=_to_type_ref(node.return_type),
        params=params,
        annotations=[_to_annotation(a) for a in (node.annotations or [])],
        modifiers=frozenset(node.modifiers or ()),
        line=position.line if position else 0,
    )


def _fields_of(node) -> List[JavaField]:
    out: List[JavaField] = []
    for fd in getattr(node, "fields", None) or []:
        base = _to_type_ref(fd.type)
        annotations = [_to_annotation(a) for a in (fd.annotations or [])]
        for declarator in fd.declarators:
            extra = len(declarator.dimensions or [])
            ftype = base
            if base is not None and extra:
                ftype = TypeRef(base.name, base.args, base.dims + extra, base.primitive)
            out.append(
                JavaField(
                    name=declarator.name,
                    type=ftype,
                    modifiers=frozenset(fd.modifiers or ()),
                    annotations=annotations,
                )
            )
    return out


def _class_of(node, package: str, path: str, imports: List[str], wildcards: List[str]) -> JavaClass:
    kind = {
        "ClassDeclaration": "class",
        "InterfaceDeclaration": "interface",
        "EnumDeclaration": "enum",
        "AnnotationDeclaration": "annotation",
    }.get(type(node).__name__, "class")

    extends_node = getattr(node, "extends", None)
    if kind == "interface" and isinstance(extends_node, list):
        implements = [_to_type_ref(e) for e in extends_node]
        extends = None
    else:
        extends = _to_type_ref(extends_node) if extends_node is not None else None
        implements = [_to_type_ref(i) for i in (getattr(node, "implements", None) or [])]

    enum_constants: List[str] = []
    if kind == "enum":
        body = getattr(node, "body", None)
        for const in (getattr(body, "constants", None) or []):
            enum_constants.append(const.name)

    return JavaClass(
        simple_name=node.name,
        package=package,
        file=path,
        kind=kind,
        modifiers=frozenset(node.modifiers or ()),
        type_params=[tp.name for tp in (getattr(node, "type_parameters", None) or [])],
        extends=extends,
        implements=[i for i in implements if i is not None],
        annotations=[_to_annotation(a) for a in (node.annotations or [])],
        fields=_fields_of(node),
        methods=[_method_of(m) for m in (getattr(node, "methods", None) or [])],
        imports=imports,
        wildcard_imports=wildcards,
        enum_constants=enum_constants,
    )


# --------------------------------------------------------------------------- #
# index
# --------------------------------------------------------------------------- #


class JavaIndex:
    """Filename-keyed, lazily-parsed index over one or more source roots."""

    def __init__(self, roots: Sequence[str]):
        self.roots = list(roots)
        self._paths_by_simple_name: Dict[str, List[str]] = {}
        self._classes_by_path: Dict[str, List[JavaClass]] = {}
        self._parse_failures: Dict[str, str] = {}
        self._scan()

    # -- discovery ---------------------------------------------------------- #

    def _scan(self) -> None:
        for root in self.roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in (".git", "target", "node_modules")]
                if SRC_MARKER not in dirpath:
                    continue
                for name in filenames:
                    if not name.endswith(".java"):
                        continue
                    self._paths_by_simple_name.setdefault(name[:-5], []).append(
                        os.path.join(dirpath, name)
                    )

    @property
    def parse_failures(self) -> Dict[str, str]:
        return dict(self._parse_failures)

    def all_paths(self) -> List[str]:
        return sorted(p for paths in self._paths_by_simple_name.values() for p in paths)

    # -- parsing ------------------------------------------------------------ #

    def classes_in(self, path: str) -> List[JavaClass]:
        cached = self._classes_by_path.get(path)
        if cached is not None:
            return cached
        result: List[JavaClass] = []
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                tree = javalang.parse.parse(handle.read())
        except Exception as exc:  # noqa: BLE001 - report, never crash the run
            self._parse_failures[path] = f"{type(exc).__name__}: {exc}"
            self._classes_by_path[path] = result
            return result

        package = tree.package.name if tree.package else ""
        imports, wildcards = [], []
        for imp in tree.imports or []:
            if imp.static:
                continue
            if imp.wildcard:
                wildcards.append(imp.path)
            else:
                imports.append(imp.path)

        for type_node in tree.types or []:
            result.append(_class_of(type_node, package, path, imports, wildcards))
            body = getattr(type_node, "body", None)
            members = body if isinstance(body, list) else getattr(body, "declarations", None) or []
            for member in members:
                if type(member).__name__ in (
                    "ClassDeclaration",
                    "InterfaceDeclaration",
                    "EnumDeclaration",
                ):
                    result.append(_class_of(member, package, path, imports, wildcards))

        self._classes_by_path[path] = result
        return result

    def class_at(self, path: str, simple_name: str) -> Optional[JavaClass]:
        for candidate in self.classes_in(path):
            if candidate.simple_name == simple_name:
                return candidate
        return None

    # -- resolution --------------------------------------------------------- #

    def _path_for_fqn(self, fqn: str) -> Optional[str]:
        simple = fqn.rsplit(".", 1)[-1]
        expected_tail = os.path.join(*fqn.split(".")) + ".java"
        for path in self._paths_by_simple_name.get(simple, []):
            if path.endswith(expected_tail):
                return path
        return None

    def resolve(self, name: str, context: Optional[JavaClass]) -> Optional[JavaClass]:
        """Resolve a simple type name as written inside ``context``.

        Order: explicit import -> same package -> wildcard import -> unique
        global filename match. Ambiguous global matches return ``None``.
        """
        if not name:
            return None
        simple = name.rsplit(".", 1)[-1]

        if context is not None:
            for imported in context.imports:
                if imported.rsplit(".", 1)[-1] == simple:
                    path = self._path_for_fqn(imported)
                    if path:
                        found = self.class_at(path, simple)
                        if found:
                            return found

            same_dir = os.path.join(os.path.dirname(context.file), simple + ".java")
            if os.path.exists(same_dir):
                found = self.class_at(same_dir, simple)
                if found:
                    return found

            # nested type declared in the same file
            for candidate in self.classes_in(context.file):
                if candidate.simple_name == simple:
                    return candidate

            for pkg in context.wildcard_imports:
                path = self._path_for_fqn(f"{pkg}.{simple}")
                if path:
                    found = self.class_at(path, simple)
                    if found:
                        return found

        paths = self._paths_by_simple_name.get(simple, [])
        if len(paths) == 1:
            return self.class_at(paths[0], simple)
        return None

    def resolve_ambiguity(self, name: str) -> List[str]:
        return list(self._paths_by_simple_name.get(name.rsplit(".", 1)[-1], []))
