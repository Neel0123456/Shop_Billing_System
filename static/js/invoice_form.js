/* ==========================================================================
   Invoice form behaviour.

   Everything here is convenience only - every rule is enforced again on the
   server in billing/forms.py. If JavaScript is blocked the form still works,
   it just stops showing live totals.

   Responsibilities:
     - add / remove line item rows (Django formset management form upkeep)
     - live per-line and bill totals, with Indian digit grouping
     - show only the payment fields that the chosen status needs
     - auto-fill a returning customer from their phone number
   ========================================================================== */
(function () {
  "use strict";

  var form = document.getElementById("invoice-form");
  if (!form) return;

  var PREFIX = "items";
  var totalFormsInput = document.getElementById("id_" + PREFIX + "-TOTAL_FORMS");
  var body = document.getElementById("items-body");
  var template = document.getElementById("empty-row-template");
  var addButton = document.getElementById("add-row");

  /* ------------------------------------------------------------ helpers */
  function toNumber(value) {
    var n = parseFloat(value);
    return isNaN(n) ? 0 : n;
  }

  function round2(n) {
    return Math.round((n + Number.EPSILON) * 100) / 100;
  }

  /** 1234567.5 -> "12,34,567.50" (Indian grouping, matches the server). */
  function formatINR(value) {
    var negative = value < 0;
    var fixed = Math.abs(round2(value)).toFixed(2);
    var parts = fixed.split(".");
    var whole = parts[0];
    var out;

    if (whole.length > 3) {
      var tail = whole.slice(-3);
      var head = whole.slice(0, -3);
      var groups = [];
      while (head.length > 2) {
        groups.unshift(head.slice(-2));
        head = head.slice(0, -2);
      }
      if (head) groups.unshift(head);
      out = groups.join(",") + "," + tail;
    } else {
      out = whole;
    }
    out = out + "." + parts[1];
    return negative ? "-" + out : out;
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function toggleRow(id, show) {
    var el = document.getElementById(id);
    if (el) el.style.display = show ? "" : "none";
  }

  /* ------------------------------------------------------- row plumbing */
  function visibleRows() {
    return Array.prototype.filter.call(
      body.querySelectorAll("tr.li-row"),
      function (row) {
        return !row.classList.contains("marked-delete");
      }
    );
  }

  function renumberRows() {
    var n = 0;
    Array.prototype.forEach.call(body.querySelectorAll("tr.li-row"), function (row) {
      var label = row.querySelector(".row-number");
      if (row.classList.contains("marked-delete")) {
        if (label) label.textContent = "-";
        return;
      }
      n += 1;
      if (label) label.textContent = n;
    });
    setText("row-count", n);
  }

  function addRow() {
    var index = parseInt(totalFormsInput.value, 10);
    var html = template.innerHTML.replace(/__prefix__/g, index);

    var holder = document.createElement("tbody");
    holder.innerHTML = html.trim();
    var row = holder.querySelector("tr");

    body.appendChild(row);
    totalFormsInput.value = index + 1;

    var qty = row.querySelector(".li-qty");
    if (qty && !qty.value) qty.value = "1";

    renumberRows();
    recalculate();

    var desc = row.querySelector(".li-desc");
    if (desc) desc.focus();
  }

  function removeRow(row) {
    var deleteBox = row.querySelector('input[type="checkbox"][name$="-DELETE"]');
    var isSaved = row.querySelector('input[name$="-id"]');
    var hasId = isSaved && isSaved.value;

    if (hasId && deleteBox) {
      // An existing row: flag it for deletion so the server removes it.
      deleteBox.checked = true;
      row.classList.add("marked-delete");
      row.querySelectorAll("input, select").forEach(function (el) {
        if (el.type !== "checkbox" && !el.name.endsWith("-id")) el.readOnly = true;
      });
    } else if (visibleRows().length > 1) {
      // A brand new row: drop it and reindex the remaining ones.
      row.remove();
      reindexRows();
    } else {
      // Never leave the bill with zero rows; just clear this one.
      row.querySelectorAll("input").forEach(function (el) {
        if (el.type !== "checkbox" && !el.name.endsWith("-id")) el.value = "";
      });
      var qty = row.querySelector(".li-qty");
      if (qty) qty.value = "1";
    }

    renumberRows();
    recalculate();
  }

  /** Rewrite name/id attributes so the formset indexes stay contiguous. */
  function reindexRows() {
    var rows = body.querySelectorAll("tr.li-row");
    Array.prototype.forEach.call(rows, function (row, index) {
      row.querySelectorAll("input, select, textarea, label").forEach(function (el) {
        ["name", "id", "for"].forEach(function (attr) {
          var value = el.getAttribute(attr);
          if (!value) return;
          el.setAttribute(
            attr,
            value.replace(
              new RegExp(PREFIX + "-(\\d+|__prefix__)-"),
              PREFIX + "-" + index + "-"
            )
          );
        });
      });
    });
    totalFormsInput.value = rows.length;
  }

  /* ------------------------------------------------------- calculations */
  function lineTotals(row) {
    var qty = toNumber(row.querySelector(".li-qty") && row.querySelector(".li-qty").value) || 1;
    var rate = toNumber(row.querySelector(".li-rate") && row.querySelector(".li-rate").value);
    var typeEl = row.querySelector(".li-disc-type");
    var valueEl = row.querySelector(".li-disc-value");
    var discountValue = toNumber(valueEl && valueEl.value);
    var gross = round2(qty * rate);
    var discount = 0;

    if (discountValue > 0 && gross > 0) {
      if (typeEl && typeEl.value === "PERCENT") {
        discount = round2((gross * Math.min(discountValue, 100)) / 100);
      } else {
        discount = Math.min(round2(discountValue), gross);
      }
    }
    return { gross: gross, discount: discount, net: round2(gross - discount) };
  }

  function recalculate() {
    var subtotal = 0;
    var discountTotal = 0;

    visibleRows().forEach(function (row) {
      var t = lineTotals(row);
      subtotal += t.gross;
      discountTotal += t.discount;

      var amountEl = row.querySelector(".row-amount");
      var discountEl = row.querySelector(".row-discount");
      if (amountEl) amountEl.textContent = formatINR(t.net);

      // A zero discount is never displayed - same rule as the printed bill.
      if (discountEl) {
        if (t.discount > 0) {
          var typeEl = row.querySelector(".li-disc-type");
          var valueEl = row.querySelector(".li-disc-value");
          discountEl.textContent =
            typeEl && typeEl.value === "PERCENT"
              ? "-" + toNumber(valueEl.value) + "% (-" + formatINR(t.discount) + ")"
              : "-" + formatINR(t.discount);
        } else {
          discountEl.textContent = "";
        }
      }
    });

    subtotal = round2(subtotal);
    discountTotal = round2(discountTotal);
    var total = round2(subtotal - discountTotal);

    setText("t-subtotal", formatINR(subtotal));
    setText("t-discount", formatINR(discountTotal));
    setText("t-total", formatINR(total));
    toggleRow("t-discount-row", discountTotal > 0);

    updatePaymentSummary(total);
  }

  /* ------------------------------------------------- payment status logic */
  var paidInput = document.getElementById("id_paid_amount");
  var dueWrap = document.getElementById("due-wrap");
  var paidWrap = document.getElementById("paid-wrap");

  function selectedStatus() {
    var checked = form.querySelector('input[name="payment_status"]:checked');
    return checked ? checked.value : "PAID";
  }

  function applyStatusUI() {
    var status = selectedStatus();

    form.querySelectorAll(".pay-option").forEach(function (option) {
      option.classList.toggle("selected", option.getAttribute("data-value") === status);
    });

    // Only the fields that this status actually needs are shown.
    var needsPaid = status === "UNDERPAID" || status === "OVERPAID";
    var needsDue = status === "UNDERPAID" || status === "PENDING";

    if (paidWrap) paidWrap.classList.toggle("show", needsPaid);
    if (dueWrap) dueWrap.classList.toggle("show", needsDue);

    if (paidInput) {
      if (status === "PAID") {
        paidInput.value = document.getElementById("t-total").textContent.replace(/,/g, "");
      } else if (status === "PENDING") {
        paidInput.value = "0";
      }
    }
    recalculate();
  }

  function updatePaymentSummary(total) {
    var status = selectedStatus();
    var paid;

    if (status === "PAID") {
      paid = total;
      if (paidInput) paidInput.value = total.toFixed(2);
    } else if (status === "PENDING") {
      paid = 0;
      if (paidInput) paidInput.value = "0";
    } else {
      paid = toNumber(paidInput && paidInput.value);
    }

    setText("t-paid", formatINR(paid));

    var balance = round2(total - paid);
    toggleRow("t-pending-row", balance > 0);
    toggleRow("t-overpaid-row", balance < 0);
    setText("t-pending", formatINR(Math.max(balance, 0)));
    setText("t-overpaid", formatINR(Math.abs(Math.min(balance, 0))));
  }

  /* ------------------------------------------- returning customer lookup */
  var phoneInput = document.getElementById("id_customer_phone");
  var nameInput = document.getElementById("id_customer_name");
  var emailInput = document.getElementById("id_customer_email");
  var cityInput = document.getElementById("id_customer_city");
  var hint = document.getElementById("customer-hint");
  var lookupTimer = null;

  function lookupCustomer() {
    var url = form.getAttribute("data-lookup-url");
    var phone = (phoneInput.value || "").replace(/\D/g, "");
    if (!url || phone.length < 7) {
      if (hint) hint.textContent = "";
      return;
    }

    fetch(url + "?phone=" + encodeURIComponent(phone), {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        if (!data || !data.found) {
          if (hint) hint.textContent = "New customer";
          return;
        }
        if (hint) {
          hint.textContent =
            "Returning customer - " + data.invoice_count + " earlier bill(s)";
        }
        // Only fill blanks, never overwrite what the operator typed.
        if (nameInput && !nameInput.value) nameInput.value = data.name || "";
        if (emailInput && !emailInput.value) emailInput.value = data.email || "";
        if (cityInput && !cityInput.value) cityInput.value = data.city || "";
      })
      .catch(function () {
        /* offline or blocked: silently skip, the form still works */
      });
  }

  /* ------------------------------------------------------------- wiring */
  if (addButton) addButton.addEventListener("click", addRow);

  body.addEventListener("click", function (event) {
    var button = event.target.closest(".btn-row-del");
    if (button) removeRow(button.closest("tr.li-row"));
  });

  body.addEventListener("input", function (event) {
    if (event.target.closest("tr.li-row")) recalculate();
  });
  body.addEventListener("change", function (event) {
    if (event.target.closest("tr.li-row")) recalculate();
  });

  form.querySelectorAll('input[name="payment_status"]').forEach(function (radio) {
    radio.addEventListener("change", applyStatusUI);
  });
  if (paidInput) {
    paidInput.addEventListener("input", function () {
      recalculate();
    });
  }

  if (phoneInput) {
    phoneInput.addEventListener("input", function () {
      clearTimeout(lookupTimer);
      lookupTimer = setTimeout(lookupCustomer, 450);
    });
  }

  // Enter inside a line item adds the next row instead of submitting the bill.
  body.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && event.target.tagName === "INPUT") {
      event.preventDefault();
      var rows = visibleRows();
      if (event.target.closest("tr") === rows[rows.length - 1]) addRow();
    }
  });

  renumberRows();
  applyStatusUI();
  recalculate();
})();
