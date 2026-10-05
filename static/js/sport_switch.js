/**
 * TennisFan — переключение теннис/падел без перезагрузки страницы.
 */
(function() {
    var pendingController = null;
    var requestId = 0;
    var JSON_SCRIPT_IDS = ['profile-progress-data', 'season-points-data'];

    function sportFromHref(href) {
        try {
            return new URL(href, window.location.href).searchParams.get('sport') || 'tennis';
        } catch (err) {
            return 'tennis';
        }
    }

    function currentSport() {
        try {
            return new URL(window.location.href).searchParams.get('sport') || 'tennis';
        } catch (err) {
            return 'tennis';
        }
    }

    function isHomeSportPage() {
        return Boolean(document.getElementById('home-rating-block'));
    }

    function buildPublicUrl(sport) {
        var url = new URL(window.location.href);
        url.searchParams.set('sport', sport);
        url.searchParams.delete('page');
        url.searchParams.delete('partial');
        url.hash = '';
        return url.pathname + url.search;
    }

    function buildFetchUrl(publicUrl) {
        var url = new URL(publicUrl, window.location.origin);
        if (isHomeSportPage()) {
            url.searchParams.set('partial', 'sport');
        }
        return url.toString();
    }

    function applySwitcherSport(sport) {
        var isPadel = sport === 'padel';
        document.querySelectorAll('.profile-sport-switch').forEach(function(nav) {
            var choices = nav.querySelectorAll('.profile-sport-switch__choice');
            var tennisChoice = choices[0];
            var padelChoice = choices[1];
            var track = nav.querySelector('.profile-sport-switch__track');

            if (tennisChoice) {
                tennisChoice.classList.toggle('is-active', !isPadel);
                if (!isPadel) {
                    tennisChoice.setAttribute('aria-current', 'page');
                } else {
                    tennisChoice.removeAttribute('aria-current');
                }
            }
            if (padelChoice) {
                padelChoice.classList.toggle('is-active', isPadel);
                if (isPadel) {
                    padelChoice.setAttribute('aria-current', 'page');
                } else {
                    padelChoice.removeAttribute('aria-current');
                }
            }
            if (track) {
                track.classList.toggle('is-padel', isPadel);
                track.setAttribute('aria-checked', isPadel ? 'true' : 'false');
                track.setAttribute('href', buildPublicUrl(isPadel ? 'tennis' : 'padel'));
                track.setAttribute(
                    'aria-label',
                    isPadel
                        ? 'Сейчас падел, переключить на теннис'
                        : 'Сейчас теннис, переключить на падел'
                );
            }
        });

        document.querySelectorAll('[data-sport-title]').forEach(function(el) {
            var tennisTitle = el.getAttribute('data-title-tennis');
            var padelTitle = el.getAttribute('data-title-padel');
            if (tennisTitle && padelTitle) {
                el.textContent = isPadel ? padelTitle : tennisTitle;
            }
        });

        var sportInput = document.getElementById('home-filter-sport');
        if (sportInput) {
            sportInput.value = sport;
        }

        var homeTournamentsTitle = document.querySelector(
            '.section-header--home-tournaments .section-title'
        );
        if (homeTournamentsTitle) {
            homeTournamentsTitle.textContent = isPadel ? 'Турниры — падел' : 'Турниры';
        }
        var allTournamentsLink = document.querySelector('.home-tournaments-header__all');
        if (allTournamentsLink) {
            try {
                var allUrl = new URL(allTournamentsLink.href, window.location.origin);
                allUrl.searchParams.set('sport', sport);
                allTournamentsLink.setAttribute('href', allUrl.pathname + allUrl.search);
            } catch (err) {
                allTournamentsLink.setAttribute('href', '/tournaments/?sport=' + sport);
            }
        }
    }

    function parseHtml(html) {
        return new DOMParser().parseFromString(html, 'text/html');
    }

    function replaceJsonScript(fromDoc, scriptId) {
        var incoming = fromDoc.getElementById(scriptId);
        var current = document.getElementById(scriptId);
        if (incoming && current) {
            current.replaceWith(document.importNode(incoming, true));
        } else if (incoming && !current) {
            document.body.appendChild(document.importNode(incoming, true));
        } else if (!incoming && current) {
            current.remove();
        }
    }

    function swapSportFragments(fromDoc) {
        var swapped = false;
        document.querySelectorAll('[data-sport-swap]').forEach(function(el) {
            var key = el.getAttribute('data-sport-swap');
            if (!key) {
                return;
            }
            var incoming = fromDoc.querySelector('[data-sport-swap="' + key + '"]');
            if (!incoming) {
                return;
            }
            el.innerHTML = incoming.innerHTML;
            swapped = true;
        });
        JSON_SCRIPT_IDS.forEach(function(scriptId) {
            replaceJsonScript(fromDoc, scriptId);
        });
        return swapped;
    }

    function setSwapLoading(isLoading) {
        document.querySelectorAll('[data-sport-swap]').forEach(function(el) {
            el.classList.toggle('is-loading', isLoading);
        });
    }

    function revealSwappedCards() {
        document.querySelectorAll('[data-sport-swap] .card, [data-sport-swap] .match-card').forEach(function(card) {
            card.style.transition = 'none';
            card.classList.add('card-in-view');
            card.style.willChange = 'auto';
            card.style.opacity = '1';
            card.style.transform = 'none';
        });
    }

    function switcherIndex(nav) {
        var navs = document.querySelectorAll('.profile-sport-switch');
        return Array.prototype.indexOf.call(navs, nav);
    }

    function switcherAt(index) {
        var navs = document.querySelectorAll('.profile-sport-switch');
        if (index >= 0 && index < navs.length) {
            return navs[index];
        }
        return navs[0] || null;
    }

    function pinSwitcher(switcher, anchorTop) {
        if (!switcher || anchorTop === null || typeof anchorTop === 'undefined') {
            return;
        }
        var delta = switcher.getBoundingClientRect().top - anchorTop;
        if (Math.abs(delta) >= 1) {
            window.scrollBy(0, delta);
        }
    }

    function afterSwap() {
        revealSwappedCards();
        if (window.TennisonProfile) {
            if (typeof window.TennisonProfile.applySubscriptionBars === 'function') {
                window.TennisonProfile.applySubscriptionBars();
            }
            if (typeof window.TennisonProfile.initCharts === 'function') {
                window.TennisonProfile.initCharts();
            }
            if (typeof window.TennisonProfile.alignCharts === 'function') {
                window.TennisonProfile.alignCharts();
            }
        }
        document.dispatchEvent(new CustomEvent('home:tournaments-replaced'));
    }

    function loadSport(publicUrl, options) {
        var opts = options || {};
        var fetchUrl = buildFetchUrl(publicUrl);
        var nextSport = sportFromHref(publicUrl);
        var thisRequest = ++requestId;

        if (pendingController) {
            pendingController.abort();
        }
        pendingController = typeof AbortController === 'function' ? new AbortController() : null;

        applySwitcherSport(nextSport);
        setSwapLoading(true);
        if ('scrollRestoration' in history) {
            history.scrollRestoration = 'manual';
        }
        var clickedSwitcher = opts.switcher || null;
        var clickedIndex = switcherIndex(clickedSwitcher);
        var anchorTop = clickedSwitcher ? clickedSwitcher.getBoundingClientRect().top : null;

        fetch(fetchUrl, {
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            },
            signal: pendingController ? pendingController.signal : undefined
        })
            .then(function(response) {
                if (thisRequest !== requestId) {
                    return null;
                }
                if (!response.ok) {
                    throw new Error('sport-switch-failed');
                }
                return response.text();
            })
            .then(function(html) {
                if (thisRequest !== requestId || html === null) {
                    return;
                }
                if (window.TennisonProfile && typeof window.TennisonProfile.destroyCharts === 'function') {
                    window.TennisonProfile.destroyCharts();
                }
                var swapped = swapSportFragments(parseHtml(html));
                applySwitcherSport(nextSport);
                setSwapLoading(false);
                if (!swapped) {
                    throw new Error('sport-switch-empty');
                }
                if (opts.pushState !== false) {
                    history.pushState({ sportSwitch: true }, '', publicUrl);
                }
                afterSwap();
                if (clickedSwitcher) {
                    pinSwitcher(switcherAt(clickedIndex), anchorTop);
                    requestAnimationFrame(function() {
                        pinSwitcher(switcherAt(clickedIndex), anchorTop);
                    });
                }
            })
            .catch(function(err) {
                if (err && err.name === 'AbortError') {
                    return;
                }
                setSwapLoading(false);
                window.location.assign(publicUrl);
            });
    }

    document.addEventListener('click', function(event) {
        var link = event.target.closest && event.target.closest('.profile-sport-switch a');
        if (!link || event.defaultPrevented) {
            return;
        }
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target === '_blank') {
            return;
        }

        var nextSport = sportFromHref(link.getAttribute('href') || link.href);
        event.preventDefault();
        if (nextSport === currentSport()) {
            return;
        }
        loadSport(buildPublicUrl(nextSport), {
            pushState: true,
            switcher: link.closest('.profile-sport-switch')
        });
    });

    window.addEventListener('popstate', function() {
        if (!document.querySelector('.profile-sport-switch')) {
            return;
        }
        var url = new URL(window.location.href);
        url.searchParams.delete('partial');
        loadSport(url.pathname + url.search, { pushState: false });
    });
})();
