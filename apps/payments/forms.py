from django import forms


class DonateForm(forms.Form):
    name_or_email = forms.CharField(
        label="Имя или Email",
        max_length=200,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Введите ваше имя или email"}
        ),
        help_text="Укажите имя или email для связи (необязательно для зарегистрированных пользователей)",
    )
    amount = forms.DecimalField(
        label="Сумма (руб)",
        min_value=10,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "Введите сумму"}
        ),
    )
    comment = forms.CharField(
        label="Комментарий",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Ваше сообщение (необязательно)",
            }
        ),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        # Если пользователь авторизован, поле имени/email необязательно
        if user and user.is_authenticated:
            self.fields["name_or_email"].required = False
            self.fields["name_or_email"].help_text = (
                "Необязательно (вы авторизованы как {})".format(
                    user.email or user.get_full_name() or "пользователь"
                )
            )
