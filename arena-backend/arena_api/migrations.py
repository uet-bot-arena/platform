from pathlib import Path


def migrate(store):
    with store.lock:
        store.db.execute("PRAGMA foreign_keys=ON")
        store.db.execute("PRAGMA busy_timeout=5000")
        store.db.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)")
        store.db.commit()
        done = {r[0] for r in store.db.execute("SELECT version FROM schema_migrations")}
        files = sorted((Path(__file__).parent / "migrations").glob("*.sql"))
        known = {int(p.name.split("_")[0]) for p in files}
        if done - known:
            raise RuntimeError("Database schema is newer than this server")
        for path in files:
            version = int(path.name.split("_")[0])
            if version not in done:
                try:
                    store.db.executescript("BEGIN IMMEDIATE;\n" + path.read_text(encoding="utf-8") + f"\nINSERT INTO schema_migrations VALUES({version});\nCOMMIT;")
                except Exception:
                    store.db.rollback()
                    raise
