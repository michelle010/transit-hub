# 城市中转枢纽

中国大陆城市交通枢纽中转决策工具。当前实现覆盖城市/枢纽基础数据、查询 API、AMap POI/路径 Provider、离线 GTFS 铁路时刻表 POC、Transfer Evaluation API，以及首个浏览器查询流程。页面通过后端返回的计划车次、预计接驳时间和解释原因比较同城铁路客运站。

本仓库不提供票务、价格、酒店或通用旅行功能。Fixture 中的路线时间和车次均为明确标记的测试数据，不能作为真实出行信息。

## 环境要求

- Python 3.12 或更高版本
- `uv`
- Node.js 22.12 或更高版本及 npm（Vitest 5 的最低版本要求）
- Docker Compose（用于本地 PostgreSQL）

## 启动 PostgreSQL

在仓库根目录执行：

```bash
cp .env.example .env
docker compose up -d db
```

`.env.example` 中的数据库账号仅供本机开发容器使用，不可用于生产。`.env` 已被 Git 忽略。

## 启动后端

```bash
cd apps/api
uv sync --dev
uv run alembic upgrade head
uv run python -m app.db.seed
uv run uvicorn app.main:app --reload
```

默认 API 地址为 <http://127.0.0.1:8000>。基础健康检查为
<http://127.0.0.1:8000/health>；运维探针分别为
`GET /api/health/live`（只检查进程）和 `GET /api/health/ready`（检查数据库、Alembic、
canonical registry 及当前 Provider 配置）。`ready` 在检查失败时返回 HTTP 503，响应只包含
稳定状态码，不包含 DSN、API Key 或堆栈。城市搜索与城市枢纽 API 分别为：

```text
GET /api/cities/search?q=成都
GET /api/cities/{city_id}/hubs
```

城市搜索响应包含 `query`、`count`、`items`；城市枢纽响应包含 `city`、`count`、`items`。API 错误使用 `{ "error": { "code", "message", "details" } }` 结构。

## 启动前端

在仓库根目录另开终端：

```bash
npm install
cp apps/web/.env.example apps/web/.env.local
npm run web:dev
```

打开 <http://localhost:3000>。Next.js 服务端从 `apps/web/.env.local` 读取 `API_BASE_URL` 调用 FastAPI；同一个变量也用于 Next.js rewrite，把浏览器的同源 `/api/transfer/evaluate` 请求转发到 FastAPI。这个变量不会发送到浏览器，也没有任何 `NEXT_PUBLIC_*` Provider Key。

首页的查询表单支持中转城市、到达枢纽、目的城市、到达日期/时间、托运行李状态以及公共交通/驾车方式。日期和时间会按中国大陆 `Asia/Shanghai` 语义组合为带 `+08:00` 的 `arrival_at`，不会使用浏览器所在机器的时区。正常运行时结果只来自真实 `POST /api/transfer/evaluate` 响应；前端不会调用 AMap 或铁路 Provider，也不会在后端失败时静默切换到 Fixture 结果。

城市和到达枢纽输入使用 canonical registry 的同源发现接口：`GET /api/cities/search?q=` 和
`GET /api/cities/{city_id}/hubs`。输入建议使用 200ms debounce，城市枢纽建议只保留当前城市中 active 的
机场和客运铁路站。建议是输入辅助，不是校验门槛；可以选择 canonical 名称，也可以直接提交后端支持的别名或自由文本。
发现请求失败或没有结果时输入仍然可编辑和提交。前端不会调用 AMap POI、GTFS 或其他外部搜索服务来做 autocomplete。

前端会保留后端候选顺序和状态，展示首选站、建议出发时间、理论最早到站时间、最早推荐车次、可行/推荐车次数量、原因以及部分数据提示。候选站支持比较不同接驳方式，并可展开查看后端已经返回的计划车次；车次状态会显示为“余量充足”“较稳妥”“时间偏紧”或“来不及”，不会在浏览器重新计算。分数只用于后端确定顺序，不作为概率或保证展示；分数接近的两个可行方案会得到中性的“值得比较”提示。`PARTIAL` 是可展示的成功结果，不会被当作请求错误；没有推荐站时也会保留候选列表。

## 验证

后端：

```bash
cd apps/api
uv run ruff check .
uv run pytest
```

前端（在仓库根目录）：

```bash
npm run lint
npm test
npm run typecheck
npm run format:check
```

确定性的浏览器回归（Playwright 会拦截城市、枢纽和 Transfer API，不需要 PostgreSQL、AMap Key、GTFS 或互联网）：

```bash
npx playwright install chromium
npm run web:e2e
npm run web:e2e:headed
```

在已安装 Google Chrome 的 macOS 开发机上，也可以使用系统浏览器运行同一套测试：

```bash
PLAYWRIGHT_CHANNEL=chrome npm run web:e2e
```

Playwright 配置包含 `desktop-chromium`（`Asia/Shanghai`）、`desktop-los-angeles`
（`America/Los_Angeles`）和 `mobile-chromium` 项目，覆盖 alias 提交、发现失败/竞态、
PARTIAL、无推荐、API 错误、车次展开、跨午夜显示、请求字段和移动端横向溢出。真实
FastAPI/PostgreSQL/AMap/GTFS 的手工 smoke 与这套离线 E2E 分开，不能用 E2E fixture 代替真实 Provider 验证。

空数据库迁移：先启动 Compose 数据库，再在 `apps/api` 下执行 `uv run alembic upgrade head`。当前 migration 到 `0007_create_hub_reconciliation_overrides`。Seed 使用稳定标识并可重复执行，不会因重复运行添加重复城市、枢纽、别名或 Provider 引用。

## 配置

仓库根目录的 `.env.example` 预留 `DATABASE_URL`、`AMAP_API_KEY`、`AMAP_BASE_URL`、超时、有限重试、请求级 operation budget、进程内并发上限和缓存 TTL、`CANDIDATE_EVALUATION_MAX_CONCURRENCY`、`RAIL_PROVIDER`、`RAIL_GTFS_PATH`、`RAIL_DATA_STALE_AFTER_DAYS`、`RAIL_SEARCH_CACHE_ENABLED`、`RAIL_SEARCH_CACHE_TTL_SECONDS`、`RAIL_SEARCH_CACHE_MAX_ENTRIES`、`RAIL_SEARCH_HORIZON_HOURS`、`APP_ENV` 和 `LOG_LEVEL`；`apps/web/.env.example` 预留 Next.js 服务端使用的 `API_BASE_URL`。高德 Key 只在后端读取，绝不使用 `NEXT_PUBLIC_AMAP_API_KEY`。

AMap 配置方式：申请高德 Web 服务 API Key 后，将其写入本机 `.env` 的 `AMAP_API_KEY`，不要写入源码或前端环境变量。AMap Provider 只负责 POI 发现/校验与路线查询，不会自动把 POI 写入 canonical hub registry。没有 Key 时，默认测试与 Fixture Provider 完全不需要网络。

### Public launch licensing review（BL-105）

