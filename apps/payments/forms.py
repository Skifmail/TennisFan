from django import forms

class DonateForm(forms.Form):
    amount = forms.DecimalField(label="Сумма (руб)", min_value=10, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Введите сумму'}))
    comment = forms.CharField(label="Комментарий", widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Ваше сообщение (необязательно)'}), required=False)
