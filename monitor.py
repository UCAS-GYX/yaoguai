# 实时监控页：http://127.0.0.1:8765  （看画面和日志 + 「继续执行」按钮；不能点游戏）
# 用法: ./venv/bin/python monitor.py [--lan]      （start_bot.py 会自动在后台启动它）
#   --lan：同一 WiFi 下的其他设备也能看（需要访问码，见 monitor_key.txt）；「继续执行」仍然只有本机能点
import hmac, io, json, os, secrets, socket, subprocess, sys, threading, time
from http.cookies import SimpleCookie
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


def status():
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


PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>妖怪金手指 监控</title>
<style>
body{margin:0;font:14px/1.5 -apple-system,"PingFang SC",sans-serif;background:#16181d;color:#e6e6e6}
main{display:flex;gap:20px;padding:16px;align-items:flex-start}
#game{max-width:45vw;border-radius:8px;background:#000;display:block}   /* 按图片原尺寸显示，不拉伸 */
section{flex:1;min-width:0}
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
</style></head><body><main>
<div>
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
<section aria-live="polite">
<h1>妖怪金手指 · 后台运行监控</h1>
<div id="alert" role="alert"></div>
<div class="ctl"><button id="resume" type="button" disabled>继续执行</button><button id="stop" type="button" class="danger" disabled>退出</button><span id="msg" role="status"></span></div>
<div class="cards">
 <div class="card"><b>脚本</b><span id="run">-</span></div>
 <div class="card"><b>当前关卡</b><span id="level">-</span></div>
 <div class="card"><b>最近通关</b><span id="win">-</span></div>
 <div class="card"><b>日志更新</b><span id="age">-</span></div>
 <div class="card"><b>后台 Chrome</b><span id="chrome">-</span></div>
</div>
<h2>后台负载 <small id="lavg"></small></h2>
<div class="load">
 <table><thead><tr><th scope="col">程序</th><th scope="col">CPU</th><th scope="col">内存</th><th scope="col">进程数</th></tr></thead><tbody id="loadt"></tbody></table>
 <div><canvas id="spark" width="300" height="70" role="img" aria-label="后台总 CPU 最近 5 分钟走势"></canvas><small id="sparkt"></small></div>
</div>
<h2>每关记录（levels.log）</h2><pre id="levels"></pre>
<h2>运行日志（run.log 最近 30 行）</h2><pre id="log"></pre>
<h2>事件（events.log）</h2><pre id="events"></pre>
</section></main>
<script>
const $ = id => document.getElementById(id);
const TOKEN = "__TOKEN__";
let busy = false;
function setText(id, text, cls) { const e = $(id); e.textContent = text; e.className = cls || ""; }
function setButton(s) {
  if (busy) return;
  const b = $("resume"), x = $("stop");
  b.disabled = !s || !s.local || s.running || s.starting;
  b.textContent = !s ? "继续执行" : s.running ? "运行中" : s.starting ? "处理中…" : !s.local ? "继续执行（仅本机可点）" : "继续执行";
  x.disabled = !s || !s.local || s.starting || (!s.running && !s.chrome);
  x.textContent = s && !s.local ? "退出（仅本机可点）" : "退出";
}
async function act(path, hint) {
  busy = true; $("resume").disabled = $("stop").disabled = true; $("msg").textContent = hint;
  try {
    const r = await fetch(path, {method: "POST", headers: {"X-Token": TOKEN}});
    $("msg").textContent = (await r.json()).msg;
  } catch (e) { $("msg").textContent = "请求失败：" + e; }
  busy = false; refreshStatus();
}
$("resume").onclick = () => act("/api/resume", "正在启动（会先拉起后台浏览器，最多约 30 秒）…");
$("stop").onclick = () => {
  if (confirm("确定退出？会停止脚本并关闭后台浏览器，当前这一关会中断（记为 crash，不算失败）。之后点「继续执行」会重新开始。"))
    act("/api/stop", "正在退出…");
};
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
    setButton(s); drawLoad(s.load);
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

    def _key_ok(self, qs):
        """局域网访问：网址里的 ?key=访问码，或之前访问时留下的 cookie"""
        if not LAN:
            return False
        k = (qs.get("key") or [""])[0]
        if not k:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            k = c["ygkey"].value if "ygkey" in c else ""
        return bool(k) and hmac.compare_digest(k, KEY)

    def do_POST(self):
        if self.path not in ("/api/resume", "/api/stop"):
            return self._send(404, "text/plain", b"not found")
        origin = self.headers.get("Origin")
        if (not self._local() or self.headers.get("X-Token") != TOKEN
                or (origin and origin.split("//")[-1] not in ALLOWED_HOSTS)):
            return self._json(403, "拒绝：「继续执行」「退出」只能在运行脚本的这台电脑上点（页面是旧的就刷新一下）")
        self._json(*(resume() if self.path == "/api/resume" else stop()))

    def do_GET(self):
        u = urlsplit(self.path)
        qs = parse_qs(u.query)
        local = self._local()
        if not local and not self._key_ok(qs):
            return self._send(403, "text/plain; charset=utf-8", "需要访问码：请用电脑上给出的完整链接（带 ?key=...）".encode())
        try:
            if u.path == "/":
                body = PAGE.replace("__TOKEN__", TOKEN if local else "").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                if not local:                              # 记住访问码，之后的截图/状态请求不用每次带 key
                    self.send_header("Set-Cookie", f"ygkey={KEY}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict")
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/status.json":
                s = dict(status(), local=local)
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


if __name__ == "__main__":
    print(f"监控页（本机）：http://127.0.0.1:{PORT}", flush=True)
    if LAN:
        ip = lan_ip()
        print(f"监控页（同一 WiFi 的其他设备）：http://{ip or '本机局域网IP'}:{PORT}/?key={KEY}", flush=True)
    ThreadingHTTPServer(("0.0.0.0" if LAN else "127.0.0.1", PORT), Handler).serve_forever()
