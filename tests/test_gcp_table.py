import pytest

from aerotools.tools.fastgcp import gcp_table as gt


def _read(text, **kwargs):
    table = gt.parse_text(text, **kwargs)
    return table, gt.guess_columns(table)


def test_french_semicolon_csv_with_header_and_decimal_commas():
    text = "Nom;X;Y;Z\nGCP1;1234567,89;6789012,34;45,6\nGCP2;1234600,10;6789100,00;46,1\n"
    table, mapping = _read(text)
    assert table.delimiter == ";" and table.has_header
    assert mapping == {"name": 0, "x": 1, "y": 2, "z": 3}
    records, rejected = gt.build_records(table, mapping)
    assert rejected == []
    assert records[0]["label"] == "GCP1"
    assert records[0]["x"] == pytest.approx(1234567.89)
    assert records[1]["z"] == pytest.approx(46.1)


def test_english_comma_csv_with_header_words_in_another_order():
    text = "Easting,Northing,Elevation,Point\n100.5,200.5,10.5,A1\n101.5,201.5,11.5,A2\n"
    table, mapping = _read(text)
    assert table.delimiter == ","
    assert mapping == {"name": 3, "x": 0, "y": 1, "z": 2}


def test_headers_with_units_and_accents():
    text = "Désignation;X (m);Y (m);Altitude NGF (m)\nP1;1;2;3\nP2;4;5;6\n"
    _table, mapping = _read(text)
    assert mapping == {"name": 0, "x": 1, "y": 2, "z": 3}


def test_tab_separated_without_header():
    text = "PS_1\t722351.74\t4238810.64\t6.17\nPS_2\t722298.50\t4238754.98\t7.58\n"
    table, mapping = _read(text)
    assert table.delimiter == "\t" and not table.has_header
    assert mapping == {"name": 0, "x": 1, "y": 2, "z": 3}
    assert len(gt.build_records(table, mapping)[0]) == 2


def test_space_separated_with_decimal_commas_is_not_read_as_a_comma_csv():
    text = "GCP1 1234,5 5678,9 12,3\nGCP2 1235,5 5679,9 13,3\n"
    table, mapping = _read(text)
    assert table.delimiter == " "
    records, _ = gt.build_records(table, mapping)
    assert records[0]["x"] == pytest.approx(1234.5)
    assert records[1]["z"] == pytest.approx(13.3)


def test_no_name_column_generates_names_and_z_can_be_missing():
    text = "1000.0;2000.0\n1001.0;2001.0\n"
    table, mapping = _read(text)
    assert mapping == {"name": None, "x": 0, "y": 1, "z": None}
    records, _ = gt.build_records(table, mapping)
    assert [r["label"] for r in records] == ["GCP1", "GCP2"]
    assert records[0]["z"] is None


def test_purely_numeric_point_numbers_in_a_four_column_file():
    text = "1 1000.5 2000.5 30.5\n2 1001.5 2001.5 31.5\n"
    table, mapping = _read(text)
    assert mapping == {"name": 0, "x": 1, "y": 2, "z": 3}


def test_comments_blank_lines_bom_and_quotes():
    data = ('﻿# export du 3 novembre\n\n"Nom";"X";"Y";"Z"\n'
            '"GCP 1";"10,5";"20,5";"3,5"\n// fin\n').encode("utf-8")
    table, mapping = _read(gt.decode(data))
    records, _ = gt.build_records(table, mapping)
    assert [r["label"] for r in records] == ["GCP 1"]
    assert records[0]["x"] == pytest.approx(10.5)


def test_windows_1252_file():
    data = "Nom;X;Y;Z\nCité;1;2;3\n".encode("cp1252")
    assert "Cité" in gt.decode(data)


def test_bad_rows_are_rejected_with_a_reason_and_line_number():
    text = "Nom;X;Y;Z\nGCP1;1;2;3\nGCP2;abc;2;3\nGCP3;1;;3\nGCP4;1;2;xx\n;1;2;3\n"
    table, mapping = _read(text)
    records, rejected = gt.build_records(table, mapping)
    assert [r["label"] for r in records] == ["GCP1"]
    assert rejected == [(3, "x"), (4, "y"), (5, "z"), (6, "name")]


def test_repeated_name_keeps_the_last_line():
    text = "Nom;X;Y;Z\nA;1;1;1\nB;2;2;2\nA;3;3;3\n"
    table, mapping = _read(text)
    records, _ = gt.build_records(table, mapping)
    assert [(r["label"], r["x"]) for r in records] == [("A", 3.0), ("B", 2.0)]


def test_missing_xy_columns_reject_everything():
    table = gt.parse_text("a;b\nc;d\n")
    assert gt.build_records(table, {"name": 0, "x": None, "y": None, "z": None})[1] == [(0, "columns")]


@pytest.mark.parametrize("text, expected", [
    ("1234.5", 1234.5), ("1234,5", 1234.5), ("1.234,5", 1234.5), ("1,234.5", 1234.5),
    ("-0,25", -0.25), ("+3", 3.0), ("1e3", 1000.0), (" 5 ", 5.0),
    ("", None), ("abc", None), ("nan", None), ("1.2.3", None), (None, None),
])
def test_parse_number(text, expected):
    assert gt.parse_number(text) == expected


def test_geographic_detection():
    assert gt.looks_geographic([{"x": 8.74, "y": 41.9}])
    assert not gt.looks_geographic([{"x": 722351.0, "y": 4238810.0}])
    assert not gt.looks_geographic([])


def test_column_choices_use_header_or_first_row():
    table = gt.parse_text("Nom;X\nA;1\n")
    assert gt.column_choices(table) == ["1 · Nom", "2 · X"]
    table = gt.parse_text("A;1\nB;2\n")
    assert gt.column_choices(table) == ["1 · A", "2 · 1"]


def test_wgs84_is_suggested_for_degrees_unless_the_chunk_is_geographic():
    from aerotools.tools.fastgcp import gcp_import

    degrees = [{"x": 8.74, "y": 41.9}]
    meters = [{"x": 722351.0, "y": 4238810.0}]
    assert gcp_import.should_suggest_wgs84(degrees, "projected")
    assert gcp_import.should_suggest_wgs84(degrees, None)
    assert not gcp_import.should_suggest_wgs84(degrees, "geographic")
    assert not gcp_import.should_suggest_wgs84(meters, "projected")
    assert not gcp_import.should_suggest_wgs84([], "projected")


def test_every_rejection_code_has_a_text():
    from aerotools.tools.fastgcp import gcp_import

    assert set(gcp_import.REASON_KEYS) == {"x", "y", "z", "name", "columns"}
    assert gcp_import.reject_text("z")
