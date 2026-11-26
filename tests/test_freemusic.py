import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import freemusic


def test_seconds_parser():
    assert freemusic._seconds(127) == 127
    assert freemusic._seconds("127") == 127
    assert freemusic._seconds("04:29") == 269
    assert freemusic._seconds("1:02:03") == 3723
    assert freemusic._seconds("") == 0
    assert freemusic._seconds("bad") == 0


def test_mainstream_sources_registered():
    assert "itunes" in freemusic.PROVIDERS
    assert "deezer" in freemusic.PROVIDERS
    assert "audius" in freemusic.PROVIDERS


def test_track_fields():
    t = freemusic.Track("audius", "id", "Title", "Artist", "Album", "Hip Hop",
                        120, "https://example/stream.mp3", "CC-BY", "https://art")
    d = t.__dict__
    for field in ("source", "id", "title", "artist", "album", "genre", "duration",
                  "stream_url", "license", "art"):
        assert field in d, field


if __name__ == "__main__":
    test_seconds_parser()
    test_track_fields()
    print("PASS")
