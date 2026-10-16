"use strict";
const p$ = id => document.getElementById(id), pLetters = "ABCDEFGHJKLMNOPQRST", practiceBase = "/api/local-practice/rooms";
let practiceGame = null, practiceSelected = null, practiceBusy = false, practiceRooms = [], practiceBooted = false, practicePolling = false, practiceEpoch = 0;
let practiceTableId = sessionStorage.getItem("localPracticeTable") || "";
function pNode(tag, text, cls) { const n = document.createElement(tag); n.textContent = text; if (cls) n.className = cls; return n; }
async function pApi(path, body, method = "POST") {
  const response = await fetch(path, body === undefined ? {cache:"no-store"} : {method, headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  let data;try {data=await response.json();}catch {throw Error("無法連線，請稍後重新整理。");}
  if(!response.ok)throw Error(typeof data.detail==="string"?data.detail:"請檢查輸入設定");
  return data;
}
function pPath(action="") {return `${practiceBase}/${practiceGame.id}${action?"/"+action:""}`;}
function pVersion() {return {version:practiceGame.version};}
function pSetGame(game) {
  if(practiceGame?.id!==game?.id || practiceGame?.version!==game?.version)practiceSelected=null;
  practiceGame=game;
  practiceTableId=game?.id||"";
  if(practiceTableId)sessionStorage.setItem("localPracticeTable",practiceTableId);else sessionStorage.removeItem("localPracticeTable");
}
function pDraw() {
  const canvas=p$("practice-board"),c=canvas.getContext("2d"),n=practiceGame?.settings.board_size||19,step=660/(n-1);
  c.clearRect(0,0,740,740);c.fillStyle="#d5ae70";c.fillRect(0,0,740,740);c.strokeStyle="#775a34";c.lineWidth=1;
  for(let i=0;i<n;i++){const v=40+i*step;c.beginPath();c.moveTo(40,v);c.lineTo(700,v);c.moveTo(v,40);c.lineTo(v,700);c.stroke();c.fillStyle="#684b28";c.font="12px system-ui";c.textAlign="center";c.fillText(pLetters[i],v,21);c.fillText(String(n-i),18,v+4);}
  const stars=n===19?[3,9,15]:n===13?[3,6,9]:[2,4,6];for(const x of stars)for(const y of stars){c.beginPath();c.arc(40+x*step,40+y*step,3,0,Math.PI*2);c.fill();}
  if(!practiceGame)return;
  for(let i=0;i<practiceGame.board.length;i++){const stone=practiceGame.board[i];if(stone===".")continue;const x=40+(i%n)*step,y=40+Math.floor(i/n)*step;c.beginPath();c.arc(x,y,step*.44,0,Math.PI*2);const grad=c.createRadialGradient(x-step*.13,y-step*.16,1,x,y,step*.44);grad.addColorStop(0,stone==="B"?"#5a5d58":"#fff");grad.addColorStop(1,stone==="B"?"#141a17":"#d4d8cd");c.fillStyle=grad;c.fill();}
  practiceGame.candidates.forEach((hint,index)=>{if(hint.move==="pass")return;const x=40+pLetters.indexOf(hint.move[0])*step,y=40+(n-Number(hint.move.slice(1)))*step,banned=hint.move===practiceGame.forbidden;c.beginPath();c.arc(x,y,step*.32,0,Math.PI*2);c.fillStyle=banned?"#b34638":"#1c6554";c.fill();c.fillStyle="white";c.font="bold 13px system-ui";c.textAlign="center";c.fillText(banned?"×":String(practiceGame.forbidden?index:index+1),x,y+5);});
  const last=practiceGame.moves.at(-1);if(last&&last[1]!=="pass"){c.strokeStyle=last[0]==="B"?"white":"#1b2e27";c.lineWidth=2;c.beginPath();c.arc(40+pLetters.indexOf(last[1][0])*step,40+(n-Number(last[1].slice(1)))*step,step*.12,0,Math.PI*2);c.stroke();}
  if(practiceSelected&&practiceSelected!=="pass"){c.strokeStyle="#256cbe";c.lineWidth=3;c.beginPath();c.arc(40+pLetters.indexOf(practiceSelected[0])*step,40+(n-Number(practiceSelected.slice(1)))*step,step*.43,0,Math.PI*2);c.stroke();}
}
function pRender() {
  const g=practiceGame,playable=!!g&&g.status==="playing"&&g.human_turn&&!practiceBusy;
  let status=!g?"尚未開始":g.status==="finished"?"本局練習已結束":g.status==="error"?"分析暫停":g.status==="analyzing"?(g.mode==="ai"&&!g.human_turn?"電腦思考中…":"AI 分析中，請稍候…"):`輪到 ${g.turn==="B"?"黑":"白"} 棋`;
  if(g?.mode==="ai"&&g.status==="playing")status+=` · 你執${g.human_color==="B"?"黑":"白"}`;
  p$("practice-status").textContent=status;p$("practice-move-number").textContent=`已落 ${g?.moves.length||0} 手${g?.status==="finished"?"":` · 接下來第 ${(g?.moves.length||0)+1} 手`}`;
  p$("practice-rules").textContent=g?`${g.settings.board_size} 路 · 黑貼 ${g.settings.komi} 目 · ${g.settings.rules==="chinese"?"中國":"日本"}規則`:"";
  p$("practice-selection").textContent=practiceSelected?`已選 ${practiceSelected}，請確認落子`:playable?"請選擇落點":"";
  p$("practice-submit").disabled=!playable||!practiceSelected;p$("practice-pass").disabled=!playable||g?.forbidden==="pass";
  p$("practice-undo").disabled=!g||!g.moves.some(m=>g.mode==="local"||m[0]===g.human_color)||practiceBusy;
  p$("practice-finish").disabled=!g||g.status==="finished"||practiceBusy;p$("practice-close").disabled=!g||practiceBusy;
  p$("practice-retry").classList.toggle("hidden",g?.status!=="error");p$("practice-retry").disabled=practiceBusy;
  p$("practice-sgf").classList.toggle("hidden",!g);if(g)p$("practice-sgf").href=pPath("sgf");
  p$("practice-settings").querySelector("button").disabled=practiceBusy||!practiceBooted;p$("practice-table").disabled=practiceBusy;
  p$("practice-hints").replaceChildren();if(g){if(g.error)p$("practice-hints").append(pNode("p",g.error));g.candidates.forEach((h,i)=>p$("practice-hints").append(pNode("div",`${h.move===g.forbidden?"✕ 禁止":"建議 "+(g.forbidden?i:i+1)}　${h.move}　${(h.winrate*100).toFixed(4)}%`,"hint-row"+(h.move===g.forbidden?" forbidden":""))));}
  const result=g?.result;let resultText="";if(result){const winner=result.winner==="B"?"黑棋獲勝":result.winner==="W"?"白棋獲勝":"平手";if(result.score)resultText=`${winner} · 黑 ${result.score.black}／白 ${result.score.white}，差 ${result.score.margin}。依棋盤現況計分，未另移除死子。`;else resultText=result.reason==="resign"?winner+"（認輸）":"本局已結束，可下載棋譜或收桌。";}
  p$("practice-result").textContent=resultText;pDraw();
}
async function pList(epoch=practiceEpoch) {
  const data=await pApi(practiceBase);if(epoch!==practiceEpoch)return false;practiceBooted=true;practiceRooms=data.rooms;
  p$("practice-capacity").textContent=`目前全場 ${data.tables}／${data.max_tables} 桌 · 這個瀏覽器 ${data.rooms.length} 桌`;
  const select=p$("practice-table");select.replaceChildren();if(!data.rooms.length){const empty=pNode("option","尚未開桌");empty.value="";select.append(empty);}
  for(const room of data.rooms){const option=pNode("option",`${room.name} · ${room.mode==="ai"?"人對電腦":"同機雙人"} · ${room.moves} 手`);option.value=room.id;select.append(option);}
  if(!data.rooms.some(r=>r.id===practiceTableId)){practiceTableId=data.rooms[0]?.id||"";pSetGame(null);practiceTableId=data.rooms[0]?.id||"";}
  select.value=practiceTableId;return true;
}
async function pSync() {
  const epoch=practiceEpoch;if(!await pList(epoch)||epoch!==practiceEpoch)return;const ident=practiceTableId;if(ident){const data=await pApi(`${practiceBase}/${ident}`);if(epoch!==practiceEpoch||ident!==practiceTableId)return;pSetGame(data.game);}pRender();
}
async function pRun(action) {
  if(practiceBusy)return;practiceEpoch++;practiceBusy=true;p$("practice-notice").textContent="";pRender();
  try{const result=await action();if(result.game)pSetGame(result.game);else if(result.closed)pSetGame(null);await pList();}
  catch(error){p$("practice-notice").textContent=error.message;try{if(practiceTableId)pSetGame((await pApi(`${practiceBase}/${practiceTableId}`)).game);}catch{pSetGame(null);}}
  finally{practiceBusy=false;pRender();}
}
p$("practice-settings").elements.mode.onchange=e=>{p$("practice-settings").elements.human_color.disabled=e.target.value!=="ai";};
p$("practice-settings").onsubmit=event=>{event.preventDefault();const f=event.target.elements;pRun(()=>pApi(practiceBase,{name:f.name.value,mode:f.mode.value,human_color:f.human_color.value,board_size:Number(f.board_size.value),komi:Number(f.komi.value),rules:f.rules.value,forbid_best:f.forbid_best.checked}));};
p$("practice-board").onclick=event=>{if(!practiceGame||practiceBusy||practiceGame.status!=="playing"||!practiceGame.human_turn)return;const rect=event.currentTarget.getBoundingClientRect(),n=practiceGame.settings.board_size,step=660/(n-1),x=Math.round(((event.clientX-rect.left)*740/rect.width-40)/step),y=Math.round(((event.clientY-rect.top)*740/rect.height-40)/step);if(x<0||y<0||x>=n||y>=n)return;const move=pLetters[x]+(n-y);if(practiceGame.board[y*n+x]!=="."){p$("practice-notice").textContent="此處已有棋子";return;}if(move===practiceGame.forbidden){p$("practice-notice").textContent="這是 AI 首選，此桌禁止選擇";return;}practiceSelected=move;pRender();};
p$("practice-submit").onclick=()=>pRun(()=>pApi(pPath("move"),{...pVersion(),move:practiceSelected}));
p$("practice-pass").onclick=()=>pRun(()=>pApi(pPath("move"),{...pVersion(),move:"pass"}));
for(const action of ["undo","retry"])p$("practice-"+action).onclick=()=>pRun(()=>pApi(pPath(action),pVersion()));
p$("practice-finish").onclick=()=>{if(confirm(practiceGame?.mode==="ai"?"要認輸並結束這局練習嗎？":"要結束這局練習嗎？"))pRun(()=>pApi(pPath("finish"),pVersion()));};
p$("practice-close").onclick=()=>{if(confirm("收桌會清除本局，需要保存時請先下載 SGF。確定收桌？"))pRun(()=>pApi(pPath(),pVersion(),"DELETE"));};
p$("practice-table").onchange=async event=>{practiceEpoch++;const epoch=practiceEpoch;practiceTableId=event.target.value;practiceSelected=null;try{const data=await pApi(`${practiceBase}/${practiceTableId}`);if(epoch!==practiceEpoch)return;pSetGame(data.game);pRender();}catch(error){p$("practice-notice").textContent=error.message;}};
p$("practice-refresh").onclick=()=>pRun(async()=>{await pSync();return {};});
pRender();pSync().catch(error=>{p$("practice-notice").textContent=error.message;pRender();});
setInterval(async()=>{if(practiceBusy||practicePolling||document.hidden)return;practicePolling=true;try{await pSync();}catch(error){p$("practice-notice").textContent=error.message;}finally{practicePolling=false;}},1200);
