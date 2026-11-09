// No inline scripts: the CSP only allows this file.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-autosubmit]").forEach((el) =>
    el.addEventListener("change", () => el.form.submit()));

  document.querySelectorAll("form[data-confirm]").forEach((form) =>
    form.addEventListener("submit", (ev) => {
      if (!confirm(form.dataset.confirm)) ev.preventDefault();
    }));

  // Connect OneDrive: go on to Microsoft by navigation, not a form redirect
  // (form-action would block that). The link works without JavaScript too.
  const signIn = document.querySelector("a[data-auto-follow]");
  if (signIn) window.location.assign(signIn.href);

  document.querySelectorAll("[data-copy]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(btn.dataset.copy);
        btn.textContent = "Copied";
      } catch (e) {
        btn.textContent = "Select the address above and copy it";
      }
    }));

  // Import page: it opens at once, then reads the folder's PDFs one at a time while saying what it is
  // doing, instead of the browser waiting on one slow request (scanned PDFs take several seconds each).
  const importForm = document.getElementById("import-form");
  // The button says what it will do: nothing to import but some to skip is "Skip 3", not "Import ticked".
  const ticked = (prefix) => importForm.querySelectorAll(`input[name^=${prefix}_]:checked`).length;
  const importLabel = () => {
    const [n, s] = [ticked("include"), ticked("skip")];
    return n && s ? `Import ${n} and skip ${s}` : s ? `Skip ${s}` : "Import ticked";
  };
  const relabel = () => {
    const button = importForm.querySelector("button[type=submit]");
    if (button && !button.disabled) button.textContent = importLabel();
  };
  if (importForm) {
    importForm.addEventListener("change", relabel);
    relabel();
  }

  // Pressing Import with files left neither ticked to import nor to skip: offer to skip them, so they
  // stop turning up. Registered first, so the ticks are made before the form is sent.
  if (importForm) importForm.addEventListener("submit", () => {
    const left = [...importForm.querySelectorAll("input[name^=include_]")]
      .map((box) => ({ box, skip: importForm.elements["skip_" + box.name.slice(8)] }))
      .filter((r) => r.skip && !r.box.checked && !r.skip.checked);
    if (left.length && confirm(
      `${left.length} file${left.length === 1 ? " isn't" : "s aren't"} ticked to import. ` +
      `Skip ${left.length === 1 ? "it" : "them"} so ${left.length === 1 ? "it stops" : "they stop"} being offered?\n\n` +
      `OK skips ${left.length === 1 ? "it" : "them"}; Cancel leaves ${left.length === 1 ? "it" : "them"} for next time.`)) {
      left.forEach((r) => { r.skip.checked = true; });
      relabel();
    }
  });
  if (importForm && importForm.dataset.scanUrl) runImport(importForm);

  async function runImport(f) {
    const text = document.getElementById("import-status-text");
    const bar = document.getElementById("import-progress");
    const rows = f.querySelector("tbody");
    const submit = f.querySelector("button[type=submit]");
    // Say what is happening now, and how long this step has taken once it's noticeable.
    let current = "";
    let since = Date.now();
    const render = () => {
      const seconds = Math.floor((Date.now() - since) / 1000);
      text.textContent = current + (ticking && seconds >= 3 ? ` (${seconds} s)` : "");
    };
    let ticking = true;
    const ticker = setInterval(render, 1000);
    const say = (msg) => { current = msg; since = Date.now(); render(); };
    const stop = (msg, link) => {
      ticking = false;
      clearInterval(ticker);
      bar.hidden = true;
      say(msg);
      if (link) {
        const a = document.createElement("a");
        a.href = link;
        a.textContent = " Go to the OneDrive page";
        text.append(a);
      }
    };

    // Skipping a file means not importing it: ticking one clears the other.
    f.addEventListener("change", (e) => {
      const m = /^(include|skip)_(\d+)$/.exec(e.target.name || "");
      if (m && e.target.checked) {
        const other = f.elements[(m[1] === "skip" ? "include_" : "skip_") + m[2]];
        if (other) other.checked = false;
      }
    });

    f.addEventListener("submit", () => {
      submit.disabled = true;
      submit.textContent = ticked("include") ? "Importing…" : "Saving…";
    });

    let scan;
    try {
      const res = await fetch(f.dataset.scanUrl);
      scan = await res.json();
      if (!res.ok) return stop(scan.error || "Couldn't look in the folder.", scan.url);
    } catch (e) {
      return stop("Couldn't look in the folder. Reload the page to try again.");
    }
    if (!scan.files.length) {
      return stop(scan.listed ? "Every PDF in the folder already has an expense." : "No PDFs found in that folder.");
    }

    const total = scan.files.length;
    bar.max = total;
    bar.value = 0;
    f.hidden = false;
    for (let i = 0; i < total; i++) {
      const file = scan.files[i];
      say(`Reading ${i + 1} of ${total}: ${file.name}` +
          (file.size > 300000 ? " (large or scanned PDFs take a little longer)" : ""));
      try {
        const body = new FormData();
        body.append("n", i);
        ["id", "name", "folder", "url", "size"].forEach((k) => body.append(k, file[k]));
        const res = await fetch(f.dataset.rowUrl, { method: "POST", body });
        if (!res.ok) throw new Error("HTTP " + res.status);
        rows.insertAdjacentHTML("beforeend", await res.text());
      } catch (e) {
        const tr = rows.insertRow();
        const td = tr.insertCell();
        td.colSpan = 8;
        td.textContent = `Couldn't read ${file.name} (${e.message}). Reload the page to try again.`;
      }
      bar.value = i + 1;
    }
    f.elements.count.value = total;
    submit.disabled = false;
    f.dispatchEvent(new Event("change"));
    ticking = false;
    clearInterval(ticker);
    bar.hidden = true;
    say(`Read ${total} PDF${total === 1 ? "" : "s"}. Check each row, then press Import.`);
  }

  const form = document.getElementById("expense-form");
  if (!form) return;

  // An upload can take a few seconds: say so, and stop a second click sending it twice.
  function markSending() {
    const file = form.elements.invoice_file;
    const save = form.querySelector("button[type=submit]");
    if (file && file.files.length) {
      save.disabled = true;
      save.textContent = "Uploading…";
    }
  }

  // Adding: ask the server whether this looks like an expense already recorded (same car and amount,
  // close dates) before anything is sent, so a chosen PDF isn't lost and nothing is uploaded twice.
  // The server checks again, so this only makes the question smoother.
  const dupBox = document.getElementById("duplicate-warning");
  let confirmed = false;
  function sendNow() {
    confirmed = true;
    markSending();
    form.submit();
  }
  form.addEventListener("submit", async (ev) => {
    if (!form.dataset.duplicatesUrl || confirmed || form.elements.confirm_duplicate.value) {
      markSending();
      return;
    }
    ev.preventDefault();
    const { car_id, date, time, amount } = form.elements;
    let matches = [];
    try {
      const q = new URLSearchParams({ car_id: car_id.value, date: date.value, time: time.value, amount: amount.value });
      const res = await fetch(form.dataset.duplicatesUrl + "?" + q);
      if (res.ok) matches = (await res.json()).matches;
    } catch (e) { /* the server will ask if it matters */ }
    if (!matches.length) return sendNow();
    const list = document.getElementById("duplicate-list");
    list.textContent = "";
    matches.forEach((m) => {
      const li = document.createElement("li");
      li.append([m.date, m.category, m.vendor, m.amount_text].filter(Boolean).join(" · ") + " — ");
      const a = document.createElement("a");
      a.href = m.edit_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = "open it";
      li.append(a);
      list.append(li);
    });
    dupBox.hidden = false;
    dupBox.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  const dupSave = document.getElementById("duplicate-save");   // only in the inline version of the warning
  const dupCancel = document.getElementById("duplicate-cancel");
  if (dupSave) {
    dupSave.addEventListener("click", () => {
      form.elements.confirm_duplicate.value = "1";      // tells the server too: it checks again
      sendNow();
    });
  }
  if (dupCancel) dupCancel.addEventListener("click", () => { dupBox.hidden = true; });
  const odo = form.elements.odometer_km;
  const hint = document.getElementById("odometer-hint");

  async function fill(manual) {
    const { car_id, date, time } = form.elements;
    if (!car_id.value || !date.value || !time.value) {
      if (manual) hint.textContent = "Pick the car, date and time first.";
      return;
    }
    const q = new URLSearchParams({ car_id: car_id.value, date: date.value, time: time.value });
    try {
      const res = await fetch(form.dataset.odometerUrl + "?" + q);
      const data = await res.json();
      if (data.odometer_km == null) {
        hint.textContent = "TeslaMate has no position at or before that time.";
      } else {
        odo.value = data.odometer_km;
        hint.textContent = "Filled from TeslaMate.";
      }
    } catch (e) {
      hint.textContent = "Lookup failed.";
    }
  }

  document.getElementById("fill-odometer").addEventListener("click", () => fill(true));

  // Choosing the invoice PDF fills in what can be read from it, but never a field the person
  // has already typed in or changed.
  const pdfInput = form.elements.invoice_file;
  const prefillHint = document.getElementById("prefill-hint");
  const touched = new Set();
  ["car_id", "category_id", "date", "time", "amount", "odometer_km", "vendor"].forEach((name) => {
    const el = form.elements[name];
    if (!el) return;
    ["input", "change"].forEach((ev) => el.addEventListener(ev, () => touched.add(name)));
  });
  if (pdfInput && prefillHint && form.dataset.prefillUrl) {
    pdfInput.addEventListener("change", async () => {
      if (!pdfInput.files.length) return;
      prefillHint.textContent = "Reading the invoice…";
      try {
        const body = new FormData();
        body.append("invoice_file", pdfInput.files[0]);
        const res = await fetch(form.dataset.prefillUrl, { method: "POST", body });
        const data = await res.json();
        if (!res.ok) {
          prefillHint.textContent = data.error || "Couldn't read that file.";
          return;
        }
        const filled = [];
        const set = (name, label, value) => {
          if (value == null || value === "" || touched.has(name)) return;
          form.elements[name].value = value;
          filled.push(label);
        };
        set("category_id", "category", data.category_id);
        set("date", "date", data.date);
        set("amount", "amount", data.amount);
        set("vendor", "vendor", data.vendor);
        set("odometer_km", "odometer", data.odometer_km);
        if (data.odometer_km && filled.includes("odometer")) hint.textContent = "Read from the invoice.";
        else if (!data.odometer_km && !touched.has("odometer_km")) await fill(false);
        prefillHint.textContent = filled.length
          ? "Filled from the invoice: " + filled.join(", ") + ". Check them before saving."
          : "Couldn't read anything from that PDF; fill the fields in.";
      } catch (e) {
        prefillHint.textContent = "Couldn't read the invoice.";
      }
    });
  }
  // New expense with nothing typed yet: fill automatically.
  if (!odo.value) fill(false);
});
