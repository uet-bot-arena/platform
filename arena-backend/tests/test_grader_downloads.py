import hashlib
import hmac
import json
from pathlib import Path
import time
from unittest.mock import patch

from test_web_api import APIHarness


class GraderDownloads(APIHarness):
    def setUp(self):
        super().setUp()
        self.downloads = Path(self.tmp.name) / 'downloads'
        self.downloads.mkdir()
        self.files = []
        for content, template in [(b'grader zip', 'uet-arena-grader-{}.zip'), (b'rootfs', 'rootfs-{}.tar.zst')]:
            digest = hashlib.sha256(content).hexdigest()
            name = template.format(digest[:12] if template.startswith('uet') else digest)
            (self.downloads / name).write_bytes(content)
            self.files.append(dict(file=name, sha256=digest, bytes=len(content)))
        self.manifest = self.files[0] | dict(grader_version='test', linux_rootfs=self.files[1])
        (self.downloads / 'grader-latest.json').write_text(json.dumps(self.manifest))
        self.app.state.settings.secrets._values['verification_hmac_key'] = 'test-only-download-key'
        patcher = patch('arena_api.grader_routes.DOWNLOADS', self.downloads)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_metadata_admin_only_and_both_links_signed_for_seven_days(self):
        team, _ = self.create_team()
        for client in [self.other_client(), team]:
            self.assertEqual(client.get('/api/v1/admin/grader-package').status_code, 403)
        item = self.client.get('/api/v1/admin/grader-package')
        self.assertEqual(item.status_code, 200, item.text)
        self.assertIn('no-store', item.headers['cache-control'])
        for entry in [item.json(), item.json()['linux_rootfs']]:
            url = entry['url']; _, _, _, _, _, exp, sig, file = url.split('/')
            now = int(time.time()*1000)
            self.assertTrue(now+7*86400000-10000 < int(exp) <= now+7*86400000)
            expected = hmac.new(b'test-only-download-key', f'grader-download|{file}|{exp}'.encode(), hashlib.sha256).hexdigest()
            self.assertEqual(sig, expected)
            response = self.other_client().get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['x-accel-redirect'], '/_grader_files/'+entry['file'])
            self.assertEqual(response.headers['cache-control'], 'private, no-store')

    def test_tampering_expiry_wrong_file_and_internal_urls_rejected(self):
        item = self.client.get('/api/v1/admin/grader-package').json()
        url = item['url']; parts = url.split('/')
        variants = [url.replace(parts[6], ('0' if parts[6][0]!='0' else '1')+parts[6][1:]),
                    url.replace(parts[5], str(int(time.time()*1000)-1)),
                    url.replace(parts[5], str(int(time.time()*1000)+8*86400000)),
                    url.replace(parts[5], 'not-a-time'),
                    url.replace(item['file'], self.files[1]['file']),
                    url.replace(item['file'], 'grader-latest.json'),
                    url.replace(item['file'], item['file']+'.sha256'),
                    '/_grader_files/'+item['file'], '/downloads/'+item['file']]
        for path in variants:
            with self.subTest(path=path):self.assertEqual(self.other_client().get(path).status_code, 404)
        for exp in [str(int(time.time()*1000)-1000),str(int(time.time()*1000)+8*86400000)]:
            sig=hmac.new(b'test-only-download-key',f"grader-download|{item['file']}|{exp}".encode(),hashlib.sha256).hexdigest()
            self.assertEqual(self.other_client().get(f"/api/v1/grader/download/{exp}/{sig}/{item['file']}").status_code,404)
        (self.downloads / item['file']).unlink()
        self.assertEqual(self.other_client().get(url).status_code, 404)

    def test_signed_symlink_is_not_served(self):
        item = self.client.get('/api/v1/admin/grader-package').json()
        path = self.downloads / item['file'];path.unlink();path.symlink_to(self.downloads/'grader-latest.json')
        self.assertEqual(self.other_client().get(item['url']).status_code, 404)
