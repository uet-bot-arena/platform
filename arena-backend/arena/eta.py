"""Shared ten-minute exponentially weighted game/check duration estimates."""
import math

WINDOW_MS=600_000


class Timing:
    def __init__(self,store):
        self.store=store
        with store.lock,store.db:
            store.db.executescript("""
              CREATE TABLE IF NOT EXISTS duration_samples(
                job TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,profile_id TEXT NOT NULL,platform TEXT NOT NULL,runtime_id TEXT NOT NULL,
                ended_ms INTEGER NOT NULL,duration_ms INTEGER NOT NULL CHECK(duration_ms>=0));
              CREATE INDEX IF NOT EXISTS duration_lookup ON duration_samples(kind,profile_id,platform,runtime_id,ended_ms);
              CREATE INDEX IF NOT EXISTS duration_age ON duration_samples(ended_ms);
            """)

    def record(self,job,runtime,created_ms,ended_ms):
        # Caller holds the accepted-result transaction; identity is pinned at lease time.
        self.store.db.execute('INSERT OR IGNORE INTO duration_samples VALUES(?,?,?,?,?,?,?)',
            (job['id'],job['kind'],job['profile']['id'],runtime['platform'],runtime['runtime_id'],ended_ms,max(0,ended_ms-created_ms)))
        self.store.db.execute('DELETE FROM duration_samples WHERE ended_ms<?',(ended_ms-WINDOW_MS,))

    def estimate(self,kind,profile_id,platform,runtime_id,remaining,slots,*,started,now_ms):
        # now_ms uses service.lease_now_ms(), the samples' monotonic anchor.
        # No estimate before work is received, or without compatible available slots.
        if not started or remaining<=0 or slots<=0:
            return None
        with self.store.lock:
            samples=self.store.db.execute('SELECT ended_ms,duration_ms FROM duration_samples WHERE kind=? AND profile_id=? AND platform=? AND runtime_id=? AND ended_ms BETWEEN ? AND ?',
                (kind,profile_id,platform,runtime_id,now_ms-WINDOW_MS,now_ms)).fetchall()
        if not samples:
            return None
        weights=[math.exp((end-now_ms)/WINDOW_MS) for end,_ in samples]
        mean=sum(weight*sample[1] for weight,sample in zip(weights,samples))/sum(weights)/1000
        seconds=math.ceil(remaining/slots)*mean
        return dict(lower_seconds=math.floor(seconds*.75),upper_seconds=math.ceil(seconds*1.25),
                    mean_game_seconds=mean,samples=len(samples),as_of_ms=now_ms)
