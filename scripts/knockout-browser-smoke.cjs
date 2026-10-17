"use strict";
const {chromium}=require("playwright"),fs=require("fs"),path=require("path"),os=require("os"),{spawn}=require("child_process");
const root=path.resolve(__dirname,".."),temp=fs.mkdtempSync(path.join(os.tmpdir(),"yanghai-knockout-browser-")),port=8778,base=`http://127.0.0.1:${port}`;
const config=path.join(temp,"knockout.json");
fs.writeFileSync(config,JSON.stringify({data_dir:path.join(temp,"data"),open_browser:false,
 katago_bin:path.join(root,".runtime/katago/squashfs-root/AppRun"),katago_model:path.join(root,".runtime/model.txt.gz"),katago_config:path.join(root,".runtime/analysis.cfg")}));
const server=spawn(path.join(root,".venv/bin/python"),["launcher.py","--knockout","--config",config,"--port",String(port),"--no-browser","--no-pause"],{cwd:root,stdio:["ignore","ignore","pipe"]});
let browser,page,log="",errors=[];server.stderr.on("data",chunk=>log+=chunk.toString());
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function assert(value,message){if(!value)throw Error(message);}
async function until(action,message){for(let i=0;i<300;i++){if(await action())return;await sleep(100);}throw Error(message);}
(async()=>{
 await until(async()=>{try{return(await(await fetch(base+"/api/health")).json()).ai_ready;}catch{return false;}},"Server did not start");
 browser=await chromium.launch({executablePath:"/usr/bin/chromium",headless:true,args:["--no-sandbox","--no-proxy-server"]});
 page=await(await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:"reduce"})).newPage();page.on("pageerror",error=>errors.push(error.message));page.on("dialog",dialog=>dialog.accept());
 await page.goto(base);await page.locator("#knockout-form").waitFor();
 assert(await page.locator('nav [data-view="bracket"]').isVisible(),"Bracket navigation hidden");
 const names=Array.from({length:8},(_,index)=>`自填第${index+1}隊`);
 for(let index=0;index<8;index++)await page.locator(`#knockout-form [name="team${index+1}"]`).fill(names[index]);
 await page.locator('#knockout-form [name="board_size"]').selectOption("9");await page.locator('#knockout-form [name="visits"]').fill("32");await page.locator('#knockout-form [name="rules_confirmed"]').check();
 await page.locator('#knockout-form [type="submit"]').click();await page.locator('[data-next-match="quarter-1"]').waitFor();
 async function state(){return page.evaluate(async()=> (await fetch("/api/state")).json());}
 async function game(){return(await state()).games.at(-1);}
 async function match(id){return(await state()).bracket.stages.flatMap(stage=>stage.matches).find(match=>match.id===id);}
 assert((await state()).teams.length===8,"Default roster leaked into manual tournament");
 assert((await match("quarter-1")).teams[1].name===names[1],"Manual pairing order changed");
 await page.locator('[data-next-match="quarter-1"]').click();await page.locator("#roll-dice").waitFor();
 for(let attempt=0;attempt<50;attempt++){
  for(let side=0;side<2;side++){
   await page.locator("#roll-dice").click();await page.locator("#dice-stage").waitFor({state:"visible"});
   await until(()=>page.locator("#dice-confirm").isEnabled(),"Dice never settled");await page.locator("#dice-confirm").click();
  }
  if((await game()).pregame.winner)break;
 }
 await page.locator("#choose-W").click();await page.locator("#ready-game").click();await until(()=>page.locator("#ready-game").isEnabled(),"Second team cannot ready");await page.locator("#ready-game").click();
 await page.locator("#match-status").filter({hasText:"對弈中"}).waitFor();
 for(let moveIndex=0;moveIndex<2;moveIndex++){
  const current=await game(),move=current.candidates[1].move,canvas=page.locator("#board"),letters="ABCDEFGHJKLMNOPQRST";await canvas.scrollIntoViewIfNeeded();const r=await canvas.boundingBox();
  await page.mouse.click(r.x+(40+letters.indexOf(move[0])*660/8)*r.width/740,r.y+(40+(9-Number(move.slice(1)))*660/8)*r.height/740);
  await page.locator("#submit-move").click();await page.locator("#move-number").filter({hasText:`已落 ${moveIndex+1} 手`}).waitFor();await page.locator("#match-status").filter({hasText:"對弈中"}).waitFor();
 }
 for(let index=0;index<2;index++){
  await page.locator("#pass").click();await page.locator("#move-number").filter({hasText:`已落 ${index+3} 手`}).waitFor();
  if(!index)await page.locator("#match-status").filter({hasText:"對弈中"}).waitFor();
 }
 await page.locator("#match-status").filter({hasText:"雙方停一手計分"}).waitFor();
 async function arena(){await page.locator('nav [data-view="arena"]').click();await page.locator("#knockout-matches").waitFor();}
 async function hostWin(id,winner=null){
  await arena();await until(async()=>await page.locator(`[data-next-match="${id}"]`).isEnabled()&&(await page.locator(`[data-next-match="${id}"]`).innerText()).startsWith("開啟"),"Next game not enabled: "+id);
  const count=(await state()).games.length;await page.locator(`[data-next-match="${id}"]`).click();await until(async()=>(await state()).games.length===count+1,"Next game not created");
  const current=await game();winner=winner||current.seats.B.team;const color=current.seats.B.team===winner?"B":"W";
  await page.locator(`#duel-award-${color}`).filter({hasText:current.seats[color].name}).click();
  await page.locator("#match-status").filter({hasText:current.seats[color].name+" 獲勝"}).waitFor();
  await until(async()=>!!(await game()).result,"Referee result not saved");return winner;
 }
 for(const index of [2,3,4])await hostWin(`quarter-${index}`);
 const first=await hostWin("semi-1");assert(!(await match("semi-1")).winner,"One win incorrectly ends semifinal");
 await hostWin("semi-1",first);assert((await match("semi-1")).wins[first]===2,"Semifinal 2-0 incorrect");
 const secondMatch=await match("semi-2"),a=secondMatch.teams[0].id,b=secondMatch.teams[1].id;
 await hostWin("semi-2",a);await hostWin("semi-2",b);assert(!(await match("semi-2")).winner,"1-1 incorrectly advances");await hostWin("semi-2",b);
 const final=await match("final-1"),champion=final.teams[1].id;
 await hostWin("final-1",final.teams[0].id);await hostWin("final-1",champion);assert(!(await state()).bracket.champion,"Champion declared at 1-1");await hostWin("final-1",champion);
 await page.locator('nav [data-view="bracket"]').click();await page.locator("#bracket-champion").filter({hasText:final.teams[1].name}).waitFor();
 assert(await page.locator(".bracket-match").count()===7,"Eight-team bracket incomplete");assert(!(await page.locator("#bracket").innerText()).includes("20,000"),"Prize returned to bracket");
 await page.screenshot({path:path.join(root,".runtime/knockout-bracket.png"),fullPage:true});await page.reload();await page.locator("#bracket-champion").filter({hasText:final.teams[1].name}).waitFor();
 await arena();assert(await page.locator('[data-next-match="final-1"]').isDisabled(),"Fourth final game allowed");assert(!await page.locator("#knockout-setup").isVisible(),"Saved names can be overwritten");
 assert(!errors.length,"Browser errors: "+errors.join(","));
 const report={passed:true,manual_eight_names:true,real_ai_first_quarter:true,other_games:"organizer adjudication",semifinal_two_zero_and_two_one:true,final_two_one:true,bracket_and_champion:true,reload_preserves_series:true,browser_errors:errors.length};
 fs.writeFileSync(path.join(root,".runtime/knockout-browser-report.json"),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
})().catch(async error=>{console.error(error.stack);if(page)console.error(await page.locator("#notice").innerText());console.error(log.slice(-2000));process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill("SIGTERM");await new Promise(resolve=>server.exitCode!==null?resolve():server.once("exit",resolve));fs.rmSync(temp,{recursive:true,force:true});});
