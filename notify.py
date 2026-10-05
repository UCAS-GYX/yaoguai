# 异常通知：macOS 系统通知（默认开）+ 飞书群机器人（可选：把 webhook 地址写进 notify_webhook.txt 才会发）。
# 同一条消息 10 分钟内只发一次；发送在后台线程里做，不卡 bot。
import json, os, subprocess, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WEBHOOK_FILE = os.path.join(HERE, "notify_webhook.txt")
QUIET = 600
_LAST = {}
_LOCK = threading.Lock()


def _webhook():
    try:
        with open(WEBHOOK_FILE, encoding="utf-8") as f:
            u = f.read().strip()
        return u if u.startswith("https://open.feishu.cn/") or u.startswith("https://open.larksuite.com/") else ""
    except OSError:
        return ""


def _send(title, msg):
    try:                                               # macOS 通知（文字通过参数传给 AppleScript，不拼接进脚本，防注入）
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        "display notification (item 2 of argv) with title (item 1 of argv) sound name \"Glass\"",
                        "-e", "end run", title, msg[:200]], timeout=10, capture_output=True)
    except Exception:
        pass
    url = _webhook()
    if url:
        try:
            body = json.dumps({"msg_type": "text", "content": {"text": f"{title}\n{msg[:1000]}"}}).encode()
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass


def notify(msg, title="妖怪金手指", key=None):
    """key 相同的消息 10 分钟内只发一次（默认用消息前 40 个字当 key）"""
    k = key or msg[:40]
    with _LOCK:
        if time.time() - _LAST.get(k, 0) < QUIET:
            return False
        _LAST[k] = time.time()
    threading.Thread(target=_send, args=(title, msg), daemon=True).start()
    return True


if __name__ == "__main__":                             # 手动测试：./venv/bin/python notify.py
    notify("这是一条测试通知", key="test")
    time.sleep(3)
    print("已发送测试通知" + ("（含飞书）" if _webhook() else "（未配置飞书 webhook）"))
