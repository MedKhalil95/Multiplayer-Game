# game_factory.py  –  single import point for the server.
#
#   from game_factory import GameFactory, BotFactory, InputState
#
#   game  = GameFactory.create("crash_bash", game_id, player_ids)
#   bot   = BotFactory.create("crash_bash",  bot_id,  difficulty="medium")
#   state = game.update({pid: inp, ...})

from base_game     import BaseHeadlessGame, InputState
from game1_crashbash import CrashBashGame
from game2_tntbattle import TnTBattleGame
from bot_ai        import BotFactory          # re-export


class GameFactory:

    CONFIGS = {
        "crash_bash": {
            "name":        "Crash Bash",
            "description": "Keep the ball out of your goal – last player with points wins",
            "min_players": 2,
            "max_players": 4,
        },
        "tnt_battle": {
            "name":        "TNT Battle",
            "description": "Pick up crates and throw them at enemies – last one alive wins",
            "min_players": 2,
            "max_players": 4,
        },
    }

    _CLASSES = {
        "crash_bash": CrashBashGame,
        "tnt_battle": TnTBattleGame,
    }

    @classmethod
    def create(cls, game_type: str, game_id: str,
               player_ids: list, config: dict = None) -> BaseHeadlessGame:
        if game_type not in cls._CLASSES:
            raise ValueError(f"Unknown game type '{game_type}'. "
                             f"Valid: {list(cls._CLASSES)}")
        cfg = cls.CONFIGS[game_type]
        n   = len(player_ids)
        if not (cfg["min_players"] <= n <= cfg["max_players"]):
            raise ValueError(
                f"{cfg['name']} needs {cfg['min_players']}–{cfg['max_players']} "
                f"players, got {n}.")
        return cls._CLASSES[game_type](game_id, player_ids, config or {})

    @classmethod
    def game_types(cls) -> list:
        return list(cls.CONFIGS)


# ── self-test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys, json, random

    PASS = "\033[32mPASS\033[0m"
    FAIL = "\033[31mFAIL\033[0m"

    def run_test(label, game_type, player_ids, human_inputs_fn,
                 max_ticks=6000, required_keys=None):
        required_keys = required_keys or {
            "game_id","game_type","tick","players","game_over","winner",
            "arena_w","arena_h",
        }
        game = GameFactory.create(game_type, f"test_{game_type}", player_ids)
        bots = {pid: BotFactory.create(game_type, pid, "medium")
                for pid in player_ids[1:]}

        state, ticks = {}, 0
        for tick in range(max_ticks):
            state = game.get_state()
            inputs = human_inputs_fn(player_ids[0], tick)
            for bid, bot in bots.items():
                inputs[bid] = bot.decide(state)
            state = game.update(inputs)
            ticks = tick + 1
            if game.is_over():
                break

        missing = required_keys - set(state.keys())
        ok = not missing
        verdict = PASS if ok else FAIL
        print(f"  {verdict}  {label}")
        print(f"         ticks={ticks}  over={state['game_over']}  "
              f"winner={state['winner']}")
        if missing:
            print(f"         MISSING keys: {missing}")
        return ok

    print("=" * 56)
    print("  Headless game self-test")
    print("=" * 56)

    results = []

    # ── Crash Bash: 4-player, score-drain race ──────────────────────
    print("\n[1] Crash Bash – 4 players (1 human + 3 bots)")

    def cb_human(pid, tick):
        return {pid: InputState(pid,
                                left  = tick % 40 < 20,
                                right = tick % 40 >= 20)}

    results.append(run_test(
        "4-player score drain to 0",
        "crash_bash",
        ["human", "bot_b", "bot_c", "bot_d"],
        cb_human,
    ))

    # ── Crash Bash: 2-player ────────────────────────────────────────
    print("\n[2] Crash Bash – 2 players")
    results.append(run_test(
        "2-player finishes or times out",
        "crash_bash",
        ["h1", "h2"],
        lambda pid, t: {pid: InputState(pid, right=True)},
        max_ticks=12000,
    ))

    # ── TNT Battle: 4-player ────────────────────────────────────────
    print("\n[3] TNT Battle – 4 players (1 human + 3 bots)")

    def tnt_human(pid, tick):
        return {pid: InputState(pid,
                                right  = tick % 60 < 30,
                                up     = tick % 90 < 15,
                                action = tick % 72 == 0)}

    results.append(run_test(
        "4-player HP battle",
        "tnt_battle",
        ["human", "bot_2", "bot_3", "bot_4"],
        tnt_human,
    ))

    # ── TNT Battle: 2-player ────────────────────────────────────────
    print("\n[4] TNT Battle – 2 players")
    results.append(run_test(
        "2-player 1v1",
        "tnt_battle",
        ["p1", "p2"],
        lambda pid, t: {pid: InputState(pid, right=True, action=(t%80==0))},
    ))

    # ── State-shape spot-check ───────────────────────────────────────
    print("\n[5] State shape spot-checks")

    g = GameFactory.create("crash_bash", "shape1", ["a","b","c","d"])
    s = g.get_state()
    cb_keys = {"balls","goals","score_events","initial_score","goal_size"}
    ok = cb_keys.issubset(s.keys())
    print(f"  {'PASS' if ok else 'FAIL'}  CrashBash extra keys present: {cb_keys}")
    results.append(ok)

    g2 = GameFactory.create("tnt_battle", "shape2", ["a","b"])
    s2 = g2.get_state()
    tb_keys = {"pickup_crates","thrown_crates","explosions","hit_events"}
    ok2 = tb_keys.issubset(s2.keys())
    print(f"  {'PASS' if ok2 else 'FAIL'}  TnTBattle extra keys present: {tb_keys}")
    results.append(ok2)

    # ── Summary ─────────────────────────────────────────────────────
    print()
    total  = len(results)
    passed = sum(results)
    print("=" * 56)
    print(f"  {passed}/{total} tests passed")
    print("=" * 56)
    if passed < total:
        sys.exit(1)