# Nexus v2 — Step 0 环境盘点

盘点时间：2026-09-24（本机时区 Asia/Shanghai）  
目标：当前仓库工作区（本地绝对路径因隐私已省略）
状态：Step 0 PASS；以下内容来自当时现场检查，不从手册推定既有部署路径。

## 主机与工具

| 项目 | 现场结果 | 验证 |
|---|---|---|
| 操作系统 | Windows，NT build `10.0.22631.0`；发行版/版本名称未能由受限 WMI 查询确认 | `.NET Environment.OSVersion`；WMI 返回拒绝访问 |
| PowerShell | `7.6.5` | `$PSVersionTable` |
| Python | `3.11.0` | `python --version` |
| Git | `2.40.0.windows.1` | `git --version` |
| SQLite | `3.38.4`（Python `sqlite3`） | `sqlite3.sqlite_version` |
| SQLite FTS5 | 可用 | 内存库创建 FTS5 虚表成功 |
| JSON Schema 验证器 | 当时检查的 Python 与 Node 运行时中未发现 | 检查常用 Python 验证包与 Node Ajv |
| 可用空间 | 盘点时系统盘空间充足；精确值省略 | PowerShell FileSystem drive |

未因环境盘点安装依赖。基础运行时中有可用 YAML/Pydantic/cryptography 支持；Step 1 使用了仓库声明的验证依赖。

## 工作区与 Git

- 初始分支为 `main`，初始提交为仓库原有 root commit；Nexus 实施使用独立分支，未覆盖初始分支。
- 初始工作区只含仓库说明文件；没有项目 `AGENTS.md`、Nexus 源码、同名数据库或忽略规则。
- Nexus v2 源码位于 `kernel/`、`adapters/`、`schemas/`、`migrations/` 和 `tests/`；运行数据、真实凭证、Trace 与 payload 不提交。
- Git 元数据写入曾受环境权限控制；受控写入完成了分支与阶段提交。

## 既有本地系统（脱敏摘要）

- 现场只读检查确认存在既有用户级 Codex 配置、Skills、插件/连接器配置与本地 SQLite 状态；它们不属于本仓库发布内容。
- 盘点期间仅记录相关文件是否可用及其治理边界；未读取或记录任何密钥值、认证内容、私人聊天、Memory 或业务 payload。
- 已验证的外部快照用于 Step 0 回滚；快照位于仓库之外，未复制进 Git。其具体本机路径和文件清单因隐私不公开。
- 未在当时检查范围内发现既有 Nexus v1.2 Runtime 或同名 Nexus 数据库。此结论仅限于当时已检查范围，不推断其他位置。

## Step 0 快照与验证

- 已创建仓库外的本地保护快照；不随源码发布，具体路径和文件数量已脱敏。
- 快照清单包含文件长度与 SHA-256；对抽样配置/Skill 副本做逐字节哈希核验，并对一致性 SQLite 副本执行 `PRAGMA integrity_check = ok`。
- 快照源系统未修改，快照未进入 Git。
- 回退基线：初始 Git root commit 与上述隔离快照。提交历史中的本机快照路径已在发布脱敏中移除。

## 未决现场事项

1. 当时无法通过受限权限确认操作系统发行版名称；密钥内容未读取，因此不推断 Credential Broker/密钥库状态。
2. Hosted v0.1 不接独立真实模型 Provider 或外部写工具；独立 Provider/Credential Broker 配置属于 `DEFERRED / NOT_CONFIGURED`，不代表 Hosted Core 失败。
