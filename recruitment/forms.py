from decimal import Decimal

from django import forms
from django.template.defaultfilters import filesizeformat

from .models import (
    MARK_CHOICES,
    RATING_CHOICES,
    Candidate,
    CandidateReference,
    Choice,
    Circular,
    Department,
    Exam,
    Question,
    cv_max_bytes,
    fmt_mark,
)
from .utils import PHONE_ERROR, validate_bd_phone


class StyledFormMixin:
    """Adds CSS classes to every widget so templates stay simple."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "checkbox")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "input select")
                widget.attrs.setdefault("data-searchable", "")
            else:
                widget.attrs.setdefault("class", "input")
            if "data-bd-phone" in widget.attrs:
                widget.attrs["maxlength"] = "13"  # 8801XXXXXXXXX


def clean_phone_value(value):
    phone = validate_bd_phone(value)
    if phone is None:
        raise forms.ValidationError(PHONE_ERROR)
    return phone


CV_ATTRS = {"accept": ".pdf,.doc,.docx", "class": "input file-input"}
CV_HELP = "PDF, DOC or DOCX. Maximum size 1 MB."


class CvField(forms.FileField):
    """File input used for CVs - the size/type limits live on the model validators."""

    def __init__(self, **kwargs):
        kwargs.setdefault("label", "Upload CV")
        kwargs.setdefault("help_text", CV_HELP)
        kwargs.setdefault("widget", forms.ClearableFileInput(attrs=CV_ATTRS))
        super().__init__(**kwargs)

    def clean(self, data, initial=None):
        file = super().clean(data, initial)
        # checked here too so the candidate sees the message before the upload is stored
        if file and getattr(file, "size", 0) > cv_max_bytes():
            raise forms.ValidationError(
                f"This file is {filesizeformat(file.size)}. The CV must not be larger than "
                f"{filesizeformat(cv_max_bytes())} - please upload a smaller file."
            )
        return file


PHONE_ATTRS = {
    "placeholder": "01XXXXXXXXX or 8801XXXXXXXXX",
    "inputmode": "numeric",
    "autocomplete": "tel",
    "maxlength": "13",
    "data-bd-phone": "",
}


# ---------------------------------------------------------------- public
class PublicSearchForm(StyledFormMixin, forms.Form):
    phone = forms.CharField(
        label="Phone Number",
        max_length=20,
        widget=forms.TextInput(attrs=PHONE_ATTRS),
    )
    department = forms.ModelChoiceField(
        label="Department", queryset=Department.objects.none(), empty_label="Select department"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = Department.objects.filter(
            candidates__circular__is_active=True
        ).distinct()

    def clean_phone(self):
        return clean_phone_value(self.cleaned_data["phone"])


class ReferenceForm(StyledFormMixin, forms.ModelForm):
    """One reference row on the registration page (the + button adds more)."""

    class Meta:
        model = CandidateReference
        fields = ["name", "organisation", "designation", "phone", "email"]
        labels = {
            "name": "Reference name",
            "organisation": "Company / University",
            "designation": "Designation",
            "phone": "Phone number",
            "email": "Email (optional)",
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Name of the person"}),
            "organisation": forms.TextInput(attrs={"placeholder": "Where they work or study"}),
            "designation": forms.TextInput(attrs={"placeholder": "e.g. Manager, Lecturer"}),
            "phone": forms.TextInput(attrs=PHONE_ATTRS),
            "email": forms.EmailInput(attrs={"placeholder": "name@example.com"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # a row that is added must carry a name and a phone number; a row left completely
        # empty is simply dropped (Django skips unchanged extra forms)
        self.fields["name"].required = True
        self.fields["phone"].required = True

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def clean_phone(self):
        return clean_phone_value(self.cleaned_data["phone"])


class BaseReferenceFormSet(forms.BaseInlineFormSet):
    """Empty rows are simply ignored, so a candidate may give none, one or many."""

    def clean(self):
        super().clean()
        self.kept = [f for f in self.forms if f.cleaned_data.get("name") and not f.cleaned_data.get("DELETE")]

    def save_for(self, candidate):
        for order, form in enumerate(getattr(self, "kept", []), start=1):
            reference = form.save(commit=False)
            reference.candidate = candidate
            reference.order = order
            reference.save()


ReferenceFormSet = forms.inlineformset_factory(
    Candidate, CandidateReference, form=ReferenceForm, formset=BaseReferenceFormSet,
    extra=0, can_delete=False, max_num=10, validate_max=True,  # rows appear only when "+" is clicked
)


class RegistrationForm(StyledFormMixin, forms.ModelForm):
    """Public online registration: circular -> department -> candidate details."""

    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1"}))

    # Optional for candidates: set required=True here to make a CV compulsory again.
    cv = CvField(required=False, label="Upload CV (optional)")

    class Meta:
        model = Candidate
        fields = ["circular", "department", "name", "phone", "email", "cv"]
        labels = {"name": "Full Name", "phone": "Phone Number", "email": "Email (optional)"}
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Your full name", "autocomplete": "name"}),
            "phone": forms.TextInput(attrs=PHONE_ATTRS),
            "email": forms.EmailInput(attrs={"placeholder": "you@example.com", "autocomplete": "email"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["circular"].queryset = Circular.objects.open_for_registration()
        self.fields["circular"].empty_label = "Select circular"
        self.fields["department"].queryset = Department.objects.filter(
            circulars__in=self.fields["circular"].queryset
        ).distinct()
        self.fields["department"].empty_label = "Select department"
        self.fields["department"].widget.attrs["data-depends-on"] = "circular"
        self.fields["website"].widget.attrs["class"] = "hp-field"

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def clean_phone(self):
        return clean_phone_value(self.cleaned_data["phone"])

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):  # hidden field only bots fill in
            raise forms.ValidationError("Registration could not be completed.")
        circular, department = cleaned.get("circular"), cleaned.get("department")
        if circular and department and not circular.departments.filter(pk=department.pk).exists():
            self.add_error("department", "Please choose a department of the selected circular.")
        return cleaned

    def validate_unique(self):
        # show a friendly message instead of Django's default constraint error
        c = self.cleaned_data
        if c.get("circular") and c.get("department") and c.get("phone") and Candidate.objects.filter(
            circular=c["circular"], department=c["department"], phone=c["phone"]
        ).exists():
            self.add_error("phone", "This phone number is already registered for this department.")


# ---------------------------------------------------------------- panel
class PanelLoginForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "root@iglweb.com", "autofocus": True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Password"}))


class CircularForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Circular
        fields = [
            "title", "reference_no", "published_on", "interview_date", "departments", "description",
            "is_active", "registration_open",
        ]
        widgets = {
            "published_on": forms.DateInput(
                format="%Y-%m-%d", attrs={"data-datepicker": "", "placeholder": "Select date", "autocomplete": "off"}
            ),
            "interview_date": forms.DateInput(
                format="%Y-%m-%d", attrs={"data-datepicker": "", "placeholder": "Select date", "autocomplete": "off"}
            ),
            "description": forms.Textarea(attrs={"rows": 3}),
            "departments": forms.SelectMultiple(attrs={"data-placeholder": "Select departments"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["departments"].required = True
        self.fields["departments"].help_text = (
            "Departments this circular is hiring for. Add new ones on the Departments page."
        )


class DepartmentForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "e.g. Software Engineering"})}

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        qs = Department.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("This department already exists.")
        return name


class CandidateForm(StyledFormMixin, forms.ModelForm):
    cv = CvField(required=False)

    class Meta:
        model = Candidate
        fields = [
            "circular", "department", "name", "phone", "email", "cv", "status",
            "written_mark",
            "rating", "expected_salary", "available_date", "waiting_reason", "note",
            "education", "experience",
        ]
        widgets = {
            "phone": forms.TextInput(attrs=PHONE_ATTRS),
            "education": forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. BSc in CSE, Daffodil International University"}),
            "experience": forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. 2 years - Junior Developer at ..."}),
            "rating": forms.NumberInput(attrs={"min": "1", "max": "5", "placeholder": "1 - 5"}),
            "expected_salary": forms.NumberInput(attrs={"min": "0", "placeholder": "e.g. 30000"}),
            "available_date": forms.DateInput(
                format="%Y-%m-%d", attrs={"data-datepicker": "", "placeholder": "Select date", "autocomplete": "off"}
            ),
            "note": forms.Textarea(attrs={"rows": 3}),
            "waiting_reason": forms.Textarea(attrs={"rows": 2, "placeholder": "Only HR can see this"}),
            **{
                f: forms.NumberInput(attrs={"min": "0", "max": "5", "step": "1", "inputmode": "numeric",
                                            "placeholder": "Not taken"})
                for f in ["written_mark"]
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["circular"].empty_label = "Select circular"
        self.fields["department"].empty_label = "Select department"
        self.fields["department"].help_text = "Only the departments of the selected circular are listed."
        self.fields["department"].widget.attrs["data-depends-on"] = "circular"

    def clean_phone(self):
        return clean_phone_value(self.cleaned_data["phone"])

    def clean(self):
        cleaned = super().clean()
        circular, department = cleaned.get("circular"), cleaned.get("department")
        if circular and department:
            allowed = circular.departments.all()
            if allowed.exists() and department not in allowed:
                self.add_error("department", f"“{department}” is not a department of this circular.")
        return cleaned


class ReviewForm(forms.ModelForm):
    """HR review popup on the candidate list."""

    expected_salary = forms.CharField(required=False)  # accepts "30,000"

    class Meta:
        model = Candidate
        fields = ["rating", "expected_salary", "available_date", "note"]

    def clean_expected_salary(self):
        raw = (self.cleaned_data.get("expected_salary") or "").replace(",", "").replace("৳", "").strip()
        if not raw:
            return None
        if not raw.isdigit():
            raise forms.ValidationError("Enter the salary as a whole number, e.g. 30000.")
        value = int(raw)
        if value > 100_000_000:
            raise forms.ValidationError("Salary looks too large.")
        return value


class CandidateUploadForm(StyledFormMixin, forms.Form):
    circular = forms.ModelChoiceField(queryset=Circular.objects.all(), empty_label="Select circular")
    file = forms.FileField(
        label="Candidate file (.csv or .xlsx)",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.xlsx"}),
    )
    default_status = forms.ChoiceField(
        choices=Candidate.Status.choices,
        initial=Candidate.Status.PENDING,
        help_text="Used when the file has no Status column (or the cell is empty).",
    )
    update_existing = forms.BooleanField(
        required=False,
        initial=True,
        label="Update existing candidates (same circular + department + phone)",
    )


class BulkActionForm(forms.Form):
    ACTIONS = [
        ("selected", "Mark as Selected"),
        ("waiting", "Mark as Waiting"),
        ("not_selected", "Mark as Not Selected"),
        ("pending", "Mark as Pending"),
        ("delete", "Delete"),
    ]
    action = forms.ChoiceField(choices=ACTIONS)
    ids = forms.CharField()


# ---------------------------------------------------------------- interview
class InterviewSearchForm(forms.Form):
    """Find a candidate by phone number or email on the Interview page."""

    q = forms.CharField(
        label="Phone number or email",
        max_length=120,
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "placeholder": "01711000001  or  candidate@example.com",
                "autocomplete": "off",
                "autofocus": True,
            }
        ),
    )

    def clean_q(self):
        return self.cleaned_data["q"].strip()


class InterviewForm(StyledFormMixin, forms.ModelForm):
    """Interview sheet: the four marks as 0-5 dropdowns, plus the HR review and the result."""

    # plain file input: the CV already on file is shown (with a View button) above the form
    cv = CvField(required=False, label="Replace / attach CV", widget=forms.FileInput(attrs=CV_ATTRS))
    expected_salary = forms.CharField(required=False, label="Expected salary (BDT)")  # accepts "30,000"

    class Meta:
        model = Candidate
        fields = [
            "written_mark",
            "rating", "status", "waiting_reason", "expected_salary", "available_date", "note", "cv",
        ]
        widgets = {
            "status": forms.Select(),
            "available_date": forms.DateInput(
                format="%Y-%m-%d", attrs={"data-datepicker": "", "placeholder": "Select date", "autocomplete": "off"}
            ),
            "note": forms.Textarea(attrs={"rows": 4, "placeholder": "Interview notes, strengths, concerns…"}),
            "waiting_reason": forms.Textarea(attrs={"rows": 2, "placeholder": "Why waiting? Only HR can see this."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field, label in Candidate.MARK_FIELDS:
            self.fields[field] = forms.TypedChoiceField(
                label=label, choices=MARK_CHOICES, coerce=int, required=False, empty_value=None,
                widget=forms.Select(attrs={"class": "input mark-select", "data-mark": field}),
                help_text="Out of 5",
            )
            value = getattr(self.instance, field, None)
            self.initial[field] = "" if value is None else str(int(value))
        self.fields["rating"] = forms.TypedChoiceField(
            label="Overall review", choices=RATING_CHOICES, coerce=int, required=False, empty_value=None,
            widget=forms.Select(attrs={"class": "input"}),
        )
        self.initial["rating"] = "" if self.instance.rating is None else str(self.instance.rating)
        self.fields["status"].widget.attrs["class"] = "input"
        self.fields["status"].widget.attrs.pop("data-searchable", None)  # plain dropdown, only 3 options
        self.fields["expected_salary"].widget.attrs.update(
            {"class": "input", "inputmode": "numeric", "placeholder": "e.g. 30,000", "autocomplete": "off"}
        )
        if self.instance.expected_salary is not None:
            self.initial["expected_salary"] = f"{self.instance.expected_salary:,}"

    def clean_expected_salary(self):
        raw = (self.cleaned_data.get("expected_salary") or "").replace(",", "").replace("৳", "").strip()
        if not raw:
            return None
        if not raw.isdigit():
            raise forms.ValidationError("Enter the salary as a whole number, e.g. 30000.")
        value = int(raw)
        if value > 100_000_000:
            raise forms.ValidationError("Salary looks too large.")
        return value


# ---------------------------------------------------------------- written exam (panel)
class ExamForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Exam
        fields = [
            "title", "circular", "departments", "instructions", "duration_minutes",
            "questions_per_candidate", "shuffle_questions", "shuffle_choices", "strict_mode", "is_open", "show_score",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Written Test - Junior Developer"}),
            "instructions": forms.Textarea(attrs={"rows": 3, "placeholder": "Read every question carefully…"}),
            "departments": forms.SelectMultiple(attrs={"data-placeholder": "All departments of the circular"}),
            "duration_minutes": forms.NumberInput(attrs={"min": "1", "max": "600", "placeholder": "No time limit"}),
            "questions_per_candidate": forms.NumberInput(attrs={"min": "1", "placeholder": "All questions"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["circular"].empty_label = "Select circular"
        self.fields["departments"].required = False
        self.fields["departments"].widget.attrs["data-depends-on"] = "circular"

    def clean(self):
        cleaned = super().clean()
        circular, departments = cleaned.get("circular"), cleaned.get("departments")
        if circular and departments:
            extra = [d.name for d in departments if not circular.departments.filter(pk=d.pk).exists()]
            if extra:
                self.add_error("departments", f"Not a department of this circular: {', '.join(extra)}.")
        if cleaned.get("strict_mode") and not cleaned.get("duration_minutes"):
            self.add_error("duration_minutes", "Strict mode needs a time limit - the sheet is handed in when it ends.")
        return cleaned


CHOICE_COUNT = 6


class QuestionForm(StyledFormMixin, forms.ModelForm):
    """One question. For multiple choice, up to 6 options with one marked correct."""

    class Meta:
        model = Question
        fields = ["text", "kind", "marks", "required", "order"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3, "placeholder": "Type the question…"}),
            "marks": forms.NumberInput(attrs={"min": "0.25", "step": "0.25"}),
            "order": forms.NumberInput(attrs={"min": "1"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].widget.attrs.pop("data-searchable", None)  # only two options
        self.fields["kind"].widget.attrs["data-question-kind"] = ""
        existing = list(self.instance.choices.all()) if self.instance.pk else []
        for i in range(1, CHOICE_COUNT + 1):
            choice = existing[i - 1] if i <= len(existing) else None
            self.fields[f"choice_{i}"] = forms.CharField(
                label=f"Option {i}", max_length=300, required=False,
                widget=forms.TextInput(attrs={"class": "input", "placeholder": "Leave empty to skip"}),
                initial=choice.text if choice else "",
            )
        correct = next((i for i, c in enumerate(existing, start=1) if c.is_correct), None)
        self.fields["correct_choice"] = forms.ChoiceField(
            label="Correct option", required=False,
            choices=[(str(i), str(i)) for i in range(1, CHOICE_COUNT + 1)],
            widget=forms.RadioSelect(attrs={"class": "choice-radio"}),
            initial=str(correct) if correct else None,
        )

    def choice_values(self):
        return [(i, (self.cleaned_data.get(f"choice_{i}") or "").strip()) for i in range(1, CHOICE_COUNT + 1)]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("kind") != Question.Kind.MCQ:
            return cleaned
        filled = [i for i, text in self.choice_values() if text]
        if len(filled) < 2:
            self.add_error("choice_1", "A multiple choice question needs at least 2 options.")
        correct = cleaned.get("correct_choice")
        if not correct:
            self.add_error("correct_choice", "Mark which option is correct.")
        elif int(correct) not in filled:
            self.add_error("correct_choice", "The correct option is empty.")
        return cleaned

    def save(self, commit=True):
        question = super().save(commit)
        question.choices.all().delete()
        if question.kind == Question.Kind.MCQ:
            correct = int(self.cleaned_data.get("correct_choice") or 0)
            for order, (index, text) in enumerate(((i, t) for i, t in self.choice_values() if t), start=1):
                Choice.objects.create(question=question, order=order, text=text, is_correct=index == correct)
        return question


# ---------------------------------------------------------------- written exam (public)
class ExamIdentifyForm(forms.Form):
    """The candidate proves they registered, with the phone number or email they used."""

    identity = forms.CharField(
        label="Phone number or email",
        max_length=120,
        widget=forms.TextInput(attrs={
            "class": "input", "placeholder": "01XXXXXXXXX  or  you@example.com",
            "autocomplete": "off", "autofocus": True,
        }),
    )

    def clean_identity(self):
        return self.cleaned_data["identity"].strip()


class ExamAnswerForm(forms.Form):
    """Built from this candidate's own set of questions: radios for MCQ, a text box for written answers."""

    def __init__(self, *args, questions=None, choices_for=None, draft=None, **kwargs):
        if draft and not args and "data" not in kwargs:
            kwargs["initial"] = {f"q{pk}": value for pk, value in draft.items()}  # answers saved before a refresh
        super().__init__(*args, **kwargs)
        self.questions = list(questions or [])
        self.choice_objects = {}
        for question in self.questions:
            name = f"q{question.pk}"
            if question.is_mcq:
                options = list(choices_for(question)) if choices_for else list(question.choices.all())
                self.choice_objects.update({str(option.pk): option for option in options})
                self.fields[name] = forms.ChoiceField(
                    choices=[(str(option.pk), option.text) for option in options],
                    required=question.required, label=question.text,
                    widget=forms.RadioSelect(attrs={"class": "choice-radio"}),
                )
            else:
                self.fields[name] = forms.CharField(
                    required=question.required, label=question.text,
                    widget=forms.Textarea(attrs={"class": "input", "rows": 5, "placeholder": "Write your answer here…"}),
                )
            self.fields[name].error_messages["required"] = "Please answer this question."

    def chosen_choice(self, question):
        """The Choice object the candidate picked, or None."""
        return self.choice_objects.get(self.cleaned_data.get(f"q{question.pk}") or "")

    def question_fields(self):
        return [(question, self[f"q{question.pk}"]) for question in self.questions]


