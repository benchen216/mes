# qcadoo MES static OpenAPI generator

Generates OpenAPI 3.0 specs by parsing the Java sources. Nothing is introspected at
runtime; no service, database or network is touched.

## Why static analysis

qcadoo MES runs **Spring 3.2.11** (2014). Every mainstream OpenAPI generator is out
of reach:

| Tool | Requirement | Verdict |
|---|---|---|
| springdoc-openapi | Spring 5+ | unusable |
| springfox-swagger2 2.x | Spring 4+ | unusable |
| swagger-springmvc 0.9.x | Spring 3.x | unmaintained for 10+ years |

So the endpoints are recovered from the source tree instead.

**Implementation: `javalang` AST parsing, not regular expressions.** This was a
deliberate choice, and it earns its keep — three of the correctness problems found
while building this tool are invisible to a regex:

1. `ProductLookupController` and six siblings declare **zero** endpoint methods.
   All their endpoints are inherited from the abstract generic base
   `BasicLookupController<R>`. A per-file regex scan misses 13 live endpoints and
   emits a bogus base-class endpoint with no class-level path.
2. `ResourceLookupController.getRecords(..., ResourceDTO)` **overrides**
   `BasicLookupController.getRecords(..., R)`. Recognising that as an override
   requires substituting the type variable `R`, which needs the `extends` clause.
3. Return types like `GridResponse<R>` only become `GridResponse<ProductDTO>` once
   the subclass's type argument is resolved.

`javalang` parses **80/80** controller files and **2949/2949** indexed sources with
zero failures, so there is no regex fallback path and no silent partial parsing.

## Requirements

```bash
python3 -m pip install javalang pyyaml
# optional, for the validation step:
python3 -m pip install openapi-spec-validator
```

## Usage

```bash
# generate everything into tools/generated/
python3 mes-bdd-tests/tools/generate_openapi.py

# one plugin module only
python3 mes-bdd-tests/tools/generate_openapi.py --only orders --verbose

# CI guard: exit 1 if the checked-in output is stale
python3 mes-bdd-tests/tools/generate_openapi.py --check

# diff against the hand-written, SpecFormula-verified control group
python3 mes-bdd-tests/tools/compare_with_reference.py
```

Useful flags: `--repo-root`, `--output`, `--summaries`, `--rest-prefix`,
`--no-per-module`.

## Output

Everything lands in `tools/generated/` (git-ignored territory as far as the build is
concerned — nothing else in the repo is written to):

| File | Contents |
|---|---|
| `qcadoo-mes-all.openapi.yml` | all 130 endpoints in one spec |
| `qcadoo-<module>.generated.openapi.yml` | one spec per plugin module |
| `generation-report.md` | human-readable report: totals, exclusions, gaps |
| `generation-report.json` | same data, machine-readable |

The hand-written
`mes-bdd-tests/src/test/resources/specs/api/qcadoo-orders.openapi.yml` is **never**
touched, and the per-module files carry a `.generated.` infix so they can't be
confused with it.

## The `/rest` prefix

`mes-application/src/main/webapp/WEB-INF/web.xml` maps the DispatcherServlet at
`/rest/*`, so `@RequestMapping("/order")` is served at `/order`
relative to the servlet, i.e. `/rest/order` absolute. The generator prepends `/rest`
to every path.

SpecFormula does **not** apply `servers[].url`, so the prefix is baked directly into
the path key (`/rest/dashboardKanban/ordersPending`) rather than left to `servers`.
`servers[].url` is therefore emitted as `/`, which keeps the spec correct for
conformant tooling too. (The hand-written reference sets `servers: /rest` *and*
prefixes its paths; a strict client would read that as `/rest/rest/...`.)

Two controllers — `ActionsController` and `DocumentPositionsController` — already
declare `/rest` in their own class-level `@RequestMapping`, so they legitimately live
at **`/rest/rest/actions`** and **`/rest/rest/documentPositions`**. This is not a
generator bug: the frontend calls exactly those URLs, e.g.
`mes-plugins-material-flow-resources/.../js/gridOptions.js:555`.

## Results

| Metric | Value |
|---|---|
| Controllers scanned (`@Controller`, concrete) | 79 |
| Endpoints emitted | 130 |
| — inherited from a generic base class | 13 |
| Endpoints excluded | 75 |
| `components/schemas` generated | 77 |
| Types that degraded to `type: object` | 1 |
| Structurally opaque payload uses | 26 |
| Summaries still needing human words | 124 |

