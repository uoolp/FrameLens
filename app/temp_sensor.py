"""GPU 温度采集：AMD ADL2 PMLog 传感器。

本机 ADL2_Overdrive5/6_Temperature_Get 均不支持（rc=-1 / rc=-8），只能走 Overdrive8 的 PMLog。

结构：ADL2_New_QueryPMLogData_Get 返回 2052 字节 = 4 + 256×8，
      即 int sSize + 256 组 {int supported; int value}（supported 只占低字节，等价于 8 字节）。
索引即 ADL 官方枚举 ADLPMLogSensors 的值（见 AMD GPUOpen adl_structures.h / LibreHardwareMonitor 一致实现）：
    1  ADL_PMLOG_CLK_GFXCLK            核心时钟 MHz
    2  ADL_PMLOG_CLK_MEMCLK            显存时钟 MHz
    8  ADL_PMLOG_TEMPERATURE_EDGE     GPU 边缘温度 ℃   ← 主温度
    9  ADL_PMLOG_TEMPERATURE_MEM      显存温度 ℃
    14 ADL_PMLOG_FAN_RPM              风扇转速 RPM
    15 ADL_PMLOG_FAN_PERCENTAGE       风扇占空比 %
    19 ADL_PMLOG_INFO_ACTIVITY_GFX     GPU 利用率 %（注意：不是温度）
    23 ADL_PMLOG_ASIC_POWER            整卡功耗 W
    27 ADL_PMLOG_TEMPERATURE_HOTSPOT   热点温度 ℃
    28 ADL_PMLOG_TEMPERATURE_GFX       图形核心温度 ℃（部分型号无此传感器）
"""
import ctypes
import threading
from ctypes import CFUNCTYPE, Structure, byref, c_char, c_int, c_size_t, c_void_p

_MAX_PATH = 256
_PMLOG_INTS = 520           # 容纳 sSize + 256×2 个 int
_SENSOR_TEMP_EDGE = 8       # GPU 边缘温度（主温度）
_SENSOR_TEMP_GFX = 28       # 图形核心温度（部分型号提供，作为替代）
_SENSOR_TEMP_MEM = 9        # 显存温度
_SENSOR_TEMP_HOTSPOT = 27   # 热点温度
_SENSOR_CORE_CLOCK = 1
_SENSOR_MEM_CLOCK = 2
_SENSOR_FAN_RPM = 14
_SENSOR_ASIC_POWER = 23
_SENSOR_TEMP_CPU = 32       # CPU 温度（仅核显/APU 适配器提供）
_SENSOR_TEMP_SOC = 29
_SENSOR_CPU_CLOCK = 34
_SENSOR_CPU_POWER = 33


class _AdapterInfo(Structure):
    _fields_ = [
        ("iSize", c_int), ("iAdapterIndex", c_int),
        ("strUDID", c_char * _MAX_PATH), ("iBusNumber", c_int),
        ("iDeviceNumber", c_int), ("iFunctionNumber", c_int),
        ("iVendorID", c_int), ("strAdapterName", c_char * _MAX_PATH),
        ("strDisplayName", c_char * _MAX_PATH), ("iPresent", c_int),
        ("iExist", c_int), ("strDriverPath", c_char * _MAX_PATH),
        ("strDriverPathExt", c_char * _MAX_PATH),
        ("strPNPString", c_char * _MAX_PATH), ("iOSDisplayIndex", c_int),
    ]


_MEM_ALLOC = CFUNCTYPE(c_void_p, c_size_t)


def _decode(raw):
    return raw.split(b"\x00", 1)[0].decode("mbcs", errors="replace").strip()


