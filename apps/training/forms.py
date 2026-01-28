"""
Training forms.
"""

from django import forms

from apps.courts.models import Court

from .models import CoachApplication, Training, TrainingEnrollment


class TrainingEnrollmentForm(forms.ModelForm):
    """Форма записи на тренировку: ФИО, Telegram, email, время, корт, согласие."""

    agree_legal = forms.BooleanField(
        required=True,
        label="",
        widget=forms.CheckboxInput(attrs={"class": "form-checkbox"}),
    )
    preferred_datetime = forms.DateTimeField(
        required=False,
        label="Желаемое время",
        widget=forms.DateTimeInput(
            attrs={"class": "form-control", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
    )

    class Meta:
        model = TrainingEnrollment
        fields = (
            "full_name",
            "telegram",
            "whatsapp",
            "email",
            "preferred_datetime",
            "desired_court",
            "message",
        )
        widgets = {
            "full_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Фамилия Имя Отчество"}
            ),
            "telegram": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "@username или +7..."}
            ),
            "whatsapp": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "+7XXXXXXXXXX"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "email@example.com"}
            ),
            "desired_court": forms.Select(attrs={"class": "form-control"}),
            "message": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Дополнительные пожелания (необязательно)",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["desired_court"].queryset = Court.objects.filter(is_active=True).order_by("name")
        self.fields["desired_court"].required = False
        self.fields["desired_court"].empty_label = "—— Не выбрано ——"
        self.fields["message"].required = False
        self.fields["whatsapp"].required = False

    def clean_agree_legal(self):
        if not self.cleaned_data.get("agree_legal"):
            raise forms.ValidationError("Необходимо согласие на обработку персональных данных.")
        return True


class CoachApplicationForm(forms.ModelForm):
    """Форма заявки «Стать тренером». Поля как у тренера в админке."""

    class Meta:
        model = CoachApplication
        fields = (
            "applicant_name",
            "applicant_email",
            "applicant_phone",
            "name",
            "photo",
            "bio",
            "experience_years",
            "specialization",
            "phone",
            "telegram",
            "whatsapp",
            "max_contact",
            "city",
        )
        widgets = {
            "applicant_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "ФИО"}
            ),
            "applicant_email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "email@example.com"}
            ),
            "applicant_phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "+7XXXXXXXXXX"}
            ),
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Как вас представлять"}
            ),
            "photo": forms.FileInput(
                attrs={"class": "form-control", "accept": "image/*"}
            ),
            "bio": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Опыт, достижения, подход к тренировкам",
                }
            ),
            "experience_years": forms.NumberInput(
                attrs={"class": "form-control", "min": 0, "max": 50}
            ),
            "specialization": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Например: взрослые, дети, мини-теннис",
                }
            ),
            "phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Телефон для связи"}
            ),
            "telegram": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "@username"}
            ),
            "whatsapp": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "+7XXXXXXXXXX"}
            ),
            "max_contact": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ссылка на профиль MAX (из «Поделиться»)",
                }
            ),
            "city": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Город"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["applicant_phone"].required = False
        self.fields["photo"].required = False
        self.fields["bio"].required = False
        self.fields["specialization"].required = False
        self.fields["phone"].required = False
        self.fields["telegram"].required = False
        self.fields["whatsapp"].required = False
        self.fields["max_contact"].required = False


class TrainingForm(forms.ModelForm):
    """Форма создания/редактирования тренировки тренером."""

    class Meta:
        model = Training
        fields = (
            "title",
            "short_description",
            "description",
            "training_type",
            "skill_level",
            "target_category",
            "court",
            "city",
            "duration_minutes",
            "max_participants",
            "price",
            "schedule",
            "image",
            "is_active",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "short_description": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "training_type": forms.Select(attrs={"class": "form-control"}),
            "skill_level": forms.Select(attrs={"class": "form-control"}),
            "target_category": forms.Select(attrs={"class": "form-control"}),
            "court": forms.Select(attrs={"class": "form-control"}),
            "city": forms.TextInput(attrs={"class": "form-control"}),
            "duration_minutes": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "max_participants": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "schedule": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "image": forms.FileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.users.models import SkillLevel

        self.fields["court"].queryset = Court.objects.filter(is_active=True).order_by("name")
        self.fields["court"].required = False
        self.fields["court"].empty_label = "—— Не выбрано ——"
        self.fields["target_category"].required = False
        self.fields["target_category"].choices = [("", "———")] + list(SkillLevel.choices)
        self.fields["short_description"].required = False
        self.fields["schedule"].required = False
        self.fields["price"].required = False
        self.fields["image"].required = False
