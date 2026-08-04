"""Discovers Spring MVC controllers and extracts their HTTP endpoints.

Targets Spring 3.2 conventions as used by qcadoo MES:
  * ``@Controller`` + ``@ResponseBody`` (no ``@RestController`` exists in this repo)
  * ``@RequestMapping`` only (no ``@GetMapping`` / ``@PostMapping`` shortcuts)
  * endpoints inherited from generic abstract base controllers

Every method that carries ``@RequestMapping`` is either turned into an endpoint
or recorded as an exclusion with a reason - nothing is dropped silently.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import javatypes
from .javaindex import Annotation, JavaClass, JavaIndex, JavaMethod, JavaParam, TypeRef

URI_TEMPLATE_VAR = re.compile(r"\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^}]*)?\}")

MEDIA_TYPE_CONSTANTS = {
    "MediaType.APPLICATION_JSON_VALUE": "application/json",
    "MediaType.APPLICATION_JSON_UTF8_VALUE": "application/json;charset=UTF-8",
    "MediaType.APPLICATION_XML_VALUE": "application/xml",
    "MediaType.TEXT_PLAIN_VALUE": "text/plain",
    "MediaType.TEXT_HTML_VALUE": "text/html",
    "MediaType.APPLICATION_OCTET_STREAM_VALUE": "application/octet-stream",
    "MediaType.APPLICATION_FORM_URLENCODED_VALUE": "application/x-www-form-urlencoded",
    "MediaType.MULTIPART_FORM_DATA_VALUE": "multipart/form-data",
    "MediaType.APPLICATION_PDF_VALUE": "application/pdf",
}

DEFAULT_VERB = "get"


@dataclass
class ParamSpec:
    name: str
    location: str  # path | query | header
    required: bool
    java_type: Optional[TypeRef]
    default: Optional[object] = None


@dataclass
class Endpoint:
    http_method: str
    path: str
    controller: JavaClass
    declaring_class: JavaClass
    method: JavaMethod
    substitution: Dict[str, TypeRef]
    produces: str
    consumes: Optional[str]
    params: List[ParamSpec]
    body_type: Optional[TypeRef]
    return_type: Optional[TypeRef]
    module: str
    inherited: bool
    notes: List[str] = field(default_factory=list)

    @property
    def source(self) -> str:
        return f"{self.declaring_class.simple_name}.{self.method.name}"

    @property
    def key(self) -> str:
        """Stable identity for this endpoint.

        Must include the concrete controller and the path: a single declaring
        class can back many endpoints once inheritance is resolved (seven
        controllers inherit ``BasicLookupController.getRecords``).
        """
        return f"{self.controller.simple_name}.{self.method.name}#{self.http_method}#{self.path}"


@dataclass
class Exclusion:
    controller: str
    method: str
    path: str
    reason: str
    file: str


@dataclass
class ExtractionResult:
    endpoints: List[Endpoint] = field(default_factory=list)
    exclusions: List[Exclusion] = field(default_factory=list)
    controllers: List[JavaClass] = field(default_factory=list)
    unbound_params: List[Tuple[str, str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _as_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _mapping_paths(ann: Optional[Annotation]) -> List[str]:
    if ann is None:
        return [""]
    raw = _as_list(ann.first_of("value", "path"))
    return raw or [""]


def _mapping_verbs(ann: Optional[Annotation]) -> List[str]:
    if ann is None:
        return []
    verbs = []
    for entry in _as_list(ann.value("method")):
        verbs.append(entry.rsplit(".", 1)[-1].lower())
    return verbs


def _media_type(ann: Optional[Annotation], key: str) -> Optional[str]:
    if ann is None:
        return None
    values = _as_list(ann.value(key))
    if not values:
        return None
    first = values[0]
    return MEDIA_TYPE_CONSTANTS.get(first, first)


def join_paths(*segments: str) -> str:
    parts: List[str] = []
    for segment in segments:
        if not segment:
            continue
        parts.extend(piece for piece in segment.split("/") if piece)
    return "/" + "/".join(parts) if parts else "/"


def module_of(path: str) -> str:
    match = re.search(r"mes-plugins[/\\]mes-plugins-([^/\\]+)", path)
    if match:
        return match.group(1)
    if "mes-application" in path:
        return "application"
    return "unknown"


def _param_annotation(param: JavaParam) -> Tuple[Optional[str], Optional[Annotation]]:
    for kind in ("PathVariable", "RequestParam", "RequestBody", "RequestHeader", "ModelAttribute"):
        ann = param.annotation(kind)
        if ann is not None:
            return kind, ann
    return None, None


def _annotated_name(ann: Optional[Annotation], fallback: str) -> str:
    if ann is None:
        return fallback
    named = ann.first_of("value", "name")
    if isinstance(named, list):
        named = named[0] if named else None
    return str(named) if named else fallback


def _truthy(value) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    return None


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #


class ControllerExtractor:
    def __init__(self, index: JavaIndex, rest_prefix: str = "/rest"):
        self.index = index
        self.rest_prefix = rest_prefix

    def extract(self, candidate_paths: List[str]) -> ExtractionResult:
        result = ExtractionResult()
        for path in sorted(candidate_paths):
            for java_class in self.index.classes_in(path):
                if java_class.annotation("Controller") is None:
                    continue
                if "abstract" in java_class.modifiers:
                    continue
                result.controllers.append(java_class)
                self._extract_class(java_class, result)
        return result

    # -- per controller ----------------------------------------------------- #

    def _inheritance_chain(
        self, controller: JavaClass
    ) -> List[Tuple[JavaClass, Dict[str, TypeRef], bool]]:
        """(class, type-variable substitution, is_inherited) from self upward."""
        chain: List[Tuple[JavaClass, Dict[str, TypeRef], bool]] = [(controller, {}, False)]
        current, subst, depth = controller, {}, 0
        while current.extends is not None and depth < 8:
            parent = self.index.resolve(current.extends.name, current)
            if parent is None or parent.fqn == current.fqn:
                break
            parent_subst: Dict[str, TypeRef] = {}
            for position, name in enumerate(parent.type_params):
                args = current.extends.args
                if position < len(args) and args[position] is not None:
                    parent_subst[name] = args[position].substitute(subst)
            chain.append((parent, parent_subst, True))
            current, subst, depth = parent, parent_subst, depth + 1
        return chain

    def _extract_class(self, controller: JavaClass, result: ExtractionResult) -> None:
        class_mapping = controller.annotation("RequestMapping")
        class_paths = _mapping_paths(class_mapping)
        class_verbs = _mapping_verbs(class_mapping)
        class_response_body = controller.annotation("ResponseBody") is not None

        seen_signatures: set = set()
        for declaring, subst, inherited in self._inheritance_chain(controller):
            for method in declaring.methods:
                mapping = method.annotation("RequestMapping")
                if mapping is None:
                    continue
                # Substitute type variables before comparing, so that
                # ResourceLookupController.getRecords(..., ResourceDTO) is seen as an
                # override of BasicLookupController.getRecords(..., R).
                signature = (
                    method.name,
                    tuple(
                        p.type.substitute(subst).render() if p.type else "?" for p in method.params
                    ),
                )
                if signature in seen_signatures:
                    continue  # overridden in a subclass - the subclass version wins
                seen_signatures.add(signature)
                self._extract_method(
                    controller=controller,
                    declaring=declaring,
                    method=method,
                    mapping=mapping,
                    class_paths=class_paths,
                    class_verbs=class_verbs,
                    class_response_body=class_response_body,
                    subst=subst,
                    inherited=inherited,
                    result=result,
                )

    def _extract_method(
        self,
        *,
        controller: JavaClass,
        declaring: JavaClass,
        method: JavaMethod,
        mapping: Annotation,
        class_paths: List[str],
        class_verbs: List[str],
        class_response_body: bool,
        subst: Dict[str, TypeRef],
        inherited: bool,
        result: ExtractionResult,
    ) -> None:
        method_paths = _mapping_paths(mapping)
        full_paths = [
            join_paths(self.rest_prefix, class_path, method_path)
            for class_path in class_paths
            for method_path in method_paths
        ]
        primary_path = full_paths[0]

        notes: List[str] = []
        if len(full_paths) > 1:
            notes.append(
                "multiple @RequestMapping paths declared; extra paths: "
                + ", ".join(full_paths[1:])
            )

        has_response_body = class_response_body or method.annotation("ResponseBody") is not None
        return_type = method.return_type.substitute(subst) if method.return_type else None

        if not has_response_body:
            result.exclusions.append(
                Exclusion(
                    controller.simple_name,
                    method.name,
                    primary_path,
                    "no @ResponseBody - renders a JSP view, not a JSON payload",
                    declaring.file,
                )
            )
            return

        if return_type is not None and return_type.name in javatypes.VIEW_RETURN_TYPES:
            result.exclusions.append(
                Exclusion(
                    controller.simple_name,
                    method.name,
                    primary_path,
                    f"returns {return_type.name} - view resolution, not a JSON payload",
                    declaring.file,
                )
            )
            return

        verbs = _mapping_verbs(mapping) or class_verbs
        if not verbs:
            verbs = [DEFAULT_VERB]
            notes.append(
                "no HTTP method declared on @RequestMapping; Spring maps all verbs - "
                f"defaulted to {DEFAULT_VERB.upper()}"
            )

        declared_produces = _media_type(mapping, "produces")
        produces = declared_produces or "application/json"
        if declared_produces is None:
            notes.append(
                "no 'produces' declared; assumed application/json (Spring content "
                "negotiation with Jackson on the classpath)"
            )
        consumes = _media_type(mapping, "consumes")

        params, body_type, param_notes = self._extract_params(
            declaring, method, subst, primary_path, result
        )
        notes.extend(param_notes)

        for template_var in URI_TEMPLATE_VAR.findall(primary_path):
            if not any(p.location == "path" and p.name == template_var for p in params):
                params.append(
                    ParamSpec(
                        name=template_var,
                        location="path",
                        required=True,
                        java_type=TypeRef("String"),
                    )
                )
                notes.append(
                    f"path variable '{template_var}' present in the URI template but not "
                    "bound by a @PathVariable parameter; typed as string"
                )

        for verb in verbs:
            result.endpoints.append(
                Endpoint(
                    http_method=verb,
                    path=primary_path,
                    controller=controller,
                    declaring_class=declaring,
                    method=method,
                    substitution=subst,
                    produces=produces,
                    consumes=consumes,
                    params=list(params),
                    body_type=body_type,
                    return_type=return_type,
                    module=module_of(controller.file),
                    inherited=inherited,
                    notes=list(notes),
                )
            )

    def _extract_params(
        self,
        declaring: JavaClass,
        method: JavaMethod,
        subst: Dict[str, TypeRef],
        path: str,
        result: ExtractionResult,
    ) -> Tuple[List[ParamSpec], Optional[TypeRef], List[str]]:
        params: List[ParamSpec] = []
        body_type: Optional[TypeRef] = None
        notes: List[str] = []

        for param in method.params:
            kind, ann = _param_annotation(param)
            java_type = param.type.substitute(subst) if param.type else None

            if kind == "RequestBody":
                body_type = java_type
                continue
            if kind == "PathVariable":
                params.append(
                    ParamSpec(
                        name=_annotated_name(ann, param.name),
                        location="path",
                        required=True,
                        java_type=java_type,
                    )
                )
                continue
            if kind == "RequestHeader":
                required = _truthy(ann.value("required")) if ann else None
                params.append(
                    ParamSpec(
                        name=_annotated_name(ann, param.name),
                        location="header",
                        required=True if required is None else required,
                        java_type=java_type,
                        default=ann.value("defaultValue") if ann else None,
                    )
                )
                continue
            if kind == "RequestParam":
                default = ann.value("defaultValue") if ann else None
                required = _truthy(ann.value("required")) if ann else None
                if required is None:
                    required = default is None
                params.append(
                    ParamSpec(
                        name=_annotated_name(ann, param.name),
                        location="query",
                        required=required,
                        java_type=java_type,
                        default=default,
                    )
                )
                continue

            # No binding annotation.
            if java_type is not None and java_type.name in javatypes.IMPLICIT_PARAM_TYPES:
                continue
            type_label = java_type.render() if java_type else "?"
            notes.append(
                f"parameter '{param.name}' ({type_label}) has no binding annotation; "
                "Spring binds it as a @ModelAttribute from the query string - "
                "its individual fields are not modelled"
            )
            result.unbound_params.append(
                (f"{declaring.simple_name}.{method.name}", f"{param.name}: {type_label}", path)
            )

        return params, body_type, notes
