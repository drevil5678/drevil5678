"""Run the extracted Linux bundle from elsewhere, with real AI and restart."""
import asyncio
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from scripts.smoke import run as tournament_smoke


async def readiness(port):
    async with httpx.AsyncClient(timeout=2,trust_env=False) as client:
        for _ in range(120):
            try:
                result=await client.get(f"http://127.0.0.1:{port}/api/health")
                result.raise_for_status()
                assert result.json()["ai_ready"],result.json()
                return
            except httpx.ConnectError:
                await asyncio.sleep(.25)
        raise AssertionError("frozen app never became ready")


def main(archive,port):
    archive=Path(archive).resolve()
    expected=archive.with_suffix(".zip.sha256").read_text().split()[0]
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected,"archive checksum mismatch"
    with tempfile.TemporaryDirectory(prefix="yanghai-frozen-") as directory:
        temp=Path(directory)
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                assert (temp/entry.filename).resolve().is_relative_to(temp)
                assert not entry.filename.endswith("admin-token") and not entry.filename.endswith(".sqlite3")
            z.extractall(temp)
        bundle=next(p for p in temp.iterdir() if p.is_dir())
        executable=bundle/"YanghaiTournament"
        for p in (executable,bundle/"katago/AppRun",bundle/"katago/usr/bin/katago"):
            p.chmod(0o755)
        data=temp/"event-data"
        settings=json.loads((bundle/"tournament.example.json").read_text())
        settings.update({"host":"127.0.0.1","port":port,"open_browser":False,"data_dir":str(data)})
        config=bundle/"tournament.json"
        config.write_text(json.dumps(settings))
        environment=os.environ.copy()
        # The executable must not rely on the development virtualenv, cwd, or a
        # working Internet proxy to load bundled Python/static assets/AI.
        for key in ("KATAGO_BIN","KATAGO_MODEL","KATAGO_CONFIG","AI_CONCURRENCY","TOURNAMENT_DATA"):
            environment.pop(key,None)
        environment["PYTHONHOME"]="/no-system-python-needed"
        environment["PYTHONPATH"]="/no-source-code-needed"
        environment["PATH"]="/usr/bin:/bin"
        for key in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY"):
            environment[key]="http://127.0.0.1:1"
        with open(temp/"server.log","w+") as log:
            diagnostic=subprocess.run([str(executable),"--check","--no-pause"],cwd=temp,env=environment,capture_output=True,text=True,timeout=150)
            assert diagnostic.returncode==0,(diagnostic.stdout,diagnostic.stderr)
            assert not (data/"tournament.sqlite3").exists(),"diagnostic altered tournament DB"

            def start():
                return subprocess.Popen([str(executable),"--no-browser","--no-pause"],cwd=temp,env=environment,stdout=log,stderr=log)

            process=start()
            try:
                asyncio.run(readiness(port))
                report=asyncio.run(tournament_smoke(port,data,32,19,128))
                token=(data/"admin-token").read_text().strip()
                with httpx.Client(base_url=f"http://127.0.0.1:{port}",trust_env=False) as client:
                    for path in ("/","/static/app.js","/static/style.css"):
                        r=client.get(path)
                        assert r.status_code==200 and len(r.content)>100
                    before=client.get("/api/admin/state",headers={"Authorization":"Bearer "+token}).json()
                process.send_signal(signal.SIGINT)
                assert process.wait(timeout=15)==0
                process=start()
                asyncio.run(readiness(port))
                with httpx.Client(base_url=f"http://127.0.0.1:{port}",trust_env=False) as client:
                    after=client.get("/api/admin/state",headers={"Authorization":"Bearer "+token}).json()
                    assert len(after["teams"])==32 and len(after["games"])==16
                    assert after["games"][0]["moves"]==before["games"][0]["moves"]
                    assert all(g["status"]=="paused" for g in after["games"])
                duplicate=subprocess.run([str(executable),"--no-browser","--no-pause","--port",str(port+1)],cwd=temp,env=environment,capture_output=True,text=True,timeout=15)
                assert duplicate.returncode==1 and "已有伺服器" in duplicate.stderr
                assert (data/"admin-token").read_text().strip()==token
                backups=list((data/"backups").glob("before-start-*.sqlite3"))
                assert len(backups)==1
                check=sqlite3.connect(backups[0])
                assert check.execute("PRAGMA quick_check").fetchone()[0]=="ok"
                saved=json.loads(check.execute("SELECT data FROM state").fetchone()[0])
                assert len(saved["teams"])==32 and len(saved["games"])==16
                check.close()
                report.update({"packaged_app":True,"platform":"Linux x86_64","archive":archive.name,
                    "real_ai_diagnostic":True,"assets_without_source_directory":True,
                    "restart_preserves_data":True,"duplicate_launch_rejected":True,"startup_backup_verified":True})
                (ROOT/".runtime/portable-smoke-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
                print(json.dumps(report,ensure_ascii=False,indent=2))
            except Exception:
                log.flush();log.seek(0);print(log.read()[-4000:])
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=15)
                    except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("archive",type=Path)
    parser.add_argument("--port",type=int,default=8769)
    args=parser.parse_args()
    main(args.archive,args.port)
