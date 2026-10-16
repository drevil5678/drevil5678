"use strict";
const $ = (id) => document.getElementById(id);
const labels = {
  ready: "等待開賽",
  analyzing: "AI 分析中 · 計時暫停",
  playing: "對弈中",
  paused: "裁判暫停",
  finished: "已結束",
};
const stages = {
  swiss: "瑞士賽",
  quarter: "八強賽",
  semi: "四強賽",
  final: "冠軍戰",
};
const letters = "ABCDEFGHJKLMNOPQRST";
let snapshot,
  adminState,
  currentGame,
  selected,
  seatColor,
  receivedAt,
  currentView = "arena";
let networkState;
let selectedRecordTeam = sessionStorage.getItem("teamId") || null;
let adminToken = sessionStorage.getItem("adminToken") || "",
  seatToken = "",
  selectedGame = null,
  lastVersion = null;
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function button(text, action, cls) {
  const b = el("button", text, cls);
  b.type = "button";
  b.onclick = () => safe(action);
  return b;
}
function notice(message) {
  $("notice").textContent = message;
  $("notice").classList.add("show");
  clearTimeout(notice.timer);
  notice.timer = setTimeout(() => $("notice").classList.remove("show"), 10000);
}
async function safe(action) {
  try {
    await action();
  } catch (error) {
    notice(error.message);
  }
}
async function api(path, { method = "GET", body, token } = {}) {
  const headers = {};
  if (token) headers.Authorization = "Bearer " + token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("伺服器暫時無法回應");
    }
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "輸入格式錯誤，請檢查欄位",
    );
  }
  return response.json();
}
function showView(view) {
  currentView = view;
  document
    .querySelectorAll(".view")
    .forEach((s) => s.classList.toggle("hidden", s.id !== view));
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view !== "match") {
    selectedGame = null;
    currentGame = null;
    location.hash = view;
  }
  if (view === "admin" && adminToken) safe(refreshAdmin);
  if (view === "records") safe(refreshRecords);
}
document
  .querySelectorAll("nav button")
  .forEach((b) => (b.onclick = () => showView(b.dataset.view)));
