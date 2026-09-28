# Nexus Utility Validation v1 — Preregistration / Readiness

**状态：** `PROPOSED / EXTERNAL REVIEW REQUIRED / NOT READY FOR TRIAL EXECUTION`
**设计基线：** `nexus-academy-bootstrap` @ `387cf6dee90f752fe8b2c8dc4ac268b54cd7e783`
**本文件不是执行授权。** `formal_trial_count = 0`；本轮没有运行 Host、Skill 或 Utility trial。

## 1. Scope

本 preregistration 评估同一个 Codex Host 上 Nexus 已实现的 production path 是否能以可接受的 Nexus 自身开销，提升真实、可验证任务的完成价值。主要目标是 **cost per verified successful task**，而不是单独追求较低 token 数。

范围限于现有 Nexus Task/Run、Authority、Memory、Evidence、Artifact、Trace、Context Pack、Metering、Skill Registry/Resolution 及 Operator Panel 能实际提供的行为。Nexus 长期项目是 workload domain；本设计不增加 first-class Project schema。

Skill Estate Audit v2 已关闭；真实 Skill usage/utility evidence 将从 Utility Validation 开始积累，静态库存本身不作为删除依据。该审计不构成 Skill 使用、成功或低效的运行证据，也不在本轮重做。

Phase 6 保持 `CLOSED / INCONCLUSIVE — FORMAL MATRIX EXHAUSTED`。本设计不重开 Phase 6、Academy formal matrix、Capability Certification、Shadow 或 Host portability。

以下未来能力不属于 v1 scored capability：Knowledge Intake/Capture、Procedure/Playbook、automatic Skill synthesis、Knowledge Gap Analysis、Nexus Scout、Obsidian-like Knowledge Explorer、完整产品化 Project Reconstruction/Session Bootstrap、autonomous self-upgrade。它们只列在第 15 节。

## 2. Research Questions

1. 在任务类别和验证标准匹配时，Nexus ACTIVE 是否提高 verified task success、first-pass success 或长期任务恢复质量？
2. 现有 Context Pack、admitted Memory 与可复用 Evidence/Artifact 是否减少任务重新获取信息的工作，同时保持来源、授权和时效正确？
3. 现有 Skill Registry 的候选检索与 HOST_NATIVE/NEXUS_FALLBACK resolution 是否在相关任务上带来可验证效用，且避免无关 Skill 加载？
4. 收益扣除 Nexus retrieval、Context 编译、额外工具操作、verification、人为介入和运行维护开销后，cost per verified successful task 是否改善？
5. stale/superseded state 被选用、重复调查、重复读取、context miss 和恢复耗时在当前 production path 中能否被可靠观测？

研究结果只适用于测试的 Codex Host、Nexus 版本、配置和任务样本。此设计不预设 Nexus 有净收益，也不把任一指标的缺失解释为 0。

## 3. Current Implemented Capability Boundary

| 能力 | 当前 production 可观察事实 | 不能据此声称 |
|---|---|---|
| Task/Run/Attempt/Trace | Nexus Run、状态、Attempt/Subtask 和部分事件可从 Core projection 检查 | Host 原生任务都已被 Nexus 摄取；A/B 两组都有等价 Nexus Run |
| Context Pack | ACTIVE 下可按固定策略从合格 canonical refs 和 admitted Memory 编译确定性 Pack；保存关联、refs、lineage、精确 UTF-8 bytes/hash | Host 一定接收或模型一定看见 Pack；Pack bytes 是 tokens |
| Context delivery | 对相同 Task/Run 的 Run Manifest 可记录 Host 声明/Manifest 声明；独立实际 delivery 仍未知 | 声明等于实际 delivery 或 model-visible exposure |
| Memory | Run authority/boundary 下可搜索 admitted Memory；Context Pack 可记录纳入的引用 | 已测量重复搜索、避免读取或调查工作 |
| Evidence/Artifact | 可用现有对象、证据、Artifact、lineage 和 purge/redaction 状态 | A/B 之间已经有完整的复用收益计量 |
| Skill Registry | 可做 metadata-only candidate discovery/ranking、governed resolution；记录候选数、结果、selection latency；fallback 才保存受治理 instruction Artifact 和 exact bytes | Host-native discovered 即 selected/loaded/used；Codex partial inventory 的 absence 等于 unavailable |
| Metering | 能持久化 Host-declared/observed/derived/unavailable 字段；缺失为 null + UNAVAILABLE；Context Pack bytes 可记录 | 当前 Hosted/CLI product path 已给每个真实 turn 提供完整 Host token/latency 数据 |
| Verification | 对 Nexus Run 可保存既有 verification verdict/evidence | Host-native baseline 自动拥有等价、独立的验证记录 |
| Companion Panel | command-scoped Operator Panel 可读取 sanitized production projection | 它与另一 writer 并发实时运行，或它是 turn-level instrumentation |

