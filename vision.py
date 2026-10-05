# 画面识别（纯函数，不点击、不截图、不依赖 bot 的全局状态），方便单独测试。
# 坐标都是画布比例 (0..1)；像素判断先缩到 391 宽，和截图分辨率（1 倍/2 倍）无关。
import glob, os, re
from PIL import Image, ImageChops, ImageDraw, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
ARROW_SZ = 18
ARROW_CDP = os.path.join(HERE, "arrow_tpl_cdp.png")   # 后台 Chrome 颜色下自动采集的箭头模板
_ARROW = {}
DB_W = 391


def find_bed(texts, gen_labels=()):
    """床的位置每关不同：找房间下半部的「N级」标签（床下方的等级字），返回点击点。gen_labels=发电机标签位置（排除）"""
    best = None
    for t, x, y in texts:
        if re.fullmatch(r"\d{1,2}级", t.strip()) and 0.64 <= y <= 0.88:
            if any(abs(x - gx) < 0.05 and abs(y - gy) < 0.05 for gx, gy in gen_labels):
                continue
            d = (x - 0.72) ** 2 + (y - 0.78) ** 2
            if best is None or d < best[0]:
                best = (d, x, y)
    return None if best is None else (best[1] - 0.02, best[2] - 0.03)


def parse_prereq(text):
    """面板里的「前置：N级大门」/「大门达到N级」=> ('door'|'bed'|其他名字, N)"""
    m = re.search(r"前置\s*[:：;；]?\s*(\d{1,2})\s*级\s*(大门|床铺|[^\s|]{1,4})", text)
    if m:
        n, what = int(m.group(1)), m.group(2)
    else:
        m = re.search(r"(大门|床铺)达到\s*(\d{1,2})\s*级", text)
        if not m:
            return None
        what, n = m.group(1), int(m.group(2))
    return ({"大门": "door", "床铺": "bed"}.get(what, what), n)


def _templates():
    """arrow_tpl*.png 全部加载（旧整屏截图模板 + 后台 Chrome 自动采集的模板），取最低分"""
    if "tpls" not in _ARROW:
        tpls = []
        for p in sorted(glob.glob(os.path.join(HERE, "arrow_tpl*.png"))):
            try:
                im = Image.open(p).convert("RGB")
                if im.size == (ARROW_SZ, ARROW_SZ):
                    tpls.append(im)
            except Exception:
                pass
        m = Image.new("L", (ARROW_SZ, ARROW_SZ), 0)
        ImageDraw.Draw(m).ellipse((3, 3, ARROW_SZ - 4, ARROW_SZ - 4), fill=255)
        _ARROW.update(tpls=tpls, mask=m)
    return _ARROW["tpls"], _ARROW["mask"]


def _small(img):
    W, H = img.size
    return img.convert("RGB").resize((DB_W, int(DB_W * H / W)))


def find_arrows(img, y0=0.48, y1=0.86, thr=28):
    """房间里的绿色「可升级」箭头圆圈（只有现在能升级的建筑才有）。模板 18x18（391 宽画布），
    圆形掩膜比较，箭头 ≤22、其他 ≥37。返回 [(fx, fy, score)]"""
    tpls, mask = _templates()
    if not tpls:
        return []
    sm = _small(img); w, h = sm.size; px = sm.load(); r2 = ARROW_SZ // 2

    def score(cx, cy):
        c = sm.crop((cx - r2, cy - r2, cx + r2, cy + r2))
        return min(sum(ImageStat.Stat(ImageChops.difference(c, t), mask).mean) / 3 for t in tpls)
    cands = set()
    for y in range(int(y0 * h), int(y1 * h), 2):
        for x in range(r2, w - r2, 2):
            r, g, b = px[x, y]
            if g >= 180 and g - r >= 50 and -8 <= b - r <= 25:   # 箭头浅绿环
                for dx in (-4, 0, 4):
                    for dy in (-4, 0, 4):
                        cands.add((x + dx, y + dy))
    scored = sorted((score(cx, cy), cx, cy) for cx, cy in cands)
    refined = []
    for sc, cx, cy in scored:                          # 粗筛 < 40 的点再逐像素细化 ±3
        if sc > 40:
            break
        if any(abs(cx - o[1]) <= 6 and abs(cy - o[2]) <= 6 for o in refined):
            continue
        refined.append(min((score(x, y), x, y) for x in range(cx - 3, cx + 4) for y in range(cy - 3, cy + 4)))
    out = []
    for sc, cx, cy in sorted(refined):
        if sc > thr:
            break
        if all(abs(cx - o[0]) > 10 or abs(cy - o[1]) > 10 for o in out):
            out.append((cx, cy, sc))
    return [(round(x / w, 3), round(y / h, 3), round(sc, 1)) for x, y, sc in out]


