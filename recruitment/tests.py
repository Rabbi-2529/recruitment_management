import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Candidate, Circular, Department
from .utils import normalize_phone, validate_bd_phone

TEMP_MEDIA = tempfile.mkdtemp()


def cv_file(name="cv.pdf", size=1024):
    return SimpleUploadedFile(name, b"x" * size, content_type="application/pdf")


class PhoneTests(TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_phone("+880 1711-000001"), "01711000001")
        self.assertEqual(normalize_phone(1711000001), "01711000001")
        self.assertEqual(normalize_phone("01711000001"), "01711000001")


class BdPhoneValidationTests(TestCase):
    def test_valid_formats(self):
        self.assertEqual(validate_bd_phone("01958666961"), "01958666961")
        self.assertEqual(validate_bd_phone("8801958666961"), "01958666961")

    def test_invalid_formats(self):
        for bad in ["023", "02312345678", "1958666961", "0195866696", "019586669612",
                    "880195866696", "88019586669612", "+8801958666961", "01258666961", "abc"]:
            self.assertIsNone(validate_bd_phone(bad), bad)

    def test_landing_shows_invalid_message(self):
        dept = Department.objects.create(name="Accounts")
        resp = self.client.post(reverse("public:landing"), {"phone": "02312345678", "department": dept.pk})
        self.assertContains(resp, "Invalid phone number")


class PortalTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.circular = Circular.objects.create(title="Junior Developer", reference_no="IGL-2026-01")
        self.dept = Department.objects.create(name="Software Engineering")

    def test_panel_requires_login(self):
        resp = self.client.get(reverse("panel:dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("panel:login"), resp["Location"])

    def test_default_admin_not_installed(self):
        self.assertEqual(self.client.get("/admin/").status_code, 404)

    def test_login_and_upload_and_search(self):
        resp = self.client.post(reverse("panel:login"), {"email": "root@iglweb.com", "password": "StrongPass#2026"})
        self.assertRedirects(resp, reverse("panel:dashboard"))

        csv_data = (
            "Name,Phone,Department,Status\n"
            "Abdul Karim,01711000001,Software Engineering,Selected\n"
            "Nusrat Jahan,+8801811000002,Accounts,\n"
            "Bad Row,123,Accounts,\n"
        ).encode()
        resp = self.client.post(
            reverse("panel:candidate_upload"),
            {
                "circular": self.circular.pk,
                "file": SimpleUploadedFile("list.csv", csv_data, content_type="text/csv"),
                "default_status": "pending",
                "update_existing": "on",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Candidate.objects.count(), 2)
        self.assertEqual(len(resp.context["report"]["errors"]), 1)

        nusrat = Candidate.objects.get(phone="01811000002")
        self.assertEqual(nusrat.status, "pending")
        self.client.post(reverse("panel:candidate_status", args=[nusrat.pk]), {"status": "selected"})
        nusrat.refresh_from_db()
        self.assertEqual(nusrat.status, "selected")

        self.client.logout()
        resp = self.client.post(reverse("public:landing"), {"phone": "01711000001", "department": self.dept.pk})
        self.assertContains(resp, "You are selected")
        self.assertContains(resp, "01958666999")
        self.assertContains(resp, "hr@iglweb.com")

        resp = self.client.post(reverse("public:landing"), {"phone": "8801711000001", "department": self.dept.pk})
        self.assertContains(resp, "You are selected")

        resp = self.client.post(reverse("public:landing"), {"phone": "01999999999", "department": self.dept.pk})
        self.assertContains(resp, "No record found")

    def test_datatable_endpoint(self):
        self.client.force_login(self.root)
        for i in range(30):
            Candidate.objects.create(
                circular=self.circular, department=self.dept, name=f"Person {i}", phone=f"017{i:08d}",
                status="selected" if i % 3 == 0 else "pending",
            )
        url = reverse("panel:candidate_data")
        data = self.client.get(url, {"draw": 1, "start": 25, "length": 25}).json()
        self.assertEqual((data["recordsTotal"], data["recordsFiltered"], len(data["data"])), (30, 30, 5))
        data = self.client.get(url, {"draw": 2, "start": 0, "length": 25, "status": "selected"}).json()
        self.assertEqual(data["recordsFiltered"], 10)
        data = self.client.get(url, {"draw": 3, "start": 0, "length": 25, "search[value]": "Person 12"}).json()
        self.assertEqual([r["name"] for r in data["data"]], ["Person 12"])

        c = Candidate.objects.first()
        resp = self.client.post(
            reverse("panel:candidate_status", args=[c.pk]), {"status": "not_selected"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertTrue(resp.json()["ok"])

    def test_candidate_department_must_belong_to_circular(self):
        self.client.force_login(self.root)
        other = Department.objects.create(name="Accounts")
        self.circular.departments.add(self.dept)
        url = reverse("panel:candidate_add")
        data = {"circular": self.circular.pk, "department": other.pk, "name": "X", "phone": "01711000009", "status": "pending"}
        resp = self.client.post(url, data)
        self.assertContains(resp, "is not a department of this circular")
        data["department"] = self.dept.pk
        self.assertEqual(self.client.post(url, data).status_code, 302)
        page = self.client.get(url)
        self.assertEqual(page.context["circular_departments"], {str(self.circular.pk): [self.dept.pk]})

    def test_upload_links_departments_to_circular(self):
        self.client.force_login(self.root)
        csv_data = b"Name,Phone,Department\nA,01711000001,Marketing\nB,01711000002,Software Engineering\n"
        self.client.post(reverse("panel:candidate_upload"), {
            "circular": self.circular.pk, "default_status": "pending",
            "file": SimpleUploadedFile("l.csv", csv_data, content_type="text/csv"),
        })
        self.assertEqual(sorted(self.circular.departments.values_list("name", flat=True)), ["Marketing", "Software Engineering"])


class MarksTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.circular = Circular.objects.create(title="Officer", reference_no="IGL-M-1")
        self.dept = Department.objects.create(name="Accounts")
        self.c = Candidate.objects.create(circular=self.circular, department=self.dept, name="Mim", phone="01811000001")

    def test_total_is_the_written_mark_only(self):
        from decimal import Decimal as D

        self.assertIsNone(self.c.total_marks)
        # Dress Up / Body Language / Viva moved to Marking Criteria - old values are no longer counted
        self.c.dress_up_mark, self.c.body_language_mark, self.c.viva_mark = D("4"), D("3"), D("4")
        self.c.save()
        self.assertEqual((self.c.total_marks, self.c.marks_out_of), (None, 0))
        self.c.written_mark = D("4")
        self.c.save()
        self.assertEqual((self.c.total_marks, self.c.marks_out_of, self.c.score_percent), (D("4"), 5, D("80")))


    def test_marks_endpoint(self):
        self.client.force_login(self.root)
        url = reverse("panel:candidate_marks", args=[self.c.pk])
        ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
        r = self.client.post(url, {"field": "written_mark", "value": "4"}, **ajax).json()
        self.assertEqual((r["total"], r["out_of"], r["percent"]), ("4", 5, "80"))
        r = self.client.post(url, {"field": "written_mark", "value": ""}, **ajax).json()  # not taken
        self.assertEqual((r["total"], r["out_of"]), ("", 0))
        self.assertEqual(self.client.post(url, {"field": "written_mark", "value": "6"}, **ajax).status_code, 400)
        # whole numbers only - a decimal is rejected by the backend, not rounded
        for bad in ("4.5", "2.25", "-1", "abc"):
            self.assertEqual(self.client.post(url, {"field": "written_mark", "value": bad}, **ajax).status_code, 400, bad)
        # the old interview marks can no longer be set from the list
        self.assertEqual(self.client.post(url, {"field": "viva_mark", "value": "4"}, **ajax).status_code, 400)
        self.assertEqual(self.client.post(url, {"field": "name", "value": "1"}, **ajax).status_code, 400)
        self.client.post(url, {"field": "written_mark", "value": "5"}, **ajax)
        data = self.client.get(reverse("panel:candidate_data"), {"draw": 1, "order[0][column]": 5, "order[0][dir]": "desc"}).json()
        self.assertEqual(data["data"][0]["written_mark"], "5")
        self.assertNotIn("viva_mark", data["data"][0])


    def test_upload_with_marks(self):
        self.client.force_login(self.root)
        csv_data = (b"Name,Phone,Department,Written\nA,01711000001,Accounts,3\n"
                    b"B,01711000002,Accounts,9\nC,01711000003,Accounts,3.5\n")
        resp = self.client.post(reverse("panel:candidate_upload"), {
            "circular": self.circular.pk, "default_status": "pending",
            "file": SimpleUploadedFile("m.csv", csv_data, content_type="text/csv"),
        })
        a = Candidate.objects.get(phone="01711000001")
        self.assertEqual((str(a.total_marks), a.marks_out_of, a.source), ("3.00", 5, "upload"))
        self.assertEqual(len(resp.context["report"]["errors"]), 2)  # 9 is too high, 3.5 is not a whole number
        self.assertFalse(Candidate.objects.filter(phone="01711000003").exists())

class RegistrationTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.dev = Department.objects.create(name="Software Engineering")
        self.acc = Department.objects.create(name="Accounts")
        self.circular = Circular.objects.create(title="Developer", reference_no="IGL-R-1")
        self.circular.departments.add(self.dev)
        self.url = reverse("public:register")

    def test_register_success(self):
        resp = self.client.post(self.url, {"circular": self.circular.pk, "department": self.dev.pk,
                                           "name": "  Arif   Hasan ", "phone": "8801711000001", "email": "",
                                           "cv": cv_file()})
        self.assertRedirects(resp, reverse("public:register_done"))
        c = Candidate.objects.get()
        self.assertEqual((c.name, c.phone, c.source, c.status), ("Arif Hasan", "01711000001", "online", "pending"))
        self.assertTrue(c.cv.name.endswith(".pdf"))
        done = self.client.get(reverse("public:register_done"))
        self.assertContains(done, "IGL-R-1-")

    def test_cv_is_optional_but_still_capped_at_1mb(self):
        base = {"circular": self.circular.pk, "department": self.dev.pk, "name": "Arif", "phone": "01711000001"}
        resp = self.client.post(self.url, base)  # registering without a CV is allowed
        self.assertRedirects(resp, reverse("public:register_done"))
        self.assertFalse(Candidate.objects.get(phone="01711000001").cv)
        Candidate.objects.all().delete()

        resp = self.client.post(self.url, {**base, "cv": cv_file("big.pdf", 1024 * 1024 + 1)})
        self.assertContains(resp, "must not be larger than")
        resp = self.client.post(self.url, {**base, "cv": cv_file("photo.png")})
        self.assertContains(resp, "File extension")
        self.assertEqual(Candidate.objects.count(), 0)

    def test_department_must_belong_to_circular_and_no_duplicates(self):
        base = {"circular": self.circular.pk, "name": "A", "phone": "01711000001"}
        base_file = lambda: {**base, "cv": cv_file()}  # noqa: E731 - a fresh file per post
        # department not offered by any open circular
        resp = self.client.post(self.url, {**base_file(), "department": self.acc.pk})
        self.assertContains(resp, "Select a valid choice")
        # department offered by another open circular, but not this one
        other = Circular.objects.create(title="Accountant", reference_no="IGL-R-2")
        other.departments.add(self.acc)
        resp = self.client.post(self.url, {**base_file(), "department": self.acc.pk})
        self.assertContains(resp, "Please choose a department of the selected circular.")
        self.assertEqual(Candidate.objects.count(), 0)
        self.client.post(self.url, {**base_file(), "department": self.dev.pk})
        resp = self.client.post(self.url, {**base_file(), "department": self.dev.pk})
        self.assertContains(resp, "already registered for this department")
        self.assertEqual(Candidate.objects.count(), 1)

    def test_closed_or_inactive_circular(self):
        self.circular.registration_open = False
        self.circular.save()
        resp = self.client.get(self.url)
        self.assertContains(resp, "Registration is closed")
        resp = self.client.post(self.url, {"circular": self.circular.pk, "department": self.dev.pk, "name": "A",
                                           "phone": "01711000001", "cv": cv_file()})
        self.assertEqual(Candidate.objects.count(), 0)

    def test_honeypot_blocks_bots(self):
        self.client.post(self.url, {"circular": self.circular.pk, "department": self.dev.pk, "name": "Bot",
                                    "phone": "01711000001", "website": "http://spam", "cv": cv_file()})
        self.assertEqual(Candidate.objects.count(), 0)


class ReviewTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.client.force_login(self.root)
        self.circular = Circular.objects.create(title="Officer", reference_no="IGL-V-1")
        self.dept = Department.objects.create(name="Accounts")
        self.c = Candidate.objects.create(circular=self.circular, department=self.dept, name="Mim", phone="01811000001")
        self.url = reverse("panel:candidate_review", args=[self.c.pk])

    def test_save_review(self):
        import datetime

        r = self.client.post(self.url, {"rating": "4", "expected_salary": "30,000", "available_date": "2026-10-01",
                                        "note": "Good communication"})
        self.assertTrue(r.json()["ok"])
        self.c.refresh_from_db()
        self.assertEqual((self.c.rating, self.c.expected_salary, self.c.available_date, self.c.note),
                         (4, 30000, datetime.date(2026, 10, 1), "Good communication"))
        row = self.client.get(reverse("panel:candidate_data"), {"draw": 1}).json()["data"][0]
        self.assertEqual((row["rating"], row["expected_salary"], row["available_date"]), (4, 30000, "2026-10-01"))
        # clearing everything
        r = self.client.post(self.url, {"rating": "", "expected_salary": "", "available_date": "", "note": ""})
        self.c.refresh_from_db()
        self.assertEqual((self.c.rating, self.c.expected_salary, self.c.available_date), (None, None, None))

    def test_invalid_review(self):
        r = self.client.post(self.url, {"rating": "7", "expected_salary": "abc", "available_date": "tomorrow"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()["errors"]), {"rating", "expected_salary", "available_date"})

    def test_upload_review_columns(self):
        import datetime

        csv_data = ("Name,Phone,Department,Rating,Expected Salary,Available Date,Note\n"
                    "A,01711000001,Accounts,5,\"35,000\",01/11/2026,Strong\n"
                    "B,01711000002,Accounts,9,,,\n").encode()
        resp = self.client.post(reverse("panel:candidate_upload"), {
            "circular": self.circular.pk, "default_status": "pending",
            "file": SimpleUploadedFile("r.csv", csv_data, content_type="text/csv"),
        })
        a = Candidate.objects.get(phone="01711000001")
        self.assertEqual((a.rating, a.expected_salary, a.available_date, a.note), (5, 35000, datetime.date(2026, 11, 1), "Strong"))
        self.assertEqual(len(resp.context["report"]["errors"]), 1)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class InterviewDateTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.dept = Department.objects.create(name="Accounts")
        self.circular = Circular.objects.create(title="Officer", reference_no="IGL-I-1")
        self.circular.departments.add(self.dept)

    def test_admin_sets_and_edits_interview_date(self):
        import datetime

        self.client.force_login(self.root)
        url = reverse("panel:circular_edit", args=[self.circular.pk])
        data = {"title": "Officer", "reference_no": "IGL-I-1", "departments": [self.dept.pk], "is_active": "on",
                "registration_open": "on", "interview_date": "2026-10-04"}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.circular.refresh_from_db()
        self.assertEqual(self.circular.interview_date, datetime.date(2026, 10, 4))
        self.assertIn('value="2026-10-04"', self.client.get(url).content.decode())
        self.client.post(url, {**data, "interview_date": ""})
        self.circular.refresh_from_db()
        self.assertIsNone(self.circular.interview_date)

    def test_candidate_sees_interview_date_after_registration(self):
        import datetime

        self.circular.interview_date = datetime.date(2026, 10, 4)
        self.circular.save()
        self.assertContains(self.client.get(reverse("public:register")), "Sunday, 04 Oct 2026")
        self.client.post(reverse("public:register"), {"circular": self.circular.pk, "department": self.dept.pk,
                                                      "name": "Arif", "phone": "01711000001", "cv": cv_file()})
        done = self.client.get(reverse("public:register_done"))
        self.assertContains(done, "Sunday, 04 Oct 2026")
        self.assertContains(done, "Your time slot will be informed by HR.")
        self.assertNotContains(done, " AM<")
        for site in ("www.iglweb.com", "www.felnatech.com", "www.felnadma.com", "www.iglgroup.org"):
            self.assertContains(done, f'href="https://{site}"')
        # HR changes the date later -> candidate sees the new date on the result page
        self.circular.interview_date = datetime.date(2026, 10, 5)
        self.circular.save()
        result = self.client.post(reverse("public:landing"), {"phone": "01711000001", "department": self.dept.pk})
        self.assertContains(result, "Monday, 05 Oct 2026")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class InterviewTests(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.client.force_login(self.root)
        self.circular = Circular.objects.create(title="Officer", reference_no="IGL-I-1")
        self.dept = Department.objects.create(name="Accounts")
        self.c = Candidate.objects.create(
            circular=self.circular, department=self.dept, name="Mim Akter",
            phone="01811000001", email="mim@example.com",
        )
        self.search_url = reverse("panel:interview")
        self.detail_url = reverse("panel:interview_detail", args=[self.c.pk])

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.search_url).status_code, 302)
        self.assertEqual(self.client.get(self.detail_url).status_code, 302)

    def test_search_by_phone_or_email_opens_the_sheet(self):
        for query in ["01811000001", "8801811000001", "mim@example.com", "MIM@example.com"]:
            resp = self.client.get(self.search_url, {"q": query})
            self.assertRedirects(resp, self.detail_url, msg_prefix=query)

    def test_suggest_endpoint(self):
        url = reverse("panel:interview_suggest")
        self.assertEqual(self.client.get(url, {"q": "0"}).json()["results"], [])  # too short

        for term in ["01811", "8801811000001", "mim@example", "Mim"]:
            results = self.client.get(url, {"q": term}).json()["results"]
            self.assertEqual([r["phone"] for r in results], ["01811000001"], term)
            self.assertEqual(results[0]["url"], self.detail_url)
            self.assertEqual((results[0]["name"], results[0]["department"]), ("Mim Akter", "Accounts"))

        self.assertEqual(self.client.get(url, {"q": "01999999999"}).json()["results"], [])
        self.client.logout()
        self.assertEqual(self.client.get(url, {"q": "01811"}).status_code, 302)

    def test_search_page_has_the_suggestion_box(self):
        page = self.client.get(self.search_url)
        self.assertContains(page, "candidate-suggest")
        self.assertContains(page, reverse("panel:interview_suggest"))

    def test_recent_table_loads_from_the_datatables_endpoint(self):
        page = self.client.get(self.search_url)
        self.assertContains(page, "recent-candidates")
        self.assertContains(page, reverse("panel:candidate_data"))

        Candidate.objects.create(circular=self.circular, department=self.dept, name="Abul Kalam",
                                 phone="01811000009", email="abul@example.com")
        # the table orders by column name, so its own column layout is used - not the candidate list's
        data = self.client.get(reverse("panel:candidate_data"), {
            "draw": 1, "start": 0, "length": 10,
            "order[0][column]": 0, "order[0][dir]": "asc", "columns[0][data]": "name",
        }).json()
        self.assertEqual([r["name"] for r in data["data"]], ["Abul Kalam", "Mim Akter"])
        self.assertIn("interview_url", data["data"][0])

        data = self.client.get(reverse("panel:candidate_data"), {
            "draw": 2, "start": 0, "length": 10, "search[value]": "abul@example.com",
        }).json()
        self.assertEqual([r["name"] for r in data["data"]], ["Abul Kalam"])

    def test_search_without_match_and_with_several_matches(self):
        resp = self.client.get(self.search_url, {"q": "01999999999"})
        self.assertContains(resp, "No candidate found")

        Candidate.objects.create(circular=self.circular, department=self.dept, name="Mim Hasan", phone="01811000002")
        resp = self.client.get(self.search_url, {"q": "Mim"})
        self.assertEqual(len(resp.context["matches"]), 2)


    def test_marks_dropdowns_and_review_are_saved(self):
        import datetime

        resp = self.client.post(self.detail_url, {
            "dress_up_mark": "4", "body_language_mark": "3.5", "viva_mark": "5", "written_mark": "4",
            "rating": "4", "status": "selected", "expected_salary": "30,000",
            "available_date": "2026-10-01", "note": "Confident",
        })
        self.assertRedirects(resp, reverse("panel:evaluation_detail", args=[self.c.pk]))
        self.c.refresh_from_db()
        self.assertEqual((str(self.c.total_marks), self.c.marks_out_of, str(self.c.score_percent)),
                         ("4.00", 5, "80.00"))
        self.assertIsNone(self.c.viva_mark)  # the removed fields are ignored even if posted
        self.assertEqual((self.c.rating, self.c.status, self.c.expected_salary, self.c.available_date, self.c.note),
                         (4, "selected", 30000, datetime.date(2026, 10, 1), "Confident"))

        page = self.client.get(self.detail_url)
        self.assertIsNone(page.context["chart"]["summary"]["written"])  # the chart uses the written exam result (none here)
        self.assertEqual(page.context["chart"]["max_mark"], 5)
        self.assertContains(page, 'id="chart-data"')
        self.assertContains(page, "status-pill status-selected")
        self.assertNotContains(page, "Evaluation Final Status")

        # a written mark with decimals is refused by the sheet form
        resp = self.client.post(self.detail_url, {"written_mark": "3.5", "status": "selected", "rating": "",
                                                  "expected_salary": "", "available_date": "", "note": ""})
        self.assertEqual(resp.status_code, 200)
        self.c.refresh_from_db()
        self.assertEqual(self.c.written_mark, 4)

    def test_cv_button_and_download(self):
        page = self.client.get(self.detail_url)
        self.assertContains(page, "Not uploaded")

        self.client.post(self.detail_url, {
            "dress_up_mark": "", "body_language_mark": "", "viva_mark": "", "written_mark": "",
            "rating": "", "status": "pending", "expected_salary": "", "available_date": "", "note": "",
            "cv": cv_file("mim.pdf"),
        })
        self.c.refresh_from_db()
        self.assertTrue(self.c.cv)

        cv_url = reverse("panel:candidate_cv", args=[self.c.pk])
        self.assertContains(self.client.get(self.detail_url), cv_url)
        resp = self.client.get(cv_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), b"x" * 1024)

        self.client.logout()
        self.assertEqual(self.client.get(cv_url).status_code, 302)

    def test_cv_over_1mb_is_rejected_in_the_panel(self):
        resp = self.client.post(self.detail_url, {
            "dress_up_mark": "", "body_language_mark": "", "viva_mark": "", "written_mark": "",
            "rating": "", "status": "pending", "expected_salary": "", "available_date": "", "note": "",
            "cv": cv_file("big.pdf", 1024 * 1024 + 1),
        })
        self.assertContains(resp, "must not be larger than")
        self.c.refresh_from_db()
        self.assertFalse(self.c.cv)
