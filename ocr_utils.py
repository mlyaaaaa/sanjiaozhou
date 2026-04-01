# -*- coding: utf-8 -*-
"""
屏幕文字识别辅助：价格解析、倒计时解析。
依赖 Tesseract；中文需安装 chi_sim 等语言包。
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

import pytesseract
from PIL import Image
import pyautogui


def configure_tesseract(cmd: str, lang: str) -> None:
    """设置 tesseract 可执行路径与默认语言。"""
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    # lang 在 image_to_string 时传入


def ocr_region(image: Image.Image, lang: str) -> str:
    """
    对整幅图像做 OCR；调用方可先用 crop 截取 ROI。
    PSM 7：单行文本，适合价格、倒计时条。
    """
    config = "--psm 7"
    text = pytesseract.image_to_string(image, lang=lang, config=config)
    return text.strip()


def parse_price(text: str) -> Optional[int]:
    """
    从 OCR 结果中提取整数价格。
    兼容常见分隔符与噪声；无法解析则返回 None。
    """
    if not text:
        return None
    # 保留数字，合并连续数字段，取最长或最后一个合理段
    digits_only = re.sub(r"[^\d]", "", text)
    if not digits_only:
        return None
    # 若出现多段，尝试取最大的一段（常为价格）
    parts = re.findall(r"\d{2,}", text.replace(",", "").replace("，", ""))
    if parts:
        try:
            return int(max(parts, key=len))
        except ValueError:
            pass
    try:
        return int(digits_only)
    except ValueError:
        return None


def parse_countdown_seconds(text: str) -> Optional[float]:
    """
    解析倒计时为「剩余秒数」。
    支持 MM:SS、M:SS、SS、以及纯秒数等变体；OCR 可能把 : 识别成 ; 或 . 
    """
    if not text:
        return None
    cleaned = text.replace("；", ":").replace(";", ":").replace("．", ".").strip()
    # MM:SS 或 M:SS
    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", cleaned)
    if m:
        minutes = int(m.group(1))
        seconds = int(m.group(2))
        return float(minutes * 60 + seconds)
    # 仅秒
    m2 = re.search(r"(\d{1,3})\s*秒", cleaned)
    if m2:
        return float(int(m2.group(1)))
    # 连续数字可能是 SS 误识别
    nums = re.findall(r"\d+", cleaned)
    if len(nums) >= 2:
        return float(int(nums[0]) * 60 + int(nums[1]))
    if len(nums) == 1 and len(nums[0]) <= 2:
        return float(int(nums[0]))
    return None


def capture_screen_region(region: Tuple[int, int, int, int]) -> Image.Image:
    """截取屏幕矩形区域 (x, y, w, h)，返回 PIL Image（RGB）。"""
    x, y, w, h = region
    return pyautogui.screenshot(region=(x, y, w, h))
