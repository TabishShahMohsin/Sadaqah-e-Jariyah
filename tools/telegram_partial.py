"""Download a Telegram video that stalls, keeping every byte Telegram will hand over.

The Telegram apps give up on a file when one piece fails and throw away what they had. This asks Telegram's API for
the file in 512 KB pieces; a piece that keeps failing is skipped (and retried at the end), so everything else still
arrives. Re-running continues where the last run stopped. A video with a few missing pieces can then be repaired.

One-time setup (on your Mac, in Terminal):
    pip3 install telethon
    Get an api_id and api_hash at https://my.telegram.org  (API development tools → create any app)

Run:
    python3 tools/telegram_partial.py
The first run asks for the api_id, api_hash, your phone number and the login code (the login is kept in
~/.sej-telegram.session). The file lands in ~/Downloads, with a .parts file recording which pieces arrived.
"""
import asyncio, getpass, json, os, sys

CHAT = -1002160483528          # the "Material" group
MESSAGE = 257                  # "Expt No. 9" video (17:28)
OUT = os.path.expanduser("~/Downloads/Expt-9_Galvanometer_partial.mp4")
STATE = OUT + ".parts"
SESSION = os.path.expanduser("~/.sej-telegram")
PIECE = 512 * 1024
TRIES = 3

async def fetch(client, media, offset, size):
    data = b""
    async for part in client.iter_download(media, offset=offset, request_size=size, chunk_size=size, limit=1):
        data = part
    return data

async def main():
    from telethon import TelegramClient
    api_id = os.environ.get("TG_API_ID") or input("api_id: ").strip()
    api_hash = os.environ.get("TG_API_HASH") or getpass.getpass("api_hash (hidden): ").strip()
    client = TelegramClient(SESSION, int(api_id), api_hash, request_retries=2, timeout=20)
    await client.start()
    msg = await client.get_messages(CHAT, ids=MESSAGE)
    if not msg or not msg.media:
        sys.exit(f"message {MESSAGE} has no media - is this account in the Material group?")
    total = msg.document.size
    pieces = list(range(0, total, PIECE))
    got = set(json.load(open(STATE))) if os.path.exists(STATE) else set()
    if not os.path.exists(OUT):
        open(OUT, "wb").close()
    if os.path.getsize(OUT) and not got:      # a file from the first version of this script: its start is good
        have = os.path.getsize(OUT) - os.path.getsize(OUT) % PIECE
        got = {p for p in pieces if p + PIECE <= have}
    print(f"video: {total/1e6:.1f} MB, {len(got)}/{len(pieces)} pieces already here")
    failed = []
    with open(OUT, "r+b") as fh:
        for rnd in range(2):                  # one pass over everything, then one more over the pieces that failed
            todo = [p for p in pieces if p not in got] if rnd == 0 else failed
            failed = []
            for p in todo:
                size = min(PIECE, total - p)
                req = PIECE if size == PIECE else ((size + 4095) // 4096) * 4096
                for attempt in range(TRIES):
                    try:
                        data = await fetch(client, msg.media, p, req)
                        if data:
                            fh.seek(p); fh.write(data[:size]); got.add(p); break
                    except Exception as e:
                        if "FileReference" in type(e).__name__:
                            msg = await client.get_messages(CHAT, ids=MESSAGE)
                        await asyncio.sleep(1)
                else:
                    failed.append(p)
                    print(f"\n  piece at {p/1e6:.1f} MB failed, moving on")
                json.dump(sorted(got), open(STATE, "w"))
                print(f"\r  {len(got)}/{len(pieces)} pieces ({len(got)*PIECE/1e6:.0f} MB)", end="", flush=True)
            if not failed:
                break
        fh.truncate(total)
    await client.disconnect()
    missing = [p for p in pieces if p not in got]
    print(f"\nSaved {OUT}\n{len(pieces)-len(missing)}/{len(pieces)} pieces; missing:",
          ", ".join(f"{p/1e6:.1f} MB" for p in missing) or "none - the file is complete")

asyncio.run(main())
