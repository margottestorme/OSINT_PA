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
            throw new Error(data.detail || "Erreur serveur (" + resp.status + ")");
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

    // --- Stream webcam ---
    async function streamInfo() {
        return _request("GET", "/api/stream/info");
    }
    async function streamStart() {
        return _request("GET", "/api/stream/start");
    }
    async function streamStop() {
        return _request("GET", "/api/stream/stop");
    }
    async function streamStatus() {
        return _request("GET", "/api/stream/status");
    }

    // --- Stats & édition ---
    async function getStats() {
        return _request("GET", "/api/stats");
    }

    async function updatePerson(personId, body) {
        return _request("PATCH", "/api/persons/" + personId, { json: body });
    }

    return {
        setToken,
        getToken,
        clearToken,
        isAuthenticated,
        login,
        logout,
        search,
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
        streamStart,
        streamStop,
        streamStatus,
        getStats,
        updatePerson,
    };
})();
