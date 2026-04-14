# server.py  –  FastAPI game server  (replaces Flask version)
#
# Install:  pip install fastapi uvicorn
# Run:      uvicorn server:app --host 0.0.0.0 --port 8000 --reload
#
# What changed from Flask version
# ────────────────────────────────
# 1. FastAPI + Pydantic – typed request bodies, auto /docs
# 2. Hybrid slots model – any mix of humans and bots per room:
#      total_slots = 4, bot_slots = 2  →  2 humans + 2 bots
#      total_slots = 4, bot_slots = 0  →  4 humans only
#      total_slots = 4, bot_slots = 3  →  1 human  + 3 bots (starts immediately)
# 3. GET /api/rooms/{id}  – room detail endpoint for the waiting screen
# 4. slots_dict() in broadcast so the waiting-room UI knows every seat

import sys, os, json, uuid, time, threading, queue, random as _random
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent / "games"))
from games.game_factory import GameFactory, BotFactory, InputState

# ── app ───────────────────────────────────────────────────────────────
app = FastAPI(title="MultiGame Server", version="2.0")
app.add_middleware(CORSMiddleware,
                   allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

SOUNDS_DIR = Path(__file__).parent / "sounds"
if SOUNDS_DIR.exists():
    app.mount("/sounds", StaticFiles(directory=str(SOUNDS_DIR)), name="sounds")

rooms: dict[str, "RoomState"] = {}
rooms_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────
#  RoomState
# ─────────────────────────────────────────────────────────────────────

PLAYER_COLOR_PALETTE = [
    "#DC5050", "#5050DC", "#50C864", "#DCDC50",
    "#DC50DC", "#50DCDC", "#FF8C00", "#A0A0FF",
]

BOT_NAMES = ["Cortex", "Crash", "Dingodile", "Brio", "Tiny", "Cong"]

def _pick_bot_name(exclude: set = None) -> str:
    """Pick a random bot name, avoiding already-used names when possible."""
    exclude = exclude or set()
    available = [n for n in BOT_NAMES if n not in exclude]
    if not available:
        available = BOT_NAMES  # all used — reset and allow repeats
    return _random.choice(available)

class RoomState:
    """
    Slot model
    ──────────
    total_slots  2-4  (capped to game max)
    bot_slots    0-3  (capped to total_slots - 1, so ≥ 1 human always)
    human_slots  = total_slots - bot_slots

    Humans fill seats 1..human_slots in join order.
    Bots  fill seats human_slots+1..total_slots (fixed from room creation).

    Game starts when human_count == human_slots AND all humans are ready.
    If human_slots == 1 (solo vs bots), the game starts immediately on create.
    """

    def __init__(self, room_id, game_type, host_id, host_name,
                 total_slots, bot_slots, bot_difficulty, host_color=None,
                 team_mode=False):

        cfg         = GameFactory.CONFIGS[game_type]
        total_slots = min(total_slots, cfg["max_players"])
        bot_slots   = max(0, min(bot_slots, total_slots - 1))

        self.room_id        = room_id
        self.game_type      = game_type
        self.host_id        = host_id
        self.total_slots    = total_slots
        self.bot_slots      = bot_slots
        self.human_slots    = total_slots - bot_slots
        self.bot_difficulty = bot_difficulty
        # team_mode: only valid for exactly 4-slot games (2v2).
        # total must be 4 and exactly 2 human slots or 4 (all-human).
        # We store the intent; actual teams are assembled in _start_game.
        self.team_mode      = team_mode and (total_slots == 4)

        # {pid: {name, ready, number, is_bot, color}}
        chosen_color = host_color if host_color else PLAYER_COLOR_PALETTE[0]
        self.players: dict[str, dict] = {
            host_id: {"name": host_name, "ready": False,
                      "number": 1, "is_bot": False, "color": chosen_color}
        }
        # slot_map: slot_number (1..total_slots) -> pid, "bot", or None
        # Humans can freely pick ANY of the total_slots positions.
        # Unoccupied positions will be filled by bots at game start.
        self.slot_map: dict[int, str | None] = {i+1: None for i in range(self.total_slots)}
        self.slot_map[1] = host_id  # host starts in slot 1
        self.status      = "waiting"
        self.game        = None
        self.bots: dict  = {}
        self.input_queues: dict[str, queue.Queue] = {
            host_id: queue.Queue(maxsize=4)
        }
        self.subscribers: dict[str, queue.Queue] = {}
        self._subs_lock  = threading.Lock()
        self.last_state: dict = {}
        self.name_map:   dict = {}   # pid/bid -> display name, set by _start_game
        self.created_at  = time.time()

    # ── properties ───────────────────────────────────────────────────

    @property
    def human_count(self): return len(self.players)

    @property
    def is_full(self): return self.human_count >= self.human_slots

    @property
    def all_ready(self):
        return self.is_full and all(p["ready"] for p in self.players.values())

    def taken_colors(self) -> set:
        return {p["color"] for p in self.players.values()}

    # ── pub/sub ───────────────────────────────────────────────────────

    def subscribe(self, sub_id):
        q = queue.Queue(maxsize=10)
        with self._subs_lock:
            self.subscribers[sub_id] = q
        return q

    def unsubscribe(self, sub_id):
        with self._subs_lock:
            self.subscribers.pop(sub_id, None)

    def broadcast(self, payload: dict):
        self.last_state = payload
        data = json.dumps(payload)
        with self._subs_lock:
            stale = []
            for sid, q in self.subscribers.items():
                try:   q.put_nowait(data)
                except queue.Full: stale.append(sid)
            for sid in stale:
                self.subscribers.pop(sid, None)

    # ── serialisation ────────────────────────────────────────────────

    def lobby_dict(self):
        cfg = GameFactory.CONFIGS[self.game_type]
        return {
            "room_id":     self.room_id,
            "game_type":   self.game_type,
            "game_name":   cfg["name"],
            "host_id":     self.host_id,
            "host_name":   self.players[self.host_id]["name"],
            "human_count": self.human_count,
            "human_slots": self.human_slots,
            "bot_slots":   self.bot_slots,
            "total_slots": self.total_slots,
            "team_mode":   self.team_mode,
            "status":      self.status,
            "created_ago": int(time.time() - self.created_at),
        }

    def reset_for_rematch(self):
        """Reset room back to waiting state, keeping same players and config."""
        # Reset all human ready flags
        for p in self.players.values():
            p["ready"] = False
        # Flush every input queue so stale keys from the last game
        # don't bleed into the new one
        for q in self.input_queues.values():
            while not q.empty():
                try:    q.get_nowait()
                except queue.Empty: break
        # Clear game state
        self.game    = None
        self.bots    = {}
        self.status  = "waiting"
        self.last_state = {}
        # Keep slot_map but clear slots whose player left
        for sn in list(self.slot_map):
            pid = self.slot_map[sn]
            if pid and pid not in self.players:
                self.slot_map[sn] = None
        # Re-seat any player not currently in slot_map
        for pid in self.players:
            if pid not in self.slot_map.values():
                free = next((s for s in sorted(self.slot_map)
                             if self.slot_map[s] is None), None)
                if free: self.slot_map[free] = pid

    def slots_dict(self):
        """One entry per total_slot for the waiting-room UI.
        Human slots respect slot_map so players can choose their seat/team.
        Team A = slots 1,3 (top+left sides); Team B = slots 2,4 (bottom+right).
        """
        # All total_slots are shown. Humans occupy their chosen slot;
        # remaining slots will be filled by bots at game start.
        slots = []
        # Figure out which slots humans have claimed
        human_slots_taken = {sn for sn, pid in self.slot_map.items()
                             if pid and pid in self.players}
        # Count how many bot slots are "default" (not overridden by a human)
        bot_counter = 0
        for slot_number in range(1, self.total_slots + 1):
            team_label = None
            if self.team_mode:
                team_label = "A" if slot_number in (1, 3) else "B"
            pid = self.slot_map.get(slot_number)
            if pid and pid in self.players:
                # Human occupying this slot
                p = self.players[pid]
                slots.append({"slot": slot_number, "pid": pid, "name": p["name"],
                               "ready": p["ready"], "is_bot": False, "filled": True,
                               "team": team_label})
            elif slot_number in human_slots_taken:
                # Should not happen, but guard anyway
                slots.append({"slot": slot_number, "filled": False, "is_bot": False,
                               "team": team_label})
            else:
                # This slot will be a bot (or is open for a human to claim)
                human_count_so_far = len(self.players)
                free_slots_for_humans = self.human_slots - human_count_so_far
                # A slot is "open for human" if we still have human_slots to fill
                # and the total humans haven't filled all human_slots yet
                bot_counter += 1
                used_names = {s["name"].split(" ")[0] for s in slots if s.get("is_bot")}
                bot_name = _pick_bot_name(exclude=used_names)
                slots.append({"slot": slot_number, "filled": True,
                               "is_bot": True, "ready": True,
                               "name": bot_name, "team": team_label,
                               "displaceable": free_slots_for_humans > 0})
        return slots


# ─────────────────────────────────────────────────────────────────────
#  Game loop
# ─────────────────────────────────────────────────────────────────────

TICK_DT = 1.0 / 60


def _game_loop(room: RoomState):
    print(f"[loop] start  room={room.room_id}  "
          f"humans={room.human_slots}  bots={room.bot_slots}")
    last = time.perf_counter()

    while room.status == "playing":
        now  = time.perf_counter()
        last = now

        inputs = {}
        for pid in room.game.player_ids:
            if pid in room.bots:
                inp = room.bots[pid].decide(room.last_state)
            else:
                q   = room.input_queues.get(pid)
                inp = InputState.neutral(pid)
                action_latched = False
                if q:
                    while not q.empty():
                        try:
                            raw = q.get_nowait()
                            inp = InputState.from_dict(pid, raw)
                            # Latch action: if ANY queued frame had action=true, keep it
                            if raw.get("action"):
                                action_latched = True
                        except queue.Empty:
                            break
                if action_latched:
                    inp.action = True
            inputs[pid] = inp

        try:
            state = room.game.update(inputs, dt=TICK_DT)
        except Exception as e:
            print(f"[loop] error  room={room.room_id}: {e}")
            break

        # Enrich player entries with display name and is_bot flag
        for pid, pdata in state.get("players", {}).items():
            pdata["name"]   = room.name_map.get(pid, pid)
            pdata["is_bot"] = pid not in room.players

        room.broadcast(state)

        if room.game.is_over():
            room.status = "finished"
            final = room.game.get_state()
            # Enrich final state too
            for pid, pdata in final.get("players", {}).items():
                pdata["name"]   = room.name_map.get(pid, pid)
                pdata["is_bot"] = pid not in room.players
            final["host_id"] = room.host_id
            room.broadcast(final)
            print(f"[loop] over  room={room.room_id}  "
                  f"winner={room.game.get_winner()}")
            break

        sleep = TICK_DT - (time.perf_counter() - now)
        if sleep > 0:
            time.sleep(sleep)

    print(f"[loop] stopped  room={room.room_id}")


def _start_game(room: RoomState):
    # Build all_ids preserving slot order across ALL total_slots.
    # Human-occupied slots keep their pid; remaining slots get bot ids.
    all_ids  = []
    bot_idx  = 0
    bot_map  = {}  # slot_number -> bot_id
    for sn in sorted(room.slot_map):
        pid = room.slot_map[sn]
        if pid and pid in room.players:
            all_ids.append(pid)
        else:
            bid = f"bot_{bot_idx+1}_{room.room_id[:6]}"
            all_ids.append(bid)
            bot_map[sn] = bid
            bot_idx += 1
    human_ids = [pid for pid in all_ids if pid in room.players]
    bot_ids   = [bid for bid in all_ids if bid not in room.players]

    # Build per-player color map
    taken       = room.taken_colors()
    bot_palette = [c for c in PLAYER_COLOR_PALETTE if c not in taken]
    color_map   = {pid: room.players[pid]["color"] for pid in human_ids}
    for i, bid in enumerate(bot_ids):
        color_map[bid] = (bot_palette[i % len(bot_palette)] if bot_palette
                          else PLAYER_COLOR_PALETTE[i % len(PLAYER_COLOR_PALETTE)])

    # Build per-player name map
    name_map = {pid: room.players[pid]["name"] for pid in human_ids}
    for i, bid in enumerate(bot_ids):
        used_names = set(name_map.values())
        name_map[bid] = _pick_bot_name(exclude=used_names)

    # slot_numbers: {pid -> slot_number (1=top,2=bottom,3=left,4=right)}
    # This is the physical side each player CHOSE in the waiting room,
    # derived from their position in slot_map.
    slot_number_map = {}
    for sn in sorted(room.slot_map):
        pid = room.slot_map[sn]
        if pid and pid in room.players:
            slot_number_map[pid] = sn  # sn IS the side number (1-4)

    game_config = {"colors": color_map, "names": name_map,
                   "slot_numbers": slot_number_map}
    room.name_map = name_map   # persist for _game_loop enrichment

    # ── Team mode (2v2) ────────────────────────────────────────────────
    # Assign teams so teammates occupy adjacent sides:
    #   Team A → positions 0 and 2 in all_ids (side-slots 1 and 3: top + left)
    #   Team B → positions 1 and 3 in all_ids (side-slots 2 and 4: bottom + right)
    # This matches the adjacent-side layout in CrashBashGame.__init__.
    if room.team_mode and len(all_ids) == 4:
        # Slot order IS team order: slots 1&3 → Team A, slots 2&4 → Team B
        game_config["teams"] = {
            "A": [all_ids[0], all_ids[2]],   # slot positions 1 & 3 → top + left
            "B": [all_ids[1], all_ids[3]],   # slot positions 2 & 4 → bottom + right
        }

    room.game   = GameFactory.create(room.game_type, room.room_id, all_ids, game_config)
    room.status = "playing"

    for bid in bot_ids:
        room.bots[bid] = BotFactory.create(
            room.game_type, bid, room.bot_difficulty)

    room.last_state = room.game.get_state()
    room.broadcast({**room.last_state, "_event": "game_starting",
                    "host_id": room.host_id})

    # Countdown runs in a background thread so it doesn't block the endpoint
    threading.Thread(target=_countdown_then_loop, args=(room,), daemon=True).start()


def _countdown_then_loop(room: RoomState):
    """Broadcast 3-2-1-Go then start the game loop."""
    for n in (3, 2, 1):
        room.broadcast({"_event": "countdown", "count": n})
        time.sleep(1.0)
    room.broadcast({"_event": "countdown", "count": 0})   # "Go!"
    time.sleep(0.25)   # tiny pause so "Go!" is visible before first tick
    _game_loop(room)


# ─────────────────────────────────────────────────────────────────────
#  Pydantic models
# ─────────────────────────────────────────────────────────────────────

class CreateBody(BaseModel):
    game_type:      str  = "crash_bash"
    player_name:    str  = "Player"
    player_color:   Optional[str] = None
    total_slots:    int  = Field(4, ge=2, le=4)
    bot_slots:      int  = Field(0, ge=0, le=3)
    bot_difficulty: str  = "medium"
    team_mode:      bool = False
    player_id:      Optional[str] = None

class JoinBody(BaseModel):
    player_name:    str           = "Player"
    player_color:   Optional[str] = None
    player_id:      Optional[str] = None
    preferred_slot: Optional[int] = None  # 1-indexed slot the player wants

class ReadyBody(BaseModel):
    player_id: Optional[str] = None

class PickSlotBody(BaseModel):
    slot:      int            # 1-indexed desired slot number
    player_id: Optional[str] = None

class InputBody(BaseModel):
    input:     dict           = Field(default_factory=dict)
    player_id: Optional[str] = None


def _pid(header: Optional[str], body: Optional[str]) -> str:
    return header or body or uuid.uuid4().hex


# ─────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────

@app.get("/api/rooms")
def list_rooms():
    with rooms_lock:
        waiting = [r.lobby_dict() for r in rooms.values()
                   if r.status == "waiting"]
    return {"rooms": waiting}


@app.get("/api/rooms/{room_id}")
def get_room(room_id: str):
    r = rooms.get(room_id)
    if not r: raise HTTPException(404, "Room not found")
    return {**r.lobby_dict(), "slots": r.slots_dict()}


@app.post("/api/rooms/create")
def create_room(body: CreateBody,
                x_player_id: Optional[str] = Header(None)):

    if body.game_type not in GameFactory.game_types():
        raise HTTPException(400, f"Unknown game_type '{body.game_type}'")

    player_id = _pid(x_player_id, body.player_id)
    room_id   = uuid.uuid4().hex[:8]
    room      = RoomState(room_id, body.game_type, player_id, body.player_name,
                          body.total_slots, body.bot_slots, body.bot_difficulty,
                          host_color=body.player_color,
                          team_mode=body.team_mode)

    with rooms_lock:
        rooms[room_id] = room

    print(f"[room] created  id={room_id}  type={body.game_type}  "
          f"humans={room.human_slots}  bots={room.bot_slots}  "
          f"host={body.player_name}")

    # Solo vs bots: start immediately
    if room.human_slots == 1:
        room.players[player_id]["ready"] = True
        _start_game(room)

    return {"room_id": room_id, "player_id": player_id,
            "status": room.status, "game_type": body.game_type,
            "human_slots": room.human_slots, "bot_slots": room.bot_slots,
            "slots": room.slots_dict()}


@app.post("/api/rooms/{room_id}/join")
def join_room(room_id: str, body: JoinBody,
              x_player_id: Optional[str] = Header(None)):

    room = rooms.get(room_id)
    if not room: raise HTTPException(404, "Room not found")

    player_id = _pid(x_player_id, body.player_id)

    # ── Known player reconnecting (e.g. after a page refresh) ──────────
    # This check MUST come before the status/full guards so that a player
    # who refreshes mid-game is let back in instead of getting a 409.
    if player_id in room.players:
        p = room.players[player_id]
        # Keep name/color fresh from the reconnect payload
        p["name"] = body.player_name or p["name"]
        if body.player_color and body.player_color not in room.taken_colors():
            p["color"] = body.player_color
        # Only reset ready flag when still in waiting room
        if room.status == "waiting":
            p["ready"] = False
            room.broadcast({"_event": "player_joined", "slots": room.slots_dict(),
                            "room_id": room_id})
        return {"room_id": room_id, "player_id": player_id,
                "number": p["number"],
                "status": room.status, "human_slots": room.human_slots,
                "bot_slots": room.bot_slots, "slots": room.slots_dict(),
                "host_id": room.host_id}

    # ── New player joining ──────────────────────────────────────────────
    if room.status != "waiting": raise HTTPException(409, "Game already started")
    if room.is_full:             raise HTTPException(409, "Room is full")

    # Determine which slot to place this player in.
    # All total_slots are candidates — humans can displace bot-default slots.
    # "Free" means no human pid is there (bot-default slots count as free for joining).
    preferred = getattr(body, "preferred_slot", None)
    human_occupied = {sn for sn, pid in room.slot_map.items() if pid in room.players}
    free_slots = [s for s in room.slot_map if s not in human_occupied]
    if preferred and preferred in free_slots:
        chosen_slot = preferred
    elif free_slots:
        chosen_slot = min(free_slots)
    else:
        raise HTTPException(409, "Room is full")
    number = chosen_slot
    taken  = room.taken_colors()
    if body.player_color and body.player_color not in taken:
        join_color = body.player_color
    else:
        join_color = next((c for c in PLAYER_COLOR_PALETTE if c not in taken),
                          PLAYER_COLOR_PALETTE[number % len(PLAYER_COLOR_PALETTE)])
    room.players[player_id] = {"name": body.player_name, "ready": False,
                                "number": number, "is_bot": False,
                                "color": join_color}
    room.slot_map[chosen_slot] = player_id
    room.input_queues[player_id] = queue.Queue(maxsize=4)

    print(f"[room] join  id={room_id}  player={body.player_name}  "
          f"{room.human_count}/{room.human_slots} humans")

    room.broadcast({"_event": "player_joined", "slots": room.slots_dict(),
                    "room_id": room_id})

    return {"room_id": room_id, "player_id": player_id, "number": number,
            "status": room.status, "human_slots": room.human_slots,
            "bot_slots": room.bot_slots, "slots": room.slots_dict()}


@app.post("/api/rooms/{room_id}/leave")
def leave_room(room_id: str,
               x_player_id: Optional[str] = Header(None)):
    """
    Remove a human player from a waiting room so they (or anyone with the
    same player_id) can rejoin later after e.g. changing their name.
    Only valid while the room is still in 'waiting' status.
    The host cannot leave (they must disband the room by abandoning it).
    """
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404, "Room not found")
    if room.status != "waiting":
        raise HTTPException(409, "Cannot leave a room that has already started")

    player_id = x_player_id or ""
    if player_id not in room.players:
        return {"status": "ok", "message": "Not in room"}

    if player_id == room.host_id:
        raise HTTPException(403, "Host cannot leave – close the room instead")

    # Remove the player, their input queue, and their slot
    del room.players[player_id]
    room.input_queues.pop(player_id, None)
    for sn in room.slot_map:
        if room.slot_map[sn] == player_id:
            room.slot_map[sn] = None
            break

    # Re-number players by their slot position
    for sn, pid in sorted(room.slot_map.items()):
        if pid and pid in room.players:
            room.players[pid]["number"] = sn

    print(f"[room] leave  id={room_id}  player={player_id}  "
          f"remaining={room.human_count}/{room.human_slots}")

    room.broadcast({"_event": "player_left", "slots": room.slots_dict(),
                    "room_id": room_id})

    return {"status": "ok", "slots": room.slots_dict()}


