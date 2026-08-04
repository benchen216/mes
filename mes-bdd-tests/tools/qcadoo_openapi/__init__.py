"""Static OpenAPI 3.0 spec generation for the qcadoo MES Spring 3.2 codebase.

No runtime introspection: everything is derived from the Java AST, because none
of the mainstream OpenAPI generators support Spring 3.2.
"""

__all__ = ["javaindex", "javatypes", "schemas", "controllers", "naming", "emit", "report"]
