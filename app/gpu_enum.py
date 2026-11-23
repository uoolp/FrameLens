"""DXGI 适配器枚举：LUID -> 显卡名称/显存容量映射（纯 ctypes，无额外依赖）。"""
import ctypes
from ctypes import wintypes

dxgi = ctypes.WinDLL("dxgi")

# {7b7166ec-21c7-44ae-b21a-c9ae321ae369}
IID_IDXGIFactory = (ctypes.c_ubyte * 16)(
    0xEC, 0x66, 0x71, 0x7B, 0xC7, 0x21, 0xAE, 0x44,
    0xB2, 0x1A, 0xC9, 0xAE, 0x32, 0x1A, 0xE3, 0x69)


class DXGI_ADAPTER_DESC(ctypes.Structure):
    _fields_ = [
        ("Description", wintypes.WCHAR * 128),
        ("VendorId", ctypes.c_uint),
        ("DeviceId", ctypes.c_uint),
        ("SubSysId", ctypes.c_uint),
        ("Revision", ctypes.c_uint),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", ctypes.c_int64),
    ]


dxgi.CreateDXGIFactory.restype = ctypes.c_long
dxgi.CreateDXGIFactory.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]


def _vfn(ptr, index, restype, *argtypes):
    """从 COM 对象 vtable 取出方法（对象首字段是 vtable 指针，需二次解引用）。"""
    object_slots = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))
    vtable = ctypes.cast(object_slots[0], ctypes.POINTER(ctypes.c_void_p))
    return ctypes.cast(vtable[index], ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes))


def enumerate_adapters():
    """返回 [{name, luid_low, luid_high, vram_bytes, vendor_id}]，按 DXGI 顺序。"""
    factory = ctypes.c_void_p()
    hr = dxgi.CreateDXGIFactory(ctypes.byref(IID_IDXGIFactory), ctypes.byref(factory))
    if hr != 0:
        raise OSError(f"CreateDXGIFactory failed: 0x{hr & 0xFFFFFFFF:08X}")

    # IDXGIFactory 继承 IDXGIObject(4 方法)，EnumAdapters 是 vtable index 7
    enum_adapters = _vfn(factory, 7, ctypes.c_long,
                         ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
    factory_release = _vfn(factory, 2, ctypes.c_ulong)

    adapters = []
    i = 0
    while True:
        adapter = ctypes.c_void_p()
        hr = enum_adapters(factory, i, ctypes.byref(adapter))
        if hr != 0:  # DXGI_ERROR_NOT_FOUND
            break
        # IDXGIAdapter::GetDesc 是 vtable index 8
        get_desc = _vfn(adapter, 8, ctypes.c_long, ctypes.POINTER(DXGI_ADAPTER_DESC))
        adapter_release = _vfn(adapter, 2, ctypes.c_ulong)

        desc = DXGI_ADAPTER_DESC()
        if get_desc(adapter, ctypes.byref(desc)) == 0:
            luid = desc.AdapterLuid & 0xFFFFFFFFFFFFFFFF
            adapters.append({
                "name": desc.Description,
                "luid_low": luid & 0xFFFFFFFF,
                "luid_high": (luid >> 32) & 0xFFFFFFFF,
                "vram_bytes": int(desc.DedicatedVideoMemory),
                "vendor_id": desc.VendorId,
            })
        adapter_release(adapter)
        i += 1

    factory_release(factory)
    return adapters


if __name__ == "__main__":
    for a in enumerate_adapters():
        print(f"{a['name']}  LUID={a['luid_high']:08X}_{a['luid_low']:08X}  "
              f"VRAM={a['vram_bytes'] / 1024**3:.2f}GB  vendor=0x{a['vendor_id']:04X}")
