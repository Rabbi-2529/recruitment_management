from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.template.defaultfilters import filesizeformat
from django.utils import timezone
from django.utils.text import slugify

from .utils import normalize_phone


class CircularQuerySet(models.QuerySet):
    def open_for_registration(self):
        return self.filter(is_active=True, registration_open=True, departments__isnull=False).distinct()


class Circular(models.Model):
    """A job circular. It lists one or more departments; every candidate belongs to one circular + department."""

    title = models.CharField(max_length=200)
    reference_no = models.CharField("Reference No.", max_length=60, unique=True)
    published_on = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(
        default=True, help_text="Only active circulars are searchable on the landing page."
    )
    departments = models.ManyToManyField(
        "Department", related_name="circulars", blank=True, help_text="Departments this circular is hiring for."
    )
    interview_date = models.DateField(
        "Interview date", null=True, blank=True,
        help_text="Shown to candidates after registration and on the result page. Time slots are shared by HR.",
    )
    registration_open = models.BooleanField(
        default=True, help_text="Candidates can register online for this circular (only if it is also active)."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CircularQuerySet.as_manager()

    class Meta:
        ordering = ["-published_on", "-created_at"]

    def __str__(self):
        return f"{self.title} ({self.reference_no})"


class Department(models.Model):
    name = models.CharField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def get_or_create_by_name(cls, name):
        name = " ".join((name or "").split())
        existing = cls.objects.filter(name__iexact=name).first()
        if existing:
            return existing, False
        return cls.objects.create(name=name), True


MARK_MAX = Decimal("5")
MARK_VALIDATORS = [MinValueValidator(Decimal("0")), MaxValueValidator(MARK_MAX)]


# Marks are whole numbers only: 0, 1, 2, 3, 4, 5 (no .5).
WHOLE_MARK_VALIDATORS = [MinValueValidator(0), MaxValueValidator(5)]
MARK_CHOICES = [("", "Not taken")] + [(str(i), str(i)) for i in range(int(MARK_MAX) + 1)]
RATING_CHOICES = [("", "Not rated")] + [(str(i), f"{i} - {label}") for i, label in enumerate(
    ["Poor", "Fair", "Good", "Very good", "Excellent"], start=1
)]


def fmt_mark(value):
    """Decimal -> short string without trailing zeros ("4.50" -> "4.5"), empty for None."""
    return "" if value is None else f"{Decimal(value).normalize():f}"


def mark_field(label):
    return models.DecimalField(
        label, max_digits=4, decimal_places=2, null=True, blank=True, validators=MARK_VALIDATORS,
        help_text=f"Out of {MARK_MAX:g}. Leave empty if not taken.",
    )


CV_EXTENSIONS = ["pdf", "doc", "docx"]


def cv_max_bytes():
    return getattr(settings, "CV_MAX_BYTES", 1024 * 1024)


def validate_cv_size(value):
    """Keep CVs small - the limit is 1 MB (see CV_MAX_BYTES in settings)."""
    limit = cv_max_bytes()
    if value.size > limit:
        raise ValidationError(
            f"This file is {filesizeformat(value.size)}. The CV must not be larger than "
            f"{filesizeformat(limit)} - please upload a smaller file."
        )


def cv_upload_to(instance, filename):
    """cv/2026/09/abdul-karim-01711000001.pdf"""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "pdf"
    stem = slugify(f"{instance.name}-{instance.phone}") or "cv"
    return f"cv/{timezone.now():%Y/%m}/{stem}.{suffix}"


class Candidate(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SELECTED = "selected", "Selected"
        WAITING = "waiting", "Waiting"
        NOT_SELECTED = "not_selected", "Not Selected"

    class Source(models.TextChoices):
        ADMIN = "admin", "Added by HR"
        UPLOAD = "upload", "File upload"
        ONLINE = "online", "Online registration"

    # (model field, column label) - order used everywhere marks are shown
    # Dress Up / Body Language / Viva are now lines in Marking Criteria (each interviewer's own sheet).
    MARK_FIELDS = [
        ("written_mark", "Written"),
    ]

    circular = models.ForeignKey(Circular, on_delete=models.CASCADE, related_name="candidates")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="candidates")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, db_index=True)
    email = models.EmailField(blank=True)
    cv = models.FileField(
        "CV", upload_to=cv_upload_to, blank=True,
        validators=[FileExtensionValidator(CV_EXTENSIONS), validate_cv_size],
        help_text="PDF, DOC or DOCX - maximum 1 MB.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    education = models.TextField(blank=True, help_text="Degrees / institutions.")
    experience = models.TextField(blank=True, help_text="Work experience summary.")
    waiting_reason = models.TextField(
        "Waiting reason (HR only)", blank=True,
        help_text="Why this candidate is on the waiting list. Never shown to the candidate.",
    )
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.ADMIN)

    # no longer shown or counted - kept only so marks entered before are not lost
    dress_up_mark = mark_field("Dress up")
    body_language_mark = mark_field("Body language")
    viva_mark = mark_field("Viva")
    written_mark = models.PositiveSmallIntegerField(
        "Written", null=True, blank=True, validators=WHOLE_MARK_VALIDATORS,
        help_text="Whole number 0 to 5. Leave empty if not taken.",
    )
    # calculated from the marks that were taken (empty marks are ignored)
    total_marks = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, editable=False)
    marks_out_of = models.PositiveSmallIntegerField(default=0, editable=False)
    score_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, editable=False)

    # HR review
    rating = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)], help_text="1 to 5 stars."
    )
    expected_salary = models.PositiveIntegerField("Expected salary (BDT)", null=True, blank=True)
    available_date = models.DateField("Available date", null=True, blank=True, help_text="Date the candidate can join.")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["circular", "department", "phone"], name="unique_candidate_per_circular_department"
            )
        ]

    def __str__(self):
        return f"{self.name} - {self.phone}"

    def calculate_total(self):
        """Total only counts the parts the candidate actually took (non-empty marks)."""
        taken = [getattr(self, f) for f, _ in self.MARK_FIELDS if getattr(self, f) is not None]
        if not taken:
            self.total_marks, self.marks_out_of, self.score_percent = None, 0, None
            return
        self.total_marks = sum(taken, Decimal("0"))
        self.marks_out_of = int(MARK_MAX * len(taken))
        self.score_percent = (self.total_marks * 100 / self.marks_out_of).quantize(Decimal("0.01"))

    @property
    def cv_filename(self):
        return self.cv.name.rsplit("/", 1)[-1] if self.cv else ""

    @property
    def marks(self):
        """[(label, Decimal|None), ...] in the fixed column order - used by the Interview page."""
        return [(label, getattr(self, field)) for field, label in self.MARK_FIELDS]

    @property
    def written_attempts(self):
        """Submitted written exam answer sheets, newest first."""
        return self.exam_attempts.filter(submitted_at__isnull=False).select_related("exam").order_by("-submitted_at")

    @property
    def registration_no(self):
        return f"{self.circular.reference_no}-{self.pk:05d}"

    def save(self, *args, **kwargs):
        self.phone = normalize_phone(self.phone)
        self.calculate_total()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and any(f in update_fields for f, _ in self.MARK_FIELDS):
            kwargs["update_fields"] = set(update_fields) | {"total_marks", "marks_out_of", "score_percent"}
        super().save(*args, **kwargs)