发布前的 AMap 账号/服务授权、路线缓存与展示条款、铁路 GTFS 来源权利、派生数据存储/展示和归属声明审计记录在 [`docs/PUBLIC_LAUNCH_REVIEW.md`](docs/PUBLIC_LAUNCH_REVIEW.md)。当前工程审计已完成，但公共或商业发布闸门仍为 **BLOCKED**，因为仓库没有这些外部权利确认；不要把可用的 API Key 或可下载的 feed 视为发布授权。

### Public launch evidence closure（T-098）

逐闸门证据登记在 [`docs/PUBLIC_LAUNCH_EVIDENCE.md`](docs/PUBLIC_LAUNCH_EVIDENCE.md)。可离线检查当前登记是否结构完整以及是否仍有发布阻塞：

```bash
uv run --project apps/api python scripts/check_public_launch_readiness.py
uv run --project apps/api python scripts/check_public_launch_readiness.py --json
```

退出码 `0` 表示登记结果为 `CLEAR`，`1` 表示有效但为 `CONDITIONAL`/`BLOCKED`，`2` 表示登记缺失或格式无效。Checker 只验证证据元数据，不替代供应商合同、账号授权或法律判断；私有合同、账号信息和 Secret 不应提交到 Git。

## Generic Hub reconciliation（BL-014）

Provider adapter 先把 GTFS stop 或 AMap POI 转换成 provider-independent 的
`ProviderHubCandidate`，再交给 `HubReconciliationService`。通用层不会读取 GTFS
raw row 或 AMap raw JSON，也不会自动创建或合并 canonical Hub：

```text
provider-specific normalization
  → ProviderHubCandidate
  → HubReconciliationService / CanonicalHubIndex
  → HubReconciliationResult
```

结果明确区分：

- `MATCHED`：唯一安全匹配，带 canonical Hub ID 和匹配策略；
- `AMBIGUOUS`：存在多个同等候选，保留稳定排序的 candidate IDs；
- `UNRESOLVED`：未知对象、类型冲突或城市冲突，没有安全的自动匹配。

匹配优先级是确定性的：已有 `provider + provider_id` 引用、exact alias、规范化
canonical name、规范化 alias，并在有城市信息时使用城市约束。Hub type 是强约束，机场
不会因为名称相似匹配铁路站；城市冲突也不会被第一条数据库记录覆盖。坐标不会跨 WGS84
与 GCJ02 静默比较；不同名称策略指向不同 canonical Hub 时返回显式 `STRATEGY_CONFLICT`
歧义。当前 reconciliation 不依赖新的地理编码或 AMap 请求。

GTFS `station_mapper.py` 现在只是适配器：它把 `GTFSStop` 转换为
`ProviderHubCandidate`，调用同一套 generic matching policy，再把结果转换回现有的
`StationMatch`/`StationReconciliationReport`，因此 importer 的 provider refs、未匹配和
歧义诊断保持兼容。AMap 已有的 `HubCandidate` 也可以通过同一 normalized boundary
参与机场/铁路 reconciliation；POI 过滤仍在 AMap adapter 内完成。

reconciliation diagnostics 通过结构化事件记录 provider、hub type、结果、策略、reason
code、candidate count 和规则版本，不记录 raw provider payload、Secret 或本地绝对路径。
`TransferPreflightService` 继续把必需铁路 Hub 的 provider reference 作为 live gate；普通
reconciliation 只产生结果，不负责 operator 决策。

### Manual Hub correction（BL-015）

当 GTFS、AMap 或其他 Provider 的规范化 identity 无法安全自动匹配时，运维人员可以
通过 `scripts/manage_hub_overrides.py` 将一个明确的
`provider + provider_object_type + provider_hub_id` 映射到已经存在的 canonical Hub：
下面的 UUID 和 provider ID 是示例，执行前替换成实际稳定标识。

```bash
uv run --project apps/api python scripts/manage_hub_overrides.py apply \
  --provider CHINA_RAILWAY_GTFS --provider-object-type STOP \
  --provider-hub-id stop-example \
  --canonical-hub-id 00000000-0000-0000-0000-000000000000 \
  --hub-type RAILWAY --operator operator@example.com \
  --reason "已根据运营方站点清单确认" --json
```

命令还支持 `inspect`、`history`、`revoke`，以及带 `--replace` 的原子替换。每次
apply/replace/revoke 都写入当前覆盖记录和 append-only audit event；同一 Provider identity
最多只有一个 ACTIVE 覆盖，ProviderRef 会在同一事务中同步，撤销时恢复原来的引用状态。
覆盖层优先于 BL-014 自动 reconciliation，但仍会验证目标 Hub 存在、active、是客运 Hub、
类型和城市一致。目标失效或 ProviderRef 被外部改动时会返回明确的数据质量错误，不会静默
回退到名称匹配。operator identity 是审计输入，不是认证证明；本项目没有为该 CLI 增加
公开 admin API。操作日志只记录稳定 ID、状态和耗时，不记录 correction reason、raw payload
或 Secret。

查询和撤销使用相同的稳定 Provider identity：

```bash
uv run --project apps/api python scripts/manage_hub_overrides.py inspect \
  --provider CHINA_RAILWAY_GTFS --provider-object-type STOP \
  --provider-hub-id stop-example --json
uv run --project apps/api python scripts/manage_hub_overrides.py history \
  --provider CHINA_RAILWAY_GTFS --provider-object-type STOP \
  --provider-hub-id stop-example --json
uv run --project apps/api python scripts/manage_hub_overrides.py revoke \
  --provider CHINA_RAILWAY_GTFS --provider-object-type STOP \
  --provider-hub-id stop-example --operator operator@example.com \
  --reason "已确认原 Provider 引用恢复" --json
```

可选的真实接口 Smoke Test：

```bash
uv run --project apps/api python scripts/amap_smoke.py
```

该命令会尝试查询“成都天府国际机场 → 成都东站”的公交和驾车路线，输出当前实时结果，不参与 pytest，也不对实时耗时做 snapshot 断言。AMap 使用 Web Service 2.0 的 POI 文本搜索、公交综合路径和驾车路径接口；正式上线前仍需评估配额、缓存和服务条款。

应用层通过 `RouteCacheService` 按 `AMAP_TRANSIT_CACHE_TTL_SECONDS`（默认 6 小时）和
`AMAP_DRIVING_CACHE_TTL_SECONDS`（默认 30 分钟）写入 `route_cache`，缓存命中时不会重复请求高德。

AMap 每次 HTTP 调用使用 `AMAP_TIMEOUT_SECONDS` 作为单次 attempt timeout。BL-100 默认最多
执行 2 次 attempt（初始调用加 1 次 retry），仅对连接/读超时和选定的 HTTP 5xx 重试；
认证、普通 4xx、429/quota、无路线、坐标和响应格式错误不会自动重试。`AMAP_RETRY_BASE_DELAY_MS`
和 `AMAP_RETRY_MAX_DELAY_MS` 控制有限退避，`AMAP_MAX_RETRIES_PER_REQUEST` 限制同一 HTTP
请求上下文的总 retry slots（默认 2）。缓存命中不会消耗 provider attempt 或 retry slot；本地
GTFS 查询和数据库错误不套用 AMap 重试策略；Fixture mode 不增加人工重试延迟。

