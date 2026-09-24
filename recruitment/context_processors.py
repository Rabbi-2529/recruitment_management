from django.conf import settings


def hr_contact(request):
    return {"HR_PHONE": settings.HR_PHONE, "HR_EMAIL": settings.HR_EMAIL}
