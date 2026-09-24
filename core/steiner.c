/*
 * steiner.c -- minimum Steiner tree core with canonical tie breaking.
 *
 * Implemented from scratch (no generic optimiser, no edge-set enumeration):
 *   - terminal-subset dynamic programming (Dreyfus-Wagner recurrence),
 *   - node aggregation merge (disjoint-set union, also used to contract
 *     mandatory edges during witness reconstruction),
 *   - multi-source shortest-path closure (one multi-source Dijkstra per
 *     terminal subset, every vertex seeded at once).
 *
 * Canonical witness: after the optimal cost B is known, the lexicographically
 * smallest sorted edge-id list among all B-cost witness trees is built by a
 * greedy prefix scan over edges in canonical (input) order. For each candidate
 * edge the solver asks whether an optimal tree exists that (a) contains every
 * edge already required, (b) avoids every edge already rejected, and (c)
 * contains the candidate. That oracle contracts the required edges through
 * DSU node aggregation and runs one Steiner DP on the contracted multigraph.
 * Keeping only the single best (cost, witness) pair per DP state would not be
 * sound for lexicographic minimisation, so cost DP and witness reconstruction
 * are deliberately separate phases.
 *
 * Input (stdin, text):
 *   line 1: n m k
 *   line 2: k terminal vertex indices
 *   next m lines: u v cost      (edge input order = canonical edge-id order)
 *
 * Output (stdout):
 *   OK
 *   <cost>
 *   <number of selected edge indices>
 *   <zero-based edge indices in input order, one per line>
 * or:
 *   UNCONNECTED
 * or:
 *   OVERFLOW
 */

#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_N 64
#define MAX_M 256
#define MAX_K 10
#define MAX_STATES (1 << MAX_K)
/* Saturated infinity; every reachable answer is strictly smaller. */
#define INF (LLONG_MAX / 4)

typedef long long ll;

static int n, m, k;
static int eu[MAX_M], ev[MAX_M];
static ll ew[MAX_M];
static int term[MAX_K];

static ll dp[MAX_STATES][MAX_N];

/* Forward-star graph rebuilt for every feasibility oracle. */
typedef struct {
    int to;
    int next;
    ll w;
} Arc;
static Arc arcs[2 * MAX_M];
static int first[MAX_N];
static int arc_count;

static void add_arc(int u, int v, ll w) {
    arcs[arc_count].to = v;
    arcs[arc_count].w = w;
    arcs[arc_count].next = first[u];
    first[u] = arc_count++;
}

static ll add_sat(ll a, ll b, int *overflow) {
    if (a >= INF || b >= INF || a > INF - b) {
        *overflow = 1;
        return INF;
    }
    return a + b;
}

/* ---- disjoint-set union (node aggregation) ---------------------------- */

static int dsu_parent[MAX_N];
static int dsu_rankk[MAX_N];

/* Independent DSU tracking the greedy scan's required-edge forest; the
 * feasibility oracle mutates dsu_parent/dsu_rankk via dsu_reset(), so the two
 * must not share storage. */
static int scan_parent[MAX_N];
static int scan_rankk[MAX_N];

static void dsu_reset(int count) {
    for (int i = 0; i < count; i++) {
        dsu_parent[i] = i;
        dsu_rankk[i] = 0;
    }
}

static int dsu_find(int x) {
    while (dsu_parent[x] != x) {
        dsu_parent[x] = dsu_parent[dsu_parent[x]];
        x = dsu_parent[x];
    }
    return x;
}

static void dsu_union(int a, int b) {
    int ra = dsu_find(a), rb = dsu_find(b);
    if (ra == rb) return;
    if (dsu_rankk[ra] < dsu_rankk[rb]) {
        int t = ra; ra = rb; rb = t;
    }
    dsu_parent[rb] = ra;
    if (dsu_rankk[ra] == dsu_rankk[rb]) dsu_rankk[ra]++;
}

static int scan_find(int x) {
    while (scan_parent[x] != x) {
        scan_parent[x] = scan_parent[scan_parent[x]];
        x = scan_parent[x];
    }
    return x;
}

static void scan_union(int a, int b) {
    int ra = scan_find(a), rb = scan_find(b);
    if (ra == rb) return;
    if (scan_rankk[ra] < scan_rankk[rb]) {
        int t = ra; ra = rb; rb = t;
    }
    scan_parent[rb] = ra;
    if (scan_rankk[ra] == scan_rankk[rb]) scan_rankk[ra]++;
}

/* ---- binary heap for multi-source Dijkstra ---------------------------- */

typedef struct { ll d; int v; } HeapItem;

/* Dijkstra performs O(V*E) successful relaxations per closure even on the
 * largest instance; a generous power-of-two arena keeps the static heap safe
 * with a hard guard in heap_push(). */
