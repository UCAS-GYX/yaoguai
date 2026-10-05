# Mac 自动脚本 改动与发现记录（Asia/Shanghai）

## 02:20–02:30
- 02:21 Mac 上编译了 macOS Vision OCR 小工具 `~/yaoguai/ocr`（源码 `ocr.swift`，`swiftc -O`），整张画布 OCR 约 0.3 s，能读出「第14关」、金币数、面板文字、提示文字。
- 02:23 脚本 v3（部署 + `--resume --no-opening` 重启）：
  - `Canvas.ocr()`、`read_level`/`read_coins`、`check_alerts`（看到「其他地方登/重新登录/请上阵5个神将」→ 存 alert 截图并停止）。
  - 升床/升门：点开面板后 OCR 面板文字、点升级后 OCR 提示（升级成功/资源不足），面板变化时写入日志。
  - 战斗日志：每轮写 关卡/金币/召唤次数；battle_log.csv 改为 关卡、结果、召唤次数、结算文字。
  - `state.json` 记录每关失败次数；同关失败 5 次自动停止；已失败过的关卡召唤间隔从每 4 轮改为每 2 轮。
  - `--no-opening`：中途接手时不重复开局召唤。
  - `explore_build` 文件触发一次性「建造机关」探索。
- 发现（第14关）：
  - 床 7 级需要「前置：8级大门」+ 800 金 + 30 电；门 6→7 需 500 金 + 电力；电力 0 → 一直「资源不足」，金币闲置到 8800。
  - 「建造机关」按钮弹出的是机关卡片「恶魔地刺（流血，4 星）」的展示面板，不是发电机建造菜单；点空白就关闭，未见放置/花费。房间地面的「+」格子单击没有反应（只测了 (0.616,0.697) 一格）。
  - 胜利画面：OCR「挑战成功 / 获得奖励 / 双倍奖励(绿,广告) / 返回(黄, 约 (0.676,0.743))」。点「返回」后弹「提示：每关通关双倍奖励仅有1次领取机会…  观影双倍 / 放弃(约 (0.650,0.593))」→ 点「放弃」。
  - 回主页后底部「开始挑战」变成「已通关 请领取奖励」（所以主页颜色识别失败）。
  - 旧版 after_battle 的盲点坐标对这个界面都不对（「返回」(0.07,0.96) 无效，「拒绝双倍」(0.65,0.59) 点到了奖励物品）。
- 02:27–02:29 脚本 v4：
  - 结算识别：弹窗清理时若 OCR 到「挑战成功/失败」等字样立即判定结束（不再等 40 s）；结束后不再升级/召唤。
  - 升级/召唤前先确认还在战斗中（避免在结算画面上乱点）。
  - `click_text()` 按 OCR 文字点击按钮，黑名单：双倍/观影/刷新/领取/充值/购买/分享/关注/看广告/免费。
  - `handle_other()`：结算→「返回」；双倍确认框→「放弃」；主页「已通关 请领取奖励」→ 点宝箱 (0.3228,0.669) 领奖；「开始挑战/继续挑战/下一关」文字按钮。
  - after_battle 改为按画面识别循环处理，阵容页上什么都不点。
- 02:29 第15关（鬼哭狼嚎，Boss 假面怨灵）已开始（进入方式不明，可能是父代理或用户点的），用 `--resume` 重启脚本（不再带 --fix-lineup，第14/15关都能开打说明已是 5 人）。

## 02:30–02:37
- 「建造机关」机制弄清楚了（本游戏淘宝版实测）：
  - 左下「建造机关」按钮只弹出机关卡片展示（恶魔地刺/流血），点卡片不会建造，点空白关闭。
  - **真正的建造入口是点房间地面的空「+」格子** → 弹出「新增建筑」面板：
    - 1级发电机：每秒 +1 电，数量 0/2，价格 300 金，购买按钮约 (0.80,0.49)
    - 1级招财猫：每秒 +10 金，数量 0/1，价格 60 电，按钮约 (0.80,0.57)
  - 02:34 实测在 (0.50,0.76) 格子买了 1 座发电机：金币 -300，电力 15→17 开始上涨，格子上出现「1级」标签。（点 (0.83,0.49)/(0.85,0.485) 那两次没买成，(0.80,0.49) 成功。）
  - 门 6→7 要 500 金 + 30 电；床 7 要「前置 8 级大门」+ 800 金 + 30 电；床 3 要「前置 2 级大门」。没有电的话门卡在 6、床卡在 6，金币会闲置（第 14 关结束时闲置 8800）。
