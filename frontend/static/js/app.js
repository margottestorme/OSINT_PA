/**
 * Logique de l'interface — OSINT Platform
 */

const PAGE_SIZE = 10;

document.addEventListener("DOMContentLoaded", () => {
    const loginForm = document.getElementById("login-form");
    const logoutBtn = document.getElementById("logout-btn");

    if (loginForm) {
        loginForm.addEventListener("submit", handleLogin);
        return;
    }

    // Pages protégées
    if (!API.isAuthenticated()) {
        window.location.href = "/";
        return;
    }
    if (logoutBtn) logoutBtn.addEventListener("click", () => API.logout());

    if (document.getElementById("dropzone")) initDashboard();
    if (document.getElementById("report-content")) initReport();
    if (document.getElementById("sources-container")) initResults();
    if (document.getElementById("global-graph")) initGraph();
});


// =====================================================
// Login
// =====================================================

async function handleLogin(e) {
    e.preventDefault();
    const errorEl = document.getElementById("login-error");
    const btn = document.getElementById("login-btn");
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;

    errorEl.classList.add("hidden");
    btn.disabled = true;
    btn.textContent = "Connexion...";

    try {
        await API.login(username, password);
        window.location.href = "/dashboard";
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    } finally {
        btn.disabled = false;
        btn.textContent = "Se connecter";
    }
}


// =====================================================
// Dashboard
// =====================================================

let personsOffset = 0;
let historyOffset = 0;
let personsCache = {};

function initDashboard() {
    // Nom de l'utilisateur (décodé du JWT)
    const user = currentUser();
    const info = document.getElementById("user-info");
    if (info) info.textContent = user;
    const avatar = document.getElementById("user-avatar");
    if (avatar) avatar.textContent = user.slice(0, 2).toUpperCase();

    initUpload();
    document.getElementById("search-form").addEventListener("submit", handleSearch);
    document.getElementById("history-refresh").addEventListener("click", () => loadHistory(0));
    document.getElementById("refresh-all").addEventListener("click", refreshAll);
    initPagination("persons", loadPersons, () => personsOffset);
    initPagination("history", loadHistory, () => historyOffset);
    initEditModal();
    document.getElementById("persons-body").addEventListener("click", onPersonsClick);

    refreshAll();

    // Pré-remplissage + lancement auto depuis une entité cliquée (results.html → ?q=&type=)
    const params = new URLSearchParams(window.location.search);
    const q = params.get("q");
    if (q) {
        document.getElementById("search-query").value = q;
        const type = params.get("type");
        if (type) document.getElementById("search-type").value = type;
        document.getElementById("search-section").scrollIntoView({ behavior: "smooth" });
        document.getElementById("search-form").requestSubmit();
    }
}

function refreshAll() {
    loadStats();
    loadPersons(0);
    loadHistory(0);
}


// --- KPI ---
async function loadStats() {
    try {
        const s = await API.getStats();
        document.getElementById("kpi-searches").textContent = s.total_searches;
        document.getElementById("kpi-persons").textContent = s.total_persons;
        document.getElementById("kpi-correlations").textContent = s.total_correlations;
        document.getElementById("kpi-activity").textContent =
            s.last_activity ? formatDate(s.last_activity) : "—";
    } catch (err) {
        toast("Impossible de charger les statistiques", "error");
    }
}


// --- Upload (drag & drop) ---
function initUpload() {
    const dz = document.getElementById("dropzone");
    const input = document.getElementById("face-file");

    dz.addEventListener("click", () => input.click());
    dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
    dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
    dz.addEventListener("drop", (e) => {
        e.preventDefault();
        dz.classList.remove("dragover");
        if (e.dataTransfer.files[0]) doUpload(e.dataTransfer.files[0]);
    });
    input.addEventListener("change", () => { if (input.files[0]) doUpload(input.files[0]); });
}

