# OneDrive uploads

With this on, the expense form takes the invoice PDF itself. The app uploads it to a subfolder of
the folder you chose for that car, names it from the expense (`261107 Tyres - Tyre Shop.pdf`) and
stores the file's OneDrive link as the expense's invoice link. It works with a personal Microsoft
account (OneDrive Personal).

## Turning it on

1. **Add an encryption key.** Add a key to `.env` and restart the app, which creates its own tables:

    ```bash
    EXPENSES_ENCRYPTION_KEY=<output of: openssl rand -base64 32 | tr '+/' '-_'>
    ```

    The key encrypts the client secret and the saved sign-in (a refresh token for your whole
    OneDrive) before they go into the database, so a database dump or backup on its own can't
    open the drive. Keep the key out of those backups. If you lose or change it, enter the
    secret and connect again.

2. **Register an app with Microsoft** (once per installation; see
    [Registering the app](#registering-the-app) below), then enter its client ID and secret on
    the app's **OneDrive** page. The page shows the exact redirect URI to register.

3. **Connect OneDrive.** You sign in on Microsoft's page and come straight back. The app asks
    for delegated `Files.ReadWrite` and `offline_access`.

4. **Choose a folder for each car** by browsing your OneDrive, creating one with **New folder**
    if it doesn't exist yet. Uploads go by the folder's ID, so renaming or moving it later
    doesn't break them.

<!-- screenshot: onedrive-page - The OneDrive page, connected, with a folder chosen for a car -->

## Where files go

Each invoice goes into a subfolder of the car's folder, set per category on the **Categories** page.
The standard categories start mapped (Tyres, filters, alignment and rotation to `Maintenance`;
Insurance and Registration to folders of the same name); a blank box means a folder named after the
category. A subfolder is made when first needed, and found again whatever its case.

## File names

Files are named `%date %category - %vendor` by default (date, category, vendor). An existing name gets
a suffix; nothing is ever overwritten. The date part is `yymmdd` unless you change it under
**Settings → Invoice file name date** (for example `yyyy-mm-dd`). The whole name is a template under
**Settings → Invoice file name**, a single line of `%` aliases:

| Alias | Becomes |
|---|---|
| `%date` | the invoice date, in the date format above |
| `%category` | the category, e.g. Tyres |
| `%vendor` | the vendor or shop (left out when blank) |
| `%car` | the car's name |
| `%amount` | the amount paid, e.g. 1234.50 |
| `%%` | a percent sign |

For example `%date - %category - %vendor` gives `261107 - Tyres - Tyre Shop.pdf`. It must include
`%date` and `%category` or `%vendor`. A blank alias is left out together with the separator before it
(no vendor gives `261107 - Tyres.pdf`). It applies to new uploads and to renames on import; files
already in OneDrive keep their names.

## Choosing a file already in OneDrive

If the invoice is already in OneDrive, press **Choose from OneDrive** in the invoice box instead.
A window opens over the form, in the car's folder: open a folder to go into it, or **Up** to go above
it, all the way to the top of your drive. Choosing a PDF makes its link the invoice link and fills in
the form from what can be read in it, as an uploaded file does. Nothing is copied or moved.

<!-- screenshot: form-onedrive-picker - The OneDrive window on the Add expense form, listing folders and PDFs -->

## Limits

PDFs only (checked by content, not just the name), up to 20 MB. Uploading on the edit page
replaces the link, and the page says so after saving; the old file stays in OneDrive. If OneDrive
isn't connected, or the car has no folder, the expense is still saved and the notice says the PDF wasn't
uploaded. Disconnecting deletes the saved sign-in;
to revoke the app's access entirely, remove it at
[account.live.com/consent/Manage](https://account.live.com/consent/Manage).

## Registering the app

Any Microsoft account can register the app, including the personal one that owns the OneDrive.
It needs no paid Azure subscription: Microsoft creates a free directory for personal-account
registrations. The account choice that matters comes later, at **Connect OneDrive**, where you
sign in as the account whose OneDrive gets the invoices.

1. In the [Microsoft Entra admin center](https://entra.microsoft.com), go to **Identity** →
    **Applications** → **App registrations** → **New registration**.
2. **Name:** anything, e.g. `teslamate-expenses`.
3. **Supported account types:** **Personal Microsoft accounts only**. "Accounts in any
    organizational directory and personal Microsoft accounts" also works.
4. **Redirect URI:** platform **Web**, and the address shown on the app's OneDrive page,
    `https://<your host><BASE_PATH>/onedrive/callback`. It must match byte for byte, scheme
    and path included, and Microsoft accepts plain `http://` only for `localhost`. Then
    **Register**.
5. **Authentication:** leave **Allow public client flows** at **No** (the default). Turning it on
    makes Microsoft treat the app as a public client, and every token refresh then fails with
    `AADSTS90023: Public clients can't send a client secret`.
6. **API permissions** (optional): **Add a permission** → **Microsoft Graph** →
    **Delegated permissions** → `Files.ReadWrite` and `offline_access`. If you skip this, you're
    asked to consent to the same two at the first sign-in.
7. **Certificates & secrets** → **Client secrets** → **New client secret**. Copy its **Value**
    straight away; it's shown only once, and the **Secret ID** next to it is not the secret.
    Secrets last at most 24 months, so note the **Expires** date.
8. From **Overview**, copy the **Application (client) ID**. It isn't secret.
9. On the app's **OneDrive** page, paste the client ID, the secret's value and its expiry date,
    then press **Connect OneDrive**.

A dev stack or second installation needs its own callback address: add it as another **Web**
redirect URI on the same registration.

A redirect URI you have just added can take a few minutes to take effect. If **Connect OneDrive**
shows Microsoft's "invalid request" error straight after adding one, and the address matches
exactly, wait a few minutes and press it again.

Why the normal web sign-in and not a device code or the "nativeclient" redirect: both have
been tried with personal accounts and fail. A device-code sign-in marks the app as a public
client and breaks refreshes (`AADSTS90023` above). Microsoft's out-of-band
`https://login.microsoftonline.com/common/oauth2/nativeclient` page now redirects to an error
page after a few seconds, which made rclone and the
[abraunegg/onedrive](https://github.com/abraunegg/onedrive/discussions/3558) client drop it too.

References: [Register an application](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app) ·
[Authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow) ·
[Graph permissions](https://learn.microsoft.com/en-us/graph/permissions-reference) ·
[Remove an app's access to your account](https://account.live.com/consent/Manage)

Every installation registers its own app because Microsoft only redirects to addresses
registered on the app, and each installation has its own. Sign-in uses PKCE, and the
callback must return to the browser that started it.

The redirect URI is built from the proxy's `X-Forwarded-Proto` and `X-Forwarded-Host`
headers, so the proxy must set them (Traefik does).

