"""通过 PDH 性能计数器采集 GPU 利用率与显存（纯 ctypes，任何品牌通用）。

数据源（与任务管理器同源）：
  \\GPU Engine(*)\\Utilization Percentage      各进程/引擎利用率
  \\GPU Adapter Memory(*)\\Dedicated Usage     专用显存（字节）
  \\GPU Adapter Memory(*)\\Shared Usage        共享系统内存（字节）
"""
import ctypes
import re
from ctypes import wintypes

pdh = ctypes.WinDLL("pdh")

PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x00000000
PDH_CSTATUS_NEW_DATA = 0x00000001

_LUID_RE = re.compile(r"luid_0x[0-9A-Fa-f]+_0x([0-9A-Fa-f]+)")


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [
        ("CStatus", wintypes.DWORD),
        ("_pad", wintypes.DWORD),
        ("doubleValue", ctypes.c_double),  # union，8 字节即完整联合
    ]


class PDH_FMT_COUNTERVALUE_ITEM(ctypes.Structure):
    _fields_ = [
        ("szName", wintypes.LPWSTR),
        ("FmtValue", PDH_FMT_COUNTERVALUE),
    ]


pdh.PdhOpenQueryW.restype = ctypes.c_ulong
pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t,
                              ctypes.POINTER(wintypes.HANDLE)]
pdh.PdhAddCounterW.restype = ctypes.c_ulong
pdh.PdhAddCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR,
                               ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
pdh.PdhCollectQueryData.restype = ctypes.c_ulong
pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
pdh.PdhGetFormattedCounterArrayW.restype = ctypes.c_ulong
pdh.PdhGetFormattedCounterArrayW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
pdh.PdhCloseQuery.restype = ctypes.c_ulong
pdh.PdhCloseQuery.argtypes = [wintypes.HANDLE]


class GpuPdh:
    def __init__(self):
        self._query = wintypes.HANDLE()
        self._counters = {}
        hr = pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query))
        if hr != 0:
            raise OSError(f"PdhOpenQuery failed: 0x{hr & 0xFFFFFFFF:08X}")
        for key, path in (
            ("util", r"\GPU Engine(*)\Utilization Percentage"),
            ("dedicated", r"\GPU Adapter Memory(*)\Dedicated Usage"),
            ("shared", r"\GPU Adapter Memory(*)\Shared Usage"),
        ):
            h = wintypes.HANDLE()
            hr = pdh.PdhAddCounterW(self._query, path, 0, ctypes.byref(h))
            if hr != 0:
                raise OSError(f"PdhAddCounter {path} failed: 0x{hr & 0xFFFFFFFF:08X}")
            self._counters[key] = h

    def _counter_array(self, handle):
        # 首次探测所需缓冲区大小
        buf_size = wintypes.DWORD(0)
        item_count = wintypes.DWORD(0)
        hr = pdh.PdhGetFormattedCounterArrayW(
            handle, PDH_FMT_DOUBLE, ctypes.byref(buf_size),
            ctypes.byref(item_count), None)
        if hr != PDH_MORE_DATA and hr != 0:
            return []
        # GPU Engine 实例动态增减：失败时 PDH 会回写新的所需大小，跟随重试
        for _ in range(6):
            buf = ctypes.create_string_buffer(buf_size.value)
            hr = pdh.PdhGetFormattedCounterArrayW(
                handle, PDH_FMT_DOUBLE, ctypes.byref(buf_size),
                ctypes.byref(item_count), buf)
            if hr == 0:
                items = (PDH_FMT_COUNTERVALUE_ITEM * item_count.value).from_buffer(buf)
                out = []
                for it in items:
                    if it.FmtValue.CStatus in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
                        out.append((it.szName or "", it.FmtValue.doubleValue))
                return out
        return []

    def sample(self):
        """返回 {luid_low(int): {'util':%, 'dedicated':bytes, 'shared':bytes}}。"""
        pdh.PdhCollectQueryData(self._query)
        result = {}

        def bucket(luid):
            return result.setdefault(
                luid, {"util": 0.0, "dedicated": 0.0, "shared": 0.0})

        # 利用率：同一适配器取所有引擎/进程中的最大值（与任务管理器口径一致）
        for name, value in self._counter_array(self._counters["util"]):
            m = _LUID_RE.search(name)
            if m:
                b = bucket(int(m.group(1), 16))
                if value > b["util"]:
                    b["util"] = value

        for key in ("dedicated", "shared"):
            for name, value in self._counter_array(self._counters[key]):
                m = _LUID_RE.search(name)
                if m:
                    bucket(int(m.group(1), 16))[key] = value

        return result

    def close(self):
        if self._query:
            pdh.PdhCloseQuery(self._query)
            self._query = wintypes.HANDLE()


if __name__ == "__main__":
    import time
    g = GpuPdh()
    g.sample()  # 首次采样作为基线
    time.sleep(1.5)
    for luid, d in g.sample().items():
        print(f"LUID 0x{luid:08X}  util={d['util']:6.2f}%  "
              f"vram={d['dedicated'] / 1024**2:7.1f}MB  shared={d['shared'] / 1024**2:7.1f}MB")
    g.close()
