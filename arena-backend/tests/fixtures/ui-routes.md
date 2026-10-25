# UI request contract

| Trang | API khi chuyển trang | Snapshot khi chuyển trang |
|---|---|---|
| home | /ui/personal?view=home, /ui/read?view=home | competitions.json |
| team | /ui/personal?view=team, /ui/read?view=team | — |
| submissions | /ui/personal?view=submissions, /ui/read?view=submissions | — |
| standings | — | competitions.json, competitions/{id}.json, fixtures/{id}.json, standings/{id}.json |
| schedule | — | competitions.json, competitions/{id}.json, fixtures/{id}.json, standings/{id}.json |
| queue | — | queue.json |
| competitions | — | competitions.json, competitions/{id}.json, fixtures/{id}.json, standings/{id}.json |
| live | — | competitions.json, competitions/{id}.json, fixtures/{id}.json, standings/{id}.json |
| announcements | — | announcements.json |
| benchmark | /ui/page?view=benchmark | — |
| practice | /ui/page?view=practice | — |
| jobs | /ui/page?view=jobs&job_id={job} | — |
| matching | /matching | — |
| rulesets | /rulesets | — |
