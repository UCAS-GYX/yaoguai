# 后台 Chrome（DevTools 协议）：截图 / 点击 / 刷新都在网页内部完成，不动真鼠标、不截整屏，游戏可以在无窗口的 Chrome 里跑。
# 专用配置目录 chrome_bot_profile（和日常用的 Chrome 分开），调试端口只监听 127.0.0.1。
import base64, io, json, os, signal, subprocess, threading, time, urllib.request
import websocket
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 9333
# 优先用独立的 Chrome for Testing（应用标识 com.google.chrome.for.testing）：
# 用系统的 Google Chrome 跑无窗口实例时，系统把它和你日常的 Chrome 当成同一个应用，
# 点程序坞图标/别的程序打开链接会被发到这个看不见的后台实例 => 日常 Chrome「打不开、卡住」。
CFT = os.path.join(HERE, "chrome-cft", "chrome-mac-arm64", "Google Chrome for Testing.app",
                   "Contents", "MacOS", "Google Chrome for Testing")
SYS_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USE_CFT = os.path.exists(CFT)
CHROME = CFT if USE_CFT else SYS_CHROME
PROFILE = os.path.join(HERE, "chrome_cft_profile" if USE_CFT else "chrome_bot_profile")
GAME_URL = "https://game.taobao.com/detail?appid=3000000136863671&hdkf_from=hdkf_pc1&horizontal=false"
# 无窗口模式默认 UA 带 HeadlessChrome，换成普通 Chrome 的 UA
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")
WINDOW = (1280, 900)
DPR = 1                      # 1 倍像素：游戏渲染量是 2 倍时的 1/4（降温）。OCR 前会放大 2 倍，像素判断都按比例/缩略图算


class CDPError(Exception):
    """协议返回的错误（参数不对等），不是连接问题，不重连"""


def alive():
    try:
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1))
    except Exception:
        return None


def launch(headless=True):
    """调试端口没起来就启动专用 Chrome（打开游戏页）。返回是否可用。"""
    if alive():
        return True
    # 不恢复上次的标签页：否则「恢复的游戏页 + 启动参数里的游戏页」两个同时加载，CPU 翻倍，还容易卡死
    sess = os.path.join(PROFILE, "Default", "Sessions")
    if os.path.isdir(sess):
        for f in os.listdir(sess):
            try:
                os.remove(os.path.join(sess, f))
            except OSError:
                pass
    args = [CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
            "--no-first-run", "--no-default-browser-check", f"--window-size={WINDOW[0]},{WINDOW[1]}",
            # 2 倍像素用启动参数固定（每个连接各自设 Emulation 覆盖会互相干扰，视口曾被改成 794x1602）
            f"--force-device-scale-factor={DPR}",
            "--mute-audio",                            # 游戏音效静音（无窗口也会出声）
            # 后台/被遮挡时不降速、不暂停渲染
            "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling"]
    if USE_CFT:
        args += ["--use-mock-keychain"]                # 不弹钥匙串授权框（无窗口时弹框会卡住启动）
    if headless:
        args += ["--headless", f"--user-agent={UA}"]
    subprocess.Popen(args + [GAME_URL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):
        if alive():
            return True
        time.sleep(0.5)
    return False


def find_pids(script):
    """精确找「用 Python 运行 script」的进程（只看程序名和参数，不做文字模糊匹配）。
    以前用 pkill -f yaoguai_bot.py，命令行里只要出现这几个字（比如一条查看日志的终端命令）也会被误杀。"""
    out = subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True).stdout
    me, pids = os.getpid(), []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].isdigit() or int(parts[0]) == me:
            continue
        if "ython" not in os.path.basename(parts[1]):
            continue
        if any(os.path.basename(a) == script for a in parts[2:5]):
            pids.append(int(parts[0]))
    return pids


