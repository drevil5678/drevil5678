"use strict";
const l$ = id => document.getElementById(id), lLetters = "ABCDEFGHJKLMNOPQRST";
const modeNames = {pvp: "區網真人", ai: "對電腦", local: "同機雙人"};
const statusNames = {waiting: "等待對手", analyzing: "AI 分析／排隊中", playing: "對弈中", error: "分析暫停", finished: "本局已結束"};
let roomId = null, roomGame = null, selected = null, busy = false, polling = false, tables = 0, aiReady = false;
let saved;
try { saved = JSON.parse(sessionStorage.getItem("practiceRoomTokens") || "{}"); } catch { saved = {}; }
if (!saved || typeof saved !== "object" || Array.isArray(saved)) saved = {};
function token() { return roomId ? saved[roomId] || "" : ""; }
function saveToken(id, value) { saved[id] = value; sessionStorage.setItem("practiceRoomTokens", JSON.stringify(saved)); }
function node(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }
function note(text) { l$("lobby-notice").textContent = text; }
async function api(path, body, method = "POST", credential = token()) {
  const options = {headers: credential ? {Authorization: "Bearer " + credential} : {}};
  if (body !== undefined) { options.method = method; options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  const response = await fetch(path, options), data = await response.json();
  if (!response.ok) { const error = Error(typeof data.detail === "string" ? data.detail : "請檢查輸入設定"); error.status = response.status; throw error; }
  return data;
}
function path(action = "") { return "/api/practice-lan/rooms/" + roomId + (action ? "/" + action : ""); }
function version() { return {version: roomGame.version}; }
function accept(data) {
  if (!data.game || data.game.id !== roomId || (roomGame?.id === roomId && data.game.version < roomGame.version)) return;
  if (roomGame?.version !== data.game.version) selected = null;
  roomGame = data.game;
  renderRoom();
}
function canPlay() { const g = roomGame; return !!g && !busy && !g.undo_pending && g.status === "playing" && ["both", g.turn].includes(g.viewer.color); }
function draw() {
  const g = roomGame, c = l$("room-board").getContext("2d"), n = g?.settings.board_size || 19, step = 660 / (n - 1);
  c.clearRect(0, 0, 740, 740); c.fillStyle = "#d5ae70"; c.fillRect(0, 0, 740, 740); c.strokeStyle = "#775a34"; c.lineWidth = 1;
  for (let i = 0; i < n; i++) {
    const v = 40 + i * step; c.beginPath(); c.moveTo(40, v); c.lineTo(700, v); c.moveTo(v, 40); c.lineTo(v, 700); c.stroke();
    c.fillStyle = "#684b28"; c.font = "12px system-ui"; c.textAlign = "center"; c.fillText(lLetters[i], v, 21); c.fillText(String(n - i), 18, v + 4);
  }
  const stars = n === 19 ? [3, 9, 15] : n === 13 ? [3, 6, 9] : [2, 4, 6];
  for (const x of stars) for (const y of stars) { c.beginPath(); c.arc(40 + x * step, 40 + y * step, 3, 0, Math.PI * 2); c.fill(); }
  if (!g) return;
  for (let i = 0; i < g.board.length; i++) {
    if (g.board[i] === ".") continue;
    const x = 40 + (i % n) * step, y = 40 + Math.floor(i / n) * step;
    c.beginPath(); c.arc(x, y, step * .44, 0, Math.PI * 2); c.fillStyle = g.board[i] === "B" ? "#18251f" : "#fafbf6"; c.fill();
  }
  g.candidates.forEach((h, i) => {
    if (h.move === "pass") return;
    const x = 40 + lLetters.indexOf(h.move[0]) * step, y = 40 + (n - Number(h.move.slice(1))) * step, forbidden = h.move === g.forbidden;
    c.beginPath(); c.arc(x, y, step * .32, 0, Math.PI * 2); c.fillStyle = forbidden ? "#b34638" : "#1c6554"; c.fill();
    c.fillStyle = "white"; c.font = "bold 13px system-ui"; c.textAlign = "center"; c.fillText(forbidden ? "×" : String(g.forbidden ? i : i + 1), x, y + 5);
  });
  if (selected && selected !== "pass") {
    c.strokeStyle = "#256cbe"; c.lineWidth = 3; c.beginPath(); c.arc(40 + lLetters.indexOf(selected[0]) * step, 40 + (n - Number(selected.slice(1))) * step, step * .43, 0, Math.PI * 2); c.stroke();
  }
}
function renderRoom() {
  const g = roomGame;
  if (!g) return;
  l$("room-title").textContent = g.name;
  l$("room-details").textContent = `${modeNames[g.mode]} · ${g.settings.board_size} 路 · 黑貼 ${g.settings.komi} 目 · ${g.settings.rules === "chinese" ? "中國" : "日本"}規則`;
  l$("room-players").textContent = `● 黑：${g.seats.B.name}　○ 白：${g.seats.W.name}`;
  l$("room-status").textContent = statusNames[g.status] + (g.status === "playing" ? ` · 輪到${g.turn === "B" ? "黑" : "白"}棋 · ${g.moves.length} 手` : "");
  if (g.result?.winner) l$("room-status").textContent = `${g.seats[g.result.winner].name} 獲勝（對手認輸）`;
  if (g.result?.reason === "double_pass") l$("room-status").textContent = "雙方停一手，本局結束";
  l$("room-role").textContent = g.viewer.color === "both" ? "同機雙人：你可操作黑白雙方" : g.viewer.color ? `你執${g.viewer.color === "B" ? "黑" : "白"}棋` : "目前為觀戰模式";
  l$("room-selection").textContent = selected ? `已選 ${selected}，請確認落子` : canPlay() ? "請選擇落點" : "";
  l$("room-submit").disabled = !canPlay() || !selected;
  l$("room-pass").disabled = !canPlay() || g.forbidden === "pass";
  l$("room-undo").disabled = !g.viewer.color || busy || !g.moves.length || !!g.undo_pending || !["playing", "finished"].includes(g.status)
    || (g.mode === "ai" && !g.moves.some(m => m[0] === g.viewer.color));
  l$("room-undo").textContent = g.mode === "pvp" ? "申請悔棋" : "悔棋";
  l$("room-finish").textContent = g.status === "waiting" ? "結束等待" : g.mode === "local" ? "結束對局" : "認輸";
  l$("room-finish").disabled = !g.viewer.color || busy || g.status === "finished";
  l$("room-retry").classList.toggle("hidden", g.status !== "error" || !g.viewer.color);
  l$("room-retry").disabled = busy;
  l$("room-close").classList.toggle("hidden", !g.viewer.owner);
  l$("room-close").disabled = busy;
  l$("room-entry").classList.toggle("hidden", !!g.viewer.color);
  l$("room-join").classList.toggle("hidden", g.mode !== "pvp" || g.status !== "waiting" || !!g.viewer.color);
  l$("room-credential").classList.toggle("hidden", !g.viewer.color);
  if (g.viewer.color) l$("room-my-token").value = token();
  l$("room-sgf").href = path("sgf");
  l$("room-hints").replaceChildren();
  if (g.error) l$("room-hints").append(node("p", g.error));
  for (const [i, h] of g.candidates.entries()) l$("room-hints").append(node("div", `${h.move === g.forbidden ? "✕ 禁止" : "建議 " + (g.forbidden ? i : i + 1)}　${h.move}　${(h.winrate * 100).toFixed(4)}%`, "hint-row" + (h.move === g.forbidden ? " forbidden" : "")));
  l$("room-undo-answer").classList.toggle("hidden", !g.undo_pending);
  const requester = g.undo_pending?.color === g.viewer.color;
  l$("room-undo-message").textContent = requester ? "已向對手申請悔棋，等待回應" : "對手申請悔棋一手，是否同意？";
  l$("room-undo-accept").classList.toggle("hidden", requester || !g.viewer.color);
  l$("room-undo-reject").classList.toggle("hidden", !g.viewer.color);
  l$("room-undo-reject").textContent = requester ? "取消申請" : "拒絕";
  l$("room-undo-accept").disabled = l$("room-undo-reject").disabled = busy;
  draw();
}
async function refreshLobby() {
  const data = await api("/api/practice-lan/rooms", undefined, "GET", "");
  tables = data.tables;
  l$("lobby-count").textContent = `${tables} / ${data.max_tables} 桌`;
  l$("lobby-create-button").disabled = busy || tables >= data.max_tables || !aiReady;
  l$("lobby-rooms").replaceChildren();
  if (!data.rooms.length) l$("lobby-rooms").append(node("p", "目前沒有桌子，開第一桌吧。"));
  for (const room of data.rooms) {
    const card = node("article", undefined, "practice-room-card"), enter = node("button", saved[room.id] ? "回到我的桌" : "入桌／觀戰", "secondary");
    card.append(node("h3", room.name), node("p", `${modeNames[room.mode]} · ${room.board_size} 路 · ${statusNames[room.status]} · ${room.moves} 手`), node("p", `黑：${room.seats.B.name} ／ 白：${room.seats.W.name}`));
    enter.onclick = () => { location.hash = "room/" + room.id; };
    card.append(enter); l$("lobby-rooms").append(card);
  }
}
async function refreshRoom() {
  const id = roomId;
  if (!id) return;
  try { const data = await api("/api/practice-lan/rooms/" + id); if (id === roomId) accept(data); }
  catch (error) {
    if (id !== roomId) return;
    if (error.status === 404) { delete saved[id]; sessionStorage.setItem("practiceRoomTokens", JSON.stringify(saved)); location.hash = ""; }
    throw error;
  }
}
async function run(action) {
  if (busy) return;
  busy = true; note(""); renderRoom(); l$("lobby-create-button").disabled = true;
  try { const data = await action(); if (data) accept(data); }
  catch (error) { note(error.message); if (roomId) { try { await refreshRoom(); } catch {} } }
  finally { busy = false; renderRoom(); if (!roomId) { try { await refreshLobby(); } catch {} } }
}
async function navigate() {
  roomId = location.hash.match(/^#room\/([a-f0-9]{16})$/)?.[1] || null;
  roomGame = null; selected = null;
  l$("lobby-view").classList.toggle("hidden", !!roomId); l$("room-view").classList.toggle("hidden", !roomId);
  try { if (roomId) await refreshRoom(); else await refreshLobby(); } catch (error) { note(error.message); }
}
l$("lobby-create").onsubmit = event => {
  event.preventDefault(); const f = event.target.elements;
  run(async () => {
    const data = await api("/api/practice-lan/rooms", {name: f.name.value, player_name: f.player_name.value,
      mode: f.mode.value, human_color: f.human_color.value, board_size: Number(f.board_size.value),
      komi: Number(f.komi.value), rules: f.rules.value, forbid_best: f.forbid_best.checked}, "POST", "");
    saveToken(data.game.id, data.token); location.hash = "room/" + data.game.id;
  });
};
l$("room-join").onsubmit = event => {
  event.preventDefault(); run(async () => { const id = roomId, data = await api(path("join"), {player_name: l$("room-join-name").value}); if (data.token) saveToken(id, data.token); return data; });
};
l$("room-recover").onsubmit = event => {
  event.preventDefault(); run(async () => { const id = roomId, value = l$("room-token").value.trim(), data = await api(path(), undefined, "GET", value); if (!data.game.viewer.color) throw Error("這不是本桌的操作碼"); saveToken(id, value); l$("room-token").value = ""; return data; });
};
l$("room-board").onclick = event => {
  if (!canPlay()) return;
  const r = event.currentTarget.getBoundingClientRect(), n = roomGame.settings.board_size, step = 660 / (n - 1),
    x = Math.round(((event.clientX - r.left) * 740 / r.width - 40) / step), y = Math.round(((event.clientY - r.top) * 740 / r.height - 40) / step);
  if (x < 0 || y < 0 || x >= n || y >= n) return;
  const chosen = lLetters[x] + (n - y);
  if (roomGame.board[y * n + x] !== ".") return note("此處已有棋子");
  if (chosen === roomGame.forbidden) return note("這是 AI 首選，此桌禁止選擇");
  selected = chosen; renderRoom();
};
l$("room-submit").onclick = () => run(() => api(path("move"), {...version(), move: selected}));
l$("room-pass").onclick = () => run(() => api(path("move"), {...version(), move: "pass"}));
for (const action of ["undo", "retry"]) l$("room-" + action).onclick = () => run(() => api(path(action), version()));
l$("room-finish").onclick = () => { if (confirm(roomGame.mode === "local" || roomGame.status === "waiting" ? "確定結束本局？" : "確定認輸並結束本局？")) run(() => api(path("finish"), version())); };
l$("room-undo-accept").onclick = () => run(() => api(path("undo-response"), {...version(), accept: true}));
l$("room-undo-reject").onclick = () => run(() => api(path("undo-response"), {...version(), accept: false}));
l$("room-back").onclick = () => { location.hash = ""; };
l$("room-close").onclick = () => {
  if (!confirm("收桌會釋放名額並清除本桌棋局，請先下載 SGF。確定收桌？")) return;
  run(async () => { const id = roomId; await api(path(), version(), "DELETE"); delete saved[id]; sessionStorage.setItem("practiceRoomTokens", JSON.stringify(saved)); location.hash = ""; });
};
l$("room-copy-token").onclick = async () => {
  try { await navigator.clipboard.writeText(token()); note("已複製你的本桌操作碼"); }
  catch { const input = l$("room-my-token"); input.type = "text"; input.focus(); input.select(); note("請按 Ctrl+C 複製，並私下保管此碼"); }
};
l$("room-my-token").onblur = event => { event.target.type = "password"; };
window.addEventListener("hashchange", navigate);
async function health() { const h = await api("/api/health", undefined, "GET", ""); aiReady = h.ai_ready; l$("lobby-ai").textContent = aiReady ? "練習 AI 已就緒；分析時請稍候，電腦會自動落子。" : "練習 AI 尚未就緒，請聯絡主辦。"; }
(async () => {
  try {
    await health();
    const data = await api("/api/practice-lan/network", undefined, "GET", "");
    for (const url of data.urls) { const line = node("p", "練習網址："), a = node("a", url); a.href = url; line.append(a); l$("practice-share-links").append(line); }
    await navigate();
  } catch (error) { note(error.message); }
})();
setInterval(async () => {
  if (polling) return; polling = true;
  try { if (roomId) await refreshRoom(); else { await health(); await refreshLobby(); } }
  catch (error) { note(error.message); }
  finally { polling = false; }
}, 1500);
