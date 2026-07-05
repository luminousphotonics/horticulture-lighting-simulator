// @ts-check

import { closeAboutModal } from "./renderers.js";
import { els } from "./state.js";

export const DEMO_GUIDE_STORAGE_KEY = "horticulture-lighting-simulator.demoGuide.preference.v2";
export const DEMO_GUIDE_PREFERENCES = Object.freeze({
  ask: "ask",
  completed: "completed",
  dismissed: "dismissed",
});

const TOUR_STEPS = [
  {
    title: "Choose a system",
    body: "Start with Proposed LED, Conventional LED, or 1000W HPS to compare the research layout against practical baselines.",
    selector: "#rad-mode",
  },
  {
    title: "Choose room size and target",
    body: "Set the room and target PPFD. Hosted production uses precomputed public bundles for fast, safe demo playback.",
    selector: "#radiance-form",
  },
  {
    title: "Run the simulation",
    body: "Run + Visualize loads matching Radiance results, metrics, heatmaps, and artifact links when a precomputed bundle is available.",
    selector: "#btn-rad-all",
  },
  {
    title: "Review PPFD results",
    body: "The visual panels show Viridis PPFD maps, while the metrics panel summarizes intensity and uniformity.",
    selector: ".radiance-visuals",
  },
  {
    title: "Open 3D Assembly",
    body: "After a completed run, View 3D Assembly opens the optimized GLB CAD viewer for the selected system.",
    selector: "#btn-rad-assembly",
  },
  {
    title: "Explore the 3D viewer",
    body: "Inside the viewer, orbit, pan, zoom, adjust fixture height, toggle fixtures, and switch on the PPFD heatmap layer.",
    selector: "#btn-rad-assembly",
  },
  {
    title: "View technical context",
    body: "Use About for engineering highlights and GitHub for the source code, reproducibility scripts, and deployment notes.",
    selector: ".navbar__links",
  },
];

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "input:not([disabled])",
  "a[href]",
].join(",");

const ALLOWED_DEMO_GUIDE_PREFERENCES = /** @type {string[]} */ (Object.values(DEMO_GUIDE_PREFERENCES));

/**
 * @returns {Storage | null}
 */
function browserStorage() {
  try {
    return window.localStorage;
  } catch (_err) {
    return null;
  }
}

/**
 * @param {Storage | null=} storage
 * @returns {string}
 */
export function getDemoGuidePreference(storage = browserStorage()) {
  if (!storage) {
    return DEMO_GUIDE_PREFERENCES.ask;
  }
  try {
    const value = storage.getItem(DEMO_GUIDE_STORAGE_KEY);
    if (ALLOWED_DEMO_GUIDE_PREFERENCES.includes(value || "")) {
      return value || DEMO_GUIDE_PREFERENCES.ask;
    }
  } catch (_err) {
    return DEMO_GUIDE_PREFERENCES.ask;
  }
  return DEMO_GUIDE_PREFERENCES.ask;
}

/**
 * @param {string} preference
 * @param {Storage | null=} storage
 */
export function setDemoGuidePreference(preference, storage = browserStorage()) {
  if (!ALLOWED_DEMO_GUIDE_PREFERENCES.includes(preference)) {
    return;
  }
  if (!storage) {
    return;
  }
  try {
    storage.setItem(DEMO_GUIDE_STORAGE_KEY, preference);
  } catch (_err) {
    // localStorage may be unavailable in hardened browser contexts.
  }
}

/**
 * @param {Storage | null=} storage
 * @returns {boolean}
 */
export function shouldShowDemoGuidePrompt(storage = browserStorage()) {
  return getDemoGuidePreference(storage) === DEMO_GUIDE_PREFERENCES.ask;
}

function visibleFocusableElements(root) {
  return Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR))
    .filter((element) => element instanceof HTMLElement && !element.hasAttribute("hidden") && element.offsetParent !== null);
}

export function initDemoGuide() {
  const guide = new DemoGuideController();
  guide.init();
  return guide;
}

