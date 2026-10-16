"use strict";
// Uses the cloud image's Playwright + Chromium. No credentials are logged.
const { chromium } = require("playwright");
const fs = require("fs"),
  path = require("path"),
  os = require("os");
const { spawn } = require("child_process");
const root = path.resolve(__dirname, ".."),
  data = fs.mkdtempSync(path.join(os.tmpdir(), "yanghai-browser-"));
const port = 8767,
  initialBase = `http://127.0.0.1:${port}`;
let base = initialBase;
const env = {
  ...process.env,
  HOST: "0.0.0.0",
  TOURNAMENT_DATA: data,
  KATAGO_BIN: path.join(root, ".runtime/katago/squashfs-root/AppRun"),
  KATAGO_MODEL: path.join(root, ".runtime/model.txt.gz"),
  KATAGO_CONFIG: path.join(root, ".runtime/analysis.cfg"),
};
const server = spawn(
  path.join(root, ".venv/bin/python"),
  [
    "-m",
    "uvicorn",
    "server.app:create_app",
    "--factory",
    "--host",
    "0.0.0.0",
    "--port",
    String(port),
    "--workers",
    "1",
    "--no-access-log",
  ],
  { cwd: root, env, stdio: ["ignore", "ignore", "pipe"] },
);
let serverLog = "",
  browser, practiceServer;
