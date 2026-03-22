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
        "easy":   {"skip": 0.30, "noise": 0.40},
        "medium": {"skip": 0.12, "noise": 0.20},
        "hard":   {"skip": 0.03, "noise": 0.06},
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

    def _think(self, state: dict) -> InputState:
        me = self._my(state)
        if me is None or me.get("eliminated"):
            return InputState.neutral(self.bot_id)

        side = me.get("side", "bottom")
        mx   = me["x"] + me.get("size", 36) / 2   # my centre X
        my_  = me["y"] + me.get("size", 36) / 2   # my centre Y

        balls = state.get("balls", [])
        if not balls:
            return InputState.neutral(self.bot_id)

        # Pick most threatening ball: closest to my goal strip
        goal_rect = state.get("goals", {}).get(self.bot_id, {}).get("rect", [0,0,0,0])
        gx, gy, gw, gh = goal_rect

        # goal centre
        gcx = gx + gw / 2
        gcy = gy + gh / 2

        best_ball = min(balls, key=lambda b: dist(b["x"] + b.get("size",18)/2,
                                                   b["y"] + b.get("size",18)/2,
                                                   gcx, gcy))
        bcx = best_ball["x"] + best_ball.get("size", 18) / 2
        bcy = best_ball["y"] + best_ball.get("size", 18) / 2

        # Decide movement: only move on the axis that matters
        if side in ("top", "bottom"):
            # move left/right to align X with ball X
            diff = bcx - mx
            return InputState(self.bot_id,
                              left  = diff < -4,
                              right = diff >  4)
        else:
            # move up/down to align Y with ball Y
            diff = bcy - my_
            return InputState(self.bot_id,
                              up   = diff < -4,
                              down = diff >  4)


# ── TNT Battle bot ─────────────────────────────────────────────────────
#
# Strategy:
#   • If not holding a crate: move toward the nearest pickup crate.
#   • If holding a crate: move toward the nearest enemy.
#     When close enough (< 160 px) or facing them, throw.
#   • Dodge: if a thrown crate is heading toward us, move perpendicular.

class TntBattleBot(BotAI):

    def _think(self, state: dict) -> InputState:
        me = self._my(state)
        if me is None or me.get("eliminated"):
            return InputState.neutral(self.bot_id)

        mx, my_ = me["x"] + me.get("size",36)/2, me["y"] + me.get("size",36)/2
        holding  = me.get("held_crate", False)
        stunned  = me.get("stunned", False)
        enemies  = self._others(state)

        if stunned:
            return InputState.neutral(self.bot_id)

        # ── dodge incoming crates ──────────────────────────────────────
        dodge_x, dodge_y = 0.0, 0.0
        for tc in state.get("thrown_crates", []):
            if tc.get("owner_id") == self.bot_id:
                continue
            dx = tc["x"] - mx
            dy = tc["y"] - my_
            if dist(tc["x"], tc["y"], mx, my_) < 100:
                # move perpendicular to incoming vector
                dodge_x = -dy
                dodge_y =  dx

        if math.hypot(dodge_x, dodge_y) > 0:
            # normalise
            n = math.hypot(dodge_x, dodge_y)
            return InputState(self.bot_id,
                              left  = dodge_x / n < -0.3,
                              right = dodge_x / n >  0.3,
                              up    = dodge_y / n < -0.3,
                              down  = dodge_y / n >  0.3)

        # ── go pick up a crate ────────────────────────────────────────
        if not holding:
            crates = state.get("pickup_crates", [])
            if crates:
                nearest = min(crates, key=lambda c: dist(c["x"], c["y"], mx, my_))
                return self._move_toward(nearest["x"], nearest["y"], mx, my_)
            # no crates – wander toward centre
            return self._move_toward(self.ARENA_W / 2, self.ARENA_H / 2, mx, my_)

        # ── holding crate: hunt nearest enemy ────────────────────────
        if not enemies:
            return InputState.neutral(self.bot_id)

        target = min(enemies, key=lambda p: dist(p["x"], p["y"], mx, my_))
        tx     = target["x"] + target.get("size", 36) / 2
        ty     = target["y"] + target.get("size", 36) / 2
        d      = dist(tx, ty, mx, my_)

        throw_now = d < 160 or (self.difficulty == "hard" and d < 240)

        inp = self._move_toward(tx, ty, mx, my_)
        return InputState(self.bot_id,
                          up     = inp.up,
                          down   = inp.down,
                          left   = inp.left,
                          right  = inp.right,
                          action = throw_now)

    def _move_toward(self, tx, ty, mx, my_) -> InputState:
        dx = tx - mx
        dy = ty - my_
        return InputState(self.bot_id,
                          left  = dx < -8,
                          right = dx >  8,
                          up    = dy < -8,
                          down  = dy >  8)

    # expose arena size for wander target
    ARENA_W = 800
    ARENA_H = 600


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