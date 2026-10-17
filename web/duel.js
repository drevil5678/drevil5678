"use strict";

// The local PK view shares the tournament's board, dice and game controls.
// This class stays inactive on the normal LAN website.
class DuelMode {
  constructor() { this.enabled = false; this.credentials = {}; }

  async sync(state) {
    if (!state.duel) return;
    Object.assign(this.credentials, state.duel.credentials);
    if (!this.enabled) {
      this.enabled = true;
      document.body.classList.add("duel-mode");
      document.title = state.knockout ? "四強賽 · 單機版" : "單機雙隊 PK · 楊海分站賽";
      diceStage.shared = true;
      document.querySelector(".brand").replaceChildren(el("span", state.knockout ? "四強賽 · 單機版" : "單機雙隊 PK"));
      const heading = document.querySelector("#arena .heading");
      heading.querySelector("div").replaceChildren(el("h1", state.knockout ? "四支隊伍，爭取冠軍。" : "兩支隊伍，一台電腦。"), el("p", state.knockout ? "四強一戰決勝，冠軍戰三戰兩勝。兩隊在這台電腦輪流操作，每局重新擲骰猜先。" : "不需網路，由雙方輪流操作。使用正式判勝規則，紀錄獨立保存；比賽結果請交主辦登錄。"));
      if (!state.knockout) {
      const panel = el("article", undefined, "panel");
      panel.id = "duel-setup";
      panel.innerHTML = `
        <h2>建立備用對戰</h2>
        <form id="duel-form" class="form-grid">
          <label>隊伍一<input name="team_a" value="甲隊" maxlength="60" required></label>
          <label>隊伍二<input name="team_b" value="乙隊" maxlength="60" required></label>
          <label>棋盤<select name="board_size"><option value="19">19 路</option><option value="13">13 路</option><option value="9">9 路</option></select></label>
          <label>黑棋貼目<input name="komi" type="number" step="0.5" min="-50" max="50" value="7.5" required></label>
          <label>規則<select name="rules"><option value="chinese">中國規則</option><option value="japanese">日本規則</option></select></label>
          <label>AI 分析 visits<input name="visits" type="number" min="16" max="100000" value="128" required></label>
          <label class="check"><input name="championship" type="checkbox">冠軍戰計時（15 分鐘；自備設備 10 分鐘）</label>
          <label class="check"><input name="device_a" type="checkbox">隊伍一攜帶電子產品</label>
          <label class="check"><input name="device_b" type="checkbox">隊伍二攜帶電子產品</label>
          <label class="check span"><input name="rules_confirmed" type="checkbox" required>已核對棋盤、貼目與計分規則；同意從第 61 手起，連續三個自身回合勝率 ≤ 40% 判負</label>
          <p id="duel-setup-status" class="span"></p>
          <button type="submit">建立對戰並猜先</button>
          <a href="/api/duel/export" class="download">下載全部備用對戰紀錄</a>
        </form>`;
      heading.after(panel);
      $("duel-form").onsubmit = event => {
        event.preventDefault();
        safe(async () => {
          const form = event.target, fields = form.elements;
          const submit = form.querySelector('[type="submit"]');
          submit.disabled = true;
          try {
            const game = await api("/api/duel/new", {method: "POST", body: {
              team_a: fields.team_a.value, team_b: fields.team_b.value,
              board_size: Number(fields.board_size.value), komi: Number(fields.komi.value),
              rules: fields.rules.value, visits: Number(fields.visits.value),
              championship: fields.championship.checked,
              device_a: fields.device_a.checked, device_b: fields.device_b.checked,
              rules_confirmed: fields.rules_confirmed.checked,
            }});
            snapshot = await api("/api/state");
            await this.sync(snapshot);
            renderArena();
            await openGame(game.id);
          } finally { submit.disabled = (snapshot.games || []).some(game => game.status !== "finished"); }
        });
      };
      }
      const controls = el("article", undefined, "panel");
      controls.id = "duel-controls";
      controls.append(el("h2", "同機輪流操作"));
      const operator = el("p"); operator.id = "duel-operator"; controls.append(operator);
      const actions = el("div", undefined, "actions");
      const pause = button("主辦暫停", () => this.action("pause", {reason: "單機備援主辦暫停"}), "secondary");
      pause.id = "duel-pause";
      pause.disabled = true;
      const resume = button("主辦恢復對弈", () => this.action("resume")); resume.id = "duel-resume";
      resume.disabled = true;
      actions.append(pause, resume);
      for (const color of ["B", "W"]) {
        const adjudicate = button("", async () => {
          const seat = currentGame.seats[color];
          if (confirm(`主辦確認裁定 ${seat.name} 獲勝？`)) await this.action("adjudicate", {winner: seat.team, points: 1});
        }, "secondary");
        adjudicate.id = "duel-award-" + color;
        adjudicate.disabled = true;
        actions.append(adjudicate);
      }
      controls.append(actions, el("small", "主辦裁定前可先暫停；分析時雙方計時暫停。關閉程式會保存棋局，重啟後請按恢復對弈。"));
      document.querySelector("#match aside").prepend(controls);
      $("back").textContent = state.knockout ? "← 返回四強對戰與紀錄" : "← 返回備用對戰與紀錄";
      const latest = state.games.at(-1);
      if (!state.knockout && latest && !location.hash.startsWith("#game/")) await openGame(latest.id);
    }
    if (state.knockout) return;
    const fields = $("duel-form").elements, locked = !!state.games.length;
    for (const key of ["board_size", "komi", "rules", "visits"]) {
      if (locked || !this.settingsLoaded) fields[key].value = state.settings[key];
      fields[key].disabled = locked;
    }
    this.settingsLoaded = true;
    const ongoing = state.games.some(game => game.status !== "finished");
    $("duel-form").querySelector('[type="submit"]').disabled = ongoing;
    $("duel-setup-status").textContent = ongoing ? "目前有未結束棋局，請先完成或由主辦裁定。" : locked ? "可建立下一局；已保存的棋盤、貼目與分析設定保持一致。" : "一般賽局每隊 10 分鐘；攜帶電子產品者 5 分鐘。建立後雙方擲骰、選色並準備。";
  }

