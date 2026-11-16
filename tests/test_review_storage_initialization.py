"""Real absent-parent Windows initialization and restrictive ACL regression."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.review.models import ReviewError
from backend.review.persistence import ReviewJournal
from backend.review.secure_directory import user_local_root, is_reparse
from backend.review.windows_security import current_actor


@unittest.skipUnless(os.name == 'nt', 'Windows local storage and ACL integration')
class StorageInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(prefix='stair-init-synthetic-');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.owner=current_actor()

    def journal(self,path=None):
        return ReviewJournal(path or self.base/'application'/'StudentReview',self.owner)

    def powershell(self,script,**env):
        exe=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        # PowerShell 7's inherited module path can prevent Windows PowerShell
        # from loading its own Security module. Use that runtime's defaults.
        environment={k:v for k,v in os.environ.items() if k.upper()!='PSMODULEPATH'}
        r=subprocess.run([str(exe),'-NoProfile','-NonInteractive','-Command',"$ErrorActionPreference='Stop';"+script],
                         env={**environment,**env},capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stderr)
        return r.stdout.strip()

    def junction(self,path,target):
        self.powershell('New-Item -ItemType Junction -Path $env:STAIR_TEST_LINK -Target $env:STAIR_TEST_TARGET -ErrorAction Stop | Out-Null',
                        STAIR_TEST_LINK=str(path),STAIR_TEST_TARGET=str(target))
        self.assertTrue(is_reparse(path))
        self.addCleanup(os.rmdir,path) # Remove only the owned junction, never its target.

    def test_real_first_run_both_app_parent_and_child_absent(self):
        # The parent is directly under LocalAppData, not a pre-resolved temp
        # directory: this reproduces Store Python's first-create redirection.
        root=user_local_root();name='STAIR-initialization-test-'+uuid4().hex
        path=root/name/'StudentReview'
        self.assertFalse(path.parent.exists());self.assertFalse(path.exists())
        def cleanup_owned_probe():
            actual=(root/name).resolve()
            if actual.is_relative_to(root) and actual.name==name and not is_reparse(actual) and actual.is_dir():
                shutil.rmtree(actual)
        self.addCleanup(cleanup_owned_probe)
        journal=self.journal(path)
        self.assertTrue(journal.directory.is_relative_to(root))
        self.assertTrue(journal.records.is_dir())
        with journal.locked():
            self.assertEqual(journal.load(),(None,0,None))
            journal.append({'synthetic':'first-run recovery only'})
        before={p.name:p.read_bytes() for p in journal.records.iterdir()}
        restarted=self.journal(path)
        self.assertEqual(restarted.directory,journal.directory)
        with restarted.locked():self.assertEqual(restarted.load()[0],{'synthetic':'first-run recovery only'})
        self.assertEqual(before,{p.name:p.read_bytes() for p in restarted.records.iterdir()})

    def test_acl_current_logon_and_system_only(self):
        journal=self.journal()
        with journal.locked():journal.append({'synthetic':'private test payload'})
        for path in (journal.directory,journal.records,next(journal.records.iterdir()),journal.directory/'writer.lock'):
            data=json.loads(self.powershell('''$a=Get-Acl -LiteralPath $env:STAIR_TEST_ACL -ErrorAction Stop
[pscustomobject]@{protected=$a.AreAccessRulesProtected;ids=@($a.Access|ForEach-Object {$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value});rights=@($a.Access|ForEach-Object {$_.FileSystemRights.ToString()})}|ConvertTo-Json -Compress''',STAIR_TEST_ACL=str(path)))
            self.assertEqual(set(data['ids']),{self.owner.removeprefix('windows-logon:'),'S-1-5-18'})
            self.assertTrue(all(v=='FullControl' for v in data['rights']))
        # Reinitialization protects existing records too, without rewriting data.
        self.journal()
        data=json.loads(self.powershell('''$a=Get-Acl -LiteralPath $env:STAIR_TEST_ACL -ErrorAction Stop
[pscustomobject]@{protected=$a.AreAccessRulesProtected}|ConvertTo-Json -Compress''',STAIR_TEST_ACL=str(journal.directory)))
        self.assertTrue(data['protected'])

    def test_relative_traversal_reserved_and_unc_paths_rejected(self):
        root=user_local_root()
        for path in ('relative-review',r'C:relative-review',r'\\server\share\review',root,
                     root/'..'/'escaped-review',root/'CON',root/'unsafe:stream',root/'trailing.'):
            with self.subTest(path=str(path)):
                with self.assertRaises(ReviewError):self.journal(path)

    def test_outside_user_local_root_not_created(self):
        path=Path.home()/'Documents'/('STAIR-disallowed-'+uuid4().hex)
        self.assertFalse(path.exists())
        with self.assertRaisesRegex(ReviewError,'REVIEW_STORAGE_OUTSIDE_LOCAL_ROOT'):self.journal(path)
        self.assertFalse(path.exists())

    def test_parent_junction_rejected_without_following_target(self):
        target=self.base/'target';target.mkdir();(target/'preserve').write_bytes(b'unchanged')
        link=self.base/'application';self.junction(link,target)
        with self.assertRaisesRegex(ReviewError,'REVIEW_STORAGE_PATH_INVALID'):self.journal(link/'StudentReview')
        self.assertFalse((target/'StudentReview').exists());self.assertEqual((target/'preserve').read_bytes(),b'unchanged')

    def test_records_junction_rejected(self):
        directory=self.base/'application';directory.mkdir()
        target=self.base/'target';target.mkdir()
        self.junction(directory/'records',target)
        with self.assertRaisesRegex(ReviewError,'REVIEW_STORAGE_PATH_INVALID'):self.journal(directory)

    def test_reparse_in_existing_tree_rejected_before_permissions(self):
        journal=self.journal();target=self.base/'target';target.mkdir()
        self.junction(journal.directory/'unsafe-link',target)
        with self.assertRaisesRegex(ReviewError,'REVIEW_STORAGE_PATH_INVALID'):self.journal()

    def test_regular_file_in_place_of_directory_preserved(self):
        path=self.base/'application';path.write_bytes(b'preserve existing file')
        with self.assertRaises(ReviewError):self.journal(path/'StudentReview')
        self.assertEqual(path.read_bytes(),b'preserve existing file')

    def test_permission_failure_fails_closed(self):
        path=self.base/'new-parent'/'StudentReview'
        with patch('backend.review.secure_directory.WindowsDirectories.create',side_effect=PermissionError('Synthetic access denial')):
            with self.assertRaisesRegex(ReviewError,'REVIEW_STORAGE_ACCESS_DENIED'):self.journal(path)
        self.assertFalse(path.exists())

    def test_acl_failure_does_not_rewrite_existing_history(self):
        journal=self.journal()
        with journal.locked():journal.append({'synthetic':'retained encrypted data'})
        before={p.name:p.read_bytes() for p in journal.records.iterdir()}
        with patch('backend.review.secure_directory.WindowsDirectories.restrict',side_effect=PermissionError('Synthetic ACL denial')):
            with self.assertRaisesRegex(ReviewError,'REVIEW_STORAGE_ACCESS_DENIED'):self.journal()
        self.assertEqual(before,{p.name:p.read_bytes() for p in journal.records.iterdir()})

    def test_external_hard_link_rejected(self):
        journal=self.journal();external=self.base/'outside-journal';external.write_bytes(b'outside journal')
        os.link(external,journal.directory/'unexpected-hard-link')
        with self.assertRaisesRegex(ReviewError,'REVIEW_STORAGE_PATH_INVALID'):self.journal()
        self.assertEqual(external.read_bytes(),b'outside journal')

    def test_existing_writer_prevents_acl_changes(self):
        journal=self.journal()
        with journal.locked(), patch('backend.review.secure_directory.WindowsDirectories.restrict') as restrict:
            with self.assertRaisesRegex(ReviewError,'REVIEW_STORAGE_BUSY'):self.journal()
            restrict.assert_not_called()
