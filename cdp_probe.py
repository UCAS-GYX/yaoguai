# CDP 可行性验证：单独起一个 Chrome（专用配置目录 + 本机调试端口），截图/点击都走 DevTools 协议，不动真鼠标、不截整屏。
# 用法: ./venv/bin/python cdp_probe.py launch [--headless] | shot [文件名] | click X Y | info | anim [次数] | quit
import base64, io, json, os, sys, time, urllib.request
import websocket
from PIL import Image, ImageChops, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 9333                                            # 只监听 127.0.0.1
PROFILE = os.path.join(HERE, "chrome_bot_profile")     # 专用配置目录，和你日常用的 Chrome 完全分开
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
GAME_URL = "https://game.taobao.com/detail?appid=3000000136863671&hdkf_from=hdkf_pc1&horizontal=false"
# 无窗口模式默认 UA 带 HeadlessChrome，换成普通 Chrome 的 UA
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")


def launch(headless):
    import cdp_backend                                 # 和 bot 用同一套启动参数
    print("Chrome 已启动" if cdp_backend.launch(headless) else "Chrome 调试端口 20 秒内没起来")


def page_ws():
    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3))
    pages = [t for t in tabs if t.get("type") == "page"]
    for t in pages:
        if "taobao" in t.get("url", ""):
            return t["webSocketDebuggerUrl"]
    return pages[0]["webSocketDebuggerUrl"]


class CDP:
    def __init__(self, url=None):
        self.ws = websocket.create_connection(url or page_ws(), suppress_origin=True, timeout=20)
        self.i = 0

    def call(self, method, **params):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:                                    # 跳过事件消息，只等自己的回复
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})

    def shot(self):
        t = time.time()
        data = self.call("Page.captureScreenshot", format="png")["data"]
        return Image.open(io.BytesIO(base64.b64decode(data))), time.time() - t

    def click(self, x, y):
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=typ, x=x, y=y, button="left", clickCount=1)
            time.sleep(0.08)

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")


INFO_JS = r"""
(async () => {
  const rect = e => { const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
  let n = 0; const t0 = performance.now();
  await new Promise(ok => { const f = () => { n++; performance.now() - t0 < 1000 ? requestAnimationFrame(f) : ok(); }; requestAnimationFrame(f); });
  return {
    url: location.href, title: document.title, visibility: document.visibilityState, focus: document.hasFocus(),
    viewport: [innerWidth, innerHeight], dpr: devicePixelRatio, webdriver: navigator.webdriver, ua: navigator.userAgent,
    rafPerSec: n,
    iframes: [...document.querySelectorAll('iframe')].map(e => ({src: (e.src || '').slice(0, 120), rect: rect(e)})),
    canvases: [...document.querySelectorAll('canvas')].map(e => ({size: [e.width, e.height], rect: rect(e)})),
  };
})()
"""


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "info"
    if cmd == "launch":
        return launch("--headless" in sys.argv)
    if cmd == "quit":
        v = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=3))
        CDP(v["webSocketDebuggerUrl"]).call("Browser.close")
        return print("已关闭")
    c = CDP()
    if cmd == "shot":
        img, dt = c.shot()
        path = os.path.join(HERE, sys.argv[2] if len(sys.argv) > 2 else "_cdp.png")
        img.save(path)
        print(f"截图 {img.size} 用时 {dt:.2f}s -> {path}")
    elif cmd == "click":
        c.click(float(sys.argv[2]), float(sys.argv[3]))
        print("已点击", sys.argv[2], sys.argv[3])
    elif cmd == "info":
        print(json.dumps(c.js(INFO_JS), ensure_ascii=False, indent=1))
        tree = c.call("Page.getFrameTree")["frameTree"]
        for ch in tree.get("childFrames", []):
            print("子 frame:", ch["frame"].get("url", "")[:140])
    elif cmd == "anim":                                # 连拍，看画面有没有在动（和 bot 的冻结判断同一种算法）
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 6
        prev = None
        for i in range(n):
            img, dt = c.shot()
            g = img.convert("L").resize((160, 100))
            d = ImageStat.Stat(ImageChops.difference(prev, g)).mean[0] if prev else 0
            print(f"#{i} 截图用时 {dt:.2f}s  与上一张差异 {d:.2f}")
            prev = g
            time.sleep(1)


if __name__ == "__main__":
    main()
