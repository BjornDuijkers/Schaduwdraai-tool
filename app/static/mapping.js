const iconPaths = {
  home: '<path d="M3 11.5 12 4l9 7.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 19.5z"/><path d="M9 21v-7h6v7"/>',
  clipboard: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 3.5V2h6v1.5M9 9h6m-6 4h4m-4 4h3"/>',
  "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/>',
  user: '<circle cx="12" cy="8" r="3"/><path d="M5 20c.7-4 3-6 7-6s6.3 2 7 6"/><circle cx="12" cy="12" r="9"/>',
  chart: '<rect x="4" y="7" width="16" height="14" rx="2"/><path d="M8 17V9m4 8V4m4 13v-6"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19 14.5l1.5 1-.1 2.2-2 1-1 2-2.2.5-1.2-1.4h-4L8.8 21.2l-2.2-.5-1-2-2-1-.1-2.2 1.5-1v-5l-1.5-1 .1-2.2 2-1 1-2 2.2-.5L10 4.2h4l1.2-1.4 2.2.5 1 2 2 1 .1 2.2-1.5 1z"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.8 9a2.4 2.4 0 1 1 3.5 2.2c-.9.5-1.3 1-1.3 2.1M12 17h.01"/>',
  "chevron-down": '<path d="m7 10 5 5 5-5"/>',
  "chevron-right": '<path d="m9 6 6 6-6 6"/>',
  moon: '<path d="M20 15.5A8 8 0 0 1 8.5 4a8 8 0 1 0 11.5 11.5z"/>',
  document: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h4"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  swap: '<path d="m7 7 3-3m-3 3 3 3M7 7h11M17 17l-3 3m3-3-3-3m3 3H6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  filter: '<path d="M4 5h16l-6.3 7.2V19l-3.4 2v-8.8z"/>',
  sparkles: '<path d="m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2zM6 14l.8 2.2L9 17l-2.2.8L6 20l-.8-2.2L3 17l2.2-.8zM18 14l.6 1.4L20 16l-1.4.6L18 18l-.6-1.4L16 16l1.4-.6z"/>',
  more: '<circle cx="12" cy="5" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="19" r="1" fill="currentColor" stroke="none"/>',
  refresh: '<path d="M16.5 7.5A6 6 0 1 0 18 13M17 4v4h-4"/>'
};

