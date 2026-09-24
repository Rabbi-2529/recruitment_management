import importlib
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Candidate, Circular, Department, EvaluationCriterion, InterviewEvaluation

migration = importlib.import_module("recruitment.migrations.0016_move_old_marks_to_criteria")


class MoveOldMarksTests(TestCase):
    def setUp(self):
        # start as a site that never had these criteria (the test database already ran this migration)
        EvaluationCriterion.objects.filter(name__in=["Dress Up", "Body Language", "Viva"]).delete()
        self.root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        self.dept = Department.objects.create(name="Sales")
        circular = Circular.objects.create(title="C", reference_no="C-1")
        self.mim = Candidate.objects.create(circular=circular, department=self.dept, name="Mim", phone="01811000001",
                                            dress_up_mark=Decimal("4"), body_language_mark=Decimal("3"),
                                            viva_mark=Decimal("5"), written_mark=Decimal("4"))
        self.only_viva = Candidate.objects.create(circular=circular, department=self.dept, name="Viva only",
                                                  phone="01811000002", viva_mark=Decimal("2"))
        self.none = Candidate.objects.create(circular=circular, department=self.dept, name="No marks", phone="01811000003")

    def run_migration(self):
        migration.move_old_marks(apps, None)

    def test_criteria_added_and_old_marks_moved(self):
        self.run_migration()
        names = list(EvaluationCriterion.objects.values_list("name", "max_marks"))
        for name in ("Dress Up", "Body Language", "Viva"):
            self.assertIn((name, Decimal("5")), names)

        evaluation = InterviewEvaluation.objects.get(candidate=self.mim)
        self.assertEqual(evaluation.interviewer, self.root)
        self.assertTrue(evaluation.is_submitted)
        self.assertEqual({s.criterion_name: s.mark for s in evaluation.scores.all()},
                         {"Dress Up": 4, "Body Language": 3, "Viva": 5})
        self.assertEqual((evaluation.total_mark, evaluation.out_of), (Decimal("12"), Decimal("15")))

        viva = InterviewEvaluation.objects.get(candidate=self.only_viva)
        self.assertEqual((viva.total_mark, viva.out_of), (Decimal("2"), Decimal("5")))
        self.assertFalse(InterviewEvaluation.objects.filter(candidate=self.none).exists())

        # the Admin's evaluation page shows the moved marks
        self.client.force_login(self.root)
        page = self.client.get(reverse("panel:evaluation_detail", args=[self.mim.pk]))
        self.assertContains(page, "Dress Up")
        self.assertContains(page, "Imported from the old interview sheet")

    def test_admin_sheet_with_imported_marks_can_be_completed(self):
        from .evaluation import sheet_rows

        self.run_migration()
        imported = InterviewEvaluation.objects.get(candidate=self.mim)
        rows = sheet_rows(imported, include_new=True)
        names = [label for _, label, _, _ in rows]
        self.assertEqual(names[:3], ["Dress Up", "Body Language", "Viva"])  # the old marks stay first
        self.assertEqual(len(rows), EvaluationCriterion.objects.filter(is_active=True).count())

        self.client.force_login(self.root)
        detail = reverse("panel:evaluation_detail", args=[self.mim.pk])
        data = {f"ev-mark_{key}": (str(mark) if mark is not None else "1") for key, _, _, mark in rows}
        data["ev-submit"] = "1"
        self.client.post(detail, data)
        imported.refresh_from_db()
        self.assertEqual(imported.scores.count(), len(rows))
        self.assertEqual(imported.total_mark, Decimal("12") + (len(rows) - 3))

    def test_running_twice_changes_nothing(self):
        self.run_migration()
        self.run_migration()
        self.assertEqual(InterviewEvaluation.objects.count(), 2)
        self.assertEqual(EvaluationCriterion.objects.filter(name="Dress Up").count(), 1)
        self.assertEqual(InterviewEvaluation.objects.get(candidate=self.mim).scores.count(), 3)

    def test_existing_criterion_with_same_name_is_reused(self):
        EvaluationCriterion.objects.create(name="viva", max_marks=Decimal("10"), order=1)
        self.run_migration()
        self.assertEqual(EvaluationCriterion.objects.filter(name__iexact="viva").count(), 1)


    def test_imported_sheet_does_not_change_the_main_status(self):
        self.run_migration()
        self.mim.refresh_from_db()
        self.assertEqual(self.mim.status, "pending")