class DemoGuideController {
  constructor() {
    /** @type {HTMLElement | null} */
    this.root = null;
    /** @type {HTMLElement | null} */
    this.card = null;
    /** @type {HTMLElement | null} */
    this.highlight = null;
    /** @type {HTMLElement | null} */
    this.eyebrow = null;
    /** @type {HTMLElement | null} */
    this.title = null;
    /** @type {HTMLElement | null} */
    this.body = null;
    /** @type {HTMLElement | null} */
    this.progress = null;
    /** @type {HTMLLabelElement | null} */
    this.rememberLabel = null;
    /** @type {HTMLInputElement | null} */
    this.rememberInput = null;
    /** @type {HTMLButtonElement | null} */
    this.backButton = null;
    /** @type {HTMLButtonElement | null} */
    this.nextButton = null;
    /** @type {HTMLButtonElement | null} */
    this.skipButton = null;
    /** @type {HTMLElement | null} */
    this.opener = null;
    this.stepIndex = 0;
    this.startedWithRemember = false;
    this.mode = "";
    this.keydownHandler = this.onKeydown.bind(this);
    this.resizeHandler = this.syncTourPosition.bind(this);
  }

  init() {
    this.createDom();
    this.bindEvents();
    if (shouldShowDemoGuidePrompt()) {
      window.setTimeout(() => this.openPrompt(), 350);
    }
  }

  createDom() {
    const root = document.createElement("div");
    root.id = "demo-guide-root";
    root.className = "demo-guide hidden";
    root.setAttribute("aria-live", "polite");

    const highlight = document.createElement("div");
    highlight.className = "demo-guide__highlight";
    highlight.setAttribute("aria-hidden", "true");

    const card = document.createElement("section");
    card.className = "demo-guide__card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "false");
    card.setAttribute("aria-labelledby", "demo-guide-title");
    card.tabIndex = -1;

    const eyebrow = document.createElement("p");
    eyebrow.className = "demo-guide__eyebrow";

    const title = document.createElement("h2");
    title.id = "demo-guide-title";

    const body = document.createElement("p");
    body.className = "demo-guide__body";

    const progress = document.createElement("p");
    progress.className = "demo-guide__progress";

    const rememberLabel = document.createElement("label");
    rememberLabel.className = "demo-guide__remember";
    const rememberInput = document.createElement("input");
    rememberInput.type = "checkbox";
    rememberInput.id = "demo-guide-remember";
    const rememberText = document.createElement("span");
    rememberText.textContent = "Remember my choice";
    rememberLabel.append(rememberInput, rememberText);

    const actions = document.createElement("div");
    actions.className = "demo-guide__actions";

    const backButton = document.createElement("button");
    backButton.className = "btn btn--secondary";
    backButton.type = "button";
    backButton.textContent = "Back";

    const skipButton = document.createElement("button");
    skipButton.className = "btn btn--secondary";
    skipButton.type = "button";
    skipButton.textContent = "Skip";

    const nextButton = document.createElement("button");
    nextButton.className = "btn btn--primary";
    nextButton.type = "button";
    nextButton.textContent = "Next";

    actions.append(backButton, skipButton, nextButton);
    card.append(eyebrow, title, body, progress, rememberLabel, actions);
    root.append(highlight, card);
    document.body.append(root);

    this.root = root;
    this.card = card;
    this.highlight = highlight;
    this.eyebrow = eyebrow;
    this.title = title;
    this.body = body;
    this.progress = progress;
    this.rememberLabel = rememberLabel;
    this.rememberInput = rememberInput;
    this.backButton = backButton;
    this.nextButton = nextButton;
    this.skipButton = skipButton;
  }

  bindEvents() {
    this.nextButton?.addEventListener("click", () => {
      if (this.mode === "prompt") {
        this.startTour({ remember: Boolean(this.rememberInput?.checked) });
        return;
      }
      this.nextStep();
    });
    this.backButton?.addEventListener("click", () => {
      if (this.mode === "prompt") {
        this.close({ preference: this.rememberInput?.checked ? DEMO_GUIDE_PREFERENCES.dismissed : "" });
        return;
      }
      this.previousStep();
    });
    this.skipButton?.addEventListener("click", () => {
      this.close({ preference: this.startedWithRemember ? DEMO_GUIDE_PREFERENCES.completed : "" });
    });
    els.btnDemoGuideManual?.addEventListener("click", () => {
      closeAboutModal();
      window.setTimeout(() => this.startTour({ manual: true }), 80);
    });
    document.addEventListener("keydown", this.keydownHandler, true);
    window.addEventListener("resize", this.resizeHandler);
    window.addEventListener("scroll", this.resizeHandler, true);
  }

