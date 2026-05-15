from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from qa_service import classify_qa_subtype, build_teacher_answer_parts


class QaTeacherModeTests(unittest.TestCase):
    def test_classify_product_question(self) -> None:
        self.assertEqual(
            "product_knowledge",
            classify_qa_subtype("钻石4C应该怎么给顾客解释？"),
        )

    def test_classify_compliance_question(self) -> None:
        self.assertEqual(
            "compliance_boundary",
            classify_qa_subtype("这种情况能不能承诺保值回购？"),
        )

    def test_classify_sales_question(self) -> None:
        self.assertEqual(
            "sales_script",
            classify_qa_subtype("顾客说太贵了我应该怎么接？"),
        )

    def test_teacher_parts_split_existing_answer_text(self) -> None:
        parts = build_teacher_answer_parts(
            question="顾客说太贵了我应该怎么接？",
            answer_text="先认同顾客的预算顾虑，再解释材质工艺和佩戴价值。可以说这款贵在工艺稳定、售后完善，不建议一上来就主动降价。",
            knowledge_patch="这题你最该记住的是先认同顾虑，再拆价值。",
        )

        self.assertIn("先", parts["answer_brief"])
        self.assertTrue(parts["answer_reason"])
        self.assertTrue(parts["answer_example"])
        self.assertTrue(parts["coach_question"])

    def test_teacher_parts_generate_fallback_for_system_question(self) -> None:
        parts = build_teacher_answer_parts(
            question="系统里怎么查看培训完成率？",
            answer_text="",
            knowledge_patch="",
        )

        self.assertEqual("system_usage", parts["subtype"])
        self.assertTrue(parts["answer_brief"])
        self.assertTrue(parts["answer_reason"])
        self.assertTrue(parts["answer_example"])
 
    def test_teacher_parts_do_not_echo_knowledge_patch_as_followup(self) -> None:
        parts = build_teacher_answer_parts(
            question="椤惧璇村お璐典簡鎴戝簲璇ユ€庝箞鎺ワ紵",
            answer_text="",
            knowledge_patch="这题你最该记住的是：先给判断，再给依据，最后补一个门店里的说法。",
        )

        self.assertNotIn("如果你愿意，我可以继续把", parts["coach_question"])
        self.assertNotIn("先给判断，再给依据", parts["coach_question"])


if __name__ == "__main__":
    unittest.main()
