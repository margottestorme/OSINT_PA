/**
 * Client API centralisé — OSINT Platform
 * JWT stocké en sessionStorage (persiste entre navigations, effacé à la fermeture de l'onglet)
 */

const TOKEN_KEY = "osint_token";

const API = (() => {
    function setToken(token) {
        sessionStorage.setItem(TOKEN_KEY, token);
    }

    function getToken() {
        return sessionStorage.getItem(TOKEN_KEY);
    }

    function clearToken() {
        sessionStorage.removeItem(TOKEN_KEY);
    }

    function isAuthenticated() {
        return sessionStorage.getItem(TOKEN_KEY) !== null;
    }

    function _headers(extra = {}) {
        const h = { ...extra };
        const token = getToken();
        if (token) {
            h["Authorization"] = "Bearer " + token;
        }
        return h;
    }

    // Normalise le `detail` d'une réponse d'erreur FastAPI en message affichable.
    // - detail = tableau (erreurs de validation 422) → premier message lisible
    // - detail = chaîne (HTTPException) → utilisé tel quel
    // - sinon → repli générique avec le code HTTP
    function extractErrorMessage(data, status) {
        const detail = data && data.detail;
        if (Array.isArray(detail)) {
            const first = detail[0];
            return (first && (first.msg || first.message)) || "Erreur de validation";
        }
        if (typeof detail === "string") {
            return detail;
        }
        return "Erreur serveur (" + status + ")";
    }

    async function _request(method, url, options = {}) {
        const config = {
            method,
            headers: _headers(options.headers || {}),
        };
        if (options.body) {
            config.body = options.body;
        }
        if (options.json) {
            config.headers["Content-Type"] = "application/json";
            config.body = JSON.stringify(options.json);
        }

        let resp;
        try {
            resp = await fetch(url, config);
        } catch {
            throw new Error("Impossible de contacter le serveur");
        }

        if ((resp.status === 401 || resp.status === 403) && !options.skipAuthRedirect) {
            clearToken();
            window.location.href = "/";
            throw new Error("Session expirée");
        }

        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(extractErrorMessage(data, resp.status));
        }

        return resp.json();
    }

    // --- Auth ---
    async function login(username, password) {
        const data = await _request("POST", "/api/auth/login", {
            json: { username, password },
            skipAuthRedirect: true,
        });
        setToken(data.access_token);
        return data;
    }

    function logout() {
        clearToken();
        window.location.href = "/";
    }

    // --- Recherche OSINT ---
    async function search(query, queryType) {
        return _request("POST", "/api/search", {
            json: { query, query_type: queryType },
        });
    }

    // --- Recherche full-text (Elasticsearch) ---
    async function fulltextSearch(q, type = "all", limit = 50, offset = 0) {
        const params = new URLSearchParams({ q, type, limit, offset });
        return _request("GET", "/api/search/fulltext?" + params.toString());
    }

    async function searchStatus() {
        return _request("GET", "/api/search/status");
    }

    // --- Facial ---
    async function uploadFace(file) {
        const formData = new FormData();
        formData.append("file", file);
        return _request("POST", "/api/faces/upload", { body: formData });
    }

    async function listFacePersons(limit = 20, offset = 0) {
        return _request("GET", "/api/faces/persons?limit=" + limit + "&offset=" + offset);
    }

    // --- Données ---
    async function listPersons(limit = 20, offset = 0) {
        return _request("GET", "/api/persons?limit=" + limit + "&offset=" + offset);
    }

    async function getPersonReport(personId, limit = 20, offset = 0) {
        return _request("GET", "/api/persons/" + personId + "/report?limit=" + limit + "&offset=" + offset);
    }

    async function listHistory(limit = 20, offset = 0) {
        return _request("GET", "/api/history?limit=" + limit + "&offset=" + offset);
    }

    async function getSearch(searchId) {
        return _request("GET", "/api/history/" + searchId);
    }

    async function getSearchResults(searchId, limit = 100, offset = 0) {
        return _request("GET", "/api/history/" + searchId + "/results?limit=" + limit + "&offset=" + offset);
    }

    async function listCorrelations(limit = 20, offset = 0) {
        return _request("GET", "/api/correlations?limit=" + limit + "&offset=" + offset);
    }

    async function listEmails(limit = 200, offset = 0) {
        return _request("GET", "/api/emails?limit=" + limit + "&offset=" + offset);
    }

    async function listSites(limit = 200, offset = 0) {
        return _request("GET", "/api/sites?limit=" + limit + "&offset=" + offset);
    }

    // --- Stream webcam (analyse côté client) ---
    async function streamInfo() {
        return _request("GET", "/api/stream/info");
    }
    async function streamStatus() {
        return _request("GET", "/api/stream/status");
    }
    async function streamAnalyze(frame) {
        return _request("POST", "/api/stream/analyze", { json: { frame } });
    }

    // --- Stats & édition ---
    async function getStats() {
        return _request("GET", "/api/stats");
    }

    async function updatePerson(personId, body) {
        return _request("PATCH", "/api/persons/" + personId, { json: body });
    }

    async function addPersonEmail(personId, email, source = "manual") {
        return _request("POST", "/api/persons/" + personId + "/emails", { json: { email, source } });
    }

    async function enrichPerson(personId) {
        return _request("POST", "/api/persons/" + personId + "/enrich");
    }

    // --- Enquêtes (Module 14) ---
    async function listInvestigations(status = "") {
        const params = new URLSearchParams();
        if (status) params.set("status", status);
        const qs = params.toString();
        return _request("GET", "/api/investigations" + (qs ? "?" + qs : ""));
    }

    async function getInvestigation(id) {
        return _request("GET", "/api/investigations/" + id);
    }

    async function createInvestigation(body) {
        return _request("POST", "/api/investigations", { json: body });
    }

    async function updateInvestigation(id, body) {
        return _request("PATCH", "/api/investigations/" + id, { json: body });
    }

    async function deleteInvestigation(id) {
        return _request("DELETE", "/api/investigations/" + id);
    }

    async function addInvestigationEntity(id, body) {
        return _request("POST", "/api/investigations/" + id + "/entities", { json: body });
    }

    async function removeInvestigationEntity(id, linkId) {
        return _request("DELETE", "/api/investigations/" + id + "/entities/" + linkId);
    }

    async function getInvestigationTimeline(id) {
        return _request("GET", "/api/investigations/" + id + "/timeline");
    }

    // --- Énumération de pseudos (Module 15) ---
    async function getUsernamePlatforms() {
        return _request("GET", "/api/username/platforms");
    }
    // Persiste des résultats déjà collectés (issus du stream) sans re-énumérer.
    async function saveUsernameSearch(username, results) {
        return _request("POST", "/api/username/save", { json: { username, results } });
    }
    // Le stream SSE est consommé directement via fetch+ReadableStream côté page
    // (EventSource ne peut pas porter le header Authorization), en réutilisant getToken().

    // Export PDF : réponse binaire → on récupère un Blob (pas du JSON) + le nom de fichier
    async function exportInvestigationPdf(id) {
        let resp;
        try {
            resp = await fetch("/api/investigations/" + id + "/export/pdf", { headers: _headers() });
        } catch {
            throw new Error("Impossible de contacter le serveur");
        }
        if (resp.status === 401 || resp.status === 403) {
            clearToken();
            window.location.href = "/";
            throw new Error("Session expirée");
        }
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(extractErrorMessage(data, resp.status));
        }
        const blob = await resp.blob();
        const cd = resp.headers.get("Content-Disposition") || "";
        const m = cd.match(/filename="?([^"]+)"?/);
        return { blob, filename: m ? m[1] : "rapport_enquete_" + id + ".pdf" };
    }

    return {
        setToken,
        getToken,
        clearToken,
        isAuthenticated,
        login,
        logout,
        search,
        fulltextSearch,
        searchStatus,
        uploadFace,
        listFacePersons,
        listPersons,
        getPersonReport,
        listHistory,
        getSearch,
        getSearchResults,
        listCorrelations,
        listEmails,
        listSites,
        streamInfo,
        streamStatus,
        streamAnalyze,
        getStats,
        updatePerson,
        addPersonEmail,
        enrichPerson,
        listInvestigations,
        getInvestigation,
        createInvestigation,
        updateInvestigation,
        deleteInvestigation,
        addInvestigationEntity,
        removeInvestigationEntity,
        getInvestigationTimeline,
        exportInvestigationPdf,
        getUsernamePlatforms,
        saveUsernameSearch,
    };
})();
