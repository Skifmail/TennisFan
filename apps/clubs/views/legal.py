"""
Публичная оферта клуба и редактирование владельцем.
"""

from __future__ import annotations

import markdown
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.clubs.forms import ClubLegalDocumentForm
from apps.clubs.models import Club, ClubLegalDocument
from apps.clubs.services import user_can_edit_club_settings, user_can_manage_club


@require_GET
def club_legal_public(request: HttpRequest, club_id: int) -> HttpResponse:
    """Публичная страница оферты клуба (только при is_published)."""
    club = get_object_or_404(Club, pk=club_id)
    doc = ClubLegalDocument.objects.filter(club=club).first()
    if not doc or not doc.is_published:
        raise Http404("Оферта не опубликована")
    html_content = (
        markdown.markdown(doc.content or "", extensions=["extra"])
        if doc.content
        else ""
    )
    return render(
        request,
        "clubs/club_legal_public.html",
        {
            "club": club,
            "content": html_content,
            "title": doc.title,
            "document_version": doc.version,
            "is_club_panel": True,
            "can_manage_club": user_can_manage_club(request.user, club),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def club_legal_edit(request: HttpRequest, club_id: int) -> HttpResponse:
    """Редактирование оферты клуба (только администратор клуба)."""
    club = get_object_or_404(Club, pk=club_id)
    if not user_can_edit_club_settings(request.user, club):
        messages.error(
            request, "Редактировать оферту может только администратор клуба."
        )
        return redirect("clubs:club_public_detail", slug=club.slug)

    doc, _ = ClubLegalDocument.objects.get_or_create(
        club=club,
        defaults={
            "title": f"Публичная оферта клуба «{club.name}»",
            "content": "",
            "version": "1.0",
            "is_published": False,
        },
    )
    if request.method == "POST":
        form = ClubLegalDocumentForm(request.POST, instance=doc)
        if form.is_valid():
            form.save()
            messages.success(request, "Оферта клуба сохранена.")
            return redirect("clubs:club_legal_edit", club_id=club.id)
    else:
        form = ClubLegalDocumentForm(instance=doc)

    return render(
        request,
        "clubs/club_legal_edit.html",
        {
            "club": club,
            "form": form,
            "is_club_panel": True,
        },
    )
