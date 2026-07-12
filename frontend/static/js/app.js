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
    bindGlobalSearch();  // barre de recherche globale (topbar de toutes les pages)

    if (document.getElementById("dropzone")) initDashboard();
    if (document.getElementById("report-content")) initReport();
    if (document.getElementById("sources-container")) initResults();
    if (document.getElementById("global-graph")) initGraph();
    if (document.getElementById("video-stage")) initStream();
    if (document.getElementById("ft-results")) initSearchPage();
    if (document.getElementById("investigations-list")) initInvestigations();
    if (document.getElementById("investigation-detail")) initInvestigationDetail();
    if (document.getElementById("uname-grid")) initUsername();
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
    initAddToInvestigationModal();
    document.getElementById("persons-body").addEventListener("click", onPersonsClick);
    document.getElementById("history-body").addEventListener("click", onHistoryClick);

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
        resultEl.innerHTML = `<strong>${kind}</strong>${sourceBadgeHtml(data.source)} — ID ${data.person_id}, ` +
            `confiance ${(data.confidence_score * 100).toFixed(1)}%, ${data.faces_count} visage(s)`;
        resultEl.classList.remove("hidden");
        toast(kind, "success");
        loadPersons(0);
        loadStats();
        // Le serveur lance l'enrichissement OSINT pour un match sur un sujet d'enquête nommé.
        // On suit le job (404 géré = pas de job créé, ex. visage encore "Inconnu").
        if (!data.is_new && data.source === "investigation") {
            watchEnrichment(data.person_id, resultEl);
        }
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
                    `<td>${escapeHtml(p.first_name)}${sourceBadgeHtml(p.source)}</td>` +
                    `<td>${escapeHtml(p.last_name)}</td>` +
                    `<td>${(p.confidence_score * 100).toFixed(0)}%</td>` +
                    `<td class="text-muted">${formatDate(p.created_at)}</td>` +
                    `<td><div class="flex gap-8">` +
                    `<button class="btn btn-ghost btn-sm" data-edit="${p.id}"><i data-lucide="edit-3"></i></button>` +
                    `<button class="btn btn-ghost btn-sm" data-add-person="${p.id}" title="Ajouter à une enquête"><i data-lucide="folder-plus"></i></button>` +
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
    const addBtn = e.target.closest("[data-add-person]");
    const reportBtn = e.target.closest("[data-report]");
    if (editBtn) openEditModal(personsCache[editBtn.getAttribute("data-edit")], () => loadPersons(personsOffset));
    else if (addBtn) {
        const p = personsCache[addBtn.getAttribute("data-add-person")];
        const label = p ? `${p.first_name} ${p.last_name}`.trim() : null;
        openAddToInvestigationModal("person", addBtn.getAttribute("data-add-person"), label);
    }
    else if (reportBtn) window.location.href = "/report?id=" + reportBtn.getAttribute("data-report");
}

function onHistoryClick(e) {
    const addBtn = e.target.closest("[data-add-search]");
    if (addBtn) {
        openAddToInvestigationModal("search", addBtn.getAttribute("data-add-search"),
            addBtn.getAttribute("data-label"));
    }
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
    setEditSourceOptions(person.source || "investigation");
    document.getElementById("edit-error").classList.add("hidden");
    document.getElementById("edit-modal").classList.remove("hidden");
}