# ----------------------------------------------------------------- written exam
def new_exam_token():
    import secrets

    return secrets.token_urlsafe(9)  # 12 characters, e.g. "K3mP9xQz7Ab1"


class Exam(models.Model):
    """A written exam for one circular. Candidates open it with a link and answer online."""

    circular = models.ForeignKey(Circular, on_delete=models.CASCADE, related_name="exams")
    departments = models.ManyToManyField(
        Department, blank=True, related_name="exams",
        help_text="Leave empty to allow every department of the circular.",
    )
    title = models.CharField(max_length=200)
    instructions = models.TextField(blank=True, help_text="Shown to the candidate before the questions start.")
    token = models.CharField(max_length=32, unique=True, default=new_exam_token, editable=False)
    duration_minutes = models.PositiveSmallIntegerField(
        "Time limit (minutes)", null=True, blank=True,
        help_text="The timer starts when the candidate enters their phone number, and the sheet closes itself "
                  "when the time is up. Leave empty for no time limit.",
    )
    questions_per_candidate = models.PositiveSmallIntegerField(
        "Questions per candidate", null=True, blank=True,
        help_text="Each candidate gets this many questions, picked at random from the list. Empty = all questions.",
    )
    shuffle_questions = models.BooleanField(
        "Shuffle question order", default=True, help_text="Every candidate sees the questions in a different order."
    )
    shuffle_choices = models.BooleanField(
        "Shuffle options", default=True, help_text="Multiple choice options are shown in a different order."
    )
    strict_mode = models.BooleanField(
        "Strict mode (anti-cheating)", default=True,
        help_text="Needs a time limit. The sheet is submitted at once if the candidate switches tab, minimises, "
                  "opens another window or presses Print Screen. Copy, paste and right-click are blocked. Answers "
                  "are saved automatically; the candidate submits, or the sheet is handed in when time is over.",
    )
    is_open = models.BooleanField("Exam open", default=True, help_text="Candidates can only answer while it is open.")
    show_score = models.BooleanField(
        "Show score to candidate", default=False, help_text="Only the auto-marked part is shown.",
    )
    pass_percent = models.PositiveSmallIntegerField(
        "Pass mark (%)", default=40, validators=[MaxValueValidator(100)],
        help_text="The written result shows Passed / Failed against this percentage.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("public:exam_start", args=[self.token])

    @property
    def total_marks(self):
        return sum((q.marks for q in self.questions.all()), Decimal("0"))

    def allowed_departments(self):
        """Departments a candidate may come from: the chosen ones, or all of the circular."""
        return self.departments.all() or self.circular.departments.all()

    def eligible_candidates(self):
        return Candidate.objects.filter(circular=self.circular, department__in=self.allowed_departments())

    def is_available(self):
        return self.is_open and self.circular.is_active and self.questions.exists()

    @property
    def is_strict(self):
        """Strict mode only works together with a time limit."""
        return self.strict_mode and bool(self.duration_minutes)

    def draw_questions(self):
        """Pick one candidate's random set of questions from the pool."""
        import random

        pool = list(self.questions.all())
        if self.questions_per_candidate and self.questions_per_candidate < len(pool):
            pool = random.sample(pool, self.questions_per_candidate)
        if self.shuffle_questions:
            random.shuffle(pool)
        else:
            pool.sort(key=lambda q: (q.order, q.pk))
        return [q.pk for q in pool]


class Question(models.Model):
    class Kind(models.TextChoices):
        MCQ = "mcq", "Multiple choice"
        TEXT = "text", "Written answer"

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="questions")
    order = models.PositiveSmallIntegerField(default=1)
    text = models.TextField("Question")
    kind = models.CharField("Answer type", max_length=10, choices=Kind.choices, default=Kind.MCQ)
    marks = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("1"),
                                validators=[MinValueValidator(Decimal("0.25"))])
    required = models.BooleanField(default=True, help_text="The candidate must answer this question.")

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"Q{self.order}. {self.text[:60]}"

    @property
    def is_mcq(self):
        return self.kind == self.Kind.MCQ

    @property
    def correct_choice(self):
        return self.choices.filter(is_correct=True).first()


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    order = models.PositiveSmallIntegerField(default=1)
    text = models.CharField(max_length=300)
    is_correct = models.BooleanField(default=False)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text


