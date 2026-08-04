#!/usr/bin/env python3
"""Generate OpenAPI 3.0 specs from the qcadoo MES Java sources.

qcadoo MES runs Spring 3.2.11 (2014). springdoc-openapi needs Spring 5+,
springfox-swagger2 2.x needs Spring 4+, and swagger-springmvc 0.9.x has been
unmaintained for a decade - so the only workable route is static analysis of the
source tree. This script parses the Java AST with `javalang`, walks every
`@Controller`, and emits OpenAPI 3.0 YAML plus a generation report.

Usage:
    python3 mes-bdd-tests/tools/generate_openapi.py
    python3 mes-bdd-tests/tools/generate_openapi.py --only orders --verbose
    python3 mes-bdd-tests/tools/generate_openapi.py --check   # fail if output is stale

Nothing under the repository other than the output directory is written to, and
no service or database is contacted.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from typing import Dict, List

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qcadoo_openapi import report as report_mod  # noqa: E402
from qcadoo_openapi.controllers import ControllerExtractor  # noqa: E402
from qcadoo_openapi.emit import SpecBuilder, dump_yaml, slugify  # noqa: E402
from qcadoo_openapi.javaindex import JavaIndex  # noqa: E402
from qcadoo_openapi.naming import tag_for_module  # noqa: E402
from qcadoo_openapi.schemas import SchemaRegistry  # noqa: E402

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(TOOLS_DIR, "..", ".."))
DEFAULT_OUTPUT = os.path.join(TOOLS_DIR, "generated")
SUMMARY_OVERRIDES = os.path.join(TOOLS_DIR, "summaries.yml")
SOURCE_ROOTS = ["mes-plugins", "mes-application"]
SPEC_VERSION = "1.0.0"


def load_summary_overrides(path: str) -> Dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    summaries = loaded.get("summaries", loaded)
    if not isinstance(summaries, dict):
        return {}
    return {str(key): str(value) for key, value in summaries.items() if value}


def candidate_controller_files(index: JavaIndex) -> List[str]:
    """Cheap pre-filter: only files whose text mentions the annotations we need."""
    candidates: List[str] = []
    for path in index.all_paths():
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        if "@Controller" in text and "@RequestMapping" in text:
            candidates.append(path)
    return candidates


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", default=REPO_ROOT, help="qcadoo MES checkout root")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="output directory")
    parser.add_argument("--summaries", default=SUMMARY_OVERRIDES, help="summary override file")
    parser.add_argument("--rest-prefix", default="/rest", help="DispatcherServlet prefix from web.xml")
    parser.add_argument("--only", action="append", default=[], help="limit to these plugin modules")
    parser.add_argument("--no-per-module", action="store_true", help="only emit the combined spec")
    parser.add_argument("--check", action="store_true", help="exit 1 if any output file would change")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    repo_root = os.path.abspath(args.repo_root)
    roots = [os.path.join(repo_root, name) for name in SOURCE_ROOTS]
    roots = [root for root in roots if os.path.isdir(root)]
    if not roots:
        print(f"error: no source roots found under {repo_root}", file=sys.stderr)
        return 2

    index = JavaIndex(roots)
    candidates = candidate_controller_files(index)
    if args.verbose:
        print(f"indexed {len(index.all_paths())} java files, {len(candidates)} controller candidates")

    extraction = ControllerExtractor(index, rest_prefix=args.rest_prefix).extract(candidates)
    if args.only:
        wanted = set(args.only)
        extraction.endpoints = [e for e in extraction.endpoints if e.module in wanted]

    registry = SchemaRegistry(index=index)
    builder = SpecBuilder(registry, load_summary_overrides(args.summaries))

    os.makedirs(args.output, exist_ok=True)
    pending: Dict[str, str] = {}

    if not args.no_per_module:
        by_module: Dict[str, List] = defaultdict(list)
        for endpoint in extraction.endpoints:
            by_module[endpoint.module].append(endpoint)
        for module, endpoints in sorted(by_module.items()):
            document = builder.build(
                endpoints,
                f"qcadoo MES - {tag_for_module(module)} API (generated)",
                SPEC_VERSION,
                repo_root,
            )
            filename = f"qcadoo-{slugify(module)}.generated.openapi.yml"
            pending[os.path.join(args.output, filename)] = dump_yaml(document)

    # Build the combined spec last so summary and conflict bookkeeping in the
    # builder reflects every endpoint, not just the last module.
    combined = builder.build(
        extraction.endpoints, "qcadoo MES REST API (generated)", SPEC_VERSION, repo_root
    )
    pending[os.path.join(args.output, "qcadoo-mes-all.openapi.yml")] = dump_yaml(combined)

    written = sorted(os.path.relpath(path, repo_root) for path in pending)
    json_report = report_mod.build_json_report(
        extraction, registry, builder.summary_report, builder.path_conflicts, written, repo_root
    )
    json_report["totals"]["parse_failures"] = len(index.parse_failures)
    json_report["parse_failures"] = {
        os.path.relpath(path, repo_root): reason for path, reason in index.parse_failures.items()
    }
    pending[os.path.join(args.output, "generation-report.json")] = report_mod.dump_json(json_report)
    pending[os.path.join(args.output, "generation-report.md")] = report_mod.build_markdown_report(
        json_report
    )

    if args.check:
        stale = [
            path
            for path, content in pending.items()
            if not os.path.exists(path) or open(path, encoding="utf-8").read() != content
        ]
        if stale:
            print("stale generated files:", file=sys.stderr)
            for path in sorted(stale):
                print(f"  {os.path.relpath(path, repo_root)}", file=sys.stderr)
            return 1
        print("generated specs are up to date")
        return 0

    for path, content in pending.items():
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    totals = json_report["totals"]
    print(
        f"controllers={totals['controllers_scanned']} "
        f"endpoints={totals['endpoints_emitted']} "
        f"excluded={totals['endpoints_excluded']} "
        f"schemas={totals['schemas_generated']} "
        f"unresolved={totals['unresolved_types']} "
        f"summaries_to_curate={totals['summaries_generated']}"
    )
    for path in sorted(pending):
        print(f"  wrote {os.path.relpath(path, repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
