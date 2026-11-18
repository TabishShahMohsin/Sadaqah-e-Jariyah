#!/bin/sh
# Deploy the website. Staging first, look at it, then production.
#
#   tools/deploy.sh staging      build _dist-staging  → https://staging.sej.org.in
#                                file links read the dataset's main; noindex; features on trial
#   tools/deploy.sh production   build _dist          → https://sej.org.in
#                                file links pinned to the newest release tag (run hf_publish.py push first)
#
# A bad production deploy is undone in the Cloudflare dashboard: Workers & Pages → sadaqah-e-jariyah →
# Deployments → an earlier production deployment → Rollback.
set -e
cd "$(dirname "$0")/.."
case "$1" in
  staging) out=_dist-staging ;;
  production) out=_dist ;;
  *) echo "usage: tools/deploy.sh staging|production" >&2; exit 1 ;;
esac
python3 tools/hf_publish.py site --env "$1"
rm -f "$out/brand/intro.webp"
npx wrangler pages deploy "$out" --project-name sadaqah-e-jariyah --branch "$1"