async function doUpload(file) {
    const errorEl = document.getElementById("upload-error");
    const resultEl = document.getElementById("upload-result");
    const dzText = document.getElementById("dropzone-text");
    errorEl.classList.add("hidden");
    resultEl.classList.add("hidden");

    if (!["image/jpeg", "image/png"].includes(file.type)) {
        errorEl.textContent = "Format non supporté. Utilisez JPG ou PNG.";
        errorEl.classList.remove("hidden");
        return;
    }
    if (file.size > 5 * 1024 * 1024) {
        errorEl.textContent = "Fichier trop volumineux (max 5 Mo).";
        errorEl.classList.remove("hidden");
        return;
    }

    dzText.textContent = "Analyse en cours...";
    try {
        const data = await API.uploadFace(file);
        const kind = data.is_new ? "Nouvelle personne créée" : "Personne reconnue";
        resultEl.innerHTML = `<strong>${kind}</strong> — ID ${data.person_id}, ` +
            `confiance ${(data.confidence_score * 100).toFixed(1)}%, ${data.faces_count} visage(s)`;
        resultEl.classList.remove("hidden");
        toast(kind, "success");
        loadPersons(0);
        loadStats();
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    } finally {
        dzText.textContent = "Deposez une image ici";
        document.getElementById("face-file").value = "";
    }
}


// --- Recherche OSINT avec polling ---
async function handleSearch(e) {
    e.preventDefault();
    const btn = document.getElementById("search-btn");
    const label = document.getElementById("search-btn-label");
    const errorEl = document.getElementById("search-error");
    const statusEl = document.getElementById("search-status");
    const query = document.getElementById("search-query").value.trim();
    const queryType = document.getElementById("search-type").value;

    errorEl.classList.add("hidden");
    if (!query) return;

    // id de la dernière recherche connue (pour détecter la nouvelle)
    let baseline = 0;
    try {
        const h = await API.listHistory(1, 0);
        baseline = h.length ? h[0].id : 0;
    } catch { /* ignore */ }

    btn.disabled = true;
    label.innerHTML = '<span class="spinner"></span> Lancement...';
    try {
        await API.search(query, queryType);
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
        btn.disabled = false;
        label.innerHTML = '<i data-lucide="search"></i> Rechercher';
        lucide.createIcons();
        return;
    }

    statusEl.classList.remove("hidden");
    pollSearch(baseline, query, () => {
        btn.disabled = false;
        label.innerHTML = '<i data-lucide="search"></i> Rechercher';
        lucide.createIcons();
        statusEl.classList.add("hidden");
    });
    document.getElementById("search-query").value = "";
}

function pollSearch(baseline, query, done) {
    let elapsed = 0;
    const interval = setInterval(async () => {
        elapsed += 2;
        try {
            const h = await API.listHistory(1, 0);
            if (h.length && h[0].id > baseline) {
                clearInterval(interval);
                done();
                toast(`Recherche "${query}" terminée — ${h[0].result_count} résultat(s)`, "success");
                loadHistory(0);
                loadStats();
                return;
            }
        } catch { /* ignore, on retente */ }
        if (elapsed >= 120) {
            clearInterval(interval);
            done();
            toast("La recherche prend plus de temps que prévu…", "info");
        }
    }, 2000);
}


// --- Personnes ---
async function loadPersons(offset) {
    personsOffset = offset;
    const loading = document.getElementById("persons-loading");
    const table = document.getElementById("persons-table");
    const empty = document.getElementById("persons-empty");
    const errorEl = document.getElementById("persons-error");
    const body = document.getElementById("persons-body");

    loading.classList.remove("hidden");
    table.classList.add("hidden");
    empty.classList.add("hidden");
    errorEl.classList.add("hidden");

    try {
        const data = await API.listFacePersons(PAGE_SIZE, offset);
        personsCache = {};
        if (data.length === 0 && offset === 0) {
            empty.classList.remove("hidden");
        } else {
            body.innerHTML = "";
            for (const p of data) {
                personsCache[p.id] = p;
                const tr = document.createElement("tr");
                tr.innerHTML =
                    `<td class="mono">#${p.id}</td>` +
                    `<td>${escapeHtml(p.first_name)}</td>` +
                    `<td>${escapeHtml(p.last_name)}</td>` +
                    `<td>${(p.confidence_score * 100).toFixed(0)}%</td>` +
                    `<td class="text-muted">${formatDate(p.created_at)}</td>` +
                    `<td><div class="flex gap-8">` +
                    `<button class="btn btn-ghost btn-sm" data-edit="${p.id}"><i data-lucide="edit-3"></i></button>` +
                    `<button class="btn btn-ghost btn-sm" data-report="${p.id}"><i data-lucide="file-text"></i> Rapport</button>` +
                    `</div></td>`;
                body.appendChild(tr);
            }
            table.classList.remove("hidden");
            lucide.createIcons();
        }
        updatePagination("persons", offset, data.length);
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    } finally {
        loading.classList.add("hidden");
    }
}

