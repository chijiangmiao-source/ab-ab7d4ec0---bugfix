"""Steiner subnet solver: Python front-end around the native DP core.

The algorithmic work (terminal-subset Dreyfus-Wagner DP, node aggregation
merge by disjoint-set union, multi-source shortest-path closure) lives in
``core/steiner.c``; see its header comment for the recurrence and the greedy
oracle-based canonical witness construction. This module:

* pre-checks topology with a DSU aggregation pass so dangling endpoints and
  split components get precise, locatable errors;
* streams the indexed instance to the native core (one short-lived process
  per audit, so no result can ever leak between requests);
* rebuilds the canonical edge set and derives its adjacency list.
"""

from __future__ import annotations

import os
import subprocess

from .errors import TopologyError
from .validation import Edge, Problem


class _DSU:
    """Node aggregation (union-find) over the undirected input graph."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def check_feasibility(problem: Problem) -> None:
    """Aggregate nodes through edges and validate endpoint connectivity.

    Raises a locatable topology error for dangling (degree-zero) endpoints or
    endpoints spread over more than one connected component.
    """
    n = len(problem.nodes)
    degree = [0] * n
    dsu = _DSU(n)
    for e in problem.edges:
        degree[e.source] += 1
        degree[e.target] += 1
        dsu.union(e.source, e.target)

    # Dangling endpoints first: endpoint list order gives stable pointers.
    for pos, v in enumerate(problem.endpoints):
        if degree[v] == 0:
            raise TopologyError(
                "DANGLING_ENDPOINT",
                f"endpoint {problem.nodes[v]!r} has no incident edge",
                pointer=f"/endpoints/{pos}",
            )

    groups: dict[int, list[int]] = {}
    for v in problem.endpoints:
        groups.setdefault(dsu.find(v), []).append(v)
    if len(groups) > 1:
        root_of_first = dsu.find(problem.endpoints[0])
        split_pos = next(
            pos
            for pos, v in enumerate(problem.endpoints)
            if dsu.find(v) != root_of_first
        )
        components = [
            sorted(problem.nodes[v] for v in members)
            for members in groups.values()
        ]
        components.sort()
        raise TopologyError(
            "ENDPOINTS_UNCONNECTED",
            "endpoints lie in different connected components; no edge set "
            "can join them",
            pointer=f"/endpoints/{split_pos}",
            components=components,
        )


def _core_path() -> str:
    override = os.environ.get("STEINER_CORE")
    if override:
        return override
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "core",
        "steiner",
    )


def _encode_instance(problem: Problem) -> tuple[str, list[Edge]]:
    """Serialise in canonical edge order (ASCII ascending edge id).

    The tie-break rule compares ascending edge-id lists lexicographically, so
    the core's edge input order MUST be id-sorted, not request-array order.
    Returns the payload and the ordered edges for position mapping.
    """
    ordered = sorted(problem.edges, key=lambda e: e.id)
    lines = [
        f"{len(problem.nodes)} {len(ordered)} {len(problem.endpoints)}",
        " ".join(str(v) for v in problem.endpoints),
    ]
    for e in ordered:
        lines.append(f"{e.source} {e.target} {e.cost}")
    return "\n".join(lines) + "\n", ordered


def run_core(
    problem: Problem, timeout: float = 30.0
) -> tuple[int, tuple[int, ...], str, list[Edge]]:
    """Invoke the native solver.

    Returns (cost, positions into the id-sorted edge list, status, ordered).
    """
    payload, ordered = _encode_instance(problem)
    proc = subprocess.run(
        [_core_path()],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"steiner core exited with {proc.returncode}: {proc.stderr.strip()}"
        )
    tokens = proc.stdout.split()
    status = tokens[0]
    if status == "OK":
        cost = int(tokens[1])
        count = int(tokens[2])
        positions = tuple(int(x) for x in tokens[3:3 + count])
        return cost, positions, status, ordered
    return 0, (), status, ordered


def solve(problem: Problem) -> tuple[int, list[Edge], tuple[str, ...]]:
    """Return ``(cost, edges, canonical edge ids)`` for the optimal subnet."""
    check_feasibility(problem)
    cost, positions, status, ordered = run_core(problem)

    if status == "UNCONNECTED":
        # Defensive: check_feasibility should already have caught this.
        raise TopologyError(
            "ENDPOINTS_UNCONNECTED",
            "endpoints cannot be joined by any edge set",
        )
    if status == "OVERFLOW":  # pragma: no cover - documented hard boundary
        raise TopologyError(
            "COST_OVERFLOW",
            "total optimum cost exceeds the representable range",
        )

    selected = [ordered[pos] for pos in positions]
    chosen_ids = tuple(e.id for e in selected)
    if chosen_ids != tuple(sorted(chosen_ids)):
        raise RuntimeError("solver invariant: witness ids not ascending")

    _verify_witness(problem, selected, cost)
    return cost, selected, chosen_ids


def _verify_witness(
    problem: Problem, selected: list[Edge], claimed_cost: int
) -> None:
    """Defensive invariant check on the witness returned by the native core.

    It must be a single connected tree spanning every endpoint whose edge
    costs sum to the reported value. Any failure is an internal solver bug;
    it surfaces as a 500 rather than ever returning a partial subnet.
    """
    n = len(problem.nodes)
    dsu = _DSU(n)
    total = 0
    seen: set[str] = set()
    incident: set[int] = set()
    for e in selected:
        if e.id in seen:
            raise RuntimeError("solver invariant: duplicated witness edge")
        seen.add(e.id)
        total += e.cost
        dsu.union(e.source, e.target)
        incident.update((e.source, e.target))
    if total != claimed_cost:
        raise RuntimeError("solver invariant: witness cost mismatch")
    roots = {dsu.find(v) for v in problem.endpoints}
    if len(roots) != 1:
        raise RuntimeError("solver invariant: endpoints not connected")
    # A connected acyclic subgraph on its incident vertices satisfies
    # |edges| == |vertices| - 1; guard against accidental cycles.
    if len(selected) != len(incident) - 1:
        raise RuntimeError("solver invariant: witness is not a tree")


def build_adjacency(
    nodes: list[str], selected: list[Edge]
) -> dict[str, list[dict[str, object]]]:
    """Derive the connected adjacency list induced by the chosen edge set."""
    adj: dict[str, list[tuple[str, str, int]]] = {}
    for e in selected:
        s, t = nodes[e.source], nodes[e.target]
        adj.setdefault(s, []).append((t, e.id, e.cost))
        adj.setdefault(t, []).append((s, e.id, e.cost))
    return {
        label: [
            {"to": neighbor, "edge": eid, "cost": c}
            for neighbor, eid, c in sorted(
                adj[label], key=lambda x: (x[0], x[1])
            )
        ]
        for label in sorted(adj)
    }
