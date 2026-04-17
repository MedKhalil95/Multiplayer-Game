document.getElementById("splashBtn").addEventListener("click", function(){
  // Play the intro music on this user gesture — guaranteed to work in all browsers
  var audio = document.getElementById("introAudio");
  audio.play().catch(function(){});

  // Fade out and remove splash
  var splash = document.getElementById("splashScreen");
  splash.style.transition = "opacity .4s";
  splash.style.opacity = "0";
  setTimeout(function(){ splash.remove(); }, 420);
});
const S = {
  playerId: localStorage.getItem("pid") || rndId(),
  roomId:   null, gameType: "crash_bash",
  hostId:   null,   // set when we join/create a room and on every game state
  sse: null, inputLoop: null, lastState: null, keys: {},
  pidToName: {},    // pid → display name, built from room slots
  selectedColor: null,  // chosen player color hex
  preferredSlot: null,  // slot number player wants to occupy
};
localStorage.setItem("pid", S.playerId);

function rndId(){ return Math.random().toString(36).slice(2,10); }

// ═══════════════════════════════════════════════════════════ COLOR PICKER
const COLOR_PALETTE = [
  "#DC5050","#5050DC","#50C864","#DCDC50",
  "#DC50DC","#50DCDC","#FF8C00","#A0A0FF",
];

function initColorPicker(containerId, storageKey){
  const saved = localStorage.getItem(storageKey) || COLOR_PALETTE[0];
  if(!S.selectedColor) S.selectedColor = saved;
  _renderSwatches(containerId, saved);
}

// ═══════════════════════════════════════════════════════════ PERSONAL KIT SYSTEM
// Like PES/FIFA: you always see yourself in YOUR chosen kit.
// Opponents keep their server-assigned color unless it visually clashes with yours,
// in which case they get a unique contrast color on YOUR screen only.
// This is purely local — other players see their own remapped view.

// Distinct "away kit" colors used only for opponents when their color clashes with you.
// These are kept visually distinct from the main palette for clarity.
const OPPONENT_CONTRAST_COLORS = [
  "#FF6B35",  // vivid orange
  "#00D4AA",  // teal-mint
  "#FF3FA4",  // hot pink
  "#B8FF3F",  // lime green
  "#9B59B6",  // purple
  "#1ABC9C",  // emerald
];

/**
 * Returns a display-color map  { pid -> hexColor }  for the local viewer.
 *
 * Rules:
 *  1. Local player always gets S.selectedColor (their chosen kit).
 *  2. Every other player keeps their server color…
 *  3. …unless it's too close to the local player's color, in which case
 *     they're assigned a unique contrast color from OPPONENT_CONTRAST_COLORS.
 *  4. No two opponents share the same contrast color on this screen.
 */
function resolveDisplayColors(players) {
  const myId   = S.playerId;
  const myKit  = S.selectedColor || COLOR_PALETTE[0];

  const displayMap = {};
  const usedContrast = new Set();

  // Pass 1: assign local player their kit
  for (const [pid] of Object.entries(players)) {
    if (pid === myId) {
      displayMap[pid] = myKit;
    }
  }

  // Pass 2: assign opponents
  let contrastIdx = 0;
  for (const [pid, p] of Object.entries(players)) {
    if (pid === myId) continue;

    const serverColor = p.color;
    const clashes = _colorsClash(serverColor, myKit);

    if (!clashes) {
      displayMap[pid] = serverColor;
    } else {
      // Pick next unused contrast color
      let picked = null;
      while (contrastIdx < OPPONENT_CONTRAST_COLORS.length) {
        const candidate = OPPONENT_CONTRAST_COLORS[contrastIdx++];
        if (!usedContrast.has(candidate)) {
          picked = candidate;
          usedContrast.add(candidate);
          break;
        }
      }
      // Fallback: cycle through contrast palette if we ran out
      if (!picked) picked = OPPONENT_CONTRAST_COLORS[(contrastIdx - 1) % OPPONENT_CONTRAST_COLORS.length];
      displayMap[pid] = picked;
    }
  }

  return displayMap;
}

/**
 * Returns true when two hex colors are visually too similar to tell apart
 * on the game canvas (Euclidean distance in RGB space, threshold ~80/255).
 */
function _colorsClash(hexA, hexB) {
  if (!hexA || !hexB) return false;
  const [r1,g1,b1] = _hexToRgb(hexA);
  const [r2,g2,b2] = _hexToRgb(hexB);
  const dist = Math.sqrt((r1-r2)**2 + (g1-g2)**2 + (b1-b2)**2);
  return dist < 80;
}

function _hexToRgb(hex) {
  const n = parseInt(hex.replace("#",""), 16);
  return [(n>>16)&255, (n>>8)&255, n&255];
}

/**
 * Deep-clone the state, replacing each player's color with the
 * local viewer's resolved display color.  Does NOT mutate the original.
 */
function applyPersonalKit(state) {
  if (!state || !state.players) return state;
  const colorMap = resolveDisplayColors(state.players);
  // Shallow-clone state, deep-clone just the players map
  const patched = Object.assign({}, state, { players: {} });
  for (const [pid, p] of Object.entries(state.players)) {
    patched.players[pid] = Object.assign({}, p, {
      color: colorMap[pid] || p.color,
    });
  }
  // Also patch goals in crash_bash so your goal matches your kit
  if (state.goals) {
    patched.goals = {};
    for (const [pid, g] of Object.entries(state.goals)) {
      patched.goals[pid] = Object.assign({}, g, {
        color: colorMap[pid] || g.color,
      });
    }
  }
  return patched;
}

function _renderSwatches(containerId, selected){
  const container = document.getElementById(containerId);
  if(!container) return;
  container.innerHTML = COLOR_PALETTE.map(c => `
    <div class="color-swatch${c===selected?' selected':''}"
         style="background:${c}"
         title="${c}"
         onclick="pickColor('${c}','${containerId}')"></div>
  `).join("");
}

function pickColor(hex, containerId){
  S.selectedColor = hex;
  // sync both pickers to match
  _renderSwatches("createColorSwatches", hex);
  _renderSwatches("joinColorSwatches",   hex);
  localStorage.setItem("playerColor", hex);
}

// ═══════════════════════════════════════════════════════════ SLOT CONFIG
// slotTypes[i] = "human" | "bot" | "empty"  for i = 0..3
let slotTypes = ["human","bot","bot","bot"];   // default: 1 human + 3 bots

function initSlots(){
  slotTypes = ["human","bot","bot","empty"];  // default: 3-player game
  renderSlots(); updateSlotSummary();
  _syncPickerBtn(3);
}

function setPlayerCount(n){
  // Slot 0 is always human (the host). Remaining n-1 slots default to bots.
  // Any slots beyond n are set to empty.
  slotTypes = slotTypes.map((t,i) => {
    if(i === 0) return "human";         // host always human
    if(i < n)   return "bot";           // fill up to n with bots
    return "empty";                     // rest unused
  });
  renderSlots(); updateSlotSummary(); _syncPickerBtn(n);
  _updateTeamModeVisibility();
}

function _updateTeamModeVisibility(){
  const active = slotTypes.filter(t=>t!=="empty").length;
  const sec    = document.getElementById("teamModeSection");
  const prev   = document.getElementById("teamPreview");
  const chk    = document.getElementById("teamModeCheck");
  if(sec) sec.style.display = (active === 4) ? "" : "none";
  // Hide preview if we switched away from 4-player
  if(active !== 4 && chk) chk.checked = false;
  if(prev) prev.style.display = (chk && chk.checked) ? "" : "none";
}

function _syncPickerBtn(n){
  document.querySelectorAll(".pcp-btn").forEach(b=>{
    b.classList.toggle("active", parseInt(b.dataset.n)===n);
  });
}

function renderSlots(){
  const grid = document.getElementById("slotGrid");
  grid.innerHTML = "";
  slotTypes.forEach((type, i) => {
    const cell = document.createElement("div");
    cell.className = `slot-cell ${type}`;
    cell.innerHTML = `
      <div class="slot-icon">${type==="human"?"👤":type==="bot"?"🤖":"➕"}</div>
      <div class="slot-label">${type==="human"?"Human":type==="bot"?"Bot":"Empty"}</div>`;
    cell.addEventListener("click", () => cycleSlot(i));
    grid.appendChild(cell);
  });
}

function cycleSlot(i){
  // slot 0 is always the host (human) – can't change it
  if(i === 0) return;
  const order = ["empty","human","bot"];
  const cur   = slotTypes[i];
  slotTypes[i] = order[(order.indexOf(cur)+1) % order.length];
  renderSlots(); updateSlotSummary();
  // Sync the count picker to whichever button matches current active slots
  const active = slotTypes.filter(t=>t!=="empty").length;
  if(active >= 2 && active <= 4) _syncPickerBtn(active);
  else document.querySelectorAll(".pcp-btn").forEach(b=>b.classList.remove("active"));
  _updateTeamModeVisibility();
}

function updateSlotSummary(){
  const humans = slotTypes.filter(t=>t==="human").length;
  const bots   = slotTypes.filter(t=>t==="bot").length;
  const total  = humans + bots;
  document.getElementById("slotSummary").textContent =
    `${total} player game · ${humans} human${humans>1?"s":""} · ${bots} bot${bots!==1?"s":""}`;
}

// ═══════════════════════════════════════════════════════════ NAVIGATION
function showScreen(id){
  document.querySelectorAll(".screen").forEach(s=>s.classList.remove("active"));
  document.getElementById(id).classList.add("active");
}

function selectGame(type, el){
  S.gameType = type;
  document.querySelectorAll(".game-card").forEach(c=>c.classList.remove("sel"));
  el.classList.add("sel");
}

function switchTab(tab, btn){
  document.querySelectorAll(".tab").forEach(t=>t.classList.remove("active"));
  btn.classList.add("active");
  document.getElementById("createPanel").style.display = tab==="create"?"":"none";
  document.getElementById("joinPanel").style.display   = tab==="join"  ?"":"none";
  if(tab==="join") loadRooms();
}

// ═══════════════════════════════════════════════════════════ KEY CONFIG
const DEFAULT_KEYS = {up:"ArrowUp",down:"ArrowDown",left:"ArrowLeft",right:"ArrowRight",action:" "};
let keyBindings = JSON.parse(localStorage.getItem("kb")||"null") || {...DEFAULT_KEYS};
let listeningKey = null;

function openKeyScreen(){ showKeyConfig(); showScreen("keyScreen"); }

function showKeyConfig(){
  const c = document.getElementById("keyConfigRows");
  const labels = {up:"Up",down:"Down",left:"Left",right:"Right",action:"Action / Throw"};
  c.innerHTML = "";
  for(const [k,label] of Object.entries(labels)){
    const row = document.createElement("div");
    row.className = "key-row";
    row.innerHTML = `<label>${label}</label>
      <span class="key-badge" id="kb_${k}" onclick="listenKey('${k}')">${fmtKey(keyBindings[k])}</span>`;
    c.appendChild(row);
  }
}

function fmtKey(k){ return k===" "?"Space":k.replace("Arrow","").replace("Key","")||k; }

function listenKey(action){
  listeningKey = action;
  const b = document.getElementById(`kb_${action}`);
  b.textContent = "…"; b.classList.add("listening");
}

function resetKeys(){
  keyBindings = {...DEFAULT_KEYS};
  localStorage.setItem("kb", JSON.stringify(keyBindings));
  showKeyConfig();
}