Response media types: 123 `application/json`, 7 `text/plain`. Of the JSON ones, 103
carry an explicit `produces = MediaType.APPLICATION_JSON_VALUE` and 20 have no
`produces` at all (see limitations).

Verification performed:

* `yaml.safe_load` parses all 11 emitted files.
* `openapi-spec-validator` validates all 11 against the OpenAPI 3.0 schema.
* All 130 `operationId`s and all 130 `summary`s are unique.
* An independent AST sweep finds 92 `produces = APPLICATION_JSON_VALUE` annotations;
  **all 92 are accounted for** (90 in concrete controllers, plus 2 in the abstract
  base that expand to 13 inherited endpoints). Zero unaccounted.
* Against the hand-written control group: **6/6 endpoints match structurally**
  (path, verb, parameters, request body fields, response shape). See below.

## Comparison with the hand-written control group

`compare_with_reference.py` inlines all `$ref`s in both specs and compares
structure, so schema naming differences don't produce false diffs.

```
--- structural matches (6/6) ---
MATCH    GET  /rest/dashboardKanban/ordersPending     - array<object{...22 fields...}>
MATCH    GET  /rest/dashboardKanban/ordersInProgress  - array<object{...22 fields...}>
MATCH    GET  /rest/dashboardKanban/ordersCompleted   - array<object{...22 fields...}>
MATCH    PUT  /rest/dashboardKanban/updateOrderState/{orderId} - object{message, order}
MATCH    POST /rest/order  - object{additionalInformation, code, message, number, operationalTasks, order}
MATCH    GET  /rest/productionLines/default - object{id, name, number}

--- structural differences (0) ---
(none)
```

Remaining differences, both expected:

* **`operationId`** — the reference calls `POST /rest/order` `createOrder`; the
  generator derives `saveOrder` from the Java method name `saveOrder()`.
* **`enum` on `OrderHolder.state`** — the reference lists the seven order states.
  The Java field is `private String state`, so the enum is human knowledge that the
  source does not contain. The generator emits `type: string`.

Summaries matched once the six reference summaries were transcribed into
`summaries.yml`, which is exactly the curation workflow described below.

## Summaries and the curation workflow

SpecFormula matches operations by their `summary` string, so summaries must exist
and be unique — but the Java sources contain no summary text whatsoever. Each
operation therefore gets one of:

* **curated** — from `tools/summaries.yml`, keyed by `ClassName.methodName`.
  Marked `x-summary-source: curated`.
* **generated** — `"<Humanised method name> (<ControllerName>)"`, e.g.
  `Get orders pending (DashboardKanbanController)`. Marked
  `x-summary-source: generated`.

The controller name is part of the generated summary on purpose: it guarantees
uniqueness (six controllers expose a method called `getRecords`) and makes the
summary traceable back to source. To curate one, add an entry and regenerate:

```yaml
summaries:
  OrdersApiController.saveOrder: 建立訂單
```

The key may name either the concrete `@Controller` or the declaring class — the
latter lets a single entry cover all seven inheritors of `BasicLookupController`.

Find everything still awaiting words — `generation-report.md` lists them all in a
table, and the JSON report carries the same list:

```bash
python3 -c "import json; d=json.load(open('mes-bdd-tests/tools/generated/generation-report.json')); \
print(d['totals']['summaries_generated'], 'to curate;', d['totals']['summaries_curated'], 'curated')"
# 124 to curate; 6 curated
```

(Grepping the YAML for `x-summary-source: generated` returns 125 — the spec's own
`info.description` explains the marker and matches too.)

## What gets excluded, and why

All 75 exclusions share one reason: **no `@ResponseBody`**. In Spring 3.2 a
`@RequestMapping` method without `@ResponseBody` resolves a JSP view rather than
serialising a payload, so these are UI routes, not API endpoints. They are listed
individually in `generation-report.md` rather than dropped silently.

A second exclusion rule exists but is never reached in this codebase: a method that
returns `ModelAndView` / `View` / `RedirectView` is excluded even if it carries
`@ResponseBody`.

## Known limitations

Listed honestly; each is visible in `generation-report.md`.

