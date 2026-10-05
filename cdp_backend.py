# 后台 Chrome（DevTools 协议）：截图 / 点击 / 刷新都在网页内部完成，不动真鼠标、不截整屏，游戏可以在无窗口的 Chrome 里跑。
# 专用配置目录 chrome_bot_profile（和日常用的 Chrome 分开），调试端口只监听 127.0.0.1。
import base64, io, json, os, subprocess, threading, time, urllib.request
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
    if not stray or len(stray) == len(pages):
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


def replace_page():
    """游戏页卡死时：通过浏览器级连接新开一个游戏页，再关掉旧页（旧页的渲染进程不响应，页面级连接用不了）。"""
    v = alive()
    if not v:
        return
    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3))
    old = [t["id"] for t in tabs if t.get("type") == "page"]
    ws = websocket.create_connection(v["webSocketDebuggerUrl"], suppress_origin=True, timeout=10)
    try:
        msgs = [("Target.createTarget", {"url": GAME_URL})] + [("Target.closeTarget", {"targetId": i}) for i in old]
        for n, (m, p) in enumerate(msgs, 1):
            ws.send(json.dumps({"id": n, "method": m, "params": p}))
            while json.loads(ws.recv()).get("id") != n:
                pass
    finally:
        ws.close()
    print(f"[cdp] 游戏页无响应，已新开游戏页并关闭 {len(old)} 个旧页", flush=True)


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
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], suppress_origin=True, timeout=12)
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
        with self.lock:
            for attempt in range(2):
                try:
                    if self.ws is None:
                        self._connect()
                    r = self._call(method, **params)
                    self.fails = 0
                    return r
                except CDPError:
                    raise
                except Exception:
                    try:
                        self.ws and self.ws.close()
                    except Exception:
                        pass
                    self.ws = None
                    if attempt:
                        self.fails = getattr(self, "fails", 0) + 1
                        if self.fails >= 2 and self.auto:      # 页面卡死（渲染进程 100% CPU、协议不回）=> 换一个新标签页
                            self.fails = 0
                            replace_page()
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
