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
    function setupUserDropdown(toggleId, dropdownId) {
        const userMenuToggle = document.getElementById(toggleId);
        const userDropdown = document.getElementById(dropdownId);
        if (!userMenuToggle || !userDropdown) return;
        const dropdown = userMenuToggle.closest('.nav-dropdown');
        if (!dropdown) return;
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
    setupUserDropdown('user-menu-toggle', 'user-dropdown');
    setupUserDropdown('club-user-menu-toggle', 'club-user-dropdown');

    // Auto-hide alerts after 5 seconds
    document.querySelectorAll('.alert').forEach(function(el) {
        var messagesContainer = el.closest('.messages-container');
        if (messagesContainer && messagesContainer.dataset.persistMessages === 'true') {
            return;
        }
        setTimeout(function() {
            el.style.transition = 'opacity 0.3s ease';
            el.style.opacity = '0';
            el.addEventListener('transitionend', function() {
                el.remove();
            }, { once: true });
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
    var cards = document.querySelectorAll('.main .card, .main .match-card');
    var staggerMs = 100;
    var pendingReveal = false;

    function sortByPosition(nodes) {
        var arr = Array.prototype.slice.call(nodes);
        var rects = new Map();
        arr.forEach(function(el) { rects.set(el, el.getBoundingClientRect()); });
        return arr.sort(function(a, b) {
            var ra = rects.get(a), rb = rects.get(b);
            var rowA = Math.round(ra.top / 30), rowB = Math.round(rb.top / 30);
            if (rowA !== rowB) return rowA - rowB;
            return ra.left - rb.left;
        });
    }

    function revealBatch() {
        pendingReveal = false;
        var winH = window.innerHeight;
        var toReveal = [];
        cards.forEach(function(card) {
            if (card.classList.contains('card-in-view')) return;
            var r = card.getBoundingClientRect();
            if (r.top < winH + 60) toReveal.push(card);
        });
        if (toReveal.length === 0) return;
        toReveal = sortByPosition(toReveal);
        toReveal.forEach(function(card, i) {
            card.style.transitionDelay = (i * staggerMs) / 1000 + 's';
            card.classList.add('card-in-view');
            if (observer) observer.unobserve(card);
            card.addEventListener('transitionend', function onEnd() {
                card.removeEventListener('transitionend', onEnd);
                card.style.willChange = 'auto';
            }, { once: true });
        });
    }

    var observer = null;
    if (cards.length && 'IntersectionObserver' in window) {
        observer = new IntersectionObserver(function(entries) {
            var hasNew = entries.some(function(e) { return e.isIntersecting; });
            if (hasNew && !pendingReveal) {
                pendingReveal = true;
                requestAnimationFrame(revealBatch);
            }
        }, { rootMargin: '0px 0px -40px 0px', threshold: 0.01 });
        cards.forEach(function(card) {
            card.style.willChange = 'transform, opacity';
            observer.observe(card);
        });
        requestAnimationFrame(revealBatch);
    } else {
        cards.forEach(function(card) {
            card.classList.add('card-in-view');
        });
    }

    // Footer accordion: на desktop открыт только один раздел, на mobile поведение прежнее
    var footerSections = Array.from(document.querySelectorAll('.footer-section'));
    function isDesktopFooter() {
        return window.matchMedia('(min-width: 769px)').matches;
    }
    function setFooterSectionState(section, isOpen) {
        var sectionBtn = section.querySelector('[data-footer-toggle]');
        section.classList.toggle('is-open', isOpen);
        if (sectionBtn) sectionBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    }
    function initDesktopFooterState() {
        if (!footerSections.length) return;
        if (isDesktopFooter()) {
            footerSections.forEach(function(section) {
                setFooterSectionState(section, false);
            });
            return;
        }
        footerSections.forEach(function(section) {
            setFooterSectionState(section, false);
        });
    }
    footerSections.forEach(function(section) {
        var btn = section.querySelector('[data-footer-toggle]');
        if (!btn) return;
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            var willOpen = !section.classList.contains('is-open');
            if (isDesktopFooter()) {
                footerSections.forEach(function(otherSection) {
                    setFooterSectionState(otherSection, false);
                });
                if (willOpen) {
                    setFooterSectionState(section, true);
                }
                return;
            }
            var isOpen = section.classList.toggle('is-open');
            btn.setAttribute('aria-expanded', isOpen === true ? 'true' : 'false');
        });
    });
    initDesktopFooterState();
    window.addEventListener('resize', initDesktopFooterState);

    // Home tournaments: AJAX фильтры и пагинация
    var tournamentsForm = document.getElementById('home-tournaments-filter');
    var tournamentsBlock = document.getElementById('home-tournaments-block');
    var tournamentsSection = document.querySelector('.section-tournaments-spaced');

    if (tournamentsForm && tournamentsBlock) {
        var baseUrl = tournamentsForm.getAttribute('action') || window.location.pathname;

        function attachPaginationHandlers() {
            tournamentsBlock.querySelectorAll('[data-page-link="home-tournaments"]').forEach(function(link) {
                link.addEventListener('click', function(e) {
                    e.preventDefault();
                    var url = new URL(this.href, window.location.origin);
                    url.searchParams.set('partial', 'tournaments');
                    loadTournaments(url.toString(), true);
                });
            });
        }

        function updateUrlWithoutPartial(fullUrl) {
            try {
                var url = new URL(fullUrl, window.location.origin);
                url.searchParams.delete('partial');
                history.pushState({ homeTournaments: true }, '', url.pathname + (url.search ? url.search : ''));
            } catch (e) {
                // ignore
            }
        }

        function scrollToTournaments() {
            var target = tournamentsSection || tournamentsBlock;
            if (!target) return;
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }

        function loadTournaments(url, pushState) {
            tournamentsBlock.classList.add('is-loading');
            fetch(url, {
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                }
            })
                .then(function(response) { return response.text(); })
                .then(function(html) {
                    tournamentsBlock.innerHTML = html;
                    tournamentsBlock.classList.remove('is-loading');
                    if (pushState) {
                        updateUrlWithoutPartial(url);
                    }
                    attachPaginationHandlers();
                    scrollToTournaments();
                })
                .catch(function() {
                    tournamentsBlock.classList.remove('is-loading');
                });
        }

        tournamentsForm.addEventListener('submit', function(e) {
            e.preventDefault();
            var formData = new FormData(tournamentsForm);
            var params = new URLSearchParams(formData);
            params.set('partial', 'tournaments');
            var url = baseUrl + '?' + params.toString();
            loadTournaments(url, true);
        });

        var resetLink = document.querySelector('[data-home-tournaments-reset="1"]');
        if (resetLink) {
            resetLink.addEventListener('click', function(e) {
                e.preventDefault();
                tournamentsForm.reset();
                var url = baseUrl + '?partial=tournaments';
                loadTournaments(url, true);
            });
        }

        attachPaginationHandlers();

        window.addEventListener('popstate', function() {
            var url = new URL(window.location.href);
            url.searchParams.set('partial', 'tournaments');
            loadTournaments(url.toString(), false);
        });
    }

    // Club dashboard: bottom nav "Ещё" sheet (mobile)
    (function() {
        var bottomNav = document.querySelector('.club-bottom-nav');
        var sheet = document.getElementById('club-bottom-sheet');
        if (!bottomNav || !sheet) return;

        var moreBtn = bottomNav.querySelector('[data-club-bottom-more]');
        var backdrop = sheet.querySelector('[data-club-bottom-close]');

        function openSheet() {
            sheet.classList.add('is-open');
            sheet.setAttribute('aria-hidden', 'false');
        }

        function closeSheet() {
            sheet.classList.remove('is-open');
            sheet.setAttribute('aria-hidden', 'true');
        }

        if (moreBtn) {
            moreBtn.addEventListener('click', function(e) {
                e.preventDefault();
                openSheet();
            });
        }

        if (backdrop) {
            backdrop.addEventListener('click', function() {
                closeSheet();
            });
        }

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                closeSheet();
            }
        });
    })();
});
