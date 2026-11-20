/* Existing TAR UMT session endpoints only. Tokens stay in the app's memory. */
(function (global) {
    "use strict";
    async function sessionRequest(base, path, token) {
        if (!token) throw new Error("No active TAR UMT session. Connect first.");
        const response = await global.fetch(base + path, {method:"POST", credentials:"omit", cache:"no-store", redirect:"error",
            headers:{"Content-Type":"application/json"}, body:JSON.stringify({token})});
        if (!response.headers.get("content-type")?.includes("application/json")) throw new Error("Invalid TAR UMT response.");
        let payload;
        try { payload = await response.json(); }
        catch (_) { throw new Error("Invalid TAR UMT response."); }
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
        if (data.application) {
            const a=data.application;
            let original;
            try { original=JSON.parse(a.reconstruction_json); }
            catch (_) { throw new Error('Invalid structured assessment binding.'); }
            if(data.canonical?.source.route!=='tarumt_html' || a.pipeline!=='tarumt-structured-review-v1' ||
                JSON.stringify(a.current_packet)!==JSON.stringify(data.canonical) ||
                a.reconstruction?.canonical_output?.profile_id!==a.pipeline ||
                JSON.stringify(original)!==JSON.stringify(a.reconstruction))throw new Error('Invalid structured assessment binding.');
            global.STAIRUpload.validatePolicy({profile:'m5-source-review-v1',registered_routing_ready:false,decisions:a.policy},data.canonical.events);
        }
        return data.canonical || null;
    }
    global.STAIRIntranet = Object.freeze({sessionRequest, structuredPacket});
})(globalThis);
