/* Monument Watch dashboard. Vanilla JS; data comes from data/data.js which
 * defines window.MW_DATA (works from file:// and any static host). */
(function () {
  "use strict";

  if (!window.MW_DATA) {
    var err = document.getElementById("load-error");
    err.hidden = false;
    err.textContent = "No data found. Run `python run.py` first to generate site/data/.";
    return;
  }

  var DATA = window.MW_DATA;
  var items = DATA.items || [];
  var CAT_LABELS = {
    "federal-register": "Federal Register", "mining-claims": "Mining claims",
    "leasing": "Leasing", "planning-nepa": "Planning / NEPA",
    "litigation": "Litigation", "congress": "Congress",
    "state-lands": "State lands", "policy": "Policy", "news": "News"
  };
  var TAG_LABELS = { "bears-ears": "Bears Ears", "grand-staircase": "Grand Staircase" };

  /* ---------- last visit / new flags ---------- */
  var lastVisit = localStorage.getItem("mw_last_visit");
  var nowIso = new Date().toISOString();
  // First visit: treat the last 24h as "new" so the page isn't a wall of NEW.
  var newCutoff = lastVisit || new Date(Date.now() - 864e5).toISOString();
  items.forEach(function (it) { it._new = it.first_seen > newCutoff; });
  localStorage.setItem("mw_last_visit", nowIso);

  var newCount = items.filter(function (i) { return i._new; }).length;
  var thirtyDays = new Date(Date.now() - 30 * 864e5).toISOString();
  var last30 = items.filter(function (i) {
    return (i.date && i.date >= thirtyDays.slice(0, 10)) || i.first_seen >= thirtyDays;
  }).length;

  /* ---------- header stats + priority banner ---------- */
  var stats = document.getElementById("header-stats");
  stats.innerHTML =
    '<div class="stat' + (newCount ? " alert" : "") + '"><b>' + newCount + "</b>new since last visit</div>" +
    '<div class="stat"><b>' + last30 + "</b>items, last 30 days</div>" +
    '<div class="stat"><b>' + (DATA.health || []).filter(function (h) { return h.status === "green"; }).length +
    "/" + (DATA.health || []).length + "</b>sources healthy</div>";

  var prioNew = items.filter(function (i) { return i._new && i.tags.indexOf("priority") !== -1; });
  if (prioNew.length) {
    var banner = document.getElementById("priority-banner");
    banner.hidden = false;
    banner.textContent = "⚠ " + prioNew.length + " new priority item" +
      (prioNew.length > 1 ? "s" : "") + " — " +
      prioNew.slice(0, 3).map(function (i) { return i.title; }).join(" · ");
  }

  document.getElementById("generated-at").textContent =
    "Data generated " + (DATA.meta ? DATA.meta.generated_at : "?") + " UTC.";

  /* ---------- map ---------- */
  var boundaryNote = document.getElementById("boundary-note");
  boundaryNote.textContent = (DATA.meta && DATA.meta.boundary_note) || "";
  if (DATA.meta && !DATA.meta.reduced_boundaries_available) {
    boundaryNote.textContent += " — reduced boundaries pending publication.";
  }

  // canvas renderer: thousands of claim polygons pan smoothly vs SVG
  var map = L.map("map", { scrollWheelZoom: false, preferCanvas: true });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 15, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  }).addTo(map);

  var boundsSet = false;
  if (DATA.boundaries && DATA.boundaries.features && DATA.boundaries.features.length) {
    var boundary = L.geoJSON(DATA.boundaries, {
      style: { color: "#22303c", weight: 2, fill: false, dashArray: "6 4" },
      onEachFeature: function (f, layer) {
        layer.bindTooltip((f.properties && f.properties.NCA_NAME) || "Monument boundary");
      }
    }).addTo(map);
    map.fitBounds(boundary.getBounds());
    boundsSet = true;
  }
  if (!boundsSet) map.setView([37.6, -111.0], 8);

  if (DATA.reduced && DATA.reduced.features) {
    L.geoJSON(DATA.reduced, {
      style: { color: "#1e8e3e", weight: 1.5, fillColor: "#1e8e3e", fillOpacity: 0.12 }
    }).addTo(map).bindTooltip("Post-proclamation (reduced) boundary");
  }

  function featureAge(f) {
    var seen = f.properties.first_seen || "";
    var d30 = new Date(Date.now() - 30 * 864e5).toISOString();
    var d180 = new Date(Date.now() - 180 * 864e5).toISOString();
    if (seen > d30) return "new";
    if (seen > d180) return "recent";
    return "old";
  }
  var AGE_STYLE = {
    "new":    { color: "#c0392b", fillColor: "#c0392b", weight: 1.5, fillOpacity: 0.5 },
    "recent": { color: "#ca6f1e", fillColor: "#ca6f1e", weight: 1,   fillOpacity: 0.35 },
    "old":    { color: "#5d7fa3", fillColor: "#5d7fa3", weight: 0.6, fillOpacity: 0.18 }
  };

  if (DATA.features && DATA.features.features && DATA.features.features.length) {
    L.geoJSON(DATA.features, {
      style: function (f) { return AGE_STYLE[featureAge(f)]; },
      pointToLayer: function (f, latlng) {   // wells are points, claims are polygons
        var s = AGE_STYLE[featureAge(f)];
        return L.circleMarker(latlng, { radius: 5, color: s.color,
          fillColor: s.fillColor, weight: 1, fillOpacity: 0.8 });
      },
      onEachFeature: function (f, layer) {
        var p = f.properties;
        layer.bindPopup(
          '<b>' + escapeHtml(p.title) + "</b><br>" +
          escapeHtml(p.category) + " · first seen " + (p.first_seen || "?").slice(0, 10) +
          (p.url ? '<br><a href="' + p.url + '" target="_blank" rel="noopener">source record ↗</a>' : "")
        );
      }
    }).addTo(map);
  }

  document.getElementById("map-legend").innerHTML =
    '<span><span class="swatch" style="background:#c0392b"></span>claim first seen &lt;30d</span>' +
    '<span><span class="swatch" style="background:#ca6f1e"></span>&lt;180d</span>' +
    '<span><span class="swatch" style="background:#5d7fa3"></span>older</span>' +
    '<span><span class="swatch" style="border:2px dashed #22303c;background:none"></span>2021 boundary</span>';

  /* ---------- health panel ---------- */
  var tbody = document.querySelector("#health-table tbody");
  (DATA.health || []).forEach(function (h) {
    var tr = document.createElement("tr");
    var status = h.last_error
      ? '<span class="err">' + escapeHtml(h.last_error) + "</span>"
      : (h.note ? '<span class="note">' + escapeHtml(h.note) + "</span>" : "ok");
    tr.innerHTML =
      '<td><span class="dot ' + h.status + '"></span></td>' +
      "<td>" + escapeHtml(h.source) + "</td>" +
      "<td>" + (h.last_success ? h.last_success.replace("T", " ").replace("Z", "") : "never") + "</td>" +
      "<td>" + h.item_count + (h.new_count ? ' <span class="new-flag">+' + h.new_count + "</span>" : "") + "</td>" +
      "<td>" + status + "</td>";
    tbody.appendChild(tr);
  });

  /* ---------- manual checks ---------- */
  var mc = document.getElementById("manual-checks");
  ((DATA.meta && DATA.meta.manual_checks) || []).forEach(function (c) {
    var li = document.createElement("li");
    li.innerHTML = '<a href="' + c.url + '" target="_blank" rel="noopener">' +
      escapeHtml(c.name) + " ↗</a><div class='what'>" + escapeHtml(c.what_to_check) + "</div>";
    mc.appendChild(li);
  });

  /* ---------- feed with filters ---------- */
  var activeCats = {}, activeTags = {}, searchText = "", newOnly = false;
  var PAGE = 150, shown = PAGE;

  var cats = unique(items.map(function (i) { return i.category; }));
  var catRow = document.getElementById("category-pills");
  cats.forEach(function (c) {
    catRow.appendChild(pill(CAT_LABELS[c] || c, "var(--cat-" + c + ")", function (on) {
      activeCats[c] = on; render();
    }));
  });
  var tagRow = document.getElementById("tag-pills");
  Object.keys(TAG_LABELS).forEach(function (t) {
    tagRow.appendChild(pill(TAG_LABELS[t], "#22303c", function (on) {
      activeTags[t] = on; render();
    }));
  });
  document.getElementById("search-box").addEventListener("input", function (e) {
    searchText = e.target.value.toLowerCase(); shown = PAGE; render();
  });
  document.getElementById("new-only").addEventListener("change", function (e) {
    newOnly = e.target.checked; shown = PAGE; render();
  });
  document.getElementById("show-more").addEventListener("click", function () {
    shown += PAGE; render();
  });

  function visible() {
    var catFilter = Object.keys(activeCats).filter(function (k) { return activeCats[k]; });
    var tagFilter = Object.keys(activeTags).filter(function (k) { return activeTags[k]; });
    return items.filter(function (i) {
      if (newOnly && !i._new) return false;
      if (catFilter.length && catFilter.indexOf(i.category) === -1) return false;
      if (tagFilter.length && !tagFilter.some(function (t) { return i.tags.indexOf(t) !== -1; })) return false;
      if (searchText && (i.title + " " + i.summary).toLowerCase().indexOf(searchText) === -1) return false;
      return true;
    });
  }

  function render() {
    var feed = document.getElementById("feed");
    feed.innerHTML = "";
    var list = visible();
    if (!list.length) {
      feed.innerHTML = '<div class="empty-feed">No items match the current filters.</div>';
      document.getElementById("show-more").hidden = true;
      return;
    }
    var day = null;
    list.slice(0, shown).forEach(function (i) {
      var d = i.date || i.first_seen.slice(0, 10);
      if (d !== day) {
        day = d;
        var h = document.createElement("div");
        h.className = "day-header";
        h.textContent = d;
        feed.appendChild(h);
      }
      feed.appendChild(card(i));
    });
    document.getElementById("show-more").hidden = list.length <= shown;
  }

  function card(i) {
    var el = document.createElement("div");
    el.className = "card";
    el.style.borderLeftColor = "var(--cat-" + i.category + ", var(--line))";
    var tags = i.tags.map(function (t) {
      return t === "priority" ? '<span class="prio">PRIORITY</span>' : escapeHtml(t);
    }).join(" · ");
    el.innerHTML =
      '<div class="top"><span class="badge" style="background:var(--cat-' + i.category + ',#888)">' +
      escapeHtml(CAT_LABELS[i.category] || i.category) + "</span>" +
      '<span class="muted">' + escapeHtml(i.source) + "</span>" +
      (i._new ? '<span class="new-flag">NEW</span>' : "") + "</div>" +
      '<a class="title" href="' + (i.url || "#") + '" target="_blank" rel="noopener">' +
      escapeHtml(i.title) + "</a>" +
      (i.summary ? '<p class="summary">' + escapeHtml(i.summary) + "</p>" : "") +
      (tags ? '<div class="tags">' + tags + "</div>" : "");
    return el;
  }

  function pill(label, color, onToggle) {
    var b = document.createElement("button");
    b.className = "pill";
    b.textContent = label;
    b.addEventListener("click", function () {
      var on = !b.classList.contains("active");
      b.classList.toggle("active", on);
      b.style.background = on ? color : "";
      shown = PAGE;
      onToggle(on);
    });
    return b;
  }

  function unique(arr) {
    return arr.filter(function (v, idx) { return arr.indexOf(v) === idx; }).sort();
  }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  render();
})();
