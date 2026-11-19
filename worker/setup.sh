#!/bin/sh
# One-time setup of the contribution intake (worker/) at https://intake.sej.org.in, then open the Contribute page.
# Run from anywhere:  sh worker/setup.sh      (safe to run again: every step skips what already exists)
set -e
cd "$(dirname "$0")"
ROOT=..
W="npx --yes wrangler@4"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "1/7  Cloudflare sign-in"
$W whoami 2>/dev/null | grep -q "logged in" || $W login

say "2/7  File storage: none (links only; R2 would need a paid activation)"

say "3/7  Database (D1: sej-intake)"
d1_id() {  # the database's id from `d1 list --json`, empty if it doesn't exist
  $W d1 list --json 2>/dev/null | python3 -c '
import json, sys
t = sys.stdin.read(); t = t[t.find("["):] if "[" in t else "[]"
try: print(next((d["uuid"] for d in json.loads(t) if d.get("name") == "sej-intake"), ""))
except ValueError: print("")'
}
ID=$(d1_id)
if [ -z "$ID" ]; then
  $W d1 create sej-intake
  ID=$(d1_id)
fi
if [ -z "$ID" ]; then echo "Couldn't create or find the D1 database sej-intake (the error is above)."; exit 1; fi
python3 - "$ID" <<'PY'
import re, sys
s = open("wrangler.toml").read()
s = re.sub(r'database_id = "[^"]*"', f'database_id = "{sys.argv[1]}"', s)
open("wrangler.toml", "w").write(s)
PY
echo "database sej-intake: $ID"
$W d1 execute sej-intake --remote --file=schema.sql -y >/dev/null && echo "tables ready"

say "4/7  Deploy the intake Worker (intake.sej.org.in)"
$W deploy

say "5/7  Admin token (for tools/intake.py)"
CONF="$ROOT/catalog/intake-config.json"
if [ ! -f "$CONF" ]; then
  python3 - "$CONF" <<'PY'
import json, secrets, sys
json.dump({"api": "https://intake.sej.org.in", "admin_token": secrets.token_urlsafe(40)}, open(sys.argv[1], "w"), indent=1)
PY
  echo "written to catalog/intake-config.json (git ignores it)"
fi
python3 -c 'import json,sys; sys.stdout.write(json.load(open(sys.argv[1]))["admin_token"])' "$CONF" | $W secret put ADMIN_TOKEN

say "6/7  Turnstile (the human check on the form)"
echo "In the Cloudflare dashboard: Turnstile → Add widget"
echo "  name: sej.org.in contributions · mode: Managed"
echo "  hostnames: sej.org.in, staging.sej.org.in, sadaqah-e-jariyah.pages.dev"
echo "Then copy its two keys here."
printf 'Site key: '; read SITEKEY
printf 'Secret key (hidden): '; stty -echo; read TSECRET; stty echo; echo
printf '%s' "$TSECRET" | $W secret put TURNSTILE_SECRET
python3 - "$ROOT/catalog/site-config.json" "$SITEKEY" <<'PY'
import json, sys
c = json.load(open(sys.argv[1])); c["turnstile_sitekey"] = sys.argv[2].strip()
json.dump(c, open(sys.argv[1], "w"), indent=1)
PY

say "7/7  GitHub notification (optional)"
echo "An issue in TabishShahMohsin/Sadaqah-e-Jariyah for every contribution, so you get notified."
echo "Needs a fine-grained token: github.com/settings/personal-access-tokens → this repository only → Issues: Read and write."
printf 'Paste it, or press Enter to skip (hidden): '; stty -echo; read GHT; stty echo; echo
[ -n "$GHT" ] && printf '%s' "$GHT" | $W secret put GITHUB_TOKEN

say "Check"
curl -fsS https://intake.sej.org.in/api/health && echo
echo "Done. Open the Contribute page:  tools/deploy.sh production && tools/deploy.sh staging"
