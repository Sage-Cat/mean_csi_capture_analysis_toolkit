import unittest

from csi_capture.analysis.radio_state import (
    APObservation,
    ScanSample,
    parse_netsh_output,
    parse_nmcli_output,
    split_nmcli_escaped_fields,
    summarize_radio_state,
)


class RadioStateParserTests(unittest.TestCase):
    def test_split_nmcli_escaped_fields_preserves_colons(self):
        line = r"*:Lab\:AP:AA\:BB\:CC\:DD\:EE\:FF:6:2437 MHz:130 Mbit/s:78:WPA2"
        fields = split_nmcli_escaped_fields(line)
        self.assertEqual(fields[1], "Lab:AP")
        self.assertEqual(fields[2], "AA:BB:CC:DD:EE:FF")

    def test_parse_nmcli_output_parses_24ghz_row(self):
        text = r"*:Lab\:AP:AA\:BB\:CC\:DD\:EE\:FF:6:2437 MHz:130 Mbit/s:78:WPA2"
        rows = parse_nmcli_output(text, sample_index=1, captured_at_utc="2026-03-09T11:00:00Z")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.ssid, "Lab:AP")
        self.assertEqual(row.bssid, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(row.channel, 6)
        self.assertEqual(row.signal_quality_pct, 78)
        self.assertTrue(row.signal_dbm_estimated)

    def test_parse_netsh_output_parses_multiple_bssids(self):
        text = """
SSID 1 : Campus
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Signal             : 82%
         Radio type         : 802.11n
         Channel            : 6
    BSSID 2                 : 11:22:33:44:55:66
         Signal             : 71%
         Radio type         : 802.11g
         Channel            : 11
"""
        rows = parse_netsh_output(text, sample_index=1, captured_at_utc="2026-03-09T11:00:00Z")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].ssid, "Campus")
        self.assertEqual(rows[0].channel, 6)
        self.assertEqual(rows[1].channel, 11)
        self.assertEqual(rows[0].security, "WPA2-Personal / CCMP")


class RadioStateSummaryTests(unittest.TestCase):
    def _observation(self, *, sample_index: int, ssid: str, bssid: str, channel: int, signal_dbm: float):
        return APObservation(
            sample_index=sample_index,
            captured_at_utc=f"2026-03-09T11:00:0{sample_index}Z",
            scanner="nmcli",
            ssid=ssid,
            bssid=bssid,
            channel=channel,
            frequency_mhz=2407 + (channel * 5),
            signal_dbm=signal_dbm,
            signal_dbm_estimated=True,
            signal_quality_pct=70,
            security="WPA2",
            radio_type=None,
            rate_mbps=130.0,
            in_use=False,
            raw_signal="70",
        )

    def test_summary_excludes_experiment_ap_from_external_counts(self):
        sample_1 = ScanSample(
            sample_index=1,
            captured_at_utc="2026-03-09T11:00:01Z",
            scanner="nmcli",
            command=("nmcli",),
            interface="wlan0",
            observations=(
                self._observation(
                    sample_index=1,
                    ssid="EXP_AP",
                    bssid="AA:AA:AA:AA:AA:AA",
                    channel=11,
                    signal_dbm=-45.0,
                ),
                self._observation(
                    sample_index=1,
                    ssid="NEIGHBOR_11",
                    bssid="BB:BB:BB:BB:BB:BB",
                    channel=11,
                    signal_dbm=-62.0,
                ),
                self._observation(
                    sample_index=1,
                    ssid="NEIGHBOR_6",
                    bssid="CC:CC:CC:CC:CC:CC",
                    channel=6,
                    signal_dbm=-58.0,
                ),
            ),
            raw_output="sample1",
        )
        sample_2 = ScanSample(
            sample_index=2,
            captured_at_utc="2026-03-09T11:00:03Z",
            scanner="nmcli",
            command=("nmcli",),
            interface="wlan0",
            observations=(
                self._observation(
                    sample_index=2,
                    ssid="EXP_AP",
                    bssid="AA:AA:AA:AA:AA:AA",
                    channel=11,
                    signal_dbm=-44.0,
                ),
                self._observation(
                    sample_index=2,
                    ssid="NEIGHBOR_11",
                    bssid="BB:BB:BB:BB:BB:BB",
                    channel=11,
                    signal_dbm=-64.0,
                ),
            ),
            raw_output="sample2",
        )

        report = summarize_radio_state(
            [sample_1, sample_2],
            focus_channel=11,
            experiment_ssid="EXP_AP",
            experiment_bssid=None,
            top_n=5,
        )

        self.assertEqual(report["summary"]["unique_bssids_24ghz_external"], 2)
        self.assertEqual(report["summary"]["focus_channel_mean_exact_bssid_count"], 1.0)
        self.assertEqual(report["summary"]["recommended_channel_non_overlapping"], 1)
        self.assertEqual(report["top_interferers"][0]["bssid"], "BB:BB:BB:BB:BB:BB")


if __name__ == "__main__":
    unittest.main()
