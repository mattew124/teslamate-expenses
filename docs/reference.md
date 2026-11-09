# Reference

## Invoice links

**Invoice links** must be `https://` (rendered as a link, so `javascript:`/`data:` are refused). Whoever opens one needs access to it, and it lasts as long as its sharing settings. An uploaded invoice's link is its plain OneDrive address, not a share link: it opens for you, signed in to that Microsoft account, and for nobody else.

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

An installation on a release candidate (`0.3.0-rc1`) is told about newer candidates and the stable
release that replaces them. One on a stable release is only told about stable releases.

## Logs

`docker logs` shows gunicorn's request lines plus the app's own events. `LOG_LEVEL` (`debug`, `info` the default, `warning`, `error`; set in `.env`, restart to change) sets both. `info` records startup, the update check's result, schema changes, expenses saved, updated or deleted, and OneDrive sign-in and upload results. `debug` adds detail such as retries. Tokens, secrets, vendors, notes and amounts are never logged, except that file names, which can contain a vendor, appear at `debug`.

## Timezone

Date and time are entered in `APP_TZ` and stored as `timestamptz`. TeslaMate's `positions.date` is naive UTC; the odometer lookup converts before comparing.

## CSRF protection

POSTs must carry a same-origin `Origin`/`Referer` header (CSRF guard). Plain `curl` needs `-H 'Origin: https://your-host'`.

## Not covered yet

Depreciation or resale value (so "TCO" is spend, not net cost), storing invoices in the app itself (they go to your cloud storage), deleting categories.
