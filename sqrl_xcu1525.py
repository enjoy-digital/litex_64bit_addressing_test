#!/usr/bin/env python3

#
# This file is part of LiteX-Boards.
#
# Copyright (c) 2020 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2023 Gwenhael Goavec-Merou <gwenhael@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import os

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *

from litex_boards.platforms import sqrl_xcu1525

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.led import LedChaser

from litedram.modules import MT40A512M8
from litedram.phy import usddrphy

from litepcie.phy.usppciephy import USPPCIEPHY
from litepcie.software import generate_litepcie_software

# CRG ----------------------------------------------------------------------------------------------

class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq, ddram_channel):
        self.rst       = Signal()
        self.cd_sys    = ClockDomain()
        self.cd_sys4x  = ClockDomain()
        self.cd_pll4x  = ClockDomain()
        self.cd_idelay = ClockDomain()

        # # #

        self.pll = pll = USPMMCM(speedgrade=-2)
        self.comb += pll.reset.eq(self.rst)
        pll.register_clkin(platform.request("clk300", ddram_channel), 300e6)
        pll.create_clkout(self.cd_pll4x, sys_clk_freq*4, buf=None, with_reset=False)
        pll.create_clkout(self.cd_idelay, 500e6)
        platform.add_false_path_constraints(self.cd_sys.clk, pll.clkin) # Ignore sys_clk to pll.clkin path created by SoC's rst.

        self.specials += [
            Instance("BUFGCE_DIV",
                p_BUFGCE_DIVIDE=4,
                i_CE=1, i_I=self.cd_pll4x.clk, o_O=self.cd_sys.clk),
            Instance("BUFGCE",
                i_CE=1, i_I=self.cd_pll4x.clk, o_O=self.cd_sys4x.clk),
        ]

        self.idelayctrl = USPIDELAYCTRL(cd_ref=self.cd_idelay, cd_sys=self.cd_sys)

# BaseSoC ------------------------------------------------------------------------------------------

class BaseSoC(SoCCore):
    def __init__(self, sys_clk_freq=125e6, ddram_channel=0,
        with_led_chaser = True,
        with_pcie       = False,
        with_sata       = False,
        sdram_test      = False,
        **kwargs):
        platform = sqrl_xcu1525.Platform()

        # CRG --------------------------------------------------------------------------------------
        self.crg = _CRG(platform, sys_clk_freq, ddram_channel)

        # SoCCore ----------------------------------------------------------------------------------
        SoCCore.__init__(self, platform, sys_clk_freq, ident="LiteX SoC on XCU1525", **kwargs)

        # DDR4 SDRAM -------------------------------------------------------------------------------
        if not self.integrated_main_ram_size:
            self.ddrphy = usddrphy.USPDDRPHY(
                pads             = platform.request("ddram", ddram_channel),
                memtype          = "DDR4",
                sys_clk_freq     = sys_clk_freq,
                iodelay_clk_freq = 500e6)
            self.add_sdram("sdram",
                phy           = self.ddrphy,
                module        = MT40A512M8(sys_clk_freq, "1:4"),
                size          = 0x40000000,
                l2_cache_size = 0
            )
            # Workadound for Vivado 2018.2 DRC, can be ignored and probably fixed on newer Vivado versions.
            platform.add_platform_command("set_property SEVERITY {{Warning}} [get_drc_checks PDCN-2736]")

            if sdram_test:
                from math import log2

                from litedram.frontend.wishbone import LiteDRAMWishbone2Native

                from litex.soc.integration.soc import SoCRegion
                from litex.soc.interconnect import wishbone

                # Request a LiteDRAM native port.
                port = self.sdram.crossbar.get_port()
                port.data_width = 2**int(log2(port.data_width)) # Round to nearest power of 2.

                # Add SDRAM region.
                myram_region = SoCRegion(
                    origin = 0x1_0000_00000,
                    size   = 0x8_0000_0000,
                    mode   = "rwx")
                self.bus.add_region("myram", myram_region)

                # Create Wishbone Slave.
                wb_sdram = wishbone.Interface(
                    data_width    = self.bus.data_width,
                    address_width = self.bus.address_width,
                    addressing    = "word")
                self.bus.add_slave(name="myram", slave=wb_sdram)

                litedram_wb = wishbone.Interface(
                    data_width    = port.data_width,
                    address_width = self.bus.address_width,
                    addressing    = "word")
                self.submodules += wishbone.Converter(wb_sdram, litedram_wb)

                # Wishbone Slave <--> LiteDRAM bridge.
                self.wishbone_bridge = LiteDRAMWishbone2Native(
                    wishbone     = litedram_wb,
                    port         = port,
                    base_address = self.bus.regions["myram"].origin >> 4 # WB is shifted by 32bits + 4 to fit 512Bits
                )

        # PCIe -------------------------------------------------------------------------------------
        if with_pcie:
            self.pcie_phy = USPPCIEPHY(platform, platform.request("pcie_x4"),
                data_width = 128,
                bar0_size  = 0x20000)
            self.add_pcie(phy=self.pcie_phy, ndmas=1)

        # SATA -------------------------------------------------------------------------------------
        if with_sata:
            from litex.build.generic_platform import Subsignal, Pins
            from litesata.phy import LiteSATAPHY

            # IOs
            _sata_io = [
                # SFP 2 SATA Adapter / https://shop.trenz-electronic.de/en/TE0424-01-SFP-2-SATA-Adapter
                ("qsfp2sata", 0,
                    Subsignal("tx_p", Pins("N9")),
                    Subsignal("tx_n", Pins("N8")),
                    Subsignal("rx_p", Pins("N4")),
                    Subsignal("rx_n", Pins("N3")),
                ),
            ]
            platform.add_extension(_sata_io)

            # RefClk, Generate 150MHz from PLL.
            self.cd_sata_refclk = ClockDomain()
            self.crg.pll.create_clkout(self.cd_sata_refclk, 150e6)
            sata_refclk = ClockSignal("sata_refclk")

            # PHY
            self.sata_phy = LiteSATAPHY(platform.device,
                refclk     = sata_refclk,
                pads       = platform.request("qsfp2sata"),
                gen        = "gen2",
                clk_freq   = sys_clk_freq,
                data_width = 16)

            # Core
            self.add_sata(phy=self.sata_phy, mode="read+write")

        # Leds -------------------------------------------------------------------------------------
        if with_led_chaser:
            self.leds = LedChaser(
                pads         = platform.request_all("user_led"),
                sys_clk_freq = sys_clk_freq)

