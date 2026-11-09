# Reference

## Invoice links

**Invoice links** must be `https://` (rendered as a link, so `javascript:`/`data:` are refused). Whoever opens one needs access to it, and it lasts as long as its sharing settings. An uploaded invoice's link is its plain OneDrive address, not a share link: it opens for you, signed in to that Microsoft account, and for nobody else.

## Logs

`docker logs` shows gunicorn's request lines plus the app's own events. `LOG_LEVEL` (`debug`, `info` the default, `warning`, `error`; set in `.env`, restart to change) sets both. `info` records startup, schema changes, expenses saved, updated or deleted, and OneDrive sign-in and upload results. `debug` adds detail such as retries. Tokens, secrets, vendors, notes and amounts are never logged, except that file names, which can contain a vendor, appear at `debug`.

## Timezone

Date and time are entered in `APP_TZ` and stored as `timestamptz`. TeslaMate's `positions.date` is naive UTC; the odometer lookup converts before comparing.

## CSRF protection

POSTs must carry a same-origin `Origin`/`Referer` header (CSRF guard). Plain `curl` needs `-H 'Origin: https://your-host'`.

## Not covered yet

Depreciation or resale value (so "TCO" is spend, not net cost), storing invoices in the app itself (they go to your cloud storage), deleting categories.