function table(headers, rows) {
  const wrapper = el("div", undefined, "table-scroll"),
    t = el("table"),
    head = el("thead"),
    tr = el("tr");
  headers.forEach((h) => tr.append(el("th", h)));
  head.append(tr);
  t.append(head);
  const body = el("tbody");
  for (const row of rows) {
    const r = el("tr");
    for (const value of row) {
      const c = el("td");
      c.append(value instanceof Node ? value : el("span", value));
      r.append(c);
    }
    body.append(r);
  }
  t.append(body);
  wrapper.append(t);
  return wrapper;
}
function timeText(seconds) {
  seconds = Math.max(0, Math.ceil(seconds));
  return (
    String(Math.floor(seconds / 60)).padStart(2, "0") +
    ":" +
    String(seconds % 60).padStart(2, "0")
  );
}
function renderArena() {
  const s = snapshot.settings;
  $("guide-settings").textContent = `本場 ${s.board_size} 路 · 黑貼 ${s.komi} 目 · ${s.rules === "chinese" ? "中國" : "日本"}規則。勝率已計入此貼目；從第 ${s.loss_start_ply} 手起，連續 ${s.loss_turns} 個自身回合 ≤ ${(s.threshold * 100).toFixed(2)}% 判負。${s.allow_pass ? "允許停一手，雙方停一手後由裁判處理。" : "本場不允許停一手。"}`;
  $("rules-summary").replaceChildren(
    ...[
      `${s.board_size} 路 · 黑貼 ${s.komi} 目`,
      "每方 10 分鐘 / 自備設備 5 分鐘",
      `第 61 手起 · 連續 3 回合 ≤ ${(s.threshold * 100).toFixed(2)}% 判負`,
    ].map((t) => el("span", t)),
  );
  const round = snapshot.rounds.at(-1);
  $("round-label").textContent = round
    ? `${stages[round.stage]} · 第 ${round.number} 輪`
    : "等待主辦方開賽";
  const games = round
    ? snapshot.games.filter((g) => g.round_id === round.id)
    : [];
  $("empty").classList.toggle("hidden", !!games.length);
  $("games").replaceChildren();
  for (const g of games) {
    const card = el("article", undefined, "game-card");
    card.append(
      el("span", `TABLE ${String(g.table).padStart(2, "0")}`, "eyebrow"),
      el("h3", `${g.seats.B.name}  vs  ${g.seats.W.name}`),
      el("span", labels[g.status], "badge"),
      el(
        "p",
        `黑 ${timeText(g.remaining.B)}　白 ${timeText(g.remaining.W)} · ${g.moves.length} 手`,
      ),
      button("進入對戰桌", () => openGame(g.id), "secondary"),
    );
    $("games").append(card);
  }
  $("ranking").replaceChildren(
    table(
      ["順位", "隊伍", "積分", "對手分總和", "報名序"],
      snapshot.standings.map((t) => [
        t.rank,
        button(t.name, () => openTeam(t.id), "secondary team-link"),
        t.score,
        t.buchholz,
        t.order,
      ]),
    ),
  );
  $("awards").replaceChildren();
  renderRecordSelector();
  if (snapshot.awards.length) {
    $("awards").append(
      el("h2", "獎勵與出場費"),
      table(
        ["隊伍", "名次", "隊長積分", "可分配積分", "獎金", "出場費"],
        snapshot.awards.map((a) => [
          a.team,
          a.placement,
          a.captain_points,
          a.distributable_points,
          a.prize_twd,
          a.appearance_twd,
        ]),
      ),
    );
  }
}
function renderRecordSelector() {
  const select = $("record-team"), teams = snapshot.teams || [];
  if (!teams.some(t => t.id === selectedRecordTeam)) selectedRecordTeam = teams[0]?.id || null;
  select.replaceChildren();
  if (!teams.length) select.append(el("option", "尚無報名隊伍"));
  for (const team of teams) { const option = el("option", team.name); option.value = team.id; select.append(option); }
  if (selectedRecordTeam) select.value = selectedRecordTeam;
}
async function openTeam(ident) {
  selectedRecordTeam = ident;
  showView("records");
  $("record-team").value = ident;
  location.hash = "team/" + ident;
  await refreshRecords();
}
$("record-team").onchange = () => safe(() => openTeam($("record-team").value));
$("team-entry").onsubmit = event => {
  event.preventDefault();
  safe(async () => {
    const code = $("team-code").value.trim(), team = await api("/api/team/identify", { token: code });
    sessionStorage.setItem("teamCode", code);
    sessionStorage.setItem("teamId", team.id);
    $("team-code").value = "";
    await openTeam(team.id);
    notice("已辨識本隊；進入本隊對戰桌時會自動使用隊伍點棋碼");
  });
};
async function refreshRecords() {
  if (!snapshot) return;
  renderRecordSelector();
  const target = $("team-records"), ident = selectedRecordTeam;
  if (!ident) { target.replaceChildren(el("p", "主辦方建立隊伍後，會顯示對戰紀錄。")); return; }
  const data = await api(`/api/teams/${ident}/history`);
  if (ident !== selectedRecordTeam) return;
  target.replaceChildren(el("h2", data.team.name));
  if (data.standing) target.append(el("p", `目前第 ${data.standing.rank} 名 · ${data.standing.score} 分 · 對手分總和 ${data.standing.buchholz}`));
  target.append(el("p", data.next.message, "record-next"));
  for (const [title, rows] of [["當前對戰", data.current], ["已排定的下一場", data.upcoming], ["歷史對戰", data.history]]) {
    const panel = el("article", undefined, "panel"); panel.append(el("h2", title));
    if (!rows.length) panel.append(el("p", title === "歷史對戰" ? "尚無已完成的對戰。" : "目前沒有已排定的對戰。"));
    else panel.append(table(["賽程", "桌次", "本隊棋色", "對手", "狀態／結果", "積分", "棋局"], rows.map(g => {
      const outcome = g.outcome ? `${g.outcome === "win" ? "勝" : "負"} · ${g.reason === "threshold" ? "連續低勝率" : g.reason === "timeout" ? "時間耗盡" : "裁判裁定"}` : labels[g.status];
      const links = el("div"); links.append(button("查看棋局", () => openGame(g.id), "secondary"));
      if (g.status === "finished") { const a = el("a", "SGF"); a.href = `/api/games/${g.id}/sgf`; a.className = "download"; links.append(a); }
      return [`${stages[g.stage]} · 第 ${g.round} 輪`, g.table, g.color === "B" ? "黑" : "白", button(g.opponent, () => openTeam(g.opponent_id), "secondary"), outcome, g.outcome ? g.points : "—", links];
    })));
    target.append(panel);
  }
}
async function openGame(id) {
  selectedGame = id;
  seatColor = null;
  selected = null;
  lastVersion = null;
  seatToken = sessionStorage.getItem("seat:" + id) || "";
  $("seat-token").value = seatToken;
  currentView = "match";
  document
    .querySelectorAll(".view")
    .forEach((s) => s.classList.toggle("hidden", s.id !== "match"));
  location.hash = "game/" + id;
  await refreshGame();
  const teamId = sessionStorage.getItem("teamId"), code = sessionStorage.getItem("teamCode");
  if (code && Object.values(currentGame.seats).some(s => s.team === teamId)) seatToken = code;
  $("seat-token").value = seatToken;
  if (seatToken) await joinSeat();
}
async function refreshGame() {
  if (!selectedGame) return;
  currentGame = await api("/api/games/" + selectedGame);
  receivedAt = performance.now();
  if (currentGame.version !== lastVersion) {
    selected = null;
    lastVersion = currentGame.version;
  }
  renderMatch();
}
function renderMatch() {
  const g = currentGame;
  if (!g) return;
  $("match-title").replaceChildren(
    el("h1", `${g.seats.B.name}　vs　${g.seats.W.name}`),
    el(
      "span",
      `${stages[g.stage]} · 第 ${g.round} 輪 · 第 ${g.table} 桌`,
      "badge",
    ),
  );
  $("match-status").textContent =
    labels[g.status] +
    (g.pause_reason && g.status === "paused" ? "：" + g.pause_reason : "");
  if (g.result) {
    const winner = Object.values(g.seats).find(
      (s) => s.team === g.result.winner,
    );
    $("match-status").textContent =
      `${winner.name} 獲勝 · ${g.result.reason === "threshold" ? "連續低勝率" : g.result.reason === "timeout" ? "對手時間耗盡" : g.result.reason}`;
  }
  $("seat-status").textContent = seatColor
    ? `你是 ${seatColor === "B" ? "黑" : "白"} 棋點棋手`
    : "目前為觀戰模式";
  $("own-device").disabled = g.status !== "ready" || !seatColor;
  $("save-device").disabled = g.status !== "ready" || !seatColor;
  if (seatColor) $("own-device").checked = g.seats[seatColor].own_device;
  $("pass").disabled = !(
    seatColor === g.turn &&
    g.status === "playing" &&
    snapshot.settings.allow_pass
  );
  $("sgf").href = `/api/games/${g.id}/sgf`;
  $("hints").replaceChildren(
    el("p", `黑連續低勝率 ${g.streak.B}/3 · 白 ${g.streak.W}/3`),
  );
  for (const [index, hint] of g.candidates.entries())
    $("hints").append(
      el(
        "div",
        `${index === 0 ? "✕ 禁止" : "建議 " + index}　${hint.move}　${(hint.winrate * 100).toFixed(4)}%`,
        "hint-row" + (index === 0 ? " forbidden" : ""),
      ),
    );
  if (g.analysis_seconds !== undefined)
    $("hints").append(
      el("small", `本次分析 ${g.analysis_seconds} 秒 · 勝率為輪到行棋方觀點`),
    );
  updateSelection();
  drawBoard();
  renderClocks();
}
function renderClocks() {
  if (!currentGame) return;
  const g = currentGame,
    delta =
      g.status === "playing" ? (performance.now() - receivedAt) / 1000 : 0;
  $("clocks").replaceChildren();
  for (const color of ["B", "W"]) {
    const remaining = g.remaining[color] - (g.turn === color ? delta : 0);
    const box = el(
      "div",
      undefined,
      "clock" + (g.turn === color && g.status === "playing" ? " active" : ""),
    );
    box.append(
      el("small", color === "B" ? "● 黑棋" : "○ 白棋"),
      el("strong", timeText(remaining)),
      el("small", g.seats[color].name),
    );
    $("clocks").append(box);
  }
}
function updateSelection() {
  const allowed =
    currentGame &&
    currentGame.status === "playing" &&
    currentGame.turn === seatColor;
  $("selection").textContent = selected
    ? `已選 ${selected} · 請確認落子`
    : allowed
      ? "請選擇落點"
      : seatColor
        ? "等待輪到你行棋"
        : "輸入操作碼後可行棋";
  $("submit-move").disabled = !allowed || !selected;
}
function coord(move, size) {
  if (move === "pass") return null;
  return [letters.indexOf(move[0]), size - Number(move.slice(1))];
}
function drawBoard() {
  const g = currentGame;
  if (!g || !snapshot) return;
  const canvas = $("board"),
    c = canvas.getContext("2d"),
    n = snapshot.settings.board_size,
    size = 740,
    pad = 40,
    step = (size - 2 * pad) / (n - 1);
  c.clearRect(0, 0, size, size);
  c.fillStyle = "#d5ae70";
  c.fillRect(0, 0, size, size);
  c.strokeStyle = "#775a34";
  c.lineWidth = 1;
  for (let i = 0; i < n; i++) {
    const v = pad + i * step;
    c.beginPath();
    c.moveTo(pad, v);
    c.lineTo(size - pad, v);
    c.moveTo(v, pad);
    c.lineTo(v, size - pad);
    c.stroke();
    c.fillStyle = "#684b28";
    c.font = "12px system-ui";
    c.textAlign = "center";
    c.fillText(letters[i], v, 21);
    c.fillText(String(n - i), 18, v + 4);
  }
  const stars = n === 19 ? [3, 9, 15] : n === 13 ? [3, 6, 9] : [2, 4, 6];
  for (const x of stars)
    for (const y of stars) {
      c.beginPath();
      c.arc(pad + x * step, pad + y * step, 3, 0, Math.PI * 2);
      c.fill();
    }
  for (let i = 0; i < g.board.length; i++) {
    const stone = g.board[i];
    if (stone === ".") continue;
    const x = pad + (i % n) * step,
      y = pad + Math.floor(i / n) * step;
    c.beginPath();
    c.arc(x, y, step * 0.44, 0, Math.PI * 2);
    const gradient = c.createRadialGradient(
      x - step * 0.13,
      y - step * 0.16,
      1,
      x,
      y,
      step * 0.44,
    );
    gradient.addColorStop(0, stone === "B" ? "#5a5d58" : "#fff");
    gradient.addColorStop(1, stone === "B" ? "#141a17" : "#d4d8cd");
    c.fillStyle = gradient;
    c.shadowColor = "#0004";
    c.shadowBlur = 3;
    c.shadowOffsetY = 2;
    c.fill();
    c.shadowBlur = 0;
    c.shadowOffsetY = 0;
  }
  g.candidates.forEach((hint, index) => {
    const p = coord(hint.move, n);
    if (!p) return;
    const x = pad + p[0] * step,
      y = pad + p[1] * step;
    c.fillStyle = index === 0 ? "#b34638" : "#1c6554";
    c.beginPath();
    c.arc(x, y, step * 0.32, 0, Math.PI * 2);
    c.fill();
    c.fillStyle = "white";
    c.font = `bold ${Math.max(11, step * 0.34)}px system-ui`;
    c.textAlign = "center";
    c.fillText(index === 0 ? "×" : index, x, y + step * 0.12);
  });
  if (selected) {
    const p = coord(selected, n);
    if (p) {
      c.strokeStyle = "#256cbe";
      c.lineWidth = 3;
      c.beginPath();
      c.arc(pad + p[0] * step, pad + p[1] * step, step * 0.43, 0, Math.PI * 2);
      c.stroke();
    }
  }
  const last = g.moves.at(-1);
  if (last) {
    const p = coord(last[1], n);
    if (p) {
      c.strokeStyle = "#dc9354";
      c.lineWidth = 2;
      c.strokeRect(pad + p[0] * step - 4, pad + p[1] * step - 4, 8, 8);
    }
  }
}
$("board").onclick = (event) => {
  if (
    !currentGame ||
    currentGame.status !== "playing" ||
    seatColor !== currentGame.turn
  )
    return;
  const rect = $("board").getBoundingClientRect(),
    n = snapshot.settings.board_size,
    step = 660 / (n - 1);
  const x = Math.round(
      (((event.clientX - rect.left) * 740) / rect.width - 40) / step,
    ),
    y = Math.round(
      (((event.clientY - rect.top) * 740) / rect.height - 40) / step,
    );
  if (x < 0 || x >= n || y < 0 || y >= n) return;
  if (currentGame.board[y * n + x] !== ".") {
    notice("此處已有棋子");
    return;
  }
  const move = letters[x] + (n - y);
  if (move === currentGame.forbidden) {
    notice("這是 AI 最高勝率落點，禁止選擇");
    return;
  }
  selected = move;
  updateSelection();
  drawBoard();
};
async function sendMove(move) {
  await api(`/api/games/${selectedGame}/move`, {
    method: "POST",
    body: { move, version: currentGame.version },
    token: seatToken,
  });
  selected = null;
  await refreshGame();
}
$("submit-move").onclick = () => safe(() => sendMove(selected));
$("pass").onclick = () =>
  safe(async () => {
    if (currentGame.forbidden === "pass")
      throw new Error("停一手是本回合 AI 最高勝率選點，不可使用");
    if (confirm("確定停一手？雙方連續停一手將交由裁判處理。"))
      await sendMove("pass");
  });
