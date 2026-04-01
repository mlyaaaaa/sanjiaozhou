# -*- coding: utf-8 -*-
"""
三角洲行动 - 交易行辅助脚本（示例实现）

运行: python main.py
依赖: 见 requirements.txt；Windows 需安装 Tesseract-OCR 及中文语言包（chi_sim）。

控制键（可在 config.json 中修改）:
  - 启动/继续: 默认 F8
  - 暂停: 默认 F10
  - 紧急停止: 默认 F9（立即停止主循环并退出监听线程外的逻辑）

重要说明:
  1. 本脚本仅供学习交流；使用自动化可能违反游戏用户协议并导致封号，请自行承担风险。
  2. 所有坐标、区域需按 1920x1080 窗口模式自行校准 config.json。
  3. 降低风险的核心手段：贝塞尔曲线移动（human_mouse）、非固定间隔（正态分布延迟）、
     避免机械式直线与固定节拍（见 human_mouse.human_delay）。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pyautogui
from pynput import keyboard

from human_mouse import burst_clicks_at_region, click_at_region_center, human_delay, move_bezier_to
from ocr_utils import capture_screen_region, configure_tesseract, ocr_region, parse_countdown_seconds, parse_price


# PyAutoGUI 默认在鼠标移到屏幕左上角时抛出异常紧急停止；保留该安全机制
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def setup_logging(cfg: Dict[str, Any]) -> None:
    log_cfg = cfg.get("logging", {})
    level_name = log_cfg.get("level", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)
    handlers: List[logging.Handler] = []
    if log_cfg.get("console", True):
        handlers.append(logging.StreamHandler(sys.stdout))
    log_file = log_cfg.get("file")
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers or [logging.StreamHandler(sys.stdout)],
        force=True,
    )


def normalize_key(name: str) -> str:
    """将 'f8' / 'F8' 规范为小写键名（如 f8）。"""
    return name.strip().lower()


def key_is_function(key: keyboard.Key | keyboard.KeyCode, fname: str) -> bool:
    """判断按键是否为指定的 F1–F12（fname 如 'f8'）。"""
    fn = normalize_key(fname)
    if not (fn.startswith("f") and fn[1:].isdigit()):
        return False
    num = int(fn[1:])
    attr = f"f{num}"
    fk = getattr(keyboard.Key, attr, None)
    return fk is not None and key == fk


class BotState:
    """线程安全的状态：运行 / 暂停 / 紧急停止。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.paused = True
        self.emergency_stop = False

    def set_emergency(self) -> None:
        with self._lock:
            self.emergency_stop = True
            self.running = False
            self.paused = True

    def start_or_resume(self) -> None:
        with self._lock:
            self.emergency_stop = False
            self.running = True
            self.paused = False

    def pause(self) -> None:
        with self._lock:
            self.paused = True

    def snapshot(self) -> tuple:
        with self._lock:
            return self.running, self.paused, self.emergency_stop


def process_cheap_buy(
    item: Dict[str, Any],
    human_cfg: Dict[str, Any],
    lang: str,
    log: logging.Logger,
) -> None:
    """
    低价购入：截取价格区域 OCR → 解析数字 → 低于阈值则贝塞尔移动到购买按钮并点击。
    """
    pr = item.get("price_region")
    br = item.get("buy_button_region")
    if not pr or not br:
        log.warning("条目 %s 缺少 price_region 或 buy_button_region，跳过", item.get("id"))
        return
    img = capture_screen_region(tuple(pr))
    raw = ocr_region(img, lang)
    price = parse_price(raw)
    max_p = int(item.get("max_price", 0))
    log.info(
        "[%s] OCR 价格区域 原文=%r 解析=%s 阈值=%s",
        item.get("name", item.get("id")),
        raw,
        price,
        max_p,
    )
    if price is None:
        return
    if price > max_p:
        return
    log.info("[%s] 价格满足条件，执行购买点击", item.get("name", item.get("id")))
    click_at_region_center(br, human_cfg=human_cfg, move_first=True)


