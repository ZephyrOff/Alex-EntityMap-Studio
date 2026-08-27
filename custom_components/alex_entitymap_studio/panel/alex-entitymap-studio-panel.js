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
          border-radius: 16px; padding: 20px; margin-bottom: 18px;
        }
        .card h2 { margin: 0 0 12px; font-size: 15px; font-weight: 600; }
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
        <button class="refresh-btn" id="refresh-btn">Actualiser</button>
      </div>

      <div class="layout">
        <div class="sidebar">
          <div class="filters">
            <select id="domain-filter">
              <option value="">Tous les domaines</option>
            </select>
            <input type="text" id="text-filter" placeholder="Rechercher..." />
          </div>
          <div id="entity-list"></div>
        </div>
        <div class="content" id="content"></div>
      </div>
    `;

    this.shadowRoot.querySelector("#menu-btn").addEventListener("click", () => {
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    });
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
}

customElements.define("alex-entitymap-studio-panel", AlexEntityMapStudioPanel);
