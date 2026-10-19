# CodexBox 交付控制台

[English](README.en.md)

本机项目、会话与需求管理台。首页按 Codex 桌面本机项目展示会话，支持状态筛选、搜索、排序、详情和 `codex://` 跳转；右侧可打开管理面板和需求账本。Python 3.9+ 即可运行，服务只监听 loopback，不执行生产部署。

## 快速开始

1. 下载并解压 `releases/需求交付控制台-0.1.0-web.zip`，在目录内运行 `python3 launcher.py`；macOS 也可运行 `启动交付控制台.command`。
2. 打开终端显示的本机地址，通常为 `http://127.0.0.1:8767/`。首次引导预选近七个自然日及未排除项目；可调整范围或跳过。只有点击“开始采集”才读取历史用户输入并生成待人工审阅的候选。
3. 在管理面板确认候选、设置项目采集排除或生成日报/周报。需求账本保留进度、证据和人工决策。停止运行 `python3 stop.py`。

![首次引导，合成数据](docs/images/first-run.png)
![会话工作台，合成数据](docs/images/session-board.png)
![管理面板，合成数据](docs/images/management.png)

## 数据边界

真实采集摘要、会话状态、需求账本、生成技能与运行日志只保存在本机 `~/.local/share/delivery-console/private/`；`runtime.json` 运行令牌保存在其上级数据目录。**不要上传或共享数据目录**。扩展适配器也只从该私有目录加载；仓库和 ZIP 不包含任何个人项目记录、来源适配、业务翻译或原始聊天。详见 [隐私与扩展](docs/privacy.md)。

采集使用本地规则初筛，不是自动 AI 语义判断。每十分钟对账仅检测本机输入元数据变化，不能推断任务完成，也不能保证每轮自动调用技能。技能显式上报的状态及人工复核均需核对证据；审批不授权生产操作。桌面索引格式及协议跳转依赖本机 Codex 版本。

## 源码验证

```bash
python3 tools/package.py
python3 -m unittest discover -s tests -v
```

打包必须先于测试。ZIP 和 SHA-256 生成于 `releases/` 并复制到桌面；运行测试仅用临时合成数据。可选浏览器检查依赖 Playwright/Chromium，应用运行本身不需要 Node。仓库公开发布应从全新 Git 历史开始，不要推送旧仓库的历史提交与旧发行包。
