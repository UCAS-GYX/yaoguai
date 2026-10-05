"""回归测试（离线，不连后台浏览器、不点游戏）。用法：./venv/bin/python -m unittest discover -s tests -v
素材在 tests/fixtures：battle_L67.png（后台 1 倍截图，第 67 关战斗）、battle_live.png（第 70 关战斗）、
embedded_page.png（旧整屏截图：淘宝网页小窗口 + 推广弹窗）、home_promo_entry.png（旧整屏截图：主页，含活动入口）。"""
import os, subprocess, sys, tempfile, time, unittest
from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
FIX = os.path.join(HERE, "tests", "fixtures")

import cdp_backend  # noqa: E402
import vision  # noqa: E402
import yaoguai_bot as yb  # noqa: E402
import ocr_client  # noqa: E402
import notify  # noqa: E402
import stop_bot  # noqa: E402


def _forbid(*a, **k):
    raise AssertionError("测试中禁止连接后台浏览器")


# 不连浏览器：DOM 查询固定为「已全屏」，其他协议调用一律报错
yb.BR.call = _forbid
yb.BR.fullscreen_state = lambda: {"exit": True, "btn": None}
yb.game_visible = lambda max_age=1.5: True
yb.log = lambda m: None
yb.sleep = lambda s: None


def img(name):
    return Image.open(os.path.join(FIX, name)).convert("RGB")


def canvas():
    cv = yb.Canvas.__new__(yb.Canvas)                  # 不走 __init__（会连浏览器）
    cv.c, cv.scale = dict(yb.CALIB_DEFAULT), 1
    return cv


def T(*words):
    return [(w, 0.5, 0.5) for w in words]


class Vision(unittest.TestCase):
    def test_bar_colors(self):
        self.assertTrue(vision._dbar_seg((0, 255, 79)))        # 后台浏览器血条色
        self.assertTrue(vision._dbar_seg((117, 252, 105)))     # 旧整屏截图血条色
        self.assertFalse(vision._dbar_seg((4, 223, 127)))      # 「N级」字
        self.assertFalse(vision._dbar_seg((100, 202, 122)))    # 箭头环

    def test_door_bar_and_door(self):
        for name in ("battle_L67.png", "battle_live.png"):
            im = img(name)
            g = vision.door_bar_locate(im)
            self.assertIsNotNone(g, name)
            self.assertTrue(0.2 < g["x0"] < 0.4 and 0.52 < g["y0"] < 0.6, (name, g))
            self.assertGreater(vision.door_bar_measure(im, g), 20, name)
            d = vision.find_door([], im)                        # OCR 认不出「N级」 => 按血条定位
            self.assertTrue(0.25 < d[0] < 0.42 and 0.55 < d[1] < 0.62, (name, d))

    def test_scale_invariant(self):
        im = img("battle_L67.png")
        big = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
        a, b = vision.door_bar_locate(im), vision.door_bar_locate(big)
        for k in ("x0", "x1", "y0", "y1"):
            self.assertAlmostEqual(a[k], b[k], delta=0.012, msg=k)

    def test_find_door_by_label(self):
        self.assertEqual(vision.find_door([("4级", 0.265, 0.564)]), (0.265 + 0.063, 0.564 + 0.022))

    def test_find_bed(self):
        p = vision.find_bed([("3级", 0.73, 0.80)])
        self.assertAlmostEqual(p[0], 0.71); self.assertAlmostEqual(p[1], 0.77)
        self.assertIsNone(vision.find_bed([("3级", 0.73, 0.80)], gen_labels=[(0.73, 0.80)]))

    def test_find_bed_ocr_tail(self):
        """日志里床标签常被认成「6级：」，以前严格匹配「6级」，没找到床 27%"""
        self.assertIsNotNone(vision.find_bed([("6级：", 0.73, 0.80)]))
        self.assertIsNone(vision.find_bed([("1级招财猫", 0.73, 0.80)]))

    def test_parse_prereq(self):
        self.assertEqual(vision.parse_prereq("升级 | 前置：8级大门 | 800"), ("door", 8))
        self.assertEqual(vision.parse_prereq("大门达到3级"), ("door", 3))
        self.assertIsNone(vision.parse_prereq("升级成功"))

    def test_arrow_template_capture(self):
        im = img("battle_L67.png")
        saved, vision.ARROW_CDP = vision.ARROW_CDP, os.path.join(tempfile.mkdtemp(), "arrow_tpl_cdp.png")
        try:
            self.assertTrue(vision.save_arrow_template(im, (0.5, 0.6)))
            self.assertEqual(Image.open(vision.ARROW_CDP).size, (vision.ARROW_SZ, vision.ARROW_SZ))
            self.assertFalse(vision.save_arrow_template(im, (0.001, 0.6)))   # 贴边：不采
        finally:
            vision.ARROW_CDP = saved
            vision._ARROW.clear()


class Screens(unittest.TestCase):
    def test_in_battle(self):
        cv = canvas()
        for name in ("battle_L67.png", "battle_live.png"):
            self.assertTrue(cv.in_battle(img(name)), name)
            self.assertEqual(cv.screen(img(name)), "battle", name)

    def test_battle_ocr(self):
        texts = canvas().ocr(img("battle_L67.png"), tag="test")
        self.assertIn("建造机关", yb.ocr_join(texts))

    def test_home_is_not_promo(self):
        """10:28 误判：主页的活动入口「地府公会战 万鬼无限城」只有标题，不是弹窗"""
        t = canvas().ocr(img("home_promo_entry.png"), tag="test")
        self.assertTrue(t)
        self.assertFalse(yb.is_promo(t, "other"))

    def test_embedded_page_text(self):
        t = canvas().ocr(img("embedded_page.png"), tag="test")
        self.assertTrue(yb.embedded_page(t))
        self.assertFalse(yb.is_promo(t, "other"))              # 网页布局上不按画布坐标点弹窗


