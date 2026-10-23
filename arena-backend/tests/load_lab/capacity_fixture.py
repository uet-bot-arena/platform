"""Offline synthetic qualification/fixture for read/SSE capacity, never real scores."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from arena.store import Store
from arena.service import Service
from arena_api.accounts import Accounts
from arena_api.competitions import Competitions
import test_competitions as helpers


def main():
    store=Store(sys.argv[1],recover_jobs=False)
    try:
        accounts=Accounts(store);service=Service(store,start_worker=False)
        comp=Competitions(store,service,accounts)
        actor=accounts.one("SELECT id FROM accounts WHERE username='organizer'")['id']
        item=comp.create(actor,dict(title='SSE capacity fixture',max_teams=2,match_ms=60000,runtime_id='sha256:'+'1'*64),'capacity-fixture-create')
        ident=item['id'];comp.transition(actor,ident,'open','capacity-fixture-open')
        for team in accounts.rows("SELECT team_id FROM accounts WHERE username IN ('cap-0','cap-1') ORDER BY username"):
            body=helpers.CompetitionAPI.qualified(SimpleNamespace(store=store),team['team_id'],item['config'])
            store.update(body['submission_id'],version='v99.0.0')
            comp.enter(actor,team['team_id'],ident,body,'capacity-fixture-entry-'+team['team_id'])
        comp.lock(actor,ident,'capacity-fixture-lock')
        comp.transition(actor,ident,'running','capacity-fixture-start')
        comp.tick()
        print(json.dumps(dict(competition_id=ident)))
    finally:
        store.db.close()


if __name__=='__main__':main()
