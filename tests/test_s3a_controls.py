"""Synthetic frontend logic/markup checks; actual layout verified in browser QA."""
from pathlib import Path
import shutil
import re
import subprocess
import unittest
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
NODE=shutil.which('node') or str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')


class S3aControlTests(unittest.TestCase):
    def test_accessible_markup_and_disabled_mutation_controls(self):
        page=BeautifulSoup((ROOT/'frontend/index.html').read_text(encoding='utf-8'),'html.parser')
        for id in ['settingsDrawer','intranetModal']:
            panel=page.find(id=id)
            self.assertEqual(panel['role'],'dialog');self.assertEqual(panel['aria-modal'],'true')
            self.assertIsNotNone(page.find(id=panel['aria-labelledby']))
        self.assertEqual(page.find(id='mobileMenu')['aria-controls'],'sidebar')
        self.assertEqual(page.find(id='mobileMenu')['aria-expanded'],'false')
        for id in ['thresholdRange','saveSettings','syncButton','downloadIcs']:
            self.assertTrue(page.find(id=id).has_attr('disabled'))
        self.assertTrue(page.select_one('#eventForm button.button.primary').has_attr('disabled'))

    def test_extraction_controls_and_script_order_preserved(self):
        page=BeautifulSoup((ROOT/'frontend/index.html').read_text(encoding='utf-8'),'html.parser')
        self.assertEqual(page.find(id='fileInput')['accept'],'.pdf,.png,.jpg,.jpeg')
        ids={item.get('id') for item in page.select('input[type=checkbox]')}
        self.assertTrue({'runP1','runLayoutAssociation','runRuledGridS2a','runBlockGridS2b'}<=ids)
        self.assertFalse(page.find(id='runRuledGridS2a').has_attr('checked'))
        self.assertFalse(page.find(id='runBlockGridS2b').has_attr('checked'))
        scripts=[item.get('src') for item in page.select('script[src]')]
        self.assertLess(scripts.index('./js/controls.js'),scripts.index('./js/app.js'))

    def test_responsive_containment_contract(self):
        # Static regression guards only. Measured overflow is verified separately in the browser.
        css=(ROOT/'frontend/styles.css').read_text(encoding='utf-8')
        self.assertRegex(css,r'html\s*\{\s*min-width:\s*0;')
        self.assertRegex(css,r'\.status-label\s*\{\s*white-space:\s*normal;')
        self.assertIn('.workspace-grid > .panel { min-width: 0; }',css)
        self.assertRegex(css,r'\.mobile-sidebar-close\s*\{[^}]*color:\s*white;[^}]*background:\s*transparent;')
        self.assertRegex(css,r'\.drawer-backdrop\s*\{[^}]*pointer-events:\s*none;')
        self.assertRegex(css,r'\.drawer-backdrop\s*\{[^}]*transition:\s*opacity')
        page=BeautifulSoup((ROOT/'frontend/index.html').read_text(encoding='utf-8'),'html.parser')
        self.assertEqual(page.find(id='weekGrid')['tabindex'],'0')
        self.assertIn('scroll horizontally',page.find(id='weekGrid')['aria-label'])


def attach(case):
    def test(self):
        result=subprocess.run([NODE,str(ROOT/'tests/s3a_frontend_checks.js'),case],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr[-2500:])
    setattr(S3aControlTests,'test_'+case,test)


for case in ['mobile_open_close','menu_escape_outside_and_scroll_restore','mobile_navigation_and_invalid_target',
    'menu_keyboard_trap','breakpoint_focus_and_state','settings_mobile_focus_return','settings_desktop_outside_and_resize',
    'login_escape_clears_password_and_traps_focus','dated_week_filtering_and_boundaries','empty_gap_and_undated_not_repeated',
    'unknown_dates_and_invalid_rollovers','scope_replacement_resets_view','source_week_bounds_and_conflicting_day','extreme_dates_and_safety_controls',
    'delayed_drawer_focus_is_guarded','sample_status_replaces_previous_upload_status']:
    attach(case)