class Texts(unittest.TestCase):
    def test_end_screen(self):
        self.assertEqual(yb.classify_end(T("挑战成功", "奖励预览")), "胜")
        self.assertEqual(yb.classify_end(T("挑战失败", "再接再厉")), "负")
        self.assertIsNone(yb.classify_end(T("升级成功", "召唤")))
        self.assertTrue(yb.is_end_screen(T("每关通关双倍奖励仅有1次领取机会")))

    def test_double_reward_not_promo(self):
        self.assertFalse(yb.is_promo(T("前往领取双倍", "放弃")))

    def test_checkbox_promo(self):
        self.assertTrue(yb.is_promo(T("公会封魔录", "今日不再提示", "前往")))

    def test_build_panel_cat_only(self):
        """用户截图：发电机满了，面板只剩招财猫 0/1（60 电）。以前把它当成发电机 0 座，一关里点了 100 多次"""
        t = [("新增建筑", 0.50, 0.37), ("每秒", 0.30, 0.43), ("10", 0.37, 0.43), ("数量:0/1", 0.62, 0.43),
             ("摸鱼的招财猫，挥手随缘招财", 0.43, 0.46), ("60", 0.83, 0.45), ("1级招财猫", 0.16, 0.48)]
        rows = yb.parse_build_panel(t)
        self.assertNotIn("gen", rows)
        self.assertEqual((rows["cat"]["n"], rows["cat"]["m"]), (0, 1))
        self.assertEqual(rows["cat"]["btn"], (0.83, 0.45))

    def test_build_panel_two_rows(self):
        t = [("新增建筑", 0.50, 0.37), ("每秒+1电", 0.30, 0.43), ("数量:1/2", 0.62, 0.43), ("300", 0.82, 0.45),
             ("1级发电机", 0.16, 0.48), ("每秒", 0.30, 0.52), ("数量:0/1", 0.62, 0.52), ("60", 0.82, 0.54),
             ("1级招财猫", 0.16, 0.57)]
        rows = yb.parse_build_panel(t)
        self.assertEqual((rows["gen"]["n"], rows["gen"]["m"], rows["gen"]["btn"]), (1, 2, (0.82, 0.45)))
        self.assertEqual((rows["cat"]["n"], rows["cat"]["btn"]), (0, (0.82, 0.54)))

    def test_research_due(self):
        self.assertFalse(yb.research_due({"last_win": 71, "research_at": 71}))
        self.assertFalse(yb.research_due({"last_win": 80, "research_at": 71}))
        self.assertTrue(yb.research_due({"last_win": 81, "research_at": 71}))
        self.assertFalse(yb.research_due({"last_win": 81, "research_at": 71, "research_try": 81}))   # 同一通关数只试一次

    def test_research_levels(self):
        t = [("房间研发", 0.50, 0.12), ("52", 0.08, 0.30), ("51", 0.08, 0.43), ("48", 0.08, 0.85), ("键升级", 0.52, 0.96)]
        self.assertEqual(yb._research_levels(t), [48, 51, 52])

    def test_read_level(self):
        self.assertEqual(yb.read_level([("第 64 关", 0.1, 0.1)]), 64)
        self.assertIsNone(yb.read_level([("勞67夫", 0.1, 0.1)]))


class Processes(unittest.TestCase):
    def test_find_pids_exact(self):
        """以前 pkill -f yaoguai_bot.py 会误杀命令行里带这几个字的终端命令"""
        d = tempfile.mkdtemp()
        script = os.path.join(d, "yg_test_target.py")
        with open(script, "w") as f:
            f.write("import time\ntime.sleep(30)\n")
        real = subprocess.Popen([sys.executable, script])
        shell = subprocess.Popen(["/bin/sh", "-c", "sleep 30; echo yg_test_target.py"])
        try:
            time.sleep(0.5)
            pids = cdp_backend.find_pids("yg_test_target.py")
            self.assertIn(real.pid, pids)
            self.assertNotIn(shell.pid, pids)
        finally:
            real.kill(); shell.kill()

    def test_ocr_client_recovers(self):
        c = ocr_client.OcrClient()
        path = os.path.join(FIX, "battle_L67.png")
        a = c.run(path)
        c.p.kill(); c.p.wait()                          # 常驻进程被杀 => 下次自动重启
        self.assertEqual(c.run(path), a)
        c.close()

    def test_notify_rate_limit(self):
        sent, real = [], notify._send
        notify._send = lambda t, m: sent.append(m)
        try:
            self.assertTrue(notify.notify("测试", key="_unit"))
            self.assertFalse(notify.notify("测试", key="_unit"))   # 10 分钟内同一条不重复发
            time.sleep(0.2)
            self.assertEqual(sent, ["测试"])
        finally:
            notify._send = real
            notify._LAST.pop("_unit", None)

    def test_stop_after_level_flag(self):
        stop_bot.stop_after_level(True)
        try:
            self.assertTrue(stop_bot.stop_pending())
            self.assertEqual(stop_bot.STOP_FLAG, yb.STOP_FLAG)
        finally:
            stop_bot.stop_after_level(False)
        self.assertFalse(stop_bot.stop_pending())

    def test_monitor_status_cache(self):
        import monitor
        n, real = [0], monitor._status
        monitor._status = lambda: (n.__setitem__(0, n[0] + 1), {"running": False})[1]
        monitor._ST["v"] = None
        try:
            monitor.status(); monitor.status(); monitor.status()
            self.assertEqual(n[0], 1)                  # 1 秒内只真正统计一次
        finally:
            monitor._status = real
            monitor._ST["v"] = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