  openPrompt() {
    if (document.querySelector(".radiance-modal:not(.hidden)")) {
      window.setTimeout(() => this.openPrompt(), 700);
      return;
    }
    this.opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    this.mode = "prompt";
    this.startedWithRemember = false;
    this.root?.classList.remove("hidden", "demo-guide--tour");
    this.root?.classList.add("demo-guide--prompt");
    this.highlight?.setAttribute("hidden", "");
    if (this.eyebrow) {
      this.eyebrow.textContent = "First-run guide";
    }
    if (this.title) {
      this.title.textContent = "Would you like a quick walkthrough?";
    }
    if (this.body) {
      this.body.textContent = "It will show how to run a precomputed simulation, compare systems, open the 3D assembly viewer, and toggle the PPFD heatmap.";
    }
    if (this.progress) {
      this.progress.textContent = "";
    }
    if (this.rememberLabel) {
      this.rememberLabel.hidden = false;
    }
    if (this.rememberInput) {
      this.rememberInput.checked = false;
    }
    if (this.backButton) {
      this.backButton.textContent = "Not now";
      this.backButton.setAttribute("aria-label", "Not now");
      this.backButton.hidden = false;
    }
    if (this.skipButton) {
      this.skipButton.hidden = true;
    }
    if (this.nextButton) {
      this.nextButton.textContent = "Start walkthrough";
      this.nextButton.setAttribute("aria-label", "Start walkthrough");
    }
    this.focusCard();
  }

  /**
   * @param {{remember?: boolean, manual?: boolean}=} options
   */
  startTour(options = {}) {
    this.opener = document.activeElement instanceof HTMLElement ? document.activeElement : this.opener;
    this.startedWithRemember = Boolean(options.remember);
    if (this.startedWithRemember) {
      setDemoGuidePreference(DEMO_GUIDE_PREFERENCES.completed);
    }
    this.mode = "tour";
    this.stepIndex = 0;
    this.root?.classList.remove("hidden", "demo-guide--prompt");
    this.root?.classList.add("demo-guide--tour");
    this.highlight?.removeAttribute("hidden");
    if (this.rememberLabel) {
      this.rememberLabel.hidden = true;
    }
    if (this.skipButton) {
      this.skipButton.hidden = false;
      this.skipButton.textContent = "Skip";
      this.skipButton.setAttribute("aria-label", "Skip walkthrough");
    }
    this.renderStep();
    if (options.manual) {
      this.focusCard();
    }
  }

  renderStep() {
    const step = TOUR_STEPS[this.stepIndex];
    if (!step) {
      this.close({ preference: this.startedWithRemember ? DEMO_GUIDE_PREFERENCES.completed : "" });
      return;
    }
    if (this.eyebrow) {
      this.eyebrow.textContent = "Demo guide";
    }
    if (this.title) {
      this.title.textContent = step.title;
    }
    if (this.body) {
      this.body.textContent = step.body;
    }
    if (this.progress) {
      this.progress.textContent = `Step ${this.stepIndex + 1} of ${TOUR_STEPS.length}`;
    }
    if (this.backButton) {
      this.backButton.textContent = "Back";
      this.backButton.setAttribute("aria-label", "Back");
      this.backButton.hidden = false;
      this.backButton.disabled = this.stepIndex === 0;
    }
    if (this.nextButton) {
      const finalStep = this.stepIndex === TOUR_STEPS.length - 1;
      this.nextButton.textContent = finalStep ? "Done" : "Next";
      this.nextButton.setAttribute("aria-label", finalStep ? "Done with walkthrough" : "Next walkthrough step");
    }
    this.syncTourPosition();
    this.focusCard();
  }

  syncTourPosition() {
    if (this.mode !== "tour" || !this.root || !this.card || !this.highlight) {
      return;
    }
    const step = TOUR_STEPS[this.stepIndex];
    const target = step?.selector ? document.querySelector(step.selector) : null;
    let rect = target instanceof HTMLElement ? target.getBoundingClientRect() : null;
    if (target instanceof HTMLElement) {
      target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
      rect = target.getBoundingClientRect();
    }
    window.requestAnimationFrame(() => {
      const currentRect = target instanceof HTMLElement ? target.getBoundingClientRect() : rect;
      this.placeTourElements(currentRect);
    });
  }

