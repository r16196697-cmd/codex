# Exact-Hash Object Classification Lowering v1

这是已有 Authority / Approval 语义的窄收紧，不是 remote reader 或 release protocol。

## Canonical hash binding

首次 `OBJECT + CLASSIFICATION_LOWER` 从 `ObjectStore.get_object_metadata()` 解析目标，
要求存在匹配的 Object、`payload_state=AVAILABLE`、有效的 canonical SHA-256
`integrity_hash`。该 hash 来自既有 immutable envelope，不接受 caller hash 参数。
Authority 将它传给既有 `evaluate_authorization()` / `_validate_approval()`。
Approval 的 target ref、target type、payload hash、scope、policy 和 authority chain
均按既有规则验证。无 hash、错误 hash或另一 Object 的 hash 不再授权新 Object lowering。

读取 metadata 沿用既有 Purge read barrier。提交事务内再次调用 `_assert_unbarred()`，
阻止在 approval 与写入之间发生的 purge/barrier 导致新 lowering 进入 unavailable target。
这不是新的 Object-state 模型。Canonical metadata 的 hash 不由 adapter、模型或 caller 决定。

`OBJECT + CLASSIFY` 不要求 envelope 已存在，保留 initial assertion → Object creation
顺序。RUN / TRACE_EVENT lowering 保持原有语义。本 amendment 不改变这些 subject 的 hash contract。

## Replay 与兼容性

CommandLedger 的历史 request shape 不变。已提交 assertion 的 exact replay 检查仍在
新的 hash/authorization 检查之前：返回历史已提交事实，不重新授权，不重复 assertion、
event 或 mutation。Grant 撤销、Approval 过期、Object 后续 Purge 不将 replay 变成新授权。
改变 assertion、Object ref 或 grant 的同 command request 仍按既有 ledger 冲突规则处理。

这是 **authority semantics tightening**：旧无 hash Object lowering Approval 无法授权新的
lowering。已有 canonical committed result 不迁移、不删改，历史 replay 保留。
源码审查未发现 adapters/application workflow 依赖无 hash OBJECT lowering；发现的依赖
为隔离 Authority / egress fixtures，现已补真实 Object 和 canonical hash。
没有访问 production，因此这不是 production 历史数据审计结论。

Persistence migration: **NOT REQUIRED**。DB、Approval、Object、ClassificationAssertion
schema 与 persisted format 均不变。Rollback 为 code rollback only；回滚也会恢复此前允许
无 hash Approval 授权新 Object lowering 的较弱语义，必须作为 Authority 行为回退审查。

## Selected Release sufficiency evaluation

隔离 fixture 复用 immutable `artifact`、canonical JSON、`derived_from`、`created_by_run`
和既有 Task/Run。测试中的 projection document 只是一份 evaluation fixture，不注册通用
release schema，不构成新增生产协议。

Source A / B 分别为 PROJECT_PRIVATE + LOCAL_ONLY / NO_EXTERNAL_EGRESS。
确定性选择固定的小字段集合，记录 projection_version、精确 sorted source refs、selected
fields 和 workspace；重排等价输入得到相同 bytes/hash。未选择的 source 内容不会输出。
`put_object()` 要求 projection sensitivity / tags 覆盖 sources，直接低分类创建仍触发
`DERIVED_CLASSIFICATION_DOWNGRADE`。

只有绑定 projection Object ID、CLASSIFICATION_LOWER 和其 exact immutable hash 的
ACTIVE HUMAN Approval，才能产生 projection current lowered leaf。Projection decision
从 RESTRICTIVE_TAG DENY 变为 ALLOW；sources 的 payload/hash/current classification
完全不变，source-only 和 source + released projection 混合输入仍 DENY。
ALLOW 只是 policy eligibility，不授予 caller read authority，不执行 egress。

Changed content 不能覆盖原 Object，也不能改变已有 derived source refs；新 bytes 需要
新的 Object identity/hash 和新的 HUMAN Approval，旧 Approval 不能跨 Object 使用。

Source purge 使用既有 PurgeService：explicit derived relation 将已 released projection
纳入 descendant closure，删除其 payload/envelope、redact classification/Approval refs、
删除 lineage projections。Projection 成为 PURGED tombstone，不能再读或 ALLOW，也不能新
lowering。未被 purge 的另一 source 保持受限。没有新增 retention/purge exception。

上述只评估 Nexus 内部受治理的 selected artifact。没有发送数据，也不声称可以撤回
已经交付到外部系统的副本；remote authentication、egress execution、delivery/retention
协议与 Remote MCP 均未实现。

结论范围：该链条在本 amendment 后可由现有 primitives 表达，**不需要 release table、
新的 persisted release schema 或第二套 Authority**。是否独立接受由外部审查决定。
