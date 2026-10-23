"""Hub-side check of a grader's report: replay the moves with the hub's own engine.

A grader is trusted to time turns and to observe bot faults (crash, timeout...), but every move
it reports is replayed here. A report whose moves, final board or result disagree with the
replay is rejected (result_mismatch) and never scored.
"""
from arena import engine
import math
from arena.adjudication import CURRENT, policy, position_key
from arena.config import stored_profile
from arena.protocol import Fault
from arena.runner import digest, engine_for, fixtures


def mismatch(detail):
    return Fault("result_mismatch", "Hub replay disagrees with the grader report: " + detail)


def verify(job, status, report):
    if status != "completed":
        return
    if not isinstance(report, dict):
        raise mismatch("report missing")
    if job["kind"] == "benchmark":
        verify_benchmark(job, report)
    else:
        verify_match(job, report)


def verify_benchmark(job, report):
    move_engine = engine_for(job)
    cases = fixtures(move_engine)
    samples = report.get("samples")
    if report.get("passed") is not True or not isinstance(samples, list) or len(samples) != len(cases) * 3:
        raise mismatch("benchmark needs 72 passed samples")
    if report.get("suite_sha256") != digest(cases):
        raise mismatch("benchmark suite differs")
    bounded=job.get('profile',{}).get('turn_ms')==500
    if bounded and (report.get('profile')!=job['profile'] or report.get('clock_scope')!='per_sample_500_ms'):
        raise mismatch('benchmark must use its pinned per-sample 500 ms clock')
    for index, sample in enumerate(samples):
        case = cases[index % len(cases)]
        if not isinstance(sample, dict) or sample.get("case_id") != case["id"]:
            raise mismatch(f"sample {index} is for another case")
        if bounded and (type(sample.get('wall_ms')) not in (int,float) or not math.isfinite(sample['wall_ms']) or not 0<=sample['wall_ms']<500):
            raise mismatch(f'sample {index} exceeds its 500 ms clock')
        action = sample.get("action")
        if not isinstance(action, dict) or "resign" in action:
            raise mismatch(f"sample {index} has no legal move")
        try:
            move_engine.apply(case["state"], action)
        except (ValueError, KeyError, TypeError):
            raise mismatch(f"sample {index} move is illegal") from None


def verify_match(job, report):
    official = job.get("official")
    rules = policy(official.get("adjudication") if official else job.get("adjudication"))
    move_engine = engine_for(job)
    key_of = move_engine.position_key if job.get("ruleset") else position_key
    p = stored_profile(job["profile"])
    state = move_engine.initial_state()
    claimed = report.get("result")
    moves = report.get("moves")
    if not isinstance(moves, list):
        raise mismatch("moves missing")
    faults = report.get("startup_faults")
    if faults:
        if moves or not isinstance(faults, list) or not 1 <= len(faults) <= 2:
            raise mismatch("startup failure with moves")
        winner = engine.other(faults[0]["side"]) if len(faults) == 1 else None
        state = engine.finish(state, winner, "startup_failure" if winner else "double_startup_failure")
    else:
        seen = {key_of(state): 1}
        for index, move in enumerate(moves):
            if state["result"]:
                raise mismatch(f"move {index} after the game ended")
            if not isinstance(move, dict) or move.get("side") != state["to_move"]:
                raise mismatch(f"move {index} by the wrong side")
            try:
                if job.get("ruleset"):
                    state, _ = move_engine.transition(state, move.get("action"))
                else:
                    state = engine.apply(state, move.get("action"))
            except (ValueError, KeyError, TypeError):
                raise mismatch(f"move {index} is illegal") from None
            if not state["result"] and rules == CURRENT:
                key = key_of(state)
                seen[key] = seen.get(key, 0) + 1
                if seen[key] >= rules["repetition_count"]:
                    state = engine.finish(state, None, "threefold_repetition")
        fault = report.get("fault")
        if fault:
            if state["result"] or not isinstance(fault, dict) or fault.get("side") != state["to_move"]:
                raise mismatch("fault by the wrong side or after the game ended")
            if fault.get("code") in ("worker_error", "worker_unavailable"):
                raise mismatch("infrastructure failure scored as a bot fault")
            state = engine.finish(state, engine.other(fault["side"]), fault["code"])
            if not state["result"] and rules == CURRENT:
                key = key_of(state)
                seen[key] = seen.get(key, 0) + 1
        if not state["result"]:
            reason = (claimed or {}).get("reason")
            if reason == "ply_limit" and rules == CURRENT and len(moves) == p.max_plies:
                state = engine.finish(state, None, "ply_limit")
            elif reason == "wall_limit" and rules == CURRENT and len(moves) < p.max_plies:
                state = engine.finish(state, None, "wall_limit")  # timed by the trusted grader
            elif report.get("aborted"):
                if claimed is not None:
                    raise mismatch("aborted game with a result")
                return
            else:
                raise mismatch("game did not end")
    if report.get("final_state") != state or claimed != state["result"]:
        raise mismatch("final board or result differs")
