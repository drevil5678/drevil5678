"""Build a self-contained folder for THIS OS; no cross-compilation claims."""
import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def verified_archive(url, expected):
    cache = ROOT / ".runtime" / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / url.rsplit("/", 1)[-1]
    if archive.exists():
        content = archive.read_bytes()
    else:
        with urllib.request.urlopen(url, timeout=120) as response:
            content = response.read()
    if hashlib.sha256(content).hexdigest() != expected:
        raise RuntimeError("官方套件摘要不符，拒絕打包：" + archive.name)
    if not archive.exists():
        archive.write_bytes(content)
    return archive


def copy_engine(bundle):
    model_url = "https://github.com/lightvector/KataGo/releases/download/v1.0/b6c96-s103408384-d26419149.zip"
    model_hash = "aed74da661e25d056b3f8628c28f3829c4e02a9c2003a8af54d8ce211e50f63d"
    archive = verified_archive(model_url, model_hash)
    (bundle/"model").mkdir()
    with zipfile.ZipFile(archive) as z:
        (bundle/"model/model.txt.gz").write_bytes(z.read("b6c96-s103408384-d26419149/model.txt.gz"))
    if platform.system() == "Linux":
        from scripts.install_katago import install
        install()
        shutil.copytree(ROOT/".runtime/katago/squashfs-root", bundle/"katago", symlinks=True)
        url = "https://github.com/lightvector/KataGo/releases/download/v1.16.3/katago-v1.16.3-eigenavx2-linux-x64.zip"
        digest = "c14e4199318b33d2257bc7177fc65ac5081e9ce5a002bfbc00be1594f6f1f6ec"
        archive = verified_archive(url, digest)
        with zipfile.ZipFile(archive) as z:
            (bundle/"katago/README-upstream.txt").write_bytes(z.read("README.txt"))
    elif platform.system() == "Windows":
        url = "https://github.com/lightvector/KataGo/releases/download/v1.16.3/katago-v1.16.3-eigenavx2-windows-x64.zip"
        digest = "e104393ea46c3ce4814e416352c35adc163dd12ad22a07e7b556db0c3480a804"
        archive = verified_archive(url, digest)
        destination = bundle/"katago"
        destination.mkdir()
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                target = (destination/member.filename).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise RuntimeError("套件含無效路徑")
            z.extractall(destination)
        if not (destination/"katago.exe").exists():
            raise RuntimeError("官方 Windows 套件結構與預期不同，請檢查後再打包")
    else:
        raise RuntimeError("目前打包流程只支援 Linux / Windows x86_64")
    return {"binary_url":url,"binary_archive_sha256":digest,"model_url":model_url,"model_archive_sha256":model_hash}


def build(output):
    if platform.machine().lower() not in ("x86_64", "amd64") or platform.system() not in ("Linux","Windows"):
        raise RuntimeError("請在目標平台的 x86_64 機器建置；不支援跨作業系統編譯")
    if importlib.util.find_spec("PyInstaller") is None:
        raise RuntimeError("請先執行 uv sync --locked --group build")
    output = Path(output).resolve()
    archive = Path(str(output) + ".zip")
    if output.exists() or archive.exists():
        raise RuntimeError("保留已有輸出，請用 --output 指定新路徑：" + str(output))
    (ROOT/"build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="portable-", dir=ROOT/"build") as directory:
        stage = Path(directory)
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console", "--name", "YanghaiTournament",
                   "--distpath", str(stage/"dist"), "--workpath", str(stage/"work"), "--specpath", str(stage),
                   "--add-data", f"{ROOT/'web'}{os.pathsep}web", "--add-data", f"{ROOT/'config'}{os.pathsep}config",
                   "--hidden-import", "uvicorn.logging", "--hidden-import", "uvicorn.loops.asyncio",
                   "--hidden-import", "uvicorn.protocols.http.h11_impl", "--hidden-import", "uvicorn.lifespan.on",
                   "--hidden-import", "anyio._backends._asyncio", str(ROOT/"launcher.py")]
        subprocess.run(command, cwd=ROOT, check=True)
        bundle = stage/"dist/YanghaiTournament"
        provenance = copy_engine(bundle)
        (bundle/"config").mkdir(exist_ok=True)
        shutil.copy2(ROOT/"config/analysis-cpu.cfg", bundle/"config/analysis.cfg")
        # Keep sample configuration separate: first launch creates tournament.json.
        configuration = {"host":"0.0.0.0","port":8000,"data_dir":"data",
                         "katago_bin":"katago/katago.exe" if os.name=="nt" else "katago/AppRun",
                         "katago_model":"model/model.txt.gz","katago_config":"config/analysis.cfg",
                         "ai_concurrency":1,"auto_backup_seconds":600,"open_browser":True}
        (bundle/"tournament.example.json").write_text(json.dumps(configuration,indent=2)+"\n",encoding="utf-8")
        shutil.copy2(ROOT/"docs/本地執行檔.md", bundle/"READ-ME-FIRST.md")
        shutil.copy2(ROOT/"docs/活動部署.md", bundle/"EVENT-OPERATIONS.md")
        provenance.update({"os":platform.system(),"architecture":platform.machine(),"python":platform.python_version(),
                           "model_purpose":"CPU development/preflight only, not formal event certification"})
        (bundle/"BUILD-INFO.json").write_text(json.dumps(provenance,indent=2)+"\n",encoding="utf-8")
        # No live database, administrator key or player codes are ever included.
        if list(bundle.rglob("*.sqlite3")) or list(bundle.rglob("admin-token")):
            raise RuntimeError("拒絕將賽事資料或管理金鑰放入發布套件")
        output.parent.mkdir(parents=True,exist_ok=True)
        shutil.move(str(bundle),str(output))
    # A ZIP can lose Unix symlink/execute metadata. The frozen executable is
    # chmod-restorable; KataGo's core usr/ subtree uses ordinary files. Recreate
    # with file mode attributes, dereferencing only the verified asset symlinks.
    shutil.make_archive(str(output),"zip",root_dir=output.parent,base_dir=output.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(digest+"  "+archive.name+"\n",encoding="utf-8")
    print("Portable folder:",output)
    print("Archive:",archive)
    print("SHA-256:",digest)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=ROOT/"dist"/f"yanghai-{platform.system().lower()}-x64")
    args=parser.parse_args()
    try:
        build(args.output)
    except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