// Le select ne propose que les deux origines réellement assignables par un analyste
// (investigation / consented_demo). Si la personne est déjà lfw_dataset (jeu académique
// seedé par script), on ajoute temporairement l'option pour l'afficher correctement.
function setEditSourceOptions(source) {
    const sel = document.getElementById("edit-source");
    sel.querySelectorAll("option[data-dynamic]").forEach((o) => o.remove());
    if (!["investigation", "consented_demo"].includes(source)) {
        const opt = document.createElement("option");
        opt.value = source;
        opt.textContent = "Jeu academique LFW";
        opt.setAttribute("data-dynamic", "1");
        sel.appendChild(opt);
    }
    sel.value = source;
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
        source: document.getElementById("edit-source").value,
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
                const addBtn = `<button class="btn btn-ghost btn-sm" data-add-search="${h.id}" data-label="${escapeHtml(h.query)}" title="Ajouter à une enquête"><i data-lucide="folder-plus"></i></button>`;
                tr.innerHTML =
                    `<td>${escapeHtml(h.query)}</td>` +
                    `<td><span class="badge">${escapeHtml(h.query_type)}</span></td>` +
                    `<td><span class="status-badge done"><i data-lucide="check" style="width:13px;height:13px;"></i> Terminé</span></td>` +
                    `<td>${h.result_count}</td>` +
                    `<td class="text-muted">${formatDate(h.created_at)}</td>` +
                    `<td><div class="flex gap-8">${viewBtn}${addBtn}</div></td>`;
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
    initAddToInvestigationModal();
    document.getElementById("person-edit").addEventListener("click", () => {
        if (reportPerson) openEditModal(reportPerson, () => loadReport(reportPersonId));
    });
    document.getElementById("person-usernames").addEventListener("click", () => {
        if (!reportPerson) return;
        const slug = usernameSlug(reportPerson.first_name, reportPerson.last_name);
        if (!slug) { toast("Nom insuffisant pour déduire un pseudo", "info"); return; }
        window.location.href = "/username?q=" + encodeURIComponent(slug);
    });
    document.getElementById("person-enrich").addEventListener("click", onEnrichClick);
    document.getElementById("enrichment-rerun").addEventListener("click", onEnrichClick);
    document.getElementById("add-email-form").addEventListener("submit", onAddEmailSubmit);
    document.getElementById("emails-body").addEventListener("click", onReportEntityClick);
    document.getElementById("sites-body").addEventListener("click", onReportEntityClick);

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
        renderEnrichmentStatus(personId);  // section « Dernier enrichissement » (si un job existe)
        renderPersonGraph(data);
        renderReportTable("emails", data.emails, (e) =>
            `<td>${escapeHtml(e.email)}</td><td><span class="badge">${escapeHtml(e.source)}</span></td><td class="text-muted">${formatDate(e.created_at)}</td>` +
            `<td><button class="btn btn-ghost btn-sm" data-add-email="${e.id}" title="Ajouter à une enquête"><i data-lucide="folder-plus"></i></button></td>`);
        renderReportTable("sites", data.sites, (s) =>
            `<td>${escapeHtml(s.url)}</td><td>${escapeHtml(s.server || "—")}</td><td class="text-muted">${formatDate(s.discovered_at)}</td>` +
            `<td><button class="btn btn-ghost btn-sm" data-add-site="${s.id}" title="Ajouter à une enquête"><i data-lucide="folder-plus"></i></button></td>`);
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

async function onAddEmailSubmit(e) {
    e.preventDefault();
    const input = document.getElementById("add-email-input");
    const email = input.value.trim();
    if (!email || !reportPersonId) return;
    try {
        await API.addPersonEmail(reportPersonId, email);
        input.value = "";
        toast("Email ajouté", "success");
        loadReport(reportPersonId);
    } catch (err) {
        toast(err.message, "error");
    }
}

async function onEnrichClick() {
    if (!reportPersonId) return;
    const personId = reportPersonId;
    const btn = document.getElementById("person-enrich");
    const reset = () => {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="zap"></i> Enrichir';
        lucide.createIcons();
    };

    try {
        await API.enrichPerson(personId, "manual");
    } catch (err) {
        toast(err.message, "error");
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Enrichissement...';
    pollEnrichmentStatus(personId, {
        onState: (d) => renderEnrichmentStatusFrom(d),
        onDone: (d) => {
            reset();
            if (d && d.status === "completed") {
                toast(`✓ Enrichissement terminé : ${enrichmentSummaryText(d.summary)} trouvés`, "success");
                if (reportPersonId === personId) loadReport(personId);
            } else {
                if (d) renderEnrichmentStatusFrom(d);
                if (d && d.status === "failed")
                    toast("Enrichissement échoué" + (d.error_message ? " : " + d.error_message : ""), "error");
            }
        },
    });
}

// Section « Dernier enrichissement » de la page rapport. renderEnrichmentStatus va chercher
// le dernier job (utilisé au chargement) ; renderEnrichmentStatusFrom rend depuis des données
// déjà en main (utilisé pendant le polling, pour éviter une requête en double).
async function renderEnrichmentStatus(personId) {
    let d = null;
    try { d = await API.getEnrichmentStatus(personId); } catch { d = null; }  // 404 = aucun job
    renderEnrichmentStatusFrom(d);
}

function renderEnrichmentStatusFrom(d) {
    const section = document.getElementById("enrichment-section");
    if (!section) return;
    if (!d) { section.classList.add("hidden"); return; }
    section.classList.remove("hidden");

    const labels = { pending: "En attente", running: "En cours", completed: "Terminé", failed: "Échoué" };
    const badgeCls = { pending: "pending", running: "pending", completed: "done", failed: "archived" };
    const badge = document.getElementById("enrichment-status-badge");
    badge.textContent = labels[d.status] || d.status;
    badge.className = "status-badge " + (badgeCls[d.status] || "");

    document.getElementById("enrichment-trigger").textContent = "Déclenché par : " + (d.triggered_by || "—");
    const when = d.completed_at || d.started_at;
    document.getElementById("enrichment-date").textContent = when ? formatDate(when) : "";
    document.getElementById("enrichment-duration").textContent =
        d.duration_seconds != null ? `· ${d.duration_seconds}s` : "";

    const sum = document.getElementById("enrichment-summary");
    if (d.status === "completed" && d.summary) {
        const s = d.summary;
        sum.textContent = `${s.emails_found || 0} emails · ${s.sites_found || 0} sites · `
            + `${s.correlations_created || 0} corrélations · ${s.username_platforms_found || 0} plateformes`;
    } else {
        sum.textContent = "";
    }

    const errEl = document.getElementById("enrichment-error");
    if (d.status === "failed" && d.error_message) {
        errEl.textContent = d.error_message;
        errEl.classList.remove("hidden");
    } else {
        errEl.classList.add("hidden");
    }

    document.getElementById("enrichment-rerun")
        .classList.toggle("hidden", !(d.status === "completed" || d.status === "failed"));
}

function renderPersonHeader(p) {
    const seed = (p.first_name || "") + (p.last_name || "");
    let h = 0;
    for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    const avatar = document.getElementById("person-avatar");
    avatar.textContent = ((p.first_name[0] || "") + (p.last_name[0] || "")).toUpperCase() || "?";
    avatar.style.background = AVATAR_COLORS[h % AVATAR_COLORS.length];

    document.getElementById("person-name").innerHTML =
        escapeHtml(`${p.first_name} ${p.last_name}`) + sourceBadgeHtml(p.source);
    document.getElementById("person-score").textContent = `${(p.confidence_score * 100).toFixed(0)}% confiance`;
    document.getElementById("person-id").textContent = `#${p.id}`;
    document.getElementById("person-date").textContent = formatDate(p.created_at);

    // Enrichissement OSINT réservé aux sujets d'enquête : on masque le bouton pour un sujet
    // consentant / une entrée LFW (le backend le refuserait de toute façon, mais autant ne
    // pas le proposer). Voir CLAUDE.md, section reconnaissance faciale.
    const enrichBtn = document.getElementById("person-enrich");
    if (enrichBtn) enrichBtn.classList.toggle("hidden", p.source !== "investigation");

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

function onReportEntityClick(e) {
    const emailBtn = e.target.closest("[data-add-email]");
    const siteBtn = e.target.closest("[data-add-site]");
    if (emailBtn) {
        const id = emailBtn.getAttribute("data-add-email");
        const em = (reportExport && reportExport.emails || []).find((x) => String(x.id) === id);
        openAddToInvestigationModal("email", id, em ? em.email : ("email #" + id));
    } else if (siteBtn) {
        const id = siteBtn.getAttribute("data-add-site");
        const st = (reportExport && reportExport.sites || []).find((x) => String(x.id) === id);
        openAddToInvestigationModal("site", id, st ? st.url : ("site #" + id));
    }
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
// Modal partagé « Ajouter à une enquête » (Module 14)
// Réutilisable depuis dashboard, rapport, historique.
// =====================================================

let _addInvTarget = null;  // { type, id }

function initAddToInvestigationModal() {
    const modal = document.getElementById("add-inv-modal");
    if (!modal) return;  // page sans ce modal
    document.getElementById("add-inv-cancel").addEventListener("click", closeAddToInvestigationModal);
    document.getElementById("add-inv-save").addEventListener("click", saveAddToInvestigation);
    modal.addEventListener("click", (e) => {
        if (e.target.id === "add-inv-modal") closeAddToInvestigationModal();
    });
}

async function openAddToInvestigationModal(entityType, entityId, label) {
    _addInvTarget = { type: entityType, id: entityId };
    const select = document.getElementById("add-inv-select");
    const err = document.getElementById("add-inv-error");
    const save = document.getElementById("add-inv-save");

    document.getElementById("add-inv-target").textContent =
        `Entité : ${label || (entityType + " #" + entityId)}`;
    document.getElementById("add-inv-notes").value = "";
    err.classList.add("hidden");
    select.innerHTML = "<option>Chargement...</option>";
    save.disabled = true;
    document.getElementById("add-inv-modal").classList.remove("hidden");

    try {
        const list = await API.listInvestigations("open");
        if (!list.length) {
            select.innerHTML = "<option value=''>— Aucune enquête ouverte —</option>";
            err.textContent = "Aucune enquête ouverte. Créez-en une depuis la page Enquêtes.";
            err.classList.remove("hidden");
            save.disabled = true;
        } else {
            select.innerHTML = list.map((i) =>
                `<option value="${i.id}">${escapeHtml(i.title)}</option>`).join("");
            save.disabled = false;
        }
    } catch (e) {
        select.innerHTML = "";
        err.textContent = e.message;
        err.classList.remove("hidden");
    }
}

function closeAddToInvestigationModal() {
    document.getElementById("add-inv-modal").classList.add("hidden");
}

async function saveAddToInvestigation() {
    const select = document.getElementById("add-inv-select");
    const invId = select.value;
    const err = document.getElementById("add-inv-error");
    if (!invId) {
        err.textContent = "Sélectionnez une enquête.";
        err.classList.remove("hidden");
        return;
    }
    const notes = document.getElementById("add-inv-notes").value.trim();
    const title = (select.options[select.selectedIndex] || {}).text || "l'enquête";
    try {
        await API.addInvestigationEntity(invId, {
            entity_type: _addInvTarget.type,
            entity_id: parseInt(_addInvTarget.id, 10),
            notes: notes || null,
        });
        closeAddToInvestigationModal();
        toast(`Ajouté à l'enquête « ${title} »`, "success");
    } catch (e) {
        err.textContent = e.message;
        err.classList.remove("hidden");
    }
}


// =====================================================
// Enquêtes (Module 14) — liste (investigations.html)
// =====================================================

// Métadonnées d'affichage par type d'entité rattachée
const INV_ENTITY_META = {
    person:      { icon: "user",      label: "Personne",   cls: "" },
    email:       { icon: "mail",      label: "Email",      cls: "email" },
    site:        { icon: "globe",     label: "Site",       cls: "site" },
    correlation: { icon: "git-merge", label: "Corrélation", cls: "corr" },
    search:      { icon: "search",    label: "Recherche",  cls: "" },
    username_search: { icon: "at-sign", label: "Pseudo", cls: "" },
};
const INV_STATUS_META = {
    open:     { cls: "open",     label: "Ouverte" },
    closed:   { cls: "closed",   label: "Fermée" },
    archived: { cls: "archived", label: "Archivée" },
};

function setUserChip() {
    const user = currentUser();
    const info = document.getElementById("user-info");
    if (info) info.textContent = user;
    const avatar = document.getElementById("user-avatar");
    if (avatar) avatar.textContent = user.slice(0, 2).toUpperCase();
}

let invStatusFilter = "";

function initInvestigations() {
    setUserChip();

    document.getElementById("new-investigation-btn").addEventListener("click", openCreateModal);
    document.getElementById("create-cancel").addEventListener("click", closeCreateModal);
    document.getElementById("create-save").addEventListener("click", saveInvestigation);
    document.getElementById("create-modal").addEventListener("click", (e) => {
        if (e.target.id === "create-modal") closeCreateModal();
    });

    document.getElementById("status-filter").addEventListener("click", (e) => {
        const tab = e.target.closest(".tab");
        if (!tab) return;
        document.querySelectorAll("#status-filter .tab").forEach((t) => t.classList.toggle("active", t === tab));
        invStatusFilter = tab.getAttribute("data-status");
        loadInvestigations();
    });

    document.getElementById("investigations-list").addEventListener("click", (e) => {
        const card = e.target.closest(".inv-card");
        if (card) window.location.href = "/investigation_detail?id=" + card.getAttribute("data-id");
    });

    loadInvestigations();
}

async function loadInvestigations() {
    const loading = document.getElementById("investigations-loading");
    const list = document.getElementById("investigations-list");
    const empty = document.getElementById("investigations-empty");
    const errorEl = document.getElementById("investigations-error");
    loading.classList.remove("hidden");
    list.classList.add("hidden");
    empty.classList.add("hidden");
    errorEl.classList.add("hidden");

    try {
        const items = await API.listInvestigations(invStatusFilter);
        if (!items.length) {
            empty.classList.remove("hidden");
        } else {
            list.innerHTML = items.map(renderInvestigationCard).join("");
            list.classList.remove("hidden");
        }
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    } finally {
        loading.classList.add("hidden");
        lucide.createIcons();
    }
}

function renderInvestigationCard(inv) {
    const st = INV_STATUS_META[inv.status] || { cls: "", label: inv.status };
    const desc = inv.description ? escapeHtml(inv.description) : "<span class='text-muted'>Aucune description</span>";
    return `<div class="inv-card" data-id="${inv.id}">
        <div class="flex between items-center" style="gap:8px;">
            <span class="inv-card-title">${escapeHtml(inv.title)}</span>
            <span class="status-badge ${st.cls}">${st.label}</span>
        </div>
        <div class="inv-card-desc">${desc}</div>
        <div class="inv-card-meta">
            <span><i data-lucide="layers"></i> ${inv.entity_count} entité${inv.entity_count > 1 ? "s" : ""}</span>
            <span><i data-lucide="calendar"></i> ${formatDate(inv.created_at)}</span>
        </div>
    </div>`;
}

function openCreateModal() {
    document.getElementById("create-title").value = "";
    document.getElementById("create-description").value = "";
    document.getElementById("create-error").classList.add("hidden");
    document.getElementById("create-modal").classList.remove("hidden");
    document.getElementById("create-title").focus();
}

function closeCreateModal() {
    document.getElementById("create-modal").classList.add("hidden");
}

async function saveInvestigation() {
    const title = document.getElementById("create-title").value.trim();
    const description = document.getElementById("create-description").value.trim();
    const errorEl = document.getElementById("create-error");
    if (!title) {
        errorEl.textContent = "Le titre est obligatoire.";
        errorEl.classList.remove("hidden");
        return;
    }
    try {
        await API.createInvestigation({ title, description: description || null });
        closeCreateModal();
        toast("Enquête créée", "success");
        loadInvestigations();
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    }
}


// =====================================================
// Enquêtes (Module 14) — détail (investigation_detail.html)
// =====================================================

let currentInvestigationId = null;

function initInvestigationDetail() {
    setUserChip();
    const id = new URLSearchParams(window.location.search).get("id");
    if (!id) {
        showInvestigationError("Aucune enquête spécifiée.");
        return;
    }
    currentInvestigationId = id;

    // Onglets Entités / Timeline
    document.getElementById("inv-tabs").addEventListener("click", (e) => {
        const tab = e.target.closest(".tab");
        if (!tab) return;
        const name = tab.getAttribute("data-tab");
        document.querySelectorAll("#inv-tabs .tab").forEach((t) => t.classList.toggle("active", t === tab));
        document.querySelectorAll("#investigation-detail .tab-panel").forEach((p) =>
            p.classList.toggle("active", p.id === "panel-" + name));
    });

    // Export PDF — téléchargement direct (Blob), sans changer de page
    document.getElementById("export-pdf-btn").addEventListener("click", exportInvestigationPdf);

    // Modal d'ajout d'entité
    document.getElementById("add-entity-btn").addEventListener("click", openAddEntityModal);
    document.getElementById("add-entity-cancel").addEventListener("click", closeAddEntityModal);
    document.getElementById("add-entity-save").addEventListener("click", saveEntity);
    document.getElementById("add-entity-modal").addEventListener("click", (e) => {
        if (e.target.id === "add-entity-modal") closeAddEntityModal();
    });

    // Retrait d'une entité (délégation sur le tableau)
    document.getElementById("entities-body").addEventListener("click", (e) => {
        const btn = e.target.closest("[data-remove]");
        if (btn) removeEntity(btn.getAttribute("data-remove"));
    });

    loadInvestigation(currentInvestigationId);
}

async function loadInvestigation(id) {
    const loading = document.getElementById("investigation-loading");
    const detail = document.getElementById("investigation-detail");
    const errorEl = document.getElementById("investigation-error");
    loading.classList.remove("hidden");
    detail.classList.add("hidden");
    errorEl.classList.add("hidden");

    try {
        // Détail (ids de rattachement + notes) + timeline (libellés + dates de découverte)
        const [inv, timeline] = await Promise.all([
            API.getInvestigation(id),
            API.getInvestigationTimeline(id),
        ]);

        renderInvestigationHeader(inv);

        // Carte des libellés résolus, indexée par "type:entity_id" (fournie par la timeline)
        const labels = {};
        timeline.items.forEach((it) => { labels[it.entity_type + ":" + it.entity_id] = it.label; });

        renderEntities(inv.entities, labels);
        renderInvestigationTimeline(timeline.items);

        document.getElementById("export-pdf-btn").disabled = false;
        detail.classList.remove("hidden");
        lucide.createIcons();
    } catch (err) {
        showInvestigationError(err.message);
    } finally {
        loading.classList.add("hidden");
    }
}

function renderInvestigationHeader(inv) {
    document.querySelector(".topbar h1").textContent = inv.title;
    document.getElementById("inv-title").textContent = inv.title;

    const st = INV_STATUS_META[inv.status] || { cls: "", label: inv.status };
    const badge = document.getElementById("inv-status");
    badge.textContent = st.label;
    badge.className = "status-badge " + st.cls;

    document.getElementById("inv-id").textContent = "#" + inv.id;
    document.getElementById("inv-analyst").innerHTML =
        `<i data-lucide="user"></i> ${inv.analyst_id ? "Analyste #" + inv.analyst_id : "—"}`;
    document.getElementById("inv-created").innerHTML =
        `<i data-lucide="calendar"></i> Créée le ${formatDate(inv.created_at)}`;
    document.getElementById("inv-updated").innerHTML =
        `<i data-lucide="refresh-cw"></i> Maj ${formatDate(inv.updated_at)}`;

    const desc = document.getElementById("inv-description");
    if (inv.description) {
        desc.textContent = inv.description;
        desc.classList.remove("hidden");
    } else {
        desc.classList.add("hidden");
    }
}

function renderEntities(entities, labels) {
    document.getElementById("count-entities").textContent = entities.length;
    const body = document.getElementById("entities-body");
    const empty = document.getElementById("entities-empty");

    if (!entities.length) {
        body.innerHTML = "";
        empty.classList.remove("hidden");
        return;
    }
    empty.classList.add("hidden");

    body.innerHTML = entities.map((en) => {
        const meta = INV_ENTITY_META[en.entity_type] || { icon: "box", label: en.entity_type, cls: "" };
        const label = labels[en.entity_type + ":" + en.entity_id] || ("#" + en.entity_id);
        return `<tr>
            <td><span class="badge ${meta.cls}"><i data-lucide="${meta.icon}"></i> ${meta.label}</span></td>
            <td>${escapeHtml(label)} <span class="text-muted mono">#${en.entity_id}</span></td>
            <td class="text-muted">${en.notes ? escapeHtml(en.notes) : "—"}</td>
            <td class="text-muted">${formatDate(en.added_at)}</td>
            <td><button class="btn btn-ghost btn-sm" data-remove="${en.id}" title="Retirer de l'enquête"><i data-lucide="x"></i></button></td>
        </tr>`;
    }).join("");
}

function renderInvestigationTimeline(items) {
    const timeline = document.getElementById("inv-timeline");
    const empty = document.getElementById("timeline-empty");

    if (!items.length) {
        timeline.innerHTML = "";
        empty.classList.remove("hidden");
        return;
    }
    empty.classList.add("hidden");

    timeline.innerHTML = items.map((it) => {
        const meta = INV_ENTITY_META[it.entity_type] || { label: it.entity_type, cls: "" };
        const date = it.discovered_at || it.added_at;
        const note = it.notes ? ` · <span class="text-muted">${escapeHtml(it.notes)}</span>` : "";
        return `<div class="timeline-item ${meta.cls}">
            <div class="tl-title">${meta.label} : ${escapeHtml(it.label)}${note}</div>
            <div class="tl-date">${formatDate(date)}</div>
        </div>`;
    }).join("");
}

async function exportInvestigationPdf() {
    const btn = document.getElementById("export-pdf-btn");
    btn.disabled = true;
    try {
        const { blob, filename } = await API.exportInvestigationPdf(currentInvestigationId);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        toast("Rapport PDF téléchargé", "success");
    } catch (err) {
        toast(err.message, "error");
    } finally {
        btn.disabled = false;
    }
}

function openAddEntityModal() {
    document.getElementById("entity-type").value = "person";
    document.getElementById("entity-id").value = "";
    document.getElementById("entity-notes").value = "";
    document.getElementById("add-entity-error").classList.add("hidden");
    document.getElementById("add-entity-modal").classList.remove("hidden");
    document.getElementById("entity-id").focus();
}

function closeAddEntityModal() {
    document.getElementById("add-entity-modal").classList.add("hidden");
}

async function saveEntity() {
    const entity_type = document.getElementById("entity-type").value;
    const entity_id = parseInt(document.getElementById("entity-id").value, 10);
    const notes = document.getElementById("entity-notes").value.trim();
    const errorEl = document.getElementById("add-entity-error");

    if (!entity_id || entity_id < 1) {
        errorEl.textContent = "Identifiant d'entité invalide.";
        errorEl.classList.remove("hidden");
        return;
    }
    try {
        await API.addInvestigationEntity(currentInvestigationId, { entity_type, entity_id, notes: notes || null });
        closeAddEntityModal();
        toast("Entité rattachée", "success");
        loadInvestigation(currentInvestigationId);
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    }
}

async function removeEntity(linkId) {
    if (!confirm("Retirer cette entité de l'enquête ?")) return;
    try {
        await API.removeInvestigationEntity(currentInvestigationId, linkId);
        toast("Entité retirée", "info");
        loadInvestigation(currentInvestigationId);
    } catch (err) {
        toast(err.message, "error");
    }
}

function showInvestigationError(message) {
    const errorEl = document.getElementById("investigation-error");
    errorEl.textContent = message;
    errorEl.classList.remove("hidden");
    document.getElementById("investigation-loading").classList.add("hidden");
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
// Page stream webcam (stream.html)
// =====================================================

// Architecture CLIENT-SIDE : la webcam est capturée dans le navigateur (getUserMedia).
// Toutes les 500 ms, un frame est capturé, envoyé à POST /api/stream/analyze, et les
// visages détectés sont dessinés sur un <canvas> superposé à la <video>.
let streamRunning = false;
let streamInterval = null;
let mediaStream = null;
let analyzeInFlight = false;   // skip un frame tant que l'analyse précédente n'est pas finie
let fpsSamples = [];           // horodatages des analyses réussies (FPS effectif sur 1 s)
let streamMatchPersonId = null; // person_id du dernier match enrichissable (bouton cliquable)
let lastKnownMatch = null;     // dernier visage reconnu (survit à quelques frames sans match)
let matchMissStreak = 0;       // frames consécutives sans ce match, avant abandon
const MATCH_MISS_TOLERANCE = 4; // ~2s à 500ms/frame — absorbe le clignotement frame-à-frame

function initStream() {
    const user = currentUser();
    document.getElementById("user-info").textContent = user;
    document.getElementById("user-avatar").textContent = user.slice(0, 2).toUpperCase();

    document.getElementById("toggle-btn").addEventListener("click", toggleStream);
    document.getElementById("register-btn").addEventListener("click", registerStreamFace);
    document.getElementById("stream-enrich-btn").addEventListener("click", onStreamEnrichClick);

    const toggle = document.getElementById("toggle-btn");
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        showStreamError("Votre navigateur ne supporte pas l'accès à la caméra (getUserMedia).");
        toggle.disabled = true;
        return;
    }
    // Avertissement HTTP non bloquant : en dev (localhost / IP privée) Chrome autorise
    // getUserMedia en HTTP ; en production, HTTPS est requis.
    if (location.protocol !== "https:" && !isLocalHost()) {
        showStreamWarning("En production, HTTPS est requis pour accéder à la webcam. "
            + "En développement local (localhost / IP privée), Chrome l'autorise en HTTP.");
    }
    toggle.disabled = false;
    document.getElementById("placeholder-text").textContent =
        "Cliquez sur « Démarrer » pour activer votre caméra.";
}

function isLocalHost() {
    const h = location.hostname;
    return h === "localhost" || h === "127.0.0.1" || h === "[::1]"
        || /^(10\.|127\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h);
}

async function toggleStream() {
    if (streamRunning) stopStream();
    else await startStream();
}

async function startStream() {
    document.getElementById("stream-error").classList.add("hidden");
    const toggle = document.getElementById("toggle-btn");
    toggle.disabled = true;

    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } });
    } catch (err) {
        toggle.disabled = false;
        showCameraError(err);
        return;
    }

    const video = document.getElementById("webcam");
    video.srcObject = mediaStream;
    try { await video.play(); } catch { /* autoplay muet : ignore */ }

    document.getElementById("webcam-wrap").classList.remove("hidden");
    document.getElementById("video-placeholder").classList.add("hidden");

    streamRunning = true;
    fpsSamples = [];
    analyzeInFlight = false;
    setToggle(true);

    if (video.readyState >= 1) syncCanvasSize();
    else video.addEventListener("loadedmetadata", syncCanvasSize, { once: true });

    streamInterval = setInterval(analyzeFrame, 500);
    toggle.disabled = false;
    toast("Caméra activée", "success");
}