BL-104 为每个 HTTP 请求增加独立的 AMap logical operation budget：
`AMAP_MAX_OPERATIONS_PER_REQUEST` 默认 16，缓存命中不消耗 slot，重试也不重复消耗
logical operation。`AMAP_MAX_CONCURRENT_OPERATIONS` 默认 4，用于限制进程内同时进行的
实际 AMap 操作；它不会限制 Fixture、GTFS 或数据库。operation budget、BL-100 retry budget
和并发限制是三个独立概念。预算耗尽使用 `PROVIDER_CALL_BUDGET_EXHAUSTED`，不伪装成上游
HTTP 429；上游 429 仍然不重试。

## Safe Transfer Time（STT）

后端的 STT 是纯领域计算，不直接调用 AMap 或铁路 Provider。应用层先取得
normalized `RouteOption`，再交给 `SafeTransferCalculator`；因此 AMap、Fixture 或未来的
其他路线 Provider 都可以复用同一套规则。当前规则版本为 `stt-v1`，初始参数是产品规划参数，
不是机场、铁路或地图服务的官方保证：

```text
STT = arrival_release_buffer
    + baggage_buffer
    + origin_hub_internal_buffer
    + route_duration
    + destination_station_entry_buffer
    + risk_buffer
```

默认值为下机释放 15 分钟、机场内部移动 15 分钟、铁路进站 30 分钟；无托运行李缓冲为
0 分钟、有托运为 25 分钟、不确定为 20 分钟。风险缓冲按托运状态为 20/25/25 分钟。
`route_duration` 使用 `RouteOption.duration_seconds`，不会重复计算在其他 buffer 中。
行李状态使用 `BaggageStatus.NONE`、`CHECKED`、`UNKNOWN`，不使用布尔值代替“不确定”。
本轮规则保存在后端版本化配置中，不新增数据库表或 migration。

结果同时返回：

- `theoretical_earliest_station_ready_at`：不含风险缓冲，理论上完成进站准备的最早时间；
- `recommended_departure_after`：理论时间再加风险缓冲后的建议出发时间；
- 每一项 buffer、总时长、规则版本和 machine-readable reason codes。

具体车次按出发时间分类为 `INFEASIBLE`、`TIGHT`、`SAFE`、`SPACIOUS`。边界分别是：早于
理论时间不可行；理论时间（含）到建议时间之前偏紧；建议时间（含）为安全；建议时间再
宽裕 60 分钟（含）为宽裕。STT 只提供规划建议，不保证航班、道路、公共交通或铁路实际运行。

### Railway → railway transfer semantics（BL-042）

STT 会根据 canonical hub identity 明确区分三种到达关系：

- `AIRPORT_TO_RAILWAY` 沿用机场释放、行李、枢纽内部移动、路线、进站和风险规则；
- `RAILWAY_SAME_STATION` 要求到达站和候选站的 canonical hub ID 相同。它仍会查询计划车次，但不会调用自站到自站的 RoutingProvider 或 RouteCache，也不会把机场托运行李时间带入铁路到达；
- `RAILWAY_CROSS_STATION` 用铁路到达准备缓冲、现有 normalized `RouteOption.duration_seconds`、目的站进站缓冲和风险缓冲计算 STT，允许的公共交通/驾车模式仍走现有 RoutingProvider、RouteCache、重试和 AMap 限额链路。

`stt-v1` 的铁路起始参数是铁路到达准备 10 分钟、同站换乘准备 15 分钟；跨站路线使用相同的铁路到达准备、路线时长和现有铁路进站 30 分钟。铁路到达的 `baggage_seconds` 为 0，表示产品没有把机场行李提取假设迁移到铁路到达。铁路风险余量沿用无托运行李的 20 分钟。以上都是产品规划参数，不是铁路运营或车站官方保证；车次分类仍使用同一套理论/建议/宽裕边界。

API 在现有 `safe_transfer` DTO 中附加 `transfer_kind`，同站的 `route` 为 `null`，跨站则保留每种已允许的路线、时长和 STT。前端使用该字段显示“同站换乘”或“需要跨站换乘”，不会自行计算 STT。路线失败、铁路无车和 Provider 部分失败继续沿用现有 partial/infeasible 语义；backup train 只从已经分类的车次派生，铁路搜索缓存也不把 transfer kind 放入查询 key。

## Candidate Recommendation Engine

候选推荐引擎（T-060～T-062）从 canonical hub registry 中选择转运城市内处于 active
状态、提供客运服务的铁路枢纽。它不会按距离提前删站，也不会在服务代码中硬编码成都东
等站名。每个候选站都会保留允许的 `TRANSIT` 和 `DRIVING` 路线结果，并依次经过
normalized `RouteOption`、STT、计划车次查询和 `TrainConnectionEvaluation`；路线 Provider
或 RailProvider 的部分失败会保留其余可用结果，并通过稳定的 reason code 表达。

当前 V1 排序是可重复的启发式规则，不是机器学习模型，也不使用 LLM、票价或余票。路线
和候选站先分别在 0～1 之间归一化，再按以下权重计算 weighted score：
当前参数配置版本为 `ranking-v1`；版本会随结果元数据返回，便于解释历史推荐。

```text
connection time       35%   30 分钟达到最高分，120 分钟降为 0
feasible train count  30%   5 趟达到饱和，不让车次数量线性无限放大
safe margin           25%   首趟 SAFE/SPACIOUS 车次相对建议时间，60 分钟达到饱和
complexity            10%   公交换乘次数和步行距离；驾车使用配置化 baseline
```

路线选择会优先保留能产生 `SAFE`/`SPACIOUS` 车次的路线，再比较上述得分；所有允许的
路线仍在结果中可供比较。候选站状态为 `GOOD`（有 SAFE/SPACIOUS）、`RISKY`（只有
TIGHT）或 `INFEASIBLE`（没有可行车次）；排序第一名的 `GOOD` 候选标记为
`RECOMMENDED`。排序 tie-breaker 使用推荐车次数、最早推荐车次、路线时长和 canonical
站名/UUID，保证相同输入得到相同顺序。推荐结果只描述计划车次，不表示有票或可购买。

候选站评估（BL-061）在 application service 的候选站边界使用有界并发。后端配置
`CANDIDATE_EVALUATION_MAX_CONCURRENCY`（默认 `3`，允许 `1..16`）控制同一请求中同时运行的候选
评估数量；设置为 `1` 即恢复串行行为。生产请求图为每个并发候选创建独立的 SQLAlchemy
`AsyncSession`，共享无状态 Provider、解析后的 GTFS feed 和现有铁路缓存，不会并发使用同一个
数据库 session。候选完成顺序不会进入排序：结果会按候选生成顺序恢复后再交给既有 ranking 和
recommendation。该设置是进程内执行策略，与 `AMAP_MAX_CONCURRENT_OPERATIONS`、请求级 AMap
operation budget 和 retry budget 分开；不会改变 STT、车次分类、排名权重或推荐语义。

