"""Indexed intake and a real 1,000-repository, 200 ms/request poll batch."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from test_web_api import APIHarness


class GitHubCosts(APIHarness):
    def test_status_and_pending_checks_use_partial_indexes(self):
        a=self.app.state.accounts
        queries=["SELECT DISTINCT g.* FROM github_checks c INDEXED BY github_checks_unsent CROSS JOIN github_commits g ON g.team_id=c.team_id AND g.sha=c.sha WHERE c.status_sent=0 AND g.state IN ('pass','fail')",
                 "SELECT * FROM github_checks WHERE state IN ('waiting','checking') AND retry_ms<=0"]
        for query in queries:
            plan=' '.join(r['detail'] for r in a.rows('EXPLAIN QUERY PLAN '+query))
            self.assertNotIn('SCAN g ',plan)
            self.assertNotIn('SCAN github_commits',plan)
            self.assertNotIn('SCAN github_checks',plan)
            self.assertTrue('github_checks_unsent' in plan or 'github_checks_due' in plan,plan)

    def test_thousand_due_repos_finish_within_60_seconds_without_per_batch_sleep(self):
        gh=self.app.state.github;store=self.app.state.store
        with store.transaction():
            for i in range(1000):
                ident=str(i)
                store.db.execute("INSERT INTO teams(id,name,name_key,email,phone,members,created_ms) VALUES(?,?,?,'fixture@vnu.test','','[]',0)",(ident,ident,ident))
                store.db.execute("INSERT INTO github_repositories(team_id,repo_id,owner,name,installation_id,attached_ms,etag) VALUES(?,?,'fixture',?,1,0,'same')",(ident,i,ident))
        completed=threading.Event();reads=[]
        class SlowGitHub:
            def get(self,owner,name,installation,suffix='',headers=None):
                time.sleep(.2)
                if not suffix:return 200,{},dict(id=int(name),private=True)
                reads.append(name)
                if len(reads)==1000:completed.set()
                return 304,{},None
        gh.client=SlowGitHub()
        gh.pollers=ThreadPoolExecutor(max_workers=32)
        self.addCleanup(gh.close_pools)
        sql=[];store.db.set_trace_callback(lambda statement:sql.append(statement) if statement=='COMMIT' else None)
        try:
            started=time.monotonic();gh.thread.start()
            self.assertTrue(completed.wait(60),'1,000 due repositories exceeded 60 seconds')
            self.assertLessEqual(time.monotonic()-started,60)
            gh.stop.set();gh.wake.set();gh.thread.join(5)
            self.assertEqual(len(set(reads)),1000)
            self.assertLessEqual(len(sql),32,'Poll persisted each repo separately')
        finally:store.db.set_trace_callback(None)