function showCameraError(err) {
    const name = err && err.name;
    let msg;
    if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
        msg = "Accès refusé. Autorisez l'accès à la caméra dans votre navigateur.";
    } else if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") {
        msg = "Aucune caméra détectée sur cet appareil.";
    } else if (name === "NotReadableError") {
        msg = "La caméra est déjà utilisée par une autre application.";
    } else {
        msg = "Impossible d'accéder à la caméra : " + ((err && err.message) || "erreur inconnue");
    }
    showStreamError(msg);
    toast(msg, "error");
}

function stopStream() {
    if (streamInterval) { clearInterval(streamInterval); streamInterval = null; }
    if (mediaStream) {
        mediaStream.getTracks().forEach((t) => t.stop());  // libère la caméra
        mediaStream = null;
    }
    streamRunning = false;
    analyzeInFlight = false;

    const video = document.getElementById("webcam");
    video.srcObject = null;
    clearOverlay();
    document.getElementById("webcam-wrap").classList.add("hidden");
    document.getElementById("video-placeholder").classList.remove("hidden");
    document.getElementById("placeholder-text").textContent = "Caméra arrêtée. Cliquez sur « Démarrer ».";

    setToggle(false);
    resetStreamStats();
    toast("Caméra arrêtée", "info");
}

function setToggle(running) {
    const btn = document.getElementById("toggle-btn");
    const dot = document.getElementById("status-dot");
    btn.innerHTML = running
        ? '<i data-lucide="square"></i> <span id="toggle-label">Arreter</span>'
        : '<i data-lucide="play"></i> <span id="toggle-label">Demarrer</span>';
    btn.classList.toggle("btn-primary", !running);
    btn.classList.toggle("btn-ghost", running);
    document.getElementById("stat-status").textContent = running ? "En cours" : "Arrete";
    dot.classList.toggle("on", running);
    dot.classList.toggle("off", !running);
    lucide.createIcons();
}

