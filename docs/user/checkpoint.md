# Project Nexus Checkpoint / Milestone v1

`nexus checkpoint` 把一个明确的项目状态更新组合成一次操作：只读 preflight、一次 HUMAN 确认、Task Start、Continuation Commit、Task Finish、完成证据验证。它复用现有治理服务，不执行实现工作，也不证明工作质量。

从已 attach 的 Project Nexus 根目录或子目录运行：

```powershell
nexus checkpoint --accepted-revision <independently-accepted-sha> `
  --objective "当前目标" --completed "本次完成摘要" --next "下一步"
```

也可以使用 `nexus checkpoint --proposal <proposal.json>`。UTF-8 JSON 必须恰好包含：

```json
{
  "accepted_revision": "<independently-accepted-sha>",
  "current_objective": "当前目标",
  "recent_work": "本次完成摘要",
  "next_step": "下一步"
}
```

proposal 文件放在仓库外；Git worktree 必须严格 CLEAN。Git fact 验证本地 HEAD、branch、configured upstream tracking ref；不声称实时验证 remote。accepted revision 必须等于此次验证的 HEAD。工作摘要、objective 和 next step 保持 `HUMAN_OPERATOR_ASSERTION`，模型可见性仍为 `UNKNOWN`。

## 一次确认

preflight 检查实例、模式、空闲状态、Current State、verified Context、Git、原始 principal、schema、classification、完整精确资源闭包、collision 和 Context 大小。检查失败不请求确认。

真实交互终端显示完整产品语义与授权范围，输入：

```text
CHECKPOINT project-nexus
```

禁止 `--yes`、管道确认或非 TTY bypass。确认前不开 writer。拒绝不会写 canonical 数据；host-local serialization lock 可能已创建。确认绑定完整冻结请求，而非三条独立命令的默认批准。内部 callbacks 每次检查该请求的 canonical CommandLedger 精确绑定，然后复用同一次 HUMAN 授权。

授权仅允许一个 ORCHESTRATOR Task，零 child / MODEL / TOOL call；固定有限预算；两小时原始 Grant；精确 scope；无 EGRESS、DELEGATE、EFFECT、Skill admin 或配置权限。

## Retry 与 crash

默认公开 identity 从产品 proposal 确定性生成。**原命令原参数重试**即可，不需要管理内部 IDs。可选 `--id release-example` 为不同 milestone 指定一个短的公开 handle；相同 handle 改变语义返回 `COMMAND_CONFLICT`。

内部计划和时间戳冻结在以下显式 host-local receipt：

```text
<host-registry-parent>/checkpoints-v1/<instance-id>/cp-<digest>.json
```

receipt 不属于 canonical Project truth，且不进入仓库、Manifest 或 Context。应保留它以便恢复；它单独存在不构成授权。完整请求 hash 由既有 canonical CommandLedger 绑定。缺失或被改写的 receipt 不能重建历史授权；fail closed。这个 v1 不提供 receipt 跨 host 迁移或清理。

同一实例的 Checkpoint 用已接受的 OS filesystem lock 串行化。进程退出释放锁；留下的 sidecar 不代表仍被锁住。receipt 原子写入发生在确认后、canonical binding 前；在两者之间中断，重试仍需确认。binding 后中断则不再确认。

部分执行返回 `CHECKPOINT_PARTIAL_RESUMABLE` 和稳定 reason。原计划重试复用已有 Task/Run/Grant，继续尚未完成的阶段。Continuation 已提交时保留新 Current State，继续 Finish。原 Grant 过期、撤销、出现新的 current state 或安全门禁不满足时，禁止向前写入，绝不 replacement Grant 或回滚。此时保留现场等待治理处理；`RESUMABLE` 不保证任何 authority 状态下都可以继续。

完整成功返回 `CHECKPOINT_COMPLETED`、state revision、accepted revision、Current State / Context refs 与 Task/Run outcome。丢失成功响应后的 exact replay 验证三阶段历史证据，零 canonical mutation、零确认，即使更晚的 Checkpoint 已前进。历史 replay 不要求当前 Grant 或 live Git。

## Launcher 与范围

使用已安装的 `nexus` entry point；无需 PYTHONPATH 或 data-root/policy/journal 参数。安装后新开的普通 PowerShell 必须能通过 `Get-Command nexus` 找到 launcher。当前 shell 未继承已更新 PATH 时，重新打开终端；这不改变 Host Bootstrap。该 release 的部署应继续使用 pinned accepted runtime，不能把真实 launcher 指向未接受的开发树。

v1 仅支持已接受 Continuation protocol 的 `project-nexus`。它未改变 Core、Authority、Task/Run、Continuation、Finish、Effect、Purge 或 Recovery；不新增数据库、canonical schema 或 migration。旧三阶段 engineering CLI 保持可用。
