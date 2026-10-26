import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / "provision" / "flash.py"
spec = importlib.util.spec_from_file_location("pico_flash", PATH)
flash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flash)


class ProvisionTests(unittest.TestCase):
    def test_stage_manifest_matches_exact_firmware_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            manifest = flash.stage(target)
            self.assertEqual(len(manifest), 14)
            for name, expected in manifest.items():
                self.assertEqual(hashlib.sha256((target/name).read_bytes()).hexdigest(), expected)
            self.assertFalse((target/"simctl.py").exists())
            self.assertFalse((target/"client.py").exists())
            for name in ("core.py", "canonical.py", "wire.py", "face.py"):
                self.assertEqual((target/"breaker"/name).read_bytes(), (flash.SUPERVISOR/"breaker"/name).read_bytes())

    def test_uf2_rejects_wrong_checksum_and_invalid_block(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"fixture.uf2"
            path.write_bytes(b"x"*512)
            with self.assertRaises(ValueError): flash.check_uf2(path, "0"*64)
            with self.assertRaises(ValueError): flash.check_uf2(path, flash.sha(path))

    def test_uf2_all_block_magics_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"fixture.uf2"
            block = struct.pack("<II", 0x0a324655, 0x9e5d5157) + bytes(500) + struct.pack("<I", 0x0ab16f30)
            path.write_bytes(block*2)
            flash.check_uf2(path, flash.sha(path))
            path.write_bytes(block + bytes(512))
            with self.assertRaises(ValueError): flash.check_uf2(path, flash.sha(path))


class PanelEvidenceTests(unittest.TestCase):
    def test_arrival_host_record_checksums_exact_device_setting(self):
        from contextlib import redirect_stdout
        import io
        import json
        from unittest.mock import patch
        from breaker.panel_record import save
        spec = importlib.util.spec_from_file_location('pico_panel_test', PATH.with_name('panel_test.py'))
        panel = importlib.util.module_from_spec(spec); spec.loader.exec_module(panel)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'SIM-panel-host.json'
            def usb_fixture(command, **kwargs):
                # Fake USB transport only; no hardware/port is opened by this test.
                save(command[-1], 'V2_SSD1680', 'SIM fixture V2', 'a'*64)
            argv = ['panel_test.py', '--port', '/dev/SIM-no-hardware', '--panel', 'V2',
                    '--identity', 'SIM fixture V2', '--output', str(output)]
            with patch.object(panel.sys, 'argv', argv), patch.object(panel.subprocess, 'run', usb_fixture), redirect_stdout(io.StringIO()):
                panel.main()
            record = json.loads(output.read_text())
            copied = output.with_name(output.stem+'-device.json').read_bytes()
            self.assertEqual(record['device_record_sha256'], hashlib.sha256(copied).hexdigest())
            self.assertEqual(record['device_record']['profile'], 'V2_SSD1680')
            self.assertEqual(record['status'], 'operator_verified')
