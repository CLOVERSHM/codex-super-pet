---
name: session-status
description: Summarize the current Codex conversation's task state for the local project-session dashboard after meaningful task work or when blocked; do not create requirements or approve actions.
---

# 会话状态回报

完成当前轮任务或明确卡点时，判断任务是否真正完成；回复结束不代表完成。
先通过当前可信上下文取得**本对话的准确 thread_id**，不得从标题或工作目录猜。
然后使用同级 `../manage-delivery/scripts/bridge.py`：

```bash
python3 "$BRIDGE" board-context --id "$THREAD_ID"
```

读取 `project_id`、`latest_turn_id`、`observation_hash`，核对确是当前对话。
无法准确取得身份或版本时向用户说明回报缺口，不把别的对话写入。
基于本轮实际结果整理一个短报告，不读取其他对话：

```bash
python3 "$BRIDGE" board-report --json '{"thread_id":"...","project_id":"...","turn_id":"...","observation_hash":"...","task_key":"稳定任务名","workflow":"通用任务","stage":"排查","state":"blocked","summary":"已定位问题，修复未完成","reason":"测试环境不可用","next_action":"恢复隔离环境后复测","evidence":["脱敏证据引用"]}'
```

统一状态：`in_progress`、`completed`、`blocked`、`waiting_approval`、
`waiting_user`、`unverified`。仅全部用户任务要求已完成才用 `completed`。
阻塞、待审批和待补充必须写具体原因及下一步；审批状态不等于授权。
同一个任务沿用 `task_key`，新任务另取稳定标识，不凭聊天标题合并独立任务。
项目加白、来源异常、缺少可靠身份或新输入竞态时不强写或绕过门禁。
仅提交脱敏短摘要和证据引用，不提交原聊天、令牌、工具输出或生产数据。
控制台只记录 skill 声明，不独立认证完成或部署。技能发现不等于每轮自动触发；
10 分钟对账只能发现缺失或过期回报，不会自动运行本技能或推断任务状态。
