"""Map a source column type to a model field type."""
from living_reports.semantic.kinds import FieldType

# Longest prefixes first, so "DATETIME" is not read as "DATE" and "INTERVAL" not as "INT".
_PREFIXES: list[tuple[str, FieldType]] = sorted(
    [
        *((p, "string") for p in ("VARCHAR", "TEXT", "CHAR", "BPCHAR", "STRING", "UUID")),
        *(
            (p, "integer")
            for p in (
                "TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT", "UTINYINT",
                "USMALLINT", "UINTEGER", "UBIGINT", "INT2", "INT4", "INT8",
            )
        ),
        *(
            (p, "decimal")
            for p in ("DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL", "FLOAT4", "FLOAT8")
        ),
        *((p, "boolean") for p in ("BOOLEAN", "BOOL")),
        *((p, "datetime") for p in ("TIMESTAMP", "DATETIME")),
        ("DATE", "date"),
    ],
    key=lambda pair: -len(pair[0]),
)
_NOT_A_TYPE = ("INTERVAL",)


def field_type(source_type: str) -> FieldType | None:
    """The field type for a column type name. Case-insensitive prefix match; else None."""
    name = source_type.strip().upper()
    if name.startswith(_NOT_A_TYPE):
        return None
    for prefix, kind in _PREFIXES:
        if name.startswith(prefix):
            return kind
    return None
