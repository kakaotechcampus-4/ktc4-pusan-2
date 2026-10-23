"""문서 검사기의 오탐·누락·Git 비교 경계를 작은 임시 저장소로 검증한다."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import check


ARCH = '# 0. 목적\n\n# 1. 구성\n\n## 1-1. 저장\n\n# 2. 통신\n'
FEATURE = '# 0. 목적\n\n# 5. 계약\n\n## 5-1. 입력\n입력 설명\n\n# 12. 저장\n\n## 12-1. DB\n저장 설명\n\n# 15. 한계\n'


class IssueBodyTests(unittest.TestCase):
    def test_recognized_relationships(self):
        for body in ['Part of #225', 'Closes #1', '- Fixes #3', '관련 Issue: #19',
                     'Part of https://github.com/team/repo/issues/1', 'Fixes #1, #2',
                     '### 내용\n\n관련 Issue #7\n', 'closes #2.']:
            with self.subTest(body=body):
                self.assertTrue(check.has_issue_reference(body))

    def test_examples_and_casual_mentions_do_not_count(self):
        for body in ['참고로 #225를 확인하세요', 'PR #225', 'Part of #0',
                     '> Part of #225', '````md\nPart of #225\n````',
                     '<!--\nPart of #225\n-->', '    Fixes #1',
                     '`Part of #225`', 'Part of #225 작업 참고',
                     'Part of https://github.com/team/repo/pull/225']:
            with self.subTest(body=body):
                self.assertFalse(check.has_issue_reference(body))

    def test_lazy_continuation_in_quote_is_not_issue_relationship(self):
        self.assertFalse(check.has_issue_reference('> 관계 줄 예시\nPart of #225\n'))
        self.assertTrue(check.has_issue_reference('> 관계 줄 예시\n\nPart of #225\n'))

    def test_issue_line_in_list_code_and_multiline_code_is_not_relationship(self):
        self.assertFalse(check.has_issue_reference('- ```md\n  Part of #225\n  ```\n'))
        self.assertFalse(check.has_issue_reference('`예시\nPart of #225\n끝`\n'))

    def test_four_space_fence_does_not_close_top_level_code(self):
        self.assertFalse(check.has_issue_reference('```\n    ```\nPart of #225\n```\n'))

    def test_thematic_break_ends_quote_before_relationship(self):
        self.assertTrue(check.has_issue_reference('> 예시\n---\nPart of #225\n'))


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.write('docs/ai/architecture.md', ARCH)
        self.write('docs/ai/features/sample.md', FEATURE)
        self.base = self.save('base')

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        return result.stdout.decode().strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode('utf-8'))

    def save(self, message='change'):
        self.git('add', '.')
        self.git('commit', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def scan(self):
        return check.problems(check.Snapshot(self.repo, self.save()))

    def compare(self, body='Part of #225', base=None, head='HEAD', merge=None):
        return check.run(self.repo, base or self.base, head, merge, body)

    def assert_contract_change(self, before, after):
        self.write('docs/ai/features/sample.md', before)
        base = self.save()
        self.write('docs/ai/features/sample.md', after)
        self.save()
        self.assertTrue(self.compare(body='', base=base)[3])

    def test_valid_relative_links_sections_and_anchors(self):
        self.write('docs/ai/README.md',
            '[구성](architecture.md#1-구성)\n`architecture.md` 1-1 · 2절\n'
            '[기능](features/sample.md#5-1-입력)\n')
        self.assertEqual(self.scan(), [])

    def test_reference_failure_and_external_cases(self):
        cases = [
            ('[없음](features/missing.md)\n', ['link']),
            ('[틀림](architecture.md#없는-제목)\n', ['anchor']),
            ('![그림](missing.png)\n', ['link']),
            ('[밖](../../../private.md)\n', ['link']),
            ('[공식](https://example.invalid/not-found#1)\n', []),
            ('# 1. 안내\n\n99절 확인\n', ['section']),
            ('`architecture.md` 9절\n', ['section']),
            ('`architecture.md` 99\n', ['section']),
        ]
        for content, expected in cases:
            with self.subTest(content=content):
                self.write('docs/ai/README.md', content)
                self.assertEqual([p.kind for p in self.scan()], expected)

    def test_percent_encoded_korean_anchor(self):
        self.write('docs/ai/README.md', '[구성](architecture.md#1-%EA%B5%AC%EC%84%B1)\n')
        self.assertEqual(self.scan(), [])

    def test_directory_link_uses_readme(self):
        self.write('ai/tools/docs-check/README.md', '# 검사\n')
        self.write('docs/ai/README.md', '[검사](../../ai/tools/docs-check/)\n')
        self.assertEqual(self.scan(), [])

    def test_fences_comments_quotes_are_not_references(self):
        self.write('docs/ai/README.md',
            '````md\n[없음](missing.md)\n```\n99절\n````\n'
            '<!-- [없음](missing.md) 99절 -->\n> 99절\n    99절\n')
        self.assertEqual(self.scan(), [])

    def test_feature_can_use_short_common_filename(self):
        self.write('docs/ai/features/sample.md', FEATURE + '\n`architecture.md` 1-1절\n')
        self.assertEqual(self.scan(), [])

    def test_deleted_target_breaks_unchanged_inbound_link(self):
        self.write('docs/ai/README.md', '[저장](architecture.md#1-1-저장)\n')
        base = self.save()
        self.write('docs/ai/architecture.md', ARCH.replace('## 1-1. 저장', '## 1-2. 저장'))
        self.save()
        _, introduced, _, missing = self.compare(base=base)
        self.assertEqual(introduced[0].source, 'docs/ai/README.md')
        self.assertFalse(missing)

    def test_unchanged_baseline_errors_are_reported_but_not_blocking(self):
        self.write('docs/ai/README.md', '[없음](missing.md)\n')
        base = self.save()
        self.write('other.txt', 'unrelated')
        self.save()
        before, introduced, _, missing = self.compare(base=base, body='')
        self.assertEqual(len(before), 1)
        self.assertEqual(introduced, [])
        self.assertFalse(missing)

    def test_new_occurrence_of_same_error_is_blocking(self):
        self.write('docs/ai/README.md', '[없음](missing.md)\n')
        base = self.save()
        self.write('docs/ai/README.md', '[없음](missing.md)\n[없음](missing.md)\n')
        self.save()
        self.assertEqual(len(self.compare(base=base)[1]), 1)

    def test_architecture_change_requires_relationship_line(self):
        self.write('docs/ai/architecture.md', ARCH + '\n통신 설명 추가\n')
        self.save()
        self.assertTrue(self.compare(body='참고 #225')[3])
        self.assertFalse(self.compare(body='Part of #225')[3])

    def test_feature_contract_child_change_requires_issue(self):
        self.write('docs/ai/features/sample.md', FEATURE.replace('입력 설명', '다른 입력'))
        self.save()
        self.assertTrue(self.compare(body='')[3])

    def test_feature_storage_child_change_requires_issue(self):
        self.write('docs/ai/features/sample.md', FEATURE.replace('저장 설명', '새 저장'))
        self.save()
        self.assertTrue(self.compare(body='')[3])

    def test_non_contract_change_does_not_require_issue(self):
        self.write('docs/ai/features/sample.md', FEATURE + '\n알려진 한계\n')
        self.save()
        self.assertFalse(self.compare(body='')[3])

    def test_base_advance_requires_explicit_merge_snapshot(self):
        self.git('checkout', '-b', 'feature')
        self.write('other.txt', 'head')
        head = self.save()
        self.git('checkout', 'main')
        self.write('base.txt', 'base advancement')
        base = self.save()
        with self.assertRaises(check.CheckError):
            self.compare(base=base, head=head)

    def test_real_merged_tree_is_used_for_reference_integrity(self):
        self.git('checkout', '-b', 'feature')
        self.write('docs/ai/README.md', '[새 제목](architecture.md#3-새제목)\n')
        head = self.save()
        self.git('checkout', 'main')
        self.write('docs/ai/architecture.md', ARCH + '\n# 3. 새제목\n')
        base = self.save()
        self.git('merge', '--no-edit', '--no-ff', 'feature')
        merged = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.compare(body='', base=base, head=head, merge=merged)[1], [])

    def test_checker_does_not_change_checkout_or_index(self):
        self.write('local-untracked.txt', 'local')
        before = (self.git('rev-parse', 'HEAD'), self.git('status', '--porcelain'),
                  (self.repo / '.git/index').read_bytes())
        self.compare()
        after = (self.git('rev-parse', 'HEAD'), self.git('status', '--porcelain'),
                 (self.repo / '.git/index').read_bytes())
        self.assertEqual(before, after)

    def test_cli_reads_body_from_environment(self):
        self.write('docs/ai/architecture.md', ARCH + '\n새 계약\n')
        head = self.save()
        env = dict(os.environ, TEST_DOCS_PR_BODY='Part of #225')
        result = subprocess.run([os.sys.executable, str(Path(check.__file__)), '--repo', str(self.repo),
            '--base', self.base, '--head', head, '--pr-body-env', 'TEST_DOCS_PR_BODY'],
            env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_missing_body_environment_is_execution_error(self):
        env = dict(os.environ)
        env.pop('TEST_DOCS_MISSING_BODY', None)
        result = subprocess.run([os.sys.executable, str(Path(check.__file__)), '--repo', str(self.repo),
            '--base', self.base, '--pr-body-env', 'TEST_DOCS_MISSING_BODY'],
            env=env, capture_output=True)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