没有项目级对象不妨碍按同一 repository/worktree、task card 和既有 Task/Run 范围组织研究；不可为方便分析伪造 Project 或 Nexus Run。

## 4. Conditions

采用最多两组的候选比较；不为对称性构造不支持的 OBSERVE 组。

### A — `HOST_NATIVE / BYPASS`（baseline candidate）

任务由同一 Codex Host 按日常 Host-native 路径完成。Nexus 不自动检索、注入或选择 Context、Memory、Skill，也不自动摄取 Host Task/result。Host 原生配置和能力照常存在，因此 A 不是“空上下文”或“无 Skills”基线。研究记录只能来自预先批准的外部/研究 ledger，不伪造 Nexus execution receipt。

### B — `NEXUS_ACTIVE`（Nexus participation candidate）

仅使用本基线已经存在、满足现有 governance 的 production Context Pack、admitted Memory、Evidence/Artifact refs 和 Skill Registry/resolution。Host-native 已明确可用时优先委托 Host；只有明确 unavailable、Skill 合格且 ACTIVE 时才能走 Nexus fallback。记录实际发生的 Nexus operations。

B 目前尚不能作为可计分的端到端 Host condition：需先证明 Pack/Skill resolution 与特定 Codex turn 的绑定、delivery 和独立任务 verification。编译/选择本身不证明模型暴露。第 11 节的 readiness gaps 关闭并经独立 review 前，不得开始计分执行。

### C — `OBSERVE`

`NOT_APPLICABLE / UNAVAILABLE`。当前没有能把纯 Host-native OBSERVE 工作与任一 Nexus Task/Run 正确关联的 observation identity；Metering 对此类新 telemetry ingestion 明确 fail closed。不得借用其它 mode 下创建的 Run，也不得改变 Host 行为以制造基线。

## 5. Workload / Task Families

候选 workload domain 为 Nexus repository 的真实长期维护任务。先建立 8 个匹配任务对，以下每类至少一对；若无法提出满足相同验证标准的两个不同任务，该 pair 在执行前排除并记录原因，不能用完全相同任务重放：

1. 恢复当前状态并回答一个可由冻结来源验证的项目状态问题；
2. 查找一项历史 Decision/Evidence，并判断其当前有效性；
3. 复用既有 Artifact/Evidence 完成一个有独立验收的后续任务；
4. 代码定位及限定范围的小修复；
5. 判断文档与实现的一致性；
6. 需要一个明确相关、已登记 Skill 的任务；
7. 明确不需要 Skill 的任务，用于发现无关 Skill 选择/加载；
8. 涉及 stale/superseded 信息风险的任务。

每个 pair 使用不同但难度、所需事实数量、代码/文档范围、验证步骤和允许时间相匹配的 task cards。任务文本、验收标准、起始 commit/worktree、相关 canonical refs、截止时间和 pair rationale 均须在看见条件结果前冻结。不能为方便 Nexus 而把需要的证据只放入 Nexus。

## 6. Matching / Contamination Controls

- 推荐每 pair 含 A、B 各一项，共 8 对 / 16 次任务执行；在两个条件中使用不同的目标事实/文件实例，避免直接答案复用。
- 每次执行使用新会话。代码变更任务使用隔离 worktree/等价隔离副本；两条件以相同 commit 和等价输入开始，结果不互相合并后再执行另一项。
- 在 pair 内随机决定 A/B 顺序，并跨 pair 平衡顺序。保存随机种子、分配表和 task card hash；执行人员不能依据已见结果换序。
- 冻结每项任务可访问的 Nexus canonical state snapshot 与 Host 配置观察。不得在先做的条件中写入可被后做条件读取的新答案；如实际发生，标记污染并依预先规则排除整对。
- Host cross-session memory/ambient effects 无法完全控制；记录为 `UNCHARACTERIZED`，不得将单次有序差异解释为因果。
- 不允许先后对同一个隐藏答案运行 A 和 B，不允许重试失败任务或只重跑某一条件。协议失败按 stop/exclusion rule 处理并保留首次记录。

