# base_game.py
# Shared foundation – no pygame.display, no pygame.draw, no screen.
# pygame.Rect / math are pure data structures and are allowed.

import time
import math
from abc import ABC, abstractmethod


class InputState:
    """
    Normalised per-player input for one tick.
    The server (or local test harness) fills this in from
    keyboard events, touch events, or bot decisions.
    """
    __slots__ = ("player_id", "up", "down", "left", "right", "action", "jump")

    def __init__(self, player_id: str,
                 up=False, down=False, left=False, right=False, action=False, jump=False):
        self.player_id = player_id
        self.up     = up
        self.down   = down
        self.left   = left
        self.right  = right
        self.action = action  # "throw" in TNT, unused in CrashBash
        self.jump   = jump    # TNT Battle jump

    @classmethod
    def neutral(cls, player_id: str) -> "InputState":
        return cls(player_id)

    @classmethod
    def from_dict(cls, player_id: str, d: dict) -> "InputState":
        return cls(player_id,
                   up     = bool(d.get("up")),
                   down   = bool(d.get("down")),
                   left   = bool(d.get("left")),
                   right  = bool(d.get("right")),
                   action = bool(d.get("action")),
                   jump   = bool(d.get("jump")))

    def to_dict(self) -> dict:
        return {"up": self.up, "down": self.down,
                "left": self.left, "right": self.right,
                "action": self.action, "jump": self.jump}


class BaseHeadlessGame(ABC):
    """
    Contract every headless game must honour:
      update(inputs, dt)  – advance one tick; inputs = {player_id: InputState}
      get_state()         – return a JSON-serialisable dict
      is_over()           – True once the game has ended
      get_winner()        – player_id string, or None
    """

    ARENA_W   = 800
    ARENA_H   = 600
    TICK_RATE = 60
    TICK_DT   = 1.0 / TICK_RATE

    def __init__(self, game_id: str, player_ids: list, config: dict = None):
        self.game_id    = game_id
        self.player_ids = list(player_ids)
        self.config     = config or {}
        self.tick       = 0
        self._over      = False
        self._winner    = None
        self._started   = time.time()

    # ── subclass interface ────────────────────────────────────────────

    @abstractmethod
    def update(self, inputs: dict, dt: float = None) -> dict:
        ...

    @abstractmethod
    def get_state(self) -> dict:
        ...

    # ── helpers ───────────────────────────────────────────────────────

    def is_over(self)   -> bool: return self._over
    def get_winner(self):        return self._winner
    def elapsed(self)   -> float: return time.time() - self._started

    def _end_game(self, winner=None):
        self._over   = True
        self._winner = winner

    @staticmethod
    def clamp(v, lo, hi): return max(lo, min(hi, v))

    @staticmethod
    def rects_overlap(ax, ay, aw, ah, bx, by, bw, bh) -> bool:
        return ax < bx+bw and ax+aw > bx and ay < by+bh and ay+ah > by