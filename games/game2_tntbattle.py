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

PLAYER_SPEED   = 7          # px/tick
PLAYER_SIZE    = 36
PLAYER_HP      = 100
CRATE_SIZE     = 32         # pickup crate
THROWN_SIZE    = 24         # thrown crate
THROW_POWER    = 15         # initial speed of thrown crate (px/tick)
CRATE_GRAVITY  = 0.5        # px/tick² applied to thrown crates
CRATE_DRAG     = 0.99       # velocity multiplier per tick
THROW_STUN     = 10         # ticks after throwing
EXPLODE_RADIUS = 60         # px
EXPLODE_DAMAGE = 40
EXPLODE_LIFE   = 25         # ticks the explosion visual lasts
MAX_CRATES     = 8
CRATE_SPAWN_LO = 120        # ticks (2 s at 60 tps)
CRATE_SPAWN_HI = 240        # 4 s

PLAYER_COLORS = ["#5050DC", "#50C864", "#DCDC50","#DC5050"]

# ── melee punch (action when no crate held) ───────────────────────────
MELEE_RANGE    = 80          # px – max distance to land a punch
MELEE_DAMAGE   = 15          # HP deducted per punch
MELEE_KNOCKBACK = 8          # px applied toward target
MELEE_COOLDOWN = 30          # ticks between punches (0.5 s at 60 tps)

# ── health fruit ──────────────────────────────────────────────────────
FRUIT_SIZE        = 20          # px square
FRUIT_HEAL        = 30          # HP restored on pickup
MAX_FRUITS        = 3           # max fruits on screen at once
FRUIT_SPAWN_LO    = 180         # ticks (~3 s)
FRUIT_SPAWN_HI    = 360         # ticks (~6 s)

# ── 500 lbs weight ────────────────────────────────────────────────────
WEIGHT_SIZE       = 40          # px square (large, imposing)
WEIGHT_SPAWN_TICK = 900         # tick 900 = 15 s at 60 tps
WEIGHT_HOLD_TICKS = 720        # 10 s after pickup → holder is eliminated
WEIGHT_TRANSFER_RANGE = 50      # px – touching an enemy transfers the curse


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
        self.melee_cooldown = 0         # ticks until next punch is allowed
        self.has_weight     = False     # True while cursed by 500 lbs
        self.weight_timer   = 0         # ticks remaining before elimination

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
            "melee_ready": self.melee_cooldown <= 0,
            "has_weight":  self.has_weight,
            "weight_timer": self.weight_timer,
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


class HealthFruit:
    """Green fruit that restores HP when a player walks over it."""
    _ctr = 0

    def __init__(self, x: float, y: float):
        HealthFruit._ctr += 1
        self.fruit_id = f"hf_{HealthFruit._ctr}"
        self.x    = x
        self.y    = y
        self.size = FRUIT_SIZE
        self.heal = FRUIT_HEAL

    def to_dict(self) -> dict:
        return {
            "fruit_id": self.fruit_id,
            "x":        round(self.x, 1),
            "y":        round(self.y, 1),
            "size":     self.size,
            "heal":     self.heal,
        }


