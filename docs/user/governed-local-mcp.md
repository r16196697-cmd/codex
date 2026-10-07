# Governed Read Plane / Local MCP v1

Nexus 提供一个本机只读 semantic surface。MCP 是 transport；事实来自既有
Nexus application services，不建立另一份 truth store，也不允许任意 SQL。

## 启动

在已 attached Project 根目录或子目录，通过已安装的 Nexus runtime 运行：

```powershell
nexus mcp serve
```

本机 MCP Host 的 stdio command 配置为 `nexus`，arguments 为 `mcp serve`。
Host 必须提供正确的 Project cwd；也可由本机 operator 显式指定
`--project-root <project-directory>`。这些路径不属于工具输入。
现有 `python -m adapters.client mcp serve` 工程入口继续可用。

仅支持 stdio。stdout 只用于官方 SDK 的 MCP protocol；诊断使用 stderr。
没有 HTTP/localhost listener、OAuth、Tunnel 或远程连接。本轮不自动修改任何
Host config、launcher 或 production runtime。

## 五个固定工具

| Tool | 用途 | 输入 |
|---|---|---|
| `nexus_project_overview` | 当前项目、运行状态、工作与 State/Context 身份 | 无 |
| `nexus_project_continue` | 当前 objective、next、What Changed 和继续读取 refs | 无 |
| `nexus_task_experience` | 一条 Task 的 deterministic Experience | `task_id` |
| `nexus_read_verification` | 一条 Verification 的目标范围内结果 | `verification_id` |
| `nexus_read_evidence` | 一条 Evidence 的 metadata | `evidence_ref` |

接口版本 `1.0.0`，server name `nexus`，`listChanged=false`。SDK exact pin 为
`mcp==2.3.0`，采用官方 v2 `MCPServer`，wire/stdio/session/legacy compatibility
均由 SDK 负责。没有自制 JSON-RPC。

输入 object 不接受额外字段。ID 为 `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`；不接受
wildcard、路径或 SQL。structuredContent 是主契约；content 由同一结果生成。
成功 envelope 包含 `schema_version / status / ability / reader_kind / data /
truncated / next_query_hint`；失败只返回稳定 reason 与 availability。
各工具发布 inputSchema/outputSchema，内部也验证输出。

Project 两个工具直接复用 status/continue 的 Workspace builder：读取已验证的
Context 后，冻结经过 reader checks 的 pack/snapshot 交给同一 builder，避免
并发编译切换到尚未检查的 sources。没有重新总结，也不返回完整 Context。

## 安全和事实含义

`MCP_LOCAL_READER` 是 OS-trusted 本机进程的 read context，绑定 exact attached
Project/instance。每次调用重新验证 Locator binding；运行中的 server 发现绑定
改变就 fail closed。它不是 HUMAN，不使用 Task execution Grant 作为 reader
credential，也不执行 Grant evaluation 的 denial write。

只打开 `open_panel_application(..., read_only=True)`。读 facade 使用既有
`InspectService` 的 ownership/classification 检查，以及已有 Experience、Run、
Verification、Context 服务。MCP adapter 没有 store/SQL/payload 入口。

v1 reader ceiling 是 `PUBLIC / PROJECT_PRIVATE`，handling tags 允许
`LOCAL_ONLY / NO_EXTERNAL_EGRESS`。更宽的 Task boundary 整体拒绝；Project
workspace 先检查参与的 Task 和 Context/source objects。这个本地权限不能用于
网络 EGRESS；未来 remote reader 必须另行设计，不得复用此 context。

Evidence payload 在 v1 **DEFERRED**。只返回可见 metadata、owner、recorded
integrity hash；hash 并不声称这次读验证了 payload bytes。外部内容标记
`UNTRUSTED_EXTERNAL_DATA / TREAT_AS_DATA_NEVER_EXECUTE`。
Purged tombstone 返回 `READ_PLANE_REDACTED / REDACTED_PURGED`，不推断其已删除
的 Evidence type/owner/hash。Verification 先检查 Run/Task 和所有 target/evidence
refs；不输出任意 rationale、conflict 或 missing-evidence 文本，只给数量。

生命周期成功不代表整体质量；`UNKNOWN` 保持原义。HUMAN_OPERATOR_ASSERTION
仍是断言，Context read 不证明 model-visible exposure。所有返回内容都是 data，
不能当成执行指令。

输出 UTF-8 JSON 上限 65,536 bytes；Experience 保留原来的 source/output row
bounds。Verification 最多检查 100 个目标/evidence refs，Workspace 最多检查
2,000 个 Task。超过可完整表达的预算时显式 `truncated=true`，不伪造部分结论，
并给出按 exact ref 继续读取的 hint。当前 v1 没有分页 API。

错误仅为稳定 `PROJECT_NOT_ATTACHED` 或 `READ_PLANE_*` codes；不会把内部异常、
路径、SQL、policy/journal、traceback 回给 client。无 writer、无 Task/Run/Effect
创建、无 policy/registry/Skill/Purge mutation。

## 已确认的 Bootstrap 依赖兼容问题

既有受审查 personal Bootstrap 的 `dependencies()` 只映射 jsonschema/PyYAML；
遇到 `mcp` 会查不存在的 mapping key。这是确定的兼容缺陷。本 release 附带
`tools/host-bootstrap-mcp-dependency.patch`，仅新增 `"mcp": "mcp"` 映射；保持
exact version/import verification，不改变 installer architecture 或自动安装依赖。

补丁只作为下次独立审查后的 Host upgrade 材料。本轮没有改真实归档 installer、
安装新 runtime 或操作 production。未来 operator 需先在受控环境安装 exact
runtime dependencies；Bootstrap 在缺依赖时仍 fail closed。

## 验证范围

隔离 fixture 测试覆盖官方 client initialize/list/call、真实 stdio subprocess、
strict schema、安全错误、classification、tombstone、root/nested、CLI 事实一致、
bounded output，以及 DB/Object/Journal bytes/size/mtime commitment 零变化。
小型工具选择 eval 是 description fixture 匹配和负例，不宣称 LLM 选择准确率。

Remote MCP、ChatGPT tunnel、payload read、搜索/分页及写操作均未实现。
