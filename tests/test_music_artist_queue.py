import asyncio, os, sys, types
ROOT=r"C:\Users\Geggais and Rick\Documents\Codex\olive_assist_v2"; sys.path.insert(0,ROOT); os.chdir(ROOT)
import olive_hub as oh

class FakeMA:
    enabled=True
    def __init__(self): self.queued=None
    async def search(self,query,limit=20):
        return {"tracks":[
          {"name":"Song A","uri":"yt://a"},
          {"name":"Song B","uri":"yt://b"},
          {"name":"No uri","item_id":"c","provider":"yt"},
        ]}
    async def queue_uris(self,uris): self.queued=uris; return True
    async def stop(self): return True

ma=FakeMA()
ctx=types.SimpleNamespace(music_assistant=ma, tv=types.SimpleNamespace(), http=None)
r=asyncio.run(oh._ma_artist_reply("Lil Wayne",ctx))
print("reply:",r.text if r else None)
print("queued:",ma.queued)
assert ma.queued==["yt://a","yt://b","yt://c"]
print("PASS")
