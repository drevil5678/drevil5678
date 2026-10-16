"use strict";
const p$ = id => document.getElementById(id), pLetters = "ABCDEFGHJKLMNOPQRST";
let practiceGame = null, practiceSelected = null, practiceBusy = false;
function pNode(tag, text, cls) { const n = document.createElement(tag); n.textContent = text; if (cls) n.className = cls; return n; }
async function pApi(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json();
  if (!r.ok) throw Error(typeof data.detail === "string" ? data.detail : "請檢查輸入設定");
  return data;
}
function pVersion() { return { game_id: practiceGame.id, version: practiceGame.version }; }
function pDraw() {
  const canvas = p$("practice-board"), c = canvas.getContext("2d"), n = practiceGame?.settings.board_size || 19, step = 660 / (n-1);
  c.clearRect(0,0,740,740); c.fillStyle="#d5ae70"; c.fillRect(0,0,740,740); c.strokeStyle="#775a34"; c.lineWidth=1;
  for(let i=0;i<n;i++) { const v=40+i*step; c.beginPath();c.moveTo(40,v);c.lineTo(700,v);c.moveTo(v,40);c.lineTo(v,700);c.stroke();c.fillStyle="#684b28";c.font="12px system-ui";c.textAlign="center";c.fillText(pLetters[i],v,21);c.fillText(String(n-i),18,v+4); }
  const stars=n===19?[3,9,15]:n===13?[3,6,9]:[2,4,6];
  for(const x of stars) for(const y of stars) { c.beginPath();c.arc(40+x*step,40+y*step,3,0,Math.PI*2);c.fill(); }
  if(!practiceGame) return;
  for(let i=0;i<practiceGame.board.length;i++) { const stone=practiceGame.board[i]; if(stone===".") continue; const x=40+(i%n)*step,y=40+Math.floor(i/n)*step; c.beginPath();c.arc(x,y,step*.44,0,Math.PI*2);const grad=c.createRadialGradient(x-step*.13,y-step*.16,1,x,y,step*.44);grad.addColorStop(0,stone==="B"?"#5a5d58":"#fff");grad.addColorStop(1,stone==="B"?"#141a17":"#d4d8cd");c.fillStyle=grad;c.fill(); }
  practiceGame.candidates.forEach((hint,index)=>{ if(hint.move==="pass")return; const x=40+pLetters.indexOf(hint.move[0])*step,y=40+(n-Number(hint.move.slice(1)))*step,banned=hint.move===practiceGame.forbidden;c.beginPath();c.arc(x,y,step*.32,0,Math.PI*2);c.fillStyle=banned?"#b34638":"#1c6554";c.fill();c.fillStyle="white";c.font="bold 13px system-ui";c.textAlign="center";c.fillText(banned?"×":String(practiceGame.forbidden?index:index+1),x,y+5); });
  if(practiceSelected && practiceSelected!=="pass") { c.strokeStyle="#256cbe";c.lineWidth=3;c.beginPath();c.arc(40+pLetters.indexOf(practiceSelected[0])*step,40+(n-Number(practiceSelected.slice(1)))*step,step*.43,0,Math.PI*2);c.stroke(); }
}
function pRender() {
  const g=practiceGame, playable=!!g&&g.status==="playing"&&!practiceBusy;
  p$("practice-status").textContent=practiceBusy?"AI 分析中，請稍候":!g?"尚未開始":g.status==="finished"?"本局練習已結束":g.status==="error"?"分析暫停":`輪到 ${g.turn==="B"?"黑":"白"} 棋 · ${g.moves.length} 手`;
  p$("practice-rules").textContent=g?`${g.settings.board_size} 路 · 黑貼 ${g.settings.komi} 目 · ${g.settings.rules==="chinese"?"中國":"日本"}規則`:"";
  p$("practice-selection").textContent=practiceSelected?`已選 ${practiceSelected}，請確認落子`:playable?"請選擇落點":"";
  p$("practice-submit").disabled=!playable||!practiceSelected;
  p$("practice-pass").disabled=!playable||g?.forbidden==="pass";
  p$("practice-undo").disabled=!g||!g.moves.length||practiceBusy;
  p$("practice-finish").disabled=!g||g.status==="finished"||practiceBusy;
  p$("practice-retry").classList.toggle("hidden",g?.status!=="error");p$("practice-retry").disabled=practiceBusy;
  p$("practice-sgf").classList.toggle("hidden",!g);
  p$("practice-settings").querySelectorAll("button").forEach(b=>b.disabled=practiceBusy);
  p$("practice-hints").replaceChildren();
  if(g) { if(g.error) p$("practice-hints").append(pNode("p",g.error));g.candidates.forEach((h,i)=>p$("practice-hints").append(pNode("div",`${h.move===g.forbidden?"✕ 禁止":"建議 "+(g.forbidden?i:i+1)}　${h.move}　${(h.winrate*100).toFixed(4)}%`,"hint-row"+(h.move===g.forbidden?" forbidden":"")))); }
  pDraw();
}
async function pRun(action) {
  if(practiceBusy)return;practiceBusy=true;p$("practice-notice").textContent="";pRender();
  try { practiceGame=(await action()).game;practiceSelected=null; }
  catch(e) { p$("practice-notice").textContent=e.message;try {practiceGame=(await pApi("/api/practice/state")).game;}catch{} }
  finally {practiceBusy=false;pRender();}
}
p$("practice-settings").onsubmit=e=>{e.preventDefault();if(practiceGame?.moves.length&&!confirm("開始新局會清除目前練習；需要保存時請先下載 SGF。"))return;const f=e.target.elements;pRun(()=>pApi("/api/practice/new",{board_size:Number(f.board_size.value),komi:Number(f.komi.value),rules:f.rules.value,forbid_best:f.forbid_best.checked}));};
p$("practice-board").onclick=e=>{if(!practiceGame||practiceBusy||practiceGame.status!=="playing")return;const r=e.currentTarget.getBoundingClientRect(),n=practiceGame.settings.board_size,step=660/(n-1),x=Math.round(((e.clientX-r.left)*740/r.width-40)/step),y=Math.round(((e.clientY-r.top)*740/r.height-40)/step);if(x<0||y<0||x>=n||y>=n)return;const move=pLetters[x]+(n-y);if(practiceGame.board[y*n+x]!=="."){p$("practice-notice").textContent="此處已有棋子";return;}if(move===practiceGame.forbidden){p$("practice-notice").textContent="這是 AI 首選，此練習禁止選擇";return;}practiceSelected=move;pRender();};
p$("practice-submit").onclick=()=>pRun(()=>pApi("/api/practice/move",{...pVersion(),move:practiceSelected}));
p$("practice-pass").onclick=()=>pRun(()=>pApi("/api/practice/move",{...pVersion(),move:"pass"}));
for(const action of ["undo","finish","retry"])p$("practice-"+action).onclick=()=>pRun(()=>pApi("/api/practice/"+action,pVersion()));
pApi("/api/practice/state").then(data=>{practiceGame=data.game;pRender();}).catch(()=>{p$("practice-notice").textContent="請用 Start-Practice-Offline.cmd 啟動單機練習後再開啟此頁。";pRender();});
