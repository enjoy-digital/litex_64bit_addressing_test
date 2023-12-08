#!/usr/bin/env python3
#
# This file is part of litex_64bit_addressing_test
#
# Copyright (c) 2023 Gwenhael Goavec-Merou <gwenhael@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import argparse

from migen import *
from migen.genlib.misc import timeline

from litex.gen import *

from litex.soc.interconnect         import wishbone

from litex.soc.interconnect.csr import *

# Utils --------------------------------------------------------------------------------------------

class WishbonePacketStreamer(LiteXModule):
    def __init__(self, addressing="byte", init_adr=0, init_val=0, dw=32, adr_width=64, max_len=0x100):
        self.bus = bus = wishbone.Interface(dw=32, adr_width=64, addressing=addressing)

        self.end   = Signal()
        self.start = Signal()

        # # #

        # Parameters.
        # -----------
        _init_adr = init_adr if addressing == "byte" else init_adr >> 2
        _incr_adr = 4 if addressing == "byte" else 1
        _max_len  = max_len if addressing == "byte" else max_len >> 2

        # Signals.
        # --------
        base_addr = Signal(adr_width)
        sent_data = Signal(dw)
        delay     = Signal(10)

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            NextValue(base_addr, _init_adr),
            NextValue(sent_data, init_val),
            NextValue(delay, 0),
            If(self.start,
                NextState("WR_DAT"),
            )
        ),
        fsm.act("WR_DAT",
            NextValue(bus.dat_w, sent_data),
            bus.adr.eq(base_addr),
            bus.stb.eq(1),
            bus.we.eq(1),
            bus.cyc.eq(1),
            bus.sel.eq(2**(32//8) - 1),
            If(bus.ack,
                NextState("WAIT_DELAY"),
            )
        ),
        fsm.act("WAIT_DELAY",
            NextValue(delay, delay + 1),
            If(delay == 10,
                NextValue(delay, 0),
                NextValue(base_addr, base_addr + _incr_adr),
                NextValue(sent_data, sent_data + 1),
                If(_init_adr + _max_len - _incr_adr == base_addr,
                    self.end.eq(1),
                    NextState("IDLE"),
                ).Else(
                    NextState("WR_DAT"),
                )
            )
        )

class WishbonePacketChecker(LiteXModule):
    def __init__(self, addressing="byte", init_adr=0, init_val=0, dw=32, adr_width=64, max_len=0x100):
        self.bus = bus = wishbone.Interface(dw=32, adr_width=64, addressing=addressing)

        self.end   = Signal()
        self.start = Signal()

        # # #

        # Parameters.
        # -----------
        self.dw        = dw
        self.adr_width = adr_width
        _init_adr      = init_adr if addressing == "byte" else init_adr >> 2
        _incr_adr      = 4 if addressing == "byte" else 1
        _max_len       = max_len if addressing == "byte" else max_len >> 2

        # Signals.
        # --------
        self.data_error = Signal()
        base_addr       = Signal(adr_width)
        self.recv_data  = Signal(dw)

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            NextValue(base_addr, _init_adr),
            NextValue(self.recv_data, init_val),
            If(self.start,
                NextState("RD_DAT"),
            )
        ),
        fsm.act("RD_DAT",
            bus.adr.eq(base_addr),
            bus.stb.eq(1),
            bus.we.eq(0),
            bus.cyc.eq(1),
            bus.sel.eq(2**(32//8) - 1),
            If(bus.ack,
                If(bus.dat_r != self.recv_data,
                    self.data_error.eq(1),
                ),
                NextState("WAIT_RD_ACK"),
            )
        ),
        fsm.act("WAIT_RD_ACK",
            NextValue(base_addr, base_addr + _incr_adr),
            NextValue(self.recv_data, self.recv_data + 1),

            If(_init_adr + _max_len - _incr_adr == base_addr,
               self.end.eq(1),
                NextState("IDLE"),
            ).Else(
                NextState("RD_DAT"),
            )
        )

        self.sync += [
            If(bus.stb & bus.cyc & ~bus.we & bus.ack,
                Display("addr %08x %08x dat_r %08x -> %08x", base_addr, bus.adr, self.recv_data, bus.dat_r),
            )
        ]

    def add_debug(self, banner):
        last_loop = Signal(32)
        data_error_msg = " Data Error @ 0x\%0{}x: 0x\%0{}x vs 0x\%0{}x".format(
            self.adr_width//4,
            self.dw//4,
            self.dw//4)
        self.sync += [
            If(self.data_error,
                Display(banner + data_error_msg,
                    self.bus.adr,
                    self.bus.dat_r,
                    self.recv_data,
                )
            ),
            timeline(self.data_error, [
                (128, [Finish()])
            ])
        ]
