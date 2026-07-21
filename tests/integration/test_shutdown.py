"""Graceful shutdown: `kill` (SIGTERM) and Ctrl-C (SIGINT) both stop the
server cleanly -- it unwinds through its finally, exits 0, and prints no
KeyboardInterrupt traceback. Regression guard for the detached-process case
where SIGINT used to be dropped and only SIGKILL worked.
"""

import signal
import subprocess
import sys
import time

import pytest


def _start_until_listening(tmp_path):
    log = tmp_path / "server.log"
    handle = open(log, "w")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "cucco.server.app",
            "--host", "127.0.0.1", "--port", "0",  # ephemeral: no port collisions
            "--admin-port", "0", "--gc-interval", "0",
        ],
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError("server exited before listening:\n" + log.read_text())
        if "cucco-server listening" in log.read_text():
            return proc, log
        time.sleep(0.1)
    proc.kill()
    raise AssertionError("server never logged 'listening':\n" + log.read_text())


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_server_exits_cleanly_on_signal(tmp_path, sig):
    proc, log = _start_until_listening(tmp_path)
    proc.send_signal(sig)
    try:
        returncode = proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail(f"server ignored {sig.name}:\n{log.read_text()}")
    text = log.read_text()
    assert returncode == 0, f"unclean exit {returncode} on {sig.name}:\n{text}"
    assert "Traceback" not in text, f"traceback on {sig.name}:\n{text}"
    assert "shutting down" in text, f"no graceful-shutdown log on {sig.name}:\n{text}"
