import { closeAboutModal, openAboutModal } from "./renderers.js";
import { els } from "./state.js";

export function initAboutActions() {
  if (els.btnAboutOpen) {
    els.btnAboutOpen.addEventListener("click", openAboutModal);
  }
  if (els.aboutModalClose) {
    els.aboutModalClose.addEventListener("click", closeAboutModal);
  }
  if (els.aboutModalCancel) {
    els.aboutModalCancel.addEventListener("click", closeAboutModal);
  }
  if (els.aboutModal) {
    els.aboutModal.addEventListener("click", (event) => {
      if (event.target === els.aboutModal) {
        closeAboutModal();
      }
    });
  }
}
