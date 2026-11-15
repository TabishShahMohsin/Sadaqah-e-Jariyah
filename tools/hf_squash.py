#!/usr/bin/env python3
"""Squash the dataset's history into one commit made by the account in catalog/hf-config.json,
and replace the old release tags (which still point at the old commits) with one fresh tag.

PERMANENT: old commits and tags can't be brought back. Run it yourself, once, after the
dataset has been moved to the organization and hf-config.json holds the NEW account's token.

    python3 tools/hf_squash.py            # shows what it would do
    python3 tools/hf_squash.py --yes      # does it
"""
import json, sys, datetime, pathlib
from huggingface_hub import HfApi

cfg = json.loads((pathlib.Path(__file__).resolve().parent.parent / "catalog/hf-config.json").read_text())
repo, branch = cfg["repo_id"], cfg.get("branch", "main")
api = HfApi(token=cfg["token"])
me = api.whoami()["name"]
OLD = "TabishShahMohsin"

if repo.split("/")[0] == OLD:
    sys.exit(f"repo_id is still {repo}. Move the dataset to the organization and update repo_id first.")
if me == OLD:
    sys.exit("The token in hf-config.json belongs to your personal account. Use the new account's token, "
             "otherwise the squashed commit is authored by your personal account again.")

refs = api.list_repo_refs(repo, repo_type="dataset")
tags = [t.name for t in refs.tags]
authors = {a for c in api.list_repo_commits(repo, repo_type="dataset", revision=branch) for a in (c.authors or [])}
new_tag = "v" + datetime.date.today().isoformat()
print(f"dataset: {repo}   acting as: {me}")
print(f"commit authors now on {branch}: {sorted(authors)}")
print(f"old tags to delete ({len(tags)}): {', '.join(tags) or '-'}")
print(f"will squash {branch} into one commit by {me}, then tag it {new_tag}")
if "--yes" not in sys.argv:
    sys.exit("Nothing changed. Run again with --yes to do it.")

api.super_squash_history(repo, repo_type="dataset", branch=branch,
                         commit_message="Sadaqah-e-Jariyah archive")
for t in tags:
    api.delete_tag(repo, tag=t, repo_type="dataset")
if new_tag not in tags:
    api.create_tag(repo, tag=new_tag, revision=branch, repo_type="dataset")
authors = {a for c in api.list_repo_commits(repo, repo_type="dataset", revision=branch) for a in (c.authors or [])}
print(f"done. commit authors on {branch}: {sorted(authors)}; tag {new_tag}")
