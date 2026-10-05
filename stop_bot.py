# 退出：结束守护进程和 bot（不会自动重启），再关闭后台浏览器。用法: ./venv/bin/python stop_bot.py
# 监控页的「退出」按钮也调用这里的 stop_all()。之后用 start_bot.py --resume 或监控页「继续执行」重新开始。
import json, os, signal, time
import websocket
import cdp_backend


def _kill(pids, sig=signal.SIGTERM):
    for p in pids:
        try:
            os.kill(p, sig)
        except ProcessLookupError:
            pass


def stop_all(close_browser=True):
    """返回 (之前是否在运行, 是否关闭了后台浏览器)"""
    sup, bot = cdp_backend.find_pids("supervise.py"), cdp_backend.find_pids("yaoguai_bot.py")
    was = bool(sup or bot)
    _kill(sup)                                         # 守护收到 SIGTERM 会先结束 bot，且不重启
    for _ in range(20):
        if not cdp_backend.find_pids("yaoguai_bot.py"):
            break
        time.sleep(0.5)
    _kill(cdp_backend.find_pids("yaoguai_bot.py"))     # 没有守护（单独运行的 bot）或 10 秒还没退
    closed = False
    v = cdp_backend.alive() if close_browser else None
    if v:
        try:
            ws = websocket.create_connection(v["webSocketDebuggerUrl"], suppress_origin=True, timeout=10)
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            ws.close()
            for _ in range(20):
                if not cdp_backend.alive():
                    closed = True
                    break
                time.sleep(0.5)
        except Exception:
            pass
    return was, closed


STOP_FLAG = os.path.join(cdp_backend.HERE, "STOP_AFTER_LEVEL")


def stop_after_level(on=True):
    """打完这关再退出：写一个标记文件，bot 在关卡间隙看到就关浏览器、正常退出（守护不重启）。on=False 取消"""
    if on:
        with open(STOP_FLAG, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
    else:
        try:
            os.remove(STOP_FLAG)
        except OSError:
            pass


def stop_pending():
    return os.path.exists(STOP_FLAG)


if __name__ == "__main__":
    import sys
    if "--after-level" in sys.argv:
        stop_after_level(True)
        print("已设置：打完当前这关再退出（取消：./venv/bin/python stop_bot.py --cancel）")
    elif "--cancel" in sys.argv:
        stop_after_level(False)
        print("已取消「打完这关再退出」")
    else:
        was, closed = stop_all()
        print(("已停止脚本" if was else "脚本本来就没在运行") + ("，已关闭后台浏览器" if closed else ""))
