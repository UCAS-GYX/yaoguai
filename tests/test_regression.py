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
        # 17:49 第 81 关：只看到双倍奖励确认框（没看到「挑战成功」）=> 也是胜
        self.assertEqual(yb.classify_end(T("提示", "每关通关双倍奖励仅有1次领取机", "会，是否前往领取双倍奖励", "放弃")), "胜")

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


class StubCanvas:
    """战斗动作测试用：截图/OCR 用桩，cv.click 只记录调用，不连浏览器"""
    def __init__(self, frames, texts, red=False):
        self.cv = canvas()
        self.frames, self.texts, self.clicks = list(frames), texts, []
        cv = self.cv
        cv.grab = self.grab
        cv.ocr = lambda img=None, tag="": self.texts(self) if callable(self.texts) else self.texts
        cv.click = lambda name, wait=0.6, note="": self.clicks.append(name)
        cv.wait_changed = lambda before, timeout=3.0, thr=2.5: False
        cv.price_is_red = lambda: red

    def grab(self):
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


class Battle(unittest.TestCase):
    def setUp(self):
        yb.reset_battle(80)
        yb.END_SEEN[0] = False
        self._sleep, yb.time.sleep = yb.time.sleep, lambda s: None   # 不真等
        self._event, yb.event = yb.event, lambda *a, **k: None       # 不写 events.log

    def tearDown(self):
        yb.time.sleep, yb.event = self._sleep, self._event
        yb.OPENING[0] = False
        yb.END_SEEN[0] = False
        yb.SLOW_FLAG[0] = False

    # ---- 游戏时钟 ----
    def test_game_clock_fixture(self):
        t = canvas().ocr(img("battle_L67.png"), tag="test")   # OCR 实际读成「402:16」（左边图标被认成 4）
        self.assertEqual(yb.read_game_clock(t), 136)
        self.assertFalse(yb.clock_paused(t))

    def test_game_clock_text(self):
        self.assertEqual(yb.read_game_clock([("02:16", 0.81, 0.14)]), 136)
        self.assertEqual(yb.read_game_clock([("07:20 暂停中", 0.81, 0.14)]), 440)
        self.assertTrue(yb.clock_paused([("07:20 暂停中", 0.81, 0.14)]))
        self.assertTrue(yb.clock_paused([("2703:27-百停中.", 0.81, 0.14)]))   # 日志里的 OCR 变体
        self.assertEqual(yb.read_game_clock([("2703:27-百停中.", 0.81, 0.14)]), 207)
        self.assertIsNone(yb.read_game_clock([("205:", 0.81, 0.14), ("第81关", 0.08, 0.14)]))
        self.assertIsNone(yb.read_game_clock([("12:30", 0.5, 0.6)]))           # 不在顶部
        self.assertIsNone(yb.read_game_clock([]))

    # ---- 走速 ----
    def test_pace_normal(self):
        s = [(0, 10, False), (25, 35, False), (52, 61, False), (78, 88, False)]
        streak, r = yb.clock_pace(s)
        self.assertEqual(streak, 0)
        self.assertLess(r, 1.1)

    def test_pace_slow(self):
        """L69：墙钟 640 秒游戏才 03:26（约 3 倍）"""
        s = [(0, 10, False), (30, 20, False), (60, 30, False), (90, 40, False)]
        streak, r = yb.clock_pace(s)
        self.assertEqual(streak, 3)
        self.assertAlmostEqual(r, 3.0)
        self.assertEqual(yb.clock_pace(s[:2])[0], 1)                         # 只慢 1 轮不算
        self.assertEqual(yb.clock_pace(s + [(115, 65, False)])[0], 0)        # 恢复正常 => 清零

    def test_pace_paused(self):
        """暂停中（选卡/弹窗没关）游戏时钟不走，不算变慢；读不到计时的样本跳过"""
        s = [(0, 100, False), (25, 100, True), (50, 100, True), (60, None, False), (75, 125, False), (100, 150, False)]
        self.assertEqual(yb.clock_pace(s)[0], 0)
        s2 = [(0, 100, False), (25, 100, False), (50, 100, False)]          # 没暂停但时钟不走 = 卡住
        self.assertEqual(yb.clock_pace(s2)[0], 2)

    def test_pace_glitch_resets(self):
        s = [(0, 300, False), (30, 310, False), (60, 5, False), (90, 35, False)]   # 时钟倒退（页面重载/读错）
        self.assertEqual(yb.clock_pace(s)[0], 0)

    def test_pace_sample_sets_flag_and_restart(self):
        yb.BATTLE["paused_s"] = 0.0
        for w, g in ((1000, 10), (1030, 20), (1060, 30)):
            yb.pace_sample([(f"{g // 60:02d}:{g % 60:02d}", 0.8, 0.14)], now=w)
        self.assertTrue(yb.SLOW_FLAG[0])
        restarted, real = [], (yb.cdp_backend.renderer_stats, yb.cdp_backend.restart_browser)
        yb.cdp_backend.renderer_stats = lambda: (10.0, 500)
        yb.cdp_backend.restart_browser = lambda: restarted.append(1) or True
        try:
            yb.BROWSER_LEVELS[0] = 1
            yb.periodic_restart()                     # 关卡间隙：标记 => 立即重启（没到 5 关）
            self.assertEqual(restarted, [1])
            self.assertFalse(yb.SLOW_FLAG[0])
            yb.periodic_restart()                     # 标记只用一次
            self.assertEqual(restarted, [1])
        finally:
            yb.cdp_backend.renderer_stats, yb.cdp_backend.restart_browser = real
            yb.BROWSER_LEVELS[0] = 0
            yb.BR.ws = None

    def test_pace_sample_deducts_pause(self):
        """选卡/弹窗暂停的墙钟时间扣掉：墙钟 30 秒里暂停 18 秒、游戏走 12 秒 => 正常"""
        for w, g, p in ((1000, 10, 0.0), (1030, 22, 18.0), (1060, 34, 36.0), (1090, 46, 54.0)):
            yb.BATTLE["paused_s"] = p
            yb.pace_sample([(f"00:{g:02d}", 0.8, 0.14)], now=w)
        self.assertFalse(yb.SLOW_FLAG[0])

    # ---- 召唤前读金币/价格 ----
    def test_summon_skip_when_coins_lt_price(self):
        s = StubCanvas([img("battle_L67.png")], [("30", 0.14, 0.207), ("40", 0.53, 0.905), ("召唤", 0.5, 0.95)])
        yb.summon(s.cv)
        self.assertNotIn("summon", s.clicks)
        self.assertEqual(yb.BATTLE["summons"], 0)

    def test_summon_when_coins_enough(self):
        s = StubCanvas([img("battle_L67.png")], canvas().ocr(img("battle_L67.png"), tag="test"), red=True)
        self.assertIsNone(yb.summon_blocked(s.cv))    # 读到 850 ≥ 56：以 OCR 为准（不看红色）
        yb.summon(s.cv)
        self.assertEqual(s.clicks, ["summon"])        # 没看到选卡界面 => 不乱点卡
        self.assertEqual(yb.BATTLE["summons"], 1)

    def test_summon_fallback_price_red(self):
        s = StubCanvas([img("battle_L67.png")], [("召唤", 0.5, 0.95)], red=True)   # 读不到数字 => 沿用红色检测
        self.assertEqual(yb.summon_blocked(s.cv), "召唤价格为红色")
        yb.summon(s.cv)
        self.assertNotIn("summon", s.clicks)

    def test_read_price(self):
        t = canvas().ocr(img("battle_live.png"), tag="test")
        self.assertEqual((yb.read_coins(t), yb.read_price(t)), (10630, 320))
        self.assertIsNone(yb.read_price([("7折", 0.654, 0.884)]))

    # ---- 开局不盲点空白 ----
    COUNTDOWN = [("幽怨邪煞", 0.5, 0.17), ("？", 0.3, 0.4), ("等待加入", 0.3, 0.45), ("10秒后开始", 0.5, 0.7)]

    def test_opening_no_blind_click(self):
        gray = Image.new("RGB", (396, 801), (60, 60, 60))
        yb.OPENING[0] = True
        s = StubCanvas([gray], self.COUNTDOWN)
        self.assertFalse(yb.clear_popups(s.cv))
        self.assertEqual(s.clicks, [])                # 没有弹窗文字 => 不点空白
        s2 = StubCanvas([gray], [("点击空白处跳过", 0.5, 0.8), ("观影免费", 0.3, 0.9)])
        yb.clear_popups(s2.cv)
        self.assertIn("blank", s2.clicks)             # 真弹窗照常关
        yb.OPENING[0] = False
        s3 = StubCanvas([gray], self.COUNTDOWN)
        yb.clear_popups(s3.cv)
        self.assertIn("blank", s3.clicks)             # 开局以外维持旧行为

    def test_opening_wait_countdown(self):
        gray, bt = Image.new("RGB", (396, 801), (60, 60, 60)), img("battle_L67.png")
        s = StubCanvas([gray] * 6 + [bt], self.COUNTDOWN)
        self.assertTrue(yb.opening_wait(s.cv, timeout=5, need=3))
        self.assertEqual(s.clicks, [])

    def test_play_battle_opening_no_blank(self):
        """开局整段：倒计时 -> 战斗；没有弹窗时一次空白都不点（升级/召唤本身用桩）"""
        gray, bt = Image.new("RGB", (396, 801), (60, 60, 60)), img("battle_L67.png")
        s = StubCanvas([gray] * 4 + [bt], self.COUNTDOWN)
        done = []
        real = (yb.bed_upgrade, yb.door_upgrade, yb.summon, yb.wait_battle)
        yb.bed_upgrade = lambda cv: done.append("bed")
        yb.door_upgrade = lambda cv: done.append("door")
        yb.summon = lambda cv: done.append("summon")
        yb.wait_battle = lambda cv, t0, se: "胜"
        try:
            self.assertEqual(yb.play_battle(s.cv, time.time()), "胜")
        finally:
            yb.bed_upgrade, yb.door_upgrade, yb.summon, yb.wait_battle = real
        self.assertEqual(done, ["bed"] * 2 + ["door"] * 3 + ["summon"] * 4)
        self.assertNotIn("blank", s.clicks)
        self.assertFalse(yb.OPENING[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
