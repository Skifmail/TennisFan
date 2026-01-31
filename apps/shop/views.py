"""
Shop views.
"""

import logging

import markdown
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.telegram_notify import notify_purchase_request

from .forms import PurchaseRequestForm
from .models import Product, PurchaseRequest, ShopPage

logger = logging.getLogger(__name__)


def shop_list(request):
    """Страница магазина — сетка товаров."""
    shop_page = ShopPage.get_singleton()
    intro_html = markdown.markdown(shop_page.intro_text or "", extensions=["extra"])
    products = Product.objects.prefetch_related("photos").order_by("order", "id")
    context = {
        "shop_page": shop_page,
        "intro_html": intro_html,
        "products": products,
    }
    return render(request, "shop/list.html", context)


def product_detail(request, pk):
    """Детальная страница товара."""
    product = get_object_or_404(
        Product.objects.prefetch_related("photos"),
        pk=pk,
    )
    form = PurchaseRequestForm()
    if request.user.is_authenticated:
        form.initial = {
            "first_name": request.user.first_name or "",
            "last_name": request.user.last_name or "",
            "contact_phone": getattr(request.user, "phone", "") or "",
        }
    show_modal = request.GET.get("buy") == "1"
    context = {
        "product": product,
        "form": form,
        "show_modal": show_modal,
    }
    return render(request, "shop/product_detail.html", context)


@require_POST
def purchase_request_create(request, product_id):
    """Создание заявки на покупку (AJAX или обычный POST)."""
    product = get_object_or_404(Product.objects.prefetch_related("photos"), pk=product_id)
    form = PurchaseRequestForm(request.POST)
    if not form.is_valid():
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "errors": form.errors}, status=400)
        messages.error(request, "Исправьте ошибки в форме.")
        return render(
            request,
            "shop/product_detail.html",
            {"product": product, "form": form, "show_modal": True},
        )

    pr = form.save(commit=False)
    pr.product = product
    if request.user.is_authenticated:
        pr.user = request.user
    pr.save()

    try:
        notify_purchase_request(pr)
    except Exception as e:
        logger.warning("Telegram notify for purchase request failed: %s", e)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "message": "Заявка отправлена!"})
    messages.success(request, "Заявка отправлена! Мы свяжемся с вами.")
    return redirect("shop_product_detail", pk=product_id)