function resetStreamStats() {
    document.getElementById("stat-fps").textContent = "—";
    document.getElementById("stat-faces").textContent = "—";
    document.getElementById("stat-matches").textContent = "—";
    lastKnownMatch = null;
    matchMissStreak = 0;
    streamMatchPersonId = null;
    showIdentity("empty");
}

function syncCanvasSize() {
    // Le canvas overlay utilise le référentiel natif de la vidéo (coords des visages) ;
    // le CSS l'étire à la taille affichée, identique à la vidéo → rectangles alignés.
    const video = document.getElementById("webcam");
    const overlay = document.getElementById("overlay");
    overlay.width = video.videoWidth || 640;
    overlay.height = video.videoHeight || 480;
}

async function analyzeFrame() {
    // Skip si l'analyse précédente n'est pas terminée → évite l'accumulation (serveur lent)
    if (!streamRunning || analyzeInFlight) return;
    const video = document.getElementById("webcam");
    if (!video.videoWidth) return;

    const cap = document.getElementById("capture");
    cap.width = video.videoWidth;
    cap.height = video.videoHeight;
    cap.getContext("2d").drawImage(video, 0, 0, cap.width, cap.height);
    const frame = cap.toDataURL("image/jpeg", 0.7);

    analyzeInFlight = true;
    try {
        const data = await API.streamAnalyze(frame);
        if (!streamRunning) return;  // arrêté pendant la requête
        drawFaces(data.faces);
        updateStreamStats(data);
    } catch (err) {
        // Frame ignoré (429 rate limit, erreur ponctuelle) — pas de spam de toasts
        console.warn("[stream] frame ignoré :", err.message);
    } finally {
        analyzeInFlight = false;
    }
}

