"""Interview Call + SMS, Interviewer role, department permissions, separate evaluations, Admin view."""
import datetime
import io
import urllib.parse
import zipfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import (
    Answer,
    Candidate,
    Circular,
    Department,
    EvaluationCriterion,
    Exam,
    ExamAttempt,
    InterviewCall,
    InterviewCallCandidate,
    InterviewerDepartment,
    InterviewerProfile,
    InterviewEvaluation,
    Question,
    SmsGateway,
    SmsLog,
    SmsSenderId,
)

User = get_user_model()
API_KEY = "SECRET-API-KEY-123456"


class FakeResponse:
    def __init__(self, body='{"response_code": 202, "success_message": "SMS Submitted Successfully"}', status=200):
        self.body, self.status = body.encode(), status

    def read(self, *_):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_docx(rows):
    """A tiny BD Jobs style .docx: header + rows of [SL, Image, Name cell, Career, Experience, Applied, Remarks]."""
    def cell(text):
        runs = "".join(
            (f"<w:r><w:br/></w:r>" if i else "") + f"<w:r><w:t>{line}</w:t></w:r>"
            for i, line in enumerate(text.split("\n"))
        )
        return f"<w:tc><w:p>{runs}</w:p></w:tc>"

    header = ["SL", "Image", "Name", "Career Summary", "Experience And Application Status", "Applied On", "Remarks"]
    body = "".join("<w:tr>" + "".join(cell(c) for c in row) + "</w:tr>" for row in [header] + rows)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body><w:tbl>{body}</w:tbl></w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("word/document.xml", xml)
    return buffer.getvalue()


class InterviewModuleBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.dev = Department.objects.create(name="Software Developer")
        self.sales = Department.objects.create(name="Corporate Sales")
        self.support = Department.objects.create(name="IT Support")
        self.circular = Circular.objects.create(title="Hiring 2026", reference_no="IGL-M-1",
                                                interview_date=datetime.date(2026, 9, 20))
        self.circular.departments.add(self.dev, self.sales, self.support)
        self.rahim = Candidate.objects.create(circular=self.circular, department=self.dev, name="Rahim Ahmed",
                                              phone="01911111111", email="rahim@example.com")
        self.sales_person = Candidate.objects.create(circular=self.circular, department=self.sales, name="Sadia",
                                                     phone="01822222222")
        self.support_person = Candidate.objects.create(circular=self.circular, department=self.support,
                                                       name="Imran", phone="01733333333")
        self.gateway = SmsGateway.objects.create(name="IGL SMS", api_key=API_KEY)
        self.sender = SmsSenderId.objects.create(gateway=self.gateway, sender_id="IGL", is_default=True)

    def make_interviewer(self, email, departments, **perms):
        user = User.objects.create_user(email, email, "InterviewPass#2026", first_name=email.split("@")[0])
        profile = InterviewerProfile.objects.create(user=user, **perms)
        for d in departments:
            InterviewerDepartment.objects.create(interviewer=profile, department=d)
        return user

    def upload_call(self, content, filename, department=None, template=None):
        self.client.force_login(self.admin)
        data = {
            "uploaded_file": SimpleUploadedFile(filename, content),
            "department": (department or self.dev).pk,
            "gateway": self.gateway.pk, "sender": self.sender.pk,
            "message_template": template or "Dear {candidate_name},\nYou are invited for an interview on 20-09-2026 "
                                             "at 10:00 AM, House 33, Road 04, Dhanmondi, Dhaka-1205.\n\nIGL Group",
        }
        resp = self.client.post(reverse("panel:interview_call_create"), data)
        call = InterviewCall.objects.latest("pk")
        self.assertRedirects(resp, reverse("panel:interview_call_mapping", args=[call.pk]))
        return call