候选任务属于当前 HTTP 请求生命周期。取消请求时，子任务会被取消、task-local session 会回滚并
关闭，AMap semaphore 与铁路 cache single-flight 仍由各自现有边界负责释放和清理。附近铁路、附近
机场和灵活日期功能不会在本轮增加外层并发，避免嵌套放大 Provider 压力。

### Backup-train robustness（BL-054）

每个候选站会从已经完成分类的车次集合中派生一组轻量的时刻表韧性信息：
`earliest_recommended_train` 仍是首选车次；在同一候选、同一既有铁路搜索窗口内，之后最早的
`SAFE`/`SPACIOUS` 车次是备选车次。结果通过新增的 `train_robustness` 字段返回，包含首选、备选、
`backup_available`、`backup_departure_gap_seconds` 和描述性状态
`BACKUP_AVAILABLE`、`NO_BACKUP`、`NO_PRIMARY_TRAIN`。没有备选车次是搜索窗口内的正常信息，
不是 Provider 错误；前端会提示“当前搜索时段内暂无后续推荐车次”，不会声称没有其他火车。

备选选择只消费现有 normalized `TrainConnectionEvaluation`，不发起第二次铁路或路线查询，
不使用票价/余票，也不增加 STT、候选状态、排序分数、排名或日期便利度。`backup_departure_gap_seconds`
只是两趟计划车次的时刻表间隔，不是延误容忍度或赶车概率。跨午夜时间继续使用现有
`Asia/Shanghai` 和服务日语义；附近目的站的 `destination_hub` 元数据也会随备选车次保留。

## Chengdu → Leshan Vertical Slice

T-070 将现有模块串成一条离线可重复的应用链路：

```text
TransferContext
  → canonical hub candidate generation
  → RoutingProvider / RouteCache
  → Safe Transfer Time
  → RailSearchService
  → TrainConnectionEvaluation
  → candidate aggregation
  → deterministic ranking
  → explainable TransferEvaluationResult
```

Fixture smoke 不需要 PostgreSQL、AMap Key、网络或 GTFS：

```bash
uv run --project apps/api python scripts/transfer_chengdu_leshan.py --mode fixture
```

Fixture 使用成都天府机场、canonical registry 中的成都铁路站和乐山目标站，故意让成都南
的接驳更短但只有偏紧车次，让成都东拥有安全车次；这用于证明推荐不能只按距离或路线时长。
输出会保留每个候选站的状态、最佳路线模式、路线时长、STT 时间、计划车次数量、最早安全
车次和 reason codes。

Live smoke 是开发者手工验证命令，使用真实 PostgreSQL canonical registry、已经导入的本地
GTFS timetable 和真实 AMap routing。这里的 live 只表示 routing 和本地数据源是真实配置，
不表示实时铁路状态、余票或官方库存；如果 feed 没有导入、日期超出范围、关键站未完成
reconciliation 或坐标不是 GCJ02，preflight 会停止并输出稳定错误码，不会静默回退到 fixture。

准备 live 数据：

```bash
cp .env.example .env
# 在根目录 .env 中填写后端专用 AMAP_API_KEY=...
docker compose up -d db
cd apps/api
uv run alembic upgrade head
uv run python -m app.db.seed
cd ../..
mkdir -p data/external
# 将 chinese-railway-gtfs 的解压目录或 zip 放到 data/external/（该目录已被 Git 忽略）
uv run --project apps/api python scripts/import_rail_gtfs.py \
  data/external/chinese-railway-gtfs
```

开发验证时建议限定日期，避免一次展开整个 feed 的多年服务日：

```bash
uv run --project apps/api python scripts/import_rail_gtfs.py \
  data/external/chinese-railway-gtfs-feed/output_gtfs.zip \
  --service-date 2026-09-18
```

Importer 输出 `available_from`、`available_to`、`matched`、`unmatched`、`ambiguous`，以及
逐站的 `matched_stations` / `unresolved_stations`；在 JSON 中搜索成都东、成都南、成都西、
乐山即可确认关键站的 reconciliation。日期必须
显式传给 live runner，不能假设 feed 覆盖某一天：

```bash
uv run --project apps/api python scripts/transfer_chengdu_leshan.py \
  --mode live \
  --gtfs data/external/chinese-railway-gtfs \
  --arrival-at "2026-09-18T14:20:00+08:00"
```

如果请求日期不在 feed 的 `available_from`～`available_to` 内，命令会报告
`RAIL_DATA_OUT_OF_RANGE` 及请求日期和 feed 边界，不会自动换日期。live 路线请求复用现有
`RouteCacheService`；第一次运行可能访问 AMap，之后相同路线和时间 bucket 可命中缓存。

### T-096 生产式 live verification

`scripts/live_smoke.py` 是只读的 live 验证编排器，不会 seed、导入 GTFS，也不会把
Fixture Provider 当作生产数据。它先检查后端配置、PostgreSQL、Alembic head、canonical
registry、GTFS 文件/日期范围/已导入日期服务、站点 reconciliation 和 GCJ02 坐标，再调用
现有 `POST /api/transfer/evaluate`。通过 FastAPI 后，它还会从 Next.js origin 发送同一个
请求，验证 `/api/transfer/evaluate` rewrite 确实到达真实后端。它只校验 API 响应结构和
provider metadata，不复制 STT、铁路搜索或 ranking 逻辑。预检还会输出铁路来源、版本、服务
日期范围和 `FRESH`/`STALE`/`UNKNOWN` 状态；`STALE` 或 `UNKNOWN` 是诊断提示，不会阻止已有
的日期覆盖、站点 reconciliation 或 transfer 评估。

先在两个终端启动服务：

```bash
cd apps/api
uv run uvicorn app.main:app --app-dir . --host 127.0.0.1 --port 8000
# 另一个终端（仓库根目录）
npm run web:dev
```

使用默认的成都→乐山验收输入运行完整检查：

```bash
uv run --project apps/api python scripts/live_smoke.py
```

验证 BL-042 铁路到站场景时，可以把到达枢纽改为已 reconciliation 的铁路站；预检和真实
API 会保留同站/跨站语义，不会把铁路到达当成机场到达：

```bash
uv run --project apps/api python scripts/live_smoke.py \
  --arrival-hub "成都东站" \
  --expected-arrival-hub "成都东站"
```

验证有界的日期比较时显式开启它；这会沿用同一主日期并检查 `flexible_date_comparison`
的日期顺序、`+08:00` 偏移和每项状态：

```bash
uv run --project apps/api python scripts/live_smoke.py \
  --flexible-dates --flexible-days-before 1 --flexible-days-after 1
```

