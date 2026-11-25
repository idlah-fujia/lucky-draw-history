import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHONE = re.compile(r"(?<!\d)[89]\d{7}(?!\d)")


def _listed_files() -> list[str]:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    others = subprocess.check_output(["git", "ls-files", "-z", "--others", "--exclude-standard"], cwd=ROOT)
    names = []
    for blob in (tracked, others):
        names.extend(part.decode("utf-8") for part in blob.split(b"\0") if part)
    return sorted(set(names))


def _is_encrypted(path: str) -> bool:
    return ".enc.b64." in path or path.endswith(".enc.b64.txt")


def test_tracked_files_have_no_plaintext_mobile_numbers():
    hits = []
    for rel in _listed_files():
        if _is_encrypted(rel):
            continue
        file_path = ROOT / rel
        if not file_path.is_file():
            continue
        text = file_path.read_text(encoding="utf-8", errors="replace")
        if PHONE.search(text):
            hits.append(rel)
    assert hits == []


def test_env_files_are_not_tracked():
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    assert [path for path in tracked if path == ".env" or path.startswith(".env.")] == []