class SubmitReason(models.TextChoices):
    SUBMITTED = "submitted", "Submitted by candidate"
    TIME_UP = "time_up", "Time over"
    TAB_SWITCH = "tab_switch", "Left the exam page"
    WINDOW_BLUR = "window_blur", "Opened another window"
    SCREENSHOT = "screenshot", "Screenshot key pressed"
    PAGE_CLOSED = "page_closed", "Closed the page"


class ExamAttempt(models.Model):
    SubmitReason = SubmitReason
    CHEATING_REASONS = {SubmitReason.TAB_SWITCH, SubmitReason.WINDOW_BLUR, SubmitReason.SCREENSHOT}

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="attempts")
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="exam_attempts")
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    auto_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, editable=False)
    manual_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, editable=False)
    total_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, editable=False)
    out_of = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"), editable=False)
    # this candidate's random set: question ids in the order they are shown
    question_order = models.JSONField(default=list, editable=False)
    # answers saved automatically while the exam is running: {"<question id>": "<choice id or text>"}
    draft = models.JSONField(default=dict, editable=False)
    submit_reason = models.CharField(max_length=20, choices=SubmitReason.choices, blank=True, editable=False)
    # what happened during the exam, e.g. [{"event": "tab_switch", "at": "2026-09-21T10:15:03+06:00"}]
    events = models.JSONField(default=list, editable=False)

    class Meta:
        ordering = ["-submitted_at", "-started_at"]
        constraints = [
            models.UniqueConstraint(fields=["exam", "candidate"], name="unique_attempt_per_candidate")
        ]

    def __str__(self):
        return f"{self.candidate.name} - {self.exam.title}"

    @property
    def is_submitted(self):
        return self.submitted_at is not None

    @property
    def was_forced(self):
        """Submitted because of a rule break (tab switch, other window, screenshot key)."""
        return self.submit_reason in self.CHEATING_REASONS

    @property
    def is_expired(self):
        return self.deadline is not None and timezone.now() > self.deadline

    @property
    def seconds_left(self):
        if self.deadline is None:
            return None
        return max(0, int((self.deadline - timezone.now()).total_seconds()))

    def log(self, event, **extra):
        self.events = [*self.events, {"event": event, "at": timezone.now().isoformat(timespec="seconds"), **extra}]

    def questions(self):
        """The questions drawn for this candidate, in their order."""
        if not self.question_order:
            return list(self.exam.questions.prefetch_related("choices"))
        by_id = {q.pk: q for q in Question.objects.filter(pk__in=self.question_order).prefetch_related("choices")}
        return [by_id[pk] for pk in self.question_order if pk in by_id]

    def choices_for(self, question):
        """Options in this candidate's order (shuffled per candidate, but stable across refreshes)."""
        import random

        options = list(question.choices.all())
        if self.exam.shuffle_choices:
            random.Random(f"{self.pk}-{question.pk}").shuffle(options)
        return options

    @property
    def needs_marking(self):
        return self.answers.filter(question__kind=Question.Kind.TEXT, awarded=None).exists()

    @property
    def percent(self):
        if not self.total_score or not self.out_of:
            return None
        return (self.total_score * 100 / self.out_of).quantize(Decimal("0.01"))

    @property
    def result_label(self):
        """Passed / Failed against the exam's pass mark, or "Marking pending"."""
        if not self.is_submitted:
            return "Not submitted"
        if self.needs_marking:
            return "Marking pending"
        return "Passed" if (self.percent or 0) >= self.exam.pass_percent else "Failed"

    @property
    def deadline(self):
        if not self.exam.duration_minutes:
            return None
        return self.started_at + timezone.timedelta(minutes=self.exam.duration_minutes)

    def recalculate(self):
        """Auto marks come from the MCQs, manual marks from the written answers HR has marked."""
        answers = list(self.answers.select_related("question"))
        auto = sum((a.awarded or Decimal("0")) for a in answers if a.question.kind == Question.Kind.MCQ)
        manual_marked = [a.awarded for a in answers if a.question.kind == Question.Kind.TEXT and a.awarded is not None]
        self.auto_score = auto
        self.manual_score = sum(manual_marked, Decimal("0")) if manual_marked else None
        self.total_score = auto + sum(manual_marked, Decimal("0"))
        # out of the marks of this candidate's own set
        self.out_of = sum((q.marks for q in self.questions()), Decimal("0"))
        self.save(update_fields=["auto_score", "manual_score", "total_score", "out_of"])

    def apply_to_written_mark(self):
        """Copy the exam result into the candidate's Written mark (out of 5)."""
        percent = self.percent
        if percent is None:
            return None
        from decimal import ROUND_HALF_UP

        mark = int((percent * MARK_MAX / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        self.candidate.written_mark = min(max(mark, 0), int(MARK_MAX))
        self.candidate.save(update_fields=["written_mark", "updated_at"])
        return mark


class Answer(models.Model):
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    choice = models.ForeignKey(Choice, on_delete=models.SET_NULL, null=True, blank=True)
    choice_text = models.CharField(max_length=300, blank=True)  # kept even if the choice is edited later
    text_answer = models.TextField(blank=True)
    awarded = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["question__order", "question_id"]
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"], name="unique_answer_per_question")
        ]

    def __str__(self):
        return f"{self.attempt_id} - Q{self.question.order}"

    @property
    def is_correct(self):
        if self.question.kind != Question.Kind.MCQ or self.choice_id is None:
            return None
        return bool(self.choice and self.choice.is_correct)



# ----------------------------------------------------------------- interviewers
class InterviewerProfile(models.Model):
    """Makes a normal Django user an Interviewer. Admins are superusers and need no profile."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="interviewer_profile")
    departments = models.ManyToManyField(
        Department, through="InterviewerDepartment", related_name="interviewers", blank=True,
    )
    phone = models.CharField(max_length=20, blank=True)
    designation = models.CharField(max_length=120, blank=True)
    can_edit_written_marks = models.BooleanField(
        "May mark written answers", default=False,
        help_text="Allow this interviewer to mark / change written exam answers.",
    )
    can_edit_submitted = models.BooleanField(
        "May edit after submitting", default=False,
        help_text="Allow changes to an evaluation after it was submitted (until the Admin makes the final decision).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__first_name", "user__email"]

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.user.get_full_name() or self.user.email or self.user.username


class InterviewerDepartment(models.Model):
    interviewer = models.ForeignKey(InterviewerProfile, on_delete=models.CASCADE, related_name="department_links")
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="interviewer_links")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["interviewer", "department"], name="unique_interviewer_department")
        ]

    def __str__(self):
        return f"{self.interviewer} - {self.department}"


# ----------------------------------------------------------------- interview evaluation
class EvaluationCriterion(models.Model):
    """One line on the interview marking sheet, e.g. "Technical Knowledge - 25 marks"."""

    name = models.CharField(max_length=120)
    # every question is marked 0-5 in whole numbers
    max_marks = models.PositiveSmallIntegerField(default=5, validators=[MinValueValidator(1), MaxValueValidator(5)])
    order = models.PositiveSmallIntegerField(default=1)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, help_text="Inactive criteria are not on new marking sheets.")

    class Meta:
        ordering = ["order", "id"]
        verbose_name_plural = "evaluation criteria"

    def __str__(self):
        return f"{self.name} ({self.max_marks:g})"


class InterviewSetting(models.Model):
    """Single row: how the final combined result is calculated."""

    show_combined = models.BooleanField("Show a combined final result", default=True)
    written_weight = models.PositiveSmallIntegerField(
        "Written exam weight (%)", default=40, validators=[MaxValueValidator(100)],
    )
    interview_weight = models.PositiveSmallIntegerField(
        "Interview weight (%)", default=60, validators=[MaxValueValidator(100)],
    )

    def __str__(self):
        return "Interview settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def combined(self, written_percent, interview_percent):
        """Weighted final % - uses whatever part exists when only one is available."""
        if written_percent is None and interview_percent is None:
            return None
        if written_percent is None:
            return interview_percent
        if interview_percent is None:
            return written_percent
        total_weight = (self.written_weight + self.interview_weight) or 1
        value = (Decimal(written_percent) * self.written_weight + Decimal(interview_percent) * self.interview_weight)
        return (value / total_weight).quantize(Decimal("0.01"))


class InterviewEvaluation(models.Model):
    """One interviewer's own marking sheet for one candidate. Never shared or overwritten."""

    class Recommendation(models.TextChoices):
        STRONGLY_RECOMMEND = "strongly_recommend", "Strongly recommend"
        RECOMMEND = "recommend", "Recommend"
        NEUTRAL = "neutral", "Neutral"
        NOT_RECOMMEND = "not_recommend", "Not recommended"

    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="evaluations")
    interviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="evaluations")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="evaluations")
    total_mark = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"), editable=False)
    out_of = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"), editable=False)
    comments = models.TextField(blank=True)
    strengths = models.TextField(blank=True)
    weaknesses = models.TextField(blank=True)
    recommendation = models.CharField(max_length=24, choices=Recommendation.choices, blank=True)
    remarks = models.TextField("Remarks", blank=True)
    is_submitted = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["candidate", "interviewer__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["candidate", "interviewer"], name="unique_evaluation_per_interviewer")
        ]

    def __str__(self):
        return f"{self.candidate.name} - {self.interviewer}"

    @property
    def interviewer_name(self):
        return self.interviewer.get_full_name() or self.interviewer.email or self.interviewer.username

    @property
    def percent(self):
        if not self.out_of:
            return None
        return (self.total_mark * 100 / self.out_of).quantize(Decimal("0.01"))

    def recalculate(self):
        scores = list(self.scores.all())
        self.total_mark = sum((sc.mark for sc in scores if sc.mark is not None), Decimal("0"))
        self.out_of = sum((sc.max_marks for sc in scores), Decimal("0"))
        self.save(update_fields=["total_mark", "out_of", "updated_at"])


