"""系统与硬件采集：psutil（CPU/内存/目标进程）+ GpuPdh（GPU 利用率/显存）+ GPU/CPU 温度，1Hz。"""
import statistics
import threading
import time
from collections import deque

import psutil

from cpu_temp import CpuTemperature
from gpu_pdh import GpuPdh
from temp_sensor import GpuTemperature
from winproc import describe_processes


class SystemCollector:
    def __init__(self, target_exe, on_sample, gpu_name=""):
        self._target = target_exe.lower()
        # 取扩展名前的部分作为前缀，兼容 r5apex.exe 与 r5apex_dx12.exe
        self._target_prefix = self._target.split(".")[0]
        self._on_sample = on_sample
        self._gpu_name = gpu_name
        self._gpu = None
        self._temp = None
        self._temp_hist = deque(maxlen=3)
        self._cpu_temp = None
        self._cpu_temp_hist = deque(maxlen=3)
        self._thread = None
        self._alive = False
        self._target_proc = None
        self._target_pid = None
        self._proc_lock = threading.Lock()

    @property
    def running(self):
        return self._alive

    @property
    def temp_available(self):
        return bool(self._temp and self._temp.available)

    @property
    def temp_source(self):
        if self.temp_available:
            return self._temp.adapter_name
        return ""

    @property
    def cpu_temp_available(self):
        if self._temp is not None and self._temp.available and self._temp.apu_available:
            return True
        return bool(self._cpu_temp and self._cpu_temp.available)

    @property
    def cpu_temp_source(self):
        if self._temp is not None and self._temp.available and self._temp.apu_available:
            return "AMD 驱动（" + self._temp.apu_name + "）"
        if self._cpu_temp and self._cpu_temp.available:
            return "Core Temp 共享内存"
        return ""

    def start(self):
        if self._alive:
            return
        self._gpu = GpuPdh()
        self._gpu.sample()  # 建立基线
        try:
            self._temp = GpuTemperature(self._gpu_name)
        except Exception:
            self._temp = None
        try:
            self._cpu_temp = CpuTemperature()
        except Exception:
            self._cpu_temp = None
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        self._alive = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _resolve_target(self):
        """先用 Toolhelp32 确认进程是否存在（反作弊下也能读到名字），
        再尝试用 psutil 绑定句柄读取 CPU/内存；句柄被拒时仍判定为存活。"""
        with self._proc_lock:
            try:
                hits = describe_processes(self._target_prefix, limit=4)
            except Exception:
                hits = []
            if not hits:
                self._target_proc = None
                self._target_pid = None
                return None
            self._target_pid = hits[0][0]
            if self._target_proc is not None:
                try:
                    if self._target_proc.is_running():
                        return self._target_proc
                except Exception:
                    self._target_proc = None
            try:
                proc = psutil.Process(self._target_pid)
                self._target_proc = proc
                return proc
            except Exception:
                self._target_proc = None
                return None

    def _loop(self):
        next_tick = time.time()
        while self._alive:
            try:
                gpu_data = self._gpu.sample()
                # 温度：中值滤波剔除 ADL 偶发的初始化脏值
                temp = hotspot = vram_temp = core_clk = mem_clk = fan = power = None
                if self._temp is not None and self._temp.available:
                    try:
                        (temp, hotspot, vram_temp, core_clk, mem_clk,
                         fan, power) = self._temp.read_detail()
                    except Exception:
                        temp = hotspot = vram_temp = None
                    if temp is not None:
                        self._temp_hist.append(temp)
                        temp = statistics.median(self._temp_hist)
                # CPU 温度：优先 AMD 驱动（核显/APU 适配器的 TEMP_CPU 传感器，
                # 与 CPU 同 die，无需任何第三方程序）；读不到时回退 Core Temp 共享内存
                cpu_temp = cpu_temps = cpu_tjmax = None
                if self._temp is not None and self._temp.available:
                    try:
                        adl_cpu = self._temp.read_cpu_detail()
                        cpu_temp = adl_cpu[0]
                    except Exception:
                        cpu_temp = None
                if cpu_temp is None and self._cpu_temp is not None:
                    try:
                        cpu_temp, cpu_temps, cpu_tjmax = self._cpu_temp.read()
                    except Exception:
                        cpu_temp = cpu_temps = cpu_tjmax = None
                if cpu_temp is not None:
                    self._cpu_temp_hist.append(cpu_temp)
                    cpu_temp = statistics.median(self._cpu_temp_hist)
                proc = self._resolve_target()
                proc_cpu = proc_mem = proc_threads = None
                if proc is not None:
                    try:
                        proc_cpu = proc.cpu_percent()
                        proc_mem = proc.memory_info().rss
                        proc_threads = proc.num_threads()
                    except Exception:
                        proc = None

                sample = {
                    "wall": time.time(),
                    "cpu_total": psutil.cpu_percent(interval=None),
                    "cpu_per_core": psutil.cpu_percent(interval=None, percpu=True),
                    "mem_total": psutil.virtual_memory().total,
                    "mem_used": psutil.virtual_memory().used,
                    "mem_percent": psutil.virtual_memory().percent,
                    "gpu": gpu_data,
                    "gpu_temp": temp,
                    "gpu_hotspot": hotspot,
                    "gpu_vram_temp": vram_temp,
                    "gpu_core_clock": core_clk,
                    "gpu_mem_clock": mem_clk,
                    "fan_rpm": fan,
                    "gpu_power": power,
                    "cpu_temp": cpu_temp,
                    "cpu_temps": cpu_temps,
                    "cpu_tjmax": cpu_tjmax,
                    "target_cpu": proc_cpu,
                    "target_mem": proc_mem,
                    "target_threads": proc_threads,
                    "target_alive": proc is not None or self._target_pid is not None,
                    "target_pid": self._target_pid,
                }
                self._on_sample(sample)
            except Exception:
                pass

            next_tick += 1.0
            sleep_for = max(0.05, next_tick - time.time())
            time.sleep(sleep_for)
            next_tick = time.time()

    def stop(self):
        self._alive = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._gpu:
            self._gpu.close()
            self._gpu = None
        if self._temp:
            try:
                self._temp.close()
            except Exception:
                pass
            self._temp = None
        if self._cpu_temp:
            try:
                self._cpu_temp.close()
            except Exception:
                pass
            self._cpu_temp = None
