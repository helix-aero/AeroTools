RTKLIB-EX 2.5.1 (the "demo5" fork of RTKLIB by rtklibexplorer), Windows x64 command line programs, copied unchanged from the
official release archive RTKLIB_EX_2.5.1.zip (22 June 2026, https://github.com/rtklibexplorer/RTKLIB/releases).

  rnx2rtkp.exe   post processing positioning (the PPK engine of Aero-PPK)        SHA-256 e3dcb3d7ebc57ed8fb3d48f6be2de70c0744b6ad56913846e4b303297a348926
  convbin.exe    raw receiver log (RTCM 3 `.bin`) -> RINEX                       SHA-256 245647b373cdc95f369dac2187027b36fe7dd0e035c57e2d48582bfb72f75d0b
  crx2rnx.exe    Compact RINEX (Hatanaka) -> RINEX                               SHA-256 a5bf1806ba68fcea8f7c7c00609c08a35a3f1d13ff77066843cdbdc79e59b2f5

Licence
  RTKLIB and RTKLIB-EX: BSD 2-clause, Copyright (c) 2007-2020 T. Takasu (see LICENSE.txt, the file of the project). The
  licence text must travel with the binaries: do not remove it.
  crx2rnx.exe: Y. Hatanaka's Compact RINEX decoder, part of the RNXCMP software of the Geospatial Information Authority of
  Japan, the copy distributed inside the RTKLIB binary packages. The RTKLIB licence says companion binaries keep their own
  licence: see LICENSE_RNXCMP.txt, which must travel with the program. It is redistributed as it is, at no charge.
  Reference: Hatanaka, Y. (2008), A Compression Format and Tools for GNSS Observation Data, Bulletin of the Geospatial
  Information Authority of Japan, 55, 21-30. Aero-PPK needs it for Hatanaka files (.crx / .d).

Aero-PPK runs these programs as separate processes with a generated configuration file. It never modifies them.
Another build can be used instead: put the three programs in a folder and point the setting `aeroppk.rtklib_dir` at it.