function onPersonsClick(e) {
    const editBtn = e.target.closest("[data-edit]");
    const reportBtn = e.target.closest("[data-report]");
    if (editBtn) openEditModal(personsCache[editBtn.getAttribute("data-edit")], () => loadPersons(personsOffset));
    else if (reportBtn) window.location.href = "/report?id=" + reportBtn.getAttribute("data-report");
}


// --- Modal édition personne (réutilisable via callback onSaved) ---
let _editOnSaved = null;

function initEditModal() {
    document.getElementById("edit-cancel").addEventListener("click", closeEditModal);
    document.getElementById("edit-save").addEventListener("click", saveEdit);
    document.getElementById("edit-modal").addEventListener("click", (e) => {
        if (e.target.id === "edit-modal") closeEditModal();
    });
}

function openEditModal(person, onSaved) {
    if (!person) return;
    _editOnSaved = onSaved || null;
    document.getElementById("edit-person-id").value = person.id;
    document.getElementById("edit-first-name").value = person.first_name;
    document.getElementById("edit-last-name").value = person.last_name;
    document.getElementById("edit-notes").value = person.notes || "";
    document.getElementById("edit-error").classList.add("hidden");
    document.getElementById("edit-modal").classList.remove("hidden");
}

function closeEditModal() {
    document.getElementById("edit-modal").classList.add("hidden");
}

async function saveEdit() {
    const id = document.getElementById("edit-person-id").value;
    const errorEl = document.getElementById("edit-error");
    const body = {
        first_name: document.getElementById("edit-first-name").value.trim(),
        last_name: document.getElementById("edit-last-name").value.trim(),
        notes: document.getElementById("edit-notes").value.trim(),
    };
    try {
        await API.updatePerson(id, body);
        closeEditModal();
        toast("Personne mise à jour", "success");
        if (_editOnSaved) _editOnSaved();
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    }
}


// --- Historique ---
async function loadHistory(offset) {
    historyOffset = offset;
    const loading = document.getElementById("history-loading");
    const table = document.getElementById("history-table");
    const empty = document.getElementById("history-empty");
    const errorEl = document.getElementById("history-error");
    const body = document.getElementById("history-body");

    loading.classList.remove("hidden");
    table.classList.add("hidden");
    empty.classList.add("hidden");
    errorEl.classList.add("hidden");

    try {
        const data = await API.listHistory(PAGE_SIZE, offset);
        if (data.length === 0 && offset === 0) {
            empty.classList.remove("hidden");
        } else {
            body.innerHTML = "";
            for (const h of data) {
                const tr = document.createElement("tr");
                const viewBtn = h.result_count > 0
                    ? `<a class="btn btn-ghost btn-sm" href="/results?id=${h.id}"><i data-lucide="eye"></i> Voir résultats</a>`
                    : `<span class="text-muted">—</span>`;
                tr.innerHTML =
                    `<td>${escapeHtml(h.query)}</td>` +
                    `<td><span class="badge">${escapeHtml(h.query_type)}</span></td>` +
                    `<td><span class="status-badge done"><i data-lucide="check" style="width:13px;height:13px;"></i> Terminé</span></td>` +
                    `<td>${h.result_count}</td>` +
                    `<td class="text-muted">${formatDate(h.created_at)}</td>` +
                    `<td>${viewBtn}</td>`;
                body.appendChild(tr);
            }
            table.classList.remove("hidden");
            lucide.createIcons();
        }
        updatePagination("history", offset, data.length);
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    } finally {
        loading.classList.add("hidden");
    }
}


// --- Pagination ---
function initPagination(prefix, loadFn, getOffset) {
    document.getElementById(prefix + "-prev").addEventListener("click", () => {
        loadFn(Math.max(0, getOffset() - PAGE_SIZE));
    });
    document.getElementById(prefix + "-next").addEventListener("click", () => {
        loadFn(getOffset() + PAGE_SIZE);
    });
}

