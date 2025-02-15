from os import stat
import string

import numpy as np

from GomokuLib.Algo import njit_classic_pruning, njit_dynamic_hpruning, njit_classic_heuristic, njit_dynamic_heuristic
import GomokuLib.Typing as Typing
from GomokuLib.Game.GameEngine import Gomoku
from GomokuLib.Algo.aligns_graphs import (
    init_my_heuristic_graph,
    init_opp_heuristic_graph,
    init_my_captures_graph,
    init_opp_captures_graph
)

import numba as nb
from numba import njit
from numba.experimental import jitclass
from numba.core.typing import cffi_utils

import fastcore._algo as _fastcore

cffi_utils.register_module(_fastcore)
_algo = _fastcore.lib
ffi = _fastcore.ffi

gettime_ctype = cffi_utils.make_function_type(_algo.gettime)


@nb.vectorize('float32(int32, float32, float32, float32)')
def _get_mc_policy(s_v: np.ndarray, sa_v: np.ndarray, sa_r: np.ndarray,
                     c: Typing.mcts_float_nb_dtype):
    return (sa_r / (sa_v + 1)) + (c * np.sqrt(np.log(s_v) / (sa_v + 1)))


@jitclass()
class MCTSNjit:

    engine: Gomoku
    mcts_turn_iter: Typing.mcts_int_nb_dtype
    mcts_turn_time: Typing.mcts_int_nb_dtype
    states: Typing.nbStateDict
    path: Typing.nbPathArray
    c: Typing.mcts_float_nb_dtype
    get_time_cfunc: gettime_ctype

    depth: Typing.mcts_int_nb_dtype
    max_depth: Typing.mcts_int_nb_dtype
    end_game: nb.boolean
    current_statehash: Typing.nbStrDtype
    gamestatehash: Typing.nbStrDtype

    my_h_graph: Typing.nbHeuristicGraph
    opp_h_graph: Typing.nbHeuristicGraph
    my_cap_graph: Typing.nbHeuristicGraph
    opp_cap_graph: Typing.nbHeuristicGraph
    heuristic_pows: Typing.nbHeuristicData
    heuristic_dirs: Typing.nbHeuristicData
    tmp_h_rewards: Typing.nbHeuristicrewards

    def __init__(self, 
                 engine: Gomoku,
                 iter: Typing.MCTSIntDtype = 0,
                 time: Typing.MCTSIntDtype = 0
                 ):

        self.engine = engine.clone()
        self.mcts_turn_iter = iter
        self.mcts_turn_time = time

        self.c = np.sqrt(2)
        self.get_time_cfunc = _algo.gettime

        self.init()
        self.path = np.zeros((361, 2), dtype=Typing.MCTSIntDtype)

        # Init data for heuristic
        self.my_h_graph = init_my_heuristic_graph()
        self.opp_h_graph = init_opp_heuristic_graph()
        self.my_cap_graph = init_my_captures_graph()
        self.opp_cap_graph = init_opp_captures_graph()
        self.heuristic_pows = np.array([
                [8192, 4096, 2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1],
                [8192, 4096, 2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1],
                [8192, 4096, 2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1],
                [8192, 4096, 2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1]
            ], dtype=Typing.MCTSIntDtype
        )
        self.heuristic_dirs = np.array([
                [-1, 1],
                [0, 1],
                [1, 1],
                [1, 0]
            ], dtype=Typing.MCTSIntDtype
        )
        self.tmp_h_rewards = np.zeros((21, 21), dtype=Typing.HeuristicGraphDtype)

    def init(self):
        self.states = nb.typed.Dict.empty(
            key_type=Typing.nbStrDtype,
            value_type=Typing.nbState
        )

    def compile(self, game_engine: Gomoku):
        self.do_your_fck_work(game_engine, 2, 0)

    def str(self):
        return f"MCTSNjit ({self.mcts_turn_iter} iter | {self.mcts_turn_time} ms)"

    def get_state_data(self, game_engine: Gomoku) -> Typing.nbStateDict:

        mcts_data = nb.typed.Dict.empty(
            key_type=nb.types.unicode_type,
            value_type=Typing.nbState
        )
        statehash = self.fast_tobytes(game_engine.board)
        if statehash in self.states:
            statedata = self.states[statehash]
            mcts_data['mcts_state_data'] = statedata
        else:
            mcts_data['mcts_state_data'] = np.zeros(1, dtype=Typing.StateDataDtype)
        return mcts_data

    def do_your_fck_work(self, game_engine: Gomoku, iter: int = 0, time: int = 0) -> tuple:

        if iter <= 1 and time <= 0:
            time = self.mcts_turn_time
            iter = self.mcts_turn_iter if self.mcts_turn_iter > 1 else 2

        self.max_depth = 0
        self.gamestatehash = self.fast_tobytes(game_engine.board)

        i = 0
        ts = self.get_time_cfunc()
        if time > 0:
            print(f"\n[MCTSNjit: Start for {time} ms]")

            ti = ts
            while ti - ts < time - 1:
                self._do_one_iter(game_engine)
                ti = self.get_time_cfunc()
                i += 1

            print(f"[MCTSNjit: Done in {i} iterations]\n")
        else:
            print(f"\n[MCTSNjit: Start {iter} iterations]")

            while i < iter:
                self._do_one_iter(game_engine)
                i += 1

            ti = self.get_time_cfunc()
            print(f"[MCTSNjit: Done in {ti - ts} ms]\n")

        state_data = self.states[self.gamestatehash][0]
        state_data['max_depth'] = self.max_depth

        if np.all(game_engine.board == 0):
            return 9, 9
        else:
            sa_v, sa_r = state_data['stateAction']
            arg = np.argmax(sa_r / (sa_v + 1))
            return arg // 19, arg % 19

    def _do_one_iter(self, game_engine: Gomoku):

        self.current_statehash = self.gamestatehash
        self.engine.update(game_engine)

        self.mcts()
        if self.depth + 1 > self.max_depth:
            self.max_depth = self.depth + 1

    def mcts(self):

        old_best_action = np.zeros(2, dtype=Typing.TupleDtype)
        best_action = np.zeros(2, dtype=Typing.TupleDtype)

        # print(f"\n[MCTSNjit mcts function iter]\n")
        self.depth = 0
        self.end_game = self.engine.isover()
        statehashes = []
        while self.current_statehash in self.states and not self.end_game:

            state_data = self.states[self.current_statehash][0]

            # Prepare data for action selection
            policy = self.get_policy(state_data)
            pruning = self.dynamic_pruning(state_data['pruning'])

            # Choose the best action to explore
            old_best_action[:] = best_action
            best_action = self.lazy_selection(policy, state_data['actions'], pruning)
            self.path[self.depth][:] = best_action

            # Update statehash with action taken
            statehashes.append(self.current_statehash)
            self.update_statehash(best_action)

            # Update engine with action taken
            self.engine.apply_action(best_action)
            self.engine.next_turn()
            self.depth += 1

            # Remove potential captured stone from statehash
            self.update_statehash_endturn()

            self.end_game = self.engine.isover()

        # prepare data for expansion
        actions = self.engine.get_lazy_actions()
        pruning_arr = self.new_state_pruning()
        if self.depth > 1:
            old_statehash = statehashes[-2]
        else:
            old_statehash = None

        # Compute the reward of this new state
        reward = self.award(old_statehash, best_action, old_best_action)

        # Expand this new state & Backpropagate the reward
        self.expand(actions, reward, pruning_arr)
        self.backpropagation(statehashes, reward)

    def get_policy(self, state_data: Typing.StateDataDtype) -> Typing.nbPolicy:
        """
            ucb(s, a) = exploitation_rate(s, a) + exploration_rate(s, a)

                exploitation_rate(s, a) = rewards(s, a)     / (visits(s, a)     + 1)
                exploration_rate(s, a) =  c * sqrt( log( visits(s) ) / (1 + visits(s, a)) )

        """
        sa_v, sa_r = state_data['stateAction']
        return _get_mc_policy(state_data['visits'], sa_v, sa_r, self.c)

    def policy_transformation(self, actions, policy, pruning):
        """
            Priority selection:
                Valid non prune action first
                Valid prune action
                Invalid action
        """
        if pruning < 1:
            if actions > 0:
                return -1
            else:
                return -2
        else:
            if actions > 0:
                return policy
            else:
                return -2

    def fetch_upper_policies(self, policy: np.ndarray, actions: np.ndarray, pruning: np.ndarray, exp_gz: np.ndarray):
        """
            Return indexes of bests policy cells, and the number of these at index -1 
        """
        best_actions = np.empty((362, 2), dtype=Typing.TupleDtype)

        pmax = -10
        k = 0
        for i in range(exp_gz[0], exp_gz[2]):
            for j in range(exp_gz[1], exp_gz[3]):
                value = self.policy_transformation(actions[i, j], policy[i, j], pruning[i, j])

                if pmax < value:
                    k = 0
                    pmax = value

                if pmax == value:
                    best_actions[k][0] = i
                    best_actions[k][1] = j
                    k += 1

        best_actions[-1, 0] = k
        return best_actions

    def lazy_selection(self, policy: np.ndarray, actions: np.ndarray, pruning: np.ndarray):
        """
            Start with action=0 on stones and action=1 elsewhere
            If action=1 is select, tests its validity before returning
        """
        gAction = np.zeros(2, dtype=Typing.TupleDtype)
        exp_gz = self.engine.get_expanded_game_zone()
        while True: # return to while true

            arr = self.fetch_upper_policies(policy, actions, pruning, exp_gz)

            len = arr[-1, 0]
            arr_pick = np.arange(len)
            np.random.shuffle(arr_pick) # Slow ...
            for e in arr_pick:
                x, y = arr[e]
                gAction[:] = (x, y)
                if actions[x, y] == 2:
                    return gAction
                elif self.engine.is_valid_action(gAction):
                    actions[x, y] = 2
                    return gAction
                else:
                    actions[x, y] = 0

    # def get_expanded_game_zone(self):
    #     game_zone = np.copy(self.engine.get_game_zone())

    #     if game_zone[0] > 0:
    #         game_zone[0] -= 1

    #     if game_zone[1] > 0:
    #         game_zone[1] -= 1
        
    #     if game_zone[2] < 18:
    #         game_zone[2] += 2
    #     elif game_zone[2] < 19:
    #         game_zone[2] += 1
        
    #     if game_zone[3] < 18:
    #         game_zone[3] += 2
    #     elif game_zone[3] < 19:
    #         game_zone[3] += 1

    #     return game_zone

    def update_statehash(self, best_action: Typing.TupleDtype):
        """
            Add new stone
        """
        rawidx = best_action[0] * 19 + best_action[1]
        newStone = '1' if self.engine.player_idx == 0 else '2'
        self.current_statehash = self.current_statehash[:rawidx] + newStone + self.current_statehash[rawidx + 1:]

    def update_statehash_endturn(self):
        """
            Remove captured stones
        """
        if self.engine.is_capture_active:
            captures = self.engine.capture.captured_buffer
            for i in range(self.engine.capture.capture_count):
                rawidx0 = captures[i, 0, 0] * 19 + captures[i, 0, 1]
                rawidx1 = captures[i, 1, 0] * 19 + captures[i, 1, 1]
                if rawidx0 > rawidx1:
                    rawidx1 ^= rawidx0
                    rawidx0 ^= rawidx1
                    rawidx1 ^= rawidx0

                self.current_statehash = (
                    self.current_statehash[:rawidx0] + '0'
                    + self.current_statehash[rawidx0 + 1:rawidx1] + '0'
                    + self.current_statehash[rawidx1 + 1:]
                )

    def expand(self, actions: np.ndarray, reward: Typing.heuristic_graph_nb_dtype, pruning_arr: np.ndarray):
        state = np.zeros(1, dtype=Typing.StateDataDtype)

        state[0]['max_depth'] = self.depth
        state[0]['visits'] = 1
        state[0]['rewards'] = reward       # Useless data for MCTS, usefull for UI
        # state[0]['stateAction'][...] = 0.
        state[0]['heuristic'] = reward

        exp_gz = self.engine.get_expanded_game_zone()
        row_start = exp_gz[0]
        col_start = exp_gz[1]
        row_end = exp_gz[2]
        col_end = exp_gz[3]

        for r in range(row_start, row_end):
            for c in range(col_start, col_end):
                state[0]['actions'][r, c] = actions[r, c]
                state[0]['h_rewards'][r + 2, c + 2] = self.tmp_h_rewards[r + 2, c + 2]
                state[0]['pruning'][0, r, c] = pruning_arr[0, r, c]
                state[0]['pruning'][1, r, c] = pruning_arr[1, r, c]
                state[0]['pruning'][2, r, c] = pruning_arr[2, r, c]

        h_capture = self.engine.capture.get_captures()
        state[0]['h_captures'][0] = h_capture[0]
        state[0]['h_captures'][1] = h_capture[1]
    
        self.states[self.current_statehash] = state

    def new_state_pruning(self, engine: Gomoku = None):

        if engine is None:
            engine = self.engine

        game_zone = engine.get_game_zone()
        g0 = game_zone[0]
        g1 = game_zone[1]
        g2 = game_zone[2]
        g3 = game_zone[3]
        return njit_dynamic_hpruning(engine.board, g0, g1, g2, g3, engine.player_idx,
            self.my_h_graph, self.opp_h_graph, self.my_cap_graph, self.opp_cap_graph)

    def dynamic_pruning(self, pruning_arr: np.ndarray):
        if self.depth == 0:        # Depth 0
            return pruning_arr[0]
        if self.depth > 2:         # Depth 3 | ...
            return pruning_arr[2]
        else:                      # Depth 1 | 2
            return pruning_arr[1]

    def classic_pruning(self):
        return njit_classic_pruning(self.engine.board)

    def award(self, old_statehash, best_action, old_best_action):

        if self.engine.isover():
            return 1 if self.engine.winner == self.engine.player_idx else 0
        else:
            return self.dynamic_heuristic(old_statehash, best_action, old_best_action)

    def dynamic_heuristic(self, old_statehash, best_action, old_best_action):
        board = self.engine.board

        cap = self.engine.get_captures()
        c0 = cap[self.engine.player_idx]
        c1 = cap[self.engine.player_idx ^ 1]

        game_zone = self.engine.get_game_zone()
        g0 = game_zone[0]
        g1 = game_zone[1]
        g2 = game_zone[2]
        g3 = game_zone[3]

        if self.depth > 1:
            old_state_data = self.states[old_statehash][0]
            self.tmp_h_rewards[...] = old_state_data['h_rewards']    # Data from 2 turns.
            old_c0 = old_state_data['h_captures'][self.engine.player_idx]        # Same player_idx
            old_c1 = old_state_data['h_captures'][self.engine.player_idx ^ 1]
        else:
            self.tmp_h_rewards = np.zeros((21, 21), dtype=Typing.HeuristicGraphDtype)
            old_c0 = 0
            old_c1 = 0

        return njit_dynamic_heuristic(board, c0, c1, g0, g1, g2, g3, self.engine.player_idx,
            self.my_h_graph, self.opp_h_graph, self.my_cap_graph, self.opp_cap_graph, self.heuristic_pows, self.heuristic_dirs,
            self.tmp_h_rewards, best_action[0], best_action[1], old_best_action[0], old_best_action[1], old_c0, old_c1)

    def heuristic(self, engine: Gomoku = None):
        if engine is None:
            engine = self.engine

        board = engine.board

        cap = engine.get_captures()
        c0 = cap[engine.player_idx]
        c1 = cap[engine.player_idx ^ 1]

        game_zone = engine.get_game_zone()
        g0 = game_zone[0]
        g1 = game_zone[1]
        g2 = game_zone[2]
        g3 = game_zone[3]

        return njit_classic_heuristic(board, c0, c1, g0, g1, g2, g3, engine.player_idx,
            self.my_h_graph, self.opp_h_graph, self.my_cap_graph, self.opp_cap_graph, self.heuristic_pows, self.heuristic_dirs)

    def backpropagation(self, statehashes, reward: Typing.heuristic_graph_nb_dtype):
        for i in range(self.depth - 1, -1, -1):
            reward = 1 - (0.96 * reward)
            self.backprop_memory(self.path[i], reward, statehashes[i])

    def backprop_memory(self, best_action, reward: Typing.heuristic_graph_nb_dtype, statehash: string):
        r, c = best_action
        state_data = self.states[statehash][0]

        state_data['visits'] += 1                           # update n count
        state_data['rewards'] += reward                     # Useless data for MCTS, usefull for UI
        state_data['stateAction'][0, r, c] += 1             # update count
        state_data['stateAction'][1, r, c] += reward        # update reward

    def fast_tobytes(self, arr: Typing.BoardDtype):
        byte_list = []
        for i in range(19):
            for j in range(19):
                byte_list.append('1' if arr[0, i, j] == 1 else ('2' if arr[1, i, j] == 1 else '0'))
        return ''.join(byte_list)
