# -*- coding: utf-8 -*-
"""
模拟人类鼠标轨迹与随机间隔。
使用三次贝塞尔曲线避免直线移动，降低被行为检测标记的风险。
"""

from __future__ import annotations

import math
import random
import time
from typing import Iterable, List, Sequence, Tuple

import pyautogui

Point = Tuple[float, float]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _bezier_point(t: float, p0: Point, p1: Point, p2: Point, p3: Point) -> Point:
    """三次贝塞尔曲线在参数 t∈[0,1] 上的点。"""
    u = 1.0 - t
    x = (
        u**3 * p0[0]
        + 3 * u**2 * t * p1[0]
        + 3 * u * t**2 * p2[0]
        + t**3 * p3[0]
    )
    y = (
        u**3 * p0[1]
        + 3 * u**2 * t * p1[1]
        + 3 * u * t**2 * p2[1]
        + t**3 * p3[1]
    )
    return (x, y)


def _random_control_points(
    start: Point,
    end: Point,
    max_offset: float,
) -> Tuple[Point, Point]:
    """
    在起点与终点附近随机生成两个控制点，使曲线呈弧形而非直线。
    max_offset 越大，弯曲程度越明显（仍受屏幕边界约束时可在外部调小）。
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.hypot(dx, dy) or 1.0
    # 垂直于位移方向的单位法向量，用于把控制点“掰弯”
    nx = -dy / dist
    ny = dx / dist
    lateral = random.uniform(-max_offset, max_offset)
    # 控制点大致分布在路径的 1/3 与 2/3 处，并带随机偏移
    p1 = (
        start[0] + dx * random.uniform(0.2, 0.45) + nx * lateral + random.uniform(-2, 2),
        start[1] + dy * random.uniform(0.2, 0.45) + ny * lateral + random.uniform(-2, 2),
    )
    p2 = (
        start[0] + dx * random.uniform(0.55, 0.8) - nx * lateral * random.uniform(0.5, 1.2)
        + random.uniform(-2, 2),
        start[1] + dy * random.uniform(0.55, 0.8) - ny * lateral * random.uniform(0.5, 1.2)
        + random.uniform(-2, 2),
    )
    return p1, p2


def move_bezier_to(
    x: int,
    y: int,
    *,
    steps_min: int = 20,
    steps_max: int = 40,
    jitter_px: float = 2.0,
    duration_jitter: float = 0.02,
) -> None:
    """
    将鼠标以贝塞尔曲线移动到整数坐标 (x, y)。
    steps：曲线离散采样点数，略多则更平滑；加入微量抖动模拟手部微颤。
    """
    start_x, start_y = pyautogui.position()
    p0: Point = (float(start_x), float(start_y))
    p3: Point = (float(x), float(y))
    dist = math.hypot(p3[0] - p0[0], p3[1] - p0[1])
    # 偏移量与距离成比例，避免短距离移动过度弯曲
    offset = _clamp(dist * 0.12, 8.0, 80.0) + random.uniform(0, 6)
    p1, p2 = _random_control_points(p0, p3, offset)
    steps = random.randint(steps_min, steps_max)
    points: List[Point] = []
    for i in range(steps + 1):
        t = i / steps
        # 轻微缓动：两端慢中间快，更接近人手
        t_ease = t * t * (3.0 - 2.0 * t)
        bx, by = _bezier_point(t_ease, p0, p1, p2, p3)
        jx = random.uniform(-jitter_px, jitter_px)
        jy = random.uniform(-jitter_px, jitter_px)
        points.append((bx + jx, by + jy))

    # 按段分配时间，使整体移动时长略有随机
    base_pause = duration_jitter + random.uniform(0.001, 0.004)
    for px, py in points:
        pyautogui.moveTo(int(round(px)), int(round(py)), duration=base_pause)
    # 终点消除抖动误差，确保落在目标像素上
    pyautogui.moveTo(x, y, duration=base_pause)


def human_delay(
    mean_sec: float,
    std_sec: float,
    min_sec: float,
    max_sec: float,
) -> None:
    """
    正态分布随机等待；截断在 [min_sec, max_sec]，避免极端值。
    用于点击间隔、循环周期等，避免固定节拍。
    """
    sample = random.gauss(mean_sec, std_sec)
    wait = _clamp(sample, min_sec, max_sec)
    time.sleep(wait)


def region_center(region: Sequence[int]) -> Tuple[int, int]:
    """region = [x, y, w, h] → 中心点像素坐标。"""
    x, y, w, h = region
    return int(x + w / 2), int(y + h / 2)


def click_at_region_center(
    region: Sequence[int],
    *,
    human_cfg: dict,
    move_first: bool = True,
) -> None:
    """移动到区域中心并点击（可选先移动再点，抢购模式可设为 False 仅点击）。"""
    cx, cy = region_center(region)
    if move_first:
        move_bezier_to(
            cx,
            cy,
            steps_min=human_cfg["bezier_steps_min"],
            steps_max=human_cfg["bezier_steps_max"],
            jitter_px=human_cfg["bezier_jitter_px"],
        )
        human_delay(
            human_cfg["delay_mean_sec"],
            human_cfg["delay_std_sec"],
            human_cfg["delay_min_sec"],
            human_cfg["delay_max_sec"],
        )
    pyautogui.click(cx, cy)


def burst_clicks_at_region(
    region: Sequence[int],
    duration_sec: float,
    interval_sec: float,
) -> int:
    """
    在限定时间内对区域中心高频点击；间隔略随机化。
    返回点击次数。
    """
    cx, cy = region_center(region)
    end = time.perf_counter() + duration_sec
    count = 0
    while time.perf_counter() < end:
        pyautogui.click(cx, cy)
        count += 1
        # 间隔在目标值附近微抖动，避免完全等间隔
        jittered = max(0.015, interval_sec * random.uniform(0.85, 1.15))
        time.sleep(jittered)
    return count
