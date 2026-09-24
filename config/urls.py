from django.urls import include, path

urlpatterns = [
    path("panel/", include("recruitment.panel_urls")),
    path("interviewer/", include("recruitment.interviewer_urls")),
    path("", include("recruitment.urls")),
]