function updatePagination(prefix, offset, count) {
    const prev = document.getElementById(prefix + "-prev");
    const next = document.getElementById(prefix + "-next");
    const info = document.getElementById(prefix + "-page-info");
    prev.disabled = offset === 0;
    next.disabled = count < PAGE_SIZE;
    info.textContent = "Page " + (Math.floor(offset / PAGE_SIZE) + 1);
}


// =====================================================
// Page rapport personne (report.html)
// =====================================================

const AVATAR_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#ef4444", "#0ea5e9", "#14b8a6"];
let reportPersonId = null;
let reportPerson = null;
let reportExport = null;

function initReport() {
    const user = currentUser();
    document.getElementById("user-info").textContent = user;
    document.getElementById("user-avatar").textContent = user.slice(0, 2).toUpperCase();

    initEditModal();
    initReportTabs();
    document.getElementById("person-edit").addEventListener("click", () => {
        if (reportPerson) openEditModal(reportPerson, () => loadReport(reportPersonId));
    });

    const params = new URLSearchParams(window.location.search);
    const id = params.get("id");
    if (!id || isNaN(Number(id))) {
        showReportError("Identifiant de personne manquant ou invalide.");
        return;
    }
    reportPersonId = Number(id);
    loadReport(reportPersonId);
}

function initReportTabs() {
    document.getElementById("report-tabs").addEventListener("click", (e) => {
        const tab = e.target.closest(".tab");
        if (!tab) return;
        const name = tab.getAttribute("data-tab");
        document.querySelectorAll("#report-tabs .tab").forEach((t) => t.classList.toggle("active", t === tab));
        document.querySelectorAll(".tab-panel").forEach((p) =>
            p.classList.toggle("active", p.id === "panel-" + name));
    });
}

async function loadReport(personId) {
    const loading = document.getElementById("report-loading");
    const content = document.getElementById("report-content");
    const errorEl = document.getElementById("report-error");
    loading.classList.remove("hidden");
    content.classList.add("hidden");
    errorEl.classList.add("hidden");

    try {
        const data = await API.getPersonReport(personId);
        reportPerson = data.person;
        reportExport = data;

        renderPersonHeader(data.person);
        renderPersonGraph(data);
        renderReportTable("emails", data.emails, (e) =>
            `<td>${escapeHtml(e.email)}</td><td><span class="badge">${escapeHtml(e.source)}</span></td><td class="text-muted">${formatDate(e.created_at)}</td>`);
        renderReportTable("sites", data.sites, (s) =>
            `<td>${escapeHtml(s.url)}</td><td>${escapeHtml(s.server || "—")}</td><td class="text-muted">${formatDate(s.discovered_at)}</td>`);
        renderReportTable("correlations", data.correlations, (c) => {
            const ips = (c.data && c.data.ips) ? c.data.ips.join(", ") : "—";
            return `<td>${c.email_id || "—"}</td><td>${c.site_id || "—"}</td>` +
                   `<td>${(c.score * 100).toFixed(0)}%</td><td class="mono">${escapeHtml(ips)}</td>` +
                   `<td class="text-muted">${formatDate(c.created_at)}</td>`;
        });
        document.getElementById("count-emails").textContent = data.emails.length;
        document.getElementById("count-sites").textContent = data.sites.length;
        document.getElementById("count-correlations").textContent = data.correlations.length;
        renderActivityTimeline(data);

        const exportBtn = document.getElementById("export-btn");
        exportBtn.disabled = false;
        exportBtn.addEventListener("click", () => downloadJson(`osint-person-${data.person.id}.json`, data));

        content.classList.remove("hidden");
        lucide.createIcons();
    } catch (err) {
        showReportError(err.message);
    } finally {
        loading.classList.add("hidden");
    }
}