$("back").onclick = () => showView("arena");
async function joinSeat() {
  const s = await api(`/api/games/${selectedGame}/seat`, { token: seatToken });
  seatColor = s.color;
  if (s.credential === "team") {
    sessionStorage.setItem("teamCode", seatToken);
    sessionStorage.setItem("teamId", s.team);
    selectedRecordTeam = s.team;
  }
  sessionStorage.setItem("seat:" + selectedGame, seatToken);
  renderMatch();
}
$("seat-form").onsubmit = (event) => {
  event.preventDefault();
  seatToken = $("seat-token").value.trim();
  safe(joinSeat);
};
$("save-device").onclick = () =>
  safe(async () => {
    await api(`/api/games/${selectedGame}/device`, {
      method: "POST",
      token: seatToken,
      body: { own_device: $("own-device").checked },
    });
    await refreshGame();
    notice("設備選項已儲存");
  });
$("admin-token").value = adminToken;
$("login").onsubmit = (event) => {
  event.preventDefault();
  adminToken = $("admin-token").value.trim();
  safe(async () => {
    await refreshAdmin();
    sessionStorage.setItem("adminToken", adminToken);
    loadSettings();
  });
};
$("logout").onclick = () => {
  sessionStorage.removeItem("adminToken");
  adminToken = "";
  adminState = null;
  $("admin-token").value = "";
  $("admin-content").classList.add("hidden");
};
async function refreshAdmin() {
  adminState = await api("/api/admin/state", { token: adminToken });
  const wasHidden = $("admin-content").classList.contains("hidden");
  $("admin-content").classList.remove("hidden");
  if (wasHidden) loadSettings();
  renderAdmin();
  try {
    await refreshNetwork();
  } catch (error) {
    $("network-message").textContent = "區網資訊暫時無法取得，請按「重新檢查」。";
  }
}
async function refreshNetwork() {
  const network = await api("/api/admin/network", { token: adminToken });
  if (JSON.stringify(networkState) === JSON.stringify(network)) return;
  networkState = network;
  const previous = $("network-address").value;
  $("network-address").replaceChildren();
  for (const item of network.interfaces) {
    const option = el("option", `${item.name} · ${item.address}`);
    option.value = item.url;
    $("network-address").append(option);
  }
  if (network.interfaces.some((item) => item.url === previous))
    $("network-address").value = previous;
  $("network-mode").textContent = !network.lan_enabled
    ? "本機測試模式"
    : network.interfaces.length ? "區網模式 · 網址已取得" : "等待場地網路";
  $("network-mode").className = "badge" + (network.warning ? " warn" : "");
  $("network-message").textContent = network.warning || (network.interfaces.length > 1
    ? "找到多個網路位址，請選擇與選手相同場地網路的網卡；不要選 VPN。"
    : "先讓一台選手電腦打開此網址，再讓其他電腦加入。選手不需要安裝程式。");
  $("network-ai").textContent = network.ai_ready ? "AI 已就緒" : "AI 未就緒，請先排除原因";
  $("network-ai").className = "badge" + (network.ai_ready ? "" : " warn");
  $("network-data").textContent = `賽事資料：${network.data_dir}；備份位置：${network.backup_dir}`;
  updateParticipantUrl();
}
function updateParticipantUrl() {
  const url = $("network-address").value;
  $("participant-url").value = url;
  $("copy-participant-url").disabled = !url;
  $("print-participant-url").disabled = !url;
}
$("network-address").onchange = updateParticipantUrl;
$("network-refresh").onclick = () => safe(async () => {
  networkState = undefined;
  await refreshNetwork();
  notice("區域網路資訊已更新");
});
$("copy-participant-url").onclick = () => safe(async () => {
  const input = $("participant-url");
  if (!input.value) return;
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(input.value);
      notice("參賽網址已複製");
      return;
    } catch { /* LAN HTTP or clipboard permission: use local selection below. */ }
  }
  input.focus();
  input.select();
  let copied = false;
  try { copied = document.execCommand("copy"); } catch { /* manual copy remains available */ }
  notice(copied ? "參賽網址已複製" : "網址已選取，請按 Ctrl+C 複製");
});
$("print-participant-url").onclick = () => {
  const url = $("participant-url").value;
  if (!url) return;
  $("print-network").replaceChildren(
    el("h1", "楊海分站賽 · 選手入口"),
    el("p", "連到場地區域網路，在瀏覽器輸入："),
    el("p", url, "printed-url"),
    el("p", "進入自己的對戰桌，再輸入主辦方發給你的操作碼。選手電腦不需安裝任何比賽程式。"),
  );
  document.body.dataset.printMode = "lan";
  window.print();
};
function loadSettings() {
  if (!adminState) return;
  const f = $("settings-form");
  for (const [key, value] of Object.entries(adminState.settings)) {
    const input = f.elements.namedItem(key);
    if (!input) continue;
    if (input.type === "checkbox") input.checked = value;
    else input.value = key === "threshold" ? value * 100 : value;
  }
  for (const input of f.elements) input.disabled = adminState.games.length > 0;
}
$("settings-form").onsubmit = (event) => {
  event.preventDefault();
  safe(async () => {
    const f = event.target.elements;
    await api("/api/admin/settings", {
      method: "POST",
      token: adminToken,
      body: {
        board_size: Number(f.board_size.value),
        komi: Number(f.komi.value),
        rules: f.rules.value,
        threshold: Number(f.threshold.value) / 100,
        visits: Number(f.visits.value),
        allow_pass: f.allow_pass.checked,
        rules_confirmed: f.rules_confirmed.checked,
      },
    });
    await refreshAdmin();
    notice("規則已儲存");
  });
};
$("team-form").onsubmit = (event) => {
  event.preventDefault();
  safe(async () => {
    const f = event.target.elements,
      ident = f.id.value;
    const body = {
      name: f.name.value,
      players: [f.player1.value, f.player2.value],
      mars_points: [Number(f.points1.value), Number(f.points2.value)],
      eligibility_checked: f.eligibility_checked.checked,
      checked_in: f.checked_in.checked,
    };
    await api("/api/admin/teams" + (ident ? "/" + ident : ""), {
      method: ident ? "PUT" : "POST",
      token: adminToken,
      body,
    });
    event.target.reset();
    f.id.value = "";
    await refreshAdmin();
    notice("隊伍已儲存");
  });
};
$("team-clear").onclick = () => {
  $("team-form").reset();
  $("team-form").elements.id.value = "";
};
function editTeam(t) {
  const f = $("team-form").elements;
  f.id.value = t.id;
  f.name.value = t.name;
  f.player1.value = t.players[0];
  f.player2.value = t.players[1];
  f.points1.value = t.mars_points[0];
  f.points2.value = t.mars_points[1];
  f.checked_in.checked = t.checked_in;
  f.eligibility_checked.checked = t.eligibility_checked;
  $("team-form").scrollIntoView({ behavior: "smooth" });
}
function renderAdmin() {
  const s = adminState;
  $("team-count").textContent =
    `${s.teams.length}/32 隊 · ${s.teams.filter((t) => t.checked_in).length} 隊已檢錄`;
  $("roster").replaceChildren(
    table(
      ["序", "隊伍", "隊長／隊員", "檢錄", "操作", "點棋碼"],
      s.teams
        .slice()
        .sort((a, b) => a.order - b.order)
        .map((t) => [
          t.order,
          t.name,
          t.players.join(" / "),
          t.checked_in ? "已檢錄" : "未檢錄",
          button("編輯", () => editTeam(t), "secondary"),
          teamCodeActions(t),
        ]),
    ),
  );
  $("referee-games").replaceChildren();
  const round = s.rounds.at(-1);
  for (const g of s.games.filter((g) => g.round_id === round?.id)) {
    const card = el("article", undefined, "panel"),
      actions = el("div", undefined, "actions");
    card.append(
      el("h2", `第 ${g.table} 桌`),
      el("p", `${g.seats.B.name} vs ${g.seats.W.name}`),
      el("span", labels[g.status], "badge"),
    );
    if (g.status === "ready")
      actions.append(button("開賽", () => referee(g, "start")));
    if (["playing", "analyzing"].includes(g.status))
      actions.append(button("暫停", () => referee(g, "pause"), "secondary"));
    if (g.status === "paused")
      actions.append(
        button("恢復", () => referee(g, "resume")),
        button("重賽", () => referee(g, "rematch"), "danger"),
      );
    if (g.status !== "finished")
      actions.append(
        button("裁定結果", () => referee(g, "adjudicate"), "secondary"),
      );
    card.append(
      actions,
      button("檢視棋局", () => openGame(g.id), "secondary"),
    );
    $("referee-games").append(card);
  }
  $("audit").replaceChildren(
    ...s.events
      .slice(-15)
      .reverse()
      .map((e) =>
        el(
          "div",
          `${new Date(e.time * 1000).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })} · ${e.kind} · ${JSON.stringify(e.detail)}`,
          "audit-row",
        ),
      ),
  );
}
function teamCodeActions(team) {
  const actions = el("div", undefined, "actions");
  actions.append(button("查看點棋碼", () => { prompt(`${team.name}：本隊固定點棋碼（整場沿用）`, team.play_code); }, "secondary"));
  actions.append(button("重新制發", async () => {
    const reason = prompt("重新制發會使本隊舊點棋碼失效，請填寫原因：");
    if (!reason?.trim()) return;
    await api(`/api/admin/teams/${team.id}/code`, { method: "POST", token: adminToken, body: { reason } });
    await refreshAdmin(); notice("已重新制發點棋碼，請將新碼交給該隊指定點棋手");
  }, "secondary"));
  return actions;
}
async function referee(g, action) {
  let reason = "",
    winner = null,
    points = 1;
  if (["pause", "rematch", "adjudicate"].includes(action)) {
    reason = prompt("請填寫裁判處理原因（將記錄在稽核紀錄）");
    if (!reason) return;
  }
  if (
    action === "start" &&
    !confirm(
      `確認第 ${g.table} 桌開賽？\n黑方 ${g.seats.B.own_device ? 5 : 10} 分鐘；白方 ${g.seats.W.own_device ? 5 : 10} 分鐘。`,
    )
  )
    return;
  if (action === "rematch" && !confirm("確定重賽？原棋譜會保留在裁判紀錄中。"))
    return;
  if (action === "adjudicate") {
    const color = prompt(
      `勝方請輸入 B 或 W：\nB：${g.seats.B.name}\nW：${g.seats.W.name}`,
    )?.toUpperCase();
    if (!["B", "W"].includes(color)) return;
    winner = g.seats[color].team;
    points = Number(prompt("勝方積分：輸入 1 或 1.25（需依已公告規則）", "1"));
  }
  await api(`/api/admin/games/${g.id}/action`, {
    method: "POST",
    token: adminToken,
    body: { action, reason, winner, points },
  });
  await refreshAdmin();
}
for (const kind of ["swiss", "playoff"])
  $(kind).onclick = () =>
    safe(async () => {
      if (!confirm("確定建立下一輪？規則及本輪配對建立後將鎖定。")) return;
      await api("/api/admin/rounds/" + kind, {
        method: "POST",
        token: adminToken,
      });
      await refreshAdmin();
      await poll();
      loadSettings();
    });
