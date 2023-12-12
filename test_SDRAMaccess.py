#!/usr/bin/env python3

#
# This file is part of litex_64bit_addressing_test.
#
# Copyright (c) 2020-2023 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2023 Gwenhael Goavec-Merou <gwenhael@enjoy-digital.fr>
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

def sram_test(port, offset, seed):
    wb = RemoteClient(port=port)
    wb.open()

    base_addr = wb.mems.myram.base + offset

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
    
    size = 0x100

    print(f"Fill SDRAM (addr {base_addr:08x} with counter:")
    mem_write(base_addr, [i+seed for i in range(0x100)])
    print("")

    mem_dump(base_addr, size)

    ram0 = mem_read(base_addr, size)
    #ram1 = mem_read(wb.mems.myram1.base, wb.mems.myram1.size)

    for i in range(size//4):
        if ram0[i] != seed + i:
            print(f"Error read {ram0[i]:08x}@{base_addr+(4*i):08x} vs {seed + i:08x}")
            return False
    print("ok")

    wb.close()
    return True

# Write then Read ------------------------------------------------------------------------------------

def wr_rd(port, offset, value):
    wb = RemoteClient(port=port)
    wb.open()

    base_addr = wb.mems.myram.base + offset

    wb.write(base_addr, value)
    data = wb.read(base_addr)

    print(f"SDRAM access @0x{base_addr:08x} write: 0x{value:08x} read: 0x{data:08x}")

    wb.close()

# Run ----------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SRAM Acess test utility")
    parser.add_argument("--port",   default="1234",       help="Host bind port")
    parser.add_argument("--ident",  action="store_true",  help="Read FPGA identifier")
    parser.add_argument("--sram",   action="store_true",  help="Test SRAM access (linear)")
    parser.add_argument("--wr-rd",  action="store_true",  help="write then read seed @ mem addr + offset")
    parser.add_argument("--offset", default="0",          help="SDRAM address offset")
    parser.add_argument("--seed",   default="0xCAFEBEBE", help="SDRAM base value to write")
    args = parser.parse_args()

    port = int(args.port, 0)
    offset = int(args.offset, 0)
    seed   = int(args.seed, 0)

    if args.ident:
        ident_test(port=port)

    if args.sram:
        if not sram_test(port=port, offset=offset, seed=seed):
            return

    if args.wr_rd:
        wr_rd(port=port, offset=offset, value=seed)

if __name__ == "__main__":
    main()

