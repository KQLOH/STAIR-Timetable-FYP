# Starter setup and actual runnable scope

This package needs Python 3.12 for its **standard-library documentation utility/tests**. It needs no pip packages, virtual environment copy, Node, OCR executable, university login, dataset or original project. A separate environment is optional; no dependency manifests are included because the timetable application is not shipped.

From the starter root:

```powershell
python --version
python -B -S -m unittest discover -s tests -v
python -B -S -m starter_tools.markdown_links --root .
```

`-B` avoids bytecode files and `-S` disables site-package loading. The checker is offline, checks relative file/directory targets, skips external/anchor-only links and rejects targets outside its root. It is a deliberately small parser: it does not validate heading anchors, complex Markdown syntax, external URL availability, privacy, authorship or research evidence. Passing it is not permission to publish arbitrary files.

There is no STAIR server/website to start from this package. Future cleared application modules will need their actual declared environment and scope-specific tests. Do not copy the original app, dependency set, private runtime config or hidden resources to make this starter appear runnable.

Only the starter tests in [verification](starter-verification.md) were executed on this copy. No fresh Git clone, full application suite or physical clean-machine test is claimed. Use the [manual GitHub guide](github-upload.md) after final review.
