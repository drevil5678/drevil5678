"use strict";

class KnockoutMode {
  constructor() { this.enabled = false; this.devices = new Map(); this.rendered = ""; this.busy = false; }

  sync(state) {
    if (!state.knockout) return;
    if (!this.enabled) {
      this.enabled = true;
      document.body.classList.add("knockout-mode");
      $("bracket-description").textContent = "依輸入順序相鄰配對；八強、四強一局定勝負，只有冠軍戰三戰兩勝。勝隊自動帶入下一階段。";
      const setup = el("article", undefined, "panel"); setup.id = "knockout-setup";
      setup.innerHTML = `<h2>輸入八強隊名</h2><p>第 1 隊對第 2 隊、第 3 隊對第 4 隊，依此類推。建立後隊名與規則鎖定，請先核對配對順序。</p>
        <form id="knockout-form" class="form-grid">
        ${Array.from({length:8},(_,index)=>`<label>第 ${index+1} 隊（八強第 ${Math.floor(index/2)+1} 組）<input name="team${index+1}" maxlength="60" required></label>`).join("")}
        <label>棋盤<select name="board_size"><option value="19">19 路</option><option value="13">13 路</option><option value="9">9 路</option></select></label>
        <label>黑棋貼目<input name="komi" type="number" value="7.5" step="0.5" min="-50" max="50" required></label>
        <label>規則<select name="rules"><option value="chinese">中國規則</option><option value="japanese">日本規則</option></select></label>
        <label>AI visits<input name="visits" type="number" value="128" min="16" max="100000" required></label>
        <label class="check span"><input name="rules_confirmed" type="checkbox" required>已核對隊名、配對順序與規則；禁止 AI 最佳落點，第 61 手起連續三個自身回合 ≤ 40% 判負，雙方虛手依棋盤現況計分</label>
        <button type="submit">建立八強對決圖</button></form>`;
      document.querySelector("#arena .heading").after(setup);
      const matches = el("section"); matches.id = "knockout-matches"; setup.after(matches);
      const download = el("a", "下載整屆淘汰賽紀錄（JSON）", "download"); download.href = "/api/duel/export"; matches.after(download);
      $("knockout-form").onsubmit = event => {
        event.preventDefault();
        safe(async () => {
          if (this.busy) return;
          this.busy = true; event.target.querySelector("button").disabled = true;
          try {
            const fields = event.target.elements;
            await api("/api/knockout/setup", {method:"POST",body:{
              names:Array.from({length:8},(_,index)=>fields[`team${index+1}`].value),
              board_size:Number(fields.board_size.value),komi:Number(fields.komi.value),
              rules:fields.rules.value,visits:Number(fields.visits.value),rules_confirmed:fields.rules_confirmed.checked,
            }});
            snapshot = await api("/api/state"); this.sync(snapshot); renderArena();
            notice("八強對決圖已建立，請選擇要進行的對戰。");
          } finally { this.busy = false; event.target.querySelector("button").disabled = false; }
        });
      };
      for (const key of ["board_size", "komi", "rules", "visits"]) $("knockout-form").elements[key].value = state.settings[key];
    }
    $("knockout-setup").classList.toggle("hidden",state.knockout.initialized);
    const tree = state.bracket, active = state.games.find(game=>game.status!=="finished");
    const signature = JSON.stringify(tree)+":"+(active?.id||"")+":"+(active?.status||"");
    if (this.rendered===signature) return; this.rendered=signature;
    const target=$("knockout-matches"); target.replaceChildren();
    if (tree.champion) target.append(el("p", `冠軍：${tree.champion.name}`, "champion-banner"));
    if (!state.knockout.initialized) return;
    target.append(el("p", "八強、四強每局每隊 10 分鐘（攜帶電子產品者 5 分鐘）；冠軍戰每局 15 分鐘（攜帶電子產品者 10 分鐘）。每局開啟前確認設備選項。"));
    for (const stage of tree.stages) {
      target.append(el("h2", stage.label));
      const grid=el("div",undefined,"grid");
      for (const match of stage.matches) {
        const card=el("article",undefined,"panel"); card.dataset.knockoutMatch=match.id;
        card.append(el("h3",`第 ${match.number} 組`));
        for (const team of match.teams) card.append(el("p",team?`${team.name}　${match.wins[team.id]||0} 勝`:"待前組勝隊"));
        if (match.winner) card.append(el("strong",`${match.winner.name} ${stage.stage==="final"?"奪冠":"晉級"}`));
        const current=active?.knockout_match===match.id?active:null;
        const devices=[];
        match.teams.forEach(team=>{
          if(!team)return;const label=el("label",undefined,"check"),input=document.createElement("input");input.type="checkbox";
          const key=match.id+":"+team.id;input.checked=this.devices.get(key)||false;input.disabled=!!active||!!match.winner;
          input.onchange=()=>this.devices.set(key,input.checked);label.append(input,el("span",`${team.name} 攜帶電子產品`));card.append(label);devices.push(input);
        });
        const next=button(current?`回到第 ${current.round} 局`:match.winner?"本組已結束":`開啟第 ${match.games.length+1} 局`,async()=>{
          if(current){await openGame(current.id);return;}if(this.busy)return;
          this.busy=true;next.disabled=true;
          try{
            const game=await api(`/api/knockout/matches/${match.id}/next`,{method:"POST",body:{device_a:devices[0]?.checked||false,device_b:devices[1]?.checked||false}});
            snapshot=await api("/api/state");await duelMode.sync(snapshot);this.sync(snapshot);renderArena();await openGame(game.id);
          }finally{this.busy=false;next.disabled=!!snapshot.games.find(game=>game.status!=="finished")&&!current;}
        });
        next.dataset.nextMatch=match.id;
        next.disabled=!current&&(!!active||!!match.winner||!match.teams.every(Boolean));card.append(next);grid.append(card);
      }
      target.append(grid);
    }
  }
}
