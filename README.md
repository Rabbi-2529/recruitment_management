# IGL Interview Portal (Recruitment Management)

A Django recruitment portal built for **IGL Group**. It runs the whole hiring cycle in one place:
candidates check their interview result or register online, HR manages circulars and candidate lists,
SMS interview calls go out from an uploaded shortlist, candidates sit a written exam in the browser,
and each interviewer marks the candidate on their own private sheet while HR sees every mark together
with the graphs on one dashboard.

Live: <https://careers.iglweb.com>

---

## Table of contents

- [What the portal does](#what-the-portal-does)
- [Who uses it](#who-uses-it)
- [Screens at a glance](#screens-at-a-glance)
- [How the marking works](#how-the-marking-works)
- [Technology](#technology)
- [Project layout](#project-layout)
- [Data model](#data-model)
- [Running it locally](#running-it-locally)
- [Configuration](#configuration)
- [Deployment (cPanel + Passenger)](#deployment-cpanel--passenger)
- [SMS interview calls](#sms-interview-calls)
- [Tests](#tests)
- [Design system](#design-system)
- [Security notes](#security-notes)

---

## What the portal does

### 1. Public site (no login)

| Page | What it is for |
| --- | --- |
| `/` | A candidate enters the phone number they applied with and picks a department, and sees their result: **Selected**, **Waiting**, **Not selected** or still **Pending**. Selected candidates also see the interview date and the HR contact. |
| `/register/` | Online application: name, phone, email, department, education, experience and a CV upload (PDF/DOC/DOCX). Only circulars that are open for registration are offered. |
| `/exam/<link>/` | The written exam. The candidate proves who they are with the phone number or email they registered with; if there is no match, the page asks them to register first. |

Phone numbers are validated in Bangladeshi format (`01XXXXXXXXX` or `8801XXXXXXXXX`), in the browser
and again on the server, so a typo like `023...` is refused with a clear message.

### 2. HR panel — `/panel/`

Django's own admin is deliberately **not** installed; the panel is a purpose-built HR interface.

- **Dashboard** — candidate counts by status, circulars and departments at a glance.
- **Circulars** — job circulars with a reference number, the departments they cover, an interview date
  (date only, because several time slots are announced separately) and a registration on/off switch.
- **Departments** — the department list used everywhere else.
- **Candidates** — a server-side DataTable with search, filters (circular, department, status),
  paging and CSV export. Marks and status can be changed inline, one row at a time. Candidates are
  added one by one or uploaded in bulk from CSV/XLSX; the upload reports every skipped row and why.
- **Interview** — search a candidate by phone or name, then open their sheet.
- **Interview sheet** (`/panel/evaluations/<id>/`) — one page per candidate that carries everything:
  profile, interview details, every interviewer's marks per question, totals, percentages, comments,
  the written exam result, the final combined result, HR's own review (rating, expected salary,
  available date, note, CV) and four graphs drawn from the database.
- **Written Exam** — build exams and questions (MCQ and written), fix how many questions each
  candidate gets, the duration and the pass mark, then mark the written answers and copy the result
  onto the candidate.
- **Interview Call** — upload a shortlist (XLSX, CSV, TXT or a BD Jobs `.docx` applicant list), map the
  columns, preview each message, then send the interview call by SMS.
- **SMS Log** — every message with its status, the gateway response and a retry button.
- **Evaluations** — a DataTable of every candidate being interviewed: department, status, interviewers,
  written result, interview average and combined result, with filters.
- **Interviewers** — create interviewer accounts and assign their departments and permissions.
- **Marking Criteria** — the questions on every interviewer's sheet, each marked out of 5, plus the
  weights of the final combined result (written % vs interview %).

### 3. Interviewer portal — `/interviewer/`

Interviewers sign in at the same login page and land in their own portal. They only ever see the
candidates of the departments assigned to them, and only their own marks.

- **Dashboard** — what is waiting for their marks, what they have submitted, and their drafts.
- **My Interviews / Candidates** — DataTables with search, sorting, paging and filters (department,
  main status, their own marking progress).
- **Open Sheet** — the scorecard: candidate and interview details, the questions with 0–5 buttons, a
  live score ring with a progress count, strengths, weaknesses, comments and a recommendation. It can
  be saved as a draft and submitted later.
- **Written Results** — the written exam results of their departments, and the answer sheets
  (markable only if HR granted that permission).

---

## Who uses it

| Role | How it is defined | Where it goes |
| --- | --- | --- |
| **Candidate** | no account | public pages |
| **Admin / HR** | a Django superuser (created with `python manage.py create_root`) | `/panel/` |
| **Interviewer** | a user with an `InterviewerProfile` plus one or more departments | `/interviewer/` |

The same login form serves both staff roles and sends each user to the right home page. An
interviewer cannot open panel pages, another department's candidates, or another interviewer's marks.

---

## Screens at a glance

```
Public                HR panel (/panel/)                 Interviewer (/interviewer/)
─────────             ──────────────────                 ───────────────────────────
Result check          Dashboard                          Dashboard
Registration + CV     Circulars / Departments            My Interviews
Written exam          Candidates (DataTable, CSV)        Candidates
                      Interview sheet + graphs           Open Sheet (scorecard)
                      Written Exam builder + marking     Written Results
                      Interview Call (upload → SMS)
                      SMS Log / SMS Settings
                      Evaluations / Interviewers
                      Marking Criteria
```

---

## How the marking works

- **One status only.** `Candidate.status` (Pending / Selected / Waiting / Not selected) is the single
  status used by the public result page, the panel, the Evaluations list and the interviewer portal.
  A *Waiting* candidate also carries a reason that only HR can see — it is never shown publicly.
- **Whole marks 0–5.** Every question on every sheet is marked with a whole number from 0 to 5. The
  browser blocks anything else and the server refuses it again (model validators plus a check in
  `EvaluationScore.save()`), so a value like `4.5` can never be stored.
- **Separate sheets.** Each interviewer has their own `InterviewEvaluation` for a candidate, with one
  `EvaluationScore` per question. Interviewers never see each other's marks; HR sees them all.
- **Draft → submitted → locked.** A sheet can be saved as a draft, then submitted. After submitting it
  is locked (unless HR granted "may edit after submitting"), and it locks for everyone as soon as HR
  sets a final main status. HR can unlock a single sheet.
- **HR marks too.** The Admin has their own sheet on the same page, kept separate from the
  interviewers' average.
- **Final combined result.** The written exam percentage and the average interview percentage are
  weighted (40 / 60 by default, configurable in Marking Criteria).

The graphs on the interview sheet are all drawn from the database: marks per question per interviewer
with the average line, each interviewer's total, the written/interview/combined summary, and where the
candidate stands against the rest of their department.

---

## Technology

- **Django 5.1**, Python 3.11
- **SQLite** (the whole portal is one small site on shared hosting)
- **WhiteNoise** for static files
- **DataTables 2.1.8**, **jQuery 3.7.1**, **Select2 4.1**, **Chart.js 4.4** from CDN
- **openpyxl** for XLSX uploads; `.docx` applicant lists are parsed from the zipped XML directly
- A hand-written date picker and phone validator, so there is no heavy front-end build step
- No Node build, no framework: server-rendered templates and a little vanilla JavaScript

---

## Project layout

```
config/                 settings, root URLs, WSGI
passenger_wsgi.py       cPanel/Passenger entry point
recruitment/
  models.py             all models (see below)
  urls.py               public site
  panel_urls.py         HR panel
  interviewer_urls.py   interviewer portal
  views_public.py       result check, registration, written exam
  views_panel.py        circulars, departments, candidates, exams
  views_interview.py    interview sheet, evaluations, interview calls, SMS, criteria
  views_interviewer.py  the interviewer portal
  forms.py              every form, with the shared styling mixin
  evaluation.py         marking rules: locking, totals, chart data
  sms.py                the SMS gateway client
  interview_call_import.py  XLSX / CSV / TXT / BD Jobs .docx parsing
  permissions.py        role checks and department scoping
  utils.py              phone normalising, whole-mark parsing, helpers
  templates/{public,panel,interviewer}/
  management/commands/  create_root, make_developer_exam
static/{css,js,img}/
```

## Data model

| Model | What it holds |
| --- | --- |
| `Circular` | a job circular: title, reference number, departments, interview date, registration open |
| `Department` | departments used by circulars, candidates and interviewers |
| `Candidate` | the applicant: contact, department, circular, **status**, waiting reason, written mark, HR review (rating, expected salary, available date, note), CV, source |
| `Exam`, `Question`, `Choice` | the written exam: random question sets, duration, pass mark, strict mode |
| `ExamAttempt`, `Answer` | one candidate's sitting, their answers, auto and manual marks |
| `InterviewerProfile`, `InterviewerDepartment` | who is an interviewer, their departments and permissions |
| `EvaluationCriterion` | the questions on the marking sheet (each out of 5) |
| `InterviewEvaluation`, `EvaluationScore` | one interviewer's sheet for one candidate, and each mark on it |
| `InterviewSetting` | the written/interview weights of the combined result |
| `SmsGateway`, `SmsSenderId` | SMS API credentials and sender IDs (stored in the database, never in code) |
| `InterviewCall`, `InterviewCallCandidate`, `SmsLog` | an uploaded shortlist, its people, and every message sent |

`InterviewCallCandidate` is intentionally **separate** from `Candidate`: an uploaded shortlist is a
calling list, not the candidate database, and rows are never copied across.

---

## Running it locally

Requires Python 3.11+.

```bash
git clone https://github.com/Rabbi-2529/recruitment_management.git
cd recruitment_management
python -m venv venv
venv/Scripts/activate        # Windows; use source venv/bin/activate on Linux/macOS
pip install -r requirements.txt
python manage.py migrate
python manage.py create_root  # creates the Admin account
python manage.py runserver
```

Then open <http://localhost:8000/> for the public site and <http://localhost:8000/panel/> for the HR
panel. `create_root` creates `root@iglweb.com` and asks for a password (or takes `--password`).

To try the written exam quickly, `python manage.py make_developer_exam` builds a sample exam.

---

## Configuration

Everything is read from environment variables, with sensible defaults for local work.

| Variable | Default | What it does |
| --- | --- | --- |
| `DJANGO_DEBUG` | off | `1` turns debug on. **Production runs with it off.** |
| `DJANGO_SECRET_KEY` | generated into `secret_key.txt` | the signing key; set it in production |
| `DJANGO_ALLOWED_HOSTS` | localhost | comma-separated host names |
| `DJANGO_DB_PATH` | `db.sqlite3` | where the SQLite file lives |
| `DJANGO_MEDIA_ROOT` | `media/` | uploaded CVs and shortlists |
| `DJANGO_STATIC_ROOT` | `staticfiles/` | `collectstatic` target |
| `DJANGO_URL_PREFIX` | empty | set when the app is served under a sub-path |

`secret_key.txt`, `db.sqlite3` and `media/` are git-ignored: no secrets and no candidate data are in
this repository.

---

## Deployment (cPanel + Passenger)

The live site runs as a cPanel "Setup Python App":

| Setting | Value |
| --- | --- |
| Application root | `~/careers.iglweb.com/careers` |
| Startup file | `passenger_wsgi.py` |
| Entry point | `application` |
| Python | 3.11 virtualenv at `~/virtualenv/careers.iglweb.com/careers/3.11` |

`passenger_wsgi.py` forces debug off and serves the site from the domain root. To deploy an update,
upload the project files, then from the app's virtualenv:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
touch tmp/restart.txt
```

Static files are served by WhiteNoise, so no extra web-server configuration is needed.

---

## SMS interview calls

1. Upload the shortlist — XLSX, CSV, TXT, or the `.docx` applicant list exported from BD Jobs.
2. Map the columns (name, phone, and any reference you want to keep).
3. Write the message. Placeholders such as `{candidate_name}`, `{phone}` and `{department}` are filled
   in per person, and the character count is shown as you type.
4. Preview any person's exact message, then send.

Each recipient gets **one** request of their own, duplicate phone numbers are dropped, and every
attempt is written to the SMS log with the gateway's response so failures can be retried. Which
response codes count as success is configurable per gateway (the IGL gateway returns `445000`).

The API key lives in the database and is used server-side only — it never reaches the browser, and it
is stripped from anything written to the log.

---

## Tests

```bash
python manage.py test recruitment
```

99 tests cover the public result check and registration, phone validation, the panel and its
DataTable endpoints, CSV/XLSX uploads, the written exam (including the strict anti-cheat mode), the
waiting status, interview calls and SMS parsing, role permissions and department scoping, separate
interviewer sheets with their locking rules, whole-mark enforcement, and the data migrations.

Tests never send real SMS: the gateway call is mocked, because shortlists contain real applicants'
phone numbers.

---

## Design system

- **Navy chrome, steel-blue actions.** Deep navy sidebar and page headers, `#1d4ed8` for primary
  buttons and links, cool grey surfaces, one card style with a soft shadow.
- **Status colours are reserved:** teal-green = Selected, blue = Waiting, amber = Pending,
  red = Not selected. They are never reused as decoration.
- **Marks read as one scale.** The 0–5 buttons and mark chips use a single teal ramp from light to
  dark, checked for colour-blind separation and contrast rather than picked by eye.
- **Charts** use a fixed, validated categorical order (blue, amber, teal, crimson, violet), always
  with a legend, and every number in the graphs is also in a table on the same page.
- Every page works down to phone width; tables scroll instead of breaking the layout.

---

## Security notes

- Debug is off by default; the secret key, database and uploaded media are never committed.
- Interviewers are scoped to their own departments and their own sheets at the view level, not just
  hidden in templates.
- The written exam's strict mode logs tab switches and blocks copy/screenshot shortcuts, and the
  candidate still has a manual Submit button.
- The waiting reason is HR-only and never rendered on the public result page.
- The SMS API key is server-side only and is never rendered into HTML or JavaScript.

---

© IGL Group. Internal recruitment software.
