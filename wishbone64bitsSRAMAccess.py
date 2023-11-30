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

from litex.build.generic_platform import *
from litex.build.sim              import SimPlatform
from litex.build.sim.config       import SimConfig
from litex.build.sim.verilator    import verilator_build_args, verilator_build_argdict

from litex.soc.integration.common   import *
from litex.soc.integration.soc      import SoCRegion
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder  import *

from litex.soc.interconnect         import wishbone

from litex.soc.interconnect.csr import *

# Utils --------------------------------------------------------------------------------------------

class WishbonetPacketStreamer(LiteXModule):
    def __init__(self, addressing="byte", init_adr=0, init_val=0, dw=32, adr_width=64, max_len=0x100):
        self.wb = wb = wishbone.Interface(dw=32, adr_width=64, addressing=addressing)

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
            NextValue(wb.dat_w, sent_data),
            wb.adr.eq(base_addr),
            wb.stb.eq(1),
            wb.we.eq(1),
            wb.cyc.eq(1),
            wb.sel.eq(2**(32//8) - 1),
            If(wb.ack,
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

class WishbonetPacketChecker(LiteXModule):
    def __init__(self, addressing="byte", init_adr=0, init_val=0, dw=32, adr_width=64, max_len=0x100):
        self.wb = wb = wishbone.Interface(dw=32, adr_width=64, addressing=addressing)

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
            wb.adr.eq(base_addr),
            wb.stb.eq(1),
            wb.we.eq(0),
            wb.cyc.eq(1),
            wb.sel.eq(2**(32//8) - 1),
            If(wb.ack,
                If(wb.dat_r != self.recv_data,
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
            If(wb.stb & wb.cyc & ~wb.we & wb.ack,
                Display("addr %08x %08x dat_r %08x -> %08x", base_addr, wb.adr, self.recv_data, wb.dat_r),
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
                    self.wb.adr,
                    self.wb.dat_r,
                    self.recv_data,
                )
            ),
            timeline(self.data_error, [
                (128, [Finish()])
            ])
        ]

# IOs ----------------------------------------------------------------------------------------------

_io = [
    # Clk / Rst.
    ("sys_clk", 0, Pins(1)),
    ("sys_rst", 0, Pins(1)),
]

# Platform -----------------------------------------------------------------------------------------

class Platform(SimPlatform):
    name = "sim"
    def __init__(self):
        SimPlatform.__init__(self, "SIM", _io)

# SimSoC -------------------------------------------------------------------------------------------

class SimSoC(SoCMini):
    def __init__(self, addressing="byte", default_trace=1):
        # Parameters.
        sys_clk_freq = int(1e6)

        # Platform.
        platform = Platform()
        self.comb += platform.trace.eq(1)

        # CRG --------------------------------------------------------------------------------------
        self.crg = CRG(platform.request("sys_clk"))

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(self, platform, clk_freq=sys_clk_freq, bus_address_width=64)

        # SRAMs.
        # ------
        self.add_ram("myram0",
            origin = 0x4_0000_0000,
            size   = 0x100,
        )
        self.add_ram("myram1",
            origin = 0x0_0002_0000,
            size   = 0x100,
        )

        # Wishbone Packet writer.
        # -----------------------
        self.pkt_stream_h = WishbonetPacketStreamer(addressing, 0x400000000, 0x12345678, 32, 64, 0x100)
        self.pkt_stream_l = WishbonetPacketStreamer(addressing, 0x2_0000, 0xCAFEBEBE, 32, 64, 0x100)

        # Wishbone Packet checker.
        # -----------------------
        self.pkt_check_h  = WishbonetPacketChecker(addressing, 0x400000000, 0x12345678, 32, 64, 0x100)
        self.pkt_check_l  = WishbonetPacketChecker(addressing, 0x2_0000, 0xCAFEBEBE, 32, 64, 0x100)

        self.pkt_check_h.add_debug("[Checker High]")
        self.pkt_check_l.add_debug("[Checker Low]")

        self.bus.add_master("streamer_high", self.pkt_stream_h.wb)
        self.bus.add_master("checker_high",  self.pkt_check_h.wb)
        self.bus.add_master("streamer_low",  self.pkt_stream_l.wb)
        self.bus.add_master("checker_low",   self.pkt_check_l.wb)

        # Pipeline sequence.
        # ------------------
        self.comb += [
            self.pkt_stream_l.start.eq(self.pkt_stream_h.end),
            self.pkt_check_h.start.eq(self.pkt_stream_l.end),
            self.pkt_check_l.start.eq(self.pkt_check_h.end),
        ]

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="RESET")
        fsm.act("RESET",
            self.pkt_stream_h.start.eq(1),
            NextState("WAIT-END-CHECK"),
        ),
        fsm.act("WAIT-END-CHECK",
            If(self.pkt_check_l.end,
               Finish()
            )
        )

        self.sync += If(self.pkt_check_l.end, Finish())

        # Debug ------------------------------------------------------------------------------------

        platform.add_debug(self, reset=default_trace)

def main():
    parser = argparse.ArgumentParser(description="Verilator test for 64bits addressing")
    parser.add_argument("--addressing", default="byte", help="Addressing mode (byte or word)")
    verilator_build_args(parser)
    args = parser.parse_args()
    verilator_build_kwargs = verilator_build_argdict(args)
    sim_config = SimConfig(default_clk="sys_clk")

    # Create SoC.
    soc = SimSoC(args.addressing)
    builder = Builder(soc)
    builder.build(sim_config=sim_config, **verilator_build_kwargs, run=1)

if __name__ == "__main__":
    main()

