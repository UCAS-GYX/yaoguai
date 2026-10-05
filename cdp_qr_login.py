# 扫码登录辅助：刷新登录页 → 导出二维码图片并打开 → 等待登录成功；二维码失效时自动刷新并重新打开新图。
import os, subprocess, time
from PIL import Image
import cdp_probe

HERE = cdp_probe.HERE
QR_JS = """(() => { const cv = [...document.querySelectorAll('canvas')].find(e => e.getBoundingClientRect().width > 150);
  if (!cv) return null; const r = cv.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()"""


def export_qr(c):
    rect = None
    for _ in range(20):                                # 等二维码画布出现
        rect = c.js(QR_JS)
        if rect:
            break
        time.sleep(0.5)
    if not rect:
        return None
    time.sleep(1.0)                                    # 等二维码画完
    img, _ = c.shot()
    x, y, w, h = rect
    pad = 16
    qr = img.crop((int(x - pad), int(y - pad), int(x + w + pad), int(y + h + pad))).convert("RGB")
    path = os.path.join(HERE, f"_qr_{time.strftime('%H%M%S')}.png")   # 每次新文件名，避免预览显示旧图
    qr.resize((560, 560), Image.NEAREST).save(path)
    subprocess.run(["open", "-a", "Preview", path])
    return path


def expired(c):
    img, _ = c.shot()
    p = os.path.join(HERE, "_qr_check.png"); img.save(p)
    out = subprocess.run([os.path.join(HERE, "ocr"), p], capture_output=True, text=True, timeout=10).stdout
    return any(k in out for k in ("失效", "过期", "点击刷新", "刷新二维码"))


def main():
    c = cdp_probe.CDP()
    c.call("Page.reload", ignoreCache=True)
    time.sleep(3)
    print("二维码已打开:", export_qr(c), flush=True)
    t0 = time.time()
    while time.time() - t0 < 600:                      # 最多等 10 分钟
        time.sleep(3)
        url = c.js("location.href") or ""
        if "login.taobao.com" not in url:
            print("登录成功，当前页面:", url[:120], flush=True)
            return
        if expired(c):
            c.call("Page.reload", ignoreCache=True)
            time.sleep(3)
            print("二维码失效，已刷新:", export_qr(c), flush=True)
    print("10 分钟内没有登录，退出", flush=True)


if __name__ == "__main__":
    main()
