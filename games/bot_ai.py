# bot_ai.py  –  Server-side bot AI for both headless games.
#
# Usage:
#   bot = BotFactory.create("crash_bash",  bot_id, difficulty="medium")
#   bot = BotFactory.create("tnt_battle",  bot_id, difficulty="medium")
#
#   inp = bot.decide(game.get_state())   # call once per tick per bot

import math
import random
from base_game import InputState


# ── helpers ────────────────────────────────────────────────────────────

def dist(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


# ── base ───────────────────────────────────────────────────────────────

class BotAI:
    DIFFICULTY = {
        # skip=chance of doing nothing (reaction lag), noise=chance of random move
        "easy":   {"skip": 0.60, "noise": 0.65},
        "medium": {"skip": 0.30, "noise": 0.35},
        "hard":   {"skip": 0.08, "noise": 0.12},
    }

    def __init__(self, bot_id: str, difficulty: str = "medium"):
        self.bot_id     = bot_id
        self.difficulty = difficulty
        cfg = self.DIFFICULTY.get(difficulty, self.DIFFICULTY["medium"])
        self._skip      = cfg["skip"]    # probability of returning neutral this tick
        self._noise     = cfg["noise"]   # probability of a random action
        self._last_inp  = InputState.neutral(bot_id)

    def decide(self, state: dict) -> InputState:
        if random.random() < self._skip:
            return self._last_inp          # reaction-time delay

        inp = self._think(state)

        if random.random() < self._noise:
            inp = self._random_input()

        self._last_inp = inp
        return inp

    def _think(self, state: dict) -> InputState:
        raise NotImplementedError

    def _my(self, state: dict) -> dict | None:
        return state.get("players", {}).get(self.bot_id)

    def _others(self, state: dict) -> list[dict]:
        return [p for pid, p in state.get("players", {}).items()
                if pid != self.bot_id and not p.get("eliminated", False)]

    def _random_input(self) -> InputState:
        return InputState(self.bot_id,
                          up     = random.random() < 0.3,
                          down   = random.random() < 0.3,
                          left   = random.random() < 0.4,
                          right  = random.random() < 0.4,
                          action = random.random() < 0.15)


# ── Crash Bash bot ─────────────────────────────────────────────────────
#
# The CrashBash players are CONSTRAINED to move only along one axis
# (top/bottom players are horizontal; left/right players are vertical).
# The bot's job is to position itself so the ball doesn't enter its goal.
#
# Strategy:
#   • Find the ball that is heading most directly toward THIS player's goal.
#   • Move to intercept it (centre of player aligns with ball centre).
#   • If no threatening ball, stay near the centre of the goal.

class CrashBashBot(BotAI):
    def __init__(self, bot_id: str, difficulty: str = "medium"):
        super().__init__(bot_id, difficulty)
        self._target_x = None  # smoothed target position
        self._target_y = None
        # Sluggish on easy, snappy on hard
        self._smooth_factor = {"easy": 0.04, "medium": 0.09, "hard": 0.22}.get(difficulty, 0.09)
        
    def _think(self, state: dict) -> InputState:
        me = self._my(state)
        if me is None or me.get("eliminated"):
            return InputState.neutral(self.bot_id)

        side = me.get("side", "bottom")
        mx   = me["x"] + me.get("size", 36) / 2
        my_  = me["y"] + me.get("size", 36) / 2

        balls = state.get("balls", [])
        if not balls:
            return InputState.neutral(self.bot_id)

        # Pick most threatening ball
        goal_rect = state.get("goals", {}).get(self.bot_id, {}).get("rect", [0,0,0,0])
        gx, gy, gw, gh = goal_rect
        gcx = gx + gw / 2
        gcy = gy + gh / 2

        best_ball = min(balls, key=lambda b: dist(b["x"] + b.get("size",18)/2,
                                                   b["y"] + b.get("size",18)/2,
                                                   gcx, gcy))
        bcx = best_ball["x"] + best_ball.get("size", 18) / 2
        bcy = best_ball["y"] + best_ball.get("size", 18) / 2

        # Calculate desired position (aligned with ball)
        if side in ("top", "bottom"):
            desired_x = bcx - me.get("size", 36) / 2
            # Add some randomness based on difficulty
            if random.random() < self._noise:
                desired_x += random.uniform(-20, 20)
            
            # Smooth movement (prevents instant teleportation)
            if self._target_x is None:
                self._target_x = desired_x
            else:
                self._target_x += (desired_x - self._target_x) * self._smooth_factor
            
            diff = self._target_x - mx
            dz = {"easy": 30, "medium": 14, "hard": 4}.get(self.difficulty, 14)
            return InputState(self.bot_id,
                              left  = diff < -dz,
                              right = diff >  dz)
        else:
            desired_y = bcy - me.get("size", 36) / 2
            if random.random() < self._noise:
                desired_y += random.uniform(-20, 20)
            
            if self._target_y is None:
                self._target_y = desired_y
            else:
                self._target_y += (desired_y - self._target_y) * self._smooth_factor
            
            diff = self._target_y - my_
            dz = {"easy": 30, "medium": 14, "hard": 4}.get(self.difficulty, 14)
            return InputState(self.bot_id,
                              up   = diff < -dz,
                              down = diff >  dz)

# ── TNT Battle bot ─────────────────────────────────────────────────────
#
# Strategy:
#   • If not holding a crate: move toward the nearest pickup crate.
#   • If holding a crate: move toward the nearest enemy.
#     When close enough (< 160 px) or facing them, throw.
#   • Dodge: if a thrown crate is heading toward us, move perpendicular.

class TntBattleBot(BotAI):
    def __init__(self, bot_id: str, difficulty: str = "medium"):
        super().__init__(bot_id, difficulty)
        self._smooth_x = None
        self._smooth_y = None
        self._smooth_factor = {"easy": 0.05, "medium": 0.12, "hard": 0.25}.get(difficulty, 0.12)
        
    def _move_toward(self, tx, ty, mx, my_) -> InputState:
        # Smooth target position
        if self._smooth_x is None:
            self._smooth_x = tx
            self._smooth_y = ty
        else:
            self._smooth_x += (tx - self._smooth_x) * self._smooth_factor
            self._smooth_y += (ty - self._smooth_y) * self._smooth_factor

        dx = self._smooth_x - mx
        dy = self._smooth_y - my_

        # Add small random deadzone based on difficulty
        deadzone = {"easy": 28, "medium": 14, "hard": 4}.get(self.difficulty, 14)
        return InputState(self.bot_id,
                          left  = dx < -deadzone,
                          right = dx > deadzone,
                          up    = dy < -deadzone,
                          down  = dy > deadzone)

    # expose arena size for wander target
    ARENA_W = 800
    ARENA_H = 600

    def _think(self, state: dict) -> InputState:
        me = self._my(state)
        if me is None or me.get("eliminated"):
            return InputState.neutral(self.bot_id)

        mx = me["x"] + me.get("size", 36) / 2
        my_ = me["y"] + me.get("size", 36) / 2

        enemies = self._others(state)

        # ── No crate held ────────────────────────────────────────────
        if not me.get("held_crate"):
            # Melee punch: if an enemy is within range and cooldown is up, punch!
            melee_ready = me.get("melee_ready", True)
            # Easy bots never melee; medium/hard scale the trigger range
            melee_range = {"easy": 0, "medium": 55, "hard": 75}.get(self.difficulty, 55)
            if melee_ready and enemies and melee_range > 0:
                nearest_enemy = min(enemies, key=lambda p: dist(
                    mx, my_,
                    p["x"] + p.get("size", 36) / 2,
                    p["y"] + p.get("size", 36) / 2))
                ex = nearest_enemy["x"] + nearest_enemy.get("size", 36) / 2
                ey = nearest_enemy["y"] + nearest_enemy.get("size", 36) / 2
                enemy_dist = dist(mx, my_, ex, ey)
                if enemy_dist < melee_range:   # within melee range → punch & chase
                    inp = self._move_toward(ex, ey, mx, my_)
                    return InputState(self.bot_id,
                                      up=inp.up, down=inp.down,
                                      left=inp.left, right=inp.right,
                                      action=True)

            # Otherwise go for health fruits or crates as usual
            targets = []
            if me.get("hp", 100) < 60:
                for f in state.get("health_fruits", []):
                    targets.append((f["x"] + f.get("size", 20) / 2,
                                    f["y"] + f.get("size", 20) / 2))
            for c in state.get("pickup_crates", []):
                targets.append((c["x"] + c.get("size", 32) / 2,
                                 c["y"] + c.get("size", 32) / 2))

            if targets:
                tx, ty = min(targets, key=lambda t: dist(mx, my_, t[0], t[1]))
                return self._move_toward(tx, ty, mx, my_)
            # Wander toward centre if nothing to pick up
            return self._move_toward(self.ARENA_W / 2, self.ARENA_H / 2, mx, my_)

        # ── Holding crate – chase nearest enemy and throw ────────────
        if not enemies:
            return InputState.neutral(self.bot_id)

        target = min(enemies, key=lambda p: dist(mx, my_,
                                                  p["x"] + p.get("size", 36) / 2,
                                                  p["y"] + p.get("size", 36) / 2))
        tx = target["x"] + target.get("size", 36) / 2
        ty = target["y"] + target.get("size", 36) / 2
        d  = dist(mx, my_, tx, ty)

        # Easy bots throw too early (inaccurate); hard bots wait for better range
        throw_range = {"easy": 260, "medium": 175, "hard": 130}.get(self.difficulty, 175)
        throw = d < throw_range
        inp = self._move_toward(tx, ty, mx, my_)
        return InputState(self.bot_id,
                          up=inp.up, down=inp.down,
                          left=inp.left, right=inp.right,
                          action=throw)

# ── factory ────────────────────────────────────────────────────────────

class BotFactory:
    _MAP = {
        "crash_bash": CrashBashBot,
        "tnt_battle": TntBattleBot,
    }

    @classmethod
    def create(cls, game_type: str, bot_id: str,
               difficulty: str = "medium") -> BotAI:
        klass = cls._MAP.get(game_type)
        if klass is None:
            raise ValueError(f"No bot AI for game_type='{game_type}'")
        return klass(bot_id, difficulty)