## 7. Metrics

### Outcome

- `task_success`: 由每个 task card 预先冻结的可复核验收检查判定；
- `verification_verdict`: 保留 verdict、依据及 verifier 身份/独立性；A/B 使用同一验收规则。仅 Nexus Run 的既有 verification record 可直接提供 Nexus 侧记录；两组不等价时必须标为缺失；
- `first_pass_success`, retry/attempt count: 只有完整、连续的执行/attempt 记录时才计数；
- `unresolved/inconclusive`: 独立结果类别，不并为失败或成功；
- 主分析单位是 pair，核心目标为成本/每个 verified successful task。若未能可靠量化共同成本，只报告分项，不构造综合分。

### Context 与 Host usage

- Pack exact serialized byte size、hash、selected refs、编译策略：来自 Context Pack/Object/lineage，bytes 为 `DERIVED` 的确定序列化量；
- model-visible input、cached input、output、reasoning tokens：需要与该 task/turn 精确关联的真实 Host telemetry；当前通常 `null + UNAVAILABLE`；
- 若未来 Host 只提供同 basis 的 total input 和 cached input，uncached input 才可按差值标 `DERIVED`。缺任何一项都不推算；
- Host-declared total-turn usage 仍为 `HOST_DECLARED`，不能改称 model-visible token count。Caching 效益属于 Host 行为，不记为 Nexus savings；Pack bytes 不换算成 token。

### Reacquisition / continuity

- exact canonical ref 在 Pack/Manifest/Artifact lineage 中出现可描述性计数为 `DERIVED`；这只是已引用记录，不能证明避免了重读；
- repeated file reads、repeated searches、duplicate investigation、context miss、re-fetch/reacquisition、manual restatement、resume time、stale/superseded state 实际被使用：目前无跨 A/B 可靠、逐任务的 production event identity/telemetry，均为 `UNAVAILABLE`，不能记 0；
- stale state 只有与预先冻结的 source state 比对且有可审计选择/使用证据时才能计数。

### Skill utility

每项任务尽量记录 registry candidate count、候选 identity（安全摘要）、resolution state、selected identity、instruction load state/bytes、selection latency、是否出现可归因的 Host override/disagreement，以及最终任务结果。当前 Nexus ResolutionRecord 可提供部分 Nexus selection/fallback 数据；Codex inventory 是 partial positive-evidence adapter。

Host-native package `AVAILABLE` 只表示发现 exact package revision；不代表 selected、loaded、delivered、model-visible 或 used。Host-native 的真实 selection/load/use、irrelevant instruction exposure 和 override/disagreement 当前 `UNAVAILABLE`。无记录使用不得作为 Skill 删除证据。Instruction bytes 不等于 tokens。

## 8. Telemetry Provenance

每个字段保存值及来源标签：`OBSERVED`、`HOST_DECLARED`、`DERIVED`、`UNAVAILABLE`。估算若未来必要，必须独立标 `ESTIMATED` 并保存 pricing/version/formula/basis；v1 不需要估算成本，也不以估算补缺。

缺失值为 `null + UNAVAILABLE`。不允许将缺失填成 0、空字符串或默认成功；不允许把 Nexus Pack compile、Host declaration 或 Skill selection 推断成模型可见。

## 9. Skill Utility Observation

Skill Estate Audit v2 是 read-only 静态库存基线，不是使用频率或 utility 证据。运行期按任务保存可观察到的 registry candidate、resolution outcome、selection、fallback instruction load exact bytes/latency 和结果。分别表示 `REGISTERED`、`AVAILABLE`、`SELECTED`、`INSTRUCTION_LOADED`、`DELIVERED`、`MODEL_VISIBLE`、`USED`；任一前态不推断后态。

Codex inventory 未发现某包意味着 `UNKNOWN`，不是 `UNAVAILABLE`；未知 Host inventory 不触发 fallback。Host-native availability 也不能冒充 Nexus selection。报告中 `no observed use` 不等于 `DELETE_CANDIDATE`，不在实验期间变更 Skill、配置、review state 或 registry。

## 10. Nexus Overhead

应按 task/condition 记录可取得的：Context/Memory retrieval latency、Context compilation latency、Skill selection latency、额外 Host/tool calls、Context/Skill extra input tokens、存储/索引读写或交互、verification 时间/调用、人工介入与耗时、instrumentation 开销，以及失败/重试开销。

