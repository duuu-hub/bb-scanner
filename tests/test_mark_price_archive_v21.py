"""V21 official mark-price archive integrity and exact alignment tests."""
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts import mark_price_archive_v21 as archive

MONTH = "2022-01"
T = int(pd.Timestamp(MONTH + "-01", tz="UTC").timestamp() * 1000)


def csv_row(ts, o, h, l, c):
    return f"{ts},{o},{h},{l},{c},0,{ts + archive.BAR - 1},0,0,0,0,0\n"


def zip_bytes(text):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zipped:
        zipped.writestr("mark.csv", text)
    return payload.getvalue()


def official_bytes():
    header = "open_time,open,high,low,close,ignore,close_time,ignore,ignore,ignore,ignore,ignore\n"
    raw = zip_bytes(header + csv_row(T, 100, 102, 99, 101) + csv_row(T + archive.BAR, 101, 103, 100, 102))
    checksum = (hashlib.sha256(raw).hexdigest() + "  BTCUSDT-15m-2022-01.zip\n").encode()
    return raw, checksum


class MarkArchiveTests(unittest.TestCase):
    def setUp(self):
        archive.INPUTS.clear()

    def test_header_and_headerless_arrays_agree(self):
        header = "open_time,open,high,low,close,ignore,close_time,ignore,ignore,ignore,ignore,ignore\n"
        text = csv_row(T, 100, 102, 99, 101) + csv_row(T + archive.BAR, 101, 103, 100, 102)
        first = archive.parse_zip(zip_bytes(header + text), MONTH)
        second = archive.parse_zip(zip_bytes(text), MONTH)
        for left, right in zip(first, second):
            np.testing.assert_array_equal(left, right)
        self.assertEqual(first[0].dtype, np.dtype("int64"))

    def test_invalid_width_header_timestamp_geometry_and_nonpositive_values_fail(self):
        bad = [
            "1,2,3\n",
            "wrong,header,names,here,x,0,0,0,0,0,0,0\n",
            csv_row(T + 1, 100, 102, 99, 101),
            csv_row(T, 100, 98, 99, 101),
            csv_row(T, 0, 102, 99, 101),
            csv_row(T, 100, 102, 99, float("nan")),
            csv_row(T, 100, 102, 99, 101) + csv_row(T, 100, 102, 99, 101),
        ]
        for text in bad:
            with self.subTest(text=text), self.assertRaises((ValueError, pd.errors.EmptyDataError)):
                archive.parse_zip(zip_bytes(text), MONTH)

    def test_original_checksum_npz_and_verified_cache_are_immutable(self):
        raw, checksum = official_bytes()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(archive, "_get", side_effect=[raw, checksum]) as get:
                first = archive.load_month("BTCUSDT", MONTH, root / "cache", root / "evidence")
            self.assertEqual(get.call_count, 2)
            with patch.object(archive, "_get", side_effect=AssertionError("immutable cache")):
                second = archive.load_month("BTCUSDT", MONTH, root / "cache", root / "evidence2")
            for left, right in zip(first, second):
                np.testing.assert_array_equal(left, right)
            paths = archive.filenames("BTCUSDT", MONTH, root / "evidence")
            meta = json.loads(paths[3].read_text())
            self.assertEqual(paths[0].read_bytes(), raw)
            self.assertEqual(paths[1].read_bytes(), checksum)
            self.assertEqual(archive.digest(paths[0]), meta["official_checksum_sha256"])
            self.assertEqual(archive.digest(paths[2]), meta["npz_sha256"])

    def test_each_verified_cache_component_corruption_fails_without_redownload(self):
        for part in range(4):
            with self.subTest(part=part), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                raw, checksum = official_bytes()
                with patch.object(archive, "_get", side_effect=[raw, checksum]):
                    archive.load_month("BTCUSDT", MONTH, root / "cache", root / "evidence")
                paths = archive.filenames("BTCUSDT", MONTH, root / "cache")
                if part == 3:
                    meta = json.loads(paths[3].read_text())
                    meta["source_url"] = "wrong"
                    paths[3].write_text(json.dumps(meta))
                else:
                    paths[part].write_bytes(b"corrupt")
                with patch.object(archive, "_get", side_effect=AssertionError("no replacement")), self.assertRaises(ValueError):
                    archive.load_month("BTCUSDT", MONTH, root / "cache", root / "failure")
                self.assertEqual(archive.INPUTS["BTCUSDT/" + MONTH]["status"], "CACHE_INTEGRITY_ERROR")

    def test_404_checksum_failure_and_transient_are_not_imputed(self):
        raw, _ = official_bytes()
        cases = [
            ([None], "MARK_DATA_GAP", False),
            ([raw, None], "MARK_DATA_GAP", False),
            ([raw, b"bad"], "MARK_DATA_GAP", False),
            ([raw, RuntimeError("429")], "TRANSIENT_SOURCE_ERROR", True),
        ]
        for effects, status, raises in cases:
            with self.subTest(effects=effects), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                with patch.object(archive, "_get", side_effect=effects):
                    if raises:
                        with self.assertRaises(RuntimeError):
                            archive.load_month("BTCUSDT", MONTH, root / "cache", root / "evidence")
                    else:
                        self.assertIsNone(archive.load_month("BTCUSDT", MONTH, root / "cache", root / "evidence"))
                self.assertEqual(archive.INPUTS["BTCUSDT/" + MONTH]["status"], status)

    def test_exact_alignment_missing_month_and_stage_cut(self):
        data = (np.array([T, T + 2 * archive.BAR], np.int64),) + (np.array([100.0, 102.0]),) * 4
        timeline = np.array([T, T + archive.BAR, T + 2 * archive.BAR], np.int64)
        aligned = archive.align(timeline, data, {MONTH}, T + 2 * archive.BAR)
        np.testing.assert_array_equal(aligned["mark_available"], [True, False, False])
        self.assertTrue(np.isnan(aligned["mark_close"][1:]).all())
        self.assertFalse(archive.align(timeline, data, set(), T + 3 * archive.BAR)["mark_available"].any())

    def test_future_mark_values_cannot_change_prior_alignment(self):
        timeline = np.arange(T, T + 8 * archive.BAR, archive.BAR, dtype=np.int64)
        values = np.linspace(100, 107, 8)
        data = (timeline.copy(), values.copy(), values.copy(), values.copy(), values.copy())
        old = archive.align(timeline, data, {MONTH}, T + 8 * archive.BAR)
        changed = tuple(value.copy() for value in data)
        changed[-1][5:] *= 9
        new = archive.align(timeline, changed, {MONTH}, T + 8 * archive.BAR)
        for key in old:
            np.testing.assert_array_equal(old[key][:5], new[key][:5])


if __name__ == "__main__":
    unittest.main()