$("backup").onclick = () =>
  safe(async () => {
    const result = await api("/api/admin/backup", {
      method: "POST",
      token: adminToken,
    });
    notice("備份已儲存於主機資料目錄的 backups 資料夾：" + result.filename);
  });
$("export").onclick = () =>
  safe(async () => {
    const r = await fetch("/api/admin/export", {
      headers: { Authorization: "Bearer " + adminToken },
    });
    if (!r.ok) throw new Error("匯出失敗，請重新登入");
    const url = URL.createObjectURL(await r.blob()),
      a = el("a");
    a.href = url;
    a.download = "tournament-export.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
$("seats").onclick = () =>
  safe(async () => {
    await refreshAdmin();
    const last = adminState.rounds.at(-1);
    if (!last) throw new Error("請先建立本輪賽程");
    $("print-seats").replaceChildren(el("h1", "楊海分站賽 · 點棋手操作碼"));
    for (const g of adminState.games.filter((g) => g.round_id === last.id))
      for (const color of ["B", "W"]) {
        const slip = el("article", undefined, "seat-slip");
        slip.append(
          el(
            "h2",
            `第 ${g.table} 桌 · ${color === "B" ? "黑" : "白"}方 · ${g.seats[color].name}`,
          ),
          el("p", `${stages[g.stage]} 第 ${g.round} 輪`),
          el("code", g.seat_tokens[color]),
          el("p", "在賽場選擇對戰桌，輸入操作碼；請只交給指定點棋手。"),
        );
        $("print-seats").append(slip);
      }
    document.body.dataset.printMode = "seats";
    window.print();
  });
$("team-codes").onclick = () => safe(async () => {
  await refreshAdmin();
  if (!adminState.teams.length) throw Error("請先建立隊伍，即可賽前制發點棋碼");
  $("print-seats").replaceChildren(el("h1", "楊海分站賽 · 隊伍固定點棋碼"));
  for (const team of adminState.teams.slice().sort((a, b) => a.order-b.order)) {
    const card = el("article", undefined, "seat-slip");
    card.append(el("h2", team.name), el("code", team.play_code), el("p", "整場沿用此碼。每輪進入本隊對戰桌輸入，系統會辨識棋色；只交給本隊指定點棋手。"));
    $("print-seats").append(card);
  }
  document.body.dataset.printMode = "seats"; window.print();
});
// Atomic roster import: import in original registration order, then check in each
// team after the referee has inspected eligibility documents.
const importPanel = el("article", undefined, "panel"),
  importForm = el("form"),
  csvBox = el("textarea");
csvBox.id = "roster-import";
csvBox.rows = 12;
csvBox.placeholder =
  "隊長姓名\t隊員姓名\t隊名\t點棋碼\n王小明\t陳小華\t探索隊\t\n\n也支援原有英文欄位 CSV。點棋碼可留白，由本機自動制發。";
csvBox.required = true;
csvBox.setAttribute("aria-label", "貼上報名名單與預製點棋碼");
const importButton = el("button", "匯入名單與點棋碼");
importButton.type = "submit";
importForm.append(csvBox, importButton);
importPanel.append(
  el("h2", "批次匯入報名名單"),
  el(
    "p",
    "直接貼上含標題的 Excel／試算表資料，或英文欄位 CSV，最多 32 隊。依貼上順序建立報名順位；點棋碼可使用預製碼，留白則自動產生。重複匯入會整批拒絕，不會覆寫原有隊伍與點棋碼。匯入後仍需補齊姓名、確認資格並逐隊檢錄。",
  ),
  importForm,
);
$("admin-content").insertBefore(importPanel, $("referee-games"));
importForm.onsubmit = (event) => {
  event.preventDefault();
  safe(async () => {
    const result = await api("/api/admin/teams/import", {
      method: "POST",
      token: adminToken,
      body: { csv: csvBox.value },
    });
    csvBox.value = "";
    await refreshAdmin();
    notice(`已匯入 ${result.imported} 隊與固定點棋碼；可先列印發放，檢錄前請確認姓名與資格`);
  });
};
$("backup").parentElement.append(
  button(
    "全場暫停",
    async () => {
      const reason = prompt("請填寫全場暫停原因");
      if (!reason) return;
      const result = await api("/api/admin/pause-all", {
        method: "POST",
        token: adminToken,
        body: { action: "pause", reason },
      });
      await refreshAdmin();
      notice(`已暫停 ${result.paused} 桌，恢復時請逐桌確認`);
    },
    "danger",
  ),
);
let polling = false,
  pollCount = 0;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    snapshot = await api("/api/state");
    renderArena();
    if (selectedGame) await refreshGame();
    if (currentView === "records") await refreshRecords();
    if (currentView === "admin" && adminToken && pollCount % 5 === 0)
      await refreshAdmin();
    if (pollCount % 10 === 0) {
      const h = await api("/api/health");
      $("connection").textContent = h.ai_ready
        ? "已連線 · AI 就緒"
        : "已連線 · AI 未就緒";
      $("connection").className = "badge" + (h.ai_ready ? "" : " warn");
    }
    pollCount++;
  } catch (error) {
    $("connection").textContent = "連線中斷 · 計時以伺服器為準";
    $("connection").className = "badge error";
  } finally {
    polling = false;
  }
}
safe(async () => {
  await poll();
  const hash = location.hash.slice(1);
  if (hash.startsWith("game/")) await openGame(hash.slice(5));
  else if (hash.startsWith("team/")) await openTeam(hash.slice(5));
  else if (["arena", "standings", "records", "admin", "guide"].includes(hash))
    showView(hash);
});
setInterval(poll, 1000);
setInterval(renderClocks, 200);