document.addEventListener("keydown", e=>{
  if(listeningKey){
    keyBindings[listeningKey] = e.key;
    localStorage.setItem("kb", JSON.stringify(keyBindings));
    listeningKey = null; showKeyConfig(); e.preventDefault(); return;
  }
  S.keys[e.key] = true;
}, true);
document.addEventListener("keyup", e=>{ S.keys[e.key] = false; }, true);

// ═══════════════════════════════════════════════════════════ CREATE
async function createGame(){
  _cdVoice.unlock();
  const name  = document.getElementById("createName").value.trim()||"Player";
  const btn   = document.getElementById("createBtn");
  btn.disabled = true; btn.textContent = "Creating…";
  document.getElementById("createError").textContent = "";

  const humans     = slotTypes.filter(t=>t==="human").length;
  const bots       = slotTypes.filter(t=>t==="bot").length;
  const total      = humans + bots;
  const teamMode   = total === 4 && !!(document.getElementById("teamModeCheck")?.checked);

  const body = {
    game_type:      S.gameType,
    player_name:    name,
    player_color:   S.selectedColor || COLOR_PALETTE[0],
    total_slots:    total,
    bot_slots:      bots,
    bot_difficulty: document.getElementById("botDiff").value,
    team_mode:      teamMode,
    player_id:      S.playerId,
  };

  try{
    const res  = await fetch("/api/rooms/create",{
      method:"POST",
      headers:{"Content-Type":"application/json","X-Player-Id":S.playerId},
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if(!res.ok) throw new Error(data.detail||data.error||"Server error");

    S.roomId  = data.room_id;
    S.hostId  = data.player_id;   // creator is always host
    S.pidToName[S.playerId] = name;
    localStorage.setItem("playerName", name);
    if(data.slots) data.slots.forEach(sl => { if(sl.pid && sl.name) S.pidToName[sl.pid] = sl.name; });

    // If game didn't start immediately (multi-human room), let host pick slot
    if(data.status !== "playing" && data.slots){
      const allSlots = data.slots.filter(s => !s.is_bot || s.displaceable);
      if(allSlots.length > 1){
        const TEAM_COLORS = {A:"🔴",B:"🔵"};
        const labels = allSlots.map(s => {
          const teamIcon = s.team ? ` ${TEAM_COLORS[s.team]||""} Team ${s.team}` : "";
          const youTag   = s.filled && s.pid === S.playerId ? " ← you (default)" : "";
          const botTag   = s.is_bot ? " (replaces bot)" : "";
          return `Slot ${s.slot}${teamIcon}${youTag}${botTag}`;
        });
        // Show picker — host can confirm default slot 1 or pick another
        const choice = await showSlotPicker(labels, allSlots.map(s=>s.slot),
                        "Choose your starting position", "You're currently in Slot 1 by default.");
        if(choice !== null && choice !== 1){
          // Call pick_slot to move the host before anyone else joins
          await fetch(`/api/rooms/${data.room_id}/pick_slot`,{
            method:"POST",
            headers:{"Content-Type":"application/json","X-Player-Id":S.playerId},
            body: JSON.stringify({slot: choice, player_id: S.playerId}),
          });
        }
      }
    }
    enterRoom(data.room_id, data.status==="playing");

  }catch(e){
    document.getElementById("createError").textContent = e.message;
  }finally{
    btn.disabled = false; btn.textContent = "Create & Play";
  }
}

// ═══════════════════════════════════════════════════════════ JOIN
async function loadRooms(){
  const list = document.getElementById("roomList");
  list.innerHTML = '<p class="notice">Loading…</p>';
  try{
    const data = await fetch("/api/rooms").then(r=>r.json());
    if(!data.rooms.length){
      list.innerHTML = '<p class="notice">No open rooms. Create one!</p>'; return;
    }
    list.innerHTML = data.rooms.map(r=>`
      <div class="room-item">
        <div>
          <h4>${r.game_name}</h4>
          <p>Host: ${r.host_name} · ${r.human_count}/${r.human_slots} humans
             · ${r.bot_slots} bot${r.bot_slots!==1?"s":""}
             · ${r.created_ago}s ago</p>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <span class="badge open">Open</span>
          <button class="btn btn-primary" style="padding:5px 13px;font-size:.78rem"
                  onclick="joinRoom('${r.room_id}')">Join</button>
        </div>
      </div>`).join("");
  }catch(e){ list.innerHTML = '<p class="notice">Could not load rooms.</p>'; }
}

async function joinByCode(){
  _cdVoice.unlock();
  const code = document.getElementById("joinCode").value.trim().toLowerCase();
  const name = document.getElementById("joinName").value.trim()||"Player";
  if(!code){ document.getElementById("joinError").textContent="Enter a room code"; return; }
  await joinRoom(code, name);
}

async function joinRoom(roomId, nameOverride){
  const name = nameOverride || document.getElementById("joinName").value.trim()||"Player";
  document.getElementById("joinError").textContent = "";

  // Fetch room details to show available slots before joining
  let roomInfo = null;
  try{ roomInfo = await fetch(`/api/rooms/${roomId}`).then(r=>r.json()); }catch{}

  // If there are multiple free human slots and team mode, let player pick
  if(roomInfo && roomInfo.slots){
    // Available slots = empty human slots + bot-default slots the human can displace
    const availableSlots = roomInfo.slots.filter(s =>
      (!s.is_bot && !s.filled) || (s.is_bot && s.displaceable)
    );
    if(availableSlots.length > 1){
      const TEAM_COLORS = {A:"🔴",B:"🔵"};
      const labels = availableSlots.map(s => {
        const teamIcon = s.team ? ` ${TEAM_COLORS[s.team]||""} Team ${s.team}` : "";
        const botNote  = s.is_bot ? " (replaces bot)" : "";
        return `Slot ${s.slot}${teamIcon}${botNote}`;
      });
      const choice = await showSlotPicker(labels, availableSlots.map(s=>s.slot));
      if(choice !== null) S.preferredSlot = choice;
    } else if(availableSlots.length === 1){
      S.preferredSlot = availableSlots[0].slot;
    }
  }

  try{
    const res  = await fetch(`/api/rooms/${roomId}/join`,{
      method:"POST",
      headers:{"Content-Type":"application/json","X-Player-Id":S.playerId},
      body: JSON.stringify({player_name:name, player_id:S.playerId,
                            player_color: S.selectedColor || COLOR_PALETTE[0],
                            preferred_slot: S.preferredSlot || null}),
    });
    const data = await res.json();
    if(!res.ok) throw new Error(data.detail||data.error||"Could not join");
    S.preferredSlot = null;  // clear after use
    S.roomId = roomId;
    S.pidToName[S.playerId] = name;
    localStorage.setItem("playerName", name);
    if(data.host_id) S.hostId = data.host_id;
    if(data.slots) data.slots.forEach(sl => { if(sl.pid && sl.name) S.pidToName[sl.pid] = sl.name; });
    enterRoom(roomId, data.status==="playing");
  }catch(e){ document.getElementById("joinError").textContent = e.message; }
}

/** Show a modal slot-picker dialog. Returns chosen slot number or null if dismissed. */
function showSlotPicker(labels, slotNumbers,
    title="Choose your slot",
    subtitle="Pick the position you want to play in"){
  return new Promise(resolve => {
    const overlay = document.createElement("div");
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:9999;"
                          + "display:flex;align-items:center;justify-content:center";
    const box = document.createElement("div");
    box.style.cssText = "background:#1a1a2e;border:2px solid var(--border);border-radius:14px;"
                      + "padding:24px 28px;max-width:360px;width:90%;text-align:center";
    box.innerHTML = `<h3 style="margin:0 0 6px">${title}</h3>
      <p style="color:var(--muted);font-size:.83rem;margin:0 0 16px">${subtitle}</p>
      <div style="display:flex;flex-direction:column;gap:9px">
        ${labels.map((l,i) => `<button onclick="this.closest('.sp-overlay').resolve(${slotNumbers[i]})"
          style="padding:10px 18px;border-radius:8px;border:1.5px solid var(--acc);
                 background:#0e0e1e;color:var(--acc);font-size:.92rem;cursor:pointer;
                 transition:.15s;text-align:left"
          onmouseover="this.style.background='var(--acc)';this.style.color='#000'"
          onmouseout="this.style.background='#0e0e1e';this.style.color='var(--acc)'">${l}</button>`).join("")}
        <button onclick="this.closest('.sp-overlay').resolve(null)"
          style="margin-top:4px;padding:7px;border-radius:8px;border:1.5px solid var(--border);
                 background:none;color:var(--muted);font-size:.82rem;cursor:pointer">Keep current slot</button>
      </div>`;
    overlay.classList.add("sp-overlay");
    overlay.resolve = (v) => { document.body.removeChild(overlay); resolve(v); };
    overlay.appendChild(box);
    document.body.appendChild(overlay);
  });
}

// ═══════════════════════════════════════════════════════════ ROOM ENTRY
function enterRoom(roomId, alreadyPlaying){
  // Persist so a page refresh can reconnect
  localStorage.setItem("roomId",   roomId);
  localStorage.setItem("gameType", S.gameType);

  if(S.sse){ S.sse.close(); S.sse = null; }
  S.sse = new EventSource(`/api/rooms/${roomId}/stream`);
  S.sse.onmessage  = onMsg;
  S.sse.onerror    = ()=>console.warn("SSE reconnecting…");

  // Always start with a clean ready button regardless of how we got here
  resetReadyBtn();

  document.getElementById("displayRoomId").textContent = roomId.toUpperCase();
  document.getElementById("waitGameType").textContent  =
    S.gameType==="crash_bash"?"⚽ Crash Bash":"💥 TNT Battle";

  if(alreadyPlaying){ showScreen("gameScreen"); startInputLoop(); }
  else { showScreen("waitScreen"); renderWaitSlots([]); }
}

// ═══════════════════════════════════════════════════════════ WAITING ROOM
function renderWaitSlots(slots){
  const c = document.getElementById("waitSlots");
  if(!slots.length){ c.innerHTML = '<div class="notice">Connecting…</div>'; return; }

  const isTeamMode = slots.some(s => s.team);
  const iAmInRoom  = slots.some(s => s.pid === S.playerId);
  const myKit      = S.selectedColor || COLOR_PALETTE[0];

  const kitBanner = slots.length > 1 ? `
    <div style="display:flex;align-items:center;gap:8px;padding:7px 14px;
                margin-bottom:6px;background:rgba(255,255,255,.04);
                border:1.5px solid var(--border);border-radius:8px;font-size:.8rem;color:var(--muted)">
      👕 Your kit:
      <span style="display:inline-block;width:20px;height:20px;border-radius:5px;
                   background:${myKit};border:2px solid rgba(255,255,255,.25);flex-shrink:0"></span>
      <span style="color:var(--text)">${myKit}</span>
      <span style="margin-left:auto;font-size:.72rem;opacity:.7">(opponents see their own kit)</span>
    </div>` : "";

  const TEAM_COLORS = {A:"#E05050", B:"#5080E0"};
  const teamHeader = isTeamMode ? `
    <div style="display:flex;gap:8px;margin-bottom:8px">
      <div style="flex:1;background:rgba(224,80,80,.12);border:1.5px solid #E05050;
                  border-radius:8px;padding:8px 10px;font-size:.8rem;text-align:center">
        <span style="color:#E05050;font-weight:700">🔴 Team A</span><br>
        <span style="color:var(--muted);font-size:.72rem">Slots 1 &amp; 3 · Top + Left</span>
      </div>
      <div style="flex:1;background:rgba(80,128,224,.12);border:1.5px solid #5080E0;
                  border-radius:8px;padding:8px 10px;font-size:.8rem;text-align:center">
        <span style="color:#5080E0;font-weight:700">🔵 Team B</span><br>
        <span style="color:var(--muted);font-size:.72rem">Slots 2 &amp; 4 · Bottom + Right</span>
      </div>
    </div>` : "";

  window._lastSlots = slots;  // cache so pickSlot() can read current state
  c.innerHTML = kitBanner + teamHeader + slots.map(s=>{
    const isYou        = s.pid === S.playerId;
    const isBotDefault = s.is_bot && !isYou;           // bot placeholder, no human here
    const isEmptyHuman = !s.is_bot && !s.filled;       // genuine empty human slot
    // Player can move here if: they are in the room AND
    // the slot is either an empty human slot OR a displaceable bot slot
    const canMove = iAmInRoom && !isYou &&
                    (isEmptyHuman || (isBotDefault && s.displaceable));

    let cls = "wait-slot";
    if(isBotDefault && !canMove) cls += " bot-slot";
    else if(isBotDefault)        cls += " bot-slot slot-displaceable";
    else if(s.filled)            cls += " human-filled";
    if(isYou)                    cls = "wait-slot you";
    if(canMove && isEmptyHuman)  cls += " slot-pickable";

    const icon = isYou        ? "👤"
               : isBotDefault ? "🤖"
               : s.filled     ? "👤"
               : "⬜";

    const name = isYou        ? s.name + " (you)"
               : isBotDefault && canMove ? s.name + " — click to take this slot"
               : isBotDefault ? s.name
               : s.filled     ? s.name
               : canMove      ? "Click to move here"
               : "Waiting…";

    const badge = isYou    ? `<button class="btn-move" onclick="pickSlot(${s.slot})">↕ Switch slot</button>`
                : canMove  ? `<button class="btn-move" onclick="pickSlot(${s.slot})">➡ Take slot ${s.slot}</button>`
                : isBotDefault ? "🤖 Bot"
                : s.filled ? (s.ready ? "✅ Ready" : "⏳ Waiting")
                : "";

    let teamBadge = "";
    if(isTeamMode && s.team){
      const tc = TEAM_COLORS[s.team] || "#888";
      teamBadge = `<span style="font-size:.72rem;font-weight:700;color:${tc};margin-left:4px">${s.team==="A"?"🔴":"🔵"} Team ${s.team}</span>`;
    }

    const dotStyle = isYou ? `style="background:${myKit}"` : "";
    const clickHandler = canMove ? `onclick="pickSlot(${s.slot})"` : "";
    return `<div class="${cls}" ${clickHandler}>
      <div class="wait-dot" ${dotStyle}></div>
      <span class="wait-name">${icon} <strong>Slot ${s.slot}</strong> — ${name}${teamBadge}</span>
      <span class="wait-status">${badge}</span>
    </div>`;
  }).join("");
}

async function pickSlot(slotNumber){
  if(!S.roomId) return;
  // If clicking own slot, show the full slot picker instead
  const mySlot = (window._lastSlots||[]).find(s=>s.pid===S.playerId);
  if(mySlot && mySlot.slot === slotNumber){
    const slots = window._lastSlots || [];
    const available = slots.filter(s =>
      s.slot !== slotNumber &&
      ((!s.is_bot && !s.filled) || (s.is_bot && s.displaceable))
    );
    if(!available.length){ return; }  // nowhere to go
    const TEAM_COLORS = {A:"🔴",B:"🔵"};
    const labels = available.map(s=>{
      const t = s.team ? ` ${TEAM_COLORS[s.team]||""} Team ${s.team}` : "";
      const b = s.is_bot ? " (replaces bot)" : "";
      return `Slot ${s.slot}${t}${b}`;
    });
    const choice = await showSlotPicker(labels, available.map(s=>s.slot),
      "Switch your slot", `Currently in Slot ${slotNumber} — pick a new position`);
    if(choice === null) return;
    slotNumber = choice;
  }
  try{
    const res = await fetch(`/api/rooms/${S.roomId}/pick_slot`,{
      method: "POST",
      headers: {"Content-Type":"application/json","X-Player-Id": S.playerId},
      body: JSON.stringify({slot: slotNumber, player_id: S.playerId}),
    });
    if(!res.ok){
      const d = await res.json();
      alert(d.detail || "Could not switch slot");
    }
    // Server broadcasts player_joined → renderWaitSlots refreshes automatically
  } catch(e){ console.error("pickSlot error", e); }
}

let readySent = false;

function resetReadyBtn(){
  readySent = false;
  const btn = document.getElementById("readyBtn");
  if(btn){
    btn.textContent = "✅ Ready";
    btn.disabled    = false;
  }
}

async function sendReady(){
  _cdVoice.unlock();
  if(readySent) return;
  readySent = true;
  const btn = document.getElementById("readyBtn");
  btn.textContent = "✅ Waiting for others…";
  btn.disabled    = true;
  await fetch(`/api/rooms/${S.roomId}/ready`,{
    method:"POST",
    headers:{"Content-Type":"application/json","X-Player-Id":S.playerId},
    body: JSON.stringify({player_id:S.playerId}),
  });
}

function leaveRoom(){
  // Tell the server to remove us from the waiting room so we can rejoin
  // later (e.g. after changing name). Best-effort — ignore network errors.
  if(S.roomId){
    fetch(`/api/rooms/${S.roomId}/leave`, {
      method: "POST",
      headers: { "X-Player-Id": S.playerId },
    }).catch(()=>{});
  }
  localStorage.removeItem("roomId");
  localStorage.removeItem("gameType");
  if(S.sse){ S.sse.close(); S.sse = null; }
  S.roomId = null;
  resetReadyBtn();
  showScreen("lobbyScreen");
}

// ═══════════════════════════════════════════════════════════ SSE HANDLER
function onMsg(ev){
  let msg; try{ msg = JSON.parse(ev.data); }catch{ return; }
  const evt = msg._event;

  // Always capture host_id if the server sends it
  if(msg.host_id) S.hostId = msg.host_id;

  // Build / update pid → name map from any slot data the server sends
  if(msg.slots){
    msg.slots.forEach(sl => {
      if(sl.pid && sl.name) S.pidToName[sl.pid] = sl.name;
    });
  }

  if(evt==="waiting"||evt==="player_joined"||evt==="ready_update"||evt==="player_left"){
    renderWaitSlots(msg.slots||[]);
    // game_starting event handles the actual transition; skip here to avoid
    // starting the input loop before the game object exists on the server
    if(msg.status==="playing" && !S.inputLoop){ showScreen("gameScreen"); startInputLoop(); }
    const humans = (msg.slots||[]).filter(s=>!s.is_bot);
    const filled = humans.filter(s=>s.filled).length;
    const total  = humans.length;
    document.getElementById("waitStatus").textContent =
      `${filled}/${total} humans joined`;
    return;
  }

  // Host triggered a rematch — all players silently return to waiting screen
  if(evt==="rematch_called"){
    stopInputLoop();
    S.keys = {};
    _hideCountdown();
    document.getElementById("gameOverlay").classList.remove("show");
    renderWaitSlots(msg.slots||[]);
    resetReadyBtn();
    // Solo-vs-bots room: human_slots==1 so server starts immediately;
    // wait for game_starting event to switch to game screen.
    showScreen("waitScreen");
    return;
  }

  if(evt==="game_starting"){
    showScreen("gameScreen");
    startInputLoop();
    // On touch devices auto-fullscreen so the arena fills the screen immediately
    if(window.matchMedia("(pointer:coarse)").matches && !_isFullscreen()){
      if(_fsSupported){
        const el  = document.getElementById("gameScreen");
        const req = el.requestFullscreen || el.webkitRequestFullscreen || el.mozRequestFullScreen;
        if(req) req.call(el).catch(() => _enterFakeFS());
        else _enterFakeFS();
      } else {
        _enterFakeFS();
      }
    }
    // Show "3" immediately so the screen isn't blank while waiting for first countdown tick
    _showCountdown(3);
  }

  if(evt==="countdown"){
    // Make sure we're on the game screen (in case game_starting was missed)
    const gs = document.getElementById("gameScreen");
    if(!gs.classList.contains("active")){ showScreen("gameScreen"); startInputLoop(); }
    _showCountdown(msg.count);
  }

  if(msg.game_type){
    const firstFrame = !S.lastState;
    S.lastState = msg;
    renderGame(msg); updateHud(msg);
    // On the very first game-state frame the HUD/controls may not have their
    // final layout height yet (especially on mobile).  Schedule a second render
    // after two animation frames so getBoundingClientRect returns settled values.
    if(firstFrame){
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if(S.lastState) renderGame(S.lastState);
      }));
    }
    if(msg.game_over){ stopInputLoop(); showGameOver(msg); }

  }
}

