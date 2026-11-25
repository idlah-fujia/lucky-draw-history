import base64
import os
import subprocess
import sys
from pathlib import Path

from app.crypto_util import decrypt_bytes, encrypt_bytes

ROOT = Path(__file__).resolve().parents[1]


def test_round_trip_and_cli(tmp_path):
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    plain = "Event,ID Firm\nSample,North Studio\n".encode("utf-8")
    token = encrypt_bytes(plain, key)
    assert decrypt_bytes(token, key) == plain

    source = tmp_path / "plain.txt"
    target = tmp_path / "plain.enc.b64.txt"
    source.write_bytes(plain)
    env = os.environ.copy()
    env["DATA_KEY"] = key
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "encrypt_file.py"), str(source), str(target)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert decrypt_bytes(target.read_text(encoding="utf-8"), key) == plain


def test_cli_refuses_missing_key(tmp_path):
    source = tmp_path / "plain.txt"
    source.write_text("hello", encoding="utf-8")
    env = os.environ.copy()
    env.pop("DATA_KEY", None)
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "encrypt_file.py"), str(source), str(tmp_path / "out.txt")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 2