// Puce HTML d'origine d'une personne, selon Person.source. Chaîne vide pour une personne
// d'enquête standard (source="investigation") — pas de badge, cas par défaut.
function sourceBadgeHtml(source) {
    if (source === "lfw_dataset")
        return ` <span class="lfw-badge" title="Jeu de données académique public (Labeled Faces in the Wild) — pas une vraie personne investiguée">LFW</span>`;
    if (source === "consented_demo")
        return ` <span class="consent-badge" title="Sujet consentant enregistré pour la démonstration — hors périmètre d'enquête, aucun enrichissement OSINT automatique">✓ Sujet consentant</span>`;
    return "";
}

function drawFaces(faces) {
    const overlay = document.getElementById("overlay");
    const ctx = overlay.getContext("2d");
    ctx.clearRect(0, 0, overlay.width, overlay.height);

    faces.forEach((face) => {
        const { top, right, bottom, left } = face.location;
        const known = !face.is_new;
        const isLfw = face.source === "lfw_dataset";
        const isDemo = face.source === "consented_demo";
        // vert = personne réellement investiguée / ambre = jeu de données académique LFW /
        // violet = sujet consentant de démo / rouge = inconnu — jamais confondus entre eux
        const color = !known ? "#ef4444" : (isLfw ? "#f59e0b" : (isDemo ? "#8b5cf6" : "#10b981"));
        const tag = isLfw ? " (LFW)" : (isDemo ? " (démo)" : "");
        const label = known
            ? `${face.name || "?"} · ${Math.round((face.confidence || 0) * 100)}%${tag}`
            : "Inconnu";

        ctx.lineWidth = 3;
        ctx.strokeStyle = color;
        ctx.strokeRect(left, top, right - left, bottom - top);

        // Badge (nom/score) au-dessus du rectangle (ou juste sous le bord haut si pas de place)
        ctx.font = "600 16px Inter, system-ui, sans-serif";
        const padX = 7, badgeH = 24;
        const w = ctx.measureText(label).width + padX * 2;
        const y = top - badgeH >= 0 ? top - badgeH : top;
        ctx.fillStyle = color;
        ctx.fillRect(left, y, w, badgeH);
        ctx.fillStyle = "#ffffff";
        ctx.textBaseline = "middle";
        ctx.fillText(label, left + padX, y + badgeH / 2 + 1);
    });
}

function clearOverlay() {
    const overlay = document.getElementById("overlay");
    overlay.getContext("2d").clearRect(0, 0, overlay.width, overlay.height);
}

function updateStreamStats(data) {
    const now = performance.now();
    fpsSamples.push(now);
    fpsSamples = fpsSamples.filter((t) => now - t < 1000);
    document.getElementById("stat-fps").textContent = fpsSamples.length;
    document.getElementById("stat-faces").textContent = data.faces_count;

    const known = data.faces.filter((f) => !f.is_new);
    document.getElementById("stat-matches").textContent = known.length;

    if (known.length) {
        matchMissStreak = 0;
        lastKnownMatch = known[0];
        renderMatch(lastKnownMatch);
    } else if (lastKnownMatch && matchMissStreak < MATCH_MISS_TOLERANCE) {
        // Match perdu sur CETTE frame (angle, éclairage, bruit d'embedding) : chaque frame est
        // comparée indépendamment côté serveur (aucune mémoire d'une requête à l'autre), donc
        // un aller-retour "reconnu"/"non reconnu" de quelques frames est attendu, pas une vraie
        // disparition. On continue d'afficher le dernier match connu quelques cycles avant de
        // l'abandonner, sinon la carte d'identité (et le bouton Enrichir) clignote en continu.
        matchMissStreak++;
        renderMatch(lastKnownMatch);
    } else if (data.faces_count > 0) {
        lastKnownMatch = null;
        showIdentity("new");
    } else {
        lastKnownMatch = null;
        matchMissStreak = 0;
        showIdentity("empty");
    }
}

function renderMatch(m) {
    document.getElementById("match-name").innerHTML =
        escapeHtml(m.name || "?") + sourceBadgeHtml(m.source);
    document.getElementById("match-score").textContent =
        `${Math.round((m.confidence || 0) * 100)}% de confiance`;
    document.getElementById("match-report").href = "/report?id=" + m.person_id;
    // Enrichissement OSINT proposé (jamais auto-déclenché) pour tout vrai sujet d'enquête
    // reconnu — un sujet consentant / une entrée LFW n'y a jamais droit (le backend le
    // refuserait de toute façon). Pas de seuil de confiance additionnel ici : le filtre
    // qualité est déjà le seuil de matching côté serveur (FACE_MATCH_THRESHOLD + marge
    // d'ambiguïté, cf. compare_embedding) — un match accepté peut légitimement descendre
    // à ~45 % de confiance (webcam en direct vs une seule photo enregistrée), un seuil
    // additionnel à 70 % ici masquait le bouton pour la quasi-totalité des matchs réels.
    const enrichable = m.source === "investigation";
    streamMatchPersonId = enrichable ? m.person_id : null;
    document.getElementById("match-enrich-wrap").classList.toggle("hidden", !enrichable);
    showIdentity("match");
}

function showIdentity(state) {
    document.getElementById("identity-empty").classList.toggle("hidden", state !== "empty");
    document.getElementById("identity-match").classList.toggle("hidden", state !== "match");
    document.getElementById("identity-new").classList.toggle("hidden", state !== "new");
}

// Polling générique du statut d'enrichissement (Étapes 3+4). Interroge
// GET /api/persons/{id}/enrichment_status toutes les 3 s ; appelle onState à chaque tick puis
// onDone(data) au premier état terminal (completed/failed). onDone(null) si aucun job (404
// persistant) ou timeout. Garde-fou anti-boucle infinie.
function pollEnrichmentStatus(personId, { onState, onDone } = {}) {
    let ticks = 0;
    const maxTicks = 100;  // ~5 min à 3 s d'intervalle
    const timer = setInterval(async () => {
        ticks++;
        let data;
        try {
            data = await API.getEnrichmentStatus(personId);
        } catch (err) {
            // 404 = job pas encore visible (fenêtre courte) : on retente quelques fois.
            if (ticks > 5) { clearInterval(timer); if (onDone) onDone(null); }
            return;
        }
        if (onState) onState(data);
        if (data.status === "completed" || data.status === "failed" || ticks >= maxTicks) {
            clearInterval(timer);
            if (onDone) onDone(data);
        }
    }, 3000);
    return timer;
}

// Résumé lisible d'un job terminé, pour les toasts.
function enrichmentSummaryText(summary) {
    const s = summary || {};
    return `${s.emails_found || 0} emails, ${s.sites_found || 0} sites, `
        + `${s.username_platforms_found || 0} plateformes`;
}