默认输入为 `成都`、`成都天府机场`、`乐山`、
`2026-09-18T14:20:00+08:00`、`CHECKED`、`TRANSIT DRIVING` 和 12 小时铁路窗口。
可以通过 `--arrival-at`、`--gtfs-path`、`--api-url`、`--web-url`、
`--rail-horizon-hours` 和重复的 `--required-station` 覆盖；显式 GTFS 路径必须与后端
`RAIL_GTFS_PATH` 一致。只检查本地依赖而不访问 HTTP 时使用：

```bash
uv run --project apps/api python scripts/live_smoke.py --skip-http
```

常见失败码包括 `DATABASE_UNAVAILABLE`、`ALEMBIC_NOT_AT_HEAD`、`CANONICAL_DATA_NOT_LOADED`、
`AMAP_API_KEY_MISSING`、`LIVE_PROVIDER_MISMATCH`、`RAIL_DATA_NOT_LOADED`、
`RAIL_DATA_OUT_OF_RANGE`、`STATION_NOT_RECONCILED`、`COORDINATE_SYSTEM_UNSUPPORTED`、
`FASTAPI_UNAVAILABLE`、`NEXTJS_UNAVAILABLE` 和 `NEXTJS_REWRITE_ERROR`。输出只显示配置是否
存在、错误类型和非敏感统计，不打印 `AMAP_API_KEY`、`DATABASE_URL`、Authorization header
或 provider 原始响应。`--json` 可额外输出每项机器可读结果，但同样经过脱敏。

如果 feed 没有显式的来源更新时间，live smoke 会显示 `RAIL_SOURCE_TIMESTAMP_UNKNOWN`、
`source_updated_at: null` 和 `freshness_status: UNKNOWN`；这是可信的未知状态，不会用本地
文件时间或导入时间替代。导入器 JSON 输出也包含相同的来源名称/版本/覆盖范围字段，便于
开发者记录 provenance。

该命令使用真实 PostgreSQL/本地 GTFS/AMap 配置；它与默认的 Fixture Playwright E2E 完全
分开。`npm run web:e2e` 仍然离线、拦截 API 且不需要数据库、GTFS、AMap Key 或互联网。

铁路 feed 的来源、授权和非实时性质见下节；公共或商业发布前必须重新确认数据许可。

### T-097 生产观测与运维诊断

每个 FastAPI 请求都会通过 `X-Request-ID` 关联。客户端传入不超过 128 个字符且只含
`A-Z a-z 0-9 . _ : -` 的 ID 时会复用，否则后端生成 UUID4；ID 会安全地返回在响应头中。
请求上下文使用 async-safe 的 context variable，因此并发请求不会串 ID。默认不会记录完整
请求 body、query、headers、Cookie 或 Provider raw payload。

后端使用标准库 logging 输出 JSON line 事件，覆盖 HTTP 请求、Transfer evaluation、路由
Provider 和铁路搜索。事件包含稳定的 `event`、`request_id`、`duration_ms`、Provider、结果数量、
`data_completeness` 和 failure code；耗时使用 monotonic timer。`COMPLETE` 与 `PARTIAL` 是
业务结果状态，部分 Provider 失败仍可返回 HTTP 200，并不会被记录成服务器崩溃；没有铁路服务
或没有推荐站也是正常业务结果。

Provider retry 只在真实发生 retry 时输出 `provider.request.retry`，字段包含安全的 operation、
attempt、max_attempts、retryable、will_retry、delay 和 failure code；最终 provider event 会
记录实际 attempts、retry_count 和 final outcome。日志不会记录 AMap key、DATABASE_URL、
Authorization、Cookie 或 raw provider response。

安全事件示例：

```json
{"event":"transfer.evaluate.completed","request_id":"example-request","duration_ms":812,"candidate_count":4,"has_recommendation":true,"data_completeness":"COMPLETE"}
```

日志和 live smoke 复用同一套 T-096 脱敏规则。AMap Key、完整 `DATABASE_URL`/密码、Authorization、
Cookie、token 和原始 Provider 响应不会进入日志或公开健康响应。健康端点是：

```text
GET /api/health/live   # 只回答进程是否运行
GET /api/health/ready  # 快速检查数据库、Alembic、canonical data 和 Provider 配置
```

T-096 的 live smoke 会额外验证这两个端点、FastAPI 的 `X-Request-ID`，以及 Next.js 同源
`POST /api/transfer/evaluate` rewrite 是否保留 response header。它仍然是人工运行的真实
PostgreSQL/GTFS/AMap 检查；默认 pytest 和 Playwright 不访问外部网络。

### BL-024 公共交通不可用与夜间模式

路线 Provider 的结果会明确区分“当前查询时段没有该交通方式路线”和“Provider/基础设施暂时
失败”。AMap 返回空公交/驾车路线时，归一化为 `RouteAvailability.UNAVAILABLE` 与
`RouteFailureReason.NO_ROUTE`；超时、认证、配额、请求预算耗尽和其他 Provider 错误不会被
伪装成夜间无车。这个判断使用 Provider 返回的结果，不在业务层硬编码夜间小时，也不会把
失败写入成功路线缓存或改变既有 retry/quota 语义。

`allowed_modes` 是硬约束：只允许 `TRANSIT` 时不会隐式加入驾车，只允许 `DRIVING` 时不会请求
公交；两种方式都允许时，一种方式的确定性不可用会保留另一种可用路线，并在候选中返回增量的
`availability` / `reason` 字段。没有可用公交但驾车可用的候选仍可正常评价；所有允许方式都
没有路线时，候选保留为不可行的正常业务结果，而不是把确定性 no-route 升级为 HTTP 503。
Provider 故障仍按既有全局依赖失败语义处理。公共交通前端文案会分别显示“当前查询时段不可用”
和“路线暂时无法查询”，不会暗示其中一种是另一种。默认 Fixture Provider 支持显式配置模式为
`UNAVAILABLE` 或 Provider failure，离线测试可覆盖这两条路径；同站铁路到达仍然跳过路线调用，
跨站铁路到达继续按允许模式保留部分失败。

### T-100 路线分段详情

路线结果现在可以通过 `RouteOption.segments` 提供有序、Provider 无关的接驳分段。每段只
包含安全的标准字段：`segment_type`（`WALK`、`BUS`、`SUBWAY`、`RAILWAY`、`TAXI`、
`DRIVING` 或 `TRANSIT`）、标签/线路、可选的起终点、指引、时长、距离、车辆类型和站数。
AMap 原始 JSON、polyline 和第三方字段不会进入 Domain 或 API；未知的 Provider 分段会保留
为 `TRANSIT`/“其他交通”，避免把整段路线静默丢弃。旧的 `route_cache` 分段缺少这些新增
字段时按空值读取，无需 migration。

`POST /api/transfer/evaluate` 以可选字段向后兼容地返回这些分段。前端在每种可用接驳方式下
提供默认收起的“查看接驳详情”按钮；没有分段时不显示空面板。展开后按路线顺序展示步行、公交、
地铁、铁路、驾车等信息，同时保留后端选择的最佳路线和原有排名/部分失败语义。

### T-101 目的地附近替代铁路站

请求可以通过 `include_alternative_hubs: true` opt-in 展示目的城市附近的铁路替代站：

