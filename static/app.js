function setScanProgress(progressBox, update) {
  const stage = progressBox.querySelector("[data-scan-stage]");
  const count = progressBox.querySelector("[data-scan-count]");
  const detail = progressBox.querySelector("[data-scan-detail]");
  const bar = progressBox.querySelector("[data-scan-bar]");

  if (update.stage) {
    stage.textContent = update.stage;
  }

  const scanned = Number(update.scanned || 0);
  const total = update.total === null || update.total === undefined ? null : Number(update.total);
  count.textContent = total ? `${scanned} / ${total} scannet` : `${scanned} scannet`;

  if (total && total > 0) {
    bar.style.width = `${Math.min(100, Math.round((scanned / total) * 100))}%`;
  } else if (update.state === "running") {
    bar.style.width = "18%";
  } else {
    bar.style.width = "0%";
  }

  if (update.state === "complete") {
    progressBox.classList.remove("scan-progress-error");
    progressBox.classList.add("scan-progress-success");
    bar.style.width = "100%";
    detail.textContent = `Fandt ${update.found || 0} trackingnumre. ${update.new_shipments || 0} nye pakker gemt. Opdaterer oversigten...`;
  } else if (update.state === "error") {
    progressBox.classList.remove("scan-progress-success");
    progressBox.classList.add("scan-progress-error");
    detail.textContent = update.error || "Scanningen mislykkedes.";
  } else {
    progressBox.classList.remove("scan-progress-success", "scan-progress-error");
    const accountText = update.account_label ? ` for ${update.account_label}` : "";
    detail.textContent = `FjordParcel scanner den valgte mailkonto${accountText} og checker moenstre for DAO, PostNord, Bring og GLS.`;
  }
}

function toggleShipmentDetails(row) {
  const details = row.parentElement.querySelector("[data-shipment-details]");
  if (!details) {
    return;
  }

  const isExpanded = row.getAttribute("aria-expanded") === "true";
  row.setAttribute("aria-expanded", String(!isExpanded));
  details.hidden = isExpanded;
}

function pollScanStatus(form, progressBox, jobId) {
  const statusTemplate = form.dataset.scanStatusUrlTemplate;
  const statusUrl = statusTemplate.replace("__JOB_ID__", jobId);

  const poll = async () => {
    const response = await fetch(statusUrl, {
      headers: { Accept: "application/json" },
    });
    const payload = await response.json();
    if (!payload.ok) {
      throw new Error(payload.error || "Could not read scan status.");
    }

    const job = payload.job;
    setScanProgress(progressBox, job);

    if (job.state === "complete") {
      window.setTimeout(() => window.location.reload(), 2200);
      return;
    }
    if (job.state === "error") {
      form.querySelector("button[type='submit']").disabled = false;
      return;
    }

    window.setTimeout(poll, 800);
  };

  poll().catch((error) => {
    setScanProgress(progressBox, {
      state: "error",
      stage: "Scan failed",
      error: error.message,
      scanned: 0,
      total: null,
    });
    form.querySelector("button[type='submit']").disabled = false;
  });
}

