from django.urls import path

from . import views_public

app_name = "public"

urlpatterns = [
    path("", views_public.landing, name="landing"),
    path("register/", views_public.register, name="register"),
    path("register/done/", views_public.register_done, name="register_done"),
    # written exam (link shared by HR)
    path("exam/<str:token>/", views_public.exam_start, name="exam_start"),
    path("exam/<str:token>/questions/", views_public.exam_questions, name="exam_questions"),
    path("exam/<str:token>/done/", views_public.exam_done, name="exam_done"),
    path("exam/<str:token>/save/", views_public.exam_autosave, name="exam_autosave"),
    path("exam/<str:token>/leave/", views_public.exam_leave, name="exam_leave"),
]