@app.post("/api/rooms/{room_id}/ready")
def set_ready(room_id: str, body: ReadyBody,
              x_player_id: Optional[str] = Header(None)):

    room = rooms.get(room_id)
    if not room: raise HTTPException(404, "Room not found")

    player_id = _pid(x_player_id, body.player_id)
    if player_id not in room.players:
        raise HTTPException(403, "Not in this room")

    room.players[player_id]["ready"] = True

    cfg    = GameFactory.CONFIGS[room.game_type]
    enough = room.human_count + room.bot_slots >= cfg["min_players"]

    if room.all_ready and enough and room.status == "waiting":
        _start_game(room)

    room.broadcast({"_event": "ready_update", "slots": room.slots_dict(),
                    "status": room.status})

    return {"all_ready": room.all_ready, "status": room.status,
            "slots": room.slots_dict()}


@app.post("/api/rooms/{room_id}/rematch")
def rematch(room_id: str,
            x_player_id: Optional[str] = Header(None)):
    """
    Host-only.  Resets the finished room back to 'waiting' so the same
    players can play again without rejoining.  Broadcasts rematch_called
    so all clients automatically return to the waiting screen.
    """
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404, "Room not found")

    player_id = x_player_id or ""
    if player_id != room.host_id:
        raise HTTPException(403, "Only the host can start a rematch")

    if room.status not in ("finished", "playing"):
        raise HTTPException(409, f"Room is not finished (status={room.status})")

    room.reset_for_rematch()

    print(f"[room] rematch  id={room_id}  host={player_id}")

    room.broadcast({
        "_event":      "rematch_called",
        "room_id":     room_id,
        "slots":       room.slots_dict(),
        "host_id":     room.host_id,
    })

    return {"status": "waiting", "slots": room.slots_dict()}