// Suit un enrichissement en injectant une ligne de statut dans `container` (upload dashboard).
function watchEnrichment(personId, container) {
    const box = document.createElement("div");
    box.style.marginTop = "8px";
    box.style.fontSize = "0.88rem";
    box.innerHTML = '<span class="spinner"></span> Enrichissement OSINT en cours...';
    container.appendChild(box);
    pollEnrichmentStatus(personId, {
        onDone: (d) => {
            if (!d) { box.remove(); return; }
            if (d.status === "completed") {
                box.innerHTML = `<span style="color:var(--success); font-weight:600;">✓ Enrichissement terminé</span> — `
                    + `${enrichmentSummaryText(d.summary)} · <a href="/report?id=${personId}">Voir le rapport</a>`;
                toast(`✓ Enrichissement terminé : ${enrichmentSummaryText(d.summary)} trouvés`, "success");
            } else {
                box.innerHTML = `<span style="color:var(--danger);">Enrichissement échoué</span>`
                    + (d.error_message ? ` — ${escapeHtml(d.error_message)}` : "");
            }
        },
    });
}

// Déclenchement MANUEL de l'enrichissement depuis le flux (jamais automatique). Le backend
// re-vérifie que la personne est bien un sujet d'enquête (source=investigation).
async function onStreamEnrichClick() {
    const personId = streamMatchPersonId;
    if (!personId) return;
    const btn = document.getElementById("stream-enrich-btn");
    const reset = () => {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="zap"></i> Enrichir maintenant';
        lucide.createIcons();
    };
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Enrichissement lancé...';
    try {
        await API.enrichPerson(personId, "stream");
    } catch (err) {
        toast(err.message, "error");
        reset();
        return;
    }
    pollEnrichmentStatus(personId, {
        onState: (d) => {
            if (d.status === "pending" || d.status === "running")
                btn.innerHTML = '<span class="spinner"></span> Enrichissement en cours...';
        },
        onDone: (d) => {
            reset();
            if (d && d.status === "completed")
                toast(`✓ Enrichissement terminé : ${enrichmentSummaryText(d.summary)} trouvés`, "success");
            else if (d && d.status === "failed")
                toast("Enrichissement échoué" + (d.error_message ? " : " + d.error_message : ""), "error");
        },
    });
}

function registerStreamFace() {
    const video = document.getElementById("webcam");
    if (!video.videoWidth) { toast("Caméra non prête", "error"); return; }
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob(async (blob) => {
        if (!blob) return;
        const file = new File([blob], "capture.jpg", { type: "image/jpeg" });
        try {
            const data = await API.uploadFace(file);
            toast(data.is_new
                ? "Visage enregistré — il sera reconnu dès les prochains frames"
                : "Visage déjà connu", "success");
        } catch (err) {
            toast(err.message, "error");
        }
    }, "image/jpeg", 0.9);
}

function showStreamError(msg) {
    const el = document.getElementById("stream-error");
    el.textContent = msg;
    el.classList.remove("hidden");
}

function showStreamWarning(msg) {
    const el = document.getElementById("stream-warning");
    if (!el) return;
    el.textContent = msg;
    el.classList.remove("hidden");
}


// =====================================================
// Utilitaires
// =====================================================

function initials(first, last) {
    return ((first && first[0] || "") + (last && last[0] || "")).toUpperCase() || "?";
}

// =====================================================
// Recherche globale (full-text Elasticsearch)
// =====================================================

// Barre de recherche présente dans la topbar de toutes les pages :
// Enter ou clic sur l'icône → redirection vers /search?q=...
function bindGlobalSearch() {
    document.querySelectorAll(".global-search").forEach((form) => {
        form.addEventListener("submit", (e) => {
            e.preventDefault();
            const input = form.querySelector(".global-search-input");
            const q = input ? input.value.trim() : "";
            if (q) window.location.href = "/search?q=" + encodeURIComponent(q);
        });
    });
}

// Métadonnées d'affichage par index ES (ordre d'affichage des groupes)
const FT_GROUPS = [
    { index: "osint_persons",        key: "persons", emoji: "👤", label: "Personnes" },
    { index: "osint_emails",         key: "emails",  emoji: "📧", label: "Emails" },
    { index: "osint_sites",          key: "sites",   emoji: "🌐", label: "Sites" },
    { index: "osint_search_results", key: "results", emoji: "🔍", label: "Recherches" },
];

function initSearchPage() {
    const user = currentUser();
    const info = document.getElementById("user-info");
    if (info) info.textContent = user;
    const avatar = document.getElementById("user-avatar");
    if (avatar) avatar.textContent = user.slice(0, 2).toUpperCase();

    const q = (new URLSearchParams(window.location.search).get("q") || "").trim();
    const gsi = document.querySelector(".global-search-input");
    if (gsi) gsi.value = q;

    const subtitle = document.getElementById("ft-subtitle");
    if (!q) {
        if (subtitle) subtitle.textContent = "Saisissez une requête dans la barre de recherche.";
        document.getElementById("ft-loading").classList.add("hidden");
        showFtEmpty("Aucune requête", "Utilisez la barre de recherche en haut pour lancer une recherche globale.");
        return;
    }
    if (subtitle) subtitle.textContent = `Résultats pour « ${q} »`;
    runFulltext(q);
}

async function runFulltext(q) {
    const loading = document.getElementById("ft-loading");
    const results = document.getElementById("ft-results");
    const empty = document.getElementById("ft-empty");
    const unavailable = document.getElementById("ft-unavailable");

    loading.classList.remove("hidden");
    empty.classList.add("hidden");
    unavailable.classList.add("hidden");
    results.innerHTML = "";

    let data;
    try {
        data = await API.fulltextSearch(q, "all", 50, 0);
    } catch (err) {
        loading.classList.add("hidden");
        toast(err.message, "error");
        showFtEmpty("Erreur", "La recherche a échoué. Réessayez.");
        return;
    }
    loading.classList.add("hidden");

    if (!data.hits || data.hits.length === 0) {
        // total=0 : distinguer « aucun résultat » d'un « ES indisponible »
        let available = true;
        try { available = (await API.searchStatus()).available; } catch { available = true; }
        if (!available) {
            unavailable.classList.remove("hidden");
            document.getElementById("ft-total").textContent = "";
        } else {
            showFtEmpty("Aucun résultat", `Aucune correspondance pour « ${q} ».`);
        }
        return;
    }

    document.getElementById("ft-total").textContent =
        `${data.total} résultat${data.total > 1 ? "s" : ""}`;
    renderFulltextGroups(data.hits);
    lucide.createIcons();
}

function showFtEmpty(title, text) {
    const empty = document.getElementById("ft-empty");
    empty.querySelector("h3").textContent = title;
    empty.querySelector("p").textContent = text;
    empty.classList.remove("hidden");
    document.getElementById("ft-total").textContent = "";
}

function renderFulltextGroups(hits) {
    // Score en % de pertinence : normalisé sur le score max de l'ensemble des hits
    const maxScore = hits.reduce((m, h) => Math.max(m, h.score || 0), 0) || 1;

    const byIndex = {};
    hits.forEach((h) => { (byIndex[h.index] = byIndex[h.index] || []).push(h); });

    const container = document.getElementById("ft-results");
    container.innerHTML = "";

    FT_GROUPS.forEach((g) => {
        const groupHits = byIndex[g.index];
        if (!groupHits || !groupHits.length) return;

        const section = document.createElement("div");
        section.className = "ft-group";
        section.innerHTML =
            `<div class="ft-group-header">` +
            `<span class="ft-emoji">${g.emoji}</span> ${g.label}` +
            `<span class="ft-count">${groupHits.length}</span></div>`;
        groupHits.forEach((h) => section.appendChild(ftItem(g, h, maxScore)));
        container.appendChild(section);
    });
}

