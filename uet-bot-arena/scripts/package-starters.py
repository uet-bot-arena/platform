"""Package source-only starter ZIPs, preserving the vendored header's license."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

assets = Path(__file__).resolve().parents[1] / 'dist/assets'
for runtime, names in {'cpp': ['bot.cpp', 'json.hpp', 'README.md', 'LICENSE.MIT'],
                       'codon': ['bot.py', 'requirements.txt', 'README.md']}.items():
    archive = assets / ('bot-starter-' + runtime + '.zip')
    with ZipFile(archive, 'w', ZIP_DEFLATED) as zipped:
        for name in names:
            zipped.write(assets / ('starter-' + runtime) / name, name)
    with ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        assert sorted(zipped.namelist()) == sorted(names)
    print(archive.name, archive.stat().st_size, 'bytes; source only')
