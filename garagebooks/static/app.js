// No inline scripts: the CSP only allows this file.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form:not([data-live]) [data-autosubmit]").forEach((el) =>
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
        a.textContent = " Go to the " + (f.dataset.setupLabel || "Invoice storage page");
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

  // Expense list: rows (a table) or tiles (a card each). view.js has already applied the saved choice
  // (phones get tiles until one is made); this wires the buttons. Filtering and sorting swap the list in
  // place instead of reloading the page, so nothing flashes and the scroll position stays put.
  const root = document.documentElement;
  const markView = () => document.querySelectorAll("[data-view-choice]").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.viewChoice === root.dataset.view)));
  function bindList() {
    markView();
    document.querySelectorAll("[data-view-choice]").forEach((b) =>
      b.addEventListener("click", () => {
        root.dataset.view = b.dataset.viewChoice;
        try { localStorage.setItem("expenses-view", root.dataset.view); } catch (e) { /* not remembered */ }
        markView();
      }));
    bindColumns();
    const live = document.querySelector("form[data-live]");
    if (!live) return;
    live.querySelectorAll("[data-autosubmit]").forEach((el) =>
      el.addEventListener("change", (ev) => {
        showList(location.pathname + "?" + new URLSearchParams(new FormData(live)), el.name);
      }));
    document.querySelectorAll("a.sort").forEach((a) =>
      a.addEventListener("click", (ev) => {
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return;
        ev.preventDefault();
        showList(a.href, null);
      }));
  }
  // Rows: drag the edge of a column heading (or focus it and use the arrow keys) to change that column's
  // width; double-click it to go back to the built-in widths. The other columns keep theirs, so a wider
  // column makes the table wider. The widths are remembered in the browser, like the layout.
  function bindColumns() {
    const cols = window.expenseColumns;
    const heads = [...document.querySelectorAll(".list thead th")];
    if (!cols || heads.length !== cols.names.length) return;
    const measured = () => Object.fromEntries(heads.map((th, i) => [cols.names[i], Math.round(th.getBoundingClientRect().width)]));
    const keep = (widths) => {
      try { localStorage.setItem("expenses-columns", JSON.stringify(widths)); } catch (e) { /* not remembered */ }
    };
    heads.slice(0, -1).forEach((th, i) => {
      const name = cols.names[i];
      const handle = document.createElement("span");
      handle.className = "col-resize";
      handle.tabIndex = 0;
      handle.setAttribute("role", "separator");
      handle.setAttribute("aria-orientation", "vertical");
      handle.setAttribute("aria-label", "Width of the " + (th.textContent.trim().replace(/\s*[▲▼]$/, "") || "last") + " column");
      handle.title = "Drag to change the column's width. Double-click for the usual widths.";
      const set = (widths, value) => { widths[name] = Math.max(56, Math.min(1200, Math.round(value))); cols.apply(widths); };
      handle.addEventListener("pointerdown", (ev) => {
        if (ev.button) return;
        ev.preventDefault();
        const widths = measured();
        const from = widths[name];
        const x0 = ev.clientX;
        handle.setPointerCapture(ev.pointerId);
        handle.classList.add("dragging");
        const move = (e) => set(widths, from + e.clientX - x0);
        const done = () => {
          handle.removeEventListener("pointermove", move);
          handle.classList.remove("dragging");
          keep(widths);
        };
        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", done, { once: true });
        handle.addEventListener("pointercancel", done, { once: true });
      });
      handle.addEventListener("keydown", (ev) => {
        if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
        ev.preventDefault();
        const widths = measured();
        set(widths, widths[name] + (ev.key === "ArrowRight" ? 16 : -16));
        keep(widths);
      });
      handle.addEventListener("dblclick", () => {
        try { localStorage.removeItem("expenses-columns"); } catch (e) { /* nothing kept */ }
        cols.apply(null);
      });
      th.append(handle);
    });
  }
  let listRequest = 0;
  async function showList(url, focusName, push = true) {
    const mine = ++listRequest;
    try {
      const res = await fetch(url, { headers: { Accept: "text/html" } });
      if (!res.ok) throw new Error(res.status);
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      // Asked for something newer meanwhile (Back then Forward, two quick changes): that answer is the
      // one to show, whichever arrives first.
      if (mine !== listRequest) return;
      const fresh = doc.querySelector("main");
      if (!fresh) throw new Error("no list");
      // The table scrolls sideways on a phone: keep it where it was, rather than snapping back to the left.
      const sideways = document.querySelector(".table-wrap")?.scrollLeft || 0;
      document.querySelector("main").replaceWith(fresh);
      const wrap = document.querySelector(".table-wrap");
      if (wrap) wrap.scrollLeft = sideways;
      if (push) history.pushState(null, "", url);
      bindList();
      const again = focusName && document.querySelector(`.filter [name=${focusName}]`);
      if (again) again.focus({ preventScroll: true });
    } catch (e) {
      window.location.assign(url);               // anything unexpected: an ordinary page load
    }
  }
  window.addEventListener("popstate", () => { if (document.querySelector("form[data-live]")) showList(location.href, null, false); });
  if (document.querySelector("form[data-live]")) bindList();

  const form = document.getElementById("expense-form");
  if (!form) return;

  // Amount: type a sum such as 120.50+89.95 or 400/3 (or use the keys under it) and it is worked out when
  // you leave the field, press Enter, or save. Numbers, + - * / x ÷ × and brackets only: nothing is ever
  // evaluated as code. The server still checks the final figure.
  const amountInput = form.elements.amount;
  const calcNote = document.getElementById("amount-calc");
  function calculate(text) {
    const src = text.replace(/[$,\s]/g, "").replace(/[×x]/gi, "*").replace(/÷/g, "/").replace(/[−–]/g, "-");
    let pos = 0;
    const fail = () => { throw new Error("bad sum"); };
    const number = () => {
      const m = /^\d*\.?\d+|^\d+\.?/.exec(src.slice(pos));
      if (!m) fail();
      pos += m[0].length;
      return parseFloat(m[0]);
    };
    const factor = () => {
      if (src[pos] === "-") { pos++; return -factor(); }
      if (src[pos] === "(") {
        pos++;
        const v = expr();
        if (src[pos++] !== ")") fail();
        return v;
      }
      return number();
    };
    const term = () => {
      let v = factor();
      while (src[pos] === "*" || src[pos] === "/") v = src[pos++] === "*" ? v * factor() : v / factor();
      return v;
    };
    const expr = () => {
      let v = term();
      while (src[pos] === "+" || src[pos] === "-") v = src[pos++] === "+" ? v + term() : v - term();
      return v;
    };
    try {
      const v = expr();
      if (pos !== src.length || !Number.isFinite(v)) return null;
      return Math.round((v + Math.sign(v) * 1e-9) * 100) / 100;
    } catch (e) {
      return null;
    }
  }
  const isSum = (text) => /[-+*/×÷()]|\dx\d/i.test(text.replace(/^\s*[$-]?\s*/, ""));
  function settleAmount() {
    if (!isSum(amountInput.value)) return;
    const v = calculate(amountInput.value);
    if (v === null) return;                    // left as typed: the server explains what's wrong
    amountInput.value = v.toFixed(2);
    calcNote.textContent = "";
  }
  function previewAmount() {
    const v = isSum(amountInput.value) ? calculate(amountInput.value) : null;
    calcNote.textContent = v === null ? "" : `= $${v.toLocaleString("en-AU", { minimumFractionDigits: 2 })}`;
  }
  if (amountInput && calcNote) {
    amountInput.addEventListener("input", previewAmount);
    amountInput.addEventListener("blur", settleAmount);
    amountInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && isSum(amountInput.value) && calculate(amountInput.value) !== null) {
        ev.preventDefault();                   // show the answer first; Enter again saves
        settleAmount();
      }
    });
    form.addEventListener("submit", settleAmount);   // registered before the duplicate check, so it sees the figure
    const keys = document.getElementById("amount-keys");
    keys.hidden = false;
    keys.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.key === "=") {
        settleAmount();
      } else {
        const [from, to] = [amountInput.selectionStart ?? amountInput.value.length, amountInput.selectionEnd ?? amountInput.value.length];
        amountInput.setRangeText(b.dataset.key, from, to, "end");
        previewAmount();
      }
      amountInput.focus();
    }));
  }

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

  // Dropping a PDF onto the invoice box is the same as choosing it with the button.
  const dropBox = document.getElementById("invoice-drop");
  if (dropBox && pdfInput) {
    document.getElementById("invoice-drop-hint").hidden = false;
    ["dragenter", "dragover"].forEach((name) => dropBox.addEventListener(name, (ev) => {
      ev.preventDefault();
      dropBox.classList.add("over");
    }));
    ["dragleave", "drop"].forEach((name) => dropBox.addEventListener(name, () => dropBox.classList.remove("over")));
    dropBox.addEventListener("drop", (ev) => {
      ev.preventDefault();
      const pdf = [...((ev.dataTransfer && ev.dataTransfer.files) || [])]
        .find((f) => f.type === "application/pdf" || /\.pdf$/i.test(f.name));
      if (!pdf) return;
      const chosen = new DataTransfer();
      chosen.items.add(pdf);
      pdfInput.files = chosen.files;
      pdfInput.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  // Choose a PDF that is already in the car's storage (OneDrive, or the invoice folder on the server):
  // a window over the form, so there's no page to come back from. It opens in the car's folder and can
  // go up from there. The file isn't copied: it becomes the expense's invoice (its link, or its path in
  // the folder), and what can be read in it fills the form as a chosen file would.
  const picker = document.getElementById("onedrive-picker");
  const pickButton = document.getElementById("onedrive-pick");
  const browseUrls = { onedrive: form.dataset.onedriveBrowseUrl, local: form.dataset.localBrowseUrl };
  if (picker && pickButton && (browseUrls.onedrive || browseUrls.local)) {
    // The storage the chosen car's folder is in; a car without one gets the installation's own.
    const storageOf = () => {
      const option = form.elements.car_id.selectedOptions && form.elements.car_id.selectedOptions[0];
      const own = option && option.dataset.storage;
      if (browseUrls[own]) return own;
      if (browseUrls[form.dataset.defaultStorage]) return form.dataset.defaultStorage;
      return browseUrls.onedrive ? "onedrive" : "local";
    };
    const showStorage = () => document.querySelectorAll("[data-storage-label]").forEach((el) => {
      el.hidden = el.dataset.storageLabel !== storageOf();
    });
    showStorage();
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
      const kind = storageOf();
      const key = kind + "/" + car + "/" + (folder || "");
      if (!seen.has(key)) {
        const url = browseUrls[kind].replace(/0$/, car) + (folder ? "?folder=" + encodeURIComponent(folder) : "");
        const asked = fetch(url).then(async (res) => {
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || (kind === "local" ? "Couldn't open the invoice folder." : "Couldn't open OneDrive."));
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
      if (pdfInput) pdfInput.value = "";      // the file picked here replaces any chosen on this computer
      const kind = storageOf();
      if (kind === "local") {
        // No link to fill in: the expense keeps the file's path, and the app serves it.
        const stored = document.getElementById("stored-invoice");
        const link = document.getElementById("stored-invoice-link");
        form.elements.invoice_path.value = file.id;
        form.elements.remove_invoice.checked = false;
        link.textContent = file.name;
        link.href = stored.dataset.fileUrl + "?path=" + encodeURIComponent(file.id);
        stored.hidden = false;
      } else {
        form.elements.invoice_url.value = file.web_url;
      }
      pickHint.textContent = "Linked: " + file.name;
      if (prefillHint && form.dataset.prefillUrl) {
        const body = new FormData();
        body.append(kind + "_id", file.id);
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
    form.elements.car_id.addEventListener("change", () => { seen = new Map(); showStorage(); if (picker.open) show(null); });
  }
  // New expense with nothing typed yet: fill automatically.
  if (!odo.value) fill(false);
});