```json
{
  "transfer_city": "成都",
  "arrival_hub": "成都天府机场",
  "destination_city": "乐山",
  "arrival_at": "2026-09-18T14:20:00+08:00",
  "baggage": "CHECKED",
  "allowed_modes": ["TRANSIT", "DRIVING"],
  "rail_horizon_hours": 12,
  "include_alternative_hubs": true
}
```

替代站只从 canonical `hubs` registry 生成，并要求 active、`RAILWAY`、客运站、有效坐标和当前
RailProvider 的 reconciliation。系统以目的城市坐标为参考，用后端配置的 Haversine 直线距离
做发现筛选，按距离和稳定的 canonical tie-breaker 排序，再限制数量。这个距离只用于附近站发现，
不是道路距离、接驳时间或排名分数；请求中的 `destination_city` 始终保持用户输入的目标城市。

配置位于根目录 `.env`（示例见 `.env.example`）：

```text
NEARBY_RAILWAY_RADIUS_METERS=50000
NEARBY_RAILWAY_MAX_ALTERNATIVES=3
```

主站和附近站都会复用现有 `RailSearchService`、GTFS service date、跨午夜和 slash-form 车次语义。
每个车次可选返回 `destination_hub`，其中 `is_nearby_alternative` 和距离用于前端标识“附近替代站”。
前端会明确提示尚未计算替代站到最终目的地的接驳时间；T-101 不包含 BL-083 door-to-door 评分，
也不会改动 STT 或 V1 的四项 ranking 权重。未 reconciliation 或无车的可选附近站不会覆盖有效的主站
结果，铁路 Provider 全局故障仍沿用既有错误语义。

### T-102 到达侧附近机场参考

同一个 `include_alternative_hubs: true` 开关还可以 opt-in 展示到达侧的附近机场。机场候选只来自
当前中转城市 canonical `hubs` registry，并要求 active、`AIRPORT`、客运服务、有效坐标；请求中的
到达机场会被排除。发现使用后端配置的 Haversine 直线距离和数量上限，距离只用于发现与稳定排序，
不会进入接驳时长、STT 或铁路候选排名。

```text
NEARBY_AIRPORT_RADIUS_METERS=100000
NEARBY_AIRPORT_MAX_ALTERNATIVES=3
```

每个机场结果都有独立的候选铁路站、路线、STT、计划车次和推荐状态，主机场结果保持原样；前端把它们
标记为“附近到达机场参考”，并明确说明这是“相同到达时间抵达该机场”的 what-if 比较，不会把机场
加入铁路站全局排名，也不会计算航班可用性、票价、余票或到最终目的地的机场选择价值。没有附近机场
是正常结果，单个替代机场失败也不会覆盖主机场的有效结果。

### T-103 灵活日期比较

请求可以通过一个有界的 `flexible_dates` 对象比较同一到达时间在相邻日期的铁路衔接便利度：

```json
{
  "arrival_at": "2026-09-18T14:20:00+08:00",
  "flexible_dates": {
    "enabled": true,
    "days_before": 1,
    "days_after": 1
  }
}
```

前后偏移默认最多 3 天；当前主日期始终保留为主结果，日期比较只作为新增的
`flexible_date_comparison` 返回。每个日期沿用现有 CandidateGenerator、RoutingProvider、STT、
RailSearchService 和 CandidateRankingService，不会重新实现候选站排名，也不会重复评估主日期。

日期便利度是 `date-convenience-v1` 的确定性指标，使用：

- 可行车次数：`min(count / 5, 1)`；
- 最佳推荐阈值后衔接余量：`min(margin_minutes / 60, 1)`；
- 服务分布：每个日期实际铁路搜索窗口（默认 12 小时）划分为 4 个等长时段，统计含可行车次的时段数。

最终分数为 0–100 的四舍五入值：
`50% × 可行车次 + 30% × 衔接余量 + 20% × 服务分布`。这些指标只比较日期便利度，
不代表票价、余票、客流、延误概率或航班情况。铁路数据超出 GTFS 范围的可选日期会单独标记为不可用，
不会隐藏其他日期；主日期的失败语义保持原有 API 行为。

日期比较复用现有 RoutingProvider 的路线语义，不把 AMap 路线结果解释为按日期预测的交通流量或航班延误。

前后日期使用 `Asia/Shanghai` 的墙上时间生成，例如 `14:20 +08:00` 会在每个日期保持不变。
如果同时开启附近铁路站/机场替代，主日期仍返回完整的 T-101/T-102 结果；日期比较只评估请求的主到达枢纽，
不会展开日期 × 机场的组合。

后端配置中的 `FLEXIBLE_DATE_MAX_OFFSET_DAYS=3` 是执行上限，不是铁路运营规则。默认关闭时，现有单日查询
流程和响应字段含义不变。

### T-104 可分享查询链接

完成一次查询后，页面会把**本次已提交的查询条件**写入当前浏览器 URL，供复制和分享。页面打开带有这些
参数的链接时只恢复表单，不会自动发起评估请求；用户确认后再点击“开始评估”。未提交过查询时，复制
按钮保持禁用。用户修改表单但没有重新提交时，复制的仍是当前结果对应的最后一次已提交查询。

URL 只使用 allowlist 中的非敏感查询状态，参数顺序固定为：

```text
transfer_city
arrival_hub
destination_city
arrival_date
arrival_time
baggage
modes
rail_horizon_hours
include_alternative_hubs
flexible_dates
days_before
days_after
```

`modes` 始终按 `TRANSIT,DRIVING` 的固定顺序编码；`include_alternative_hubs=1` 表示开启附近替代枢纽；
启用日期比较时使用 `flexible_dates=1` 以及 `days_before`、`days_after`。铁路搜索时长接受 1–48 小时，
日期前后偏移接受 0–3 天。铁路搜索时长（默认 12 小时）始终编码；未启用的可选项会省略，解析时回到当前
表单默认值。

日期和时间分别保存为中国本地墙上时间（例如 `arrival_date=2026-09-18`、`arrival_time=14:20`），
不会转换成浏览器时区或 UTC 字符串；提交时仍由现有表单序列化为 `2026-09-18T14:20:00+08:00`。
未知或非法参数会被忽略并使用安全默认值，页面不会自动提交或白屏。URL 不包含 API 响应、车次、评分、
provider 状态、请求 ID、数据库/GTFS 路径、AMap key 或其他 Secret。复制失败时页面会提示从地址栏复制当前链接。

## Transfer Evaluation API（T-080/T-081）

当前后端提供 `POST /api/transfer/evaluate`。HTTP 层只负责请求校验、canonical city/hub
解析和 DTO 序列化，随后调用已经验证的 `TransferEvaluationService`；不会在路由处理器中直接
调用 AMap、GTFS 或排名公式。

请求示例（`arrival_at` 必须包含时区偏移，业务会按 `Asia/Shanghai` 计算）：

