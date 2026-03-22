# game2_tntbattle.py  –  TNT Battle  (headless)
#
# Faithful translation of your PyGame original:
#   • 2-4 players, free movement in an open arena (no platforms).
#   • Pickup crates (PickupCrate) spawn every 2-4 s around the arena.
#   • Walk over a crate to pick it up.  Press ACTION to throw it in the
#     direction you last moved.
#   • Thrown crates fly with gravity, rotating, and slow slightly.
#   • On player hit → Explosion: radius 60, damage 40.
#     Explosion.apply() fans damage (frac = 1 - dist/radius, min 0.2),
#     applies knockback and stun – exact port of your Explosion class.
#   • Each player starts with HP = 100.  HP ≤ 0 → eliminated.
#   • Last player alive wins.
#
# No pygame.display / pygame.draw / pygame.Surface anywhere.

import math
import random
from base_game import BaseHeadlessGame, InputState

# ── constants ─────────────────────────────────────────────────────────

PLAYER_SPEED   = 4          # px/tick
PLAYER_SIZE    = 36
PLAYER_HP      = 100
CRATE_SIZE     = 32         # pickup crate
THROWN_SIZE    = 24         # thrown crate
THROW_POWER    = 15         # initial speed of thrown crate (px/tick)
CRATE_GRAVITY  = 0.3        # px/tick² applied to thrown crates
CRATE_DRAG     = 0.99       # velocity multiplier per tick
THROW_STUN     = 10         # ticks after throwing
EXPLODE_RADIUS = 60         # px
EXPLODE_DAMAGE = 40
EXPLODE_LIFE   = 25         # ticks the explosion visual lasts
MAX_CRATES     = 8
CRATE_SPAWN_LO = 120        # ticks (2 s at 60 tps)
CRATE_SPAWN_HI = 240        # 4 s

PLAYER_COLORS = ["#DC5050", "#5050DC", "#50C864", "#DCDC50"]


# ── spawn positions (mirrors original Player.__init__) ────────────────

