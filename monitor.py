# 实时监控页：http://127.0.0.1:8765  （看画面和日志 + 「继续执行」按钮；不能点游戏）
# 用法: ./venv/bin/python monitor.py [--lan]      （start_bot.py 会自动在后台启动它）
#   --lan：同一 WiFi 下的其他设备也能看（需要访问码，见 monitor_key.txt）；「继续执行」仍然只有本机能点
import collections, hmac, io, json, os, secrets, socket, subprocess, sys, threading, time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from PIL import Image
import cdp_backend
import stop_bot

HERE = cdp_backend.HERE
PORT = 8765
LAN = "--lan" in sys.argv
KEY_FILE = os.path.join(HERE, "monitor_key.txt")
# 截图不自动启动 Chrome；只有「继续执行」按钮会启动（走 start_bot.py）
BR = cdp_backend.Browser(auto_launch=False)
# 每次启动监控随机生成，只写在监控页里。别的网站拿不到它，没法跨站替你点「继续执行」
TOKEN = secrets.token_urlsafe(16)
ALLOWED_HOSTS = (f"127.0.0.1:{PORT}", f"localhost:{PORT}")   # 防 DNS 重绑定：只认这两个 Host
START_LOCK = threading.Lock()


def load_key():
    """局域网访问码：第一次生成后存在 monitor_key.txt（仅本用户可读），重启监控不变，别人收藏的链接一直能用"""
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            k = f.read().strip()
        if len(k) >= 8:
            return k
    except OSError:
        pass
    k = secrets.token_urlsafe(8)
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(k + "\n")
    os.chmod(KEY_FILE, 0o600)
    return k


KEY = load_key() if LAN else ""


