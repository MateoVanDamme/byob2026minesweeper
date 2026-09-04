/* QR Minesweeper: phone page. Opened by scanning a tile's QR code.
   Shows the tile and lets the player choose REVEAL or FLAG. */
(() => {
  const $ = (id) => document.getElementById(id);
  const m = location.pathname.match(/\/t\/(\d+)/i);
  const id = m ? parseInt(m[1], 10) : NaN;

  const big = $("big");
  const msg = $("msg");
  const actions = $("actions");
  const btnReveal = $("btn-reveal");
  const btnFlag = $("btn-flag");
  const btnReset = $("btn-reset");

  const statusLabel = { ready: "READY", playing: "LIVE", won: "CLEARED", lost: "DETONATED" };
  let coord = "";

  function show(word, text, opts = {}) {
    big.textContent = word;
    big.classList.toggle("word", !opts.number);
    msg.textContent = text;
    document.body.classList.toggle("boom", !!opts.boom);
    actions.hidden = !opts.choose;
    btnFlag.textContent = opts.flagged ? "UNFLAG" : "FLAG";
    btnReset.hidden = !opts.reset;
    setBusy(false);
  }

  function setBusy(b) {
    for (const el of [btnReveal, btnFlag, btnReset]) el.disabled = b;
  }

  async function api(path, method = "GET") {
    const r = await fetch(path, { method, cache: "no-store" });
    return r.json();
  }

  // ---------------------------------------------------------------- views
  function renderInfo(info) {
    coord = info.coord;
    $("coord").textContent = `TILE ${coord}`;
    $("status").textContent = `BOARD ${statusLabel[info.status] || ""}`;

    if (info.over) {
      const lost = info.status === "lost";
      return show(lost ? "DETONATED" : "CLEARED",
                  "THIS ROUND IS OVER. START A NEW ONE?",
                  { reset: true, boom: lost });
    }
    switch (info.state) {
      case "open":
        return info.count === 0
          ? show("CLEAR", "ALREADY OPEN. SCAN ANOTHER TILE.")
          : show(String(info.count), "ALREADY OPEN. SCAN ANOTHER TILE.", { number: true });
      case "flagged":
        return show(coord, "FLAGGED. REVEAL IT ANYWAY, OR TAKE THE FLAG OFF.",
                    { choose: true, flagged: true });
      default:
        return show(coord, "REVEAL IT, OR FLAG IT AS A MINE.", { choose: true });
    }
  }

  function renderReveal(r) {
    $("status").textContent = `BOARD ${statusLabel[r.status] || ""}`;
    switch (r.result) {
      case "boom":
        return show("BOOM", "THAT WAS A MINE. GAME OVER.", { boom: true, reset: true });
      case "win":
        return show("CLEARED", "LAST SAFE TILE. THE BOARD IS CLEAN.", { reset: true });
      case "ok":
        return r.count === 0
          ? show("CLEAR", `NO MINES NEARBY. OPENED ${r.opened} TILE${r.opened === 1 ? "" : "S"}.`)
          : show(String(r.count), `${r.count} MINE${r.count === 1 ? "" : "S"} TOUCHING THIS TILE`, { number: true });
      case "already":
        return show(String(r.count), "ALREADY OPEN. SCAN ANOTHER TILE.", { number: true });
      case "over":
        return show(r.status === "won" ? "CLEARED" : "DETONATED",
                    "THIS ROUND IS OVER. START A NEW ONE?",
                    { reset: true, boom: r.status === "lost" });
      default:
        return show("?", "UNEXPECTED REPLY");
    }
  }

  // ---------------------------------------------------------------- actions
  async function load() {
    if (Number.isNaN(id)) return show("?", "UNKNOWN TILE");
    $("tile-id").textContent = `TILE ${String(id).padStart(2, "0")}`;
    try {
      const info = await api(`/api/tile/${id}`);
      if (info.error) return show("?", "UNKNOWN TILE");
      renderInfo(info);
    } catch (_) {
      show("OFFLINE", "COULD NOT REACH THE SERVER. SAME WIFI?");
    }
  }

  btnReveal.addEventListener("click", async () => {
    setBusy(true);
    try {
      renderReveal(await api(`/api/reveal/${id}`, "POST"));
    } catch (_) {
      show("OFFLINE", "COULD NOT REACH THE SERVER.");
    }
  });

  btnFlag.addEventListener("click", async () => {
    setBusy(true);
    try {
      const r = await api(`/api/flag/${id}`, "POST");
      if (!r.changed) return load(); // tile got opened or round ended meanwhile
      if (r.flagged) {
        show("FLAGGED", "MARKED ON THE WALL. CHANGED YOUR MIND?", { choose: true, flagged: true });
      } else {
        show(coord, "FLAG REMOVED.", { choose: true });
      }
    } catch (_) {
      show("OFFLINE", "COULD NOT REACH THE SERVER.");
    }
  });

  btnReset.addEventListener("click", async () => {
    setBusy(true);
    try {
      await api("/api/reset", "POST");
      show("RESET", "FRESH BOARD ON THE WALL. GO SCAN.");
      $("status").textContent = "BOARD READY";
    } catch (_) {
      show("OFFLINE", "COULD NOT REACH THE SERVER.");
    }
  });

  load();
})();
