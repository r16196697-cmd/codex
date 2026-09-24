# Nexus v2 — Step 0 环境盘点

盘点时间：2026-09-24（本机时区 Asia/Shanghai）  
目标：`<LOCAL_PATH_REDACTED>`  
状态：Step 0 PASS；以下内容基于现场检查，不从手册推定既有部署路径。

## 主机与工具

| 项目 | 现场结果 | 验证 |
|---|---|---|
| 操作系统 | Windows，NT build `10.0.22631.0`；发行版/版本名称未能由受限 WMI 查询确认 | `.NET Environment.OSVersion`；WMI 返回拒绝访问 |
| PowerShell | `7.6.5` | `$PSVersionTable` |
| Python | `C:\Python\Python311\python.exe`，`3.11.0` | `python --version` |
| Git | `2.40.0.windows.1` | `git --version` |
| SQLite | `3.38.4`（Python `sqlite3`） | `sqlite3.sqlite_version` |
| SQLite FTS5 | 可用 | 内存库创建 FTS5 虚表成功 |
| JSON Schema 验证器 | 当前 Python、Codex 捆绑 Python 与已检查 Node 捆绑依赖中均未发现 | `jsonschema`、`fastjsonschema`、`pytest` 未安装；Node bundle 未发现 Ajv |
| 当前可用空间 | C: 约 150.6 GB | PowerShell FileSystem drive |

Python 已安装模块抽查：`PyYAML` 可导入；`pydantic`、`cryptography` 可在捆绑 Python 导入。运行时尚无 JSON Schema 验证器。没有安装任何依赖。

## 工作区与 Git

- 工作目录/仓库根：`<LOCAL_PATH_REDACTED>`
- 初始分支：`main`；初始 HEAD：`ac61316178d7e145ed420de2fbb9ee0273067263`
- 初始工作区干净，仅有 `README.md` 与 `.gitattributes`；没有项目 `AGENTS.md`、Nexus 源码、同名数据库或忽略规则。
- 已按部署手册创建分支 `nexus-v2-runtime`；当前仍以同一 HEAD 为回滚基线，未改动 `main`。
- `.git` 元数据受到沙箱只读拒绝规则保护。创建分支经受控审批成功；后续提交也需要相同受控写入流程。
- Nexus v2 源码当前位于仓库的 `kernel/`、`adapters/`、`schemas/`、`migrations/` 和 `tests/`；生产数据必须单独 gitignore，真实凭证/Trace/payload 不提交。

## 既有 Codex 系统（只读审计）

- 用户已明确说明，旧系统是此前成功升级的全局经验沉淀部署。全局文件为 `<LOCAL_PATH_REDACTED>` 与 `<LOCAL_PATH_REDACTED>`。
- 本次只读核对两文件的快照副本与现场原件 SHA-256 相同。用户列出的升级前 AGENTS/Skill 备份及 config 测试备份仍在原处。它们是受保护旧系统的一部分，本部署不修改。
- 个人 Skill 发现目录：`<LOCAL_PATH_REDACTED>`，现场有 105 个目录（含 `.system`）；项目 `project-experience-curator` 的当前文件及升级前备份均存在。
- 用户级 `<LOCAL_PATH_REDACTED>`、`config.toml` 存在；配置中有一个 `node_repl` MCP server section、十个 Plugin section。没有读取或记录任何密钥值；配置中未发现 Nexus 模型 Profile/Adapter 配置。
- `auth.json`、`.env`、`.env.txt` 存在，内容未读取；确切 Credential Broker/操作系统密钥库策略未知。Nexus v0.1 暂不接真实模型或外部写工具，此项不阻塞 Steps 1–7，接真实能力前必须明确。
- 用户级 Codex SQLite 数据文件位于 `<LOCAL_PATH_REDACTED>` 与 `<LOCAL_PATH_REDACTED>`。用户备份根为 `<LOCAL_PATH_REDACTED>`。
- 在已检查的当前仓库、`Documents\Codex` Nexus 文件名结果及 `.codex` 顶层 Nexus 目录中，未发现既有 Nexus v1.2 Runtime 或 Nexus 数据库。已存在的旧经验沉淀部署不等同于 Nexus Kernel v1.2；不作此推定。
- Codex 全局配置可解析；用户级 SQLite 数据库包括活动 WAL，快照采用 SQLite backup API，而非直接复制 WAL 文件。

## Step 0 快照与验证

- 快照目录：`<LOCAL_PATH_REDACTED>`
- 内容：全局 AGENTS/config、认证文件（只复制、不读取值）、模型目录文件、历史元数据、memories、个人 Skills、插件目录，以及 7 个 SQLite 一致性副本；不含临时目录/缓存目录。
- 快照清单：`snapshot-manifest.json`，覆盖生成时的 3,201 个文件并记录 SHA-256 与长度。
- 验证通过：快照的全局 AGENTS、当前 `project-experience-curator/SKILL.md`、升级前 Skill 备份和 `config.toml` 与原件逐字节哈希一致；配置 TOML 可解析；7 个 SQLite 副本均 `PRAGMA integrity_check = ok`。
- 快照位于仓库之外、既有用户备份目录中，继承该目录 ACL；未复制进 Git。源系统未修改。
- 回退基线：Git commit `ac61316178d7e145ed420de2fbb9ee0273067263` + 上述隔离快照。

## 未决现场事项

1. 系统发行版名称和 Windows Credential Broker 的使用状态因权限/未读取凭证值而未知；不得以推测填补。
2. 系统与捆绑运行时无 JSON Schema 验证库。Step 1 必须使用成熟验证器；若无法通过可审计依赖安装获得它，Step 1 标记 BLOCKED，不得自造子集验证器。
3. 当前用户级 `.codex` 目录为只读沙箱范围；本任务仅使用已审批的快照恢复点，不修改旧系统。