function svgIcon(name) {
  return `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${iconPaths[name] || ""}</svg>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalize(value) {
  return String(value ?? "").toLocaleLowerCase("nl").replace(/[^a-z0-9]+/g, " ").trim();
}

document.querySelectorAll("[data-icon]").forEach((node) => { node.innerHTML = svgIcon(node.dataset.icon); });

const dataElement = document.querySelector("#mapping-data");
const workspace = JSON.parse(dataElement?.textContent || "null") || {
  project: null, source: [], target: [], relations: [],
  stats: { progress: 0, mapped: 0, proposed: 0, review: 0, unmapped: 0, total: 0 }
};
const statusLabels = { matched: "Gemat", proposed: "Voorgesteld", review: "Te beoordelen", unmapped: "Ongemapt" };
const state = {
  sourceQuery: "", targetQuery: "", filters: new Set(), relations: true,
  selectedSource: null, selectedTarget: null, busy: false
};

function relationForSource(name) {
  return workspace.relations.find((relation) => normalize(relation.source) === normalize(name));
}

function relationForTarget(name) {
  return workspace.relations.find((relation) => normalize(relation.target) === normalize(name));
}

function sourceRowTemplate(row) {
  const relation = relationForSource(row.name);
  const relationCopy = relation ? `→ ${escapeHtml(relation.target)}` : "Nog niet gekoppeld";
  const checked = state.selectedSource === row.name ? "checked" : "";
  const selected = checked ? " selected" : "";
  const confidence = relation && relation.status !== "matched" ? `<span class="confidence">${Math.round(relation.confidence * 100)}%</span>` : "";
  return `<div class="component-row source-row${selected}" data-name="${escapeHtml(normalize(row.name))}" data-value="${escapeHtml(row.name)}" data-status="${row.mapping_status}">
    <label class="row-check"><input type="radio" name="source-component" value="${escapeHtml(row.name)}" aria-label="${escapeHtml(row.name)} selecteren" ${checked}/><span></span></label>
    <span class="component-name">${escapeHtml(row.name)}<small class="mobile-relation">${relationCopy}</small></span>
    <span class="status-pill ${row.mapping_status}">${statusLabels[row.mapping_status]}</span>${confidence}
    <button class="row-menu" type="button" aria-label="Details voor ${escapeHtml(row.name)}">${svgIcon("more")}</button>
  </div>`;
}

function targetRowTemplate(row) {
  const relation = relationForTarget(row.name);
  const checked = state.selectedTarget === row.name ? "checked" : "";
  const selected = checked ? " selected" : "";
  const marker = row.mapping_status === "matched" ? "✓" : "";
  return `<div class="component-row target-row${selected}" data-name="${escapeHtml(normalize(row.name))}" data-value="${escapeHtml(row.name)}" data-status="${row.mapping_status}">
    <label class="target-select"><input type="radio" name="target-component" value="${escapeHtml(row.name)}" aria-label="${escapeHtml(row.name)} selecteren" ${checked}/><span class="refresh-icon">${svgIcon("refresh")}</span></label>
    <span class="component-name">${escapeHtml(row.name)}${relation ? `<small class="mobile-relation">← ${escapeHtml(relation.source)}</small>` : ""}</span>
    <span class="status-icon ${row.mapping_status}" aria-label="${statusLabels[row.mapping_status]}">${marker}</span>
    <button class="row-menu" type="button" aria-label="Details voor ${escapeHtml(row.name)}">${svgIcon("more")}</button>
  </div>`;
}

function rowVisible(row, query) {
  return normalize(row.name).includes(query) && (!state.filters.size || state.filters.has(row.mapping_status));
}

function renderRows() {
  const visibleSource = workspace.source.filter((row) => rowVisible(row, state.sourceQuery));
  const visibleTarget = workspace.target.filter((row) => rowVisible(row, state.targetQuery));
  document.querySelector("#source-list").innerHTML = visibleSource.map(sourceRowTemplate).join("") || '<p class="empty-state">Geen componenten gevonden.</p>';
  document.querySelector("#target-list").innerHTML = visibleTarget.map(targetRowTemplate).join("") || '<p class="empty-state">Geen componenten gevonden.</p>';
  document.querySelector("#source-visible-count").textContent = visibleSource.length;
  document.querySelector("#target-visible-count").textContent = visibleTarget.length;
  document.querySelector("#selected-count").textContent = Number(Boolean(state.selectedSource)) + Number(Boolean(state.selectedTarget));
  bindRows();
  renderRelations(visibleSource, visibleTarget);
  updateMapButton();
}

function bindRows() {
  document.querySelectorAll('input[name="source-component"]').forEach((input) => input.addEventListener("change", () => {
    state.selectedSource = input.value;
    const suggestion = relationForSource(input.value);
    if (suggestion && suggestion.status !== "matched") state.selectedTarget = suggestion.target;
    renderRows();
  }));
  document.querySelectorAll('input[name="target-component"]').forEach((input) => input.addEventListener("change", () => {
    state.selectedTarget = input.value;
    renderRows();
  }));
  document.querySelectorAll(".component-row").forEach((row) => row.addEventListener("click", (event) => {
    if (event.target.closest("button, input, label")) return;
    const radio = row.querySelector('input[type="radio"]');
    radio.click();
  }));
  document.querySelectorAll(".row-menu").forEach((button) => button.addEventListener("click", () => {
    const row = button.closest(".component-row");
    const item = [...workspace.source, ...workspace.target].find((candidate) => candidate.name === row.dataset.value);
    showToast(`${row.dataset.value}: ${item?.count || 0} regels, totaal ${item?.total || "0,00"}`);
  }));
}

function renderRelations(sourceRows, targetRows) {
  const canvas = document.querySelector("#relations-canvas");
  const svg = canvas.querySelector("svg");
  const height = Math.max(sourceRows.length, targetRows.length, 11) * 40;
  canvas.style.height = `${height}px`;
  svg.style.height = `${height}px`;
  svg.setAttribute("viewBox", `0 0 382 ${height}`);
  if (!state.relations) {
    svg.innerHTML = "";
    return;
  }
  const sourceIndex = new Map(sourceRows.map((row, index) => [normalize(row.name), index]));
  const targetIndex = new Map(targetRows.map((row, index) => [normalize(row.name), index]));
  svg.innerHTML = workspace.relations.map((relation) => {
    const sourcePosition = sourceIndex.get(normalize(relation.source));
    const targetPosition = targetIndex.get(normalize(relation.target));
    if (sourcePosition === undefined || targetPosition === undefined) return "";
    const y1 = sourcePosition * 40 + 20;
    const y2 = targetPosition * 40 + 20;
    const cssStatus = relation.status === "review" ? "review" : relation.status;
    return `<path class="relation ${cssStatus}-line" d="M3 ${y1} C130 ${y1} 252 ${y2} 379 ${y2}"/>
      <circle class="${cssStatus}-point" cx="3" cy="${y1}" r="4"/><circle class="${cssStatus}-point" cx="379" cy="${y2}" r="4"/>`;
  }).join("");
}

function initializeWorkspace() {
  const project = workspace.project;
  const stats = workspace.stats;
  if (project) {
    document.querySelector("#source-provider").textContent = `Systeem 1 (${project.provider_a})`;
    document.querySelector("#target-provider").textContent = `Systeem 2 (${project.provider_b})`;
    document.querySelector("#source-badge").textContent = String(project.provider_a).slice(0, 4).toUpperCase();
    document.querySelector("#source-system-name").textContent = project.source_a;
    document.querySelector("#target-system-name").textContent = project.source_b;
    document.querySelector("#source-title").innerHTML = `${escapeHtml(project.provider_a)} <span>(${workspace.source.length})</span>`;
    document.querySelector("#target-title").innerHTML = `${escapeHtml(project.provider_b)} <span>(${workspace.target.length})</span>`;
    document.querySelector("#source-search").placeholder = `Zoek component in ${project.provider_a}...`;
    document.querySelector("#target-search").placeholder = `Zoek component in ${project.provider_b}...`;
  }
  document.querySelector("#progress-percent").textContent = `${stats.progress}%`;
  document.querySelector("#progress-copy").textContent = `${stats.mapped} van ${stats.total} componenten gemapt`;
  const ring = document.querySelector("#progress-ring");
  ring.setAttribute("aria-label", `${stats.progress} procent voltooid`);
  ring.style.background = `conic-gradient(var(--matched) 0 ${stats.progress}%, #e4e7ef ${stats.progress}% 100%)`;
  document.querySelector("#stat-mapped").textContent = stats.mapped;
  document.querySelector("#stat-proposed").textContent = stats.proposed;
  document.querySelector("#stat-unmapped").textContent = stats.unmapped;
  document.querySelector("#stat-review").textContent = stats.review;
  const recommended = workspace.relations.filter((relation) => relation.status === "proposed" && relation.recommended);
  const acceptButton = document.querySelector("#accept-suggestions");
  acceptButton.textContent = `Suggesties accepteren (${recommended.length})`;
  acceptButton.disabled = !project || recommended.length === 0;
  document.querySelector("#auto-map").disabled = !project;
}

function updateMapButton() {
  const button = document.querySelector("#auto-map");
  button.disabled = !workspace.project || !state.selectedSource || !state.selectedTarget || state.busy;
  button.innerHTML = `${svgIcon("sparkles")}${state.selectedSource && state.selectedTarget ? "Selectie koppelen" : "Selecteer twee componenten"}`;
}

async function saveMappings(mappings) {
  if (!workspace.project || !mappings.length || state.busy) return;
  state.busy = true;
  updateMapButton();
  document.querySelector("#accept-suggestions").disabled = true;
  showToast(mappings.length === 1 ? "Mapping wordt opgeslagen..." : `${mappings.length} mappings worden opgeslagen...`);
  try {
    const endpoint = mappings.length === 1
      ? `/api/projects/${workspace.project.id}/component-aliases`
      : `/api/projects/${workspace.project.id}/component-aliases/batch`;
    const body = mappings.length === 1 ? mappings[0] : { mappings };
    const response = await fetch(endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "Mapping kon niet worden opgeslagen.");
    showToast(mappings.length === 1 ? "Mapping opgeslagen en toegepast" : `${payload.saved} mappings opgeslagen en toegepast`);
    window.setTimeout(() => { window.location.href = `/?project_id=${encodeURIComponent(workspace.project.id)}`; }, 550);
  } catch (error) {
    state.busy = false;
    updateMapButton();
    initializeWorkspace();
    showToast(error.message);
  }
}

function selectedMapping() {
  if (!state.selectedSource || !state.selectedTarget) return null;
  const suggestion = workspace.relations.find((relation) =>
    relation.source === state.selectedSource && relation.target === state.selectedTarget
  );
  return {
    source_a: state.selectedSource,
    source_b: state.selectedTarget,
    canonical: suggestion?.canonical || state.selectedSource
  };
}

function showToast(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => { toast.hidden = true; }, 3200);
}