server.stderr.on("data", (chunk) => (serverLog += chunk.toString()));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function assert(value, message) {
  if (!value) throw new Error(message);
}
async function get(endpoint, token) {
  const r = await fetch(base + endpoint, {
    headers: token ? { Authorization: "Bearer " + token } : {},
  });
  assert(r.ok, "API read failed");
  return r.json();
}
(async () => {
  for (let i = 0; i < 100; i++) {
    try {
      const h = await get("/api/health");
      assert(h.ai_ready, "AI not ready");
      break;
    } catch (e) {
      if (i === 99) throw e;
      await sleep(250);
    }
  }
  const adminToken = fs
    .readFileSync(path.join(data, "admin-token"), "utf8")
    .trim();
  browser = await chromium.launch({
    executablePath: "/usr/bin/chromium",
    headless: true,
    args: ["--no-sandbox", "--no-proxy-server"],
  });
  const context = await browser.newContext({
      viewport: { width: 1440, height: 1050 },
    }),
    admin = await context.newPage(),
    errors = [];
  admin.on("pageerror", (e) => errors.push(e.message));
  admin.on("dialog", (dialog) =>
    dialog.accept(dialog.type() === "prompt" ? "瀏覽器操作測試" : ""),
  );
  await admin.addInitScript(() => { window.print = () => {}; });
  await admin.goto(base + "/#admin");
  await admin.locator("#admin-token").fill(adminToken);
  await admin.locator("#login button[type=submit]").click();
  await admin.locator("#admin-content").waitFor({ state: "visible" });
  await admin.locator("#network-ai").filter({ hasText: "CPU" }).waitFor();
  assert((await admin.locator("#engine-details").innerText()).includes("目前使用 CPU"), "Admin does not identify the real CPU backend");
  const network = await get("/api/admin/network", adminToken);
  assert(network.lan_enabled && network.interfaces.length, "LAN addresses unavailable");
  base = new URL(network.interfaces[0].url).origin;
  await admin.goto(base + "/#admin");
  await admin.locator("#admin-token").fill(adminToken);
  await admin.locator("#login button[type=submit]").click();
  await admin.locator("#participant-url").filter({ visible: true }).waitFor();
  assert((await admin.locator("#participant-url").inputValue()) === network.interfaces[0].url, "share URL wrong");
  await admin.locator("#copy-participant-url").click();
  await admin.locator("#notice").filter({ hasText: /已複製|Ctrl\+C/ }).waitFor();
  await admin.locator("#network-refresh").click();
  await admin.locator("#notice").filter({ hasText: "資訊已更新" }).waitFor();
  await admin.locator("#network-panel").screenshot({ path: path.join(root, ".runtime/lan-admin.png") });
  await admin.locator('#settings-form [name="rules_confirmed"]').check();
  await admin.locator("#settings-form button[type=submit]").click();
  await admin.locator("#notice").filter({ hasText: "規則已儲存" }).waitFor();
  await admin.locator("#roster-import").fill("隊長姓名\t隊員姓名\t隊名\n隊長0\t隊員0\t探索隊\n隊長1\t隊員1\t星際隊");
  await admin.getByRole("button", {name: "匯入名單與點棋碼", exact: true}).click();
  await admin.locator("#notice").filter({hasText: "已匯入 2 隊"}).waitFor();
  assert((await admin.locator("#roster-import").inputValue()) === "", "private roster remains in import field");
  const issuedTeams = (await get("/api/admin/state", adminToken)).teams;
  assert(issuedTeams.every(t => t.play_code), "team codes not generated before pairing");
  assert(issuedTeams.every(t => t.checked_in && t.eligibility_checked), "teams not automatically ready");
  await admin.locator("#team-codes").click();
  await admin.waitForFunction(() => document.body.dataset.printMode === "seats");
  const issuedText = await admin.locator("#print-seats").textContent();
  assert(issuedTeams.every(t => issuedText.includes(t.play_code)), "preissued code cards missing");
  await admin.locator("#swiss").click();
  await admin.locator("#referee-games .panel").waitFor();
  const state = await get("/api/admin/state", adminToken),
    game = state.games[0];
  for (const color of ["B", "W"]) assert(game.seat_tokens[color] === issuedTeams.find(t => t.id === game.seats[color].team).play_code, "round changed team code");
  await admin.locator("#seats").click();
  await admin.waitForFunction(() => document.body.dataset.printMode === "seats");
  await admin.locator("#print-participant-url").click();
  await admin.emulateMedia({ media: "print" });
  const printed = await admin.locator("body").innerText();
  assert(printed.includes(network.interfaces[0].url) && !printed.includes(game.seat_tokens.B) && !printed.includes(game.seat_tokens.W), "LAN poster exposes operation codes");
  assert(await admin.locator("#print-seats").isHidden(), "seat sheet visible on LAN poster");
  await admin.pdf({ path: path.join(root, ".runtime/lan-participant-poster.pdf"), format: "A4" });
  await admin.emulateMedia({ media: "screen" });
  const playerContext = await browser.newContext({
      viewport: { width: 1280, height: 1000 },
    }),
    black = await playerContext.newPage();
  black.on("pageerror", (e) => errors.push(e.message));
  await black.goto(base + "/#game/" + game.id);
  await black.locator("#seat-token").fill(game.seat_tokens.B);
  await black.locator("#seat-form button[type=submit]").click();
  await black.locator("#seat-status").filter({ hasText: "你是 黑" }).waitFor();
  await black.locator("#own-device").check();
  await black.locator("#save-device").click();
  await black
    .locator("#notice")
    .filter({ hasText: "設備選項已儲存" })
    .waitFor();
  await sleep(1300);
  await admin.locator("#start-swiss").click();
  async function playerPost(endpoint, token, payload) {
    const r = await fetch(base + endpoint, { method: "POST", headers: {
      Authorization: "Bearer " + token, "Content-Type": "application/json"
    }, body: JSON.stringify(payload) });
    assert(r.ok, "Pregame action failed: " + endpoint);
    return r.json();
  }
  const credentials = Object.fromEntries(["B", "W"].map(color => [game.seats[color].team, game.seat_tokens[color]]));
  let pregame;
  for (let i=0;i<50;i++) {
    pregame = (await get("/api/games/" + game.id)).pregame;
    for (const [team, token] of Object.entries(credentials)) {
      if (!pregame.rolls[team]) await playerPost(`/api/games/${game.id}/dice`, token, { attempt: pregame.attempt });
    }
    pregame = (await get("/api/games/" + game.id)).pregame;
    if (pregame.winner) break;
    if (i===49) throw Error("Dice did not produce non-tied result");
  }
  await playerPost(`/api/games/${game.id}/color`, credentials[pregame.winner], {
    attempt: pregame.attempt, color: pregame.winner === game.seats.B.team ? "B" : "W"
  });
  for (const token of Object.values(credentials)) await playerPost(`/api/games/${game.id}/ready`, token, { attempt: pregame.attempt, ready: true });
  await black.locator("#match-status").filter({ hasText: "對弈中" }).waitFor();
  const publicGame = await get("/api/games/" + game.id);
  assert(
    publicGame.remaining.B <= 300 && publicGame.remaining.B > 290,
    "5 minute clock not applied",
  );
  await black.locator("#board").scrollIntoViewIfNeeded();
  const rectangle = await black.locator("#board").boundingBox(),
    letters = "ABCDEFGHJKLMNOPQRST";
  async function clickMove(move) {
    const x = letters.indexOf(move[0]),
      y = 19 - Number(move.slice(1)),
      step = 660 / 18;
    await black.mouse.click(
      rectangle.x + ((40 + x * step) * rectangle.width) / 740,
      rectangle.y + ((40 + y * step) * rectangle.height) / 740,
    );
  }
  await clickMove(publicGame.forbidden);
  await black.locator("#notice").filter({ hasText: "禁止選擇" }).waitFor();
  assert(
    await black.locator("#submit-move").isDisabled(),
    "forbidden choice enabled submission",
  );
  await clickMove(publicGame.candidates[1].move);
  await black.locator("#submit-move").click();
  for (let i = 0; i < 100; i++) {
    const g = await get("/api/games/" + game.id);
    if (g.moves.length === 1 && g.status === "playing") break;
    if (i === 99) throw new Error("Move did not complete");
    await sleep(100);
  }
  await black.screenshot({
    path: path.join(root, ".runtime/match-desktop.png"),
    fullPage: true,
  });
  await black.setViewportSize({ width: 390, height: 844 });
  await black.screenshot({
    path: path.join(root, ".runtime/match-mobile.png"),
    fullPage: true,
  });
  assert(
    await black.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 2,
    ),
    "mobile horizontal overflow",
  );
  await sleep(1300);
  await admin.getByRole("button", { name: "暫停", exact: true }).click();
  await admin
    .locator("#referee-games .badge")
    .filter({ hasText: "裁判暫停" })
    .waitFor();
  await admin.getByRole("button", { name: "恢復", exact: true }).click();
  await admin
    .locator("#referee-games .badge")
    .filter({ hasText: "對弈中" })
    .waitFor();
  await admin.locator("#backup").click();
  await admin
    .locator("#notice")
    .filter({ hasText: "備份已儲存於主機" })
    .waitFor();
  await black.getByRole("button", { name: "隊伍紀錄", exact: true }).click();
  await black.locator("#team-code").fill(game.seat_tokens.B);
  await black.locator("#team-entry button[type=submit]").click();
  await black.locator("#team-records").filter({ hasText: "瑞士制下一輪對手" }).waitFor();
  await black.getByRole("button", { name: "參賽說明", exact: true }).click();
  const guide = await black.locator("#guide").innerText();
  assert(!guide.includes("GPU") && !guide.includes("資料庫") && !guide.includes("裁判與主辦方"), "guide exposes organizer operations");
  const decided = await fetch(base + `/api/admin/games/${game.id}/action`, { method: "POST", headers: { Authorization: "Bearer " + adminToken, "Content-Type": "application/json" }, body: JSON.stringify({ action: "adjudicate", winner: game.seats.B.team, points: 1 }) });
  assert(decided.ok, "demo adjudication failed");
  await black.getByRole("button", { name: "隊伍紀錄", exact: true }).click();
  await black.locator("#team-records").filter({ hasText: "勝 · 裁判裁定" }).waitFor();
  await black.locator("#team-records a").filter({ hasText: "SGF" }).waitFor();
  await black.screenshot({ path: path.join(root, ".runtime/team-records.png"), fullPage: true });
  const practiceConfig = path.join(data, "practice-config.json");
  fs.writeFileSync(practiceConfig, JSON.stringify({ host: "0.0.0.0", port: 8771,
    data_dir: path.join(data, "practice-formal-marker"), open_browser: false,
    katago_bin: env.KATAGO_BIN, katago_model: env.KATAGO_MODEL, katago_config: env.KATAGO_CONFIG }));
  practiceServer = spawn(path.join(root, ".venv/bin/python"), [path.join(root, "launcher.py"), "--config", practiceConfig, "--practice", "--port", "8771", "--no-browser", "--no-pause"], { cwd: root, env, stdio: ["ignore", "ignore", "pipe"] });
  practiceServer.stderr.on("data", chunk => serverLog += chunk.toString());
  const practiceBase = "http://127.0.0.1:8771";
  for (let i=0;i<120;i++) { try { const h=await fetch(practiceBase+"/api/health"); if(h.ok&&(await h.json()).ai_ready)break; } catch{} if(i===119)throw Error("Practice server not ready");await sleep(250); }
  const practicePage = await (await browser.newContext({ viewport: { width: 1440, height: 1100 } })).newPage();
  practicePage.on("pageerror", e=>errors.push(e.message));
  await practicePage.goto(practiceBase);
  await practicePage.locator("#practice-settings button[type=submit]").click();
  await practicePage.locator("#practice-status").filter({ hasText: "輪到 黑" }).waitFor();
  async function practiceGame() { return (await (await fetch(practiceBase+"/api/practice/state")).json()).game; }
  async function practiceClick(move) { const b=practicePage.locator("#practice-board");await b.scrollIntoViewIfNeeded();const r=await b.boundingBox(),x=letters.indexOf(move[0]),y=19-Number(move.slice(1));await practicePage.mouse.click(r.x+(40+x*660/18)*r.width/740,r.y+(40+y*660/18)*r.height/740); }
  let training = await practiceGame();
  await practiceClick(training.forbidden);
  await practicePage.locator("#practice-notice").filter({ hasText: "禁止選擇" }).waitFor();
  assert(await practicePage.locator("#practice-submit").isDisabled(), "practice accepted forbidden click");
  for (const expected of ["白", "黑"]) {
    training = await practiceGame();await practiceClick(training.candidates[1].move);await practicePage.locator("#practice-submit").click();
    await practicePage.locator("#practice-status").filter({ hasText: "輪到 "+expected }).waitFor();
  }
  await practicePage.locator("#practice-undo").click();
  await practicePage.locator("#practice-status").filter({ hasText: "輪到 白" }).waitFor();
  assert((await practiceGame()).moves.length===1, "practice undo wrong");
  assert((await (await fetch(practiceBase+"/api/practice/sgf")).text()).includes("KM[7.5]"), "practice SGF komi missing");
  await practicePage.screenshot({ path: path.join(root, ".runtime/practice-desktop.png"), fullPage: true });
  await practicePage.locator("#practice-finish").click();await practicePage.locator("#practice-status").filter({ hasText: "已結束" }).waitFor();
  assert(await practicePage.locator("#practice-submit").isDisabled(), "practice finished but enabled move");
  assert(!await practicePage.getByRole("button",{name:"主辦管理",exact:true}).count(), "practice exposes admin UI");
  assert(errors.length === 0, "Browser errors: " + errors.join(","));
  console.log(
    JSON.stringify(
      {
        passed: true,
        checks: [
          "Chinese spreadsheet roster import and clearing private input",
          "admin login",
          "rule settings",
          "automatic registration eligibility and check-in",
          "bulk round start, dice, color selection and both ready",
          "pairing",
          "player authentication",
          "5-minute device selection",
          "forbidden click",
          "legal board move",
          "next-side analysis",
          "referee pause/resume",
          "backup",
          "mobile layout",
          "LAN private-interface browser access",
          "participant URL discovery/copy/refresh",
          "LAN HTTP clipboard fallback",
          "printed participant URL excludes seat codes",
          "preissued team codes printed before pairing",
          "fixed team code seat authentication",
          "team current/history records and Swiss waiting message",
          "history SGF download link",
          "participant-only guide",
          "same-PC two-person practice with real AI",
          "practice forbidden choice and alternating browser moves",
          "practice undo and SGF with 7.5 komi",
          "practice finish and participant-only UI",
        ],
        browser_errors: errors.length,
      },
      null,
      2,
    ),
  );
})()
  .catch((error) => {
    console.error(error.message);
    console.error(serverLog.slice(-2000));
    process.exitCode = 1;
  })
  .finally(async () => {
    if (browser) await browser.close();
    if (practiceServer) { practiceServer.kill("SIGTERM");await new Promise(resolve=>{if(practiceServer.exitCode!==null)resolve();else practiceServer.once("exit",resolve);}); }
    server.kill("SIGTERM");
    await new Promise((resolve) => {
      if (server.exitCode !== null) resolve();
      else server.once("exit", resolve);
    });
    fs.rmSync(data, { recursive: true, force: true });
  });