// ═════════════════════════════════════════════════════════ COUNTDOWN VOICE - MALE ONLY
const _cdVoice = {
  _synth: window.speechSynthesis || null,
  _voice: null,
  _unlocked: false,
  _pending: null,

  // List of known male voice names across all platforms
  _MALE_NAMES: /\b(alex|daniel|fred|thomas|oliver|george|arthur|rishi|aaron|david|james|john|paul|mark|eric|brian|guy|carlos|diego|jorge|luca|reed|rock|sandy|wayne|viktor|stefan|yannick|eddy|nicolas|neel|aarav|otoya|ichiro|ryan|tom|gordon|lee|bruce|felix|hans|henrik|tarik|onur|mehmet|ali|junior|microsoft|googleuk|google-uk|en-us-x-sfg|en-us-x-iob|en-us-x-iom|en-gb-x-rjs|en-gb-x-gbd)\b/i,

  _isMale(v){
    const n = (v.name + " " + v.voiceURI).toLowerCase();
    // Check explicit male name patterns
    if(this._MALE_NAMES.test(n)) return true;
    // Check for "male" keyword in the name
    if(/\bmale\b/i.test(n) && !/female/i.test(n)) return true;
    // Android specific: Google voices that don't specify gender
    if(/google/i.test(n) && /male/i.test(n)) return true;
    // Default to false - we only want explicit male voices
    return false;
  },

  _pickVoice(){
    if(!this._synth) return;
    const voices = this._synth.getVoices();
    if(!voices.length) return;
    
    console.log("[Voice] Available voices:", voices.map(v => v.name + " (" + v.lang + ")").join(", "));
    
    // Priority 1: Find an explicit male English voice
    let picked = voices.find(v => /^en/i.test(v.lang) && this._isMale(v));
    
    // Priority 2: Any voice with "male" in the name (any language)
    if(!picked){
      picked = voices.find(v => v.name.toLowerCase().includes("male"));
    }
    
    // Priority 3: Any English voice (fallback - will use low pitch)
    if(!picked){
      picked = voices.find(v => /^en/i.test(v.lang));
    }
    
    // Priority 4: First available voice
    if(!picked && voices.length){
      picked = voices[0];
    }
    
    this._voice = picked;
    console.log("[Voice] Selected:", this._voice ? this._voice.name : "NONE", 
                "| Is male:", this._voice ? this._isMale(this._voice) : false);
  },

  init(){
    if(!this._synth) return;
    this._pickVoice();
    this._synth.onvoiceschanged = () => this._pickVoice();
  },

  unlock(){
    _sfx.unlock();   // also unlock the Web Audio context on this gesture
    if(this._unlocked || !this._synth) return;
    const u = new SpeechSynthesisUtterance("\u00a0");
    u.volume = 0;
    u.onend  = () => {
      this._unlocked = true;
      if(this._pending){
        const { text, opts } = this._pending;
        this._pending = null;
        this._fire(text, opts);
      }
    };
    this._synth.speak(u);
  },

  speak(text, opts = {}){
    if(!this._synth) return;
    if(!this._unlocked){
      this._pending = { text, opts };
      return;
    }
    this._fire(text, opts);
  },

  _fire(text, { pitch = 1, rate = 0.85, volume = 1 } = {}){
    this._synth.cancel();
    const u = new SpeechSynthesisUtterance(text);
    
    // Use the selected voice (preferring male if found)
    if(this._voice) u.voice = this._voice;
    
    // Force a deep, masculine pitch regardless of which voice was selected
    // Lower pitch = deeper voice (0.4-0.6 sounds most masculine)
    // Even if we got a female voice, this makes it sound male
    u.pitch  = 0.5;   // Deep, masculine pitch
    u.rate   = rate;
    u.volume = volume;
    u.lang   = "en-US";
    
    this._synth.speak(u);
  }
};
_cdVoice.init();

