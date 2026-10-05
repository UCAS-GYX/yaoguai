#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
妖怪金手指（淘宝网页小游戏）自动点击脚本 —— 后台版（macOS）
游戏跑在专用的无窗口 Chrome 里（cdp_backend.py），截图/点击都走 DevTools 协议，不动鼠标、不截屏幕。
依赖: pip install pillow websocket-client pynput   (pynput 可选，用于 F8 热键停止)
用法:
  python yaoguai_bot.py            # 运行（画布位置自动从网页读取）
  python yaoguai_bot.py --resume   # 当前已在战斗中，直接接手
停止: kill 进程 / 终端里 Ctrl+C
登录过期: ./venv/bin/python cdp_qr_login.py 扫码
"""
import collections, csv, json, os, re, signal, sys, time, threading
from datetime import datetime

import warnings
import cdp_backend
import notify
import ocr_client
import vision
from vision import find_arrows, find_door, parse_prereq, door_bar_locate, door_bar_measure, DB_W
warnings.filterwarnings("ignore", category=DeprecationWarning)
from PIL import Image, ImageChops, ImageStat

# ======================= 可调参数 =======================
# 所有坐标均为相对游戏画布的比例 (0..1)，由 1280x800 屏幕上画布 x466..813, y94..795 换算得出
POSITIONS = {
    "start":     (0.5014, 0.8759),  # 主页「开始挑战」
    "challenge": (0.5014, 0.9643),  # 阵容页「挑战」
    "summon":    (0.5014, 0.9315),  # 战斗中「召唤」
    "card1":     (0.1988, 0.7932),  # 召唤后第一张卡（3 张卡时）
    "card2":     (0.3285, 0.7932),  # 第一张卡（2 张卡时）
    "card3":     (0.5014, 0.7932),  # 第一张卡（1 张卡时）
    "door":      (0.3285, 0.5835),  # 城门升级箭头
    "upgrade":   (0.7954, 0.5050),  # 城门面板里的黄色「升级」
    "blank":     (0.0400, 0.4500),  # 空白处：左侧墙边，点不到神将/怪物；用于关闭转盘/建筑/升级面板（右下角「自动」别点）
    "nav_blank": (0.8000, 0.3500),  # 非战斗界面（主页奖励弹窗等）的空白处；主页左侧是活动图标，不能用左边
    "info_close": (0.8820, 0.7850), # 「神将信息」等信息面板右下角的「返回」(粉红色) 按钮
    "bed":       (0.7315, 0.7430),  # 床铺（绿色升级箭头处）
    "bed_up":    (0.7775, 0.5220),  # 床铺面板里的「升级」
    "fixcard":   (0.5014, 0.8450),  # 阵容页第二排第 3 张（罗刹女）—— 仅 --fix-lineup 时用来恢复误点
    "pause":     (0.0716, 0.2570),  # 战斗中左上角红色暂停按钮（用于判断是否在战斗中）
    "decline":   (0.6455, 0.5906),  # 拒绝「双倍奖励」
    "chest":     (0.3228, 0.6690),
    "top_mask":  (0.5000, 0.1300),  # 弹窗上方的暗色遮罩（随机事件等点这里关闭）
    "info_back": (0.8820, 0.7850),  # 右下角「返回」  # 主页奖励宝箱
    "promo_check": (0.4040, 0.7970), # 推广弹窗（地府公会战/万鬼无限城）的「今日不再提示」勾选框（OCR 失败时的后备位置）
    "promo_blank": (0.5000, 0.1300), # 推广弹窗关闭：点上方遮罩（绝不点「前往」）
    "back":      (0.0692, 0.9643),  # 左下角「返回」
    "price":     (0.5014, 0.9087),  # 「召唤」按钮上方的价格数字（用于红色检测）
    "build":     (0.0750, 0.9480),  # 左下角「建造机关」（仅探索模式 explore_build 使用）
}
CHECK_PRICE_RED = True      # 召唤价格变红（金币不足）时跳过召唤；设 False 关闭检测
RED_RATIO = 0.08            # 采样框中“红色像素”占比超过此值即视为红色
PRICE_BOX = 0.02            # 价格采样框半宽（相对画布宽度）
EARLY_DOOR_TRIES = 3        # 开局城门升级次数
EARLY_SUMMONS = 4           # 开局召唤次数（简单关卡 3~4 次即可，每次召唤会暂停游戏）
EARLY_BED_TRIES = 2         # 开局升床次数（床升到 2 级）
SUMMON_EVERY = 4            # 战斗循环中每隔几轮召唤一次（其余轮只升床/升门）
LOOP_INTERVAL = 20          # 战斗中循环间隔（秒）
MAX_BATTLE_SEC = 15 * 60    # 单场最长战斗时间（秒），到时即执行结算流程
DETECT_END = True           # 通过左上角暂停按钮判断战斗是否结束
STATIC_DIFF = 1.5           # 画面“静止”阈值（灰度平均差）
MAX_LEVELS = 9999           # patch26：原 50 => 跑满 50 场后脚本自己结束、无人值守时白等；改成基本不限
MAX_NO_PROGRESS = 3         # 连续多少次“无进展”（点击后画面几乎不变）就停止
PERIODIC_SUMMON = "auto"    # patch26："always"=旧行为（每 SUMMON_EVERY 轮召唤一次）；"auto"=只有本关失败过 / 门掉过血 / 进过防守模式才周期召唤
                            #          （用户规则：简单关卡只召唤 3~4 次，其余靠升床/升门；门血<50% 和防守模式的额外召唤不受影响）
FAST_POPUP = True           # patch26：战斗中弹窗（精英掉落转盘等，游戏暂停中）点空白后用像素快速轮询，1.2 s 没关再点一次，不再等 OCR
SPEED_2X = False            # patch26：左上「X1」倍速按钮。默认关闭！只有用户确认它免费（不是月卡/充值）后才打开
SPEED_POS = (0.069, 0.304)  # 「X1」按钮位置（暂停按钮正下方）
CALIB_FILE = "calib_cdp.json"   # CDP 版：画布位置是网页内 CSS 像素（旧版整屏坐标在 calib.json，不再使用）
LOG_FILE = "battle_log.csv"
# =======================================================

IS_MAC = sys.platform == "darwin"
STOP = threading.Event()
HERE = os.path.dirname(os.path.abspath(__file__))
BR = cdp_backend.Browser()     # 后台 Chrome：截图/点击都走 DevTools 协议，不动真鼠标、不截整屏


def slow_click(x, y):
    """网页内 CSS 像素坐标；先移过去再按下/抬起（游戏对瞬间点击有时不响应）"""
    BR.click(x, y)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def start_hotkey():
    try:
        from pynput import keyboard
    except ImportError:
        log("未安装 pynput，F8 热键不可用；请用 Ctrl+C 或把鼠标移到左上角停止。")
        return

    def on_press(key):
        if key == keyboard.Key.f8:
            log("检测到 F8，准备停止……")
            STOP.set()
            return False
    keyboard.Listener(on_press=on_press, daemon=True).start()
    log("按 F8 可随时停止。")


def sleep(sec):
    """可被 F8 打断的等待"""
    if STOP.wait(sec):
        raise KeyboardInterrupt


# ---------------- 校准与坐标换算 ----------------
# CDP 版：画布 = 网页里游戏 iframe 的位置（DOM 直接读出），不用手动校准。calib_cdp.json 只是上次的结果。
CALIB_DEFAULT = {"left": 442, "top": 6, "width": 397, "height": 801}


def load_calib(force=False):
    path = os.path.join(HERE, CALIB_FILE)
    calib = dict(CALIB_DEFAULT)
    if not force and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                calib.update(json.load(f))
        except Exception:
            pass
    log(f"已读取画布位置: {calib}")
    return calib


def find_canvas(img, scale):
    """在整屏截图里找游戏画布（黑色网页背景中间的非黑矩形），返回逻辑坐标 dict 或 None"""
    g = img.convert("L"); W, H = g.size
    s = g.point(lambda v: 255 if v > 14 else 0).resize((W // 2, H // 2)); w, h = s.size; px = s.load()
    strip = int(w * 0.25)
    top = None
    for y in range(int(h * 0.08), h // 2):            # 浏览器工具栏下方第一行「两侧基本全黑」= 网页区顶部
        lf = sum(1 for x in range(0, strip, 2) if px[x, y] > 127) / (strip / 2)
        rf = sum(1 for x in range(w - strip, w, 2) if px[x, y] > 127) / (strip / 2)
        if lf < 0.03 and rf < 0.15:
            top = y; break
    if top is None:
        return None
    hh = h - top
    cols = [sum(1 for y in range(top, h, 2) if px[x, y] > 127) / (hh / 2) for x in range(w)]
    xs = [x for x in range(w) if cols[x] > 0.6]
    if not xs:
        return None
    x0, x1 = xs[0], xs[-1]
    rows = [sum(1 for x in range(x0, x1 + 1, 2) if px[x, y] > 127) / ((x1 - x0 + 1) / 2) for y in range(top, h)]
    ys = [i for i, r in enumerate(rows) if r > 0.6]
    if not ys:
        return None
    y0, y1 = top + ys[0], top + ys[-1]
    f = 2 / scale
    return dict(left=round(x0 * f), top=round(y0 * f), width=round((x1 - x0 + 1) * f), height=round((y1 - y0 + 1) * f))


class Canvas:
    def __init__(self, calib):
        self.c = calib
        self.scale = BR.dpr or 1                       # 截图像素 = CSS 像素 × dpr（和 Retina 整屏截图一样是 2 倍）
        log(f"截图缩放系数: {self.scale:.2f}（后台 Chrome，视口 {BR.size()}）")
        self.autocalib("启动")

    def autocalib(self, why=""):
        """CDP 版：画布 = 网页里游戏 iframe 的位置（DOM 读出，不受弹窗遮罩干扰）。只在全屏布局下采用，偏差 >3 点就更新并保存。"""
        if not game_visible(0):
            return False
        try:
            g = BR.game_rect()
            vh = BR.size()[1]
        except Exception as e:
            log(f"自动校准失败: {e}")
            return False
        if not g:
            return False
        r = dict(left=round(g[0]), top=round(g[1]), width=round(g[2]), height=round(g[3]))
        if r["width"] < 250 or not (0.44 < r["width"] / r["height"] < 0.56) or r["height"] < 0.9 * vh:
            return False                               # 不像全屏游戏画布（嵌入式小窗口/加载中）=> 不改
        old = {k: self.c[k] for k in ("left", "top", "width", "height")}
        if all(abs(r[k] - old[k]) <= 3 for k in old):
            return False
        self.c.update(r)
        try:
            with open(os.path.join(HERE, CALIB_FILE), "w", encoding="utf-8") as f:
                json.dump(self.c, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        log(f"自动校准{('（' + why + '）') if why else ''}：画布位置变化 {old} -> {r}，已更新 calib.json")
        return True

    def abs(self, name):
        fx, fy = POSITIONS[name]
        return (self.c["left"] + fx * self.c["width"], self.c["top"] + fy * self.c["height"])

    def click(self, name, wait=0.6, note=""):
        wait_game_visible()                            # 游戏不在前台时绝不点击
        now = time.time()
        if name in NAV_COOLDOWN and now - LAST_CLICK.get(name, 0) < NAV_COOLDOWN[name]:
            sleep(0.3); return                         # ALAS 式按钮冷却：同一按钮 2~3 秒内不重复点
        LAST_CLICK[name] = now
        if name not in WD_IGNORE:
            RECENT_CLICKS.append(name)
            if len(RECENT_CLICKS) >= 15 and RECENT_CLICKS.count(name) >= 12 and not CLICK_WD["flag"]:
                CLICK_WD["flag"] = True
                log(f"看门狗：最近 15 次点击里 {RECENT_CLICKS.count(name)} 次是「{name}」，触发恢复链")
                event("watchdog", None, extra=f"button={name} 12/15")
        x, y = self.abs(name)
        slow_click(x, y)
        if note:
            log(note)
        sleep(wait)

    def grab(self):
        """截取画布区域（截图像素坐标）"""
        c = self.c                                     # 整个视口截下来，本地裁出画布（返回 dpr 倍像素）
        return BR.shot(clip=(c["left"], c["top"], c["width"], c["height"]))

    def grab_fast(self):
        """只用来比较画面有没有变化：JPEG 编码更省 CPU，不用于颜色判断"""
        c = self.c
        return BR.shot(clip=(c["left"], c["top"], c["width"], c["height"]), fmt="jpeg", quality=50)

    def wait_changed(self, before, timeout=3.0, thr=2.5):
        """点击后轮询（0.25 s）直到画面变化或超时；变化后再等 0.35 s 让动画稳定。返回是否变化"""
        t = time.time()
        while time.time() - t < timeout:
            time.sleep(0.25)
            if self.diff(before, self.grab_fast()) > thr:
                time.sleep(0.35)
                return True
        return False

    def diff(self, a, b):
        a = a.convert("L").resize((80, 160)); b = b.convert("L").resize((80, 160))
        return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]

    def in_battle(self, img=None):
        """左上角红色暂停按钮还在 => 战斗中"""
        img = self.grab() if img is None else img; w, h = img.size
        fx, fy = POSITIONS["pause"]; cx, cy = int(fx * w), int(fy * h); r = max(4, int(0.025 * w))
        px = list(img.crop((cx - r, cy - r, cx + r, cy + r)).convert("RGB").getdata())  # noqa
        red = sum(1 for (R, G, B) in px if R > 90 and R > 1.8 * G and R > 1.8 * B)
        return red / max(1, len(px)) > 0.4

    def color_at(self, fx, fy, img=None):
        img = self.grab() if img is None else img; w, h = img.size
        cx, cy = int(fx * w), int(fy * h); r = max(3, int(0.012 * w))
        px = list(img.crop((cx - r, cy - 3, cx + r, cy + 3)).convert("RGB").getdata())
        return tuple(sum(p[i] for p in px) / len(px) for i in range(3))

    @staticmethod
    def near(c, ref, tol=40):
        return sum((a - b) ** 2 for a, b in zip(c, ref)) ** 0.5 < tol

    def screen(self, img=None):
        """粗略识别当前界面: battle / home / lineup / other"""
        img = self.grab() if img is None else img
        if self.in_battle(img):
            return "battle"
        if self.near(self.color_at(*POSITIONS["start"], img), (80, 148, 140)):
            return "home"
        if self.near(self.color_at(*POSITIONS["challenge"], img), (200, 172, 126)):
            return "lineup"
        return "other"

    def ocr(self, img=None, tag="ocr"):
        """用 macOS Vision（./ocr，可执行文件由 ocr.swift 编译）识别画布文字。
        返回 [(text, x, y)]，x/y 为文字框中心的画布比例坐标。失败时返回 []。"""
        exe = os.path.join(HERE, "ocr")
        if not os.path.exists(exe) or not game_visible():
            return []
        try:
            img = self.grab() if img is None else img
            if img.width < 600:                        # 1 倍截图（约 397 宽）先放大 2 倍再识别，小字「4级」/价格更准
                img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
            path = os.path.join(HERE, f"_{tag}.png"); img.save(path)
            out = ocr_client.ocr_text(path, 8)          # 常驻 OCR 进程（约 0.17 s/次，原每次启动进程约 0.32 s）
            body = "\n".join(l for l in out.splitlines() if not re.match(r"^\s*[\dPEC-]{6,}|^.*huntu", l))  # 去掉游戏底部版本号行
            page_check(body)                           # 淘宝网页/崩溃页文字 => 标记，由导航/战斗循环做恢复
            m_ov = re.search(r"(?<![\d.])\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])|https?:|\.com|GitLab|个账号|mail\.", body)
            if m_ov:
                _VIS["block_until"] = time.time() + 6      # 画布被浏览器下拉框/其他内容遮住 => 暂停点击
                log(f"画布上出现浏览器/网页内容（被遮挡：{m_ov.group(0)}），暂停点击几秒")
                return []
            res, boxes = [], []
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) == 5:
                    t, x, y, w, h = parts[0], *map(float, parts[1:])
                    res.append((t, round(x + w / 2, 3), round(y + h / 2, 3)))
                    boxes.append((t, x, y, w, h))
            OCR_LAST.append((res, boxes))                  # patch25：保留文字框（左上角+宽高），用于定位勾选框（监控线程也会 OCR，按对象匹配）
            return res
        except Exception as e:
            log(f"OCR 失败: {e}")
            return []

    def card_checked(self, name):
        img = self.grab(); w, h = img.size
        fx, fy = POSITIONS[name]; cx, cy = int(fx * w), int(fy * h); r = max(6, int(0.03 * w))
        px = list(img.crop((cx - r, cy - r, cx + r, cy + r)).convert("RGB").getdata())
        g = sum(1 for (R, G, B) in px if G > 150 and G > R + 40 and G > B + 40)
        return g / max(1, len(px)) > 0.15

    def price_is_red(self):
        img = self.grab()
        w, h = img.size
        fx, fy = POSITIONS["price"]
        r = max(2, int(PRICE_BOX * w))
        box = img.crop((int(fx * w) - r, int(fy * h) - r // 2, int(fx * w) + r, int(fy * h) + r // 2))
        px = list(box.convert("RGB").getdata())
        red = sum(1 for (R, G, B) in px if R > 180 and G < 90 and B < 90)
        return red / max(1, len(px)) >= RED_RATIO


# ---------------- 动作 ----------------
UPG_LOG = {"door": "", "bed": ""}
PANEL_KEY = {"door": "大门", "bed": "床铺"}


def find_bed(texts):
    """床的位置每关不同：按 OCR「N级」标签找（识别逻辑在 vision.py，这里排除本关发电机的标签）"""
    return vision.find_bed(texts, BATTLE.get("gen_labels", []))


def pick_arrow(target, arrows):
    """门的箭头在上墙（y<0.64，取最上面的）；床的箭头在下方（优先已知床位置附近，否则离 (0.72,0.76) 最近）"""
    if target == "door":
        c = [a for a in arrows if a[1] < 0.64]
        return min(c, key=lambda a: a[1])[:2] if c else None
    c = [a for a in arrows if a[1] >= 0.64 and a[:2] not in BATTLE.get("not_bed", [])]
    bp = BATTLE.get("bed_pos")
    if bp:
        c = [a for a in c if abs(a[0] - bp[0]) < 0.06 and abs(a[1] - bp[1]) < 0.06]
        return c[0][:2] if c else None
    return min(c, key=lambda a: (a[0] - 0.72) ** 2 + (a[1] - 0.76) ** 2)[:2] if c else None


# ---------------- 门血条 / 防守模式（patch23）----------------
# 门左边「N级」标签右侧的亮绿色分段血条：每秒在战斗画面上读一次绿色格子的列数（391 宽缩略图上）。
# 记录本关最高值；中位数连续 2 次低于最高值的 90% => 门在挨打 => 防守模式：钱够（召唤价格不是红色）
# 就召唤，间隔约 4 秒；20 秒没再掉血或血条回升到 95% 以上 => 退出。全部在脚本内处理，不写 ALERT。
DEFENSE = True
DEF_DROP = 0.90              # 低于最高值的 90% => 进入防守模式
DEF_RECOVER = 0.95           # 回升到 95% 以上 => 退出
DEF_CALM = 20                # 多少秒没继续掉血 => 退出
DEF_REDROP = 0.06            # 因“不再掉血”退出后，要再掉这么多才重新进入
DEF_GAP = 3.0                # 防守召唤间隔（秒，从上一次选卡结束算）
DEF_FAIL_GAP = 10.0          # 召唤后没出选卡界面/选卡没关 => 缓 10 秒再试
DOOR_READ_EVERY = 1.0        # 读血条间隔（秒）


def _dw():
    return BATTLE.setdefault("dw", {"geo": None, "max": 0, "hist": [], "low_n": 0, "mode": False, "modes": 0,
                                    "dsum": 0, "msum": 0, "next_read": 0.0, "next_sum": 0.0, "lv": None,
                                    "loc_next": 0.0, "reloc_next": 0.0, "bad": 0, "stable": None, "ratio": None,
                                    "red_logged": False, "nf_logged": False, "err": False})


def door_watch_pause(sec=1.5):
    """脚本自己点过东西（升级面板/召唤选卡/弹窗）后，先别读血条，丢掉旧样本，避免把遮挡当成掉血"""
    d = _dw()
    d["hist"] = []
    d["next_read"] = max(d["next_read"], time.time() + sec)


def _def_stop(d, why):
    d["mode"] = False
    log(f"退出防守模式（{why}），本次防守召唤 {d['msum']} 次，本关防守召唤共 {d['dsum']} 次")


def _def_summon(cv, d):
    """防守模式召唤一次：价格红色就等；仍然只选第一张卡并确认选卡界面已关闭"""
    if END_SEEN[0]:
        return False
    if CHECK_PRICE_RED and cv.price_is_red():
        if not d["red_logged"]:
            d["red_logged"] = True
            log("防守模式：召唤价格为红色（金币不足），等钱够再召唤")
        d["next_sum"] = time.time() + 2
        return False
    d["red_logged"] = False
    if not clear_popups(cv):
        d["next_sum"] = time.time() + 3
        return True
    texts = cv.ocr(tag="def")                          # 选卡界面还开着/已结算 => 先处理，不叠加召唤
    if not texts:                                      # 游戏不在前台/被遮挡 => 不点
        d["next_sum"] = time.time() + 2
        return False
    if is_end_screen(texts):
        END_SEEN[0] = True
        return True
    if picker_open(texts):
        pick_card(cv)
        d["next_sum"] = time.time() + DEF_GAP
        door_watch_pause(1.0)
        return True
    t = time.time()
    cv.click("summon", 1.2, "召唤（防守模式）")
    BATTLE["summons"] += 1
    ok = pick_card(cv)
    if ok:
        d["dsum"] += 1; d["msum"] += 1
    d["t_fall"] = d.get("t_fall", t) + (time.time() - t)   # 选卡时游戏暂停，不算进“没掉血”的时间
    d["next_sum"] = time.time() + (DEF_GAP if ok else DEF_FAIL_GAP)
    door_watch_pause(1.0)
    return True


def door_watch(cv, img):
    """战斗画面（暂停按钮可见）时由 wait_battle 每 0.5 s 调用；内部每 DOOR_READ_EVERY 秒读一次门血条。
    返回 True 表示做了点击（调用方应重新截图）。任何异常都只记一次日志，不影响主流程。"""
    if not DEFENSE:
        return False
    d = _dw()
    try:
        acted = False
        now = time.time()
        if now >= d["next_read"]:
            d["next_read"] = now + DOOR_READ_EVERY
            _door_read(img, d, now)
        if d["mode"] and time.time() - d.get("t_ok", now) > DEF_CALM:
            d["stable"] = None
            _def_stop(d, f"{DEF_CALM} 秒读不到门血条")
        if d["mode"] and time.time() >= d["next_sum"]:
            acted = _def_summon(cv, d)
        return acted
    except Exception as e:
        if not d["err"]:
            d["err"] = True
            log(f"门血条/防守模式出错（忽略，不影响其他操作）：{e}")
        return False


def _door_read(img, d, now):
    lv = BATTLE.get("door_lv")
    if lv != d["lv"]:
        if d["lv"] is not None:                        # 门升级 => 回血、血条可能变长：重新定位、重置基准
            d.update(geo=None, max=0, hist=[], low_n=0, stable=None)
            if d["mode"]:
                _def_stop(d, f"门升到 {lv} 级，回血")
        d["lv"] = lv
    if d["geo"] is None:
        if now < d["loc_next"]:
            return
        g = door_bar_locate(img)
        if not g:                                      # 找不到（被遮挡/不在画面）=> 安静跳过，3 秒后再找
            d["loc_next"] = now + 3
            if not d["nf_logged"]:
                d["nf_logged"] = True
                log("门血条：暂时没找到，稍后再找（不影响其他操作）")
            return
        d.update(geo=g, bad=0, reloc_next=now + 30)
        log(f"门血条定位：x {g['x0']:.3f}–{g['x1']:.3f}，y {g['y0']:.3f}–{g['y1']:.3f}")
    n = door_bar_measure(img, d["geo"])
    if n == 0:                                         # 整条都看不到亮绿 => 被遮挡/变暗，不算掉血
        d["bad"] += 1
        if d["bad"] >= 15:                             # 长时间看不到：重新定位（沿用旧位置，直到找到新的）
            g = door_bar_locate(img)
            if g:
                if abs(g["x0"] - d["geo"]["x0"]) > 0.02 or abs(g["y0"] - d["geo"]["y0"]) > 0.02:
                    d.update(max=0, hist=[])
                d["geo"] = g
            d["bad"] = 0
        return
    d["bad"] = 0
    d["t_ok"] = now
    d["hist"] = (d["hist"] + [n])[-3:]
    if len(d["hist"]) < 3:
        return
    med = sorted(d["hist"])[1]
    if med > d["max"]:
        d["max"] = med
    r = med / max(1, d["max"])
    d["ratio"] = r
    if not d["mode"] and r >= DEF_RECOVER and now >= d["reloc_next"]:   # 满血时偶尔重新定位：血条变长就换新基准
        d["reloc_next"] = now + 30
        g = door_bar_locate(img)
        if g and (g["x1"] - g["x0"]) > (d["geo"]["x1"] - d["geo"]["x0"]) + 2.5 / DB_W:
            d.update(geo=g, max=0, hist=[])
            log(f"门血条变长，重新定位：x {g['x0']:.3f}–{g['x1']:.3f}")
            return
    if d["mode"]:
        if r < d["low"] - 0.02:
            d["low"], d["t_fall"] = r, now
        if r >= DEF_RECOVER:
            d["stable"] = None
            _def_stop(d, f"血条回升到 {r:.0%}")
        elif now - d["t_fall"] >= DEF_CALM:
            d["stable"] = d["low"]
            _def_stop(d, f"{DEF_CALM} 秒没有继续掉血，血条 {r:.0%}")
        return
    if d["stable"] is not None and r >= DEF_RECOVER:
        d["stable"] = None
    limit = DEF_DROP if d["stable"] is None else min(DEF_DROP, d["stable"] - DEF_REDROP)
    d["low_n"] = d["low_n"] + 1 if r < limit else 0
    if d["low_n"] >= 2:                                # 连续 2 次中位数都低 => 真的在掉血
        d.update(mode=True, low=r, t_fall=now, t_ok=now, msum=0, low_n=0, next_sum=0.0)
        d["modes"] += 1
        log(f"门在掉血：血条 {r:.0%}（{med}/{d['max']} 列）→ 进入防守模式（钱够就召唤，间隔约 {DEF_GAP:.0f} 秒）")


def _upgrade(cv, target, btn, name):
    if target == "bed" and BATTLE.get("bed_max"):
        return "已满级"                                 # 床本关已满级，不再点（门满级仍打开面板读血量）
    if not clear_popups(cv):
        return ""
    aimg = cv.grab()
    arrows = find_arrows(aimg)
    pos = pick_arrow(target, arrows)
    by_arrow = pos is not None
    if pos is None:                                    # 没有绿色箭头 => 现在升不了（钱/电/前置不够或满级）
        k = target + "_noarrow"
        BATTLE[k] = BATTLE.get(k, 0) + 1
        if BATTLE[k] % 4 != 0:                         # 每 4 次才按旧办法试一次（兜底 + 读门血量）
            return "无箭头"
        if target == "bed" and BATTLE.get("bed_pos"):
            return "无箭头"                            # 床位置已知又没箭头 = 现在升不了，盲点只会报「没找到床铺」
        pos = BATTLE.get(target + "_pos")
    else:
        BATTLE[target + "_noarrow"] = 0
    if pos:
        POSITIONS["_t"] = pos; cv.click("_t", 0.9)
    else:
        cv.click(target, 0.9)
    tp = cv.ocr(tag="panel")
    panel = ocr_join(tp, 0.3, 0.75)                    # 面板文字（等级/花费/提示）
    if PANEL_KEY[target] in panel and pos:
        BATTLE[target + "_pos"] = pos                  # 记住这关的床/门位置
        if by_arrow and not vision.has_cdp_template() and vision.save_arrow_template(aimg, pos):
            log(f"已在后台浏览器画面里采集箭头模板（{target} 箭头 {pos}），之后箭头识别更准")
    elif target == "bed" and pos and pos[1] >= 0.64:
        BATTLE.setdefault("not_bed", []).append(pos)   # 这个箭头不是床（可能是发电机）
    if PANEL_KEY[target] not in panel:
        if not close_info(cv, cv.ocr(tag="panel2")):
            cv.click("blank", 0.6)
        else:
            time.sleep(0.8)
        fimg = cv.grab(); tf = cv.ocr(fimg, tag="find")
        bp = find_bed(tf) if target == "bed" else find_door(tf, fimg)   # 床/门位置每关不同 => 按 OCR 等级标签找（门还可按血条找）
        if not bp:
            BATTLE["miss"] = BATTLE.get("miss", 0) + 1
            if BATTLE["miss"] % 2 == 0:
                cv.autocalib("连续找不到床/门")
            log(f"尝试升级{name}：没找到{name}，跳过（含「级」的文字：{[(t, x, y) for t, x, y in tf if '级' in t][:6]}）")
            return ""
        BATTLE[target + "_pos"] = bp
        log(f"{name}位置更新为 ({bp[0]:.3f},{bp[1]:.3f})")
        POSITIONS["_t"] = bp; cv.click("_t", 0.9)
        t3 = cv.ocr(tag="panel")
        panel = ocr_join(t3, 0.3, 0.75)
        if PANEL_KEY[target] not in panel:
            BATTLE[target + "_pos"] = None
            log(f"尝试升级{name}：按 OCR 位置仍没打开{name}面板，跳过")
            if not close_info(cv, t3):
                cv.click("blank", 0.4)
            return ""
    m_lv = re.search(r"(\d{1,2})级" + PANEL_KEY[target], panel)
    if m_lv:
        BATTLE[target + "_lv"] = int(m_lv.group(1))
    need = parse_prereq(panel)
    if need and need[0] != target and BATTLE.get(need[0] + "_lv", 0) < need[1]:
        BATTLE["need"] = (target, need[0], need[1])      # 前置条件没满足 => 先升前置建筑，不点升级
        log(f"{name}升级需要前置：{need[1]}级{'大门' if need[0] == 'door' else '床铺'}，先去升那个")
        cv.click("blank", 0.4)
        return "前置"
    if target == "door":                               # 门血量「当前/上限」=> 判断是否危险
        m = re.search(r"(\d[\d ]{2,7})\s*/\s*(\d{4,6})", panel)   # OCR 会在数字中间插空格：「37 120/37120」
        if m:
            cur, top = int(m.group(1).replace(" ", "")), int(m.group(2))
            if 0 < top and cur <= top:                 # 当前血量大于上限 = 读错了（如「12450/ 2450」），不采用
                BATTLE["door_hp"] = cur / top
    if "已满级" in panel:
        if not BATTLE.get(target + "_max"):
            log(f"{name}已满级，本关不再点升级")
        BATTLE[target + "_max"] = True
        cv.click("blank", 0.4)
        return "已满级"
    cv.click(btn, 0.7)                                 # 面板固定在同一位置（OCR 的「升级」是面板标题，不是按钮）
    toast = ocr_join(cv.ocr(tag="toast"), 0.2, 0.3)      # 「升级成功」/「金币不足」等提示
    need = parse_prereq(toast)
    if need and need[0] != target:
        BATTLE["need"] = (target, need[0], need[1])
    if "升级成功" in toast and BATTLE.get(target + "_lv"):
        BATTLE[target + "_lv"] += 1
    msg = f"尝试升级{name}：{toast or '-'}"
    if panel != UPG_LOG[target]:                       # 面板内容变化时才打印，避免刷屏
        UPG_LOG[target] = panel
        msg += f"  [面板] {panel[:160]}"
    log(msg)
    cv.click("blank", 0.4)
    return toast


BUILD_TILES = [(0.50, 0.76), (0.27, 0.76), (0.62, 0.70), (0.15, 0.70), (0.39, 0.76), (0.62, 0.76),
               (0.27, 0.70), (0.15, 0.76)]   # 房间地面「+」格子候选（房间布局每关不同，靠 OCR「新增建筑」确认）
GEN_BUY = (0.80, 0.49)       # OCR 找不到价格按钮时的后备位置（旧版固定坐标）
MAX_GENS = 2
BUILD_KINDS = (("gen", ("发电机", "每秒+1电", "发电")), ("cat", ("招财猫", "招财", "摸鱼")))
BUILD_NAME = {"gen": "发电机", "cat": "招财猫"}
BUILD_GAP = 45               # 两次打开「新增建筑」至少隔 45 秒（以前一关里点了 100 多次）


def parse_build_panel(texts):
    """「新增建筑」面板（位置随点的格子变化）：每行 = 名字/说明 + 「数量:n/m」+ 右侧价格按钮。
    返回 {kind: {"n": n, "m": m, "btn": (x, y) 或 None}}，kind = gen（发电机）/ cat（招财猫）。
    以前只取第一个「数量」、点固定坐标：发电机满了以后面板只剩招财猫，把它的 0/1 当成发电机，一关里点了 100 多次。"""
    rows = []
    for t, x, y in texts:
        m = re.search(r"数量\s*[:：]?\s*(\d)\s*/\s*(\d)", t.replace(" ", ""))
        if m:
            rows.append({"y": y, "n": int(m.group(1)), "m": int(m.group(2)), "kind": None, "btn": None})
    if not rows:
        return {}
    def near(y):                                       # 名字/说明/价格都在本行「数量」那一行或它下面 => 归到上方最近的行
        above = [r for r in rows if r["y"] <= y + 0.02]
        return max(above, key=lambda r: r["y"]) if above else min(rows, key=lambda r: abs(r["y"] - y))
    for t, x, y in texts:
        r = near(y)
        if abs(r["y"] - y) > 0.07:
            continue
        for kind, words in BUILD_KINDS:
            if r["kind"] is None and any(w in t for w in words):
                r["kind"] = kind
        if x >= 0.68 and re.fullmatch(r"[^\d]{0,2}\d{2,4}", t.strip()) and abs(r["y"] - y) <= 0.04:
            r["btn"] = (x, y)                          # 价格按钮（300 金 / 60 电）
    return {r["kind"]: r for r in rows if r["kind"]}


def build_room(cv):
    """点空地格子打开「新增建筑」：先建发电机（300 金，+1 电/秒，每关最多 2 座），满了再建招财猫（60 电，+10 金/秒，1 座）。
    门 6 级以后、床 7 级都要电。按 OCR 找按钮；全满 / 资源不足就关面板，本关不再反复打开。"""
    if BATTLE.get("build_done") or time.time() < BATTLE.get("build_next", 0) or not clear_popups(cv):
        return
    BATTLE["build_next"] = time.time() + BUILD_GAP
    tried = BATTLE.setdefault("bad_tiles", [])
    for tile in BUILD_TILES:
        if tile in tried:
            continue
        POSITIONS["_b"] = tile; cv.click("_b", 1.0)
        texts = cv.ocr(tag="build")
        j = ocr_join(texts)
        if "新增建筑" not in j:
            tried.append(tile)
            if not close_info(cv, texts) and not cv.in_battle():
                cv.click("blank", 0.6)                 # 关掉误开的面板（建筑升级等）
            time.sleep(0.6)
            clear_popups(cv)
            continue
        rows = parse_build_panel(texts)
        gen, cat = rows.get("gen"), rows.get("cat")
        if gen:
            BATTLE["gens"], BATTLE["gen_max"] = gen["n"], gen["m"]   # 上限以面板为准（房间研发后 2 → 3）
        want = (("gen", gen) if gen and gen["n"] < gen["m"] else
                ("cat", cat) if cat and cat["n"] < cat["m"] else None)
        if want is None:
            BATTLE["build_done"] = True
            cv.click("blank", 0.6)
            have = "；".join(f"{BUILD_NAME[k]} {v['n']}/{v['m']}" for k, v in rows.items()) or "面板没认出来"
            log(f"建筑已满（{have}），本关不再建造。[面板] {j[:100]}")
            return
        kind, r = want
        POSITIONS["_b"] = r["btn"] or (GEN_BUY if kind == "gen" else (GEN_BUY[0], r["y"]))
        cv.click("_b", 1.2)
        toast = ocr_join(cv.ocr(tag="build2"))
        if "不足" in toast:
            log(f"建造{BUILD_NAME[kind]}：资源不足，{BUILD_GAP} 秒后再试")
            cv.click("blank", 0.6)
            return
        tried.append(tile)                             # 这个格子已经有建筑了
        if kind == "gen":
            BATTLE["gens"] = r["n"] + 1
            BATTLE.setdefault("gen_labels", []).append((tile[0], tile[1] + 0.04))
        log(f"建造{BUILD_NAME[kind]}：格子 {tile}，第 {r['n'] + 1}/{r['m']} 座（按钮 {POSITIONS['_b']}）")
        if "新增建筑" in toast:
            cv.click("blank", 0.6)
        return
    BATTLE["build_done"] = True
    log("没有找到能打开「新增建筑」的空格子，本关不再建造")


build_generator = build_room                           # 旧名字（explore 等地方还在用）


def door_upgrade(cv):
    return _upgrade(cv, "door", "upgrade", "城门")


def bed_upgrade(cv):
    return _upgrade(cv, "bed", "bed_up", "床铺")


# ---------------- 推广弹窗（patch25）----------------
# 地府公会战 / 万鬼无限城 等活动推广弹窗：按钮「前往」+ 勾选框「今日不再提示」。
# 处理：勾「今日不再提示」（OCR 定位，后备 (0.404,0.797)）→ 点上方遮罩 (0.5,0.13) 关闭；绝不点「前往」；叠加弹窗最多处理 4 层。
OCR_LAST = collections.deque(maxlen=8)   # [(texts, boxes)]：Canvas.ocr 的结果 + 文字框（左上角+宽高，画布比例）
PROMO_TITLES = ("万鬼无限城", "地府公会战", "公会战开放", "无限城", "归墟幻海")
PROMO_BODY = ("开放中", "已开启", "本期倒计时", "倒计时：", "倒计时:")   # 弹窗正文（主页只有活动入口标题，没有这些）
PROMO_GO = ("前往", "立即前往", "前往参与", "去参加", "去看看")
GO_RE = re.compile(r"^(前往|立即前往|前往参与|去参加|去看看|前|往)[：:。.,，]?$")   # OCR 常把「前往」拆成「前：」「往」
CHK_RE = re.compile(r"今日不再|不再提示|不再提醒")
HOME_WORDS = ("邮箱", "背包", "任务", "好友", "排行", "图鉴", "挂机", "商城")
EMBED_WORDS = ("淘宝网首页", "已买到的宝贝", "购物车", "淘宝游戏中心", "游戏中心", "操作说明", "回访入口")
PROMO = {"mute_until": 0.0, "noeff": 0, "hist": collections.deque(maxlen=16)}
PROMO_MUTE = 90             # 误判保护：点遮罩连续 2 次画面没变 / 90 秒内处理 ≥8 层 => 90 秒内不再按推广弹窗处理


def ocr_boxes(texts):
    for res, boxes in reversed(list(OCR_LAST)):
        if res is texts:
            return boxes
    return []


def looks_home(texts):
    """主页（可能被弹窗半遮住）：顶部/侧边入口文字 ≥2 个 => 不能盲点右下角（公会页签）"""
    j = ocr_join(texts or [])
    return sum(k in j for k in HOME_WORDS) >= 2


_EMB = {"t": 0.0, "v": False}


def embedded_page(texts):
    """游戏掉回嵌入式小窗口（calib 不对），只能点网页「全屏」，不能按画布坐标点。
    CDP 版：先看网页 DOM（有「全屏」按钮、没有「退出全屏」=> 嵌入式，2 秒缓存），再看画布 OCR 里的淘宝网页文字。"""
    if time.time() - _EMB["t"] > 2.0:
        try:
            fs = BR.fullscreen_state()
            _EMB["v"] = bool(fs.get("btn")) and not fs.get("exit")
        except Exception:
            _EMB["v"] = False
        _EMB["t"] = time.time()
    if _EMB["v"]:
        return True
    j = ocr_join(texts or [])
    return any(k in j for k in EMBED_WORDS)


def is_promo(texts, sc="other"):
    """推广弹窗（地府公会战/万鬼无限城…）：「今日不再提示」/ 单个「前往」/ 活动标题+弹窗正文。"""
    if not texts or sc == "battle" or time.time() < PROMO["mute_until"]:
        return False
    j = ocr_join(texts)
    if "双倍奖励" in j or "前往领取双倍" in j:
        return False                                   # 结算双倍确认框由原流程处理（点放弃）
    if embedded_page(texts):
        return False                                   # 嵌入式小窗口：先点「全屏」，不在网页上按画布坐标乱点
    if CHK_RE.search(j):
        return True
    if any(k in j for k in PROMO_TITLES) and any(k in j for k in PROMO_BODY):
        return True                                    # 主页本身有「地府公会战 万鬼无限城 归墟幻海」入口 => 只有标题不算
    return sum(1 for t, x, y in texts if t.strip() in PROMO_GO) == 1   # 单个「前往」按钮（任务列表有多个前往，不算）


def promo_checkbox(texts):
    """「今日不再提示」勾选框的点击点（画布比例）：文字框左边缘再往左 0.9 个字宽。
    只有 OCR 里确实有「今日不再提示」时才点（文字框缺失 => 后备 (0.404,0.797)）；
    只看到标题/「前往」时返回 None（只点遮罩）——主页 (0.40,0.80) 正好是「万鬼无限城」活动入口。"""
    boxes = ocr_boxes(texts)
    goes = [(x, y, w, h) for t, x, y, w, h in boxes if GO_RE.match(t.strip())]
    pt = None
    for t, x, y, w, h in boxes:
        m = CHK_RE.search(t)
        if not m:
            continue
        k = t.find("今日")
        s0 = k if 0 <= k <= m.start() else m.start()
        cw = w / max(1, len(t))
        pt = (x + s0 * cw - 0.9 * cw, y + h / 2)
        break
    if pt is None:
        if not CHK_RE.search(ocr_join(texts or [])):
            return None
        pt = POSITIONS["promo_check"]
    if not (0.05 < pt[0] < 0.95 and 0.3 < pt[1] < 0.97):
        pt = POSITIONS["promo_check"]
    for x, y, w, h in goes:                            # 绝不点到「前往」按钮
        if x - 0.03 <= pt[0] <= x + w + 0.03 and y - 0.02 <= pt[1] <= y + h + 0.02:
            log(f"勾选框位置 ({pt[0]:.3f},{pt[1]:.3f}) 太靠近「前往」，不点勾选框")
            return None
    return pt


def _promo_mute(why):
    PROMO["mute_until"] = time.time() + PROMO_MUTE
    PROMO["noeff"] = 0
    log(f"推广弹窗处理暂停 {PROMO_MUTE} 秒（{why}，可能是误判），交给其他恢复逻辑")


def handle_promo(cv, img=None, texts=None, layers=4):
    """检测并关闭推广弹窗（可叠加多层）：勾「今日不再提示」→ 短暂等待 → 点上方遮罩 (0.5,0.13)。绝不点「前往」。返回是否处理过。"""
    n = 0
    for i in range(layers):
        if STOP.is_set():
            break
        if img is None:
            img = cv.grab()
        if texts is None:
            texts = cv.ocr(img, tag="promo")
        if not is_promo(texts, cv.screen(img)):
            break
        n += 1
        j = ocr_join(texts)
        title = next((k for k in PROMO_TITLES if k in j), "推广")
        pt = promo_checkbox(texts)
        if pt:
            POSITIONS["_chk"] = pt
            cv.click("_chk", 0.7, f"推广弹窗（{title}，第 {n} 层）：勾「今日不再提示」@({pt[0]:.3f},{pt[1]:.3f})（不点「前往」）")
            img = cv.grab()                            # 勾选后的画面作为基准，判断遮罩点击是否关掉了弹窗
        cv.click("promo_blank", 0.1, f"推广弹窗（{title}，第 {n} 层）：点上方遮罩 (0.5,0.13) 关闭")
        changed = cv.wait_changed(img, 2.0)
        now = time.time()
        PROMO["hist"].append(now)
        PROMO["noeff"] = 0 if changed else PROMO["noeff"] + 1
        if PROMO["noeff"] >= 2:
            _promo_mute("点遮罩连续 2 次画面没变"); break
        if sum(1 for t in PROMO["hist"] if now - t < 90) >= 8:
            _promo_mute("90 秒内已处理 8 层"); break
        sleep(0.6)
        img = texts = None
    if n:
        event("promo", None, extra=f"layers={n}")
    return n > 0


def game_ui(cv, img=None, texts=None):
    """游戏界面已加载（主页/阵容/战斗或游戏内文字）。嵌入式小窗口不算（还要点「全屏」）。"""
    if embedded_page(texts):
        return False
    img = cv.grab() if img is None else img
    if cv.screen(img) in ("battle", "home", "lineup"):
        return True
    j = ocr_join(texts or [])
    if re.search(r"开始挑战|已通关|请领取奖励|召唤|第\s*\d{1,3}\s*关|不再提示", j):
        return True
    return looks_home(texts)


def clear_popups(cv, tries=3):
    """战斗中出现转盘/精英掉落/盲盒等弹窗时（暂停按钮被遮住），点空白处关闭。
    返回 True 表示已回到战斗画面。注意空白处避开弹窗底部的「观影免费/刷新」按钮。"""
    for i in range(tries):
        img = cv.grab()
        if cv.in_battle(img):
            return True
        if END_SEEN[0]:
            return False
        texts = cv.ocr(img, tag="popup")
        if is_end_screen(texts):
            END_SEEN[0] = True
            return False
        if not texts:
            time.sleep(0.5)
            continue                                   # 没确认到弹窗就不点
        if picker_open(texts):
            pick_card(cv); continue
        if is_promo(texts):
            handle_promo(cv, img, texts); continue
        if "随机事件" in ocr_join(texts):
            cv.click("top_mask", 0.05, "随机事件弹窗：点上方遮罩关闭（不点免费/视频）")
            cv.wait_changed(img, 1.5); continue
        if not close_info(cv, texts):
            cv.click("blank", 0.05, "检测到弹窗（转盘/掉落等），点击空白处关闭" if i == 0 else "")
        cv.wait_changed(img, 1.5)
    return cv.in_battle()


CLOSE_WORDS = ("关闭", "放弃", "取消", "确定", "确认", "知道了", "我知道了", "X", "×")


def close_ladder(cv, texts, step):
    """未知遮罩/弹窗的关闭阶梯（第 step 步，0..3）。从不点 免费/观影/视频（NEVER_CLICK）。"""
    k = step % 4
    if k == 0:
        cv.click("nav_blank", 0.05, "关闭阶梯 1/4：点空白 (0.80,0.35)")
    elif k == 1:
        cv.click("top_mask", 0.05, "关闭阶梯 2/4：点上方遮罩 (0.5,0.13)")
    elif k == 2:
        if looks_home(texts):
            log("关闭阶梯 3/4：像主页（右下是公会页签），跳过右下「返回」")
        else:
            cv.click("info_back", 0.05, "关闭阶梯 3/4：点右下「返回」(0.882,0.785)")
    else:
        if not click_text(cv, texts, CLOSE_WORDS, 0.05):
            log("关闭阶梯 4/4：没有可点的 关闭/放弃/取消/X 文字")


def ladder_alert(cv, texts, why):
    if FREEZE.get("alerted"):
        return
    FREEZE["alerted"] = True
    shot = save_shot(cv, "alert")
    write_alert(why, shot, texts)
    event("alert", None, texts=texts, extra=f"{why} shot={shot}")


INFO_WORDS = ("神将信息", "姓名", "怪物信息", "弱点", "抗性", "特点")


def close_info(cv, texts):
    """神将信息/怪物信息面板：点右下角「返回」(0.882,0.785) 关闭。返回 True 表示处理了"""
    j = ocr_join(texts)
    if "神将信息" in j or ("姓名" in j and "返回" in j):
        cv.click("info_close", 0.05, "神将信息面板：点右下角「返回」关闭")
        return True
    return False


def is_end_screen(texts):
    j = ocr_join(texts)
    return bool(classify_end(texts)) or "双倍奖励仅有" in j or "前往领取双倍" in j


END_SEEN = [False]
NEVER_CLICK = ("双倍", "观影", "刷新", "领取", "充值", "购买", "分享", "关注", "看广告", "免费", "视频", "广告")


def click_text(cv, texts, words, wait=2.0):
    """点击 OCR 识别到的按钮文字（完全匹配 words 之一）。绝不点 NEVER_CLICK 中的文字。"""
    for t, x, y in texts:
        tt = t.strip()
        if tt in words and not any(k in tt for k in NEVER_CLICK):
            POSITIONS["_txt"] = (x, y)
            cv.click("_txt", wait, f"点击文字按钮「{tt}」@({x:.2f},{y:.2f})")
            return True
    return False


def ocr_join(texts, y0=0.0, y1=1.0):
    return " | ".join(t for t, x, y in texts if y0 <= y <= y1 and not re.match(r"^\d{6,}|^P\d{5}", t))


def read_level(texts):
    for t, x, y in texts:
        m = re.search(r"第\s*(\d{1,3})\s*关", t)
        if m:
            return int(m.group(1))
    return None


def read_coins(texts):
    """战斗画面左上角金币数（钞票图标右侧，约 (0.14, 0.21)）"""
    for t, x, y in texts:
        if 0.08 <= x <= 0.22 and 0.18 <= y <= 0.23 and re.fullmatch(r"\d{1,7}", t.strip()):
            return int(t)
    return None


ALERT_STOP = ("其他地方登", "重新登录", "登录失效", "请登录", "请上阵5个神将", "请上阵5个")


def check_alerts(texts):
    """发现需要登录 / 阵容不足等提示 => 立即停止脚本"""
    for t, x, y in texts:
        for k in ALERT_STOP:
            if k in t:
                save_shot_raw("alert")
                log(f"检测到提示「{t}」，脚本停止，请人工处理。")
                write_alert(f"检测到提示「{t}」，脚本停止")
                STOP.set()
                raise KeyboardInterrupt
    return False


WIN_WORDS = ("挑战成功", "战成功", "胜利", "战斗成功", "获得奖励", "奖励预览")
LOSE_WORDS = ("失败", "再接再厉", "被攻破", "重新挑战", "再来一次", "战败", "失守")


def classify_end(texts):
    j = ocr_join(texts).replace("升级成功", "").replace("召唤成功", "").replace("双倍奖励仅有", "")
    if any(k in j for k in ("上传失败", "离开房间", "网络", "重连")):
        return "中断"
    if any(k in j for k in LOSE_WORDS):
        return "负"
    if any(k in j for k in WIN_WORDS):
        return "胜"
    return None


STATE_FILE = "state.json"


def load_state():
    try:
        with open(os.path.join(HERE, STATE_FILE), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"fails": {}}


def save_state(st):
    with open(os.path.join(HERE, STATE_FILE), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


FIX_LINEUP = [False]
SHOT_DIR = "shots"
SHOT_EVERY = 10             # 每隔几秒存一张游戏截图（供分析优化）
SHOT_KEEP = 600             # 最多保留多少张


SHOT_REGION = {"picker_stuck": (0.0, 0.5, 1.0, 1.0), "picker": (0.0, 0.5, 1.0, 1.0),   # 选卡 => 下半屏
               "popup": (0.0, 0.2, 1.0, 0.8)}                                         # 对话框 => 中间


def save_shot(cv, tag="t"):
    """只截游戏画布（或相关区域），缩到 280 宽、灰度、JPEG 质量 50（省流量/省 token）"""
    if not game_visible():
        return                                         # 不截用户自己的画面
    try:
        d = os.path.join(HERE, SHOT_DIR); os.makedirs(d, exist_ok=True)
        img = cv.grab()
        r = SHOT_REGION.get(tag)
        if r:
            W0, H0 = img.size
            img = img.crop((int(r[0] * W0), int(r[1] * H0), int(r[2] * W0), int(r[3] * H0)))
        img = img.convert("L").resize((280, max(1, int(280 * img.height / img.width))))
        path = os.path.join(d, f"{datetime.now():%m%d_%H%M%S}_{tag}.jpg")
        img.save(path, quality=50)
        files = sorted(os.listdir(d))
        for f in files[:-SHOT_KEEP]:
            os.remove(os.path.join(d, f))
        return path
    except Exception as e:
        log(f"截图失败: {e}")


CV = [None]
NAV_COOLDOWN = {"chest": 2.5, "start": 2.5, "challenge": 3.0, "nav_blank": 2.0, "top_mask": 2.0, "info_back": 2.0}
LAST_CLICK = {}
RECENT_CLICKS = collections.deque(maxlen=15)
WD_IGNORE = ("summon", "card1", "card2", "card3", "bed_up", "upgrade", "blank", "_u", "_t", "_b")   # 战斗中正常的重复点击（_t=点床/门，_b=点建造格子）
CLICK_WD = {"flag": False}


def save_shot_raw(tag):
    if CV[0] is not None:
        save_shot(CV[0], tag)


EVENTS_FILE = "events.log"
ALERT_FILE = "ALERT"
FREEZE_THR = 0.3            # 1 秒前后两帧「中间 60% 区域」（80x160 灰度裁出 48x96）平均差 < 0.3 视为「画面没动」
FREEZE_BOX = (16, 32, 64, 128)   # 只看画布中间（忽略边缘动画、顶部计时器、底部按钮）
FREEZE_SEC = 5              # 连续 5 秒没动 => 异常（对话框/面板/卡死）
FREEZE = {"since": None, "flag": False, "handled_at": None, "alerted": False}


def event(kind, cv=None, texts=None, shot=False, extra=""):
    """事件日志 events.log（制表符分隔）：时间  关卡  界面  事件  附加信息  OCR文字"""
    sc = ""
    try:
        if cv is not None:
            sc = cv.screen()
            if texts is None and kind in ("freeze", "alert", "stuck", "popup"):
                texts = cv.ocr(tag="ev")
    except Exception:
        pass
    line = "\t".join([f"{datetime.now():%Y-%m-%d %H:%M:%S}", f"level={BATTLE.get('level')}", f"screen={sc}",
                      f"event={kind}", extra.replace("\t", " "), ocr_join(texts or [])[:240].replace("\t", " ")])
    try:
        with open(os.path.join(HERE, EVENTS_FILE), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if shot and cv is not None:
        save_shot(cv, kind)


def write_alert(reason, shot="", texts=None):
    with open(os.path.join(HERE, ALERT_FILE), "w", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} level={BATTLE.get('level')} {reason}\n")
        f.write(f"shot={shot or ''}\n")
        f.write(f"ocr={ocr_join(texts or [])[:300]}\n")
    log(f"!!! 写入 ALERT：{reason}")
    notify.notify(f"第 {BATTLE.get('level')} 关：{reason}", title="妖怪金手指 需要处理")


def start_monitor(cv):
    """后台线程：每秒比较一次缩小的画面。连续 5 秒几乎不变 => 置 FREEZE['flag'] 让主线程恢复；
    恢复后 8 秒仍不变 => 写 ALERT；画面重新动起来 => 删除 ALERT。只截图不点击。"""
    def loop():
        prev = None
        while not STOP.is_set():
            STOP.wait(1.0)
            try:
                if not game_visible():
                    prev = None; FREEZE["since"] = None
                    continue
                img = cv.grab_fast().convert("L").resize((80, 160)).crop(FREEZE_BOX)
            except Exception:
                continue
            now = time.time()
            if prev is not None and ImageStat.Stat(ImageChops.difference(prev, img)).mean[0] < FREEZE_THR:
                if FREEZE["since"] is None:
                    FREEZE["since"] = now - 1
                if now - FREEZE["since"] >= FREEZE_SEC and FREEZE["handled_at"] is None:
                    FREEZE["flag"] = True
                ha = FREEZE["handled_at"]
                if ((ha and now - ha > 8) or now - FREEZE["since"] > FREEZE_SEC + 15) and not FREEZE["alerted"] \
                        and not PAGE["busy"] and not cv.in_battle():   # 只在有弹窗/遮罩时写 ALERT；网页恢复中不写
                    FREEZE["alerted"] = True
                    shot = save_shot(cv, "alert")
                    try:
                        tx = cv.ocr(tag="alert")
                    except Exception:
                        tx = []
                    write_alert(f"画面中部冻结 {now - FREEZE['since']:.0f} 秒，自动恢复无效", shot, tx)
                    event("alert", None, texts=tx, extra=f"frozen {now - FREEZE['since']:.0f}s after recovery shot={shot}")
            else:
                if FREEZE["alerted"]:
                    try:
                        os.remove(os.path.join(HERE, ALERT_FILE))
                    except OSError:
                        pass
                    log("画面恢复变化，已删除 ALERT")
                    event("recovered", None)
                FREEZE.update(since=None, flag=False, handled_at=None, alerted=False)
            prev = img
    threading.Thread(target=loop, daemon=True).start()
    log(f"画面冻结监测已启动（{FREEZE_SEC} 秒不变 => 自动恢复；仍不变 => 写 {ALERT_FILE}）；不再定时截图")


def handle_freeze(cv):
    """主线程：画面冻结 5 秒后的 OCR 恢复。返回 'end' 表示看到结算画面。"""
    FREEZE["flag"] = False
    FREEZE["handled_at"] = time.time()
    img = cv.grab()
    sc = cv.screen(img)
    texts = cv.ocr(img, tag="freeze")
    event("freeze", cv, texts, shot=True, extra=f"since={time.time() - (FREEZE['since'] or time.time()):.0f}s")
    log(f"画面 {FREEZE_SEC} 秒没有变化（{sc}），尝试恢复。[画面文字] {ocr_join(texts)[:160]}")
    check_alerts(texts)
    if is_promo(texts, sc):
        handle_promo(cv, img, texts); return ""
    if close_info(cv, texts):
        return ""
    if picker_open(texts):
        pick_card(cv); return ""
    if "随机事件" in ocr_join(texts):
        cv.click("top_mask", 0.5, "冻结恢复：随机事件，点上方遮罩"); return ""
    if is_end_screen(texts):
        return "end"
    if sc in ("home", "lineup"):
        return ""                                      # 主页/阵容页由导航流程处理
    if click_text(cv, texts, ("确定", "确认", "知道了", "我知道了", "关闭"), 0.5):
        return ""
    if sc == "battle" or texts:
        cv.click("blank" if sc == "battle" or BATTLE.get("in_battle") else "nav_blank", 0.5, "冻结恢复：点空白")
    return ""


_VIS = {"t": 0.0, "ok": True, "warned": False, "url": "", "login_alert": False}


def game_visible(max_age=1.5):
    """CDP 版：后台 Chrome 当前页面是游戏页（game.taobao.com）。和你在不在用电脑、窗口在不在前面无关。
    掉到登录页 => 写 ALERT（需要重新扫码：./venv/bin/python cdp_qr_login.py），登录回来后自动删除。"""
    if time.time() < _VIS.get("block_until", 0):
        return False                                   # 刚在画布上 OCR 到网页内容
    if time.time() - _VIS["t"] < max_age:
        return _VIS["ok"]
    try:
        url = BR.url()
        ok = "game.taobao.com" in url
    except Exception as e:
        url, ok = f"(连接失败 {e})", False
    _VIS.update(t=time.time(), ok=ok, url=url)
    if not ok and "login" in url and not _VIS["login_alert"]:
        _VIS["login_alert"] = True
        write_alert("后台 Chrome 掉到了淘宝登录页，需要重新扫码：cd ~/yaoguai && ./venv/bin/python cdp_qr_login.py")
    elif ok and _VIS["login_alert"]:
        _VIS["login_alert"] = False
        try:
            os.remove(os.path.join(HERE, ALERT_FILE))
        except OSError:
            pass
        log("已重新登录，删除 ALERT")
    return ok


def wait_game_visible():
    while not game_visible(0):
        if not _VIS["warned"]:
            _VIS["warned"] = True
            log(f"后台 Chrome 不在游戏页（{_VIS['url'][:80]}），暂停点击/截图，等它回到游戏页……")
        sleep(5)
    if _VIS["warned"]:
        _VIS["warned"] = False
        log("后台 Chrome 回到游戏页，继续")
        time.sleep(1)


def focus_browser():
    """不再抢焦点（会打断用户操作）；只等游戏标签回到前台"""
    wait_game_visible()


def summon(cv):
    if not clear_popups(cv):
        return
    if CHECK_PRICE_RED and cv.price_is_red():
        log("召唤价格为红色（金币不足），跳过召唤")
        return
    cv.click("summon", 1.2, "召唤")
    BATTLE["summons"] += 1
    pick_card(cv)


def picker_open(texts):
    j = ocr_join(texts)
    return "请点击选择" in j or "任一选项" in j


def pick_card(cv):
    """选卡界面（「请点击选择上方任一选项」，1~3 张卡）：依次试 3 张卡时第 1 张 / 2 张卡时第 1 张 / 1 张卡（居中），
    每点一次都用 OCR 确认选卡界面是否关闭。"""
    t = cv.ocr(tag="pick")
    for _ in range(3):                                 # 选卡界面有时 2–3 秒才弹出（以前只等 0.6 秒，一天误报 9 次）
        if picker_open(t):
            break
        time.sleep(0.6); t = cv.ocr(tag="pick")
    if not picker_open(t):
        log(f"召唤后没看到选卡界面（等了约 3 秒）。[画面文字] {ocr_join(t, 0.2, 0.95)[:100]}")
        return False
    order = ["card1", "card2", "card3"]
    first = first_card_slot(t)                         # patch26：按 OCR 卡名定位最左边那张卡（=第一张），先点它
    if first:
        order.remove(first); order.insert(0, first)
    for name in order:
        cv.click(name, 0.9)
        t = cv.ocr(tag="pick")
        if not picker_open(t):
            log(f"已选择卡牌（{name}）")
            return True
    log("选卡界面点了 3 个位置仍未关闭")
    event("picker_stuck", cv, t, shot=True)
    return False


def first_card_slot(texts):
    """patch26：选卡界面卡名在 y≈0.82–0.86（推荐/特性行除外）。取最左边卡名的 x，吸附到已知的 3 个第一张卡位置之一；
    吸附不上（偏差 >0.07）就返回 None，退回旧的 card1→card2→card3 顺序。实测 s12.png：2 张卡时卡名 x≈0.33 / 0.66。"""
    xs = [x for t, x, y in texts if 0.80 <= y <= 0.865 and len(t.strip()) >= 2 and not re.search(r"\d|推荐|请点击|召唤|建造|自动", t)]
    if not xs:
        return None
    x0 = min(xs)
    name, fx = min(((n, POSITIONS[n][0]) for n in ("card1", "card2", "card3")), key=lambda a: abs(a[1] - x0))
    if abs(fx - x0) > 0.07:
        return None
    if (name == "card3" and len(xs) != 1) or (name == "card2" and len(xs) != 2):
        return None                                    # 卡名数量和布局对不上（可能漏识别了左边的卡）=> 走旧顺序，保证不会点到第 2/3 张
    return name


BATTLE = {"level": None, "summons": 0, "end_texts": ""}


def reset_battle(level):
    BATTLE.clear()
    BATTLE.update(level=level, summons=0, end_texts="", gens=0, bad_tiles=[], gen_labels=[], bed_pos=None)


AUTO_BUILD = True            # 门升不动（资源不足=缺电）且金币够时，自动建发电机


POLL = 1.0                   # 战斗中轮询间隔（秒）：原 0.5，改 1 秒降低截图开销（结算/弹窗最多晚 0.5 秒发现）
TIMING = {"end": None}       # 记录结算出现的时刻，用来统计「结算→下一关开战」耗时


def end_battle(cv, texts, reason):
    TIMING["end"] = time.time()
    BATTLE["in_battle"] = False
    event("battle_end", cv, texts, shot=True, extra=reason)
    BATTLE["end_texts"] = ocr_join(texts)
    log(f"{reason}。[结算画面文字] {BATTLE['end_texts'][:200]}")   # patch26：去掉重复的 save_shot("end")（event 已存 battle_end）
    check_alerts(texts)
    END_SEEN[0] = False
    if page_bad():
        return "crash"
    return classify_end(texts) or ("crash" if "按结算处理" in reason else "结束")


def battle_round(cv, n, t0, summon_every):
    """每 LOOP_INTERVAL 秒一轮：读 HUD、升床/升门、建发电机、召唤"""
    texts = cv.ocr(tag="hud")
    check_alerts(texts)
    if picker_open(texts):                             # 选卡界面没关（游戏暂停中）=> 先选卡
        pick_card(cv)
        texts = cv.ocr(tag="hud")
    lv = read_level(texts)
    if lv:
        BATTLE["level"] = lv
    coins = read_coins(texts)
    dt = ""
    nd = BATTLE.get("need")
    if nd and BATTLE.get(nd[1] + "_lv", 0) >= nd[2]:
        log(f"前置已满足（{nd[1]} {BATTLE.get(nd[1] + '_lv')} 级 ≥ {nd[2]}）")
        BATTLE["need"] = nd = None
    if nd and nd[1] in ("door", "bed"):                # 先把前置建筑升到要求等级
        fn = door_upgrade if nd[1] == "door" else bed_upgrade
        for _ in range(4):
            r = fn(cv) or ""
            if nd[1] == "door":
                dt = r
            if "升级成功" not in r or BATTLE.get(nd[1] + "_lv", 0) >= nd[2]:
                break
        if BATTLE.get(nd[1] + "_lv", 0) >= nd[2]:
            BATTLE["need"] = None
            log(f"前置已满足，继续升{'床铺' if nd[0] == 'bed' else '大门'}")
        else:
            BATTLE["need_tries"] = BATTLE.get("need_tries", 0) + 1
            if BATTLE["need_tries"] >= 4:                # 4 轮都没升上去（找不到/没钱）=> 放开，下轮重新试床
                BATTLE["need"] = None; BATTLE["need_tries"] = 0
    else:
        for _ in range(3):                             # 床=经济，钱够就连升（最多 3 级/轮）
            if "升级成功" not in (bed_upgrade(cv) or ""):
                break
    if not BATTLE.get("need"):
        dt = door_upgrade(cv) or dt
    if END_SEEN[0]:
        return
    door_stuck = "资源不足" in (dt or "") or (dt == "无箭头" and BATTLE.get("door_lv", 0) >= 6 and not BATTLE.get("door_max"))
    gens_full = BATTLE.get("gens", 0) >= BATTLE.get("gen_max", MAX_GENS)   # 发电机满了 => 有电就建招财猫（+10 金/秒）
    if AUTO_BUILD and ((door_stuck and (coins or 0) >= 350) or gens_full):
        build_room(cv)
    hp = BATTLE.get("door_hp", 1.0)
    br = BATTLE.get("dw", {}).get("ratio")
    if br is not None:
        hp = min(hp, br)                               # 门血条实时读数（面板读数可能是旧的）
    dwx = BATTLE.get("dw", {})
    if (dwx.get("ratio") is not None and dwx["ratio"] < 0.95) or dwx.get("modes", 0) or BATTLE.get("door_hp", 1.0) < 0.95:
        BATTLE["hurt"] = True                          # patch26：本关门掉过血（粘滞）
    periodic = PERIODIC_SUMMON == "always" or summon_every < SUMMON_EVERY or BATTLE.get("hurt")
    if n % summon_every == 0 and periodic:
        summon(cv)
    elif hp < 0.5:                                     # 门血不到一半 => 额外召唤
        log(f"门血量 {hp:.0%}，额外召唤一次")
        summon(cv)
    if END_SEEN[0]:
        return
    if os.path.exists(os.path.join(HERE, "explore_build")):
        explore_build(cv)
    dw = BATTLE.get("dw", {})
    dbs = (f"，门血条 {dw['ratio']:.0%}" if dw.get("ratio") is not None else "") + ("，防守中" if dw.get("mode") else "")
    log(f"战斗进行中……第 {BATTLE['level']} 关，已 {int(time.time() - t0)} 秒，金币 {coins}，召唤 {BATTLE['summons']} 次{dbs}")


def restore_battle_fullscreen(cv):
    """战斗中游戏掉回淘宝网页小窗口（14:06 第 69 关）：点网页「全屏」，等画面回来，还在战斗就接着打。返回是否恢复成功"""
    log("战斗中游戏掉回网页小窗口，点「全屏」恢复")
    if not restore_fullscreen(tries=2, activate=False, quiet=True):
        return False
    cv.autocalib("战斗中恢复全屏")
    for _ in range(10):
        sleep(1)
        if cv.in_battle():
            PAGE.update(kind=None)
            _EMB["t"] = 0.0
            log("已恢复全屏，继续本关战斗")
            event("fullscreen_restored", cv)
            return True
    return False


def wait_battle(cv, t0, summon_every=SUMMON_EVERY):
    """战斗主循环：每 1 s 看一眼暂停按钮；弹窗（转盘等）立刻点空白关闭；结算画面立刻返回。"""
    n = 0
    next_round = time.time() + 1
    popup_t, blanks = None, 0
    pop_ev = None                                      # patch26：待写的 popup 事件（点完再写，弹窗期间游戏暂停）
    while time.time() - t0 < MAX_BATTLE_SEC:
        sleep(POLL)
        wait_game_visible()
        if FREEZE["flag"] and handle_freeze(cv) == "end":
            END_SEEN[0] = True
        if END_SEEN[0]:                                # 升级/召唤过程中已看到结算画面
            return end_battle(cv, cv.ocr(tag="end"), "结算画面出现（操作中发现）")
        img = cv.grab()
        if cv.in_battle(img):
            if pop_ev is not None:
                event("popup", cv, pop_ev, shot=False); pop_ev = None
            if popup_t:
                log(f"弹窗已关闭，从出现到关闭用时 {time.time() - popup_t:.1f} 秒（点空白 {blanks} 次）")
                popup_t, blanks = None, 0
                door_watch_pause(1.0)                  # 弹窗淡出动画期间不读门血条
            if time.time() >= next_round:
                n += 1
                battle_round(cv, n, t0, summon_every)
                door_watch_pause(1.5)                  # 刚点过升级面板/选卡 => 丢掉旧样本
                next_round = time.time() + LOOP_INTERVAL
                continue
            door_watch(cv, img)                        # 门血条 + 防守模式召唤
            continue
        # 暂停按钮不见了：转盘/掉落弹窗，或者结算画面
        if embedded_page([]) and restore_battle_fullscreen(cv):
            continue                                   # 战斗中掉回网页小窗口 => 点「全屏」接着打（不作废本关）
        texts = cv.ocr(img, tag="poll")
        if popup_t is None:
            popup_t = time.time()
            pop_ev = texts                             # patch26：事件/截图挪到点完之后（弹窗期间游戏暂停，先关弹窗）
        if page_bad() and PAGE["kind"] == "page" and restore_battle_fullscreen(cv):
            continue
        if page_bad():                                 # 标签页崩溃 / 全屏恢复不了 => 本关作废（crash，不算失败）
            r = end_battle(cv, texts, f"战斗中网页异常（{PAGE['kind']}），本关记为 crash")
            page_recover(cv, "战斗中网页异常")
            return r
        if not texts:
            continue                                   # 被遮挡/OCR 失败：下一轮再看
        if is_end_screen(texts):
            return end_battle(cv, texts, f"结算画面出现（暂停按钮消失 {time.time() - popup_t:.1f} 秒后识别）")
        j = ocr_join(texts)
        if "即将行动" in j or "已出现" in j:
            continue                                   # Boss 出场提示横幅，等它自己消失
        if blanks in (3, 6):
            cv.autocalib("暂停按钮持续不见")
        if blanks >= 8 and time.time() - popup_t > 12:
            return end_battle(cv, texts, "暂停按钮消失超过 12 秒且无法关闭，按结算处理")
        if picker_open(texts):
            pick_card(cv); continue
        if is_promo(texts):
            handle_promo(cv, img, texts); continue
        if "随机事件" in ocr_join(texts):
            cv.click("top_mask", 0.05, "随机事件弹窗：点上方遮罩关闭（不点免费/视频）")
            cv.wait_changed(img, 1.5); continue
        if not close_info(cv, texts):
            cv.click("blank", 0.05, f"弹窗（{j[:30]}）：点空白关闭" if blanks == 0 else "")
            if FAST_POPUP:
                blanks += fast_reclick(cv)             # patch26：像素轮询暂停按钮，转盘停了还没关就再点一次空白
        blanks += 1
        if pop_ev is not None and not cv.in_battle():
            event("popup", cv, pop_ev, shot=True); pop_ev = None
        if not (FAST_POPUP and cv.in_battle()):
            cv.wait_changed(img, 1.5)
    log("达到最长战斗时间，执行结算流程")
    return "超时结算"


def fast_reclick(cv, tries=3, gap=0.6):              # 原 1.2：日志里 44 次弹窗有 39 次要点 2 下
    """patch26：点空白后每 0.3 s 只看暂停按钮像素（不 OCR）；gap 秒内没回到战斗且画面上没有结算/选卡迹象 => 再点一次空白。
    数据：145 次弹窗里 103 次要点 2 次（中位 5.8 s，点 1 次的 3.6 s）——第 1 次点在转盘转动中被忽略。返回额外点击次数。"""
    extra = 0
    for _ in range(tries):
        t = time.time()
        while time.time() - t < gap:
            time.sleep(0.3)
            if cv.in_battle():
                return extra
        if END_SEEN[0]:
            return extra
        img = cv.grab()
        if cv.in_battle(img):
            return extra
        tx = cv.ocr(img, tag="poll")                  # 再点之前确认仍是普通弹窗（不是结算/选卡/推广/随机事件）
        j = ocr_join(tx)
        if not tx or is_end_screen(tx) or picker_open(tx) or is_promo(tx) or "随机事件" in j or "即将行动" in j or "已出现" in j:
            return extra
        if close_info(cv, tx):
            return extra
        cv.click("blank", 0.05); extra += 1
    return extra


def try_speed_2x(cv):
    """patch26（默认关闭）：开局点一次「X1」切 2 倍速。点前 OCR 确认按钮是 X1；点后如出现月卡/充值/开通/购买等文字 => 点上方遮罩关闭，本次运行不再尝试。"""
    if not SPEED_2X or BATTLE.get("speed_done") or SPEED_STATE["blocked"]:
        return
    BATTLE["speed_done"] = True
    t = cv.ocr(tag="speed")
    near = [tt for tt, x, y in t if abs(x - SPEED_POS[0]) < 0.06 and abs(y - SPEED_POS[1]) < 0.03]
    if not any(re.fullmatch(r"[Xx×]\s*1", s.strip()) for s in near):
        return                                         # 已是 X2 或没看到按钮 => 不点
    POSITIONS["_speed"] = SPEED_POS; cv.click("_speed", 0.8, "倍速：点「X1」")
    t2 = cv.ocr(tag="speed")
    j = ocr_join(t2)
    if re.search(r"月卡|充值|开通|购买|特权|解锁|元|观看|广告", j) or not cv.in_battle():
        SPEED_STATE["blocked"] = True
        log(f"倍速需要付费/广告（{j[:60]}），关闭弹窗，本次运行不再尝试")
        cv.click("top_mask", 0.6)
        clear_popups(cv)
        return
    log("倍速：" + ("已切到 X2" if re.search(r"[Xx×]\s*2", j) else f"点后未见 X2（{j[:40]}）"))


SPEED_STATE = {"blocked": False}


def explore_build(cv):
    """一次性探索「建造机关」界面：打开、截图+OCR 记录、点空白关闭。由 ~/yaoguai/explore_build 文件触发。"""
    try:
        os.remove(os.path.join(HERE, "explore_build"))
    except OSError:
        pass
    cv.click("build", 1.5, "探索：打开「建造机关」")
    save_shot(cv, "build")
    texts = cv.ocr(tag="build")
    log("[建造机关文字] " + " | ".join(f"{t}@({x:.2f},{y:.2f})" for t, x, y in texts)[:900])
    cv.click("blank", 1.0)
    clear_popups(cv)
    save_shot(cv, "buildclose")


def play_battle(cv, t0, opening=True, summon_every=SUMMON_EVERY):
    if opening:
        log("开局：升床 + 升门 + 召唤")
        sleep(3)
        try_speed_2x(cv)
        for _ in range(EARLY_BED_TRIES):
            bed_upgrade(cv)
        for _ in range(EARLY_DOOR_TRIES):
            door_upgrade(cv)
        for _ in range(EARLY_SUMMONS):
            summon(cv)
            clear_popups(cv)
    return wait_battle(cv, t0, summon_every)


LINEUP_WORDS = ("同步主队", "预设", "筛选")


# ---------------- 房间研发（用户规则：每打 10 关升级 1 次）----------------
# 15:55 手动实测（1 倍画布比例）：主页底部「主角」(0.58,0.98) → 「房间研发」(0.22,0.64) → 「一键升级」(0.52,0.96)
# （研发等级 15–20 → 48–52）→ 右下角返回图标 (0.90,0.99) 回到主角页 → 底部「主线」(0.42,0.975) 回主页。
RESEARCH_EVERY = 10
R_POS = {"hero_tab": (0.58, 0.98), "research": (0.22, 0.64), "one_key": (0.52, 0.96),
         "r_back": (0.90, 0.99), "main_tab": (0.42, 0.975)}


def research_due(st):
    w = int(st.get("last_win") or 0)
    return w > 0 and w - int(st.get("research_at", 0)) >= RESEARCH_EVERY and st.get("research_try") != w


def _find(texts, words, y0=0.0, y1=1.0):
    for t, x, y in texts:
        if y0 <= y <= y1 and any(w in t for w in words):
            return (x, y)
    return None


def _tap(cv, pos, wait=2.0, note=""):
    POSITIONS["_r"] = pos
    cv.click("_r", wait, note)


def _research_levels(texts):
    """研发面板左侧一列等级数字（x≈0.08）"""
    return sorted(int(t) for t, x, y in texts if x < 0.15 and 0.2 < y < 0.92 and re.fullmatch(r"\d{1,3}", t.strip()))


def _is_home(texts):
    j = ocr_join(texts)
    return bool(re.search(r"开始挑战|已通关|请领取奖励", j)) or looks_home(texts)


def do_research(cv, st):
    """主页 → 主角 → 房间研发 → 一键升级 → 右下角返回 → 主线。每个通关数只试一次（失败也不反复点）。"""
    w = int(st.get("last_win") or 0)
    st["research_try"] = w; save_state(st)
    log(f"房间研发：已通关第 {w} 关（上次研发在第 {st.get('research_at', 0)} 关），按规则每 {RESEARCH_EVERY} 关升级一次")
    t = cv.ocr(tag="research")
    _tap(cv, _find(t, ("主角", "生角"), 0.93) or R_POS["hero_tab"], 2.5, "房间研发：点底部「主角」")
    t = cv.ocr(tag="research")
    p = _find(t, ("房间研发",), 0.5, 0.75)
    if not p:
        log(f"房间研发：主角页没找到「房间研发」，放弃本次。[画面文字] {ocr_join(t)[:100]}")
        _research_back(cv)
        return False
    _tap(cv, p, 2.5, "房间研发：点「房间研发」")
    t = cv.ocr(tag="research")
    before = _research_levels(t)
    btn = _find(t, ("一键升级", "键升级", "键开级"), 0.9)
    if not btn:
        log(f"房间研发：没找到「一键升级」，放弃本次。[画面文字] {ocr_join(t)[:100]}")
        _research_back(cv)
        return False
    _tap(cv, btn, 2.5, "房间研发：点「一键升级」")
    t = cv.ocr(tag="research")
    after = _research_levels(t)
    toast = ocr_join(t, 0.2, 0.9)
    log(f"房间研发：一键升级完成，研发等级 {before[:1] + before[-1:]} → {after[:1] + after[-1:]}"
        + (f"（提示：{toast[:40]}）" if "不足" in toast else ""))
    st["research_at"] = w; save_state(st)
    event("research", cv, t, extra=f"win={w} {before[-1:]}->{after[-1:]}")
    _research_back(cv)
    return True


def _research_back(cv):
    """研发面板右下角返回图标 → 主角页 → 底部「主线」回主页（最多试 3 次）"""
    for _ in range(3):
        t = cv.ocr(tag="research")
        if _is_home(t):
            log("房间研发：已回到主页，继续主线挑战")
            return True
        if _find(t, ("房间研发",), 0.08, 0.16):        # 还在研发面板（标题在顶部）
            _tap(cv, R_POS["r_back"], 2.0, "房间研发：点右下角返回")
            continue
        _tap(cv, _find(t, ("主线",), 0.93) or R_POS["main_tab"], 2.5, "房间研发：点底部「主线」")
    log("房间研发：3 次没回到主页，交给导航流程处理")
    return False


def to_next_battle(cv, st, timeout=150):
    """从结算画面/主页/阵容页走到下一场战斗开始。每次只点一下，点完等画面变化再判断。
    返回 True=已进入战斗。阵容页上只点「挑战」。"""
    t_start = time.time()
    chest_clicks, unknown, last_log, ladder = 0, 0, "", 0
    last_sig, sig_t = None, time.time()
    while time.time() - t_start < timeout:
        wait_game_visible()
        if FREEZE["flag"]:
            handle_freeze(cv)
        img = cv.grab()
        if cv.in_battle(img):
            page_ok()
            return True
        sc = cv.screen(img)
        texts = cv.ocr(img, tag="nav")
        if page_bad():                                 # 淘宝网页文字 / 崩溃页 => 全屏 / 重载
            page_recover(cv, "导航中看到网页/崩溃页文字")
            unknown = 0; ladder = 0; sig_t = time.time(); continue
        if texts and embedded_page(texts) and restore_fullscreen(tries=1, activate=False, quiet=True):
            wait_loaded(cv, FS_WAIT); cv.autocalib("全屏后")       # patch25：嵌入式小窗口 => 点网页「全屏」，等加载
            unknown = 0; ladder = 0; sig_t = time.time(); continue
        if not texts and not game_visible():
            continue
        check_alerts(texts)
        j = ocr_join(texts)
        if is_promo(texts, sc):                        # patch25：推广弹窗（地府公会战/万鬼无限城）=> 勾不再提示 + 点遮罩
            handle_promo(cv, img, texts)
            unknown = 0; ladder = 0; sig_t = time.time(); continue
        sig = (sc, j[:40])
        if sig != last_sig:
            last_sig, sig_t = sig, time.time()
        if CLICK_WD["flag"] or time.time() - sig_t > 60:      # 看门狗：同一按钮狂点 / 60 秒界面没变
            why = "同一按钮 ≥12/15 次" if CLICK_WD["flag"] else "60 秒界面没有变化"
            log(f"看门狗（{why}）：走一轮关闭阶梯")
            CLICK_WD["flag"] = False; RECENT_CLICKS.clear(); sig_t = time.time()
            for k in range(4):
                close_ladder(cv, cv.ocr(tag="wd"), k); cv.wait_changed(img, 2.0)
            if not cv.wait_changed(img, 0.5):
                ladder_alert(cv, cv.ocr(tag="alert"), f"看门狗（{why}）：恢复链后画面未变")
            continue
        if sc == "lineup" or (sum(k in j for k in LINEUP_WORDS) >= 2 and "挑战" in j):
            lv = read_level(texts) or level_fallback(None, st)
            if lv:
                BATTLE["level"] = lv
            if FIX_LINEUP[0]:
                FIX_LINEUP[0] = False
                if not cv.card_checked("fixcard"):
                    cv.click("fixcard", 1.5, "恢复被误点掉的第 5 个神将（罗刹女）")
                    save_shot(cv, "lineupfix")
                else:
                    log("罗刹女已在阵容中，无需恢复")
            cv.click("challenge", 0.05, f"阵容页（第 {BATTLE['level']} 关）：点击「挑战」（不改阵容）")
            cv.wait_changed(img, 3.0)
            check_alerts(cv.ocr(tag="lineup2"))        # 「请上阵5个神将」=> 停止
            for k2 in range(90):                       # Boss 出场倒计时 ~13 s，期间只等不点
                sleep(0.3)
                im2 = cv.grab()
                if cv.in_battle(im2):
                    page_ok()
                    return True
                if cv.screen(im2) == "lineup":
                    break                              # 还在阵容页：回到外层重新判断
                if k2 % 4 != 3:
                    continue                           # patch26：只看暂停按钮像素，每 4 次才 OCR 一次（OCR+截图 ≈1–1.4 s）
                t2 = cv.ocr(im2, tag="intro")
                check_alerts(t2)
                if t2 and not any(k in ocr_join(t2) for k in ("即将行动", "已出现", "秒")):
                    break                              # 不是出场倒计时（弹窗等）=> 外层处理
            continue
        clicked = False
        if ("崩溃" in j or "重新加载" in j or ("啦！" in j and "问题" in j)) and "开始挑战" not in j:
            PAGE.update(kind="crash", seen=time.time())
            page_recover(cv, "Chrome 页面崩溃"); unknown = 0; ladder = 0; continue
        if "随机事件" in j:
            cv.click("top_mask", 0.05, "随机事件弹窗：点上方遮罩 (0.5,0.13) 关闭（不点免费/视频）"); clicked = True
        elif "双倍奖励仅有" in j or "前往领取双倍" in j:
            clicked = click_text(cv, texts, ("放弃",), 0.05)          # 双倍确认框 => 放弃
        elif classify_end(texts):
            clicked = click_text(cv, texts, ("返回",), 0.05)          # 结算画面 => 返回
        elif "恭喜获得" in j:
            cv.click("nav_blank", 0.05, "奖励弹窗：点空白关闭"); clicked = True
        elif ("请领取奖励" in j or "已通关" in j) and chest_clicks < 3:
            chest_clicks += 1
            cv.click("chest", 0.05, "主页（已通关）：点宝箱领通关奖励"); clicked = True
        elif sc == "home" and research_due(st):
            do_research(cv, st); unknown = 0; ladder = 0; sig_t = time.time(); continue
        elif sc == "home":
            cv.click("start", 0.05, "主页：点击「开始挑战」"); clicked = True
        elif click_text(cv, texts, ("开始挑战", "继续挑战"), 0.05):
            clicked = True
        elif "即将行动" in j or "已出现" in j:
            sleep(0.5); continue                       # 出场倒计时
        if clicked:
            unknown = 0; ladder = 0
            if not cv.wait_changed(img, 3.0):
                sleep(0.5)                             # 没变化：可能点击没生效，下一轮重新判断（不连点）
            continue
        unknown += 1
        if unknown % 4 == 2:
            cv.autocalib("界面识别失败")
        if unknown % 10 == 5 and restore_fullscreen(tries=1, activate=False, quiet=True):
            wait_loaded(cv, FS_WAIT); cv.autocalib("全屏后")       # patch25：掉回嵌入式小窗口 => 点网页「全屏」
            unknown = 0; ladder = 0; sig_t = time.time(); continue
        if j[:60] != last_log:
            last_log = j[:60]
            log(f"  未识别界面，等待。[画面文字] {j[:200]}")
        if close_info(cv, texts):
            cv.wait_changed(img, 2.0); continue
        tabs = [(x, y) for t, x, y in [(tt[0], tt[1], tt[2]) for tt in texts] if t.strip() == "主线" and y > 0.88] if texts else []
        if tabs and not any(k in j for k in ("开始挑战", "已通关", "请领取奖励")):
            POSITIONS["_tab"] = tabs[0]                # 跑到别的页签（神将/商城/副本…）=> 点底部「主线」回主页
            cv.click("_tab", 0.05, "不在主页：点底部「主线」页签返回")
            cv.wait_changed(img, 2.0); continue
        if unknown % 4 == 0:                           # 约 2~3 s 没认出 => 关闭阶梯走一步
            close_ladder(cv, texts, ladder)
            ladder += 1
            cv.wait_changed(img, 2.0)
            if ladder >= 4 and ladder % 4 == 0:        # 完整走完一轮阶梯
                sleep(5.0)
                if not cv.wait_changed(img, 0.5):
                    ladder_alert(cv, cv.ocr(tag="alert"), "未知弹窗：关闭阶梯走完一轮后画面 5 秒未变")
        else:
            sleep(0.5)
    return False


def write_log(row):
    path = os.path.join(HERE, LOG_FILE)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["开始时间", "结束时间", "关卡", "结果", "召唤次数", "结算文字"])
        w.writerow(row)


def finish_level(cv, st, start, result):
    """记录一关的结果；同一关失败 5 次则停止"""
    lv = BATTLE["level"]
    dw = BATTLE.get("dw", {})
    if dw.get("mode"):
        _def_stop(dw, "战斗结束")
    write_log([f"{start:%Y-%m-%d %H:%M:%S}", f"{datetime.now():%Y-%m-%d %H:%M:%S}", lv or "?", result,
               BATTLE["summons"], BATTLE["end_texts"][:120]])
    if lv:
        key = str(lv)
        if result == "负":
            st["fails"][key] = st["fails"].get(key, 0) + 1
        elif result == "胜":
            st["fails"].pop(key, None)
            st["last_win"] = lv
        save_state(st)
        log(f"第 {lv} 关：{result}（该关累计失败 {st['fails'].get(key, 0)} 次，召唤 {BATTLE['summons']} 次）")
        event("result", None, extra=f"{result} fails={st['fails'].get(key, 0)} summons={BATTLE['summons']}")
        try:                                           # 每关一行精简汇总 levels.log
            end = datetime.now()
            with open(os.path.join(HERE, "levels.log"), "a", encoding="utf-8") as f:
                f.write(f"L{lv}\t{start:%H:%M:%S}\t{end:%H:%M:%S}\t{int((end - start).total_seconds())}s\t{result}\t"
                        f"summons={BATTLE['summons']}\tfails={st['fails'].get(key, 0)}\t"
                        f"def_summons={dw.get('dsum', 0)}\tdef_modes={dw.get('modes', 0)}\n")
        except Exception:
            pass
        if st["fails"].get(key, 0) >= 5:
            log(f"第 {lv} 关已失败 5 次，脚本停止。")
            write_alert(f"第 {lv} 关已失败 5 次，脚本停止")
            STOP.set()
            raise KeyboardInterrupt


def summon_every_for(st):
    """已失败过的关卡更积极召唤"""
    f = st["fails"].get(str(BATTLE["level"]), 0) if BATTLE["level"] else 0
    return SUMMON_EVERY if f == 0 else 2


# ---------------- 网页崩溃 / 掉回淘宝网页小窗口 的自动恢复（patch24）----------------
# 画布 OCR 里出现淘宝网页文字（淘宝网首页/已买到的宝贝/购物车/操作说明）或崩溃页（崩溃啦）=> 恢复：
# 第 1 次：全屏（崩溃页先重载）；第 2 次：重载标签页 + 全屏；第 3 次起：等 60 s + 重载 + 全屏，一直循环，不停脚本。
# ALERT 只在第 3 次恢复时写一次；重新进入战斗后自动删除。
PAGE = {"kind": None, "seen": 0.0, "n": 0, "busy": False, "alerted": False, "last_reload": 0.0}
RELOAD_GAP = 180            # patch25：两次重载标签页至少间隔 3 分钟
LOAD_WAIT = 120             # patch25：重载后最多等 120 秒让游戏加载完成（期间不再重载）
FS_WAIT = 30                # 点「全屏」后最多等 30 秒（原 60；日志里成功的加载都在 0–20 秒内，等满 60 秒的 4 次最后都失败了）
PAGE_WORDS = ("淘宝网首页", "已买到的宝贝", "购物车")
CRASH_WORDS = ("崩溃啦", "崩溃了", "喔唷，崩溃")


def page_check(body):
    """由 Canvas.ocr 调用：只做标记，不点击"""
    if any(k in body for k in CRASH_WORDS) and "开始挑战" not in body:
        PAGE.update(kind="crash", seen=time.time())
    elif any(k in body for k in PAGE_WORDS) or ("操作说明" in body and not re.search(r"第\s*\d{1,3}\s*关|开始挑战|召唤", body)):
        PAGE.update(kind="page", seen=time.time())


def page_bad(max_age=4.0):
    return PAGE["kind"] is not None and time.time() - PAGE["seen"] < max_age


def page_ok():
    """进入战斗 = 恢复成功：清零升级计数，删掉本功能写的 ALERT"""
    if PAGE["n"] or PAGE["alerted"]:
        log(f"网页恢复成功（共尝试 {PAGE['n']} 次），重新进入战斗")
        if PAGE["alerted"]:
            try:
                os.remove(os.path.join(HERE, ALERT_FILE))
            except OSError:
                pass
    PAGE.update(kind=None, n=0, alerted=False)


def chrome_activate():
    """CDP 版：后台 Chrome 不需要调到前台（也不抢你的焦点）"""
    return True


def chrome_reload():
    """CDP 版：直接让后台 Chrome 刷新游戏页；页面跑到别处（非登录页）就重新打开游戏地址"""
    try:
        url = BR.url()
        if "login" in url:
            log("后台 Chrome 在登录页，不重载（需要重新扫码）")
            return False
        if "game.taobao.com" in url:
            BR.reload()
        else:
            log(f"后台 Chrome 不在游戏页（{url[:60]}），重新打开游戏地址")
            BR.goto()
        log("已重新加载游戏页，等 15 秒")
        sleep(15)
        return True
    except Exception as e:
        log(f"重新加载失败：{e}")
        return False


def soft_clear(cv, steps=3):
    """重载前的软恢复：嵌入式小窗口 => 点「全屏」；推广弹窗 → 信息面板 → 关闭阶梯（主页不点右下）。返回 True 表示看到游戏界面。"""
    for k in range(steps):
        if STOP.is_set() or not game_visible(0):
            return False
        img = cv.grab(); texts = cv.ocr(img, tag="soft")
        if embedded_page(texts):
            if restore_fullscreen(tries=1, activate=False, quiet=True):
                wait_loaded(cv, FS_WAIT); cv.autocalib("全屏后"); continue
            return False                               # 网页布局：不按画布坐标乱点
        if is_promo(texts, cv.screen(img)):
            handle_promo(cv, img, texts); continue
        if game_ui(cv, img, texts):
            return True
        if close_info(cv, texts):
            cv.wait_changed(img, 2.0); continue
        if not texts:
            return False                               # 黑屏/加载中/网页：不乱点
        close_ladder(cv, texts, k); cv.wait_changed(img, 2.0)
    img = cv.grab(); texts = cv.ocr(img, tag="soft")
    return game_ui(cv, img, texts)


def wait_loaded(cv, timeout=LOAD_WAIT):
    """重载/全屏后等游戏真正加载完（游戏界面出现，最多 timeout 秒）。期间：看到网页「全屏」就点，看到推广弹窗就关。绝不重载。"""
    t0 = time.time(); i = 0; same = 0; last_j = None; next_diag = t0 + 15
    _VIS["block_until"] = 0
    log(f"等待游戏加载完成（最多 {timeout:.0f} 秒，期间不重载）")
    while time.time() - t0 < timeout and not STOP.is_set():
        i += 1
        if not game_visible(0):
            sleep(1.5); continue
        img = cv.grab(); texts = cv.ocr(img, tag="load")
        emb = embedded_page(texts)
        if emb and restore_fullscreen(tries=1, activate=False, quiet=True):
            cv.autocalib("全屏后"); continue           # 嵌入式布局 => 点「全屏」（先看 DOM，已全屏时立刻返回）
        if emb:
            sleep(1.5); continue                       # 网页布局但「全屏」还没出现：只等，不点
        sc = cv.screen(img)
        if is_promo(texts, sc):
            handle_promo(cv, img, texts); continue
        if game_ui(cv, img, texts):
            log(f"游戏已加载（等了 {time.time() - t0:.0f} 秒）")
            return True
        j = ocr_join(texts)
        # 画面上是游戏里的文字（不是加载进度）且连续 2 次一样 => 已加载，交给导航流程识别（奖励弹窗等）
        loading = not texts or re.search(r"加载|载入|%|正在|连接|Loading", j)
        same = same + 1 if (not loading and j[:40] == last_j) else 0
        last_j = j[:40]
        if same >= 1:
            log(f"游戏画面已稳定（等了 {time.time() - t0:.0f} 秒，界面 {sc}），交给导航流程处理。[画面文字] {j[:80]}")
            return True
        if time.time() >= next_diag:                   # 每 15 秒留一行诊断，方便以后查「为什么等这么久」
            next_diag = time.time() + 15
            log(f"  仍在等加载（{time.time() - t0:.0f} 秒）：界面 {sc}，DOM {BR.fullscreen_state()}，[画面文字] {j[:80] or '（无）'}")
        sleep(1.5)
    log(f"等了 {time.time() - t0:.0f} 秒，游戏仍未加载完成")
    return False


def page_recover(cv, why, soft_ok=True):
    """patch25 逐级恢复：先关推广弹窗/遮罩 → 嵌入式小窗口点「全屏」→ 重载标签页（两次重载间隔 ≥ RELOAD_GAP，
    重载后等游戏加载最多 LOAD_WAIT 秒，期间不再重载）。完成后重新校准画布。绝不停止脚本。"""
    PAGE["n"] += 1; n = PAGE["n"]; kind = PAGE["kind"] or "?"
    PAGE["busy"] = True
    try:
        log(f"网页恢复第 {n} 次（{why}，类型 {kind}）")
        if n >= 3 and not PAGE["alerted"]:
            PAGE["alerted"] = True
            write_alert(f"网页异常/无法进入战斗，自动恢复中（第 {n} 次，脚本不会停止）")
        chrome_activate()
        if kind != "crash" and soft_clear(cv) and soft_ok:
            log("软恢复（关弹窗/遮罩）后看到游戏界面，本次不重载")
            return
        if restore_fullscreen(tries=2):
            if wait_loaded(cv, FS_WAIT):
                cv.autocalib("全屏后")
                return
        if kind == "crash" or n >= 2:
            gap = time.time() - PAGE["last_reload"]
            if gap < RELOAD_GAP:
                log(f"距上次重载仅 {gap:.0f} 秒（< {RELOAD_GAP}），不重载，继续等游戏加载")
                wait_loaded(cv, max(20, min(LOAD_WAIT, RELOAD_GAP - gap)))
            elif chrome_reload():
                PAGE["last_reload"] = time.time()
                wait_loaded(cv, LOAD_WAIT)
        _VIS["block_until"] = 0
        sleep(2)
        if not cv.autocalib("网页恢复后"):
            log(f"网页恢复后画布位置未变：{ {k: cv.c[k] for k in ('left', 'top', 'width', 'height')} }")
    finally:
        PAGE.update(kind=None, busy=False)


def level_fallback(texts=None, st=None):
    """关卡号：OCR「第N关」（战斗/阵容/主页）→ state.json 的 last_win + 1"""
    lv = read_level(texts or [])
    if lv:
        return lv
    try:
        st = st or load_state()
        if st.get("last_win"):
            return int(st["last_win"]) + 1
    except Exception:
        pass
    return None


def restore_fullscreen(tries=8, activate=True, quiet=False):
    """页面重新加载后游戏会回到嵌在淘宝游戏中心里的小窗口：找网页上的「全屏」按钮点一下，恢复黑底大画布（calib 依赖此布局）。"""
    if not game_visible(0):
        return False                                   # 后台 Chrome 不在游戏页：不点
    for i in range(tries):
        try:
            fs = BR.fullscreen_state()                 # 先看网页 DOM：已是全屏 / 「全屏」按钮位置
            if fs.get("exit"):
                return False
            if fs.get("btn"):
                cx, cy = fs["btn"]
                log(f"点网页「全屏」按钮 @({cx:.0f},{cy:.0f}) 恢复大画布")
                BR.click(cx, cy); sleep(4); return True
            sw, sh = BR.size()                         # DOM 没找到 => 整页截图 OCR 兜底
            path = os.path.join(HERE, "_fs.png"); BR.shot().save(path)
            out = ocr_client.ocr_text(path, 10)
            for line in out.splitlines():
                parts = line.split("\t")
                tt = parts[0].strip() if len(parts) == 5 else ""
                if tt.startswith("全屏") and len(tt) <= 4 and "退出" not in tt:   # OCR 常带图标：「全屏 口」
                    x, y, w, h = map(float, parts[1:])
                    if y < 0.5:
                        cx, cy = (x + w / 2) * sw, (y + h / 2) * sh
                        log(f"点网页「全屏」按钮 @({cx:.0f},{cy:.0f})（OCR 定位）恢复大画布")
                        BR.click(cx, cy); sleep(4); return True
        except Exception as e:
            log(f"找「全屏」按钮失败：{e}")
        if i < tries - 1:
            sleep(5)
    if not quiet:
        log("没找到网页「全屏」按钮（可能已是全屏/页面还没加载完）")
    return False


START_MTIME = os.path.getmtime(os.path.abspath(__file__))


def maybe_reload():
    """脚本文件被更新过 => 在关卡间隙原地重启进程加载新代码（不打断战斗、无需外部重启）"""
    path = os.path.abspath(__file__)
    try:
        if os.path.getmtime(path) <= START_MTIME:
            return
        compile(open(path, encoding="utf-8").read(), path, "exec")   # 语法错误就不重载
    except Exception as e:
        log(f"新脚本无法加载（{e}），继续用旧代码")
        return
    log("检测到脚本已更新，原地重启加载新代码……")
    args = [a for a in sys.argv[1:] if a not in ("--resume", "--no-opening", "--fix-lineup")]
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, "-u", path] + args)


STOP_FLAG = os.path.join(HERE, "STOP_AFTER_LEVEL")


def run(cv, resume=False):
    no_progress = 0
    st = load_state()
    if resume and not cv.in_battle():       # 不在战斗画面（主页等）=> 不接手，直接走主页流程
        wait_game_visible()
        if not cv.in_battle():
            log("--resume：当前不在战斗画面，改走主页→开始挑战流程")
            resume = False
    if resume:                              # 从正在进行的战斗接手
        log("接手当前战斗")
        reset_battle(level_fallback(cv.ocr(), st))
        BATTLE["in_battle"] = True
        t0 = time.time(); start = datetime.now()
        result = play_battle(cv, t0, opening="--no-opening" not in sys.argv, summon_every=summon_every_for(st))
        finish_level(cv, st, start, result)
    k = 0
    while k < MAX_LEVELS:                    # 只有真正进入战斗才计场数；进不去就一直恢复重试
        if os.path.exists(STOP_FLAG):        # 监控页「打完这关再退出」/ stop_bot.py --after-level
            try:
                os.remove(STOP_FLAG)
            except OSError:
                pass
            log("收到「打完这关再退出」：本关已结束，关闭后台浏览器并退出（守护不会重启）")
            cdp_backend.close_browser()
            return
        tidy_tabs()
        maybe_reload()
        log(f"======== 第 {k + 1} 场（本次运行）========")
        reset_battle(None)
        if not to_next_battle(cv, st):
            no_progress += 1
            event("stuck", cv, shot=True, extra=f"{no_progress}/{MAX_NO_PROGRESS}")
            log(f"没有进入战斗（第 {no_progress} 次），自动恢复，不停止脚本（已存截图 stuck）")
            page_recover(cv, f"连续 {no_progress} 次无法进入战斗", soft_ok=no_progress < 3)
            continue
        no_progress = 0
        k += 1
        t0 = time.time(); start = datetime.now()
        if not BATTLE["level"]:
            BATTLE["level"] = level_fallback(cv.ocr(), st)
        lat = f"，距上一关结算 {t0 - TIMING['end']:.1f} 秒" if TIMING["end"] else ""
        log(f"进入战斗：第 {BATTLE['level']} 关（该关此前失败 {st['fails'].get(str(BATTLE['level']), 0)} 次）{lat}")
        BATTLE["in_battle"] = True
        event("battle_start", cv, shot=True, extra=lat.lstrip("，"))
        result = play_battle(cv, t0, summon_every=summon_every_for(st))
        finish_level(cv, st, start, result)
    log(f"已达到最大场数 {MAX_LEVELS}，脚本结束。")


def tidy_tabs():
    """关卡间隙：关掉别的程序往后台 Chrome 里开的非淘宝标签页"""
    try:
        n = cdp_backend.close_stray_pages()
        if n:
            log(f"后台 Chrome 里有 {n} 个非淘宝标签页（不是本脚本开的），已关闭")
    except Exception as e:
        log(f"清理标签页失败：{e}")


def _on_signal(sig, frame):
    """被别的程序结束时留一行日志，并用非 0 退出码退出（supervise.py 据此自动重启）"""
    log(f"收到信号 {signal.Signals(sig).name}（被其他程序结束），退出")
    sys.stdout.flush()
    os._exit(128 + sig)


def main():
    args = sys.argv[1:]
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGHUP, _on_signal)
    calib = load_calib(force="--calib" in args)
    try:
        os.remove(STOP_FLAG)                 # 重新启动 = 想继续跑：清掉上次没用掉的「打完这关再退出」
    except OSError:
        pass
    try:
        if not cdp_backend.launch():
            log("后台 Chrome 启动失败，脚本退出")
            return
        tidy_tabs()
        cv = Canvas(calib)
        CV[0] = cv
        start_hotkey()
        start_monitor(cv)
        log("后台模式：游戏在无窗口 Chrome 里跑，不动鼠标、不截屏幕，可以正常用电脑")
        sleep(1)
        FIX_LINEUP[0] = "--fix-lineup" in args
        run(cv, resume="--resume" in args)
    except KeyboardInterrupt:
        log("已手动停止。")


if __name__ == "__main__":
    main()