def close_stray_pages():
    """关掉后台 Chrome 里不是淘宝的标签页（12:40 发现别的程序往这个 Chrome 里开了 80+ 个飞书登录页，占 14 GB 内存）。
    没有淘宝页时什么都不关（避免关光所有页面）。返回关掉的数量。"""
    v = alive()
    if not v:
        return 0
    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3))
    pages = [t for t in tabs if t.get("type") == "page"]
    stray = [t for t in pages if "taobao.com" not in t.get("url", "")]
    if len(stray) == len(pages):
        return 0
    # 重复的游戏页也关掉，只留一个（重启浏览器时会恢复上次的标签页，再加上启动参数里的地址 => 两个游戏页同时渲染）
    game = [t for t in pages if "taobao.com" in t.get("url", "")]
    stray += game[1:]
    if not stray:
        return 0
    ws = websocket.create_connection(v["webSocketDebuggerUrl"], suppress_origin=True, timeout=10)
    try:
        for i, t in enumerate(stray, 1):
            ws.send(json.dumps({"id": i, "method": "Target.closeTarget", "params": {"targetId": t["id"]}}))
            while json.loads(ws.recv()).get("id") != i:
                pass
    finally:
        ws.close()
    return len(stray)


def close_browser(wait=10):
    """关闭后台浏览器（整个进程）。返回是否已关闭"""
    v = alive()
    if not v:
        return True
    try:
        ws = websocket.create_connection(v["webSocketDebuggerUrl"], suppress_origin=True, timeout=10)
        ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
        ws.close()
    except Exception:
        return False
    for _ in range(int(wait * 2)):
        if not alive():
            return True
        time.sleep(0.5)
    return False


REPLACE_GAP = 120            # 两次换页至少间隔 2 分钟（新页加载时主线程也很忙，不能一超时就再换 => 循环换页）
REPLACE_SLOW = 120           # 换页后 2 分钟内，请求超时放宽到 45 秒，耐心等新页加载
REPLACE_FILE = os.path.join(HERE, ".last_replace")   # 存时间戳：bot 崩溃重启后也记得刚换过页


def renderer_pids():
    """后台浏览器（本项目专用配置目录）的渲染进程。只匹配带 --user-data-dir=PROFILE 和 --type=renderer 的进程，不碰日常 Chrome"""
    out = subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        p = line.split(None, 1)
        if len(p) == 2 and "--type=renderer" in p[1] and f"--user-data-dir={PROFILE}" in p[1]:
            pids.append(int(p[0]))
    return pids


def last_replace():
    try:
        return os.path.getmtime(REPLACE_FILE)
    except OSError:
        return 0.0


def browser_main_pids():
    """后台浏览器主进程（本项目专用配置目录、不带 --type= 的那个）"""
    out = subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True).stdout
    return [int(p[0]) for p in (l.split(None, 1) for l in out.splitlines())
            if len(p) == 2 and f"--user-data-dir={PROFILE}" in p[1] and "--type=" not in p[1]]