- 床的位置每关不一样（第 14 关床在 (0.75,0.77)，第 15 关在 (0.70,0.79)），第 15 关 02:32 以后点 (0.7315,0.743) 打不开床铺面板。
- 脚本 v5（02:36 部署，第 16 关开局时重启）：
  - 升级前检查面板文字含「床铺」/「大门」，没打开就关掉；床没打开时用 OCR 找房间下半部的「N级」标签重新定位床（每关缓存）。
  - `build_generator()`：门升级提示「资源不足」且金币 ≥350 时，依次点候选空格子，OCR 看到「新增建筑」才点发电机购买；读「数量：n/2」，最多 2 座；误开的面板点空白关闭。
  - 双倍奖励确认框优先于结算判断（「每关通关双倍奖励」里的「通关」曾被当成胜利字样，导致反复点「返回」）；胜利关键词收紧为 挑战成功/胜利/战斗成功/获得奖励。
  - 第 15 关结束流程（v4）验证通过：返回 → 放弃 → 主页「已通关 请领取奖励」→ 点宝箱 → 恭喜获得 → 开始挑战 → 阵容页挑战 → 第 16 关。

## 02:38–02:42（第 16 关）
- 自动建发电机验证成功：02:39:13 在 (0.50,0.76) 建第 1 座，02:40:24 在 (0.27,0.76) 建第 2 座；电力 17→67 上涨；门 6→7（500 金+30 电）→8（1000 金+60 电），02:41 门显示「已满级」（这关门上限 8）；床 7 升级成功（前置 8 级门 + 800 金 + 30 电）。
- 门满级后金币又开始闲置（3200+）。按用户规则没有加召唤。
- 脚本 v6（已拷到 Mac，用 `restart_at_boundary.py` 等第 16 关结束后自动重启加载）：读门面板「当前血/上限」，门血 < 50% 时这一轮额外召唤一次（用户规则允许）。

## 02:53–03:06 changes (v7 → patch17)
- **game_visible()** (osascript: Chrome frontmost + active tab on game.taobao.com). When not visible the bot does nothing (no click/shot/OCR) and does not steal focus. Browser-overlay text on canvas => clicks blocked 6 s.
- **Fast loop (02:55)**: wait_battle polls 0.5 s, battle_round every 20 s, popups dismissed immediately, end detected ~1–3 s; to_next_battle() is a click → wait_changed state machine; maybe_reload() execv's when yaoguai_bot.py mtime changes (at level boundaries); blank click (0.04,0.45) in battle, nav_blank (0.80,0.35) elsewhere; close_info closes 神将信息 (返回 0.882,0.785).
- **Auto-calibration (02:58)**: find_canvas on black page background; runs at nav start / repeated misses / missing pause button; rewrites calib.json if offset > 3 pt.
- **Freeze monitor (02:58)**: thread compares 80×160 frames each second (diff < 0.3 = frozen; normal battle 1.5–9). 5 s frozen → handle_freeze() (logs freeze, saves shot, tries close_info / picker / end / 确定 / blank). Still frozen 8 s later → writes `~/yaoguai/ALERT` (deleted automatically when frames move again).
- **Prerequisite chain (03:00)**: parse 前置N级大门 / 大门达到N级 → upgrade the prerequisite first (gives up after 4 rounds).
- **Green-arrow upgrades (03:03)**: bed/door positions change per level; the only reliable cue is the green up-arrow circle shown when a building is upgradable right now. Template `arrow_tpl.png` (18×18 at 391-wide), circular-masked; arrows score ≤ 23, others ≥ 37, threshold 28. Door arrow is on top wall (y < 0.64), bed lower right. No arrow → skip ("无箭头"); every 4th time falls back to the stored position (also reads door HP).
- **patch15–17 (03:04–03:05)**:
  - pick_card(): after each try (card1 → card2 → card3 first positions = (0.1988 / 0.3285 / 0.5014, 0.7932)) verify by OCR that 请点击选择/任一选项 disappeared. Old code accepted any pixel change → with a single card (centered 0.5014) it logged success while the picker stayed open (game 暂停中).
  - picker check added to clear_popups, wait_battle popup branch, handle_freeze and start of every battle_round.
  - find_door OCR fallback now clicks label + (0.063, 0.022) = the green arrow (e.g. 0.327,0.586), not the HP bar/label.
  - 升级 button: fixed position again — the OCR'd 「升级」 is the panel title, not the button.

