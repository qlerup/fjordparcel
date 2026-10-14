# FjordParcel

A self-hosted parcel tracker that scans connected mailboxes and collects tracking updates in one dashboard. The interface currently uses Danish labels.

## Features

- Microsoft/Hotmail/Outlook OAuth with IMAP, and Gmail OAuth with the Gmail API.
- Multiple connected mail accounts, individual scan settings, background scanning and scan progress.
- Parcel extraction for DAO, PostNord, GLS and Bring. The mail parser also recognizes FedEx references and tracking links.
- Manual parcel entry, labels, refresh, archive/restore and hide/unhide controls.
- Optional background mail scanning and tracking refresh; tracking updates run every 30 minutes when enabled.
- Carrier-specific postal-code settings and configurable 17TRACK integration for PostNord tracking.
- Administrator and regular-user roles, hashed passwords and temporary login lockout.
- FjordHub shared accounts, SSO, first-login onboarding and active-session access checks.
- Administrator-only update checks, update progress and scheduled update checking.

## Install through FjordHub

Select **FjordParcel** in the catalog, choose storage and a free port (default **8096**), then install. Open the app from FjordHub for single-use SSO, or sign in directly with the same account. Managed installations skip local account setup and preserve the user's first/last-name display name. Administrators manage connections, configuration and users; regular users have the parcel view.

First-login email confirmation and replacement of a temporary password can be completed inside the app. Current Hub membership is checked during existing sessions; removed access invalidates the session and Hub outages block protected requests.

An administrator can require an email update in FjordHub. Opening or returning to the app presents a mandatory form, even for existing logins. Saving a valid, unique address clears the shared requirement across connected apps. Update both Hub and Parcel for this flow.

## Standalone installation

```sh
git clone https://github.com/qlerup/fjordparcel.git
cd fjordparcel
cp .env.example .env
# Set a long random SECRET_KEY and your storage/public URL settings.
docker compose up -d --build
```

Open `http://localhost:8096` and create the first administrator. Use HTTPS for public access.

## Configuration

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Flask session key; use a long random value and preserve it. |
| `PUBLIC_BASE_URL` | Optional public URL fallback. Change the saved address under Settings → Mail. |
| `DATA_DIR` | Host data directory. Fresh setup records an absolute path so the updater reuses the same mount. |
| `APP_ENCRYPTION_KEY` | Optional Fernet token-encryption key; generated and stored locally if omitted. Preserve it. |

Database, configuration and encrypted tokens persist in the mounted data directory, normally `./data/`. The container reads its internal data mount, not the host path. Configure the public address after installation so OAuth callback origins match.

## Mail connections

### Microsoft / Hotmail / Outlook

1. Create an app registration in the [Azure portal](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps).
2. Register `<PUBLIC_BASE_URL>/auth/microsoft/callback`.
3. Grant delegated `IMAP.AccessAsUser.All` from Office 365 Exchange Online.
4. Save the client ID and client secret under Settings → Mail → Configure Hotmail, then connect the account.

### Gmail

1. Create an OAuth 2.0 client in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the Gmail API and configure `https://www.googleapis.com/auth/gmail.readonly`.
3. Register `<PUBLIC_BASE_URL>/auth/google/callback`.
4. Save the client ID and client secret under Settings → Mail → Configure Gmail, then connect the account.

Gmail read scopes are restricted. For private testing, keep the OAuth app in testing and add the intended Gmail address as a test user. Provider consent, callback configuration and carrier API availability remain prerequisites. Save the PostNord 17TRACK key in carrier settings; extraction from email and live tracking are separate operations.

## Updates and backup

Update managed deployments through FjordHub. Standalone deployments can use the update UI or rebuild:

```sh
git pull --ff-only
docker compose up -d --build
```

Back up the complete data directory, `.env` and original encryption/session keys. Stop before a file-based SQLite copy. Rebuilds preserve mounted data. Restore compatible code and data together; do not replace keys during an ordinary update.

## Development

```sh
python -m pip install -r requirements.txt
python -m pytest tests -q
```

Flask, SQLite and Gunicorn provide the backend. `app.py` implements routes; `tracking.py` parses carrier references; `storage.py` stores parcels/users/settings; `mail_services.py`, `imap_mail.py` and `gmail_mail.py` implement mail access; `app_config.py` handles settings and encryption. UI and regression tests are in `templates/`, `static/` and `tests/`.

## License

MIT.
