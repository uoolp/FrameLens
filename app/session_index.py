"""录制会话索引：扫描 sessions 目录，把每场录制的关键指标压成一行摘要并缓存。

历史列表要秒开，就不能每次重新读十几 MB 的 CSV；这里按
「文件名 + 大小 + 修改时间」做缓存键，只有新增或改动过的场次才重算。
"""
import json
import os

CACHE_NAME = ".session_index.json"


def _col_index(header, names):
    cols = [c.strip().lower().replace("\ufeff", "") for c in header.split(",")]
    out = {}
    for n in names:
        out[n] = cols.index(n) if n in cols else None
    return out


def _scan_frames(path):
    """流式解析帧 CSV，返回 (帧数, 时长秒, 平均FPS, 1%Low, 0.1%Low)。"""
    walls, fms = [], []
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            header = fh.readline()
            idx = _col_index(header, ("wall", "frame_ms"))
            wi, fi = idx["wall"], idx["frame_ms"]
            if wi is None or fi is None:
                return None
            need = max(wi, fi)
            for line in fh:
                parts = line.split(",")
                if len(parts) <= need:
                    continue
                try:
                    w = float(parts[wi])
                    m = float(parts[fi])
                except ValueError:
                    continue
                if m <= 0:
                    continue
                walls.append(w)
                fms.append(m)
    except OSError:
        return None
    if not fms:
        return None

    n = len(fms)
    total = sum(fms)
    avg_fps = 1000.0 * n / total if total > 0 else 0.0
    srt = sorted(fms)
    p99 = srt[min(n - 1, int(n * 0.99))]
    p999 = srt[min(n - 1, int(n * 0.999))]
    duration = (max(walls) - min(walls)) if walls else total / 1000.0
    return {
        "frame_count": n,
        "duration": round(duration, 1),
        "avg_fps": round(avg_fps, 1),
        "low1": round(1000.0 / p99, 1) if p99 > 0 else 0.0,
        "low01": round(1000.0 / p999, 1) if p999 > 0 else 0.0,
        "p99": round(p99, 2),
    }


def _scan_system(path):
    """流式解析系统 CSV，返回温度/占用均值与峰值。"""
    gpu, gt, ct, gt_peak, ct_peak = [], [], [], 0.0, 0.0
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            header = fh.readline()
            idx = _col_index(header, ("gpu_util", "gpu_temp", "cpu_temp"))
            gui, gti, cti = idx["gpu_util"], idx["gpu_temp"], idx["cpu_temp"]
            need = max(x for x in (gui, gti, cti) if x is not None) \
                if any(x is not None for x in (gui, gti, cti)) else -1
            if need < 0:
                return {}
            for line in fh:
                parts = line.split(",")
                if len(parts) <= need:
                    continue
                if gui is not None:
                    try:
                        gpu.append(float(parts[gui]))
                    except ValueError:
                        pass
                if gti is not None:
                    try:
                        v = float(parts[gti])
                        gt.append(v)
                        gt_peak = max(gt_peak, v)
                    except ValueError:
                        pass
                if cti is not None:
                    try:
                        v = float(parts[cti])
                        ct.append(v)
                        ct_peak = max(ct_peak, v)
                    except ValueError:
                        pass
    except OSError:
        return {}

    out = {}
    if gpu:
        out["gpu_util_avg"] = round(sum(gpu) / len(gpu), 1)
    if gt:
        out["gpu_temp_avg"] = round(sum(gt) / len(gt), 1)
        out["gpu_temp_max"] = round(gt_peak, 1)
    if ct:
        out["cpu_temp_avg"] = round(sum(ct) / len(ct), 1)
        out["cpu_temp_max"] = round(ct_peak, 1)
    return out


def _pairs(sessions_dir):
    """把 frames_xxx.csv 与 system_xxx.csv 按时间戳配对。"""
    stems = {}
    try:
        names = os.listdir(sessions_dir)
    except OSError:
        return stems
    for name in names:
        if not name.endswith(".csv") or name.startswith("_rec_") \
                or name.startswith("."):
            continue
        if name.startswith("frames_"):
            stem = name[len("frames_"):-4]
            stems.setdefault(stem, {})["frames"] = os.path.join(sessions_dir, name)
        elif name.startswith("system_"):
            stem = name[len("system_"):-4]
            stems.setdefault(stem, {})["system"] = os.path.join(sessions_dir, name)
    return stems


def load_index(sessions_dir, progress=None, force=False):
    """返回按时间倒序排列的场次摘要列表，带磁盘缓存。"""
    cache_path = os.path.join(sessions_dir, CACHE_NAME)
    cache = {}
    if not force and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cache = json.load(fh)
        except Exception:
            cache = {}

    pairs = _pairs(sessions_dir)
    total = len(pairs)
    fresh = {}
    out = []
    for i, (stem, files) in enumerate(sorted(pairs.items(), reverse=True)):
        frames = files.get("frames")
        if not frames or not os.path.exists(frames):
            continue
        try:
            st = os.stat(frames)
        except OSError:
            continue
        key = f"{st.st_size}|{int(st.st_mtime)}"
        cached = cache.get(stem)
        if cached and cached.get("key") == key:
            entry = cached
        else:
            if progress:
                progress(i + 1, total, stem)
            stat = _scan_frames(frames)
            if not stat:
                continue
            entry = {"key": key, "stem": stem, "mtime": st.st_mtime,
                     "size": st.st_size, "frames": frames,
                     "system": files.get("system")}
            entry.update(stat)
            entry.update(_scan_system(files.get("system")))
            fresh[stem] = entry
        out.append(entry)

    if fresh or len(out) != len(cache):
        try:
            merged = dict(cache)
            merged.update(fresh)
            keep = {e["stem"]: e for e in out}
            merged = {k: v for k, v in merged.items() if k in keep}
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(merged, fh, ensure_ascii=False, indent=0)
        except Exception:
            pass

    out.sort(key=lambda e: e.get("mtime", 0), reverse=True)
    return out


def fmt_duration(seconds):
    m, s = divmod(int(round(seconds)), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_mtime(ts):
    import time
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
