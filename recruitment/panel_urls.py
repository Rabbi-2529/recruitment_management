from django.urls import path

from . import views_interview as iv
from . import views_panel as v

app_name = "panel"

urlpatterns = [
    path("login/", v.panel_login, name="login"),
    path("logout/", v.panel_logout, name="logout"),
    path("", v.dashboard, name="dashboard"),
    # circulars
    path("circulars/", v.circular_list, name="circular_list"),
    path("circulars/add/", v.circular_form, name="circular_add"),
    path("circulars/<int:pk>/edit/", v.circular_form, name="circular_edit"),
    path("circulars/<int:pk>/delete/", v.circular_delete, name="circular_delete"),
    # departments
    path("departments/", v.department_list, name="department_list"),
    path("departments/<int:pk>/edit/", v.department_edit, name="department_edit"),
    path("departments/<int:pk>/delete/", v.department_delete, name="department_delete"),
    # interview
    path("interview/", v.interview, name="interview"),
    path("interview/suggest/", v.interview_suggest, name="interview_suggest"),
    path("interview/<int:pk>/", iv.candidate_sheet, name="interview_detail"),  # same page as evaluation_detail
    # written exam
    path("exams/", v.exam_list, name="exam_list"),
    path("exams/add/", v.exam_form, name="exam_add"),
    path("exams/<int:pk>/edit/", v.exam_form, name="exam_edit"),
    path("exams/<int:pk>/delete/", v.exam_delete, name="exam_delete"),
    path("exams/<int:pk>/questions/", v.exam_questions, name="exam_questions"),
    path("exams/<int:exam_pk>/questions/add/", v.question_form, name="question_add"),
    path("exams/<int:exam_pk>/questions/<int:pk>/edit/", v.question_form, name="question_edit"),
    path("exams/<int:exam_pk>/questions/<int:pk>/delete/", v.question_delete, name="question_delete"),
    path("exams/<int:pk>/responses/", v.exam_responses, name="exam_responses"),
    path("responses/<int:pk>/", v.exam_response, name="exam_response"),
    # interview call (upload -> map columns -> preview -> SMS)
    path("interview-calls/", iv.interview_call_list, name="interview_call_list"),
    path("interview-calls/new/", iv.interview_call_create, name="interview_call_create"),
    path("interview-calls/<int:pk>/", iv.interview_call_detail, name="interview_call_detail"),
    path("interview-calls/<int:pk>/columns/", iv.interview_call_mapping, name="interview_call_mapping"),
    path("interview-calls/<int:pk>/edit/", iv.interview_call_edit, name="interview_call_edit"),
    path("interview-calls/<int:pk>/delete/", iv.interview_call_delete, name="interview_call_delete"),
    path("interview-calls/<int:pk>/preview/", iv.interview_call_preview, name="interview_call_preview"),
    path("interview-calls/<int:pk>/queue/", iv.interview_call_queue, name="interview_call_queue"),
    path("interview-calls/<int:pk>/send/", iv.interview_call_send_batch, name="interview_call_send_batch"),
    path("sms/log/", iv.sms_log, name="sms_log"),
    path("sms/log/data/", iv.sms_log_data, name="sms_log_data"),
    path("sms/settings/", iv.sms_settings, name="sms_settings"),
    path("sms/gateways/<int:pk>/", iv.sms_gateway_edit, name="sms_gateway_edit"),
    path("sms/senders/<int:pk>/", iv.sms_sender_edit, name="sms_sender_edit"),
    # interviewers & evaluations
    path("interviewers/", iv.interviewer_list, name="interviewer_list"),
    path("interviewers/new/", iv.interviewer_form, name="interviewer_add"),
    path("interviewers/<int:pk>/", iv.interviewer_form, name="interviewer_edit"),
    path("marking-criteria/", iv.criteria, name="criteria"),
    path("marking-criteria/<int:pk>/", iv.criterion_edit, name="criterion_edit"),
    path("evaluations/", iv.evaluation_list, name="evaluation_list"),
    path("evaluations/data/", iv.evaluation_data, name="evaluation_data"),
    path("evaluations/<int:pk>/", iv.candidate_sheet, name="evaluation_detail"),
    path("evaluations/<int:pk>/summary/", iv.candidate_summary, name="candidate_summary"),
    # candidates
    path("candidates/", v.candidate_list, name="candidate_list"),
    path("candidates/data/", v.candidate_data, name="candidate_data"),
    path("candidates/add/", v.candidate_form, name="candidate_add"),
    path("candidates/upload/", v.candidate_upload, name="candidate_upload"),
    path("candidates/template.csv", v.candidate_template, name="candidate_template"),
    path("candidates/export.csv", v.candidate_export, name="candidate_export"),
    path("candidates/bulk/", v.candidate_bulk, name="candidate_bulk"),
    path("candidates/<int:pk>/edit/", v.candidate_form, name="candidate_edit"),
    path("candidates/<int:pk>/status/", v.candidate_status, name="candidate_status"),
    path("candidates/<int:pk>/marks/", v.candidate_marks, name="candidate_marks"),
    path("candidates/<int:pk>/review/", v.candidate_review, name="candidate_review"),
    path("candidates/<int:pk>/cv/", v.candidate_cv, name="candidate_cv"),
    path("candidates/<int:pk>/delete/", v.candidate_delete, name="candidate_delete"),
]
