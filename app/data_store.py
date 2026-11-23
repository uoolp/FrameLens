"""线程安全数据总线：帧事件、系统样本、平滑 FPS、录制切片、CSV 导出与会话统计。"""
import csv
import os
import statistics
import threading
import time
from collections import deque

from bottleneck import classify


def foreground_frames(frames, min_fps=40.0, min_dur=1.0):
    """剔除"游戏不在前台"造成的连续低帧段。

    判定：连续 min_dur 秒以上帧率都低于 min_fps（默认 40）→ 整段视为后台/失焦，
    不计入平均帧与分位数统计。单帧或几帧的短暂掉帧（真实卡顿）仍保留。
    剔除后剩余不足 30% 时放弃剔除，避免把本来就低的帧率全删掉。
    """
    keep = []
    seg = []
    seg_t = 0.0
    t = 0.0
    for f in frames:
        fms = f.get("frame_ms")
        if not fms:
            continue
        if 1000.0 / fms < min_fps:
            if not seg:
                seg_t = t
            seg.append(f)
        else:
            if seg and (t - seg_t) >= min_dur:
                pass
            else:
                keep.extend(seg)
            seg = []
            keep.append(f)
        t += fms / 1000.0
    if seg and (t - seg_t) < min_dur:
        keep.extend(seg)
    if not frames or len(keep) < max(20, int(len(frames) * 0.3)):
        return list(frames)
    return keep


