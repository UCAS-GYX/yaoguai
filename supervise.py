# 守护进程：yaoguai_bot.py 被意外结束（非 0 退出码 / 被信号杀掉）就自动重启（带 --resume）。
# 正常退出（退出码 0：手动 Ctrl+C、检测到需要人工处理的提示、跑满场数）不重启。
# 停止全部：./venv/bin/python stop_bot.py   （给守护发 SIGTERM，它会先结束 bot 再退出，不会重启；并关闭后台浏览器）
import os, signal, subprocess, sys, time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_RESTARTS = 5             # 30 分钟内最多重启 5 次，超过就停（避免死循环）
WINDOW = 30 * 60
child = [None]
stopping = [False]


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] [守护] {msg}", flush=True)


def on_stop(sig, frame):
    stopping[0] = True
    log(f"收到 {signal.Signals(sig).name}，结束 bot 后退出（不重启）")
    if child[0] and child[0].poll() is None:
        child[0].terminate()


signal.signal(signal.SIGTERM, on_stop)
signal.signal(signal.SIGINT, on_stop)
signal.signal(signal.SIGHUP, on_stop)

args = sys.argv[1:]
restarts = []
while True:
    child[0] = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "yaoguai_bot.py")] + args, cwd=HERE)
    log(f"bot 已启动 pid {child[0].pid}")
    code = child[0].wait()
    if stopping[0]:
        break
    if code == 0:
        log("bot 正常退出（退出码 0），不重启")
        break
    how = f"被信号 {signal.Signals(-code).name} 结束" if code < 0 else f"退出码 {code}"
    now = time.time()
    restarts = [t for t in restarts if now - t < WINDOW] + [now]
    if len(restarts) > MAX_RESTARTS:
        log(f"bot 意外退出（{how}），30 分钟内已重启 {MAX_RESTARTS} 次，不再重启")
        with open(os.path.join(HERE, "ALERT"), "w", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} bot 反复意外退出（{how}），守护已停止，请看 run.log\n")
        break
    log(f"bot 意外退出（{how}），10 秒后自动重启（--resume）")
    time.sleep(10)
    if "--resume" not in args:
        args = args + ["--resume"]
log("守护退出")
