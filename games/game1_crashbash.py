# game1_crashbash.py  –  Crash Bash  (headless)
#
# Faithful translation of your PyGame original:
#   • 2-4 players, each guarding one side of the arena (top/bottom/left/right).
#   • 4 balls bounce around.  When a ball enters a player's goal strip, that
#     player loses 1 point.
#   • Each player starts with INITIAL_SCORE = 20 points (configurable).
#   • Reach 0 → eliminated.  Last player alive wins.
#   • Players can deflect balls by moving into them (push influence preserved).
#   • Ball speed starts slow and increases each time a player hits it.
#
# Nothing in this file imports pygame.display / pygame.draw / pygame.Surface.
# pygame.Rect is used only for AABB collision math (pure data).

import math
import random
from base_game import BaseHeadlessGame, InputState

# ── constants ─────────────────────────────────────────────────────────

INITIAL_SCORE = 20          # points each player starts with
PLAYER_SPEED  = 9           # px / tick  (matches original min(w,h)//160 ≈ 4-5)
BALL_SPEED_START = 3.5      # starting speed (slower for early game)
BALL_SPEED_MAX = 8.0        # maximum speed after many hits
BALL_SPEED_INCREMENT = 0.25  # speed increase per player hit
PLAYER_SIZE   = 36          # px square
BALL_SIZE     = 18          # px square  (used as diameter for circle)
GOAL_DEPTH    = 20          # px – how deep the scoring strip is
STUN_TICKS    = 16          # ticks player is stunned after ball hit
BALL_PUSH     = 0.35        # fraction of player speed added to ball on deflect
BALL_COUNT    = 4

PLAYER_COLORS = ["#DC5050", "#5050DC", "#50C864", "#DCDC50"]

# goal size as a fraction of the shorter arena side (original used min(w,h)//3)
GOAL_FRACTION = 1 / 3


def _goal_size(arena_w, arena_h):
    return int(min(arena_w, arena_h) * GOAL_FRACTION)


# ── goal geometry (mirrors original get_goal_rect) ────────────────────

