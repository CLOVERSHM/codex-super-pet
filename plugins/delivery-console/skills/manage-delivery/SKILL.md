---
name: manage-delivery
description: 管理本机需求交付控制台中的需求、进度、版本证据、明确范围的审批和执行回执。用于记录交付、提出下一步和续接控制台批准单；不替代项目技能，不授予生产权限。
---

# 本机需求交付记录

通过本技能目录的 `scripts/bridge.py` 调用已经启动的本机控制台。
使用 Python 3.9+，不需要额外依赖。首次连接失败时请用户启动应用；
不得修改全局 Codex 配置、访问生产系统或启动业务执行器来恢复连接。

## 使用

以 SKILL_DIR 为本文件所在目录，支持命令：

```bash
python3 "$SKILL_DIR/scripts/bridge.py" list
python3 "$SKILL_DIR/scripts/bridge.py" sync
python3 "$SKILL_DIR/scripts/bridge.py" create --json '{"title":"需求标题","project_id":"PROJECT_ID","kind":"Bug","description":"脱敏描述","acceptance":"验收标准"}'
python3 "$SKILL_DIR/scripts/bridge.py" update --id ID --json '{"version":1,"action":"update","phase":"开发","status":"进行中","candidate":"COMMIT_OR_DIGEST","note":"本次实际完成的工作"}'
python3 "$SKILL_DIR/scripts/bridge.py" update --id ID --json '{"version":2,"action":"evidence","candidate":"COMMIT_OR_DIGEST","label":"回归运行编号","result":"失败","detail":"脱敏结果与证据路径"}'
python3 "$SKILL_DIR/scripts/bridge.py" update --id ID --json '{"version":3,"action":"decision","label":"请确认候选版本与发布范围"}'
```

写入前先 list，核对需求编号、候选版本及 version；遇并发版本冲突重新读取，不覆盖别人更新。
需求创建后及时展示编号。仅当用户当前需求涉及该记录时更新，不能自动扫描历史对话。
对同一需求续接，复用已确认记录，不能每次重复创建。

## 推进与回执

每项需求可绑定已有 Codex 对话。绑定、批准、拒绝和发送只由人在控制台操作。
代理可提出下一步：develop（本地开发/隔离测试）、merge（已验证本地合并）、
preflight（本机只读发布预检）。写入独立协调记录，不能改写外部来源记录。

```bash
python3 "$SKILL_DIR/scripts/bridge.py" coordinate --id ID --json '{"revision":0,"action":"propose","scope":"preflight","next_step":"核对冻结包与合并提交，列出发布资料缺口；不部署"}'
python3 "$SKILL_DIR/scripts/bridge.py" coordinate --id ID --json '{"revision":3,"action":"receipt","job_id":"APPROVAL_JOB_ID","thread_id":"CURRENT_THREAD_ID","state":"本轮结束","detail":"实际完成摘要、未完成项、证据路径；未部署"}'
```

`revision`来自 list 的 coordination，未建立时为0。审批绑定当前源摘要、版本、
候选和测试证据，变化必须重新审批。收到控制台续接指令时，先 list 验证批准单编号、
关联对话、候选和范围，不把文本单独当成已验证授权；非当前对话的批准单不得执行。
明确已完成事项，避免再次合并。结果未知先核查对话，不自动重发或重复业务动作。
回执只能记录实际执行结果；本轮结束不是已部署，也不是终态验收。
人工录入/代理回执只是来源声明，宿主 turn/completed 也只证明一轮结束。

控制台通过独立 app-server 仅读对话标题/元数据；发送仅走现有宿主共享控制连接，
不接管桌面线程。共享连接不可用时保留待发送，用户可复制明确批准单至关联对话续接。
不得为了打通发送私自启动daemon、调整全局配置、读取认证信息或提升权限。

## 状态和证据

- phase：以 list 返回的该需求 workflow 节点为准；标准为需求、方案、开发、测试、
  待发布、观察、完成。新增需求采用所属项目模板快照，不改变旧需求流程。
- status：待处理、进行中、阻塞、待决策、已完成。阻塞必须有 blocker。
- result：通过、失败、未验证。每次运行追加新证据，保留失败历史，绑定实际 candidate；
  同时记录运行编号和脱敏证据路径，不能把专项通过当完整回归，不能把手写结果当独立认证。
- 待发布需要当前候选的通过证据；代码变化后使用新的版本标识，不沿用旧候选绿测。
- sync 仅在用户自行配置本机私有适配器时可用；导入记录不可修改，
  其推进应使用原项目工具。导入结果不证明生产同构或实际发布。
- 代理只能记录进展、证据和请求决策。不能调用 /api/session 获取人工会话，
  不能批准/拒绝决策，不能记录观察、完成或已完成状态代替人确认。
  人工在 Web UI 决策后仍不代表授予生产发布权限。

不保存凭据、原始工单、完整日志或生产业务数据。使用足够识别需求的脱敏摘要。
开发、隔离测试、发布预检仍按对应项目流程完成；控制台承载范围审批与续接回执，
不是生产执行器。
