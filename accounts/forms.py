"""Auth forms: styled, email-based, with helpful error messages."""
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserChangeForm,
    UserCreationForm,
)

User = get_user_model()

TEXT_INPUT_CLASS = "form-control"


class EmailLoginForm(AuthenticationForm):
    """AuthenticationForm with an email field and shop-friendly wording."""

    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={
                "class": TEXT_INPUT_CLASS,
                "placeholder": "you@shop.com",
                "autocomplete": "username",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": TEXT_INPUT_CLASS,
                "placeholder": "Your password",
                "autocomplete": "current-password",
            }
        ),
    )
    remember_me = forms.BooleanField(
        label="Keep me signed in on this computer",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Email or password is not correct. Please try again.",
        "inactive": "This account has been disabled. Contact the shop owner.",
    }

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()


class ShopPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        label="Registered email",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "class": TEXT_INPUT_CLASS,
                "placeholder": "you@shop.com",
                "autocomplete": "email",
                "autofocus": True,
            }
        ),
    )

    def get_users(self, email):
        """Same behaviour as Django, but tolerant of case differences."""
        return super().get_users(email.strip().lower())


class ShopSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.update(
                {"class": TEXT_INPUT_CLASS, "autocomplete": "new-password"}
            )
        self.fields["new_password1"].label = "New password"
        self.fields["new_password2"].label = "Confirm new password"


class ProfileForm(forms.ModelForm):
    """Lets the logged-in user fix their own name / phone."""

    class Meta:
        model = User
        fields = ("full_name", "phone", "email")
        widgets = {
            "full_name": forms.TextInput(attrs={"class": TEXT_INPUT_CLASS}),
            "phone": forms.TextInput(attrs={"class": TEXT_INPUT_CLASS}),
            "email": forms.EmailInput(attrs={"class": TEXT_INPUT_CLASS}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        qs = User.objects.filter(email=email).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Another account already uses this email.")
        return email


# --- Admin forms -----------------------------------------------------------
class AdminUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("email", "full_name", "phone")


class AdminUserChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"
