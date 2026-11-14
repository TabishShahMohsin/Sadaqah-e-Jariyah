"""Download a Telegram video that stalls near the end, keeping every byte Telegram will hand over.

The Telegram apps give up on a file whose last piece fails and throw away what they had. This asks Telegram's API for
the file piece by piece, retries the failing piece in smaller sizes, and keeps whatever came through, so a video that
stops at 215.4 of 216.4 MB can still be repaired and watched.

One-time setup (on your Mac, in Terminal):
    pip3 install telethon
    Get an api_id and api_hash at https://my.telegram.org  (API development tools → create any app)

Run:
    python3 tools/telegram_partial.py
It asks for the api_id, api_hash, your phone number and the login code the first time (the login is kept in
~/.sej-telegram.session so later runs don't ask again). The file lands in ~/Downloads.
"""
import asyncio, getpass, os, sys

CHAT = -1002160483528          # the "Material" group
MESSAGE = 257                  # "Expt No. 9" video (17:28)
OUT = os.path.expanduser("~/Downloads/Expt-9_Galvanometer_partial.mp4")
SESSION = os.path.expanduser("~/.sej-telegram")

async def main():
    from telethon import TelegramClient, errors
    api_id = os.environ.get("TG_API_ID") or input("api_id: ").strip()
    api_hash = os.environ.get("TG_API_HASH") or getpass.getpass("api_hash (hidden): ").strip()
    client = TelegramClient(SESSION, int(api_id), api_hash)
    await client.start()
    msg = await client.get_messages(CHAT, ids=MESSAGE)
    if not msg or not msg.media:
        sys.exit(f"message {MESSAGE} has no media - is this account in the Material group?")
    doc = msg.document
    total = doc.size
    print(f"{msg.message or 'video'}: {total/1e6:.1f} MB")
    done = os.path.getsize(OUT) if os.path.exists(OUT) else 0
    done -= done % 4096                       # resume on a 4 KB boundary
    with open(OUT, "r+b" if done else "wb") as fh:
        fh.seek(done); fh.truncate()
        steps = [512 * 1024, 128 * 1024, 32 * 1024, 4096]   # Telegram serves pieces of 4 KB multiples within 1 MB
        step, fails = steps[0], 0
        while done < total:
            try:
                chunk = b""
                async for part in client.iter_download(msg.media, offset=done, request_size=step, chunk_size=step, limit=1):
                    chunk = part
                if not chunk:
                    raise ValueError("empty piece")
                fh.write(chunk); done += len(chunk); fails = 0
                print(f"\r  {done/1e6:7.1f} / {total/1e6:.1f} MB", end="", flush=True)
            except errors.FileReferenceExpiredError:
                msg = await client.get_messages(CHAT, ids=MESSAGE)
            except Exception as e:                # the bad piece: try it in smaller sizes, then give up on the rest
                fails += 1
                if step > steps[-1]:
                    step = steps[steps.index(step) + 1]
                    print(f"\n  piece at {done/1e6:.1f} MB failed ({type(e).__name__}); trying {step//1024} KB pieces")
                elif fails >= 5:
                    print(f"\n  Telegram won't send anything past {done/1e6:.2f} MB; keeping what came through.")
                    break
                await asyncio.sleep(2)
    await client.disconnect()
    print(f"\nSaved {done/1e6:.2f} of {total/1e6:.2f} MB to {OUT}")

asyncio.run(main())
