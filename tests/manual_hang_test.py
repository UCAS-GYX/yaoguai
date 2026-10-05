"""手动实测：游戏页卡死（渲染进程死循环、协议不回）时，Browser 能否自动换一个新游戏页。
会打开后台浏览器、故意让游戏页卡死，所以 bot 运行时不要跑。用法：./venv/bin/python tests/manual_hang_test.py"""
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cdp_backend  # noqa: E402


def page_ids():
    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{cdp_backend.PORT}/json", timeout=3))
    return [t["id"] for t in tabs if t.get("type") == "page"]


assert not cdp_backend.find_pids("yaoguai_bot.py"), "bot 在运行，先 stop_bot.py"
assert cdp_backend.launch(), "后台浏览器起不来"
b = cdp_backend.Browser()
print("游戏页:", b.url()[:60])
old = page_ids()
b.js("setTimeout(() => { for (;;) {} }, 100); 1")       # 100 ms 后主线程死循环（模拟 13:09 的卡死）
time.sleep(1)
t0 = time.time()
for i in range(3):
    try:
        print(f"第 {i + 1} 次请求：成功 {b.url()[:50]}（{time.time() - t0:.0f} 秒）")
        break
    except Exception as e:
        print(f"第 {i + 1} 次请求：失败 {type(e).__name__}（{time.time() - t0:.0f} 秒）")
time.sleep(3)
new = page_ids()
replaced = bool(new) and not set(new) & set(old)
print("旧页已关闭、换成新页:", replaced)
ok = False
for _ in range(10):
    try:
        ok = "game.taobao.com" in b.url()
        break
    except Exception:
        time.sleep(2)
print("新页能正常响应:", ok)
print("结论:", "通过" if replaced and ok else "未通过")
sys.exit(0 if replaced and ok else 1)
