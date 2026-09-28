# Nexus Utility Validation v1 — Tier 1 Instrumentation Implementation

**状态：** `IMPLEMENTATION ONLY / FORMAL UTILITY TRIAL EXECUTION NOT AUTHORIZED`

本实现增加 evaluation-only observation 和 controller 边界，不创建 task cards，也没有运行 Codex Host、Utility trial 或 Academy。当前正式 Utility trial count 仍为 `0`。

## Observation ledger

`eval/utility-validation-v1-observation.schema.json` 定义 Utility Validation v1 使用的 observation event schema version 2；`scripts/eval/utility_validation_observation.py` 将事件写入 repo 外 append-only JSONL。事件使用全局递增 sequence、canonical JSON SHA-256 hash chain、严格重复 JSON key 检查和跨进程 append lock。重复 trial/pair-condition、非法状态转移、冲突 identity、损坏前缀及撕裂的末行均 fail closed。Ledger 是独立评估记录，不是 Nexus Object、Memory、Evidence、Trace、Task、Run 或 execution receipt。

A/B 共用 `study_id`、`pair_id`、frozen task/rubric/start-state identity。`TRIAL_OPENED` 绑定 `FROZEN_REPO_SNAPSHOT`、starting commit 和由 Git commit/tree content identity 确定性派生的 workspace SHA-256；pair identity validator 要求 A/B source identity、starting commit 与 rubric 一致。Host start 事件再次记录 controller 实际验证的 commit、identity 和 clean observation。`HOST_NATIVE_BYPASS` 必须将 `nexus_task_id` 和 `nexus_run_id` 留为 `null`；B 可引用真实 Nexus Task/Run。Ledger 不保存 prompt、response、transcript、instruction/context 正文、工具参数、绝对路径或私密配置，只保存 hash、byte length、受限状态、provenance 和必要的相关 ID。

## B intervention transport boundary

`adapters/client/utility_validation.py` 只组合真实 Context Pack、Skill resolution 与受治理 Artifact。它复用现有 Context Pack / Skill application 服务及 Object integrity 查询，不写 SQLite，也不执行 Skill scripts。包编译、Skill resolution 和 `INTERVENTION_PREPARED` 仅表示已准备；此时 `preparation_status=PREPARED_NOT_SUBMITTED`，不能称为已收到或已交付。

`scripts/eval/run_utility_validation_v1.py` 只接受由 task-freeze/controller caller 预先提供的 repo 外冻结 source snapshot，不负责 clone 或重建 baseline。Host invocation 前会重新验证 snapshot 的 Git 根目录、`HEAD == starting_commit`、严格 clean 状态（包含 untracked、ignored 和 submodule 变化），并核对由 commit/tree identity 派生的 `workspace_identity_sha256`。任何缺失、dirty、位于 source checkout 内、commit/hash 不符的 workspace 都以 `START_STATE_MISMATCH` 终止，且不会创建 Host invocation 或调用 CLI。Ledger 只保存 `FROZEN_REPO_SNAPSHOT`、commit、content identity hash 和 `workspace_clean_observed=true`，不保存本机绝对路径。A/B matched pair 必须共享 starting commit、workspace identity、task card、environment snapshot 和 acceptance rubric；可用独立 clone/snapshot 隔离每个 condition，但其内容身份必须相同。一次性 snapshot 构建发生在 trial open 前；trial open 后的 workspace validation 自然计入 trial wall time。

Host subprocess 的 `cwd` 是已验证的冻结 snapshot，而不是空目录或当前开发 worktree。因此 A 可从与 B 相同的冻结 repository/docs/history 自行读取，B 的 Nexus-derived context/fallback 才是额外 treatment。Workspace equality 只证明原始 repository baseline 一致；task-card freeze 仍须确认主分析中的关键事实可从该 shared source 获取，Nexus-only facts 不可进入 confirmatory matched set。Host 仍以 `--sandbox read-only` 运行；代码修复任务只能要求输出 patch/diff，再由独立 evaluator 应用到独立临时副本并执行冻结 checks。不得声称 Codex 在该 invocation 中直接修改 workspace。

Invocation start 事件先记录冻结 argv digest、精确 stdin digest/byte length、`shell=false`、workspace commit/hash/clean observation 和 cwd policy。只有 subprocess API 正常返回后，完成事件才记 `input_submission_status=CONTROLLER_SUBMITTED_TO_CODEX_CLI` 并重申相同输入 hash/长度；启动失败或 timeout 时为 `SUBMISSION_UNCONFIRMED`。这证明的是：

