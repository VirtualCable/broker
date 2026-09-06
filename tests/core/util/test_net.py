#
# Copyright (c) 2022 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#    * Neither the name of Virtual Cable S.L. nor the names of its contributors
#      may be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import ipaddress
import logging

from uds.core.util import net

from ...utils.test import UDSTestCase

logger: logging.Logger = logging.getLogger(__name__)


class RecoverIpsTest(UDSTestCase):
    """
    Contracts for net.recover_ips, focused on the X-Forwarded-For sanitization:
    every component of the header MUST be a valid IP, anything else is dropped
    (an attacker can inject arbitrary data on XFF, and downstream consumers --
    i.e. custom authenticator javascript -- must never see it).
    """

    def test_no_xff(self) -> None:
        info = net.recover_ips("1.2.3.4", "")
        self.assertEqual(info.ip, "1.2.3.4")
        self.assertEqual(info.ip_proxy, "1.2.3.4")
        self.assertEqual(info.ip_version, 4)

    def test_valid_xff_is_reversed(self) -> None:
        # XFF: CLIENT, NEAR_PROXY (nginx is remote_addr)
        info = net.recover_ips("10.0.0.1", "203.0.113.7, 10.0.0.2")
        self.assertEqual(info.ip, "10.0.0.1")
        self.assertEqual(info.ip_proxy, "10.0.0.2")  # Reversed, nearest proxy first

    def test_invalid_entries_are_dropped(self) -> None:
        # Attacker injected garbage through XFF must never reach consumers
        for injected in (
            "<script>alert(1)</script>",
            "javascript:alert(1)",
            "not-an-ip",
            "1.2.3.4.5",
            "999.999.999.999",
            "' OR 1=1 --",
        ):
            with self.subTest(injected=injected):
                info = net.recover_ips("10.0.0.1", f"{injected}, 203.0.113.7, {injected}")
                self.assertEqual(info.ip, "10.0.0.1")
                self.assertEqual(info.ip_proxy, "203.0.113.7")

    def test_empty_entries_are_dropped(self) -> None:
        info = net.recover_ips("10.0.0.1", "203.0.113.7, , 10.0.0.2,")
        self.assertEqual(info.ip, "10.0.0.1")
        self.assertEqual(info.ip_proxy, "10.0.0.2")

    def test_all_entries_invalid_falls_back_to_remote_addr(self) -> None:
        info = net.recover_ips("10.0.0.1", "<script>alert(1)</script>, not-an-ip")
        self.assertEqual(info.ip, "10.0.0.1")
        self.assertEqual(info.ip_proxy, "10.0.0.1")

    def test_ipv6_zone_id_is_stripped_but_ip_kept(self) -> None:
        # Zone id (%eth0) is stripped for validation, but the (valid) ip is kept
        info = net.recover_ips("10.0.0.1", "fe80::1%eth0")
        self.assertEqual(info.ip, "10.0.0.1")
        self.assertEqual(info.ip_proxy, "fe80::1")
        self.assertEqual(info.ip_version, 4)

    def test_ipv6_remote_addr_and_xff(self) -> None:
        info = net.recover_ips("2001:db8::1", "2001:db8::2, 2001:db8::3")
        self.assertEqual(info.ip, "2001:db8::1")
        self.assertEqual(info.ip_proxy, "2001:db8::3")  # Reversed, nearest proxy first
        self.assertEqual(info.ip_version, 6)

    def test_mixed_v4_v6_xff_keeps_valid_entries_in_order(self) -> None:
        info = net.recover_ips(
            "10.0.0.1",
            "2001:db8::2, not-an-ip, 10.0.0.2, <script>alert(1)</script>",
        )
        self.assertEqual(info.ip, "10.0.0.1")
        self.assertEqual(info.ip_proxy, "10.0.0.2")  # Reversed: last valid entry first
        self.assertEqual(info.ip_version, 4)

    def test_invalid_ipv6_entries_are_dropped(self) -> None:
        for injected in (
            "2001:db8::zzzz",
            "2001:db8:::1",
            "fe80::%eth0",  # Zone id alone is not a valid ip after strip
            "2001:db8::1:2:3:4:5:6:7:8",  # Too many groups
        ):
            with self.subTest(injected=injected):
                info = net.recover_ips("2001:db8::1", f"{injected}, 2001:db8::2")
                self.assertEqual(info.ip, "2001:db8::1")
                self.assertEqual(info.ip_proxy, "2001:db8::2")
                self.assertEqual(info.ip_version, 6)

    def test_ipv4_mapped_ipv6_from_remote_addr_is_normalized(self) -> None:
        info = net.recover_ips("::ffff:203.0.113.7", "")
        self.assertEqual(info.ip, "203.0.113.7")
        self.assertEqual(info.ip_proxy, "203.0.113.7")
        self.assertEqual(info.ip_version, 4)

    def test_ipv4_mapped_ipv6_from_xff_is_normalized(self) -> None:
        # Unix socket: empty remote_addr, all entries in IPv4-mapped form
        info = net.recover_ips("", "::ffff:203.0.113.7, ::ffff:10.0.0.2")
        self.assertEqual(info.ip, "10.0.0.2")
        self.assertEqual(info.ip_proxy, "203.0.113.7")
        self.assertEqual(info.ip_version, 4)

    def test_empty_remote_addr_with_ipv6_xff(self) -> None:
        info = net.recover_ips("", "2001:db8::2, 2001:db8::3")
        self.assertEqual(info.ip, "2001:db8::3")
        self.assertEqual(info.ip_proxy, "2001:db8::2")
        self.assertEqual(info.ip_version, 6)

    def test_ipv6_falls_back_to_remote_addr_when_all_xff_invalid(self) -> None:
        info = net.recover_ips("2001:db8::1", "2001:db8::zzzz, <script>alert(1)</script>")
        self.assertEqual(info.ip, "2001:db8::1")
        self.assertEqual(info.ip_proxy, "2001:db8::1")
        self.assertEqual(info.ip_version, 6)

    def test_empty_remote_addr_uses_first_xff_entry(self) -> None:
        # Unix socket (nginx -> gunicorn): remote_addr is empty
        info = net.recover_ips("", "203.0.113.7, 10.0.0.2")
        self.assertEqual(info.ip, "10.0.0.2")
        self.assertEqual(info.ip_proxy, "203.0.113.7")

    def test_empty_remote_addr_with_invalid_xff(self) -> None:
        info = net.recover_ips("", "<script>alert(1)</script>")
        self.assertEqual(info.ip, "")
        self.assertEqual(info.ip_proxy, "")


