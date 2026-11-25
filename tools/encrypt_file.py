"""Encrypt a file for Lucky Draw History.

Output is base64(12-byte IV || AES-256-GCM ciphertext and tag), no AAD.
The key is the DATA_KEY environment variable (base64 of 32 bytes), not an argument.

    DATA_KEY='...' python tools/encrypt_file.py INPUT OUTPUT
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.crypto_util import CryptoError, encrypt_bytes  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: DATA_KEY=... python tools/encrypt_file.py INPUT OUTPUT", file=sys.stderr)
        return 2
    key = os.environ.get("DATA_KEY", "").strip()
    if not key:
        print("DATA_KEY env var is required", file=sys.stderr)
        return 2
    source = Path(argv[1])
    target = Path(argv[2])
    try:
        token = encrypt_bytes(source.read_bytes(), key)
    except CryptoError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    target.write_text(token, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