class GpuTemperature:
    """ADL PMLog 温度读取器。初始化失败时 available=False，调用方应静默降级。"""

    def __init__(self, gpu_name_hint=""):
        self.available = False
        self.error = ""
        self.adapter_name = ""
        self.apu_name = ""
        self._apu_index = None
        self._ctx = c_void_p()
        self._adl = None
        self._index = None
        self._buf = (c_int * _PMLOG_INTS)()
        self._lock = threading.Lock()
        self._keep = []
        try:
            self._init(gpu_name_hint)
        except Exception as exc:  # 驱动缺失、无权限、结构变化都不应拖垮主程序
            self.error = f"{type(exc).__name__}: {exc}"

    def _init(self, hint):
        try:
            adl = ctypes.WinDLL("atiadlxx.dll")
        except OSError as exc:
            self.error = f"无法加载 atiadlxx.dll: {exc}"
            return

        def _alloc(size):
            block = ctypes.create_string_buffer(size)
            self._keep.append(block)
            return ctypes.cast(block, c_void_p)

        cb = _MEM_ALLOC(_alloc)
        self._keep.append(cb)  # 回调对象必须常驻，否则 ADL 后续调用会崩
        if adl.ADL2_Main_Control_Create(cb, 1, byref(self._ctx)) != 0:
            self.error = "ADL2_Main_Control_Create 失败"
            return

        count = c_int(0)
        adl.ADL2_Adapter_NumberOfAdapters_Get(self._ctx, byref(count))
        if count.value <= 0:
            self.error = "ADL 未枚举到适配器"
            return

        infos = (_AdapterInfo * count.value)()
        for i in range(count.value):
            infos[i].iSize = ctypes.sizeof(_AdapterInfo)
        adl.ADL2_Adapter_AdapterInfo_Get(
            self._ctx, ctypes.cast(infos, c_void_p), ctypes.sizeof(infos))

        candidates = []
        for i in range(count.value):
            if not infos[i].iPresent:
                continue
            candidates.append((i, _decode(infos[i].strAdapterName)))

        chosen = None
        if hint:
            hint_low = hint.lower()
            for idx, name in candidates:
                token = hint_low.split("radeon")[-1].strip()
                for part in (token, hint_low):
                    if part and part in name.lower():
                        chosen = (idx, name)
                        break
                if chosen:
                    break
        if chosen is None:
            # 回退：优先名字里带 RX 的独显，否则第一个在场的适配器
            for idx, name in candidates:
                if "rx" in name.lower():
                    chosen = (idx, name)
                    break
        if chosen is None and candidates:
            chosen = candidates[0]

        if chosen is None:
            self.error = "没有可用适配器"
            return

        self._adl = adl
        self._index = chosen[0]
        self.adapter_name = chosen[1]

        # CPU 温度只由核显/APU 适配器提供（CPU 与核显同 die），单独记一个索引
        self._scan_apu(candidates)

        # 探测一次，确认该适配器真的支持 PMLog 温度
        temp = self.read()
        if temp is None:
            self.error = f"适配器 {chosen[0]} 读不到温度传感器"
            return
        self.available = True
        self.error = ""

    def _scan_apu(self, candidates):
        """找到提供 TEMP_CPU(32) 的适配器（核显/APU），用于读 CPU 温度。"""
        for idx, name in candidates:
            if idx == self._index:
                continue
            data = self._query(idx)
            if not data:
                continue
            value = data.get(_SENSOR_TEMP_CPU)
            if value is not None and 0 < value < 120:
                self._apu_index = idx
                self.apu_name = name
                return

    def _query(self, index=None):
        """返回 {传感器索引: 数值}，调用方持有锁。"""
        idx = self._index if index is None else index
        ctypes.memset(self._buf, 0, ctypes.sizeof(self._buf))
        self._buf[0] = ctypes.sizeof(self._buf)
        rc = self._adl.ADL2_New_QueryPMLogData_Get(
            self._ctx, idx, ctypes.cast(self._buf, c_void_p))
        if rc != 0:
            return None
        out = {}
        for s in range(256):
            base = 1 + s * 2
            if self._buf[base] != 0:
                out[s] = self._buf[base + 1]
        return out

    def read_detail(self):
        """返回 (边缘温度, 热点温度, 显存温度, 核心频率, 显存频率, 风扇RPM, 整卡功耗)。

        传感器未提供时对应项为 None；温度经合理性校验，避免把其它传感器误当温度。
        """
        if not self._adl:
            return (None,) * 7
        with self._lock:
            data = self._query()
        if not data:
            return (None,) * 7

        def temp(idx):
            value = data.get(idx)
            # 0-120℃ 之外视为无效（部分卡在传感器缺失时报 0 或 54000）
            return value if value is not None and 0 < value < 120 else None

        edge = temp(_SENSOR_TEMP_EDGE) or temp(_SENSOR_TEMP_GFX)
        return (edge, temp(_SENSOR_TEMP_HOTSPOT), temp(_SENSOR_TEMP_MEM),
                data.get(_SENSOR_CORE_CLOCK), data.get(_SENSOR_MEM_CLOCK),
                data.get(_SENSOR_FAN_RPM), data.get(_SENSOR_ASIC_POWER))

    @property
    def apu_available(self):
        """核显/APU 适配器是否提供 CPU 温度。"""
        return self._apu_index is not None

    def read_cpu_detail(self):
        """返回 (CPU 温度, SoC 温度, CPU 频率 MHz, CPU 功耗 W)，需核显/APU 适配器。"""
        if not self._adl or self._apu_index is None:
            return (None,) * 4
        with self._lock:
            data = self._query(self._apu_index)
        if not data:
            return (None,) * 4

        def temp(idx):
            value = data.get(idx)
            return value if value is not None and 0 < value < 120 else None

        return (temp(_SENSOR_TEMP_CPU), temp(_SENSOR_TEMP_SOC),
                data.get(_SENSOR_CPU_CLOCK), data.get(_SENSOR_CPU_POWER))

    def read(self):
        return self.read_detail()[0]

    def close(self):
        if self._adl is not None and self._ctx:
            try:
                self._adl.ADL2_Main_Control_Destroy(self._ctx)
            except Exception:
                pass
        self._adl = None
        self._ctx = c_void_p()
        self.available = False
