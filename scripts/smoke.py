"""Real KataGo smoke + 50 parallel HTTP clients, with isolated tournament data."""
import asyncio
import json
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent


async def run(port, data, team_count, board_size, visits, host="127.0.0.1"):
    base = f"http://{host}:{port}"
    async with httpx.AsyncClient(base_url=base, timeout=180, limits=httpx.Limits(max_connections=80), trust_env=False) as client:
        for _ in range(120):
            try:
                response = await client.get("/api/health")
                if response.status_code == 200:
                    assert response.json()["ai_ready"], response.json()
                    break
            except httpx.ConnectError:
                pass
            await asyncio.sleep(.25)
        else:
            raise AssertionError("Server did not become ready")
        auth = {"Authorization": "Bearer " + (data/"admin-token").read_text().strip()}

        async def post(path, body=None, headers=auth):
            response = await client.post(path, headers=headers, json=body)
            assert response.status_code == 200, (path, response.status_code, response.text)
            return response.json()

        await post("/api/admin/settings", {"board_size":board_size, "rules":"chinese", "komi":7.5,
            "visits":visits, "threshold":.4, "allow_pass":True, "rules_confirmed":True})
        await post("/api/admin/teams/import", {"csv": "name,captain,member\n" + "\n".join(f"測試{i},甲{i},乙{i}" for i in range(team_count))})
        admin = (await client.get("/api/admin/state", headers=auth)).json()
        for team in admin["teams"]:
            await client.put("/api/admin/teams/"+team["id"], headers=auth, json={**team, "checked_in": True, "eligibility_checked": True})
        await post("/api/admin/rounds/swiss")
        admin = (await client.get("/api/admin/state",headers=auth)).json()
        games = admin["games"]
        for g in games:
            await post(f"/api/admin/games/{g['id']}/action", {"action":"start"})
        latency = []

        async def poll(index):
            # Separate clients/connections emulate independent player browsers.
            async with httpx.AsyncClient(base_url=base, timeout=30, trust_env=False) as browser:
                for _ in range(5):
                    started = time.perf_counter()
                    response = await browser.get("/api/games/"+games[index%len(games)]["id"] if index<32 else "/api/state")
                    response.raise_for_status()
                    assert "token" not in response.text
                    latency.append(time.perf_counter()-started)
                    await asyncio.sleep(.1)

        await asyncio.gather(*(poll(i) for i in range(50)))
        deadline=time.time()+180
        while time.time()<deadline:
            states = await asyncio.gather(*(client.get("/api/games/"+g["id"]) for g in games))
            public = [r.json() for r in states]
            if any(g["status"]=="paused" for g in public):
                audit=(await client.get("/api/admin/state",headers=auth)).json()["events"]
                raise AssertionError([e for e in audit if e["kind"]=="ai_error"])
            if all(g["status"]=="playing" for g in public):
                break
            await asyncio.sleep(.1)
        else:
            raise AssertionError("All simultaneous game analyses did not finish")
        for g in public:
            assert len(g["candidates"])>=2 and g["forbidden"]==g["candidates"][0]["move"]
        g, admin_g = public[0], games[0]
        player = {"Authorization":"Bearer "+admin_g["seat_tokens"]["B"]}
        response = await client.post(f"/api/games/{g['id']}/move",headers=player,json={"move":g["forbidden"],"version":g["version"]})
        assert response.status_code==400
        await post(f"/api/games/{g['id']}/move", {"move":g["candidates"][1]["move"],"version":g["version"]}, player)
        for _ in range(600):
            after=(await client.get("/api/games/"+g["id"])).json()
            if after["status"]=="playing":break
            assert after["status"]!="paused",after
            await asyncio.sleep(.1)
        assert after["turn"]=="W" and len(after["moves"])==1 and after["candidates"]
        await post("/api/admin/pause-all", {"action":"pause","reason":"煙霧測試完成"})
        await post("/api/admin/backup")
        return {"ai":"real KataGo", "board_size":board_size, "visits":visits,
                "simultaneous_games":len(games),"clients":50,"successful_requests":len(latency),
                "http_p95_ms":round(sorted(latency)[int(len(latency)*.95)-1]*1000,1),
                "analysis_completion_max_s":max(g["analysis_seconds"] for g in public),
                "forbidden_move_rejected":True,"legal_move_and_next_analysis":True,"all_paused_and_backed_up":True}


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--teams",type=int,default=32)
    parser.add_argument("--board",type=int,default=19)
    parser.add_argument("--visits",type=int,default=128)
    parser.add_argument("--port",type=int,default=8766)
    parser.add_argument("--report",default=".runtime/smoke-report.json")
    args=parser.parse_args()
    if not 2<=args.teams<=32 or args.teams%2:parser.error("teams must be even, 2..32")
    if args.board not in (9,13,19):parser.error("board must be 9,13,19")
    with tempfile.TemporaryDirectory(prefix="yanghai-smoke-") as directory:
        data=Path(directory)
        env=os.environ.copy()
        env["TOURNAMENT_DATA"]=str(data)
        env.setdefault("KATAGO_BIN",str(ROOT/".runtime/katago/squashfs-root/AppRun"))
        env.setdefault("KATAGO_MODEL",str(ROOT/".runtime/model.txt.gz"))
        env.setdefault("KATAGO_CONFIG",str(ROOT/".runtime/analysis.cfg"))
        with open(data/"server.log","w+") as log:
            process=subprocess.Popen([str(ROOT/".venv/bin/python"),"-m","uvicorn","server.app:create_app","--factory","--host","127.0.0.1","--port",str(args.port),"--workers","1","--no-access-log"],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                report=asyncio.run(run(args.port,data,args.teams,args.board,args.visits))
                report["measured_at_utc"]=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
                path=ROOT/args.report
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
                print(json.dumps(report,ensure_ascii=False,indent=2))
            except Exception:
                log.seek(0)
                print(log.read()[-4000:])
                engine_log=data/"katago.log"
                if engine_log.exists():print(engine_log.read_text()[-4000:])
                raise
            finally:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=="__main__":
    main()
