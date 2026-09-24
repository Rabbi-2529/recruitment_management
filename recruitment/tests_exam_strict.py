import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import ExamForm
from .models import Candidate, Choice, Circular, Department, Exam, ExamAttempt, Question
from .views_public import close_expired_attempts


class StrictExamTests(TestCase):
    """Anti-cheating: autosave, tab switch / other window / screenshot hand the sheet in, time over."""

    def setUp(self):
        self.dept = Department.objects.create(name="Software Engineering")
        self.circular = Circular.objects.create(title="Developer", reference_no="IGL-S-1")
        self.circular.departments.add(self.dept)
        Candidate.objects.create(circular=self.circular, department=self.dept, name="Mim", phone="01711000001")
        self.exam = Exam.objects.create(
            circular=self.circular, title="Strict Test", duration_minutes=10, strict_mode=True, shuffle_questions=False,
        )
        self.mcq = Question.objects.create(exam=self.exam, order=1, text="2 + 2 = ?", kind="mcq", marks=2)
        Choice.objects.create(question=self.mcq, order=1, text="3")
        self.right = Choice.objects.create(question=self.mcq, order=2, text="4", is_correct=True)
        self.written = Question.objects.create(exam=self.exam, order=2, text="Why us?", kind="text", marks=3)
        t = self.exam.token
        self.start_url = reverse("public:exam_start", args=[t])
        self.questions_url = reverse("public:exam_questions", args=[t])
        self.save_url = reverse("public:exam_autosave", args=[t])
        self.leave_url = reverse("public:exam_leave", args=[t])
        self.done_url = reverse("public:exam_done", args=[t])
        self.client.post(self.start_url, {"identity": "01711000001"})
        self.attempt = ExamAttempt.objects.get()

    def answers(self, text="Great team"):
        return {f"q{self.mcq.pk}": str(self.right.pk), f"q{self.written.pk}": text}

    def test_strict_page_has_rules_and_submit_button(self):
        page = self.client.get(self.questions_url)
        self.assertContains(page, "Exam Rules")
        self.assertContains(page, 'id="submit-exam"')
        self.assertContains(page, 'data-strict="1"')
        self.assertContains(page, "submits your exam immediately")

    def test_autosave_keeps_answers_after_refresh(self):
        resp = self.client.post(self.save_url, self.answers("Half written"))
        self.assertEqual(resp.json()["closed"], False)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.draft[str(self.written.pk)], "Half written")
        page = self.client.get(self.questions_url)  # refresh
        self.assertContains(page, "Half written")
        self.assertContains(page, f'value="{self.right.pk}" class="choice-radio" id="id_q{self.mcq.pk}_')

    def test_candidate_can_submit_before_time_is_over(self):
        resp = self.client.post(self.questions_url, self.answers())
        self.assertRedirects(resp, self.done_url)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.submit_reason, "submitted")
        self.assertEqual(str(self.attempt.auto_score), "2.00")

    def test_tab_switch_submits_with_the_answers_so_far(self):
        self.client.post(self.save_url, self.answers("Draft answer"))
        resp = self.client.post(self.leave_url, {**self.answers("Latest answer"), "reason": "tab_switch"})
        self.assertTrue(resp.json()["closed"])
        self.attempt.refresh_from_db()
        self.assertTrue(self.attempt.is_submitted)
        self.assertEqual(self.attempt.submit_reason, "tab_switch")
        self.assertTrue(self.attempt.was_forced)
        self.assertEqual(self.attempt.answers.get(question=self.written).text_answer, "Latest answer")
        self.assertEqual(str(self.attempt.auto_score), "2.00")
        self.assertIn("tab_switch", [e["event"] for e in self.attempt.events])
        done = self.client.get(self.done_url)
        self.assertContains(done, "submitted automatically: left the exam page")
        # the sheet cannot be opened again
        self.assertRedirects(self.client.get(self.questions_url), self.done_url)

    def test_screenshot_and_other_window_reasons(self):
        self.client.post(self.leave_url, {"reason": "screenshot"})
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.get_submit_reason_display(), "Screenshot key pressed")

    def test_leaving_is_only_noted_when_not_strict(self):
        self.exam.strict_mode = False
        self.exam.save()
        resp = self.client.post(self.leave_url, {**self.answers(), "reason": "window_blur"})
        self.assertFalse(resp.json()["closed"])
        self.attempt.refresh_from_db()
        self.assertFalse(self.attempt.is_submitted)
        self.assertEqual(self.attempt.events[-1]["event"], "window_blur")

    def test_time_over_submits_saved_answers_even_if_browser_was_closed(self):
        self.client.post(self.save_url, self.answers("Saved before closing"))
        ExamAttempt.objects.filter(pk=self.attempt.pk).update(started_at=timezone.now() - datetime.timedelta(minutes=11))
        close_expired_attempts(self.exam)  # runs when HR opens the answer sheets
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.submit_reason, "time_up")
        self.assertEqual(self.attempt.answers.get(question=self.written).text_answer, "Saved before closing")
        self.assertEqual(str(self.attempt.auto_score), "2.00")

    def test_timer_hand_in_at_zero_is_accepted_without_required_answers(self):
        ExamAttempt.objects.filter(pk=self.attempt.pk).update(
            started_at=timezone.now() - datetime.timedelta(minutes=10, seconds=-3)  # 3 seconds left
        )
        resp = self.client.post(self.questions_url, {})
        self.assertRedirects(resp, self.done_url)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.submit_reason, "time_up")

    def test_autosave_after_time_is_over_closes_the_sheet(self):
        ExamAttempt.objects.filter(pk=self.attempt.pk).update(started_at=timezone.now() - datetime.timedelta(minutes=11))
        resp = self.client.post(self.save_url, self.answers())
        self.assertTrue(resp.json()["closed"])
        self.attempt.refresh_from_db()
        self.assertTrue(self.attempt.is_submitted)

    def test_strict_mode_needs_a_time_limit(self):
        form = ExamForm(data={"title": "X", "circular": self.circular.pk, "strict_mode": "on", "is_open": "on"})
        self.assertFalse(form.is_valid())
        self.assertIn("duration_minutes", form.errors)

    def test_hr_sees_why_the_sheet_was_submitted(self):
        self.client.post(self.leave_url, {"reason": "window_blur"})
        root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.client.force_login(root)
        listing = self.client.get(reverse("panel:exam_responses", args=[self.exam.pk]))
        self.assertContains(listing, "Opened another window")
        sheet = self.client.get(reverse("panel:exam_response", args=[self.attempt.pk]))
        self.assertContains(sheet, "Exam log")
        self.assertContains(sheet, "Opened another window or app")
