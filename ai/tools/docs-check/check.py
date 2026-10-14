"""AI 문서의 절·링크·PR Issue 참조를 커밋 사본끼리 비교한다.

Git의 blob만 읽는다. checkout·fetch·index 변경이나 네트워크 요청은 하지 않는다.
계약 변경은 merge-base→head, 참조 오류는 실제 base→병합 결과로 나눠 검사한다.
Markdown의 일반적인 제목·링크·절 참조를 지원하며 자유로운 한국어 문장 전체를
해석하지 않는다. 합의 댓글·리뷰 승인·문서와 구현의 일치는 사람이 확인한다.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import difflib
import html
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import unicodedata
from urllib.parse import unquote, urlsplit

if sys.version_info < (3, 10):
    print('검사 실행 오류: Python 3.10 이상이 필요합니다', file=sys.stderr)
    raise SystemExit(2)


class CheckError(Exception):
    """검사를 실행할 수 없는 입력·Git 오류."""


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                            env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'})
    if result.returncode:
        raise CheckError(result.stderr.decode('utf-8', errors='replace').strip())
    return result.stdout


def commit(repo: Path, ref: str) -> str:
    return git(repo, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()


def ancestor(repo: Path, before: str, after: str) -> bool:
    result = subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', before, after],
                            capture_output=True, env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'})
    if result.returncode not in (0, 1):
        raise CheckError(result.stderr.decode('utf-8', errors='replace').strip())
    return result.returncode == 0


def checked_document(path: str) -> bool:
    """AI 문서와 이를 안내하는 Markdown만 시작점으로 검사한다."""
    return path.endswith('.md') and (
        path.startswith('docs/ai/') or path in {
            'AGENTS.md', 'CLAUDE.md', 'ai/AGENTS.md', 'ai/CLAUDE.md',
            'ai/README.md', 'ai/research/README.md',
        } or path.startswith('ai/tools/docs-check/'))


class Snapshot:
    def __init__(self, repo: Path, sha: str):
        self.repo, self.sha = repo, sha
        self.files: dict[str, str] = {}
        for record in git(repo, 'ls-tree', '-r', '-z', '--full-tree', sha).split(b'\0'):
            if not record:
                continue
            metadata, name = record.split(b'\t', 1)
            mode, kind, blob = metadata.split()
            if kind == b'blob':
                self.files[name.decode('utf-8')] = blob.decode('ascii')
        self.cache: dict[str, Document] = {}

    def document(self, path: str) -> 'Document':
        if path not in self.cache:
            try:
                text = git(self.repo, 'cat-file', 'blob', self.files[path]).decode('utf-8')
            except UnicodeDecodeError as exc:
                raise CheckError(f'{path}: UTF-8 문서가 아닙니다') from exc
            self.cache[path] = Document(text)
        return self.cache[path]

    def resolve(self, source: str, target: str) -> str | None:
        # URL 경로는 POSIX 기준이며 디스크 파일을 직접 열지 않는다.
        path = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(target)))
        if path.startswith('../') or path == '..' or path.startswith('/'):
            return None
        if path in self.files:
            return path
        for name in ('README.md', 'readme.md'):
            candidate = posixpath.join(path, name)
            if candidate in self.files:
                return candidate
        if any(name.startswith(path.rstrip('/') + '/') for name in self.files):
            return path.rstrip('/') + '/'
        return None


def mask_inline_code(text: str, keep_file_names: bool = False) -> str:
    """문단의 코드스팬을 가리되 HTML 주석 안의 backtick은 짝짓지 않는다."""
    output, position = [], 0
    while position < len(text):
        if position == 0 or text[position - 1] == '\n':
            end = text.find('\n', position)
            end = len(text) if end < 0 else end + 1
            line = text[position:end]
            item = re.match(r'^ {0,3}(?:[-*+]|\d+[.)])\s+', line)
            indent = item.end() if item else 0
            marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line[indent:].rstrip('\n'))
            if marker:
                cursor = end
                while cursor < len(text):
                    stop = text.find('\n', cursor)
                    stop = len(text) if stop < 0 else stop + 1
                    following = text[cursor:stop]
                    if indent and following.strip() and len(following) - len(following.lstrip(' ')) < indent:
                        break
                    closing = re.match(r'^ {0,3}(' + re.escape(marker[1][0]) + r'{'+ str(len(marker[1])) + r',})\s*$', following[indent:])
                    cursor = stop
                    if closing:
                        break
                output.append(text[position:cursor])
                position = cursor
                continue
            if line.startswith(('    ', '\t')) or re.match(r'^ {0,3}>', line):
                output.append(line)
                position = end
                continue
        if text.startswith('<!--', position):
            stop = text.find('-->', position + 4)
            stop = len(text) if stop < 0 else stop + 3
            output.append(text[position:stop])
            position = stop
        elif text[position] == '`':
            delimiter = re.match(r'`+', text[position:])[0]
            start = position + len(delimiter)
            boundary = re.search(r'\n(?:[ \t]*\n| {0,3}(?:#{1,6}\s|`{3,}|~{3,}|>|[-*+]\s|\d+[.)]\s))', text[start:])
            limit = start + boundary.start() if boundary else len(text)
            closing = re.compile(r'(?<!`)' + re.escape(delimiter) + r'(?!`)').search(text, start, limit)
            if closing:
                stop = closing.end()
                content = text[start:closing.start()]
                span = text[position:stop]
                if not (keep_file_names and FILE_REFERENCE.fullmatch(content)):
                    span = ''.join('\n' if char == '\n' else '\uFFFC' for char in span)
                output.append(span)
                position = stop
            else:
                output.append(delimiter)
                position = start
        else:
            output.append(text[position])
            position += 1
    return ''.join(output)


def visible_lines(text: str) -> list[str]:
    """줄 번호를 보존하며 HTML 주석·fenced/들여쓴 코드·인용을 제외한다."""
    output = []
    fence: str | None = None
    width = 0
    fence_indent = 0
    comment = False
    quote = False
    list_indent = 0
    masked_lines = mask_inline_code(text).splitlines()
    for number, line in enumerate(text.splitlines()):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence is not None and fence_indent and line.strip() and len(line) - len(line.lstrip(' ')) < fence_indent:
            fence = None
        if fence is not None:
            closing_line = line[fence_indent:] if line.startswith(' ' * fence_indent) else line
            closing = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', closing_line)
            if closing and closing[1][0] == fence and len(closing[1]) >= width and not closing[2].strip():
                fence = None
            output.append('')
            continue
        item = re.match(r'^ {0,3}(?:[-*+]|\d+[.)])\s+', line)
        if item and not comment:
            list_indent = item.end()
            logical = line[list_indent:]
        elif list_indent and len(line) - len(line.lstrip(' ')) >= list_indent:
            logical = line[list_indent:]
        else:
            logical = line
            if line.strip():
                list_indent = 0
        if not comment and re.match(r'^ {0,3}>', logical):
            quote = True
            output.append('')
            continue
        if quote and re.fullmatch(r' {0,3}(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})', logical):
            quote = False
        if not comment and quote and logical.strip() and not re.match(
                r'^ {0,3}(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|`{3,}|~{3,})', logical):
            output.append('')
            continue
        quote = False
        if not comment and logical.startswith(('    ', '\t')):
            output.append('')
            continue
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', logical)
        if marker and not comment:
            fence, width = marker[1][0], len(marker[1])
            fence_indent = list_indent
            output.append('')
            quote = False
            continue
        clean, position = '', 0
        while position < len(line):
            if comment:
                stop = line.find('-->', position)
                if stop < 0:
                    clean += ' ' * (len(line) - position)
                    break
                clean += ' ' * (stop + 3 - position)
                position, comment = stop + 3, False
            elif line.startswith('<!--', position) and masked_lines[number][position:position + 4] == '<!--':
                comment = True
            elif line[position] == '`':
                delimiter = re.match(r'`+', line[position:])[0]
                stop = line.find(delimiter, position + len(delimiter))
                if stop >= 0:
                    clean += line[position:stop + len(delimiter)]
                    position = stop + len(delimiter)
                else:
                    clean += line[position]
                    position += 1
            else:
                clean += line[position]
                position += 1
        if re.match(r'^ {0,3}>', clean):
            quote = True
            output.append('')
        elif quote and clean.strip() and not re.match(r'^ {0,3}(?:#{1,6}\s|[-*+]\s|\d+[.)]\s)', clean):
            output.append('')
        elif not list_indent and clean.startswith(('    ', '\t')):
            quote = False
            output.append('')
        else:
            quote = False
            output.append(clean)
    return output


def slug(text: str) -> str:
    code: list[str] = []
    def keep_code(match: re.Match) -> str:
        code.append(match[2])
        return f'codeplaceholder{len(code) - 1}end'
    text = re.sub(r'(`+)(.*?)\1', keep_code, text)
    text = re.sub(r'<[^>]*>', '', text)
    for start, end, _ in reversed(list(inline_links(text))):
        label = text[start:end].split('](', 1)[0].lstrip('!')[1:]
        text = text[:start] + label + text[end:]
    text = html.unescape(text)
    text = re.sub(r'(?<!\w)(_{1,2})(?=\S)(.+?)(?<=\S)\1(?!\w)', r'\2', text)
    for index, value in enumerate(code):
        text = text.replace(f'codeplaceholder{index}end', value)
    text = re.sub(r'[`*~]', '', text).lower().strip()
    return ''.join(char for char in text if char in '-_ ' or unicodedata.category(char)[0] in 'LN').replace(' ', '-')


HEADING = re.compile(r'^ {0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$')
SECTION = re.compile(r'^(\d+(?:-\d+)*)[.]?(?:\s|$)')
NUMBER = r'\d+(?:-\d+)*'
NUMBERS = rf'{NUMBER}(?:\s*(?:[·,/~–]|및|와)\s*{NUMBER})*'
SECTION_REFERENCE = re.compile(rf'(?<![\w-])({NUMBERS})\s*절')
FILE_REFERENCE = re.compile(r'(?P<file>(?:[\w.-]+/)*[\w.-]+\.md)')
AFTER_FILE = re.compile(rf'^`*\s+({NUMBERS})(?:\s*절|(?=$|[^\w-]|의|은|는|을|를|에))')
LINK_DEFINITION = re.compile(r'^ {0,3}\[[^]]+\]:\s*(<[^>]+>|\S+)')


def inline_links(line: str):
    """일반 링크·이미지의 목적지에서 이스케이프·괄호 깊이를 추적한다."""
    for opening in re.finditer(r'!?\[(?:\\.|[^\]\n])*\]\(\s*', line):
        start, position = opening.start(), opening.end()
        prefix = line[:start + int(line[start:].startswith('!'))]
        if (len(prefix) - len(prefix.rstrip(chr(92)))) % 2:
            continue
        first, depth = position, 0
        if position < len(line) and line[position] == '<':
            stop = line.find('>', position + 1)
            if stop < 0:
                continue
            destination = line[position + 1:stop]
            position = stop + 1
        else:
            while position < len(line):
                char = line[position]
                if char == chr(92) and position + 1 < len(line):
                    position += 2
                    continue
                if char == '(':
                    depth += 1
                elif char == ')':
                    if depth == 0:
                        break
                    depth -= 1
                elif char.isspace():
                    break
                position += 1
            if depth:
                continue
            destination = line[first:position]
        closing = re.match(r'(?:\s+["\'][^\n]*?["\'])?\s*\)', line[position:])
        if closing:
            yield start, position + closing.end(), destination


def section_numbers(expression: str, existing: set[str]) -> list[str]:
    """n-m은 하위 절, ~·–는 같은 깊이의 범위로 해석한다."""
    chunks = re.split(r'\s*(?:[·,/]|및|와)\s*', expression)
    numbers = []
    for chunk in chunks:
        bounds = re.split(r'\s*[~–]\s*', chunk)
        if len(bounds) == 1:
            numbers.append(bounds[0])
            continue
        start, end = bounds
        left, right = start.split('-'), end.split('-')
        if len(left) != len(right) or left[:-1] != right[:-1]:
            numbers.extend((start, end))
            continue
        first, last = int(left[-1]), int(right[-1])
        if last < first or last - first > 1000:
            numbers.extend((start, end))
            continue
        numbers.extend('-'.join([*left[:-1], str(n)]) for n in range(first, last + 1))
    return list(dict.fromkeys(numbers))


class Document:
    def __init__(self, text: str):
        self.text = text
        self.lines = visible_lines(text)
        self.reference_lines = mask_inline_code('\n'.join(self.lines), keep_file_names=True).splitlines()
        self.sections: set[str] = set()
        self.anchors: set[str] = set()
        self.context: dict[int, str] = {}
        self.section_contents: dict[str, str] = {}
        headings: list[tuple[int, int, str | None]] = []
        context = ''
        heading_anchors: set[str] = set()
        anchor_lines = mask_inline_code('\n'.join(self.lines)).split('\n')
        for i, line in enumerate(self.lines, 1):
            anchor_line = anchor_lines[i - 1]
            for anchor in re.findall(r'<a\s+[^>]*(?:id|name)=["\']([^"\']+)["\']', anchor_line):
                self.anchors.add(anchor)
            heading = HEADING.match(line)
            if heading:
                title = heading[2]
                key = slug(title)
                candidate, count = key, 0
                while candidate in heading_anchors:
                    count += 1
                    candidate = f'{key}-{count}'
                self.anchors.add(candidate)
                heading_anchors.add(candidate)
                context = candidate
                section = SECTION.match(title)
                number = section[1] if section else None
                if number:
                    self.sections.add(number)
                headings.append((i, len(heading[1]), number))
            self.context[i] = context
        for index, (start, level, number) in enumerate(headings):
            if not number:
                continue
            stop = next((line for line, following_level, _ in headings[index + 1:]
                         if following_level <= level), len(self.lines) + 1)
            # 본문 코드·예시도 계약 절의 내용이다. 주석/코드 제외는 참조 검색에만 쓴다.
            content = '\n'.join(text.splitlines()[start - 1:stop - 1])
            self.section_contents[number] = self.section_contents.get(number, '') + '\n' + content


@dataclass(frozen=True)
class Problem:
    source: str
    line: int
    kind: str
    target: str
    value: str
    context: str
    statement: str

    @property
    def key(self) -> tuple[str, ...]:
        # 줄 번호 자체는 식별자에 넣지 않고 기준본의 실제 오류 줄 대응도 함께 본다.
        return (self.source, self.kind, self.target, self.value, self.context, self.statement)

    def describe(self) -> str:
        return f'{self.source}:{self.line}: {self.kind}: {self.target} {self.value}'


def file_target(snapshot: Snapshot, source: str, name: str) -> str | None:
    if '/' in name and name in snapshot.files:
        return name
    target = snapshot.resolve(source, name)
    if target:
        return target
    # 기능 문서에서 `architecture.md`처럼 공통 문서명을 짧게 부르는 관례.
    if '/' not in name:
        if name == 'README.md' and source.startswith('docs/ai/features/'):
            feature = posixpath.basename(source).removesuffix('.md')
            research = f'ai/research/{feature}/README.md'
            if research in snapshot.files:
                return research
        if name in {'architecture.md', 'ai-service.md'}:
            target = snapshot.resolve('docs/ai/README.md', name)
            if target:
                return target
        matches = [path for path in snapshot.files if path.startswith('docs/ai/')
                   and posixpath.basename(path) == name]
        if len(matches) == 1:
            return matches[0]
    return None


def problems(snapshot: Snapshot) -> list[Problem]:
    output: list[Problem] = []
    for source in sorted(snapshot.files):
        if not checked_document(source):
            continue
        document = snapshot.document(source)
        for line_number, line in enumerate(document.reference_lines, 1):
            def add(kind: str, target: str, value: str) -> None:
                output.append(Problem(source, line_number, kind, target, value,
                                      document.context[line_number], line.strip()))
            occupied: list[tuple[int, int]] = []
            explicit: list[tuple[int, int]] = []
            links = list(inline_links(line))
            definition = LINK_DEFINITION.match(line)
            if definition:
                links.append((definition.start(), definition.end(), definition[1].strip('<>')))
            for start, end, raw in links:
                raw = re.sub(r'\\([() ])', r'\1', raw)
                url = urlsplit(raw)
                occupied.append((start, end))
                if url.scheme or url.netloc or raw.startswith('/'):
                    continue
                target = snapshot.resolve(source, url.path) if url.path else source
                if target is None:
                    add('link', raw, '파일 없음')
                elif url.fragment and target.endswith('.md'):
                    anchor = unquote(url.fragment)
                    if anchor not in snapshot.document(target).anchors:
                        add('anchor', target, '#' + anchor)
                expression = AFTER_FILE.match(line[end:])
                if target and target.endswith('.md') and expression:
                    explicit.append((end, end + expression.end()))
                    existing = snapshot.document(target).sections
                    for number in section_numbers(expression[1], existing):
                        if number not in existing:
                            add('section', target, number + '절')
            # URL 속 숫자/파일명은 별도의 문서 절 참조로 세지 않는다.
            references_line = ''.join(' ' if any(a <= i < b for a, b in occupied) else char
                                      for i, char in enumerate(line))
            files = list(FILE_REFERENCE.finditer(references_line))
            for filename in files:
                expression = AFTER_FILE.match(references_line[filename.end():])
                if not expression:
                    continue
                target = file_target(snapshot, source, filename['file'])
                explicit.append((filename.end(), filename.end() + expression.end()))
                if target is None:
                    add('section-file', filename['file'], '파일 없음')
                    continue
                existing = snapshot.document(target).sections
                for number in section_numbers(expression[1], existing):
                    if number not in existing:
                        add('section', target, number + '절')
            for reference in SECTION_REFERENCE.finditer(references_line):
                if any(a <= reference.start() < b for a, b in explicit):
                    continue
                targets = [source]
                if re.search(r'(?:기능 문서|각 기능)\s*$',
                             references_line[:reference.start()]):
                    targets = [path for path in snapshot.files
                               if path.startswith('docs/ai/features/') and path.endswith('.md')]
                for target in targets:
                    existing = snapshot.document(target).sections
                    for number in section_numbers(reference[1], existing):
                        if number not in existing:
                            add('section', target, number + '절')
    return output


def matching_lines(old: Document | None, new: Document) -> dict[int, int]:
    before = old.text.splitlines() if old else []
    after = new.text.splitlines()
    output = {}
    for tag, old_first, _, first, last in difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes():
        if tag == 'equal':
            output.update((line + 1, old_first + line - first + 1) for line in range(first, last))
    return output


def increased_problems(base: Snapshot, merged: Snapshot,
                       before: list[Problem], after: list[Problem]) -> list[Problem]:
    remaining = Counter((problem.line, problem.key) for problem in before)
    matches: dict[str, dict[int, int]] = {}
    baseline_lines: dict[str, set[int]] = {}
    for problem in before:
        baseline_lines.setdefault(problem.source, set()).add(problem.line)
    output = []
    for problem in after:
        path = problem.source
        if path not in matches:
            old = base.document(path) if path in base.files else None
            matches[path] = matching_lines(old, merged.document(path))
        previous = (matches[path].get(problem.line), problem.key)
        ambiguous = False
        if previous[0] is not None and path in base.files:
            old, new = base.document(path), merged.document(path)
            raw_before, raw_after = old.text.splitlines(), new.text.splitlines()
            statement = raw_before[previous[0] - 1]
            occurrences = [i + 1 for i, line in enumerate(raw_before) if line == statement]
            if old.text != new.text and len(occurrences) > 1 and any(
                    (line, problem.key) not in remaining for line in occurrences):
                anchored = all(
                    1 <= adjacent <= len(raw_after)
                    and matches[path].get(adjacent) == previous[0] + adjacent - problem.line
                    and matches[path].get(adjacent) not in baseline_lines.get(path, set())
                    and old.lines[matches[path][adjacent] - 1].strip()
                    and new.lines[adjacent - 1].strip()
                    and raw_before.count(raw_before[matches[path][adjacent] - 1]) == 1
                    and raw_after.count(raw_after[adjacent - 1]) == 1
                    for adjacent in (problem.line - 1, problem.line + 1))
                ambiguous = not anchored
        if remaining[previous] == 0 or ambiguous:
            output.append(problem)
        else:
            remaining[previous] -= 1
    return output


def contract_changes(before: Snapshot, after: Snapshot) -> list[str]:
    output = []
    arch = 'docs/ai/architecture.md'
    if before.files.get(arch) != after.files.get(arch):
        output.append(arch)
    paths = set(before.files) | set(after.files)
    for path in sorted(paths):
        if not (path.startswith('docs/ai/features/') and path.endswith('.md')):
            continue
        if before.files.get(path) == after.files.get(path):
            continue
        old = before.document(path).section_contents if path in before.files else {}
        new = after.document(path).section_contents if path in after.files else {}
        for section in ('5', '12'):
            keys = set(old) | set(new)
            if any(old.get(key) != new.get(key) for key in keys
                   if key == section or key.startswith(section + '-')):
                output.append(f'{path} {section}절')
    return output


def has_issue_reference(body: str) -> bool:
    """독립적인 Issue 관계 줄만 인정한다. 일반 문장·코드·주석·인용 제외."""
    pattern = re.compile(
        r'^\s*(?:[-*+]\s+)?(?:Part of|Closes|Fixes|관련 Issue)\s*:?\s*'
        r'(?:#[1-9]\d*|https://github\.com/[\w.-]+/[\w.-]+/issues/[1-9]\d*)'
        r'(?:\s*(?:[,·]|및)\s*#[1-9]\d*)*\s*[.]?\s*$', re.I)
    visible = mask_inline_code('\n'.join(visible_lines(body)))
    return any(pattern.fullmatch(line) for line in visible.splitlines())


def run(repo: Path, base_ref: str, head_ref: str, merge_ref: str | None,
        body: str) -> tuple[list[Problem], list[Problem], list[str], bool]:
    base_sha, head_sha = commit(repo, base_ref), commit(repo, head_ref)
    bases = git(repo, 'merge-base', '--all', base_sha, head_sha).decode().splitlines()
    if len(bases) != 1:
        raise CheckError('merge-base가 하나가 아니므로 비교 기준을 확정할 수 없습니다')
    merge_base = bases[0]
    if merge_ref:
        merged_sha = commit(repo, merge_ref)
        if not ancestor(repo, base_sha, merged_sha) or not ancestor(repo, head_sha, merged_sha):
            raise CheckError('--merge-head는 base와 head를 모두 포함하는 커밋이어야 합니다')
        if merged_sha != head_sha:
            parents = git(repo, 'rev-list', '--parents', '-n', '1', merged_sha).decode().split()[1:]
            if len(parents) != 2 or set(parents) != {base_sha, head_sha}:
                raise CheckError('--merge-head의 직접 부모는 현재 base와 head여야 합니다')
    elif ancestor(repo, base_sha, head_sha):
        merged_sha = head_sha
    else:
        raise CheckError('base가 head의 조상이 아닙니다. 실제 병합 커밋을 --merge-head로 지정하세요')
    baseline = Snapshot(repo, base_sha)
    merged = Snapshot(repo, merged_sha)
    before, after = problems(baseline), problems(merged)
    introduced = increased_problems(baseline, merged, before, after)
    changes = contract_changes(Snapshot(repo, merge_base), Snapshot(repo, head_sha))
    return before, introduced, changes, bool(changes) and not has_issue_reference(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--base', required=True, help='현재 PR base 커밋 또는 ref')
    parser.add_argument('--head', default='HEAD', help='현재 PR head 커밋 또는 ref')
    parser.add_argument('--merge-head', help='base와 head를 포함하는 실제 병합 커밋')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--pr-body-file', type=Path)
    source.add_argument('--pr-body-env', help='PR 본문이 든 환경변수 이름')
    args = parser.parse_args(argv)
    try:
        if args.pr_body_file:
            body = args.pr_body_file.read_text(encoding='utf-8-sig')
        else:
            if args.pr_body_env not in os.environ:
                raise CheckError(f'PR 본문 환경변수가 없습니다: {args.pr_body_env}')
            body = os.environ[args.pr_body_env]
        before, introduced, changes, missing = run(
            args.repo, args.base, args.head, args.merge_head, body)
        for problem in before:
            print('기존 오류: ' + problem.describe())
        for problem in introduced:
            print('신규 오류: ' + problem.describe())
        for change in changes:
            print('계약 변경: ' + change)
        if missing:
            print('PR 본문에 Part of #N / Closes #N / Fixes #N / 관련 Issue: #N 독립 줄이 필요합니다')
        print(f'기존 오류 {len(before)}개, 신규 오류 {len(introduced)}개, 계약 변경 {len(changes)}개')
        print('합의 댓글·승인 리뷰·문서와 구현의 일치는 별도로 확인하세요.')
        return 1 if introduced or missing else 0
    except (CheckError, OSError, ValueError) as exc:
        print('검사 실행 오류: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
