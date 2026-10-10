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
  base = `http://127.0.0.1:${port}`;
const env = {
  ...process.env,
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
    "127.0.0.1",
    "--port",
    String(port),
    "--workers",
    "1",
    "--no-access-log",
  ],
  { cwd: root, env, stdio: ["ignore", "ignore", "pipe"] },
);
let serverLog = "",
  browser;
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
    args: ["--no-sandbox"],
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
  await admin.goto(base + "/#admin");
  await admin.locator("#admin-token").fill(adminToken);
  await admin.locator("#login button[type=submit]").click();
  await admin.locator("#admin-content").waitFor({ state: "visible" });
  await admin.locator('#settings-form [name="rules_confirmed"]').check();
  await admin.locator("#settings-form button[type=submit]").click();
  await admin.locator("#notice").filter({ hasText: "規則已儲存" }).waitFor();
  for (const [index, name] of ["探索隊", "星際隊"].entries()) {
    await admin.locator('#team-form [name="name"]').fill(name);
    await admin.locator('#team-form [name="player1"]').fill("隊長" + index);
    await admin.locator('#team-form [name="player2"]').fill("隊員" + index);
    await admin.locator('#team-form [name="eligibility_checked"]').check();
    await admin.locator('#team-form [name="checked_in"]').check();
    await admin.locator("#team-form button[type=submit]").click();
    await admin.locator("#roster td").filter({ hasText: name }).waitFor();
  }
  await admin.locator("#swiss").click();
  await admin.locator("#referee-games .panel").waitFor();
  const state = await get("/api/admin/state", adminToken),
    game = state.games[0];
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
  await admin.getByRole("button", { name: "開賽", exact: true }).click();
  await black.locator("#match-status").filter({ hasText: "對弈中" }).waitFor();
  const publicGame = await get("/api/games/" + game.id);
  assert(
    publicGame.remaining.B <= 300 && publicGame.remaining.B > 290,
    "5 minute clock not applied",
  );
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
    .filter({ hasText: "已儲存於伺服器" })
    .waitFor();
  assert(errors.length === 0, "Browser errors: " + errors.join(","));
  console.log(
    JSON.stringify(
      {
        passed: true,
        checks: [
          "admin login",
          "rule settings",
          "registration and check-in",
          "pairing",
          "player authentication",
          "5-minute device selection",
          "forbidden click",
          "legal board move",
          "next-side analysis",
          "referee pause/resume",
          "backup",
          "mobile layout",
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
    server.kill("SIGTERM");
    await new Promise((resolve) => {
      if (server.exitCode !== null) resolve();
      else server.once("exit", resolve);
    });
    fs.rmSync(data, { recursive: true, force: true });
  });
