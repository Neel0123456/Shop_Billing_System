"""Authentication views: login (throttled), logout, password reset, profile."""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import UpdateView

from .forms import (
    EmailLoginForm,
    ProfileForm,
    ShopPasswordResetForm,
    ShopSetPasswordForm,
)

logger = logging.getLogger(__name__)


def _throttle_key(request) -> str:
    """Cache key for failed-login counting: per IP, so one PC cannot brute force."""
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    ip = ip or request.META.get("REMOTE_ADDR", "unknown")
    return f"login-attempts:{ip}"


class ThrottledLoginView(auth_views.LoginView):
    """
    Login page with a simple cache-backed lockout.

    Keeps a dependency-free brake on password guessing; swap in django-axes if
    you ever need per-account lockouts and an audit trail.
    """

    template_name = "accounts/login.html"
    authentication_form = EmailLoginForm
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        self.locked_out = cache.get(_throttle_key(request), 0) >= settings.LOGIN_MAX_ATTEMPTS
        if self.locked_out and request.method == "POST":
            messages.error(
                request,
                "Too many failed attempts. Please wait 15 minutes and try again.",
            )
            return redirect("accounts:login")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        cache.delete(_throttle_key(self.request))

        # "Keep me signed in" -> persistent session, otherwise browser session.
        if not form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(0)

        logger.info("Login success for %s", form.cleaned_data.get("username"))
        response = super().form_valid(form)
        messages.success(self.request, f"Welcome back, {self.request.user.get_short_name()}.")
        return response

    def form_invalid(self, form):
        key = _throttle_key(self.request)
        attempts = cache.get(key, 0) + 1
        cache.set(key, attempts, settings.LOGIN_LOCKOUT_SECONDS)
        logger.warning(
            "Failed login #%s for %s", attempts, form.data.get("username", "?")
        )
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["locked_out"] = self.locked_out
        return ctx


class LogoutView(auth_views.LogoutView):
    """Logout (POST only, per Django 5 defaults)."""

    next_page = reverse_lazy("accounts:login")


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "emails/password_reset_email.txt"
    html_email_template_name = "emails/password_reset_email.html"
    subject_template_name = "emails/password_reset_subject.txt"
    form_class = ShopPasswordResetForm
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        logger.info("Password reset requested for %s", form.cleaned_data.get("email"))
        return super().form_valid(form)


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = ShopSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")
    post_reset_login = False


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = ShopSetPasswordForm
    success_url = reverse_lazy("accounts:profile")

    def get_form_class(self):
        # PasswordChangeView needs the old-password field, keep Django's form.
        return auth_views.PasswordChangeView.form_class

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field in form.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
        return form

    def form_valid(self, form):
        messages.success(self.request, "Password updated.")
        return super().form_valid(form)


class ProfileView(LoginRequiredMixin, UpdateView):
    template_name = "accounts/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, "Profile saved.")
        return super().form_valid(form)


@login_required
def whoami(request):
    """Tiny helper page, handy when debugging sessions on the shop PC."""
    return render(request, "accounts/whoami.html")
