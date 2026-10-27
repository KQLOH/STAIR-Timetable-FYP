/* Website navigation only. No extraction, research, review or calendar authority. */
const STAIRControls = (() => {
    const focusSelector = 'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]';

    class Shell {
        constructor() {
            this.mobile = window.matchMedia('(max-width: 820px)');
            this.active = null;
            this.restore = null;
            this.savedOverflow = '';
            this.inertBefore = new Map();
        }

        setup() {
            const byId = id => document.getElementById(id);
            byId('mobileMenu').addEventListener('click', () => this.active === 'sidebar' ? this.close() : this.openMenu());
            byId('closeMobileMenu').addEventListener('click', () => this.close());
            byId('navigationBackdrop').addEventListener('click', () => this.close());
            byId('openSettings').addEventListener('click', () => this.openDialog('settingsDrawer'));
            byId('closeSettings').addEventListener('click', () => this.close());
            byId('drawerBackdrop').addEventListener('click', () => {
                if (this.active === 'settingsDrawer') this.close();
            });
            byId('reviewBackdrop')?.addEventListener('click', () => {
                if (this.active === 'reviewDrawer') this.close();
            });
            byId('intranetModal').addEventListener('click', event => {
                if (event.target === byId('intranetModal')) this.close();
            });
            document.addEventListener('keydown', event => this.onKey(event));
            document.addEventListener('focusin', event => {
                const panel = byId(this.active);
                if (panel && !panel.contains(event.target)) this.focusFirst(panel);
            });
            this.mobile.addEventListener('change', () => {
                const wasMenu = this.active === 'sidebar';
                if (this.active === 'sidebar') this.close(false);
                this.syncMenu();
                // A resize must not leave focus in an inert/off-screen sidebar.
                if (this.mobile.matches && !this.active && byId('sidebar').contains(document.activeElement)) byId('mobileMenu').focus();
                else if (wasMenu && !this.mobile.matches) byId('pageTitle').focus();
            });
            this.syncMenu();
        }

        focusable(panel) {
            return [...panel.querySelectorAll(focusSelector)].filter(node => node.getClientRects().length && !node.closest('[inert]'));
        }

        focusFirst(panel) {
            (this.focusable(panel)[0] || panel).focus();
        }

        syncMenu() {
            const sidebar = document.getElementById('sidebar');
            const open = this.active === 'sidebar';
            sidebar.classList.toggle('open', open);
            sidebar.inert = Boolean(this.active && this.active !== 'sidebar') || (this.mobile.matches && !open);
            sidebar.setAttribute('aria-hidden', String(sidebar.inert));
            if (open) {
                sidebar.setAttribute('role', 'dialog');
                sidebar.setAttribute('aria-modal', 'true');
            } else {
                sidebar.removeAttribute('role');
                sidebar.removeAttribute('aria-modal');
            }
            document.getElementById('mobileMenu').setAttribute('aria-expanded', String(open));
            document.getElementById('mobileMenu').setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
            document.getElementById('navigationBackdrop').classList.toggle('visible', open);
        }

        isolate(nodes) {
            this.savedOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
            for (const node of nodes) {
                this.inertBefore.set(node, node.inert);
                node.inert = true;
            }
        }

        openMenu() {
            if (!this.mobile.matches) return;
            if (this.active) this.close(false);
            this.restore = document.getElementById('mobileMenu');
            this.active = 'sidebar';
            this.syncMenu();
            this.isolate([document.querySelector('.main-area')]);
            this.focusFirst(document.getElementById('sidebar'));
        }

        openDialog(id, opener = document.activeElement) {
            if (this.active === id) return;
            // Settings opened from the mobile menu returns to its visible trigger.
            if (this.active === 'sidebar') opener = document.getElementById('mobileMenu');
            if (this.active) this.close(false);
            this.restore = opener;
            this.active = id;
            const panel = document.getElementById(id);
            panel.classList.add('open');
            panel.setAttribute('aria-hidden', 'false');
            document.getElementById(this.backdrop(id)).classList.add('visible');
            this.isolate([document.querySelector('.main-area'), document.getElementById('sidebar')]);
            this.focusFirst(panel);
            // A newly visible drawer may still be at the first animation frame.
            // Retry only while this dialog still owns focus, without stealing it
            // after a close, navigation, or a different dialog opening.
            window.requestAnimationFrame?.(() => {
                if (this.active === id && !panel.contains(document.activeElement)) this.focusFirst(panel);
            });
        }

        backdrop(id) {
            return id === 'settingsDrawer' ? 'drawerBackdrop' : id === 'reviewDrawer' ? 'reviewBackdrop' : 'intranetModalBackdrop';
        }

        close(returnFocus = true) {
            if (!this.active) return;
            const id = this.active;
            const panel = document.getElementById(id);
            this.active = null;
            for (const [node, wasInert] of this.inertBefore) node.inert = wasInert;
            this.inertBefore.clear();
            document.body.style.overflow = this.savedOverflow;
            if (id !== 'sidebar') {
                panel.classList.remove('open');
                panel.setAttribute('aria-hidden', 'true');
                document.getElementById(this.backdrop(id)).classList.remove('visible');
                if (id === 'intranetModal') document.getElementById('intranetPassword').value = '';
            }
            this.syncMenu();
            if (returnFocus) {
                const target = this.restore;
                if (target?.isConnected && !target.closest('[inert]') && target.getClientRects().length) target.focus();
                else document.getElementById(this.mobile.matches ? 'mobileMenu' : 'pageTitle').focus();
            }
            this.restore = null;
        }

        navigate() {
            if (this.active) this.close(false);
            document.getElementById('pageTitle').focus();
        }

        onKey(event) {
            if (!this.active) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                this.close();
            } else if (event.key === 'Tab') {
                const panel = document.getElementById(this.active);
                const nodes = this.focusable(panel);
                const first = nodes[0], last = nodes[nodes.length - 1];
                if (!nodes.length) { event.preventDefault(); panel.focus(); }
                else if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) {
                    event.preventDefault(); last.focus();
                } else if (!event.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) {
                    event.preventDefault(); first.focus();
                }
            }
        }
    }
    return {Shell};
})();
