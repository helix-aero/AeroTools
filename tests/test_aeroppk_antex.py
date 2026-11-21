"""Aero-PPK optional base antenna calibration (ANTEX): reading the file, matching the RINEX antenna string, the solver keys."""
import os

from aerotools.tools.aeroppk import antex, rtklib

# Synthetic ANTEX excerpt: the real file is about 60 MB and is not shipped (the layout is the public ANTEX 1.4 one).
ATX = (
    "     1.4            M                                       ANTEX VERSION / SYST\n"
    "A                                                           PCV TYPE / REFANT   \n"
    "                                                            END OF HEADER       \n"
    "                                                            START OF ANTENNA    \n"
    "BLOCK IIR-M         G031                                    TYPE / SERIAL NO    \n"
    "                                                            END OF ANTENNA      \n"
    "                                                            START OF ANTENNA    \n"
    "TRM55971.00     NONE                                        TYPE / SERIAL NO    \n"
    "                                                            END OF ANTENNA      \n"
    "                                                            START OF ANTENNA    \n"
    "LEIAR25.R3      LEIT                                        TYPE / SERIAL NO    \n"
    "                                                            END OF ANTENNA      \n"
)


def write_atx(tmp_path):
    path = tmp_path / "test.atx"
    path.write_text(ATX, encoding="ascii")
    return str(path)


def test_antenna_strings_are_split_into_model_and_radome():
    assert antex.normalise("TRM55971.00     NONE") == ("TRM55971.00", "NONE")
    assert antex.normalise("TRM55971.00") == ("TRM55971.00", "NONE")
    assert antex.normalise("  ") == ("", "")


def test_only_receiver_antennas_are_listed_not_satellites(tmp_path):
    models = antex.available_models(write_atx(tmp_path))
    assert models == {("TRM55971.00", "NONE"), ("LEIAR25.R3", "LEIT")}
    assert antex.available_models(str(tmp_path / "missing.atx")) == set()


def test_lookup_prefers_the_radome_then_none_then_gives_up(tmp_path):
    models = antex.available_models(write_atx(tmp_path))
    assert antex.lookup(models, "LEIAR25.R3      LEIT") == "LEIAR25.R3 LEIT"
    assert antex.lookup(models, "TRM55971.00     SCIS") == "TRM55971.00 NONE"           # radome not calibrated: the bare antenna
    assert antex.lookup(models, "LEIAR25.R3      NONE") is None                          # only the radome version exists
    assert antex.lookup(models, "SOMETHING.ELSE  NONE") is None
    assert antex.lookup(models, "") is None


def _keys(options):
    return {k.strip(): v.strip() for k, v in (line.split("=", 1) for line in rtklib.config_text(options).splitlines())}


def test_solver_keys_are_off_by_default_and_on_with_a_calibrated_antenna():
    plain = _keys(rtklib.Options(base_xyz=(1.0, 2.0, 3.0)))
    assert plain["pos1-posopt2"] == "off" and plain["file-rcvantfile"] == "" and plain["ant2-anttype"] == ""
    keys = _keys(rtklib.Options(base_xyz=(1.0, 2.0, 3.0), antex_file="C:/x/igs20.atx", base_antenna="TRM55971.00 NONE"))
    assert keys["pos1-posopt2"] == "on" and keys["pos1-posopt1"] == "off"
    assert keys["file-rcvantfile"] == "C:/x/igs20.atx" and keys["ant2-anttype"] == "TRM55971.00 NONE"
    assert keys["file-satantfile"] == ""
    # a file without an antenna type changes nothing
    assert _keys(rtklib.Options(base_xyz=(1.0, 2.0, 3.0), antex_file="C:/x/igs20.atx", base_antenna=""))["pos1-posopt2"] == "off"


def test_ensure_reuses_the_downloaded_file(tmp_path, monkeypatch):
    target = tmp_path / "igs20.atx"
    target.write_bytes(b"x" * 1500000)
    monkeypatch.setattr(antex, "cache_path", lambda: str(target))
    called = []
    monkeypatch.setattr(antex.net, "download", lambda *a, **k: called.append(a))
    assert antex.ensure() == str(target) and called == []
    os.remove(str(target))
    antex.ensure()
    assert len(called) == 1 and called[0][0] == antex.URL


def test_the_ngs_file_is_a_second_download_next_to_the_igs_one(tmp_path, monkeypatch):
    assert antex.SOURCES["igs20"] == (antex.URL, "igs20.atx")
    assert antex.SOURCES["ngs20"] == ("https://www.ngs.noaa.gov/ANTCAL/LoadFile?file=ngs20.atx", "ngs20.atx")
    assert os.path.basename(antex.cache_path("ngs20")) == "ngs20.atx" and os.path.basename(antex.cache_path()) == "igs20.atx"
    assert os.path.dirname(antex.cache_path("ngs20")) == os.path.dirname(antex.cache_path())
    folder = tmp_path / "antex"
    monkeypatch.setattr(antex, "cache_path", lambda source="igs20": str(folder / antex.SOURCES[source][1]))
    called = []

    def download(url, target, **kwargs):
        called.append(url)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(b"x" * 1500000)
    monkeypatch.setattr(antex.net, "download", download)
    assert antex.ensure(source="ngs20") == str(folder / "ngs20.atx") and called == [antex.SOURCES["ngs20"][0]]
    antex.ensure(source="ngs20")
    assert len(called) == 1                                                       # kept after the first download


def test_the_first_calibration_file_that_has_the_antenna_wins(tmp_path):
    igs_like = tmp_path / "igs.atx"
    igs_like.write_text(ATX, encoding="ascii")
    ngs_like = tmp_path / "ngs.atx"
    ngs_like.write_text(ATX.replace("TRM55971.00     NONE", "TRM59800.80     NONE").replace("LEIAR25.R3      LEIT", "TRM59800.80     SCIS"), encoding="ascii")
    paths = [str(igs_like), str(ngs_like)]
    assert antex.choose(paths, "TRM55971.00     NONE") == (str(igs_like), "TRM55971.00 NONE")
    assert antex.choose(paths, "TRM59800.80     SCIS") == (str(ngs_like), "TRM59800.80 SCIS")           # only the second file has it
    assert antex.choose(paths, "NOPE            NONE") == (None, None)
    assert antex.choose([None, str(ngs_like)], "TRM59800.80     NONE") == (str(ngs_like), "TRM59800.80 NONE")
    assert rtklib.Options().antex_extra == []
