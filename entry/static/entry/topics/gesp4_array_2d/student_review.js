function setupStudentReview() {
  const shell = document.querySelector("[data-student-review-shell]");
  if (!shell) {
    return;
  }

  const defaultKey = shell.dataset.defaultSection || "overview";
  const panels = [...shell.querySelectorAll("[data-review-panel]")];
  const panelMap = new Map(panels.map((panel) => [panel.dataset.reviewPanel, panel]));
  const navItems = [...shell.querySelectorAll(".review-nav-button[data-review-target]")];
  const select = shell.querySelector("[data-review-select]");
  const main = shell.querySelector("[data-review-main]");

  const syncActiveControls = (activeKey) => {
    navItems.forEach((button) => {
      const isActive = button.dataset.reviewTarget === activeKey;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-current", isActive ? "page" : "false");
    });

    if (select) {
      select.value = activeKey;
    }
  };

  const updateHash = (activeKey) => {
    try {
      window.history.replaceState(null, "", `#${activeKey}`);
    } catch (_error) {
      window.location.hash = activeKey;
    }
  };

  const activatePanel = (activeKey, { updateLocation = true, scrollToTop = true } = {}) => {
    const resolvedKey = panelMap.has(activeKey) ? activeKey : defaultKey;

    panels.forEach((panel) => {
      const isActive = panel.dataset.reviewPanel === resolvedKey;
      panel.hidden = !isActive;
      panel.classList.toggle("is-active", isActive);
    });

    syncActiveControls(resolvedKey);

    if (updateLocation) {
      updateHash(resolvedKey);
    }

    if (scrollToTop) {
      (main || shell).scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  };

  shell.addEventListener("click", (event) => {
    const button = event.target.closest("[data-review-target]");
    if (!button || !shell.contains(button)) {
      return;
    }

    const targetKey = button.dataset.reviewTarget;
    if (!targetKey) {
      return;
    }

    activatePanel(targetKey);
  });

  if (select) {
    select.addEventListener("change", () => {
      activatePanel(select.value);
    });
  }

  const initialHash = window.location.hash.replace("#", "");
  activatePanel(initialHash || defaultKey, {
    updateLocation: Boolean(initialHash),
    scrollToTop: false,
  });
}

function setupVisibilityToggles() {
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-visibility-toggle]");
    if (!button) {
      return;
    }

    const panelId = button.dataset.panelId;
    if (!panelId) {
      return;
    }

    const panel = document.getElementById(panelId);
    if (!panel) {
      return;
    }

    const shouldShow = panel.hasAttribute("hidden");
    if (shouldShow) {
      panel.removeAttribute("hidden");
    } else {
      panel.setAttribute("hidden", "");
    }

    button.setAttribute("aria-expanded", shouldShow ? "true" : "false");
    button.textContent = shouldShow
      ? button.dataset.labelHide || "隐藏"
      : button.dataset.labelShow || "显示";
  });
}

function init() {
  setupStudentReview();
  setupVisibilityToggles();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}