def renderer_stats():
    """后台浏览器渲染进程里最大的 (CPU%, 内存MB)。游戏页跑久了会越来越重（16:50 实测 97 分钟后 202% / 2.8 GB）"""
    pids = renderer_pids()
    if not pids:
        return 0.0, 0
    out = subprocess.run(["ps", "-o", "pcpu=,rss=", "-p", ",".join(map(str, pids))], capture_output=True, text=True).stdout
    vals = [(float(a), int(b) // 1024) for a, b in (l.split() for l in out.splitlines() if len(l.split()) == 2)]
    return (max(v[0] for v in vals), max(v[1] for v in vals)) if vals else (0.0, 0)


def replace_page():
    """游戏页卡死时：整个后台浏览器重启（登录信息在配置目录里，不会丢；启动前清掉上次的标签页记录）。
    15:45 实测：只换标签页没用，新页会复用卡住的渲染进程；杀渲染进程后恢复的页面也一直不响应；
    整个浏览器关掉重开（不恢复旧标签页）5 秒就正常。距上次重启不到 REPLACE_GAP 秒就不做，返回 False。"""
    if not alive() or time.time() - last_replace() < REPLACE_GAP:
        return False
    try:
        prev = os.path.getmtime(HANG_FILE)
    except OSError:
        prev = 0.0
    with open(HANG_FILE, "w") as f:
        f.write(str(time.time()))
    # 10-06 实测：缓存坏了时每次重启都卡（渲染进程 3 GB、CPU 60–90%），清缓存后正常。连续卡死 => 重启时清缓存
    clear = time.time() - prev < HANG_CLEAR_WINDOW
    ok = restart_browser(clear=clear)
    print(f"[cdp] 游戏页无响应，已重启后台浏览器{'并清缓存' if clear else ''}（{'成功' if ok else '失败'}）", flush=True)
    return ok


HANG_FILE = os.path.join(HERE, ".last_hang")
HANG_CLEAR_WINDOW = 900      # 15 分钟内第二次卡死就清缓存
# 只删缓存，不动 Cookies / Local Storage（登录信息在那里）
CACHE_DIRS = ["Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache",
              os.path.join("Service Worker", "CacheStorage"), os.path.join("Service Worker", "ScriptCache")]


def clear_cache():
    """浏览器关闭后调用：删除配置目录里的缓存文件夹。返回删掉的个数"""
    import shutil
    n = 0
    for d in [os.path.join(PROFILE, "Default", c) for c in CACHE_DIRS] + \
             [os.path.join(PROFILE, c) for c in ("GrShaderCache", "ShaderCache", "GraphiteDawnCache")]:
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n


def restart_browser(clear=False):
    """关掉并重新打开后台浏览器（不恢复旧标签页）。卡死恢复和「每 N 关定期重启降 CPU」都用它。clear=True 时关闭后清缓存"""
    with open(REPLACE_FILE, "w") as f:
        f.write(str(time.time()))
    if not close_browser(wait=8):                      # 正常关不掉 => 结束主进程（只匹配本项目的配置目录）
        for pid in browser_main_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for _ in range(10):
            if not alive():
                break
            time.sleep(0.5)
    if clear:
        time.sleep(1)                                  # 等子进程退干净再删文件
        print(f"[cdp] 已清理缓存目录 {clear_cache()} 个", flush=True)
    return launch()


class Browser:
    """线程安全：主线程和冻结监测线程共用一条连接（加锁）。连接断了自动重连，Chrome 没了自动重新启动。"""

    def __init__(self, auto_launch=True):
        self.lock = threading.RLock()
        self.ws = None
        self.i = 0
        self.auto = auto_launch
        self.dpr = DPR                                 # 连接后按页面实际 devicePixelRatio 更新
        self.vw, self.vh = WINDOW

    def _connect(self):
        if not alive():
            if not self.auto or not launch():
                raise RuntimeError("后台 Chrome 没在运行")
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3))
        pages = [t for t in tabs if t.get("type") == "page"]
        if not pages:
            raise RuntimeError("后台 Chrome 没有页面")
        page = (next((t for t in pages if "game.taobao.com" in t.get("url", "")), None)
                or next((t for t in pages if "taobao.com" in t.get("url", "")), pages[0]))
        slow = time.time() - last_replace() < REPLACE_SLOW    # 刚换过页：新页还在加载，多等一会
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], suppress_origin=True, timeout=45 if slow else 12)
        self._call("Emulation.setFocusEmulationEnabled", enabled=True)
        r = self._call("Runtime.evaluate", expression="[innerWidth, innerHeight, devicePixelRatio]", returnByValue=True)
        self.vw, self.vh, self.dpr = r["result"]["value"]

    def _call(self, method, **params):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:                                    # 跳过事件消息，只等自己的回复
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise CDPError(f"{method}: {m['error']}")
                return m.get("result", {})

    def call(self, method, **params):
        """连接断了重连一次；同一次请求两次都超时（页面卡死：渲染进程 100% CPU、协议不回）=> 立刻换新游戏页再试一次。
        以前要「同一进程里两次请求都失败」才换页，bot 启动时一超时就崩溃重启、计数清零，15:07 因此反复崩溃 5 次。"""
        with self.lock:
            timeouts = 0
            for attempt in range(3):
                try:
                    if self.ws is None:
                        self._connect()
                    return self._call(method, **params)
                except CDPError:
                    raise
                except Exception as e:
                    try:
                        self.ws and self.ws.close()
                    except Exception:
                        pass
                    self.ws = None
                    if isinstance(e, (websocket.WebSocketTimeoutException, TimeoutError)):
                        timeouts += 1
                    if attempt == 1 and timeouts == 2 and self.auto:
                        try:
                            ok = replace_page()
                        except Exception:
                            raise e
                        if not ok:
                            raise                      # 2 分钟内刚换过页：不再换，交给调用方（下次请求用 45 秒超时等新页）
                        time.sleep(2)
                        continue
                    if attempt >= 1:
                        raise

    def size(self):
        """视口大小（CSS 像素，点击用这个坐标系）。每次现读，页面布局变了也不会用旧值。"""
        v = self.js("[innerWidth, innerHeight, devicePixelRatio]")
        if v:
            self.vw, self.vh, self.dpr = v
        return self.vw, self.vh

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    def shot(self, clip=None, fmt="png", quality=60):
        """截图（设备像素 = CSS × dpr）。clip=(x, y, w, h) 为 CSS 像素：整个视口截下来再在本地裁剪。
        不把 clip 传给 Chrome：无窗口模式下带 clip 的截图会临时改视口大小，13:00 视口卡在 167x337，画面全白、点击全偏。
        fmt="jpeg"：只用来比画面变化（冻结检测/点击后等变化），编码更快；像素颜色判断仍用 png。"""
        p = {"format": fmt, "captureBeyondViewport": False, "optimizeForSpeed": True}
        if fmt == "jpeg":
            p["quality"] = quality
        data = self.call("Page.captureScreenshot", **p)["data"]
        img = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
        if clip:
            s = self.dpr or 1                          # dpr 由启动参数固定，比缓存的视口宽度可靠
            x, y, w, h = clip
            img = img.crop((round(x * s), round(y * s), round((x + w) * s), round((y + h) * s)))
        return img

    def click(self, x, y):
        """CSS 像素坐标；先移过去再按下/抬起（游戏对瞬间点击有时不响应）"""
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        time.sleep(0.05)
        self.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        time.sleep(0.1)
        self.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)

    def drag(self, x0, y0, x1, y1, steps=14, dur=0.6):
        """按住 (x0,y0) 拖到 (x1,y1) 再松开（CSS 像素）。召唤兽要拖到 Boss 身上"""
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x0, y=y0)
        time.sleep(0.05)
        self.call("Input.dispatchMouseEvent", type="mousePressed", x=x0, y=y0, button="left", buttons=1, clickCount=1)
        time.sleep(0.15)
        for i in range(1, steps + 1):
            k = i / steps
            self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x0 + (x1 - x0) * k, y=y0 + (y1 - y0) * k,
                      button="left", buttons=1)
            time.sleep(dur / steps)
        time.sleep(0.15)
        self.call("Input.dispatchMouseEvent", type="mouseReleased", x=x1, y=y1, button="left", buttons=0, clickCount=1)

    def url(self):
        return self.js("location.href") or ""

    def reload(self):
        self.call("Page.reload", ignoreCache=False)

    def goto(self, url=GAME_URL):
        self.call("Page.navigate", url=url)

    def game_rect(self):
        """游戏 iframe 在页面里的位置 (left, top, width, height)，CSS 像素；没有就 None"""
        r = self.js("""(() => { const f = [...document.querySelectorAll('iframe')].map(e => e.getBoundingClientRect())
            .filter(r => r.width > 100 && r.height > 100).sort((a, b) => b.width * b.height - a.width * a.height)[0];
            return f ? [f.left, f.top, f.width, f.height] : null; })()""")
        return tuple(r) if r else None

    def fullscreen_state(self):
        """网页上的「全屏」/「退出全屏」按钮：返回 {'exit': 是否已全屏, 'btn': 「全屏」按钮中心 (x, y) 或 None}"""
        return self.js("""(() => {
            const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? r : null; };
            const pick = t => [...document.querySelectorAll('body *')].filter(e => (e.textContent || '').trim() === t && vis(e))
                .map(e => vis(e)).sort((a, b) => a.width * a.height - b.width * b.height)[0];
            const b = pick('全屏');
            return {exit: !!pick('退出全屏'), btn: b ? [b.left + b.width / 2, b.top + b.height / 2] : null};
        })()""") or {"exit": False, "btn": None}