class DataStore:
    def __init__(self, gpu_luid, gpu_vram_bytes, sessions_dir):
        self._lock = threading.RLock()
        self.frames = deque(maxlen=60000)
        self.sys_samples = deque(maxlen=7200)
        self.latest_frame = None
        self.latest_sys = None
        self._fps_ema = None
        self.gpu_luid = gpu_luid
        self.gpu_vram = gpu_vram_bytes
        self.sessions_dir = sessions_dir
        self.start_wall = time.time()
        # 墙钟可能被系统校时（NTP）一次性回拨十几秒，直接记 time.time() 会让
        # 曲线时间轴倒流；改用单调时钟推进，保证样本时间严格递增。
        self._wall_base = time.time()
        self._clock_base = time.monotonic()
        self.recording = False
        self.record_start_wall = None
        self._rec_fh = None
        self._rec_writer = None
        self._rec_frame_path = None

    # ---------- 写入 ----------
    def _now(self):
        """单调递增的墙钟：不受系统校时影响，保证时间轴不倒流。"""
        return self._wall_base + (time.monotonic() - self._clock_base)

    def push_frame(self, frame):
        # 过滤异常帧：帧时间 < 1ms（>1000 FPS）为游戏退出/切换瞬间的脏数据
        fms = frame.get("frame_ms")
        if fms is None or fms < 1.0:
            return
        with self._lock:
            frame["wall"] = self._now()
            self.frames.append(frame)
            self.latest_frame = frame
            inst = 1000.0 / fms
            self._fps_ema = inst if self._fps_ema is None \
                else 0.9 * self._fps_ema + 0.1 * inst
            if self.recording and self._rec_writer is not None:
                self._rec_writer.writerow([
                    frame["wall"], fms, inst,
                    frame.get("gpu_busy_ms"), frame.get("gpu_wait_ms"),
                    frame.get("cpu_busy_ms"), frame.get("present_mode")])

    def push_sys(self, sample):
        with self._lock:
            sample["wall"] = self._now()
            gpu = sample["gpu"].get(self.gpu_luid, {})
            gpu_util = gpu.get("util", 0.0)
            vram_used = gpu.get("dedicated", 0.0)
            vram_ratio = (vram_used / self.gpu_vram) if self.gpu_vram else None
            core_max = max(sample["cpu_per_core"]) if sample["cpu_per_core"] else 0.0

            busy_ratio = self._gpu_busy_ratio_locked()
            label, reason = classify({
                "fps": self._fps_ema or 0.0,
                "fps_cv": self._fps_cv_locked(),
                "gpu_util": gpu_util,
                "gpu_busy_ratio": busy_ratio,
                "cpu_total": sample["cpu_total"],
                "cpu_core_max": core_max,
                "vram_ratio": vram_ratio,
                "target_alive": sample["target_alive"],
            })
            sample["gpu_util"] = gpu_util
            sample["gpu_vram_used"] = vram_used
            sample["vram_ratio"] = vram_ratio
            sample["gpu_busy_ratio"] = busy_ratio
            sample["cpu_core_max"] = core_max
            sample["gpu_temp"] = sample.get("gpu_temp")
            sample["bottleneck"] = label
            sample["bottleneck_reason"] = reason
            self.sys_samples.append(sample)
            self.latest_sys = sample

    # ---------- 读取 ----------
    @property
    def fps(self):
        with self._lock:
            return self._fps_ema or 0.0

    def snapshot(self):
        """供 UI 刷新的安全快照。"""
        with self._lock:
            return {
                "fps": self._fps_ema or 0.0,
                "latest_frame": self.latest_frame,
                "latest_sys": self.latest_sys,
                "frames": list(self.frames),
                "sys_samples": list(self.sys_samples),
                "recording": self.recording,
                "record_elapsed": (self._now() - self.record_start_wall)
                if self.recording and self.record_start_wall else 0.0,
            }

    def _gpu_busy_ratio_locked(self):
        now = time.time()
        vals = [f["gpu_busy_ms"] / f["frame_ms"]
                for f in self.frames
                if f.get("gpu_busy_ms") is not None
                and f.get("frame_ms") and now - f["wall"] < 1.2]
        return statistics.mean(vals) if vals else None

    def _fps_cv_locked(self):
        vals = [1000.0 / f["frame_ms"]
                for f in list(self.frames)[-30:]
                if f.get("frame_ms")]
        if len(vals) < 10:
            return None
        mean = statistics.mean(vals)
        return statistics.pstdev(vals) / mean if mean > 0 else None

    # ---------- 录制 ----------
    def start_recording(self):
        with self._lock:
            if self.recording:
                return
            self.recording = True
            self.record_start_wall = self._now()
            # 流式写临时帧文件，长录制不依赖内存环形缓冲
            self._rec_frame_path = (
                f"{self.sessions_dir}/_rec_{int(self.record_start_wall)}.tmp.csv")
            self._rec_fh = open(self._rec_frame_path, "w", newline="",
                                encoding="utf-8-sig")
            self._rec_writer = csv.writer(self._rec_fh)
            self._rec_writer.writerow(["wall", "frame_ms", "fps", "gpu_busy_ms",
                                       "gpu_wait_ms", "cpu_busy_ms", "present_mode"])

    def stop_recording(self):
        with self._lock:
            if not self.recording:
                return None
            start = self.record_start_wall
            self.recording = False
            self.record_start_wall = None
            if self._rec_fh is not None:
                try:
                    self._rec_fh.flush()
                    self._rec_fh.close()
                except Exception:
                    pass
            self._rec_fh = None
            self._rec_writer = None
            return start

    def _recorded_slice(self, start):
        if start is None:
            return list(self.frames), list(self.sys_samples)
        frames = [f for f in self.frames if f["wall"] >= start]
        samples = [s for s in self.sys_samples if s["wall"] >= start]
        return frames, samples

    # ---------- 导出与统计 ----------
    def export(self, sessions_dir, start=None):
        """导出帧级 + 系统级 CSV，返回 (frame_path, sys_path, summary)。"""
        with self._lock:
            _, samples = self._recorded_slice(start)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            frame_path = f"{sessions_dir}/frames_{stamp}.csv"
            sys_path = f"{sessions_dir}/system_{stamp}.csv"

            rec_tmp = self._rec_frame_path if start is not None else None
            if rec_tmp and os.path.exists(rec_tmp):
                # 录制模式：流式文件直接落盘，长录制不被内存环形缓冲截断
                os.replace(rec_tmp, frame_path)
                frames = self._read_frame_times(frame_path)
                self._rec_frame_path = None
            else:
                frames = (list(self.frames) if start is None
                          else [f for f in self.frames if f["wall"] >= start])
                self._write_frames_csv(frame_path, frames)

            self._write_sys_csv(sys_path, samples)
            return frame_path, sys_path, self.summarize(frames, samples)

    @staticmethod
    def _read_frame_times(path):
        out = []
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    out.append({"frame_ms": float(r["frame_ms"])})
                except (TypeError, ValueError):
                    continue
        return out

    @staticmethod
    def _write_frames_csv(frame_path, frames):
        with open(frame_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["wall", "frame_ms", "fps", "gpu_busy_ms",
                        "gpu_wait_ms", "cpu_busy_ms", "present_mode"])
            for f in frames:
                w.writerow([
                    f["wall"], f.get("frame_ms"),
                    1000.0 / f["frame_ms"] if f.get("frame_ms") else None,
                    f.get("gpu_busy_ms"), f.get("gpu_wait_ms"),
                    f.get("cpu_busy_ms"), f.get("present_mode"),
                ])

    @staticmethod
    def _write_sys_csv(sys_path, samples):
        with open(sys_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["wall", "fps_target_cpu", "cpu_total", "cpu_core_max",
                        "gpu_util", "gpu_vram_used", "vram_ratio",
                        "mem_percent", "gpu_temp", "gpu_hotspot",
                        "gpu_vram_temp", "cpu_temp", "bottleneck"])
            for s in samples:
                w.writerow([
                    s["wall"], s.get("target_cpu"), s["cpu_total"],
                    s["cpu_core_max"], s["gpu_util"], s["gpu_vram_used"],
                    s.get("vram_ratio"), s["mem_percent"], s.get("gpu_temp"),
                    s.get("gpu_hotspot"), s.get("gpu_vram_temp"),
                    s.get("cpu_temp"), s["bottleneck"],
                ])

    def summarize(self, frames, samples):
        raw_n = len(frames)
        frames = foreground_frames(frames)
        frame_times = [f["frame_ms"] for f in frames if f.get("frame_ms")]
        out = {"frame_count": len(frame_times),
               "dropped_bg_frames": max(0, raw_n - len(frames))}
        if frame_times:
            ft_sorted = sorted(frame_times)

            def pct(p):
                return ft_sorted[min(len(ft_sorted) - 1,
                                     int(round(len(ft_sorted) * p)))]
            out["avg_fps"] = 1000.0 / statistics.mean(frame_times)
            out["max_fps"] = 1000.0 / min(frame_times)
            out["min_fps"] = 1000.0 / max(frame_times)
            out["fps_1pct_low"] = 1000.0 / pct(0.99)
            out["fps_01pct_low"] = 1000.0 / pct(0.999)
            out["avg_frame_ms"] = statistics.mean(frame_times)

        def avg(key):
            vals = [s[key] for s in samples if s.get(key) is not None]
            return statistics.mean(vals) if vals else None

        out["avg_gpu_util"] = avg("gpu_util")
        out["avg_cpu_total"] = avg("cpu_total")
        out["avg_cpu_core_max"] = avg("cpu_core_max")
        out["avg_mem_percent"] = avg("mem_percent")
        out["avg_vram_ratio"] = avg("vram_ratio")

        temps = [s["gpu_temp"] for s in samples if s.get("gpu_temp") is not None]
        if temps:
            out["avg_gpu_temp"] = statistics.mean(temps)
            out["max_gpu_temp"] = max(temps)
            out["min_gpu_temp"] = min(temps)

        cpu_temps = [s["cpu_temp"] for s in samples
                     if s.get("cpu_temp") is not None]
        if cpu_temps:
            out["avg_cpu_temp"] = statistics.mean(cpu_temps)
            out["max_cpu_temp"] = max(cpu_temps)
            out["min_cpu_temp"] = min(cpu_temps)

        out["duration"] = (samples[-1]["wall"] - samples[0]["wall"]) if len(samples) >= 2 else 0.0

        counts = {}
        for s in samples:
            counts[s["bottleneck"]] = counts.get(s["bottleneck"], 0) + 1
        total = sum(counts.values()) or 1
        out["bottleneck_share"] = {k: v / total for k, v in counts.items()}
        return out
