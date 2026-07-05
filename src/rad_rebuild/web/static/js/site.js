// @ts-check

(function () {
    "use strict";

    const root = document.documentElement;
    const storageKey = "site-theme";
    const navToggle = document.querySelector("[data-nav-toggle]");
    const navPanel = document.querySelector("[data-nav-panel]");
    const navLinks = Array.from(document.querySelectorAll("[data-nav-links] a"));
    const themeButtons = Array.from(document.querySelectorAll("[data-theme-toggle]"));

    function preferredTheme() {
        try {
            const stored = window.localStorage.getItem(storageKey);
            if (stored === "light" || stored === "dark") {
                return stored;
            }
        } catch (_err) {
            // Ignore storage failures and use the product default.
        }
        return "dark";
    }

    function syncThemeButtons(theme) {
        themeButtons.forEach((button) => {
            const label = button.querySelector(".theme-button__label");
            button.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
            if (label) {
                label.textContent = theme === "dark" ? "Dark mode" : "Light mode";
            }
        });
    }

    function applyTheme(theme) {
        const normalized = theme === "light" ? "light" : "dark";
        root.dataset.theme = normalized;
        syncThemeButtons(normalized);
        try {
            window.localStorage.setItem(storageKey, normalized);
        } catch (_err) {
            // Ignore storage failures.
        }
    }

    applyTheme(preferredTheme());

    themeButtons.forEach((button) => {
        button.addEventListener("click", () => {
            applyTheme(root.dataset.theme === "dark" ? "light" : "dark");
        });
    });

    function setNavOpen(open) {
        if (!navToggle || !navPanel) {
            return;
        }
        navToggle.setAttribute("aria-expanded", open ? "true" : "false");
        navPanel.classList.toggle("is-open", open);
        document.body?.classList.toggle("site-nav-open", open);
    }

    function navIsOpen() {
        return Boolean(navToggle && navToggle.getAttribute("aria-expanded") === "true");
    }

    if (navToggle instanceof HTMLElement && navPanel instanceof HTMLElement) {
        navToggle.addEventListener("click", () => {
            setNavOpen(!navIsOpen());
        });

        navLinks.forEach((link) => {
            link.addEventListener("click", () => {
                setNavOpen(false);
            });
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && navIsOpen()) {
                event.preventDefault();
                setNavOpen(false);
                navToggle.focus({ preventScroll: true });
            }
        });

        document.addEventListener("focusin", (event) => {
            if (!navIsOpen()) {
                return;
            }
            const target = event.target;
            if (target instanceof Node && (navPanel.contains(target) || navToggle.contains(target))) {
                return;
            }
            setNavOpen(false);
        });
    }
})();
