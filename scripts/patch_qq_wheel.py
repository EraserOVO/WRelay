"""Reproduce the metadata-only QQ adapter patch from the verified PyPI wheel."""
import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_URL = "https://files.pythonhosted.org/packages/8c/68/adcff2c5601d1e35e759b40018fc76aac05fb61481d91a744551a1c3d0fc/nonebot_adapter_qq-1.7.2-py3-none-any.whl"
UPSTREAM_SHA256 = "487aa6473c1c9874fe5ed088817589f7c6a09967b8ac468b29a48e41cce4f1e6"
OLD_INFO = "nonebot_adapter_qq-1.7.2.dist-info/"
NEW_INFO = "nonebot_adapter_qq-1.7.2+wrelay.1.dist-info/"
OUTPUT = ROOT / "vendor/wheels/nonebot_adapter_qq-1.7.2+wrelay.1-py3-none-any.whl"


def patched_files(upstream):
    if hashlib.sha256(upstream).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("Upstream wheel SHA-256 mismatch")
    with zipfile.ZipFile(io.BytesIO(upstream)) as archive:
        files = {n.replace(OLD_INFO, NEW_INFO): archive.read(n)
                 for n in archive.namelist() if not n.endswith("/RECORD")}
    key = NEW_INFO + "METADATA"
    metadata = files[key]
    for old, new in (
        (b"Version: 1.7.2\n", b"Version: 1.7.2+wrelay.1\n"),
        (b"Requires-Dist: cryptography>=43.0.3,<49.0.0\n",
         b"Requires-Dist: cryptography>=50.0.1,<51.0.0\n"),
    ):
        if metadata.count(old) != 1:
            raise ValueError("Unexpected upstream metadata")
        metadata = metadata.replace(old, new)
    files[key] = metadata.replace(b"License-Expression: MIT\n",
                                 b"License-Expression: MIT\nLicense-File: LICENSE\n")
    files[NEW_INFO + "licenses/LICENSE"] = (ROOT / "vendor/qq-adapter-LICENSE").read_text(encoding="utf-8").encode()
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, data in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256=" + digest, len(data)))
    writer.writerow((NEW_INFO + "RECORD", "", ""))
    files[NEW_INFO + "RECORD"] = record.getvalue().encode()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-wheel", type=Path, help="Use an already downloaded official wheel")
    args = parser.parse_args()
    if args.upstream_wheel:
        upstream = args.upstream_wheel.read_bytes()
    else:
        with urllib.request.urlopen(UPSTREAM_URL, timeout=60) as response:
            upstream = response.read()
    files = patched_files(upstream)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Stored ZIP entries make output independent of the local zlib version.
    with zipfile.ZipFile(OUTPUT, "w") as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 28, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    print(f"{OUTPUT.name}: sha256={hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
