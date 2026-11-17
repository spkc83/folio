"""Errors the query engine raises. Each one carries a sentence for the person who asked."""


class OutOfScope(Exception):
    """The model cannot answer the query. ``unknown`` lists ids the model does not have."""

    def __init__(self, reason: str, unknown: list[str] | None = None, detail: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.unknown = unknown or []
        # The technical cause (for example the join path search), for the API detail.
        self.detail = detail or reason


class QueryRejected(Exception):
    """A SQL text or an expression fails the read-only check."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
