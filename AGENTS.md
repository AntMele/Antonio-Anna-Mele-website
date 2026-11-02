# Website maintenance workflow

These are the owner's standing preferences for future chats in this project. More recent explicit user instructions take precedence.

## Edit, preview, approve, publish

1. Make requested website, news, CV, and document edits locally. Inspect the existing sources and preserve unrelated changes.
2. Build a local preview, check the affected content and links, and show the user the result with a concise explanation. For layout changes, check desktop and mobile; for PDF changes, inspect rendered pages, equations, selectable text, and links.
3. Default to preview first and publication only after the user approves. An explicit request such as "update this and publish" already authorizes publication; do not ask again. Approval for an earlier release does not authorize unrelated future releases.
4. Once approved, commit only the intended public changes and push to `master` in `AntMele/Antonio-Anna-Mele-website`. This triggers the GitHub Actions production deployment. Do not push to `master` or dispatch production merely to create a preview.
5. Wait for the workflow to finish, resolve failures, and verify the live pages, changed downloads, and relevant response headers. Report the actual result; do not treat a successful push as proof of deployment. Public documents deploy to Pages before Firebase: a later Firebase failure leaves the main website unchanged but documents may already have updated. Roll back both through the same workflow when needed.

## Build and deployment

- Site: Hugo/LoveIt at `https://antonioannamele.com`; Firebase project/site `antonioannamele`.
- Read the current workflows before deploying. The reviewed toolchain is Hugo **0.107.0 extended**, Node **22**, and Firebase CLI **13.29.1**.
- Use `scripts/build_bandwidth_preview.py` to build outside the repository; it preserves the published URL compatibility baseline in `public/`. Do not replace this process with an unchecked plain Hugo build or deploy stale generated files.
- Run `scripts/verify_site_build.py --root <preview>` and appropriate checks from `.github/actions/build-hosting/action.yml`. Use `scripts/prepare_hosting_config.py` for an isolated Firebase emulator configuration and `scripts/verify_hosting_cache.py` to check actual headers. Public-only builds/checks use `--public-only`.
- Production uses `.github/workflows/firebase-hosting-merge.yml` and the `production-private` environment, restricted to `master`. It builds and checks public content, restores the unlisted document privately, validates the complete release and headers, then deploys. Pull requests run public checks without private secrets or deployment.
- Do not bypass a failed check or publish a release missing either research statement.
- Read `WEBSITE_MAINTENANCE.md` for the public-document hosting workflow, preview setup, and new-device recovery. Verify the latest successful workflow run before assuming pending changes are live. Public PDFs in `hosting-downloads.json` publish to Pages before Firebase; the Pages job must never have private-document secrets or upload the whole website. Update the allowlist metadata with `scripts/hosting_downloads.py --refresh` when a public PDF changes. Preserve its website URL, all existing aliases, and stable Pages filename.

## Documents and privacy

- Preserve existing URLs, both research-statement versions, and the unlisted statement's current URL. The public version is `static/documents/Research_Statement_Mele_Public.pdf`.
- Never commit the unlisted PDF, its actual filename, token, or URL, or include them in public pages, sitemaps, logs, artifacts, caches, or documentation. Find the local ignored copy through `scripts/private_documents.py`; use generic descriptions in public changes.
- Keep the unlisted PDF's `X-Robots-Tag: noindex, nofollow, nosnippet` header. It is link-only, not access-controlled; do not describe it as authenticated or confidential hosting.
- Updating the unlisted PDF requires updating its protected GitHub environment secret bundle as well as the local ignored copy. Follow `scripts/restore_private_document.py` and the local activation report: the bundle contains metadata and PDF bytes, split into `PRIVATE_DOCUMENT_PART_*` secrets with a checksum; `PRIVATE_DOCUMENT_PART_COUNT` is a non-sensitive environment variable. Keep secret values out of command arguments and output. Missing/corrupt private content must block deployment.
- CV updates may come as requested text edits or a replacement PDF. Inspect the available source before deciding how to update it and preserve its existing public link.
- Keep original documents before optimization in ignored local storage outside published content. Preserve readable equations, selectable text, and working links.

## Standing preferences and operational notes

- Keep the original homepage portrait `static/images/PhotoSlack3.jpg`. The owner explicitly rejected portrait compression; do not resize, recompress, or replace it unless asked.
- Preserve the site's appearance and content when optimizing bandwidth. Keep long immutable caching limited to content-hashed assets; HTML and replaceable PDFs must revalidate.
- Do not change billing or hosting providers without explicit authorization. Preserve existing logging, monitoring, and the approved retention policy of the current release plus ten previous releases.
- Never stage all files blindly. Keep `output/`, private documents, `.firebase-ci/`, caches, and local backups out of Git and published content. Preserve unrelated local work.
- Historical setup and verification evidence is in the ignored local `output/bandwidth-optimization/github-workflow-activation.md` and neighboring reports. These may contain private details: read locally, never publish them. Recheck current state rather than treating old reports as current evidence.