# ================================================================ interview call, SMS, interviewers
from django.contrib.auth import get_user_model  # noqa: E402

from .interview_call_import import SUPPORTED_EXTENSIONS, TARGET_FIELDS  # noqa: E402
from .models import (  # noqa: E402
    EvaluationCriterion,
    InterviewCall,
    InterviewEvaluation,
    InterviewSetting,
    SmsGateway,
    SmsSenderId,
)

INTERVIEW_FILE_MAX_BYTES = 10 * 1024 * 1024
DATE_ATTRS = {"data-datepicker": "", "placeholder": "Select date", "autocomplete": "off"}


class InterviewCallForm(StyledFormMixin, forms.ModelForm):
    """Step 1: the file plus the interview details and the SMS set-up."""

    class Meta:
        model = InterviewCall
        fields = ["uploaded_file", "title", "department", "gateway", "sender", "message_template"]
        labels = {"uploaded_file": "Candidate file", "gateway": "SMS API", "sender": "Approved Sender ID"}
        widgets = {
            "uploaded_file": forms.FileInput(attrs={"accept": ",".join(SUPPORTED_EXTENSIONS)}),
            "title": forms.TextInput(attrs={"placeholder": "e.g. Corporate Sales Intern - Interview Call"}),
            "message_template": forms.Textarea(attrs={
                "rows": 8, "data-sms-template": "",
                "placeholder": "Write the full SMS: date, time, venue and anything else the candidate needs.",
            }),
        }
        help_texts = {
            "uploaded_file": "Excel (.xlsx), CSV, text (.txt), or the BD Jobs applicant list (.docx). Max 10 MB.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].required = False
        self.fields["department"].empty_label = "Select department"
        self.fields["gateway"].queryset = SmsGateway.objects.filter(is_active=True)
        self.fields["gateway"].empty_label = "Select SMS API"
        self.fields["sender"].queryset = SmsSenderId.objects.filter(is_active=True, gateway__is_active=True)
        self.fields["sender"].empty_label = "Select Sender ID"
        self.fields["gateway"].required = self.fields["sender"].required = False
        default_sender = SmsSenderId.objects.filter(is_default=True, is_active=True, gateway__is_active=True).first()
        if default_sender and not self.instance.pk:
            self.initial.setdefault("gateway", default_sender.gateway_id)
            self.initial.setdefault("sender", default_sender.pk)
        if self.instance.pk:  # the file cannot be swapped after the columns were mapped
            self.fields.pop("uploaded_file")

    def clean_uploaded_file(self):
        file = self.cleaned_data["uploaded_file"]
        if not file.name.lower().endswith(SUPPORTED_EXTENSIONS):
            raise forms.ValidationError("Upload a .xlsx, .csv, .txt or .docx file.")
        if file.size > INTERVIEW_FILE_MAX_BYTES:
            raise forms.ValidationError(f"The file is {filesizeformat(file.size)} - the limit is 10 MB.")
        return file

    def clean(self):
        cleaned = super().clean()
        gateway, sender = cleaned.get("gateway"), cleaned.get("sender")
        if sender and gateway and sender.gateway_id != gateway.pk:
            self.add_error("sender", "This Sender ID belongs to another SMS API.")
        if sender and not gateway:
            cleaned["gateway"] = sender.gateway
        if not cleaned.get("title") and cleaned.get("department"):
            from django.utils import timezone

            cleaned["title"] = f"{cleaned['department'].name} - {timezone.localdate():%d %b %Y}"
        if not (cleaned.get("message_template") or "").strip():
            self.add_error("message_template", "Write the SMS message.")
        return cleaned


class ColumnMappingForm(forms.Form):
    """Step 2: which column of the file is the name, phone, email ..."""

    def __init__(self, *args, headers=(), initial_map=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "— not in the file —")] + [(h, h) for h in headers]
        for field, label in TARGET_FIELDS:
            self.fields[field] = forms.ChoiceField(
                label=label, choices=choices, required=field == "phone",
                widget=forms.Select(attrs={"class": "input"}),
                initial=(initial_map or {}).get(field, ""),
            )
        self.fields["phone"].error_messages["required"] = "Choose the column that holds the phone number."

    def clean(self):
        cleaned = super().clean()
        chosen = [v for v in cleaned.values() if v]
        if len(chosen) != len(set(chosen)):
            raise forms.ValidationError("A column can only be used for one field.")
        return cleaned


