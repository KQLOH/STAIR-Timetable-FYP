/* Loaded only by the opt-in local Windows host; no credentials in web storage. */
(() => {
    'use strict';
    const origin = 'http://127.0.0.1:8020';
    if (location.origin !== origin) return;
    document.addEventListener('DOMContentLoaded', () => {
        const panel = document.createElement('section');
        panel.className = 'panel';panel.setAttribute('aria-label', 'Local student review access');
        const title = document.createElement('h3');title.textContent = 'Review your own timetable';
        const accessDetails = document.createElement('details'), summary = document.createElement('summary');summary.textContent = 'Editing access: instructions and Advanced Details';accessDetails.append(summary);
        const note = document.createElement('p');note.textContent = 'Your Windows logon and an explicit confirmation in the local host console authorize only the selected source. After a restart, reconnect and reselect the matching original file to inspect image crops.';
        const request = document.createElement('button');request.type = 'button';request.className = 'button secondary';request.id = 'requestLocalReview';request.textContent = 'Request access to this timetable';
        const connect = document.createElement('button');connect.type = 'button';connect.className = 'button secondary';connect.id = 'connectLocalReview';connect.textContent = 'Connect approved review';connect.disabled = true;
        const instructions = document.createElement('p');instructions.id = 'localReviewInstructions';
        const status = document.createElement('p');status.id = 'localReviewStatus';status.setAttribute('role', 'status');status.textContent = 'Not authorized. Upload your original timetable first.';
        let binding = '', sourceKey = '';
        request.addEventListener('click', async () => {
            request.disabled = true;
            try {
                const source = reviewSource().packet?.source;
                if (!source || !state.upload?.file) throw Error('Upload the original file before requesting access.');
                if(reviewController.session&&reviewController.transport&&reviewController.session.source_bindings?.some(([id,hash])=>id===source.source_document_id&&hash===source.content_sha256)){await reviewController.refreshQueue();if(reviewController.session){await reviewController.restoreSavedSource?.();status.textContent='Saved reviews are already connected for this source. Your checklist and unsaved entries are retained.';if(typeof scheduleCheckController!=='undefined')scheduleCheckController?.syncReviewState();return;}}
                reviewController.forget();binding = '';connect.disabled = true;
                const response = await fetch(origin + '/__local_review__/pending', {method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
                    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({source_document_id: source.source_document_id, content_sha256: source.content_sha256})});
                if (!response.ok) throw Error('The local host refused this request.');
                const pending = await response.json();
                if (pending.authorized !== false || !/^[A-Za-z0-9_-]{43}$/.test(pending.binding) || !/^[A-Za-z0-9_-]{24}$/.test(pending.nonce)) throw Error('Invalid pending channel.');
                binding = pending.binding;sourceKey = source.source_document_id + ':' + source.content_sha256;
                connect.disabled = false;
                accessDetails.open = true;status.textContent = 'Access requested. Follow the instructions below to personally run the consent command, then connect your approved review.';
                instructions.textContent = 'In your local host console, personally run: authorize ' + pending.nonce + ' "<path to the original timetable file>" within 120 seconds. Source SHA-256: ' + source.content_sha256 + '. This does not submit any field correction.';
            } catch (error) {status.textContent = error.message;}
            finally {request.disabled = false;}
        });
        connect.addEventListener('click', async () => {
            const source = reviewSource().packet?.source;
            if (!binding || sourceKey !== source?.source_document_id + ':' + source?.content_sha256) {
                status.textContent = 'The source changed. Request fresh access.';return;
            }
            const credential = binding;
            const alreadyConnected=!!reviewController.session&&!!reviewController.transport&&reviewController.session.source_bindings?.some(([id,hash])=>id===source.source_document_id&&hash===source.content_sha256);
            if(!alreadyConnected)reviewController.attachTrustedHostTransport(async (url, options = {}) => {
                const target = new URL(url), method = (options.method || 'GET').toUpperCase();
                if (target.origin !== origin || !target.pathname.startsWith('/api/review/') || target.username || target.password || target.hash || target.href.includes('%') || !['GET', 'POST'].includes(method)) throw Error('Review transport refused.');
                const headers = new Headers(options.headers || {});
                for (const name of ['Authorization', 'Cookie', 'X-STAIR-Review-Binding']) if (headers.has(name)) throw Error('Credential replacement refused.');
                headers.set('X-STAIR-Review-Binding', credential);
                return fetch(target.href, {...options, method, headers, credentials: 'same-origin', redirect: 'error', cache: 'no-store'});
            });
            await reviewController.refreshQueue();
            const restored=await reviewController.restoreSavedSource?.();
            status.textContent = restored?.status==='RESTORED' ? 'Saved reviews reconnected. Latest saved class details are now shown. Editing and calendar approval remain separate.' : reviewController.session ? 'Editing enabled for this timetable. Choose Edit class to correct a detail. Saved corrections survive restart; each save needs your explicit confirmation.' : 'No authorization confirmed. Check the host console; request fresh access if expired.';
        });
        accessDetails.append(note, instructions);panel.append(title, request, connect, status, accessDetails);
        (document.getElementById?.('view-review') || document.querySelector('.main-area')).prepend(panel);
    });
})();
