"""Kinds, payload validation, and edge rules."""

from dataclasses import asdict

from pydantic import ValidationError

from folio_graph.model import ANY, EdgeRule, GraphError, KindSpec, NodeChange


class Registry:
    def __init__(self) -> None:
        self._kinds: dict[str, KindSpec] = {}

    def register(self, spec: KindSpec) -> None:
        self._kinds[spec.name] = spec

    def kind(self, name: str) -> KindSpec:
        try:
            return self._kinds[name]
        except KeyError:
            raise GraphError(f"Unknown kind: {name}") from None

    def validate(self, change: NodeChange) -> dict:
        spec = self.kind(change.kind)
        try:
            return spec.schema.model_validate(change.payload).model_dump(mode="json")
        except ValidationError as err:
            first = err.errors()[0]
            where = ".".join(str(p) for p in first["loc"]) or "payload"
            raise GraphError(
                f"{change.kind} {change.id!r} is not valid: {where}: {first['msg']}"
            ) from None

    def edge_rule(self, edge_kind: str, from_kind: str, to_kind: str) -> EdgeRule:
        for spec in self._kinds.values():
            for rule in spec.edges:
                if (
                    rule.edge_kind == edge_kind
                    and rule.from_kind in (from_kind, ANY)
                    and rule.to_kind in (to_kind, ANY)
                ):
                    return rule
        raise GraphError(f"No rule allows {from_kind} -[{edge_kind}]-> {to_kind}")

    @staticmethod
    def schema_document(spec: KindSpec) -> dict:
        return {
            "schema": spec.schema.model_json_schema(),
            "edges": [asdict(rule) for rule in spec.edges],
        }