class SmsSendForm(StyledFormMixin, forms.ModelForm):
    """On the preview page: pick the API / Sender ID and edit the message before sending."""

    class Meta:
        model = InterviewCall
        fields = ["gateway", "sender", "message_template"]
        labels = {"gateway": "SMS API", "sender": "Approved Sender ID", "message_template": "SMS message"}
        widgets = {"message_template": forms.Textarea(attrs={"rows": 7, "data-sms-template": ""})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["gateway"].queryset = SmsGateway.objects.filter(is_active=True)
        self.fields["sender"].queryset = SmsSenderId.objects.filter(is_active=True, gateway__is_active=True)
        self.fields["gateway"].required = self.fields["sender"].required = True
        self.fields["gateway"].empty_label = "Select SMS API"
        self.fields["sender"].empty_label = "Select Sender ID"

    def clean(self):
        cleaned = super().clean()
        gateway, sender = cleaned.get("gateway"), cleaned.get("sender")
        if sender and gateway and sender.gateway_id != gateway.pk:
            self.add_error("sender", "This Sender ID belongs to another SMS API.")
        if not (cleaned.get("message_template") or "").strip():
            self.add_error("message_template", "Write the SMS message.")
        return cleaned


class SmsGatewayForm(StyledFormMixin, forms.ModelForm):
    api_key = forms.CharField(
        label="API key", required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Stored on the server only. Leave empty to keep the current key.",
    )

    class Meta:
        model = SmsGateway
        fields = ["name", "base_url", "api_key", "success_codes", "is_active"]

    def clean_api_key(self):
        key = (self.cleaned_data.get("api_key") or "").strip()
        if not key and not self.instance.pk:
            raise forms.ValidationError("Enter the API key.")
        return key or self.instance.api_key


class SmsSenderForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SmsSenderId
        fields = ["gateway", "sender_id", "is_default", "is_active"]
        labels = {"gateway": "SMS API"}

    def save(self, commit=True):
        sender = super().save(commit)
        if commit and sender.is_default:  # only one default
            SmsSenderId.objects.exclude(pk=sender.pk).update(is_default=False)
        return sender


class InterviewerForm(StyledFormMixin, forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(help_text="Used to sign in.")
    phone = forms.CharField(max_length=20, required=False)
    designation = forms.CharField(max_length=120, required=False)
    password = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="At least 8 characters. When editing, leave empty to keep the current password.",
    )
    departments = forms.ModelMultipleChoiceField(
        queryset=Department.objects.all(), required=False, widget=forms.CheckboxSelectMultiple,
        label="Assigned departments",
    )
    is_active = forms.BooleanField(label="Active (can sign in)", required=False, initial=True)
    can_edit_written_marks = forms.BooleanField(
        label="May mark written exam answers", required=False,
        help_text="Off: the interviewer can only view written results.",
    )
    can_edit_submitted = forms.BooleanField(
        label="May edit an evaluation after submitting", required=False,
        help_text="Off: once submitted, only an Admin can unlock the evaluation.",
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["departments"].widget.attrs.pop("class", None)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        users = get_user_model().objects.filter(username__iexact=email)
        if self.user is not None:
            users = users.exclude(pk=self.user.pk)
        if users.exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def clean_password(self):
        password = self.cleaned_data.get("password") or ""
        if self.user is None and not password:
            raise forms.ValidationError("Set a password for the new interviewer.")
        if password:
            from django.contrib.auth.password_validation import validate_password

            validate_password(password, self.user)
        return password


class EvaluationCriterionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = EvaluationCriterion
        fields = ["name", "order", "description", "is_active"]  # every question is marked out of 5


class InterviewSettingForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = InterviewSetting
        fields = ["show_combined", "written_weight", "interview_weight"]


class EvaluationForm(StyledFormMixin, forms.ModelForm):
    """An interviewer's own marking sheet - one mark field per criterion."""

    class Meta:
        model = InterviewEvaluation
        fields = ["comments", "strengths", "weaknesses", "recommendation", "remarks"]
        widgets = {
            "comments": forms.Textarea(attrs={"rows": 3}),
            "strengths": forms.Textarea(attrs={"rows": 2}),
            "weaknesses": forms.Textarea(attrs={"rows": 2}),
            "remarks": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {"recommendation": "Recommendation", "remarks": "Remarks"}

    def __init__(self, *args, rows=(), **kwargs):
        """rows: [(key, label, max_marks, current mark)]"""
        super().__init__(*args, **kwargs)
        self.mark_rows = list(rows)
        for key, label, max_marks, mark in self.mark_rows:
            self.fields[f"mark_{key}"] = forms.IntegerField(
                label=label, min_value=0, max_value=int(max_marks), required=False, initial=mark,
                error_messages={"invalid": "Whole numbers only (0, 1, 2, 3, 4, 5)."},
                widget=forms.NumberInput(attrs={
                    "class": "input eval-mark", "min": "0", "max": str(int(max_marks)), "step": "1",
                    "inputmode": "numeric", "data-max": str(int(max_marks)), "placeholder": f"0-{int(max_marks)}",
                }),
            )
        self.fields["recommendation"].widget.attrs.pop("data-searchable", None)

    def mark_fields(self):
        return [(self[f"mark_{key}"], label, max_marks) for key, label, max_marks, _ in self.mark_rows]

    def require_all_marks(self):
        """Before submitting, every criterion must have a mark."""
        complete = True
        for key, _label, _max, _mark in self.mark_rows:
            if self.cleaned_data.get(f"mark_{key}") is None:
                self.add_error(f"mark_{key}", "Give a mark before submitting.")
                complete = False
        return complete