当前只具备部分真实基项：Skill resolution 保存 selection latency；Pack 有 exact bytes、对象和 lineage；其他 retrieval/compile/end-to-end wall time、A/B 可比的存储开销、extra tool calls、人工耗时和 instrumentation overhead 没有完整逐任务 ledger。不可把不存在的费用写 0。没有经过预先定义、同口径量化的 overhead 时，不发布 net value 或 cost-per-success 数值。

## 11. Instrumentation Readiness Matrix

| 指标 | 当前来源/production path | 当前来源标签 | v1 readiness |
|---|---|---|---|
| Task/Run/Attempt status | Core Run/Task/Trace projection | `OBSERVED`（Nexus Run 范围） | 部分；A baseline 无等价 Nexus receipt |
| task success / independent verification | task-card acceptance + 可审计 evidence；Core Verification 仅适用于有 Nexus Run 的路径 | 外部记录 `OBSERVED`，Core verdict 按实际来源 | 未就 A/B 统一验证闭环 |
| Context pack identity/refs/hash/bytes | ContextPackService、Object/Artifact/lineage、Panel safe projection | `DERIVED`/Core persisted fact | 对已编译 Pack 就绪；turn binding 未就绪 |
| actual Host delivery | 当前 Manifest/Host 声明路径 | `HOST_DECLARED` 或 `UNAVAILABLE` | 不等于实际 delivery |
| model-visible context / token exposure | 无可证明逐 turn 的 Codex exposure signal | `UNAVAILABLE` | 缺口 |
| Host total-turn token fields | Metering 可保存真实输入；当前没有确认的 production emitter/turn correlation | `HOST_DECLARED`（若确实提供），否则 `UNAVAILABLE` | 缺口；不估算 |
| cached/uncached/output/reasoning tokens | 同一 Host turn telemetry；缺项保持缺项 | 原始字段按来源，uncached 可条件 `DERIVED` | 缺口/仅部分 |
| Memory/Artifact reference reuse | Pack/Manifest/lineage 的已持久 refs | `DERIVED` | 可描述；不能证明避免工作 |
| repeated reads/searches/reacquisition | 无覆盖 A/B 的来源级事件计量 | `UNAVAILABLE` | 缺口 |
| Skill registry resolution/load bytes/selection latency | SkillApplicationService/ResolutionRecord | Core record / `DERIVED` | Nexus 侧部分就绪 |
| Host-native Skill selected/loaded/used | 无公开、可归因的 turn-level inventory/use signal | `UNAVAILABLE` | 缺口 |
| Skill instruction tokens | 无可靠 tokenizer 或 turn attribution | `UNAVAILABLE` | 缺口；不得按 bytes 估算 |
| retrieval/compile/verification latency | 目前只有部分 Skill latency；无覆盖全流程统一 timer | 已测字段才可 `OBSERVED`/`DERIVED` | 缺口 |
| extra tool calls / human intervention | 无统一可比较、归因到 task 的事件记录 | `UNAVAILABLE` | 缺口 |
| cost / token saved | 无可比较 baseline、完整 attribution/pricing | `UNAVAILABLE` | 不可声称 |

### 正式执行前最小 instrumentation gaps

1. 建立不伪造 Nexus Task/Run 的中立 trial/observation ledger，使 A、B 都有相同 task identity、条件、顺序、起止和验收引用；
2. 为 B 提供受支持且可复核的 exact Run/Task-to-Codex-turn Context/Skill delivery binding；保留 Host declaration 与真实可观察状态的差别；
3. 为 A/B 接入相同来源、可关联到每个 turn 的 Host usage/latency/tool-event observation；若 Host 不提供，则将对应假设从主分析中移除并由外部 review 同意，不补猜测值；
4. 为两组定义独立、相同的 task success verification 与 retry/first-pass 记录；
5. 对研究声称要比较的 reacquisition、人工时间和 Nexus retrieval/compile/verification overhead 提供最小 task-scoped measurements；否则这些结果保持 `UNAVAILABLE` 且不进入净收益判断。

这些是 readiness gap 清单，不是本轮实现授权。

## 12. Exclusion / Stop Rules

