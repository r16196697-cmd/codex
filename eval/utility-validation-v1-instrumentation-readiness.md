# Nexus Utility Validation v1 — Instrumentation Readiness Design

**状态：** `PROPOSED / EXTERNAL REVIEW REQUIRED / NO IMPLEMENTATION AUTHORIZED`
**基线：** preregistration `CLOSED / ACCEPTED`，commit `9cc257e6a51234dbb521544b5a9bc8c75a07739d`。
**本设计不是 trial 授权。** 没有新增 task card；trial execution 仍未授权。

## 1. Current Observation Topology

下表区分“Core 已写入的 Nexus 事实”“Host 声明”“可在外层 CLI 观察的事件”和“当前没有可信来源的值”。字段存在或 service 能接收参数，不等于生产路径已经产生该 observation。

| 当前路径 | 能真实产生的 observation | 绑定范围与限制 |
|---|---|---|
| `ContextPackService.compile()` / `pack_status()` | Pack object/ref、Task/Run、选择 basis、入选 canonical refs/count、content/integrity hash、精确序列化 bytes、compiled time | 是 Nexus 编译事实，且 pack 与 Run 绑定；未证明 Host 收到或模型看见。 |
| Context Pack delivery 检查 | 对相同 Task/Run 的 Run Manifest 中，可能有 `HOST_DECLARED_DELIVERY` 或 manifest 声明 | 是声明/manifest evidence；`actual_host_delivery=UNKNOWN`、`delivery_observed=false`、`model_visible_exposure=UNKNOWN`。 |
| `MemoryService.search_admitted()` | 在指定 Run grant/boundary 下查找并返回 admitted Memory 及对象 refs/body | 该 search 本身没有通用、逐任务的历史搜索/重取计量；Context Pack 可记录 query hash 和入选 refs，不保存被避免的工作。 |
| `SkillApplicationService` / `SkillRegistryService` | Nexus Registry metadata、候选数/rank basis、resolution record、selected Skill identity、Nexus fallback instruction Artifact/ref/hash/bytes、selection latency | `CodexAgentSkillsInventoryAdapter` 是 partial positive-evidence inventory。Host package 被发现只说明 `AVAILABLE`；未发现为 `UNKNOWN`。Native selected/loaded/used 不可由 inventory 推出。 |
| `MeteringService` | 能保存已提供的 Host-declared 字段及来源；缺值为 `null + UNAVAILABLE`；Context Pack compile 可记录精确 bytes | `record_host_declared()` 要求真实 Task/Run、同一 Run grant 和 ACTIVE；新 OBSERVE telemetry 缺少 observation identity 时 fail closed。没有找到将当前 Codex turn 自动送入该 service 的 production emitter。 |
| `RunService` / `TraceRuntime` | Nexus Task/Run/Attempt 状态和 Core trace event 时间、refs、authority 等 | 这些是 Nexus execution/control-plane records。`CodexHostedBridge` 可记录 Host-declared manifests/children，但 bridge 本身不调用模型；manifest 不能证明实际 Host turn 或内容曝光。 |
| `VerificationService` | Nexus Run 下 object integrity / evidence refs 等既有 verification result | T1 integrity result 不等于任意任务的独立质量验收；Host-native A baseline 当前没有对等 Nexus Run/result。 |
| `adapters/client/__main__.py` 与 Panel | CLI 可组合现有 Core service；command-scoped Panel 展示 sanitized projections | 当前不是 Codex turn hook。Panel 不观测对话、model input 或 native Skill activation。 |
| Phase 5/6 外部 CLI harness 历史记录 | 过去真实 `codex exec --json` 执行记录中有 `thread.started` ID、`turn.completed`、Host `usage`、可解析工具事件、exit status、output 与 controller wall time | 是此前 Academy harness 的外部真实观测；不是当前 Slice 1/2/3 product telemetry，也不证明 JSON event schema 对所有 CLI/app 版本稳定。Phase 6 incident 不重开、不重跑。 |

### Data-flow diagram

