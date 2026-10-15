from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Profile:
    id: str = "ottv2-practice-v1"
    turn_ms: int = 15_000
    match_ms: int | None = None
    grace_ms: int = 3_000
    startup_ms: int = 2_000
    build_ms: int = 30_000
    memory_mib: int = 256
    build_memory_mib: int = 1024
    cpu_count: int = 1
    max_plies: int = 512
    job_wall_ms: int = 1_200_000
    max_line_bytes: int = 65_536
    max_stderr_bytes: int = 262_144
    max_zip_bytes: int = 10 * 1024 * 1024
    max_source_bytes: int = 20 * 1024 * 1024

    def public(self):
        value = asdict(self)
        if self.match_ms is None:
            value.pop("match_ms")  # Preserve the exact pinned legacy profile.
        return value


def profile(turn_ms=15_000):
    if type(turn_ms) is not int or turn_ms not in (15_000, 30_000, 60_000, 300_000):
        raise ValueError("turn_ms must be 15000, 30000, 60000 or 300000")
    return Profile(id=f"ottv2-practice-{turn_ms // 1000}s-v1", turn_ms=turn_ms)


def match_profile(match_ms=300_000):
    if type(match_ms) is not int or match_ms not in (60_000, 300_000):
        raise ValueError("match_ms must be 60000 (Bullet) or 300000 (Normal)")
    return Profile(id=f"ottv2-bank-{match_ms // 1000}s-v2", match_ms=match_ms, turn_ms=match_ms)


def request_profile(body):
    if body.get("match_ms") is not None and body.get("turn_ms") is not None:
        raise ValueError("Choose match_ms or legacy turn_ms, not both")
    return profile(body["turn_ms"]) if body.get("turn_ms") is not None else match_profile(body.get("match_ms", 300_000))


def stored_profile(value):
    return match_profile(value["match_ms"]) if value.get("match_ms") is not None else profile(value["turn_ms"])


def worker_image(p, experimental=False):
    if p.match_ms is not None:
        return "uet-arena-worker:" + ("experimental-clock-v2" if experimental else "clock-v2")
    return "uet-arena-worker:experimental-v1" if experimental else None


BUILD_FLAGS = ["-std=c++17", "-O2", "-pipe", "-Wall", "-Wextra"]