- 不满足冻结的 task card、输入 snapshot、pair matching 或隔离要求的任务，在揭盲/比较结果前按书面理由排除整对；保留排除记录，不补换到有利结果。
- Host/Nexus provenance 无法归属到唯一 task/turn、输出丢失、执行中断或 verification 不完整时，标 `INCONCLUSIVE`；不将其计成成功/失败，不重跑以替代。
- 发生跨条件答案/状态污染、未授权 source、错误 stale source、误加载无关 Skill、tool policy 不符合预注册，停止受影响 pair；若影响共享条件或安全边界，停止整个 pilot 并提交 review。
- BYPASS/OBSERVE 发生 Nexus 自动注入/摄取，ACTIVE 越过现有 Authority/Participation/Run boundary，或不合格/过期信息进入 Pack 时立即停止。
- 任何 Host/网络/工具/脚本执行超出预先批准范围、数据泄漏、私有状态入库或无法解释的 Metering provenance 均停止并保存安全、脱敏 incident record。
- 本文通过 review 本身不授权 Host execution；所有 readiness gaps 关闭、执行计划与 task set 冻结并获得单独明确授权前，保持 trial count = 0。

## 13. Analysis Plan

先发布 pair-level 数据完整性、排除/未知计数和 provenance 表；然后按预注册验收标准报告每组 verified success、first-pass/retry/inconclusive 及逐项 overhead。对 8 对仅做描述性 paired comparison 和原始分布/差值，不做统计显著性、普遍因果或跨 Host 推断。

分别报告 task success、Context/continuity、Skill utility、reacquisition、token/latency、verification、人力与 instrumentation overhead。cost per verified successful task 只有共同成本字段充分、计量口径一致且 attribution 成立时才计算，并同时报告分子/分母定义。否则明确为不可计算。不得用 LLM judge、事后阈值、新综合分、字符/token 比例或 Host cache 收益填补指标。

## 14. Interpretation Rules

- 结果描述为本 Codex Host、冻结版本和 pilot task set 中 `OBSERVED`；不宣称普遍提升或严格因果。
- Host memory/cross-session contamination 仍可能存在；随机 pair 顺序和不同任务事实只能降低明显污染，不能证明消除。
- Context Pack compiled/declared、Skill available/selected/loaded 均不表示 model-visible 或 used。
- 成功率变化不能抵消未经报告的 unrelated-task regression、stale-state misuse 或风险事件。
- 不显著/数据缺失不是无效应、零开销或失败证据；结果不足则为 inconclusive。
- v1 不是 Skill certification、Phase 6 重跑、Shadow/Production qualification 或 Academy-wide PASS。

## 15. Future Hypotheses Not Scored

Knowledge Intake/Capture、Procedure/Playbook、automatic Skill synthesis、Knowledge Gap Analysis、Nexus Scout、Obsidian-like Knowledge Explorer、完整 Project Reconstruction/Session Bootstrap、autonomous self-upgrade、第二 Host/DSH/Host portability 均为未来假设。本 preregistration 不假设这些功能已实现，也不将其输出用于 v1 scoring。

## 16. Exact Next Step After Independent Review

独立审查本 preregistration 的任务匹配、公平 baseline、验证方案、Host turn binding 和 instrumentation matrix。审查后先提出一个单独、最小、可审阅的 readiness/instrumentation 方案；本次文档不授权其实现。只有必要数据路径经过 review、A/B 的真实观察边界明确、任务卡和分析规则冻结并获得单独执行授权，才能启动一次 8-pair/16-task 的小型 pilot。若某个指标仍不可观测，则在执行前从 scored outcome 移除或保留为 `UNAVAILABLE`，不得现场补造。

## 17. Evidence Reviewed

设计依据当前仓库已有实现；相关 production surfaces 包括：

- `kernel/context/service.py` — Context Pack eligibility、选择、序列化、refs/lineage 和 delivery evidence；
- `kernel/metering/service.py` — Metering provenance、缺失值和 OBSERVE/BYPASS ingestion boundary；
- `kernel/memory/service.py` — admitted Memory、Run-governed search；
- `kernel/skills/application.py`、`kernel/skills/host.py`、`kernel/skills/service.py` — production Skill roots、partial inventory、resolution/load records；
- `kernel/run/service.py`、`kernel/verification/service.py`、`kernel/runtime/panel.py` — Run/verification/projection boundary；
- `adapters/client/hosted.py`、`adapters/panel/viewmodel.py` — Hosted declaration path 与 sanitized Operator Panel projection；
- `docs/user/nexus-utility-mvp-slice-1.md`、`docs/user/nexus-utility-mvp-slice-2.md`、`docs/user/nexus-utility-mvp-slice-3.md` — 当前产品与 participation 约束。

本文件只记录 readiness 设计，不修改这些 production 实现。
