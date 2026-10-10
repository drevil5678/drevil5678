"""Download pinned official HTTPS artifacts and check immutable hashes before use."""
import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".runtime"
ARTIFACTS = [
    ("https://github.com/lightvector/KataGo/releases/download/v1.16.3/katago-v1.16.3-eigenavx2-linux-x64.zip",
     "c14e4199318b33d2257bc7177fc65ac5081e9ce5a002bfbc00be1594f6f1f6ec", "katago", RUNTIME / "katago" / "katago"),
    ("https://github.com/lightvector/KataGo/releases/download/v1.0/b6c96-s103408384-d26419149.zip",
     "aed74da661e25d056b3f8628c28f3829c4e02a9c2003a8af54d8ce211e50f63d",
     "b6c96-s103408384-d26419149/model.txt.gz", RUNTIME / "model.txt.gz"),
]
# Binary hash is GitHub's publisher-provided release digest. The old model release
# has no published digest: its pinned hash was recorded from the official HTTPS
# release on initial acquisition, never substituted to work around a mismatch.


def install():
    import platform
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("預設安裝僅支援 Linux x86_64；其他平台請安裝官方對應版本並設定 KATAGO_BIN/MODEL/CONFIG")
    if "avx2" not in Path("/proc/cpuinfo").read_text():
        raise SystemExit("此 KataGo binary 需要 AVX2；請選用官方 Eigen 非 AVX2 版本並驗證發布摘要")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    cache = RUNTIME / "downloads"
    cache.mkdir(exist_ok=True)
    for url, digest, member, destination in ARTIFACTS:
        archive = cache / url.rsplit("/", 1)[-1]
        if not archive.exists():
            print("Downloading official artifact:", archive.name)
            with urllib.request.urlopen(url, timeout=90) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != digest:
                raise SystemExit("Artifact checksum mismatch; refusing installation")
            archive.write_bytes(data)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            raise SystemExit("Cached artifact checksum mismatch; refusing installation")
        with zipfile.ZipFile(archive) as z:
            data = z.read(member)
        if destination.exists() and hashlib.sha256(destination.read_bytes()).digest() == hashlib.sha256(data).digest():
            continue
        if destination.exists():
            raise SystemExit(f"保留現有不同檔案，請檢查後使用 KATAGO_* 指定模型或 binary：{destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        if member == "katago":
            destination.chmod(0o755)
    # Official AppImage extraction works in containers without a FUSE device.
    # Re-extract the verified image into a temporary directory and compare any
    # retained installation, rather than trusting an arbitrary existing AppRun.
    with tempfile.TemporaryDirectory(dir=RUNTIME) as tmp:
        subprocess.run([str(RUNTIME / "katago" / "katago"), "--appimage-extract"], cwd=tmp, check=True)
        source = Path(tmp) / "squashfs-root"
        destination = RUNTIME / "katago" / "squashfs-root"
        if destination.exists():
            for p in source.rglob("*"):
                retained = destination / p.relative_to(source)
                if p.is_symlink():
                    if not retained.is_symlink() or p.readlink() != retained.readlink():
                        raise SystemExit("Extracted AppImage symlink mismatch; refusing reuse")
                elif p.is_file():
                    if not retained.is_file() or hashlib.sha256(p.read_bytes()).digest() != hashlib.sha256(retained.read_bytes()).digest():
                        raise SystemExit("Extracted AppImage checksum mismatch; refusing reuse")
        else:
            shutil.copytree(source, destination, symlinks=True)
    print("KataGo artifacts verified. CPU development model installed.")


if __name__ == "__main__":
    install()
