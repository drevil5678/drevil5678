"use strict";
// Isolated real Chromium + real KataGo, with one shared computer and no team login.
const {chromium} = require("playwright");
const fs = require("fs"), path = require("path"), os = require("os"), {spawn} = require("child_process");
const root = path.resolve(__dirname, ".."), data = fs.mkdtempSync(path.join(os.tmpdir(), "yanghai-duel-browser-"));
const port = 8776, base = `http://127.0.0.1:${port}`;
const config = path.join(data, "duel.json");
fs.writeFileSync(config, JSON.stringify({data_dir:path.join(data,"data"), open_browser:false,
  katago_bin:path.join(root,".runtime/katago/squashfs-root/AppRun"),
  katago_model:path.join(root,".runtime/model.txt.gz"), katago_config:path.join(root,".runtime/analysis.cfg")}));
const server = spawn(path.join(root,".venv/bin/python"), ["launcher.py","--duel","--config",config,"--port",String(port),"--no-browser","--no-pause"], {cwd:root,stdio:["ignore","ignore","pipe"]});
let browser, page, log="", errors=[], requests=[];
server.stderr.on("data",chunk=>log+=chunk.toString());
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function assert(value,message){if(!value)throw Error(message);}
async function until(action,message){for(let i=0;i<300;i++){if(await action())return;await sleep(100);}throw Error(message);}
(async()=>{
 await until(async()=>{try{return(await(await fetch(base+"/api/health")).json()).ai_ready;}catch{return false;}},"Real AI did not start");
 browser=await chromium.launch({executablePath:"/usr/bin/chromium",headless:true,args:["--no-sandbox","--no-proxy-server"]});
 page=await(await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:"reduce"})).newPage();
 page.on("pageerror",error=>errors.push(error.message));page.on("request",request=>requests.push(request.url()));page.on("dialog",dialog=>dialog.accept());
 await page.goto(base);await page.locator("#duel-form").waitFor();
 assert(!await page.locator("nav").isVisible(),"Tournament navigation is exposed in backup mode");
 await page.locator('#duel-form [name="team_a"]').fill("單機甲隊");await page.locator('#duel-form [name="team_b"]').fill("單機乙隊");
 await page.locator('#duel-form [name="board_size"]').selectOption("9");await page.locator('#duel-form [name="visits"]').fill("32");
 await sleep(1300);assert(await page.locator('#duel-form [name="board_size"]').inputValue()==="9","Polling reset unsaved settings");
 await page.locator('#duel-form [name="championship"]').check();await page.locator('#duel-form [name="device_a"]').check();await page.locator('#duel-form [name="rules_confirmed"]').check();
 await page.locator('#duel-form [type="submit"]').click();await page.locator("#match").waitFor({state:"visible"});
 assert(!await page.locator("#seat-form").isVisible(),"Offline players still require team login");
 let game;
 async function current(){return page.evaluate(async()=>{const state=await(await fetch("/api/state")).json();return state.games.at(-1);});}
 for(let attempt=0;attempt<50;attempt++){
  for(let side=0;side<2;side++){
   await page.locator("#roll-dice").waitFor();await page.locator("#roll-dice").click();
   await page.locator("#dice-stage").waitFor({state:"visible"});
   await until(()=>page.locator("#dice-confirm").isEnabled(),"Dice confirmation never enabled");
   await sleep(350);assert(await page.locator("#dice-stage").isVisible(),"Dice dismissed without player confirmation");
   await page.locator("#dice-confirm").click();
  }
  game=await current();if(game.pregame.winner)break;
 }
 assert(game.pregame.winner,"All dice tied");
 await page.locator("#choose-W").click();
 await page.locator("#ready-game").click();await until(async()=>!(await current()).pregame.ready[game.pregame.winner]||!(await page.locator("#ready-game").isDisabled()),"Second player cannot ready");
 await until(()=>page.locator("#ready-game").isEnabled(),"Second ready missing");await page.locator("#ready-game").click();
 await page.locator("#match-status").filter({hasText:"對弈中"}).waitFor();
 const before=await current();assert(before.remaining.B>0&&before.remaining.W>0,"Clock not running");
 async function playSuggestion(){
  await page.locator("#match-status").filter({hasText:"對弈中"}).waitFor();
  const g=await current(),move=g.candidates[1].move,letters="ABCDEFGHJKLMNOPQRST",n=9;
  assert((await page.locator("#duel-operator").innerText()).includes(g.seats[g.turn].name),"Wrong team controls current turn");
  const canvas=page.locator("#board");await canvas.scrollIntoViewIfNeeded();const r=await canvas.boundingBox();
  await page.mouse.click(r.x+(40+letters.indexOf(move[0])*660/(n-1))*r.width/740,r.y+(40+(n-Number(move.slice(1)))*660/(n-1))*r.height/740);
  await page.locator("#submit-move").click();await until(async()=>(await current()).moves.length===g.moves.length+1,"Move was not accepted");
  await page.locator("#move-number").filter({hasText:`已落 ${g.moves.length+1} 手`}).waitFor();
 }
 await playSuggestion();await playSuggestion();
 await page.locator("#match-status").filter({hasText:"對弈中"}).waitFor();
 assert((await page.locator("#move-number").innerText()).includes("已落 2 手"),"Move count missing");
 await page.locator("#duel-pause").click();await page.locator("#match-status").filter({hasText:"裁判暫停"}).waitFor();
 const saved=(await current()).board;await page.reload();await page.locator("#duel-resume").waitFor();
 assert((await current()).board===saved,"Refresh discarded board");
 await page.locator("#duel-resume").click();await page.locator("#match-status").filter({hasText:"對弈中"}).waitFor();
 await page.screenshot({path:path.join(root,".runtime/duel-desktop.png"),fullPage:true});
 for(let i=0;i<2;i++){
  const count=(await current()).moves.length;
  await page.locator("#pass").click();
  await until(async()=>(await current()).moves.length===count+1,"Pass not accepted");
  await page.locator("#move-number").filter({hasText:`已落 ${count+1} 手`}).waitFor();
  if(i===0)await page.locator("#match-status").filter({hasText:"對弈中"}).waitFor();
 }
 await page.locator("#match-status").filter({hasText:"雙方停一手計分"}).waitFor();assert((await current()).result.points===1,"Double pass not scored");
 const sgf=await page.evaluate(async()=>{const g=(await(await fetch("/api/state")).json()).games.at(-1);return(await fetch(`/api/games/${g.id}/sgf`)).text();});assert(sgf.includes("KM[7.5]"),"SGF komi missing");
 await page.locator("#back").click();await page.locator("#duel-form").waitFor();
 await until(()=>page.locator('#duel-form [type="submit"]').isEnabled(),"Cannot start next game");
 await page.locator('#duel-form [name="team_a"]').fill("下一局甲隊");await page.locator('#duel-form [name="team_b"]').fill("下一局乙隊");
 await page.locator('#duel-form [name="rules_confirmed"]').check();
 await page.locator('#duel-form [type="submit"]').click();await page.locator("#match").waitFor({state:"visible"});
 assert((await current()).seats.B.name==="下一局甲隊","Next game did not preserve locked settings");
 assert(!errors.length,"Browser errors: "+errors.join(","));assert(requests.every(url=>url.startsWith(base)),"Offline page requested external service");
 const report={passed:true,real_ai:true,one_browser_two_teams:true,no_login:true,dice_confirmation:true,automatic_turn_switch:true,refresh_preserves_board:true,double_pass_scored:true,no_external_requests:true,browser_errors:errors.length};
 fs.writeFileSync(path.join(root,".runtime/duel-browser-report.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
})().catch(async error=>{console.error(error.stack);if(page)console.error(await page.locator("#notice").innerText());console.error(JSON.stringify(errors));console.error(log.slice(-2000));process.exitCode=1;}).finally(async()=>{
 if(browser)await browser.close();server.kill("SIGTERM");await new Promise(resolve=>server.exitCode!==null?resolve():server.once("exit",resolve));fs.rmSync(data,{recursive:true,force:true});
});
