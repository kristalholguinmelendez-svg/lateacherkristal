import pytest

from capcut_mcp import captions as cap


def test_timestamps_round_trip():
    assert cap.format_timestamp(3725.5) == "01:02:05,500"
    assert cap.parse_timestamp("01:02:05,500") == pytest.approx(3725.5)
    assert cap.parse_timestamp("0:03.5") == 3.5
    assert cap.parse_timestamp(7) == 7.0


def test_cues_to_srt_and_back():
    cues = [cap.Cue(0, 1.5, "Hello"), cap.Cue(2, 3, "Apple\nManzana")]
    srt = cap.cues_to_srt(cues)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,500\nHello\n")
    assert cap.parse_srt(srt) == cues


def test_rejects_bad_cue():
    with pytest.raises(ValueError):
        cap.cues_to_srt([cap.Cue(2, 1, "x")])


def test_auto_time_is_sequential_and_wrapped():
    cues = cap.auto_time_script("Hello class. Today we learn the names of fruits and vegetables in English!")
    assert len(cues) >= 2
    for a, b in zip(cues, cues[1:]):
        assert b.start > a.end
    assert all(len(line) <= 40 for c in cues for line in c.text.split("\n"))


def test_bilingual_requires_matching_count():
    cues = cap.auto_time_script("Apple. Banana.")
    out = cap.bilingual_cues(cues, ["Manzana.", "Plátano."])
    assert out[1].text == "Banana.\nPlátano."
    with pytest.raises(ValueError):
        cap.bilingual_cues(cues, ["only one"])


def test_shift():
    cues = [cap.Cue(1, 2, "a")]
    assert cap.shift_cues(cues, 0.5)[0].start == 1.5
    with pytest.raises(ValueError):
        cap.shift_cues(cues, -5)
