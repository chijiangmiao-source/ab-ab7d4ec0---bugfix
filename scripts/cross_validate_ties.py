#!/usr/bin/env python3
"""Targeted cross-validation: tie-heavy graphs and disconnected inputs.

- Tie-heavy: all weights in {1, 2} with frequent parallel edges, so many
  optimum witnesses exist and lexicographic arbitration is fully exercised.
- Disconnected: graphs generated as several components, terminal sets spread
  across them to verify UNCONNECTED.
"""

from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cross_validate import brute_force, run_core  # noqa: E402

BINARY = Path(__file__).resolve().parents[1] / "core" / "steiner"


def tie_instance(rng):
    n = rng.randint(3, 8)
    pairs = [(u, v) for u in range(n) for v in range(u + 1, n)]
    rng.shuffle(pairs)
    edges = []
    target = rng.randint(2, 14)
    for u, v in pairs:
        copies = rng.randint(1, 2)
        for _ in range(copies):
            edges.append((u, v, rng.choice([1, 1, 1, 2])))
            if len(edges) >= target:
                break
        if len(edges) >= target:
            break
    k = rng.randint(2, min(n, 5))
    terminals = sorted(rng.sample(range(n), k))
    return n, edges, terminals


def disconnected_instance(rng):
    n = rng.randint(3, 8)
    edges = []
    used = set()
    m = rng.randint(0, 8)
    for _ in range(m):
        u, v = sorted(rng.sample(range(n), 2))
        edges.append((u, v, rng.randint(1, 4)))
        used.add((u, v))
    k = rng.randint(2, min(n, 4))
    terminals = sorted(rng.sample(range(n), k))
    return n, edges, terminals


def main():
    rng = random.Random(424242)
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    for trial in range(trials):
        gen = tie_instance if trial % 2 == 0 else disconnected_instance
        n, edges, terminals = gen(rng)
        expect = brute_force(n, edges, terminals)
        got = run_core(n, edges, terminals)
        if expect != got:
            print("MISMATCH trial", trial, gen.__name__)
            print("n=", n, "edges=", edges, "terminals=", terminals)
            print("expected:", expect)
            print("core    :", got)
            sys.exit(1)
    print(f"ALL {trials} TARGETED TRIALS OK")


if __name__ == "__main__":
    main()
