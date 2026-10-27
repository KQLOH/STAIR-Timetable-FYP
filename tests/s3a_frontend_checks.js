/* Independent synthetic DOM tests. Layout measurements use the real browser separately. */
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const nodes = new Map(), documentListeners = new Map();
let narrow = true;
const media = {get matches() { return narrow; }, listeners: [], addEventListener(_, f) { this.listeners.push(f); }};
class Element {
    constructor(id = '', tag = 'button') {
        this.id = id; this.tagName = tag; this.children = []; this.parent = null;
        this.dataset = {}; this.attrs = {}; this.listeners = {}; this.style = {};
        this.inert = false; this.disabled = false; this.isConnected = true;
        this.value = ''; this.innerHTML = ''; this.textContent = '';
        const classes = new Set();
        this.classList = {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x),
            toggle(x, force) { const yes = force === undefined ? !classes.has(x) : force; yes ? classes.add(x) : classes.delete(x); return yes; }};
    }
    append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return this.attrs[k] ?? null; }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(k, f) { (this.listeners[k] ||= []).push(f); }
    click() { if (!this.disabled) for (const f of this.listeners.click || []) f({target: this}); }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    closest() { let node = this; while (node) { if (node.inert) return node; node = node.parent; } return null; }
    getClientRects() {
        if (this.id === 'mobileMenu' && !narrow || this.id === 'closeMobileMenu' && !narrow) return [];
        for (let node = this; node; node = node.parent) {
            if (['settingsDrawer', 'intranetModal'].includes(node.id) && !node.classList.contains('open')) return [];
        }
        return [{}];
    }
    focus() {
        if (this.closest() || !this.getClientRects().length) return;
        document.activeElement = this;
        for (const f of documentListeners.get('focusin') || []) f({target: this});
    }
    querySelectorAll() {
        return this.children.flatMap(child => [child, ...child.querySelectorAll()]).filter(child => ['button','input','select'].includes(child.tagName) && !child.disabled);
    }
}
const get = id => { if (id == null) return null; if (!nodes.has(id)) nodes.set(id, new Element(id)); return nodes.get(id); };
const main = get('main'), body = get('body'), sidebar = get('sidebar');
const nav = ['workspace','upload','review','calendar','analysis','history'].map(view => { const b = get('nav-'+view); b.dataset.view = view; return b; });
const views = nav.map(b => get('view-'+b.dataset.view));
sidebar.append(get('closeMobileMenu'), ...nav, get('openSettings'));
main.append(get('mobileMenu'), get('pageTitle'), ...views);
get('settingsDrawer').append(get('closeSettings'), get('thresholdRange'), get('saveSettings'));
get('thresholdRange').disabled = get('saveSettings').disabled = true;
get('intranetModal').append(get('closeIntranetModal'), get('intranetStudentId'), get('intranetPassword'), get('cancelIntranetLogin'), get('intranetLoginButton'));
body.append(sidebar, main, get('settingsDrawer'), get('intranetModal'));
for (const match of fs.readFileSync('frontend/index.html','utf8').matchAll(/\bid="([^"]+)"/g)) get(match[1]);
global.document = {body, activeElement: get('mobileMenu'), getElementById: id => nodes.get(id) || null,
    querySelector: selector => selector === '.main-area' ? main : null,
    querySelectorAll: selector => selector === '.nav-item' ? nav : selector === '.view' ? views : [],
    createElement: () => new Element(), addEventListener(k, f) { const all = documentListeners.get(k) || []; all.push(f); documentListeners.set(k, all); }};