```text
                         ┌──────────────────────────────┐
                         │ Utility Observation Ledger   │
                         │ external, versioned, neutral │
                         └──────────────┬───────────────┘
                                        │ trial/pair/condition IDs
                         ┌──────────────┴───────────────┐
                         │ frozen task card + start state│
                         └──────────────┬───────────────┘
                                        │
                ┌───────────────────────┴────────────────────────┐
                │                                                │
 A: HOST_NATIVE / BYPASS                              B: NEXUS_ACTIVE
 no Nexus Task/Run receipt                            real Nexus Task/Run (if used)
                │                                                │
 ordinary task input                                  ContextPackService /
                │                                      SkillApplicationService /
                └───────────────┐                      admitted Memory + refs
                                │                                  │
                                ▼                     existing pack/resolution IDs
                    External Codex CLI controller <────── proposed narrow adapter
                    codex exec --json                         (missing today)
                                │                                  │
                         JSONL / exit / wall time / sanitized evidence refs
                                │
                                └──────────────┬───────────────────┘
                                               ▼
                                   same independent acceptance
                                   result appended to ledger
```

A 和 B 共享 ledger 中的 study/pair/task identity，但 A 不建立虚假的 Nexus Task/Run。B 的现有 Core records 作为 optional references 附到同一 observation；它们仍不取代 Host turn identity。

## 2. Host Signals: Availability and Limits

分类针对当前仓库接入路径和可支持的 Codex CLI `--json` controller，不把其它 OpenAI API 产品的 observability 当成当前 Codex desktop/CLI 的接口。

本方案固定评估 surface 为 ordinary Windows PowerShell 启动的本机 Codex CLI `codex exec --json`；它不是把当前 Codex Desktop thread 接到 Nexus。两者不可混合成同一 Host surface。若研究要求必须测当前 Desktop 内正在运行的 turn，则当前没有 Nexus 可用的受支持 turn hook，本方案不能满足该要求。

| Signal | 分类 | 可声称的边界 |
|---|---|---|
| Controller invocation identity | `AVAILABLE_WITH_MINIMAL_ADAPTER` | 外层 controller 可为每次调用生成唯一 ID。 |
| Codex CLI thread/session ID | `AVAILABLE_WITH_MINIMAL_ADAPTER` | 既有 CLI harness 从 `thread.started.thread_id` 取得；缺事件/ID 时应为 UNKNOWN 并按规则判 invalid/inconclusive。不能据此声称有一个稳定独立 turn ID。 |
| Configured-root Skill package discovery | `AVAILABLE_NOW` | `CodexAgentSkillsInventoryAdapter` 只对配置 root 中发现的 exact package 提供 partial positive evidence；不代表完整 Codex inventory，也不代表 selected/loaded/used。 |
| Turn identity / start/end event | `AVAILABLE_WITH_MINIMAL_ADAPTER`（限已验证 CLI JSONL event shape） | 既有 runner能观察 `turn.completed`；逐版本 conformance 未验证前不能把任意字段/时间戳当稳定接口。 |
| Start/end timestamps | `AVAILABLE_WITH_MINIMAL_ADAPTER` | 外层 controller 可观察 invocation 起止 UTC 时间；它给出 end-to-end elapsed。Core Trace 时间是 Nexus event 时间。 |
| Host/model-only latency | `HOST_NOT_EXPOSED` | controller wall time包含进程启动、Host工作和结束开销，不能冒称模型 latency。 |
| Total input tokens | `AVAILABLE_WITH_MINIMAL_ADAPTER`，`HOST_DECLARED` | 既有真实 CLI result 有 `turn.completed.usage.input_tokens`；按 Host-reported total-turn usage 保存，best-effort、可能缺失。当前 product emitter 未连接。 |
| Cached input tokens | `AVAILABLE_WITH_MINIMAL_ADAPTER`，`HOST_DECLARED` | 只接受同一 Host usage object/basis 中实际报告值；prompt cache 不算 Nexus savings。 |
| Output / reasoning tokens | `AVAILABLE_WITH_MINIMAL_ADAPTER`，`HOST_DECLARED` | 只接受同一 completed-turn usage object 中报告值；不可从可见文本估算。 |
| Tool activity events | `AVAILABLE_WITH_MINIMAL_ADAPTER` | 官方 Codex eval 示例支持 `codex exec --json` JSONL 及解析公开的 command-execution events；旧 harness 也解析过 tool/MCP 类事件。只报告 adapter 实际收到的 event type/ID/count，不能声称隐藏内部活动一定完整暴露。 |
| Exact model-visible context | `HOST_NOT_EXPOSED` | CLI event/harness 不导出模型实际组装后的完整输入或每个来源组件。输入 hash 不是模型可见性证据。 |
| Host-native Skill selected / loaded / used | `HOST_NOT_EXPOSED` | 当前可取得的是有限 filesystem positive discovery；没有受支持的 turn-level activation/use signal。工具事件或输出行为不能唯一证明 Skill 使用。 |
| Host model identity | `HOST_NOT_EXPOSED`（当前接入） | 既有 Utility/Academy记录将 host model identity 保留为 `UNAVAILABLE`；不能由 CLI 安装版本或 UI 名称推断。 |
| Context submitted to CLI process | `AVAILABLE_WITH_MINIMAL_ADAPTER` | controller 可记录即将写入 stdin 的准确 payload digest/length、写入/进程完成关联和 CLI thread ID。这证明 controller 向该 CLI invocation 提交了哪些 bytes，不证明内部模型看见了它。 |
| Actual Host context delivery | 当前仅有 `HOST_DECLARED` 或 `UNAVAILABLE`；进程边界可由 adapter 观察 | Run Manifest 的 `CODEX_HOST_DECLARED` 仍是声明。process stdin submission 是更窄的 controller evidence，不应重命名为 model delivery。 |
| Cost / billable dollars | `UNSAFE_TO_INFER` | 缺少可归因到 task 的完整调用/模型/定价/缓存/重试/工具成本依据；不以 token telemetry 代替 bill。 |