function renderPersonHeader(p) {
    const seed = (p.first_name || "") + (p.last_name || "");
    let h = 0;
    for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    const avatar = document.getElementById("person-avatar");
    avatar.textContent = ((p.first_name[0] || "") + (p.last_name[0] || "")).toUpperCase() || "?";
    avatar.style.background = AVATAR_COLORS[h % AVATAR_COLORS.length];

    document.getElementById("person-name").textContent = `${p.first_name} ${p.last_name}`;
    document.getElementById("person-score").textContent = `${(p.confidence_score * 100).toFixed(0)}% confiance`;
    document.getElementById("person-id").textContent = `#${p.id}`;
    document.getElementById("person-date").textContent = formatDate(p.created_at);

    const notes = document.getElementById("person-notes");
    if (p.notes) {
        notes.textContent = p.notes;
        notes.classList.remove("hidden");
    } else {
        notes.classList.add("hidden");
    }
}

function renderPersonGraph(data) {
    const container = document.getElementById("person-graph");
    const empty = document.getElementById("graph-empty");

    if (data.emails.length === 0 && data.sites.length === 0) {
        container.classList.add("hidden");
        empty.classList.remove("hidden");
        return;
    }
    container.classList.remove("hidden");
    empty.classList.add("hidden");

    const nodes = [{
        id: "person", label: `${data.person.first_name}\n${data.person.last_name}`,
        shape: "circle", size: 26,
        color: { background: "#3b82f6", border: "#2563eb" }, font: { color: "#fff", size: 14 },
    }];
    const edges = [];

    for (const e of data.emails) {
        nodes.push({ id: "e" + e.id, label: e.email, shape: "box",
            color: { background: "#dbeafe", border: "#93c5fd" }, font: { color: "#1e40af" } });
        edges.push({ from: "person", to: "e" + e.id });
    }
    for (const s of data.sites) {
        nodes.push({ id: "s" + s.id, label: s.url.replace(/^https?:\/\//, ""), shape: "box",
            color: { background: "#d1fae5", border: "#6ee7b7" }, font: { color: "#065f46" } });
        edges.push({ from: "person", to: "s" + s.id });
    }
    for (const c of data.correlations) {
        const target = c.email_id ? "e" + c.email_id : (c.site_id ? "s" + c.site_id : null);
        if (!target) continue;
        const edge = edges.find((x) => x.to === target);
        if (edge) edge.label = `${(c.score * 100).toFixed(0)}%`;
    }

    new vis.Network(container, { nodes, edges }, {
        nodes: { borderWidth: 1.5, font: { size: 12 } },
        edges: { color: { color: "#cbd5e1" }, font: { size: 10, align: "middle", color: "#64748b" }, smooth: true },
        physics: { barnesHut: { gravitationalConstant: -4000, springLength: 130 }, stabilization: { iterations: 150 } },
        interaction: { hover: true },
    });
}

function renderActivityTimeline(data) {
    const events = [{ type: "corr", cls: "", title: "Personne créée", date: data.person.created_at }];
    for (const e of data.emails) events.push({ cls: "email", title: `Email découvert : ${e.email}`, date: e.created_at });
    for (const s of data.sites) events.push({ cls: "site", title: `Site découvert : ${s.url}`, date: s.discovered_at });
    for (const c of data.correlations) events.push({ cls: "corr", title: `Corrélation (score ${(c.score * 100).toFixed(0)}%)`, date: c.created_at });
    events.sort((a, b) => new Date(b.date) - new Date(a.date));

    document.getElementById("activity-timeline").innerHTML = events.map((ev) =>
        `<div class="timeline-item ${ev.cls}">
            <div class="tl-title">${escapeHtml(ev.title)}</div>
            <div class="tl-date">${formatDate(ev.date)}</div>
        </div>`).join("");
}

function renderReportTable(prefix, items, rowFn) {
    const body = document.getElementById(prefix + "-body");
    const empty = document.getElementById(prefix + "-empty");
    if (items.length === 0) {
        body.innerHTML = "";
        empty.classList.remove("hidden");
        return;
    }
    empty.classList.add("hidden");
    body.innerHTML = items.map((item) => `<tr>${rowFn(item)}</tr>`).join("");
}

function showReportError(message) {
    const errorEl = document.getElementById("report-error");
    errorEl.textContent = message;
    errorEl.classList.remove("hidden");
    document.getElementById("report-loading").classList.add("hidden");
}


// =====================================================
// Page résultats de recherche (results.html)
// =====================================================

const SOURCE_ICONS = {
    dns: "server", dns_mx: "server", whois: "globe", rdap: "database",
    shodan_internetdb: "shield", wikipedia: "book-open", github: "code",
    "crt.sh": "lock", gravatar: "user", duckduckgo: "search",
    virustotal: "shield-alert", otx: "shield", hunter: "mail",
};
const SOURCE_LABELS = {
    dns: "DNS", dns_mx: "DNS MX", whois: "WHOIS", rdap: "RDAP",
    shodan_internetdb: "Shodan InternetDB", wikipedia: "Wikipedia", github: "GitHub",
    "crt.sh": "crt.sh", gravatar: "Gravatar", duckduckgo: "DuckDuckGo",
    virustotal: "VirusTotal", otx: "AlienVault OTX", hunter: "Hunter.io",
};
// type d'entité → { classe CSS, type de recherche si cliquable (sinon null) }
const ENTITY_TYPES = {
    emails: { cls: "email", search: "email", singular: "email" },
    domains: { cls: "domain", search: "domain", singular: "domaine" },
    names: { cls: "name", search: "name", singular: "nom" },
    ips: { cls: "ip", search: null, singular: "IP" },
    urls: { cls: "url", search: null, singular: "url" },
};

let resultsExport = null;

function initResults() {
    document.getElementById("logout-btn").addEventListener("click", () => API.logout());
    const user = currentUser();
    document.getElementById("user-info").textContent = user;
    document.getElementById("user-avatar").textContent = user.slice(0, 2).toUpperCase();

    const params = new URLSearchParams(window.location.search);
    const id = params.get("id");
    if (!id || isNaN(Number(id))) {
        showResultsError("Identifiant de recherche manquant ou invalide.");
        return;
    }
    document.getElementById("sources-container").addEventListener("click", onEntityClick);
    loadResults(Number(id));
}

async function loadResults(searchId) {
    const loading = document.getElementById("results-loading");
    const errorEl = document.getElementById("results-error");
    loading.classList.remove("hidden");
    errorEl.classList.add("hidden");

    try {
        const [search, results] = await Promise.all([
            API.getSearch(searchId),
            API.getSearchResults(searchId),
        ]);

        // Header
        document.getElementById("sh-query").textContent = search.query;
        document.getElementById("sh-type").textContent = search.query_type;
        document.getElementById("sh-date").textContent = formatDate(search.created_at);
        document.getElementById("sh-sources").textContent = results.length;
        const totalEntities = results.reduce((sum, r) => sum + countEntities(r.extracted), 0);
        document.getElementById("sh-entities").textContent = totalEntities;
        document.getElementById("search-header").classList.remove("hidden");

        // Export
        resultsExport = { search, results };
        const exportBtn = document.getElementById("export-btn");
        exportBtn.disabled = false;
        exportBtn.addEventListener("click", () => exportJson(search));

        // Cartes par source
        if (results.length === 0) {
            document.getElementById("results-empty").classList.remove("hidden");
        } else {
            const container = document.getElementById("sources-container");
            container.innerHTML = results.map(renderSourceCard).join("");
            lucide.createIcons();
        }
    } catch (err) {
        showResultsError(err.message);
    } finally {
        loading.classList.add("hidden");
    }
}

function countEntities(extracted) {
    if (!extracted) return 0;
    return Object.values(extracted).reduce((s, arr) => s + (Array.isArray(arr) ? arr.length : 0), 0);
}

function renderSourceCard(r) {
    const icon = SOURCE_ICONS[r.source] || "radar";
    const label = SOURCE_LABELS[r.source] || r.source;
    const extracted = r.extracted || {};

    let badges = "";
    for (const [key, meta] of Object.entries(ENTITY_TYPES)) {
        const values = extracted[key] || [];
        for (const v of values) {
            const clickable = meta.search ? "clickable" : "";
            const attrs = meta.search
                ? ` data-search="${escapeHtml(v)}" data-type="${meta.search}" title="Rechercher ${escapeHtml(v)}"`
                : "";
            badges += `<span class="badge ${meta.cls} ${clickable}"${attrs}>${escapeHtml(v)}</span>`;
        }
    }
    if (!badges) badges = '<span class="text-muted" style="font-size:0.85rem;">Aucune entité extraite (payload brut ci-dessous)</span>';

    const json = escapeHtml(JSON.stringify(r.data, null, 2));

    return `
    <div class="card">
        <h2><i data-lucide="${icon}"></i> ${escapeHtml(label)}
            <span class="text-muted" style="font-weight:400; font-size:0.85rem;">· ${escapeHtml(r.entity)}</span>
        </h2>
        <div class="badge-group" style="margin-bottom:14px;">${badges}</div>
        <details class="accordion">
            <summary><i data-lucide="code-2"></i> Payload brut (JSON)</summary>
            <div class="accordion-body"><pre class="json">${json}</pre></div>
        </details>
    </div>`;
}

function onEntityClick(e) {
    const badge = e.target.closest("[data-search]");
    if (!badge) return;
    const q = badge.getAttribute("data-search");
    const type = badge.getAttribute("data-type");
    window.location.href = "/dashboard?q=" + encodeURIComponent(q) + "&type=" + encodeURIComponent(type);
}

function exportJson(search) {
    if (!resultsExport) return;
    downloadJson(`osint-search-${search.id}-${search.query_type}.json`, resultsExport);
}

function showResultsError(message) {
    const errorEl = document.getElementById("results-error");
    errorEl.textContent = message;
    errorEl.classList.remove("hidden");
    document.getElementById("results-loading").classList.add("hidden");
}


// =====================================================
// Page graphe global (graph.html)
// =====================================================

let graphData = null;
let graphNetwork = null;
let graphMeta = {};

function initGraph() {
    document.getElementById("logout-btn").addEventListener("click", () => API.logout());
    const user = currentUser();
    document.getElementById("user-info").textContent = user;
    document.getElementById("user-avatar").textContent = user.slice(0, 2).toUpperCase();

    document.getElementById("graph-fit").addEventListener("click", () => graphNetwork && graphNetwork.fit({ animation: true }));
    document.getElementById("graph-refresh").addEventListener("click", loadGraph);
    document.getElementById("graph-filters").addEventListener("change", () => {
        if (graphData) buildAndRender();
    });

    loadGraph();
}

function currentFilters() {
    const filters = {};
    document.querySelectorAll("#graph-filters [data-filter]").forEach((cb) => {
        filters[cb.getAttribute("data-filter")] = cb.checked;
    });
    return filters;
}

async function loadGraph() {
    const loading = document.getElementById("graph-loading");
    const empty = document.getElementById("graph-empty");
    loading.classList.remove("hidden");
    empty.classList.add("hidden");

    try {
        const [persons, emails, sites, correlations, stats] = await Promise.all([
            API.listPersons(200, 0),
            API.listEmails(200, 0),
            API.listSites(200, 0),
            API.listCorrelations(200, 0),
            API.getStats(),
        ]);
        graphData = { persons, emails, sites, correlations };

        document.getElementById("g-persons").textContent = stats.total_persons;
        document.getElementById("g-emails").textContent = emails.length;
        document.getElementById("g-sites").textContent = sites.length;
        document.getElementById("g-correlations").textContent = stats.total_correlations;

        buildAndRender();
    } catch (err) {
        toast(err.message, "error");
    } finally {
        loading.classList.add("hidden");
    }
}

function buildAndRender() {
    const filters = currentFilters();
    const { persons, emails, sites, correlations } = graphData;
    const personsById = Object.fromEntries(persons.map((p) => [p.id, p]));
    const emailsById = Object.fromEntries(emails.map((e) => [e.id, e]));
    const sitesById = Object.fromEntries(sites.map((s) => [s.id, s]));

    const nodes = new Map();
    const edges = new Map();
    graphMeta = {};

    const addEdge = (a, b, opts = {}) => {
        if (!nodes.has(a) || !nodes.has(b)) return;
        const key = [a, b].sort().join("|");
        if (!edges.has(key)) edges.set(key, { from: a, to: b, ...opts });
        else if (opts.width) Object.assign(edges.get(key), opts);
    };

    if (filters.persons) {
        for (const p of persons) {
            const id = "p" + p.id;
            nodes.set(id, {
                id, label: initials(p.first_name, p.last_name), title: `${p.first_name} ${p.last_name}`,
                shape: "circle", size: 22, color: { background: "#3b82f6", border: "#2563eb" }, font: { color: "#fff" },
            });
            graphMeta[id] = { report: p.id };
        }
    }
    if (filters.emails) {
        for (const e of emails) {
            const id = "e" + e.id;
            nodes.set(id, { id, label: e.email, shape: "square",
                color: { background: "#dbeafe", border: "#93c5fd" }, font: { color: "#1e40af" } });
            graphMeta[id] = { searchValue: e.email, searchType: "email" };
            if (filters.persons && e.person_id != null) addEdge("p" + e.person_id, id);
        }
    }
    if (filters.sites) {
        for (const s of sites) {
            const id = "s" + s.id;
            const host = s.url.replace(/^https?:\/\//, "");
            nodes.set(id, { id, label: host, shape: "diamond",
                color: { background: "#d1fae5", border: "#6ee7b7" }, font: { color: "#065f46" } });
            graphMeta[id] = { searchValue: host, searchType: "domain" };
        }
    }

    for (const c of correlations) {
        const width = 1 + (c.score || 0) * 5;
        const label = `${((c.score || 0) * 100).toFixed(0)}%`;
        if (c.email_id != null) addEdge("p" + c.person_id, "e" + c.email_id, { width, label });
        if (c.site_id != null) addEdge("p" + c.person_id, "s" + c.site_id, { width, label });
        if (filters.ips && c.data && Array.isArray(c.data.ips)) {
            for (const ip of c.data.ips) {
                const id = "ip" + ip;
                if (!nodes.has(id)) {
                    nodes.set(id, { id, label: ip, shape: "triangle",
                        color: { background: "#fed7aa", border: "#fb923c" }, font: { color: "#9a3412" } });
                    graphMeta[id] = {};
                }
                addEdge("p" + c.person_id, id);
                if (c.site_id != null) addEdge("s" + c.site_id, id);
            }
        }
    }

    const emptyOverlay = document.getElementById("graph-empty");
    if (nodes.size === 0) {
        if (graphNetwork) { graphNetwork.destroy(); graphNetwork = null; }
        emptyOverlay.classList.remove("hidden");
        return;
    }
    emptyOverlay.classList.add("hidden");

    graphNetwork = new vis.Network(
        document.getElementById("global-graph"),
        { nodes: Array.from(nodes.values()), edges: Array.from(edges.values()) },
        {
            nodes: { borderWidth: 1.5, font: { size: 12 } },
            edges: { color: { color: "#cbd5e1", highlight: "#3b82f6" }, font: { size: 9, color: "#94a3b8", align: "middle" }, smooth: { type: "continuous" } },
            physics: { barnesHut: { gravitationalConstant: -8000, springLength: 150, springConstant: 0.04 }, stabilization: { iterations: 200 } },
            interaction: { hover: true, tooltipDelay: 200 },
        }
    );
    graphNetwork.on("click", onGraphClick);
}

function onGraphClick(params) {
    const id = params.nodes[0];
    if (!id) return;
    const meta = graphMeta[id];
    if (!meta) return;
    if (meta.report != null) {
        window.location.href = "/report?id=" + meta.report;
    } else if (meta.searchType) {
        window.location.href = "/dashboard?q=" + encodeURIComponent(meta.searchValue) + "&type=" + meta.searchType;
    }
}


// =====================================================
// Utilitaires
// =====================================================

function initials(first, last) {
    return ((first && first[0] || "") + (last && last[0] || "")).toUpperCase() || "?";
}

function currentUser() {
    const t = API.getToken();
    if (!t) return "utilisateur";
    try {
        return JSON.parse(atob(t.split(".")[1])).sub || "utilisateur";
    } catch {
        return "utilisateur";
    }
}

function toast(message, type = "success") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const el = document.createElement("div");
    el.className = "toast " + type;
    const icon = type === "error" ? "alert-circle" : type === "info" ? "info" : "check-circle";
    el.innerHTML = `<i data-lucide="${icon}"></i> <span>${escapeHtml(message)}</span>`;
    container.appendChild(el);
    lucide.createIcons();
    setTimeout(() => el.remove(), 4500);
}

function downloadJson(filename, obj) {
    const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    toast("Export JSON téléchargé", "success");
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
}

function formatDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleDateString("fr-FR") + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}
