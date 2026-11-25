# Lucky Draw History

Phone lookup for Reno Fiesta and IDLah lucky draw wins. Staff enter a homeowner or designer number and see only that number’s rows. Admins sign in and see the full list.

Winner history is built in memory from three sources:

- `data/pre_renocode.csv.enc.b64.txt` — winners from before Renocode
- Supabase REST (`events`, `attendees`, `attendees_rf23_draft_backup`, `audit_log`), read with GET only
- `data/form_responses.csv.enc.b64.txt` — Google Form responses

The service pulls Supabase when it starts and again every 10 minutes. A failed pull keeps the last good list. Each encrypted file is base64 of a 12-byte IV plus AES-256-GCM ciphertext and tag (no extra data). `DATA_KEY` is the base64 form of the 32-byte key.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export ADMIN_PASSWORD="choose-a-password"
export SESSION_SECRET="choose-a-long-random-string"
export DATA_KEY="base64-32-byte-key"
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="service-role-key"
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-43123}"
```

The app still starts if those variables are missing. Admin sign-in stays closed without `ADMIN_PASSWORD`. Designer phone maps stay empty until `data/designer_constants.json.enc.b64.txt` can be decrypted. Health check: `GET /healthz`.

The encrypted history files are not in this repository yet. Add them before you expect past winners:

- `data/pre_renocode.csv.enc.b64.txt`
- `data/form_responses.csv.enc.b64.txt`
- `data/designer_constants.json.enc.b64.txt`

With Supabase credentials set, live Renocode rows still load. Without those credentials and without the files, a lookup reports that the number has no records.

## Encrypt a file

`DATA_KEY` is read from the environment, not from the command arguments.

```bash
DATA_KEY="base64-32-byte-key" python tools/encrypt_file.py plain.csv data/pre_renocode.csv.enc.b64.txt
```

Designer constants use the same format. The JSON object has `VERIFIED_TYPO_CANONICAL` and `WHATSAPP_KNOWN_NUMBERS` (keys are `Firm|designer name`) and `FORM_SPOUSE_MATCH_TO_EXISTING` (form row number to source, event code, firm, and phone). Until that file is added, the maps are empty.

## Tests

```bash
pytest
```

## Render

`render.yaml` defines a free web service in Singapore. In the Render dashboard, set:

| Variable | Purpose |
| --- | --- |
| `ADMIN_PASSWORD` | Admin sign-in |
| `SESSION_SECRET` | Signs the session and lookup cookies |
| `DATA_KEY` | Decrypts the CSV and constants files |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Read-only use inside the app (GET only) |

Build command: `pip install -r requirements.txt`

Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Free instances sleep after a quiet period. The list is rebuilt on the next boot.
