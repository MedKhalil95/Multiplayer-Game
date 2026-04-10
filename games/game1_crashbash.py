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
#   • Balls bounce off each other (elastic collision).
#
# Team mode (2v2):
#   Pass config={"teams": {"A": [pid1, pid2], "B": [pid3, pid4]}}
#   Teams share a combined score pool (2 × INITIAL_SCORE).
#   A team is eliminated when their pool hits 0.
#   The winner announcement names both teammates.
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
PLAYER_SIZE   = 36          # px (kept for backward-compat / bot AI)
PLAYER_W      = 72          # px – wide paddle dimension (along movement axis)
PLAYER_H      = 20          # px – thin paddle dimension (across movement axis)
BALL_SIZE     = 18          # px square  (used as diameter for circle)
GOAL_DEPTH    = 20          # px – how deep the scoring strip is
HIT_COOLDOWN  = 20          # ticks a ball ignores the SAME player after bouncing
BALL_PUSH     = 0.35        # fraction of player speed added to ball on deflect
POWER_HIT_MULTIPLIER = 2.2  # speed multiplier when action button held on hit
POWER_HIT_MAX = 14.0        # hard cap for power-hit speed
BALL_COUNT    = 4
ACTION_BUFFER = 18          # ticks the action press is remembered (≈0.3 s window)
BALL_BALL_COOLDOWN = 10     # ticks two balls ignore each other after colliding

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
    # Players sit immediately in front of their goal strip (2 px gap)
    margin = GOAL_DEPTH + 2

    if number == 1:   # top – moves horizontally (w=PLAYER_W, h=PLAYER_H)
        return (W//2 - PLAYER_W//2, margin, "top", "horizontal")
    if number == 2:   # bottom – moves horizontally
        return (W//2 - PLAYER_W//2, H - margin - PLAYER_H, "bottom", "horizontal")
    if number == 3:   # left – moves vertically (w=PLAYER_H, h=PLAYER_W)
        return (margin, H//2 - PLAYER_W//2, "left", "vertical")
    # right
    return (W - margin - PLAYER_H, H//2 - PLAYER_W//2, "right", "vertical")


# ── data classes ──────────────────────────────────────────────────────

class CBPlayer:
    def __init__(self, player_id: str, number: int, arena_w: int, arena_h: int, color: str = None):
        self.player_id  = player_id
        self.number     = number
        self.color      = color or PLAYER_COLORS[number - 1]
        self.size       = PLAYER_SIZE  # kept for bot AI compat

        x, y, side, axis = _player_start(number, arena_w, arena_h)
        self.x, self.y  = float(x), float(y)
        self.side       = side      # "top" | "bottom" | "left" | "right"
        self.axis       = axis      # "horizontal" | "vertical"

        # Rectangular paddle: wide along the movement axis, thin across it
        if axis == "horizontal":
            self.w = PLAYER_W
            self.h = PLAYER_H
        else:
            self.w = PLAYER_H
            self.h = PLAYER_W

        self.score          = INITIAL_SCORE
        self.eliminated     = False
        self.action_buffer  = 0   # ticks remaining where a buffered action press is active

    # axis-aligned bounding box corners
    @property
    def left(self):   return self.x
    @property
    def right(self):  return self.x + self.w
    @property
    def top(self):    return self.y
    @property
    def bottom(self): return self.y + self.h

    def to_dict(self) -> dict:
        return {
            "player_id":  self.player_id,
            "number":     self.number,
            "color":      self.color,
            "x":          round(self.x, 1),
            "y":          round(self.y, 1),
            "w":          self.w,
            "h":          self.h,
            "size":       self.size,   # kept for bot AI compat
            "side":       self.side,
            "score":      self.score,
            "eliminated": self.eliminated,
            "action_buffered": self.action_buffer > 0,
        }


class CBBall:
    _ctr = 0

    def __init__(self, arena_w: int, arena_h: int):
        CBBall._ctr += 1
        self.ball_id = f"ball_{CBBall._ctr}"
        self.size    = BALL_SIZE
        self.hit_count = 0
        self.current_speed = BALL_SPEED_START
        self.hit_cooldowns: dict = {}   # player_id → ticks remaining to ignore
        self.ball_cooldowns: dict = {}  # ball_id   → ticks remaining (ball-ball collision)
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
        self.hit_cooldowns = {}
        self.ball_cooldowns = {}
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


# ── team (optional 2v2 mode) ──────────────────────────────────────────

class CBTeam:
    """
    Represents one side in a 2v2 game.
    Both teammates draw from a single shared score pool.
    A team is eliminated when pool reaches 0.
    """
    def __init__(self, team_id: str, member_ids: list, color: str = None):
        self.team_id    = team_id
        self.member_ids = list(member_ids)
        self.color      = color
        self.score      = INITIAL_SCORE * len(member_ids)  # shared pool
        self.eliminated = False

    def deduct(self, amount: int = 1):
        self.score = max(0, self.score - amount)
        if self.score <= 0:
            self.eliminated = True

    def to_dict(self) -> dict:
        return {
            "team_id":    self.team_id,
            "member_ids": self.member_ids,
            "color":      self.color,
            "score":      self.score,
            "eliminated": self.eliminated,
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
        colors = self.config.get("colors", {})
        for i, pid in enumerate(player_ids[:4]):
            color = colors.get(pid) or PLAYER_COLORS[i]
            p = CBPlayer(pid, i + 1, W, H, color=color)
            self.players[pid] = p

        # ── Team mode ──────────────────────────────────────────────────
        # config["teams"] = {"A": [pid1, pid2], "B": [pid3, pid4]}
        # If absent → free-for-all (each player is their own team).
        raw_teams = self.config.get("teams")
        self.teams: dict[str, CBTeam] | None = None
        self.player_team: dict[str, str] = {}   # pid → team_id

        if raw_teams and len(raw_teams) >= 2:
            TEAM_COLORS = ["#E05050", "#5080E0", "#40C878", "#D4D440"]
            self.teams = {}
            for t_idx, (tid, members) in enumerate(raw_teams.items()):
                tc = TEAM_COLORS[t_idx % len(TEAM_COLORS)]
                team = CBTeam(tid, members, color=tc)
                self.teams[tid] = team
                for pid in members:
                    self.player_team[pid] = tid
                    if pid in self.players:
                        self.players[pid].color = tc  # tint with team colour

        # 4 balls
        self.balls = [CBBall(W, H) for _ in range(BALL_COUNT)]

        # score-loss events this tick (for renderer "flash" effects)
        self.score_events: list[dict] = []
        self._winner_display: str | None = None  # human-readable "Player A & Player B win!"

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
            self._move_player(p, inp, W, H)

        # 2. Tick down per-ball cooldowns & per-player action buffers ----
        for ball in self.balls:
            ball.hit_cooldowns = {
                pid: t - 1
                for pid, t in ball.hit_cooldowns.items()
                if t > 1
            }
            ball.ball_cooldowns = {
                bid: t - 1
                for bid, t in ball.ball_cooldowns.items()
                if t > 1
            }

        # Tick action buffers: refresh on new press, count down otherwise
        for p in alive:
            inp = inputs.get(p.player_id, InputState.neutral(p.player_id))
            if inp.action:
                p.action_buffer = ACTION_BUFFER   # re-arm / refresh
            elif p.action_buffer > 0:
                p.action_buffer -= 1

        # 3. Move balls & handle player deflections ----------------------
        for ball in self.balls:
            self._move_ball(ball, alive, inputs, W, H)

        # 3b. Ball-ball elastic collisions --------------------------------
        for i in range(len(self.balls)):
            for j in range(i + 1, len(self.balls)):
                self._collide_balls(self.balls[i], self.balls[j])

        # 4. Goal scoring ------------------------------------------------
        for ball in self.balls:
            for p in alive:
                gx, gy, gw, gh = _goal_rect(p.side, W, H)
                if (gx <= ball.cx <= gx + gw and
                        gy <= ball.cy <= gy + gh):

                    if self.teams:
                        # ── Team mode: deduct from shared pool ──────────
                        tid  = self.player_team.get(p.player_id)
                        team = self.teams.get(tid) if tid else None
                        if team and not team.eliminated:
                            team.deduct(1)
                            self.score_events.append({
                                "player_id": p.player_id,
                                "team_id":   tid,
                                "side":      p.side,
                                "team_score": team.score,
                            })
                            # Eliminate ALL members when team pool hits 0
                            if team.eliminated:
                                for mid in team.member_ids:
                                    mp = self.players.get(mid)
                                    if mp:
                                        mp.score      = 0
                                        mp.eliminated = True
                    else:
                        # ── Free-for-all: deduct individual score ────────
                        p.score -= 1
                        self.score_events.append({
                            "player_id": p.player_id,
                            "side":      p.side,
                            "score":     p.score,
                        })
                        if p.score <= 0:
                            p.score      = 0
                            p.eliminated = True

                    ball.reset()
                    break   # one scorer per ball per tick

        # 5. Win check ---------------------------------------------------
        if self.teams:
            alive_teams = [t for t in self.teams.values() if not t.eliminated]
            if len(alive_teams) <= 1:
                winning_team = alive_teams[0] if alive_teams else None
                if winning_team:
                    # Build announcement string: "Alice & Bob WIN!"
                    names = " & ".join(winning_team.member_ids)
                    self._winner_display = f"{names} WIN!"
                    self._end_game(winner=winning_team.team_id)
                else:
                    self._winner_display = "Draw!"
                    self._end_game(winner=None)
        else:
            alive_now = [p for p in self.players.values() if not p.eliminated]
            if len(alive_now) <= 1:
                winner = alive_now[0].player_id if alive_now else None
                self._winner_display = f"{winner} WINS!" if winner else "Draw!"
                self._end_game(winner=winner)

        return self.get_state()

    # ── player movement (mirrors original Player.update) ──────────────

    def _move_player(self, p: CBPlayer, inp: InputState, W: int, H: int):
        g  = _goal_size(W, H)
        sp = PLAYER_SPEED
        margin = GOAL_DEPTH + 2

        if p.axis == "horizontal":
            # clamp to goal width corridor
            min_x = W//2 - g//2
            max_x = W//2 + g//2 - p.w
            if inp.left:  p.x -= sp
            if inp.right: p.x += sp
            p.x = self.clamp(p.x, min_x, max_x)
            # y locked close to their goal strip
            p.y = float(margin) if p.side == "top" else float(H - margin - p.h)

        else:  # vertical
            min_y = H//2 - g//2
            max_y = H//2 + g//2 - p.h
            if inp.up:   p.y -= sp
            if inp.down: p.y += sp
            p.y = self.clamp(p.y, min_y, max_y)
            p.x = float(margin) if p.side == "left" else float(W - margin - p.w)

    # ── ball physics ──────────────────────────────────────────────────

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
            if ball.hit_cooldowns.get(p.player_id, 0) > 0:
                continue
            if not self.rects_overlap(ball.x, ball.y, ball.size, ball.size,
                                      p.x,    p.y,    p.w,       p.h):
                continue

            # ── determine which face the ball came from ──────────────
            # For a horizontal paddle (top/bottom): the ball always hits
            # the top or bottom face.  For vertical paddle (left/right):
            # always the left or right face.  We use the paddle's side
            # to decide the axis rather than the overlap depth, which
            # eliminates the "slide-through" case entirely.

            inp = inputs.get(p.player_id, InputState.neutral(p.player_id))

            if p.axis == "horizontal":
                # Reflect on Y axis
                if p.side == "top":
                    ball.y = p.y + p.h + 1
                    ball.dy = abs(ball.dy)
                else:  # bottom
                    ball.y = p.y - ball.size - 1
                    ball.dy = -abs(ball.dy)
                ball.dx += ((1 if inp.right else 0) - (1 if inp.left else 0)) * (PLAYER_SPEED * BALL_PUSH)

            else:  # vertical paddle
                if p.side == "left":
                    ball.x = p.x + p.w + 1
                    ball.dx = abs(ball.dx)
                else:  # right
                    ball.x = p.x - ball.size - 1
                    ball.dx = -abs(ball.dx)
                ball.dy += ((1 if inp.down else 0) - (1 if inp.up else 0)) * (PLAYER_SPEED * BALL_PUSH)

            ball.increase_speed()

            # Power hit: action pressed recently (buffered) → big speed boost.
            # Consume the buffer so one press only triggers once.
            if p.action_buffer > 0:
                p.action_buffer = 0   # consume
                spd = math.hypot(ball.dx, ball.dy)
                boosted = min(spd * POWER_HIT_MULTIPLIER, POWER_HIT_MAX)
                if spd > 0:
                    ball.dx = ball.dx / spd * boosted
                    ball.dy = ball.dy / spd * boosted
                ball.current_speed = boosted

            ball.hit_cooldowns[p.player_id] = HIT_COOLDOWN
            break

    # ── ball-ball elastic collision ────────────────────────────────────

    def _collide_balls(self, a: CBBall, b: CBBall):
        """
        Elastic 1D collision between two same-mass square balls.
        Uses circle approximation (radius = size/2) for distance check,
        then swaps velocity components along the collision normal.
        A per-pair cooldown prevents double-counting the same collision.
        """
        if a.ball_cooldowns.get(b.ball_id, 0) > 0:
            return

        r = (a.size + b.size) / 2   # sum of radii
        dx = b.cx - a.cx
        dy = b.cy - a.cy
        dist = math.hypot(dx, dy)
        if dist >= r or dist == 0:
            return

        # Normalised collision axis
        nx, ny = dx / dist, dy / dist

        # Relative velocity along normal
        rel_vn = (b.dx - a.dx) * nx + (b.dy - a.dy) * ny
        if rel_vn > 0:
            return   # already separating – skip (avoids sticky overlap)

        # Equal-mass elastic: swap the normal components
        a_vn = a.dx * nx + a.dy * ny
        b_vn = b.dx * nx + b.dy * ny
        # After elastic collision, normal velocities exchange
        a.dx += (b_vn - a_vn) * nx
        a.dy += (b_vn - a_vn) * ny
        b.dx += (a_vn - b_vn) * nx
        b.dy += (a_vn - b_vn) * ny

        # Push apart so they're no longer overlapping
        overlap = r - dist
        a.x -= nx * overlap / 2
        a.y -= ny * overlap / 2
        b.x += nx * overlap / 2
        b.y += ny * overlap / 2

        # Cooldown so we don't process this pair again next tick
        a.ball_cooldowns[b.ball_id] = BALL_BALL_COOLDOWN
        b.ball_cooldowns[a.ball_id] = BALL_BALL_COOLDOWN

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
            "game_id":        self.game_id,
            "game_type":      "crash_bash",
            "tick":           self.tick,
            "arena_w":        W,
            "arena_h":        H,
            "goal_size":      g,
            "initial_score":  INITIAL_SCORE,
            "players":        {pid: p.to_dict() for pid, p in self.players.items()},
            "balls":          [b.to_dict() for b in self.balls],
            "goals":          goals,
            "score_events":   self.score_events,
            "game_over":      self._over,
            "winner":         self._winner,
            "winner_display": self._winner_display,   # "Alice & Bob WIN!" or "PlayerX WINS!"
            "team_mode":      self.teams is not None,
            "teams":          {tid: t.to_dict() for tid, t in self.teams.items()}
                              if self.teams else None,
            "elapsed":        round(self.elapsed(), 2),
        }