def _goal_rect(side: str, arena_w: int, arena_h: int):
    """
    Returns (gx, gy, gw, gh) – the scoring rectangle for this side.
    Any ball centre inside this rect scores against the owning player.
    """
    g = _goal_size(arena_w, arena_h)
    W, H = arena_w, arena_h
    if side == "top":
        return (W//2 - g//2, 0, g, GOAL_DEPTH)
    if side == "bottom":
        return (W//2 - g//2, H - GOAL_DEPTH, g, GOAL_DEPTH)
    if side == "left":
        return (0, H//2 - g//2, GOAL_DEPTH, g)
    # right
    return (W - GOAL_DEPTH, H//2 - g//2, GOAL_DEPTH, g)


# ── player start positions (mirrors original Player.__init__) ─────────

def _player_start(number: int, arena_w: int, arena_h: int):
    """1-indexed.  Returns (x, y, side, movement_axis)."""
    W, H   = arena_w, arena_h
    sz     = PLAYER_SIZE
    # Place players very close to their goal strip (just inside the GOAL_DEPTH band)
    margin = GOAL_DEPTH + sz + 4   # a few px in front of the goal

    g      = _goal_size(W, H)

    if number == 1:   # top – moves horizontally
        return (W//2 - sz//2, margin, "top",    "horizontal")
    if number == 2:   # bottom – moves horizontally
        return (W//2 - sz//2, H - margin - sz,  "bottom", "horizontal")
    if number == 3:   # left – moves vertically
        return (margin,  H//2 - sz//2, "left",  "vertical")
    # right
    return (W - margin - sz, H//2 - sz//2, "right", "vertical")


# ── data classes ──────────────────────────────────────────────────────

class CBPlayer:
    def __init__(self, player_id: str, number: int, arena_w: int, arena_h: int):
        self.player_id  = player_id
        self.number     = number
        self.color      = PLAYER_COLORS[number - 1]
        self.size       = PLAYER_SIZE

        x, y, side, axis = _player_start(number, arena_w, arena_h)
        self.x, self.y  = float(x), float(y)
        self.side       = side      # "top" | "bottom" | "left" | "right"
        self.axis       = axis      # "horizontal" | "vertical"
        self.score      = INITIAL_SCORE
        self.stunned    = 0         # ticks remaining
        self.eliminated = False

    # axis-aligned bounding box corners
    @property
    def left(self):   return self.x
    @property
    def right(self):  return self.x + self.size
    @property
    def top(self):    return self.y
    @property
    def bottom(self): return self.y + self.size

    def to_dict(self) -> dict:
        return {
            "player_id":  self.player_id,
            "number":     self.number,
            "color":      self.color,
            "x":          round(self.x, 1),
            "y":          round(self.y, 1),
            "size":       self.size,
            "side":       self.side,
            "score":      self.score,
            "eliminated": self.eliminated,
            "stunned":    self.stunned > 0,
        }


class CBBall:
    _ctr = 0

    def __init__(self, arena_w: int, arena_h: int):
        CBBall._ctr += 1
        self.ball_id = f"ball_{CBBall._ctr}"
        self.size    = BALL_SIZE
        self.hit_count = 0          # track how many times ball has been hit by players
        self.current_speed = BALL_SPEED_START
        self._reset(arena_w, arena_h)

    def _reset(self, arena_w: int, arena_h: int):
        cx, cy = arena_w // 2, arena_h // 2
        sx = max(40, arena_w  // 6)
        sy = max(40, arena_h // 6)
        self.x  = float(random.randint(cx - sx, cx + sx))
        self.y  = float(random.randint(cy - sy, cy + sy))
        angle   = random.uniform(0, 2 * math.pi)
        self.current_speed = BALL_SPEED_START
        self.dx = math.cos(angle) * self.current_speed
        self.dy = math.sin(angle) * self.current_speed
        self._arena_w = arena_w
        self._arena_h = arena_h

    def reset(self):
        """Reset ball position and speed after scoring"""
        self.hit_count = 0
        self._reset(self._arena_w, self._arena_h)

    def increase_speed(self):
        """Increase ball speed when hit by a player"""
        self.hit_count += 1
        # Speed increases with each hit, up to maximum
        self.current_speed = min(BALL_SPEED_MAX, 
                                 BALL_SPEED_START + (self.hit_count * BALL_SPEED_INCREMENT))
        
        # Normalize current velocity and apply new speed
        current_magnitude = math.hypot(self.dx, self.dy)
        if current_magnitude > 0:
            self.dx = (self.dx / current_magnitude) * self.current_speed
            self.dy = (self.dy / current_magnitude) * self.current_speed

    @property
    def cx(self): return self.x + self.size / 2
    @property
    def cy(self): return self.y + self.size / 2

    def to_dict(self) -> dict:
        return {
            "ball_id": self.ball_id,
            "x":       round(self.x, 1),
            "y":       round(self.y, 1),
            "size":    self.size,
            "speed":   round(self.current_speed, 1),  # for debugging/UI
            "hit_count": self.hit_count,
        }


# ── main game ─────────────────────────────────────────────────────────

class CrashBashGame(BaseHeadlessGame):
    """
    Headless Crash Bash.
    update() advances one tick and returns get_state().
    """

    def __init__(self, game_id: str, player_ids: list, config: dict = None):
        super().__init__(game_id, player_ids, config)
        W, H = self.ARENA_W, self.ARENA_H

        # Build players (1-indexed numbers, up to 4)
        self.players: dict[str, CBPlayer] = {}
        for i, pid in enumerate(player_ids[:4]):
            p = CBPlayer(pid, i + 1, W, H)
            self.players[pid] = p

        # 4 balls
        self.balls = [CBBall(W, H) for _ in range(BALL_COUNT)]

        # score-loss events this tick (for renderer "flash" effects)
        self.score_events: list[dict] = []

    # ── tick ──────────────────────────────────────────────────────────

    def update(self, inputs: dict, dt: float = None) -> dict:
        if self._over:
            return self.get_state()

        self.tick        += 1
        self.score_events = []
        W, H = self.ARENA_W, self.ARENA_H
        alive = [p for p in self.players.values() if not p.eliminated]

        # 1. Move players ------------------------------------------------
        for p in alive:
            inp = inputs.get(p.player_id, InputState.neutral(p.player_id))
            if p.stunned > 0:
                p.stunned -= 1
                continue
            self._move_player(p, inp, W, H)

        # 2. Move balls & handle player deflections ----------------------
        for ball in self.balls:
            self._move_ball(ball, alive, inputs, W, H)

        # 3. Goal scoring ------------------------------------------------
        for ball in self.balls:
            for p in alive:
                gx, gy, gw, gh = _goal_rect(p.side, W, H)
                # use ball centre for scoring
                if (gx <= ball.cx <= gx + gw and
                        gy <= ball.cy <= gy + gh):
                    p.score -= 1
                    self.score_events.append({
                        "player_id": p.player_id,
                        "side":      p.side,
                        "score":     p.score,
                    })
                    ball.reset()  # Reset speed to starting value

                    if p.score <= 0:
                        p.score      = 0
                        p.eliminated = True
                    break   # one scorer per ball per tick

        # 4. Win check ---------------------------------------------------
        alive_now = [p for p in self.players.values() if not p.eliminated]
        if len(alive_now) <= 1:
            winner = alive_now[0].player_id if alive_now else None
            self._end_game(winner=winner)

        return self.get_state()

    # ── player movement (mirrors original Player.update) ──────────────

    def _move_player(self, p: CBPlayer, inp: InputState, W: int, H: int):
        g  = _goal_size(W, H)
        sp = PLAYER_SPEED
        margin = GOAL_DEPTH + p.size + 4

        if p.axis == "horizontal":
            # clamp to goal width corridor
            min_x = W//2 - g//2
            max_x = W//2 + g//2 - p.size
            if inp.left:  p.x -= sp
            if inp.right: p.x += sp
            p.x = self.clamp(p.x, min_x, max_x)
            # y locked close to their goal strip
            p.y = float(margin) if p.side == "top" else float(H - margin - p.size)

        else:  # vertical
            min_y = H//2 - g//2
            max_y = H//2 + g//2 - p.size
            if inp.up:   p.y -= sp
            if inp.down: p.y += sp
            p.y = self.clamp(p.y, min_y, max_y)
            p.x = float(margin) if p.side == "left" else float(W - margin - p.size)

    # ── ball physics (mirrors original Ball.update) ────────────────────

    def _move_ball(self, ball: CBBall, alive: list, inputs: dict, W: int, H: int):
        ball.x += ball.dx
        ball.y += ball.dy

        # Wall bounces
        if ball.x < 0:
            ball.x  = 0
            ball.dx = abs(ball.dx)
        elif ball.x + ball.size > W:
            ball.x  = W - ball.size
            ball.dx = -abs(ball.dx)

        if ball.y < 0:
            ball.y  = 0
            ball.dy = abs(ball.dy)
        elif ball.y + ball.size > H:
            ball.y  = H - ball.size
            ball.dy = -abs(ball.dy)

        # Player deflections
        for p in alive:
            if p.stunned > 0:
                continue
            if not self.rects_overlap(ball.x, ball.y, ball.size, ball.size,
                                      p.x,    p.y,    p.size,   p.size):
                continue

            # Reflect away from player centre (mirrors original)
            bx = ball.x + ball.size / 2
            by = ball.y + ball.size / 2
            px = p.x    + p.size    / 2
            py = p.y    + p.size    / 2

            vx   = bx - px
            vy   = by - py
            dist = math.hypot(vx, vy) or 0.01
            nx, ny = vx / dist, vy / dist

            speed   = math.hypot(ball.dx, ball.dy) or 3.0
            ball.dx = nx * speed
            ball.dy = ny * speed

            # Player-movement push influence (mirrors original)
            inp = inputs.get(p.player_id, InputState.neutral(p.player_id))
            if p.axis == "horizontal":
                push_x = ((1 if inp.right else 0) - (1 if inp.left else 0)) * (PLAYER_SPEED * BALL_PUSH)
                push_y = 0.0
            else:
                push_x = 0.0
                push_y = ((1 if inp.down else 0) - (1 if inp.up else 0)) * (PLAYER_SPEED * BALL_PUSH)

            ball.dx += push_x
            ball.dy += push_y

            # Separate ball from player
            ball.x += nx * 6
            ball.y += ny * 6

            # Increase ball speed when hit by player
            ball.increase_speed()

            p.stunned = STUN_TICKS
            break   # one player collision per ball per tick

    # ── state snapshot ────────────────────────────────────────────────

    def get_state(self) -> dict:
        W, H = self.ARENA_W, self.ARENA_H
        g    = _goal_size(W, H)

        # Build goal geometries so the renderer can draw them
        goals = {
            pid: {
                "side":       p.side,
                "color":      p.color,
                "rect":       list(_goal_rect(p.side, W, H)),
            }
            for pid, p in self.players.items()
        }

        return {
            "game_id":       self.game_id,
            "game_type":     "crash_bash",
            "tick":          self.tick,
            "arena_w":       W,
            "arena_h":       H,
            "goal_size":     g,
            "initial_score": INITIAL_SCORE,
            "players":       {pid: p.to_dict() for pid, p in self.players.items()},
            "balls":         [b.to_dict() for b in self.balls],
            "goals":         goals,
            "score_events":  self.score_events,
            "game_over":     self._over,
            "winner":        self._winner,
            "elapsed":       round(self.elapsed(), 2),
        }