def lan_ip():
    """本机在局域网里的地址（UDP connect 不发包，只用来让系统选出出口网卡的地址）"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def bot_running():
    return bool(cdp_backend.find_pids("yaoguai_bot.py") or cdp_backend.find_pids("supervise.py"))


def resume():
    """继续执行 = start_bot.py --resume（顺带拉起后台 Chrome）。返回 (HTTP 状态码, 提示文字)"""
    if not START_LOCK.acquire(blocking=False):
        return 409, "正在启动中，请稍等"
    try:
        if bot_running():
            return 409, "脚本已经在运行"
        r = subprocess.run([sys.executable, os.path.join(HERE, "start_bot.py"), "--resume"], cwd=HERE,
                           capture_output=True, text=True, timeout=90)
        out = (r.stdout + r.stderr).strip()
        return (200 if r.returncode == 0 else 500), out or f"退出码 {r.returncode}"
    except subprocess.TimeoutExpired:
        return 500, "启动超时（90 秒），请看 run.log"
    finally:
        START_LOCK.release()


def stop():
    """退出 = 结束守护进程和 bot（守护会先结束 bot，不会自动重启），再关闭后台浏览器（游戏也停下，不再占 CPU）。
    之后点「继续执行」会重新拉起后台浏览器和 bot。"""
    if not START_LOCK.acquire(blocking=False):
        return 409, "正在启动/退出中，请稍等"
    try:
        stop_bot.stop_after_level(False)               # 立即退出时顺便清掉「打完这关再退出」
        was, closed = stop_bot.stop_all()              # 只按进程精确结束，不会误杀别的程序
        BR.ws = None
        return 200, ("已停止脚本" if was else "脚本本来就没在运行") + ("，已关闭后台浏览器" if closed else "")
    finally:
        START_LOCK.release()


def load():
    """负载：按进程分组汇总 CPU（ps 的 %CPU，100% = 1 个核）和内存，再加系统负载"""
    out = subprocess.run(["ps", "-axo", "pcpu=,rss=,command="], capture_output=True, text=True).stdout
    groups = {"后台浏览器": ("chrome_cft_profile", "chrome_bot_profile", "remote-debugging-port=9333"),
              "bot 脚本": ("yaoguai_bot.py",), "守护进程": ("supervise.py",), "监控页": ("monitor.py",)}
    res = {k: [0.0, 0.0, 0] for k in groups}
    for line in out.splitlines():
        p = line.split(None, 2)
        if len(p) < 3:
            continue
        for k, keys in groups.items():
            if any(s in p[2] for s in keys):
                r = res[k]; r[0] += float(p[0]); r[1] += int(p[1]) / 1024; r[2] += 1
                break
    la = os.getloadavg()
    return {"groups": [{"name": k, "cpu": round(v[0], 1), "mem": round(v[1]), "n": v[2]} for k, v in res.items()],
            "total_cpu": round(sum(v[0] for v in res.values()), 1), "cores": os.cpu_count(),
            "loadavg": [round(x, 2) for x in la]}


def tail(name, n, maxbytes=32768):
    p = os.path.join(HERE, name)
    try:
        with open(p, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - maxbytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
        return lines[-n:], time.time() - os.path.getmtime(p)
    except OSError:
        return [], None


_ST = {"t": 0.0, "v": None}
_ST_LOCK = threading.Lock()


def status():
    """1 秒缓存：多人同时开监控页时，ps / 读日志 / 问浏览器每秒最多做一次"""
    with _ST_LOCK:
        if _ST["v"] is None or time.time() - _ST["t"] >= 1.0:
            _ST["v"] = _status()
            _ST["t"] = time.time()
        return dict(_ST["v"], starting=START_LOCK.locked(), stop_pending=stop_bot.stop_pending())


def _status():
    pids = [str(p) for p in cdp_backend.find_pids("yaoguai_bot.py")]
    run, age = tail("run.log", 30)
    levels, _ = tail("levels.log", 8)
    events, _ = tail("events.log", 6)
    level = None
    for line in reversed(tail("run.log", 400, 131072)[0]):
        if "进入战斗：第 " in line:
            level = line.split("进入战斗：第 ")[1].split(" ")[0]; break
    alert = ""
    try:
        with open(os.path.join(HERE, "ALERT"), encoding="utf-8") as f:
            alert = f.read()[:600]
    except OSError:
        pass
    try:
        with open(os.path.join(HERE, "state.json"), encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    try:
        url = BR.url()
    except Exception as e:
        url = f"连接不上后台 Chrome：{e}"
    return {"running": bool(pids), "pids": pids, "starting": START_LOCK.locked(), "load": load(),
            "level": level, "last_win": st.get("last_win"),
            "fails": st.get("fails", {}), "log_age": None if age is None else round(age), "alert": alert,
            "chrome": cdp_backend.alive() is not None, "url": url[:120],
            "run": run, "levels": levels,
            "events": ["  ".join(e.split("\t")[:5]) for e in events]}


def frame(ds=1):
    """只在前端请求时截一张：整个视口截下来，本地裁出游戏画布，无损 PNG（不二次压缩）。
    ds=1 原图 / 2 降采样 2 倍 / 4 降采样 4 倍（局域网看、手机看时更省流量）"""
    img = BR.shot(clip=BR.game_rect())
    if ds > 1:
        img = img.resize((max(1, img.width // ds), max(1, img.height // ds)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False, compress_level=1)   # 压缩级别低 => 编码快
    return buf.getvalue()


def lan_url():
    """带访问码的局域网链接，每次实时取 IP（换了 WiFi / DHCP 换了地址也对）"""
    ip = lan_ip() if LAN else None
    return f"http://{ip}:{PORT}/?key={KEY}" if ip else ""


# ---------- 局域网来访记录：帮你判断手机的请求到底有没有到这台电脑（不记录访问码本身）----------
VISITS = collections.deque(maxlen=8)
_VLAST = {}


def note_visit(ip, what, result, ua="", always=False):
    """记一行到 monitor.log + 最近来访列表。同一 IP 同一结果 30 秒内只记一次（always=True 时打开首页总会记）"""
    now = time.time()
    k = (ip, what, result)
    if not always and now - _VLAST.get(k, 0) < 30:
        return
    _VLAST[k] = now
    t = time.strftime("%H:%M:%S")
    VISITS.appendleft({"t": t, "ip": ip, "what": what, "result": result})
    print(f"[{t}] 局域网来访 {ip} {what} → {result}" + (f"  UA={ua[:90]}" if ua else ""), flush=True)


# ---------- 二维码：纯 Python 实现（不加依赖），字节模式 + 纠错等级 M，版本 1–10（最多约 200 字节）----------
_QR_ECC = {1: (10, 1), 2: (16, 1), 3: (26, 1), 4: (18, 2), 5: (24, 2),       # 版本: (每块纠错码字数, 块数)
           6: (16, 4), 7: (18, 4), 8: (22, 4), 9: (22, 5), 10: (26, 5)}
_QR_MASKS = (lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0)


def _gf_mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_ecc(data, degree):
    """Reed-Solomon 纠错码字"""
    div, root = [0] * (degree - 1) + [1], 1
    for _ in range(degree):
        for j in range(degree):
            div[j] = _gf_mul(div[j], root)
            if j + 1 < degree:
                div[j] ^= div[j + 1]
        root = _gf_mul(root, 2)
    res = [0] * degree
    for b in data:
        f = b ^ res.pop(0)
        res.append(0)
        for i, c in enumerate(div):
            res[i] ^= _gf_mul(c, f)
    return res


def _qr_raw_modules(ver):
    r = (16 * ver + 128) * ver + 64
    if ver >= 2:
        na = ver // 7 + 2
        r -= (25 * na - 10) * na - 55
        if ver >= 7:
            r -= 36
    return r


def _qr_format(put, size, mask):
    """格式信息（纠错 M = 00）两份 + 固定黑点"""
    rem = mask
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = (mask << 10 | rem) ^ 0x5412
    b = [(bits >> i) & 1 for i in range(15)]
    for i in range(6):
        put(8, i, b[i])
    put(8, 7, b[6]); put(8, 8, b[7]); put(7, 8, b[8])
    for i in range(9, 15):
        put(14 - i, 8, b[i])
    for i in range(8):
        put(size - 1 - i, 8, b[i])
    for i in range(8, 15):
        put(8, size - 15 + i, b[i])
    put(8, size - 8, 1)


def _qr_penalty(m):
    """简化版掩码评分（连续同色 + 2×2 同色块 + 黑白比例），只用来挑一个好扫的掩码"""
    n, p = len(m), 0
    for lines in (m, list(zip(*m))):
        for row in lines:
            run = 1
            for a, b in zip(row, row[1:]):
                if a == b:
                    run += 1
                else:
                    p += run - 2 if run >= 5 else 0
                    run = 1
            p += run - 2 if run >= 5 else 0
    p += 3 * sum(1 for y in range(n - 1) for x in range(n - 1)
                 if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1])
    dark = sum(map(sum, m))
    return p + 10 * (abs(dark * 20 - n * n * 10) // (n * n))


def qr_matrix(text):
    """返回二维码黑白矩阵 m[行][列]（1 = 黑）"""
    data = text.encode("utf-8")
    for ver in _QR_ECC:
        ecc_len, nblk = _QR_ECC[ver]
        cap = _qr_raw_modules(ver) // 8 - ecc_len * nblk
        if 4 + (8 if ver < 10 else 16) + 8 * len(data) <= cap * 8:
            break
    else:
        raise ValueError("二维码内容太长")
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in reversed(range(8 if ver < 10 else 16))]
    for c in data:
        bits += [(c >> i) & 1 for i in reversed(range(8))]
    bits += [0] * min(4, cap * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(cw) < cap:
        cw.append(pad); pad ^= 0xEC ^ 0x11
    # 分块 + 纠错 + 交织
    raw = _qr_raw_modules(ver) // 8
    nshort, short_len = nblk - raw % nblk, raw // nblk
    blocks, k = [], 0
    for i in range(nblk):
        d = cw[k:k + short_len - ecc_len + (0 if i < nshort else 1)]
        k += len(d)
        e = _rs_ecc(d, ecc_len)
        if i < nshort:
            d = d + [0]
        blocks.append(d + e)
    final = [b[i] for i in range(len(blocks[0])) for j, b in enumerate(blocks)
             if i != short_len - ecc_len or j >= nshort]
    # 功能图形
    size = ver * 4 + 17
    m = [[0] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def put(x, y, v):
        m[y][x] = 1 if v else 0; fn[y][x] = True

    for i in range(size):
        put(6, i, i % 2 == 0); put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if 0 <= cx + dx < size and 0 <= cy + dy < size:
                    put(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    if ver > 1:
        na = ver // 7 + 2
        step = (ver * 8 + na * 3 + 5) // (na * 4 - 4) * 2
        pos = [6] + sorted(size - 7 - i * step for i in range(na - 1))
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, na - 1), (na - 1, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        put(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
    _qr_format(put, size, 0)                           # 先占位，选好掩码后重画
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        vb = ver << 12 | rem
        for i in range(18):
            a, b = size - 11 + i % 3, i // 3
            put(a, b, (vb >> i) & 1); put(b, a, (vb >> i) & 1)
    # 数据（之字形）
    i, right = 0, size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if (right + 1) & 2 == 0 else vert
                if not fn[y][x] and i < len(final) * 8:
                    m[y][x] = (final[i >> 3] >> (7 - (i & 7))) & 1
                    i += 1
        right -= 2
    best = None
    for mi, f in enumerate(_QR_MASKS):
        t = [[m[y][x] ^ (0 if fn[y][x] else int(f(x, y))) for x in range(size)] for y in range(size)]

        def put2(x, y, v, t=t):
            t[y][x] = 1 if v else 0

        _qr_format(put2, size, mi)
        p = _qr_penalty(t)
        if best is None or p < best[0]:
            best = (p, t)
    return best[1]


def qr_svg(text, scale=4, border=4):
    m = qr_matrix(text)
    n = len(m) + 2 * border
    d = "".join(f"M{x + border},{y + border}h1v1h-1z" for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{n * scale}" height="{n * scale}" '
            f'shape-rendering="crispEdges" role="img" aria-label="局域网查看链接的二维码">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{d}" fill="#000"/></svg>')


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>妖怪金手指 监控</title>
<style>
html{-webkit-text-size-adjust:100%}
body{margin:0;font:14px/1.5 -apple-system,"PingFang SC",sans-serif;background:#16181d;color:#e6e6e6}
main{display:flex;gap:20px;padding:16px;align-items:flex-start}
main>*{min-width:0}
#game{max-width:45vw;height:auto;border-radius:8px;background:#000;display:block}   /* 按图片原尺寸显示，不拉伸、不放大 */
section{flex:1;min-width:0}
#lanbox{background:#1d2a3a;border-radius:6px;padding:8px 12px;margin:0 0 12px;display:none}
#lanbox a{color:#8cc8ff;word-break:break-all}
#lanbox .row{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start}
#qr{background:#fff;border-radius:4px;display:block;width:148px;height:148px}
#visits{margin:4px 0 0;padding-left:18px;font-size:12px;color:#c9d1d9}
details summary{cursor:pointer;color:#9fb3c8}
.tw{max-width:100%;overflow-x:auto}
h1{font-size:18px;margin:0 0 10px}h2{font-size:15px;margin:16px 0 6px;color:#9fb3c8}
.cards{display:flex;flex-wrap:wrap;gap:8px}
.card{background:#232730;border-radius:6px;padding:8px 12px;min-width:110px}
.card b{display:block;font-size:12px;color:#9aa4b2;font-weight:normal}
.ok{color:#6ee7a0}.bad{color:#ff7b7b}
#alert{background:#5a1f1f;border-radius:6px;padding:8px 12px;white-space:pre-wrap;display:none}
pre{background:#0f1115;border-radius:6px;padding:8px;margin:0;font:12px/1.45 Menlo,monospace;white-space:pre-wrap;word-break:break-all;max-height:42vh;overflow:auto}
small{color:#8a93a0}
.ctl{display:flex;align-items:center;gap:12px;margin:0 0 12px}
button{font:inherit;font-size:15px;padding:8px 20px;border:0;border-radius:6px;background:#2f9e5b;color:#fff;cursor:pointer}
button:disabled{background:#3a3f4a;color:#9aa4b2;cursor:default}
button:focus-visible{outline:3px solid #8cc8ff;outline-offset:2px}
#msg{white-space:pre-wrap;color:#c9d1d9}
#game[hidden]{display:none}
button.danger{background:#b4423f}
.load{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
table{border-collapse:collapse;font-size:13px}
th,td{padding:4px 12px;text-align:right;border-bottom:1px solid #2c313a}
th:first-child,td:first-child{text-align:left}
th{color:#9aa4b2;font-weight:normal}
#spark{background:#0f1115;border-radius:6px;display:block}
button.sec{background:#3b6fb6;padding:6px 14px;font-size:14px}
select{font:inherit;padding:5px 8px;border-radius:6px;background:#232730;color:#e6e6e6;border:1px solid #3a3f4a}
#viewer .ctl{flex-wrap:wrap}
@media (max-width:700px){                       /* 手机：上下单列，状态和按钮在上，画面在下，日志最后 */
 main{flex-direction:column;align-items:stretch;gap:12px;padding:10px}
 section{display:contents}
 #top{order:1}#viewer{order:2}#more{order:3}
 #game{max-width:100%}                          /* 最多占满屏宽，小图不放大（不糊），等比缩放不变形 */
 h1{font-size:17px}
 .ctl{flex-wrap:wrap;gap:8px}
 button,select{min-height:44px;font-size:16px}  /* 触控目标 ≥ 44px；16px 防 iOS 自动放大 */
 button.sec{padding:8px 14px;font-size:16px}
 #top .ctl button{flex:1 1 auto}
 .card{flex:1 1 40%;min-width:0}
 pre{max-height:50vh;overflow:auto;-webkit-overflow-scrolling:touch}
 #spark{width:100%;max-width:300px;height:auto}
 .load>*{max-width:100%}
}
</style></head><body><main>
<div id="viewer">
 <div class="ctl">
  <label for="mode">游戏画面</label>
  <select id="mode" autocomplete="off">
   <option value="off" selected>不同步（默认）</option>
   <option value="0.5">每 0.5 秒自动截图</option>
   <option value="1">每 1 秒自动截图</option>
   <option value="2">每 2 秒自动截图</option>
   <option value="5">每 5 秒自动截图</option>
   <option value="15">每 15 秒自动截图</option>
  </select>
  <label for="ds">分辨率</label>
  <select id="ds" autocomplete="off">
   <option value="1" selected>原图</option>
   <option value="2">降采样 2 倍</option>
   <option value="4">降采样 4 倍</option>
  </select>
  <button id="snap" type="button" class="sec">截一张</button>
 </div>
 <img id="game" alt="游戏画面" hidden>
 <small id="ft">画面未同步：选择自动截图，或点「截一张」</small>
</div>
<section>
<div id="top">
<h1>妖怪金手指 · 后台运行监控</h1>
<div id="alert" role="alert"></div>
<div id="lanbox">
 <b>局域网查看链接</b>（同一 WiFi 的手机用；只在本机显示）
 <div class="row">
  <div style="flex:1 1 260px;min-width:0">
   <a id="lanurl" href="#"></a>
   <div class="ctl" style="margin:6px 0"><button id="copy" type="button" class="sec">复制链接</button><span id="copymsg" role="status"></span></div>
   <small>手机用相机扫右边二维码最省事（会用 Safari 打开，不经过微信/飞书内置浏览器）。</small>
  </div>
  <img id="qr" alt="局域网查看链接的二维码" hidden>
 </div>
 <details><summary>手机打不开？最近局域网来访记录</summary>
  <ul id="visits"></ul>
  <small>手机打开后这里没有新记录 = 请求根本没到这台电脑：检查手机是否连同一个 WiFi（不是访客网络）、路由器是否开了「AP 隔离」、手机是否关了蜂窝数据/VPN/iCloud 私人中继、电脑的公司 VPN 是否禁止局域网访问；地址要用 http:// 开头，IP 要和上面一致。</small>
 </details>
</div>
<div class="ctl"><button id="resume" type="button" disabled>继续执行</button><button id="after" type="button" class="sec" disabled>打完这关再退出</button><button id="stop" type="button" class="danger" disabled>立即退出</button><span id="msg" role="status"></span></div>
<div class="cards" aria-live="polite">
 <div class="card"><b>脚本</b><span id="run">-</span></div>
 <div class="card"><b>当前关卡</b><span id="level">-</span></div>
 <div class="card"><b>最近通关</b><span id="win">-</span></div>
 <div class="card"><b>日志更新</b><span id="age">-</span></div>
 <div class="card"><b>后台 Chrome</b><span id="chrome">-</span></div>
 <div class="card"><b>局域网地址</b><span id="lanaddr">-</span></div>
</div>
</div>
<div id="more">
<h2>后台负载 <small id="lavg"></small></h2>
<div class="load">
 <div class="tw"><table><thead><tr><th scope="col">程序</th><th scope="col">CPU</th><th scope="col">内存</th><th scope="col">进程数</th></tr></thead><tbody id="loadt"></tbody></table></div>
 <div><canvas id="spark" width="300" height="70" role="img" aria-label="后台总 CPU 最近 5 分钟走势"></canvas><small id="sparkt"></small></div>
</div>
<h2>每关记录（levels.log）</h2><pre id="levels"></pre>
<h2>运行日志（run.log 最近 30 行）</h2><pre id="log"></pre>
<h2>事件（events.log）</h2><pre id="events"></pre>
</div>
</section></main>
<script>
const $ = id => document.getElementById(id);
const TOKEN = "__TOKEN__";
let busy = false;
// 手机（窄屏或触屏 UA）默认「降采样 2 倍」，省流量也更快；截图仍默认不同步
if (matchMedia("(max-width: 700px)").matches || /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)) $("ds").value = "2";
let qrFor = "";
function showLan(s) {
  $("lanaddr").textContent = !s.lan ? "未开 --lan（手机看不了）" : s.lan_addr || "取不到（没连 WiFi？）";
  $("lanaddr").className = s.lan && s.lan_addr ? "ok" : "bad";
  if (!s.local) return;                                    // 远程访问的人本来就有链接
  $("lanbox").style.display = "block";
  const a = $("lanurl"), u = s.lan_url || "";
  a.textContent = u || (s.lan ? "取不到局域网 IP（这台电脑没连 WiFi？）" : "监控页没用 --lan 启动，手机看不了");
  a.href = u || "#"; $("copy").disabled = !u;
  if (u && u !== qrFor) { qrFor = u; $("qr").src = "/qr.svg?t=" + Date.now(); $("qr").hidden = false; }  // IP 变了就重画
  if (!u) { qrFor = ""; $("qr").hidden = true; }
  const ul = $("visits"); ul.innerHTML = "";
  for (const v of s.visits || []) { const li = document.createElement("li"); li.textContent = v.t + "  " + v.ip + "  " + v.what + " → " + v.result; ul.appendChild(li); }
  if (!(s.visits || []).length) { const li = document.createElement("li"); li.textContent = "（监控页启动以来还没有局域网设备来访）"; ul.appendChild(li); }
}
$("copy").onclick = async () => {
  const u = $("lanurl").href;
  try { await navigator.clipboard.writeText(u); $("copymsg").textContent = "已复制"; }
  catch (e) {
    const r = document.createRange(); r.selectNodeContents($("lanurl"));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
    $("copymsg").textContent = document.execCommand("copy") ? "已复制" : "已选中，按 ⌘C 复制";
  }
  setTimeout(() => { $("copymsg").textContent = ""; }, 3000);
};
function setText(id, text, cls) { const e = $(id); e.textContent = text; e.className = cls || ""; }
function setButton(s) {
  if (busy) return;
  const b = $("resume"), x = $("stop");
  b.disabled = !s || !s.local || s.running || s.starting;
  b.textContent = !s ? "继续执行" : s.running ? "运行中" : s.starting ? "处理中…" : !s.local ? "继续执行（仅本机可点）" : "继续执行";
  x.disabled = !s || !s.local || s.starting || (!s.running && !s.chrome);
  x.textContent = s && !s.local ? "立即退出（仅本机可点）" : "立即退出";
  const a = $("after");
  a.disabled = !s || !s.local || s.starting || !s.running;
  a.textContent = s && s.stop_pending ? "取消「打完这关再退出」" : "打完这关再退出";
}
async function act(path, hint) {
  busy = true; $("resume").disabled = $("stop").disabled = $("after").disabled = true; $("msg").textContent = hint;
  try {
    const r = await fetch(path, {method: "POST", headers: {"X-Token": TOKEN}});
    $("msg").textContent = (await r.json()).msg;
  } catch (e) { $("msg").textContent = "请求失败：" + e; }
  busy = false; refreshStatus();
}
$("resume").onclick = () => act("/api/resume", "正在启动（会先拉起后台浏览器，最多约 30 秒）…");
$("stop").onclick = () => {
  if (confirm("确定立即退出？会停止脚本并关闭后台浏览器，当前这一关会中断（记为 crash，不算失败）。之后点「继续执行」会重新开始。"))
    act("/api/stop", "正在退出…");
};
$("after").onclick = () => act("/api/stop_after", "处理中…");
const hist = [];
function drawLoad(L) {
  $("lavg").textContent = "系统负载 " + L.loadavg.join(" / ") + "（" + L.cores + " 核）";
  $("loadt").innerHTML = "";
  for (const g of L.groups) {
    const tr = document.createElement("tr");
    for (const v of [g.name, g.cpu + "%", g.mem + " MB", g.n]) { const td = document.createElement("td"); td.textContent = v; tr.appendChild(td); }
    $("loadt").appendChild(tr);
  }
  hist.push(L.total_cpu); if (hist.length > 150) hist.shift();
  const c = $("spark"), x = c.getContext("2d"), W = c.width, H = c.height, max = Math.max(100, ...hist);
  x.clearRect(0, 0, W, H); x.strokeStyle = "#2c313a"; x.beginPath(); x.moveTo(0, H - 100 / max * H); x.lineTo(W, H - 100 / max * H); x.stroke();
  x.strokeStyle = "#6ee7a0"; x.lineWidth = 1.5; x.beginPath();
  hist.forEach((v, i) => { const px = W - (hist.length - 1 - i) * 2, py = H - v / max * (H - 4) - 2; i ? x.lineTo(px, py) : x.moveTo(px, py); });
  x.stroke();
  $("sparkt").textContent = "后台总 CPU " + L.total_cpu + "%（100% = 1 个核，灰线 = 100%），最高 " + Math.max(...hist) + "%";
}
async function refreshStatus() {
  try {
    const s = await (await fetch("/status.json", {cache: "no-store"})).json();
    setButton(s); drawLoad(s.load); showLan(s);
    setText("run", s.running ? "运行中 pid " + s.pids.join(",") : "已停止", s.running ? "ok" : "bad");
    setText("level", s.level ? "第 " + s.level + " 关" : "-");
    setText("win", s.last_win ? "第 " + s.last_win + " 关" : "-");
    setText("age", s.log_age == null ? "-" : s.log_age + " 秒前", s.log_age > 120 ? "bad" : "ok");
    setText("chrome", s.chrome ? (s.url.includes("game.taobao.com") ? "游戏页" : "非游戏页") : "未运行",
            s.chrome && s.url.includes("game.taobao.com") ? "ok" : "bad");
    $("alert").style.display = s.alert ? "block" : "none"; $("alert").textContent = s.alert ? "需要处理：\\n" + s.alert : "";
    const log = $("log"), atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
    log.textContent = s.run.join("\\n"); if (atBottom) log.scrollTop = log.scrollHeight;
    $("levels").textContent = s.levels.join("\\n"); $("events").textContent = s.events.join("\\n");
  } catch (e) { setText("run", "监控服务连接失败", "bad"); setButton(null); }
}
// 画面只在你选择时才截：默认不同步；「截一张」= 截一次；自动模式按间隔截，页面切到后台时跳过
let timer = null, loading = false;
async function snap(auto) {
  if (loading || (auto && document.hidden)) return;
  loading = true;
  try {
    const t0 = performance.now();
    const r = await fetch("/frame.png?ds=" + $("ds").value, {cache: "no-store"});
    if (!r.ok) throw new Error(await r.text());
    const blob = await r.blob(), u = URL.createObjectURL(blob), g = $("game");
    if (g.dataset.u) URL.revokeObjectURL(g.dataset.u);
    g.dataset.u = u; g.src = u; g.hidden = false;
    const t = new Date().toLocaleTimeString();
    g.onload = () => { $("ft").textContent = "截于 " + t + " · " + g.naturalWidth + "×" + g.naturalHeight +
      " · " + Math.round(blob.size / 1024) + " KB · " + Math.round(performance.now() - t0) + " ms"; };
    g.alt = "游戏画面，截于 " + t;
  } catch (e) { $("ft").textContent = "截图失败（后台 Chrome 未运行？）"; }
  loading = false;
}
$("mode").onchange = () => {
  clearInterval(timer); timer = null;
  const v = $("mode").value;
  if (v === "off") { $("ft").textContent = $("game").hidden ? "画面未同步" : "已停止同步，显示的是最后一张"; return; }
  snap(false); timer = setInterval(() => snap(true), +v * 1000);
};
$("snap").onclick = () => snap(false);
$("ds").onchange = () => { if (!$("game").hidden) snap(false); };
refreshStatus(); setInterval(refreshStatus, 2000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, msg):
        self._send(code, "application/json; charset=utf-8", json.dumps({"msg": msg}, ensure_ascii=False).encode())

    def _local(self):
        """本机访问：来源地址是 127.0.0.1 且 Host 是 127.0.0.1/localhost（防 DNS 重绑定）"""
        return self.client_address[0] in ("127.0.0.1", "::1") and self.headers.get("Host") in ALLOWED_HOSTS

    def _key_state(self, qs):
        """局域网访问：网址里的 ?key=访问码，或之前访问时留下的 cookie。
        返回 (是否通过, 说明)；说明只描述来源和对错，不含访问码本身"""
        if not LAN:
            return False, "未开 --lan"
        k, src = (qs.get("key") or [""])[0], "链接带访问码"
        if not k:
            try:
                c = SimpleCookie(self.headers.get("Cookie", ""))
                k = c["ygkey"].value if "ygkey" in c else ""
            except CookieError:
                k = ""
            src = "cookie"
        if not k:
            return False, "没带访问码"
        if hmac.compare_digest(k.encode(), KEY.encode()):
            return True, src
        return False, ("访问码不对（链接不完整？）" if src != "cookie" else "cookie 里的访问码过期")

    def do_POST(self):
        if self.path not in ("/api/resume", "/api/stop", "/api/stop_after"):
            return self._send(404, "text/plain", b"not found")
        origin = self.headers.get("Origin")
        if (not self._local() or self.headers.get("X-Token") != TOKEN
                or (origin and origin.split("//")[-1] not in ALLOWED_HOSTS)):
            return self._json(403, "拒绝：「继续执行」「退出」只能在运行脚本的这台电脑上点（页面是旧的就刷新一下）")
        if self.path == "/api/stop_after":
            on = not stop_bot.stop_pending()
            stop_bot.stop_after_level(on)
            _ST["v"] = None
            return self._json(200, "已设置：当前这关打完后自动退出（关闭后台浏览器）" if on else "已取消「打完这关再退出」")
        self._json(*(resume() if self.path == "/api/resume" else stop()))

    def do_GET(self):
        u = urlsplit(self.path)
        qs = parse_qs(u.query)
        local = self._local()
        if not local:
            ok, why = self._key_state(qs)
            ip, ua = self.client_address[0], self.headers.get("User-Agent", "")
            if u.path == "/" or not ok:                # 打开首页总记一行；截图/状态只记被拒的
                note_visit(ip, u.path, ("放行（" + why + "）") if ok else ("403 " + why),
                           ua if u.path == "/" else "", always=(u.path == "/"))
            if not ok:
                return self._send(403, "text/plain; charset=utf-8",
                                  f"需要访问码（{why}）：请用电脑监控页上「局域网查看链接」给出的完整链接（带 ?key=...），"
                                  "或扫那里的二维码".encode())
        try:
            if u.path == "/qr.svg":                    # 二维码里有访问码，只给本机
                if not local or not lan_url():
                    return self._send(403, "text/plain; charset=utf-8", "只在本机提供".encode())
                self._send(200, "image/svg+xml; charset=utf-8", qr_svg(lan_url()).encode())
            elif u.path == "/":
                body = PAGE.replace("__TOKEN__", TOKEN if local else "").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                if not local:                              # 记住访问码，之后的截图/状态请求不用每次带 key
                    self.send_header("Set-Cookie", f"ygkey={KEY}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict")
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/status.json":
                ip = lan_ip() if LAN else None
                s = dict(status(), local=local, lan=LAN, lan_addr=f"{ip}:{PORT}" if ip else "")
                if local:                              # 带访问码的链接和来访记录只给本机看
                    s.update(lan_url=lan_url(), visits=list(VISITS))
                self._send(200, "application/json; charset=utf-8", json.dumps(s, ensure_ascii=False).encode())
            elif u.path in ("/frame.png", "/frame.jpg"):
                ds = (qs.get("ds") or ["1"])[0]
                self._send(200, "image/png", frame({"2": 2, "4": 4}.get(ds, 1)))
            else:
                self._send(404, "text/plain", b"not found")
        except Exception as e:
            self._send(503, "text/plain; charset=utf-8", str(e).encode())

    def log_message(self, *a):
        pass                                           # 不刷屏

    def log_error(self, fmt, *a):
        """手机浏览器强制 https 时，发来的是 TLS 握手，http.server 会报 Bad request —— 记一行提示"""
        raw = getattr(self, "raw_requestline", b"") or b""
        if raw[:1] == b"\x16":
            note_visit(self.client_address[0], "(https)", "连不上：浏览器用了 https://，请改成 http://")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return                                     # 手机切后台/断网时常见，不打整段 traceback
        super().handle_error(request, client_address)


if __name__ == "__main__":
    print(f"[{time.strftime('%F %T')}] 监控页（本机）：http://127.0.0.1:{PORT}", flush=True)
    if LAN:
        ip = lan_ip()
        print(f"监控页（同一 WiFi 的其他设备）：http://{ip or '本机局域网IP'}:{PORT}/?key={KEY}", flush=True)
    Server(("0.0.0.0" if LAN else "127.0.0.1", PORT), Handler).serve_forever()
