# 双向同步原型

## 已实现范围

本原型独立于8767控制台，不修改真实需求或会话。合成宿主通过协议形状的
thread/started、item/started/completed、thread/goal/updated/cleared事件驱动SQLite投影。
消息项按会话与item ID去重；目标A→B→A保留两次修改，不按正文永久去重。
输入与目标正文变化增加任务版本，目标运行状态变化不等于业务需求完成。

Web通过带Bearer认证的增量长轮询即时取得新快照，断线后读取当前完整投影。
不把所有聊天逐句转成正式需求；输入先保留在合成会话投影里，业务分类尚未实现。
审批绑定任务版本、原会话ID、cwd、范围和人工意见，输入/目标修改使旧批准单失效。
发送前持久化sending状态，异常或重启记unknown，不自动重试。
精确匹配command ID与thread ID的宿主回执才显示received；这仅表示接收，不是执行完成。
批准单回环被识别为echo，不增加用户任务版本。人工角色仍是同OS用户的流程护栏。

## 运行

```bash
python3 sync_prototype.py --port 8770
```

打开终端输出的本机地址。左侧是合成宿主，不是真实Codex窗口。
可打开两个浏览器页面观察新会话、输入、目标及批准单自动同步。
启动使用临时SQLite，退出清理；不会读取本机历史聊天或公司业务数据。

```bash
python3 -m unittest discover -s tests -p test_sync_prototype.py -v
node tests/sync_browser.cjs
python3 tools/probe_host.py
```

浏览器验收需Node/Playwright/Chromium，CHROME_PATH可指定已安装Chrome。
浏览器报告和截图位于artifacts/，不入Git或分发包。

## 当前真实宿主阻塞（2026-10-15）

codex-cli 0.162.0-alpha.2的实验JSON Schema有上述事件、目标设置与turn/start/steer。
仅有schema不证明运行时支持，也不证明某客户端能收到所有桌面窗口事件。
本机daemon version只读查询报告默认app-server-control.sock不存在，
现有Rpc(shared=True)初始化失败；桌面codex进程未显示命名共享Unix socket。
没有启动/重启daemon、启用remote control、修改全局配置、读取凭据或真实聊天。
官方文档本轮访问不可用；不据此安装未确认的hooks或依赖。

这轮只完成合成协议与Web往返，不宣称真实四项联通通过。
原8767控制台和它的人工复制续接方式不变。临时原型不是已安装插件。

## 下一道验收

1. 确认本机桌面宿主实际支持的共享事件/命令入口，以及如何配置、撤销和限定项目。
2. 获得明确范围后，用人工创建的空白验收会话测试桌面新输入、目标变更、新会话事件。
3. 验证事件订阅是否全局、如何订阅已有会话、增量回补及断线后的完整性。
4. Web批准只读合成指令后，必须在同一桌面会话看到消息、准确接收回执与运行状态。
5. 单独实现执行前检查点。当前原型不阻止Codex自行继续，不能称审批执行门禁。

不可用共享入口不能由另一个独立app-server接管原桌面线程来替代。
MCP工具调用或技能主动上报也不天然覆盖所有用户输入/目标变化。
若需hooks或桌面配置变更，应先核实版本契约、列出变更与回退，再显式安装；
不得把合成成功当作全局安装和真实会话操作授权。
