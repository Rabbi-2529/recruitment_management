from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import Candidate, Circular, Department

SECRET = "Second position not approved yet - budget meeting on Monday"


class WaitingStatusTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.dept = Department.objects.create(name="Accounts")
        self.circular = Circular.objects.create(title="Officer", reference_no="IGL-W-1")
        self.circular.departments.add(self.dept)
        self.c = Candidate.objects.create(circular=self.circular, department=self.dept, name="Mim", phone="01811000001")
        self.client.force_login(self.root)

    def set_waiting(self, reason=SECRET):
        return self.client.post(
            reverse("panel:candidate_status", args=[self.c.pk]),
            {"status": "waiting", "waiting_reason": reason},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        ).json()

    def test_hr_sets_waiting_with_a_private_reason(self):
        data = self.set_waiting()
        self.assertTrue(data["ok"])
        self.c.refresh_from_db()
        self.assertEqual((self.c.status, self.c.waiting_reason), ("waiting", SECRET))
        # the reason is shown to HR in the candidate list
        row = self.client.get(reverse("panel:candidate_data"), {"draw": 1}).json()["data"][0]
        self.assertEqual(row["waiting_reason"], SECRET)

    def test_candidate_sees_waiting_but_never_the_reason(self):
        self.set_waiting()
        self.client.logout()
        page = self.client.post(reverse("public:landing"), {"phone": "01811000001", "department": self.dept.pk})
        self.assertContains(page, "You are on the waiting list")
        self.assertContains(page, "Waiting")
        self.assertNotContains(page, SECRET)
        self.assertNotContains(page, "budget meeting")

    def test_changing_status_without_reason_keeps_old_reason(self):
        self.set_waiting()
        self.client.post(reverse("panel:candidate_status", args=[self.c.pk]), {"status": "selected"},
                         HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.c.refresh_from_db()
        self.assertEqual((self.c.status, self.c.waiting_reason), ("selected", SECRET))

    def test_bulk_mark_as_waiting(self):
        self.client.post(reverse("panel:candidate_bulk"), {"action": "waiting", "ids": str(self.c.pk)})
        self.c.refresh_from_db()
        self.assertEqual(self.c.status, "waiting")

    def test_interview_sheet_saves_reason(self):
        self.client.post(reverse("panel:interview_detail", args=[self.c.pk]), {
            "status": "waiting", "waiting_reason": "Strong but junior", "note": "",
            "dress_up_mark": "", "body_language_mark": "", "viva_mark": "", "written_mark": "",
            "rating": "", "expected_salary": "", "available_date": "",
        })
        self.c.refresh_from_db()
        self.assertEqual((self.c.status, self.c.waiting_reason), ("waiting", "Strong but junior"))

    def test_upload_understands_waiting(self):
        csv_data = b"Name,Phone,Department,Status\nA,01711000001,Accounts,Waiting\nB,01711000002,Accounts,Waiting List\n"
        self.client.post(reverse("panel:candidate_upload"), {
            "circular": self.circular.pk, "default_status": "pending",
            "file": SimpleUploadedFile("w.csv", csv_data, content_type="text/csv"),
        })
        self.assertEqual(Candidate.objects.filter(status="waiting").count(), 2)

    def test_dashboard_counts_waiting(self):
        self.set_waiting()
        resp = self.client.get(reverse("panel:dashboard"))
        self.assertEqual(resp.context["stats"]["waiting"], 1)