@app.post("/api/rooms/{room_id}/pick_slot")
def pick_slot(room_id: str, body: PickSlotBody,
              x_player_id: Optional[str] = Header(None)):
    """
    Move a player to a different human slot while in the waiting room.
    The target slot must be unoccupied. The player's old slot is freed.
    """
    room = rooms.get(room_id)
    if not room: raise HTTPException(404, "Room not found")
    if room.status != "waiting": raise HTTPException(409, "Game already started")

    player_id = _pid(x_player_id, body.player_id)
    if player_id not in room.players:
        raise HTTPException(403, "Not in this room")

    target = body.slot
    if target not in room.slot_map:
        raise HTTPException(400, f"Slot {target} does not exist (valid: 1-{room.total_slots})")
    if room.slot_map[target] == player_id:
        return {"status": "ok", "slot": target, "slots": room.slots_dict()}  # no-op
    # A slot is available if no OTHER human is in it
    current_occupant = room.slot_map.get(target)
    if current_occupant and current_occupant in room.players:
        raise HTTPException(409, f"Slot {target} is already taken by another player")

    # Free old slot
    for sn in room.slot_map:
        if room.slot_map[sn] == player_id:
            room.slot_map[sn] = None
            break
    # Occupy new slot
    room.slot_map[target] = player_id
    room.players[player_id]["number"] = target
    # Reset ready so teams aren't locked in accidentally
    room.players[player_id]["ready"] = False

    print(f"[room] pick_slot  id={room_id}  player={player_id}  slot={target}")
    room.broadcast({"_event": "player_joined", "slots": room.slots_dict(),
                    "room_id": room_id})
    return {"status": "ok", "slot": target, "slots": room.slots_dict()}