1. **20 endpoints have no `produces` and are assumed `application/json`.**
   All are `@ResponseBody` methods, so with Jackson on the classpath Spring will
   content-negotiate to JSON — but the source does not say so. Twelve of them are
   `multiUploadFiles` handlers returning `void`; their real response body is
   whatever the servlet writes directly, which is not statically knowable. Every one
   carries a `NOTE:` in its `description`.

2. **`@ModelAttribute`-style query binding is not expanded (23 parameters).**
   `getRecords(..., ProductDTO record)` has no binding annotation, so Spring
   populates `record`'s fields from individual query-string keys. The spec documents
   the annotated parameters only; the jqGrid filter keys are not enumerated.

3. **Only the 200 response is modelled.** qcadoo frequently returns HTTP 200 with an
   error code *inside* the payload (`OrderCreationResponse.code = ERROR`). Error
   status codes are not inferable from source, so no 4xx/5xx responses are emitted.

4. **26 opaque payload uses.** `java.lang.Object` (21) and `org.json.JSONObject` (5)
   have no static structure and become `type: object`. The five `exportToCsv`
   endpoints take `@RequestBody JSONObject`; their request body genuinely is
   free-form JSON.

5. **`AbstractDTO` degrades to `type: object`.** `DataResponse.entities` is typed
   `List<? extends AbstractDTO>`; `AbstractDTO` is a marker interface with no
   fields, and the concrete type varies per endpoint. Static analysis cannot pick
   one. This is the single entry in `unresolved_types`.

6. **Enums are only recovered when a field's declared type is the enum.**
   `OrderHolder.state` is declared `String`, so the seven order states are lost.
   Two real enums (`SimpleResponseStatus`, `StatusCode`) are recovered correctly.

7. **`? super Foo` wildcards collapse to `Foo`.** Imprecise, though no endpoint in
   this codebase currently uses one.

8. **The `.html` suffix form is not emitted.** `web.xml` also maps `*.html`, and
   Spring 3.2's default `useSuffixPatternMatch=true` means
   `/rest/rest/documentPositions/units.html` hits the same handler as
   `/rest/rest/documentPositions/units`. Some frontend JS uses the suffixed form.
   The spec lists the canonical extension-less path only.

9. **No security schemes.** qcadoo uses Spring Security 3.2 form login plus a
   `JSESSIONID` cookie, configured in XML, not in annotations. Add it by hand if a
   consumer needs it.

10. **Ambiguous simple type names resolve to nothing.** Type resolution walks
    explicit imports, then same-package, then same-file nested types, then wildcard
    imports, then a unique global filename match. If two files in different packages
    share a class name and neither is imported, resolution gives up and reports it
    rather than guessing. This does not currently occur.

11. **Jackson annotations are ignored.** `@JsonIgnore`, `@JsonProperty(...)` and
    custom serialisers would change the wire format; the generator reads declared
    fields (minus `static` / `transient`) and inherited fields from project
    superclasses. Spot checks against the control group show the field sets match,
    but this is an assumption, not a guarantee.

## What still needs a human

1. **124 summaries** — the main task. Add entries to `summaries.yml`.
2. **`OrderHolder.state` enum** — and any other string-typed field with a closed
   value set, if feature steps need to assert on them.
3. **Error responses** — decide per endpoint whether the business-error-inside-200
   convention should be documented as a distinct response.
4. **The 20 assumed-JSON endpoints** — confirm the real content type, especially the
   twelve `void` upload handlers.
5. **`AbstractDTO` subtypes** — if a feature needs
   `/rest/product/typeahead`'s entity shape, pin the concrete DTO by hand.
6. **`operationId` review** — derived from Java method names, so several are
   controller-prefixed for uniqueness
   (`productLookupControllerGetRecords`). Rename if a consumer cares.

## Layout

```
tools/
  generate_openapi.py          CLI entry point
  compare_with_reference.py    diff against the hand-written control group
  summaries.yml                curated summary overrides
  qcadoo_openapi/
    javaindex.py               lazy AST index, type/annotation model
    javatypes.py               Java type -> OpenAPI leaf mapping
    controllers.py             controller discovery, inheritance, endpoints
    schemas.py                 components/schemas with generic substitution
    naming.py                  operationId / summary / tag derivation
    emit.py                    OpenAPI document assembly + YAML output
    report.py                  markdown + JSON reports
  generated/                   output (regenerate, don't hand-edit)
```

## Regeneration policy

`generated/` is machine output. Edit the Java sources or `summaries.yml`, then
rerun the generator. `--check` makes staleness a CI failure.
