from django.urls import path

from . import views_interviewer as v

app_name = "interviewer"

urlpatterns = [
    path("", v.dashboard, name="dashboard"),
    path("interviews/", v.my_interviews, name="interviews"),
    path("candidates/", v.candidates, name="candidates"),
    path("candidates/<int:pk>/", v.candidate_detail, name="candidate"),
    path("candidates/<int:pk>/cv/", v.candidate_cv, name="candidate_cv"),
    path("written-results/", v.written_results, name="written_results"),
    path("written-results/<int:pk>/", v.written_sheet, name="written_sheet"),
    path("profile/", v.profile, name="profile"),
    path("logout/", v.logout_view, name="logout"),
]