class InterviewCallUploadTests(InterviewModuleBase):
    CSV = (
        "SL,Name,Mobile,Alt Phone,Email,Candidate ID\n"
        "1,Rahim Ahmed,01911111111,01999999999,rahim@example.com,A-1\n"
        "2,Rahim Ahmed,01911111111,,rahim@example.com,A-1\n"
        "3,Rahim A.,8801911111111,,,\n"
        "4,Rahim,+8801911111111,,,\n"
        "5,Karim,01722-222222,,karim@example.com,A-2\n"
        "6,No Phone,N/A,,x@example.com,A-3\n"
    ).encode()

    def test_upload_map_dedupe_and_candidates_untouched(self):
        before = Candidate.objects.count()
        call = self.upload_call(self.CSV, "call.csv")
        self.assertEqual(call.headers, ["SL", "Name", "Mobile", "Alt Phone", "Email", "Candidate ID"])
        self.assertEqual(call.column_map["phone"], "Mobile")  # guessed

        page = self.client.get(reverse("panel:interview_call_mapping", args=[call.pk]))
        self.assertContains(page, "Map columns")
        resp = self.client.post(reverse("panel:interview_call_mapping", args=[call.pk]), {
            "candidate_name": "Name", "phone": "Mobile", "email": "Email", "candidate_ref": "Candidate ID",
        })
        self.assertRedirects(resp, reverse("panel:interview_call_detail", args=[call.pk]))

        people = list(call.people.order_by("row_number"))
        self.assertEqual([p.phone for p in people], ["01911111111", "01722222222", ""])  # one Rahim only
        self.assertEqual(people[0].candidate_ref, "A-1")
        self.assertEqual(people[0].matched_candidate, self.rahim)  # linked, not copied
        self.assertFalse(people[2].is_valid)
        self.assertEqual(people[2].sms_status, "invalid")
        call.refresh_from_db()
        self.assertEqual(call.duplicate_rows, 3)
        self.assertEqual(Candidate.objects.count(), before)  # NOT added to the main Candidate table

        detail = self.client.get(reverse("panel:interview_call_detail", args=[call.pk]))
        self.assertContains(detail, "Karim")
        self.assertContains(detail, "No valid phone number")
        self.assertNotContains(detail, API_KEY)

    def test_phone_column_is_required_and_used_once(self):
        call = self.upload_call(self.CSV, "call.csv")
        url = reverse("panel:interview_call_mapping", args=[call.pk])
        self.assertContains(self.client.post(url, {"candidate_name": "Name"}), "Choose the column that holds the phone")
        self.assertContains(self.client.post(url, {"phone": "Mobile", "email": "Mobile"}), "only be used for one field")

    def test_bd_jobs_docx(self):
        content = make_docx([
            ["1", "", "Rabea Akter\nAge: 31.1\nDhanmondi, Dhaka\nGovt BM College\nMasters of Arts (MA)\n"
                      "Job Matching: 64%\n01741501721,\nrabea@gmail.com",
             "Shop: Receptionist (4+ years)", "Total Experience: 4 Years\nSalary: 14,000\nTotal Applied: 1",
             "21 Sep 2026", ""],
            ["2", "", "Abdullah Al Noman\nAge: 24.4\nMirpur 1, Dhaka\nDIU\nBSc\nJob Matching: 64%\n"
                      "01644859378,01644859378\nnoman@gmail.com", "Designer", "Total Experience: 2 Years", "21 Sep 2026", ""],
        ])
        call = self.upload_call(content, "Corporate Sales Intern.docx", department=self.sales)
        self.assertEqual(call.column_map, {"candidate_name": "Name", "phone": "Phone", "email": "Email",
                                           "education": "Education", "experience": "Total Experience"})
        self.client.post(reverse("panel:interview_call_mapping", args=[call.pk]), call.column_map)
        people = list(call.people.order_by("row_number"))
        self.assertEqual([(p.candidate_name, p.phone, p.email) for p in people], [
            ("Rabea Akter", "01741501721", "rabea@gmail.com"), ("Abdullah Al Noman", "01644859378", "noman@gmail.com"),
        ])
        self.assertEqual(people[0].additional_data["Expected Salary"], "14,000")
        self.assertIn("Masters of Arts", people[0].additional_data["Education"])

    def test_plain_text_list(self):
        content = b"1. Rahim Ahmed - 01911111111\nKarim, +880 1722-222222\nRahim again 8801911111111\n"
        call = self.upload_call(content, "list.txt")
        self.client.post(reverse("panel:interview_call_mapping", args=[call.pk]), call.column_map)
        self.assertEqual(sorted(call.people.values_list("phone", flat=True)), ["01722222222", "01911111111"])

    def test_upload_form_has_no_date_time_venue_and_title_is_filled_in(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("panel:interview_call_create")).content.decode()
        for name in ("interview_date", "interview_time", "venue", "information"):
            self.assertNotIn(f'name="{name}"', page)
        self.assertIn("data-sms-counter", page)
        call = self.upload_call(b"Name,Phone\nA,01911111111\n", "a.csv")
        self.assertTrue(call.title.startswith("Software Developer - "))
        self.assertIsNone(call.interview_date)

    def test_message_is_required(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("panel:interview_call_create"), {
            "uploaded_file": SimpleUploadedFile("a.csv", b"Name,Phone\nA,01911111111\n"),
            "department": self.dev.pk, "message_template": "   ",
        })
        self.assertContains(resp, "Write the SMS message.")

    def test_unsupported_file(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("panel:interview_call_create"), {
            "uploaded_file": SimpleUploadedFile("x.pdf", b"%PDF"), "department": self.dev.pk, "message_template": "Hi",
        })
        self.assertContains(resp, "Upload a .xlsx, .csv, .txt or .docx file.")