# Build --------------------------------------------------------------------------------------------

def main():
    from litex.build.parser import LiteXArgumentParser
    parser = LiteXArgumentParser(platform=sqrl_xcu1525.Platform, description="LiteX SoC on XCU1525.")
    parser.add_target_argument("--sys-clk-freq",  default=125e6, type=float, help="System clock frequency.")
    parser.add_target_argument("--ddram-channel", default="0",               help="DDRAM channel (0, 1, 2 or 3).")
    parser.add_target_argument("--with-pcie",     action="store_true",       help="Enable PCIe support.")
    parser.add_target_argument("--driver",        action="store_true",       help="Generate PCIe driver.")
    parser.add_target_argument("--with-sata",     action="store_true",       help="Enable SATA support (over SFP2SATA).")
    parser.add_target_argument("--sdram-test", action="store_true",   help="SRAM test.")
    args = parser.parse_args()

    soc = BaseSoC(
        sys_clk_freq  = args.sys_clk_freq,
        ddram_channel = int(args.ddram_channel, 0),
        with_pcie     = args.with_pcie,
        with_sata     = args.with_sata,
        sdram_test    = args.sdram_test,
        **parser.soc_argdict
	)
    builder = Builder(soc, **parser.builder_argdict)
    if args.build:
        builder.build(**parser.toolchain_argdict)

    if args.driver:
        generate_litepcie_software(soc, os.path.join(builder.output_dir, "driver"))

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(builder.get_bitstream_filename(mode="sram"))

if __name__ == "__main__":
    main()

# ./sqrl_xcu1525.py --sdram-test --build --bus-address-width=64 --with-uartbone --uart-name crossover --csr-csv=csr.csv

# litex_server --uart --addr-width=64 --uart-port=/dev/ttyUSB2

# Read in common area (< 32bits adr):
# Write at > 32bits adr
# litex_cli --write 0x100000000 0x12345678
# read at > 32bits adr
# litex_cli --read  0x100000000
# wx100000000 : 0x12345678

# 0x40000000

# read at < 32bits adr
# litex_cli --read  0x40000000
# 0x40000000 : 0x12345678
# with bios (< 32bits adr):
# mem_read 0x40000000
# Memory dump:
# 0x40000000  78 56 34 12                                      xV4.