global.window = global; global.matchMedia = () => media; global.addEventListener = () => {};
global.fetch = () => { throw Error('No network authorized by this DOM test'); };
vm.runInThisContext(fs.readFileSync('frontend/js/controls.js', 'utf8'));
vm.runInThisContext(fs.readFileSync('frontend/js/upload.js', 'utf8'));
vm.runInThisContext(fs.readFileSync('frontend/js/app.js', 'utf8'));
vm.runInThisContext('shellControls.setup(); setupNavigation(); setupCalendarControls();');
const run = code => vm.runInThisContext(code), shell = run('shellControls'), state = run('state');
function key(name, shiftKey = false) { const event = {key: name, shiftKey, prevented: false, preventDefault() { this.prevented = true; }}; for (const f of documentListeners.get('keydown') || []) f(event); return event; }
function resize(value) { narrow = value; for (const f of media.listeners) f(); }
function seed(events, meta = null) { state.events = events; state.meta = meta; state.source = 'SYNTHETIC'; run('calendarViewport = null; renderCalendar()'); }
const event = (subject, date, day = 'monday') => ({subject, date, day, start: '09:00', end: '10:00', venue: 'TEST-ROOM', status: 'review'});
const cases = {
    mobile_open_close() {
        assert(sidebar.inert); assert.equal(get('mobileMenu').attrs['aria-expanded'], 'false');
        get('mobileMenu').click(); assert(!sidebar.inert); assert(main.inert); assert(sidebar.classList.contains('open'));
        assert.equal(sidebar.attrs['role'], 'dialog'); assert.equal(document.activeElement.id, 'closeMobileMenu');
        get('closeMobileMenu').click(); assert(sidebar.inert); assert(!main.inert); assert.equal(document.activeElement.id, 'mobileMenu');
    },
    menu_escape_outside_and_scroll_restore() {
        body.style.overflow = 'auto'; shell.openMenu(); assert.equal(body.style.overflow, 'hidden');
        assert(key('Escape').prevented); assert.equal(body.style.overflow, 'auto');
        shell.openMenu(); get('navigationBackdrop').click(); assert.equal(shell.active, null);
    },
    mobile_navigation_and_invalid_target() {
        shell.openMenu(); nav[4].click(); assert.equal(shell.active, null); assert.equal(get('pageTitle').textContent, 'Schedule Analysis');
        assert.equal(document.activeElement.id, 'pageTitle'); assert.equal(nav[4].attrs['aria-current'], 'page');
        run('showView("nonexistent")'); assert(get('view-analysis').classList.contains('active'));
    },
    menu_keyboard_trap() {
        shell.openMenu(); get('openSettings').focus(); assert(key('Tab').prevented); assert.equal(document.activeElement.id, 'closeMobileMenu');
        assert(key('Tab', true).prevented); assert.equal(document.activeElement.id, 'openSettings');
    },
    breakpoint_focus_and_state() {
        shell.openMenu(); resize(false); assert.equal(shell.active, null); assert(!sidebar.inert); assert.equal(document.activeElement.id, 'pageTitle');
        nav[2].focus(); resize(true); assert(sidebar.inert); assert.equal(document.activeElement.id, 'mobileMenu');
    },
    settings_mobile_focus_return() {
        shell.openMenu(); get('openSettings').click(); assert.equal(shell.active, 'settingsDrawer'); assert(main.inert && sidebar.inert);
        assert.equal(document.activeElement.id, 'closeSettings'); assert(get('thresholdRange').disabled && get('saveSettings').disabled);
        key('Tab'); assert.equal(document.activeElement.id, 'closeSettings');
        key('Escape'); assert.equal(document.activeElement.id, 'mobileMenu'); assert(sidebar.inert && !main.inert);
    },
    settings_desktop_outside_and_resize() {
        resize(false); get('openSettings').focus(); get('openSettings').click(); resize(true); assert(sidebar.inert && main.inert);
        get('drawerBackdrop').click(); assert.equal(shell.active, null); assert.equal(document.activeElement.id, 'mobileMenu');
        resize(false); get('openSettings').focus(); get('openSettings').click(); get('closeSettings').click(); assert.equal(document.activeElement.id, 'openSettings');
    },
    login_escape_clears_password_and_traps_focus() {
        resize(false); get('connectIntranet').focus(); run('openIntranetLogin()'); assert.equal(document.activeElement.id, 'intranetStudentId');
        get('intranetPassword').value = 'INVENTED-TEST-ONLY'; get('intranetLoginButton').focus(); key('Tab'); assert.equal(document.activeElement.id, 'closeIntranetModal');
        key('Escape'); assert.equal(get('intranetPassword').value, ''); assert.equal(document.activeElement.id, 'connectIntranet');
    },
    delayed_drawer_focus_is_guarded() {
        resize(false); const frames = []; window.requestAnimationFrame = callback => frames.push(callback);
        const close = get('closeSettings'), originalFocus = close.focus; let refuse = true;
        close.focus = function () { if (refuse) { refuse = false; return; } originalFocus.call(this); };
        get('openSettings').focus(); get('openSettings').click(); assert.notEqual(document.activeElement.id, 'closeSettings');
        frames.shift()(); assert.equal(document.activeElement.id, 'closeSettings'); shell.close();
        get('openSettings').click(); shell.close(); get('pageTitle').focus(); frames.shift()();
        assert.equal(document.activeElement.id, 'pageTitle'); assert.equal(shell.active, null);
    },
    sample_status_replaces_previous_upload_status() {
        get('runStatus').textContent = 'OLD UPLOAD PROCESSING';
        run('renderEverything = () => {}; showToast = () => {}; loadSampleData()');
        assert.equal(get('runStatus').textContent, 'Synthetic sample — not assessed');
        assert.equal(state.source, 'sample');
    },
    dated_week_filtering_and_boundaries() {
        seed([event('EARLY','2032-12-27'), event('LATER','2033-01-03'), event('UNDATED',null)]);
        assert(get('weekGrid').innerHTML.includes('EARLY')); assert(!get('weekGrid').innerHTML.includes('LATER'));
        assert(get('previousWeek').disabled); assert(!get('nextWeek').disabled); const original = JSON.stringify(state.events);
        get('nextWeek').click(); assert(get('weekGrid').innerHTML.includes('LATER')); assert(!get('weekGrid').innerHTML.includes('EARLY'));
        assert(get('nextWeek').disabled); assert.equal(JSON.stringify(state.events), original); assert(get('calendarUndated').textContent.includes('1 candidate'));
        get('previousWeek').click(); assert(get('weekGrid').innerHTML.includes('EARLY'));
    },
    empty_gap_and_undated_not_repeated() {
        seed([event('EARLY','2033-02-07'),event('LATE','2033-02-21'),event('NO-DATE','unknown')]);
        run('moveCalendarWeek(1)'); assert(!get('weekGrid').innerHTML.includes('EARLY')); assert(!get('weekGrid').innerHTML.includes('LATE'));
        assert(!get('weekGrid').innerHTML.includes('NO-DATE')); assert(get('calendarScopeNote').textContent.includes('empty days do not establish availability'));
        run('moveCalendarWeek(1)'); assert(get('weekGrid').innerHTML.includes('LATE'));
    },
    unknown_dates_and_invalid_rollovers() {
        seed([event('BAD','2033-02-30'), event('UNKNOWN',null)]); assert(get('previousWeek').disabled && get('nextWeek').disabled);
        assert.equal(run('calendarDate("2033-02-30")'), null); assert.equal(run('calendarDate("0000-01-01")'), null);
        assert.equal(run('calendarDate("2033-02-07T00:00:00Z")'), null); assert.equal(run('shiftCalendarDate("2032-02-28", 1)'), '2032-02-29');
    },
    scope_replacement_resets_view() {
        seed([event('ONE','2033-02-07'),event('TWO','2033-02-21')]); run('moveCalendarWeek(1)');
        state.events = [event('NEW','2036-05-20')]; run('sourceGeneration++; renderCalendar()');
        assert(get('weekGrid').innerHTML.includes('NEW')); assert(get('nextWeek').disabled && get('previousWeek').disabled);
    },
    source_week_bounds_and_conflicting_day() {
        seed([event('DATED','2033-02-08','monday')],{weekStart:'2033-02-07',weekEnd:'2033-02-13'});
        assert(get('weekGrid').innerHTML.includes('Printed weekday differs')); assert(get('calendarRange').textContent.includes('2033-02-07'));
        assert(get('nextWeek').disabled && get('previousWeek').disabled);
    },
    extreme_dates_and_safety_controls() {
        seed([event('UNREPRESENTABLE','9999-12-31')]); assert(get('nextWeek').disabled && get('previousWeek').disabled);
        assert(get('syncButton').disabled && get('downloadIcs').disabled); assert.equal(state.connectionToken, null);
        run('moveCalendarWeek(0)'); assert.equal(run('calendarViewport'),null);
    }
};
const selected = process.argv[2];
try { cases[selected](); console.log(JSON.stringify({case:selected,status:'passed'})); }
catch (error) { console.error(error.stack); process.exitCode=1; }
