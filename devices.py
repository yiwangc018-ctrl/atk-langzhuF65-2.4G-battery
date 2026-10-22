# -*- coding: utf-8 -*-
"""devices.py —— 三台设备的电量读取协议。

1) ATK 鼠标（373B:101A，1K 接收器）
   接口：usage page 0xFF02，17 字节报文，Report ID 0x08。
   帧（16 字节，ATK HUB 的 COMPX 格式）：
     [0]=命令 [1]=状态 [2:4]=地址(LE) [4]=数据长 [5..14]=数据 [15]=校验
     校验 = (0x55 - (sum(帧[0:15]) + 8) & 0xFF) & 0xFF
   命令 0x03 = 查询鼠标是否在线，回复 data[5] == 1 表示在线；
   命令 0x04 = 读电量，回复 data[5]=电量% data[6]=充电标志
              data[7:9]=电压(mV, 大端)。
   鼠标休眠时不回复，需要用户动一下鼠标。

2) 狼蛛 F65 键盘（3554:FA09，2.4G 接收器）
   接口：usage page 0xFF02，20 字节报文，Report ID 0x13。
   帧：[0]=0x13 [1]=命令 [2]=子命令 [3]=序号 [4..18]=数据 [19]=校验
     校验 = sum(帧[0:19]) & 0xFF
   命令 0x4A 返回 2 字节状态：data[0]=电量(0..100) data[1]=状态位
   （0x01 是满电/在线时观察到的值）。
   该协议来自 AULA F87 逆向文档（同样的 3554:FA09 接收器）自测补充。

3) 竹林鸟玄武耳机（4C4A:4155，ZhuAudio ZG2 dongle）
   只暴露一个 consumer-control HID 接口（3 字节输入、0 输出、0 Feature），
   没有厂商通道；官方驱动里也没有任何电量相关字符串。结论：读不到。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import hidlib


ATK_VID, ATK_PID, ATK_UP = 0x373B, 0x101A, 0xFF02
AULA_VID, AULA_PID, AULA_UP = 0x3554, 0xFA09, 0xFF02
ZHU_VID, ZHU_PID = 0x4C4A, 0x4155


@dataclass
class Reading:
    level: Optional[int] = None      # 0-100
    charging: Optional[bool] = None
    voltage_mv: Optional[int] = None
    note: str = ""
    ok: bool = False


def _checksum_compx(frame: bytearray) -> int:
    return (0x55 - ((sum(frame[:15]) + 8) & 0xFF)) & 0xFF


def _compx_frame(cmd: int, data: bytes = b"") -> bytes:
    f = bytearray(16)
    f[0] = cmd
    f[1] = 0
    f[4] = len(data)
    f[5:5 + len(data)] = data
    f[15] = _checksum_compx(f)
    return bytes(f)


def _atk_ask(dev: hidlib.HidDevice, cmd: int, want: int, timeout_s: float = 0.6):
    """发一条命令并等待指定命令的回复。"""
    dev.write(bytes([0x08]) + _compx_frame(cmd))
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = dev.read(17, timeout_ms=150)
        if r and len(r) >= 10 and r[0] == 0x08 and r[1] == want:
            return r
    return None


def read_atk_mouse() -> Reading:
    iface = hidlib.find(ATK_VID, ATK_PID, ATK_UP)
    if iface is None:
        return Reading(note="未检测到 ATK 接收器")
    try:
        with hidlib.HidDevice(iface) as dev:
            online = None
            t0 = time.time()
            while time.time() - t0 < 4.0:
                st = _atk_ask(dev, 0x03, 0x03, timeout_s=0.5)
                if st is not None and st[6] == 1:
                    online = st
                    break
                time.sleep(0.25)
            if online is None:
                return Reading(note="鼠标休眠中，动一下鼠标")
            for _ in range(3):
                r = _atk_ask(dev, 0x04, 0x04, timeout_s=0.6)
                if r is not None:
                    level = r[6]
                    if level > 100:
                        level = 100
                    voltage = (r[8] << 8) | r[9]
                    return Reading(level=level, charging=(r[7] != 0),
                                   voltage_mv=voltage or None, ok=True)
                time.sleep(0.15)
            return Reading(note="鼠标回了在线，但没给电量（再动一下试试）")
    except OSError as e:
        return Reading(note=f"打开失败：{e}")


def _aula_frame(cmd: int, sub: int = 0x01, seq: int = 0) -> bytes:
    f = bytearray(20)
    f[0] = 0x13
    f[1] = cmd
    f[2] = sub
    f[3] = seq
    f[19] = sum(f[:19]) & 0xFF
    return bytes(f)


def read_aula_keyboard() -> Reading:
    iface = hidlib.find(AULA_VID, AULA_PID, AULA_UP)
    if iface is None:
        return Reading(note="未检测到狼蛛接收器")
    try:
        with hidlib.HidDevice(iface) as dev:
            for _ in range(4):
                dev.write(_aula_frame(0x4A))
                deadline = time.time() + 0.6
                while time.time() < deadline:
                    r = dev.read(20, timeout_ms=150)
                    if r and len(r) >= 8 and r[1] == 0x4A:
                        level = r[5]
                        if level > 100:
                            level = 100
                        # 第二个字节含义未确认（0x01），不当作充电标志
                        return Reading(level=level, charging=None,
                                       note=f"状态位 0x{r[6]:02X}", ok=True)
                time.sleep(0.1)
            return Reading(note="键盘没回复（敲一下键盘再试）")
    except OSError as e:
        return Reading(note=f"打开失败：{e}")


def read_zhu_headset() -> Reading:
    """竹林鸟玄武：目前只用于诊断，界面不再显示它。

    dongle 只暴露 USB Audio + 一个 3 字节 consumer HID（无 Output/Feature），
    耳机开/关机时接口集合不变，USB 侧拿不到电量；官方驱动面板也没有电量功能。
    以后如果官方固件开放厂商通道，再把它加回 read_all()。
    """
    iface = None
    for d in hidlib.enumerate_hid():
        if d.vid == ZHU_VID and d.pid == ZHU_PID:
            iface = d
            break
    if iface is None:
        return Reading(note="未检测到玄武接收器")
    return Reading(note="该耳机没有向 PC 暴露电量通道")


def read_all() -> dict[str, Reading]:
    return {
        "mouse": read_atk_mouse(),
        "keyboard": read_aula_keyboard(),
    }


if __name__ == "__main__":
    for k, v in read_all().items():
        if v.ok:
            extra = f" {v.voltage_mv}mV" if v.voltage_mv else ""
            charge = " 充电中" if v.charging else ""
            print(f"{k:9} {v.level:3d}%{extra}{charge}")
        else:
            print(f"{k:9} --   {v.note}")
