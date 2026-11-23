"""瓶颈判定规则（纯函数，标签：GPU/CPU/VRAM/CAPPED/BALANCED/IDLE）。"""

REFRESH_CAPS = (30, 60, 75, 100, 120, 144, 165, 180, 240, 360)


def classify(m):
    """m 包含 fps, fps_cv, gpu_util, gpu_busy_ratio, cpu_total,
    cpu_core_max, vram_ratio, target_alive。返回 (label, reason)。"""
    if not m.get("target_alive"):
        return "IDLE", "未检测到目标进程"

    vram_ratio = m.get("vram_ratio")
    if vram_ratio is not None and vram_ratio >= 0.95:
        return "VRAM", "专用显存接近打满，可能触发爆显存卡顿"

    fps = m.get("fps") or 0
    if m.get("fps_cv") is not None and m["fps_cv"] < 0.02:
        if any(abs(fps - cap) / cap < 0.03 for cap in REFRESH_CAPS):
            return "CAPPED", "帧率被垂直同步/帧率上限锁定，无法判断真实瓶颈"

    gpu_util = m.get("gpu_util")
    gpu_busy_ratio = m.get("gpu_busy_ratio")
    if (gpu_util is not None and gpu_util >= 92) or \
       (gpu_busy_ratio is not None and gpu_busy_ratio >= 0.92):
        return "GPU", "GPU 持续满载，是当前帧率瓶颈"

    if gpu_util is not None and gpu_util < 80:
        if m.get("cpu_core_max") is not None and m["cpu_core_max"] >= 88:
            return "CPU", "单个 CPU 线程接近打满，GPU 在等待喂帧"
        if gpu_busy_ratio is not None and gpu_busy_ratio < 0.70 \
                and (m.get("cpu_total") or 0) >= 55:
            return "CPU", "CPU 喂帧不足，GPU 存在明显空闲"

    return "BALANCED", "CPU 与 GPU 负载较均衡，无明显单点瓶颈"
