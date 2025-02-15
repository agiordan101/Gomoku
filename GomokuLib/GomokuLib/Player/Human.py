class Human:

    def __init__(self, runner):
        if not hasattr(runner, "wait_player_action"):
            print(f"[Human: ERROR: Runner has no attribute wait_player_action.]")
            exit()

    def __str__(self):
        return f"Human"

    def init(self):
        pass

    def play_turn(self, runner) -> tuple[int]:
        return runner.wait_player_action()
