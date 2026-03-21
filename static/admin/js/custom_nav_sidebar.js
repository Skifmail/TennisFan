document.addEventListener("DOMContentLoaded", function () {
  const sidebar = document.getElementById("nav-sidebar");
  if (!sidebar) return;

  const accordion = sidebar.querySelector("[data-admin-nav-accordion]");
  if (!accordion) return;

  const filterInput = document.getElementById("nav-filter");
  const mobileToggle = document.getElementById("admin-nav-mobile-toggle");
  const mobilePanel = document.getElementById("admin-nav-mobile-panel");
  const sections = Array.from(
    accordion.querySelectorAll("[data-admin-nav-section]")
  );
  const triggers = Array.from(
    accordion.querySelectorAll("[data-admin-nav-trigger]")
  );

  function openSection(sectionToOpen) {
    sections.forEach((section) => {
      const trigger = section.querySelector("[data-admin-nav-trigger]");
      const panel = section.querySelector("[data-admin-nav-panel]");
      const isActive = section === sectionToOpen;
      if (trigger) {
        trigger.setAttribute("aria-expanded", isActive ? "true" : "false");
      }
      if (panel) {
        panel.hidden = !isActive;
      }
    });
  }

  const currentSection = accordion
    .querySelector(".admin-nav-accordion__item--current")
    ?.closest("[data-admin-nav-section]");
  if (currentSection) {
    openSection(currentSection);
  } else if (sections[0]) {
    openSection(sections[0]);
  }

  function setMobilePanel(open) {
    if (!mobileToggle || !mobilePanel) return;
    mobileToggle.setAttribute("aria-expanded", open ? "true" : "false");
    mobilePanel.classList.toggle("is-open", open);
  }

  if (mobileToggle && mobilePanel) {
    mobileToggle.addEventListener("click", function () {
      const expanded = mobileToggle.getAttribute("aria-expanded") === "true";
      setMobilePanel(!expanded);
    });
  }

  triggers.forEach((trigger) => {
    trigger.addEventListener("click", function () {
      const section = trigger.closest("[data-admin-nav-section]");
      if (!section) return;
      const expanded = trigger.getAttribute("aria-expanded") === "true";
      openSection(expanded ? null : section);
    });
  });

  if (filterInput) {
    filterInput.addEventListener("input", function () {
      const query = filterInput.value.trim().toLowerCase();

      sections.forEach((section) => {
        const title = (
          section.querySelector("[data-admin-nav-trigger] span")?.textContent || ""
        ).toLowerCase();
        const items = Array.from(
          section.querySelectorAll(".admin-nav-accordion__item")
        );

        let hasMatch = title.includes(query) && query !== "";
        items.forEach((item) => {
          const text = (item.textContent || "").toLowerCase();
          const match = !query || text.includes(query) || title.includes(query);
          item.hidden = !match;
          if (match) hasMatch = true;
        });

        section.hidden = !hasMatch;
        if (query && hasMatch) {
          openSection(section);
        }
      });

      if (mobileToggle && mobilePanel && query) {
        setMobilePanel(true);
      }
    });
  }

  accordion.querySelectorAll(".admin-nav-accordion__item-link").forEach((link) => {
    link.addEventListener("click", function () {
      if (window.innerWidth <= 767) {
        setMobilePanel(false);
      }
    });
  });
});
