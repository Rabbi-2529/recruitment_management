from django.conf import settings


def hr_contact(request):
    return {"HR_PHONE": settings.HR_PHONE, "HR_EMAIL": settings.HR_EMAIL}


def assets(request):
    """Cache-busting stamp for the site's own css/js."""
    return {"ASSET_V": settings.ASSET_VERSION}