class NetTest(UDSTestCase):
    def test_network_from_string_ipv4(self) -> None:
        for n in (
            ("*", 0, 4294967295),
            ("192.168.0.1", 3232235521, 3232235521),
            ("192.168.0.*", 3232235520, 3232235775),
            ("192.168.*.*", 3232235520, 3232301055),
            ("192.168.*", 3232235520, 3232301055),
            ("192.*.*.*", 3221225472, 3238002687),
            ("192.*.*", 3221225472, 3238002687),
            ("192.*", 3221225472, 3238002687),
            ("192.168.0.1 netmask 255.255.255.0", 3232235520, 3232235775),
            ("192.168.0.1/8", 3221225472, 3238002687),
            ("192.168.0.1/28", 3232235520, 3232235535),
            ("192.168.0.1-192.168.0.87", 3232235521, 3232235607),
            ("192.168.0.1 netmask 255.255.255.0", 3232235520, 3232235775),
        ):
            multiple_net: list[net.NetworkType] = net.networks_from_str(n[0])
            self.assertEqual(
                len(multiple_net),
                1,
                f"Incorrect number of network returned from {n[0]}",
            )
            self.assertEqual(
                multiple_net[0][0],
                n[1],
                f"Incorrect network start value for {n[0]}",
            )
            self.assertEqual(
                multiple_net[0][1],
                n[2],
                f"Incorrect network end value for {n[0]}",
            )

            single_net: net.NetworkType = net.network_from_str(n[0])
            self.assertEqual(
                len(single_net),
                3,
                f"Incorrect number of network returned from {n[0]}",
            )
            self.assertEqual(
                single_net[0],
                n[1],
                f"Incorrect network start value for {n[0]}",
            )
            self.assertEqual(
                single_net[1],
                n[2],
                f"Incorrect network end value for {n[0]}",
            )

        for n1 in ("192.168.0", "192.168.0.5-192.168.0.3", "no net"):
            with self.assertRaises(ValueError):
                net.networks_from_str(n1, check_mode=True)

        self.assertEqual(net.ip_to_long("192.168.0.5").ip, 3232235525)
        self.assertEqual(net.long_to_ip(3232235525, 4), "192.168.0.5")
        for n2 in range(255):
            self.assertTrue(net.contains("192.168.0.0/24", f"192.168.0.{n2}"))

        for n3 in range(4294):
            self.assertTrue(net.contains([net.NetworkType(0, 4294967295, 4)], n3 * 1000))
            self.assertTrue(net.contains(net.NetworkType(0, 4294967295, 4), n3 * 1000))

    def test_network_from_string_ipv6(self) -> None:
        # IPv6 only support standard notation, and '*', but not "netmask" or "range"
        for n in (
            (
                "*",
                0,
                2**128 - 1,
            ),  # This could be confused with ipv4 *, so we take care
            (
                "2001:db8::1",
                42540766411282592856903984951653826561,
                42540766411282592856903984951653826561,
            ),
            (
                "2001:db8::1/64",
                42540766411282592856903984951653826560,
                42540766411282592875350729025363378175,
            ),
            (
                "2001:db8::1/28",
                42540765777457292742789284203302223872,
                42540767045107892971018685700005429247,
            ),
            (
                "2222:3333:4444:5555:6666:7777:8888:9999/64",
                45371328414530988873481865147602436096,
                45371328414530988891928609221311987711,
            ),
            (
                "fe80::/10",
                338288524927261089654018896841347694592,
                338620831926207318622244848606417780735,
            ),
        ):
            multiple_net: list[net.NetworkType] = net.networks_from_str(n[0], version=(6 if n[0] == "*" else 0))
            self.assertEqual(
                len(multiple_net),
                1,
                f"Incorrect number of network returned from {n[0]}",
            )
            self.assertEqual(
                multiple_net[0][0],
                n[1],
                f"Incorrect network start value for {n[0]}",
            )
            self.assertEqual(
                multiple_net[0][1],
                n[2],
                f"Incorrect network end value for {n[0]}",
            )

            single_net: net.NetworkType = net.network_from_str(n[0], version=(6 if n[0] == "*" else 0))
            self.assertEqual(
                len(single_net),
                3,
                f"Incorrect number of network returned from {n[0]}",
            )
            self.assertEqual(
                single_net[0],
                n[1],
                f"Incorrect network start value for {n[0]}",
            )
            self.assertEqual(
                single_net[1],
                n[2],
                f"Incorrect network end value for {n[0]}",
            )

    def test_ip_to_long(self) -> None:
        for i in range(32):
            ipv4 = f"192.168.{i}.{i * 2}"
            ipv4_num = int(ipaddress.IPv4Address(ipv4))
            ipv6 = f"2001:{0xDB8 + i:04x}::{i:02x}"
            ipv6_num = int(ipaddress.IPv6Address(ipv6))

            self.assertEqual(net.ip_to_long(ipv4).ip, ipv4_num)
            self.assertEqual(net.ip_to_long(ipv4).version, 4)
            self.assertEqual(net.ip_to_long(f"::ffff:{ipv4}").ip, ipv4_num)
            self.assertEqual(net.ip_to_long(f"::ffff:{ipv4}").version, 4)

            self.assertEqual(net.ip_to_long(ipv6).ip, ipv6_num)
            self.assertEqual(net.ip_to_long(ipv6).version, 6)

        # Test some ip conversions from long to ip and viceversa
        for a in ("172", "192", "10"):
            for b in range(0, 256, 17):
                for ipv6 in range(0, 256, 13):
                    for d in range(0, 256, 11):
                        ipv4 = f"{a}.{b}.{ipv6}.{d}"
                        self.assertEqual(net.long_to_ip(net.ip_to_long(ipv4).ip, 4), ipv4)

        # iterate some ipv6 addresses
        for ipv6 in (
            "2001:db8::1",
            "2001:1::1",
            "2001:2:3::1",
            "2001:2:3:4::1",
            "2001:2:3:4:5::1",
            "2001:2:3:4:5:6:0:1",
        ):
            # Ensure converting back to string ips works
            self.assertEqual(net.long_to_ip(net.ip_to_long(ipv6).ip, 6), ipv6)
