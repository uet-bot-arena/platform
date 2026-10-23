"""Pure Elo/pairing rules. Persisting rounds and publishing standings are separate work."""
import math
from collections import deque

from arena.protocol import Fault

INITIAL = 1500.0
OFFICIAL_K = ((20, 32), (None, 16))


def expected(rating, opponent):
    return 1 / (1 + 10 ** (max(-4000, min(4000, opponent - rating)) / 400))


def k_factor(played, change_remaining=0, official=None):
    if official is True or isinstance(official, (list, tuple)):
        for limit, value in OFFICIAL_K if official is True else official:
            if limit is None or played < limit:
                return value
        raise ValueError("Official K schedule needs a final unbounded tier")
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


def _augment(graph, matching, active):
    mates = {a:b for a,b in matching for a,b in ((a,b),(b,a))}
    for start in active:
        if start in mates:
            continue
        todo, visited = deque([(start, [start])]), {start}
        found = None
        while todo and found is None:
            a, path = todo.popleft()
            for b in sorted(graph[a], key=lambda b: (-graph[a][b]['weight'], b)):
                if b in path:
                    continue
                if b not in mates:
                    found = path + [b]
                    break
                c = mates[b]
                if c not in visited and c not in path:
                    visited.add(c)
                    todo.append((c, path + [b,c]))
        if found:
            for a,b in zip(found[::2], found[1::2]):
                mates[a], mates[b] = b,a
    return [(a,b) for a,b in mates.items() if a < b]


def pair(ratings, meetings=None, recent=None, byes=None, neighbor_ranks=10, official=False, no_repeat=False):
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
            if (distance > neighbor_ranks or no_repeat and meetings.get(tuple(sorted((a,b))), 0)
                    or not official and (b in recent.get(a, ()) or a in recent.get(b, ()))):
                continue
            edges.append((distance, a, b))
    # Blossom traverses incident edges from the newest first; insertion order only breaks ties.
    for _, a, b in sorted(edges, reverse=True):
        graph.add_edge(a, b, weight=-1.0*meetings.get(tuple(sorted((a, b))), 0) if official else 0.0, uniform=1.0)
    if any(graph.degree(t) == 0 for t in active):
        raise Fault("competition_conflict", "Có đội không còn đối thủ trong phạm vi hạng cho phép")
    # Integer-valued float weights are exact here; avoid NetworkX's expensive
    # integer-only diagnostic pass. Both branches return the same matching.
    # A perfect matching of unseen edges has total meeting cost zero: already optimal.
    # Try unweighted blossom first instead of solving weighted ties on thousands of edges.
    unseen = graph.copy()
    if official:
        unseen.remove_edges_from([(a,b) for a,b,data in graph.edges(data=True) if data['weight'] < 0])
    # The nearest-first greedy solution is usually already perfect on a rank band.
    # Blossom remains the exact fallback; never accept an incomplete round.
    matching, used = [], set()
    for _, a, b in sorted(edges):
        if a not in used and b not in used and unseen.has_edge(a,b):
            matching.append((a,b))
            used.update((a,b))
    if len(used) != len(active):
        matching = _augment(unseen, matching, active)
        matched = {t for p in matching for t in p}
        if len(matching)*2 != len(active) and any(unseen.degree(t) for t in active if t not in matched):
            matching = nx.max_weight_matching(unseen, maxcardinality=True, weight="uniform")
    if official and len(matching) * 2 != len(active):
        # At least one repeat is needed for each pair missing from a maximum
        # unseen matching. A completion meeting that bound is already optimal.
        bound = len(active)//2-len(matching)
        candidate = _augment(graph, matching, active)
        if len(candidate)*2 == len(active) and -sum(graph[a][b]['weight'] for a,b in candidate) == bound:
            matching = candidate
        else:
            matching = nx.max_weight_matching(graph, maxcardinality=True)
    if len(matching) * 2 != len(active):
        raise Fault("competition_conflict", "Không ghép đủ đội trong giới hạn hạng và đối thủ gần đây")
    return sorted(tuple(sorted(p)) for p in matching), bye


def top_group(ratings, size=64):
    if type(size) is not int or size < 1:
        raise ValueError("Group size must be positive")
    return {team: ratings[team] for team in sorted(ratings, key=lambda t: (-ratings[t], t))[:size]}