`Nexus current Core + EVAL_INTERVENTION_TRANSPORT + Codex CLI input boundary`

它不证明现有 Nexus production Host delivery、Codex 是否消费全部输入、`MODEL_VISIBLE` 或模型使用了内容。`MODEL_VISIBLE`、Skill `USED`、Host model identity、provider dollar cost 和 per-exposure token attribution 均保持 `UNKNOWN / UNAVAILABLE`。Host usage 若由 CLI `turn.completed` 明确报告，仅标为 `HOST_DECLARED`；缺失字段保留 `null + UNAVAILABLE`。不从 stderr、版本或输出推测 token/model 信息。

冻结 argv 为 `codex exec --ephemeral --json --color never --sandbox read-only --skip-git-repo-check -`，以 argv list、`shell=false`、exact stdin 和 validated repo-external frozen snapshot cwd 调用。当前实现没有运行真实 CLI；`-` stdin contract 尚未通过 live conformance 验证，只由静态代码和 mock subprocess tests 覆盖。Controller 不自动重试；任何显式后续 attempt 都需使用新 invocation identity 并留在同一 trial 时间线上。

## Timing and acceptance

`trial_wall_elapsed_ms` 从 trial open 时的单调时钟起算，到 acceptance record 后完成事件写入前测得，覆盖 condition-specific preparation、Memory/Context/Skill preparation、envelope、CLI invocation、output collection 和 acceptance processing。`host_process_elapsed_ms` 只围绕 CLI subprocess API 单独计时；多次显式 attempt 的值在 terminal summary 中求和。时长由单调时钟计算，不接受调用者传入时长。若 B preparation 失败，保留 opened/preparation/inconclusive 事实和 wall time，不创建替代 trial。

Acceptance 使用 A/B 共用的 rubric ref/hash。blinded evaluator callback 只收到 transient output bytes 与 rubric identity，不接收 condition 或 Nexus metadata；ledger 仅保存 verdict、output/evidence hashes、first-pass、retry count 和 inconclusive reason。Evaluator 不应从闭包或外部通道取得 condition；确认性 task 仍须在 freeze 前定义共同且独立的客观验收。

B 有 Skill query 时，intervention fact 还保留 Core resolution status/outcome、候选数、selected Skill ref、Host-native availability/provenance、Nexus instruction load status/exact byte size、resolution latency 和 resolution ref。`HOST_NATIVE` 只表示 Nexus 根据 Host inventory 选择委托；不表示 Host selected/loaded/used。Fallback 的 load status 只表示已载入 governed Artifact，之后 CLI input submission 也不代表模型看见或使用了 instruction。

## Current observation boundary

- **可从 Nexus Core 观察：** Task/Run refs、Context Pack compile/run binding/selected refs/hash/exact bytes、Skill Registry resolution/load records、governed Artifact integrity。
- **由 eval controller 观察：** exact CLI input bytes/hash、argv/hash、controller invocation ID、subprocess latency/exit/timeout、stderr byte count only、allow-listed JSONL event summaries 和 thread ID（如果事件存在）。
- **Host 声明而非独立观测：** turn usage values，仅当允许列表中的 `turn.completed.usage` 实际存在。
- **仍不可观测或不可安全推断：** 完整模型可见上下文、Host-native Skill 是否 selected/loaded/used、模型 identity、provider cost、per-exposure token attribution、ambient context decomposition。不存在 Host 字段时不得补零或推测。

## Tests and boundary

Offline tests 使用 sanitized JSONL fixtures 和 mock subprocess；集成测试用临时 Nexus 数据根真实调用 Context Pack 与 Skill application path，但 subprocess 始终 mock。测试覆盖 hash-chain/状态机、A/B identity、privacy、parser 的缺失/畸形/未知事件、argv/stdin provenance、tool summary、explicit retry linkage、B Context/Host-native/fallback binding、partial inventory UNKNOWN，以及 preparation 与 Host process 分开的时钟。没有测试启动 `codex exec`。

本实现不是正式试验授权，也没有验证实验结果。Utility Validation v1 的 task cards、trial assignment 和 execution authorization 仍须独立审查后另行处理。
