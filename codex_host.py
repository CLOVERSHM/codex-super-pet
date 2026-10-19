"""Versioned app-server RPC. Writes only through the existing host control socket."""
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path


def executable():
    paths = [shutil.which("codex"),
             "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"]
    for path in paths:
        if path and Path(path).is_file():
            return path
    raise ValueError("未找到 Codex 命令；仍可保存审批并手动续接")


class Rpc:
    def __init__(self, shared=False):
        self.messages = queue.Queue()
        self.serial = 0
        command = [executable(), "app-server", "proxy"] if shared else [
            executable(), "app-server", "--stdio"]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.call("initialize", {"clientInfo": {"name": "delivery_console",
                                                    "version": "0.2.0"}})
            self.send({"method": "initialized"})
        except Exception:
            self.close()
            raise

    def _read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                continue
        self.messages.put(None)

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def next(self, timeout=20):
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("Codex 响应超时")
        if message is None:
            raise ConnectionError("Codex 连接中断；共享控制连接可能未启用")
        if "id" in message and "method" in message:
            # Never grant tool permissions from a delivery approval.
            self.send({"id": message["id"], "error": {
                "code": -32601, "message": "请在 Codex 中单独确认工具权限"}})
        return message

    def call(self, method, params):
        self.serial += 1
        ident = self.serial
        self.send({"id": ident, "method": method, "params": params})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            message = self.next(max(.1, deadline - time.monotonic()))
            if message.get("id") == ident and "method" not in message:
                if "error" in message:
                    raise ValueError("Codex 拒绝请求：" + str(message["error"].get("message", ""))[:300])
                return message["result"]
        raise TimeoutError("Codex 响应超时")

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        for stream in [self.process.stdin, self.process.stdout]:
            stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Host:
    def threads(self, query=""):
        with Rpc() as rpc:
            result = rpc.call("thread/list", {"limit": 50, "sortKey": "updated_at",
                                             "searchTerm": query or None, "useStateDbOnly": True})
            return [self.metadata(t) for t in result.get("data", [])]

    @staticmethod
    def metadata(thread):
        return {"id": thread["id"], "title": thread.get("name") or thread.get("preview", "")[:160],
                "cwd": thread.get("cwd", ""), "status": thread.get("status", {}).get("type", "unknown")}

    def read(self, ident):
        with Rpc() as rpc:
            return self.metadata(rpc.call("thread/read", {"threadId": ident,
                                                         "includeTurns": False})["thread"])

    def run(self, job, receipt):
        # Do not resume a desktop-owned thread in an independent server.
        try:
            rpc = Rpc(shared=True)
        except Exception:
            receipt("待发送", "共享 Codex 发送连接不可用；审批已保存，可复制续接指令到关联对话")
            return
        started = False
        try:
            thread = rpc.call("thread/read", {"threadId": job["thread"]["id"], "includeTurns": False})["thread"]
            if thread.get("cwd") != job["thread"]["cwd"]:
                receipt("发送失败", "对话工作目录已变化，请重新绑定")
                return
            if thread.get("status", {}).get("type") == "active":
                receipt("待发送", "对话正在运行，请结束后再发送；不会插入或打断当前任务")
                return
            rpc.call("thread/resume", {"threadId": job["thread"]["id"], "excludeTurns": True})
            policy = {"type": "readOnly", "networkAccess": False} if job["scope"] == "preflight" else {
                "type": "workspaceWrite", "networkAccess": False,
                "writableRoots": [job["thread"]["cwd"]]}
            # Persist uncertainty before the non-idempotent RPC is written.
            receipt("发送中", "正在发送；断连后不得自动重试")
            started = True
            result = rpc.call("turn/start", {"threadId": job["thread"]["id"],
                                            "input": [{"type": "text", "text": job["prompt"]}],
                                            "sandboxPolicy": policy, "approvalPolicy": "never"})
            turn_id = result["turn"]["id"]
            receipt("执行中", "Codex 已接收指令；不代表任务完成", turn_id)
            while True:
                try:
                    message = rpc.next(timeout=30)
                except TimeoutError:
                    continue
                params = message.get("params", {})
                if params.get("threadId") != job["thread"]["id"]:
                    continue
                if message.get("method") == "turn/completed" and params.get("turn", {}).get("id") == turn_id:
                    status = params["turn"].get("status")
                    receipt("本轮结束" if status == "completed" else "执行失败",
                            "请核验对话结果与交付证据；本轮结束不等于已发布", turn_id)
                    return
        except Exception:
            receipt("结果未知" if started else "发送失败",
                    "连接异常；请检查关联对话，结果未知时禁止自动重试")
        finally:
            rpc.close()
