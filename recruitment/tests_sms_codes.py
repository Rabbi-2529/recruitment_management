import datetime
import importlib

from django.apps import apps
from django.test import SimpleTestCase, TestCase

from .models import Candidate, Circular, Department, InterviewCall, InterviewCallCandidate, SmsLog
from .sms import looks_successful


class SuccessCodeTests(SimpleTestCase):
    """The IGL SMS API answers 445000 when the SMS is accepted."""

    def test_445000_means_sent(self):
        for body in [
            "445000",
            '{"response_code": 445000, "message": "SMS Submitted"}',
            '{"response_code": "445000"}',
            '{"code": 445000, "message_id": "abc"}',
            '{"status": "445000"}',
            '{"data": {"status_code": 445000}}',
            "445000|SMS sent successfully",
            "Status: 445000",
        ]:
            self.assertTrue(looks_successful(200, body), body)

    def test_other_codes_are_failures(self):
        for body in [
            '{"response_code": 1003, "error_message": "Invalid number"}',
            "1007",
            '{"code": 445001}',
            "4450001",
            "Error: insufficient balance",
        ]:
            self.assertFalse(looks_successful(200, body), body)
        self.assertFalse(looks_successful(500, "445000"))  # a server error is never a success

    def test_success_codes_are_configurable(self):
        self.assertTrue(looks_successful(200, '{"code": 777}', ["777"]))
        self.assertFalse(looks_successful(200, '{"code": 445000}', ["777"]))


class CorrectOldLogsTests(TestCase):
    def test_failed_logs_with_445000_become_sent(self):
        dept = Department.objects.create(name="Sales")
        circular = Circular.objects.create(title="C", reference_no="C-1")
        registered = Candidate.objects.create(circular=circular, department=dept, name="Rabea", phone="01741501721")
        call = InterviewCall.objects.create(title="t", department=dept, interview_date=datetime.date(2026, 9, 28),
                                            interview_time=datetime.time(10), venue="Dhaka", uploaded_file="x.csv")
        ok_person = InterviewCallCandidate.objects.create(interview_call=call, candidate_name="Rabea",
                                                          phone="01741501721", sms_status="failed",
                                                          matched_candidate=registered)
        bad_person = InterviewCallCandidate.objects.create(interview_call=call, candidate_name="X",
                                                           phone="01911111111", sms_status="failed")
        SmsLog.objects.create(interview_call=call, recipient=ok_person, phone=ok_person.phone, sender_id="IGL",
                              message="m", status="failed", http_status=200, api_response='{"response_code":445000}',
                              error="The SMS API did not confirm the message.")
        SmsLog.objects.create(interview_call=call, recipient=bad_person, phone=bad_person.phone, sender_id="IGL",
                              message="m", status="failed", http_status=200, api_response='{"response_code":1003}')

        migration = importlib.import_module("recruitment.migrations.0013_sms_success_codes")
        migration.mark_445000_as_sent(apps, None)

        ok_person.refresh_from_db()
        bad_person.refresh_from_db()
        registered.refresh_from_db()
        self.assertEqual((ok_person.sms_status, bad_person.sms_status), ("sent", "failed"))
        self.assertEqual(SmsLog.objects.get(recipient=ok_person).status, "success")
        self.assertEqual(SmsLog.objects.get(recipient=bad_person).status, "failed")
        self.assertEqual(registered.status, "pending")  # sending an SMS never changes the main status
