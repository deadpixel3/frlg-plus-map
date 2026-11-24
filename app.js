(() => {
  "use strict";

  const STORAGE_KEY = "frlg_plus_map_progress_v1";

  const CATEGORIES = {
    item:           { label: "Visible Items",      icon: "🔴", color: "#ef4444" },
    tm:             { label: "TMs & HMs",          icon: "💿", color: "#a855f7" },
    hidden_item:    { label: "Hidden Items",       icon: "✨", color: "#f59e0b" },
    trainer:        { label: "Trainers",           icon: "⚔️", color: "#3b82f6" },
    master_trainer: { label: "Master Trainers +",  icon: "👑", color: "#eab308" },
    gym:            { label: "Gyms & Bosses",      icon: "🏆", color: "#f97316" },
    pokemon:        { label: "Static Pokémon",     icon: "⭐", color: "#10b981" },
    npc:            { label: "NPCs, Shops & Tutors", icon: "💬", color: "#06b6d4" },
    location:       { label: "Dungeon Entrances",  icon: "🚪", color: "#64748b" },
  };

  const ENC_LABELS = {
    land: "🌿 Tall Grass / Cave",
    water: "🌊 Surfing",
    rock_smash: "🪨 Rock Smash",
    old_rod: "🎣 Old Rod",
    good_rod: "🎣 Good Rod",
    super_rod: "🎣 Super Rod",
  };

  const TYPE_COLORS = {
    Normal:   "#9ca3af",
    Fire:     "#ef4444",
    Water:    "#3b82f6",
    Electric: "#eab308",
    Grass:    "#22c55e",
    Ice:      "#38bdf8",
    Fighting: "#b91c1c",
    Poison:   "#a855f7",
    Ground:   "#ca8a04",
    Flying:   "#818cf8",
    Psychic:  "#ec4899",
    Bug:      "#84cc16",
    Rock:     "#a16207",
    Ghost:    "#6366f1",
    Dragon:   "#4f46e5",
    Dark:     "#475569",
    Steel:    "#64748b",
  };

  // Gen 3 attacking type effectiveness: GEN3_CHART[atk][def] = multiplier (omit 1)
  const GEN3_CHART = {
    Normal:   { Rock: 0.5, Ghost: 0, Steel: 0.5 },
    Fire:     { Fire: 0.5, Water: 0.5, Grass: 2, Ice: 2, Bug: 2, Rock: 0.5, Dragon: 0.5, Steel: 2 },
    Water:    { Fire: 2, Water: 0.5, Grass: 0.5, Ground: 2, Rock: 2, Dragon: 0.5 },
    Electric: { Water: 2, Electric: 0.5, Grass: 0.5, Ground: 0, Flying: 2, Dragon: 0.5 },
    Grass:    { Fire: 0.5, Water: 2, Grass: 0.5, Poison: 0.5, Ground: 2, Flying: 0.5, Bug: 0.5, Rock: 2, Dragon: 0.5, Steel: 0.5 },
    Ice:      { Fire: 0.5, Water: 0.5, Grass: 2, Ice: 0.5, Ground: 2, Flying: 2, Dragon: 2, Steel: 0.5 },
    Fighting: { Normal: 2, Ice: 2, Poison: 0.5, Flying: 0.5, Psychic: 0.5, Bug: 0.5, Rock: 2, Ghost: 0, Dark: 2, Steel: 2 },
    Poison:   { Grass: 2, Poison: 0.5, Ground: 0.5, Rock: 0.5, Ghost: 0.5, Steel: 0 },
    Ground:   { Fire: 2, Electric: 2, Grass: 0.5, Poison: 2, Flying: 0, Bug: 0.5, Rock: 2, Steel: 2 },
    Flying:   { Electric: 0.5, Grass: 2, Fighting: 2, Bug: 2, Rock: 0.5, Steel: 0.5 },
    Psychic:  { Fighting: 2, Poison: 2, Psychic: 0.5, Dark: 0, Steel: 0.5 },
    Bug:      { Fire: 0.5, Grass: 2, Fighting: 0.5, Poison: 0.5, Flying: 0.5, Psychic: 2, Ghost: 0.5, Dark: 2, Steel: 0.5 },
    Rock:     { Fire: 2, Ice: 2, Fighting: 0.5, Ground: 0.5, Flying: 2, Bug: 2, Steel: 0.5 },
    Ghost:    { Normal: 0, Psychic: 2, Ghost: 2, Dark: 0.5, Steel: 0.5 },
    Dragon:   { Dragon: 2, Steel: 0.5 },
    Dark:     { Fighting: 0.5, Psychic: 2, Ghost: 2, Dark: 0.5, Steel: 0.5 },
    Steel:    { Fire: 0.5, Water: 0.5, Electric: 0.5, Ice: 2, Rock: 2, Steel: 0.5 },
  };

  function getSpeciesTypes(species) {
    if (!state.data || !state.data.speciesTypes) return [];
    return state.data.speciesTypes[species] || [];
  }

  function renderTypeBadge(type, small = false) {
    const bg = TYPE_COLORS[type] || "#475569";
    return `<span class="type-badge${small ? " sm" : ""}" style="background:${bg}">${escapeHtml(type)}</span>`;
  }

  function renderSpeciesTypes(species, small = false) {
    const types = getSpeciesTypes(species);
    return types.map((t) => renderTypeBadge(t, small)).join("");
  }

  function getDefensiveMatchups(types) {
    const buckets = { w4: [], w2: [], r2: [], r4: [], im: [] };
    if (!types || !types.length) return buckets;
    for (const atk of Object.keys(GEN3_CHART)) {
      let mult = 1;
      for (const def of types) {
        const m = GEN3_CHART[atk][def];
        if (m !== undefined) mult *= m;
      }
      if (mult === 0) buckets.im.push(atk);
      else if (mult >= 4) buckets.w4.push(atk);
      else if (mult >= 2) buckets.w2.push(atk);
      else if (mult <= 0.25) buckets.r4.push(atk);
      else if (mult < 1) buckets.r2.push(atk);
    }
    return buckets;
  }

  function renderMatchupPanel(species) {
    const types = getSpeciesTypes(species);
    if (!types.length) return "";
    const m = getDefensiveMatchups(types);
    const rows = [
      { key: "w4", label: "4× Weak", list: m.w4 },
      { key: "w2", label: "2× Weak", list: m.w2 },
      { key: "r2", label: "½× Resist", list: m.r2 },
      { key: "r4", label: "¼× Resist", list: m.r4 },
      { key: "im", label: "0× Immune", list: m.im },
    ].filter((r) => r.list.length);

    return rows
      .map(
        (r) => `
        <div class="matchup-row">
          <span class="matchup-label ${r.key}">${r.label}</span>
          <div class="matchup-types">${r.list.map((t) => renderTypeBadge(t, true)).join("")}</div>
        </div>`
      )
      .join("");
  }

  const state = {
    data: null,
    layerId: "kanto",
    version: "FR",
    activeCategories: new Set(Object.keys(CATEGORIES)),
    foundIds: new Set(),
    hideFound: false,
    showLabels: true,
    selectedMarkerId: null,
    selectedZoneId: null,
    // Viewport transform
    scale: 0.25,
    minScale: 0.1,
    maxScale: 4.0,
    tx: 0,
    ty: 0,
    dragging: false,
    dragStartX: 0,
    dragStartY: 0,
    dragMoved: false,
  };

  // DOM refs
  const $ = (id) => document.getElementById(id);
  const elSidebar = $("sidebar");
  const elLayerSelect = $("layer-select");
  const elSearchInput = $("search-input");
  const elSearchResults = $("search-results");
  const elClearSearch = $("btn-clear-search");
  const elCategoryList = $("category-list");
  const elProgressText = $("progress-text");
  const elProgressFill = $("progress-bar-fill");
  const elViewport = $("viewport");
  const elStage = $("map-stage");
  const elMapImage = $("map-image");
  const elZonesLayer = $("zones-layer");
  const elMarkersLayer = $("markers-layer");
  const elHudZonePill = $("hud-zone-pill");
  const elHudZoneName = $("hud-zone-name");
  const elHudZoneHint = $("hud-zone-hint");
  const elHudCoords = $("hud-coords");
  const elTooltip = $("hover-tooltip");
  const elDrawer = $("detail-drawer");
  const elDrawerBadge = $("drawer-badge");
  const elDrawerBody = $("drawer-body");

  // ---------------- Persistence ----------------

  function loadProgress() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr)) state.foundIds = new Set(arr);
      }
    } catch (_) {}
  }

  function saveProgress() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify([...state.foundIds]));
    } catch (_) {}
  }

  // ---------------- Data Helpers ----------------

  function getLayer(id = state.layerId) {
    return state.data.layers.find((l) => l.id === id) || state.data.layers[0];
  }

  function getLayerMarkers(id = state.layerId) {
    return state.data.markers.filter((m) => m.layer === id);
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---------------- Transform & Pan/Zoom ----------------

  function applyTransform() {
    elStage.style.transform = `translate3d(${state.tx}px, ${state.ty}px, 0) scale(${state.scale})`;
    // Keep pins and zone labels visually crisp & readable at every zoom level
    const inv = Math.max(0.45, Math.min(2.6, 1 / state.scale));
    elStage.style.setProperty("--inv-scale", inv.toFixed(3));
    const pins = elMarkersLayer.children;
    for (let i = 0; i < pins.length; i++) {
      pins[i].style.transform = `scale(${inv})`;
    }
    const labels = elZonesLayer.querySelectorAll(".zone-label");
    const labelScale = Math.max(0.55, Math.min(2.2, 0.9 / state.scale));
    for (let i = 0; i < labels.length; i++) {
      labels[i].style.transform = `translateX(-50%) scale(${labelScale})`;
    }
  }

  function fitToScreen() {
    const layer = getLayer();
    if (!layer) return;
    const rect = elViewport.getBoundingClientRect();
    const pad = 40;
    const sx = (rect.width - pad) / layer.width;
    const sy = (rect.height - pad) / layer.height;
    state.scale = Math.max(state.minScale, Math.min(2.0, Math.min(sx, sy)));
    state.tx = (rect.width - layer.width * state.scale) / 2;
    state.ty = (rect.height - layer.height * state.scale) / 2;
    applyTransform();
  }

  function zoomAt(factor, clientX, clientY) {
    const rect = elViewport.getBoundingClientRect();
    const mx = clientX - rect.left;
    const my = clientY - rect.top;
    const newScale = Math.max(state.minScale, Math.min(state.maxScale, state.scale * factor));
    const ratio = newScale / state.scale;
    state.tx = mx - (mx - state.tx) * ratio;
    state.ty = my - (my - state.ty) * ratio;
    state.scale = newScale;
    applyTransform();
  }

  function focusPoint(x, y, targetScale = 1.35) {
    const rect = elViewport.getBoundingClientRect();
    state.scale = Math.max(state.minScale, Math.min(state.maxScale, targetScale));
    state.tx = rect.width / 2 - x * state.scale;
    state.ty = rect.height / 2 - y * state.scale;
    applyTransform();
  }

  // ---------------- Rendering ----------------

  function populateLayerSelect() {
    const groups = new Map();
    for (const l of state.data.layers) {
      if (!groups.has(l.group)) groups.set(l.group, []);
      groups.get(l.group).push(l);
    }
    elLayerSelect.innerHTML = "";
    for (const [grp, layers] of groups.entries()) {
      const og = document.createElement("optgroup");
      og.label = grp;
      for (const l of layers) {
        const opt = document.createElement("option");
        opt.value = l.id;
        opt.textContent = `${l.name} (${l.markerCount})`;
        og.appendChild(opt);
      }
      elLayerSelect.appendChild(og);
    }
    elLayerSelect.value = state.layerId;
  }

  function renderCategories() {
    const layerMarkers = getLayerMarkers();
    const counts = {};
    const foundCounts = {};
    for (const k of Object.keys(CATEGORIES)) {
      counts[k] = 0;
      foundCounts[k] = 0;
    }
    for (const m of layerMarkers) {
      if (counts[m.category] !== undefined) {
        counts[m.category]++;
        if (state.foundIds.has(m.id)) foundCounts[m.category]++;
      }
    }

    elCategoryList.innerHTML = "";
    for (const [key, meta] of Object.entries(CATEGORIES)) {
      const btn = document.createElement("button");
      btn.className = "cat-btn" + (state.activeCategories.has(key) ? "" : " off");
      btn.innerHTML = `
        <span class="cat-left">
          <span class="cat-dot" style="background:${meta.color}26;border:1px solid ${meta.color}88">${meta.icon}</span>
          <span class="cat-name">${meta.label}</span>
        </span>
        <span class="cat-count">${foundCounts[key]}/${counts[key]}</span>
      `;
      btn.addEventListener("click", () => {
        if (state.activeCategories.has(key)) state.activeCategories.delete(key);
        else state.activeCategories.add(key);
        renderCategories();
        renderMarkers();
      });
      elCategoryList.appendChild(btn);
    }
    updateProgressUI();
  }

  function updateProgressUI() {
    const trackable = state.data.markers.filter((m) => m.category !== "location");
    const found = trackable.filter((m) => state.foundIds.has(m.id)).length;
    const pct = trackable.length ? (found / trackable.length) * 100 : 0;
    elProgressText.textContent = `${found} / ${trackable.length} (${pct.toFixed(1)}%)`;
    elProgressFill.style.width = `${pct}%`;
  }

  function renderZones() {
    const layer = getLayer();
    elZonesLayer.innerHTML = "";
    for (const z of layer.zones) {
      const box = document.createElement("div");
      box.className = "zone-box" + (state.selectedZoneId === z.id ? " selected" : "");
      box.style.left = `${z.x}px`;
      box.style.top = `${z.y}px`;
      box.style.width = `${z.w}px`;
      box.style.height = `${z.h}px`;

      if (state.showLabels) {
        const lbl = document.createElement("div");
        lbl.className = "zone-label" + (z.hasEncounters ? " has-enc" : "");
        lbl.textContent = z.name;
        box.appendChild(lbl);
      }

      box.addEventListener("mouseenter", () => {
        elHudZonePill.classList.remove("hidden");
        elHudZoneName.textContent = z.name;
        elHudZoneHint.textContent = z.hasEncounters
          ? "Click for Wild Encounters & area items"
          : "Click for area summary";
      });

      box.addEventListener("click", (e) => {
        if (state.dragMoved) return;
        e.stopPropagation();
        selectZone(z);
      });

      elZonesLayer.appendChild(box);
    }
  }

  function renderMarkers() {
    const layerMarkers = getLayerMarkers();
    elMarkersLayer.innerHTML = "";

    // Gently spiral-offset markers sharing identical (x, y) (e.g., indoor summaries on a building door)
    const coordCounts = new Map();

    for (const m of layerMarkers) {
      if (!state.activeCategories.has(m.category)) continue;
      const isFound = state.foundIds.has(m.id);
      if (state.hideFound && isFound) continue;

      const meta = CATEGORIES[m.category] || { icon: "📍", color: "#38bdf8" };
      const key = `${m.x},${m.y}`;
      const dupIdx = coordCounts.get(key) || 0;
      coordCounts.set(key, dupIdx + 1);

      let px = m.x;
      let py = m.y;
      if (dupIdx > 0) {
        const angle = dupIdx * 2.39996; // golden angle
        const radius = 10 + dupIdx * 3.5;
        px += Math.cos(angle) * radius;
        py += Math.sin(angle) * radius;
      }

      const pin = document.createElement("div");
      pin.className = "map-pin" + (isFound ? " found" : "") + (state.selectedMarkerId === m.id ? " active" : "");
      pin.style.left = `${px}px`;
      pin.style.top = `${py}px`;
      pin.style.background = meta.color;
      pin.textContent = meta.icon;
      pin.dataset.id = m.id;

      pin.addEventListener("mouseenter", (e) => showTooltip(e, m.title, `${meta.label} · ${m.map}`));
      pin.addEventListener("mousemove", moveTooltip);
      pin.addEventListener("mouseleave", hideTooltip);
      pin.addEventListener("click", (e) => {
        e.stopPropagation();
        if (state.dragMoved) return;
        selectMarker(m);
      });

      elMarkersLayer.appendChild(pin);
    }
    applyTransform();
  }

  function loadLayer(layerId, fit = true) {
    state.layerId = layerId;
    state.selectedMarkerId = null;
    state.selectedZoneId = null;
    elLayerSelect.value = layerId;
    const layer = getLayer(layerId);
    elStage.style.width = `${layer.width}px`;
    elStage.style.height = `${layer.height}px`;
    elMapImage.width = layer.width;
    elMapImage.height = layer.height;
    elMapImage.src = layer.image;

    renderCategories();
    renderZones();
    renderMarkers();
    if (fit) fitToScreen();
  }

  // ---------------- Tooltips & Detail Drawer ----------------

  function showTooltip(e, title, sub) {
    elTooltip.innerHTML = `<div class="tt-title">${escapeHtml(title)}</div><div class="tt-sub">${escapeHtml(sub)}</div>`;
    elTooltip.classList.remove("hidden");
    moveTooltip(e);
  }

  function moveTooltip(e) {
    elTooltip.style.left = `${e.clientX + 14}px`;
    elTooltip.style.top = `${e.clientY + 14}px`;
  }

  function hideTooltip() {
    elTooltip.classList.add("hidden");
  }

  function closeDrawer() {
    state.selectedMarkerId = null;
    state.selectedZoneId = null;
    elDrawer.classList.add("hidden");
    renderZones();
    renderMarkers();
  }

  function selectMarker(m) {
    state.selectedMarkerId = m.id;
    state.selectedZoneId = null;
    const meta = CATEGORIES[m.category] || { label: "Marker", icon: "📍", color: "#38bdf8" };
    const isFound = state.foundIds.has(m.id);

    elDrawerBadge.style.background = `${meta.color}28`;
    elDrawerBadge.style.color = "#fff";
    elDrawerBadge.style.border = `1px solid ${meta.color}88`;
    elDrawerBadge.textContent = `${meta.icon} ${m.sub || meta.label}`;

    let html = `
      <div class="drawer-title">${escapeHtml(m.title)}</div>
      <div class="drawer-meta">
        <span>📍 ${escapeHtml(m.map)}</span>
        <span>(${Math.floor(m.x / 16)}, ${Math.floor(m.y / 16)})</span>
        ${m.badge ? `<span class="frlg-pill">${escapeHtml(m.badge)}</span>` : ""}
      </div>
    `;

    if (m.details) {
      html += `<div class="drawer-desc">${escapeHtml(m.details)}</div>`;
    }

    if (m.category !== "location") {
      html += `
        <button id="btn-toggle-found" class="found-toggle-btn ${isFound ? "is-found" : ""}">
          ${isFound ? "✓ Marked as Found (click to undo)" : "○ Mark as Found"}
        </button>
      `;
    }

    if (m.targetLayer) {
      const target = getLayer(m.targetLayer);
      html += `
        <button id="btn-jump-layer" class="primary-btn" style="width:100%;padding:10px;margin-bottom:14px;">
          Open ${escapeHtml(target ? target.name : m.targetLayer)} Map →
        </button>
      `;
    }

    if (Array.isArray(m.party) && m.party.length) {
      html += `<span class="section-label">Team (${m.party.length} Pokémon${m.double ? " · Double Battle" : ""})</span>`;
      html += `<div class="party-list">`;
      m.party.forEach((mon, idx) => {
        const typeBadges = renderSpeciesTypes(mon.species, true);
        const matchupHtml = renderMatchupPanel(mon.species);
        html += `
          <div class="mon-card">
            <div class="mon-top">
              <div class="mon-identity">
                <span>${escapeHtml(mon.species)}</span>
                ${typeBadges}
              </div>
              <div class="mon-right">
                <span class="mon-lvl">Lv. ${mon.level}</span>
                ${matchupHtml ? `<button class="mon-expand-btn" data-mon-idx="${idx}" title="Show type weaknesses & resistances">▾</button>` : ""}
              </div>
            </div>
            ${mon.item ? `<div class="mon-item">Held Item: ${escapeHtml(mon.item)}</div>` : ""}
            ${mon.moves && mon.moves.length ? `<div class="mon-moves">${mon.moves.map((mv) => `<span class="move-chip">${escapeHtml(mv)}</span>`).join("")}</div>` : ""}
            ${matchupHtml ? `<div class="matchup-panel hidden" id="matchup-${idx}">${matchupHtml}</div>` : ""}
          </div>
        `;
      });
      html += `</div>`;
    }

    elDrawerBody.innerHTML = html;
    elDrawer.classList.remove("hidden");

    elDrawerBody.querySelectorAll(".mon-expand-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const panel = $("matchup-" + btn.dataset.monIdx);
        if (!panel) return;
        const isOpen = !panel.classList.toggle("hidden");
        btn.classList.toggle("open", isOpen);
        btn.textContent = isOpen ? "▴" : "▾";
      });
    });

    const btnFound = $("btn-toggle-found");
    if (btnFound) {
      btnFound.addEventListener("click", () => {
        if (state.foundIds.has(m.id)) state.foundIds.delete(m.id);
        else state.foundIds.add(m.id);
        saveProgress();
        renderCategories();
        renderMarkers();
        selectMarker(m);
      });
    }

    const btnJump = $("btn-jump-layer");
    if (btnJump) {
      btnJump.addEventListener("click", () => {
        closeDrawer();
        loadLayer(m.targetLayer, true);
      });
    }

    renderZones();
    renderMarkers();
  }

  function selectZone(z) {
    state.selectedZoneId = z.id;
    state.selectedMarkerId = null;

    elDrawerBadge.style.background = "rgba(56, 189, 248, 0.2)";
    elDrawerBadge.style.color = "#38bdf8";
    elDrawerBadge.style.border = "1px solid rgba(56, 189, 248, 0.5)";
    elDrawerBadge.textContent = "🗺️ Area Details";

    let html = `
      <div class="drawer-title">${escapeHtml(z.name)}</div>
      <div class="drawer-meta">
        <span>Version: <strong>${state.version === "FR" ? "FireRed" : "LeafGreen"}</strong></span>
      </div>
    `;

    // Wild Encounters
    const encVer = z.encounters ? z.encounters[state.version] : null;
    if (encVer && Object.keys(encVer).length) {
      for (const [method, mons] of Object.entries(encVer)) {
        html += `
          <div class="enc-group">
            <div class="enc-group-title">${ENC_LABELS[method] || method}</div>
            <table class="enc-table">
              <thead><tr><th>Pokémon</th><th>Level</th><th>Rate</th></tr></thead>
              <tbody>
                ${mons.map((p) => `
                  <tr>
                    <td>
                      <div class="enc-species-cell">
                        <strong>${escapeHtml(p.species)}</strong>
                        ${renderSpeciesTypes(p.species, true)}
                      </div>
                    </td>
                    <td>Lv. ${p.min === p.max ? p.min : `${p.min}–${p.max}`}</td>
                    <td class="enc-rate">${p.rate}%</td>
                  </tr>
                `).join("")}
              </tbody>
            </table>
          </div>
        `;
      }
    } else {
      html += `<div class="drawer-desc">No wild Pokémon encounters in this area.</div>`;
    }

    // Markers inside this zone
    const zMarkers = getLayerMarkers().filter(
      (m) => m.x >= z.x && m.x <= z.x + z.w && m.y >= z.y && m.y <= z.y + z.h
    );
    if (zMarkers.length) {
      html += `<span class="section-label" style="margin-top:12px;">Points of Interest (${zMarkers.length})</span>`;
      html += `<div class="zone-marker-list">`;
      for (const m of zMarkers) {
        const meta = CATEGORIES[m.category] || { icon: "📍" };
        const done = state.foundIds.has(m.id) ? " ✓" : "";
        html += `
          <div class="zone-marker-row" data-mid="${escapeHtml(m.id)}">
            <span>${meta.icon} ${escapeHtml(m.title)}${done}</span>
            <span style="color:var(--text-muted);font-size:11px;">→</span>
          </div>
        `;
      }
      html += `</div>`;
    }

    elDrawerBody.innerHTML = html;
    elDrawer.classList.remove("hidden");

    elDrawerBody.querySelectorAll(".zone-marker-row").forEach((row) => {
      row.addEventListener("click", () => {
        const m = state.data.markers.find((x) => x.id === row.dataset.mid);
        if (m) {
          focusPoint(m.x, m.y, Math.max(state.scale, 1.5));
          selectMarker(m);
        }
      });
    });

    renderZones();
    renderMarkers();
  }

  // ---------------- Global Search ----------------

  function runSearch(q) {
    const query = q.trim().toLowerCase();
    elClearSearch.classList.toggle("hidden", !query);
    if (!query || query.length < 2) {
      elSearchResults.classList.add("hidden");
      return;
    }

    const results = [];

    // 1. Search markers across all layers
    for (const m of state.data.markers) {
      const hay = `${m.title} ${m.map} ${m.details || ""} ${(m.party || []).map((p) => p.species).join(" ")}`.toLowerCase();
      if (hay.includes(query)) {
        const layer = getLayer(m.layer);
        results.push({
          type: "marker",
          title: m.title,
          sub: `${m.map} · ${layer ? layer.name : m.layer}`,
          tag: (CATEGORIES[m.category] || {}).label || m.category,
          marker: m,
        });
        if (results.length >= 35) break;
      }
    }

    // 2. Search zones & wild Pokémon species across all layers
    for (const layer of state.data.layers) {
      for (const z of layer.zones) {
        let matchedSpecies = null;
        if (z.encounters) {
          const verTable = z.encounters[state.version] || {};
          for (const mons of Object.values(verTable)) {
            const hit = mons.find((p) => p.species.toLowerCase().includes(query));
            if (hit) {
              matchedSpecies = `${hit.species} (${hit.rate}%, Lv.${hit.min}${hit.min !== hit.max ? "–" + hit.max : ""})`;
              break;
            }
          }
        }
        if (z.name.toLowerCase().includes(query) || matchedSpecies) {
          results.push({
            type: "zone",
            title: matchedSpecies ? `${z.name} — Wild ${matchedSpecies}` : z.name,
            sub: layer.name,
            tag: matchedSpecies ? `Wild (${state.version})` : "Area",
            layerId: layer.id,
            zone: z,
          });
          if (results.length >= 60) break;
        }
      }
    }

    if (!results.length) {
      elSearchResults.innerHTML = `<div class="search-item"><span class="search-item-sub">No matches for "${escapeHtml(q)}"</span></div>`;
    } else {
      elSearchResults.innerHTML = results
        .map(
          (r, i) => `
          <div class="search-item" data-idx="${i}">
            <div>
              <div class="search-item-title">${escapeHtml(r.title)}</div>
              <div class="search-item-sub">${escapeHtml(r.sub)}</div>
            </div>
            <span class="search-item-tag">${escapeHtml(r.tag)}</span>
          </div>`
        )
        .join("");

      elSearchResults.querySelectorAll(".search-item").forEach((el) => {
        el.addEventListener("click", () => {
          const r = results[Number(el.dataset.idx)];
          elSearchResults.classList.add("hidden");
          if (r.type === "marker") {
            const m = r.marker;
            if (!state.activeCategories.has(m.category)) {
              state.activeCategories.add(m.category);
            }
            if (state.layerId !== m.layer) loadLayer(m.layer, false);
            focusPoint(m.x, m.y, 1.6);
            selectMarker(m);
          } else {
            if (state.layerId !== r.layerId) loadLayer(r.layerId, false);
            focusPoint(r.zone.x + r.zone.w / 2, r.zone.y + r.zone.h / 2, 1.0);
            selectZone(r.zone);
          }
        });
      });
    }
    elSearchResults.classList.remove("hidden");
  }

  // ---------------- Event Wiring ----------------

  function bindEvents() {
    elLayerSelect.addEventListener("change", () => {
      closeDrawer();
      loadLayer(elLayerSelect.value, true);
    });

    elSearchInput.addEventListener("input", () => runSearch(elSearchInput.value));
    elSearchInput.addEventListener("focus", () => {
      if (elSearchInput.value.trim().length >= 2) runSearch(elSearchInput.value);
    });
    elClearSearch.addEventListener("click", () => {
      elSearchInput.value = "";
      elSearchResults.classList.add("hidden");
      elClearSearch.classList.add("hidden");
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".search-section")) elSearchResults.classList.add("hidden");
    });

    // Version toggle
    const setVer = (ver) => {
      state.version = ver;
      $("btn-ver-fr").classList.toggle("active", ver === "FR");
      $("btn-ver-lg").classList.toggle("active", ver === "LG");
      if (state.selectedZoneId) {
        const z = getLayer().zones.find((x) => x.id === state.selectedZoneId);
        if (z) selectZone(z);
      }
      if (elSearchInput.value.trim().length >= 2 && !elSearchResults.classList.contains("hidden")) {
        runSearch(elSearchInput.value);
      }
    };
    $("btn-ver-fr").addEventListener("click", () => setVer("FR"));
    $("btn-ver-lg").addEventListener("click", () => setVer("LG"));

    // Progress & view toggles
    $("chk-hide-found").addEventListener("change", (e) => {
      state.hideFound = e.target.checked;
      renderMarkers();
    });
    $("chk-show-labels").addEventListener("change", (e) => {
      state.showLabels = e.target.checked;
      renderZones();
      applyTransform();
    });

    $("btn-show-all").addEventListener("click", () => {
      state.activeCategories = new Set(Object.keys(CATEGORIES));
      renderCategories();
      renderMarkers();
    });
    $("btn-hide-all").addEventListener("click", () => {
      state.activeCategories.clear();
      renderCategories();
      renderMarkers();
    });

    // Sidebar collapse
    $("btn-collapse-sidebar").addEventListener("click", () => {
      elSidebar.classList.add("hidden");
      $("btn-open-sidebar").classList.remove("hidden");
    });
    $("btn-open-sidebar").addEventListener("click", () => {
      elSidebar.classList.remove("hidden");
      $("btn-open-sidebar").classList.add("hidden");
    });

    // Drawer close & Type Chart modal
    $("btn-close-drawer").addEventListener("click", closeDrawer);

    const elTypeModal = $("typechart-modal");
    const closeTypeModal = () => elTypeModal.classList.add("hidden");
    $("btn-open-typechart").addEventListener("click", () => elTypeModal.classList.remove("hidden"));
    $("btn-close-typechart").addEventListener("click", closeTypeModal);
    elTypeModal.addEventListener("click", (e) => {
      if (e.target === elTypeModal) closeTypeModal();
    });

    // Export / Import / Reset
    $("btn-export-progress").addEventListener("click", () => {
      const blob = new Blob([JSON.stringify([...state.foundIds], null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "frlg-plus-map-progress.json";
      a.click();
    });
    $("btn-import-progress").addEventListener("click", () => $("import-file-input").click());
    $("import-file-input").addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      try {
        const arr = JSON.parse(await file.text());
        if (Array.isArray(arr)) {
          state.foundIds = new Set(arr);
          saveProgress();
          renderCategories();
          renderMarkers();
        }
      } catch (_) {
        alert("Invalid progress JSON file.");
      }
      e.target.value = "";
    });
    $("btn-reset-progress").addEventListener("click", () => {
      if (confirm("Reset all marked/found progress?")) {
        state.foundIds.clear();
        saveProgress();
        renderCategories();
        renderMarkers();
        if (state.selectedMarkerId) {
          const m = state.data.markers.find((x) => x.id === state.selectedMarkerId);
          if (m) selectMarker(m);
        }
      }
    });

    // Pan & Zoom
    elViewport.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      state.dragging = true;
      state.dragMoved = false;
      state.dragStartX = e.clientX - state.tx;
      state.dragStartY = e.clientY - state.ty;
      elViewport.classList.add("dragging");
    });

    window.addEventListener("pointermove", (e) => {
      const rect = elViewport.getBoundingClientRect();
      if (e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom) {
        const mapX = Math.floor((e.clientX - rect.left - state.tx) / state.scale / 16);
        const mapY = Math.floor((e.clientY - rect.top - state.ty) / state.scale / 16);
        elHudCoords.textContent = `Tile X: ${mapX}, Y: ${mapY}`;
      }
      if (!state.dragging) return;
      const nx = e.clientX - state.dragStartX;
      const ny = e.clientY - state.dragStartY;
      if (Math.abs(nx - state.tx) > 3 || Math.abs(ny - state.ty) > 3) {
        state.dragMoved = true;
      }
      state.tx = nx;
      state.ty = ny;
      applyTransform();
    });

    window.addEventListener("pointerup", () => {
      state.dragging = false;
      elViewport.classList.remove("dragging");
      setTimeout(() => (state.dragMoved = false), 30);
    });

    elViewport.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        // Normalize delta across trackpads (pixel mode) and mouse wheels (line/page mode)
        let dy = e.deltaY;
        if (e.deltaMode === 1) dy *= 24;
        else if (e.deltaMode === 2) dy *= 300;
        dy = Math.max(-80, Math.min(80, dy));
        const sensitivity = e.ctrlKey ? 0.008 : 0.0022;
        const factor = Math.exp(-dy * sensitivity);
        zoomAt(factor, e.clientX, e.clientY);
      },
      { passive: false }
    );

    $("btn-zoom-in").addEventListener("click", () => {
      const r = elViewport.getBoundingClientRect();
      zoomAt(1.35, r.left + r.width / 2, r.top + r.height / 2);
    });
    $("btn-zoom-out").addEventListener("click", () => {
      const r = elViewport.getBoundingClientRect();
      zoomAt(1 / 1.35, r.left + r.width / 2, r.top + r.height / 2);
    });
    $("btn-zoom-fit").addEventListener("click", fitToScreen);

    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (!elTypeModal.classList.contains("hidden")) closeTypeModal();
        else closeDrawer();
      }
    });
  }

  // ---------------- Boot ----------------

  async function init() {
    loadProgress();
    if (window.FRLG_MAP_DATA) {
      state.data = window.FRLG_MAP_DATA;
    } else {
      const res = await fetch("assets/data/map_data.json");
      state.data = await res.json();
    }
    populateLayerSelect();
    bindEvents();
    loadLayer("kanto", true);
  }

  init();
})();
