# Updating and recovering the website

## Everyday changes

Tell Codex what to change in this project. The normal sequence is local edits,
preview, owner approval, push to `master`, automatic deployment, and live checks.
An explicit request to update and publish already supplies approval.

The document split uses the existing public repository:

- Firebase serves the website, portrait, CV, and both research statements.
- GitHub Pages serves the public teaching notes, exercises, solutions, and slides.
- Existing website PDF URLs redirect to their corresponding Pages documents.
  The browser address changes when following that redirect. Browser PDF viewing
  is preserved where the visitor's browser supports it; personal download
  preferences still apply.
- HTML, styling, and the portrait are unchanged. Public PDF bytes are copied
  exactly; they are not recompressed or converted.

## Public document updates

Edit or replace the source listed in `hosting-downloads.json`, keeping its name
when replacing an existing document. Run `python3 scripts/hosting_downloads.py
--refresh` to refresh the reviewed checksums and matching redirects, then review
the diff. For a new document, add its source and public URL aliases to the
allowlist, refresh, and link to its website URL. Avoid direct Pages links in
website content so future hosting changes do not require editing every page.

Redirect destinations include a content-derived version marker. Replacing a PDF
changes that marker, giving browsers a fresh cache entry while preserving the
website link and physical document filename. GitHub's edge cache can still take
time to refresh; live verification waits up to twelve minutes before blocking
Firebase publication if the expected content is unavailable.

The pipeline validates the public website first, stages **only** allowlisted
documents into a fresh directory, publishes that directory to Pages, and checks
the public PDFs before deploying Firebase. It checks PDF response headers and
downloads new or changed documents to compare their bytes. Unchanged documents
receive smaller header checks. Missing files, changed checksums, unexpected
download headers, or Pages errors block the Firebase step.

The two hosts are not one atomic transaction: documents may already be updated
if the later Firebase deployment fails. Resolve the failure or restore the
previous document sources and run the workflow again. Reverting an entire site
release should use the same workflow so both hosts receive the intended version.
Preserve older document filenames that were already published.

## Preview and checks

Use the pinned toolchain in `.github/actions/build-hosting/action.yml` (Hugo
0.107.0 extended, Node 22, Firebase CLI 13.29.1). Do not use the latest Hugo
implicitly. Relevant commands are:

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/build_bandwidth_preview.py --destination /tmp/mele-site-preview
python3 scripts/verify_site_build.py --root /tmp/mele-site-preview
python3 scripts/hosting_downloads.py --stage /tmp/mele-public-documents
```

Use a fresh document staging directory. If the local unlisted copy is unavailable
on a new laptop, add `--public-only` to the website build and verification commands.
Production restores the unlisted PDF from the protected GitHub environment.
Do not copy the whole website into a Pages artifact.

For a fully local preview, serve the staged public documents with a local HTTP
server and use `scripts/prepare_hosting_config.py --emulator
--preview-download-origin http://localhost:1342` with the normal root/output
arguments. This changes only the generated emulator config. Production URLs
remain in the source configuration.

## New laptop or another device

Clone `https://github.com/AntMele/Antonio-Anna-Mele-website`, open the folder in
Codex, and ask it to set up the pinned preview tools and read `AGENTS.md`.
Authenticate GitHub to push approved changes. The hosting configuration,
instructions, public document sources, checks, and deployment workflow travel
with the repository; production credentials stay in GitHub environments.

For small public text changes, GitHub's web editor can also create a branch and
pull request. Merging to `master` publishes through the same checks. Never upload
the unlisted PDF or its link into GitHub's web editor or public repository.

Keep separate private backups of editable CV/research sources, original documents,
and the unlisted PDF/link. Untracked files and ignored local backups are not
restored by cloning. The local usage-monitoring automation must also be recreated
on a replacement laptop; it is separate from cloud deployment.

## Activation and capacity

The repository uses GitHub Actions as its Pages publishing source, with the
`github-pages` environment restricted to `master`. Its URL must match
`hosting-downloads.json`. Do not add private-document secrets to that environment.
The separate `production-private` environment remains responsible for Firebase
deployment and the unlisted document. Wait for both jobs and verify live responses
before reporting a publication as complete.

The public documents occupy about 146 MB. GitHub Pages currently allows
a published site up to 1 GB and has a soft transfer limit of 100 GB/month;
it is suitable for this current collection but is not unlimited storage or
bandwidth. Reassess as the collection and traffic grow.

Sources: [Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)
and [Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
