"""Create the CPU-only, offline Windows two-team package from a verified build."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def build(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    archive = Path(str(output) + ".zip")
    if output.exists() or archive.exists():
        raise RuntimeError("保留已有輸出，請指定新的輸出路徑")
    provenance = json.loads((source / "BUILD-INFO.json").read_text(encoding="utf-8"))
    if provenance["os"] != "Windows":
        raise RuntimeError("Windows 執行檔必須由真正的 Windows 建置產生")
    required = ["_internal", "katago", "model", "config", "YanghaiDuel.exe",
                "Start-Duel-Offline.cmd", "DUEL-READ-ME.md", "BUILD-INFO.json"]
    if any(not (source / item).exists() for item in required):
        raise RuntimeError("Windows 建置缺少單機備用版所需檔案")
    if list(source.rglob("*.sqlite3*")) or list(source.rglob("admin-token")):
        raise RuntimeError("來源含執行中資料，拒絕打包")
    output.mkdir(parents=True)
    for item in required:
        target = source / item
        if target.is_dir():
            shutil.copytree(target, output / item)
        else:
            shutil.copy2(target, output / item)
    # No live settings, databases or administrator keys are distributed.
    shutil.make_archive(str(output), "zip", root_dir=output.parent, base_dir=output.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(digest + "  " + archive.name + "\n", encoding="utf-8")
    print(json.dumps({"archive": str(archive), "sha256": digest, "mode": "offline-duel", "backend": "CPU"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    build(arguments.source, arguments.output)