function _speakCountdown(count){
  if(count > 0){
    _cdVoice.speak(String(count), { pitch: 0.85, rate: 0.78, volume: 1 });
  } else {
    _cdVoice.speak("Go!", { pitch: 1.25, rate: 1.1, volume: 1 });
  }
}

// ── iOS-safe audio engine using Web Audio API ──────────────────────────
// iOS Safari blocks HTMLAudioElement unless created inside a tap handler.
// Web Audio API decodes once into an AudioBuffer and plays via AudioContext,
// which is unlocked on the first user gesture (same tap that calls _cdVoice.unlock).
const _sfx = {
  _ctx: null,
  _buf: null,        // decoded AudioBuffer for cortex-laugh
  _unlocked: false,

  // Call once from any user-gesture handler (already done via _cdVoice.unlock)
  unlock(){
    if(this._unlocked) return;
    try {
      this._ctx = new (window.AudioContext || window.webkitAudioContext)();
      // Resume context – required on iOS after creation
      if(this._ctx.state === "suspended") this._ctx.resume();
      this._unlocked = true;
      this._load();
    } catch(e){ console.warn("[sfx] AudioContext failed:", e); }
  },

  _load(){
    if(!this._ctx || this._buf) return;
    fetch("sounds/cortex-laugh.mp3")
      .then(r => r.arrayBuffer())
      .then(ab => this._ctx.decodeAudioData(ab, buf => { this._buf = buf; }))
      .catch(e => console.warn("[sfx] load failed:", e));
  },

  // Play the buffer once; call onDone() when it ends (or immediately on failure)
  play(onDone){
    if(!this._ctx || !this._buf){
      // AudioContext not ready yet – try unlocking now then play once loaded
      if(onDone) onDone();
      return;
    }
    try {
      if(this._ctx.state === "suspended") this._ctx.resume();
      const src = this._ctx.createBufferSource();
      src.buffer = this._buf;
      src.connect(this._ctx.destination);
      if(onDone) src.onended = onDone;
      src.start(0);
    } catch(e){
      console.warn("[sfx] play failed:", e);
      if(onDone) onDone();
    }
  }
};

function _playCortexLaughTwice(){
  _sfx.play(() => {
    setTimeout(() => _sfx.play(), 300);
  });
}

function _speakGameOver(state){
  const winP = state.winner && state.players && state.players[state.winner];
  setTimeout(() => {
    // ── Team mode: use winner_display which contains both teammate names ──
    if(state.team_mode && state.winner_display){
      const phrase = state.winner_display.replace("WIN!", "win!").replace("WINS!", "wins!");
      if(!_cdVoice._synth){ _playCortexLaughTwice(); return; }
      _cdVoice._synth.cancel();
      const u = new SpeechSynthesisUtterance(phrase);
      if(_cdVoice._voice) u.voice = _cdVoice._voice;
      u.pitch = 0.5; u.rate = 0.82; u.volume = 1; u.lang = "en-US";
      u.onend = () => _playCortexLaughTwice();
      _cdVoice._synth.speak(u);
      return;
    }

    // ── Free-for-all: single winner ──
    if(!winP){ _playCortexLaughTwice(); return; }
    const name = winP.name
      || S.pidToName[winP.player_id]
      || `Player ${winP.number}`;
    const phrase = `${name} wins!`;
    if(!_cdVoice._synth){ _playCortexLaughTwice(); return; }
    _cdVoice._synth.cancel();
    const u = new SpeechSynthesisUtterance(phrase);
    if(_cdVoice._voice) u.voice = _cdVoice._voice;
    u.pitch = 0.5; u.rate = 0.82; u.volume = 1; u.lang = "en-US";
    u.onend = () => _playCortexLaughTwice();
    _cdVoice._synth.speak(u);
  }, 400);
}

// ═══════════════════════════════════════════════════════════ COUNTDOWN
function _showCountdown(count){
  const overlay = document.getElementById("countdownOverlay");
  const numEl   = document.getElementById("countdownNumber");
  const goEl    = document.getElementById("countdownGo");

  overlay.classList.add("show");

  if(count > 0){
    goEl.style.display  = "none";
    numEl.style.display = "block";
    numEl.textContent   = count;
    // Restart animation: remove then force reflow then re-add
    numEl.style.animation = "none";
    void numEl.offsetWidth;   // force reflow
    numEl.style.animation = "";
    _speakCountdown(count);
  } else {
    // count === 0  →  "GO!"
    numEl.style.display = "none";
    goEl.style.display  = "block";
    goEl.style.animation = "none";
    void goEl.offsetWidth;
    goEl.style.animation = "";
    _speakCountdown(0);
    setTimeout(_hideCountdown, 800);
  }
}

function _hideCountdown(){
  const overlay = document.getElementById("countdownOverlay");
  overlay.classList.remove("show");
  // Reset for next use
  const numEl = document.getElementById("countdownNumber");
  const goEl  = document.getElementById("countdownGo");
  if(numEl){ numEl.style.display = "block"; numEl.textContent = ""; }
  if(goEl) { goEl.style.display  = "none"; }
}

// ═══════════════════════════════════════════════════════════ INPUT
function buildInput(){
  const k = S.keys;
  const action = !!(k[keyBindings.action]) || _actionLatch;
  _actionLatch = false;   // consume latch — clears after exactly one poll
  return {
    up:     !!(k[keyBindings.up]),    down:  !!(k[keyBindings.down]),
    left:   !!(k[keyBindings.left]),  right: !!(k[keyBindings.right]),
    action,
  };
}

function startInputLoop(){
  stopInputLoop();
  S.inputLoop = setInterval(()=>{
    if(!S.roomId) return;
    fetch(`/api/rooms/${S.roomId}/input`,{
      method:"POST",
      headers:{"Content-Type":"application/json","X-Player-Id":S.playerId},
      body: JSON.stringify({input:buildInput(), player_id:S.playerId}),
    }).catch(()=>{});
  }, 33);
}

function stopInputLoop(){
  if(S.inputLoop){ clearInterval(S.inputLoop); S.inputLoop = null; }
}

// ═══════════════════════════════════════════════════════════ MOBILE JOYSTICK
let _ctrlMode = localStorage.getItem('ctrlMode') || 'joy'; // 'joy' | 'dpad'

function toggleCtrlMode(){
  _ctrlMode = _ctrlMode === 'joy' ? 'dpad' : 'joy';
  localStorage.setItem('ctrlMode', _ctrlMode);
  _applyCtrlMode();
}

function _applyCtrlMode(){
  const zone  = document.getElementById('joystickZone');
  const dpad  = document.getElementById('dpad');
  const label = document.getElementById('ctrlToggle');
  if(_ctrlMode === 'dpad'){
    zone.style.display = 'none';
    dpad.style.display = 'block';
    if(label) label.textContent = 'Switch to Joystick';
  } else {
    zone.style.display = '';
    dpad.style.display = 'none';
    if(label) label.textContent = 'Switch to D-pad';
  }
  ['up','down','left','right'].forEach(d=>{ S.keys[keyBindings[d]] = false; });
}

// ── Joystick ──────────────────────────────────────────────────────────
(function(){
  const zone  = document.getElementById('joystickZone');
  const knob  = document.getElementById('joystickKnob');
  const arrows = {
    up: document.getElementById('joyUp'),
    dn: document.getElementById('joyDn'),
    lt: document.getElementById('joyLt'),
    rt: document.getElementById('joyRt'),
  };

  const RADIUS   = 44;
  const DEAD_AXIS = 8;

  let active = false, originX = 0, originY = 0;

  function getCenter(){
    const r = zone.getBoundingClientRect();
    return { x: r.left + r.width/2, y: r.top + r.height/2 };
  }

  function setArrow(id, on){ if(arrows[id]) arrows[id].classList.toggle('lit', on); }

  function applyJoy(dx, dy){
    const dist  = Math.hypot(dx, dy);
    const ratio = Math.min(dist, RADIUS) / (dist || 1);
    knob.style.transform = `translate(calc(-50% + ${dx*ratio}px), calc(-50% + ${dy*ratio}px))`;

    const goLeft  = dx < -DEAD_AXIS;
    const goRight = dx >  DEAD_AXIS;
    const goUp    = dy < -DEAD_AXIS;
    const goDn    = dy >  DEAD_AXIS;

    S.keys[keyBindings.left]  = goLeft;
    S.keys[keyBindings.right] = goRight;
    S.keys[keyBindings.up]    = goUp;
    S.keys[keyBindings.down]  = goDn;

    setArrow('lt', goLeft);
    setArrow('rt', goRight);
    setArrow('up', goUp);
    setArrow('dn', goDn);
  }

  function onStart(e){
    e.preventDefault();
    if(_ctrlMode !== 'joy') return;
    active = true;
    const touch = e.touches ? e.touches[0] : e;
    const c = getCenter();
    originX = c.x; originY = c.y;
    applyJoy(touch.clientX - originX, touch.clientY - originY);
  }

  function onMove(e){
    if(!active || _ctrlMode !== 'joy') return;
    e.preventDefault();
    const touch = e.touches ? e.touches[0] : e;
    applyJoy(touch.clientX - originX, touch.clientY - originY);
  }

  function onEnd(e){
    e.preventDefault();
    active = false;
    knob.style.transform = 'translate(-50%, -50%)';
    S.keys[keyBindings.up]    = false;
    S.keys[keyBindings.down]  = false;
    S.keys[keyBindings.left]  = false;
    S.keys[keyBindings.right] = false;
    setArrow('up',false); setArrow('dn',false);
    setArrow('lt',false); setArrow('rt',false);
  }

  zone.addEventListener('touchstart',  onStart, {passive:false});
  zone.addEventListener('touchmove',   onMove,  {passive:false});
  zone.addEventListener('touchend',    onEnd,   {passive:false});
  zone.addEventListener('touchcancel', onEnd,   {passive:false});
  zone.addEventListener('mousedown',   onStart);
  window.addEventListener('mousemove', e=>{ if(active) onMove(e); });
  window.addEventListener('mouseup',   e=>{ if(active) onEnd(e); });
})();

