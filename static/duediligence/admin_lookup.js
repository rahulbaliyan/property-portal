/* Cascading District -> Tehsil -> Village picker for TitleCheckReport's
 * admin change form. Vanilla JS/fetch, no framework — proxies through our
 * own server-side AJAX endpoints (duediligence/views.py), which in turn
 * call the live Bhulekh portal. Villages are shown with their pargana name
 * so a human can tell apart similarly-named villages rather than the
 * picker guessing (see bhulekh.py's fetch_villages docstring). */
(function () {
  "use strict";

  function byId(id) {
    return document.getElementById(id);
  }

  function makeSelect(id, labelText) {
    var wrapper = document.createElement("div");
    wrapper.className = "form-row field-" + id;
    var label = document.createElement("label");
    label.textContent = labelText;
    label.setAttribute("for", id);
    var select = document.createElement("select");
    select.id = id;
    select.innerHTML = '<option value="">-- select --</option>';
    wrapper.appendChild(label);
    wrapper.appendChild(select);
    return { wrapper: wrapper, select: select };
  }

  function fillOptions(select, items, valueKey, labelFn) {
    select.innerHTML = '<option value="">-- select --</option>';
    items.forEach(function (item) {
      var opt = document.createElement("option");
      opt.value = item[valueKey];
      opt.textContent = labelFn(item);
      select.appendChild(opt);
    });
  }

  function fetchJson(url, params) {
    var qs = new URLSearchParams(params || {}).toString();
    return fetch(url + (qs ? "?" + qs : ""), { credentials: "same-origin" }).then(
      function (resp) {
        return resp.json();
      }
    );
  }

  document.addEventListener("DOMContentLoaded", function () {
    var hiddenDistrictName = byId("id_district_name");
    if (!hiddenDistrictName || !window.DUEDILIGENCE_URLS) {
      return; // not on the TitleCheckReport change form
    }
    var urls = window.DUEDILIGENCE_URLS;
    var hidden = {
      districtName: byId("id_district_name"),
      districtCode: byId("id_district_code"),
      tehsilName: byId("id_tehsil_name"),
      tehsilCode: byId("id_tehsil_code"),
      villageName: byId("id_village_name"),
      villageCode: byId("id_village_code"),
      parganaName: byId("id_pargana_name"),
      parganaCode: byId("id_pargana_code")
    };

    var districtField = makeSelect("dd_district", "District");
    var tehsilField = makeSelect("dd_tehsil", "Tehsil");
    var villageField = makeSelect("dd_village", "Village (name — pargana)");
    hidden.districtName.parentNode.insertBefore(districtField.wrapper, hidden.districtName);
    hidden.districtName.parentNode.insertBefore(tehsilField.wrapper, hidden.districtName);
    hidden.districtName.parentNode.insertBefore(villageField.wrapper, hidden.districtName);

    function onVillageChange() {
      var opt = villageField.select.selectedOptions[0];
      if (!opt || !opt.value) {
        hidden.villageName.value = "";
        hidden.villageCode.value = "";
        hidden.parganaName.value = "";
        hidden.parganaCode.value = "";
        return;
      }
      hidden.villageCode.value = opt.value;
      hidden.villageName.value = opt.dataset.name;
      hidden.parganaName.value = opt.dataset.pargana;
      hidden.parganaCode.value = opt.dataset.parganaCode;
    }

    function loadVillages(districtCode, tehsilCode, preselectCode) {
      fetchJson(urls.villages, { district_code: districtCode, tehsil_code: tehsilCode }).then(
        function (data) {
          var items = data.villages || [];
          villageField.select.innerHTML = '<option value="">-- select --</option>';
          items.forEach(function (v) {
            var o = document.createElement("option");
            o.value = v.code;
            o.textContent = v.name + " — " + v.pargana_name;
            o.dataset.name = v.name;
            o.dataset.pargana = v.pargana_name;
            o.dataset.parganaCode = v.pargana_code;
            villageField.select.appendChild(o);
          });
          if (preselectCode) {
            villageField.select.value = preselectCode;
            onVillageChange();
          }
        }
      );
    }

    function loadTehsils(districtCode, preselectCode, preselectVillageCode) {
      fetchJson(urls.tehsils, { district_code: districtCode }).then(function (data) {
        fillOptions(tehsilField.select, data.tehsils || [], "code", function (t) {
          return t.name + (t.name_english ? " (" + t.name_english + ")" : "");
        });
        if (preselectCode) {
          tehsilField.select.value = preselectCode;
          hidden.tehsilCode.value = preselectCode;
          loadVillages(districtCode, preselectCode, preselectVillageCode);
        }
      });
    }

    districtField.select.addEventListener("change", function () {
      var code = districtField.select.value;
      var name = districtField.select.selectedOptions[0]
        ? districtField.select.selectedOptions[0].textContent
        : "";
      hidden.districtCode.value = code;
      hidden.districtName.value = code ? name : "";
      tehsilField.select.innerHTML = '<option value="">-- select --</option>';
      villageField.select.innerHTML = '<option value="">-- select --</option>';
      onVillageChange();
      if (code) {
        loadTehsils(code);
      }
    });

    tehsilField.select.addEventListener("change", function () {
      var code = tehsilField.select.value;
      var name = tehsilField.select.selectedOptions[0]
        ? tehsilField.select.selectedOptions[0].textContent
        : "";
      hidden.tehsilCode.value = code;
      hidden.tehsilName.value = code ? name : "";
      villageField.select.innerHTML = '<option value="">-- select --</option>';
      onVillageChange();
      if (code) {
        loadVillages(hidden.districtCode.value, code);
      }
    });

    villageField.select.addEventListener("change", onVillageChange);

    // Load districts, then pre-select saved values if editing an existing report.
    fetchJson(urls.districts).then(function (data) {
      fillOptions(districtField.select, data.districts || [], "code", function (d) {
        return d.name + (d.name_english ? " (" + d.name_english + ")" : "");
      });
      var savedDistrict = hidden.districtCode.value;
      if (savedDistrict) {
        districtField.select.value = savedDistrict;
        loadTehsils(savedDistrict, hidden.tehsilCode.value, hidden.villageCode.value);
      }
    });

    // "Extract with AI" / "Run Bhulekh Check" are plain full-page POSTs
    // (see change_form.html) that can take up to ~2 minutes with zero
    // browser feedback beyond a tab spinner — which reads as "did
    // nothing" (confirmed firsthand). Show an explicit notice the moment
    // either is clicked so it's clear the click registered.
    var actionMessages = {
      "Extract with AI": "Extracting deed fields with AI — this can take up to 2 minutes. Please don’t close or refresh this tab.",
      "Run Bhulekh Check": "Checking Bhulekh records — this can take up to a minute. Please don’t close or refresh this tab."
    };
    Object.keys(actionMessages).forEach(function (label) {
      var btn = document.querySelector('input[type="submit"][value="' + label + '"]');
      if (!btn) return;
      btn.addEventListener("click", function () {
        var notice = document.createElement("p");
        notice.className = "help";
        notice.style.color = "#c9962e";
        notice.style.fontWeight = "bold";
        notice.textContent = actionMessages[label];
        btn.closest(".submit-row").insertAdjacentElement("afterend", notice);
        // Disabling the clicked button itself is deferred to the next tick.
        // Confirmed firsthand: disabling a submit button synchronously
        // inside its own "click" handler makes Chromium silently drop that
        // click's default action (the formaction submit) entirely — the
        // button visibly goes to "Working…" and stays disabled forever,
        // but the request is never actually sent. Queuing this with
        // setTimeout lets the browser dispatch the real form submission
        // first; only after that do we grey the buttons out.
        window.setTimeout(function () {
          document.querySelectorAll(
            'input[type="submit"][value="Extract with AI"], input[type="submit"][value="Run Bhulekh Check"]'
          ).forEach(function (b) {
            b.disabled = true;
          });
          btn.value = "Working…";
        }, 0);
      });
    });

    // Bhulekh checks run on a separate local poller (bhulekh.uk.gov.in
    // blocks this server's own network — see docs/BHULEKH_POLLER.md), so
    // "queued" isn't a quick request/response like the two actions above.
    // Poll for the real result instead of making the admin refresh by hand.
    if (window.DUEDILIGENCE_STATUS_POLL_URL) {
      var pollUrl = window.DUEDILIGENCE_STATUS_POLL_URL;
      var pollCount = 0;
      var pollInterval = window.setInterval(function () {
        pollCount += 1;
        fetchJson(pollUrl).then(function (data) {
          if (data.status && data.status !== "queued") {
            window.clearInterval(pollInterval);
            window.location.reload();
            return;
          }
          // Still queued after 3+ minutes — the poller may not be running.
          if (pollCount === 18) {
            var note = byId("bhulekh-queued-note");
            if (note) {
              note.textContent =
                "Still queued after a few minutes — check that the local " +
                "Bhulekh poller is actually running (docs/BHULEKH_POLLER.md).";
            }
          }
        });
      }, 10000);
    }
  });
})();
