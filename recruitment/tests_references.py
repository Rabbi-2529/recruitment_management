"""The reference rows a candidate adds on the registration page."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Candidate, CandidateReference, Circular, Department

TEMP_MEDIA = "test-media-references"


def cv_file(name="cv.pdf"):
    return SimpleUploadedFile(name, b"x" * 1024, content_type="application/pdf")


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class ReferenceRegistrationTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.dept = Department.objects.create(name="Software Engineering")
        self.circular = Circular.objects.create(title="Developer", reference_no="IGL-R-9")
        self.circular.departments.add(self.dept)
        self.url = reverse("public:register")

    def form_data(self, rows, total=None, **extra):
        data = {
            "circular": self.circular.pk, "department": self.dept.pk,
            "name": "Mim Akter", "phone": "01811000001", "email": "", "website": "",
            "ref-TOTAL_FORMS": str(total if total is not None else len(rows)),
            "ref-INITIAL_FORMS": "0", "ref-MIN_NUM_FORMS": "0", "ref-MAX_NUM_FORMS": "10",
        }
        for i, row in enumerate(rows):
            for field in ("name", "organisation", "designation", "phone", "email"):
                data[f"ref-{i}-{field}"] = row.get(field, "")
        data.update(extra)
        return data

    def test_several_references_are_saved_in_order(self):
        rows = [
            {"name": "Mr Karim", "organisation": "IGL Group", "designation": "Manager",
             "phone": "8801711000002", "email": "karim@example.com"},
            {"name": "Dr Rahman", "organisation": "Dhaka University", "designation": "Lecturer",
             "phone": "01911000003"},
        ]
        resp = self.client.post(self.url, self.form_data(rows, cv=cv_file()))
        self.assertRedirects(resp, reverse("public:register_done"))

        candidate = Candidate.objects.get(phone="01811000001")
        saved = list(candidate.references.all())
        self.assertEqual([r.name for r in saved], ["Mr Karim", "Dr Rahman"])
        self.assertEqual([r.order for r in saved], [1, 2])
        self.assertEqual(saved[0].phone, "01711000002")  # 8801... is stored as 01...
        self.assertEqual(saved[1].email, "")             # email is optional

    def test_registering_without_any_reference_still_works(self):
        resp = self.client.post(self.url, self.form_data([{}], cv=cv_file()))
        self.assertRedirects(resp, reverse("public:register_done"))
        self.assertEqual(CandidateReference.objects.count(), 0)

    def test_a_row_needs_a_name_and_a_valid_phone(self):
        resp = self.client.post(self.url, self.form_data([{"organisation": "IGL Group"}], cv=cv_file()))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "This field is required")      # name and phone are both needed
        self.assertEqual(Candidate.objects.count(), 0)           # nothing is saved when a row is wrong

    def test_a_row_left_completely_empty_is_ignored(self):
        resp = self.client.post(self.url, self.form_data([{}, {"name": "Mr Karim", "phone": "01711000002"}], cv=cv_file()))
        self.assertRedirects(resp, reverse("public:register_done"))
        self.assertEqual([r.name for r in CandidateReference.objects.all()], ["Mr Karim"])

    def test_a_bad_phone_number_is_refused(self):
        resp = self.client.post(self.url, self.form_data([{"name": "Mr Karim", "phone": "023"}], cv=cv_file()))
        self.assertContains(resp, "Invalid phone number")
        self.assertEqual(Candidate.objects.count(), 0)

    def test_no_reference_row_until_the_add_button_is_used(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'id="ref-add"')
        self.assertContains(page, 'name="ref-TOTAL_FORMS"')
        self.assertNotContains(page, "ref-0-name")        # nothing shown by default
        self.assertContains(page, "ref-__prefix__-name")  # the template the + button clones

    def test_hr_sees_the_references_on_the_candidate_sheet(self):
        from django.contrib.auth import get_user_model

        self.client.post(self.url, self.form_data(
            [{"name": "Mr Karim", "organisation": "IGL Group", "designation": "Manager", "phone": "01711000002"}],
            cv=cv_file(),
        ))
        candidate = Candidate.objects.get(phone="01811000001")
        root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.client.force_login(root)
        page = self.client.get(reverse("panel:evaluation_detail", args=[candidate.pk]))
        self.assertContains(page, "Mr Karim")
        self.assertContains(page, "IGL Group")
        self.assertContains(page, "Manager")

    def test_one_page_summary_shows_everything_hr_needs(self):
        from django.contrib.auth import get_user_model

        self.client.post(self.url, self.form_data(
            [{"name": "Mr Karim", "organisation": "IGL Group", "designation": "Manager", "phone": "01711000002"}],
            cv=cv_file(),
        ))
        candidate = Candidate.objects.get(phone="01811000001")
        root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.client.force_login(root)

        page = self.client.get(reverse("panel:candidate_summary", args=[candidate.pk]))
        self.assertEqual(page.status_code, 200)
        for text in ("Interview Summary", candidate.name, candidate.registration_no, "Mr Karim", "References",
                     "img/logo.svg", "size: A4"):
            self.assertContains(page, text)

        # the sheet links to it, and an interviewer may not open it
        sheet = self.client.get(reverse("panel:evaluation_detail", args=[candidate.pk]))
        self.assertContains(sheet, reverse("panel:candidate_summary", args=[candidate.pk]))
        self.client.logout()
        self.assertEqual(self.client.get(reverse("panel:candidate_summary", args=[candidate.pk])).status_code, 302)
