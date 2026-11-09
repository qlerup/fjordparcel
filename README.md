# FjordParcel

A self-hosted parcel tracker that scans connected mailboxes and collects tracking updates in one dashboard. The interface currently uses Danish labels.

## Features

- Microsoft/Hotmail/Outlook OAuth with IMAP, and Gmail OAuth with the Gmail API.
- Multiple connected mail accounts, individual scan settings, background scanning and scan progress.
- Parcel extraction for DAO, PostNord, GLS and Bring. The mail parser also recognizes FedEx references and tracking links.
- Manual parcel entry, labels, refresh, archive/restore and hide/unhide controls.
- Optional background mail scanning and tracking refresh; active parcels are checked every 5 minutes when tracking refresh is enabled, independently of mail scanning.
- All parcel status updates use the official 17TRACK API and the existing encrypted API key. Carrier websites are not used for tracking lookups.
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

Gmail read scopes are restricted. For private testing, keep the OAuth app in testing and add the intended Gmail address as a test user. Provider consent, callback configuration and carrier API availability remain prerequisites. Save the shared 17TRACK key in carrier settings; extraction from email and live tracking are separate operations.

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

### Manual tracking and 17TRACK

Use **Tilføj trackingnummer** at the top of the parcel overview; an optional label helps identify the parcel. Duplicate numbers refresh the existing entry. The shared integration first requests existing tracking information and registers previously unknown numbers for automatic carrier detection. New registrations are queried immediately. If 17TRACK is still collecting events, a durable background worker retries once a minute, independently of the normal auto-refresh setting. An open overview updates automatically when the result is ready; editing a form prevents an automatic reload. API failures retain the last known shipment details and display an error.

The API key under **Indstillinger → Fragtfirmaer** applies to every carrier. Existing encrypted keys are reused. Registration is subject to your 17TRACK account quota; FjordParcel does not fall back to scraping carrier websites. See the [17TRACK v2.2 API documentation](https://asset.17track.net/api/document/v2.2_en/index.html).

### Forventet levering

17TRACKs `time_metrics.estimated_delivery_date` gemmes ved trackingopdatering.
En dato, et datointerval eller en tidligst-/senest-dato vises under seneste hændelse
for pakker på vej. Datoerne følger fragtfirmaets angivne kalenderdato, uden at
midnat eller tekniske tidsstempler vises som et præcist leveringstidspunkt.
Markøren viser, om estimatet kommer fra fragtfirmaet eller 17TRACK. Manglende dato
vises som **Ikke oplyst endnu**. En fejlet API-opdatering bevarer seneste estimat;
et vellykket svar uden dato fjerner det. Afhentningsklare og leverede pakker viser
ikke længere forventet levering. Ingen ekstra API-kald er nødvendige.

### Bot notifications

Managed installations send new parcel registrations and new 17TRACK events through FjordHub's existing Matrix bot recipients. Update both FjordHub and FjordParcel. Messages include parcel label, carrier, tracking number and Danish event details when supplied by 17TRACK. Delivery is queued transactionally and retried if Hub or the bot is unavailable. Repeated lookups and translation-only changes do not send duplicates; first lookup announces only the latest event. Hidden or deleted parcels are not sent from the pending outbox. Existing shipments are not announced again at installation.

### Rutekort ved hændelser

Når en pakke foldes ud, indlæses et interaktivt Leaflet-kort som i Urban Explorer.
Vælg mellem OpenStreetMap-vejkort og Esri-satellit, zoom eller åbn et større kort.
Kendte byer vises i hændelsesrækkefølge, og orange markerer senest kendte sted.
Ukendte eller tvetydige stednavne springes over. Byopslaget bruger bundlet GeoNames;
se `map_data/README.md` for dækning, licenser og opdatering af datagrundlaget.

Den eksterne Valhalla-server beregner foreslåede lastbilruter mellem efterfølgende
byer. Der installeres ingen vejnetdata lokalt. Det er en illustration af mulig
transport, ikke transportørens faktiske GPS-rute. Vellykkede delruter gemmes uden
udløb i SQLite-tabellen `parcel_route_cache` og genbruges på tværs af pakker,
sidevisninger og genstarter. Retning, server og profil indgår i cachenøglen.
Kun nye delruter beregnes, når en ny scanning kommer til. Database-leases forhindrer
parallelle forespørgsler i at beregne samme delrute. Beregningen kører i baggrunden
med to arbejdstråde og højst 32 ventende/beregnende jobs pr. proces. Der startes
højst 64 delruter pr. kortvisning. Midlertidige fejl forsøges igen efter en time,
manglende vejforbindelser efter et døgn. Stiplede linjer viser forbindelser uden
vejdata, fx ved flytransport eller manglende dækning.

`FJORDPARCEL_VALHALLA_URL` vælger ruteserver (standard:
`https://valhalla1.openstreetmap.de`). En tom værdi slår ruteberegning fra.
Kun bykoordinater og lastbilprofil sendes til ruteserveren, ingen trackingnumre
eller hændelsestekster. Browseren henter kortfliser fra den valgte kortudbyder.
Leaflet 1.9.4 med BSD-licens er bundlet under `static/vendor/leaflet`.

Nye trackinghændelser med genkendelige steder sender samme PNG-kort gennem
FjordHubs botkø og som krypteret Matrix-billede. Begge apps og Matrix-botten
skal opdateres. Allerede afsendte hændelser genudsendes ikke. Hvis der ingen
kendte steder er, sendes tekstbeskeden alene. Hvis korttegningen eller en ny
vejstrækning ikke er klar, venter botbeskeden i køen og forsøges igen.

### Danske hændelsestekster

Kendte hændelser fra fragtfirmaerne får faste danske formuleringer i
`tracking_text.py`, før 17TRACKs maskinoversættelse anvendes. Ukendte beskeder
bevarer udbyderens detaljer. Originalteksten gemmes til senere forbedringer.
Bekræftede fejloversættelser i eksisterende historik rettes ved visning og ved
levering af ventende botbeskeder, uden at gamle hændelser genudsendes.

### Samme kort i appen og botbilleder

`static/parcel-map.js` tegner både appens interaktive kort og PNG-billedet til
botten. Begge bruger OpenFreeMap Liberty, de samme markører og den fælles
rutecache fra `parcel_routes.py`. Satellitlaget er fortsat tilgængeligt i appen.
`parcel_map_image.py` tilpasser billedformatet til rutens retning og tager et skærmbillede med den installerede
Playwright-browser. Billeder caches under datamappen i `map-images/`, op til
256 billeder. Trackingnumre og hændelsestekster sendes ikke til kortudbyderen.
MapLibre GL JS 4.7.1 og MapLibre GL Leaflet 0.1.3 er bundlet under
`static/vendor/maplibre` med deres licenser. OpenFreeMap: https://openfreemap.org/ .
Til lokal browserkontrol kan `FJORDPARCEL_MAP_BROWSER=/usr/bin/chromium` bruges.

Kortets zoom og billedformat følger alle rutens koordinater. Lodrette rejser
vises i et højt udsnit, vandrette i et bredt udsnit, med plads til bynavne og
markører. Samme tilpasning bruges i appen og i botbilledet.