def has_cdp_template():
    return os.path.exists(ARROW_CDP)


def save_arrow_template(img, pos):
    """在后台 Chrome 截图里采集一个箭头模板（以 pos 为中心 18x18，391 宽缩略图上）。
    只在「模板匹配到箭头 + 点开面板确认是床/门」之后调用，保证采到的确实是箭头。"""
    sm = _small(img); w, h = sm.size; r2 = ARROW_SZ // 2
    cx, cy = round(pos[0] * w), round(pos[1] * h)
    if not (r2 <= cx <= w - r2 and r2 <= cy <= h - r2):
        return False
    sm.crop((cx - r2, cy - r2, cx + r2, cy + r2)).save(ARROW_CDP)
    _ARROW.clear()                                     # 下次识别重新加载模板
    return True


def find_door(texts, img=None):
    """门在房间上墙，左侧有「N级」标签，右边是绿色血条；点标签右下的绿色箭头圆圈打开城门面板。
    OCR 认不出小字「N级」时（后台截图常见），改用绿色血条定位：箭头在血条左端右下方。"""
    best = None
    for t, x, y in texts:
        if re.fullmatch(r"\d{1,2}级[0O口■|\d\s【\[]*", t.strip()) and 0.45 <= y <= 0.68 and x < 0.5:
            if best is None or y < best[1]:
                best = (x, y)
    if best is not None:
        return (best[0] + 0.063, best[1] + 0.022)      # 标签右侧的绿色箭头圆圈
    g = door_bar_locate(img) if img is not None else None
    if g and g["x0"] < 0.6:
        return (round(g["x0"] + 0.034, 3), round((g["y0"] + g["y1"]) / 2 + 0.022, 3))
    return None


# ---------------- 门血条 ----------------
def _dbar_seg(p):
    r, g, b = p[:3]
    # 血条格子：旧整屏截图 (117,252,105)，后台 Chrome 截图 (0,255,79)（颜色空间不同）。
    # 「N级」字 (4,223,127)、箭头环 (100,202,122) 的蓝色都偏高，不满足
    return g >= 200 and g - r >= 100 and b <= max(r - 4, 0.40 * g)


_dbar_small = _small


def door_bar_locate(img, y0=0.40, y1=0.66, x0=0.02, x1=0.75):
    """在上墙区域找最长的一段亮绿分段条（≥14 px 宽、≥3 行一致）。返回比例坐标 {x0,x1,y0,y1} 或 None"""
    sm = _small(img); w, h = sm.size; px = sm.load()
    rows = {}
    for y in range(int(y0 * h), int(y1 * h)):
        cl, s0, p0 = [], None, None
        for x in range(int(x0 * w), int(x1 * w)):
            if not _dbar_seg(px[x, y]):
                continue
            if s0 is None:
                s0 = p0 = x
            elif x - p0 <= 5:
                p0 = x
            else:
                cl.append((s0, p0)); s0 = p0 = x
        if s0 is not None:
            cl.append((s0, p0))
        rows[y] = [c for c in cl if c[1] - c[0] >= 14]
    best = None
    for y, cl in rows.items():
        for a, b in cl:
            ys = [yy for yy in range(y - 6, y + 7)
                  if any(abs(c[0] - a) <= 4 and abs(c[1] - b) <= 4 for c in rows.get(yy, []))]
            sc = (b - a) * len(ys)
            if len(ys) >= 3 and (best is None or sc > best[0]):
                best = (sc, a, b, min(ys), max(ys))
    if not best:
        return None
    _, a, b, ya, yb = best
    return {"x0": a / w, "x1": b / w, "y0": ya / h, "y1": yb / h}


def door_bar_measure(img, g):
    """已定位的血条范围内，亮绿列数（列中一半以上行是格子绿才算）"""
    sm = _small(img); w, h = sm.size; px = sm.load()
    xa = max(0, int(g["x0"] * w) - 3); xb = min(w - 1, int(g["x1"] * w) + 4)
    ya = max(0, int(g["y0"] * h)); yb = min(h - 1, int(g["y1"] * h))
    n = 0
    for x in range(xa, xb + 1):
        hit = sum(1 for y in range(ya, yb + 1) if _dbar_seg(px[x, y]))
        if hit * 2 >= (yb - ya + 1):
            n += 1
    return n
