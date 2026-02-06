/**
 * TennisFan - Main JavaScript
 */

document.addEventListener('DOMContentLoaded', function() {
    // Mobile navigation toggle
    const navToggle = document.getElementById('nav-toggle');
    const navMenuMobile = document.getElementById('nav-menu-mobile');

    if (navToggle && navMenuMobile) {
        navToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            navMenuMobile.classList.toggle('active');
            navToggle.classList.toggle('active');
        });

        document.addEventListener('click', function(e) {
            if (!navToggle.contains(e.target) && !navMenuMobile.contains(e.target)) {
                navMenuMobile.classList.remove('active');
                navToggle.classList.remove('active');
            }
        });

        navMenuMobile.querySelectorAll('.nav-menu-link').forEach(function(link) {
            link.addEventListener('click', function() {
                navMenuMobile.classList.remove('active');
                navToggle.classList.remove('active');
            });
        });
    }

    // User dropdown toggle (desktop hover + mobile click via .open)
    const userMenuToggle = document.getElementById('user-menu-toggle');
    const userDropdown = document.getElementById('user-dropdown');

    if (userMenuToggle && userDropdown) {
        const dropdown = userMenuToggle.closest('.nav-dropdown');
        if (dropdown) {
            userMenuToggle.addEventListener('click', function(e) {
                e.stopPropagation();
                e.preventDefault();
                const isOpen = dropdown.classList.contains('open');
                document.querySelectorAll('.nav-dropdown').forEach(function(d) {
                    d.classList.remove('open');
                });
                if (!isOpen) {
                    dropdown.classList.add('open');
                }
            });

            document.addEventListener('click', function(e) {
                if (!dropdown.contains(e.target)) {
                    dropdown.classList.remove('open');
                }
            });
        }
    }

    // Auto-hide alerts after 5 seconds
    document.querySelectorAll('.alert').forEach(function(alert) {
        setTimeout(function() {
            alert.style.opacity = '0';
            alert.style.transition = 'opacity 0.3s ease';
            setTimeout(function() {
                alert.remove();
            }, 300);
        }, 5000);
    });

    // Smooth scroll for in-page anchor links
    document.querySelectorAll('a[href^="#"]').forEach(function(anchor) {
        anchor.addEventListener('click', function(e) {
            const targetId = this.getAttribute('href');
            if (targetId && targetId.length > 1) {
                e.preventDefault();
                const target = document.querySelector(targetId);
                if (target) {
                    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            }
        });
    });

    // Карточки: появление снизу вверх по очереди слева направо
    var cards = document.querySelectorAll('.main .card');
    var winH = window.innerHeight;
    var staggerMs = 120;
    function inViewport(el) {
        var r = el.getBoundingClientRect();
        return r.top < winH + 60;
    }
    function sortByPosition(nodes) {
        return Array.prototype.slice.call(nodes).sort(function(a, b) {
            var ra = a.getBoundingClientRect();
            var rb = b.getBoundingClientRect();
            var rowA = Math.round(ra.top / 30);
            var rowB = Math.round(rb.top / 30);
            if (rowA !== rowB) return rowA - rowB;
            return ra.left - rb.left;
        });
    }
    function revealBatch() {
        var toReveal = [];
        cards.forEach(function(card) {
            if (card.classList.contains('card-in-view')) return;
            if (inViewport(card)) toReveal.push(card);
        });
        if (toReveal.length === 0) return;
        toReveal = sortByPosition(toReveal);
        toReveal.forEach(function(card, i) {
            card.style.transitionDelay = (i * staggerMs) / 1000 + 's';
            card.classList.add('card-in-view');
        });
    }
    if (cards.length && 'IntersectionObserver' in window) {
        var observer = new IntersectionObserver(function(entries) {
            var hasNew = entries.some(function(e) { return e.isIntersecting; });
            if (hasNew) requestAnimationFrame(revealBatch);
        }, { rootMargin: '0px 0px -60px 0px', threshold: 0.01 });
        cards.forEach(function(card) {
            observer.observe(card);
        });
        requestAnimationFrame(revealBatch);
    } else {
        cards.forEach(function(card) {
            card.classList.add('card-in-view');
        });
    }
});
