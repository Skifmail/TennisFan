"""
Shop models: ShopPage, Product, ProductPhoto, PurchaseRequest.
"""

from django.db import models


class ShopPage(models.Model):
    """
    Singleton для страницы «Магазин».
    Редактируемое содержимое (описание, приветствие).
    """

    intro_text = models.TextField(
        "Текст на странице магазина",
        blank=True,
        help_text="Описание магазина, приветствие. Поддерживается Markdown.",
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Магазин"
        verbose_name_plural = "Магазин"

    def __str__(self) -> str:
        return "Магазин"

    @classmethod
    def get_singleton(cls) -> "ShopPage":
        """Return the single ShopPage instance, creating if needed."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create(intro_text="")
        return obj


class Product(models.Model):
    """Товар в магазине."""

    name = models.CharField("Наименование", max_length=200)
    size = models.CharField("Размер", max_length=100, blank=True)
    quantity = models.PositiveIntegerField("Количество доступно", default=0)
    description = models.TextField("Описание", blank=True)
    price = models.DecimalField(
        "Цена (руб)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    order = models.PositiveSmallIntegerField("Порядок", default=0)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.name

    @property
    def main_image(self):
        """Первое фото товара."""
        return self.photos.order_by("order").first()

    def is_available(self) -> bool:
        """Есть ли товар в наличии."""
        return self.quantity > 0


class ProductPhoto(models.Model):
    """Фото товара (до 10 на товар)."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Товар",
    )
    image = models.ImageField("Фото", upload_to="shop/products/")
    order = models.PositiveSmallIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Фото товара"
        verbose_name_plural = "Фото товаров"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"Фото {self.id} — {self.product.name}"


class PurchaseRequest(models.Model):
    """Заявка на покупку товара."""

    class Status(models.TextChoices):
        NEW = "new", "Новая"
        IN_PROGRESS = "in_progress", "В обработке"
        COMPLETED = "completed", "Выполнена"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="purchase_requests",
        verbose_name="Товар",
    )
    user = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shop_purchase_requests",
        verbose_name="Пользователь",
    )
    first_name = models.CharField("Имя", max_length=100)
    last_name = models.CharField("Фамилия", max_length=100)
    contact_phone = models.CharField("Номер для связи", max_length=50)
    comment = models.TextField("Комментарий", blank=True)
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    class Meta:
        verbose_name = "Заявка на покупку"
        verbose_name_plural = "Заявки на покупку"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.last_name} {self.first_name} — {self.product.name}"