## Reading events.log / ALERT
`~/yaoguai/events.log` is tab-separated: `time  level=N  screen=X  event=E  extra  OCR-text`. Events: battle_start, battle_end, popup, freeze, recovered, alert, stuck, picker_stuck, result. Shots saved only on these events (`shots/`). A watchdog should: `tail -f events.log`; treat `event=alert` or existence of `~/yaoguai/ALERT` as "needs a human"; `event=result` extra gives 胜/负 and fails count.

## 03:08 patch18 — review cadence & cheap monitoring (user request 03:07)
- Freeze monitor now compares only the **center 60%** of the canvas (80×160 grey thumbnail cropped to box (16,32,64,128)), ignoring animated edges / timer / bottom buttons. Diff < 0.3 for 5 s => bot self-recovers (handle_freeze). Still frozen 8 s after recovery => writes `~/yaoguai/ALERT`:
  line1 `time level=N reason`, line2 `shot=<path of 360px jpg in shots/>`, line3 `ocr=<canvas OCR text>`. ALERT is deleted automatically when the center moves again.
- New compact per-level summary `~/yaoguai/levels.log`: `L<n>  start  end  duration  result  summons=  fails=` (one line per level).
- Backup before patch: `yaoguai_bot_v8_0308.py`.

### Cheap-check recipe (watchdog: once per minute)
```
cd ~/yaoguai; tail -3 run.log | cut -c1-160; ls ALERT 2>/dev/null && cat ALERT | cut -c1-200; \
tail -2 levels.log 2>/dev/null; pgrep -f yaoguai_bot.py >/dev/null && echo RUN || echo DEAD; \
echo age=$(( $(date +%s) - $(stat -f %m run.log) ))s
```
- Normal: only that output. **No screenshots / montages / image reads.**
- Look at an image (the 360px shot named in ALERT, or one fresh `save_shot`) ONLY if: ALERT exists, OR `age` > 120 s (no log progress for > 2 min), OR process DEAD.
- Keep every output tiny: `cut -c1-160`, `grep -c` counts, never cat whole logs.

## 03:10 patch19 — small review shots (user request 03:09)
- save_shot() captures the **game canvas only** (cv.grab is the calibrated canvas), crops to the relevant region (picker/picker_stuck → bottom half y 0.5–1.0; popup → center y 0.2–0.8; freeze/alert → whole canvas), converts to **grayscale**, downsizes to **280 px wide**, JPEG **quality 50** (~10–15 KB). The ALERT `shot=` path uses the same format.
- Prefer the OCR text (ALERT line `ocr=`, events.log last column) over opening images; open the shot only when the OCR is not enough.
- Manual reviews: crop to canvas region the same way (e.g. `montage.py` only when ALERT / stalled > 2 min), never whole-screen grabs.

## 03:12–03:20 incidents & patches 20–21
- **03:12 bad auto-calibration**: after the L20 win, find_canvas was fooled by dark popup masks (got 300×574, then 369×715, later 370×715). Clicks landed in wrong places. Fix: autocalib now rejects any result whose width/height changes > 4 % or whose aspect ratio changes > 0.012 (log 「…不采用」). Real calib: (539,160,392,790).
- **03:13 随机事件 popup** (盘古 text + 2 reward items + green 免费 with video icon = AD). Fix: OCR 随机事件 → click top mask (0.5,0.13) (nav, battle popups, freeze recovery). 免费/视频/广告 added to NEVER_CLICK.
- **Generic close ladder** for unknown overlays: nav_blank (0.80,0.35) → top mask (0.5,0.13) → bottom-right 返回 (0.882,0.785) → OCR 关闭/放弃/取消/确定/X. After a full pass with the center unchanged 5 s → ALERT (small shot + OCR).
- Freeze monitor now also alerts when frozen > FREEZE_SEC+15 s even if the main loop never handled it (the 03:13 case: no ALERT was written).
- **03:14 --resume on the home screen** clicked the battle blank spot (0.04,0.45) which on home is the 炫彩殿堂 icon → opened 炫彩殿堂 (注入 page). Nothing was bought/injected (resources unchanged); closed via bottom-right back arrow. Fix: --resume now checks in_battle() first; if not in battle it runs the home → 开始挑战 flow.
- 03:16 two bot instances were briefly running (one started by someone else with `--resume`). Killed both, started exactly one. **Always `pgrep -f yaoguai_bot.py` before starting.**
- **patch21 (research guide, ALAS-style)**: per-button cooldown for nav buttons (chest/start 2.5 s, challenge 3 s, nav_blank/top_mask/info_back 2 s); click watchdog: one non-battle button ≥ 12 of last 15 clicks, or nav screen unchanged 60 s → one full close-ladder pass, then ALERT if still unchanged (`event=watchdog`).
- Not yet implemented from research (needs door HP reading during battle, risky to rush): 控门 (keep 1–2 door levels unbought at bosses, upgrade door when HP is low because upgrading heals), summon only while cost ≤ 5× income/s, 2 generators : 1 招财猫.

