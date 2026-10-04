/**
 * Автозаполнение карточки тренера из выбранного пользователя.
 */
(function ($) {
    "use strict";

    var FIELD_IDS = {
        name: "id_name",
        phone: "id_phone",
        telegram: "id_telegram",
        whatsapp: "id_whatsapp",
        max_contact: "id_max_contact",
        city: "id_city"
    };

    function defaultsUrl(userId) {
        var match = window.location.pathname.match(/^(.*\/coach\/)/);
        var base = match ? match[1] : "/admin/training/coach/";
        return base + "user-defaults/" + encodeURIComponent(userId) + "/";
    }

    function fillIfEmpty(fieldId, value) {
        var el = document.getElementById(fieldId);
        if (!el || !value) {
            return false;
        }
        if (String(el.value || "").trim()) {
            return false;
        }
        el.value = value;
        return true;
    }

    function fillSlugFromName() {
        var nameEl = document.getElementById("id_name");
        var slugEl = document.getElementById("id_slug");
        if (!nameEl || !slugEl || String(slugEl.value || "").trim() || !nameEl.value) {
            return;
        }
        $(nameEl).trigger("keyup");
        if (String(slugEl.value || "").trim()) {
            return;
        }
        if (typeof window.URLify === "function") {
            slugEl.value = window.URLify(nameEl.value, 50, true);
        }
    }

    function loadDefaults(userId) {
        if (!userId) {
            return;
        }
        fetch(defaultsUrl(userId), {
            credentials: "same-origin",
            headers: { "X-Requested-With": "XMLHttpRequest" }
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("coach-defaults-failed");
                }
                return response.json();
            })
            .then(function (data) {
                var nameFilled = fillIfEmpty(FIELD_IDS.name, data.name);
                fillIfEmpty(FIELD_IDS.phone, data.phone);
                fillIfEmpty(FIELD_IDS.telegram, data.telegram);
                fillIfEmpty(FIELD_IDS.whatsapp, data.whatsapp);
                fillIfEmpty(FIELD_IDS.max_contact, data.max_contact);
                fillIfEmpty(FIELD_IDS.city, data.city);
                if (nameFilled) {
                    fillSlugFromName();
                }
            })
            .catch(function () {
                return null;
            });
    }

    $(function () {
        var $user = $("#id_user");
        if (!$user.length) {
            return;
        }
        $user.on("change", function () {
            loadDefaults(this.value);
        });
        if ($user.val()) {
            loadDefaults($user.val());
        }
    });
})(window.django && window.django.jQuery ? window.django.jQuery : window.jQuery);
