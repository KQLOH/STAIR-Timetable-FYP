"""Small offline file-link checker, independently written for the public starter."""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit
import argparse
import os
import re

LINK = re.compile(r'!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?\)')
EXCLUDED = {'.git', '.venv', 'venv', '__pycache__', 'private', 'local', 'data', 'sources', 'baseline'}


@dataclass(frozen=True)
class Issue:
    document: str
    target: str
    reason: str


def check_document(document, root):
    root = Path(root).resolve()
    document = Path(document).resolve()
    if not document.is_relative_to(root):
        raise ValueError('Document must stay inside the checked root')
    issues = []
    for match in LINK.finditer(document.read_text(encoding='utf-8-sig')):
        target = match.group(1).strip('<>')
        parts = urlsplit(target)
        if parts.scheme or parts.netloc or target.startswith('#'):
            continue
        path_text = unquote(parts.path)
        if not path_text:
            continue
        resolved = (document.parent / path_text).resolve()
        reason = 'OUTSIDE_ROOT' if not resolved.is_relative_to(root) else 'MISSING_TARGET' if not resolved.exists() else None
        if reason:
            issues.append(Issue(document.relative_to(root).as_posix(), target, reason))
    return issues


def check_tree(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Root must be an existing directory')
    checked = 0
    issues = []
    for current, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if d not in EXCLUDED and not (Path(current)/d).is_symlink())
        for name in sorted(names):
            document = Path(current)/name
            if document.suffix.lower() == '.md' and not document.is_symlink():
                checked += 1
                issues.extend(check_document(document, root))
    return checked, issues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args(argv)
    try:
        checked, issues = check_tree(args.root)
    except ValueError as error:
        parser.error(str(error))
    for issue in issues:
        print(f'{issue.document}: {issue.reason}: {issue.target}')
    print(f'Markdown documents checked: {checked}; broken/outside file links: {len(issues)}')
    return 1 if issues else 0


if __name__ == '__main__':
    raise SystemExit(main())