class WeightItem:
    """
    The 500 lbs weight.  Spawns at arena centre first at WEIGHT_SPAWN_TICK,
    then respawns automatically once the current curse resolves (player
    crushed or… the curse clears) after another WEIGHT_SPAWN_TICK delay.
    Walking over it curses the player: after WEIGHT_HOLD_TICKS that player
    is instantly eliminated unless they transferred the curse by touching an
    enemy (who then inherits the remaining timer).
    """

    def __init__(self, x: float, y: float):
        self.x    = x
        self.y    = y
        self.size = WEIGHT_SIZE
        self.active = True   # False once someone picks it up

    def to_dict(self) -> dict:
        return {
            "x":    round(self.x, 1),
            "y":    round(self.y, 1),
            "size": self.size,
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
        color_map = (config or {}).get("colors", {})

        self.players: dict[str, TNTPlayer] = {}
        for i, pid in enumerate(player_ids[:4]):
            p = TNTPlayer(pid, i + 1, W, H)
            if pid in color_map:
                p.color = color_map[pid]
            self.players[pid] = p

        # ── Team mode ──────────────────────────────────────────────────
        # config["teams"] = {"A": [pid1, pid2], "B": [pid3, pid4]}
        # In team mode: thrown crates and melee do NOT damage teammates.
        # Win condition: a team wins when ALL enemy players are eliminated.
        raw_teams = self.config.get("teams")
        self.teams = None
        self.player_team: dict = {}
        self._winner_display = None

        if raw_teams and len(raw_teams) >= 2:
            TEAM_COLORS = ["#E05050", "#5080E0", "#40C878", "#D4D440"]
            self.teams = {}
            for t_idx, (tid, members) in enumerate(raw_teams.items()):
                tc = TEAM_COLORS[t_idx % len(TEAM_COLORS)]
                self.teams[tid] = {"member_ids": list(members), "color": tc, "eliminated": False}
                for pid in members:
                    self.player_team[pid] = tid
                    if pid in self.players:
                        self.players[pid].color = tc

        self.pickup_crates  = []
        self.thrown_crates  = []
        self.explosions     = []
        self.health_fruits  = []
        self.hit_events     = []

        # 500 lbs weight – respawns continuously
        self.weight: WeightItem | None = None
        self._weight_respawn_timer = WEIGHT_SPAWN_TICK  # first spawn at 15 s

        # Start with a short timer so first crates appear soon
        self.crate_spawn_timer = 60
        self.fruit_spawn_timer = 240   # first fruit after ~4 s

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

        # 1b. Spawn health fruits ----------------------------------------
        self.fruit_spawn_timer -= 1
        if (self.fruit_spawn_timer <= 0 and
                len(self.health_fruits) < MAX_FRUITS):
            self._try_spawn_fruit(alive, W, H)
            self.fruit_spawn_timer = random.randint(FRUIT_SPAWN_LO, FRUIT_SPAWN_HI)

        # 1c. Spawn 500 lbs weight (respawns when no curse is active) --------
        # Only tick the respawn timer when nobody is cursed and weight is off the ground
        anyone_cursed = any(p.has_weight for p in self.players.values())
        if not anyone_cursed and not (self.weight and self.weight.active):
            self._weight_respawn_timer -= 1
            if self._weight_respawn_timer <= 0:
                self.weight = WeightItem(float(W // 2 - WEIGHT_SIZE // 2),
                                         float(H // 2 - WEIGHT_SIZE // 2))
                self._weight_respawn_timer = WEIGHT_SPAWN_TICK  # reset for next cycle

        # 2. Move players + pickup / throw logic ------------------------
        weight_just_received = set()   # player_ids that got the weight this tick
        for p in alive:
            inp = inputs.get(p.player_id, InputState.neutral(p.player_id))
            self._move_player(p, inp, W, H)

            # pickup health fruit
            for fruit in self.health_fruits[:]:
                if self.rects_overlap(p.x, p.y, p.size, p.size,
                                      fruit.x, fruit.y, fruit.size, fruit.size):
                    p.hp = min(PLAYER_HP, p.hp + fruit.heal)
                    self.health_fruits.remove(fruit)
                    break

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

            # melee punch (action while NOT holding a crate)
            elif not p.held_crate and inp.action and p.stun <= 0 and p.melee_cooldown <= 0:
                self._melee_punch(p, alive)

            # ── 500 lbs weight pickup ────────────────────────────────
            if (self.weight and self.weight.active and
                    self.rects_overlap(p.x, p.y, p.size, p.size,
                                       self.weight.x, self.weight.y,
                                       self.weight.size, self.weight.size)):
                p.has_weight    = True
                p.weight_timer  = WEIGHT_HOLD_TICKS
                self.weight.active = False   # remove from arena

            # ── 500 lbs transfer (touching an enemy while cursed) ────
            if p.has_weight and p.player_id not in weight_just_received:
                for enemy in alive:
                    if enemy.player_id == p.player_id or enemy.has_weight:
                        continue
                    # Team mode: don't transfer to teammates
                    if (self.teams and
                            self.player_team.get(enemy.player_id) ==
                            self.player_team.get(p.player_id)):
                        continue
                    if math.hypot(p.cx - enemy.cx, p.cy - enemy.cy) < WEIGHT_TRANSFER_RANGE:
                        remaining                  = p.weight_timer
                        p.has_weight               = False
                        p.weight_timer             = 0
                        enemy.has_weight           = True
                        enemy.weight_timer         = remaining
                        weight_just_received.add(enemy.player_id)
                        self.hit_events.append({
                            "weight_transfer": True,
                            "from": p.player_id,
                            "to":   enemy.player_id,
                        })
                        break

        # 2b. Resolve player–player solid body collisions ---------------
        # Run a few iterations so multi-player pile-ups fully separate.
        for _ in range(3):
            for i in range(len(alive)):
                for j in range(i + 1, len(alive)):
                    self._separate_players(alive[i], alive[j], W, H)

        # tick melee cooldowns
        for p in alive:
            if p.melee_cooldown > 0:
                p.melee_cooldown -= 1

        # tick 500 lbs weight countdown ------------------------------------
        for p in alive:
            if p.has_weight:
                p.weight_timer -= 1
                if p.weight_timer <= 0:
                    p.has_weight   = False
                    p.weight_timer = 0
                    p.hp           = 0
                    p.eliminated   = True
                    self.hit_events.append({
                        "weight_crushed": True,
                        "target_id":      p.player_id,
                        "x": p.cx, "y": p.cy,
                    })

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
                # Team mode: skip teammates (no friendly fire from crates)
                if self.teams and self.player_team.get(p.player_id) == self.player_team.get(tc.owner_id):
                    continue
                if self.rects_overlap(tc.x, tc.y, tc.size, tc.size,
                                      p.x,  p.y,  p.size, p.size):
                    # Explosion at crate centre
                    ex_x = tc.x + tc.size / 2
                    ex_y = tc.y + tc.size / 2
                    ex   = ExplosionLogic(ex_x, ex_y)
                    # In team mode only apply to enemies, not teammates
                    if self.teams:
                        thrower_team = self.player_team.get(tc.owner_id)
                        targets = [pl for pl in alive
                                   if self.player_team.get(pl.player_id) != thrower_team]
                        ex.apply(targets)
                    else:
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
        if self.teams:
            # Team mode: a team is eliminated when all its members are eliminated
            for tid, team in self.teams.items():
                members_alive = [p for p in self.players.values()
                                 if p.player_id in team["member_ids"] and not p.eliminated]
                if not members_alive:
                    team["eliminated"] = True
            alive_teams = [tid for tid, t in self.teams.items() if not t["eliminated"]]
            if len(alive_teams) <= 1:
                if alive_teams:
                    winning_tid   = alive_teams[0]
                    winning_team  = self.teams[winning_tid]
                    names         = " & ".join(
                        self.config.get("names", {}).get(pid, pid)
                        for pid in winning_team["member_ids"]
                    )
                    self._winner_display = f"{names} WIN!"
                    self._end_game(winner=winning_tid)
                else:
                    self._winner_display = "Draw!"
                    self._end_game(winner=None)
        else:
            alive_now = [p for p in self.players.values() if not p.eliminated]
            if len(alive_now) <= 1:
                winner = alive_now[0].player_id if alive_now else None
                self._winner_display = (
                    f"{self.config.get('names', {}).get(winner, winner)} WINS!"
                    if winner else "Draw!"
                )
                self._end_game(winner=winner)

        return self.get_state()

    # ── helpers ───────────────────────────────────────────────────────

    def _separate_players(self, a: "TNTPlayer", b: "TNTPlayer", W: int, H: int):
        """
        Push two overlapping players apart so they don't clip through each other.
        Uses AABB overlap: find the axis of minimum penetration and push each
        player out by half the overlap on that axis.
        """
        # AABB overlap depths
        overlap_x = (a.size + b.size) / 2 - abs(a.cx - b.cx)
        overlap_y = (a.size + b.size) / 2 - abs(a.cy - b.cy)

        if overlap_x <= 0 or overlap_y <= 0:
            return   # not actually overlapping

        # Resolve along the axis with the smaller penetration (minimum push)
        if overlap_x < overlap_y:
            push = overlap_x / 2
            if a.cx < b.cx:
                a.x -= push
                b.x += push
            else:
                a.x += push
                b.x -= push
        else:
            push = overlap_y / 2
            if a.cy < b.cy:
                a.y -= push
                b.y += push
            else:
                a.y += push
                b.y -= push

        # Re-clamp both players to arena bounds
        for p in (a, b):
            p.x = self.clamp(p.x, 20, W - 20 - p.size)
            p.y = self.clamp(p.y, 20, H - 20 - p.size)

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

    def _melee_punch(self, attacker: "TNTPlayer", alive: list):
        """Deal melee damage to the nearest enemy within MELEE_RANGE (no friendly fire in team mode)."""
        best, best_dist = None, float("inf")
        for p in alive:
            if p.player_id == attacker.player_id:
                continue
            # Team mode: skip teammates
            if self.teams and self.player_team.get(p.player_id) == self.player_team.get(attacker.player_id):
                continue
            d = math.hypot(attacker.cx - p.cx, attacker.cy - p.cy)
            if d < best_dist:
                best, best_dist = p, d

        # Always apply cooldown to prevent spam, even on whiff
        attacker.melee_cooldown = MELEE_COOLDOWN

        if best is None or best_dist > MELEE_RANGE:
            return   # whiff

        best.hp -= MELEE_DAMAGE
        dx = best.cx - attacker.cx
        dy = best.cy - attacker.cy
        dist = math.hypot(dx, dy) or 1.0
        best.x += dx / dist * MELEE_KNOCKBACK
        best.y += dy / dist * MELEE_KNOCKBACK
        best.stun = max(best.stun, 8)

        if best.hp <= 0:
            best.hp = 0
            best.eliminated = True

        self.hit_events.append({
            "owner_id":  attacker.player_id,
            "target_id": best.player_id,
            "melee":     True,
            "x": best.cx,
            "y": best.cy,
        })

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

    def _try_spawn_fruit(self, alive: list, W: int, H: int):
        """Spawn a health fruit not too close to any live player."""
        for _ in range(10):
            x = float(random.randint(60, W - 60))
            y = float(random.randint(60, H - 60))
            too_close = any(
                math.hypot(p.x - x, p.y - y) < 80
                for p in alive
            )
            if not too_close:
                self.health_fruits.append(HealthFruit(x, y))
                return

    # ── state snapshot ────────────────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "game_id":        self.game_id,
            "game_type":      "tnt_battle",
            "tick":           self.tick,
            "arena_w":        self.ARENA_W,
            "arena_h":        self.ARENA_H,
            "players":        {pid: p.to_dict() for pid, p in self.players.items()},
            "pickup_crates":  [c.to_dict() for c in self.pickup_crates],
            "thrown_crates":  [c.to_dict() for c in self.thrown_crates],
            "explosions":     [e.to_dict() for e in self.explosions],
            "health_fruits":  [f.to_dict() for f in self.health_fruits],
            "hit_events":     self.hit_events,
            "weight":         self.weight.to_dict() if (self.weight and self.weight.active) else None,
            "game_over":      self._over,
            "winner":         self._winner,
            "winner_display": self._winner_display,
            "team_mode":      self.teams is not None,
            "teams":          self.teams,
            "player_team":    self.player_team,
            "elapsed":        round(self.elapsed(), 2),
        }