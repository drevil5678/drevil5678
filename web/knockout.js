"use strict";

class KnockoutMode {
  constructor() { this.enabled=false; this.devices=new Map(); this.roster=""; this.rendered=""; this.busy=false; }
  sync(state) {
    if (!state.knockout) return;
    if (!this.enabled) {
      this.enabled=true;
      document.body.classList.add("final-four-lan-mode");
      document.title="區域網路四強賽";
      document.querySelector(".brand").replaceChildren(el("span","區域網路四強賽"));
      document.querySelector("#arena .heading h1").textContent="四強上場，爭取冠軍。";
      document.querySelector("#arena .heading p:last-child").textContent="進入自己的對戰桌，選擇隊伍並輸入點棋碼。";
      $("bracket-description").textContent="四強一戰決勝；兩場勝隊進入冠軍戰，三戰兩勝決定冠軍。";
      $("seat-team-label").classList.remove("hidden");
      $("practice-toggle").closest("article").classList.add("final-four-hidden");
      const panel=el("article",undefined,"panel"); panel.id="final-four-organizer";
      panel.innerHTML=`<h2>四強隊伍與開局</h2><p>從內建 18 隊選出四隊。第 1 隊對第 2 隊，第 3 隊對第 4 隊；兩桌可同時進行。</p>
        <form id="knockout-form" class="form-grid">${Array.from({length:4},(_,index)=>`<label>第 ${index+1} 隊（四強第 ${Math.floor(index/2)+1} 組）<select name="team${index+1}" required></select></label>`).join("")}
        <button type="submit">建立四強對決圖</button></form><section id="knockout-matches"></section>`;
      $("admin-content").prepend(panel);
      $("knockout-form").onsubmit=event=>{
        event.preventDefault(); safe(async()=>{
          const f=event.target.elements;
          await api("/api/knockout/setup",{method:"POST",token:adminToken,body:{...adminState.settings,team_ids:Array.from({length:4},(_,i)=>f[`team${i+1}`].value),rules_confirmed:true}});
          await this.refresh(); notice("四強對決圖已建立，請開啟兩場四強。");
        });
      };
      $("swiss").closest(".panel").querySelector("p").textContent="四強一戰決勝；冠軍戰三戰兩勝。可在此備份、匯出或列印隊伍點棋碼。";
    }
    const roster=JSON.stringify(state.teams);
    if (this.roster!==roster) {
      this.roster=roster;
      for(let index=0;index<4;index++){
        const select=$("knockout-form").elements[`team${index+1}`],previous=select.value;
        select.replaceChildren(...state.teams.map(team=>{const option=el("option",`${String(team.order).padStart(2,"0")} · ${team.name}`);option.value=team.id;return option;}));
        select.value=state.teams.some(team=>team.id===previous)?previous:state.teams[index]?.id||"";
      }
    }
    $("knockout-form").classList.toggle("hidden",state.knockout.initialized);
    const signature=JSON.stringify(state.bracket)+":"+!!adminState;
    if (this.rendered===signature)return;this.rendered=signature;
    const target=$("knockout-matches");target.replaceChildren();
    if (!state.knockout.initialized)return;
    for(const stage of state.bracket.stages){
      target.append(el("h3",stage.label));const grid=el("div",undefined,"grid");
      for(const match of stage.matches){
        const card=el("article",undefined,"panel");card.dataset.knockoutMatch=match.id;
        card.append(el("h4",`第 ${match.number} 組`));
        for(const team of match.teams)card.append(el("p",team?`${team.name}　${match.wins[team.id]||0} 勝`:"待前組勝隊"));
        const current=state.games.find(game=>game.knockout_match===match.id&&game.status!=="finished");
        const devices=[];
        for(const team of match.teams){
          if(!team)continue;const label=el("label",undefined,"check"),input=document.createElement("input"),key=match.id+":"+team.id;
          input.type="checkbox";input.checked=this.devices.get(key)||false;input.disabled=!!current||!!match.winner;
          input.onchange=()=>this.devices.set(key,input.checked);label.append(input,el("span",`${team.name} 攜帶電子產品`));card.append(label);devices.push(input);
        }
        const next=button(current?`查看第 ${current.round} 局`:match.winner?"本組已結束":`開啟第 ${match.games.length+1} 局`,async()=>{
          if(current){await openGame(current.id);return;}
          if(this.busy)return;this.busy=true;this.rendered="";this.sync(snapshot);
          try{
            await api(`/api/knockout/matches/${match.id}/next`,{method:"POST",token:adminToken,body:{device_a:devices[0]?.checked||false,device_b:devices[1]?.checked||false}});
            await this.refresh(); notice("對戰桌已開啟，雙方可用隊伍點棋碼入座。");
          }finally{this.busy=false;this.rendered="";this.sync(snapshot);}
        });
        next.dataset.nextMatch=match.id;next.disabled=this.busy||!!match.winner||!match.teams.every(Boolean);card.append(next);grid.append(card);
      }
      target.append(grid);
    }
  }
  async refresh(){snapshot=await api("/api/state");this.rendered="";this.sync(snapshot);renderArena();await refreshAdmin();}
}
