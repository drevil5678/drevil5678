"use strict";
const {chromium}=require("playwright"),fs=require("fs"),path=require("path"),os=require("os"),{spawn}=require("child_process");
const root=path.resolve(__dirname,".."),temp=fs.mkdtempSync(path.join(os.tmpdir(),"yanghai-final-four-lan-browser-")),port=8008;
const address=Object.values(os.networkInterfaces()).flat().find(item=>item.family==="IPv4"&&!item.internal&&/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(item.address)).address,base=`http://${address}:${port}`;
const config=path.join(temp,"final-four-lan.json");
fs.writeFileSync(config,JSON.stringify({port,data_dir:path.join(temp,"data"),open_browser:false,katago_bin:path.join(root,".runtime/katago/squashfs-root/AppRun"),katago_model:path.join(root,".runtime/model.txt.gz"),katago_config:path.join(root,".runtime/analysis.cfg")}));
const server=spawn(path.join(root,".venv/bin/python"),["launcher.py","--final-four-lan","--config",config,"--no-browser","--no-pause"],{cwd:root,stdio:["ignore","ignore","pipe"]});
let browser,host,log="",errors=[];server.stderr.on("data",chunk=>log+=chunk.toString());
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function assert(value,message){if(!value)throw Error(message);}
async function until(action,message){for(let i=0;i<400;i++){if(await action())return;await sleep(100);}throw Error(message);}
async function get(url){return(await fetch(base+url)).json();}
async function state(){return get("/api/state");}
async function match(id){return(await state()).bracket.stages.flatMap(stage=>stage.matches).find(item=>item.id===id);}
async function game(id){return get(`/api/games/${id}`);}
(async()=>{
 await until(async()=>{try{return(await get("/api/health")).ai_ready;}catch{return false;}},"LAN server did not start");
 const key=fs.readFileSync(path.join(temp,"final-four-lan-data/admin-token"),"utf8").trim();
 const privateState=await(await fetch(base+"/api/admin/state",{headers:{Authorization:"Bearer "+key}})).json(),roster=privateState.teams,chosen=[0,1,10,15].map(index=>roster[index]);
 assert(roster.length===18,"Built-in roster missing");assert(!(await state()).duel,"LAN must not expose local credentials");
 browser=await chromium.launch({executablePath:"/usr/bin/chromium",headless:true,args:["--no-sandbox","--no-proxy-server"]});
 async function page(){const p=await(await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:"reduce"})).newPage();p.on("pageerror",error=>errors.push(error.message));return p;}
 host=await page();await host.goto(base+"/#admin");await host.locator("#admin-token").fill(key);await host.locator('#login button[type="submit"]').click();await host.locator("#knockout-form").waitFor({state:"visible"});
 assert(await host.locator("#knockout-form select").count()===4,"Four selectors required");
 for(let i=0;i<4;i++){assert(await host.locator(`#knockout-form [name="team${i+1}"] option`).count()===18,"Roster options missing");await host.locator(`#knockout-form [name="team${i+1}"]`).selectOption(chosen[i].id);}
 await host.locator('#knockout-form [type="submit"]').click();await host.locator('[data-next-match="semi-1"]').waitFor();
 await host.locator('[data-next-match="semi-1"]').click();await until(async()=>(await match("semi-1")).games.length===1,"First table not opened");
 await host.locator('[data-next-match="semi-2"]').click();await until(async()=>(await match("semi-2")).games.length===1,"Second table not opened");
 const firstId=(await match("semi-1")).games[0].id,secondId=(await match("semi-2")).games[0].id;
 assert((await state()).games.filter(g=>g.status!=="finished").length===2,"Both semifinals must run simultaneously");
 const players=[];
 for(let i=0;i<4;i++){
  const p=await page(),id=i<2?firstId:secondId;p.on("dialog",dialog=>dialog.accept());players.push(p);await p.goto(base+"/#game/"+id);await p.locator("#seat-token").waitFor();
  assert(!await p.locator("#knockout-form").isVisible(),"Host selector exposed to player");
  await p.locator("#seat-team-choice").selectOption(chosen[i].id);
  if(i===0){await p.locator("#seat-token").fill("WRONG");await p.locator('#seat-form button[type="submit"]').click();await p.locator("#notice").filter({hasText:"操作碼"}).waitFor();}
  await p.locator("#seat-token").fill(chosen[i].play_code);await p.locator('#seat-form button[type="submit"]').click();await p.locator("#seat-status").filter({hasText:"點棋手"}).waitFor();
 }
 assert((await fetch(base+"/api/knockout/matches/final-1/next",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"})).status===401,"Player can create matches");
 const publicText=JSON.stringify(await state());assert(roster.every(team=>!publicText.includes(team.play_code)),"Team code leaked publicly");
 async function acknowledge(p){for(let i=0;i<8;i++){if(!await p.locator("#dice-stage").isVisible())return;await until(()=>p.locator("#dice-confirm").isEnabled(),"Dice did not settle");await p.locator("#dice-confirm").click();await sleep(150);}}
 for(let attempt=0;attempt<50;attempt++){
  for(const p of players.slice(0,2)){await until(async()=>{await acknowledge(p);return await p.locator("#roll-dice").isEnabled();},"Player cannot roll");await p.locator("#roll-dice").click();await p.locator("#dice-stage").waitFor({state:"visible"});await until(()=>p.locator("#dice-confirm").isEnabled(),"Dice did not settle");await p.locator("#dice-confirm").click();}
  if((await game(firstId)).pregame.winner)break;
 }
 const latest=await game(firstId);for(const p of players.slice(0,2)){await until(async()=>(await p.evaluate(()=>currentGame?.version))>=latest.version,"Player did not receive dice result");await acknowledge(p);}
 const rolled=await game(firstId),winnerIndex=chosen.findIndex(team=>team.id===rolled.pregame.winner);await players[winnerIndex].locator("#choose-W").click();
 for(const p of players.slice(0,2)){await until(()=>p.locator("#ready-game").isEnabled(),"Player cannot ready");await p.locator("#ready-game").click();}
 await until(async()=>(await game(firstId)).status==="playing","Game did not start");
 for(let index=0;index<4;index++){
  const current=await game(firstId),p=players[chosen.findIndex(team=>team.id===current.seats[current.turn].team)];
  await p.locator("#move-number").filter({hasText:`已落 ${index} 手`}).waitFor();await p.locator("#match-status").filter({hasText:"對弈中"}).waitFor();
  if(index<2){
   const n=(await state()).settings.board_size,move=current.candidates[1].move,canvas=p.locator("#board"),letters="ABCDEFGHJKLMNOPQRST";await canvas.scrollIntoViewIfNeeded();const r=await canvas.boundingBox();
   await p.mouse.click(r.x+(40+letters.indexOf(move[0])*660/(n-1))*r.width/740,r.y+(40+(n-Number(move.slice(1)))*660/(n-1))*r.height/740);await p.locator("#submit-move").click();
  }else await p.locator("#pass").click();
  try{await until(async()=>{const g=await game(firstId);return g.moves.length===index+1&&["playing","finished"].includes(g.status);},"Move not saved");}
  catch(error){const actual=await game(firstId);throw Error(`Move ${index+1} failed: ${await p.locator("#notice").innerText()} / ${await p.locator("#selection").innerText()} / status=${actual.status} moves=${actual.moves.length} turn=${actual.turn}`);}
 }
 assert((await game(firstId)).result.reason==="score","Double pass did not score");
 host.on("dialog",dialog=>dialog.accept(dialog.type()==="prompt"?(dialog.message().includes("勝方請輸入")?(host._winnerColor||"B"):"1"):undefined));
 async function award(id,winner){
  const g=await game(id);host._winnerColor=g.seats.B.team===winner?"B":"W";
  const card=host.locator(`#referee-games [data-game-id="${id}"]`);await card.getByRole("button",{name:"裁定結果",exact:true}).click();await until(async()=>!!(await game(id)).result,"Host adjudication failed");
 }
 await award(secondId,chosen[2].id);
 const final=await match("final-1"),champion=final.teams[1].id;
 for(const winner of [final.teams[0].id,champion,champion]){
  const next=host.locator('[data-next-match="final-1"]');await until(async()=>await next.isEnabled()&&(await next.innerText()).startsWith("開啟"),"Next final game not enabled");
  const count=(await match("final-1")).games.length;await next.click();await until(async()=>(await match("final-1")).games.length===count+1,"Final not created");
  const id=(await match("final-1")).games.at(-1).id;await award(id,winner);
  if(count<2)assert(!(await state()).bracket.champion,"Champion declared before two wins");
 }
 const spectator=await page();await spectator.goto(base+"/#bracket");await spectator.locator("#bracket-champion").filter({hasText:final.teams[1].name}).waitFor();
 assert(await spectator.locator(".bracket-match").count()===3,"Wrong bracket size");await spectator.screenshot({path:path.join(root,".runtime/final-four-lan-bracket.png"),fullPage:true});
 assert(!errors.length,"Browser errors: "+errors.join(","));
 const report={passed:true,port,lan_address:address,host_dropdown_18_teams:true,separate_player_browsers:4,backend_codes:true,no_public_credentials:true,two_semifinal_tables:true,real_ai_first_semifinal:true,other_games:"organizer adjudication",semifinal_single_game:true,final_two_one:true,browser_errors:errors.length};
 fs.writeFileSync(path.join(root,".runtime/knockout-browser-report.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
})().catch(async error=>{console.error(error.stack);if(host)console.error(await host.locator("#notice").innerText());console.error(log.slice(-2000));process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill("SIGTERM");await new Promise(resolve=>server.exitCode!==null?resolve():server.once("exit",resolve));fs.rmSync(temp,{recursive:true,force:true});});
