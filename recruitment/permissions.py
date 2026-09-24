"""
Two roles:
  Admin       - a superuser; the existing HR panel at /panel/.
  Interviewer - a user with an InterviewerProfile; the portal at /interviewer/,
                limited to the departments assigned to them.
"""
from functools import wraps

from django.shortcuts import redirect
from django.urls import reverse


def is_admin(user):
    return bool(user and user.is_authenticated and user.is_active and user.is_superuser)


def is_interviewer(user):
    return bool(
        user and user.is_authenticated and user.is_active and not user.is_superuser
        and hasattr(user, "interviewer_profile")
    )


def home_url_for(user):
    if is_admin(user):
        return reverse("panel:dashboard")
    if is_interviewer(user):
        return reverse("interviewer:dashboard")
    return reverse("panel:login")


def _to_login(request):
    return redirect(f"{reverse('panel:login')}?next={request.get_full_path()}")


def admin_required(view):
    """Admin (superuser) only. A signed-in interviewer is sent to their own dashboard instead."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if is_admin(request.user):
            return view(request, *args, **kwargs)
        if is_interviewer(request.user):
            return redirect("interviewer:dashboard")
        return _to_login(request)

    return wrapper


def interviewer_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if is_interviewer(request.user):
            return view(request, *args, **kwargs)
        if is_admin(request.user):
            return redirect("panel:dashboard")
        return _to_login(request)

    return wrapper


def interviewer_departments(user):
    """Department queryset the interviewer may see (empty for everyone else)."""
    from .models import Department

    if not is_interviewer(user):
        return Department.objects.none()
    return Department.objects.filter(interviewer_links__interviewer__user=user).distinct()


def candidates_for_interviewer(user):
    """Only candidates of the interviewer's assigned departments."""
    from .models import Candidate

    return Candidate.objects.filter(department__in=interviewer_departments(user))