function closeMenus() {
  document.querySelectorAll(".popover").forEach((menu) => { menu.hidden = true; });
  document.querySelectorAll('[aria-expanded="true"]').forEach((button) => {
    if (!button.classList.contains("mobile-menu")) button.setAttribute("aria-expanded", "false");
  });
}

function toggleMenu(button, menu) {
  const open = menu.hidden;
  closeMenus();
  menu.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
}

document.querySelector("#source-search").addEventListener("input", (event) => { state.sourceQuery = normalize(event.target.value); renderRows(); });
document.querySelector("#target-search").addEventListener("input", (event) => { state.targetQuery = normalize(event.target.value); renderRows(); });
document.querySelector("#project-select").addEventListener("change", (event) => {
  if (event.target.value) window.location.href = `/?project_id=${encodeURIComponent(event.target.value)}`;
});
document.querySelector("#theme-toggle").addEventListener("change", (event) => {
  document.documentElement.dataset.theme = event.target.checked ? "dark" : "light";
});
document.querySelector("#relations-toggle").addEventListener("change", (event) => {
  state.relations = event.target.checked;
  document.querySelectorAll(".mobile-relation").forEach((label) => { label.hidden = !state.relations; });
  renderRows();
});
document.querySelector("#auto-map").addEventListener("click", () => {
  const mapping = selectedMapping();
  if (mapping) saveMappings([mapping]);
});
document.querySelector("#accept-suggestions").addEventListener("click", () => {
  const suggestions = workspace.relations
    .filter((relation) => relation.status === "proposed" && relation.recommended)
    .map((relation) => ({ source_a: relation.source, source_b: relation.target, canonical: relation.canonical }));
  if (suggestions.length) saveMappings(suggestions);
});

