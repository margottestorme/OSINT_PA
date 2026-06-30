/**
 * Logique de l'interface — OSINT Platform
 */

const PAGE_SIZE = 20;

document.addEventListener("DOMContentLoaded", () => {
    const loginForm = document.getElementById("login-form");
    const logoutBtn = document.getElementById("logout-btn");

    // --- Page de login ---
    if (loginForm) {
        loginForm.addEventListener("submit", handleLogin);
    }

    // --- Pages protégées (dashboard + rapport) ---
    if (logoutBtn) {
        if (!API.isAuthenticated()) {
            window.location.href = "/";
            return;
        }
        logoutBtn.addEventListener("click", () => API.logout());

        if (document.getElementById("upload-form")) {
            initDashboard();
        }
        if (document.getElementById("report-content")) {
            initReport();
        }
    }
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

    errorEl.hidden = true;
    btn.disabled = true;
    btn.textContent = "Connexion...";

    try {
        await API.login(username, password);
        window.location.href = "/dashboard";
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.hidden = false;
    } finally {
        btn.disabled = false;
        btn.textContent = "Se connecter";
    }
}


// =====================================================
// Dashboard — initialisation
// =====================================================

function initDashboard() {
    document.getElementById("upload-form").addEventListener("submit", handleUpload);
    document.getElementById("search-form").addEventListener("submit", handleSearch);
    document.getElementById("history-refresh").addEventListener("click", () => loadHistory(0));

    initPagination("persons");
    initPagination("history");

    loadPersons(0);
    loadHistory(0);
}


// =====================================================
// Section 1 : Upload image
// =====================================================

async function handleUpload(e) {
    e.preventDefault();

    const btn = document.getElementById("upload-btn");
    const errorEl = document.getElementById("upload-error");
    const resultEl = document.getElementById("upload-result");
    const fileInput = document.getElementById("face-file");

    errorEl.classList.add("hidden");
    resultEl.classList.add("hidden");

    const file = fileInput.files[0];
    if (!file) return;

    const allowed = ["image/jpeg", "image/png"];
    if (!allowed.includes(file.type)) {
        errorEl.textContent = "Format non supporté. Utilisez JPG ou PNG.";
        errorEl.classList.remove("hidden");
        return;
    }

    if (file.size > 5 * 1024 * 1024) {
        errorEl.textContent = "Fichier trop volumineux (max 5 Mo).";
        errorEl.classList.remove("hidden");
        return;
    }

    btn.disabled = true;
    btn.textContent = "Analyse...";

    try {
        const data = await API.uploadFace(file);
        const status = data.is_new ? "Nouvelle personne créée" : "Personne existante reconnue";
        resultEl.innerHTML =
            "<strong>" + status + "</strong><br>" +
            "Person ID : " + data.person_id + "<br>" +
            "Score de confiance : " + (data.confidence_score * 100).toFixed(1) + "%<br>" +
            "Visages détectés : " + data.faces_count;
        resultEl.classList.remove("hidden");
        fileInput.value = "";
        loadPersons(0);
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    } finally {
        btn.disabled = false;
        btn.textContent = "Analyser";
    }
}


// =====================================================
// Section 2 : Recherche OSINT
// =====================================================

async function handleSearch(e) {
    e.preventDefault();

    const btn = document.getElementById("search-btn");
    const errorEl = document.getElementById("search-error");
    const resultEl = document.getElementById("search-result");
    const query = document.getElementById("search-query").value.trim();
    const queryType = document.getElementById("search-type").value;

    errorEl.classList.add("hidden");
    resultEl.classList.add("hidden");

    if (!query) return;

    btn.disabled = true;
    btn.textContent = "Recherche...";

    try {
        const data = await API.search(query, queryType);
        resultEl.innerHTML =
            "<strong>" + data.message + "</strong><br>" +
            "Requête : " + escapeHtml(data.query) + " (" + data.query_type + ")";
        resultEl.classList.remove("hidden");
        document.getElementById("search-query").value = "";
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    } finally {
        btn.disabled = false;
        btn.textContent = "Rechercher";
    }
}


// =====================================================
// Section 3 : Liste des personnes
// =====================================================

let personsOffset = 0;

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

        if (data.length === 0 && offset === 0) {
            empty.classList.remove("hidden");
        } else {
            body.innerHTML = "";
            for (const p of data) {
                const tr = document.createElement("tr");
                tr.innerHTML =
                    "<td>" + p.id + "</td>" +
                    "<td>" + escapeHtml(p.first_name) + "</td>" +
                    "<td>" + escapeHtml(p.last_name) + "</td>" +
                    "<td>" + (p.confidence_score * 100).toFixed(1) + "%</td>" +
                    "<td>" + formatDate(p.created_at) + "</td>" +
                    '<td><button class="btn-small" data-person-id="' + p.id + '">Voir rapport</button></td>';
                body.appendChild(tr);
            }
            body.addEventListener("click", onPersonClick);
            table.classList.remove("hidden");
        }

        updatePagination("persons", offset, data.length);
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    } finally {
        loading.classList.add("hidden");
    }
}