def general_pair(ratings, meetings=None, byes=None, neighbor_ranks=10):
    """General phase: never repeat; widen the whole rank band only as far as needed."""
    def attempt(radius):
        return pair(ratings, meetings=meetings, byes=byes, neighbor_ranks=radius,
                    official=True, no_repeat=True)
    maximum = max(1, len(ratings)-1)
    radius = min(neighbor_ranks, maximum)
    try:
        pairs, bye = attempt(radius)
        return pairs, bye, radius
    except Fault:
        low = radius
    high = min(maximum, low*2)
    while True:
        try:
            found = attempt(high)
            break
        except Fault:
            if high == maximum:
                raise
            low, high = high, min(maximum, high*2)
    while low+1 < high:
        middle = (low+high)//2
        try:
            found = attempt(middle)
            high = middle
        except Fault:
            low = middle
    return *found, high


def rank(ratings, games):
    """Official Elo, wins, then head-to-head within each still tied group.

    Equal rank after these keys requires the two Bullet games; ID only makes
    display order stable and must never award a finalist place by itself.
    """
    from collections import Counter
    wins, head = Counter(), Counter()
    games = list(games)
    for game in games:
        for side in ('red','blue'):
            score=game.get(side+'_score',1-game['red_score'])
            if score == 1:
                wins[game[side]] += 1
    for game in games:
        a,b=game['red'],game['blue']
        if (ratings[a],wins[a]) == (ratings[b],wins[b]):
            head[a]+=game['red_score']
            head[b]+=game.get('blue_score',1-game['red_score'])
    ordered=sorted(ratings,key=lambda t:(-ratings[t],-wins[t],-head[t],t))
    result=[]; previous=None; number=0
    for index,team in enumerate(ordered,1):
        key=(ratings[team],wins[team],head[team])
        if key!=previous:
            number=index
        result.append(dict(team_id=team,rating=ratings[team],wins=wins[team],head_to_head=head[team],rank=number))
        previous=key
    return result


def select(ranked, size):
    """Do not break a qualifying-boundary tie by internal team ID."""
    if type(size) is not int or size < 1:
        raise ValueError('Selection size must be positive')
    if len(ranked)>size and ranked[size-1]['rank']==ranked[size]['rank']:
        raise Fault('competition_conflict','Còn bằng hạng tại ranh giới chọn đội; cần BTC quyết định')
    return ranked[:size]


def round_robin_schedule(teams):
    """A fixed game-ID order; every pair plays once in each color, without barriers."""
    names = sorted(teams)
    if len(names) != len(set(names)):
        raise ValueError('Duplicate round-robin team')
    return [(red, blue) for i, a in enumerate(names) for b in names[i+1:]
            for red, blue in ((a, b), (b, a))]


def official_phase(team_count, general_done=0, group_size=64, general_rounds=20):
    return 'general' if team_count > group_size and general_done < general_rounds else 'round_robin'


def round_robin_rank(ratings, games):
    """Points, head-to-head among equal-point teams, wins, then frozen general Elo."""
    from collections import Counter
    games = list(games)
    apply_round(ratings, games, official=0)  # Validate scores/IDs without changing Elo.
    points, wins, head, played = Counter(), Counter(), Counter(), Counter()
    for game in sorted(games, key=lambda g: g['id']):
        for side in ('red', 'blue'):
            team = game[side]
            score = game.get(side+'_score', 1-game['red_score'])
            points[team] += score
            wins[team] += score == 1
            played[team] += 1
    for game in games:
        a, b = game['red'], game['blue']
        if points[a] == points[b]:
            head[a] += game['red_score']
            head[b] += game.get('blue_score', 1-game['red_score'])
    key = lambda t: (points[t], head[t], wins[t], ratings[t])
    ordered = sorted(ratings, key=lambda t: (*(-v for v in key(t)), t))
    result, previous, number = [], None, 0
    for index, team in enumerate(ordered, 1):
        current = key(team)
        if current != previous:
            number = index
        result.append(dict(team_id=team, points=points[team], head_to_head=head[team],
                           wins=wins[team], rating=ratings[team], played=played[team], rank=number))
        previous = current
    return result
