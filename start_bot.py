# 后台启动脚本（脱离终端，关掉终端也不会停）。用法: ./venv/bin/python start_bot.py [--resume]
# 会顺带：启动后台无窗口 Chrome（如果没在跑）、启动监控页 http://127.0.0.1:8765（如果没在跑）。
import socket, subprocess, sys, os
import cdp_backend

here = os.path.dirname(os.path.abspath(__file__))


def detached(script, logname, args=()):
    log = open(os.path.join(here, logname), "a")
    return subprocess.Popen([sys.executable, "-u", os.path.join(here, script)] + list(args),
                            cwd=here, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)


if cdp_backend.find_pids("yaoguai_bot.py") or cdp_backend.find_pids("supervise.py"):
    sys.exit("bot 已经在运行，不重复启动（停止：./venv/bin/python stop_bot.py）")
if not cdp_backend.launch():
    sys.exit("后台 Chrome 启动失败")
with socket.socket() as s:
    monitor_up = s.connect_ex(("127.0.0.1", 8765)) == 0
lan = "--lan" in sys.argv                                # --lan：监控页允许同一 WiFi 的其他设备访问（需访问码）
if not monitor_up:
    print("monitor pid", detached("monitor.py", "monitor.log", ["--lan"] if lan else []).pid)
p = detached("supervise.py", "run.log", [a for a in sys.argv[1:] if a != "--lan"])   # 守护进程：bot 意外退出会自动重启
print("started supervisor pid", p.pid)
print("监控页：http://127.0.0.1:8765")
print("停止：./venv/bin/python stop_bot.py")