class WholeMarksMigrationTests(TestCase):
    """0017: criteria become "out of 5", old scores are rescaled and rounded half up."""

    def test_scores_are_rescaled_to_whole_marks(self):
        from .models import EvaluationScore

        migration_0017 = importlib.import_module("recruitment.migrations.0017_one_status_whole_marks")
        EvaluationCriterion.objects.all().delete()
        root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        dept = Department.objects.create(name="Sales")
        circular = Circular.objects.create(title="C", reference_no="C-1")
        cand = Candidate.objects.create(circular=circular, department=dept, name="A", phone="01811000009")
        tech = EvaluationCriterion.objects.create(name="Tech", order=1)
        talk = EvaluationCriterion.objects.create(name="Talk", order=2)
        EvaluationCriterion.objects.filter(pk=tech.pk).update(max_marks=25)  # an old "out of 25" line
        evaluation = InterviewEvaluation.objects.create(candidate=cand, interviewer=root, department=dept)
        # write old style values straight to the table (the model itself refuses them now)
        EvaluationScore.objects.bulk_create([
            EvaluationScore(evaluation=evaluation, criterion=tech, criterion_name="Tech", max_marks=5, mark=0),
            EvaluationScore(evaluation=evaluation, criterion=talk, criterion_name="Talk", max_marks=5, mark=0),
        ])
        EvaluationScore.objects.filter(criterion=tech).update(max_marks=25, mark=18)  # 18/25 = 3.6 -> 4
        EvaluationScore.objects.filter(criterion=talk).update(max_marks=5, mark=3)
        Candidate.objects.filter(pk=cand.pk).update(written_mark=4)

        migration_0017.to_whole_marks(apps, None)

        self.assertEqual(set(EvaluationCriterion.objects.values_list("max_marks", flat=True)), {5})
        self.assertEqual({s.criterion_name: (s.mark, s.max_marks) for s in evaluation.scores.all()},
                         {"Tech": (4, 5), "Talk": (3, 5)})
        evaluation.refresh_from_db()
        self.assertEqual((evaluation.total_mark, evaluation.out_of), (Decimal("7"), Decimal("10")))


class WholeMarksValidationTests(TestCase):
    def test_parse_whole_mark(self):
        from .utils import parse_whole_mark

        for good, value in (("0", 0), ("5", 5), (" 3 ", 3), (3, 3), ("4.0", 4)):  # 4.0 = an Excel cell
            self.assertEqual(parse_whole_mark(good), value)
        for bad in ("4.5", "6", "-1", "x", 4.5, "", None):
            self.assertIsNone(parse_whole_mark(bad), bad)

    def test_score_model_rejects_decimals_and_out_of_range(self):
        from .models import EvaluationScore

        root = get_user_model().objects.create_superuser("root@iglweb.com", "root@iglweb.com", "StrongPass#2026")
        dept = Department.objects.create(name="Sales")
        circular = Circular.objects.create(title="C", reference_no="C-1")
        cand = Candidate.objects.create(circular=circular, department=dept, name="A", phone="01811000009")
        criterion = EvaluationCriterion.objects.create(name="Tech", order=1)
        evaluation = InterviewEvaluation.objects.create(candidate=cand, interviewer=root, department=dept)
        for bad in (Decimal("4.5"), 6, -1):
            with self.assertRaises(ValueError, msg=bad):
                EvaluationScore.objects.create(evaluation=evaluation, criterion=criterion, criterion_name="Tech", mark=bad)
        EvaluationScore.objects.create(evaluation=evaluation, criterion=criterion, criterion_name="Tech", mark=5)
