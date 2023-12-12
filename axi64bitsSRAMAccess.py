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

#from litex.soc.interconnect.axi     import axi_lite
from litex.soc.interconnect         import wishbone, axi

from litex.soc.interconnect.csr import *

from utils import *

# Utils --------------------------------------------------------------------------------------------

class AXIPacketStreamer(LiteXModule):
    def __init__(self, addressing="byte", init_adr=0, init_val=0, dw=32, adr_width=64, max_len=0x100):
        self.bus = bus = axi.AXILiteInterface(
            data_width    = dw,
            address_width = adr_width,
            addressing    = addressing)

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

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            NextValue(base_addr, _init_adr),
            NextValue(sent_data, init_val),
            If(self.start,
                NextState("WR_DAT-ADR"),
            )
        ),
        fsm.act("WR_DAT-ADR",
            bus.aw.valid.eq(1),
            bus.aw.addr.eq(base_addr),
            bus.w.data.eq(sent_data),
            bus.w.valid.eq(1),
            bus.w.strb.eq(2**(32//8) - 1),
            If(bus.aw.ready,
                If(bus.w.ready,
                    NextState("WAIT_B"),
                ).Else(
                    NextState("WAIT_DAT"),
                )
            ),
        ),
        fsm.act("WAIT_DAT",
            bus.w.valid.eq(1),
            bus.w.strb.eq(2**(32//8) - 1),
            If(bus.w.ready,
               NextValue(bus.w.strb, 0),
               NextState("WAIT_B")
            ),
        ),
        fsm.act("WAIT_B",
            bus.b.ready.eq(1),
            If(bus.b.valid,
                NextValue(base_addr, base_addr + _incr_adr),
                NextValue(sent_data, sent_data + 1),
                If(_init_adr + _max_len - _incr_adr == base_addr,
                    self.end.eq(1),
                    NextState("IDLE"),
                ).Else(
                    NextState("WR_DAT-ADR"),
                )
            )
        )

class AXIPacketChecker(LiteXModule):
    def __init__(self, addressing="byte", init_adr=0, init_val=0, dw=32, adr_width=64, max_len=0x100, verbose=True):
        self.bus = bus = axi.AXILiteInterface(
            data_width    = dw,
            address_width = adr_width,
            addressing    = addressing)

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
        self.base_addr  = Signal(adr_width)
        self.recv_data  = Signal(dw)

        # FSM.
        # ----
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            NextValue(self.base_addr, _init_adr),
            NextValue(self.recv_data, init_val),
            If(self.start,
                NextState("RD_ADDR"),
            )
        ),
        fsm.act("RD_ADDR",
            bus.ar.valid.eq(1),
            bus.ar.addr.eq(self.base_addr),
            If(bus.ar.ready,
               NextState("RD_DAT"),
            )
        ),
        fsm.act("RD_DAT",
            bus.r.ready.eq(1),
            If(bus.r.valid,
                If(bus.r.data != self.recv_data,
                    self.data_error.eq(1),
                ),
                NextState("WAIT_RD_ACK"),
            )
        ),
        fsm.act("WAIT_RD_ACK",
            NextValue(self.base_addr, self.base_addr + _incr_adr),
            NextValue(self.recv_data, self.recv_data + 1),
            If(_init_adr + _max_len - _incr_adr == self.base_addr,
               self.end.eq(1),
                NextState("IDLE"),
            ).Else(
                NextState("RD_ADDR"),
            )
        )

        if verbose:
            self.sync += [
                If(bus.r.valid & bus.r.ready,
                    Display("addr %08x dat_r %08x -> %08x",
                        self.base_addr, self.recv_data, bus.r.data),
                )
            ]

    def add_debug(self, banner):
        last_loop = Signal(32)
        data_error_msg = " Data Error @ 0x\%0{}x: 0x\%0{}x vs 0x\%0{}x".format(
            self.adr_width//4, self.dw//4, self.dw//4)
        self.sync += [
            If(self.data_error,
                Display(banner + data_error_msg,
                    self.base_addr,
                    self.bus.r.data,
                    self.recv_data,
                )
            ),
            timeline(self.data_error, [
                (128, [Finish()])
            ])
        ]

def add_ram(soc, name, bus_standard, origin, size, contents=[], mode="rwx"):
    print(bus_standard)
    ram_cls = {
        "wishbone": wishbone.SRAM,
        "axi-lite": axi.AXILiteSRAM,
    }[bus_standard]
    interface_cls = {
        "wishbone": wishbone.Interface,
        "axi-lite": axi.AXILiteInterface,
    }[soc.bus.standard]
    addressing = {
        "axi-lite": "byte",
        "wishbone": "word",
    }[bus_standard]
    ram_bus = interface_cls(
        data_width    = soc.bus.data_width,
        address_width = soc.bus.address_width,
        bursting      = soc.bus.bursting,
        addressing    = addressing,
    )
    ram = ram_cls(size, bus=ram_bus, init=contents, read_only=("w" not in mode), name=name)
    soc.bus.add_slave(name=name, slave=ram.bus, region=SoCRegion(origin=origin, size=size, mode=mode))
    soc.check_if_exists(name)
    soc.logger.info("RAM {} {} {}.".format(
        colorer(name),
        colorer("added", color="green"),
        soc.bus.regions[name]))
    soc.add_module(name=name, module=ram)
    if contents != []:
        self.add_config(f"{name}_INIT", 1)
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
    def __init__(self, default_trace=1, endpoint_bus_std="axi-lite"):
        # Parameters.
        assert endpoint_bus_std in ["axi-lite", "wishbone"]

        sys_clk_freq = int(1e6)

        # Platform.
        platform = Platform()
        self.comb += platform.trace.eq(1)

        # CRG --------------------------------------------------------------------------------------
        self.crg = CRG(platform.request("sys_clk"))

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(self, platform, bus_standard="axi-lite", clk_freq=sys_clk_freq, bus_address_width=64)

        # SRAMs.
        # ------
        add_ram(self, "myram0", "axi-lite",
            origin = 0x4_0000_0000,
            size   = 0x100,
        )
        add_ram(self, "myram1", "axi-lite",
            origin = 0x0_0002_0000,
            size   = 0x100,
        )

        if endpoint_bus_std == "axi-lite":
            addressing = "byte"
            # AXI Packet writer.
            # -----------------------
            self.pkt_stream_h = AXIPacketStreamer(addressing, 0x400000000, 0x12345678, 32, 64, 0x100)
            self.pkt_stream_l = AXIPacketStreamer(addressing, 0x2_0000, 0xCAFEBEBE, 32, 64, 0x100)

            # AXI Packet checker.
            # -----------------------
            self.pkt_check_h  = AXIPacketChecker(addressing, 0x400000000, 0x12345678, 32, 64, 0x100)
            self.pkt_check_l  = AXIPacketChecker(addressing, 0x2_0000, 0xCAFEBEBE, 32, 64, 0x100)
        else:
            addressing = "word"
            # Wishbone Packet writer.
            # -----------------------
            self.pkt_stream_h = WishbonePacketStreamer(addressing, 0x400000000, 0x12345678, 32, 64, 0x100)
            self.pkt_stream_l = WishbonePacketStreamer(addressing, 0x2_0000, 0xCAFEBEBE, 32, 64, 0x100)

            # Wishbone Packet checker.
            # -----------------------
            self.pkt_check_h  = WishbonePacketChecker(addressing, 0x400000000, 0x12345678, 32, 64, 0x100)
            self.pkt_check_l  = WishbonePacketChecker(addressing, 0x2_0000, 0xCAFEBEBE, 32, 64, 0x100)

        self.pkt_check_h.add_debug("[Checker High]")
        self.pkt_check_l.add_debug("[Checker Low]")

        self.bus.add_master("streamer_high", self.pkt_stream_h.bus)
        self.bus.add_master("checker_high",  self.pkt_check_h.bus)
        self.bus.add_master("streamer_low",  self.pkt_stream_l.bus)
        self.bus.add_master("checker_low",   self.pkt_check_l.bus)

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
    parser.add_argument("--endpoint-bus-std", default="axi_lite", help="Select generators/checker bus format: wishbone, axi-lite. (default: axi-lite)")
    verilator_build_args(parser)
    args = parser.parse_args()
    verilator_build_kwargs = verilator_build_argdict(args)
    sim_config = SimConfig(default_clk="sys_clk")

    # Create SoC.
    soc = SimSoC(
        endpoint_bus_std  = args.endpoint_bus_std
    )
    builder = Builder(soc)
    builder.build(sim_config=sim_config, **verilator_build_kwargs, run=1)

if __name__ == "__main__":
    main()

# limitation:
# - addressing must be "byte" for everything in a axi-lite full chain
# - when streamer/checker are wishbone this part must be "word" (rest remains "byte)
# - wishbone.SRAM can't be used ("word" only but Interface is "byte" only)