function ftItem(group, hit, maxScore) {
    const src = hit.source || {};
    const pct = Math.max(1, Math.round((hit.score || 0) / maxScore * 100));

    let title = "", sub = "", href = "#";
    if (group.index === "osint_persons") {
        title = `${src.first_name || ""} ${src.last_name || ""}`.trim() || "Personne";
        sub = src.notes ? String(src.notes) : "";
        href = "/report?id=" + encodeURIComponent(hit.id);
    } else if (group.index === "osint_emails") {
        title = src.email || "Email";
        sub = "Source : " + (src.source || "—");
        href = "/dashboard?q=" + encodeURIComponent(src.email || "") + "&type=email";
    } else if (group.index === "osint_sites") {
        title = src.url || "Site";
        sub = src.server ? "Serveur : " + src.server : "";
        href = "/dashboard?q=" + encodeURIComponent(hostOf(src.url)) + "&type=domain";
    } else { // osint_search_results
        title = (src.entity || "Recherche") + (src.entity_type ? ` (${src.entity_type})` : "");
        sub = "Source : " + (src.source || "—");
        href = "/results?id=" + encodeURIComponent(src.search_id);
    }

    const a = document.createElement("a");
    a.className = "ft-item";
    a.href = href;

    // Fragments highlight : déjà échappés côté ES (encoder:html) → innerHTML sûr,
    // seules les balises <mark> subsistent pour le surlignage jaune.
    const frags = [];
    Object.values(hit.highlight || {}).forEach((arr) => arr.forEach((f) => frags.push(f)));
    const fragHtml = frags.length ? `<div class="ft-frag">${frags.join(" … ")}</div>` : "";

    a.innerHTML =
        `<div class="ft-ico ${group.key}">${group.emoji}</div>` +
        `<div class="ft-body">` +
        `<div class="ft-title">${escapeHtml(title)}</div>` +
        (sub ? `<div class="ft-sub">${escapeHtml(sub)}</div>` : "") +
        fragHtml +
        `</div>` +
        `<div class="ft-score"><div class="ft-pct">${pct}%</div><div class="ft-pct-label">pertinence</div></div>`;
    return a;
}

function hostOf(url) {
    try { return new URL(url).hostname; } catch { return url || ""; }
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


// =====================================================
// Énumération de pseudos (Module 15) — username.html
// SSE consommé via fetch+ReadableStream (EventSource ne
// peut pas porter le header Authorization / JWT).
// =====================================================

const UNAME_RE = /^[A-Za-z0-9._-]{3,30}$/;
const UNAME_CAT_LABELS = {
    dev: "Dev", social: "Social", gaming: "Gaming", forum: "Forum",
    creative: "Creative", professional: "Pro", dating: "Dating",
    crypto: "Crypto", news: "News",
};

// Icônes SVG inline (évite 157 appels lucide.createIcons pendant le stream)
const UNAME_SVG = {
    check: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    x: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    alert: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
};

let unameState = null;
let unameAbort = null;
let unameAudioCtx = null;

// "Jean", "Dupont" -> "jean.dupont" (sans accents, alphanumérique, 3–30 chars)
function usernameSlug(first, last) {
    const norm = (s) => (s || "")
        .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
        .toLowerCase().replace(/[^a-z0-9]/g, "");
    let slug = [norm(first), norm(last)].filter(Boolean).join(".");
    if (slug.length > 30) slug = slug.slice(0, 30);
    return slug.length >= 3 ? slug : "";
}

function initUsername() {
    setUserChip();
    initAddToInvestigationModal();

    document.getElementById("uname-form").addEventListener("submit", (e) => {
        e.preventDefault();
        startUsernameSearch(document.getElementById("uname-input").value);
    });

    // Filtres par catégorie (délégation — fonctionne même pendant le stream)
    document.getElementById("uname-filters").addEventListener("click", (e) => {
        const tab = e.target.closest(".tab");
        if (!tab) return;
        document.querySelectorAll("#uname-filters .tab").forEach((t) => t.classList.toggle("active", t === tab));
        if (unameState) unameState.catFilter = tab.getAttribute("data-cat");
        applyUnameFilters();
    });
    document.getElementById("uname-only-found").addEventListener("change", (e) => {
        if (unameState) unameState.onlyFound = e.target.checked;
        applyUnameFilters();
    });
    document.getElementById("uname-sort").addEventListener("change", (e) => {
        if (unameState) unameState.sort = e.target.value;
        applyUnameSort();
    });

    // Boutons du résumé final
    document.getElementById("uname-export").addEventListener("click", exportUsernameJson);
    document.getElementById("uname-copy").addEventListener("click", copyFoundUrls);
    document.getElementById("uname-add-inv").addEventListener("click", addUsernameSearchToInvestigation);

    // Construit les filtres de catégorie depuis le registre réel
    API.getUsernamePlatforms().then((meta) => {
        unamePlatformTotal = meta.total;
        const container = document.getElementById("uname-filters");
        meta.categories.forEach((cat) => {
            const b = document.createElement("button");
            b.className = "tab";
            b.setAttribute("data-cat", cat);
            b.textContent = UNAME_CAT_LABELS[cat] || cat;
            container.appendChild(b);
        });
    }).catch(() => { /* total sera fixé par l'event complete */ });

    // Préremplissage + lancement auto depuis la page rapport (/username?q=prenom.nom)
    const q = (new URLSearchParams(window.location.search).get("q") || "").trim();
    if (q) {
        document.getElementById("uname-input").value = q;
        if (UNAME_RE.test(q)) startUsernameSearch(q);
    }
}

// Persiste la recherche (résultats déjà collectés) puis la rattache via le modal partagé.
async function addUsernameSearchToInvestigation() {
    if (!unameState || !unameState.done) {
        toast("Lancez d'abord une recherche", "info");
        return;
    }
    const btn = document.getElementById("uname-add-inv");
    btn.disabled = true;
    try {
        const saved = await API.saveUsernameSearch(unameState.username, unameState.results.map((r) => ({
            platform: r.platform, url: r.url, category: r.category,
            found: r.found, response_time_ms: r.response_time_ms, error: r.error,
            variant: r.variant,
        })));
        openAddToInvestigationModal("username_search", saved.search_id, `Pseudo « ${unameState.username} »`);
    } catch (err) {
        toast(err.message, "error");
    } finally {
        btn.disabled = false;
    }
}

let unamePlatformTotal = 0;

function startUsernameSearch(raw) {
    const username = (raw || "").trim();
    const errEl = document.getElementById("uname-error");
    errEl.classList.add("hidden");

    if (!UNAME_RE.test(username)) {
        errEl.textContent = "Username invalide : 3 à 30 caractères, lettres/chiffres/._- uniquement.";
        errEl.classList.remove("hidden");
        return;
    }

    // Débloque l'audio (nécessite un geste utilisateur — le submit en est un)
    if (!unameAudioCtx) {
        try { unameAudioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch { unameAudioCtx = null; }
    }

    // Annule un éventuel stream précédent
    if (unameAbort) unameAbort.abort();
    unameAbort = new AbortController();

    // Réinitialise l'état + l'UI
    const activeTab = document.querySelector("#uname-filters .tab.active");
    unameState = {
        username, results: [], found: 0, checked: 0, errors: 0,
        total: unamePlatformTotal || 0,
        catFilter: activeTab ? activeTab.getAttribute("data-cat") : "",
        onlyFound: document.getElementById("uname-only-found").checked,
        sort: document.getElementById("uname-sort").value,
        soundOn: document.getElementById("uname-sound").checked,
        done: false,
    };

    document.getElementById("uname-grid").innerHTML = "";
    document.getElementById("uname-empty").classList.add("hidden");
    document.getElementById("uname-summary").classList.add("hidden");
    document.getElementById("uname-stats").classList.remove("hidden");
    document.getElementById("uname-toolbar").classList.remove("hidden");
    document.getElementById("uname-found").textContent = "0";
    document.getElementById("uname-checked").textContent = "0";
    document.getElementById("uname-total").textContent = unameState.total || "…";
    document.getElementById("uname-errors").textContent = "0";
    document.getElementById("uname-errors").parentElement.classList.add("hidden");
    document.getElementById("uname-progress-bar").style.width = "0%";
    setUnameLoading(true);

    streamUsernameCheck(username, unameAbort.signal);
}

function setUnameLoading(loading) {
    const btn = document.getElementById("uname-submit");
    btn.disabled = loading;
    btn.querySelector("span").textContent = loading ? "Recherche…" : "Rechercher";
    document.getElementById("uname-input").disabled = loading;
}

async function streamUsernameCheck(username, signal) {
    let resp;
    try {
        resp = await fetch("/api/username/check?username=" + encodeURIComponent(username), {
            headers: { Authorization: "Bearer " + API.getToken() },
            signal,
        });
    } catch (err) {
        if (err.name === "AbortError") return;
        setUnameLoading(false);
        toast("Impossible de contacter le serveur", "error");
        return;
    }

    if (resp.status === 401 || resp.status === 403) { API.clearToken(); window.location.href = "/"; return; }
    if (resp.status === 429) { setUnameLoading(false); toast("Une recherche toutes les 30 secondes maximum", "error"); return; }
    if (!resp.ok) {
        setUnameLoading(false);
        const data = await resp.json().catch(() => ({}));
        const errEl = document.getElementById("uname-error");
        errEl.textContent = data.detail || "Erreur serveur (" + resp.status + ")";
        errEl.classList.remove("hidden");
        return;
    }

    // Lecture du flux SSE : découpe sur les doubles sauts de ligne, parse "data: {...}"
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let sep;
            while ((sep = buffer.indexOf("\n\n")) !== -1) {
                const chunk = buffer.slice(0, sep);
                buffer = buffer.slice(sep + 2);
                const line = chunk.split("\n").find((l) => l.startsWith("data: "));
                if (!line) continue;
                let event;
                try { event = JSON.parse(line.slice(6)); } catch { continue; }
                if (event.type === "complete") finishUsernameSearch(event);
                else onUsernameResult(event);
            }
        }
        // Flux terminé sans event "complete" (coupure serveur/réseau)
        if (unameState && !unameState.done) {
            setUnameLoading(false);
            toast("Flux interrompu avant la fin — relancez la recherche", "info");
        }
    } catch (err) {
        if (err.name === "AbortError") return;  // nouvelle recherche ou navigation
        if (unameState && !unameState.done) {
            setUnameLoading(false);
            toast("Connexion interrompue", "error");
        }
    }
}

