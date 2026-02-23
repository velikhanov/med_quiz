from django.test import TestCase
from apps.content.models import PDFUpload, Category, Test, Question
from apps.content.parsers import QuestionParser
from django.core.files.uploadedfile import SimpleUploadedFile


class QuestionParserTests(TestCase):
    def setUp(self):
        self.test_obj = Test.objects.create(name="DAHİLİYE")
        self.category = Category.objects.create(test=self.test_obj, name="HEMATOLOJİ")

        # Create a dummy PDF file
        file_content = b"%PDF-1.4..."
        uploaded_file = SimpleUploadedFile("test.pdf", file_content, content_type="application/pdf")

        self.pdf = PDFUpload.objects.create(
            category=self.category,
            file=uploaded_file,
            title="Hematoloji PDF",
            total_pages=10
        )

    def test_handle_fragment_updates_db_question(self):
        """
        Test that handle_fragment updates text, options, and correct_option
        of an existing database question when no buffer exists.
        """
        # 1. Create an initial question in the DB (simulating a "complete enough" question from Page N)
        initial_q = Question.objects.create(
            category=self.category,
            subcategory="Anemiler",
            question_number=1,
            text="Initial question text",
            options=["A) Option 1", "B) Option 2"],
            correct_option="?",  # Initially unknown
            explanation="Initial explanation",
            page_number=1
        )

        # 2. Instantiate Parser (no buffer)
        parser = QuestionParser(self.pdf, buffer=None, current_subcat_state="Anemiler")

        # 3. Simulate a fragment from Page N+1
        # This fragment contains the rest of the question text, more options, and the correct answer.
        fragment_item = {
            "type": "fragment",
            "question": "continued text",
            "explanation": "continued explanation",
            "correct_option": "C",  # Now we know the answer
            "is_continuation": True
        }
        fragment_options = ["C) Option 3", "D) Option 4"]

        # 4. Process the fragment
        parser.handle_fragment(fragment_item, fragment_options, "Anemiler", page_num=2)

        # 5. Assertions
        # It should NOT create a new question
        self.assertEqual(len(parser.questions_to_create), 0)

        # It should have added the existing question to the update map
        self.assertIn(initial_q.id, parser.questions_to_update_map)

        updated_q = parser.questions_to_update_map[initial_q.id]

        # Check Text Update
        self.assertEqual(updated_q.text, "Initial question text continued text")

        # Check Options Update
        expected_options = ["A) Option 1", "B) Option 2", "C) Option 3", "D) Option 4"]
        self.assertEqual(updated_q.options, expected_options)

        # Check Correct Option Update (The critical fix)
        self.assertEqual(updated_q.correct_option, "C")

        # Check Explanation Update
        self.assertEqual(updated_q.explanation, "Initial explanation\ncontinued explanation")

    def test_handle_fragment_does_not_overwrite_correct_option_with_unknown(self):
        """
        Test that handle_fragment ignores correct_option if it's missing or invalid in the fragment.
        """
        initial_q = Question.objects.create(
            category=self.category,
            subcategory="Anemiler",
            question_number=2,
            text="Q2",
            options=["A) 1", "B) 2"],
            correct_option="A",  # We already know it
            explanation="Expl",
            page_number=1
        )

        parser = QuestionParser(self.pdf, buffer=None, current_subcat_state="Anemiler")

        fragment_item = {
            "type": "fragment",
            "question": "more text",
            "correct_option": "?",  # Useless info
        }

        parser.handle_fragment(fragment_item, [], "Anemiler", page_num=2)

        updated_q = parser.questions_to_update_map[initial_q.id]

        # Should remain "A"
        self.assertEqual(updated_q.correct_option, "A")