// ── D-pad ─────────────────────────────────────────────────────────────
(function(){
  const MAP = {dpUp:'up', dpDn:'down', dpLt:'left', dpRt:'right'};

  function setDpadKey(binding, val){
    S.keys[keyBindings[binding]] = val;
    const elId = Object.keys(MAP).find(k => MAP[k] === binding);
    if(elId){
      const el = document.getElementById(elId);
      if(el) el.classList.toggle('pressed', val);
    }
  }

  const _dpTouches = {};

  function dpStart(e){
    e.preventDefault();
    if(_ctrlMode !== 'dpad') return;
    for(const t of e.changedTouches){
      const el = document.elementFromPoint(t.clientX, t.clientY);
      if(!el) continue;
      const dir = MAP[el.id];
      if(dir){ _dpTouches[t.identifier] = dir; setDpadKey(dir, true); }
    }
  }

  function dpEnd(e){
    e.preventDefault();
    for(const t of e.changedTouches){
      const dir = _dpTouches[t.identifier];
      if(dir){ delete _dpTouches[t.identifier]; setDpadKey(dir, false); }
    }
  }

  function dpMove(e){
    e.preventDefault();
    if(_ctrlMode !== 'dpad') return;
    for(const t of e.changedTouches){
      const el = document.elementFromPoint(t.clientX, t.clientY);
      const newDir = el ? MAP[el.id] : null;
      const oldDir = _dpTouches[t.identifier];
      if(oldDir !== newDir){
        if(oldDir) setDpadKey(oldDir, false);
        if(newDir){ _dpTouches[t.identifier] = newDir; setDpadKey(newDir, true); }
        else delete _dpTouches[t.identifier];
      }
    }
  }

  const dpad = document.getElementById('dpad');
  dpad.addEventListener('touchstart',  dpStart, {passive:false});
  dpad.addEventListener('touchmove',   dpMove,  {passive:false});
  dpad.addEventListener('touchend',    dpEnd,   {passive:false});
  dpad.addEventListener('touchcancel', dpEnd,   {passive:false});
  Object.keys(MAP).forEach(id=>{
    const el = document.getElementById(id);
    if(!el) return;
    el.addEventListener('mousedown', ()=>setDpadKey(MAP[id], true));
    el.addEventListener('mouseup',   ()=>setDpadKey(MAP[id], false));
    el.addEventListener('mouseleave',()=>setDpadKey(MAP[id], false));
  });
})();

_applyCtrlMode();

// ═══════════════════════════════════════════════════════════ ACTION LATCH
let _actionLatch = false;

function latchAction(){
  _actionLatch = true;
}

(function(){
  const btn = document.getElementById("actionBtn");
  btn.addEventListener("touchstart",  e=>{ e.preventDefault(); latchAction(); btn.style.transform="scale(.88)"; }, {passive:false});
  btn.addEventListener("touchend",    e=>{ e.preventDefault(); btn.style.transform=""; }, {passive:false});
  btn.addEventListener("touchcancel", e=>{ e.preventDefault(); btn.style.transform=""; }, {passive:false});
  btn.addEventListener("mousedown",  ()=>{ latchAction(); btn.style.transform="scale(.88)"; });
  btn.addEventListener("mouseup",    ()=>{ btn.style.transform=""; });
})();

document.getElementById("mobileControls").style.display =
  window.matchMedia("(pointer:coarse)").matches ? "flex" : "none";
// Re-measure after controls appear so canvas height is correct
if(window.matchMedia("(pointer:coarse)").matches){
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if(S.lastState) renderGame(S.lastState);
  }));
}

// ═══════════════════════════════════════════════════════════ CANVAS
const canvas = document.getElementById("gameCanvas");
const ctx    = canvas.getContext("2d");

// ── 500 lbs weight sprite ─────────────────────────────────────────────
const _weightImg = new Image();
_weightImg.src = "sounds/500-ibs.png";

function resizeCanvas(W, H){
  const isFS     = _isFullscreen();
  const isMobile = window.matchMedia("(pointer:coarse)").matches;

  // Use visualViewport as primary source on mobile (most reliable for accounting for URL bar, etc.)
  let vvW, vvH;
  
  if(window.visualViewport){
    vvW = Math.round(window.visualViewport.width);
    vvH = Math.round(window.visualViewport.height);
  } else {
    vvW = window.innerWidth;
    vvH = window.innerHeight;
  }

  // On mobile, use the visual viewport directly for maximum space
  const bodyW = isMobile ? vvW : (document.documentElement.clientWidth || vvW);

  const hudEl = document.querySelector(".game-hud");
  const mobEl = document.getElementById("mobileControls");

  const hudH = hudEl ? Math.ceil(hudEl.getBoundingClientRect().height) || 44 : 44;
  const mobH = (mobEl && mobEl.style.display !== "none")
    ? Math.ceil(mobEl.getBoundingClientRect().height) || 0
    : 0;

  const usedH = hudH + mobH;

  // Width: use full available width on mobile
  const mxW = isFS ? vvW : (isMobile ? vvW : Math.min(bodyW, 800));
  
  // Height: maximize usage on mobile (no extra padding), use remaining space
  const pad = isFS ? 0 : (isMobile ? 0 : 8);
  const mxH = Math.max(vvH - usedH - pad, 80);

  // Calculate scale to fit within available space while maintaining aspect ratio
  const sc = Math.min(mxW / W, mxH / H);

  // Apply the calculated dimensions
  canvas.style.width  = Math.floor(W * sc) + "px";
  canvas.style.height = Math.floor(H * sc) + "px";
  canvas.width  = W;   // internal resolution
  canvas.height = H;   // internal resolution
  canvas._scale = sc;
}
const _fsSupported = !!(
  document.documentElement.requestFullscreen ||
  document.documentElement.webkitRequestFullscreen ||
  document.documentElement.mozRequestFullScreen
);

let _fakeFS = false;

function toggleFullscreen(){
  const el   = document.getElementById("gameScreen");
  const inFS = _isFullscreen();

  if(!inFS){
    if(_fsSupported){
      const req = el.requestFullscreen       ||
                  el.webkitRequestFullscreen  ||
                  el.mozRequestFullScreen;
      if(req){
        req.call(el).catch(() => {
          const req2 = document.documentElement.requestFullscreen ||
                       document.documentElement.webkitRequestFullscreen;
          if(req2) req2.call(document.documentElement).catch(_enterFakeFS);
        });
        return;
      }
    }
    _enterFakeFS();
  } else {
    if(_fakeFS){
      _exitFakeFS();
    } else {
      const exit = document.exitFullscreen        ||
                   document.webkitExitFullscreen  ||
                   document.mozCancelFullScreen   ||
                   document.msExitFullscreen;
      if(exit) exit.call(document);
    }
  }
}

function _isFullscreen(){
  return _fakeFS || !!(
    document.fullscreenElement ||
    document.webkitFullscreenElement ||
    document.mozFullScreenElement
  );
}

function _enterFakeFS(){
  _fakeFS = true;
  document.getElementById("gameScreen").classList.add("fake-fs");
  document.body.classList.add("fs-open");
  window.scrollTo(0, 0);
  _updateFsBtn();
  setTimeout(() => { if(S.lastState) renderGame(S.lastState); }, 60);
}

function _exitFakeFS(){
  _fakeFS = false;
  document.getElementById("gameScreen").classList.remove("fake-fs");
  document.body.classList.remove("fs-open");
  _updateFsBtn();
  setTimeout(() => { if(S.lastState) renderGame(S.lastState); }, 60);
}

const _SVG_EXPAND  = '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>';
const _SVG_COMPRESS= '<polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="10" y1="14" x2="3" y2="21"/><line x1="21" y1="3" x2="14" y2="10"/>';

function _updateFsBtn(){
  const inFS = _isFullscreen();
  const icon = document.getElementById("fsIcon");
  const btn  = document.getElementById("fsBtn");
  if(icon) icon.innerHTML = inFS ? _SVG_COMPRESS : _SVG_EXPAND;
  if(btn)  btn.title      = inFS ? "Exit fullscreen" : "Fullscreen";
  if(S.lastState) renderGame(S.lastState);
}

document.addEventListener("fullscreenchange",       _updateFsBtn);
document.addEventListener("webkitfullscreenchange", _updateFsBtn);
document.addEventListener("mozfullscreenchange",    _updateFsBtn);

document.addEventListener("keydown", e => {
  if(e.key === "Escape" && _fakeFS){ _exitFakeFS(); return; }
  if((e.key === "f" || e.key === "F")){
    const gs = document.getElementById("gameScreen");
    if(gs && gs.classList.contains("active")) toggleFullscreen();
  }
});

let _resizeTimer = null;
function _onViewportChange(){
  if(_resizeTimer) clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(() => {
    _resizeTimer = null;
    // Double rAF: first frame lets browser reflow (URL bar / keyboard resize),
    // second frame ensures getBoundingClientRect values are fully settled.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if(S.lastState) renderGame(S.lastState);
    }));
  }, 50);
}

if(window.visualViewport){
  window.visualViewport.addEventListener("resize",  _onViewportChange);
  window.visualViewport.addEventListener("scroll",  _onViewportChange);
} else {
  window.addEventListener("resize",             _onViewportChange);
}
window.addEventListener("orientationchange", () => {
  setTimeout(_onViewportChange, 200);
});

// Watch HUD and mobile-controls for height changes (URL-bar hide/show, etc.)
if(typeof ResizeObserver !== "undefined"){
  const _layoutObserver = new ResizeObserver(_onViewportChange);
  const _hudEl  = document.querySelector(".game-hud");
  const _mobCtl = document.getElementById("mobileControls");
  if(_hudEl)  _layoutObserver.observe(_hudEl);
  if(_mobCtl) _layoutObserver.observe(_mobCtl);
}

function renderGame(s){
  const ps = applyPersonalKit(s);   // apply local player's kit remapping
  resizeCanvas(ps.arena_w||800, ps.arena_h||600);
  if(ps.game_type==="crash_bash") renderCrashBash(ps);
  if(ps.game_type==="tnt_battle") renderTntBattle(ps);
}

function rr(x,y,w,h,r){
  ctx.beginPath();
  ctx.moveTo(x+r,y);
  ctx.lineTo(x+w-r,y); ctx.arcTo(x+w,y,x+w,y+r,r);
  ctx.lineTo(x+w,y+h-r); ctx.arcTo(x+w,y+h,x+w-r,y+h,r);
  ctx.lineTo(x+r,y+h); ctx.arcTo(x,y+h,x,y+h-r,r);
  ctx.lineTo(x,y+r); ctx.arcTo(x,y,x+r,y,r);
  ctx.closePath();
}

function playerName(p){
  if(p.name) return p.name;           // server provides the real name (human or bot)
  return S.pidToName[p.player_id] || `P${p.number}`;
}

function drawPlayerLabel(p){
  const sc   = canvas._scale || 1;
  // Keep label readable: minimum effective 10px on screen, max 13px
  const size = Math.round(Math.min(13, Math.max(10, 11 / sc)));
  ctx.fillStyle = "#000";
  ctx.font      = `bold ${size}px sans-serif`;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  const label = playerName(p);
  const pw = p.w || p.size;
  const ph = p.h || p.size;
  ctx.fillText(label, p.x + pw/2, p.y + ph/2);
}