class SmsSendingTests(InterviewModuleBase):
    def setUp(self):
        super().setUp()
        csv_data = b"Name,Phone,Roll\nRahim,01911111111,R-7\nKarim,01722222222,R-8\nNusrat,01833333333,R-9\n"
        self.call = self.upload_call(csv_data, "c.csv")
        self.client.post(reverse("panel:interview_call_mapping", args=[self.call.pk]),
                         {"candidate_name": "Name", "phone": "Phone", "candidate_ref": "Roll"})
        self.people = {p.candidate_name: p for p in self.call.people.all()}

    def send(self, ids=None, scope=None, retry=False, template=None):
        data = {"gateway": self.gateway.pk, "sender": self.sender.pk,
                "message_template": template or self.call.message_template}
        if scope:
            data["scope"] = scope
        if retry:
            data["retry"] = "1"
        if ids:
            data["ids"] = [str(i) for i in ids]
        queued = self.client.post(reverse("panel:interview_call_queue", args=[self.call.pk]), data).json()
        result = None
        while True:
            result = self.client.post(reverse("panel:interview_call_send_batch", args=[self.call.pk])).json()
            if result["done"]:
                break
        return queued, result

    def test_one_request_per_person_with_encoded_message(self):
        with mock.patch("recruitment.sms.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            queued, result = self.send(ids=[self.people["Rahim"].pk, self.people["Karim"].pk],
                                       template="Dear {candidate_name} ({candidate_id}), {department} 20-09-2026 10:00 AM & more")
        self.assertEqual(queued["queued"], 2)
        self.assertEqual(urlopen.call_count, 2)  # separately - never two numbers in one request
        urls = [c.args[0].full_url for c in urlopen.call_args_list]
        for url in urls:
            parsed = urllib.parse.urlparse(url)
            self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", "http://sms.iglweb.com/api/v1/send")
            query = urllib.parse.parse_qs(parsed.query)
            self.assertEqual(query["api_key"], [API_KEY])
            self.assertEqual(query["senderid"], ["IGL"])
            self.assertEqual(len(query["contacts"]), 1)
            self.assertNotIn(",", query["contacts"][0])
        rahim_query = urllib.parse.parse_qs(urllib.parse.urlparse(urls[0]).query)
        self.assertEqual(rahim_query["contacts"], ["01911111111"])
        self.assertEqual(rahim_query["msg"], ["Dear Rahim (R-7), Software Developer 20-09-2026 10:00 AM & more"])
        self.assertIn("msg=Dear%20Rahim", urls[0])  # URL-encoded
        self.assertEqual(result["counts"]["sent"], 2)
        self.assertEqual(result["counts"]["not_sent"], 1)
        log = SmsLog.objects.get(phone="01911111111")
        self.assertEqual((log.status, log.sender_id, log.http_status), ("success", "IGL", 200))
        self.assertIn("SMS Submitted", log.api_response)
        self.assertIsNotNone(log.sent_at)

    def test_never_sends_twice_and_retry_only_failed(self):
        responses = {"01911111111": FakeResponse(), "01722222222": FakeResponse('{"response_code": 1003, "error_message": "Invalid number"}'),
                     "01833333333": FakeResponse()}

        def fake(request, timeout):
            contact = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)["contacts"][0]
            return responses[contact]

        with mock.patch("recruitment.sms.urllib.request.urlopen", side_effect=fake) as urlopen:
            _, result = self.send(scope="all")
            self.assertEqual(urlopen.call_count, 3)
            self.assertEqual((result["counts"]["sent"], result["counts"]["failed"]), (2, 1))
            failed = SmsLog.objects.get(phone="01722222222")
            self.assertEqual(failed.status, "failed")
            self.assertIn("Invalid number", failed.api_response)

            # "send to all" again sends to nobody who already received it
            queued, _ = self.send(scope="all")
            self.assertEqual(queued["queued"], 0)
            self.assertEqual(urlopen.call_count, 3)

            # retry only the failed one
            responses["01722222222"] = FakeResponse()
            queued, result = self.send(scope="all", retry=True)
            self.assertEqual(queued["queued"], 1)
            self.assertEqual(urlopen.call_count, 4)
            self.assertEqual(result["counts"]["sent"], 3)
        self.assertEqual(SmsLog.objects.filter(phone="01911111111").count(), 1)
        self.assertTrue(SmsLog.objects.get(phone="01722222222", is_retry=True).status == "success")

    def test_network_error_is_recorded_without_leaking_key(self):
        with mock.patch("recruitment.sms.urllib.request.urlopen", side_effect=OSError(f"boom {API_KEY}")):
            _, result = self.send(ids=[self.people["Nusrat"].pk])
        self.assertEqual(result["counts"]["failed"], 1)
        log = SmsLog.objects.get()
        self.assertEqual(log.status, "failed")
        self.assertNotIn(API_KEY, log.error)
        data = self.client.get(reverse("panel:sms_log_data"), {"draw": 1, "status": "failed"})
        self.assertContains(data, "Nusrat")
        self.assertNotContains(data, API_KEY)
        self.assertEqual(data.json()["recordsFiltered"], 1)
        self.assertEqual(self.client.get(reverse("panel:sms_log_data"), {"status": "success"}).json()["recordsFiltered"], 0)
        page = self.client.get(reverse("panel:sms_log"))
        self.assertContains(page, "sms-log")
        self.assertNotContains(page, API_KEY)


    def test_sending_sms_keeps_the_main_status(self):
        with mock.patch("recruitment.sms.urllib.request.urlopen", return_value=FakeResponse()):
            self.send(ids=[self.people["Rahim"].pk])
        self.rahim.refresh_from_db()
        self.assertEqual(self.rahim.status, "pending")

    def test_preview_and_placeholders(self):
        resp = self.client.post(reverse("panel:interview_call_preview", args=[self.call.pk]), {
            "message_template": "Hi {candidate_name} ({phone}), {department}. {unknown}", "person": self.people["Karim"].pk,
        }).json()
        self.assertEqual(resp["message"], "Hi Karim (01722222222), Software Developer. {unknown}")

    def test_api_key_never_in_pages(self):
        for url in [reverse("panel:sms_settings"), reverse("panel:interview_call_detail", args=[self.call.pk]),
                    reverse("panel:sms_gateway_edit", args=[self.gateway.pk])]:
            self.assertNotContains(self.client.get(url), API_KEY)

    def test_gateway_key_kept_when_left_empty(self):
        self.client.post(reverse("panel:sms_gateway_edit", args=[self.gateway.pk]),
                         {"name": "IGL SMS", "base_url": "http://sms.iglweb.com/api/v1/send", "api_key": "", "is_active": "on"})
        self.gateway.refresh_from_db()
        self.assertEqual(self.gateway.api_key, API_KEY)


