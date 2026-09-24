#!/usr/bin/env python3
"""Brute-force cross-validation for the C Steiner core.

Enumerates every edge subset on small random graphs (this is an offline test
harness, never used by the service), computes the true minimum cost and the
lexicographically smallest sorted edge-id witness, and compares it with the
output of core/steiner.
"""

from __future__ import annotations

import itertools
import random
import subprocess
import sys
from pathlib import Path

BINARY = Path(__file__).resolve().parents[1] / "core" / "steiner"


def brute_force(n, edges, terminals):
    """Return (cost, tuple(sorted indices)) or (None, None) if infeasible."""
    best = None
    m = len(edges)
    for bits in range(1 << m):
        chosen = [j for j in range(m) if bits >> j & 1]
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        cost = 0
        for j in chosen:
            u, v, w = edges[j]
            cost += w
            ru, rv = find(u), find(v)
            if ru != rv:
                parent[ru] = rv
        root = find(terminals[0])
        if all(find(t) == root for t in terminals):
            key = (cost, tuple(sorted(chosen)))
            if best is None or key < best:
                best = key
    if best is None:
        return None, None
    return best


def run_core(n, edges, terminals):
    lines = [f"{n} {len(edges)} {len(terminals)}",
             " ".join(str(t) for t in terminals)]
    for u, v, w in edges:
        lines.append(f"{u} {v} {w}")
    proc = subprocess.run(
        [str(BINARY)], input="\n".join(lines) + "\n",
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"core failed: {proc.stderr}")
    out = proc.stdout.split()
    if out[0] == "UNCONNECTED":
        return None, None
    assert out[0] == "OK", out
    cost = int(out[1])
    count = int(out[2])
    idx = tuple(int(x) for x in out[3:3 + count])
    assert len(idx) == count
    assert idx == tuple(sorted(idx))
    return cost, idx


def random_instance(rng):
    n = rng.randint(2, 7)
    max_edges = n * (n - 1) // 2
    m = rng.randint(1, max(1, min(max_edges, rng.randint(1, 11))))
    pairs = [(u, v) for u in range(n) for v in range(u + 1, n)]
    rng.shuffle(pairs)
    edges = []
    # Possibly emit parallel edges (duplicate the same pair under a new id).
    for u, v in pairs[:m]:
        w = rng.randint(1, 6)
        edges.append((u, v, w))
        if rng.random() < 0.15 and len(edges) < 13:
            edges.append((u, v, rng.randint(1, 6)))
    k = rng.randint(2, min(n, 5))
    terminals = sorted(rng.sample(range(n), k))
    return n, edges, terminals


def main():
    rng = random.Random(20260924)
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    for trial in range(trials):
        n, edges, terminals = random_instance(rng)
        expect_cost, expect_set = brute_force(n, edges, terminals)
        got_cost, got_set = run_core(n, edges, terminals)
        if (expect_cost, expect_set) != (got_cost, got_set):
            print("MISMATCH on trial", trial)
            print("n=", n)
            print("edges (u,v,w):", edges)
            print("terminals:", terminals)
            print("expected:", expect_cost, expect_set)
            print("core    :", got_cost, got_set)
            sys.exit(1)
        if trial % 500 == 0:
            print(f"...{trial} trials ok")
    print(f"ALL {trials} TRIALS OK")


if __name__ == "__main__":
    main()