@app.post("/api/rooms/{room_id}/input")
def post_input(room_id: str, body: InputBody,
               x_player_id: Optional[str] = Header(None)):

    room = rooms.get(room_id)
    if not room or room.status != "playing":
        return Response(status_code=204)

    player_id = _pid(x_player_id, body.player_id)
    q = room.input_queues.get(player_id)
    if q:
        try: q.put_nowait(body.input)
        except queue.Full:
            try: q.get_nowait()
            except queue.Empty: pass
            try: q.put_nowait(body.input)
            except queue.Full: pass

    return Response(status_code=204)


@app.get("/api/rooms/{room_id}/stream")
def stream(room_id: str):
    room = rooms.get(room_id)
    if not room: raise HTTPException(404, "Room not found")

    sub_id  = uuid.uuid4().hex
    q       = room.subscribe(sub_id)
    initial = json.dumps(
        room.last_state if room.last_state
        else {"_event": "waiting", "room": room.lobby_dict(),
              "slots": room.slots_dict()}
    )

    def generate():
        try:
            yield f"data: {initial}\n\n"
            while True:
                try:
                    data = q.get(timeout=25)
                    yield f"data: {data}\n\n"
                    p = json.loads(data)
                    if p.get("game_over") and not p.get("_event"):
                        break
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            room.unsubscribe(sub_id)

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
def index():
    p = STATIC_DIR / "index.html"
    return FileResponse(str(p)) if p.exists() else HTMLResponse(
        "<h2>Place static/index.html next to server.py</h2>")


# ── cleanup ───────────────────────────────────────────────────────────
def _cleanup():
    while True:
        time.sleep(120)
        now = time.time()
        with rooms_lock:
            stale = [rid for rid, r in rooms.items()
                     if r.status == "finished" and now - r.created_at > 600]
            for rid in stale:
                del rooms[rid]
threading.Thread(target=_cleanup, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="127.0.0.1", port=port)