function renderCrashBash(s){
  const W=s.arena_w, H=s.arena_h;
  ctx.fillStyle="#0a0a14"; ctx.fillRect(0,0,W,H);
  ctx.strokeStyle="#2a2a3d"; ctx.lineWidth=8; ctx.strokeRect(4,4,W-8,H-8);

  // Team mode: draw a slim coloured stripe along each team's two wall edges
  if(s.team_mode && s.teams){
    const TEAM_COLORS = {A:"#E05050", B:"#5080E0"};
    for(const [tid, team] of Object.entries(s.teams)){
      const tc = TEAM_COLORS[tid] || "#888";
      ctx.save();
      ctx.globalAlpha = 0.18;
      ctx.fillStyle   = tc;
      const stripe = 4;
      if(tid === "A"){
        ctx.fillRect(0, 0, W, stripe);        // top wall stripe
        ctx.fillRect(0, 0, stripe, H);        // left wall stripe
      } else {
        ctx.fillRect(0, H-stripe, W, stripe); // bottom wall stripe
        ctx.fillRect(W-stripe, 0, stripe, H); // right wall stripe
      }
      ctx.restore();
    }
  }

  for(const[pid,g] of Object.entries(s.goals||{})){
    const[gx,gy,gw,gh]=g.rect;
    ctx.fillStyle=g.color+"44"; ctx.fillRect(gx,gy,gw,gh);
    ctx.strokeStyle=g.color; ctx.lineWidth=3; ctx.strokeRect(gx,gy,gw,gh);
  }

  for(const[pid,p] of Object.entries(s.players)){
    const pw = p.w || p.size;
    const ph = p.h || p.size;
    ctx.globalAlpha = p.eliminated ? 0.22 : 1;
    ctx.fillStyle   = p.color;
    rr(p.x,p.y,pw,ph,5); ctx.fill();
    if(pid===S.playerId){
      ctx.strokeStyle="#fff"; ctx.lineWidth=2.5; rr(p.x,p.y,pw,ph,5); ctx.stroke();
    }
    ctx.globalAlpha=p.eliminated?0.3:1;
    drawPlayerLabel(p);
    ctx.fillStyle="#fff"; ctx.font="11px sans-serif";
    ctx.textAlign="center"; ctx.textBaseline="middle";
    const scoreVal = p.eliminated ? "OUT" : p.score;
    const gap = 10;
    if(p.side === "top"){
      ctx.fillText(scoreVal, p.x+pw/2, p.y - gap);
    } else if(p.side === "bottom"){
      ctx.fillText(scoreVal, p.x+pw/2, p.y + ph + gap);
    } else if(p.side === "left"){
      ctx.textAlign = "right";
      ctx.fillText(scoreVal, p.x - gap, p.y + ph/2);
    } else {
      ctx.textAlign = "left";
      ctx.fillText(scoreVal, p.x + pw + gap, p.y + ph/2);
    }
    ctx.textAlign="center"; ctx.textBaseline="alphabetic";
    ctx.globalAlpha=1;
  }

  for(const b of s.balls||[]){
    const cx=b.x+b.size/2, cy=b.y+b.size/2;
    ctx.beginPath(); ctx.arc(cx,cy,b.size/2,0,Math.PI*2);
    ctx.fillStyle="#e0e0e0"; ctx.fill();
    ctx.beginPath(); ctx.arc(cx-b.size*.15,cy-b.size*.15,b.size*.17,0,Math.PI*2);
    ctx.fillStyle="#bbb"; ctx.fill();
  }
}

function renderTntBattle(s){
  const W=s.arena_w, H=s.arena_h;
  ctx.fillStyle="#12122a"; ctx.fillRect(0,0,W,H);
  ctx.strokeStyle="#c0c0c0"; ctx.lineWidth=3; ctx.strokeRect(8,8,W-16,H-16);

  for(const c of s.pickup_crates||[]){
    ctx.fillStyle="#a06428"; rr(c.x,c.y,c.size,c.size,4); ctx.fill();
    ctx.strokeStyle="#8c5020"; ctx.lineWidth=1.5; rr(c.x,c.y,c.size,c.size,4); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(c.x+c.size/2,c.y); ctx.lineTo(c.x+c.size/2,c.y+c.size);
    ctx.moveTo(c.x,c.y+c.size/2); ctx.lineTo(c.x+c.size,c.y+c.size/2);
    ctx.stroke();
    const glow=Math.abs(Math.sin((S.lastState?.tick||0)*.05))*.28+.08;
    ctx.beginPath(); ctx.arc(c.x+c.size/2,c.y+c.size/2,c.size/2+4,0,Math.PI*2);
    ctx.fillStyle=`rgba(200,150,60,${glow})`; ctx.fill();
  }

  for(const f of s.health_fruits||[]){
    const pulse = Math.abs(Math.sin((S.lastState?.tick||0)*.07))*.3+.1;
    ctx.beginPath(); ctx.arc(f.x+f.size/2, f.y+f.size/2, f.size/2+5, 0, Math.PI*2);
    ctx.fillStyle=`rgba(80,200,100,${pulse})`; ctx.fill();
    ctx.beginPath(); ctx.arc(f.x+f.size/2, f.y+f.size/2, f.size/2, 0, Math.PI*2);
    ctx.fillStyle="#50C864"; ctx.fill();
    ctx.strokeStyle="#2a8c3a"; ctx.lineWidth=2; ctx.stroke();
    const lx=f.x+f.size/2, ly=f.y+2;
    ctx.beginPath(); ctx.ellipse(lx+4, ly-2, 5, 3, Math.PI/4, 0, Math.PI*2);
    ctx.fillStyle="#3db83d"; ctx.fill();
    ctx.strokeStyle="#fff"; ctx.lineWidth=2;
    ctx.beginPath();
    ctx.moveTo(f.x+f.size/2, f.y+4); ctx.lineTo(f.x+f.size/2, f.y+f.size-4);
    ctx.moveTo(f.x+4, f.y+f.size/2); ctx.lineTo(f.x+f.size-4, f.y+f.size/2);
    ctx.stroke();
  }

  // ── 500 lbs weight (arena pickup) ──────────────────────────────────
  if(s.weight){
    const wt = s.weight;
    const tick = S.lastState?.tick || 0;
    const wx = wt.x, wy = wt.y, ws = wt.size;
    const wcx = wx + ws/2, wcy = wy + ws/2;

    // Danger-red pulsing aura on the ground
    const pulse = Math.abs(Math.sin(tick * 0.06)) * 0.35 + 0.15;
    const grad = ctx.createRadialGradient(wcx, wcy, 0, wcx, wcy, ws);
    grad.addColorStop(0, `rgba(255,60,60,${pulse})`);
    grad.addColorStop(1, "rgba(255,60,60,0)");
    ctx.beginPath(); ctx.arc(wcx, wcy, ws, 0, Math.PI*2);
    ctx.fillStyle = grad; ctx.fill();

    // Weight image
    if(_weightImg.complete && _weightImg.naturalWidth){
      ctx.drawImage(_weightImg, wx, wy, ws, ws);
    } else {
      // Fallback: simple grey rect until image loads
      ctx.fillStyle="#666"; ctx.fillRect(wx, wy, ws, ws);
    }

    // Warning exclamation above it
    const bounce = Math.sin(tick * 0.1) * 3;
    ctx.font = "bold 14px sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "bottom";
    ctx.fillText("⚠️", wcx, wy - 6 + bounce);
  }

  for(const tc of s.thrown_crates||[]){
    ctx.save();
    ctx.translate(tc.x+tc.size/2, tc.y+tc.size/2);
    ctx.rotate(tc.rotation*Math.PI/180);
    ctx.fillStyle="#b47832"; rr(-tc.size/2,-tc.size/2,tc.size,tc.size,3); ctx.fill();
    ctx.strokeStyle="#8c5020"; ctx.lineWidth=1.5; rr(-tc.size/2,-tc.size/2,tc.size,tc.size,3); ctx.stroke();
    ctx.restore();
  }

  for(const ex of s.explosions||[]){
    const r = ex.radius*(1-ex.frac);
    if(r<=0) continue;
    for(let i=2;i>=0;i--){
      const gr=r+i*10;
      const grd=ctx.createRadialGradient(ex.x,ex.y,0,ex.x,ex.y,gr);
      grd.addColorStop(0,`rgba(255,200,50,${ex.frac/(i+1)})`);
      grd.addColorStop(.5,`rgba(255,100,20,${ex.frac/(i+2)})`);
      grd.addColorStop(1,"rgba(200,50,0,0)");
      ctx.beginPath(); ctx.arc(ex.x,ex.y,gr,0,Math.PI*2);
      ctx.fillStyle=grd; ctx.fill();
    }
  }

  for(const[pid,p] of Object.entries(s.players)){
    ctx.globalAlpha=p.eliminated?.18:1;
    ctx.fillStyle=p.color; rr(p.x,p.y,p.size,p.size,6); ctx.fill();
    if(pid===S.playerId){
      ctx.strokeStyle="#fff"; ctx.lineWidth=2.5; rr(p.x,p.y,p.size,p.size,6); ctx.stroke();
    }
    // Team mode: draw a glowing aura around teammates (not self, not enemies)
    if(s.team_mode && s.player_team && pid !== S.playerId){
      const myTeam   = s.player_team[S.playerId];
      const theirTeam = s.player_team[pid];
      if(myTeam && myTeam === theirTeam && !p.eliminated){
        ctx.save();
        ctx.strokeStyle = "#50ff90";
        ctx.lineWidth   = 2.5;
        ctx.globalAlpha = 0.55;
        rr(p.x-3, p.y-3, p.size+6, p.size+6, 9);
        ctx.stroke();
        ctx.restore();
      }
    }
    if(!p.eliminated){
      const hpW=p.size*(p.hp/p.max_hp);
      const hpc=p.hp>60?"#3db83d":p.hp>25?"#d4b43c":"#d43c3c";
      ctx.fillStyle="#333"; ctx.fillRect(p.x,p.y-10,p.size,6);
      ctx.fillStyle=hpc;    ctx.fillRect(p.x,p.y-10,hpW,6);
      if(p.held_crate){ ctx.fillStyle="#a06428"; ctx.fillRect(p.x+p.size/2-8,p.y-24,16,14); }
      if(!p.held_crate && p.melee_ready){
        ctx.font="11px sans-serif"; ctx.textAlign="center";
        ctx.globalAlpha=0.75;
        ctx.fillText("👊",p.x+p.size/2,p.y-26);
        ctx.globalAlpha=1;
      }

      // ── 500 lbs weight above head ──────────────────────────────
      if(p.has_weight){
        const HOLD  = 600;  // must match server WEIGHT_HOLD_TICKS
        const timer = p.weight_timer || 0;
        const frac  = timer / HOLD;        // 1.0 → just picked up, 0.0 → about to die
        const tick  = S.lastState?.tick || 0;

        // Weight descends from 80px above head down to 4px above head
        const maxDrop = 76;
        const dropY   = p.y - 16 - maxDrop * (1 - frac);   // starts high, comes down
        const pcx     = p.x + p.size / 2;
        const ws      = 32;   // weight sprite size when above head

        // Shadow on player grows as weight descends
        const shadowR  = 14 + (1 - frac) * 10;
        const shadowA  = 0.15 + (1 - frac) * 0.35;
        ctx.save();
        ctx.beginPath();
        ctx.ellipse(pcx, p.y + p.size - 4, shadowR, shadowR * 0.4, 0, 0, Math.PI*2);
        ctx.fillStyle = `rgba(0,0,0,${shadowA})`;
        ctx.fill();
        ctx.restore();

        // Danger chain / rope connecting weight to player
        ctx.save();
        ctx.setLineDash([3, 4]);
        ctx.strokeStyle = `rgba(255,80,80,${0.3 + (1-frac)*0.5})`;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(pcx, dropY + ws);
        ctx.lineTo(pcx, p.y - 14);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();

        // Weight sprite above head
        ctx.save();
        // Urgent shake when < 20% time remaining
        let shakeX = 0;
        if(frac < 0.2){
          shakeX = (Math.random()-0.5) * (1 - frac/0.2) * 5;
        }
        if(_weightImg.complete && _weightImg.naturalWidth){
          ctx.drawImage(_weightImg, pcx - ws/2 + shakeX, dropY, ws, ws);
        } else {
          ctx.fillStyle="#666";
          ctx.fillRect(pcx - ws/2 + shakeX, dropY, ws, ws);
        }
        ctx.restore();

        // Countdown ring around player
        const arcFrac = timer / HOLD;
        const startA  = -Math.PI / 2;
        const endA    = startA + Math.PI * 2 * arcFrac;
        const ringR   = p.size / 2 + 7;
        // background ring
        ctx.save();
        ctx.beginPath();
        ctx.arc(pcx, p.y + p.size/2, ringR, 0, Math.PI*2);
        ctx.strokeStyle = "rgba(80,80,80,0.5)";
        ctx.lineWidth = 3; ctx.stroke();
        // foreground ring (colour shifts red as time runs out)
        const r = Math.round(255);
        const g = Math.round(200 * frac);
        ctx.beginPath();
        ctx.arc(pcx, p.y + p.size/2, ringR, startA, endA);
        ctx.strokeStyle = `rgb(${r},${g},0)`;
        ctx.lineWidth = 3; ctx.stroke();
        ctx.restore();

        // Seconds remaining label
        const secsLeft = Math.ceil(timer / 60);
        ctx.save();
        ctx.font = `bold 11px sans-serif`;
        ctx.textAlign = "center"; ctx.textBaseline = "bottom";
        ctx.fillStyle = frac < 0.3 ? "#ff4444" : "#ffcc00";
        ctx.fillText(`${secsLeft}s`, pcx, p.y - 16);
        ctx.restore();
      }
    }
    ctx.globalAlpha=1;
    drawPlayerLabel(p);
  }

  for(const ev of s.hit_events||[]){
    if(!ev.melee) continue;
    ctx.font="bold 20px sans-serif"; ctx.textAlign="center";
    ctx.fillText("💥",ev.x, ev.y-8);
  }
}

