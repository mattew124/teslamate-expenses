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

  const form = document.getElementById("expense-form");
  if (!form) return;

  // An upload can take a few seconds: say so, and stop a second click sending it twice.
  form.addEventListener("submit", () => {
    const file = form.elements.invoice_file;
    const save = form.querySelector("button[type=submit]");
    if (file && file.files.length) {
      save.disabled = true;
      save.textContent = "Uploading…";
    }
  });
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
