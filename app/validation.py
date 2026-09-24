"""Request parsing and validation.

The problem instance is::

    {
      "nodes":     [unique ASCII node ids, 2..60],
      "edges":     [{"id", "source", "target", "cost"}, 1..220],
      "endpoints": [calibration endpoint node ids, 2..10]
    }

Edges are undirected. Parallel edges between the same node pair are allowed;
self loops and duplicate edge identifiers are rejected. Costs must be positive
integers (bool is rejected explicitly so ``True`` does not sneak through as 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ValidationError

MAX_NODES = 60
MIN_NODES = 2
MAX_EDGES = 220
MIN_EDGES = 1
MAX_ENDPOINTS = 10
MIN_ENDPOINTS = 2
# The native core uses signed-64 arithmetic with a saturated infinity of
# LLONG_MAX/4. Bound each cost so the largest possible witness (220 edges)
# stays strictly below that threshold: every feasible optimum is then exact.
MAX_COST = (2**63 - 1) // (4 * (MAX_EDGES + 36))


@dataclass(frozen=True)
class Edge:
    id: str
    source: int
    target: int
    cost: int


@dataclass(frozen=True)
class Problem:
    nodes: list[str]
    # node label -> internal index
    index: dict[str, int]
    edges: list[Edge]
    endpoints: list[int]  # internal indices, first-seen order


def _require_object(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValidationError(
            "INVALID_BODY", "request body must be a JSON object"
        )


def _ascii_or_fail(label: Any, pointer: str, what: str) -> str:
    if not isinstance(label, str):
        raise ValidationError(
            "INVALID_ID", f"{what} must be a string", pointer
        )
    if label == "":
        raise ValidationError(
            "EMPTY_ID", f"{what} must not be empty", pointer
        )
    try:
        label.encode("ascii")
    except UnicodeEncodeError:
        raise ValidationError(
            "NON_ASCII_ID", f"{what} must be ASCII: {label!r}", pointer
        ) from None
    return label


def _parse_nodes(body: dict[str, Any]) -> list[str]:
    if "nodes" not in body:
        raise ValidationError("MISSING_FIELD", "nodes is required", "/nodes")
    raw = body["nodes"]
    if not isinstance(raw, list):
        raise ValidationError(
            "INVALID_FIELD", "nodes must be an array", "/nodes"
        )
    if not (MIN_NODES <= len(raw) <= MAX_NODES):
        raise ValidationError(
            "NODE_COUNT_OUT_OF_RANGE",
            f"nodes count must be between {MIN_NODES} and {MAX_NODES}, "
            f"got {len(raw)}",
            "/nodes",
        )
    nodes: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        label = _ascii_or_fail(item, f"/nodes/{i}", "node id")
        if label in seen:
            raise ValidationError(
                "DUPLICATE_NODE",
                f"duplicate node id: {label!r}",
                f"/nodes/{i}",
            )
        seen.add(label)
        nodes.append(label)
    return nodes


def _parse_endpoints(
    body: dict[str, Any], index: dict[str, int]
) -> list[int]:
    if "endpoints" not in body:
        raise ValidationError(
            "MISSING_FIELD", "endpoints is required", "/endpoints"
        )
    raw = body["endpoints"]
    if not isinstance(raw, list):
        raise ValidationError(
            "INVALID_FIELD", "endpoints must be an array", "/endpoints"
        )
    if not (MIN_ENDPOINTS <= len(raw) <= MAX_ENDPOINTS):
        raise ValidationError(
            "ENDPOINT_COUNT_OUT_OF_RANGE",
            f"endpoints count must be between {MIN_ENDPOINTS} and "
            f"{MAX_ENDPOINTS}, got {len(raw)}",
            "/endpoints",
        )
    endpoints: list[int] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        label = _ascii_or_fail(item, f"/endpoints/{i}", "endpoint node id")
        if label in seen:
            raise ValidationError(
                "DUPLICATE_ENDPOINT",
                f"duplicate endpoint: {label!r}",
                f"/endpoints/{i}",
            )
        if label not in index:
            raise ValidationError(
                "UNKNOWN_ENDPOINT",
                f"endpoint {label!r} is not declared in nodes",
                f"/endpoints/{i}",
            )
        seen.add(label)
        endpoints.append(index[label])
    return endpoints


def _parse_cost(value: Any, pointer: str) -> int:
    # bool is a subclass of int in Python; reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            "INVALID_COST", "edge cost must be a positive integer", pointer
        )
    if value <= 0:
        raise ValidationError(
            "NON_POSITIVE_COST",
            f"edge cost must be positive, got {value}",
            pointer,
        )
    if value > MAX_COST:
        raise ValidationError(
            "COST_OUT_OF_RANGE",
            f"edge cost must be <= {MAX_COST}, got {value}",
            pointer,
        )
    return value


def _parse_edges(
    body: dict[str, Any], index: dict[str, int]
) -> list[Edge]:
    if "edges" not in body:
        raise ValidationError("MISSING_FIELD", "edges is required", "/edges")
    raw = body["edges"]
    if not isinstance(raw, list):
        raise ValidationError(
            "INVALID_FIELD", "edges must be an array", "/edges"
        )
    if not (MIN_EDGES <= len(raw) <= MAX_EDGES):
        raise ValidationError(
            "EDGE_COUNT_OUT_OF_RANGE",
            f"edges count must be between {MIN_EDGES} and {MAX_EDGES}, "
            f"got {len(raw)}",
            "/edges",
        )
    edges: list[Edge] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raw):
        base = f"/edges/{i}"
        if not isinstance(item, dict):
            raise ValidationError(
                "INVALID_FIELD", "edge must be an object", base
            )
        for field in ("id", "source", "target", "cost"):
            if field not in item:
                raise ValidationError(
                    "MISSING_FIELD", f"edge.{field} is required",
                    f"{base}/{field}",
                )
        edge_id = _ascii_or_fail(item["id"], f"{base}/id", "edge id")
        if edge_id in seen_ids:
            raise ValidationError(
                "DUPLICATE_EDGE_ID",
                f"duplicate edge id: {edge_id!r}",
                f"{base}/id",
            )
        src_label = _ascii_or_fail(
            item["source"], f"{base}/source", "edge source"
        )
        dst_label = _ascii_or_fail(
            item["target"], f"{base}/target", "edge target"
        )
        if src_label not in index:
            raise ValidationError(
                "UNKNOWN_NODE",
                f"edge source {src_label!r} is not declared in nodes",
                f"{base}/source",
            )
        if dst_label not in index:
            raise ValidationError(
                "UNKNOWN_NODE",
                f"edge target {dst_label!r} is not declared in nodes",
                f"{base}/target",
            )
        if src_label == dst_label:
            raise ValidationError(
                "SELF_LOOP",
                f"edge {edge_id!r} is a self loop on {src_label!r}",
                base,
            )
        cost = _parse_cost(item["cost"], f"{base}/cost")
        seen_ids.add(edge_id)
        edges.append(
            Edge(
                id=edge_id,
                source=index[src_label],
                target=index[dst_label],
                cost=cost,
            )
        )
    return edges


def parse_problem(payload: Any) -> Problem:
    """Validate the raw JSON payload and build an indexed problem."""
    _require_object(payload)
    nodes = _parse_nodes(payload)
    index = {label: i for i, label in enumerate(nodes)}
    edges = _parse_edges(payload, index)
    endpoints = _parse_endpoints(payload, index)
    return Problem(nodes=nodes, index=index, edges=edges, endpoints=endpoints)
