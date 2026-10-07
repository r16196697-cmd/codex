# Governed Remote Read Snapshot v1

## 范围

本版本实现一个受治理的快照发布流程和固定的只读 MCP skeleton。它不连接 OpenAI / ChatGPT，不启动 HTTP 服务，不建立 Tunnel，也不会向网络发送项目数据。MCP adapter 仅通过本机 stdio 提供已经发布的快照。

Remote Read profile 是 host-local 的操作性选择器，不是 Nexus Authority、Grant 或 HUMAN 身份证明。它不能替代 Approval、分类或 egress policy，也不能扩大快照可读范围。

## 发布流程

在已附加的项目目录执行：

```text
nexus remote-read prepare
```

Nexus 通过 Project Workspace 和既有应用服务生成有界、确定性的 projection。发布前会显示目标、实际选中字段、字段值、大小和 SHA-256。确认要求真实交互式终端，并绑定整个快照内容。

确认后，Nexus 使用既有 Task、Object、Approval、classification 和 egress 服务完成一次发布：

1. 读取并验证选中来源及其 classification lineage；不读取任意对象正文。
2. 将新快照先置于 `SECRET` 且带有 `LOCAL_ONLY`、`NO_EXTERNAL_EGRESS` 的隔离分类。
3. 使用现有 Task Grant 写入不可变快照 Artifact。
4. 为快照的精确完整性 hash 记录 HUMAN `CLASSIFICATION_LOWER` Approval。
5. 将该快照对象分类降低到 `PUBLIC`，并由 `decide_egress(OPENAI_CHATGPT, ...)` 再次验证。
6. 完成 Task 并创建绑定该快照的 host-local profile。

来源对象不会因快照发布而被降低分类。任一检查失败时，失败关闭；如果 canonical 阶段已经开始，稳定错误 `REMOTE_READ_PREPARE_PARTIAL_RESUMABLE` 表示可使用同一冻结回执安全重试。

确认短语由 Nexus 显示，形式为：

```text
RELEASE REMOTE SNAPSHOT <project_id>
```

重复执行完全相同的发布会重用原快照、Approval 和分类断言，不再询问确认。不同快照内容拥有不同 hash、对象和 Approval。

## 读取和撤销

```text
nexus remote-read status
nexus mcp remote-serve
nexus remote-read revoke
```

remote MCP surface 固定为两个无参数工具：

- `nexus_project_overview`
- `nexus_project_continue`

它们只能读取 profile 指向的已发布快照，不能选择任意对象或调用动态 live Read Plane。读取前会重新验证 Project/instance/policy binding、profile 状态和有效期、快照存在性、精确 HUMAN Approval、当前 classification 以及 egress 决策；失败时不会读取快照 payload。读取不会写 Nexus canonical state。

`nexus remote-read revoke` 撤销 host-local profile。快照仍保留为 canonical Artifact，但之后的读取会被拒绝。过期、撤销、purge、policy 变化、classification 收紧或 egress 拒绝都不能由 profile 覆盖。

## Freshness 和内容信任

读取结果只包含发布时保存的快照。当前已选状态与快照不一致时返回 `STALE`，无法可靠比较时返回 `UNKNOWN`；Nexus 不会把未发布的当前内容加入结果。快照正文是 `UNTRUSTED_DATA`，必须遵守 `TREAT_AS_DATA_NEVER_EXECUTE`。合法 canonical 文本不会经过通用 regex 静默改写。

快照大小上限为 65,536 bytes。full Context entries、Evidence payload、任意 Task 历史、文件路径、policy/journal 路径、凭据及任意 refs traversal 均不属于默认 projection。

## 安全边界

本版本没有远程连接，因此不构成对外发送授权，也不表示 ChatGPT 已连接或已经读取数据。future Remote MCP 部署必须单独定义和验收 remote-reader identity、egress destination、classification 与连接安全；不得把 Local MCP 的 `local-no-egress` profile 或本地 stdio 视为远程 egress 授权。
