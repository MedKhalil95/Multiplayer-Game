/**
 * controls.js  –  Unified input manager
 *
 * Handles keyboard and USB gamepad input for up to 4 players.
 * Works for both:
 *   • Online mode  – 1 player profile (the local human)
 *   • Local mode   – up to 4 player profiles, each with their own bindings
 *
 * Public API
 * ──────────
 *   Controls.init()
 *   Controls.getInput(playerIndex)  → { up, down, left, right, action }
 *   Controls.openConfig(playerIndex, onClose)
 *   Controls.getProfiles()          → array of profile objects
 *   Controls.setPlayerCount(n)      – activates n profiles (local mode)
 *   Controls.resetActionLatch(playerIndex)
 */

const Controls = (() => {

  // ── Default key sets ─────────────────────────────────────────────────
  const DEFAULT_PROFILES = [
    // Player 1 – arrow keys
    { name: "Player 1", keys: { up:"ArrowUp", down:"ArrowDown", left:"ArrowLeft", right:"ArrowRight", action:" " }, gamepadIndex: null },
    // Player 2 – WASD
    { name: "Player 2", keys: { up:"w", down:"s", left:"a", right:"d", action:"f" }, gamepadIndex: null },
    // Player 3 – IJKL
    { name: "Player 3", keys: { up:"i", down:"k", left:"j", right:"l", action:";" }, gamepadIndex: null },
    // Player 4 – numpad
    { name: "Player 4", keys: { up:"8", down:"5", left:"4", right:"6", action:"0" }, gamepadIndex: null },
  ];

  // Gamepad button/axis mappings (standard layout)
  const GP = {
    DPAD_UP: 12, DPAD_DOWN: 13, DPAD_LEFT: 14, DPAD_RIGHT: 15,
    A: 0, B: 1, X: 2, Y: 3,
    AXIS_LX: 0, AXIS_LY: 1,
    DEAD: 0.35,
  };

  // ── State ────────────────────────────────────────────────────────────
  const _keysDown    = {};   // key → bool
  const _actionLatch = [false, false, false, false];  // per player
  let   _profiles    = null; // loaded lazily
  let   _activeCount = 1;    // how many player profiles are active

  // ── Persistence ──────────────────────────────────────────────────────
  function _load() {
    if (_profiles) return;
    _reload();
  }

  function _reload() {
    try {
      const raw = localStorage.getItem("controlProfiles");
      if (raw) {
        const saved = JSON.parse(raw);
        // Start from defaults, then layer saved data on top.
        // Keys are merged separately so missing saved keys fall back to defaults.
        // gamepadIndex: saved value wins; coerce to number-or-null so strict
        // equality checks against gp.index (always a number) work correctly.
        _profiles = DEFAULT_PROFILES.map((def, i) => {
          const s = saved[i] || {};
          const rawGpIdx = s.gamepadIndex;
          const gamepadIndex = (rawGpIdx === null || rawGpIdx === undefined)
            ? null
            : parseInt(rawGpIdx, 10);
          return Object.assign({}, def, s, {
            gamepadIndex,
            keys: Object.assign({}, def.keys, s.keys || {}),
          });
        });
      } else {
        _profiles = DEFAULT_PROFILES.map(p => JSON.parse(JSON.stringify(p)));
      }
    } catch(_) {
      _profiles = DEFAULT_PROFILES.map(p => JSON.parse(JSON.stringify(p)));
    }
  }

  function _save() {
    localStorage.setItem("controlProfiles", JSON.stringify(_profiles));
    // Refresh in-memory profiles so the guard `if (_profiles) return` in _load()
    // doesn't serve a stale snapshot (e.g. gamepadIndex that was null at init).
    _reload();
  }

  // ── Keyboard listeners ───────────────────────────────────────────────
  function _initKeyboard() {
    document.addEventListener("keydown", e => {
      if (_keysDown[e.key]) return; // ignore auto-repeat for action latch
      _keysDown[e.key] = true;
      // Set action latch for any profile whose action key was just pressed
      _profiles.forEach((p, i) => {
        if (e.key === p.keys.action) _actionLatch[i] = true;
      });
    }, true);
    document.addEventListener("keyup", e => {
      _keysDown[e.key] = false;
    }, true);
  }

  // ── Gamepad helpers ──────────────────────────────────────────────────
  function _getGamepad(index) {
    if (index === null || index === undefined) return null;
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    return pads[index] || null;
  }

  function _gpBtn(gp, btnIndex) {
    if (!gp || !gp.buttons[btnIndex]) return false;
    return gp.buttons[btnIndex].pressed || gp.buttons[btnIndex].value > 0.5;
  }

  function _gpAxis(gp, axisIndex) {
    if (!gp || gp.axes[axisIndex] === undefined) return 0;
    return gp.axes[axisIndex];
  }

  // ── Public API ───────────────────────────────────────────────────────
  function init() {
    _load();
    _initKeyboard();
  }

  function getProfiles() {
    _load();
    return _profiles;
  }

  function setPlayerCount(n) {
    _activeCount = Math.max(1, Math.min(4, n));
  }

  /**
   * Returns the current input state for a given player index (0-based).
   * Consumes the action latch.
   */
  function getInput(playerIndex) {
    _load();
    const p   = _profiles[playerIndex];
    if (!p) return { up:false, down:false, left:false, right:false, action:false };
    const k   = p.keys;
    const gp  = _getGamepad(p.gamepadIndex);

    // Keyboard
    const kUp     = !!_keysDown[k.up];
    const kDown   = !!_keysDown[k.down];
    const kLeft   = !!_keysDown[k.left];
    const kRight  = !!_keysDown[k.right];
    const kAction = !!_keysDown[k.action];

    // Gamepad
    let gpUp = false, gpDown = false, gpLeft = false, gpRight = false, gpAction = false;
    if (gp) {
      const lx = _gpAxis(gp, GP.AXIS_LX);
      const ly = _gpAxis(gp, GP.AXIS_LY);
      gpUp    = _gpBtn(gp, GP.DPAD_UP)    || ly < -GP.DEAD;
      gpDown  = _gpBtn(gp, GP.DPAD_DOWN)  || ly >  GP.DEAD;
      gpLeft  = _gpBtn(gp, GP.DPAD_LEFT)  || lx < -GP.DEAD;
      gpRight = _gpBtn(gp, GP.DPAD_RIGHT) || lx >  GP.DEAD;
      gpAction = _gpBtn(gp, GP.X) || _gpBtn(gp, GP.Y) || _gpBtn(gp, GP.A) || _gpBtn(gp, GP.B);
      // Gamepad action latch
      if (gpAction && !_gpActionWas[playerIndex]) _actionLatch[playerIndex] = true;
      _gpActionWas[playerIndex] = gpAction;
    }

    const action = kAction || gpAction || _actionLatch[playerIndex];
    _actionLatch[playerIndex] = false;

    return {
      up:     kUp    || gpUp,
      down:   kDown  || gpDown,
      left:   kLeft  || gpLeft,
      right:  kRight || gpRight,
      action,
    };
  }

  // Track previous gamepad action state to detect press edge
  const _gpActionWas = [false, false, false, false];

  function resetActionLatch(playerIndex) {
    _actionLatch[playerIndex] = false;
  }

  // ── Config Modal ─────────────────────────────────────────────────────
  /**
   * Opens the key-config modal for a given player profile.
   * @param {number} playerIndex  0-based
   * @param {Function} onClose    called when the modal is dismissed
   */
  function openConfig(playerIndex, onClose) {
    _load();
    const profile = _profiles[playerIndex];
    let listeningAction = null;

    // Detect connected gamepads
    const gamepads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) : [];

    const overlay = document.createElement("div");
    overlay.className = "ctrl-modal-overlay";
    overlay.innerHTML = `
      <div class="ctrl-modal">
        <div class="ctrl-modal-header">
          <span class="ctrl-modal-title">🎮 ${profile.name} Controls</span>
          <button class="ctrl-close" id="ctrlClose">✕</button>
        </div>

        <div class="ctrl-section-label">KEYBOARD</div>
        <div class="ctrl-key-rows" id="ctrlKeyRows"></div>

        <div class="ctrl-section-label" style="margin-top:18px">USB CONTROLLER</div>
        <div class="ctrl-gamepad-area" id="ctrlGamepadArea"></div>

        <div class="ctrl-footer">
          <button class="btn btn-secondary" id="ctrlReset">Reset Defaults</button>
          <button class="btn btn-primary" id="ctrlDone">Done</button>
        </div>
      </div>`;

    document.body.appendChild(overlay);

    function render() {
      // Key rows
      const rows = document.getElementById("ctrlKeyRows");
      const labels = { up:"↑ Up", down:"↓ Down", left:"← Left", right:"→ Right", action:"⚡ Action / Throw" };
      rows.innerHTML = Object.entries(labels).map(([action, label]) => {
        const key = profile.keys[action];
        const isListening = listeningAction === action;
        return `<div class="ctrl-key-row">
          <span class="ctrl-key-label">${label}</span>
          <button class="ctrl-key-badge ${isListening ? 'listening' : ''}"
                  data-action="${action}">${isListening ? "Press a key…" : _fmtKey(key)}</button>
        </div>`;
      }).join("");

      // Gamepad area
      const gpArea = document.getElementById("ctrlGamepadArea");
      if (gamepads.length === 0) {
        gpArea.innerHTML = `<p class="ctrl-no-gp">No controllers detected. Plug in a USB/Bluetooth gamepad and press any button.</p>`;
      } else {
        const assignedIdx = profile.gamepadIndex;
        gpArea.innerHTML = `
          <div class="ctrl-gp-list">
            <div class="ctrl-gp-option ${assignedIdx === null ? 'selected' : ''}" data-gp="none">
              <span>⌨️ Keyboard only</span>
              ${assignedIdx === null ? '<span class="ctrl-gp-check">✓</span>' : ''}
            </div>
            ${gamepads.map(gp => `
              <div class="ctrl-gp-option ${assignedIdx === gp.index ? 'selected' : ''}" data-gp="${gp.index}">
                <span>🎮 ${gp.id.slice(0, 40) || 'Controller ' + gp.index}</span>
                <span class="ctrl-gp-sub">Index ${gp.index} · ${gp.buttons.length} buttons · ${gp.axes.length} axes</span>
                ${assignedIdx === gp.index ? '<span class="ctrl-gp-check">✓</span>' : ''}
              </div>`).join("")}
          </div>
          ${assignedIdx !== null ? `<p class="ctrl-gp-hint">Left stick / D-pad = move · A/X = action</p>` : ""}`;
      }
    }

    render();

    // ── Key binding listener ──────────────────────────────────────────
    function onKeyDown(e) {
      if (!listeningAction) return;
      e.preventDefault();
      e.stopPropagation();
      profile.keys[listeningAction] = e.key;
      listeningAction = null;
      _save();
      render();
    }
    document.addEventListener("keydown", onKeyDown, true);

    // ── Gamepad polling for auto-detect ───────────────────────────────
    let gpPollTimer = setInterval(() => {
      const fresh = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) : [];
      if (fresh.length !== gamepads.length) {
        gamepads.length = 0;
        fresh.forEach(g => gamepads.push(g));
        render();
      }
    }, 800);

    // ── Click handlers ────────────────────────────────────────────────
    overlay.addEventListener("click", e => {
      const kb = e.target.closest("[data-action]");
      if (kb) {
        listeningAction = kb.dataset.action;
        render();
        return;
      }
      const gp = e.target.closest("[data-gp]");
      if (gp) {
        profile.gamepadIndex = gp.dataset.gp === "none" ? null : parseInt(gp.dataset.gp, 10);
        _save();
        render();
        return;
      }
      if (e.target.id === "ctrlReset") {
        profile.keys = { ...DEFAULT_PROFILES[playerIndex].keys };
        profile.gamepadIndex = null;
        listeningAction = null;
        _save();
        render();
        return;
      }
      if (e.target.id === "ctrlClose" || e.target.id === "ctrlDone") {
        close();
      }
    });

    function close() {
      document.removeEventListener("keydown", onKeyDown, true);
      clearInterval(gpPollTimer);
      document.body.removeChild(overlay);
      if (onClose) onClose();
    }
  }

  // ── Helpers ──────────────────────────────────────────────────────────
  function _fmtKey(k) {
    if (!k) return "—";
    if (k === " ") return "Space";
    return k.replace("Arrow", "").replace("Key", "") || k;
  }

  return { init, getInput, getProfiles, setPlayerCount, openConfig, resetActionLatch };

})();