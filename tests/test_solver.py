"""Unit tests for validation, the solver and the adjacency export.

Run with: ``python3 tests/harness.py test_solver`` from the repo root.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import assert_raises  # noqa: E402

from app.errors import TopologyError, ValidationError  # noqa: E402
from app.solver import build_adjacency, check_feasibility, solve  # noqa: E402
from app.validation import MAX_COST, parse_problem  # noqa: E402

CORE = Path(os.environ.get(
    "STEINER_CORE", Path(__file__).resolve().parents[1] / "core" / "steiner"
))
assert CORE.exists(), f"native core missing: {CORE}"


def make(nodes, edges, endpoints):
    return parse_problem(
        {"nodes": nodes, "edges": edges, "endpoints": endpoints}
    )


def edge(id_, s, t, c):
    return {"id": id_, "source": s, "target": t, "cost": c}


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

class TestValidation:
    def test_minimal_ok(self):
        p = make(["a", "b"], [edge("e1", "a", "b", 1)], ["a", "b"])
        assert len(p.edges) == 1
        assert p.endpoints == [0, 1]

    def test_parallel_edges_allowed(self):
        p = make(
            ["a", "b"],
            [edge("e1", "a", "b", 3), edge("e2", "b", "a", 2)],
            ["a", "b"],
        )
        assert len(p.edges) == 2

    def test_body_must_be_object(self):
        with assert_raises(ValidationError) as ctx:
            parse_problem(["not", "an", "object"])
        assert ctx.value.code == "INVALID_BODY"

    def test_missing_nodes(self):
        with assert_raises(ValidationError) as ctx:
            parse_problem({"edges": [], "endpoints": ["a", "b"]})
        assert ctx.value.code == "MISSING_FIELD"
        assert ctx.value.pointer == "/nodes"

    def test_node_count_too_small(self):
        with assert_raises(ValidationError) as ctx:
            parse_problem({"nodes": ["a"], "edges": [],
                           "endpoints": ["a", "a"]})
        assert ctx.value.code == "NODE_COUNT_OUT_OF_RANGE"

    def test_duplicate_node(self):
        with assert_raises(ValidationError) as ctx:
            parse_problem({"nodes": ["a", "a"], "edges": [],
                           "endpoints": ["a", "a"]})
        assert ctx.value.code == "DUPLICATE_NODE"
        assert ctx.value.pointer == "/nodes/1"

    def test_duplicate_edge_id(self):
        with assert_raises(ValidationError) as ctx:
            make(
                ["a", "b"],
                [edge("x", "a", "b", 1), edge("x", "a", "b", 2)],
                ["a", "b"],
            )
        assert ctx.value.code == "DUPLICATE_EDGE_ID"
        assert ctx.value.pointer == "/edges/1/id"

    def test_self_loop_rejected(self):
        with assert_raises(ValidationError) as ctx:
            make(["a", "b"], [edge("e", "a", "a", 1)], ["a", "b"])
        assert ctx.value.code == "SELF_LOOP"
        assert ctx.value.pointer == "/edges/0"

    def check_bad_cost(self, bad, code):
        with assert_raises(ValidationError) as ctx:
            make(["a", "b"], [edge("e", "a", "b", bad)], ["a", "b"])
        assert ctx.value.code == code, (bad, code, ctx.value.code)
        assert ctx.value.pointer == "/edges/0/cost"

    def test_bad_costs(self):
        for bad, code in [
            (0, "NON_POSITIVE_COST"),
            (-4, "NON_POSITIVE_COST"),
            (True, "INVALID_COST"),
            (2.5, "INVALID_COST"),
            ("7", "INVALID_COST"),
            (None, "INVALID_COST"),
        ]:
            self.check_bad_cost(bad, code)

    def test_cost_upper_bound(self):
        with assert_raises(ValidationError) as ctx:
            make(["a", "b"], [edge("e", "a", "b", MAX_COST + 1)],
                 ["a", "b"])
        assert ctx.value.code == "COST_OUT_OF_RANGE"

    def test_cost_at_upper_bound_ok(self):
        p = make(["a", "b"], [edge("e", "a", "b", MAX_COST)], ["a", "b"])
        assert p.edges[0].cost == MAX_COST

    def test_non_ascii_id(self):
        with assert_raises(ValidationError) as ctx:
            make(["a", "é"], [edge("e", "a", "é", 1)],
                 ["a", "é"])
        assert ctx.value.code == "NON_ASCII_ID"

    def test_empty_id(self):
        with assert_raises(ValidationError) as ctx:
            make(["a", "b"], [edge("", "a", "b", 1)], ["a", "b"])
        assert ctx.value.code == "EMPTY_ID"

    def test_unknown_endpoint(self):
        with assert_raises(ValidationError) as ctx:
            make(["a", "b"], [edge("e", "a", "b", 1)], ["a", "z"])
        assert ctx.value.code == "UNKNOWN_ENDPOINT"
        assert ctx.value.pointer == "/endpoints/1"

    def test_unknown_edge_node(self):
        with assert_raises(ValidationError) as ctx:
            make(["a", "b"], [edge("e", "a", "z", 1)], ["a", "b"])
        assert ctx.value.code == "UNKNOWN_NODE"

    def test_duplicate_endpoint(self):
        with assert_raises(ValidationError) as ctx:
            make(["a", "b"], [edge("e", "a", "b", 1)], ["a", "a"])
        assert ctx.value.code == "DUPLICATE_ENDPOINT"

    def test_endpoint_count_bounds(self):
        nodes = [f"n{i}" for i in range(11)]
        with assert_raises(ValidationError) as ctx:
            parse_problem({
                "nodes": nodes,
                "edges": [edge("e0", "n0", "n1", 1)],
                "endpoints": nodes[:11],
            })
        assert ctx.value.code == "ENDPOINT_COUNT_OUT_OF_RANGE"

    def test_edges_count_bounds(self):
        with assert_raises(ValidationError) as ctx:
            parse_problem(
                {"nodes": ["a", "b"], "edges": [], "endpoints": ["a", "b"]}
            )
        assert ctx.value.code == "EDGE_COUNT_OUT_OF_RANGE"

    def test_missing_edge_field(self):
        with assert_raises(ValidationError) as ctx:
            parse_problem({
                "nodes": ["a", "b"],
                "edges": [{"id": "e", "source": "a", "target": "b"}],
                "endpoints": ["a", "b"],
            })
        assert ctx.value.code == "MISSING_FIELD"
        assert ctx.value.pointer == "/edges/0/cost"


# --------------------------------------------------------------------------
# Solver: optimality, relays, tie arbitration, infeasibility
# --------------------------------------------------------------------------

class TestSolver:
    def test_single_edge(self):
        p = make(["a", "b"], [edge("e1", "a", "b", 7)], ["a", "b"])
        cost, selected, ids = solve(p)
        assert cost == 7
        assert ids == ("e1",)
        assert len(selected) == 1

    def test_mst_when_all_nodes_are_endpoints(self):
        nodes = ["a", "b", "c", "d"]
        edges = [
            edge("ab", "a", "b", 1),
            edge("bc", "b", "c", 2),
            edge("cd", "c", "d", 1),
            edge("da", "d", "a", 5),
            edge("ac", "a", "c", 9),
        ]
        p = make(nodes, edges, nodes)
        cost, _, ids = solve(p)
        assert cost == 4
        assert set(ids) == {"ab", "bc", "cd"}

    def test_steiner_relay_cheaper(self):
        nodes = ["a", "b", "c", "h"]
        edges = [
            edge("ha", "h", "a", 1),
            edge("hb", "h", "b", 1),
            edge("hc", "h", "c", 1),
            edge("ab", "a", "b", 3),
            edge("bc", "b", "c", 3),
            edge("ac", "a", "c", 3),
        ]
        p = make(nodes, edges, ["a", "b", "c"])
        cost, _, ids = solve(p)
        assert cost == 3
        assert set(ids) == {"ha", "hb", "hc"}

    def test_costly_branch_scanned_before_cheap_relay_chain(self):
        # Regression: in canonical (ascending id) order the scan first meets
        # the cost-2 branch A-X, which cannot belong to the optimal subnet,
        # and only afterwards the feasible low-cost relay chain A-Y, Y-B.
        # The optimum must still be found and witnessed canonically.
        nodes = ["A", "B", "X", "Y"]
        edges = [
            edge("ax-2", "A", "X", 2),
            edge("ay-1", "A", "Y", 1),
            edge("yb-1", "Y", "B", 1),
        ]
        p = make(nodes, edges, ["A", "B"])
        cost, selected, ids = solve(p)
        assert cost == 2
        assert ids == ("ay-1", "yb-1")
        assert sum(e.cost for e in selected) == cost
        # The exported adjacency is induced exactly by the selected edges:
        # the unused relay X must not appear, and every entry must be
        # backed by a selected edge (the two views recompute each other).
        adj = build_adjacency(p.nodes, selected)
        assert set(adj) == {"A", "B", "Y"}
        assert adj["A"] == [{"to": "Y", "edge": "ay-1", "cost": 1}]
        assert adj["B"] == [{"to": "Y", "edge": "yb-1", "cost": 1}]
        assert adj["Y"] == [
            {"to": "A", "edge": "ay-1", "cost": 1},
            {"to": "B", "edge": "yb-1", "cost": 1},
        ]
        by_id = {e.id: e for e in selected}
        for node, entries in adj.items():
            for entry in entries:
                e = by_id[entry["edge"]]
                assert {p.nodes[e.source], p.nodes[e.target]} == {
                    node,
                    entry["to"],
                }
                assert e.cost == entry["cost"]

    def test_costly_branch_first_request_order_independent(self):
        # Same topology as above with the request array shuffled: canonical
        # order follows ascending edge ids, not the request array order.
        nodes = ["A", "B", "X", "Y"]
        edges = [
            edge("yb-1", "Y", "B", 1),
            edge("ax-2", "A", "X", 2),
            edge("ay-1", "A", "Y", 1),
        ]
        p = make(nodes, edges, ["A", "B"])
        cost, _, ids = solve(p)
        assert cost == 2
        assert ids == ("ay-1", "yb-1")

    def test_parallel_edge_cheapest_chosen(self):
        nodes = ["a", "b"]
        edges = [edge("a-exp", "a", "b", 9), edge("a-cheap", "a", "b", 2)]
        p = make(nodes, edges, ["a", "b"])
        cost, _, ids = solve(p)
        assert cost == 2
        assert ids == ("a-cheap",)

    def test_tie_break_lexicographic_ids(self):
        nodes = ["a", "b", "c"]
        edges = [
            edge("zz-1", "a", "b", 5),
            edge("zz-2", "b", "c", 5),
            edge("aa-1", "a", "c", 5),
            edge("aa-2", "c", "b", 5),
        ]
        p = make(nodes, edges, ["a", "b", "c"])
        cost, _, ids = solve(p)
        assert cost == 10
        assert ids == ("aa-1", "aa-2")

    def test_tie_break_independent_of_request_order(self):
        nodes = ["a", "b", "c"]
        edges_shuffled = [
            edge("aa-1", "a", "c", 5),
            edge("zz-2", "b", "c", 5),
            edge("zz-1", "a", "b", 5),
            edge("aa-2", "c", "b", 5),
        ]
        p = make(nodes, edges_shuffled, ["a", "b", "c"])
        _, _, ids = solve(p)
        assert ids == ("aa-1", "aa-2")

    def test_tie_different_tree_shapes(self):
        nodes = ["t1", "t2", "x", "y"]
        edges = [
            edge("e-x1", "t1", "x", 2),
            edge("e-x2", "t2", "x", 2),
            edge("e-y1", "t1", "y", 2),
            edge("e-y2", "t2", "y", 2),
        ]
        p = make(nodes, edges, ["t1", "t2"])
        _, _, ids = solve(p)
        assert ids == ("e-x1", "e-x2")

    def test_dangling_endpoint(self):
        p = make(["a", "b", "c"], [edge("e", "a", "b", 1)], ["a", "c"])
        with assert_raises(TopologyError) as ctx:
            solve(p)
        assert ctx.value.code == "DANGLING_ENDPOINT"
        assert ctx.value.pointer == "/endpoints/1"

    def test_check_feasibility_passes_when_connected(self):
        p = make(["a", "b"], [edge("e", "a", "b", 1)], ["a", "b"])
        check_feasibility(p)

    def test_endpoints_in_separate_components(self):
        p = make(
            ["a", "b", "c", "d"],
            [edge("e1", "a", "b", 1), edge("e2", "c", "d", 1)],
            ["a", "c"],
        )
        with assert_raises(TopologyError) as ctx:
            solve(p)
        assert ctx.value.code == "ENDPOINTS_UNCONNECTED"
        assert ctx.value.components == [["a"], ["c"]]
        assert ctx.value.pointer == "/endpoints/1"

    def test_isolated_non_terminal_is_fine(self):
        p = make(
            ["a", "b", "loner"], [edge("e", "a", "b", 3)], ["a", "b"]
        )
        cost, _, ids = solve(p)
        assert cost == 3 and ids == ("e",)

    def test_adjacency_export(self):
        p = make(
            ["a", "b", "h"],
            [edge("e1", "a", "h", 2), edge("e2", "h", "b", 4)],
            ["a", "b"],
        )
        _, selected, _ = solve(p)
        adj = build_adjacency(p.nodes, selected)
        assert set(adj) == {"a", "b", "h"}
        assert adj["a"] == [{"to": "h", "edge": "e1", "cost": 2}]
        assert adj["b"] == [{"to": "h", "edge": "e2", "cost": 4}]
        assert adj["h"] == [
            {"to": "a", "edge": "e1", "cost": 2},
            {"to": "b", "edge": "e2", "cost": 4},
        ]

    def test_witness_cost_matches_edges(self):
        # The exported edges must sum exactly to the reported cost.
        nodes = [f"n{i}" for i in range(6)]
        edges = [
            edge("e0", "n0", "n1", 4),
            edge("e1", "n1", "n2", 3),
            edge("e2", "n2", "n3", 2),
            edge("e3", "n3", "n4", 5),
            edge("e4", "n4", "n5", 1),
            edge("e5", "n1", "n4", 20),
        ]
        p = make(nodes, edges, ["n0", "n3", "n5"])
        cost, selected, _ = solve(p)
        assert sum(e.cost for e in selected) == cost
