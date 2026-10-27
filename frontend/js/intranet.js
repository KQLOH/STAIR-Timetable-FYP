/* Existing TAR UMT session endpoints only. Tokens stay in the app's memory. */
(function (global) {
    "use strict";
    async function sessionRequest(base, path, token) {
        if (!token) throw new Error("No active TAR UMT session. Connect first.");
        const response = await global.fetch(base + path, {method:"POST", credentials:"omit",
            headers:{"Content-Type":"application/json"}, body:JSON.stringify({token})});
        if (!response.headers.get("content-type")?.includes("application/json")) throw new Error("Invalid TAR UMT response.");
        const payload = await response.json();
        // Do not include remote responses, credentials or tokens in errors/logs.
        if (!response.ok) {
            const error = new Error(response.status === 401 ? "TAR UMT session expired. Reconnect when needed." : `TAR UMT request failed (HTTP ${response.status}).`);
            error.status = response.status;
            throw error;
        }
        if (payload.success !== true) throw new Error("TAR UMT request was not confirmed.");
        return payload;
    }
    function structuredPacket(data) {
        if (!Array.isArray(data?.events)) throw new Error("Invalid structured timetable response.");
        if (data.canonical) global.STAIRUpload.validatePacket(data.canonical);
        return data.canonical || null;
    }
    global.STAIRIntranet = Object.freeze({sessionRequest, structuredPacket});
})(globalThis);