class EvaluationScore(models.Model):
    evaluation = models.ForeignKey(InterviewEvaluation, on_delete=models.CASCADE, related_name="scores")
    criterion = models.ForeignKey(EvaluationCriterion, on_delete=models.SET_NULL, null=True, blank=True)
    # copied so an old sheet stays correct after HR renames a criterion or changes its marks
    criterion_name = models.CharField(max_length=120)
    max_marks = models.PositiveSmallIntegerField(default=5)
    mark = models.PositiveSmallIntegerField(null=True, blank=True, validators=WHOLE_MARK_VALIDATORS)
    order = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["evaluation", "criterion"], name="unique_score_per_criterion")
        ]

    def __str__(self):
        return f"{self.criterion_name}: {self.mark}/{self.max_marks}"

    def save(self, *args, **kwargs):
        # backend guard: only whole numbers 0 .. max (5) can ever be stored
        if self.mark is not None:
            if isinstance(self.mark, bool) or int(self.mark) != self.mark:
                raise ValueError("Marks must be whole numbers (0, 1, 2, 3, 4, 5).")
            self.mark = int(self.mark)
            if not 0 <= self.mark <= (self.max_marks or 5):
                raise ValueError(f"Mark must be between 0 and {self.max_marks or 5}.")
        super().save(*args, **kwargs)


