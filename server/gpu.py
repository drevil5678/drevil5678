"""Enumerate Windows OpenCL devices in KataGo 1.16.3's device-index order."""
import ctypes
import os
from pathlib import Path


def opencl_devices(library=None):
    if library is None:
        if os.name != "nt":
            return []
        # Load the system loader, never a DLL from the current working directory.
        loader = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/OpenCL.dll"
        try:
            library = ctypes.WinDLL(str(loader))
        except OSError:
            return []
    uint, pointer = ctypes.c_uint, ctypes.c_void_p
    library.clGetPlatformIDs.argtypes = [uint, ctypes.POINTER(pointer), ctypes.POINTER(uint)]
    library.clGetPlatformIDs.restype = ctypes.c_int
    library.clGetDeviceIDs.argtypes = [pointer, ctypes.c_ulonglong, uint, ctypes.POINTER(pointer), ctypes.POINTER(uint)]
    library.clGetDeviceIDs.restype = ctypes.c_int
    library.clGetDeviceInfo.argtypes = [pointer, uint, ctypes.c_size_t, pointer, ctypes.POINTER(ctypes.c_size_t)]
    library.clGetDeviceInfo.restype = ctypes.c_int
    count = uint()
    result = library.clGetPlatformIDs(0, None, ctypes.byref(count))
    if result == -1001 or (result == 0 and count.value == 0):
        return []  # CL_PLATFORM_NOT_FOUND_KHR: driver has no installed platform.
    if result != 0 or count.value > 64:
        raise RuntimeError("OpenCL 無法列舉顯示卡平台，請更新顯示卡驅動")
    platforms = (pointer * count.value)()
    if library.clGetPlatformIDs(count, platforms, None) != 0:
        raise RuntimeError("OpenCL 平台讀取失敗")
    devices = []

    def info(device, field, value):
        if library.clGetDeviceInfo(device, field, ctypes.sizeof(value), ctypes.byref(value), None) != 0:
            raise RuntimeError("OpenCL 顯示卡資料讀取失敗")
        return value

    for platform in platforms:
        number = uint()
        # Exact same mask/order as KataGo, including CPU and accelerator entries.
        result = library.clGetDeviceIDs(platform, 2 | 4 | 8, 0, None, ctypes.byref(number))
        if result == -1:  # CL_DEVICE_NOT_FOUND
            continue
        if result != 0 or number.value > 1024:
            raise RuntimeError("OpenCL 裝置列舉失敗")
        ids = (pointer * number.value)()
        if library.clGetDeviceIDs(platform, 2 | 4 | 8, number, ids, None) != 0:
            raise RuntimeError("OpenCL 裝置讀取失敗")
        for device in ids:
            name = info(device, 0x102B, ctypes.create_string_buffer(4096)).value.decode("utf-8", errors="replace")
            vendor = info(device, 0x102C, ctypes.create_string_buffer(4096)).value.decode("utf-8", errors="replace")
            kind = info(device, 0x1000, ctypes.c_ulonglong()).value
            memory = info(device, 0x101F, ctypes.c_ulonglong()).value
            devices.append({"index": len(devices), "name": name, "vendor": vendor,
                            "gpu": bool(kind & 4), "memory_bytes": memory})
    return devices


def nvidia_device(devices, index=-1):
    choices = [device for device in devices if device["gpu"] and "nvidia" in device["vendor"].lower()]
    if index >= 0:
        return next((device for device in choices if device["index"] == index), None)
    return max(choices, key=lambda device: device["memory_bytes"], default=None)


def gpu_config(source, destination, device, concurrency):
    """Pin the detected NVIDIA device; keep tuning files within the runtime cwd."""
    lines = []
    for line in Path(source).read_text(encoding="utf-8-sig").splitlines():
        key = line.split("#", 1)[0].split("=", 1)[0].strip()
        if key == "numAnalysisThreads" or key == "homeDataDir" or key.startswith((
                "openclDeviceToUse", "openclGpuToUse", "deviceToUse", "gpuToUse")):
            continue
        lines.append(line)
    lines.extend([f"numAnalysisThreads = {concurrency}", f"openclDeviceToUse = {device['index']}",
                  "homeDataDir = katago-cache"])
    Path(destination).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Path(destination)