function onUsernameResult(result) {
    if (!unameState) return;
    unameState.results.push(result);
    // "checked" ne progresse que sur le 1er événement d'une plateforme (is_primary) : une
    // même plateforme peut désormais produire plusieurs comptes trouvés (variantes
    // différentes), qu'on ne veut pas compter plusieurs fois dans la progression sur 157.
    if (result.is_primary !== false) unameState.checked += 1;
    if (result.error) unameState.errors += 1;
    else if (result.found) unameState.found += 1;

    // Compteurs
    document.getElementById("uname-found").textContent = unameState.found;
    document.getElementById("uname-checked").textContent = unameState.checked;
    if (unameState.errors > 0) {
        document.getElementById("uname-errors").textContent = unameState.errors;
        document.getElementById("uname-errors").parentElement.classList.remove("hidden");
    }
    // Barre de progression
    if (unameState.total) {
        const pct = Math.min(100, Math.round(unameState.checked / unameState.total * 100));
        document.getElementById("uname-progress-bar").style.width = pct + "%";
    }

    // Badge
    const node = buildUnameBadge(result);
    result._node = node;
    applyUnameFilterToNode(node, result);
    document.getElementById("uname-grid").appendChild(node);
    if (unameState.sort !== "response") applyUnameSort();

    // Son de notification si trouvé
    if (result.found && unameState.soundOn) playFoundBeep();
}

function buildUnameBadge(result) {
    const state = result.error ? "errored" : (result.found ? "found" : "notfound");
    const icon = result.error ? UNAME_SVG.alert : (result.found ? UNAME_SVG.check : UNAME_SVG.x);
    const cat = UNAME_CAT_LABELS[result.category] || result.category;
    // La variante trouvée peut différer du pseudo saisi (recherche élargie) — on l'affiche
    // pour ne pas laisser croire que c'est le pseudo exact qui a été confirmé.
    const isVariant = result.found && result.variant && unameState
        && result.variant.toLowerCase() !== unameState.username.toLowerCase();

    const el = document.createElement(result.found ? "a" : "div");
    el.className = "uname-badge " + state;
    el.setAttribute("data-category", result.category);
    el.setAttribute("data-found", result.found ? "1" : "0");
    if (result.found) { el.href = result.url; el.target = "_blank"; el.rel = "noopener"; }
    el.innerHTML =
        `<div class="uname-head">${icon}<span class="uname-name">${escapeHtml(result.platform)}</span></div>` +
        `<div class="uname-cat">${escapeHtml(cat)}</div>` +
        (isVariant ? `<div class="uname-variant">trouvé comme « ${escapeHtml(result.variant)} »</div>` : "");
    return el;
}

function applyUnameFilterToNode(node, result) {
    if (!unameState) return;
    const hideByCat = unameState.catFilter && result.category !== unameState.catFilter;
    const hideByFound = unameState.onlyFound && !result.found;
    node.classList.toggle("filtered", Boolean(hideByCat || hideByFound));
}

function applyUnameFilters() {
    if (!unameState) return;
    unameState.results.forEach((r) => { if (r._node) applyUnameFilterToNode(r._node, r); });
}

function applyUnameSort() {
    if (!unameState) return;
    const grid = document.getElementById("uname-grid");
    const arr = unameState.results.filter((r) => r._node);
    const cmp = {
        alpha: (a, b) => a.platform.localeCompare(b.platform),
        category: (a, b) => (a.category.localeCompare(b.category)) || a.platform.localeCompare(b.platform),
        response: () => 0,  // ordre d'arrivée = ordre du tableau
    }[unameState.sort] || (() => 0);
    const ordered = unameState.sort === "response" ? arr : [...arr].sort(cmp);
    ordered.forEach((r) => grid.appendChild(r._node));  // appendChild déplace le nœud
}

function finishUsernameSearch(summary) {
    if (!unameState) return;
    unameState.done = true;
    unameState.total = summary.total || unameState.checked;
    if (typeof summary.found === "number") unameState.found = summary.found;
    if (typeof summary.errors === "number") unameState.errors = summary.errors;

    document.getElementById("uname-total").textContent = unameState.total;
    document.getElementById("uname-found").textContent = unameState.found;
    document.getElementById("uname-checked").textContent = unameState.checked;
    document.getElementById("uname-progress-bar").style.width = "100%";
    setUnameLoading(false);

    document.getElementById("uname-summary-title").textContent =
        `${unameState.found} compte${unameState.found > 1 ? "s" : ""} trouvé${unameState.found > 1 ? "s" : ""} sur ${unameState.checked} vérifié${unameState.checked > 1 ? "s" : ""}`;
    document.getElementById("uname-summary-sub").textContent =
        `Pseudo « ${unameState.username} » · ${unameState.errors} erreur${unameState.errors > 1 ? "s" : ""}`;
    document.getElementById("uname-summary").classList.remove("hidden");
}

function foundUrls() {
    return (unameState ? unameState.results : []).filter((r) => r.found).map((r) => r.url);
}

function exportUsernameJson() {
    if (!unameState) return;
    downloadJson(`usernames-${unameState.username}.json`, {
        username: unameState.username,
        found: unameState.found,
        checked: unameState.checked,
        errors: unameState.errors,
        results: unameState.results.map((r) => ({
            platform: r.platform, url: r.url, category: r.category,
            found: r.found, response_time_ms: r.response_time_ms, error: r.error,
            variant: r.variant,
        })),
    });
}

function copyFoundUrls() {
    const urls = foundUrls();
    if (!urls.length) { toast("Aucune URL trouvée à copier", "info"); return; }
    navigator.clipboard.writeText(urls.join("\n"))
        .then(() => toast(`${urls.length} URL${urls.length > 1 ? "s" : ""} copiée${urls.length > 1 ? "s" : ""}`, "success"))
        .catch(() => toast("Copie impossible", "error"));
}

function playFoundBeep() {
    if (!unameAudioCtx) return;
    try {
        const o = unameAudioCtx.createOscillator();
        const g = unameAudioCtx.createGain();
        o.type = "sine";
        o.frequency.value = 880;
        o.connect(g); g.connect(unameAudioCtx.destination);
        const t = unameAudioCtx.currentTime;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.14, t + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.15);
        o.start(t); o.stop(t + 0.16);
    } catch { /* audio indisponible */ }
}