def process_limited_snipe(
    item: Dict[str, Any],
    human_cfg: Dict[str, Any],
    lang: str,
    log: logging.Logger,
    state: BotState,
) -> None:
    """
    限时抢购：OCR 倒计时区域 → 剩余秒数 ≤ pre_click_seconds 时进入高频点击窗口。
    在结束前约 1 秒开始连点可提高抢到的概率（仍取决于网络与服务器）。
    """
    cr = item.get("countdown_region")
    br = item.get("buy_button_region")
    if not cr or not br:
        log.warning("条目 %s 缺少 countdown_region 或 buy_button_region，跳过", item.get("id"))
        return
    pre_sec = float(item.get("pre_click_seconds", 1.0))
    burst_dur = float(item.get("burst_duration_sec", 3.0))
    burst_iv = float(item.get("burst_interval_sec", 0.05))

    img = capture_screen_region(tuple(cr))
    raw = ocr_region(img, lang)
    remain = parse_countdown_seconds(raw)
    log.info(
        "[%s] 倒计时 OCR 原文=%r 解析剩余秒数=%s 提前量=%ss",
        item.get("name", item.get("id")),
        raw,
        remain,
        pre_sec,
    )
    if remain is None:
        return
    if remain > pre_sec:
        return

    # 进入抢购窗口前仍可做一次人性化移动到购买按钮中心（时间极紧时可缩短 burst 或省略本段）
    bx, by = int(br[0] + br[2] / 2), int(br[1] + br[3] / 2)
    move_bezier_to(
        bx,
        by,
        steps_min=human_cfg["bezier_steps_min"],
        steps_max=human_cfg["bezier_steps_max"],
        jitter_px=human_cfg["bezier_jitter_px"],
    )

    _, _, emerg = state.snapshot()
    if emerg:
        return

    log.warning("[%s] 进入高频点击窗口（约 %.2fs）", item.get("name", item.get("id")), burst_dur)
    n = burst_clicks_at_region(br, duration_sec=burst_dur, interval_sec=burst_iv)
    log.info("[%s] 高频点击结束，共点击 %d 次", item.get("name", item.get("id")), n)


def main_loop(cfg: Dict[str, Any], state: BotState) -> None:
    human_cfg = cfg["human"]
    lang = cfg.get("tesseract", {}).get("lang", "chi_sim+eng")
    loop_mean = float(cfg.get("loop_interval_sec", 0.45))
    items: List[Dict[str, Any]] = [i for i in cfg.get("items", []) if i.get("enabled", True)]
    log = logging.getLogger("trade")

    while True:
        running, paused, emerg = state.snapshot()
        if emerg:
            log.warning("检测到紧急停止，主循环退出。")
            break
        if not running or paused:
            time.sleep(0.05)
            continue

        for item in items:
            running, paused, emerg = state.snapshot()
            if emerg or not running or paused:
                break
            itype = item.get("type", "cheap_buy")
            try:
                if itype == "cheap_buy":
                    process_cheap_buy(item, human_cfg, lang, log)
                elif itype == "limited_snipe":
                    process_limited_snipe(item, human_cfg, lang, log, state)
                else:
                    log.warning("未知 type=%s，跳过 id=%s", itype, item.get("id"))
            except Exception as exc:  # noqa: BLE001 — 单条失败不应终止整体
                log.exception("处理条目失败 id=%s: %s", item.get("id"), exc)

        # 主循环周期间隔：以 loop_interval 为均值做正态随机，避免固定频率扫描
        human_delay(
            loop_mean,
            human_cfg["delay_std_sec"] * 1.2,
            human_cfg["delay_min_sec"],
            min(human_cfg["delay_max_sec"], loop_mean * 2.5),
        )


def main() -> None:
    config_path = Path(__file__).resolve().parent / "config.json"
    if not config_path.exists():
        print("未找到 config.json，请复制示例并修改后重试。", file=sys.stderr)
        sys.exit(1)

    cfg = load_config(config_path)
    setup_logging(cfg)

    tess = cfg.get("tesseract", {})
    configure_tesseract(tess.get("cmd") or "", tess.get("lang", "chi_sim+eng"))

    hotkeys = {k: normalize_key(v) for k, v in cfg.get("hotkeys", {}).items()}
    state = BotState()

    print("=== 三角洲交易行脚本 ===")
    print(f"配置文件: {config_path}")
    print(f"启动/继续: {hotkeys.get('start', 'f8')}  暂停: {hotkeys.get('pause', 'f10')}  紧急停止: {hotkeys.get('emergency_stop', 'f9')}")
    print("将鼠标快速移到屏幕左上角可触发 PyAutoGUI 安全停止。")
    print("按 启动键 开始；日志见控制台与 logging.file。\n")

    worker = threading.Thread(target=main_loop, args=(cfg, state), daemon=True)
    worker.start()

    # pynput 在部分系统上需主线程监听；此处用阻塞 listener，工作线程跑主循环
    try:

        def on_press(key: keyboard.Key | keyboard.KeyCode) -> Optional[bool]:
            start_k = hotkeys.get("start", "f8")
            pause_k = hotkeys.get("pause", "f10")
            stop_k = hotkeys.get("emergency_stop", "f9")
            log = logging.getLogger("trade")

            if key == keyboard.Key.esc:
                state.set_emergency()
                log.warning("按下 ESC，退出监听。")
                return False

            if key_is_function(key, start_k):
                state.start_or_resume()
                log.info("热键：启动/继续")
            elif key_is_function(key, pause_k):
                state.pause()
                log.info("热键：暂停")
            elif key_is_function(key, stop_k):
                state.set_emergency()
                log.warning("热键：紧急停止")
            return None

        with keyboard.Listener(on_press=on_press) as listener:
            listener.join()
    except KeyboardInterrupt:
        state.set_emergency()
        logging.getLogger("trade").info("收到 Ctrl+C，退出。")
    finally:
        state.set_emergency()
        worker.join(timeout=2.0)


if __name__ == "__main__":
    main()
