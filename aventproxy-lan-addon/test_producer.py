"""Real process lifecycle checks with a synthetic producer, without camera access."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PRODUCER = Path(__file__).with_name("producer.py").resolve()


class ProducerTests(unittest.TestCase):
    def test_termination_stops_owned_process_group_and_removes_fifo_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "child.json"
            bootstrap = """
import runpy,subprocess,sys
original = subprocess.Popen
fake = "import json,os,time; from pathlib import Path; Path(%r).write_text(json.dumps({'pid':os.getpid(),'tmp':os.environ['TMPDIR']})); time.sleep(120)"
fake = fake %% sys.argv[1]
def replace(command, **kwargs):
    assert command[0] == '/usr/local/bin/babymonitor-cli'
    assert '--signaling' in command and command[command.index('--signaling')+1] == 'lan'
    assert command[-2:] == ['--rtsp-out','rtsp://127.0.0.1:38554/test']
    assert kwargs['env']['BABYMONITOR_STREAM_TRACE'] == '/dev/null'
    return original([sys.executable,'-c',fake], **kwargs)
subprocess.Popen=replace
path=sys.argv[2];sys.argv=[path,'a'*64,'rtsp://127.0.0.1:38554/test'];runpy.run_path(path,run_name='__main__')
""".replace("%%", "%")
            supervisor = subprocess.Popen([sys.executable, "-c", bootstrap, str(marker), str(PRODUCER)])
            try:
                end = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < end:
                    time.sleep(0.02)
                self.assertTrue(marker.exists())
                child = json.loads(marker.read_text())
                self.assertTrue(Path(child["tmp"]).exists())
                supervisor.send_signal(signal.SIGTERM)
                self.assertEqual(supervisor.wait(timeout=5), 0)
                self.assertFalse(Path(child["tmp"]).exists())
                with self.assertRaises(ProcessLookupError):
                    os.kill(child["pid"], 0)
            finally:
                if supervisor.poll() is None:
                    supervisor.kill()
                    supervisor.wait()

    def test_rejects_unconfigured_camera(self):
        result = subprocess.run([sys.executable, str(PRODUCER), "7"], capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