#define HEAP_CAP (1 << 18)
static HeapItem heap[HEAP_CAP];
static int heap_size;

static void heap_push(ll d, int v) {
    if (heap_size >= HEAP_CAP) {
        fprintf(stderr, "search heap exhausted\n");
        exit(4);
    }
    int i = heap_size++;
    heap[i].d = d;
    heap[i].v = v;
    while (i > 0) {
        int p = (i - 1) / 2;
        if (heap[p].d <= heap[i].d) break;
        HeapItem t = heap[p]; heap[p] = heap[i]; heap[i] = t;
        i = p;
    }
}

static HeapItem heap_pop(void) {
    HeapItem top = heap[0];
    heap[0] = heap[--heap_size];
    int i = 0;
    for (;;) {
        int l = 2 * i + 1, r = l + 1, best = i;
        if (l < heap_size && heap[l].d < heap[best].d) best = l;
        if (r < heap_size && heap[r].d < heap[best].d) best = r;
        if (best == i) break;
        HeapItem t = heap[best]; heap[best] = heap[i]; heap[i] = t;
        i = best;
    }
    return top;
}

/*
 * Terminal-subset DP on the graph currently held in `arcs`.
 * Fills dp[mask][v] for the given `cn` vertices and returns
 * min_v dp[all][v]. Every call overwrites all rows it reads.
 */
static ll steiner_dp(int cn, int ck, const int *cterms, int *overflow) {
    int cstates = 1 << ck;
    int full = cstates - 1;

    for (int s = 0; s < cstates; s++)
        for (int v = 0; v < cn; v++) dp[s][v] = INF;
    for (int i = 0; i < ck; i++)
        dp[1 << i][cterms[i]] = 0;

    ll merged[MAX_N];

    for (int mask = 1; mask <= full; mask++) {
        int bits = 0;
        for (int x = mask; x; x &= x - 1) bits++;

        if (bits == 1) {
            for (int v = 0; v < cn; v++) merged[v] = dp[mask][v];
        } else {
            for (int v = 0; v < cn; v++) merged[v] = INF;
            int anchor = mask & -mask;
            /* Node aggregation merge: join two terminal subtrees at one
             * vertex; requiring the anchor bit visits each bipartition once. */
            for (int sub = (mask - 1) & mask; sub; sub = (sub - 1) & mask) {
                if (!(sub & anchor)) continue;
                int other = mask ^ sub;
                for (int v = 0; v < cn; v++) {
                    ll a = dp[sub][v], b = dp[other][v];
                    if (a >= INF || b >= INF) continue;
                    ll val = add_sat(a, b, overflow);
                    if (val < merged[v]) merged[v] = val;
                }
            }
        }

        /* Multi-source shortest-path closure: all vertices are seeded with
         * their merge label at the same time. */
        heap_size = 0;
        for (int v = 0; v < cn; v++) {
            dp[mask][v] = merged[v];
            if (merged[v] < INF) heap_push(merged[v], v);
        }
        while (heap_size > 0) {
            HeapItem top = heap_pop();
            if (top.d != dp[mask][top.v]) continue;  /* stale label */
            for (int a = first[top.v]; a != -1; a = arcs[a].next) {
                int w = arcs[a].to;
                int ovf = 0;
                ll nd = add_sat(top.d, arcs[a].w, &ovf);
                if (ovf) { *overflow = 1; continue; }
                if (nd < dp[mask][w]) {
                    dp[mask][w] = nd;
                    heap_push(nd, w);
                }
            }
        }
    }

    ll best = INF;
    for (int v = 0; v < cn; v++)
        if (dp[full][v] < best) best = dp[full][v];
    return best;
}

/*
 * Feasibility oracle for the greedy scan.
 *
 * Returns 1 iff a witness of total cost <= B contains every required[] edge,
 * avoids every excluded[] edge, and additionally contains `extra` (-1 for no
 * extra edge). Required edges are contracted by DSU node aggregation; their
 * fixed cost is added to a Steiner DP on the contracted multigraph.
 */
