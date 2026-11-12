# Reference

## Invoice links

**Invoice links** must be `https://` (rendered as a link, so `javascript:`/`data:` are refused). Whoever opens one needs access to it, and it lasts as long as its sharing settings. An uploaded invoice's link is its plain OneDrive address, not a share link: it opens for you, signed in to that Microsoft account, and for nobody else.

An invoice kept in the [invoice folder on the server](local-folder.md) has no link at all. The expense stores the file's path below that folder, and the app serves it at `/invoice/<expense id>`, behind the same forward-auth as every page: shown in the browser's PDF viewer, or saved as a file with `?download=1`. Only PDFs inside the mounted folder are ever served.

## Version and updates

The footer of every page links to this documentation and shows the installed version. A released
image also says whether it is the latest, and links to the release notes of the newer release when
there is one.

<!-- screenshot: footer-version - The footer, showing the version and an update notice -->

The version is the release tag the image was built from. A build without one (`docker compose build`,
the dev stack) shows *Development build* and never checks.

To find out whether a newer release exists, the app makes one anonymous request to the public
releases list on `api.github.com`, at most every six hours, in the background (a page never waits for
it). GitHub sees your server's address and a `User-Agent` of `teslamate-expenses/<version>`; nothing
from your data is sent. If GitHub can't be reached, the footer simply shows the version and tries
again later. Set `UPDATE_CHECK=0` in `.env` and restart to switch the check off.

The OneDrive button on the expense form shows OneDrive's logo, and the header's Dashboards link shows
Grafana's. The logos aren't shipped with the app: the
first time one is needed, the server asks `cdn.jsdelivr.net` once for a pinned copy from the
[selfh.st/icons](https://selfh.st/icons) collection (licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)), checks that what comes back is a plain drawing, keeps it in memory and passes it to browsers. Nothing
from your data is sent (jsDelivr sees your server's address). If it can't be reached, a plain drawing
that comes with the app (a cloud, a bar chart) is shown instead, and the server tries again later. Set `PROVIDER_ICONS=0` in
`.env` and restart to never ask. The drawing is shown as the collection supplies it, except that the
app rebuilds it from the parts it checked and writes its inline styles as plain attributes. OneDrive is a
trademark of Microsoft Corporation; Grafana is a trademark of Grafana Labs.

An installation on a release candidate (`0.3.0-rc1`) is told about newer candidates and the stable
release that replaces them. One on a stable release is only told about stable releases.

## Database connection

The app connects as `DATABASE_USER` with `DATABASE_PASS` to `DATABASE_NAME` on `DATABASE_HOST` (default `database`, the TeslaMate service name) and `DATABASE_PORT` (default `5432`). Set the last two in `.env` only if TeslaMate's database runs under another name or port.

## Logs

`docker logs` shows gunicorn's request lines plus the app's own events. `LOG_LEVEL` (`debug`, `info` the default, `warning`, `error`; set in `.env`, restart to change) sets both. `info` records startup, the update check's result, schema changes, expenses saved, updated or deleted, OneDrive sign-in and upload results, and anything the invoice folder on the server refuses (a missing folder, no permission). `debug` adds detail such as retries. Tokens, secrets, vendors, notes and amounts are never logged, except that file names, which can contain a vendor, appear at `debug`.

## Timezone

Date and time are entered in `APP_TZ` and stored as `timestamptz`. TeslaMate's `positions.date` is naive UTC; the odometer lookup converts before comparing.

## CSRF protection

POSTs must carry a same-origin `Origin`/`Referer` header (CSRF guard). Plain `curl` needs `-H 'Origin: https://your-host'`.

## Not covered yet

Depreciation or resale value (so "TCO" is spend, not net cost), deleting invoice files from the app (it removes the link; the file stays), deleting categories.
