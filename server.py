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

import sys, os, json, uuid, time, threading, queue
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent / "games"))
from game_factory import GameFactory, BotFactory, InputState

# ── app ───────────────────────────────────────────────────────────────
app = FastAPI(title="MultiGame Server", version="2.0")
app.add_middleware(CORSMiddleware,
                   allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

rooms: dict[str, "RoomState"] = {}
rooms_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────
#  RoomState
# ─────────────────────────────────────────────────────────────────────

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
                 total_slots, bot_slots, bot_difficulty):

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

        # {pid: {name, ready, number, is_bot}}
        self.players: dict[str, dict] = {
            host_id: {"name": host_name, "ready": False,
                      "number": 1, "is_bot": False}
        }
        self.status      = "waiting"
        self.game        = None
        self.bots: dict  = {}
        self.input_queues: dict[str, queue.Queue] = {
            host_id: queue.Queue(maxsize=4)
        }
        self.subscribers: dict[str, queue.Queue] = {}
        self._subs_lock  = threading.Lock()
        self.last_state: dict = {}
        self.created_at  = time.time()

    # ── properties ───────────────────────────────────────────────────

    @property
    def human_count(self): return len(self.players)

    @property
    def is_full(self): return self.human_count >= self.human_slots

    @property
    def all_ready(self):
        return self.is_full and all(p["ready"] for p in self.players.values())

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
            "host_name":   self.players[self.host_id]["name"],
            "human_count": self.human_count,
            "human_slots": self.human_slots,
            "bot_slots":   self.bot_slots,
            "total_slots": self.total_slots,
            "status":      self.status,
            "created_ago": int(time.time() - self.created_at),
        }

    def slots_dict(self):
        """One entry per total_slot for the waiting-room UI."""
        slots = []
        human_list = list(self.players.items())
        for i in range(self.human_slots):
            if i < len(human_list):
                pid, p = human_list[i]
                slots.append({"slot": i+1, "pid": pid, "name": p["name"],
                               "ready": p["ready"], "is_bot": False, "filled": True})
            else:
                slots.append({"slot": i+1, "filled": False, "is_bot": False})
        for i in range(self.bot_slots):
            slots.append({"slot": self.human_slots+i+1, "filled": True,
                           "is_bot": True, "ready": True,
                           "name": f"Bot {i+1} ({self.bot_difficulty})"})
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
                if q:
                    while not q.empty():
                        try:
                            raw = q.get_nowait()
                            inp = InputState.from_dict(pid, raw)
                        except queue.Empty:
                            break
            inputs[pid] = inp

        try:
            state = room.game.update(inputs, dt=TICK_DT)
        except Exception as e:
            print(f"[loop] error  room={room.room_id}: {e}")
            break

        room.broadcast(state)

        if room.game.is_over():
            room.status = "finished"
            room.broadcast(room.game.get_state())
            print(f"[loop] over  room={room.room_id}  "
                  f"winner={room.game.get_winner()}")
            break

        sleep = TICK_DT - (time.perf_counter() - now)
        if sleep > 0:
            time.sleep(sleep)

    print(f"[loop] stopped  room={room.room_id}")


def _start_game(room: RoomState):
    human_ids = list(room.players.keys())
    bot_ids   = [f"bot_{i+1}_{room.room_id[:6]}" for i in range(room.bot_slots)]
    all_ids   = human_ids + bot_ids

    room.game   = GameFactory.create(room.game_type, room.room_id, all_ids)
    room.status = "playing"

    for bid in bot_ids:
        room.bots[bid] = BotFactory.create(
            room.game_type, bid, room.bot_difficulty)

    room.last_state = room.game.get_state()
    room.broadcast({**room.last_state, "_event": "game_starting"})
    threading.Thread(target=_game_loop, args=(room,), daemon=True).start()


# ─────────────────────────────────────────────────────────────────────
#  Pydantic models
# ─────────────────────────────────────────────────────────────────────

class CreateBody(BaseModel):
    game_type:      str  = "crash_bash"
    player_name:    str  = "Player"
    total_slots:    int  = Field(4, ge=2, le=4)
    bot_slots:      int  = Field(0, ge=0, le=3)
    bot_difficulty: str  = "medium"
    player_id:      Optional[str] = None

class JoinBody(BaseModel):
    player_name: str          = "Player"
    player_id:   Optional[str] = None

class ReadyBody(BaseModel):
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
                          body.total_slots, body.bot_slots, body.bot_difficulty)

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
    if not room:           raise HTTPException(404, "Room not found")
    if room.status != "waiting": raise HTTPException(409, "Game already started")
    if room.is_full:       raise HTTPException(409, "Room is full")

    player_id = _pid(x_player_id, body.player_id)
    if player_id in room.players:
        raise HTTPException(409, "Already in this room")

    number = room.human_count + 1
    room.players[player_id] = {"name": body.player_name, "ready": False,
                                "number": number, "is_bot": False}
    room.input_queues[player_id] = queue.Queue(maxsize=4)

    print(f"[room] join  id={room_id}  player={body.player_name}  "
          f"{room.human_count}/{room.human_slots} humans")

    room.broadcast({"_event": "player_joined", "slots": room.slots_dict(),
                    "room_id": room_id})

    return {"room_id": room_id, "player_id": player_id, "number": number,
            "status": room.status, "human_slots": room.human_slots,
            "bot_slots": room.bot_slots, "slots": room.slots_dict()}


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
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)