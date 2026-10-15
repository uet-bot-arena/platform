"""Temporary UI review server using a copy of integration-test data.

No production accounts or tournament state are changed. The worker stays off;
this preview is for login, forms, admin screens and recorded match playback.
Use localhost instead of 127.0.0.1 to keep browser cookies separate.
"""
import argparse
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena_api.app import create_app
from arena_api.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=41820)
    parser.add_argument('--proof-data', type=Path,
                        default=ROOT / 'test-results' / 'experimental-ui-proof-final')
    args = parser.parse_args()
    proof = args.proof_data.resolve()
    if not (proof / 'arena.sqlite').is_file():
        parser.error('Run the experimental integration test with retained proof data first')
    with tempfile.TemporaryDirectory(prefix='ui-review-', dir=ROOT / 'test-results') as directory:
        target = Path(directory)
        with sqlite3.connect(f'file:{proof / "arena.sqlite"}?mode=ro', uri=True) as source:
            with sqlite3.connect(target / 'arena.sqlite') as destination:
                source.backup(destination)
        for name in ('artifacts', 'sources'):
            if (proof / name).exists():
                shutil.copytree(proof / name, target / name)
        settings = Settings(data=target, origin=f'http://localhost:{args.port}')
        print(f'UI review: {settings.origin} (copied test data; worker disabled)', flush=True)
        uvicorn.run(create_app(settings, start_worker=False), host='127.0.0.1', port=args.port,
                    access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
