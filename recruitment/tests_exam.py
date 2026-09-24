import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Candidate, Choice, Circular, Department, Exam, ExamAttempt, Question


class WrittenExamTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.dept = Department.objects.create(name="Software Engineering")
        self.other = Department.objects.create(name="Accounts")
        self.circular = Circular.objects.create(title="Junior Developer", reference_no="IGL-E-1")
        self.circular.departments.add(self.dept, self.other)
        self.candidate = Candidate.objects.create(
            circular=self.circular, department=self.dept, name="Arif Hasan",
            phone="01711000001", email="arif@example.com",
        )
        self.exam = Exam.objects.create(circular=self.circular, title="Written Test", shuffle_questions=False)
        self.mcq = Question.objects.create(exam=self.exam, order=1, text="2 + 2 = ?", kind="mcq", marks=2)
        Choice.objects.create(question=self.mcq, order=1, text="3")
        self.right = Choice.objects.create(question=self.mcq, order=2, text="4", is_correct=True)
        self.written = Question.objects.create(exam=self.exam, order=2, text="Describe yourself", kind="text", marks=3)
        self.start_url = reverse("public:exam_start", args=[self.exam.token])
        self.questions_url = reverse("public:exam_questions", args=[self.exam.token])
        self.done_url = reverse("public:exam_done", args=[self.exam.token])

    # ---------------------------------------------------------- candidate side
    def test_unregistered_phone_is_told_to_register(self):
        resp = self.client.post(self.start_url, {"identity": "01999999999"})
        self.assertContains(resp, "Please register first")
        self.assertEqual(self.exam.attempts.count(), 0)

    def test_registered_candidate_answers_and_is_auto_marked(self):
        self.assertRedirects(self.client.post(self.start_url, {"identity": "8801711000001"}), self.questions_url)
        page = self.client.get(self.questions_url)
        self.assertContains(page, "2 + 2 = ?")
        self.assertContains(page, "Describe yourself")

        resp = self.client.post(
            self.questions_url,
            {f"q{self.mcq.pk}": str(self.right.pk), f"q{self.written.pk}": "I am a developer."},
        )
        self.assertRedirects(resp, self.done_url)
        attempt = ExamAttempt.objects.get()
        self.assertTrue(attempt.is_submitted)
        self.assertEqual((str(attempt.auto_score), str(attempt.out_of)), ("2.00", "5.00"))
        self.assertTrue(attempt.needs_marking)

        # the email works too, and the sheet cannot be filled in twice
        self.assertRedirects(self.client.post(self.start_url, {"identity": "arif@example.com"}), self.done_url)
        self.assertRedirects(self.client.get(self.questions_url), self.done_url)

    def test_wrong_answer_scores_zero(self):
        wrong = self.mcq.choices.get(text="3")
        self.client.post(self.start_url, {"identity": "01711000001"})
        self.client.post(self.questions_url, {f"q{self.mcq.pk}": str(wrong.pk), f"q{self.written.pk}": "x"})
        self.assertEqual(str(ExamAttempt.objects.get().auto_score), "0.00")

    def test_required_question_must_be_answered(self):
        self.client.post(self.start_url, {"identity": "01711000001"})
        resp = self.client.post(self.questions_url, {})
        self.assertContains(resp, "Please answer this question.")
        self.assertIsNone(self.exam.attempts.get().submitted_at)

    def test_each_candidate_gets_a_random_set(self):
        for i in range(3, 9):
            Question.objects.create(exam=self.exam, order=i, text=f"Q{i}", kind="text", marks=1)
        self.exam.questions_per_candidate = 3
        self.exam.shuffle_questions = True
        self.exam.save()

        sets = []
        for i in range(6):
            person = Candidate.objects.create(
                circular=self.circular, department=self.dept, name=f"C{i}", phone=f"018110000{i:02d}"
            )
            attempt = self.exam.attempts.create(candidate=person)
            attempt.question_order = self.exam.draw_questions()
            attempt.save()
            self.assertEqual(len(attempt.question_order), 3)
            self.assertEqual(len(attempt.questions()), 3)
            sets.append(tuple(attempt.question_order))
        self.assertGreater(len(set(sets)), 1, "every candidate got the same set")

    def test_set_stays_the_same_on_refresh(self):
        for i in range(3, 9):
            Question.objects.create(exam=self.exam, order=i, text=f"Q{i}", kind="text", marks=1)
        self.exam.questions_per_candidate = 3
        self.exam.save()
        self.client.post(self.start_url, {"identity": "01711000001"})
        first = list(self.exam.attempts.get().question_order)
        self.client.get(self.questions_url)
        self.client.post(self.start_url, {"identity": "01711000001"})
        self.assertEqual(list(self.exam.attempts.get().question_order), first)

    def test_time_limit_closes_the_sheet(self):
        self.exam.duration_minutes = 10
        self.exam.save()
        self.client.post(self.start_url, {"identity": "01711000001"})
        attempt = self.exam.attempts.get()
        self.assertIsNotNone(attempt.deadline)

        # the candidate started 11 minutes ago and never pressed submit
        self.exam.attempts.filter(pk=attempt.pk).update(started_at=timezone.now() - datetime.timedelta(minutes=11))
        self.assertRedirects(self.client.get(self.questions_url), self.done_url)
        attempt.refresh_from_db()
        self.assertTrue(attempt.is_submitted)
        self.assertEqual(str(attempt.total_score), "0.00")

    def test_exam_for_another_department_and_closed_exam(self):
        self.exam.departments.add(self.other)  # only Accounts may sit this exam
        self.assertContains(self.client.post(self.start_url, {"identity": "01711000001"}), "Please register first")

        self.exam.departments.clear()
        self.exam.is_open = False
        self.exam.save()
        self.assertContains(self.client.get(self.start_url), "This exam is not open")

    # ---------------------------------------------------------- HR side
    def test_hr_creates_question_marks_answer_and_copies_written_mark(self):
        self.client.force_login(self.root)
        resp = self.client.post(
            reverse("panel:question_add", args=[self.exam.pk]),
            {"text": "Capital of Bangladesh?", "kind": "mcq", "marks": "1", "order": "3", "required": "on",
             "choice_1": "Dhaka", "choice_2": "Chittagong", "correct_choice": "1"},
        )
        self.assertEqual(resp.status_code, 302)
        question = Question.objects.get(text="Capital of Bangladesh?")
        self.assertEqual(question.correct_choice.text, "Dhaka")

        bad = self.client.post(
            reverse("panel:question_add", args=[self.exam.pk]),
            {"text": "Bad", "kind": "mcq", "marks": "1", "order": "4", "choice_1": "only one"},
        )
        self.assertContains(bad, "needs at least 2 options")

        self.client.logout()
        self.client.post(self.start_url, {"identity": "01711000001"})
        self.client.post(self.questions_url, {
            f"q{self.mcq.pk}": str(self.right.pk),
            f"q{self.written.pk}": "Hello",
            f"q{question.pk}": str(question.correct_choice.pk),
        })
        attempt = ExamAttempt.objects.get()
        answer = attempt.answers.get(question=self.written)

        self.client.force_login(self.root)
        self.client.post(reverse("panel:exam_response", args=[attempt.pk]), {f"mark_{answer.pk}": "1.5"})
        attempt.refresh_from_db()
        self.assertEqual(
            (str(attempt.manual_score), str(attempt.total_score), str(attempt.out_of)), ("1.50", "4.50", "6.00")
        )
        self.assertFalse(attempt.needs_marking)

        self.client.post(reverse("panel:exam_response", args=[attempt.pk]), {"apply_mark": "1"})
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.written_mark, 4)  # 75% of 5 = 3.75 -> whole mark 4

    def test_mark_above_question_total_is_refused(self):
        self.client.post(self.start_url, {"identity": "01711000001"})
        self.client.post(self.questions_url, {f"q{self.mcq.pk}": str(self.right.pk), f"q{self.written.pk}": "Hi"})
        attempt = ExamAttempt.objects.get()
        answer = attempt.answers.get(question=self.written)
        self.client.force_login(self.root)
        self.client.post(reverse("panel:exam_response", args=[attempt.pk]), {f"mark_{answer.pk}": "9"})
        answer.refresh_from_db()
        self.assertIsNone(answer.awarded)

    def test_exam_pages_need_login(self):
        for name, args in [("panel:exam_list", []), ("panel:exam_questions", [self.exam.pk])]:
            resp = self.client.get(reverse(name, args=args))
            self.assertIn(reverse("panel:login"), resp["Location"])