function updateHud(s){
  const ps = applyPersonalKit(s);

  if(s.team_mode && s.teams){
    // Team mode HUD: group players by team with a coloured bracket
    const TEAM_COLORS = {A:"#E05050", B:"#5080E0"};
    let html = "";
    for(const [tid, team] of Object.entries(s.teams)){
      const tc = TEAM_COLORS[tid] || team.color || "#888";
      const label = tid === "A" ? "🔴" : "🔵";
      // Shared score for crash_bash (team pool) or HP sum for tnt
      let teamScore = "";
      if(s.game_type === "crash_bash" && team.score !== undefined){
        teamScore = `<span class="hud-score" style="color:${tc}">${team.score}pts</span>`;
      }
      const memberHtml = (team.member_ids || []).map(pid => {
        const p    = ps.players[pid]; if(!p) return "";
        const val  = s.game_type === "crash_bash" ? p.score : p.hp + " HP";
        const elim = p.eliminated ? " hud-elim" : "";
        const bot  = p.is_bot ? `<span class="hud-bot">BOT</span>` : "";
        const name = playerName(p);
        return `<div class="hud-player${elim}">
          <div class="hud-color" style="background:${p.color}"></div>
          <span>${name}${bot}</span>
          <span class="hud-score">${p.eliminated ? "OUT" : val}</span>
        </div>`;
      }).join("");
      html += `<div style="display:flex;align-items:center;gap:4px;
                    border-left:3px solid ${tc};padding-left:6px;margin-right:6px">
        <span style="font-size:.75rem;color:${tc};font-weight:800;margin-right:2px">${label}</span>
        ${memberHtml}${teamScore}
      </div>`;
    }
    document.getElementById("hudPlayers").innerHTML = html;
  } else {
    document.getElementById("hudPlayers").innerHTML =
      Object.values(ps.players).map(p=>{
        const val  = s.game_type==="crash_bash" ? p.score : p.hp+" HP";
        const elim = p.eliminated?" hud-elim":"";
        const bot  = p.is_bot===true ? `<span class="hud-bot">BOT</span>` : "";
        const name = playerName(p);
        return `<div class="hud-player${elim}">
          <div class="hud-color" style="background:${p.color}"></div>
          <span>${name}${bot}</span>
          <span class="hud-score">${p.eliminated?"OUT":val}</span>
        </div>`;
      }).join("");
  }
}

function showGameOver(s){
  const ps = applyPersonalKit(s);
  const winP   = s.winner && ps.players[s.winner];
  const isHost = S.playerId === S.hostId;

  // Winner announcement — use server-provided winner_display if present
  let winnerText;
  if(s.winner_display){
    winnerText = `🎉 ${s.winner_display}`;
  } else if(winP){
    winnerText = `🎉 ${playerName(winP)} wins!`;
  } else {
    winnerText = "Draw";
  }
  document.getElementById("winnerName").textContent = winnerText;

  // Score table — in team mode show team grouping
  let rows = "";
  if(s.team_mode && s.teams){
    const TEAM_COLORS = {A:"#E05050", B:"#5080E0"};
    for(const [tid, team] of Object.entries(s.teams)){
      const tc    = TEAM_COLORS[tid] || team.color || "#888";
      const label = tid === "A" ? "🔴" : "🔵";
      rows += `<tr><td colspan="3" style="font-weight:700;color:${tc};padding-top:8px">${label} Team ${tid}</td></tr>`;
      for(const pid of (team.member_ids || [])){
        const p   = ps.players[pid];
        if(!p) continue;
        const val = s.game_type === "crash_bash" ? `${p.score} pts` : `${p.hp} HP`;
        const you = pid === S.playerId ? " (you)" : "";
        const bot = p.is_bot ? " 🤖" : "";
        rows += `<tr>
          <td style="color:${p.color}">●</td>
          <td style="color:${p.color}">${playerName(p)}${bot}${you}</td>
          <td style="text-align:right;font-weight:700">${val}</td>
        </tr>`;
      }
    }
  } else {
    rows = Object.values(ps.players)
      .sort((a,b)=> s.game_type==="crash_bash" ? b.score-a.score : b.hp-a.hp)
      .map((p,i)=>{
        const medal=["\uD83E\uDD47","\uD83E\uDD48","\uD83E\uDD49",""][i]||"";
        const val  = s.game_type==="crash_bash" ? `${p.score} pts` : `${p.hp} HP`;
        const you  = p.player_id===S.playerId?" (you)":"";
        const bot  = p.is_bot?" 🤖":"";
        return `<tr>
          <td>${medal}</td>
          <td style="color:${p.color}">${playerName(p)}${bot}${you}</td>
          <td style="text-align:right;font-weight:700">${val}</td>
        </tr>`;
      }).join("");
  }
  document.getElementById("scoreTable").innerHTML = rows;

  const actions = document.getElementById("rematchActions");
  if(isHost && S.roomId){
    actions.innerHTML = `
      <button class="btn btn-primary" onclick="hostRematch()">🔄 Play Again</button>
      <button class="btn btn-secondary" onclick="goLobby()">Lobby</button>`;
  } else if(S.roomId){
    actions.innerHTML = `
      <div class="rematch-waiting">Waiting for host to start a rematch…</div>
      <button class="btn btn-secondary" onclick="goLobby()">Leave</button>`;
  } else {
    actions.innerHTML = `
      <button class="btn btn-primary" onclick="goLobby()">Lobby</button>`;
  }

  document.getElementById("gameOverlay").classList.add("show");
  _speakGameOver(s);
}

async function hostRematch(){
  if(!S.roomId) return;
  const btn = document.querySelector("#rematchActions .btn-primary");
  if(btn){ btn.disabled = true; btn.textContent = "Starting…"; }

  try{
    const res = await fetch(`/api/rooms/${S.roomId}/rematch`,{
      method:"POST",
      headers:{"X-Player-Id": S.playerId},
    });
    if(!res.ok){
      const d = await res.json();
      throw new Error(d.detail || "Could not start rematch");
    }
  }catch(e){
    alert(e.message);
    if(btn){ btn.disabled = false; btn.textContent = "🔄 Play Again"; }
  }
}

function playAgain(){
  goLobby();
}

function goLobby(){
  document.getElementById("gameOverlay").classList.remove("show");
  stopInputLoop();
  if(S.sse){ S.sse.close(); S.sse=null; }
  S.roomId=null; S.hostId=null; S.pidToName={};
  localStorage.removeItem("roomId");
  localStorage.removeItem("gameType");
  resetReadyBtn();
  showScreen("lobbyScreen");
}

function leaveGame(){
  stopInputLoop();
  if(S.sse){ S.sse.close(); S.sse=null; }
  S.roomId=null; S.hostId=null;
  localStorage.removeItem("roomId");
  localStorage.removeItem("gameType");
  document.getElementById("gameOverlay").classList.remove("show");
  resetReadyBtn();
  showScreen("lobbyScreen");
}

initSlots();
// Wire up team-mode checkbox so the preview panel shows/hides live
(function(){
  const chk  = document.getElementById("teamModeCheck");
  const prev = document.getElementById("teamPreview");
  if(chk && prev){
    chk.addEventListener("change", ()=>{
      prev.style.display = chk.checked ? "" : "none";
    });
  }
})();
const _savedColor = localStorage.getItem("playerColor") || COLOR_PALETTE[0];
S.selectedColor = _savedColor;
initColorPicker("createColorSwatches", "playerColor");
initColorPicker("joinColorSwatches",   "playerColor");

// ═══════════════════════════════════════════════════════════ REFRESH RECONNECT
// If the player had an active room when they refreshed, silently rejoin it.
(async function tryReconnect(){
  const savedRoom = localStorage.getItem("roomId");
  const savedType = localStorage.getItem("gameType");
  if(!savedRoom) return;

  // Restore gameType so enterRoom labels the waiting screen correctly
  if(savedType) S.gameType = savedType;

  try{
    // First check whether the room still exists and we're still in it
    const res = await fetch(`/api/rooms/${savedRoom}/join`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Player-Id": S.playerId },
      body: JSON.stringify({
        player_name: localStorage.getItem("playerName") || "Player",
        player_color: S.selectedColor || COLOR_PALETTE[0],
        player_id: S.playerId,
      }),
    });
    if(!res.ok){
      // Room gone or we were never in it — clean up and stay on lobby
      localStorage.removeItem("roomId");
      localStorage.removeItem("gameType");
      return;
    }
    const data = await res.json();
    S.roomId = savedRoom;
    if(data.host_id) S.hostId = data.host_id;
    if(data.slots) data.slots.forEach(sl => { if(sl.pid && sl.name) S.pidToName[sl.pid] = sl.name; });
    enterRoom(savedRoom, data.status === "playing");
  } catch(_){
    // Network error — just stay on lobby, don't block startup
    localStorage.removeItem("roomId");
    localStorage.removeItem("gameType");
  }
})();

// ═══════════════════════════════════════════════════════════════════════
//  app_additions.js
//
//  Append this block to the END of app.js (after the last line of the
//  existing file).  It adds:
//
//   1. Mode select  (chooseMode)
//   2. Local lobby  (local game-type selector, player count, bot count,
//                    per-player row rendering)
//   3. Controls.init() wiring + openOnlineKeyConfig()
//   4. LocalMode integration  (startLocalGame, stop on goLobby, etc.)
//   5. Patches to startInputLoop / stopInputLoop to delegate to LocalMode
//      when local mode is active.
//   6. goLobby() override that returns to modeScreen correctly.
//
//  ALSO: change the first screen shown at startup from "lobbyScreen"
//  to "modeScreen".  Find the lines at the bottom of app.js that call
//  showScreen("lobbyScreen") on init and replace them — or simply let
//  index.html start with modeScreen.active (already done in new index.html).
// ═══════════════════════════════════════════════════════════════════════

// ── Boot Controls ────────────────────────────────────────────────────
Controls.init();