function bindShipmentInteractions(root = document) {
  root.querySelectorAll("[data-shipment-toggle]").forEach((row) => {
    if (row.dataset.shipmentToggleBound) {
      return;
    }
    row.dataset.shipmentToggleBound = "true";

    row.addEventListener("click", (event) => {
      if (event.target.closest("a, button, input, select, textarea, form, label")) {
        return;
      }
      toggleShipmentDetails(row);
    });

    row.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }
      if (event.target.closest("a, button, input, select, textarea, form, label")) {
        return;
      }
      event.preventDefault();
      toggleShipmentDetails(row);
    });
  });

  root.querySelectorAll("[data-rename-toggle]").forEach((button) => {
    if (button.dataset.renameToggleBound) {
      return;
    }
    button.dataset.renameToggleBound = "true";

    button.addEventListener("click", () => {
      const row = button.closest(".shipment-row");
      const form = row.querySelector(".rename-form");
      const input = form.querySelector("input[name='label']");
      form.hidden = !form.hidden;
      button.hidden = !form.hidden;
      if (!form.hidden) {
        input.focus();
        input.select();
      }
    });
  });

  root.querySelectorAll(".rename-form input[name='label']").forEach((input) => {
    if (input.dataset.renameEscapeBound) {
      return;
    }
    input.dataset.renameEscapeBound = "true";

    input.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") {
        return;
      }
      const form = input.closest(".rename-form");
      const row = input.closest(".shipment-row");
      const button = row.querySelector("[data-rename-toggle]");
      form.hidden = true;
      button.hidden = false;
      button.focus();
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindShipmentInteractions();

  document.querySelectorAll(".archive-switch").forEach((archiveSwitch) => {
    archiveSwitch.addEventListener("click", async (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      if (archiveSwitch.dataset.busy === "true") {
        event.preventDefault();
        return;
      }

      event.preventDefault();
      const href = archiveSwitch.href;
      const isOn = archiveSwitch.getAttribute("aria-checked") === "true";
      const nextIsOn = !isOn;
      const shouldReduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const container = document.querySelector("[data-archived-container]");
      const archivedSection = container?.querySelector("[data-archived-section]");
      archiveSwitch.dataset.busy = "true";
      archiveSwitch.classList.toggle("is-on", nextIsOn);
      archiveSwitch.setAttribute("aria-checked", String(nextIsOn));

      const finish = () => {
        const nextUrl = new URL(window.location.href);
        if (nextIsOn) {
          nextUrl.searchParams.delete("archived");
          archiveSwitch.href = nextUrl.toString();
          archiveSwitch.setAttribute("aria-label", "Skjul arkiverede");
        } else {
          nextUrl.searchParams.set("archived", "1");
          archiveSwitch.href = nextUrl.toString();
          archiveSwitch.setAttribute("aria-label", "Vis arkiverede");
        }
        archiveSwitch.dataset.busy = "false";
      };

      if (!container) {
        window.location.href = href;
        return;
      }

      try {
        if (nextIsOn) {
          const response = await fetch(href, { headers: { Accept: "text/html" } });
          const html = await response.text();
          const documentFragment = new DOMParser().parseFromString(html, "text/html");
          const nextSection = documentFragment.querySelector("[data-archived-section]");
          container.classList.remove("is-open", "is-closing");
          container.replaceChildren();
          if (nextSection) {
            container.appendChild(nextSection);
            bindShipmentInteractions(container);
            window.requestAnimationFrame(() => {
              container.classList.add("is-open");
            });
          }
          window.history.pushState({}, "", href);
          finish();
          return;
        }

        if (archivedSection) {
          container.classList.add("is-closing");
          container.classList.remove("is-open");
          await new Promise((resolve) => window.setTimeout(resolve, shouldReduceMotion ? 0 : 420));
          container.replaceChildren();
          container.classList.remove("is-closing");
        }
        window.history.pushState({}, "", href);
        finish();
      } catch (_error) {
        window.location.href = href;
      }
    });
  });

  window.addEventListener("popstate", () => {
    window.location.reload();
  });

  const scanForm = document.querySelector("[data-scan-start-url]");
  if (!scanForm) {
    return;
  }

  scanForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const progressBox = scanForm.querySelector("[data-scan-progress]");
    const submitButton = scanForm.querySelector("button[type='submit']");
    progressBox.hidden = false;
    submitButton.disabled = true;
    setScanProgress(progressBox, {
      state: "running",
      stage: "Starter scanning",
      scanned: 0,
      total: null,
    });

    try {
      const response = await fetch(scanForm.dataset.scanStartUrl, {
        method: "POST",
        body: new FormData(scanForm),
        headers: {
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
      });
      const payload = await response.json();
      if (!payload.ok) {
        throw new Error(payload.error || "Could not start scan.");
      }
      pollScanStatus(scanForm, progressBox, payload.job_id);
    } catch (error) {
      setScanProgress(progressBox, {
        state: "error",
        stage: "Scan failed",
        error: error.message,
        scanned: 0,
        total: null,
      });
      submitButton.disabled = false;
    }
  });
});
