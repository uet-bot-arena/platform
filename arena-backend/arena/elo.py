"""Pure Elo/pairing rules. Persisting rounds and publishing standings are separate work."""
import math

from arena.protocol import Fault

INITIAL = 1500.0


def expected(rating, opponent):
    return 1 / (1 + 10 ** (max(-4000, min(4000, opponent - rating)) / 400))


def k_factor(played, change_remaining=0, official=None):
    return official if official is not None else 32 if played < 20 or change_remaining > 0 else 16


def apply_round(ratings, games, played=None, change_remaining=None, official=None):
    """Apply an entire completed round in game-ID order, independently of finish order.

    Each game: id, red, blue, red_score, optional blue_score, status='completed'.
    An infrastructure failure leaves the whole round uncommitted. Both bots failing
    startup can both score zero, matching the existing tournament adjudication.
    """
    games = list(games)
    seen = set()
    for game in games:
        red, blue = game["red"], game["blue"]
        score = game["red_score"]
        other = game.get("blue_score", 1 - score)
        if (game["status"] != "completed" or game["id"] in seen or red == blue or red not in ratings or blue not in ratings
                or type(score) not in (int, float) or type(other) not in (int, float)
                or (score, other) not in ((1, 0), (0, 1), (.5, .5), (0, 0))):
            raise Fault("competition_conflict", "Đợt Elo chưa hoàn tất hoặc có ván không hợp lệ")
        seen.add(game["id"])
    values = dict(ratings)
    counts = {team: (played or {}).get(team, 0) for team in ratings}
    changes = {team: (change_remaining or {}).get(team, 0) for team in ratings}
    for game in sorted(games, key=lambda g: g["id"]):
        red, blue = game["red"], game["blue"]
        chance = expected(values[red], values[blue])
        score, other = game["red_score"], game.get("blue_score", 1 - game["red_score"])
        a, b = values[red], values[blue]
        values[red] = a + k_factor(counts[red], changes[red], official) * (score - chance)
        values[blue] = b + k_factor(counts[blue], changes[blue], official) * (other - (1 - chance))
        for team in (red, blue):
            counts[team] += 1
            changes[team] = max(0, changes[team] - 1)
    return values, counts, changes


def pair(ratings, meetings=None, recent=None, byes=None, neighbor_ranks=10, official=False):
    """Pair within ±N ranks; avoid recent ladder opponents, minimize official meetings.

    The graph has at most N*teams edges. Equal-cost choices follow stable node/edge order.
    Bye rotates by count, last-bye round, then ID; it never creates an Elo game.
    """
    import networkx as nx
    if type(neighbor_ranks) is not int or neighbor_ranks < 1 or any(not math.isfinite(r) for r in ratings.values()):
        raise ValueError("Invalid Elo pairing input")
    ordered = sorted(ratings, key=lambda t: (-ratings[t], t))
    meetings, recent, byes = meetings or {}, recent or {}, byes or {}
    bye = min(ordered, key=lambda t: (*byes.get(t, (0, -1)), t)) if len(ordered) % 2 else None
    positions = {team: index for index, team in enumerate(ordered)}
    active = [team for team in ordered if team != bye]
    graph = nx.Graph()
    graph.add_nodes_from(active)
    edges = []
    for i, a in enumerate(active):
        for b in active[i + 1:i + 1 + neighbor_ranks]:
            distance = abs(positions[a] - positions[b])
            if distance > neighbor_ranks or not official and (b in recent.get(a, ()) or a in recent.get(b, ())):
                continue
            edges.append((distance, a, b))
    # Blossom traverses incident edges from the newest first; insertion order only breaks ties.
    for _, a, b in sorted(edges, reverse=True):
        graph.add_edge(a, b, weight=-meetings.get(tuple(sorted((a, b))), 0) if official else 0)
    # A perfect matching of unseen edges has total meeting cost zero: already optimal.
    # Try unweighted blossom first instead of solving weighted ties on thousands of edges.
    unseen = graph.copy()
    if official:
        unseen.remove_edges_from([(a,b) for a,b,data in graph.edges(data=True) if data['weight'] < 0])
    matching = nx.max_weight_matching(unseen, maxcardinality=True, weight="uniform")
    if official and len(matching) * 2 != len(active):
        matching = nx.max_weight_matching(graph, maxcardinality=True)
    if len(matching) * 2 != len(active):
        raise Fault("competition_conflict", "Không ghép đủ đội trong giới hạn hạng và đối thủ gần đây")
    return sorted(tuple(sorted(p)) for p in matching), bye