## 03:19 FINAL (patch22) — feature freeze, stability first
- ALERT is written ONLY when a popup/overlay is on screen (pause button hidden / unknown dialog in nav) AND the center stays unchanged ≥ 5 s after the bot's own recovery (close ladder). In normal battle (pause button visible) the freeze monitor never writes ALERT; the script handles everything else itself.
- 主线 tab recovery: if the bot lands on another bottom tab/page (神将/商城/副本…) and no 开始挑战/已通关 is visible, it clicks the bottom 「主线」 tab (OCR, y > 0.88).
- Lineup is NEVER changed: on the lineup page only 挑战 is clicked. (`--fix-lineup` exists only for restoring the 5th hero after an accidental removal and is never passed by default.) Team-switch suggestions from guides (L54 / L105 walls) are report-only.
- Backup of this final version: `/workspace/yaoguai/yaoguai_bot_final_0319.py` (box). No more edits planned.

### Watchdog recipe (FINAL — replaces the earlier one)
```
ls ~/yaoguai/ALERT 2>/dev/null; tail -2 ~/yaoguai/run.log | cut -c1-160
```
Only if ALERT exists: read it (line `shot=` = 280 px gray jpg of the canvas, `ocr=` text) — prefer the OCR text, open the jpg only if needed. Otherwise do nothing.

## 03:30 patch23 — door HP bar + 防守模式 (user request 03:27)
- Backups: Mac `~/yaoguai/yaoguai_bot_pre_defense_0330.py`, box `/workspace/yaoguai/yaoguai_bot_pre_defense_0330.py` (= final_0319, md5 aa5cb515…). New md5 586c5186….
- Deployed 03:30 during L23 by atomic mv; loads by itself via maybe_reload() at the L23→L24 boundary (no restart).
- **Door HP bar**: bright green segmented bar right of the door's 「N级」 label (segment colour ≈ (117,252,105); test g≥200, g−r≥100, b≤r−4, which excludes the label text, arrow ring and 击杀回怒 floating text). door_bar_locate(): longest ≥14 px run consistent over ≥3 rows in x 0.02–0.75, y 0.40–0.66 of a 391-wide thumbnail (L22: x 0.294–0.417, y 0.559–0.568, 37 columns at full HP, 8 segments ≈ 4.6 cols each).
- door_watch() runs from wait_battle only when the pause button is visible, reading once per second: median of the last 3 readings, max seen = full. Readings with 0 green are treated as hidden (popup/panel/dim) and skipped. Samples are dropped for 1–1.5 s after every battle_round, popup close and summon. Bar not found → quiet retry every 3 s, using the last known position. Door level change (BATTLE door_lv) → re-locate + reset baseline. At full HP, re-locate every 30 s (new baseline if the bar got longer).
- **防守模式**: 2 consecutive medians < 90 % of max → log 「门在掉血 … 进入防守模式」. Summons when the price isn't red, 3 s after the previous pick finishes. Before clicking: clear_popups + OCR (picker still open → pick it; end screen → stop). pick_card still uses card1 and verifies the picker closed. A failed pick waits 10 s. Exits with 「退出防守模式（原因）…」 when the bar is back ≥95 %, 20 s pass with no further drop (picker pause time not counted; re-enters only after another ≥6 % drop), 20 s pass with no readable bar, the door levels up, or the battle ends. Never writes ALERT or events.
- Existing <50 % rule kept; it now uses min(panel HP, live bar ratio).
- Round log line gains 「门血条 NN%」 / 「防守中」. levels.log gains `def_summons=` `def_modes=`.
- Switch off: set DEFENSE = False.

