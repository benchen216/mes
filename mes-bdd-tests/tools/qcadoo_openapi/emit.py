"""Assembles OpenAPI 3.0 documents from extracted endpoints."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

import yaml

from . import javatypes, naming
from .controllers import Endpoint, ParamSpec
from .schemas import SchemaRegistry

REF_PREFIX = "#/components/schemas/"

SPEC_DESCRIPTION = """\
Auto-generated from the qcadoo MES Java sources by
`mes-bdd-tests/tools/generate_openapi.py`. Do not hand-edit: regenerate instead.

**URL structure** - `mes-application/src/main/webapp/WEB-INF/web.xml` maps the
DispatcherServlet at `/rest/*`, so every controller path is emitted with the
`/rest` prefix already baked into the path key. `servers[].url` is therefore `/`;
SpecFormula does not apply `servers[].url`, and baking the prefix in keeps the
spec correct for both.

**Authentication** - Spring Security 3.2 form login plus a `JSESSIONID` cookie,
not bearer tokens.

**Summaries** - the Java sources carry no summary text. Operations marked
`x-summary-source: generated` have a machine-derived placeholder and need human
curation; add an entry to `tools/summaries.yml` keyed by `ClassName.methodName`
to replace one.

**Responses** - only the success response is modelled. Several qcadoo endpoints
return HTTP 200 with an error code inside the payload rather than a 4xx/5xx
status, so error shapes are not inferable from the source.
"""


class _SpecDumper(yaml.SafeDumper):
    """Keeps nested block sequences indented under their key."""

    def increase_indent(self, flow=False, indentless=False):  # noqa: ARG002
        return super().increase_indent(flow, False)


def _param_object(param: ParamSpec) -> Dict[str, object]:
    schema = javatypes.query_param_schema(param.java_type) if param.java_type else {"type": "string"}
    if param.default is not None:
        schema = dict(schema)
        schema["default"] = _coerce_default(param.default, schema.get("type"))
    obj: Dict[str, object] = {
        "name": param.name,
        "in": param.location,
        "required": True if param.location == "path" else bool(param.required),
        "schema": schema,
    }
    return obj


def _coerce_default(value: object, target_type: Optional[object]) -> object:
    if target_type == "integer":
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return value
    if target_type == "number":
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return value
    if target_type == "boolean":
        return str(value).strip().lower() == "true"
    return value


def _collect_refs(node: object, out: Set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(REF_PREFIX):
            out.add(ref[len(REF_PREFIX) :])
        for value in node.values():
            _collect_refs(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_refs(value, out)


def reachable_schemas(document: Dict[str, object], registry: SchemaRegistry) -> Dict[str, object]:
    seen: Set[str] = set()
    _collect_refs(document.get("paths", {}), seen)
    frontier = list(seen)
    while frontier:
        name = frontier.pop()
        schema = registry.schemas.get(name)
        if schema is None:
            continue
        found: Set[str] = set()
        _collect_refs(schema, found)
        for candidate in found - seen:
            seen.add(candidate)
            frontier.append(candidate)
    return {name: registry.schemas[name] for name in sorted(seen) if name in registry.schemas}


class SpecBuilder:
    def __init__(self, registry: SchemaRegistry, curated_summaries: Dict[str, str]):
        self.registry = registry
        self.curated = curated_summaries
        self.summary_report: List[Tuple[str, str, str]] = []  # (source, summary, origin)
        self.path_conflicts: List[str] = []
        self._summary_origins: Dict[str, str] = {}

    def build(
        self,
        endpoints: Iterable[Endpoint],
        title: str,
        version: str,
        repo_root: str,
    ) -> Dict[str, object]:
        endpoints = list(endpoints)
        self.path_conflicts = []
        operation_ids = naming.build_operation_ids(
            [(e.key, e.controller.simple_name, e.method.name) for e in endpoints]
        )
        summaries = self._summaries(endpoints)

        paths: Dict[str, Dict[str, object]] = {}
        for endpoint in endpoints:
            key = endpoint.key
            bucket = paths.setdefault(endpoint.path, {})
            if endpoint.http_method in bucket:
                self.path_conflicts.append(
                    f"{endpoint.http_method.upper()} {endpoint.path} declared more than once "
                    f"(kept the first; duplicate from {endpoint.controller.simple_name}."
                    f"{endpoint.method.name})"
                )
                continue
            bucket[endpoint.http_method] = self._operation(
                endpoint, operation_ids[key], summaries[key], repo_root
            )

        document: Dict[str, object] = {
            "openapi": "3.0.0",
            "info": {
                "title": title,
                "version": version,
                "description": SPEC_DESCRIPTION,
            },
            "servers": [
                {
                    "url": "/",
                    "description": "Paths already include the /rest DispatcherServlet prefix",
                }
            ],
            "paths": {key: paths[key] for key in sorted(paths)},
        }
        document["components"] = {"schemas": reachable_schemas(document, self.registry)}
        return document

    # -- pieces ------------------------------------------------------------- #

    def _summaries(self, endpoints: List[Endpoint]) -> Dict[str, str]:
        raw: Dict[str, str] = {}
        origins: Dict[str, str] = {}
        for endpoint in endpoints:
            key = endpoint.key
            # Prefer an override keyed by the concrete controller, then by the
            # declaring class (which lets one entry cover all inheritors).
            alias = f"{endpoint.controller.simple_name}.{endpoint.method.name}"
            lookup = f"{endpoint.declaring_class.simple_name}.{endpoint.method.name}"
            curated = self.curated.get(alias) or self.curated.get(lookup)
            if curated:
                raw[key] = curated
                origins[key] = "curated"
            else:
                raw[key] = naming.generated_summary(
                    endpoint.controller.simple_name, endpoint.method.name
                )
                origins[key] = "generated"
        unique = naming.ensure_unique_summaries(raw)
        self.summary_report = [
            (key.split("#", 1)[0], unique[key], origins[key]) for key in sorted(unique)
        ]
        self._summary_origins = origins
        return unique

    def _operation(
        self, endpoint: Endpoint, operation_id: str, summary: str, repo_root: str
    ) -> Dict[str, object]:
        key = endpoint.key
        source_file = endpoint.declaring_class.file
        if source_file.startswith(repo_root):
            source_file = source_file[len(repo_root) :].lstrip("/")

        description_lines = [
            f"Generated from `{endpoint.declaring_class.simple_name}."
            f"{endpoint.method.name}()` in `{source_file}`."
        ]
        if endpoint.inherited:
            description_lines.append(
                f"Endpoint is inherited from `{endpoint.declaring_class.simple_name}`; "
                f"the concrete controller is `{endpoint.controller.simple_name}`."
            )
        for note in endpoint.notes:
            description_lines.append(f"NOTE: {note}")

        operation: Dict[str, object] = {
            "summary": summary,
            "description": "\n\n".join(description_lines) + "\n",
            "operationId": operation_id,
            "tags": [naming.tag_for_module(endpoint.module)],
            "x-summary-source": self._summary_origins.get(key, "generated"),
            "x-source-file": source_file,
            "x-source-method": (
                f"{endpoint.declaring_class.simple_name}.{endpoint.method.name}"
                f":{endpoint.method.line}"
            ),
        }

        parameters = [_param_object(p) for p in endpoint.params]
        if parameters:
            operation["parameters"] = parameters

        if endpoint.body_type is not None:
            body_schema = self.registry.schema_for(
                endpoint.body_type,
                endpoint.declaring_class,
                f"{endpoint.declaring_class.simple_name}.{endpoint.method.name} @RequestBody",
                endpoint.substitution,
            )
            operation["requestBody"] = {
                "required": True,
                "content": {(endpoint.consumes or "application/json"): {"schema": body_schema}},
            }

        operation["responses"] = self._responses(endpoint)
        return operation

    def _responses(self, endpoint: Endpoint) -> Dict[str, object]:
        if endpoint.return_type is None or endpoint.return_type.name in ("void", "Void"):
            return {"200": {"description": "Empty response body"}}
        schema = self.registry.schema_for(
            endpoint.return_type,
            endpoint.declaring_class,
            f"{endpoint.declaring_class.simple_name}.{endpoint.method.name} return",
            endpoint.substitution,
        )
        return {
            "200": {
                "description": f"Success. Java return type: {endpoint.return_type.render()}",
                "content": {endpoint.produces.split(";")[0]: {"schema": schema}},
            }
        }


def dump_yaml(document: Dict[str, object]) -> str:
    return yaml.dump(
        document,
        Dumper=_SpecDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
