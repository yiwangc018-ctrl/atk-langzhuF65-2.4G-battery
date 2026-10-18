# -*- coding: utf-8 -*-
"""hidlib.py —— 枚举 Windows HID 设备并用 Overlapped I/O 读写（纯 ctypes）。

说明：同一句柄上"阻塞 ReadFile + 并发 WriteFile"会直接杀进程（见上游仓库
踩坑记录），所以这里统一用 FILE_FLAG_OVERLAPPED + 事件超时 + CancelIoEx。
"""
from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes, byref, sizeof, POINTER
from dataclasses import dataclass
from typing import Optional

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
INVALID_HANDLE = wintypes.HANDLE(-1).value


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


GUID_DEVINTERFACE_HID = GUID(
    0x4D1E55B2, 0xF16F, 0x11CF,
    (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30))


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [POINTER(GUID), wintypes.LPCWSTR,
                                          wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, POINTER(GUID), wintypes.DWORD,
    POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, POINTER(wintypes.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, POINTER(HIDP_CAPS)]
hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]

k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                            wintypes.HANDLE]
k32.CreateEventW.restype = wintypes.HANDLE
k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                          POINTER(wintypes.DWORD), POINTER(OVERLAPPED)]
k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                         POINTER(wintypes.DWORD), POINTER(OVERLAPPED)]
k32.WaitForSingleObject.restype = wintypes.DWORD
k32.GetOverlappedResult.argtypes = [wintypes.HANDLE, POINTER(OVERLAPPED),
                                    POINTER(wintypes.DWORD), wintypes.BOOL]
k32.CancelIoEx.argtypes = [wintypes.HANDLE, POINTER(OVERLAPPED)]
k32.ResetEvent.argtypes = [wintypes.HANDLE]


@dataclass
class Iface:
    path: str
    vid: int
    pid: int
    usage_page: int
    usage: int
    in_len: int
    out_len: int
    feat_len: int
    product: str


def _get_product(h) -> str:
    buf = ctypes.create_string_buffer(256)
    if hid.HidD_GetProductString(h, buf, 256):
        return buf.raw.decode("utf-16-le", "ignore").split("\x00")[0]
    return ""


def enumerate_hid() -> list[Iface]:
    out: list[Iface] = []
    hdev = setupapi.SetupDiGetClassDevsW(
        byref(GUID_DEVINTERFACE_HID), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev == INVALID_HANDLE or not hdev:
        return out
    try:
        did = SP_DEVICE_INTERFACE_DATA()
        did.cbSize = sizeof(did)
        i = 0
        while setupapi.SetupDiEnumDeviceInterfaces(
                hdev, None, byref(GUID_DEVINTERFACE_HID), i, byref(did)):
            need = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, byref(did), None, 0, byref(need), None)
            buf = ctypes.create_string_buffer(need.value)
            ctypes.memmove(buf, struct.pack("<I", 8), 4)
            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, byref(did), buf, need.value, byref(need), None):
                path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
                d = _describe(path)
                if d:
                    out.append(d)
            i += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


def _describe(path: str) -> Optional[Iface]:
    h = k32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                        OPEN_EXISTING, 0, None)
    if h in (None, INVALID_HANDLE):
        return None
    try:
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = sizeof(attrs)
        if not hid.HidD_GetAttributes(h, byref(attrs)):
            return None
        caps = HIDP_CAPS()
        pdd = ctypes.c_void_p()
        if hid.HidD_GetPreparsedData(h, byref(pdd)):
            hid.HidP_GetCaps(pdd, byref(caps))
            hid.HidD_FreePreparsedData(pdd)
        return Iface(path=path, vid=attrs.VendorID, pid=attrs.ProductID,
                     usage_page=caps.UsagePage, usage=caps.Usage,
                     in_len=caps.InputReportByteLength,
                     out_len=caps.OutputReportByteLength,
                     feat_len=caps.FeatureReportByteLength,
                     product=_get_product(h))
    finally:
        k32.CloseHandle(h)


def find(vid: int, pid: int, usage_page: int) -> Optional[Iface]:
    for d in enumerate_hid():
        if d.vid == vid and d.pid == pid and d.usage_page == usage_page:
            return d
    return None


class HidDevice:
    """Overlapped 读写的 HID 句柄。"""

    def __init__(self, iface: Iface):
        self.iface = iface
        self.h = k32.CreateFileW(
            iface.path, GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED, None)
        if self.h in (None, INVALID_HANDLE):
            raise OSError(f"CreateFile 失败 err={ctypes.get_last_error()}")

    def write(self, data: bytes, timeout_ms: int = 800) -> int:
        ov = OVERLAPPED()
        ov.hEvent = k32.CreateEventW(None, True, False, None)
        try:
            buf = ctypes.create_string_buffer(bytes(data), len(data))
            written = wintypes.DWORD(0)
            ok = k32.WriteFile(self.h, buf, len(data), byref(written), byref(ov))
            if not ok:
                err = ctypes.get_last_error()
                if err != ERROR_IO_PENDING:
                    raise OSError(f"WriteFile err={err}")
                if k32.WaitForSingleObject(ov.hEvent, timeout_ms) != 0:
                    k32.CancelIoEx(self.h, byref(ov))
                    raise TimeoutError("写超时")
                if not k32.GetOverlappedResult(self.h, byref(ov), byref(written), False):
                    raise OSError("GetOverlappedResult(write) 失败")
            return written.value
        finally:
            k32.CloseHandle(ov.hEvent)

    def read(self, length: int = 65, timeout_ms: int = 600) -> Optional[bytes]:
        ov = OVERLAPPED()
        ov.hEvent = k32.CreateEventW(None, True, False, None)
        try:
            buf = ctypes.create_string_buffer(length)
            got = wintypes.DWORD(0)
            ok = k32.ReadFile(self.h, buf, length, byref(got), byref(ov))
            if not ok:
                err = ctypes.get_last_error()
                if err != ERROR_IO_PENDING:
                    raise OSError(f"ReadFile err={err}")
                if k32.WaitForSingleObject(ov.hEvent, timeout_ms) != 0:
                    k32.CancelIoEx(self.h, byref(ov))
                    return None
                if not k32.GetOverlappedResult(self.h, byref(ov), byref(got), False):
                    raise OSError("GetOverlappedResult(read) 失败")
            return buf.raw[:got.value]
        finally:
            k32.CloseHandle(ov.hEvent)

    def close(self) -> None:
        if self.h not in (None, INVALID_HANDLE):
            k32.CloseHandle(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