## 10:24 patch25 — promo popups + reload back-off (incident 10:07 after L60)
- Incident: after L60 a 地府公会战 promo (「前往」 + 「今日不再提示」) appeared; nav didn't know it, close ladder step 3 clicked bottom-right on the home screen (= 公会 tab), then crash-recovery reloaded the tab every ~75 s before the game finished loading (loop until manual restart 10:19).
- Backups: Mac `~/yaoguai/yaoguai_bot_pre_popup_1020.py`, box `/workspace/yaoguai/yaoguai_bot_pre_popup_1020.py` (md5 62c5b7a2…). New md5 e87831ee….
- **Promo popups** (`is_promo` / `handle_promo`): OCR 「不再提示」, a single 「前往」 button (task lists with several 前往 don't count), or titles 万鬼无限城/地府公会战/公会战开放/无限城 (title-only not on home). Handling: click the 「今日不再提示」 checkbox (OCR text box left edge − 0.9 char width; fallback (0.404,0.797); skipped if the point is near a 前往 box or only a title was seen) → click top mask (0.5,0.13) → re-check, up to 4 stacked layers. **Never clicks 前往.** Runs in nav (before watchdog/ladder), battle popups, freeze recovery, soft recovery and while waiting for load. events.log kind `promo`.
- Close ladder step 3 (bottom-right 返回) is skipped when the screen looks like home (≥2 of 邮箱/背包/任务/好友/排行/图鉴/挂机/商城).
- **page_recover** order: soft_clear (promo → info panel → ladder; if game UI visible and fewer than 3 failed nav attempts → no reload) → embedded 「全屏」 button (then wait_loaded 60 s) → reload, at most once per RELOAD_GAP=180 s; after a reload wait_loaded polls up to LOAD_WAIT=120 s for game UI (home/lineup/battle colours or game text), clicking 全屏 / closing promos meanwhile, never reloading. The old fixed "wait 60 s" step is gone.
- Nav also checks for the embedded 「全屏」 button every 10 unrecognised polls (only when the game tab is frontmost).
- Deployed 10:24 by atomic mv during L61; self-reloads at the L61→L62 break.

## 10:50 patch25 completed (bot STOPPED; edited + statically verified only, not deployed/run)
- Incident with the 10:24 build: from 10:28–10:31 it treated the plain home screen as a 万鬼无限城 promo, because the home activity entry reads 「地府公会战 万鬼无限城 归墟幻海」 and a title alone was enough. It clicked promo_blank ~60× and the watchdog fired. The 10:29 half-patch already needed title + body text; this build finishes the fix.
- Backups: `yaoguai_bot_pre_popup_1020.py` (last stable), `yaoguai_bot_half_1029.py` (interrupted half-patch). New md5 93744ba1….
- is_promo: matches 今日不再/不再提示/不再提醒, OR title (万鬼无限城/地府公会战/公会战开放/无限城/归墟幻海) + popup body text (开放中/已开启/本期倒计时), OR exactly one 「前往」. Returns False for the 双倍奖励 confirm (old flow clicks 放弃), for the battle screen, for the embedded web layout (淘宝 page words / 游戏中心 / 操作说明), and while muted.
- promo_checkbox: point = left edge of the OCR box for 「今日不再提示」 minus 0.9 char width (real popup OCR: (0.469,0.829), with 前往 at y 0.746–0.778). It uses the fallback (0.404,0.797) ONLY when the checkbox text was OCR'd but its box is missing. Seeing only a title or 前往 means a mask click only, because on home (0.40,0.80) is the 万鬼无限城 entry. It skips the click when the point is within 0.03/0.02 of any 前往 box, including split OCR 「前：」「往」. 前往 is never clicked.
- handle_promo: checkbox → 0.7 s → re-grab → top mask (0.5,0.13) → wait_changed → re-check, up to 4 layers. Mute guard: if 2 mask clicks in a row don't change the screen, or ≥8 layers are handled within 90 s, promo handling pauses for 90 s (is_promo → False) and the normal ladder/recovery takes over.
- OCR boxes are stored in the `OCR_LAST` deque (8 entries), matched by object identity, because the monitor thread also runs OCR.
- Embedded layout: nav, soft_clear and wait_loaded all check embedded_page() first and click the web 「全屏」 button (restore_fullscreen now also accepts OCR 「全屏 口」; the old exact match missed it). They never click canvas coordinates on the web layout. game_ui() is False while embedded.
- wait_loaded(timeout): polls up to 120 s after a reload (60 s after 全屏). Meanwhile it clicks 全屏 when embedded (and on every 3rd poll), closes promos, and returns when game UI shows. It never reloads.
- page_recover order is unchanged from the 10:24 build: soft_clear (promo/overlay first) → 全屏 → reload, at most once per 180 s.
- Tests: `./venv/bin/python test_patch25.py`, an offline test with pyautogui click/screenshot stubbed out to raise: 30/30 pass. It uses real Vision OCR of `_promo.png` (home), `_fs.png` (embedded + promo) and a crop of the promo. Checked: py_compile OK, pyflakes clean.

## 11:28 patch26 deployed (card-first-slot, fast popup reclick, PERIODIC_SUMMON=auto, MAX_LEVELS 9999, SPEED_2X off). Backup: yaoguai_bot_patch25_1127.py
