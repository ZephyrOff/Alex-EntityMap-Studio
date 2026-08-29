/* =========================================================================
 * === alex-entitymap-studio-panel ==========================================
 * Explorateur d'entites : liste par domaine, recherche, et pour chaque
 * entite selectionnee -- piece, derniere utilisation, dependances (ce
 * qu'un script/une automatisation utilise) et appelants (qui la
 * reference -- automatisations, scripts, dashboards). Les correspondances
 * trouvees par analyse de motif (Jinja avec concatenation, pas litterales)
 * sont annoncees comme telles, jamais presentees comme une certitude.
 * ========================================================================= */

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function timeAgo(iso) {
  if (!iso) return "jamais observé";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "jamais observé";
  const diffMs = Date.now() - then;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "à l'instant";
  if (mins < 60) return `il y a ${mins} min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `il y a ${hours} h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `il y a ${days} j`;
  const months = Math.floor(days / 30);
  if (months < 12) return `il y a ${months} mois`;
  return `il y a ${Math.floor(months / 12)} an(s)`;
}

const SOURCE_TYPE_LABELS = {
  automation: "Automatisation",
  script: "Script",
  dashboard: "Dashboard",
};

// Palette deliberement eloignee des couleurs "materiel design" par defaut
// (bleu/vert/rouge satures) -- tons plus doux inspires des teintes 300-400
// de Tailwind, avec du texte sombre dessus (meilleur contraste que du blanc
// sur ces tons clairs) pour un rendu plus sobre que des blocs satures.
const NODE_KIND_COLORS = {
  trigger: "#5eead4",
  condition: "#fcd34d",
  if: "#fcd34d",
  choose: "#c4b5fd",
  action: "#6ee7b7",
  stop: "#fca5a5",
  opaque: "#cbd5e1",
};
// Glyphes geometriques simples (bloc Unicode "Geometric Shapes", tres
// largement supportes) plutot que des icones custom -- un symbole distinct
// par role suffit a distinguer les noeuds d'un coup d'oeil, meme daltonien.
const NODE_KIND_ICONS = {
  trigger: "▶",
  condition: "◆",
  if: "◆",
  choose: "✦",
  action: "●",
  stop: "■",
  opaque: "○",
};
const NODE_W = 190;
const NODE_H = 56;

// Disposition en couches par distance (BFS) au declencheur le plus proche --
// testee isolement avant integration (if/then/else sur deux couches
// distinctes cote a cote, plusieurs declencheurs convergeant correctement).
function layoutGraph(nodes, edges, triggerIds) {
  const depth = {};
  const queue = [...triggerIds];
  triggerIds.forEach((id) => (depth[id] = 0));
  let qi = 0;
  while (qi < queue.length) {
    const id = queue[qi++];
    const d = depth[id];
    edges
      .filter((e) => e.source === id)
      .forEach((e) => {
        if (depth[e.target] === undefined || depth[e.target] < d + 1) {
          depth[e.target] = d + 1;
          queue.push(e.target);
        }
      });
  }
  nodes.forEach((n) => {
    if (depth[n.id] === undefined) depth[n.id] = 0;
  });

  const layers = {};
  nodes.forEach((n) => {
    const d = depth[n.id];
    (layers[d] = layers[d] || []).push(n.id);
  });

  const positions = {};
  const LAYER_HEIGHT = 130;
  const COL_WIDTH = NODE_W + 40;
  Object.keys(layers)
    .sort((a, b) => a - b)
    .forEach((d) => {
      const ids = layers[d];
      ids.forEach((id, i) => {
        positions[id] = { x: 40 + i * COL_WIDTH, y: 40 + d * LAYER_HEIGHT };
      });
    });
  return positions;
}

class AlexEntityMapStudioPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._built = false;
    this._entities = [];
    this._loading = false;
    this._error = null;
    this._selected = null;
    this._filterDomain = "";
    this._filterText = "";
    // Vue active : "checker" (existant), "info" (nouvelle), "automation" (nouvelle, en construction).
    this._activeView = "checker";
    // Cache des descriptions de services HA (get_services, commande websocket
    // native) -- charge une seule fois, reutilise pour toutes les entites
    // consultees en vue Entity Info plutot que de rappeler a chaque selection.
    this._servicesCache = null;

    // Vue Automation Checker.
    this._automationSelected = null; // entity_id de l'automatisation/script choisi
    this._automationGraph = null; // {nodes, edges, trigger_ids, condition_entities}
    this._nodePositions = {}; // {node_id: {x,y}} -- calcule par layoutGraph, deplacable a la souris
    this._graphPan = { x: 0, y: 0 };
    this._graphZoom = 1;
    this._graphDragNode = null; // id du noeud en cours de glissement, ou null
    this._graphPanning = false;
    this._selectedTriggerId = null;
    this._stateOverrides = {}; // {entity_id: valeur forcee pour la simulation}
    this._simulationResult = null; // {visited_node_ids, taken_edges, undetermined_at, stopped_reason}
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built && this.isConnected) {
      this._renderShell();
      this._built = true;
      this._loadData();
    }
  }

  set panel(panel) {
    this._panelConfig = panel && panel.config;
  }

  connectedCallback() {
    if (this._hass && !this._built) {
      this._renderShell();
      this._built = true;
      this._loadData();
    }
  }

  async _loadData() {
    this._loading = true;
    this._error = null;
    this._renderBody();
    try {
      const result = await this._hass.callWS({ type: "alex_entitymap_studio/get_map" });
      this._entities = (result && result.entities) || [];
    } catch (err) {
      this._error = (err && err.message) || String(err);
      this._entities = [];
    }
    this._loading = false;
    this._renderBody();
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          height: 100%;
          overflow: hidden;
          background: var(--primary-background-color, #111);
          color: var(--primary-text-color, #fff);
          font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
          box-sizing: border-box;
        }
        * { box-sizing: border-box; }
        .header {
          display: flex;
          align-items: center;
          flex-wrap: wrap;
          gap: 12px;
          padding: 16px 24px;
          background: var(--app-header-background-color, var(--primary-color, #03a9f4));
        }
        .header button.menu-btn {
          display: none;
          width: 40px; height: 40px;
          border-radius: 8px; border: none;
          background: transparent; color: white;
          cursor: pointer; align-items: center; justify-content: center;
          flex-shrink: 0;
        }
        .header button.menu-btn svg { width: 24px; height: 24px; fill: currentColor; }
        @media (max-width: 870px) { .header button.menu-btn { display: flex; } }
        .header h1 { margin: 0; font-size: 20px; font-weight: 500; color: white; flex: 1; }
        .refresh-btn {
          border: none; background: rgba(255,255,255,.15); color: white;
          border-radius: 8px; padding: 8px 14px; cursor: pointer; font-size: 13px;
        }
        .actions { display: flex; gap: 8px; flex-wrap: wrap; }
        .btn {
          padding: 8px 14px; border-radius: 8px; border: none; cursor: pointer;
          font-size: 13px; font-weight: 600;
        }
        .btn-outline { background: rgba(255,255,255,.12); color: white; }
        .btn-outline.active { background: white; color: var(--primary-color, #03a9f4); }
        #graph-wrap {
          position: relative;
          border: 1px solid rgba(255,255,255,.08); border-radius: 14px;
          overflow: hidden; touch-action: none;
          background-color: #14171c;
          background-image: radial-gradient(rgba(255,255,255,.07) 1px, transparent 1px);
          background-size: 24px 24px;
        }
        #automation-graph-svg { display: block; cursor: grab; }
        #automation-graph-svg.panning { cursor: grabbing; }
        .graph-node rect {
          stroke: rgba(255,255,255,.85); stroke-width: 1; cursor: grab;
          filter: drop-shadow(0 3px 6px rgba(0,0,0,.35));
        }
        .graph-node .node-icon { font-size: 15px; fill: rgba(0,0,0,.55); }
        .graph-node text.node-label { fill: rgba(0,0,0,.82); font-size: 11.5px; font-weight: 600; pointer-events: none; }
        .graph-node.visited rect { stroke: #fff59d; stroke-width: 2.5; }
        .graph-node.uncertain rect { stroke: rgba(255,255,255,.85); stroke-dasharray: 5,3; opacity: .6; }
        .node-detail-overlay {
          position: absolute; inset: 0; background: rgba(0,0,0,.55);
          display: flex; align-items: center; justify-content: center; z-index: 5;
        }
        .node-detail-box {
          background: var(--card-background-color, #1e1e1e); border-radius: 12px;
          max-width: 85%; max-height: 80%; overflow-y: auto;
          box-shadow: 0 8px 30px rgba(0,0,0,.5);
        }
        .node-detail-header {
          display: flex; justify-content: space-between; align-items: center;
          padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,.1);
          font-size: 12px; text-transform: uppercase; letter-spacing: .03em; color: var(--secondary-text-color);
        }
        .node-detail-close { cursor: pointer; opacity: .7; }
        .node-detail-close:hover { opacity: 1; }
        .node-detail-body { padding: 14px; font-size: 14px; line-height: 1.5; white-space: pre-wrap; }
        .graph-edge { stroke: rgba(255,255,255,.28); stroke-width: 2; fill: none; }
        .graph-edge.taken { stroke: #ffd54f; stroke-width: 3; filter: drop-shadow(0 0 3px rgba(255,213,79,.5)); }
        .graph-edge.uncertain { stroke: #ffd54f; stroke-width: 2; stroke-dasharray: 5,4; opacity: .65; }
        .graph-edge-label { fill: rgba(255,255,255,.75); font-size: 10px; }
        .layout { display: flex; height: calc(100% - 64px); }
        .sidebar {
          width: 340px; flex: 0 0 340px; overflow-y: auto;
          border-right: 1px solid var(--divider-color, #333);
          padding: 12px;
        }
        .filters { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
        .filters input, .filters select {
          padding: 8px 10px; border-radius: 8px;
          border: 1px solid var(--divider-color, #444);
          background: var(--card-background-color, #1e1e1e);
          color: var(--primary-text-color, #fff);
          font-size: 13px;
        }
        .entity-row {
          display: flex; flex-direction: column; gap: 2px;
          padding: 8px 10px; border-radius: 8px; cursor: pointer;
          margin-bottom: 2px;
        }
        .entity-row:hover { background: rgba(255,255,255,.06); }
        .entity-row.selected { background: rgba(var(--rgb-primary-color,3,169,244),.18); }
        .entity-row .eid { font-size: 12px; color: var(--secondary-text-color); }
        .entity-row .ename { font-size: 14px; font-weight: 600; }
        .content { flex: 1; overflow-y: auto; padding: 20px 28px; }
        .card {
          background: var(--card-background-color, #1e1e1e);
          border: 1px solid rgba(255,255,255,.06);
          border-radius: 16px; padding: 20px; margin-bottom: 18px;
          box-shadow: 0 2px 10px rgba(0,0,0,.18);
        }
        .card h2 { margin: 0 0 14px; font-size: 15px; font-weight: 600; }
        .kv-row { display: flex; gap: 10px; padding: 6px 0; font-size: 13px; }
        .kv-row .k { flex: 0 0 140px; color: var(--secondary-text-color); }
        .ref-list { display: flex; flex-direction: column; gap: 8px; }
        .ref-item {
          display: flex; align-items: center; gap: 10px;
          padding: 8px 10px; border-radius: 8px;
          border: 1px solid var(--divider-color, #333);
          font-size: 13px;
        }
        .ref-item.clickable { cursor: pointer; transition: background .12s ease; }
        .ref-item.clickable:hover { background: rgba(255,255,255,.06); }
        .badge {
          font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
          background: rgba(255,255,255,.1); flex: 0 0 auto;
        }
        .badge.pattern { background: rgba(244,169,53,.25); color: #f4a935; }
        .badge.exact { background: rgba(76,175,80,.2); color: #4caf50; }
        .badge.blueprint { background: rgba(3,169,244,.2); color: #03a9f4; }
        .empty { font-size: 13px; color: var(--secondary-text-color); padding: 8px 0; }
        .hint { font-size: 12px; color: var(--secondary-text-color); margin-top: 8px; line-height: 1.4; }
        .error { color: var(--error-color, #db4437); font-size: 13px; padding: 12px; }
      </style>

      <div class="header">
        <button class="menu-btn" id="menu-btn" title="Menu">
          <svg viewBox="0 0 24 24"><path d="M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z"/></svg>
        </button>
        <h1>Alex EntityMap Studio</h1>
        <div class="actions" style="margin:0 12px;">
          <button class="btn btn-outline" id="nav-checker-btn">Entity Checker</button>
          <button class="btn btn-outline" id="nav-info-btn">Entity Info</button>
          <button class="btn btn-outline" id="nav-automation-btn">Automation Checker</button>
        </div>
        <button class="refresh-btn" id="refresh-btn">Actualiser</button>
      </div>

      <div class="layout">
        <div class="sidebar" id="entity-sidebar">
          <div class="filters">
            <select id="domain-filter">
              <option value="">Tous les domaines</option>
            </select>
            <input type="text" id="text-filter" placeholder="Rechercher..." />
          </div>
          <div id="entity-list"></div>
        </div>
        <div class="content" id="content"></div>
        <div class="content" id="automation-content" style="display:none;"></div>
      </div>
    `;

    this.shadowRoot.querySelector("#menu-btn").addEventListener("click", () => {
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    });
    this.shadowRoot.querySelector("#nav-checker-btn").addEventListener("click", () => this._setActiveView("checker"));
    this.shadowRoot.querySelector("#nav-info-btn").addEventListener("click", () => this._setActiveView("info"));
    this.shadowRoot.querySelector("#nav-automation-btn").addEventListener("click", () => this._setActiveView("automation"));
    this._setActiveView(this._activeView);
    this.shadowRoot.querySelector("#refresh-btn").addEventListener("click", () => this._loadData());
    this.shadowRoot.querySelector("#domain-filter").addEventListener("change", (ev) => {
      this._filterDomain = ev.target.value;
      this._renderList();
    });
    this.shadowRoot.querySelector("#text-filter").addEventListener("input", (ev) => {
      this._filterText = ev.target.value.toLowerCase();
      this._renderList();
    });
  }

  _renderBody() {
    this._populateDomainFilter();
    this._renderList();
    this._renderContent();
  }

  _populateDomainFilter() {
    const sel = this.shadowRoot.querySelector("#domain-filter");
    if (!sel) return;
    const domains = Array.from(new Set(this._entities.map((e) => e.domain))).sort();
    const current = sel.value;
    sel.innerHTML =
      `<option value="">Tous les domaines</option>` +
      domains.map((d) => `<option value="${escapeHtml(d)}">${escapeHtml(d)}</option>`).join("");
    sel.value = current;
  }

  _filteredEntities() {
    return this._entities.filter((e) => {
      if (this._filterDomain && e.domain !== this._filterDomain) return false;
      if (this._filterText) {
        const hay = `${e.entity_id} ${e.name}`.toLowerCase();
        if (!hay.includes(this._filterText)) return false;
      }
      return true;
    });
  }

  _renderList() {
    const list = this.shadowRoot.querySelector("#entity-list");
    if (!list) return;

    if (this._loading) {
      list.innerHTML = `<div class="empty">Analyse en cours…</div>`;
      return;
    }
    if (this._error) {
      list.innerHTML = `<div class="error">Erreur : ${escapeHtml(this._error)}</div>`;
      return;
    }

    const items = this._filteredEntities().sort((a, b) => a.entity_id.localeCompare(b.entity_id));
    if (items.length === 0) {
      list.innerHTML = `<div class="empty">Aucune entité ne correspond.</div>`;
      return;
    }

    list.innerHTML = items
      .map(
        (e) => `
          <div class="entity-row ${this._selected === e.entity_id ? "selected" : ""}" data-id="${escapeHtml(e.entity_id)}">
            <div class="ename">${escapeHtml(e.name)}</div>
            <div class="eid">${escapeHtml(e.entity_id)}</div>
          </div>`
      )
      .join("");

    list.querySelectorAll(".entity-row").forEach((row) => {
      row.addEventListener("click", () => {
        this._selected = row.getAttribute("data-id");
        this._renderList();
        this._renderContent();
      });
    });
  }

  // Pour "Appelants" : qui reference l'entite selectionnee -- affiche la
  // source (automatisation/script/dashboard).
  // --- Navigation -----------------------------------------------------

  // Bascule entre les trois vues -- la barre laterale (liste d'entites)
  // reste partagee entre "checker" et "info" (meme jeu d'entites, deux
  // facons differentes de les consulter) ; "automation" a son propre
  // contenu, sans la barre laterale generique.
  _setActiveView(view) {
    this._activeView = view;
    const sidebar = this.shadowRoot.querySelector("#entity-sidebar");
    const content = this.shadowRoot.querySelector("#content");
    const automationContent = this.shadowRoot.querySelector("#automation-content");
    if (sidebar) sidebar.style.display = view === "automation" ? "none" : "block";
    if (content) content.style.display = view === "automation" ? "none" : "block";
    if (automationContent) automationContent.style.display = view === "automation" ? "block" : "none";

    ["checker", "info", "automation"].forEach((v) => {
      const btn = this.shadowRoot.querySelector(`#nav-${v}-btn`);
      if (btn) btn.classList.toggle("active", v === view);
    });

    if (view === "automation") {
      this._renderAutomationView();
    } else {
      this._renderContent();
    }
  }

  _openMoreInfo(entityId) {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId },
        bubbles: true,
        composed: true,
      })
    );
  }

  _navigate(path) {
    history.pushState(null, "", path);
    this.dispatchEvent(new CustomEvent("location-changed", { bubbles: true, composed: true }));
  }

  // "lovelace" (dashboard par defaut, fichier de stockage sans suffixe) ->
  // /lovelace/0 ; "lovelace.dashboard_jc" -> /dashboard_jc/0. Le nom du
  // fichier de stockage encode directement le chemin d'URL du dashboard --
  // confirme en inspectant des configurations HA reelles. Pointe vers la
  // premiere vue (index 0) : la vue/carte precise n'est pas encore tracee,
  // seulement le dashboard dans son ensemble.
  _dashboardUrlFromFilename(filename) {
    const withoutPrefix = filename.replace(/^lovelace\.?/, "");
    const urlPath = withoutPrefix || "lovelace";
    return `/${urlPath}/0`;
  }

  // Determine comment naviguer vers UNE entite precise (utilise a la fois
  // pour les cibles de "Dependances" et, indirectement, pour resoudre le
  // nav_id d'une entite deja chargee dans this._entities).
  _entityClickHandler(entityId) {
    const info = this._entities.find((e) => e.entity_id === entityId);
    if (info && info.nav_id && info.domain === "script") {
      return () => this._navigate(`/config/script/edit/${encodeURIComponent(info.nav_id)}`);
    }
    if (info && info.nav_id && info.domain === "automation") {
      return () => this._navigate(`/config/automation/edit/${encodeURIComponent(info.nav_id)}`);
    }
    if (info && info.nav_id && info.domain === "scene") {
      return () => this._navigate(`/config/scene/edit/${encodeURIComponent(info.nav_id)}`);
    }
    return () => this._openMoreInfo(entityId);
  }

  // Pour "Appelants" : qui reference l'entite selectionnee -- affiche la
  // source (automatisation/script/dashboard), cliquable pour y naviguer.
  _renderCallerList(refs) {
    if (!refs || refs.length === 0) {
      return `<div class="empty">Aucune trouvée.</div>`;
    }
    return `
      <div class="ref-list">
        ${refs
          .map(
            (r, i) => `
              <div class="ref-item clickable" data-caller-index="${i}">
                <span class="badge">${escapeHtml(SOURCE_TYPE_LABELS[r.source_type] || r.source_type)}</span>
                <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                  ${escapeHtml(r.source_id)}
                </span>
                <span class="badge ${r.confidence}">${r.confidence === "pattern" ? "détecté par motif" : "littéral"}</span>
              </div>`
          )
          .join("")}
      </div>`;
  }

  _wireCallerList(container, refs) {
    container.querySelectorAll("[data-caller-index]").forEach((el) => {
      const ref = refs[parseInt(el.getAttribute("data-caller-index"), 10)];
      if (!ref) return;
      if (ref.source_type === "dashboard") {
        el.addEventListener("click", () => this._navigate(this._dashboardUrlFromFilename(ref.source_id)));
      } else if (ref.source_nav_id) {
        const path =
          ref.source_type === "automation"
            ? `/config/automation/edit/${encodeURIComponent(ref.source_nav_id)}`
            : `/config/script/edit/${encodeURIComponent(ref.source_nav_id)}`;
        el.addEventListener("click", () => this._navigate(path));
      }
      // Sans source_nav_id resolu (correspondance alias/id non trouvee),
      // l'element reste affiche mais non cliquable plutot que de naviguer
      // vers un mauvais endroit.
    });
  }

  // Pour "Dépendances" : ce que l'entite selectionnee utilise -- affiche la
  // CIBLE (entity_id reference), pas la source (qui serait toujours
  // l'entite elle-meme, sans interet). Cas special "blueprint" : signale
  // explicitement l'usage d'un blueprint, meme sans entite associee (pas
  // cliquable, ce n'est pas une entite).
  _renderDependencyList(refs) {
    if (!refs || refs.length === 0) {
      return `<div class="empty">Aucune trouvée.</div>`;
    }
    return `
      <div class="ref-list">
        ${refs
          .map((r, i) => {
            if (r.confidence === "blueprint") {
              const bpPath = r.entity_id.replace(/^blueprint:/, "");
              return `
                <div class="ref-item">
                  <span class="badge">Blueprint</span>
                  <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                    ${escapeHtml(bpPath)}
                  </span>
                </div>`;
            }
            const domain = r.entity_id.split(".")[0];
            return `
              <div class="ref-item clickable" data-dep-index="${i}">
                <span class="badge">${escapeHtml(domain)}</span>
                <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                  ${escapeHtml(r.entity_id)}
                </span>
                ${r.via_blueprint ? `<span class="badge blueprint">via blueprint</span>` : ""}
                <span class="badge ${r.confidence}">${r.confidence === "pattern" ? "détecté par motif" : "littéral"}</span>
              </div>`;
          })
          .join("")}
      </div>`;
  }

  _wireDependencyList(container, refs) {
    container.querySelectorAll("[data-dep-index]").forEach((el) => {
      const ref = refs[parseInt(el.getAttribute("data-dep-index"), 10)];
      if (!ref || ref.confidence === "blueprint") return;
      el.addEventListener("click", this._entityClickHandler(ref.entity_id));
    });
  }

  _renderContent() {
    const content = this.shadowRoot.querySelector("#content");
    if (!content) return;

    const entity = this._entities.find((e) => e.entity_id === this._selected);
    if (!entity) {
      content.innerHTML = `<div class="empty">Sélectionne une entité dans la liste à gauche.</div>`;
      return;
    }

    if (this._activeView === "info") {
      this._renderEntityInfo(content, entity);
      return;
    }

    const hasPatternRefs =
      (entity.references || []).some((r) => r.confidence === "pattern") ||
      (entity.dependencies || []).some((r) => r.confidence === "pattern");

    content.innerHTML = `
      <div class="card">
        <h2>${escapeHtml(entity.name)}</h2>
        <div class="kv-row"><div class="k">Entity ID</div><div>${escapeHtml(entity.entity_id)}</div></div>
        <div class="kv-row"><div class="k">Domaine</div><div>${escapeHtml(entity.domain)}</div></div>
        <div class="kv-row"><div class="k">Pièce</div><div>${escapeHtml(entity.area || "non assignée")}</div></div>
        <div class="kv-row"><div class="k">Dernière utilisation</div><div>${escapeHtml(timeAgo(entity.last_used))}${entity.last_used_kind === "last_triggered" ? " (dernier déclenchement)" : entity.last_used_kind === "last_changed" ? " (dernier changement d'état)" : ""}</div></div>
        <div class="kv-row"><div class="k">État</div><div>${entity.disabled ? "Désactivée" : entity.hidden ? "Masquée" : "Active"}</div></div>
      </div>

      ${
        entity.domain === "automation" || entity.domain === "script"
          ? `<div class="card">
              <h2>Dépendances (ce que cette entité utilise)</h2>
              ${this._renderDependencyList(entity.dependencies)}
            </div>`
          : ""
      }

      <div class="card">
        <h2>Appelants (qui référence cette entité)</h2>
        ${this._renderCallerList(entity.references)}
      </div>

      ${
        hasPatternRefs
          ? `<div class="hint">
              « Détecté par motif » = trouvé via l'analyse d'une expression Jinja construite
              dynamiquement (concaténation), pas une correspondance littérale exacte dans le
              texte — fiable pour les motifs de concaténation simples, mais reste une
              détection indicative, pas une certitude absolue.
            </div>`
          : ""
      }
    `;

    if (entity.domain === "automation" || entity.domain === "script") {
      this._wireDependencyList(content, entity.dependencies || []);
    }
    this._wireCallerList(content, entity.references || []);
  }

  // --- Vue Entity Info --------------------------------------------------

  // Etat live (hass.states, deja disponible cote client, pas besoin d'un
  // aller-retour serveur) + actions possibles pour le domaine de l'entite,
  // via la commande websocket NATIVE "get_services" (utilisee par l'onglet
  // Outils de developpement > Actions de HA lui-meme) -- aucune commande
  // maison necessaire pour cette partie.
  async _renderEntityInfo(content, entity) {
    const state = this._hass.states[entity.entity_id];
    const attrs = (state && state.attributes) || {};
    const attrKeys = Object.keys(attrs).filter((k) => k !== "friendly_name");

    content.innerHTML = `
      <div class="card">
        <h2>${escapeHtml(entity.name)}</h2>
        <div class="kv-row"><div class="k">Entity ID</div><div>${escapeHtml(entity.entity_id)}</div></div>
        <div class="kv-row"><div class="k">Domaine</div><div>${escapeHtml(entity.domain)}</div></div>
        <div class="kv-row"><div class="k">Pièce</div><div>${escapeHtml(entity.area || "non assignée")}</div></div>
        <div class="kv-row"><div class="k">État actuel</div><div>${state ? escapeHtml(state.state) : "indisponible"}</div></div>
      </div>

      <div class="card">
        <h2>Attributs</h2>
        ${
          attrKeys.length
            ? attrKeys
                .map(
                  (k) => `
              <div class="kv-row"><div class="k">${escapeHtml(k)}</div><div style="word-break:break-word;">${escapeHtml(JSON.stringify(attrs[k]))}</div></div>`
                )
                .join("")
            : `<div class="empty">Aucun attribut.</div>`
        }
      </div>

      <div class="card" id="actions-card">
        <h2>Actions possibles</h2>
        <div class="empty">Chargement…</div>
      </div>
    `;

    const actionsCard = content.querySelector("#actions-card");
    try {
      if (!this._servicesCache) {
        this._servicesCache = await this._hass.callWS({ type: "get_services" });
      }
      // Si l'utilisateur a change de selection pendant le chargement, ne
      // pas ecraser l'affichage d'une autre entite entre-temps.
      if (this._selected !== entity.entity_id || this._activeView !== "info") return;

      const domainServices = (this._servicesCache && this._servicesCache[entity.domain]) || {};
      const names = Object.keys(domainServices).sort();
      if (!names.length) {
        actionsCard.innerHTML = `<h2>Actions possibles</h2><div class="empty">Aucune action connue pour le domaine « ${escapeHtml(entity.domain)} ».</div>`;
        return;
      }
      actionsCard.innerHTML = `
        <h2>Actions possibles (${escapeHtml(entity.domain)}.*)</h2>
        <div class="ref-list">
          ${names
            .map((name) => {
              const svc = domainServices[name] || {};
              const fieldNames = Object.keys(svc.fields || {});
              return `
                <div class="ref-item" style="flex-direction:column;align-items:flex-start;gap:4px;">
                  <span class="badge">${escapeHtml(entity.domain)}.${escapeHtml(name)}</span>
                  ${svc.description ? `<div style="color:var(--secondary-text-color);">${escapeHtml(svc.description)}</div>` : ""}
                  ${fieldNames.length ? `<div style="font-size:12px;color:var(--secondary-text-color);">Paramètres : ${fieldNames.map((f) => escapeHtml(f)).join(", ")}</div>` : ""}
                </div>`;
            })
            .join("")}
        </div>
      `;
    } catch (err) {
      if (this._selected !== entity.entity_id || this._activeView !== "info") return;
      actionsCard.innerHTML = `<h2>Actions possibles</h2><div class="error">Erreur : ${escapeHtml((err && err.message) || String(err))}</div>`;
    }
  }

  // --- Vue Automation Checker --------------------------------------------

  _renderAutomationView() {
    const el = this.shadowRoot.querySelector("#automation-content");
    if (!el) return;

    const automations = this._entities.filter((e) => e.domain === "automation" || e.domain === "script");

    el.innerHTML = `
      <div class="card">
        <h2>Automation Checker</h2>
        <div class="filters" style="margin-bottom:10px;">
          <input type="text" id="automation-search" placeholder="Rechercher une automatisation/un script..." style="width:100%;" />
        </div>
        <div id="automation-list" style="max-height:220px;overflow-y:auto;"></div>
      </div>
      <div id="graph-section"></div>
    `;

    this._automationSearchText = this._automationSearchText || "";
    this.shadowRoot.querySelector("#automation-search").value = this._automationSearchText;
    this._renderAutomationList(automations);

    this.shadowRoot.querySelector("#automation-search").addEventListener("input", (ev) => {
      this._automationSearchText = ev.target.value.toLowerCase();
      this._renderAutomationList(automations);
    });

    this._maybeRenderGraphSection();
  }

  _renderAutomationList(automations) {
    const list = this.shadowRoot.querySelector("#automation-list");
    if (!list) return;
    const query = this._automationSearchText || "";
    const filtered = automations.filter(
      (a) => a.name.toLowerCase().includes(query) || a.entity_id.toLowerCase().includes(query)
    );
    if (!filtered.length) {
      list.innerHTML = `<div class="empty">Aucun résultat.</div>`;
      return;
    }
    list.innerHTML = filtered
      .map(
        (a) => `
          <div class="entity-row ${a.entity_id === this._automationSelected ? "selected" : ""}" data-entity-id="${escapeHtml(a.entity_id)}">
            <div class="ename">${escapeHtml(a.name)}</div>
            <div class="eid">${escapeHtml(a.entity_id)}</div>
          </div>`
      )
      .join("");
    list.querySelectorAll(".entity-row").forEach((row) => {
      row.addEventListener("click", () => {
        this._loadAutomationGraph(row.getAttribute("data-entity-id"));
        this._renderAutomationList(automations);
      });
    });
  }

  _maybeRenderGraphSection() {
    if (this._automationSelected && this._automationGraph) {
      this._renderGraphSection();
    }
  }

  async _loadAutomationGraph(entityId) {
    this._automationSelected = entityId;
    this._automationGraph = null;
    this._simulationResult = null;
    this._stateOverrides = {};
    const section = this.shadowRoot.querySelector("#graph-section");
    if (section) section.innerHTML = `<div class="card"><div class="empty">Chargement…</div></div>`;

    try {
      const result = await this._hass.callWS({ type: "alex_entitymap_studio/get_automation_graph", entity_id: entityId });
      if (this._automationSelected !== entityId) return; // selection changee entre-temps
      this._automationGraph = result;
      this._nodePositions = layoutGraph(result.nodes, result.edges, result.trigger_ids);
      this._graphPan = { x: 0, y: 0 };
      this._graphZoom = 1;
      this._selectedTriggerId = result.trigger_ids[0] || null;
      this._renderGraphSection();
    } catch (err) {
      if (this._automationSelected !== entityId) return;
      const message = (err && err.message) || String(err);
      if (section) {
        section.innerHTML = `<div class="card"><div class="error">Impossible de construire le graphe : ${escapeHtml(message)}</div></div>`;
      }
    }
  }

  _renderGraphSection() {
    const section = this.shadowRoot.querySelector("#graph-section");
    if (!section || !this._automationGraph) return;
    const g = this._automationGraph;

    section.innerHTML = `
      <div class="card">
        <h2>Graphe</h2>
        <div id="graph-wrap">
          <svg id="automation-graph-svg" viewBox="0 0 900 840" width="100%" height="840"></svg>
        </div>
        <div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-size:11px;color:var(--secondary-text-color);">
          <span>▶ Déclencheur</span>
          <span>◆ Condition</span>
          <span>✦ Choix</span>
          <span>● Action</span>
          <span>■ Arrêt</span>
          <span>○ Autre</span>
          <span style="opacity:.7;">┄ Incertain (voir Simuler)</span>
        </div>
        <div class="hint">Molette pour zoomer, glisser le fond pour déplacer la vue, glisser un nœud pour le repositionner.</div>
      </div>

      <div class="card">
        <h2>Simuler l'exécution</h2>
        <div class="row" style="display:flex;gap:8px;align-items:center;margin-bottom:10px;">
          <label style="flex:0 0 100px;">Déclencheur</label>
          <select id="trigger-select" style="flex:1;">
            ${g.trigger_ids
              .map((tid) => {
                const n = g.nodes.find((x) => x.id === tid);
                return `<option value="${tid}" ${tid === this._selectedTriggerId ? "selected" : ""}>${escapeHtml(n ? n.label : tid)}</option>`;
              })
              .join("")}
          </select>
        </div>
        <div id="overrides-form"></div>
        <button class="btn btn-outline" id="simulate-btn" style="margin-top:8px;">Simuler</button>
        <div id="simulation-result" style="margin-top:12px;"></div>
      </div>
    `;

    this.shadowRoot.querySelector("#trigger-select").addEventListener("change", (ev) => {
      this._selectedTriggerId = ev.target.value;
    });
    this.shadowRoot.querySelector("#simulate-btn").addEventListener("click", () => this._runSimulation());

    this._renderOverridesForm();
    this._renderGraphSvg();
    this._wireGraphInteractions();
  }

  _renderOverridesForm() {
    const form = this.shadowRoot.querySelector("#overrides-form");
    if (!form || !this._automationGraph) return;
    const entities = this._automationGraph.condition_entities || [];
    if (!entities.length) {
      form.innerHTML = `<div class="hint">Aucune condition dans cette automatisation/ce script — rien à forcer.</div>`;
      return;
    }
    form.innerHTML = `
      <div class="hint" style="margin-bottom:6px;">États forcés pour la simulation (laisse vide pour utiliser l'état réel actuel) :</div>
      ${entities
        .map((eid) => {
          const st = this._hass.states[eid];
          const real = st ? st.state : "?";
          const displayName = (st && st.attributes && st.attributes.friendly_name) || eid;
          const forced = this._stateOverrides[eid] || "";
          return `
            <div class="row" style="display:flex;gap:8px;align-items:center;margin-bottom:6px;">
              <label style="flex:0 0 200px;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(eid)}">${escapeHtml(displayName)}</label>
              <input type="text" class="override-input" data-entity-id="${escapeHtml(eid)}" placeholder="réel : ${escapeHtml(real)}" value="${escapeHtml(forced)}" style="flex:1;" />
            </div>`;
        })
        .join("")}
        .join("")}
    `;
    form.querySelectorAll(".override-input").forEach((input) => {
      input.addEventListener("input", (ev) => {
        const eid = ev.target.getAttribute("data-entity-id");
        if (ev.target.value.trim()) {
          this._stateOverrides[eid] = ev.target.value.trim();
        } else {
          delete this._stateOverrides[eid];
        }
      });
    });
  }

  _renderGraphSvg() {
    const svg = this.shadowRoot.querySelector("#automation-graph-svg");
    if (!svg || !this._automationGraph) return;
    const g = this._automationGraph;
    const sim = this._simulationResult;
    const visitedSet = new Set(sim ? sim.visited_node_ids : []);
    const uncertainSet = new Set(sim ? sim.uncertain_node_ids || [] : []);
    const takenSet = new Set(sim ? sim.taken_edges.map((e) => `${e.source}|${e.target}`) : []);
    const uncertainEdgeSet = new Set(sim ? (sim.uncertain_edges || []).map((e) => `${e.source}|${e.target}`) : []);

    const edgesHtml = g.edges
      .map((e) => {
        const p1 = this._nodePositions[e.source] || { x: 0, y: 0 };
        const p2 = this._nodePositions[e.target] || { x: 0, y: 0 };
        const x1 = p1.x + NODE_W / 2;
        const y1 = p1.y + NODE_H;
        const x2 = p2.x + NODE_W / 2;
        const y2 = p2.y;
        const midX = (x1 + x2) / 2;
        const midY = (y1 + y2) / 2;
        const isTaken = takenSet.has(`${e.source}|${e.target}`);
        const isUncertainEdge = uncertainEdgeSet.has(`${e.source}|${e.target}`);
        const shortLabel = e.label ? (e.label.length > 16 ? e.label.slice(0, 15) + "…" : e.label) : "";
        return `
          <g>
            <path class="graph-edge ${isTaken ? "taken" : ""} ${isUncertainEdge ? "uncertain" : ""}" d="M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}" marker-end="url(#arrow)" />
            ${
              shortLabel
                ? `<rect x="${midX - shortLabel.length * 3.2 - 6}" y="${midY - 9}" width="${shortLabel.length * 6.4 + 12}" height="16" fill="rgba(0,0,0,0.65)" rx="4" />
                   <text class="graph-edge-label" x="${midX}" y="${midY + 3}" text-anchor="middle">${escapeHtml(shortLabel)}</text>`
                : ""
            }
          </g>`;
      })
      .join("");

    const nodesHtml = g.nodes
      .map((n) => {
        const p = this._nodePositions[n.id] || { x: 0, y: 0 };
        const color = NODE_KIND_COLORS[n.kind] || "#cbd5e1";
        const icon = NODE_KIND_ICONS[n.kind] || "○";
        const isVisited = visitedSet.has(n.id);
        const isUncertain = uncertainSet.has(n.id);
        const dimmed = sim && !isVisited && !isUncertain;
        const label = n.label.length > 30 ? n.label.slice(0, 29) + "…" : n.label;
        const stateClass = isVisited ? "visited" : isUncertain ? "uncertain" : "";
        return `
          <g class="graph-node ${stateClass}" data-node-id="${n.id}" transform="translate(${p.x},${p.y})">
            <rect width="${NODE_W}" height="${NODE_H}" rx="10" fill="${color}" opacity="${dimmed ? 0.3 : 1}" />
            <text class="node-icon" x="16" y="${NODE_H / 2 + 5}" text-anchor="middle">${icon}</text>
            <text class="node-label" x="34" y="${NODE_H / 2 + 4}">${escapeHtml(label)}</text>
          </g>`;
      })
      .join("");

    svg.innerHTML = `
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 Z" fill="rgba(255,255,255,.45)" />
        </marker>
      </defs>
      <g id="graph-viewport" transform="translate(${this._graphPan.x},${this._graphPan.y}) scale(${this._graphZoom})">
        ${edgesHtml}
        ${nodesHtml}
      </g>
    `;

    svg.querySelectorAll(".graph-node").forEach((elNode) => {
      elNode.addEventListener("pointerdown", (ev) => this._onNodePointerDown(ev, elNode.getAttribute("data-node-id")));
    });
  }

  _wireGraphInteractions() {
    const svg = this.shadowRoot.querySelector("#automation-graph-svg");
    if (!svg) return;

    svg.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const factor = ev.deltaY > 0 ? 0.9 : 1.1;
      this._graphZoom = Math.max(0.3, Math.min(3, this._graphZoom * factor));
      this._renderGraphSvg();
    });

    svg.addEventListener("pointerdown", (ev) => {
      if (ev.target.closest(".graph-node")) return; // gere par _onNodePointerDown
      this._graphPanning = true;
      this._panStart = { x: ev.clientX, y: ev.clientY, panX: this._graphPan.x, panY: this._graphPan.y };
      svg.classList.add("panning");
    });

    svg.addEventListener("pointermove", (ev) => {
      if (this._graphDragNode) {
        this._nodeDragMoved = true;
        const p = this._svgPointFromEvent(svg, ev);
        this._nodePositions[this._graphDragNode] = { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 };
        this._renderGraphSvg();
        return;
      }
      if (this._graphPanning) {
        this._graphPan = {
          x: this._panStart.panX + (ev.clientX - this._panStart.x),
          y: this._panStart.panY + (ev.clientY - this._panStart.y),
        };
        this._renderGraphSvg();
      }
    });

    const endInteraction = () => {
      // Un glissement de noeud qui n'a JAMAIS bouge est en realite un
      // simple clic -- affiche le detail complet plutot que de le traiter
      // comme un repositionnement (meme garde-fou "juste glisse" que dans
      // Alex Scene Studio, pour ne pas confondre les deux gestes).
      if (this._graphDragNode && !this._nodeDragMoved) {
        this._showNodeDetail(this._graphDragNode);
      }
      this._graphPanning = false;
      this._graphDragNode = null;
      this._nodeDragMoved = false;
      svg.classList.remove("panning");
    };
    svg.addEventListener("pointerup", endInteraction);
    svg.addEventListener("pointerleave", endInteraction);
  }

  _onNodePointerDown(ev, nodeId) {
    ev.stopPropagation();
    this._graphDragNode = nodeId;
    this._nodeDragMoved = false;
  }

  // Affiche le contenu COMPLET d'un noeud (le libelle est tronque a
  // l'affichage dans le graphe pour rester lisible) -- panneau simple en
  // survol, pas une vraie boite de dialogue modale.
  _showNodeDetail(nodeId) {
    if (!this._automationGraph) return;
    const node = this._automationGraph.nodes.find((n) => n.id === nodeId);
    if (!node) return;
    const existing = this.shadowRoot.querySelector("#node-detail-overlay");
    if (existing) existing.remove();

    const overlay = document.createElement("div");
    overlay.id = "node-detail-overlay";
    overlay.className = "node-detail-overlay";
    overlay.innerHTML = `
      <div class="node-detail-box">
        <div class="node-detail-header">
          <span>${NODE_KIND_ICONS[node.kind] || "○"} ${escapeHtml(node.kind)}</span>
          <span class="node-detail-close" title="Fermer">✕</span>
        </div>
        <div class="node-detail-body">${escapeHtml(node.label)}</div>
      </div>
    `;
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay || ev.target.classList.contains("node-detail-close")) {
        overlay.remove();
      }
    });
    this.shadowRoot.querySelector("#graph-wrap").appendChild(overlay);
  }

  // Conversion coordonnees ecran -> espace "logique" du graphe : passe par
  // l'espace utilisateur du SVG (avant transform), puis soustrait le pan et
  // divise par le zoom deja appliques au groupe <g id="graph-viewport">,
  // pour retomber dans le meme repere que _nodePositions.
  _svgPointFromEvent(svg, ev) {
    const pt = svg.createSVGPoint();
    pt.x = ev.clientX;
    pt.y = ev.clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return { x: 0, y: 0 };
    const svgPoint = pt.matrixTransform(ctm.inverse());
    return {
      x: (svgPoint.x - this._graphPan.x) / this._graphZoom,
      y: (svgPoint.y - this._graphPan.y) / this._graphZoom,
    };
  }

  async _runSimulation() {
    if (!this._automationGraph || !this._selectedTriggerId) return;
    const btn = this.shadowRoot.querySelector("#simulate-btn");
    const resultEl = this.shadowRoot.querySelector("#simulation-result");
    if (btn) btn.textContent = "Simulation en cours…";
    try {
      const result = await this._hass.callWS({
        type: "alex_entitymap_studio/simulate_automation",
        entity_id: this._automationSelected,
        trigger_id: this._selectedTriggerId,
        overrides: { ...this._stateOverrides },
      });
      this._simulationResult = result;
      this._renderGraphSvg();
      if (resultEl) {
        const reasonLabels = {
          condition_false: "Simulation arrêtée : une condition n'est pas remplie.",
          undetermined: "Simulation interrompue : impossible de déterminer une condition (état inconnu, ou construction non prise en charge dans cette version — templates Jinja bruts, repeat/parallel/wait_for_trigger).",
          end_of_branch: "Simulation terminée normalement, en fin de séquence.",
        };
        const reasonText = reasonLabels[result.stopped_reason] || "Simulation terminée.";
        const uncertainNote =
          result.uncertain_node_ids && result.uncertain_node_ids.length
            ? `<div class="hint" style="margin-top:4px;">Les nœuds en pointillés (┄) n'ont pas pu être confirmés individuellement (condition non déterminée), mais la simulation a continué au-delà car les deux issues possibles se rejoignaient au même endroit.</div>`
            : "";
        resultEl.innerHTML = `<div class="hint">${escapeHtml(reasonText)}</div>${uncertainNote}`;
      }
    } catch (err) {
      if (resultEl) {
        resultEl.innerHTML = `<div class="error">Échec de la simulation : ${escapeHtml((err && err.message) || String(err))}</div>`;
      }
    } finally {
      if (btn) btn.textContent = "Simuler";
    }
  }
}

customElements.define("alex-entitymap-studio-panel", AlexEntityMapStudioPanel);
