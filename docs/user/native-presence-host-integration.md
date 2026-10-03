# Nexus Native Presence / Agent Host Integration

Nexus Native Presence 提供只读的项目状态、紧凑 Continuity Workspace，以及可选的 Agent Host 生命周期注入。Project ID 保存在可移植的 `.nexus/project.json`；实例位置保存在本机 Host Registry。不同 Host 共用 Nexus Host Integration 与 Project Workspace，不在项目仓库复制 Continuity 业务逻辑。此功能不需要 daemon。

## 常用命令

- `nexus status` / `nexus status --json`：检查当前项目连接并显示当前、最近完成、下一步和 Context 可用状态。
- `nexus continue` / `nexus continue --json`：取得供人或 Agent 继续工作的有界 Project Workspace。它不会默认输出完整 Context Pack。
- `nexus doctor`：只读诊断 Project binding、实例、Continuity、Codex Host Adapter 和 launcher；不会自动修复。
- `nexus host status codex`：检查用户级 Codex SessionStart 注册。信任状态没有受支持的查询接口，因此安装状态不会被报告为 TRUSTED。
- `nexus host install codex`：在真实 TTY 中审阅摘要并输入 `INSTALL NEXUS HOST ADAPTER CODEX` 后，才写入用户级配置。
- `nexus host uninstall codex`：在真实 TTY 中输入 `UNINSTALL NEXUS HOST ADAPTER CODEX`，只移除 Nexus 自己的注册。

Codex Adapter 注册调用的是已安装的 `nexus hook session-start`。它不会执行当前仓库提供的脚本。Codex 的 SessionStart `startup`、`resume`、`clear`、`compact` 事件以输入中的 `cwd` 查找最近的 Project Manifest；非 Nexus 目录直接无输出。首次安装后，在 Codex 中运行 `/hooks` 检查并按 Codex 提示信任该用户级 Hook。Nexus 不绕过 Host 信任设置。

`nexus continue` 是长期通用的 pull 式 Continuity 入口。Codex Hook 或未来 Host adapter 不可用时，仍可显式运行该命令。Host Integration 返回的是临时派生上下文，不是 canonical state；SessionStart 不启动或结束 Task/Run，也不更新 Current State、Context、Memory 或 Grant。

## 信息与可见性边界

Workspace 从已验证的 Nexus Context 与 Panel lifecycle projection 确定性构建，并有大小预算；它只列出可按需读取的引用，不展开完整 Context Pack 或任意 Object payload。没有证据的值保持 `UNKNOWN` 或 `UNAVAILABLE`。Hook 输出被 Host 接收不等于 Nexus 已证明模型看到了内容；历史 `model_visible_exposure` 继续保持 `UNKNOWN`。

Codex 是当前首个 Host Adapter。其他 Host 应只负责解析各自事件并传递 Host-native context；continuity 语义仍复用公共 Host Integration。DSH、Claude Code 和通用 MCP read plane 尚未在此版本实现。