const filterPanel = document.querySelector("#filter-panel");
document.querySelector("#filter-options").innerHTML = Object.entries(statusLabels)
  .map(([status, label]) => `<label><input type="checkbox" value="${status}" />${label}</label>`).join("");
document.querySelectorAll(".filter-button").forEach((button) => button.addEventListener("click", () => {
  const open = filterPanel.hidden;
  filterPanel.hidden = !open;
  document.querySelectorAll(".filter-button").forEach((item) => item.setAttribute("aria-expanded", String(open)));
}));
filterPanel.addEventListener("change", (event) => {
  if (!event.target.matches("input")) return;
  event.target.checked ? state.filters.add(event.target.value) : state.filters.delete(event.target.value);
  renderRows();
});
document.querySelector("#clear-filters").addEventListener("click", () => {
  state.filters.clear();
  filterPanel.querySelectorAll("input").forEach((input) => { input.checked = false; });
  renderRows();
});

const profileButton = document.querySelector(".profile-button");
if (profileButton) {
  profileButton.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleMenu(profileButton, document.querySelector("#profile-menu"));
  });
}
const actionButton = document.querySelector(".action-button");
actionButton.addEventListener("click", (event) => {
  event.stopPropagation();
  toggleMenu(actionButton, document.querySelector("#actions-menu"));
});
document.querySelector("#clear-selection").addEventListener("click", () => {
  state.selectedSource = null;
  state.selectedTarget = null;
  closeMenus();
  renderRows();
});
document.querySelector("#mark-review").addEventListener("click", () => {
  closeMenus();
  showToast("Selecteer beide componenten en sla de koppeling op wanneer deze is beoordeeld.");
});
document.querySelector(".info-button").addEventListener("click", () => {
  showToast("Selecteer links een broncomponent en rechts het bijbehorende doelcomponent. De mapping wordt lokaal opgeslagen.");
});
const supportButton = document.querySelector(".support-button");
if (supportButton) {
  supportButton.addEventListener("click", () => showToast("Mappings worden lokaal opgeslagen in de Schaduwdraaitool-database."));
}
document.querySelectorAll(".nav-item[data-section]").forEach((button) => button.addEventListener("click", () => showToast(`${button.dataset.section} is nog niet ingericht.`)));

const mobileMenu = document.querySelector(".mobile-menu");
mobileMenu.addEventListener("click", () => {
  const open = document.body.classList.toggle("nav-open");
  mobileMenu.setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".profile-button, .action-button, .popover")) closeMenus();
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  closeMenus();
  document.body.classList.remove("nav-open");
  mobileMenu.setAttribute("aria-expanded", "false");
});

initializeWorkspace();
renderRows();
