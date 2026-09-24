"""
Interview-call SMS through the IGL SMS API:

    GET http://sms.iglweb.com/api/v1/send?api_key=...&contacts=01XXXXXXXXX&senderid=...&msg=...

One request per person (never several numbers in one "contacts"), everything URL-encoded,
and the API key is only ever used here on the server.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from django.utils import timezone
from django.utils.dateformat import format as date_format

# optional - the Admin writes the message; these only fill in each person's own details
PLACEHOLDERS = [
    ("{candidate_name}", "Candidate name"),
    ("{phone}", "Phone number"),
    ("{department}", "Department"),
    ("{candidate_id}", "Candidate ID / Roll"),
]
SEND_TIMEOUT_SECONDS = 20
BATCH_SIZE = 10  # messages sent per browser request, so a big list never hits the server timeout


def message_context(person):
    call = person.interview_call
    return {
        "candidate_name": person.candidate_name or "Candidate",
        "phone": person.phone,
        "department": person.department_text or call.department.name,
        # only for messages written before date/time/venue were dropped from the form
        "interview_date": date_format(call.interview_date, "d-m-Y") if call.interview_date else "",
        "interview_time": date_format(call.interview_time, "h:i A") if call.interview_time else "",
        "venue": call.venue,
        "candidate_id": person.candidate_ref,
    }


def render_message(template, person):
    """Fill {placeholders}; unknown ones are left as they are instead of breaking the message."""
    context = message_context(person)
    return re.sub(r"\{(\w+)\}", lambda m: str(context.get(m.group(1), m.group(0))), template or "").strip()


def build_url(gateway, phone, sender_id, message):
    query = urllib.parse.urlencode(
        {"api_key": gateway.api_key, "contacts": phone, "senderid": sender_id, "msg": message},
        quote_via=urllib.parse.quote,
    )
    return f"{gateway.base_url}?{query}"


def masked(text, api_key):
    return text.replace(api_key, "***") if api_key else text


DEFAULT_SUCCESS_CODES = ("445000",)  # IGL SMS API: 445000 = accepted


def _codes_in(data):
    """Values of "code"/"status" style keys in a JSON reply (top level and one level down)."""
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            name = str(key).lower()
            if isinstance(value, (dict, list)):
                found.extend(_codes_in(value) if isinstance(value, dict) else
                             [c for item in value for c in _codes_in(item)])
            elif "code" in name or "status" in name:
                found.append(str(value).strip())
    return found


def looks_successful(http_status, body, success_codes=DEFAULT_SUCCESS_CODES):
    """
    The IGL SMS API replies with a code - 445000 means the SMS was accepted. Other APIs answer with
    {"response_code": 202}, {"status": "success"} or plain text; those are understood too.
    """
    if not 200 <= (http_status or 0) < 300:
        return False
    codes = {str(c).strip() for c in (success_codes or DEFAULT_SUCCESS_CODES) if str(c).strip()}
    text = (body or "").strip()
    try:
        data = json.loads(text)
    except ValueError:
        data = None

    if isinstance(data, (dict, list, int, str)) and not isinstance(data, bool):
        values = _codes_in(data) if isinstance(data, dict) else (
            [str(data)] if not isinstance(data, list) else [c for item in data for c in _codes_in(item)]
        )
        if any(v in codes for v in values):
            return True
        if isinstance(data, dict):
            lowered = {str(k).lower(): v for k, v in data.items()}
            for key in ("response_code", "code", "status_code", "error_code"):
                if key in lowered:
                    try:
                        return int(lowered[key]) in (200, 201, 202)
                    except (TypeError, ValueError):
                        break
            for key in ("status", "success", "result"):
                if key in lowered:
                    value = str(lowered[key]).lower()
                    return value in ("success", "successful", "true", "ok", "sent", "1", "submitted")
            return not ("error" in lowered or "errors" in lowered)
        if values:  # a bare number that is not a success code
            return False

    # plain text, e.g. "445000" or "445000|SMS Submitted"
    if any(re.search(rf"(?<!\d){re.escape(code)}(?!\d)", text) for code in codes):
        return True
    low = text.lower()
    return not any(word in low for word in ("error", "fail", "invalid", "unauthori", "insufficient", "denied"))


def send_log(log, gateway):
    """Send one SmsLog (status pending) and record the outcome on the log and on the person."""
    from .models import InterviewCallCandidate, SmsLog

    url = build_url(gateway, log.phone, log.sender_id, log.message)
    http_status, body, error = None, "", ""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "IGL-Careers/1.0"})
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS) as response:
            http_status = response.status
            body = response.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        http_status = exc.code
        body = exc.read(4000).decode("utf-8", "replace") if exc.fp else ""
        error = f"HTTP {exc.code}"
    except Exception as exc:  # timeout, DNS, connection refused ...
        error = masked(f"{type(exc).__name__}: {exc}", gateway.api_key)[:500]

    ok = not error and looks_successful(http_status, body, gateway.success_code_list)
    if not ok and not error:
        error = "The SMS API did not confirm the message."
    now = timezone.now()
    log.status = SmsLog.Status.SUCCESS if ok else SmsLog.Status.FAILED
    log.http_status = http_status
    log.api_response = masked(body, gateway.api_key)[:4000]
    log.error = "" if ok else error[:500]
    log.sent_at = now
    log.save(update_fields=["status", "http_status", "api_response", "error", "sent_at"])

    person = log.recipient
    person.sms_status = InterviewCallCandidate.SmsStatus.SENT if ok else InterviewCallCandidate.SmsStatus.FAILED
    person.sms_response = log.api_response or log.error
    person.sms_sent_at = now
    person.save(update_fields=["sms_status", "sms_response", "sms_sent_at"])
    return ok
