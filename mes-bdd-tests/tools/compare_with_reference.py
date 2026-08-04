#!/usr/bin/env python3
"""Compare the generated spec against the hand-written, SpecFormula-verified spec.

`mes-bdd-tests/src/test/resources/specs/api/qcadoo-orders.openapi.yml` is the
known-good control group: it was written by hand and confirmed to parse in
SpecFormula. This script checks that the generator produces an equivalent
description of the same six endpoints - paths, verbs, parameters, request body
and response shapes. Summaries are expected to differ (the Java sources carry no
summary text) and are reported separately rather than treated as a failure.

Usage:
    python3 mes-bdd-tests/tools/compare_with_reference.py
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Set, Tuple

import yaml

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(TOOLS_DIR, "..", ".."))
REFERENCE = os.path.join(
    REPO_ROOT, "mes-bdd-tests", "src", "test", "resources", "specs", "api", "qcadoo-orders.openapi.yml"
)
GENERATED = os.path.join(TOOLS_DIR, "generated", "qcadoo-mes-all.openapi.yml")

REF_PREFIX = "#/components/schemas/"


def resolve(schema: Optional[dict], doc: dict, seen: Optional[Set[str]] = None) -> dict:
    """Inline $refs so two specs can be compared without matching schema names."""
    if not isinstance(schema, dict):
        return {}
    seen = set(seen or ())
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith(REF_PREFIX):
        name = ref[len(REF_PREFIX) :]
        if name in seen:
            return {"type": "object", "$recursive": name}
        target = (doc.get("components", {}).get("schemas", {}) or {}).get(name)
        return resolve(target, doc, seen | {name})
    out: dict = {}
    for key, value in schema.items():
        if key in ("description", "example", "title", "format", "enum", "default"):
            continue
        if key == "properties" and isinstance(value, dict):
            out["properties"] = {k: resolve(v, doc, seen) for k, v in value.items()}
        elif key == "items":
            out["items"] = resolve(value, doc, seen)
        elif key == "additionalProperties" and isinstance(value, dict):
            out["additionalProperties"] = resolve(value, doc, seen)
        else:
            out[key] = value
    return out


def shape(schema: dict) -> str:
    """One-line structural signature of a resolved schema."""
    if not schema:
        return "<empty>"
    kind = schema.get("type", "object")
    if kind == "array":
        return f"array<{shape(schema.get('items') or {})}>"
    props = schema.get("properties")
    if props:
        return "object{" + ", ".join(sorted(props)) + "}"
    return str(kind)


def content_schema(container: Optional[dict], doc: dict) -> dict:
    if not isinstance(container, dict):
        return {}
    content = container.get("content") or {}
    for media in ("application/json", *content.keys()):
        if media in content:
            return resolve(content[media].get("schema"), doc)
    return {}


def param_key(param: dict, doc: dict) -> Tuple[str, str, bool, str]:
    return (
        param.get("name", ""),
        param.get("in", ""),
        bool(param.get("required", param.get("in") == "path")),
        str((resolve(param.get("schema"), doc) or {}).get("type", "")),
    )


def compare(reference: dict, generated: dict) -> Tuple[List[str], List[str], List[str]]:
    problems: List[str] = []
    matches: List[str] = []
    notes: List[str] = []

    for path, verbs in sorted((reference.get("paths") or {}).items()):
        for verb, ref_op in sorted(verbs.items()):
            label = f"{verb.upper()} {path}"
            gen_verbs = (generated.get("paths") or {}).get(path)
            if not gen_verbs or verb not in gen_verbs:
                problems.append(f"MISSING  {label} - not present in the generated spec")
                continue
            gen_op = gen_verbs[verb]

            ref_params = {param_key(p, reference) for p in (ref_op.get("parameters") or [])}
            gen_params = {param_key(p, generated) for p in (gen_op.get("parameters") or [])}
            if ref_params != gen_params:
                only_ref = sorted(ref_params - gen_params)
                only_gen = sorted(gen_params - ref_params)
                problems.append(
                    f"PARAMS   {label} - reference-only={only_ref} generated-only={only_gen}"
                )

            ref_body = content_schema(ref_op.get("requestBody"), reference)
            gen_body = content_schema(gen_op.get("requestBody"), generated)
            if bool(ref_body) != bool(gen_body):
                problems.append(
                    f"BODY     {label} - reference={shape(ref_body)} generated={shape(gen_body)}"
                )
            elif ref_body and shape(ref_body) != shape(gen_body):
                ref_props = set((ref_body.get("properties") or {}))
                gen_props = set((gen_body.get("properties") or {}))
                problems.append(
                    f"BODY     {label} - reference-only fields={sorted(ref_props - gen_props)} "
                    f"generated-only fields={sorted(gen_props - ref_props)}"
                )

            ref_resp = content_schema((ref_op.get("responses") or {}).get("200"), reference)
            gen_resp = content_schema((gen_op.get("responses") or {}).get("200"), generated)
            if shape(ref_resp) != shape(gen_resp):
                problems.append(
                    f"RESPONSE {label} - reference={shape(ref_resp)} generated={shape(gen_resp)}"
                )
            else:
                matches.append(f"MATCH    {label} - {shape(gen_resp)}")

            if ref_op.get("summary") != gen_op.get("summary"):
                notes.append(
                    f"SUMMARY  {label}\n"
                    f"           reference: {ref_op.get('summary')!r}\n"
                    f"           generated: {gen_op.get('summary')!r} "
                    f"[{gen_op.get('x-summary-source', 'generated')}]"
                )
            if ref_op.get("operationId") != gen_op.get("operationId"):
                notes.append(
                    f"OPID     {label} reference={ref_op.get('operationId')!r} "
                    f"generated={gen_op.get('operationId')!r}"
                )

    return problems, matches, notes


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", default=REFERENCE)
    parser.add_argument("--generated", default=GENERATED)
    args = parser.parse_args(argv)

    with open(args.reference, encoding="utf-8") as handle:
        reference = yaml.safe_load(handle)
    with open(args.generated, encoding="utf-8") as handle:
        generated = yaml.safe_load(handle)

    ref_ops = sum(len(v) for v in (reference.get("paths") or {}).values())
    problems, matches, notes = compare(reference, generated)

    print(f"reference: {os.path.relpath(args.reference, REPO_ROOT)} ({ref_ops} operations)")
    print(f"generated: {os.path.relpath(args.generated, REPO_ROOT)}")
    print()
    print(f"--- structural matches ({len(matches)}/{ref_ops}) ---")
    for line in matches:
        print(line)
    print()
    print(f"--- structural differences ({len(problems)}) ---")
    for line in problems or ["(none)"]:
        print(line)
    print()
    print(f"--- expected differences: summary / operationId ({len(notes)}) ---")
    for line in notes or ["(none)"]:
        print(line)

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
