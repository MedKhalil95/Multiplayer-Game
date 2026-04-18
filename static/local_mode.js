/**
 * local_mode.js  –  Local multiplayer mode
 *
 * Strategy: Creates a normal server room with all human slots, but drives
 * every player's input from this same browser tab — each with their own
 * key/gamepad profile from Controls.  This reuses 100% of server-side
 * game logic and rendering with zero duplication.
 *
 * Public API
 * ──────────
 *   LocalMode.start(gameType, playerCount, botCount, difficulty)
 *     → navigates to waiting screen, ready fires immediately
 *   LocalMode.isActive()   → bool
 *   LocalMode.stop()
 *
 * Depends on: Controls, S (global app state), fetch, setInterval
 */

const LocalMode = (() => {

  let _active       = false;
  let _inputLoops   = [];   // one setInterval per local human player
  let _playerIds    = [];   // pid for each local player slot
  let _roomId       = null;

  /**
   * Generate a stable local player ID for slot i, persisted across refreshes.
   */
  function _localPid(i) {
    const key = `localPid_${i}`;
    let id = localStorage.getItem(key);
    if (!id) { id = "local_" + Math.random().toString(36).slice(2, 10); localStorage.setItem(key, id); }
    return id;
  }

  /**
   * Start a local game.
   * @param {string}   gameType     "crash_bash" | "tnt_battle"
   * @param {number}   humanCount   2-4 (all local humans)
   * @param {number}   botCount     0-2 extra bots on top of humans
   * @param {string}   difficulty   "easy"|"medium"|"hard"
   * @param {string[]} playerNames  display name for each human slot
   * @param {string[]} playerColors hex color for each human slot
   * @param {boolean}  teamMode     true = 2v2 teams (only valid when total players = 4)
   */
  async function start(gameType, humanCount, botCount, difficulty,
                       playerNames, playerColors, teamMode) {
    stop();
    _active = true;

    Controls.setPlayerCount(humanCount);

    // Default names/colors if not supplied
    const DEFAULT_COLORS = ["#DC5050", "#5050DC", "#50C864", "#DCDC50"];
    const names  = playerNames  || Array.from({length: humanCount}, (_, i) => `Player ${i + 1}`);
    const colors = playerColors || DEFAULT_COLORS.slice(0, humanCount);

    // Build player IDs: slot 0 is the "host" (existing S.playerId),
    // slots 1..humanCount-1 are dedicated local IDs.
    _playerIds = [S.playerId];
    for (let i = 1; i < humanCount; i++) {
      _playerIds.push(_localPid(i));
    }

    const totalSlots = humanCount + botCount;
    const useTeamMode = !!(teamMode && totalSlots === 4);

    // Create room as host (Player 1)
    let roomData;
    try {
      const res = await fetch("/api/rooms/create", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Player-Id": S.playerId },
        body: JSON.stringify({
          game_type:      gameType,
          player_name:    names[0],
          player_color:   colors[0],
          total_slots:    totalSlots,
          bot_slots:      botCount,
          bot_difficulty: difficulty,
          team_mode:      useTeamMode,
          player_id:      S.playerId,
        }),
      });
      roomData = await res.json();
      if (!res.ok) throw new Error(roomData.detail || "Could not create room");
    } catch (e) {
      alert("Local mode error: " + e.message);
      _active = false;
      return;
    }

    _roomId = roomData.room_id;
    S.roomId = _roomId;
    S.hostId = S.playerId;
    S.gameType = gameType;
    localStorage.setItem("roomId", _roomId);
    localStorage.setItem("gameType", gameType);

    // Persist P1 name for online lobby pre-fill
    localStorage.setItem("playerName", names[0]);

    // Join remaining local human players (P2..P4)
    for (let i = 1; i < humanCount; i++) {
      const pid = _playerIds[i];
      try {
        await fetch(`/api/rooms/${_roomId}/join`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Player-Id": pid },
          body: JSON.stringify({
            player_id:    pid,
            player_name:  names[i],
            player_color: colors[i],
          }),
        });
      } catch (e) {
        console.warn(`LocalMode: could not join player ${i}`, e);
      }
    }

    // Enter the room UI (connects SSE, shows wait screen)
    enterRoom(_roomId, false);

    // Immediately mark all local players as ready
    for (const pid of _playerIds) {
      try {
        await fetch(`/api/rooms/${_roomId}/ready`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Player-Id": pid },
          body: JSON.stringify({ player_id: pid }),
        });
      } catch (_) {}
    }
  }

  /**
   * Call this once the game screen is active to start per-player input loops.
   * Called automatically by startLocalInputLoops() from app.js.
   */
  function startInputLoops() {
    stopInputLoops();
    _playerIds.forEach((pid, i) => {
      const loop = setInterval(() => {
        if (!_roomId) return;
        const inp = Controls.getInput(i);
        fetch(`/api/rooms/${_roomId}/input`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Player-Id": pid },
          body: JSON.stringify({ input: inp, player_id: pid }),
        }).catch(() => {});
      }, 33);
      _inputLoops.push(loop);
    });
  }

  function stopInputLoops() {
    _inputLoops.forEach(id => clearInterval(id));
    _inputLoops = [];
  }

  function stop() {
    stopInputLoops();
    _active   = false;
    _roomId   = null;
    _playerIds = [];
  }

  function isActive() { return _active; }
  function getPlayerIds() { return _playerIds; }

  return { start, startInputLoops, stopInputLoops, stop, isActive, getPlayerIds };

})();