```json
{
  "transfer_city": "成都",
  "arrival_hub": "成都天府机场",
  "destination_city": "乐山",
  "arrival_at": "2026-09-18T14:20:00+08:00",
  "baggage": "CHECKED",
  "allowed_modes": ["TRANSIT", "DRIVING"],
  "rail_horizon_hours": 12
}
```

`baggage` 支持 `NONE`、`CHECKED`、`UNKNOWN`；路线模式只支持 `TRANSIT` 和 `DRIVING`。候选
返回完整的 rank、status、score、每种路线、STT 时间、计划车次分类、最早可行/推荐车次、
reason codes 和 route-level partial failures。车次是 timetable 中的计划车次，不代表余票、
价格或可购买性。

路线级失败保留兼容的 `ROUTE_MODE_UNAVAILABLE`，并可附带 `availability` 与 `reason`：
确定性的无路线响应是 `UNAVAILABLE` / `NO_ROUTE`，Provider、超时、认证、配额和请求预算
错误分别保持失败类别。确定性 no-route 是正常的候选不可行结果，即使所有允许模式都没有
路线也不会伪装成 Provider 宕机；只有所有候选的允许模式都属于 Provider/配额等依赖失败时，
才返回 `503 ROUTING_PROVIDER_UNAVAILABLE`。

`meta.railway_source` 是可选的铁路来源元数据，例如：

```json
{
  "provider": "CHINA_RAILWAY_GTFS",
  "source_name": "GTFS:output_gtfs.zip",
  "source_version": null,
  "source_updated_at": null,
  "service_date_start": "2026-07-21",
  "service_date_end": "2050-07-21",
  "freshness_status": "UNKNOWN",
  "age_seconds": null
}
```

该对象是增量字段；旧 Provider 没有来源元数据时仍可返回原有业务结果，前端不会自行推断
新鲜度。

`recommendation` 可以是 `null`：没有 `GOOD` 候选时这是成功的业务结果，不会伪造推荐。单个
路线模式不可用、单个候选铁路 Provider 失败或没有铁路服务时，仍尽量返回 HTTP 200 并在候选
数据中保留稳定代码；确定性的 no-route 即使覆盖全部允许模式也保持为 HTTP 200 业务结果，
只有 Provider/配额等依赖失败覆盖全部候选时才返回 `503 ROUTING_PROVIDER_UNAVAILABLE`。
规范化错误统一使用 `{ "error": { "code", "message", "details" } }`，不会返回堆栈、原始
Provider payload、API Key 或数据库凭据。常见代码包括 `TRANSFER_CITY_NOT_FOUND`、
`DESTINATION_CITY_NOT_FOUND`、`ARRIVAL_HUB_NOT_FOUND`、`ARRIVAL_HUB_AMBIGUOUS`、
`NO_CANDIDATE_STATIONS`、`RAIL_DATA_OUT_OF_RANGE`、`DATABASE_UNAVAILABLE`、
`ROUTING_PROVIDER_UNAVAILABLE`、`TRANSFER_DEPENDENCY_UNAVAILABLE` 和
`AMAP_API_KEY_MISSING`。

当 `RAIL_PROVIDER=gtfs` 时，API 请求会复用现有 live preflight 的关键数据门禁：日期范围、
已加载的有日期铁路服务、候选站 reconciliation 和 GCJ02 路由坐标；门禁失败会返回稳定错误，
不会把未 reconciliation 的站静默解释成“没有车”。Fixture 模式不执行这些外部数据门禁。

启动 API 并完成 seed 后，可用 Fixture Provider 做离线接口 smoke（不需要 AMap Key）：

```bash
curl -X POST http://127.0.0.1:8000/api/transfer/evaluate \
  -H 'content-type: application/json' \
  -d '{"transfer_city":"成都","arrival_hub":"成都天府机场","destination_city":"乐山","arrival_at":"2026-10-03T14:20:00+08:00","baggage":"CHECKED","allowed_modes":["TRANSIT","DRIVING"],"rail_horizon_hours":12}'
```

API 默认使用 `RAIL_PROVIDER=fixture`，不需要网络或 Key。真实本地 GTFS API 评估需设置：

```bash
RAIL_PROVIDER=gtfs
RAIL_GTFS_PATH=data/external/chinese-railway-gtfs-feed/output_gtfs.zip
AMAP_API_KEY=...
```

GTFS 仍是开发/POC 数据源，不是官方实时铁路库存；公开或商业发布前必须重新审核来源授权。

## Railway 数据 POC