// ── Mode Select ───────────────────────────────────────────────────────
function chooseMode(mode) {
  _cdVoice.unlock();
  if (mode === 'online') {
    showScreen('lobbyScreen');
  } else {
    showScreen('localLobbyScreen');
    renderLocalLobby();
  }
}

// ── Local Lobby State ─────────────────────────────────────────────────
let _localGameType  = 'crash_bash';
let _localHumans    = 2;
let _localBots      = 0;
let _localTeamMode  = false;

// Per-player name and color for local lobby (indexed 0-3)
const LOCAL_DEFAULT_COLORS = ["#DC5050", "#5050DC", "#50C864", "#DCDC50"];
let _localPlayerNames  = ["Player 1", "Player 2", "Player 3", "Player 4"];
let _localPlayerColors = [...LOCAL_DEFAULT_COLORS];

// Load persisted local names/colors
(function _loadLocalProfiles() {
  try {
    const raw = localStorage.getItem("localPlayerProfiles");
    if (raw) {
      const saved = JSON.parse(raw);
      saved.forEach((p, i) => {
        if (p.name)  _localPlayerNames[i]  = p.name;
        if (p.color) _localPlayerColors[i] = p.color;
      });
    }
  } catch(_) {}
})();

function _saveLocalProfiles() {
  const data = _localPlayerNames.map((name, i) => ({ name, color: _localPlayerColors[i] }));
  localStorage.setItem("localPlayerProfiles", JSON.stringify(data));
}

function localSelectGame(type, el) {
  _localGameType = type;
  document.querySelectorAll('#localLobbyScreen .game-card').forEach(c => c.classList.remove('sel'));
  el.classList.add('sel');
}

function setLocalHumanCount(n) {
  _localHumans = n;
  // Clamp bots so total <= 4
  const maxBots = Math.min(2, 4 - n);
  if (_localBots > maxBots) _localBots = maxBots;
  document.querySelectorAll('#localHumanCountRow .local-count-btn').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.n) === n);
  });
  _syncLocalBotBtns();
  _syncLocalTeamMode();
  renderLocalLobby();
}

function setLocalBotCount(n) {
  // Ensure total stays <= 4
  const maxBots = Math.min(2, 4 - _localHumans);
  _localBots = Math.min(n, maxBots);
  _syncLocalBotBtns();
  _syncLocalTeamMode();
  renderLocalLobby();
}

function _syncLocalBotBtns() {
  const maxBots = Math.min(2, 4 - _localHumans);
  document.querySelectorAll('#localBotCountRow .local-count-btn').forEach(b => {
    const v = parseInt(b.dataset.n);
    b.disabled = (v > maxBots);
    b.style.opacity = (v > maxBots) ? '.3' : '';
    b.classList.toggle('active', v === _localBots);
  });
  const diffSec = document.getElementById('localBotDiffSection');
  if (diffSec) diffSec.style.display = _localBots > 0 ? '' : 'none';
}

function _syncLocalTeamMode() {
  const total = _localHumans + _localBots;
  const sec = document.getElementById('localTeamModeSection');
  if (!sec) return;
  sec.style.display = (total === 4) ? '' : 'none';
  if (total !== 4) _localTeamMode = false;
  const chk = document.getElementById('localTeamModeCheck');
  if (chk) chk.checked = _localTeamMode;
  _renderTeamBadges();
}

function toggleLocalTeamMode(checked) {
  _localTeamMode = checked;
  _renderTeamBadges();
}

function _renderTeamBadges() {
  // Show Team A / Team B badges on player rows when team mode is on
  const total = _localHumans + _localBots;
  if (!_localTeamMode || total !== 4) {
    document.querySelectorAll('.lpr-team-badge').forEach(el => el.textContent = '');
    return;
  }
  // Teams: slots 0,2 = Team A (Red); slots 1,3 = Team B (Blue)
  const labels = ['🔴 Team A', '🔵 Team B', '🔴 Team A', '🔵 Team B'];
  for (let i = 0; i < 4; i++) {
    const el = document.getElementById(`lpr_team_${i}`);
    if (el) el.textContent = i < _localHumans ? labels[i] : '';
  }
}

function renderLocalLobby() {
  Controls.setPlayerCount(_localHumans);
  const profiles = Controls.getProfiles();
  const grid = document.getElementById('localPlayersGrid');
  if (!grid) return;

  let html = '';
  // Human players
  for (let i = 0; i < _localHumans; i++) {
    const p      = profiles[i];
    const gpIdx  = p.gamepadIndex;
    const gpText = (gpIdx !== null && gpIdx !== undefined) ? `🎮 Controller ${gpIdx}` : '';
    const keyText = `${_fmtKeyShort(p.keys.up)} ${_fmtKeyShort(p.keys.down)} ${_fmtKeyShort(p.keys.left)} ${_fmtKeyShort(p.keys.right)} · Action: ${_fmtKeyShort(p.keys.action)}`;
    const savedName  = _localPlayerNames[i]  || `Player ${i + 1}`;
    const savedColor = _localPlayerColors[i] || LOCAL_DEFAULT_COLORS[i];

    // Build color swatch row for this player
    const swatches = COLOR_PALETTE.map(c =>
      `<span class="lpr-swatch${c === savedColor ? ' sel' : ''}"
             style="background:${c}"
             onclick="setLocalPlayerColor(${i},'${c}')"></span>`
    ).join('');

    html += `<div class="local-player-row active" id="lpr_${i}">
      <div class="lpr-num" style="color:${LOCAL_DEFAULT_COLORS[i]}">${i + 1}</div>
      <div class="lpr-info">
        <input class="lpr-name-input"
               id="lpr_name_${i}"
               type="text"
               maxlength="16"
               value="${savedName}"
               placeholder="Player ${i + 1}"
               oninput="setLocalPlayerName(${i}, this.value)" />
        <div class="lpr-swatches">${swatches}</div>
        <div class="lpr-keys">${keyText}</div>
        ${gpText ? `<div class="lpr-gp">${gpText}</div>` : ''}
        <span class="lpr-team-badge" id="lpr_team_${i}"></span>
      </div>
      <button class="lpr-config-btn" onclick="openPlayerConfig(${i})">⌨️ Config</button>
    </div>`;
  }
  // Bot slots
  for (let i = 0; i < _localBots; i++) {
    html += `<div class="local-player-row bot-row">
      <div class="lpr-num">🤖</div>
      <div class="lpr-info">
        <div class="lpr-name">Bot ${i + 1}</div>
        <div class="lpr-keys">Controlled by AI</div>
      </div>
    </div>`;
  }
  grid.innerHTML = html;
  _syncLocalBotBtns();
  _syncLocalTeamMode();
}

function setLocalPlayerName(i, val) {
  _localPlayerNames[i] = val.trim() || `Player ${i + 1}`;
  _saveLocalProfiles();
}

function setLocalPlayerColor(i, hex) {
  _localPlayerColors[i] = hex;
  _saveLocalProfiles();
  renderLocalLobby();
}

function _fmtKeyShort(k) {
  if (!k) return '?';
  if (k === ' ') return 'Spc';
  return k.replace('Arrow','').replace('Key','').slice(0,4) || k.slice(0,4);
}

function openPlayerConfig(playerIndex) {
  Controls.openConfig(playerIndex, () => {
    renderLocalLobby();
  });
}

// Online: open controls config for Player 1 only
function openOnlineKeyConfig() {
  Controls.openConfig(0, () => {
    // Sync legacy keyBindings from Controls profile so existing buildInput() still works
    keyBindings = { ...Controls.getProfiles()[0].keys };
    localStorage.setItem('kb', JSON.stringify(keyBindings));
    showKeyConfig(); // refresh legacy key screen if open
  });
}

// ── Start Local Game ──────────────────────────────────────────────────
async function startLocalGame() {
  const total = _localHumans + _localBots;
  if (total < 2) {
    document.getElementById('localError').textContent = 'Need at least 2 players total.';
    return;
  }
  if (total > 4) {
    document.getElementById('localError').textContent = 'Maximum 4 players total.';
    return;
  }
  document.getElementById('localError').textContent = '';
  const btn = document.getElementById('localStartBtn');
  btn.disabled = true;
  btn.textContent = 'Starting…';

  const diff = document.getElementById('localBotDiff')?.value || 'medium';
  S.gameType = _localGameType;

  const names  = _localPlayerNames.slice(0, _localHumans).map((n, i) => n.trim() || `Player ${i + 1}`);
  const colors = _localPlayerColors.slice(0, _localHumans);

  try {
    await LocalMode.start(_localGameType, _localHumans, _localBots, diff, names, colors, _localTeamMode);
  } catch(e) {
    document.getElementById('localError').textContent = e.message || 'Failed to start.';
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ Start Game';
  }
}

// ── Patch startInputLoop to handle local mode ─────────────────────────
//
// In local mode, LocalMode.startInputLoops() drives all human players.
// We hook into the game_starting SSE event path by overriding startInputLoop.
//
const _origStartInputLoop = startInputLoop;
const _origStopInputLoop  = stopInputLoop;

// Replace startInputLoop globally
window.startInputLoop = function() {
  if (LocalMode.isActive()) {
    LocalMode.startInputLoops();
  } else {
    _origStartInputLoop();
  }
};

window.stopInputLoop = function() {
  if (LocalMode.isActive()) {
    LocalMode.stopInputLoops();
  }
  _origStopInputLoop();
};

// ── Patch goLobby to stop local mode and return to modeScreen ─────────
const _origGoLobby = goLobby;
window.goLobby = function() {
  LocalMode.stop();
  _origGoLobby();
  // After goLobby shows lobbyScreen, redirect to modeScreen
  // (goLobby calls showScreen('lobbyScreen') internally, so we override)
  showScreen('modeScreen');
};

// ── Patch leaveGame similarly ─────────────────────────────────────────
const _origLeaveGame = leaveGame;
window.leaveGame = function() {
  LocalMode.stop();
  _origLeaveGame();
  showScreen('modeScreen');
};

// ── Sync Controls → legacy keyBindings on startup ─────────────────────
// The existing buildInput() in app.js reads from keyBindings (P1 only, online).
// We keep them in sync so online play uses Controls profile 0.
(function syncLegacyBindings() {
  const p0 = Controls.getProfiles()[0];
  if (p0) {
    keyBindings = { ...p0.keys };
    localStorage.setItem('kb', JSON.stringify(keyBindings));
  }
})();

// ── Override buildInput for online mode to use Controls ───────────────
// The original buildInput() reads from S.keys + keyBindings + _actionLatch.
// We augment it to also pick up gamepad input from Controls.getInput(0).
// IMPORTANT: mobile touch (joystick / d-pad / action button) writes into
// S.keys and the local _actionLatch — we must OR those in so touch still works.
const _origBuildInput = buildInput;
window.buildInput = function() {
  if (LocalMode.isActive()) {
    // Should not be called directly in local mode (each player has own loop)
    return Controls.getInput(0);
  }
  // Get keyboard + gamepad state from Controls profile 0
  const ctrl = Controls.getInput(0);

  // Get mobile touch state from legacy S.keys / keyBindings / _actionLatch
  const k = S.keys;
  const touchAction = !!k[keyBindings.action] || _actionLatch;
  _actionLatch = false; // consume latch

  // Merge: any source being true counts as pressed
  return {
    up:     ctrl.up    || !!k[keyBindings.up],
    down:   ctrl.down  || !!k[keyBindings.down],
    left:   ctrl.left  || !!k[keyBindings.left],
    right:  ctrl.right || !!k[keyBindings.right],
    action: ctrl.action || touchAction,
  };
};