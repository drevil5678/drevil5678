"use strict";
const {chromium} = require("playwright"), fs = require("fs"), os = require("os"), path = require("path"), {spawn} = require("child_process");
const root = path.resolve(__dirname, ".."), temp = fs.mkdtempSync(path.join(os.tmpdir(), "practice-lan-browser-")), port = 8775;
const config = path.join(temp, "tournament.json");
fs.writeFileSync(config, JSON.stringify({host:"127.0.0.1", data_dir:path.join(temp,"formal-data"), open_browser:false,
  katago_bin:path.join(root,".runtime/katago/squashfs-root/AppRun"), katago_model:path.join(root,".runtime/model.txt.gz"), katago_config:path.join(root,".runtime/analysis.cfg")}));
const env = {...process.env};
for (const key of ["KATAGO_BIN","KATAGO_MODEL","KATAGO_CONFIG","AI_CONCURRENCY","TOURNAMENT_DATA"]) delete env[key];
const server = spawn(path.join(root,".venv/bin/python"), [path.join(root,"launcher.py"),"--config",config,"--practice-lan","--port",String(port),"--no-browser","--no-pause"], {cwd:root,env,stdio:["ignore","ignore","pipe"]});
let log = "", browser, base = `http://127.0.0.1:${port}`;
server.stderr.on("data", chunk => log += chunk.toString());
const pause = ms => new Promise(resolve => setTimeout(resolve,ms));
function assert(ok, message) { if (!ok) throw Error(message); }
async function api(endpoint, body, token, method="POST") {
  const r = await fetch(base+endpoint, {method:body===undefined?"GET":method, headers:{...(body===undefined?{}:{"Content-Type":"application/json"}), ...(token?{Authorization:"Bearer "+token}:{})}, ...(body===undefined?{}:{body:JSON.stringify(body)})});
  assert(r.ok, "practice browser helper API failed: "+r.status); return r.json();
}
function roomPath(id) { return "/api/practice-lan/rooms/"+id; }
async function waitGame(id, count, status="playing") {
  for(let i=0;i<400;i++) { const g=(await api(roomPath(id))).game; if(g.status===status&&g.moves.length===count)return g; assert(g.status!=="error","real AI failed"); await pause(50); }
  throw Error("practice browser room timed out");
}
async function clickMove(page, move, size=9) {
  const board=page.locator("#room-board");await board.scrollIntoViewIfNeeded();
  const rect=await board.boundingBox(), letters="ABCDEFGHJKLMNOPQRST", step=660/(size-1);
  await board.click({position:{x:(40+letters.indexOf(move[0])*step)*rect.width/740,y:(40+(size-Number(move.slice(1)))*step)*rect.height/740}});
}
(async()=>{
  for(let i=0;i<160;i++) { try { const h=await api("/api/health");if(h.ai_ready)break; }catch{}if(i===159)throw Error("practice browser server not ready");await pause(250); }
  const addresses=await api("/api/practice-lan/network");assert(addresses.urls.length,"no private LAN interface");base=addresses.urls[0].replace(/\/$/,"");
  browser=await chromium.launch({executablePath:"/usr/bin/chromium",headless:true,args:["--no-sandbox","--no-proxy-server"]});
  const contexts=await Promise.all([0,1,2].map(()=>browser.newContext({viewport:{width:1440,height:1050}})));
  const [owner,guest,spectator]=await Promise.all(contexts.map(c=>c.newPage())),errors=[];
  for(const p of [owner,guest,spectator]) {p.on("pageerror",e=>errors.push(e.message));p.on("dialog",d=>d.accept());}
  await owner.goto(base);
  await owner.locator('#lobby-create [name="name"]').fill("瀏覽器真人桌");
  await owner.locator('#lobby-create [name="player_name"]').fill("測試黑方");
  await owner.locator('#lobby-create [name="board_size"]').selectOption("9");
  await owner.locator("#lobby-create-button").click();
  await owner.waitForURL(/#room\/[a-f0-9]{16}$/);
  const id=new URL(owner.url()).hash.split("/")[1];
  await owner.locator("#room-status").filter({hasText:"等待對手"}).waitFor();
  await guest.goto(owner.url());
  await guest.locator("#room-join-name").fill("測試白方");
  await guest.locator("#room-join button").click();
  await owner.locator("#room-role").filter({hasText:"你執黑棋"}).waitFor();
  await guest.locator("#room-role").filter({hasText:"你執白棋"}).waitFor();
  await spectator.goto(owner.url());
  await spectator.locator("#room-role").filter({hasText:"觀戰模式"}).waitFor();
  assert(await spectator.locator("#room-submit").isDisabled(),"spectator can submit moves");
  let g=await waitGame(id,0);
  await owner.locator("#room-status").filter({hasText:"輪到黑棋"}).waitFor();
  await clickMove(owner,g.forbidden);
  await owner.locator("#lobby-notice").filter({hasText:"禁止選擇"}).waitFor();
  await clickMove(owner,g.candidates[1].move);await owner.locator("#room-submit").click();
  g=await waitGame(id,1);await guest.locator("#room-status").filter({hasText:"輪到白棋"}).waitFor();
  await clickMove(guest,g.candidates[1].move);await guest.locator("#room-submit").click();
  await waitGame(id,2);await owner.locator("#room-status").filter({hasText:"輪到黑棋"}).waitFor();
  await owner.locator("#room-undo").click();
  await guest.locator("#room-undo-accept").waitFor({state:"visible"});await guest.locator("#room-undo-accept").click();
  await waitGame(id,1);
  await owner.reload();await owner.locator("#room-role").filter({hasText:"你執黑棋"}).waitFor();
  await owner.locator("#room-back").click();
  await owner.locator('#lobby-create [name="mode"]').selectOption("ai");
  await owner.locator('#lobby-create [name="human_color"]').selectOption("W");
  await owner.locator('#lobby-create [name="board_size"]').selectOption("9");
  await owner.locator('#lobby-create [name="name"]').fill("瀏覽器電腦桌");
  await owner.locator("#lobby-create-button").click();await owner.waitForURL(/#room\/[a-f0-9]{16}$/);
  const computerId=new URL(owner.url()).hash.split("/")[1];assert(computerId!==id,"new room replaced existing game");
  g=await waitGame(computerId,1);await owner.locator("#room-status").filter({hasText:"輪到白棋"}).waitFor();
  await clickMove(owner,g.candidates[1].move);await owner.locator("#room-submit").click();await waitGame(computerId,3);
  await owner.locator("#room-status").filter({hasText:"輪到白棋"}).waitFor();await owner.locator("#room-undo").click();await waitGame(computerId,1);
  await owner.locator("#room-status").filter({hasText:"輪到白棋"}).waitFor();
  const sgf=await (await fetch(base+roomPath(computerId)+"/sgf")).text();assert(sgf.includes("KM[7.5]"),"room SGF komi missing");
  await owner.setViewportSize({width:390,height:844});await owner.screenshot({path:path.join(root,".runtime/practice-lan-mobile.png"),fullPage:true});
  await owner.setViewportSize({width:1440,height:1050});
  for(let i=0;i<13;i++)await api("/api/practice-lan/rooms",{name:"等待桌 "+i,board_size:9});
  await owner.locator("#room-back").click();await owner.locator("#lobby-count").filter({hasText:"15 / 15"}).waitFor();
  assert(await owner.locator("#lobby-create-button").isDisabled(),"full lobby still enables creation");
  await owner.screenshot({path:path.join(root,".runtime/practice-lan-lobby.png"),fullPage:true});
  const full=await fetch(base+"/api/practice-lan/rooms",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});assert(full.status===429,"sixteenth room accepted");
  await owner.goto(base+"/#room/"+computerId);await owner.locator("#room-close").waitFor({state:"visible"});await owner.locator("#room-close").click();
  await owner.locator("#lobby-count").filter({hasText:"14 / 15"}).waitFor();assert(await owner.locator("#lobby-create-button").isEnabled(),"closed room did not release slot");
  assert((await api(roomPath(id))).game.moves.length===1,"another room's moves changed");
  assert(errors.length===0,"browser JavaScript errors: "+errors.join(","));
  const report={passed:true,real_ai:true,lan_browser_verified:true,checks:["player-created PvP room","separate browser seats","spectator read-only","forbidden choice blocked","alternating human moves","opponent-approved undo","reload restores seat","computer plays black and replies","AI round undo","SGF komi","mobile room layout","15-table lobby and full-state button","server rejects sixteenth room","owner close releases slot","other room remains unchanged"],browser_errors:errors.length};
  fs.writeFileSync(path.join(root,".runtime/practice-lan-browser-report.json"),JSON.stringify(report,null,2)+"\n");console.log(JSON.stringify(report,null,2));
})().catch(e=>{console.error(e.stack);console.error(log.slice(-2500));process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();server.kill("SIGTERM");await new Promise(resolve=>server.exitCode!==null?resolve():server.once("exit",resolve));fs.rmSync(temp,{recursive:true,force:true});
});
