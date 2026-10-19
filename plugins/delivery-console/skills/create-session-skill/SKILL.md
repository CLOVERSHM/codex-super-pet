---
name: create-session-skill
description: Generate a project-specific Codex workflow skill compatible with the local session dashboard when a user defines a project's custom conversation workflow.
---

# 生成项目专属 skill

先明确项目准确 ID、skill slug、适用场景、工作流名称和步骤。
使用 `tools/create_project_skill.py` 生成仓库内可分发 skill；可以提供
`--instructions-file` 读取用户定义的个性化流程说明。生成后运行本目录
`tools/validate_project_skill.py` 以及通用状态契约的合成测试。
生成产物在 `plugins/delivery-console/skills/<slug>/`，不会自动安装或启用；
用户在项目配置中启用后才能使用该 `skill_id` 报告。

个性化流程由该 skill 自主执行；平台只理解统一状态、阶段、原因、下一步及
脱敏证据引用。生成器只保障格式兼容，不保障业务判断正确或每轮自动触发。
不得将这一步当成生产操作、审批授权或外部发布。
