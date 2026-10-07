# Current Classification + Read-only Egress Decision v1

这是 Identity & Authority / Classification & Egress 的窄 Foundation amendment。
本 amendment 不提供 remote reader、profile、selected release 或网络连接。

## Current 与 History

`AuthorityService.current_classification(object_id)` 只要求 `core_read`。
它在一个 read transaction 中读取 Object envelope 的初始 assertion ref，
验证同一 OBJECT subject 的 supersession 历史，并返回唯一的 current leaf：

```json
{
  "assertion_id": "fixture-current",
  "sensitivity_level": "PROJECT_PRIVATE",
  "handling_tags": [],
  "policy_version": "1"
}
```

初始 assertion 是 provenance；current leaf 是当前 classification governance truth。
不重写 envelope、旧 assertion、Object payload 或历史身份。

Resolver 要求整个 subject history 是一条合法、连通的链，且包含 envelope anchor。
不会采用另一个未连接的同 subject assertion，也不按时间挑选“最新”记录。
缺失 anchor、错误 subject、断链、policy mismatch、未排名 sensitivity、非法 tags，
均以 `AuthorizationDenied(EGRESS_CLASSIFICATION_UNAVAILABLE)` 拒绝。
缺失 Object 使用 `EGRESS_OBJECT_NOT_FOUND`；分叉、多 root、cycle 或不连通历史
使用 `EGRESS_CLASSIFICATION_AMBIGUOUS`。Resolver 的拒绝不产生 denial audit。

处理和返回均有上限：每个 Object 最多 256 条历史，最多 128 个唯一 handling tags；
assertion ID 最多 256 UTF-8 bytes、policy version 最多 128 bytes、每个 tag 最多
256 bytes。超过上限 fail closed，不截断后继续 ALLOW。

## Policy decision 与 authorization

| API | Capability | 语义 | Read-only store |
| --- | --- | --- | --- |
| `current_classification(object_id)` | `core_read` | 当前分类解析 | 支持 |
| `decide_egress(destination, object_ids)` | `core_read` | 纯 policy eligibility | 支持 |
| `evaluate_egress(destination, object_ids)` | `egress` | 既有 bool surface，复用 decision | 拒绝 |
| `authorize_egress(...)` | `egress` + Task/Grant | 既有执行授权及 denial audit | 拒绝 |

`decide_egress()` 不接收 Grant、Task、Command 或 Approval 参数。
ALLOW **只表示 canonical policy eligibility**；它不授予 caller read authority，
也不授权或执行任何外发。需要 caller authorization 的应用不得把此结果当 read credential。
旧 execution API 的签名、Grant scope 校验和 denial audit 保持不变。
read-only store 的 `egress` 禁令保持不变。

Decision 在同一个 read transaction 内解析全部 Object 的 current leaf，计算最高
sensitivity 和 handling-tag union，然后应用既有 destination rule、max sensitivity、
accepted tags 和 restrictive tags。它不采用 envelope 初始分类作为当前分类。
输入最多 256 个 Object refs，重复 refs 去重，`object_count` 是唯一 refs 的数量。
聚合 tags 超过上限也拒绝。相同输入和相同 canonical state 得到确定性结果。

返回字段：`decision`、`reason_code`、`destination`、`policy_version`、
`effective_sensitivity`、`effective_handling_tags`、`object_count`。
未能建立有效分类时 effective fields 为 `null`；不将未知 tags 表示为空列表。

稳定 reason codes：

- `EGRESS_ALLOWED`
- `EGRESS_INPUT_INVALID`
- `EGRESS_EMPTY`
- `EGRESS_OBJECT_NOT_FOUND`
- `EGRESS_CLASSIFICATION_UNAVAILABLE`
- `EGRESS_CLASSIFICATION_AMBIGUOUS`
- `EGRESS_DESTINATION_DENIED`
- `EGRESS_RESTRICTIVE_TAG`
- `EGRESS_SENSITIVITY_EXCEEDED`
- `EGRESS_TAG_NOT_ACCEPTED`

默认 policy 的 `LOCAL_ONLY` / `NO_EXTERNAL_EGRESS` 仍是 restrictive tags，destination
accepted tags 不能覆盖它们。既有 HUMAN Approval + `CLASSIFICATION_LOWER` 若合法地
产生移除 tags 的 superseding assertion，resolver 读取该 current leaf，再按 policy
做 decision。这只证明分类语义；没有实际发送数据，也未实现 selected-release workflow。

## Compatibility、验证与 rollback

`effective_classification(assertion_ids)` 仍聚合调用者明确指定的历史 assertions，
没有更改为 current resolver。Inspect、Experience、Panel、Read Plane 暂不迁移。

Persistence schema migration: **NOT REQUIRED**。没有 DB schema、persisted Object
format、envelope 或历史 assertion 的变更。只增加 read semantic，并让 egress 使用
既有 supersession truth。Rollback 为 code rollback；无需数据迁移。回滚后 egress
恢复此前使用 initial assertion 的行为，不删除已存在的 supersession 历史。

测试使用 TEMP fixtures。Happy path 通过真实 Principal、Trust Anchor、Grant、
ClassificationAssertion、Object、HUMAN Approval 创建 lowering 后关闭 writer，再用
`open_panel_application(..., read_only=True)` 验证。异常历史测试只在隔离 fixture
注入损坏记录。ALLOW、DENY、resolver 拒绝和 execution gate 拒绝均检查无新增 audit / ledger，
以及 DB/Object/Purge Journal 文件 bytes/hash、size、mtime 不变。

本 release 不配置 production destination、不访问 production、不 lowering production
classification、不创建 profile/remote MCP、不发送数据、不执行 Checkpoint。