  selectSeat(game) {
    if (!this.enabled || !game) return;
    const teams = Object.values(game.seats).map(seat => seat.team), p = game.pregame;
    let team = game.seats[game.turn].team;
    if (game.status === "ready") {
      if ((pregameBusy || diceStage.presentation) && teams.includes(seatTeam)) team = seatTeam;
      else if (!p.winner) team = teams.find(id => !p.rolls[id]) || teams[0];
      else if (!p.color_choice) team = p.winner;
      else team = teams.find(id => !p.ready[id]) || teams[0];
    }
    seatTeam = team;
    seatColor = ["B", "W"].find(color => game.seats[color].team === team);
    seatToken = this.credentials[game.id]?.[team] || "";
    const seat = game.seats[seatColor];
    $("duel-operator").textContent = game.status === "finished" ? "本局已結束，可下載 SGF 或返回建立下一局。" : `目前操作：${seat.name}${game.status === "ready" ? "（猜先與準備）" : `（${seatColor === "B" ? "黑" : "白"}棋）`}，請將操作交給該隊。`;
    $("duel-pause").disabled = !["playing", "analyzing"].includes(game.status);
    $("duel-resume").disabled = game.status !== "paused";
    for (const color of ["B", "W"]) {
      $("duel-award-" + color).textContent = `主辦裁定 ${game.seats[color].name} 勝`;
      $("duel-award-" + color).disabled = game.status === "finished";
    }
  }

  async action(action, extra = {}) {
    await api(`/api/duel/games/${currentGame.id}/action`, {method: "POST", body: {action, ...extra}});
    if (snapshot?.knockout) {
      snapshot = await api("/api/state");
      knockoutMode.sync(snapshot);
      renderArena();
    }
    await refreshGame();
  }
}