def _player_start(number: int, W: int, H: int):
    sz = PLAYER_SIZE
    if number == 1: return (W//2 - 100, 60)
    if number == 2: return (W//2 + 50,  H - 60 - sz)
    if number == 3: return (60,          H//2 - 50)
    return              (W - 60 - sz,  H//2 + 50)


# ── data classes ──────────────────────────────────────────────────────

class TNTPlayer:
    def __init__(self, player_id: str, number: int, W: int, H: int):
        self.player_id    = player_id
        self.number       = number
        self.color        = PLAYER_COLORS[number - 1]
        self.size         = PLAYER_SIZE
        x, y              = _player_start(number, W, H)
        self.x            = float(x)
        self.y            = float(y)
        self.hp           = PLAYER_HP
        self.eliminated   = False
        self.stun         = 0           # ticks
        self.held_crate   = False       # bool: is holding a crate?
        self.last_move_x  = 0.0
        self.last_move_y  = -1.0        # default throw direction: upward

    @property
    def cx(self): return self.x + self.size / 2
    @property
    def cy(self): return self.y + self.size / 2

    def throw_direction(self):
        """Normalised (dx, dy) in the last-moved direction."""
        lx, ly = self.last_move_x, self.last_move_y
        length = math.hypot(lx, ly)
        if length == 0:
            return (0.0, -1.0)
        return (lx / length, ly / length)

    def to_dict(self) -> dict:
        return {
            "player_id":  self.player_id,
            "number":     self.number,
            "color":      self.color,
            "x":          round(self.x, 1),
            "y":          round(self.y, 1),
            "size":       self.size,
            "hp":         self.hp,
            "max_hp":     PLAYER_HP,
            "eliminated": self.eliminated,
            "stunned":    self.stun > 0,
            "held_crate": self.held_crate,
        }


class PickupCrate:
    _ctr = 0

    def __init__(self, x: float, y: float):
        PickupCrate._ctr += 1
        self.crate_id = f"pc_{PickupCrate._ctr}"
        self.x  = x
        self.y  = y
        self.size = CRATE_SIZE

    def to_dict(self) -> dict:
        return {"crate_id": self.crate_id,
                "x": round(self.x, 1), "y": round(self.y, 1),
                "size": self.size}


class ThrownCrate:
    _ctr = 0

    def __init__(self, owner_id: str, x: float, y: float, vx: float, vy: float):
        ThrownCrate._ctr += 1
        self.thrown_id = f"tc_{ThrownCrate._ctr}"
        self.owner_id  = owner_id
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.size      = THROWN_SIZE
        self.rotation  = 0.0
        self.rot_speed = random.uniform(-5, 5)

    def update(self):
        self.x  += self.vx
        self.y  += self.vy
        self.vy += CRATE_GRAVITY
        self.vx *= CRATE_DRAG
        self.vy *= CRATE_DRAG
        self.rotation += self.rot_speed

    def out_of_bounds(self, W: int, H: int) -> bool:
        return self.y > H + 40 or self.x < -40 or self.x > W + 40

    def to_dict(self) -> dict:
        return {
            "thrown_id": self.thrown_id,
            "owner_id":  self.owner_id,
            "x":         round(self.x, 1),
            "y":         round(self.y, 1),
            "size":      self.size,
            "rotation":  round(self.rotation, 1),
        }


class ExplosionLogic:
    """
    Pure-data port of your Explosion class.
    apply() is called immediately on creation (mirrors original code where
    ex.apply(players) is called right after Explosion(...)).
    Tracks remaining life for the renderer.
    """
    _ctr = 0

    def __init__(self, x: float, y: float,
                 radius=EXPLODE_RADIUS, damage=EXPLODE_DAMAGE,
                 lifetime=EXPLODE_LIFE):
        ExplosionLogic._ctr += 1
        self.ex_id    = f"ex_{ExplosionLogic._ctr}"
        self.x        = x
        self.y        = y
        self.radius   = radius
        self.damage   = damage
        self.timer    = lifetime
        self.max_life = lifetime

    def apply(self, players: list):
        """
        Exact port of Explosion.apply():
          frac = max(0.2, 1 - dist/radius)
          damage = int(self.damage * frac)
          knockback = 10 * frac (along line from explosion centre to player)
          stun = int(15 * frac)
        """
        for p in players:
            if p.eliminated:
                continue
            dx   = p.cx - self.x
            dy   = p.cy - self.y
            dist = math.hypot(dx, dy)
            if dist > self.radius:
                continue

            frac   = max(0.2, 1.0 - dist / self.radius)
            damage = int(self.damage * frac)
            p.hp  -= damage

            # knockback
            if dist > 0:
                kb  = 10 * frac
                p.x += dx / dist * kb
                p.y += dy / dist * kb

            # stun
            p.stun = max(p.stun, int(15 * frac))

            if p.hp <= 0:
                p.hp        = 0
                p.eliminated = True

    def tick(self):
        self.timer -= 1

    def alive(self) -> bool:
        return self.timer > 0

    def to_dict(self) -> dict:
        return {
            "ex_id":   self.ex_id,
            "x":       round(self.x, 1),
            "y":       round(self.y, 1),
            "radius":  self.radius,
            "frac":    round(self.timer / self.max_life, 2),  # for renderer fade
        }


# ── main game ─────────────────────────────────────────────────────────

class TnTBattleGame(BaseHeadlessGame):
    """
    Headless TNT Battle.
    update() advances one tick and returns get_state().
    """

    def __init__(self, game_id: str, player_ids: list, config: dict = None):
        super().__init__(game_id, player_ids, config)
        W, H = self.ARENA_W, self.ARENA_H

        self.players: dict[str, TNTPlayer] = {}
        for i, pid in enumerate(player_ids[:4]):
            self.players[pid] = TNTPlayer(pid, i + 1, W, H)

        self.pickup_crates:  list[PickupCrate]    = []
        self.thrown_crates:  list[ThrownCrate]    = []
        self.explosions:     list[ExplosionLogic] = []
        self.hit_events:     list[dict]           = []  # per-tick, for renderer

        # Start with a short timer so first crates appear soon
        self.crate_spawn_timer = 60

    # ── tick ──────────────────────────────────────────────────────────

    def update(self, inputs: dict, dt: float = None) -> dict:
        if self._over:
            return self.get_state()

        self.tick       += 1
        self.hit_events  = []
        W, H = self.ARENA_W, self.ARENA_H
        alive = [p for p in self.players.values() if not p.eliminated]

        # 1. Spawn pickup crates ----------------------------------------
        self.crate_spawn_timer -= 1
        if (self.crate_spawn_timer <= 0 and
                len(self.pickup_crates) < MAX_CRATES):
            self._try_spawn_crate(alive, W, H)
            self.crate_spawn_timer = random.randint(CRATE_SPAWN_LO, CRATE_SPAWN_HI)

        # 2. Move players + pickup / throw logic ------------------------
        for p in alive:
            inp = inputs.get(p.player_id, InputState.neutral(p.player_id))
            self._move_player(p, inp, W, H)

            # pickup
            if not p.held_crate:
                for crate in self.pickup_crates[:]:
                    if self.rects_overlap(p.x, p.y, p.size, p.size,
                                          crate.x, crate.y, crate.size, crate.size):
                        p.held_crate = True
                        self.pickup_crates.remove(crate)
                        break

            # throw
            if p.held_crate and inp.action and p.stun <= 0:
                thrown = self._throw(p)
                self.thrown_crates.append(thrown)
                p.held_crate = False
                p.stun = THROW_STUN

        # 3. Update thrown crates + hit detection ----------------------
        for tc in self.thrown_crates[:]:
            tc.update()

            if tc.out_of_bounds(W, H):
                self.thrown_crates.remove(tc)
                continue

            hit = False
            for p in alive:
                if p.player_id == tc.owner_id:
                    continue
                if self.rects_overlap(tc.x, tc.y, tc.size, tc.size,
                                      p.x,  p.y,  p.size, p.size):
                    # Explosion at crate centre
                    ex_x = tc.x + tc.size / 2
                    ex_y = tc.y + tc.size / 2
                    ex   = ExplosionLogic(ex_x, ex_y)
                    ex.apply(alive)
                    self.explosions.append(ex)
                    self.hit_events.append({
                        "owner_id":  tc.owner_id,
                        "target_id": p.player_id,
                        "x": ex_x, "y": ex_y,
                    })
                    self.thrown_crates.remove(tc)
                    hit = True
                    break

        # 4. Age explosions --------------------------------------------
        for ex in self.explosions[:]:
            ex.tick()
            if not ex.alive():
                self.explosions.remove(ex)

        # 5. Tick stun countdown -------------------------------------
        for p in alive:
            if p.stun > 0:
                p.stun -= 1

        # 6. Win check -----------------------------------------------
        alive_now = [p for p in self.players.values() if not p.eliminated]
        if len(alive_now) <= 1:
            winner = alive_now[0].player_id if alive_now else None
            self._end_game(winner=winner)

        return self.get_state()

    # ── helpers ───────────────────────────────────────────────────────

    def _move_player(self, p: TNTPlayer, inp: InputState, W: int, H: int):
        if p.stun > 0:
            return

        spd  = PLAYER_SPEED
        move_x, move_y = 0.0, 0.0

        if inp.up:    p.y -= spd; move_y = -1.0
        if inp.down:  p.y += spd; move_y =  1.0
        if inp.left:  p.x -= spd; move_x = -1.0
        if inp.right: p.x += spd; move_x =  1.0

        if move_x != 0 or move_y != 0:
            p.last_move_x = move_x
            p.last_move_y = move_y

        # boundary clamp (mirrors original: max 20, min w-20-size)
        p.x = self.clamp(p.x, 20, W - 20 - p.size)
        p.y = self.clamp(p.y, 20, H - 20 - p.size)

    def _throw(self, p: TNTPlayer) -> ThrownCrate:
        dx, dy = p.throw_direction()
        return ThrownCrate(
            owner_id = p.player_id,
            x        = p.cx - THROWN_SIZE / 2,
            y        = p.cy - THROWN_SIZE / 2,
            vx       = dx * THROW_POWER,
            vy       = dy * THROW_POWER,
        )

    def _try_spawn_crate(self, alive: list, W: int, H: int):
        """Spawn a crate not too close to any live player (mirrors original)."""
        for _ in range(10):   # up to 10 attempts
            x = float(random.randint(50, W - 50))
            y = float(random.randint(50, H - 50))
            too_close = any(
                math.hypot(p.x - x, p.y - y) < 100
                for p in alive
            )
            if not too_close:
                self.pickup_crates.append(PickupCrate(x, y))
                return

    # ── state snapshot ────────────────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "game_id":       self.game_id,
            "game_type":     "tnt_battle",
            "tick":          self.tick,
            "arena_w":       self.ARENA_W,
            "arena_h":       self.ARENA_H,
            "players":       {pid: p.to_dict() for pid, p in self.players.items()},
            "pickup_crates": [c.to_dict() for c in self.pickup_crates],
            "thrown_crates": [c.to_dict() for c in self.thrown_crates],
            "explosions":    [e.to_dict() for e in self.explosions],
            "hit_events":    self.hit_events,
            "game_over":     self._over,
            "winner":        self._winner,
            "elapsed":       round(self.elapsed(), 2),
        }