"use strict";

// Acknowledgement is local presentation state; dice and winners stay server-owned.
class DiceStage {
  constructor(onChange) {
    this.element = document.getElementById("dice-stage");
    this.onChange = onChange;
    this.acknowledged = new Map();
    this.presentation = null;
    this.timer = null;
    this.scope = "";
    this.reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.duration = this.reducedMotion ? 200 : 2400;
  }

  node(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  useScope(game, team) {
    const scope = `${game.id}:${game.pregame.id}:${this.shared ? "same-device" : team}`;
    if (this.scope !== scope) {
      this.clear();
      this.scope = scope;
    }
    if (!this.acknowledged.has(scope)) {
      let saved = [];
      try { saved = JSON.parse(sessionStorage.getItem("dice-confirmed:" + scope) || "[]"); } catch {}
      this.acknowledged.set(scope, new Set(Array.isArray(saved) ? saved : []));
    }
  }

  key(attempt, team, values) { return `${attempt}:${team}:${values.join(",")}`; }
  confirmed(attempt, team, values) {
    return !!values && !!this.acknowledged.get(this.scope)?.has(this.key(attempt, team, values));
  }

  rounds(game) {
    const rounds = new Map((game.pregame.history || []).map(round => [round.attempt, round]));
    const p = game.pregame;
    if (Object.keys(p.rolls || {}).length) rounds.set(p.attempt, { attempt: p.attempt, rolls: p.rolls });
    return [...rounds.values()].sort((a, b) => a.attempt - b.attempt);
  }

  start(game, round, awaitingTeam = null) {
    this.clear();
    this.presentation = {
      attempt: round.attempt, rolls: { ...round.rolls }, awaitingTeam,
      teams: Object.values(game.seats).map(seat => ({ id: seat.team, name: seat.name })),
      rolling: new Set(Object.keys(round.rolls).filter(team => !this.confirmed(round.attempt, team, round.rolls[team]))),
      phase: "rolling", started: performance.now(),
    };
    if (awaitingTeam) this.presentation.rolling.add(awaitingTeam);
    this.paint();
    this.element.scrollIntoView({ block: "center", behavior: this.reducedMotion ? "instant" : "smooth" });
    this.element.focus({ preventScroll: true });
    this.schedule();
  }

  begin(game, team) {
    this.useScope(game, team);
    this.start(game, { attempt: game.pregame.attempt, rolls: game.pregame.rolls }, team);
  }

  update(game, team) {
    if (!team || !game?.pregame || game.status !== "ready") {
      this.clear();
      return false;
    }
    this.useScope(game, team);
    const rounds = this.rounds(game);
    if (this.presentation) {
      const current = this.presentation, round = rounds.find(round => round.attempt === current.attempt);
      if (round) {
        const added = Object.keys(round.rolls).filter(id => !current.rolls[id]);
        if (added.length) {
          current.rolls = { ...round.rolls };
          const newlyAnimating = added.filter(id => !current.rolling.has(id) && !this.confirmed(round.attempt, id, round.rolls[id]));
          if (newlyAnimating.length) {
            current.rolling = new Set([...current.rolling, ...newlyAnimating]);
            current.phase = "rolling";
            current.started = performance.now();
          }
          if (current.rolls[current.awaitingTeam]) current.awaitingTeam = null;
          this.paint();
          this.schedule();
        }
      }
    } else {
      const pending = rounds.find(round => Object.entries(round.rolls).some(([id, values]) => !this.confirmed(round.attempt, id, values)));
      if (pending) this.start(game, pending);
    }
    return !!this.presentation;
  }

  schedule() {
    clearTimeout(this.timer);
    const current = this.presentation;
    if (!current || current.awaitingTeam || current.phase === "result") return;
    this.timer = setTimeout(() => {
      if (this.presentation !== current) return;
      current.phase = "result";
      current.rolling.clear();
      this.paint();
      document.getElementById("dice-confirm")?.focus({ preventScroll: true });
      this.onChange();
    }, Math.max(0, this.duration - (performance.now() - current.started)));
  }

  die(value, rolling, index) {
    const flight = this.node("div", undefined, "die-flight" + (rolling ? " is-rolling" : ""));
    flight.dataset.index = index;
    if (!value && !rolling) {
      flight.append(this.node("div", "?", "die-waiting"));
      return flight;
    }
    const cube = this.node("div", undefined, "die-cube");
    cube.dataset.value = value || 1;
    cube.setAttribute("aria-hidden", "true");
    const positions = [[], [5], [1, 9], [1, 5, 9], [1, 3, 7, 9], [1, 3, 5, 7, 9], [1, 3, 4, 6, 7, 9]];
    for (let face = 1; face <= 6; face++) {
      const side = this.node("div", undefined, "die-face face-" + face);
      for (const position of positions[face]) side.append(this.node("span", undefined, "die-pip pip-" + position));
      cube.append(side);
    }
    flight.append(cube);
    return flight;
  }

  paint() {
    const current = this.presentation;
    if (!current) return;
    this.element.classList.remove("hidden");
    this.element.dataset.phase = current.phase;
    const card = this.node("div", undefined, "dice-stage-card");
    card.append(this.node("p", `GUESS FIRST · 第 ${current.attempt} 次`, "dice-stage-kicker"));
    const title = this.node("h2", current.phase === "rolling" ? "好運，正在轉動…" : "骰子落定！");
    title.id = "dice-stage-title";
    card.append(title);
    const teams = this.node("div", undefined, "dice-stage-teams");
    for (const team of current.teams) {
      const section = this.node("div", undefined, "dice-stage-team"), values = current.rolls[team.id];
      const rolling = current.phase === "rolling" && current.rolling.has(team.id);
      section.dataset.team = team.id;
      section.append(this.node("strong", team.name, "dice-stage-name"));
      const pair = this.node("div", undefined, "dice-pair");
      pair.append(this.die(values?.[0], rolling, "0"), this.die(values?.[1], rolling, "1"));
      section.append(pair, this.node("p", rolling ? "擲骰中…" : values ? `${values[0]} + ${values[1]} = ${values[0] + values[1]} 點` : "尚未擲骰", "dice-stage-total"));
      teams.append(section);
    }
    card.append(teams);
    const summary = this.node("p", "", "dice-stage-summary");
    summary.setAttribute("aria-live", "polite");
    const rolls = current.teams.map(team => current.rolls[team.id]);
    if (current.phase === "rolling") summary.textContent = "兩顆骰子，一起揭曉。";
    else if (rolls.every(Boolean)) {
      const sums = rolls.map(values => values[0] + values[1]);
      summary.textContent = sums[0] === sums[1] ? "同點！確認結果後，再擲一次。" : `${current.teams[sums[0] > sums[1] ? 0 : 1].name} 點數較高，可選擇黑白。`;
    } else summary.textContent = "已揭曉一隊的點數，確認後繼續猜先。";
    const confirm = this.node("button", "確定", "dice-confirm");
    confirm.id = "dice-confirm";
    confirm.type = "button";
    confirm.disabled = current.phase !== "result";
    confirm.onclick = () => this.confirm();
    card.append(summary, confirm);
    this.element.replaceChildren(card);
  }

  confirm() {
    const current = this.presentation;
    if (!current || current.phase !== "result") return;
    const acknowledged = this.acknowledged.get(this.scope);
    for (const [team, values] of Object.entries(current.rolls)) acknowledged.add(this.key(current.attempt, team, values));
    sessionStorage.setItem("dice-confirmed:" + this.scope, JSON.stringify([...acknowledged]));
    this.clear();
    this.onChange();
    if (!this.presentation) document.querySelector("#pregame-panel button:not(:disabled)")?.focus({ preventScroll: true });
  }

  clear() {
    clearTimeout(this.timer);
    this.presentation = null;
    this.element.classList.add("hidden");
    this.element.replaceChildren();
  }
}
