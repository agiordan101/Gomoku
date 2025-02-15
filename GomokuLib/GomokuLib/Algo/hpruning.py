import GomokuLib.Typing as Typing

import numba as nb
import numpy as np
from numba import njit

@njit()
def _get_neighbors_mask(board):
    """
        Timer par rapport à un vectorize avec la gamezone
    """

    neigh = np.zeros((19, 19), dtype=Typing.BoardDtype)

    neigh[:-1, :] |= board[1:, :]  # Roll cols to left
    neigh[1:, :] |= board[:-1, :]  # Roll cols to right
    neigh[:, :-1] |= board[:, 1:]  # Roll rows to top
    neigh[:, 1:] |= board[:, :-1]  # Roll rows to bottom

    neigh[1:, 1:] |= board[:-1, :-1]  # Roll cells to the right-bottom corner
    neigh[1:, :-1] |= board[:-1, 1:]  # Roll cells to the right-upper corner
    neigh[:-1, 1:] |= board[1:, :-1]  # Roll cells to the left-bottom corner
    neigh[:-1, :-1] |= board[1:, 1:]  # Roll cells to the left-upper corner

    return neigh

@njit()
def njit_classic_pruning(board: np.ndarray):
    full_board = board[0] | board[1]
    non_pruned = _get_neighbors_mask(full_board)  # Get neightbors, depth=1

    xp = non_pruned ^ full_board
    non_pruned = xp & non_pruned  # Remove neighbors stones already placed
    return non_pruned.astype(Typing.PruningDtype)


""" ###################################### """


@njit()
def _create_aligns_reward(board, graph, sr, sc, player_idx, pruning):
    dirs = [
        [-1, 1],
        [0, 1],
        [1, 1],
        [1, 0]
    ]
    # board = board.astype(Typing.PruningDtype)
    way = -1 if player_idx == 1 else 1
    mask_align_id = np.zeros((7, 2), dtype=Typing.BoardDtype)
    p = np.array(
        [8192, 4096, 2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1],
        dtype=Typing.PruningDtype    # np.dot handle by Numba require float
    )
    buf = np.zeros(14, dtype=Typing.PruningDtype)

    for di in range(4):

        dr, dc = dirs[di]
        r, c = sr - 2 * dr, sc - 2 * dc

        for i in range(0, 7):
            mask_align_id[i, :] = [r, c]           # Remember indexes, to create mask in case of
            buf[i*2: i*2 + 2] = board[::way, r, c]

            r += dr
            c += dc

        graph_id = np.int32(np.dot(buf, p))
        reward = np.abs(graph[graph_id])
        # print(f"Coord {sr} {sc}: graph[{graph_id}] = {np.int32(graph[graph_id] * 10)} / !=0? {nb.int32(graph[graph_id] * 10 != 0)}")
        if reward == 0.5:           # Capture: Reward 2 cells witch can make the capture
            r = mask_align_id[1][0]
            c = mask_align_id[1][1]
            if reward > pruning[r, c]:  # Only if no reward already here
                pruning[r, c] = reward

            r = mask_align_id[4][0]
            c = mask_align_id[4][1]
            if reward > pruning[r, c]:  # Only if no reward already here
                pruning[r, c] = reward

        else:                       # Prune the first cell of the 7-length mask
            for i in range(1, 7):
                # print(f"mask[{mask_align_id[i]}] = {mask[mask_align_id[i]]}")
                r = mask_align_id[i][0]
                c = mask_align_id[i][1]
                if reward > pruning[r, c]:
                    pruning[r, c] = reward


@njit()
def _create_board_hrewards(board, gz_start_r, gz_start_c, gz_end_r, gz_end_c, player_idx, my_graph, opp_graph):
    # print("hpruning start")

    # Padding: 2 on the left and top / 5 on the right and bottom
    board_pad = np.ones((2, 26, 26), dtype=Typing.BoardDtype)
    board_pad[..., 2:21, 2:21] = board

    # Do not apply supplementary useless computations on y and x
    pruning = np.zeros((26, 26), dtype=Typing.PruningDtype)
    for y in range(2 + gz_start_r, 3 + gz_end_r):
        for x in range(2 + gz_start_c, 3 + gz_end_c):

            if board_pad[player_idx, y, x]:
                _create_aligns_reward(board_pad, my_graph, y, x, player_idx, pruning)

            elif board_pad[player_idx ^ 1, y, x]:
                _create_aligns_reward(board_pad, opp_graph, y, x, player_idx, pruning)

    # print("hpruning end")
    return pruning[..., 2:21, 2:21]


@nb.vectorize('int32(float32, float64)')
def _keep_uppers(board, num):
    if board >= num:
        return 1
    else:
        return 0

@njit()
def njit_dynamic_hpruning(board, gz_start_r, gz_start_c, gz_end_r, gz_end_c, player_idx,
    my_h_graph, opp_h_graph, my_cap_graph, opp_cap_graph):

    pruning_arr = np.zeros((3, 19, 19), dtype=Typing.PruningDtype)

    align_rewards = _create_board_hrewards(board, gz_start_r, gz_start_c, gz_end_r, gz_end_c, player_idx, my_h_graph, opp_h_graph)
    rmax = np.amax(align_rewards)

    if rmax == 0:   # No aligns: classic pruning
        pruning_arr[...] = njit_classic_pruning(board)

    else:
        cap_rewards = _create_board_hrewards(board, gz_start_r, gz_start_c, gz_end_r, gz_end_c, player_idx, my_cap_graph, opp_cap_graph)
        captures = _keep_uppers(cap_rewards, 0.5)
        
        # At least 1 align: hpruning + all aligns
        pruning_arr[0][...] = captures | _keep_uppers(align_rewards, 1.)

        # For depth 1 & 2: Keep best aligns in range of 2
        pruning_arr[1][...] = captures | _keep_uppers(align_rewards, max(rmax - 2, 1.))

        # Big depth (>2): Just focus on best pruning_arr
        pruning_arr[2][...] = captures | _keep_uppers(align_rewards, rmax)

    return pruning_arr
