"""Real isolated builds: preprocess/PCH cache shares no competitor mount."""
import io
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from arena import sandbox
from arena.config import request_profile
from arena.service import Service
from arena.store import Store

def main():
    image=sandbox.image_id('uet-arena-worker:clock-v2');p=request_profile({})
    rows=[]
    with tempfile.TemporaryDirectory(prefix='arena-build-cache-') as tmp:
        store=Store(tmp);service=Service(store,start_worker=False)
        for i in range(3):
            source=f'// different team comment {i}\n'+(ROOT/'sdk/bot.cpp').read_text()
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w') as z:z.writestr('bot.cpp',source)
            submission=service.submit('team-'+str(i),'v1.0.0','cpp17',raw.getvalue(),'source-'+str(i)+'-test')
            started=time.monotonic()
            folder,manifest=service.ensure_artifact({'id':store.create('job','team-'+str(i),{'status':'building'})['id'],'image_id':image},submission,p)
            rows.append({'elapsed_ms':(time.monotonic()-started)*1000,'reported_ms':manifest['wall_ms'],'cache_hit':manifest['cache_hit'],
                         'translation_sha256':manifest['translation_sha256'],'artifact_sha256':manifest['artifact_sha256']})
        assert not rows[0]['cache_hit'] and all(row['cache_hit'] for row in rows[1:]),rows
        assert len({r['artifact_sha256'] for r in rows})==1
        assert len({r['translation_sha256'] for r in rows})==1
        store.db.close()
    output=ROOT/'test-results';output.mkdir(exist_ok=True)
    result={'image_id':image,'scope':'same code with different team comments, cold first + warm cache repeats','samples':rows,
            'median_ms':statistics.median(r['elapsed_ms'] for r in rows),'cold_ms':rows[0]['elapsed_ms'],
            'warm_median_ms':statistics.median(r['elapsed_ms'] for r in rows[1:])}
    (output/'build-cache.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