OpenAI 的公开 Codex eval 说明文档展示了 `codex exec --json` 的 JSONL 与 command-execution event 检查；这支持外部 controller 读取已输出的 event。它没有为 Nexus 提供完整 model prompt、native Skill use 或 billable cost 接口。此前真实 CLI usage 字段是经验性 Host-declared 证据；正式使用前仍需固定 CLI 版本并做离线 parser conformance，不能假设所有版本都同形。[Codex evals and `codex exec --json`](https://developers.openai.com/blog/eval-skills)

## 3. Neutral Observation Ledger Design

建议独立于 Nexus canonical execution state 的 append-only `UtilityObservation` event ledger，schema ID/version 明确为 `nexus.utility_observation@1`。小样本用外部 JSONL 即可；一份 trial 可以由 `trial.opened`、`intervention.recorded`、`trial.ended` 或 `trial.invalidated` 事件逐步组成，保证进程中断时仍保留已发生事实。不要加 DB migration，也不要写入 Nexus ObjectStore、Memory、Evidence、Trace 或正常 Panel。

每个 ledger record/event 最少包含：

- `study_id`；`trial_id`；`pair_id`；预先分配的 `condition`；
- `task_card_hash`、task variant hash、`starting_commit`；
- 脱敏 `environment_config_snapshot_ref`（仅批准字段的摘要/版本；不保存配置正文、全局指令或绝对路径）；
- `controller_invocation_id`、Host surface/CLI version、`host_session_id`、可得的 `turn_id`；未知字段明确 `null + UNAVAILABLE`；
- `started_at` / `ended_at` UTC；controller elapsed 与来源；
- 可空的真实 `nexus_task_id`、`nexus_run_id`、Context Pack ref/hash、Skill selection/resolution ref。A 必须为 null，不造 Nexus receipt；
- outcome：冻结 rubric hash、verdict、独立 evaluator reference、first-pass/attempt/retry 信息；
- contamination/exclusion/incident 状态及固定 reason code；
- telemetry references 与逐字段 `{value, unit, provenance, basis, source_ref}`；
- `unknowns` 与受限枚举 `notes`，不得放自由文本对话或 private data。

ledger 只记录身份、摘要、refs 和 provenance；不得保存 prompt、response/conversation、完整 tool arguments、Skill instruction body、credentials、Host state 或 local absolute path。原始 CLI captures 如确需保留，放 repo 外的独立 private capture location，ledger 只放 sanitized event refs/hash。ledger 不可被 Memory search、Context Pack source selection 或 Nexus run recovery 当作事实源；研究结束后可独立归档/删除，不影响 canonical state。

### A/B identity model

- 同一 study 内每一对共享 `pair_id`；两个条件有不同 `trial_id`、task variant hash 和 invocation ID。条件与顺序在执行前冻结。
- 保留 `assigned_condition` 和 `received_intervention` 两个字段。B 即使没有 eligible source、compile 失败或 inventory UNKNOWN，也仍然是分配到 B 的试验；不得因未实际介入而事后改组或删除。
- A 不分配 `nexus_task_id` / `nexus_run_id`，其结果、验证、时间和 controller events 都在独立 ledger 中。
- B 只有真正由 Core 创建时才引用 Nexus Task/Run。Run、Pack、ResolutionRecord 等内部 refs 附在共同 observation 下，不让 A 伪装拥有它们。
- ledger 读写在 controller/evaluator side-channel 完成，不进入 Host prompt，不参加 Skill/Context selection，不改变 condition behavior。

## 4. B Intervention Identity and Evidence Levels

`intervention_id` 应在调用 Host 前生成，并对 immutable request 建 hash commitment：study/trial/task/pair、assigned condition、真实 Task/Run refs（如果有）、Participation mode、Pack identity/content/integrity hash/source refs/selection basis、Skill selection/resolution identity/package revision/instruction hash/byte size/load status、controller version、输入 payload digest/length、argv digest。只存摘要和 Core refs，不存内容正文。先持久化/append intervention intent，之后追加 controller submission/result，不以成功结果倒造 intervention。

### Context state split

| State | 当前能证明什么 | Utility adapter 可增补什么 | 仍不能推断什么 |
|---|---|---|---|
| `COMPILED` | Pack artifact 和 record 已提交；bytes/hash/refs/basis 可检查 | 将 Core record/ref 固定到 trial identity | Host 收到 |
| `BOUND_TO_RUN` | Context Pack 的 Task/Run association 与 Run-governed sources | 将相同 Run ID 与 controller invocation 绑定 | 该 Run 就是某个 Host turn |
| `DELIVERY_DECLARED` | Run Manifest 声明 context ref；Codex Hosted Bridge 标记 `CODEX_HOST_DECLARED` | 保存该 manifest ref 作为 declaration evidence | 独立 delivery observation |
| `DELIVERY_OBSERVED` | 当前 production path 没有真实 Host delivery observer | adapter 可观测精确 Pack/task payload bytes 已提交给一个 codex CLI subprocess 的 stdin，并关联它的 invocation/thread | Codex 内部消费了每个 ref 或模型的完整 assembled prompt |
| `MODEL_VISIBLE` | 当前无受支持 signal | 无本轮建议的最小 adapter 能证明 | 必须保持 `UNKNOWN / UNAVAILABLE` |
| `USED_IN_OUTCOME` | 只能观察 intervention 与之后 outcome 同属一 trial | 作为 assigned intervention 的 outcome association 进行分析 | 模型实际利用了某个 Pack entry；不能按成功反推使用 |

CLI stdin 的 observed boundary 只应命名为 `CONTROLLER_SUBMITTED_TO_CODEX_CLI`，不是 `MODEL_VISIBLE`，也不覆盖 Host-native ambient context。`DELIVERY_OBSERVED` 若用于表示该边界，记录中必须保存 `boundary=CODEX_CLI_STDIN`，避免扩大语义。

### Skill state split

| State | 当前能证明什么 | 最小 adapter 可增补什么 | 不能推断什么 |
|---|---|---|---|
| `REGISTERED` | Nexus Registry entry 与 package revision/hash | 固定 registry ref/revision 到 trial | Host 有该包 |
| `AVAILABLE` | Host inventory 对 exact package 的 `ADAPTER_DISCOVERY` positive evidence；或 Nexus source 当前可读 | 固定 inventory snapshot/hash 和 provenance | Host 会选择它；未发现也不是 unavailable |
| `SELECTED` | Nexus ResolutionRecord 选中的 Skill；HOST_NATIVE resolution 只委托 | 将 selection ref 与 trial/Run 绑定 | Host-native selector 选择了它 |
| `INSTRUCTION_LOADED` | Nexus fallback 可证明已把 integrity-bound instruction snapshot 加入治理 Artifact；Nexus load bytes/status | 将 exact instruction artifact/hash/bytes 与 B intervention 绑定 | Host-native loaded state 或模型已读取 |
| `DELIVERY_DECLARED` | Run Manifest/Context refs 的声明 | 保存声明 ref | 实际 delivery |
| `DELIVERY_OBSERVED` | 当前没有 Host delivery callback | controller 可记录该 instruction bytes 确实包含在提交至 CLI stdin 的 input digest 中 | Codex 内部把它加载为 Skill，或它未被截断/转换 |
| `MODEL_VISIBLE` | 当前 Host 不暴露完整 prompt/Skill activation | 无 | 必须为 UNKNOWN |
| `USED_IN_OUTCOME` | outcome 可与 assigned condition、selection 同 trial 关联 | 分析 treatment assignment 下的结果 | 输出相似、成功或 tool call 就证明 Skill 被使用 |

现有 Host-native `AVAILABLE` 不得生成 Nexus fallback；当前 partial inventory 的 absence 是 UNKNOWN。若 B 没有可合法提供的可观测 Nexus intervention，保留 `received_intervention=NONE/UNKNOWN` 并按 preregistered intention-to-treat 规则处理，不重写历史状态。

## 5. Metric Tiers

### Tier 1 — REQUIRED FOR PILOT VALIDITY

缺少任一项都无法形成有效 A/B observation；这些值不要求 token telemetry：

1. 共用 `study_id / pair_id / trial_id`、预先分配的 condition/order、冻结的 task card/variant hash；
2. 相同任务可访问事实的起始状态记录：starting commit、共享 source snapshot/ref 和批准的环境/config snapshot reference；
3. 每次 execution 独立 invocation/session correlation、fresh-session assertion、start/end UTC 与 controller wall elapsed；
4. 任务结果由同一冻结、可复核的 acceptance rubric 判定，保留 evaluator provenance、first-pass、retry/attempt 与 unresolved；
5. B 若被称为 context/skill intervention，记录实际 Nexus Pack/Resolution identity，并观察到相应精确 payload 被提交到 Host CLI input boundary。只有 compile/select，没有提交证据时，B intervention 不成立；
6. 不匹配、cross-condition contamination、状态变化、无效输入/缺事件和 exclusion reason 有逐 trial 记录；保留失败/partial facts，不补跑替代；
7. A 的 Nexus refs 保持 null；不为对称性创建 Task/Run、Approval、Metering 或 execution receipt。

当前最关键的 validity blocker 是 **第 5 项没有已接线的 Context/Skill-to-Codex-turn adapter**。现有 Manifest declaration 不能替代它。

### Tier 2 — HIGH VALUE; ABSENCE NARROWS CLAIMS

- CLI `thread.started` ID、`turn.completed`/exit/output 和 JSONL 可观察 tool activity；未知或丢失须标明。
- Host-reported total/cached/output/reasoning token fields，带 exact usage basis 与 per-field provenance；best-effort，missing 不阻塞主 outcome pilot，但 token/context claim 需缩小。
- Pack exact bytes/hash/selected refs；Skill candidate count、resolution、fallback instruction bytes/load status；这是处理组本身的记录，不代表模型已见。
- controller elapsed；Nexus Skill selection latency；若将 retrieval/compile overhead纳入讨论，分别计时，不以总时间差倒推各步骤耗时。
- 可审计的 tool event categories/count、人工 restatement/intervention count、Artifact/Evidence refs reuse。若无法完整采集，只报观察到的值及覆盖范围。

### Tier 3 — OPTIONAL / DEFERRED

- 完整模型组装后的 model-visible prompt/context、native Skill activation/usage；当前 Host 未暴露。
- 逐文件 read/search/reacquisition 和 duplicate investigation 的全量跨条件事件、准确人工分钟、存储设备/索引成本。
- 完整 provider bill、task-level token/cost attribution、token-savings、跨 model/provider 比较、全自动 Project Reconstruction telemetry。

## 6. Reduced v1 Claim Set and Cost Decision

选择 preregistration 第 6 节决策 **B**：v1 不计算完整 `cost per verified successful task`。真正的 cost 需要等价的模型/usage/cost attribution、重试、工具与人工成本；目前来源不充分。将该核心长期目标标 `DEFERRED / UNAVAILABLE`，不得用 pack bytes、缓存命中、缺失值或模型名猜测补齐。

在 Tier 1 满足后，v1 可报告：

- 每个 assigned condition 下的 verified task success、first-pass/retry、inconclusive 和 acceptance evidence；
- controller 观测到的 end-to-end elapsed（称为 controller wall time，不称模型 latency）；
- B 实际发生的 Nexus operation、Pack exact bytes/refs、Skill resolution/load bytes/selection latency，以及与准确 CLI stdin payload digest 的关联；
- available 的 Host-declared turn usage/tool events 作为次要、有覆盖限制的指标；
- 结果与 Nexus overhead 的逐项描述，只有可比较且有来源的成本项才列数值。

若可观察到 B input 被送到 CLI 而看不到内部模型 prompt，最多声称“分配到 ACTIVE 且 controller 向该 CLI invocation 提交了已记录的 Nexus-derived payload 时，结果如何”；不得称模型看见、使用或被某条 context/Skill 改变。若连 stdin submission 都没有，不能把该执行称为 Context/Skill treatment。Token 缺失不影响 verified outcome + controller-time 的 pilot，但删除 token savings/model-visible context claims。

## 7. Outcome, Contamination, and Attribution

- 在执行前由独立评审者冻结每个 task card 的 acceptance rubric、证据定位和 pass/inconclusive 条件；同一个 blinded evaluator 仅看任务输出和必要的公开/shared evidence，不看 condition、Nexus-only metadata 或分析假设。必要时用另一 reviewer 对分歧作预注册仲裁；不得用 LLM self-score。
- B 的 Core Verification 可作为其 evidence provenance，但不直接替代 A/B 共享的 rubric。相同 Git diff、固定 deterministic check、客观状态事实优先；若某类别不能定义共同 verifier，就不进入 confirmatory matched set。
- 外层 ledger 在 Host 调用前记录 assignment/open event；调用结束后追加 turn/event/acceptance facts。记录只读采集，不返回数据给模型，不改变 ranking、Context selection、工具或任务 prompt。
- 每一条件 fresh CLI invocation/thread；不同 pair 使用不同目标事实/文件，不向下一条件共享先前输出或新增 canonical state。比较期间冻结 Nexus canonical snapshot；如 B 合法创建了新 canonical state，不向后续 A 暴露它，且相反方向也一样。
- CLI JSONL 中可观察到的工具活动作为 utility outcome/overhead，不采用 Phase 6 的 tool-free eligibility rule。只有越权写入、未授权网络/工具、协议不允许的外部状态变化或跨条件信息泄漏才触发相应 stop/exclusion；任务正常需要的工具不能自动记成污染。
- Host Memory/cross-session effects仍 `UNCHARACTERIZED`。pair 顺序平衡和不同事实减少直接答案复用风险，不能证明其消失。任何无法归因到 trial 的 telemetry 都不分摊或估算。

## 8. Project Nexus Workload Implications

Nexus repository 是真实长期项目，但无需新建 Project schema。候选连续性任务可以问：新 session 能否正确恢复当前状态/目标/下一步、识别 superseded decision、避免已知失败路径、定位 supporting Evidence、报告需补充的 Manual Restatement。它评估真实“恢复工作”，不是宣称完整 Project Reconstruction 产品已存在。

### 信息公平性

1. 为每个 pair 冻结共享原始资料基线：同一 repository commit/Git history、公开 docs、对应原始 Evidence/Artifact 来源（若属于任务范围），以及同一用户任务输入。记录可见 ref/hash，不把全文复制进 ledger。
2. A 可用普通 Codex Host 搜索该 shared repo/docs/history；B 可用同一份 shared source 加当时已存在、符合治理的 Nexus Memory/Evidence/Artifact/Context。B 的 Memory/summary 必须能追溯到共同来源，不能只给 B 一个 A 完全无从访问的“答案”。
3. 若某历史事实只存在于 Nexus canonical object 而原始 source 无法在 A 的 baseline 访问，则从主要 matched set 排除；可作为单独、明确标为 `NEXUS_ONLY_DESCRIPTIVE` 的 exploratory observation，不能拿它证明 A/B 公平优势。
4. A 不用人为复述答案；给 A 的是 normal Host 可访问资料与冻结 task card。B 不预填正确答案，只按 ACTIVE 现有 selection/boundary 编译。task card hash、shared-source refs 和 evaluator evidence map 在运行前冻结。
5. Pair 内用不同但等价的 decisions/files/issue instances；一个变体先执行后，不将其 output、Memory、new Artifact 或 git diff 暴露给另一个变体。倒序/平衡分配并记 random seed。

## 9. Minimal Instrumentation Slice

目标是只补 Tier 1 缺口，加少量 Tier 2 采集；无 token 条件、无 daemon/IPC、无新 Core 或 Metering 框架。

### 必须新增

1. Versioned、repo-external、append-only neutral observation ledger 和 validator：study/trial/pair/condition、task/start-state hashes、状态事件、acceptance/retry/exclusion、provenance/unknowns。A/B 共用，不创建 Nexus execution receipt。
2. 一个小的 external Codex CLI controller/parser adapter，固定到审核过的 `codex exec --json` invocation contract：生成 invocation ID，记录准确 process-input digest/boundary、退出/完成、thread ID、wall timestamps；只抽取允许的 JSONL event summary，原始流放在独立 private location，不纳入 Git/ledger body。
3. B 侧薄 adapter/composition：仅调用现有 ContextPackService、SkillApplicationService、Run/Verification 和 Metering projection；在 Host 调用前固定真实 Core refs/revision；将实际提交到 CLI 的 Nexus-derived payload digest 与同 invocation 关联。若不能安全/正确 materialize Pack/Skill artifact 为提交 payload，则 fail closed、标 `NO_INTERVENTION`，不宣称 B 生效。
4. 同条件的独立 acceptance record 与最小 attribution：trial ID -> invocation/thread -> actual Nexus refs (B only) -> evaluator verdict。不要把它回写成 canonical Task/Run 的替代事实。

`MODEL_VISIBLE`、`USED_IN_OUTCOME`、精确 provider cost 不在此 slice 实现；Host 不提供受支持信号时保持 UNKNOWN/UNAVAILABLE。

### 可以复用

- Metering 已有的 `OBSERVED/HOST_DECLARED/DERIVED/UNAVAILABLE`、缺失为 null、缓存/uncached 分离；不改既有 source semantics。
- Context Pack records、Object/Artifact integrity、source refs、Run association、selection basis、bytes/hash 和 lineage。
- Skill ResolutionRecord、partial Host inventory provenance、instruction Artifact hash/bytes、selection latency。
- Task/Run/Trace/Verification API 仅表示确实发生的 Nexus 事实；B optional refs only。
- Phase 5 已验证的官方 Codex CLI JSONL parsing concepts作为 adapter input，但需要固定版本并单独离线测试；不得复用其 Academy packets/evaluator 当 Utility workload。

### 明确不改

Foundation Contracts、Effect semantics、Memory truth policy、Skill lifecycle/approval semantics、Participation semantics、Phase 6 artifacts、Codex inventory completeness semantics。也不加 Project subsystem、Nexus MODEL receipt 伪造、完整 Prompt logger、native Skill usage detector、全局 token allocator 或通用 telemetry framework。

## 10. Proposed Files and Tests

仅供后续实现 review，当前均不创建：

- `eval/utility-validation-v1-observation.schema.json` — event envelope 与允许字段/枚举的版本化 schema；不含 prompt/body/path。
- `scripts/eval/utility_validation_observation.py` — neutral ledger append/strict-read/validation 与 provenance helper；数据目录必须在 Nexus repo/data root 外。
- `scripts/eval/run_utility_validation_v1.py` — external controller、CLI JSONL parser、trial lifecycle 和 A/B invocation correlation；只接受已冻结 task card 引用，绝不自动重试。
- `adapters/client/utility_validation.py` — 如需，由它把现有 Service composition 暴露为 B 的窄 application boundary；不直接 SQL，不创建平行 Core。
- `tests/eval/test_utility_validation_observation.py` — schema/version、A/B shared identity、A Nexus refs null、partial/open/ended/excluded states、duplicate id/fail-closed、unknown provenance、privacy/no prompt/path、append-only integrity。
- `tests/eval/test_utility_validation_cli_adapter.py` — sanitized JSONL fixtures + mock subprocess，验证 thread/event/usage extraction、missing usage remains null、command activity categories、timeout/partial/duplicate session stop、argv/stdin hash、no automatic retry；测试不得启动真实 Codex。
- `tests/integration/test_utility_validation_binding.py` — temporary Nexus data root下创建真实 B Run，验证 Pack/Resolution refs 与 invocation identity 精确绑定；A ledger row不产生 Task/Run；模式/authority boundary仍由现有 Core检查。
- `tests/integration/test_utility_validation_acceptance.py` — A/B 共用 frozen rubric reference、blind verdict、first-pass/retry/exclusion 和 unknown 不作零。

不需要 DB migration，除非后续 review 发现 observation 身份确实必须进入 canonical state；当前建议不要这样做。

## 11. Risks / Failure Modes

- JSONL event/usage schema 随 CLI 版本变化：pin exact CLI version，sanitized fixture 做 parser conformance；不合格时只降级对应指标或停止，不从 stderr 猜数据。
- Host input 边界 hash只代表 controller 提交内容；Host 可能重组、补充或省略内容，model-visible 继续 UNKNOWN。
- Pack 内含私有内容：ledger只保留hash/ref/长度，raw prompt和fallback body不进入 ledger/Git；外部 capture 独立治理和保留。
- B 更容易形成完整 Nexus Run/Trace，而 A 不会：不得比较两组 Core Run count；只比较 neutral ledger task outcomes。
- Nexus state 缺失/不可访问会让 B 无 intervention：保留 assigned B 与 received intervention 的差异，不剔除困难结果来美化表现。
- 研究 instrumentation 自身增加启动/序列化时间：分别计 controller/instrumentation overhead；不得将其并入“模型 latency”。
- task source只存在 Nexus 会偏向 B；按第 8 节 shared source parity 规则排除或分层描述。
- 同一 Host 的跨 session Memory/Skill 状态无法完整观察：记录为 confound，限制解释，不关闭/修改 Host 配置。
- Host-reported usage可能延迟、best-effort、不可计费；缺失值保留，成本分析 deferred。

## 12. Explicit Non-goals

本设计不实现/运行 instrumentation、Host trial、8-pair/16-task pilot、task cards 或 scorecard；不改 Skills/Codex config，不做 Skill estate cleanup，不重开 Phase 6、Academy、Capability Certification、Shadow、DSH、Second Host 或 Host portability。无 daemon、HTTP/socket/IPC、通用 telemetry framework、新 Project schema、prompt pipeline 重写、模型路由、native Skill introspection、script runner 或 autonomous Knowledge feature。

## 13. Exact Next Step After Review

先由独立审查确认：Tier 1 最小集、外部 CLI stdin 作为 intervention boundary 的准确命名、A/B source parity、neutral ledger 的 privacy/retention，以及 CLI event 字段的版本支持范围。审查通过后，单独提出并批准 Tier 1 instrumentation patch；它只完成 ledger、controller correlation、B 的真实 Pack/Skill submission binding 和独立 acceptance linkage，不自动批准 trials。

patch 完成并离线验证后，正式 task-card freeze 仍需：

1. 选出 8 对不同但可匹配的具体 Nexus workload variants，确认共同 source snapshot、起始 commit、可访问性和 pair rationale；
2. 为每对冻结 task-card/rubric hashes、客观 pass/inconclusive 标准、独立 evaluator 和 stale/superseded ground truth；
3. 生成并保存受审的随机顺序/seed，冻结 Host CLI version、sandbox/tool policy、Nexus state snapshot 和 environment refs；
4. 用 mock/offline conformance 验证 ledger 与 controller；确认 B 每次只有真实 payload submission 才算 received intervention；
5. 汇总剩余 UNKNOWN 指标和 reduced claim set，经 external review 接受；之后再单独请求正式 pilot 执行授权。

在这些步骤完成前，`trial_execution = NOT AUTHORIZED`，formal Utility trial count 保持 0。

## 14. Evidence Reviewed

直接检查的 production implementation：

- `kernel/context/service.py`
- `kernel/metering/service.py`
- `kernel/memory/service.py`
- `kernel/skills/application.py`
- `kernel/skills/host.py`
- `kernel/skills/service.py`
- `kernel/run/service.py`
- `kernel/verification/service.py`
- `adapters/client/hosted.py`
- `adapters/client/__main__.py`
- `adapters/panel/viewmodel.py`
- 既有 `scripts/eval/run_behavioral_phase5.py` 和已提交 Phase 5 result，仅用于核对历史 CLI event/usage 解析证据；没有执行该 runner或 Host。

CLI JSONL 的公开说明参考 [OpenAI Codex eval guide](https://developers.openai.com/blog/eval-skills)。该文档示例支持结构化 JSONL/tool activity parsing；本设计没有从中推导完整 prompt visibility、Skill usage 或 provider cost。