function onPersonClick(e) {
    const btn = e.target.closest("[data-person-id]");
    if (!btn) return;
    const personId = btn.getAttribute("data-person-id");
    window.location.href = "/report?id=" + personId;
}


// =====================================================
// Section 4 : Historique des recherches
// =====================================================

let historyOffset = 0;

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
                tr.innerHTML =
                    "<td>" + escapeHtml(h.query) + "</td>" +
                    "<td>" + escapeHtml(h.query_type) + "</td>" +
                    "<td>" + h.result_count + "</td>" +
                    "<td>" + formatDate(h.created_at) + "</td>";
                body.appendChild(tr);
            }
            table.classList.remove("hidden");
        }

        updatePagination("history", offset, data.length);
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    } finally {
        loading.classList.add("hidden");
    }
}


// =====================================================
// Pagination
// =====================================================

function initPagination(prefix) {
    document.getElementById(prefix + "-prev").addEventListener("click", () => {
        const current = prefix === "persons" ? personsOffset : historyOffset;
        const newOffset = Math.max(0, current - PAGE_SIZE);
        if (prefix === "persons") loadPersons(newOffset);
        else loadHistory(newOffset);
    });
    document.getElementById(prefix + "-next").addEventListener("click", () => {
        const current = prefix === "persons" ? personsOffset : historyOffset;
        if (prefix === "persons") loadPersons(current + PAGE_SIZE);
        else loadHistory(current + PAGE_SIZE);
    });
}

function updatePagination(prefix, offset, count) {
    const prev = document.getElementById(prefix + "-prev");
    const next = document.getElementById(prefix + "-next");
    const info = document.getElementById(prefix + "-page-info");

    const page = Math.floor(offset / PAGE_SIZE) + 1;
    prev.disabled = offset === 0;
    next.disabled = count < PAGE_SIZE;
    info.textContent = "Page " + page;
}


// =====================================================
// Page rapport personne
// =====================================================

function initReport() {
    const params = new URLSearchParams(window.location.search);
    const personId = params.get("id");

    if (!personId || isNaN(Number(personId))) {
        showReportError("Identifiant de personne manquant ou invalide.");
        return;
    }

    loadReport(Number(personId));
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

        // Infos personne
        document.getElementById("person-info").innerHTML =
            '<div class="field"><strong>ID :</strong> ' + data.person.id + '</div>' +
            '<div class="field"><strong>Prénom :</strong> ' + escapeHtml(data.person.first_name) + '</div>' +
            '<div class="field"><strong>Nom :</strong> ' + escapeHtml(data.person.last_name) + '</div>' +
            '<div class="field"><strong>Score :</strong> ' + (data.person.confidence_score * 100).toFixed(1) + '%</div>' +
            '<div class="field"><strong>Date :</strong> ' + formatDate(data.person.created_at) + '</div>';

        // Emails
        renderReportTable("emails", data.emails, function (e) {
            return "<td>" + escapeHtml(e.email) + "</td>" +
                   "<td>" + escapeHtml(e.source) + "</td>" +
                   "<td>" + formatDate(e.created_at) + "</td>";
        });

        // Sites
        renderReportTable("sites", data.sites, function (s) {
            return "<td>" + escapeHtml(s.url) + "</td>" +
                   "<td>" + escapeHtml(s.server || "—") + "</td>" +
                   "<td>" + formatDate(s.discovered_at) + "</td>";
        });

        // Corrélations
        renderReportTable("correlations", data.correlations, function (c) {
            return "<td>" + (c.email_id || "—") + "</td>" +
                   "<td>" + (c.site_id || "—") + "</td>" +
                   "<td>" + (c.score * 100).toFixed(1) + "%</td>" +
                   "<td>" + formatDate(c.created_at) + "</td>";
        });

        content.classList.remove("hidden");
    } catch (err) {
        showReportError(err.message);
    } finally {
        loading.classList.add("hidden");
    }
}

function renderReportTable(prefix, items, rowFn) {
    const table = document.getElementById(prefix + "-table");
    const empty = document.getElementById(prefix + "-empty");
    const body = document.getElementById(prefix + "-body");

    if (items.length === 0) {
        table.classList.add("hidden");
        empty.classList.remove("hidden");
        return;
    }

    body.innerHTML = "";
    for (const item of items) {
        const tr = document.createElement("tr");
        tr.innerHTML = rowFn(item);
        body.appendChild(tr);
    }
    table.classList.remove("hidden");
    empty.classList.add("hidden");
}

function showReportError(message) {
    const errorEl = document.getElementById("report-error");
    const loading = document.getElementById("report-loading");
    errorEl.textContent = message;
    errorEl.classList.remove("hidden");
    loading.classList.add("hidden");
}


// =====================================================
// Utilitaires
// =====================================================

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

function formatDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleDateString("fr-FR") + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}
