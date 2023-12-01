#!/usr/bin/env python3

#
# This file is part of litex_64bit_addressing_test.
#
# Copyright (c) 2020-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2023 Florent Kermarrec <gwenhael@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import sys
import time
import argparse

from litex import RemoteClient

# Identifier Test ----------------------------------------------------------------------------------

def ident_test(port):
    wb = RemoteClient(port=port)
    wb.open()

    fpga_identifier = ""

    for i in range(256):
        c = chr(wb.read(wb.bases.identifier_mem + 4*i) & 0xff)
        fpga_identifier += c
        if c == "\0":
            break

    print(fpga_identifier)

    wb.close()

# SRAM Test ----------------------------------------------------------------------------------------

def sram_test(port):
    wb = RemoteClient(port=port)
    wb.open()

    def mem_read(base, length):
        data = []
        for addr in range(base, base + length, 4):
            data.append(wb.read(addr))
        return data

    def mem_dump(base, length):
        for addr in range(base, base + length, 4):
            if (addr%16 == 0):
                if addr != base:
                    print("")
                print("0x{:08x}".format(addr), end="  ")
            data = wb.read(addr)
            for i in reversed(range(4)):
                print("{:02x}".format((data >> (8*i)) & 0xff), end=" ")
        print("")

    def mem_write(base, datas):
        print(len(datas))
        for n, addr in enumerate(range(base, base + len(datas), 4)):
            if (addr%16 == 0):
                if addr != base:
                    print("")
                print("0x{:08x}".format(addr), end="  ")
                print("0x{:08x}".format(addr//4), end="  ")
            data = datas[n]
            for i in reversed(range(4)):
                print("{:02x}".format((data >> (8*i)) & 0xff), end=" ")
            wb.write(addr, data)
        print("")

    print(f"Fill First RAM (addr {wb.mems.myram0.base:08x} with counter:")
    mem_write(wb.mems.myram0.base, [i+0xCAFEBEBE for i in range(0x100)])
    print("")

    print(f"Fill Second RAM (addr {wb.mems.myram1.base:08x} with counter:")
    mem_write(wb.mems.myram1.base, [i+0x12345678 for i in range(0x100)])

    mem_dump(wb.mems.myram0.base, wb.mems.myram0.size)
    mem_dump(wb.mems.myram1.base, wb.mems.myram1.size)

    ram0 = mem_read(wb.mems.myram0.base, wb.mems.myram0.size)
    ram1 = mem_read(wb.mems.myram1.base, wb.mems.myram1.size)

    for i in range(0x100//4):
        if ram0[i] != 0xcafebebe + i or ram1[i] != 0x12345678+i:
            print(f"Error read {ram0[i]:08x}@{wb.mems.myram0.base+(4*i):08x} and", end=' ')
            print(f"{ram1[i]:08x}@{wb.mems.myram1.base+(4*i):08x} and", end=' ')
            return False
    print("ok")

    wb.close()
    return True

# Access Test ----------------------------------------------------------------------------------------

def access_test(port):
    import random
    wb = RemoteClient(port=port)
    wb.open()

    nb_iter   = 256
    mem_size  = wb.mems.myram0.size
    mem0_base = wb.mems.myram0.base
    mem1_base = wb.mems.myram1.base

    for it in range(256):
        offset = random.randint(0, mem_size-1)
        data0  = random.randint(1, 2**32-1)
        data1  = random.randint(1, 2**32-1)

        print(f"{it:3d}/{nb_iter:3d}")
        print("\tWrite RAM0 at 0x{:08x}: 0x{:08x}".format(mem0_base + offset, data0), end=' ')
        print("RAM1 at 0x{:08x}: 0x{:08x}.".format(mem1_base + offset, data1))
        wb.write(mem0_base + offset, data0)
        wb.write(mem1_base + offset, data1)
        rd_dat0 = wb.read(mem0_base + offset)
        rd_dat1 = wb.read(mem1_base + offset)
        print("\tRead  RAM0 at 0x{:08x}: 0x{:08x}".format(mem0_base + offset, rd_dat0), end=' ')
        print("RAM1 at 0x{:08x}: 0x{:08x}.".format(mem1_base + offset, rd_dat1))
        if data0 != rd_dat0:
            print(f"RAM0 error: mismatch between write and read: 0x{data0:08x} -> 0x{rd_dat0:08x}")
            return False
        if data1 != rd_dat1:
            print(f"RAM1 error: mismatch between write and read: 0x{data1:08x} -> 0x{rd_dat1:08x}")
            return False

    wb.close()

# Run ----------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SRAM Acess test utility")
    parser.add_argument("--port",   default="1234",      help="Host bind port")
    parser.add_argument("--ident",  action="store_true", help="Read FPGA identifier")
    parser.add_argument("--sram",   action="store_true", help="Test SRAM access (linear)")
    parser.add_argument("--access", action="store_true", help="Test random Write/Read access")
    args = parser.parse_args()

    port = int(args.port, 0)

    if args.ident:
        ident_test(port=port)

    if args.sram:
        if not sram_test(port=port):
            return

    if args.access:
        access_test(port=port)

if __name__ == "__main__":
    main()

