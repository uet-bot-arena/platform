"""Verify an already installed loopback hub with a real Windows grader.

Run from the development WSL checkout, before and after clean-distro restore.
Uses only the test organizer created by the installer acceptance procedure.
"""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx

from grader_cluster import Cluster, PASSWORD, windows_temp
from elo_native import source_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', default='http://127.0.0.1:4181')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    assert urlsplit(args.origin).hostname == '127.0.0.1', 'Fixture accounts are for loopback only'
    output = Path(__file__).resolve().parents[1] / 'test-results/m1/g12'
    output.mkdir(parents=True, exist_ok=True)
    saved = output / 'native-fixture.json'
    cluster = Cluster()
    cluster.admin.close()
    cluster.origin = args.origin
    cluster.admin = httpx.Client(base_url=args.origin, timeout=60)
    client = None
    result = dict(phase='restore-verify' if args.verify else 'seed', checks=[])
    try:
        session = cluster.api(cluster.admin, 'POST', '/auth/login', json=dict(username='organizer', password=PASSWORD))
        cluster.admin.headers['X-CSRF-Token'] = session['csrf_token']
        if args.verify:
            previous = json.loads(saved.read_text())
            client = httpx.Client(base_url=args.origin, timeout=60)
            login = cluster.api(client, 'POST', '/auth/login', json=dict(username=previous['username'], password=PASSWORD))
            client.headers['X-CSRF-Token'] = login['csrf_token']
            old = cluster.api(client, 'GET', '/jobs/' + previous['benchmark_id'])
            assert old['report']['passed'] and len(old['report']['samples']) == 72
            old_match = cluster.api(client, 'GET', '/jobs/' + previous['match_id'])
            events = cluster.api(client, 'GET', '/jobs/' + previous['match_id'] + '/events')
            assert old_match['status'] == 'completed' and events['items']
            source = dict(id=previous['submission_id'])
            result['checks'].append('restored credentials, full 72-sample report and completed match/replay readable')
        else:
            username='clean-'+str(time.time_ns())+'-1'
            client = cluster.team('Clean Ubuntu '+username[-12:], username)
            result['username']=username
            archive, _ = source_fixture(0)
            source = cluster.api(client, 'POST', '/submissions', expected=201, content=archive,
                headers={'Content-Type': 'application/zip', 'X-Submission-Version': 'v1.0.0'})
        grader_name=('clean-restore-' if args.verify else 'clean-install-')+str(time.time_ns())
        cluster.grader(grader_name, 'windows', 11)
        cluster.wait_online(list(cluster.graders))
        job = cluster.api(client, 'POST', '/benchmarks', expected=202, json=dict(submission_id=source['id'], match_ms=60000))
        done = cluster.wait_job(client, job['id'])
        assert done['status'] == 'completed' and done['report']['passed'] and len(done['report']['samples']) == 72, done.get('error')
        match = cluster.api(client, 'POST', '/matches', expected=202, json=dict(red_submission_id=source['id'], blue_submission_id=source['id'], match_ms=60000,delegate_timeout_claims=True))
        played = cluster.wait_job(client, match['id'])
        assert played['status'] == 'completed', played.get('error')
        result.update(submission_id=source['id'], benchmark_id=job['id'], match_id=match['id'], runtime=done['runtime'], finished_unix=int(time.time()))
        result['checks'].append('real Windows AppContainer grader completed 72 samples and a match against installed hub')
        if not args.verify:
            saved.write_text(json.dumps(result, indent=2) + '\n')
        (output / ('restored-native.json' if args.verify else 'installed-native.json')).write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        for item in cluster.graders.values():
            cluster.api(cluster.admin, 'POST', '/admin/graders/' + item['id'] + '/revoke')
        if client:
            client.close()
        assert cluster.shared.resolve().parent == windows_temp().resolve() and cluster.shared.name.startswith('uet-cluster-')
        assert cluster.data.resolve().parent == Path('/tmp') and cluster.data.name.startswith('cluster-hub-')
        cluster.stop()
        cluster.admin.close()


if __name__ == '__main__':
    main()
