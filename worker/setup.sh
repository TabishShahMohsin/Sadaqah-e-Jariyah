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

say "2/7  Private inbox bucket (R2: sej-inbox)"
if ! out=$($W r2 bucket create sej-inbox 2>&1); then
  case "$out" in
    *already*|*exists*) echo "sej-inbox already exists" ;;
    *) echo "$out"; echo "If R2 isn't enabled yet: Cloudflare dashboard → R2 Object Storage → enable it (free tier), then run this again."; exit 1 ;;
  esac
fi

say "3/7  Database (D1: sej-intake)"
if ! grep -q 'database_id = "[0-9a-f-]\{36\}"' wrangler.toml; then
  $W d1 create sej-intake >/dev/null 2>&1 || true
  ID=$($W d1 info sej-intake --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["uuid"])')
  python3 - "$ID" <<'PY'
import re, sys
s = open("wrangler.toml").read()
s = re.sub(r'database_id = "[^"]*"', f'database_id = "{sys.argv[1]}"', s)
open("wrangler.toml", "w").write(s)
PY
  echo "database id $ID written to wrangler.toml"
fi
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
