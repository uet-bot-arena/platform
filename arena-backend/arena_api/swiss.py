"""Swiss pairing: score-distance matching, no rematch when possible, at most one bye per team."""
from arena.protocol import Fault

# Edge weight for a rematch. It dominates any score-distance cost, so a rematch is only
# used when no perfect matching without one exists (small events near the last rounds).
REMATCH_PENALTY = 10**9


def pair(teams, points, opponents, previous_byes):
    """Return (pairs, bye). Raise Fault only when the bye constraint cannot be met."""
    import networkx as nx
    ordered = sorted(teams, key=lambda t: (-points[t], t))
    bye_candidates = [None] if len(ordered) % 2 == 0 else [t for t in reversed(ordered) if t not in previous_byes]
    if not bye_candidates:
        raise Fault("competition_conflict", "Every team already had a bye; reduce the number of Swiss rounds")
    scale = len(ordered) ** 2
    for bye in bye_candidates:
        active = [t for t in ordered if t != bye]
        # Widen a sparse score-neighbour graph until a perfect matching exists.
        # This avoids a complete 2000-team graph in ordinary Swiss rounds.
        width = min(8, len(active))
        while width:
            graph = nx.Graph()
            graph.add_nodes_from(active)
            for i, a in enumerate(active):
                for j in range(i+1, min(len(active), i+width+1)):
                    b = active[j]
                    if b not in opponents.get(a, set()):
                        graph.add_edge(a, b, weight=abs(points[a]-points[b])*scale + j-i)
            matching = nx.min_weight_matching(graph)
            if len(matching)*2 == len(active):
                return sorted(tuple(sorted(p)) for p in matching), bye
            if width >= len(active):
                break
            width = min(len(active), width*2)
    # No pairing avoids every rematch (e.g. 6 teams, 5 rounds). Allow the fewest rematches,
    # still preferring close scores, instead of stopping the event.
    bye = bye_candidates[0]
    active = [t for t in ordered if t != bye]
    graph = nx.Graph()
    for i, a in enumerate(active):
        for j in range(i+1, len(active)):
            b = active[j]
            graph.add_edge(a, b, weight=(REMATCH_PENALTY if b in opponents.get(a, set()) else 0)
                           + abs(points[a]-points[b])*scale + j-i)
    matching = nx.min_weight_matching(graph)
    return sorted(tuple(sorted(p)) for p in matching), bye
