# Manual PowerShell publication and invitation steps

**Only after your final starter review.** These commands are instructions, not operations already performed by the preparation agent. They apply only to this new starter folder, never the original project/review folder. No old Git database, branch or history is imported.

## 1. Open the reviewed starter and check bytes

The public guide uses an entered path so no personal development directory is published. The owner has the exact prepared path in the handover conversation/local receipt.

```powershell
$StarterDirectory = Read-Host 'Absolute path of STAIR-Timetable-FYP-PUBLIC-STARTER'
Set-Location -LiteralPath $StarterDirectory
if ((Split-Path -Leaf (Get-Location).Path) -ne 'STAIR-Timetable-FYP-PUBLIC-STARTER') { throw 'Wrong folder' }
if (Test-Path -LiteralPath '.git') { throw 'Git already exists: inspect rather than overwrite' }
$Manifest = Get-Content -LiteralPath 'PUBLIC-STARTER-MANIFEST.json' -Raw | ConvertFrom-Json
if ($Manifest.status -ne 'READY_FOR_MANUAL_PUBLICATION_REVIEW') { throw 'Starter not verified' }
foreach ($Item in $Manifest.files) {
    if ((Get-FileHash -LiteralPath $Item.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Item.sha256) { throw "Changed file: $($Item.path)" }
}
python -B -S -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Starter tests failed' }
python -B -S -m starter_tools.markdown_links --root .
if ($LASTEXITCODE -ne 0) { throw 'Starter links failed' }
```

Inspect the complete manifest and this guide itself during review. The manifest cannot embed its own hash; its final digest is in the local receipt/owner handover. It lists only the other 19 files. All 20 files below are the approved starter set. No blanket add.

## 2–4. New Git database, explicit staging and staged review

Git 2.28+ supports this main-branch initialization; local preparation observed Git 2.56. Follow the [official GitHub local-code guide](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github), using this exact file list rather than its generic blanket-add example.

```powershell
git init -b main
if ($LASTEXITCODE -ne 0) { throw 'Initialization failed' }
$ApprovedStarterFiles = @(
    '.gitattributes'
    '.gitignore'
    'AGENTS.md'
    'CONTRIBUTING.md'
    'PUBLIC-STARTER-MANIFEST.json'
    'README.md'
    'docs/architecture.md'
    'docs/github-upload.md'
    'docs/public-safety.md'
    'docs/research-roadmap.md'
    'docs/scope-and-status.md'
    'docs/setup.md'
    'docs/starter-verification.md'
    'docs/team-workflow.md'
    'starter_tools/__init__.py'
    'starter_tools/markdown_links.py'
    'templates/annotation-guide-proposal.md'
    'templates/audit-29-page-workflow-proposal.md'
    'templates/source-inventory-template.csv'
    'tests/test_markdown_links.py'
)
git add -- $ApprovedStarterFiles
if ($LASTEXITCODE -ne 0) { throw 'Staging failed' }
$StagedFiles = @(git diff --cached --name-only)
$Mismatch = Compare-Object ($ApprovedStarterFiles | Sort-Object) ($StagedFiles | Sort-Object)
if ($Mismatch) { throw 'Unexpected staged file set; stop and inspect' }
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Staged whitespace check failed' }
git diff --cached --stat
git diff --cached
git status --short
```

Check source rights/privacy and commit-author metadata before proceeding. If Git needs your name/email, choose the identity you are willing to publish (for example your actual GitHub-provided no-reply address) and configure **this repository only**; no personal identity is invented or configured by this task. Never put authentication tokens in a URL/file/command argument.

## 5. Commit only after your staged review

```powershell
git commit -m "chore: start public STAIR FYP team collaboration"
if ($LASTEXITCODE -ne 0) { throw 'Commit failed' }
```

## 6–7. New empty PUBLIC remote, then push main

Manually create `STAIR-Timetable-FYP` at [GitHub New Repository](https://github.com/new), choose **PUBLIC**, and leave README/gitignore/license initialization unchecked. Do not import another repository. The agent did not create or check availability of this target. Supply the actual owner account/organization below; no target URL is assumed.

```powershell
$GitHubOwner = Read-Host 'Actual GitHub owner account or organization'
if ($GitHubOwner -notmatch '^[A-Za-z0-9][A-Za-z0-9-]*$') { throw 'Invalid owner name' }
$RepoName = 'STAIR-Timetable-FYP'
$RepoWebUrl = "https://github.com/$GitHubOwner/$RepoName"
$RepoUrl = "$RepoWebUrl.git"
$RemoteRefs = @(git ls-remote $RepoUrl)
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the target; check URL/access/authentication' }
if ($RemoteRefs.Count -ne 0) { throw 'Target is not empty: stop; do not pull/merge unrelated history' }
if (@(git remote).Count -ne 0) { throw 'Unexpected existing remote; stop and inspect' }
git remote add origin $RepoUrl
if ($LASTEXITCODE -ne 0) { throw 'Remote setup failed' }
git remote -v
# Verify the browser shows the intended PUBLIC target before the next command.
git push -u origin main
if ($LASTEXITCODE -ne 0) { throw 'Push failed; do not force-push' }
```

There is no old remote fetch, history copy, other-branch merge, mirror/all-refs push or forced push in this procedure. Public visibility and the exact target are your manual review decisions.

## 8. Invite the teammate

No invitation has been sent. If GitHub CLI is already installed, you can authenticate through its [browser login](https://cli.github.com/manual/gh_auth_login) and submit the collaborator invitation yourself:

```powershell
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'GitHub CLI is absent: use the browser invitation steps below' }
gh auth login --hostname github.com --git-protocol https --web
if ($LASTEXITCODE -ne 0) { throw 'GitHub authentication failed' }
$Visibility = gh api "repos/$GitHubOwner/$RepoName" --jq '.visibility'
if ($LASTEXITCODE -ne 0 -or $Visibility.Trim() -ne 'public') { throw 'Target is not confirmed PUBLIC' }
$Teammate = Read-Host 'Teammate GitHub username'
if ($Teammate -notmatch '^[A-Za-z0-9][A-Za-z0-9-]*$') { throw 'Invalid teammate username' }
gh api --method PUT "repos/$GitHubOwner/$RepoName/collaborators/$Teammate" --silent
if ($LASTEXITCODE -ne 0) { throw 'Invitation request failed' }
```

The [official collaborator API](https://docs.github.com/en/rest/collaborators/collaborators#add-a-repository-collaborator) uses PUT on that endpoint; new invitations require acceptance. No token is printed/embedded. GitHub CLI was not available or installed by this preparation task, so these CLI steps were documentation/syntax checked, not executed.

Without that CLI, open Settings and follow the [official personal-repository invitation steps](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository): Collaborators → Add people → select the teammate → send invitation. They must accept. You can open the intended settings page yourself:

```powershell
Start-Process "$RepoWebUrl/settings"
```

Then follow the [branch/PR workflow](team-workflow.md). Keep all branches free of private records and credentials. Do not add withheld application code until its separate gates are satisfied.