本轮 Railway Provider 使用 [wensimehrp/chinese-railway-gtfs](https://github.com/wensimehrp/chinese-railway-gtfs) 作为开发和 POC 数据源。该仓库的 GTFS feed 每周更新，覆盖铁路站、线路和计划时刻；它不是铁路官方 API，时刻表 provenance 可能存在商业化风险，公共或商业发布前必须重新确认数据授权。数据是 timetable，不是实时运行状态，也不代表余票、票价或可购性。

### Railway 来源与新鲜度（BL-036 / BL-102）

铁路 Provider 会把来源信息规范化为一个 `railway_source` 元数据对象，并随 transfer
结果返回：Provider、来源名称/版本、服务日期覆盖范围以及 `source_updated_at`。这个时间戳
只在 feed 明确提供可信的来源更新时间时填充；当前 `chinese-railway-gtfs` feed 没有这样的
字段，因此会明确返回 `source_updated_at: null` 和 `freshness_status: UNKNOWN`。不会使用 ZIP
文件 mtime、数据库导入时间、进程启动时间或请求时间冒充来源更新时间。

后端配置 `RAIL_DATA_STALE_AFTER_DAYS`（默认 `7`，范围 1–3650）作为运营提示阈值：已知来源
时间的年龄不超过阈值为 `FRESH`，超过阈值为 `STALE`，未知来源时间为 `UNKNOWN`。这是产品
初始的信任提示参数，不是铁路或机场官方标准。服务日期覆盖与新鲜度独立：覆盖查询日期不等于
数据一定新，数据新也不保证覆盖所请求的服务日期；超出覆盖范围仍使用独立的
`RAIL_DATA_OUT_OF_RANGE` 语义。

页面会显示来源和服务日期范围；`STALE` 会提示出行前再次核对，`UNKNOWN` 会提示更新时间
未知。新鲜度只用于来源说明，不会改变 STT、车次可行性、候选状态、排名、推荐结果或日期比较
便利度。Fixture Provider 提供固定的测试来源元数据，便于离线测试；它不代表真实铁路数据。

### Railway provider cache（BL-035）

`RailSearchService` 在规范化铁路搜索边界使用进程内缓存。缓存值是 `RailTrip` 结果，不包含
GTFS raw row、Provider 原始响应、STT、候选评价或最终推荐。默认配置为：

```env
RAIL_SEARCH_CACHE_ENABLED=true
RAIL_SEARCH_CACHE_TTL_SECONDS=1800
RAIL_SEARCH_CACHE_MAX_ENTRIES=512
```

缓存 key 包含 Provider、来源 identity/version、来源显式更新时间、起点站集合、终点站集合、
`service_date`，以及窗口查询的绝对 `window_start` / `window_end`。因此不同服务日、不同窗口、
不同 feed/source 不会互相复用。缓存有界并按 LRU 淘汰；`max_entries=0` 或显式关闭会直接调用
Provider。成功的空车次结果也会按 TTL 缓存，Provider 异常和取消不会写入缓存。相同 key 的并发
miss 使用 single-flight 合并，不会把所有无关铁路查询串行化。

GTFS 本地 feed 还有独立的有界进程内解析缓存：未变化的 ZIP/目录会复用已解析 feed，文件身份变化
会触发重新解析。这里的本地文件 identity 仅用于缓存失效，绝不会成为
`railway_source.source_updated_at`；来源更新时间仍只接受 feed 明确提供的可信字段。feed reload
后，新 source identity 会自然隔离旧的铁路搜索缓存。缓存事件包括
`rail.cache.hit`、`rail.cache.miss`、`rail.cache.coalesced`、`rail.cache.write`、
`rail.cache.evicted`、`rail.feed.loaded`、`rail.feed.reused` 和 `rail.feed.reloaded`，只记录安全的
Provider、服务日期、source version 和结果数量等字段。

缓存是单进程内存优化，多 worker 或多实例不会共享条目；部署仍需把它视为可丢弃的加速层。
它不改变 GTFS service-date、跨午夜、slash-form train number、附近铁路/机场、灵活日期、STT、
候选排名或推荐语义，也不需要 Redis 或数据库 migration。

架构保持可替换：

```text
RailProvider
    ↓
GTFSRailProvider (POC)
    ↓
future legal timetable provider
```

GTFS 导入器读取 `stops.txt`、`routes.txt`、`trips.txt`、`stop_times.txt`，并按 `calendar.txt` 和可选的 `calendar_dates.txt` 计算 `service_date`。GTFS 站点先通过名称、别名和 Provider Reference reconciliation 到 canonical Hub；不能可靠匹配的站点只进入 diagnostics，不会默认写入主数据。导入的站点坐标保持 WGS84，AMap 坐标仍保持 GCJ02。

全国 feed 的站点数量通常远大于产品维护的 canonical hub registry。导入器只把成功
reconciliation 的站点写入 `rail_service_stops`；未匹配的中间站不会触发自动建站，也不会
阻止一趟列车在两个已匹配的产品站之间导入。原始 GTFS stop 仍由本地 feed 保留，报告会
列出 unmatched/ambiguous diagnostics。这样可以查询成都东→乐山，同时保持 canonical
`hubs` 表为人工维护的产品主数据。导入器输出还包含 `skip_reason_counts` 和少量示例，区分
`full_line_trip`、`unsupported_train_identifier`、`insufficient_reconciled_stops`、
`invalid_stop_times` 与 `missing_stop_times`。

`services_created` / `stops_created` 是本次运行新增的行数，不是数据库总量；重复执行时出现
`0` 是幂等行为，应该结合 RailSearch smoke 或数据库查询确认已有数据仍可用。

车次标识按“每个组件都像公开车次号”的规则校验。普通数字、字母前缀数字，以及
`C5771/C5774`、`D972/D973B`、`4167/4170`、`09/S8512` 这类 slash-form 会原样保留，
不会猜测哪一侧是往返方向；`<线路名> (全线)` 仍被明确过滤。

使用本仓库的离线 fixture：

```bash
uv run --project apps/api python scripts/import_rail_gtfs.py data/fixtures/rail_gtfs
uv run --project apps/api python scripts/rail_gtfs_smoke.py data/fixtures/rail_gtfs --service-date 2026-10-03
```

默认查询成都东站→乐山站；检查成都南站时可复用同一脚本：

```bash
uv run --project apps/api python scripts/rail_gtfs_smoke.py \
  data/external/chinese-railway-gtfs-feed/output_gtfs.zip \
  --service-date 2026-09-18 \
  --origin-hub 成都南站 \
  --destination-hub 乐山站
```

真实 feed 不会进入 Git。下载到 `data/external/` 后，先执行 migration 和 canonical seed，再把目录或 zip 传给同一个 importer。导入可重复执行；同一 provider、service date、train number 不会重复创建。`24:xx:xx`、`25:xx:xx` 等 GTFS 时间会保留 day offset，因此支持跨午夜查询。

可选 smoke script 只读取本地 feed，不访问 12306、Ctrip 或其他订票服务。它输出计划车次、服务日期、实际本地出发/到达时间和数据范围，不对时刻做 snapshot 断言。

## 当前目录

```text
apps/
  api/       FastAPI modular monolith、SQLAlchemy、Alembic、Seed、AMap/Fixture Provider、pytest
  web/       Next.js App Router、TypeScript strict、Tailwind CSS、ESLint、Prettier
packages/
  shared/    前后端边界使用的 TypeScript API contract
data/seed/   成都、乐山、南京、苏州的开发 Seed
data/fixtures/rail_gtfs/  最小离线 Railway GTFS fixture
data/fixtures/rail_gtfs_real_shaped/  覆盖真实车次标识与未匹配中间站的离线回归 fixture
scripts/     跨应用开发脚本说明
tests/       预留跨应用测试；后端测试位于 apps/api/tests
```

## V1 private-trial deployment

V1 的产品功能已经冻结。Vercel/Render/managed PostgreSQL 的私有、非商业试用部署流程见
[`docs/V1_PRIVATE_DEPLOYMENT.md`](docs/V1_PRIVATE_DEPLOYMENT.md)。它使用根目录 monorepo
构建、服务端 `API_BASE_URL` 同源 rewrite、Render 的 `PORT` 和现有
`/api/health/live`、`/api/health/ready`，不会把 AMap Key 或数据库凭据带到浏览器。

Render 使用构建期注入的、由运维方提供并固定 SHA-256 的 GTFS zip；feed 不提交到 Git，也不
会在请求期间自动下载或回退到 Fixture Provider。迁移、canonical seed、GTFS 导入和应用启动
是四个独立步骤。部署成功只表示私有试用的连通性和配置正确，不代表公共发布授权。

城市/枢纽 Seed 标注为 `seed:test-fixture`，坐标为近似值。铁路官方站码未核实前保持为空；`FIXTURE_*` Provider ID 只用于本地契约验证。AMap POI 仅用于发现和校验候选枢纽，不是完整枢纽数据库。

## 文档

- [PRD.md](PRD.md)：产品范围
- [AGENTS.md](AGENTS.md)：代码与架构约束
- [DATABASE.md](DATABASE.md)：数据库结构
- [BACKLOG.md](BACKLOG.md)：产品/工程待办
- [TASKS.md](TASKS.md)：推荐开发顺序
- [docs/V1_PRIVATE_DEPLOYMENT.md](docs/V1_PRIVATE_DEPLOYMENT.md)：Vercel/Render 私有试用部署
- [docs/V1_CLOUDFLARE_DEPLOYMENT.md](docs/V1_CLOUDFLARE_DEPLOYMENT.md)：Cloudflare 前置代理与 Workers 兼容性说明
