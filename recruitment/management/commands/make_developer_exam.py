"""Create the ready-made "Software Developer" written exam (5 questions, 10 minutes)."""
from django.core.management.base import BaseCommand, CommandError

from recruitment.models import Choice, Circular, Exam, Question

TITLE = "Software Developer - Written Test"

# (question, marks, options, index of the correct option)
MCQ = [
    ("Which HTTP method is normally used to send new data to a server?", 2,
     ["GET", "POST", "HEAD", "OPTIONS"], 2),
    ("In a database, what does an index mainly improve?", 2,
     ["Search (read) speed", "Write speed", "Backup size", "Password security"], 1),
    ("Which one of these is a version control system?", 2,
     ["Docker", "Nginx", "Git", "Redis"], 3),
]

WRITTEN = [
    ("Describe a project you have built. What was your role, which technologies did you use, "
     "and what problem did it solve?", 7),
    ("A page on a website has become very slow. Explain, step by step, how you would find the "
     "cause and fix it.", 7),
]


class Command(BaseCommand):
    help = "Create the Software Developer written exam (3 multiple choice + 2 written, 10 minutes, 20 marks)."

    def add_arguments(self, parser):
        parser.add_argument("--circular", help="Reference No. of the circular. Default: the newest active one.")
        parser.add_argument("--minutes", type=int, default=10, help="Time limit in minutes (default 10).")
        parser.add_argument("--replace", action="store_true", help="Delete the questions of an existing exam first.")

    def handle(self, *args, **options):
        if options["circular"]:
            circular = Circular.objects.filter(reference_no__iexact=options["circular"]).first()
            if circular is None:
                raise CommandError(f"No circular with reference no '{options['circular']}'.")
        else:
            circular = Circular.objects.filter(is_active=True).first()
            if circular is None:
                raise CommandError("No active circular found. Create a circular in the panel first.")

        exam, created = Exam.objects.get_or_create(
            circular=circular,
            title=TITLE,
            defaults={
                "instructions": "Answer all 5 questions within the time limit. "
                                "Written answers are checked by HR.",
                "duration_minutes": options["minutes"],
                "is_open": True,
                "show_score": False,
                "shuffle_questions": True,
                "shuffle_choices": True,
            },
        )
        if not created:
            exam.duration_minutes = options["minutes"]
            exam.save(update_fields=["duration_minutes"])
            if options["replace"]:
                exam.questions.all().delete()
            elif exam.questions.exists():
                self.stdout.write(self.style.WARNING("This exam already has questions - use --replace to rebuild."))
                self._report(exam)
                return

        order = 1
        for text, marks, options_list, correct in MCQ:
            question = Question.objects.create(exam=exam, order=order, text=text, kind=Question.Kind.MCQ, marks=marks)
            for i, option in enumerate(options_list, start=1):
                Choice.objects.create(question=question, order=i, text=option, is_correct=i == correct)
            order += 1
        for text, marks in WRITTEN:
            Question.objects.create(exam=exam, order=order, text=text, kind=Question.Kind.TEXT, marks=marks)
            order += 1

        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Rebuilt'}: {exam.title}"))
        self._report(exam)

    def _report(self, exam):
        self.stdout.write(f"Circular:    {exam.circular}")
        self.stdout.write(f"Questions:   {exam.questions.count()}  ({exam.total_marks:g} marks)")
        self.stdout.write(f"Time limit:  {exam.duration_minutes} minutes")
        self.stdout.write(f"Exam link:   https://careers.iglweb.com{exam.get_absolute_url()}")
