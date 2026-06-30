/**
 * Client API centralisé — OSINT Platform
 * JWT stocké en mémoire uniquement (jamais localStorage)
 */

const API = (() => {
    let _token = null;

    function setToken(token) {
        _token = token;
    }

    function getToken() {
        return _token;
    }

    function clearToken() {
        _token = null;
    }

    function isAuthenticated() {
        return _token !== null;
    }

    function _headers(extra = {}) {
        const h = { ...extra };
        if (_token) {
            h["Authorization"] = "Bearer " + _token;
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

    async function listCorrelations(limit = 20, offset = 0) {
        return _request("GET", "/api/correlations?limit=" + limit + "&offset=" + offset);
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
        listCorrelations,
    };
})();
