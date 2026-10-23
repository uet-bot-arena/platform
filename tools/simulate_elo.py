"""Seeded 1000-team selection experiment for PLAN_M1 gói 7b; no hub, bot or network.

Hidden strengths use Elo units. Each game has expected score from the same logistic
formula as Elo, with draws near equal strengths. A warmup ladder supplies the official
starting ratings. This is a specified synthetic model, not measured student bot skill.
"""
import argparse
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import platform
import random
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "arena-backend"))
from arena.elo import INITIAL, apply_round, expected, pair


def trial(seed, teams, ladder_rounds, checkpoints, official_k, strength_sd, progress=None):
    rng = random.Random(seed)
    names = [f"team-{i:04d}" for i in range(teams)]
    strengths = {t: rng.gauss(INITIAL, strength_sd) for t in names}
    true_top = set(sorted(names, key=lambda t: (-strengths[t], t))[:16])
    ratings = dict.fromkeys(names, INITIAL)
    played, changes = {}, {}
    recent = {t: deque(maxlen=3) for t in names}
    meetings = Counter()
    found = {}
    for stage, rounds in (("ladder", ladder_rounds), ("official", max(checkpoints))):
        for number in range(1, rounds + 1):
            pairs, bye = pair(ratings, meetings=meetings,
                              recent={t:set(v) for t,v in recent.items()}, official=stage == "official")
            assert bye is None  # Experiment has an even number of teams.
            games = []
            for a, b in pairs:
                for leg, (red, blue) in enumerate(((a, b), (b, a))):
                    chance = expected(strengths[red], strengths[blue])
                    draw = .8 * chance * (1 - chance)  # max 20%; preserves E[score] exactly.
                    value = rng.random()
                    score = 1 if value < chance - draw / 2 else .5 if value < chance + draw / 2 else 0
                    games.append({"id": f"{a}/{b}/{leg}", "red":red, "blue":blue, "red_score":score, "status":"completed"})
                if stage == "official":
                    meetings[a,b] += 1
                recent[a].append(b)
                recent[b].append(a)
            ratings, played, changes = apply_round(ratings, games, played, changes,
                                                   official=official_k if stage == "official" else None)
            if stage == "official" and number in checkpoints:
                selected = set(sorted(names, key=lambda t: (-ratings[t],t))[:20])
                hits = len(true_top & selected)
                found[number] = {"hits":hits, "recall":hits / 16}
                if progress:
                    progress(number, found[number])
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--teams',type=int,default=1000)
    parser.add_argument('--seeds',type=int,default=20)
    parser.add_argument('--first-seed',type=int,default=20261023)
    parser.add_argument('--ladder-rounds',type=int,default=30)
    parser.add_argument('--rounds',default='30')
    parser.add_argument('--k',type=int,default=24)
    parser.add_argument('--strength-sd',type=float,default=400)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    checkpoints=sorted(set(int(n) for n in args.rounds.split(',')))
    if args.teams<20 or args.teams%2 or args.seeds<1 or args.ladder_rounds<0 or min(checkpoints)<1 or not 1<=args.k<=64 or args.strength_sd<=0:
        parser.error('Cần số đội chẵn >=20, seed/vòng/K/SD hợp lệ')
    started=time.monotonic()
    rows=[]
    import networkx
    report={"model":{"teams":args.teams,"ladder_rounds":args.ladder_rounds,"official_checkpoints":checkpoints,"official_k":args.k,
                     "strength_distribution":"normal","strength_mean":1500,"strength_sd":args.strength_sd,"max_draw_probability":.2,
                     "bot_changes":False,"color_advantage":False,"neighbor_ranks":10,"target":"true top16 contained in rated top20"},
            "runtime":{"python":platform.python_version(),"networkx":networkx.__version__},
            "code_sha256":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),ROOT/'arena-backend/arena/elo.py')},
            "trials":rows,"summary":{}}
    for i in range(args.seeds):
        seed=args.first_seed+i
        found=trial(seed,args.teams,args.ladder_rounds,checkpoints,args.k,args.strength_sd,
                    progress=lambda n,r:print(json.dumps({"seed":seed,"checkpoint":n,**r,"elapsed_s":round(time.monotonic()-started,2)}),flush=True))
        rows.append({"seed":seed,"checkpoints":found})
        print(json.dumps({"seed":seed,"results":found,"elapsed_s":round(time.monotonic()-started,2)}),flush=True)
        for n in checkpoints:
            samples=[r["checkpoints"][n]["recall"] for r in rows]
            report["summary"][n]={"runs":len(samples),"mean_recall":statistics.mean(samples),"minimum":min(samples),"maximum":max(samples),
                                  "sample_sd":statistics.stdev(samples) if len(samples)>1 else None,
                                  "mean_true_top16_in_top20":statistics.mean(samples)*16}
        report["elapsed_s"]=round(time.monotonic()-started,3)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