class InterviewerTests(InterviewModuleBase):
    def setUp(self):
        super().setUp()
        self.a = self.make_interviewer("a@iglweb.com", [self.dev, self.sales])
        self.b = self.make_interviewer("b@iglweb.com", [self.dev])
        self.c = self.make_interviewer("c@iglweb.com", [self.dev])
        # the tests use the six default criteria (6 x 5 = 30 marks); switch off the ones moved from the old sheet
        EvaluationCriterion.objects.filter(name__in=["Dress Up", "Body Language", "Viva"]).update(is_active=False)
        self.criteria = list(EvaluationCriterion.objects.filter(is_active=True))

    def marks(self, values, submit=True, user=None, **notes):
        """Form data with the field names the page uses: criteria for a new sheet, saved lines for an existing one."""
        existing = InterviewEvaluation.objects.filter(candidate=self.rahim, interviewer=user).first() if user else None
        keys = [f"s{sc.pk}" for sc in existing.scores.all()] if existing else [f"c{c.pk}" for c in self.criteria]
        data = {f"mark_{key}": str(v) for key, v in zip(keys, values)}
        data.update(notes)
        data["submit" if submit else "save"] = "1"
        return data

    def test_admin_creates_interviewer_with_departments(self):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("panel:interviewer_add"), {
            "first_name": "John", "email": "John@IGLweb.com", "password": "LongPassword#1",
            "departments": [self.dev.pk, self.support.pk], "is_active": "on",
        })
        self.assertRedirects(resp, reverse("panel:interviewer_list"))
        john = User.objects.get(username="john@iglweb.com")
        self.assertFalse(john.is_superuser or john.is_staff)
        self.assertEqual(set(john.interviewer_profile.departments.all()), {self.dev, self.support})
        self.assertEqual(self.dev.interviewers.count(), 4)  # one department -> many interviewers

    def test_login_goes_to_the_right_dashboard(self):
        resp = self.client.post(reverse("panel:login"), {"email": "a@iglweb.com", "password": "InterviewPass#2026"})
        self.assertRedirects(resp, reverse("interviewer:dashboard"))
        self.client.logout()
        resp = self.client.post(reverse("panel:login"), {"email": "root@iglweb.com", "password": "StrongPass#2026"})
        self.assertRedirects(resp, reverse("panel:dashboard"))

    def test_interviewer_cannot_open_admin_pages(self):
        self.client.force_login(self.a)
        for name in ["panel:dashboard", "panel:interview_call_list", "panel:interview_call_create", "panel:sms_settings",
                     "panel:sms_log", "panel:interviewer_list", "panel:department_list", "panel:criteria",
                     "panel:evaluation_list", "panel:candidate_list", "panel:exam_list"]:
            self.assertRedirects(self.client.get(reverse(name)), reverse("interviewer:dashboard"), msg_prefix=name)
        call = InterviewCall.objects.create(department=self.dev, interview_date="2026-09-20", interview_time="10:00",
                                            venue="x", title="t", uploaded_file="x.csv")
        for name in ["panel:interview_call_queue", "panel:interview_call_send_batch"]:
            resp = self.client.post(reverse(name, args=[call.pk]), {"scope": "all"})
            self.assertEqual(resp.status_code, 302, name)
        # "next" cannot send an interviewer to an admin page either
        self.client.logout()
        resp = self.client.post(reverse("panel:login") + "?next=/panel/sms/settings/",
                                {"email": "a@iglweb.com", "password": "InterviewPass#2026"})
        self.assertRedirects(resp, reverse("interviewer:dashboard"))

    def test_only_assigned_departments_are_visible(self):
        self.client.force_login(self.a)  # Software Developer + Corporate Sales
        page = self.client.get(reverse("interviewer:candidates"))
        self.assertContains(page, "Rahim Ahmed")
        self.assertContains(page, "Sadia")
        self.assertNotContains(page, "Imran")  # IT Support
        self.assertEqual(self.client.get(reverse("interviewer:candidate", args=[self.support_person.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("interviewer:candidate_cv", args=[self.support_person.pk])).status_code, 404)

        self.client.force_login(self.b)  # Software Developer only
        page = self.client.get(reverse("interviewer:candidates"))
        self.assertContains(page, "Rahim Ahmed")
        self.assertNotContains(page, "Sadia")
        self.assertEqual(self.client.get(reverse("interviewer:candidate", args=[self.sales_person.pk])).status_code, 404)


    def test_each_interviewer_keeps_own_marks(self):
        url = reverse("interviewer:candidate", args=[self.rahim.pk])
        self.client.force_login(self.a)
        self.client.post(url, self.marks([5, 4, 5, 3, 2, 3], comments="Strong coder"))
        self.client.force_login(self.b)
        self.client.post(url, self.marks([4, 4, 4, 3, 3, 2], comments="Good talker"))

        a_eval = InterviewEvaluation.objects.get(candidate=self.rahim, interviewer=self.a)
        b_eval = InterviewEvaluation.objects.get(candidate=self.rahim, interviewer=self.b)
        self.assertEqual((a_eval.total_mark, a_eval.out_of), (Decimal("22"), Decimal("30")))
        self.assertEqual(b_eval.total_mark, Decimal("20"))
        self.assertEqual(a_eval.comments, "Strong coder")
        self.assertEqual(a_eval.department, self.dev)

        # B's page shows only B's sheet - never A's marks or comments
        page = self.client.get(url)
        self.assertContains(page, "Good talker")
        self.assertNotContains(page, "Strong coder")
        self.assertContains(page, "status-pill status-pending")  # the one main status

        # a second submit by A cannot touch B's sheet (and A's is locked after submitting)
        self.client.force_login(self.a)
        self.client.post(url, self.marks([0, 0, 0, 0, 0, 0], user=self.a))
        b_eval.refresh_from_db()
        a_eval.refresh_from_db()
        self.assertEqual((b_eval.total_mark, a_eval.total_mark), (Decimal("20"), Decimal("22")))
        self.assertEqual(InterviewEvaluation.objects.filter(candidate=self.rahim).count(), 2)


    def test_draft_submit_lock_and_unlock(self):
        url = reverse("interviewer:candidate", args=[self.rahim.pk])
        self.client.force_login(self.a)
        self.client.post(url, self.marks([3, "", "", "", "", ""], submit=False))  # draft may be incomplete
        evaluation = InterviewEvaluation.objects.get(interviewer=self.a)
        self.assertFalse(evaluation.is_submitted)

        resp = self.client.post(url, self.marks([3, "", "", "", "", ""], user=self.a))  # submit needs every mark
        self.assertContains(resp, "Give a mark before submitting.")
        # decimals and marks above 5 are refused by the backend
        resp = self.client.post(url, self.marks([4.5, 5, 5, 5, 5, 5], user=self.a))
        self.assertContains(resp, "Whole numbers only")
        resp = self.client.post(url, self.marks([6, 5, 5, 5, 5, 5], user=self.a))
        self.assertEqual(resp.status_code, 200)
        evaluation.refresh_from_db()
        self.assertFalse(evaluation.is_submitted)

        self.client.post(url, self.marks([5, 5, 5, 5, 5, 5], user=self.a))
        evaluation.refresh_from_db()
        self.assertTrue(evaluation.is_submitted)
        self.assertEqual((evaluation.total_mark, evaluation.out_of), (Decimal("30"), Decimal("30")))
        self.rahim.refresh_from_db()
        self.assertEqual(self.rahim.status, "pending")  # marking never changes the main status

        self.client.post(url, self.marks([1, 1, 1, 1, 1, 1], user=self.a))  # locked now
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.total_mark, Decimal("30"))

        self.client.force_login(self.admin)
        self.client.post(reverse("panel:evaluation_detail", args=[self.rahim.pk]), {"unlock": evaluation.pk})
        self.client.force_login(self.a)
        self.client.post(url, self.marks([4, 4, 4, 4, 4, 4], user=self.a))
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.total_mark, Decimal("24"))


    def test_all_assigned_interviewers_complete_the_evaluation(self):
        url = reverse("interviewer:candidate", args=[self.rahim.pk])
        for user in (self.a, self.b, self.c):
            self.client.force_login(user)
            self.client.post(url, self.marks([4, 3, 3, 2, 1, 1]))
        self.client.force_login(self.admin)
        page = self.client.get(reverse("panel:evaluation_detail", args=[self.rahim.pk]))
        self.assertEqual(page.context["interviewer_submitted_count"], 3)
        chart = page.context["chart"]
        self.assertEqual(len(chart["interviewers"]), 3)
        self.assertEqual(chart["interviewers"][0]["marks"], [4, 3, 3, 2, 1, 1])
        self.assertEqual(chart["distribution"]["of"], 1)
        self.rahim.refresh_from_db()
        self.assertEqual(self.rahim.status, "pending")  # only the Admin sets the main status


    def test_admin_sees_every_mark_and_decides(self):
        url = reverse("interviewer:candidate", args=[self.rahim.pk])
        for user, values, note in ((self.a, [5, 4, 4, 3, 2, 2], "A-note"), (self.b, [5, 5, 4, 3, 3, 3], "B-note")):
            self.client.force_login(user)
            self.client.post(url, self.marks(values, comments=note))

        self.client.force_login(self.admin)
        detail = reverse("panel:evaluation_detail", args=[self.rahim.pk])
        page = self.client.get(detail)
        for text in ("A-note", "B-note", "Technical Knowledge", "mark-chip mark-5", 'id="chart-data"'):
            self.assertContains(page, text)
        self.assertEqual(page.context["submitted_count"], 2)
        self.assertEqual(page.context["average_total"], Decimal("21.50"))
        # the old "View all marks" page is the same combined page
        self.assertEqual(self.client.get(reverse("panel:interview_detail", args=[self.rahim.pk])).context["candidate"],
                         self.rahim)

        data = self.client.get(reverse("panel:evaluation_data"), {"draw": 1, "interviewer": self.a.pk}).json()
        self.assertEqual([r["name"] for r in data["data"]], ["Rahim Ahmed"])
        self.assertEqual((data["data"][0]["status"], data["data"][0]["status_label"]), ("pending", "Pending"))
        self.assertNotIn("interview_status", data["data"][0])
        self.assertContains(self.client.get(reverse("panel:evaluation_list")), 'id="evaluations"')

        # the decision is the candidate's one main status, set from the same page
        self.client.post(detail, {"status": "selected", "written_mark": "", "rating": "", "expected_salary": "",
                                  "available_date": "", "note": ""})
        self.rahim.refresh_from_db()
        self.assertEqual(self.rahim.status, "selected")
        data = self.client.get(reverse("panel:evaluation_data"), {"draw": 1, "status": "selected"}).json()
        self.assertEqual([r["name"] for r in data["data"]], ["Rahim Ahmed"])

        # after the decision interviewers cannot change marks, and they see the same status
        self.client.force_login(self.a)
        self.client.post(url, self.marks([0, 0, 0, 0, 0, 0], submit=False, user=self.a))
        self.assertEqual(InterviewEvaluation.objects.get(interviewer=self.a).total_mark, Decimal("20"))
        self.assertContains(self.client.get(url), "status-pill status-selected")
        self.assertContains(self.client.get(reverse("interviewer:candidates")), "status-pill status-selected")


    def test_admin_gives_own_marks(self):
        from .evaluation import sheet_rows

        url = reverse("interviewer:candidate", args=[self.rahim.pk])
        self.client.force_login(self.a)
        self.client.post(url, self.marks([5, 4, 5, 3, 2, 3], comments="A-note"))
        a_eval = InterviewEvaluation.objects.get(interviewer=self.a)

        self.client.force_login(self.admin)
        detail = reverse("panel:evaluation_detail", args=[self.rahim.pk])
        page = self.client.get(detail)
        self.assertContains(page, "Your marks (Admin)")
        rows = sheet_rows(None)
        data = {f"ev-mark_{key}": "5" for key, *_ in rows}
        data.update({"ev-comments": "Admin view", "ev-submit": "1"})
        self.client.post(detail, data)
        mine = InterviewEvaluation.objects.get(candidate=self.rahim, interviewer=self.admin)
        self.assertTrue(mine.is_submitted)
        self.assertEqual((mine.total_mark, mine.comments), (Decimal("30"), "Admin view"))

        # the Admin's sheet refuses decimals too
        existing = sheet_rows(mine)
        bad = {f"ev-mark_{key}": "2.5" for key, *_ in existing}
        bad["ev-save"] = "1"
        self.assertContains(self.client.post(detail, bad), "Whole numbers only")
        mine.refresh_from_db()
        self.assertEqual(mine.total_mark, Decimal("30"))

        # the Admin can still change it after submitting; the interviewer's sheet is untouched
        data = {f"ev-mark_{key}": "4" for key, *_ in existing}
        data.update({"ev-comments": "Changed", "ev-save": "1"})
        self.client.post(detail, data)
        mine.refresh_from_db()
        self.assertEqual(mine.total_mark, Decimal("24"))
        a_eval.refresh_from_db()
        self.assertEqual((a_eval.total_mark, a_eval.comments), (Decimal("22"), "A-note"))

        page = self.client.get(detail)
        self.assertEqual(page.context["interviewer_submitted_count"], 1)  # the Admin's sheet is not an interviewer's
        self.assertEqual(page.context["submitted_count"], 2)

    def test_written_result_view_only_unless_permitted(self):
        exam = Exam.objects.create(circular=self.circular, title="Written", pass_percent=50)
        q = Question.objects.create(exam=exam, text="Explain", kind="text", marks=10)
        attempt = ExamAttempt.objects.create(exam=exam, candidate=self.rahim)
        answer = Answer.objects.create(attempt=attempt, question=q, text_answer="My answer", awarded=Decimal("7"))
        ExamAttempt.objects.filter(pk=attempt.pk).update(submitted_at=datetime.datetime(2026, 9, 20, tzinfo=datetime.timezone.utc))
        attempt.refresh_from_db()
        attempt.recalculate()

        self.client.force_login(self.b)
        page = self.client.get(reverse("interviewer:candidate", args=[self.rahim.pk]))
        self.assertContains(page, "Passed")
        self.assertContains(page, "70")
        results = self.client.get(reverse("interviewer:written_results"))
        self.assertContains(results, "Rahim Ahmed")
        self.client.post(reverse("interviewer:written_sheet", args=[attempt.pk]), {f"mark_{answer.pk}": "1"})
        answer.refresh_from_db()
        self.assertEqual(answer.awarded, Decimal("7"))  # not allowed

        writer = self.make_interviewer("w@iglweb.com", [self.dev], can_edit_written_marks=True)
        self.client.force_login(writer)
        self.client.post(reverse("interviewer:written_sheet", args=[attempt.pk]), {f"mark_{answer.pk}": "4"})
        answer.refresh_from_db()
        self.assertEqual(answer.awarded, Decimal("4"))

        # another department's interviewer cannot even open it
        self.client.force_login(self.make_interviewer("s@iglweb.com", [self.support]))
        self.assertEqual(self.client.get(reverse("interviewer:written_sheet", args=[attempt.pk])).status_code, 404)

    def test_interviewer_pages_render(self):
        self.client.force_login(self.a)
        for name in ["interviewer:dashboard", "interviewer:interviews", "interviewer:candidates",
                     "interviewer:written_results", "interviewer:profile"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_admin_pages_render(self):
        self.client.force_login(self.admin)
        for name in ["panel:interview_call_list", "panel:interview_call_create", "panel:sms_log", "panel:sms_settings",
                     "panel:interviewer_list", "panel:interviewer_add", "panel:criteria", "panel:evaluation_list",
                     "panel:dashboard", "panel:candidate_add"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("panel:evaluation_detail", args=[self.rahim.pk])).status_code, 200)
