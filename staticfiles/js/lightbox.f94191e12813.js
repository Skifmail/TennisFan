/**
 * Простой лайтбокс: клик по фото открывает всплывающее окно с подписью и листанием.
 * Использование: ссылка с классом js-lightbox, атрибуты data-lightbox-src, data-lightbox-caption, data-lightbox-group.
 */
(function () {
    'use strict';

    var overlay = null;
    var imgEl = null;
    var captionEl = null;
    var currentGroup = [];
    var currentIndex = 0;

    function getOrCreateOverlay() {
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.className = 'lightbox-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', 'Просмотр изображения');
        overlay.innerHTML =
            '<button type="button" class="lightbox-close" aria-label="Закрыть">&times;</button>' +
            '<button type="button" class="lightbox-prev" aria-label="Предыдущее">&lsaquo;</button>' +
            '<button type="button" class="lightbox-next" aria-label="Следующее">&rsaquo;</button>' +
            '<div class="lightbox-content">' +
            '  <img src="" alt="" class="lightbox-img">' +
            '  <p class="lightbox-caption"></p>' +
            '</div>';
        imgEl = overlay.querySelector('.lightbox-img');
        captionEl = overlay.querySelector('.lightbox-caption');

        overlay.addEventListener('click', function (e) {
            if (e.target === overlay || e.target.classList.contains('lightbox-close')) {
                close();
            }
        });
        overlay.querySelector('.lightbox-prev').addEventListener('click', function (e) {
            e.stopPropagation();
            prev();
        });
        overlay.querySelector('.lightbox-next').addEventListener('click', function (e) {
            e.stopPropagation();
            next();
        });
        document.addEventListener('keydown', function (e) {
            if (!overlay.classList.contains('lightbox-open')) return;
            if (e.key === 'Escape') close();
            if (e.key === 'ArrowLeft') prev();
            if (e.key === 'ArrowRight') next();
        });
        document.body.appendChild(overlay);
        return overlay;
    }

    function show(src, caption, group, index) {
        currentGroup = group || [{ src: src, caption: caption || '' }];
        currentIndex = index >= 0 ? index : 0;
        var item = currentGroup[currentIndex];
        var o = getOrCreateOverlay();
        imgEl.src = item.src;
        imgEl.alt = item.caption || '';
        captionEl.textContent = item.caption || '';
        captionEl.style.display = item.caption ? '' : 'none';
        o.querySelector('.lightbox-prev').style.display = currentGroup.length > 1 ? '' : 'none';
        o.querySelector('.lightbox-next').style.display = currentGroup.length > 1 ? '' : 'none';
        o.classList.add('lightbox-open');
        document.body.style.overflow = 'hidden';
        document.body.style.perspective = 'none';
        document.body.style.webkitPerspective = 'none';
        window.scrollTo(0, 0);
    }

    function close() {
        if (!overlay) return;
        overlay.classList.remove('lightbox-open');
        document.body.style.overflow = '';
        document.body.style.perspective = '';
        document.body.style.webkitPerspective = '';
    }

    function prev() {
        if (currentGroup.length <= 1) return;
        currentIndex = (currentIndex - 1 + currentGroup.length) % currentGroup.length;
        var item = currentGroup[currentIndex];
        imgEl.src = item.src;
        imgEl.alt = item.caption || '';
        captionEl.textContent = item.caption || '';
        captionEl.style.display = item.caption ? '' : 'none';
    }

    function next() {
        if (currentGroup.length <= 1) return;
        currentIndex = (currentIndex + 1) % currentGroup.length;
        var item = currentGroup[currentIndex];
        imgEl.src = item.src;
        imgEl.alt = item.caption || '';
        captionEl.textContent = item.caption || '';
        captionEl.style.display = item.caption ? '' : 'none';
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.body.addEventListener('click', function (e) {
            var a = e.target.closest('a.js-lightbox');
            if (!a) return;
            e.preventDefault();
            var src = a.getAttribute('data-lightbox-src') || a.getAttribute('href');
            var caption = a.getAttribute('data-lightbox-caption') || '';
            var groupId = a.getAttribute('data-lightbox-group') || 'default';
            var links = document.querySelectorAll('a.js-lightbox[data-lightbox-group="' + groupId + '"]');
            var group = [];
            var index = 0;
            for (var i = 0; i < links.length; i++) {
                var s = links[i].getAttribute('data-lightbox-src') || links[i].getAttribute('href');
                var c = links[i].getAttribute('data-lightbox-caption') || '';
                if (links[i] === a) index = i;
                group.push({ src: s, caption: c });
            }
            show(src, caption, group, index);
        });
    });
})();