static int can_extend(const char *required, const char *excluded, int extra,
                      ll fixed_cost, ll B) {
    dsu_reset(n);
    ll base = fixed_cost;
    int ovf = 0;

    for (int j = 0; j < m; j++)
        if (required[j]) dsu_union(eu[j], ev[j]);
    if (extra >= 0) {
        dsu_union(eu[extra], ev[extra]);
        base = add_sat(base, ew[extra], &ovf);
        if (ovf || base > B) return 0;
    }

    /* Renumber DSU components. */
    int comp_id[MAX_N];
    memset(comp_id, -1, sizeof(comp_id));
    int cn = 0;
    for (int v = 0; v < n; v++) {
        int r = dsu_find(v);
        if (comp_id[r] == -1) comp_id[r] = cn++;
    }

    /* Terminals mapped onto components, de-duplicated in order. */
    int cterms[MAX_K];
    int ck = 0;
    for (int i = 0; i < k; i++) {
        int c = comp_id[dsu_find(term[i])];
        int seen = 0;
        for (int j = 0; j < ck; j++)
            if (cterms[j] == c) { seen = 1; break; }
        if (!seen) cterms[ck++] = c;
    }

    /* Usable, non-excluded edges between distinct components become arcs. */
    memset(first, -1, sizeof(first));
    arc_count = 0;
    for (int j = 0; j < m; j++) {
        if (required[j] || excluded[j] || j == extra) continue;
        int a = comp_id[dsu_find(eu[j])];
        int b = comp_id[dsu_find(ev[j])];
        if (a == b) continue;  /* internal to a contracted component */
        add_arc(a, b, ew[j]);
        add_arc(b, a, ew[j]);
    }

    ll tail;
    if (ck <= 1) {
        tail = 0;  /* terminals already aggregated into one component */
    } else {
        tail = steiner_dp(cn, ck, cterms, &ovf);
    }

    if (ovf || tail >= INF) return 0;
    ll total = add_sat(base, tail, &ovf);
    return !ovf && total <= B;
}

int main(void) {
    if (scanf("%d %d %d", &n, &m, &k) != 3) {
        fprintf(stderr, "bad header\n");
        return 2;
    }
    if (n < 1 || n > MAX_N || m < 0 || m > MAX_M || k < 1 || k > MAX_K) {
        fprintf(stderr, "size out of range\n");
        return 2;
    }
    for (int i = 0; i < k; i++) {
        if (scanf("%d", &term[i]) != 1) return 2;
        if (term[i] < 0 || term[i] >= n) return 2;
    }
    for (int j = 0; j < m; j++) {
        long long c;
        if (scanf("%d %d %lld", &eu[j], &ev[j], &c) != 3) return 2;
        if (eu[j] < 0 || eu[j] >= n || ev[j] < 0 || ev[j] >= n || c <= 0) {
            fprintf(stderr, "bad edge %d\n", j);
            return 2;
        }
        ew[j] = (ll)c;
    }

    /* Global optimum on the uncontracted graph. */
    memset(first, -1, sizeof(first));
    arc_count = 0;
    for (int j = 0; j < m; j++) {
        add_arc(eu[j], ev[j], ew[j]);
        add_arc(ev[j], eu[j], ew[j]);
    }
    char required[MAX_M] = {0};
    char excluded[MAX_M] = {0};
    int overflow = 0;
    ll B = steiner_dp(n, k, term, &overflow);

    if (B >= INF) {
        printf("%s\n", overflow ? "OVERFLOW" : "UNCONNECTED");
        return 0;
    }

    /*
     * Greedy prefix scan in canonical edge order. `scan_*` tracks the forest
     * of required edges, so a candidate whose endpoints are already joined
     * can never belong to a tree witness and is rejected without an oracle.
     */
    for (int v = 0; v < n; v++) {
        scan_parent[v] = v;
        scan_rankk[v] = 0;
    }
    ll fixed_cost = 0;
    for (int j = 0; j < m; j++) {
        int would_cycle =
            scan_find(eu[j]) == scan_find(ev[j]);
        if (!would_cycle &&
                fixed_cost <= B - ew[j] &&
                can_extend(required, excluded, j, fixed_cost, B)) {
            required[j] = 1;
            fixed_cost += ew[j];
            scan_union(eu[j], ev[j]);

            /* Witness complete: all terminals joined with fixed cost B. */
            int root = scan_find(term[0]);
            int joined = 1;
            for (int i = 1; i < k; i++)
                if (scan_find(term[i]) != root) { joined = 0; break; }
            if (joined) break;  /* fixed_cost must equal B by optimality */
        } else {
            excluded[j] = 1;
        }
    }

    int count = 0;
    for (int j = 0; j < m; j++) if (required[j]) count++;

    /* Final consistency: the required forest must join every terminal and
     * cost exactly B. Any failure here is an internal bug, never a partial
     * witness to the caller -- the process exits non-zero. */
    {
        ll check = 0;
        int joined = 1;
        int root = scan_find(term[0]);
        for (int j = 0; j < m; j++)
            if (required[j]) check += ew[j];
        for (int i = 1; i < k; i++)
            if (scan_find(term[i]) != root) joined = 0;
        if (!joined || check != B) {
            fprintf(stderr,
                    "witness reconstruction failed: joined=%d cost=%lld B=%lld\n",
                    joined, check, B);
            return 3;
        }
    }

    printf("OK\n%lld\n%d\n", B, count);
    for (int j = 0; j < m; j++)
        if (required[j]) printf("%d\n", j);
    return 0;
}
