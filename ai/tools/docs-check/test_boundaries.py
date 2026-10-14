"""코드 블록 경계와 중복된 기존 오류의 문맥을 회귀 검증한다."""
import unittest

import check


class BoundaryTests(unittest.TestCase):
    def test_backtick_inside_tilde_fence_does_not_open_external_code_span(self):
        document = check.Document('# 5. 계약\n~~~\n`\n~~~\n<!--\n# 15. 예시\n-->\n'
                                  '`\n입력 A\n# 12. 저장\n저장 A\n')
        self.assertEqual(document.sections, {'5', '12'})
        self.assertIn('입력 A', document.section_contents['5'])

    def test_dedent_ends_list_fence_before_contract_heading(self):
        document = check.Document('- ```\n  예시\n# 5. 계약\n입력 A\n')
        self.assertEqual(document.sections, {'5'})
        self.assertIn('입력 A', document.section_contents['5'])

    def compare_errors(self, before, after, old_lines, new_lines):
        path = 'docs/ai/README.md'
        class MemorySnapshot:
            def __init__(self, text):
                self.files = {path: 'blob'}
                self.doc = check.Document(text)

            def document(self, name):
                return self.doc

        def errors(lines):
            return [check.Problem(path, line, 'link', 'missing.md', '', 'guide',
                                  '[bad](missing.md)') for line in lines]
        return check.increased_problems(MemorySnapshot(before), MemorySnapshot(after),
                                        errors(old_lines), errors(new_lines))

    def test_one_preserved_side_cannot_offset_activated_example_error(self):
        before = '# Guide\nA\n[bad](missing.md)\nB\n```\n[bad](missing.md)\nC\n```\n'
        after = '# Guide\nA\n[bad](missing.md)\nC\n'
        self.assertEqual(len(self.compare_errors(before, after, [3], [3])), 1)

    def test_appended_text_preserves_duplicate_existing_errors(self):
        before = '# Guide\n\n[bad](missing.md)\n\n[bad](missing.md)\n\n'
        self.assertEqual(self.compare_errors(before, before + 'extra\n', [3, 5], [3, 5]), [])
