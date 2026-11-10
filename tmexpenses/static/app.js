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
  // Fill the form from what /api/prefill read in a PDF; `body` is the form data to send it.
  async function prefillFrom(body) {
    prefillHint.textContent = "Reading the invoice…";
    try {
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
  }
  if (pdfInput && prefillHint && form.dataset.prefillUrl) {
    pdfInput.addEventListener("change", () => {
      if (!pdfInput.files.length) return;
      const body = new FormData();
      body.append("invoice_file", pdfInput.files[0]);
      prefillFrom(body);
    });
  }

  // Choose a PDF that is already in OneDrive: a window over the form, so there's no page to come back
  // from. It opens in the car's folder and can go up from there. The file isn't copied: its link becomes
  // the invoice link, and what can be read in it fills the form as a chosen file would.
  const picker = document.getElementById("onedrive-picker");
  const pickButton = document.getElementById("onedrive-pick");
  if (picker && pickButton && form.dataset.onedriveBrowseUrl) {
    const pickHint = document.getElementById("onedrive-pick-hint");
    const status = document.getElementById("onedrive-picker-status");
    const pathBox = document.getElementById("onedrive-picker-path");
    const list = document.getElementById("onedrive-picker-list");
    let loading = 0;

    const entry = (label, onClick) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "linklike";
      b.textContent = label;
      b.addEventListener("click", onClick);
      li.append(b);
      list.append(li);
    };
    const kb = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " kB");

    // Folders already fetched (as promises, so a click on one still loading just waits for it). Cleared each
    // time the window opens, so what it shows is never older than that.
    let seen = new Map();
    const fetchFolder = (car, folder) => {
      const key = car + "/" + (folder || "");
      if (!seen.has(key)) {
        const url = form.dataset.onedriveBrowseUrl.replace(/0$/, car) + (folder ? "?folder=" + encodeURIComponent(folder) : "");
        const asked = fetch(url).then(async (res) => {
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || "Couldn't open OneDrive.");
          return data;
        });
        seen.set(key, asked);
        asked.catch(() => seen.delete(key));      // a failure is tried again next time
      }
      return seen.get(key);
    };

    async function show(folder) {
      const ticket = ++loading;
      status.textContent = "Loading…";
      list.textContent = "";
      const car = form.elements.car_id.value;
      if (!car) { status.textContent = "Pick the car first."; pathBox.textContent = ""; return; }
      let data;
      try {
        data = await fetchFolder(car, folder);
      } catch (e) {
        if (ticket === loading) status.textContent = e.message;
        return;
      }
      if (ticket !== loading) return;      // a newer click has replaced this listing
      pathBox.textContent = data.path;
      status.textContent = data.folders.length || data.files.length ? "" : "No folders or PDFs in here.";
      if (!data.is_root) entry("⬑ Up", () => show(data.parent_id || "root"));
      data.folders.forEach((f) => entry("📁 " + f.name, () => show(f.id)));
      data.files.forEach((f) => entry("📄 " + f.name + " (" + kb(f.size) + ")", () => choose(f)));
      // Going up is the likeliest next move: have the folder above ready while this one is read.
      if (!data.is_root) fetchFolder(car, data.parent_id || "root").catch(() => {});
    }

    function choose(file) {
      picker.close();
      if (pdfInput) pdfInput.value = "";      // the OneDrive file replaces any chosen on this computer
      form.elements.invoice_url.value = file.web_url;
      pickHint.textContent = "Linked: " + file.name;
      if (prefillHint && form.dataset.prefillUrl) {
        const body = new FormData();
        body.append("onedrive_id", file.id);
        body.append("name", file.name);
        prefillFrom(body);
      }
    }

    pickButton.addEventListener("click", () => {
      seen = new Map();
      picker.showModal();
      show(null);
    });
    document.getElementById("onedrive-picker-close").addEventListener("click", () => picker.close());
    // The car decides where the window starts: a different car means a different folder.
    form.elements.car_id.addEventListener("change", () => { seen = new Map(); if (picker.open) show(null); });
  }
  // New expense with nothing typed yet: fill automatically.
  if (!odo.value) fill(false);
});
