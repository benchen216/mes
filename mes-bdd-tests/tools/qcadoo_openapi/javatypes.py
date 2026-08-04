"""Java type -> OpenAPI schema primitives.

Only the leaf mapping lives here; container / DTO expansion is in schemas.py.
"""

from __future__ import annotations

from typing import Dict, Optional

from .javaindex import TypeRef

INTEGER_32 = {"int", "Integer", "short", "Short", "byte", "Byte", "AtomicInteger"}
INTEGER_64 = {"long", "Long", "BigInteger", "AtomicLong"}
NUMBER = {"double", "Double", "float", "Float", "BigDecimal", "Number"}
BOOLEAN = {"boolean", "Boolean", "AtomicBoolean"}
STRING = {"String", "CharSequence", "char", "Character", "UUID", "Currency", "Locale"}
DATE_TIME = {"Date", "Timestamp", "LocalDateTime", "ZonedDateTime", "OffsetDateTime", "Instant", "Calendar"}
DATE_ONLY = {"LocalDate"}
TIME_ONLY = {"LocalTime", "Time"}

COLLECTION_TYPES = {
    "List",
    "ArrayList",
    "LinkedList",
    "Collection",
    "Set",
    "HashSet",
    "LinkedHashSet",
    "TreeSet",
    "Iterable",
    "Queue",
    "Deque",
    "ImmutableList",
    "ImmutableSet",
}
MAP_TYPES = {"Map", "HashMap", "LinkedHashMap", "TreeMap", "SortedMap", "ImmutableMap", "Properties"}
OPTIONAL_TYPES = {"Optional", "Maybe"}

#: Types that carry no usable structure - they serialise as a free-form object.
OPAQUE_TYPES = {
    "Object",
    "JSONObject",
    "JSONArray",
    "JsonNode",
    "ObjectNode",
    "Entity",
    "DataDefinition",
    "Serializable",
    "Void",
    "void",
}

#: Parameter types Spring injects itself - never part of the HTTP contract.
IMPLICIT_PARAM_TYPES = {
    "Locale",
    "HttpServletRequest",
    "HttpServletResponse",
    "HttpSession",
    "ServletRequest",
    "ServletResponse",
    "Model",
    "ModelMap",
    "ModelAndView",
    "Principal",
    "Authentication",
    "BindingResult",
    "Errors",
    "SessionStatus",
    "UriComponentsBuilder",
    "WebRequest",
    "NativeWebRequest",
    "InputStream",
    "OutputStream",
    "Reader",
    "Writer",
    "TimeZone",
    "MultipartFile",
}

#: Return types that mean "render a JSP view", not "serialise a payload".
VIEW_RETURN_TYPES = {"ModelAndView", "View", "RedirectView"}


def leaf_schema(type_ref: TypeRef) -> Optional[Dict[str, object]]:
    """Return an OpenAPI schema for a scalar Java type, or None if not a leaf."""
    name = type_ref.name
    if name in INTEGER_64:
        return {"type": "integer", "format": "int64"}
    if name in INTEGER_32:
        return {"type": "integer", "format": "int32"}
    if name in NUMBER:
        return {"type": "number"}
    if name in BOOLEAN:
        return {"type": "boolean"}
    if name in STRING:
        return {"type": "string"}
    if name in DATE_TIME:
        return {"type": "string", "format": "date-time"}
    if name in DATE_ONLY:
        return {"type": "string", "format": "date"}
    if name in TIME_ONLY:
        return {"type": "string", "format": "time"}
    if name in OPAQUE_TYPES:
        return {"type": "object"}
    return None


def is_collection(type_ref: TypeRef) -> bool:
    return type_ref.dims > 0 or type_ref.name in COLLECTION_TYPES


def is_map(type_ref: TypeRef) -> bool:
    return type_ref.name in MAP_TYPES


def is_optional(type_ref: TypeRef) -> bool:
    return type_ref.name in OPTIONAL_TYPES


def element_type(type_ref: TypeRef) -> Optional[TypeRef]:
    """Element type of an array / collection, or None when unparameterised."""
    if type_ref.dims > 0:
        return TypeRef(type_ref.name, type_ref.args, type_ref.dims - 1, type_ref.primitive)
    if type_ref.args:
        return type_ref.args[0]
    return None


def map_value_type(type_ref: TypeRef) -> Optional[TypeRef]:
    if len(type_ref.args) >= 2:
        return type_ref.args[1]
    return None


def query_param_schema(type_ref: TypeRef) -> Dict[str, object]:
    """Schema for a query / path parameter: scalars, or array of scalars."""
    if is_collection(type_ref):
        inner = element_type(type_ref)
        item = leaf_schema(inner) if inner is not None else None
        return {"type": "array", "items": item or {"type": "string"}}
    return leaf_schema(type_ref) or {"type": "string"}
