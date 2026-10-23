"""Check local Markdown link targets in tracked/new repository documentation."""
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+"[^"]*")?\s*\)')


def main():
    names = subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=ROOT).decode('utf-8').split('\0')
    problems, links = [], 0
    for name in sorted(set(names)):
        path=ROOT/name
        if not name.endswith('.md') or not path.is_file():
            continue
        fence=False
        for number,line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(),1):
            if line.lstrip().startswith(('```','~~~')):
                fence=not fence
                continue
            if fence:
                continue
            for match in LINK.finditer(line):
                target=match.group(1) or match.group(2)
                parts=urlsplit(target)
                if parts.scheme or parts.netloc or not parts.path:
                    continue
                links+=1
                # Local doc links use Markdown relative paths, never workstation absolute paths.
                candidate=path.parent/unquote(parts.path)
                if not candidate.exists():
                    problems.append(f'{name}:{number}: không tìm thấy {target}')
    for message in problems:
        print(message)
    print(f'{links} link file nội bộ; {len(problems)} lỗi')
    return bool(problems)


if __name__=='__main__':
    sys.exit(main())
