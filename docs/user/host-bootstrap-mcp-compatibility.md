# Host Bootstrap MCP dependency compatibility

本 release 只为外部 reviewed Minimal Host Bootstrap 的 `dependencies()` 增加
`"mcp": "mcp"` mapping。完整 installer 不进入仓库；旧 artifact 保留。
新 artifact 的文件名、旧/新原始 SHA-256 和 exact patch hash 记录在
`tools/host-bootstrap-mcp-compatibility.json`，等待独立审查。
`tools/host-bootstrap-mcp-dependency.patch` 保留为 provenance，不自动执行。
历史 patch 的 hunk 定位比实际源码早一行，mapping 内容和 accepted source hash
完全匹配。交付的 `exact-installer.diff` 从旧/新 bytes 重新生成准确 unified diff；
其 hash 也记录在 metadata 中，历史 patch 不被覆盖或删除。

Bootstrap **只验证依赖，不安装依赖**。现有安装准备方式仍是使用选定 Python
和 canonical `requirements.lock.txt` 准备依赖，然后运行 Bootstrap 的 exact
direct dependency version/import verification。没有新增另一份 dependency list，
没有放宽 pin，也没有增加网络安装、自动升级或新的 runtime architecture。
缺包、版本不符或 import 失败继续 fail closed。

交付 artifact 保留原有 release-selection 默认值；它不会自动选择或部署
`c10b247...`。未来部署仍通过既有 reviewed activation 方式显式选择已接受的
Git SHA 和 repository，不能把 launcher 指向开发 worktree。

## 隔离验证

从 accepted Git objects materialize `690c7fe0...` 和 `c10b247...` snapshots。
在 disposable Windows Host root 和隔离 Python 环境中安装 canonical lock，
根据已安装 distribution 的 `Requires-Dist` 和当前 Windows/Python markers
检查 transitive closure；不手工维护 import/package 名单。

测试执行真实 installer 的 snapshot/dependency/smoke/switch/rollback 路径。
仅将 Host root、Python interpreter 和 launcher interpreter 常量重定向至
fixture；实际 `update_user_path()` 被替换为测试记录，避免写真实 HKCU PATH。
这些调整仅存在于测试内存中，不进入交付 artifact。
子进程移除 `PYTHONPATH/PYTHONHOME`，从 repository 外运行。
fixture 同时设置进程级 `PYTHONUTF8=1 / PYTHONIOENCODING=utf-8`；不修改真实
Host 的全局编码配置，也不改变 installer 的现有编码行为。

覆盖旧 runtime、升级后 help/MCP help、snapshot conflict、失败回滚及 import
shadow protection。官方 MCP client 只连接临时 Nexus fixture：默认 reader
initialize/list 成功而内容读取 DENIED；显式 `local-no-egress` 才能读取 fixture。
读取前后比较 fixture DB/Object/Journal bytes、size 和 mtime。

`local-no-egress` 不得用于 Codex、ChatGPT、Claude 或其他远端模型 Host；本次
不连接 production，不修改真实 launcher/runtime/Hook，不执行 Checkpoint。
此证据不等同于真实 Windows Host deployment acceptance。

## 重跑

设置 `NEXUS_REVIEWED_HOST_INSTALLER` 指向旧 accepted source，设置
`NEXUS_BOOTSTRAP_TEST_PYTHON` 指向已按 canonical lock 准备的 disposable Python。
该 interpreter 运行 `tests.integration.test_host_bootstrap_dependencies`。
可选 `NEXUS_BOOTSTRAP_RECEIPT_DIR` 仅指向 repository 外的临时输出目录。
未提供外部 artifact/隔离环境时，相关外部验证测试显式跳过。
这些环境变量仅用于 regression fixtures，不是 Bootstrap 产品接口。
