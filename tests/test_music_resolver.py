import os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import olive_hub as oh


def test_aliases():
    assert oh.correct_music_query("budubantan") == "buju banton"
    assert oh.correct_music_query("steven marley") == "stephen marley"
    assert oh.correct_music_query("a little wind") == "lil wayne"


def test_relevance():
    q = "buju banton"
    t = {"artist": "SoliDub", "title": "Buju-Banton Champion Remix", "genre": "Reggae"}
    assert oh._music_relevance(q, t) == 2
    assert oh._music_relevance(q, {"artist": "X", "title": "Y", "genre": "Pop"}) == 0


def test_cache_roundtrip():
    key = "test-cache-key"
    oh._MUSIC_CACHE[key] = {"at": time.time(), "query": key,
                            "tracks": [{"source": "audius", "id": "x", "stream_url": "http://x"}]}
    oh._music_cache_save()
    oh._MUSIC_CACHE.clear()
    oh._music_cache_load()
    assert key in oh._MUSIC_CACHE
    del oh._MUSIC_CACHE[key]
    oh._music_cache_save()


if __name__ == "__main__":
    test_aliases()
    test_relevance()
    test_cache_roundtrip()
    print("PASS")
