"""주석·코드 예시·기존 오류 상계의 반례를 검증한다."""
import unittest

import check


class RegressionTests(unittest.TestCase):
    def test_backtick_in_html_comment_does_not_hide_contract_after_code_span(self):
        document = check.Document('<!-- ` -->\n`example\nx <!--\nend`\n\n'
                                  '# 5. 계약\n입력 A\n# 12. 저장\n저장 A\n')
        self.assertEqual(document.sections, {'5', '12'})
        self.assertIn('입력 A', document.section_contents['5'])

    def test_html_anchor_inside_multiline_code_is_not_a_link_target(self):
        document = check.Document('`example\nx <a id="custom"></a>\nend`\n\n'
                                  '# intro\n# intro\n')
        self.assertEqual(document.anchors, {'intro', 'intro-1'})

    def test_repeated_adjacent_context_cannot_offset_activated_example_error(self):
        path = 'docs/ai/README.md'
        class MemorySnapshot:
            def __init__(self, text):
                self.files = {path: 'blob'}
                self.doc = check.Document(text)

            def document(self, name):
                return self.doc

        before = MemorySnapshot('# Guide\nA\n[bad](missing.md)\nB\n'
                                '```\n[bad](missing.md)\nB\n```\n')
        after = MemorySnapshot('# Guide\nA\nB\n[bad](missing.md)\nB\n')
        old_error = check.Problem(path, 3, 'link', 'missing.md', '', 'guide', '[bad](missing.md)')
        new_error = check.Problem(path, 4, 'link', 'missing.md', '', 'guide', '[bad](missing.md)')
        self.assertEqual(check.increased_problems(before, after, [old_error], [new_error]),
                         [new_error])


if __name__ == '__main__':
    unittest.main()