  /**
   * @param {DOMRect | null} rect
   */
  placeTourElements(rect) {
    if (!this.card || !this.highlight) {
      return;
    }
    const margin = 16;
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const highlightRect = rect && rect.width > 0 && rect.height > 0
      ? {
          left: Math.max(margin, rect.left - 8),
          top: Math.max(margin, rect.top - 8),
          width: Math.min(viewportWidth - margin * 2, rect.width + 16),
          height: Math.min(viewportHeight - margin * 2, rect.height + 16),
        }
      : {
          left: viewportWidth / 2 - 80,
          top: viewportHeight / 2 - 40,
          width: 160,
          height: 80,
        };
    Object.assign(this.highlight.style, {
      left: `${highlightRect.left}px`,
      top: `${highlightRect.top}px`,
      width: `${highlightRect.width}px`,
      height: `${highlightRect.height}px`,
    });

    const cardWidth = Math.min(380, viewportWidth - margin * 2);
    const cardHeight = this.card.offsetHeight || 260;
    const rightSide = highlightRect.left + highlightRect.width + margin;
    const leftSide = highlightRect.left - cardWidth - margin;
    let left = rightSide + cardWidth <= viewportWidth - margin ? rightSide : leftSide;
    if (left < margin) {
      left = Math.min(Math.max(margin, highlightRect.left), viewportWidth - cardWidth - margin);
    }
    let top = Math.min(Math.max(margin, highlightRect.top), viewportHeight - cardHeight - margin);
    if (viewportWidth <= 720) {
      left = margin;
      top = viewportHeight - cardHeight - margin;
    }
    Object.assign(this.card.style, {
      width: `${cardWidth}px`,
      left: `${left}px`,
      top: `${top}px`,
    });
  }

  nextStep() {
    if (this.stepIndex >= TOUR_STEPS.length - 1) {
      this.close({ preference: this.startedWithRemember ? DEMO_GUIDE_PREFERENCES.completed : "" });
      return;
    }
    this.stepIndex += 1;
    this.renderStep();
  }

  previousStep() {
    if (this.stepIndex === 0) {
      return;
    }
    this.stepIndex -= 1;
    this.renderStep();
  }

  /**
   * @param {{preference?: string}=} options
   */
  close(options = {}) {
    if (options.preference) {
      setDemoGuidePreference(options.preference);
    }
    this.mode = "";
    this.root?.classList.add("hidden");
    this.root?.classList.remove("demo-guide--prompt", "demo-guide--tour");
    this.highlight?.setAttribute("hidden", "");
    if (this.backButton) {
      this.backButton.disabled = false;
    }
    if (this.card) {
      this.card.removeAttribute("style");
    }
    if (this.opener?.isConnected) {
      this.opener.focus({ preventScroll: true });
    }
  }

  focusCard() {
    if (!this.card) {
      return;
    }
    window.setTimeout(() => {
      if (document.body?.classList.contains("site-nav-open")) {
        return;
      }
      const [first] = visibleFocusableElements(this.card || document.body);
      if (first instanceof HTMLElement) {
        first.focus({ preventScroll: true });
      } else {
        this.card?.focus({ preventScroll: true });
      }
    }, 0);
  }

  /**
   * @param {KeyboardEvent} event
   */
  onKeydown(event) {
    if (!this.mode || this.root?.classList.contains("hidden")) {
      return;
    }
    if (document.body?.classList.contains("site-nav-open")) {
      return;
    }
    if (document.querySelector(".radiance-modal:not(.hidden)")) {
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      this.close({ preference: this.startedWithRemember ? DEMO_GUIDE_PREFERENCES.completed : "" });
      return;
    }
    if (event.key !== "Tab" || !this.card) {
      return;
    }
    const candidates = visibleFocusableElements(this.card);
    if (!candidates.length) {
      return;
    }
    const first = candidates[0];
    const last = candidates[candidates.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus({ preventScroll: true });
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus({ preventScroll: true });
    }
  }
}
