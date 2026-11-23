"""CPU 温度：读取 Core Temp 的共享内存（CoreTempMappingObjectEx）。

原理：CPU 的 DTS 温度只能由 ring0 驱动读（rdmsr / SMN 通道），用户态程序无法直接访问。
Core Temp 自带内核驱动，并把结果发布到一块命名共享内存，官方支持第三方读取，
因此本模块不需要装驱动，只要 Core Temp 在运行即可。

实测反推的 CoreTempSharedDataEx 布局（2026-14，Ryzen 7 7840H，Core Temp 1.21）：
    offset 0      DWORD uiLoad[256]    每核负载百分比
    offset 1024   DWORD uiTjMax[128]   耐热温度，[0]=100
    offset 1536   DWORD uiCoreCnt      核心数 = 8
    offset 1540   DWORD uiCPUCnt       CPU 数 = 1
    offset 1544   float fTemp[256]     每核温度（摄氏度），前 uiCoreCnt 个有效
    offset 2576   float fVoltage[0]    核心电压（1.13V，用于交叉验证布局）
"""
import ctypes
import struct
import threading
import time
from ctypes import wintypes

_MAP_NAME = "CoreTempMappingObjectEx"
_MAP_SIZE = 8192
_OFF_LOAD = 0
_OFF_TJMAX = 1024
_OFF_CORE_CNT = 1536
_OFF_TEMP = 1544
_RETRY_SECONDS = 20.0

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenFileMappingW.restype = wintypes.HANDLE
_k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                               wintypes.DWORD, ctypes.c_size_t]
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.UnmapViewOfFile.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL


class CpuTemperature:
    """Core Temp 共享内存只读客户端；Core Temp 未运行时静默降级。"""

    def __init__(self):
        self.available = False
        self.error = ""
        self._handle = None
        self._view = None
        self._core_cnt = 0
        self._tjmax = 100
        self._last_try = 0.0
        self._lock = threading.Lock()
        self._connect()

    def _connect(self):
        self._last_try = time.time()
        self._disconnect()
        handle = _k32.OpenFileMappingW(0x0004, False, _MAP_NAME)  # FILE_MAP_READ
        if not handle:
            self.error = "Core Temp 未运行或未开启共享内存"
            return
        view = _k32.MapViewOfFile(handle, 0x0004, 0, 0, _MAP_SIZE)
        if not view:
            self.error = "共享内存映射失败"
            _k32.CloseHandle(handle)
            return
        self._handle = handle
        self._view = view
        try:
            raw = ctypes.string_at(view, _MAP_SIZE)
            core_cnt = struct.unpack_from("<I", raw, _OFF_CORE_CNT)[0]
            tjmax = struct.unpack_from("<I", raw, _OFF_TJMAX)[0]
        except OSError:
            self.error = "共享内存读取失败"
            self._disconnect()
            return
        # 合理性校验：核心数必须与逻辑核心数吻合，否则布局不符，宁可不显示
        if not (1 <= core_cnt <= 256):
            self.error = f"核心数异常({core_cnt})，结构不匹配"
            self._disconnect()
            return
        self._core_cnt = core_cnt
        self._tjmax = tjmax if 50 <= tjmax <= 130 else 100
        self.available = True
        self.error = ""

    def _disconnect(self):
        if self._view:
            try:
                _k32.UnmapViewOfFile(self._view)
            except Exception:
                pass
            self._view = None
        if self._handle:
            try:
                _k32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None
        self.available = False

    def read(self):
        """返回 (最高核心温度, [各核温度], 耐热温度)；不可用时全为 None。"""
        with self._lock:
            if self._view is None:
                # Core Temp 可能后启动，限频重连
                if time.time() - self._last_try < _RETRY_SECONDS:
                    return None, None, None
                self._connect()
                if self._view is None:
                    return None, None, None
            try:
                raw = ctypes.string_at(self._view, _MAP_SIZE)
            except OSError:
                # Core Temp 已退出，映射失效
                self._disconnect()
                self.error = "Core Temp 已退出"
                return None, None, None
            cores = []
            for i in range(self._core_cnt):
                value = struct.unpack_from("<f", raw, _OFF_TEMP + i * 4)[0]
                if 0 < value < 150:  # 过滤未启用核心的 0 值
                    cores.append(round(value, 1))
            if not cores:
                return None, None, None
            return max(cores), cores, self._tjmax

    def close(self):
        with self._lock:
            self._disconnect()
