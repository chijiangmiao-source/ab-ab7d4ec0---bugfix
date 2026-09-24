# 低温探测阵列校准端点子网审计服务

给定无向图（光纤节点与带正整数成本的干线/支路）和 2–10 个必须连通的校准
端点，返回**总成本最低且连通全部端点的边集**（非端点可作为中继，即 Steiner
树问题），并在同成本方案中以**升序边标识列表的字典序**给出唯一规范见证，
同时返回由该边集导出的连通邻接表。

- API：Python 3 标准库实现，零第三方依赖。
- 求解内核：C11 实现的终端子集动态规划（Dreyfus–Wagner）、节点汇聚合并
  （并查集）与多源最短路闭包（多源 Dijkstra）；不枚举边集、不调用通用
  优化器。
- 交付：`Dockerfile` 多阶段构建；`compose.yml` 暴露可配置宿主机端口、
  以 `/healthz` 做健康检查，并运行一次性 `verify` 服务。

## 目录结构

```
app/            HTTP 服务、请求校验、求解器 Python 前端
core/steiner.c  原生求解内核（终端子集 DP + 汇聚合并 + 多源最短路闭包）
tests/          单元测试、构建产物检查、HTTP 冒烟、verify 总入口
scripts/        离线暴力交叉验证脚本（不属于服务）
Dockerfile      gcc 编译内核 + slim 运行镜像
compose.yml     api 服务 + 一次性 verify 服务
```

## 快速开始（Docker Compose）

```bash
# 宿主机端口可配置，默认 8080
AUDIT_HOST_PORT=9090 docker compose build
AUDIT_HOST_PORT=9090 docker compose up \
    --abort-on-container-exit --exit-code-from verify
```

- `api` 在容器内监听 8080，宿主机通过 `${AUDIT_HOST_PORT:-8080}` 访问；
  Compose 健康检查周期性请求 `GET /healthz`。
- `verify` 等 `api` 健康后运行一次：求解器单元测试 → 构建产物检查 →
  HTTP 冒烟（含并列裁决与无解边界），随后以退出码报告结果（0 成功）。

仅启动 API：

```bash
AUDIT_HOST_PORT=9090 docker compose up api
curl -s http://127.0.0.1:9090/healthz
```

## 本地运行（无 Docker）

```bash
gcc -O2 -std=c11 -Wall -Wextra -o core/steiner core/steiner.c
AUDIT_PORT=8080 python3 -m app.server
# 另一终端：
python3 tests/verify.py            # 需先启动服务，默认 http://127.0.0.1:8080
```

## API

### `GET /healthz`

```json
{"status": "ok"}
```

### `POST /api/audit`

请求：

```json
{
  "nodes": ["A", "B", "C", "R"],
  "edges": [
    {"id": "e1", "source": "A", "target": "R", "cost": 5},
    {"id": "e2", "source": "B", "target": "R", "cost": 5},
    {"id": "e3", "source": "C", "target": "R", "cost": 5},
    {"id": "p1", "source": "A", "target": "B", "cost": 9}
  ],
  "endpoints": ["A", "B", "C"]
}
```

约束：节点 2–60 个且为唯一 ASCII 标识；边 1–220 条，边标识唯一、成本为
正整数；允许平行边，禁止自环；端点 2–10 个且必须在节点表中声明。

成功响应（200）：

```json
{
  "cost": 15,
  "edge_set": ["e1", "e2", "e3"],
  "edges": [
    {"id": "e1", "source": "A", "target": "R", "cost": 5},
    {"id": "e2", "source": "B", "target": "R", "cost": 5},
    {"id": "e3", "source": "C", "target": "R", "cost": 5}
  ],
  "adjacency": {
    "A": [{"to": "R", "edge": "e1", "cost": 5}],
    "B": [{"to": "R", "edge": "e2", "cost": 5}],
    "C": [{"to": "R", "edge": "e3", "cost": 5}],
    "R": [
      {"to": "A", "edge": "e1", "cost": 5},
      {"to": "B", "edge": "e2", "cost": 5},
      {"to": "C", "edge": "e3", "cost": 5}
    ]
  }
}
```

- `cost`：最低总成本；`edge_set`：按 ASCII 升序排列的规范边标识；
- `edges`：对应边的完整描述（顺序无关，按 id 排序）；
- `adjacency`：**仅由所选边集导出**的连通邻接表（未使用节点不出现）。

### 错误（稳定且可定位，绝不返回部分子网或沿用上次结果）

错误体形如 `{"code": ..., "message": ..., "pointer": ...}`，`pointer` 为
JSON Pointer 风格定位（如 `/edges/3/cost`、`/endpoints/1`）。

| HTTP | code | 触发条件 |
| --- | --- | --- |
| 400 | `MALFORMED_JSON` / `INVALID_BODY` | 非 JSON 或根不是对象 |
| 400 | `NODE_COUNT_OUT_OF_RANGE` / `DUPLICATE_NODE` / `NON_ASCII_ID` / `EMPTY_ID` | 节点问题 |
| 400 | `EDGE_COUNT_OUT_OF_RANGE` / `DUPLICATE_EDGE_ID` / `SELF_LOOP` / `UNKNOWN_NODE` | 边结构问题 |
| 400 | `INVALID_COST` / `NON_POSITIVE_COST` / `COST_OUT_OF_RANGE` | 成本问题 |
| 400 | `ENDPOINT_COUNT_OUT_OF_RANGE` / `DUPLICATE_ENDPOINT` / `UNKNOWN_ENDPOINT` | 端点问题 |
| 422 | `DANGLING_ENDPOINT` | 端点无任何关联边（悬空） |
| 422 | `ENDPOINTS_UNCONNECTED` | 端点位于不同连通分量（无法连通），附 `components` |
| 405/404/411/413/500 | 对应语义码 | 方法错误、路径不存在、缺少长度、载荷过大、内部错误 |

每次请求都重新构建问题并启动一次独立内核进程，失败不会残留任何状态。

## 算法与并列裁决

1. **终端子集动态规划（Dreyfus–Wagner）**：`dp[mask][v]` 表示连通
   `mask` 中端点且触及节点 `v` 的最小代价树。
2. **节点汇聚合并**：可行性阶段用并查集判定悬空端点与跨分量端点；DP
   合并阶段在同一顶点连接两个终端子树（枚举所有二元划分，锚定位去重）。
3. **多源最短路闭包**：每个子集以全部顶点的合并标签为种子，跑一次
   多源 Dijkstra 完成闭包松弛。
4. **规范见证**：仅在 DP 状态中保留单一（代价，见证）对不能保证全局
   字典序最优。因此先求出最优成本 `B`，再按边标识 ASCII 升序做贪心前缀
   扫描——对每条边询问“是否存在包含已选前缀与该边、成本恰为 `B` 的连通
   树”，该 oracle 用并查集收缩必选边后在收缩多重图上再做一次 Steiner DP。
   这是多项式次数的 DP 调用（≤ 边数 + 1），不枚举边集。

`scripts/cross_validate*.py` 对 5000+ 随机小图与暴力枚举逐项核对
（成本、无解、字典序见证、平行边、并列）。