# ----------------------------------------------------------------- SMS
DEFAULT_SMS_URL = "http://sms.iglweb.com/api/v1/send"


class SmsGateway(models.Model):
    """SMS API account. The API key never leaves the server."""

    name = models.CharField(max_length=100, default="IGL SMS")
    base_url = models.URLField("API URL", default=DEFAULT_SMS_URL)
    api_key = models.CharField(max_length=255)
    success_codes = models.CharField(
        "Success codes", max_length=200, default="445000",
        help_text="Codes in the API reply that mean the SMS was accepted, comma separated (IGL SMS: 445000).",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    @property
    def success_code_list(self):
        return [code.strip() for code in (self.success_codes or "").split(",") if code.strip()]

    def __str__(self):
        return self.name

    @property
    def masked_key(self):
        key = self.api_key or ""
        return (key[:3] + "•" * 8 + key[-3:]) if len(key) > 8 else "•" * 8


class SmsSenderId(models.Model):
    gateway = models.ForeignKey(SmsGateway, on_delete=models.CASCADE, related_name="sender_ids")
    sender_id = models.CharField("Approved Sender ID", max_length=30)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_default", "sender_id"]
        constraints = [
            models.UniqueConstraint(fields=["gateway", "sender_id"], name="unique_sender_per_gateway")
        ]

    def __str__(self):
        return self.sender_id


# ----------------------------------------------------------------- interview call
def interview_call_upload_to(instance, filename):
    return f"interview_calls/{timezone.now():%Y/%m}/{filename}"


DEFAULT_SMS_TEMPLATE = (
    "Dear {candidate_name},\n"
    "You are invited for an interview at IGL Group.\n\n"
    "IGL Group"
)


class InterviewCall(models.Model):
    """An uploaded list of people called for interview (e.g. a BD Jobs applicant list) - only for SMS."""

    class Stage(models.TextChoices):
        MAPPING = "mapping", "Map columns"
        READY = "ready", "Ready"

    title = models.CharField(max_length=200)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="interview_calls")
    # older calls may have these; new calls only carry the message the Admin writes
    interview_date = models.DateField(null=True, blank=True)
    interview_time = models.TimeField(null=True, blank=True)
    venue = models.CharField(max_length=255, blank=True)
    information = models.TextField("Interview information", blank=True)
    gateway = models.ForeignKey(SmsGateway, on_delete=models.SET_NULL, null=True, blank=True, related_name="calls")
    sender = models.ForeignKey(SmsSenderId, on_delete=models.SET_NULL, null=True, blank=True, related_name="calls")
    message_template = models.TextField("SMS message", default=DEFAULT_SMS_TEMPLATE)
    uploaded_file = models.FileField(upload_to=interview_call_upload_to)
    original_filename = models.CharField(max_length=255, blank=True)
    headers = models.JSONField(default=list, editable=False)
    column_map = models.JSONField(default=dict, editable=False)
    stage = models.CharField(max_length=10, choices=Stage.choices, default=Stage.MAPPING)
    total_rows = models.PositiveIntegerField(default=0, editable=False)
    duplicate_rows = models.PositiveIntegerField(default=0, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def sms_counts(self):
        people = self.people.all()
        return {
            "total": people.count(),
            "valid": people.filter(is_valid=True).count(),
            "invalid": people.filter(is_valid=False).count(),
            "sent": people.filter(sms_status=InterviewCallCandidate.SmsStatus.SENT).count(),
            "failed": people.filter(sms_status=InterviewCallCandidate.SmsStatus.FAILED).count(),
            "queued": people.filter(sms_status=InterviewCallCandidate.SmsStatus.QUEUED).count(),
            "not_sent": people.filter(sms_status=InterviewCallCandidate.SmsStatus.NOT_SENT, is_valid=True).count(),
        }


class InterviewCallCandidate(models.Model):
    """A person from the uploaded file. NOT a main Candidate - it only exists for this interview call."""

    class SmsStatus(models.TextChoices):
        NOT_SENT = "not_sent", "Not sent"
        QUEUED = "queued", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        INVALID = "invalid", "No valid phone"

    interview_call = models.ForeignKey(InterviewCall, on_delete=models.CASCADE, related_name="people")
    row_number = models.PositiveIntegerField(default=0)
    candidate_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True, help_text="Cleaned 01XXXXXXXXX - one per candidate.")
    phone_raw = models.CharField(max_length=255, blank=True)
    email = models.CharField(max_length=254, blank=True)
    candidate_ref = models.CharField("Candidate ID", max_length=100, blank=True)
    department_text = models.CharField(max_length=200, blank=True)
    additional_data = models.JSONField(default=dict, blank=True)
    # a registered candidate with the same phone (for reference only - nothing is copied)
    matched_candidate = models.ForeignKey(
        Candidate, on_delete=models.SET_NULL, null=True, blank=True, related_name="interview_calls",
    )
    is_valid = models.BooleanField(default=True)
    error = models.CharField(max_length=255, blank=True)
    sms_status = models.CharField(max_length=10, choices=SmsStatus.choices, default=SmsStatus.NOT_SENT, db_index=True)
    sms_response = models.TextField(blank=True)
    sms_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["row_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["interview_call", "phone"], condition=~models.Q(phone=""), name="unique_phone_per_call",
            )
        ]

    def __str__(self):
        return f"{self.candidate_name} - {self.phone}"


class SmsLog(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped (already sent)"

    interview_call = models.ForeignKey(InterviewCall, on_delete=models.CASCADE, related_name="sms_logs")
    recipient = models.ForeignKey(InterviewCallCandidate, on_delete=models.CASCADE, related_name="sms_logs")
    phone = models.CharField(max_length=20)
    sender_id = models.CharField(max_length=30)
    message = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    api_response = models.TextField(blank=True)
    error = models.CharField(max_length=500, blank=True)
    is_retry = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.phone} - {self.status}"


class CandidateReference(models.Model):
    """A reference the candidate gives when registering: who can vouch for them."""

    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="references")
    name = models.CharField(max_length=120)
    organisation = models.CharField("Company / University", max_length=160, blank=True)
    designation = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.name} ({self.organisation})" if self.organisation else self.name
