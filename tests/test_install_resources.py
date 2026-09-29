import io
import zipfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from shapely.geometry import Polygon

from sample_metadata_curation.install_resources import (
    create_final_cc_mapping,
    get_checklist_countries,
    get_ror_download_url,
    main,
    parse_coordinate_cleaner_ref,
    parse_ena_xml,
    parse_natural_earth_country_codes,
    parse_ror_institutions,
    save_natural_earth,
)

DENMARK_ROW = {
    "ISO_A2_EH": "DK",
    "NAME": "Denmark",
    "NAME_CIAWF": "Denmark",
    "NAME_LONG": "Denmark",
    "ADMIN": "Denmark",
    "FORMAL_EN": "Kingdom of Denmark",
    "BRK_NAME": "Denmark",
    "geometry": Polygon([(8, 54), (8, 58), (13, 58), (13, 54)]),
}

SOMALILAND_ROW = {
    "ISO_A2_EH": "-99",
    "NAME": "Somaliland",
    "NAME_CIAWF": "Somaliland",
    "NAME_LONG": "Somaliland",
    "ADMIN": "Somaliland",
    "FORMAL_EN": "Somaliland",
    "BRK_NAME": "Somaliland",
    "geometry": Polygon([(44, 8), (44, 11), (49, 11), (49, 8)]),
}


def _rows(*rows):
    return {key: [r[key] for r in rows] for key in rows[0]}


# ── parse_natural_earth_country_codes ───────────────────────────────────────


def test_parse_natural_earth_country_codes_maps_name_variants(make_ne_zip):
    zip_path = make_ne_zip(_rows(DENMARK_ROW))
    mapping = parse_natural_earth_country_codes(zip_path)
    assert mapping["Denmark"] == "DK"
    assert mapping["Kingdom of Denmark"] == "DK"


def test_parse_natural_earth_country_codes_skips_disputed_placeholder(make_ne_zip):
    # Natural Earth uses ISO_A2_EH "-99" for disputed/unrecognised territories
    zip_path = make_ne_zip(_rows(SOMALILAND_ROW))
    mapping = parse_natural_earth_country_codes(zip_path)
    assert "Somaliland" not in mapping


def test_parse_natural_earth_country_codes_first_name_variant_wins(make_ne_zip):
    zip_path = make_ne_zip(_rows(DENMARK_ROW, SOMALILAND_ROW))
    mapping = parse_natural_earth_country_codes(zip_path)
    assert mapping["Denmark"] == "DK"
    assert "Somaliland" not in mapping


# ── create_final_cc_mapping ──────────────────────────────────────────────────


def test_create_final_cc_mapping_direct_match():
    final, oceans = create_final_cc_mapping(["Denmark"], {"Denmark": "DK"})
    assert final["Denmark"] == ["Denmark", "DK"]
    assert oceans == []


def test_create_final_cc_mapping_ocean_classified_separately():
    final, oceans = create_final_cc_mapping(["Atlantic Ocean"], {})
    assert final == {}
    assert "Atlantic Ocean" in oceans


def test_create_final_cc_mapping_non_country_classified_as_ocean():
    # NON_COUNTRIES entries (islands with no ISO country of their own) are
    # bucketed with oceans/seas so they get PASS/ocean_or_sea treatment
    final, oceans = create_final_cc_mapping(["Borneo"], {})
    assert final == {}
    assert "Borneo" in oceans


def test_create_final_cc_mapping_uses_missing_country_mapping_fallback():
    # "South Korea" isn't a Natural Earth name on its own, but
    # MISSING_COUNTRY_MAPPING points it at "Korea, South"
    final, _oceans = create_final_cc_mapping(["South Korea"], {"Korea, South": "KR"})
    assert final["South Korea"] == ["Korea, South", "KR"]


def test_create_final_cc_mapping_skips_unresolvable_country():
    final, _oceans = create_final_cc_mapping(["Nonexistentland"], {})
    assert "Nonexistentland" not in final


def test_create_final_cc_mapping_skips_when_fallback_target_also_missing():
    # regression test: fallback target not present in iso_cc must not raise
    final, _oceans = create_final_cc_mapping(["Cocos Islands"], {})
    assert "Cocos Islands" not in final


# ── parse_ena_xml ─────────────────────────────────────────────────────────


ENA_XML = """<?xml version="1.0"?>
<CHECKLIST>
  <FIELD>
    <NAME>some_other_field</NAME>
    <FIELD_TYPE><TEXT_CHOICE_FIELD>
      <TEXT_VALUE><VALUE>Irrelevant</VALUE></TEXT_VALUE>
    </TEXT_CHOICE_FIELD></FIELD_TYPE>
  </FIELD>
  <FIELD>
    <NAME>geographic_location_country_andor_sea</NAME>
    <FIELD_TYPE><TEXT_CHOICE_FIELD>
      <TEXT_VALUE><VALUE>Denmark</VALUE></TEXT_VALUE>
      <TEXT_VALUE><VALUE>France</VALUE></TEXT_VALUE>
      <TEXT_VALUE><VALUE>Denmark</VALUE></TEXT_VALUE>
      <TEXT_VALUE><VALUE>not provided</VALUE></TEXT_VALUE>
    </TEXT_CHOICE_FIELD></FIELD_TYPE>
  </FIELD>
</CHECKLIST>
"""


def test_parse_ena_xml_extracts_sorted_deduped_countries():
    assert parse_ena_xml(ENA_XML) == ["Denmark", "France"]


def test_parse_ena_xml_excludes_missing_values():
    assert "not provided" not in parse_ena_xml(ENA_XML)


def test_parse_ena_xml_invalid_xml_returns_empty_list():
    assert parse_ena_xml("<not><valid xml") == []


def test_parse_ena_xml_missing_field_returns_empty_list():
    xml = "<CHECKLIST><FIELD><NAME>unrelated</NAME></FIELD></CHECKLIST>"
    assert parse_ena_xml(xml) == []


# ── parse_coordinate_cleaner_ref ─────────────────────────────────────────


def test_parse_coordinate_cleaner_ref_selects_expected_columns():
    fake_df = pd.DataFrame(
        {
            "iso2": ["DK"],
            "centroid.lon": [10.0],
            "centroid.lat": [56.0],
            "capital.lon": [12.57],
            "capital.lat": [55.68],
            "extra_column": ["ignored"],
        }
    )
    with patch(
        "sample_metadata_curation.install_resources.pyreadr.read_r",
        return_value={"countryref": fake_df},
    ):
        result = parse_coordinate_cleaner_ref(b"fake rda bytes")
    assert list(result.columns) == [
        "iso2",
        "centroid.lon",
        "centroid.lat",
        "capital.lon",
        "capital.lat",
    ]
    assert result.iloc[0]["iso2"] == "DK"


# ── parse_ror_institutions ───────────────────────────────────────────────


def _make_ror_zip(df: pd.DataFrame, filename="ror.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(filename, df.to_csv(index=False))
    return buf.getvalue()


def test_parse_ror_institutions_renames_and_selects_columns():
    df = pd.DataFrame(
        {
            "names.types.ror_display": ["Aarhus University"],
            "locations.geonames_details.country_code": ["DK"],
            "locations.geonames_details.lat": [56.15],
            "locations.geonames_details.lng": [10.2],
            "irrelevant": ["x"],
        }
    )
    result = parse_ror_institutions(_make_ror_zip(df))
    assert list(result.columns) == [
        "institution_name",
        "country_code",
        "latitude",
        "longitude",
    ]
    assert result.iloc[0]["institution_name"] == "Aarhus University"


def test_parse_ror_institutions_drops_rows_missing_coordinates():
    df = pd.DataFrame(
        {
            "names.types.ror_display": ["No Coords Institute", "Aarhus University"],
            "locations.geonames_details.country_code": ["DK", "DK"],
            "locations.geonames_details.lat": [None, 56.15],
            "locations.geonames_details.lng": [None, 10.2],
        }
    )
    result = parse_ror_institutions(_make_ror_zip(df))
    assert len(result) == 1
    assert result.iloc[0]["institution_name"] == "Aarhus University"


def test_parse_ror_institutions_no_csv_raises():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no csv here")
    with pytest.raises(ValueError, match="No CSV file found"):
        parse_ror_institutions(buf.getvalue())


def test_parse_ror_institutions_multiple_csv_raises():
    df = pd.DataFrame({"a": [1]})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("one.csv", df.to_csv(index=False))
        zf.writestr("two.csv", df.to_csv(index=False))
    with pytest.raises(ValueError, match="Multiple CSV files found"):
        parse_ror_institutions(buf.getvalue())


# ── save_natural_earth ────────────────────────────────────────────────────


def test_save_natural_earth_writes_bytes(tmp_path):
    output_path = tmp_path / "ne_countries.zip"
    save_natural_earth(b"fake zip bytes", output_path)
    assert output_path.read_bytes() == b"fake zip bytes"


# ── get_ror_download_url ─────────────────────────────────────────────────


def test_get_ror_download_url_picks_the_zip_file():
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "hits": {
            "hits": [
                {
                    "files": [
                        {
                            "key": "readme.md",
                            "links": {"self": "https://example.org/readme.md"},
                        },
                        {
                            "key": "ror-data.zip",
                            "links": {"self": "https://example.org/ror-data.zip"},
                        },
                    ]
                }
            ]
        }
    }
    with patch(
        "sample_metadata_curation.install_resources.requests.get",
        return_value=fake_response,
    ):
        url = get_ror_download_url()
    assert url == "https://example.org/ror-data.zip"


# ── get_checklist_countries ──────────────────────────────────────────────


def test_get_checklist_countries_returns_all_payloads():
    fake_ena = MagicMock(text="<xml/>")
    fake_cc = MagicMock(content=b"cc-bytes")
    fake_ror = MagicMock(content=b"ror-bytes")
    fake_ne = MagicMock(content=b"ne-bytes")

    with (
        patch(
            "sample_metadata_curation.install_resources.requests.get",
            side_effect=[fake_ena, fake_cc, fake_ror, fake_ne],
        ),
        patch(
            "sample_metadata_curation.install_resources.get_ror_download_url",
            return_value="https://example.org/ror-data.zip",
        ),
    ):
        ena_xml, cc_bytes, ror_bytes, ne_bytes, ror_url = get_checklist_countries()

    assert ena_xml == "<xml/>"
    assert cc_bytes == b"cc-bytes"
    assert ror_bytes == b"ror-bytes"
    assert ne_bytes == b"ne-bytes"
    assert ror_url == "https://example.org/ror-data.zip"


def test_get_checklist_countries_skips_natural_earth_download():
    fake_ena = MagicMock(text="<xml/>")
    fake_cc = MagicMock(content=b"cc-bytes")
    fake_ror = MagicMock(content=b"ror-bytes")

    with (
        patch(
            "sample_metadata_curation.install_resources.requests.get",
            side_effect=[fake_ena, fake_cc, fake_ror],
        ) as mock_get,
        patch(
            "sample_metadata_curation.install_resources.get_ror_download_url",
            return_value="https://example.org/ror-data.zip",
        ),
    ):
        ena_xml, cc_bytes, ror_bytes, ne_bytes, ror_url = get_checklist_countries(
            download_natural_earth=False
        )

    assert ne_bytes is None
    assert mock_get.call_count == 3


def test_get_checklist_countries_reraises_on_failure():
    with patch(
        "sample_metadata_curation.install_resources.requests.get",
        side_effect=Exception("network down"),
    ):
        with pytest.raises(Exception, match="network down"):
            get_checklist_countries()


# ── main() orchestration ─────────────────────────────────────────────────


def test_main_writes_expected_output_files(tmp_path, make_ne_zip):
    ne_zip_path = make_ne_zip(_rows(DENMARK_ROW))

    fake_centroids_df = pd.DataFrame(
        {
            "iso2": ["DK"],
            "centroid.lon": [10.0],
            "centroid.lat": [56.0],
            "capital.lon": [12.57],
            "capital.lat": [55.68],
        }
    )
    ror_df = pd.DataFrame(
        {
            "names.types.ror_display": ["Aarhus University"],
            "locations.geonames_details.country_code": ["DK"],
            "locations.geonames_details.lat": [56.15],
            "locations.geonames_details.lng": [10.2],
        }
    )

    with (
        patch(
            "sample_metadata_curation.install_resources.get_checklist_countries",
            return_value=(
                ENA_XML,
                b"fake-rda-bytes",
                _make_ror_zip(ror_df),
                ne_zip_path.read_bytes(),
                "https://example.org/ror-data.zip",
            ),
        ),
        patch(
            "sample_metadata_curation.install_resources.parse_coordinate_cleaner_ref",
            return_value=fake_centroids_df,
        ),
        patch(
            "sample_metadata_curation.install_resources.coordinate_cleaner_commit",
            return_value=None,
        ),
    ):
        main(resource_dir=tmp_path)

    # ENA_XML's checklist has "Denmark" and "France"; only Denmark is in the
    # synthetic Natural Earth fixture, so France is skipped (unresolvable),
    # not misclassified as an ocean
    mapping_csv = (tmp_path / "country_to_cc_mapping.csv").read_text()
    assert "Denmark,Denmark,DK" in mapping_csv
    assert "France" not in mapping_csv

    oceans_txt = (tmp_path / "oceans_and_seas.txt").read_text()
    assert oceans_txt == ""

    centroids_csv = pd.read_csv(tmp_path / "country_centroids_and_capitals.csv")
    assert centroids_csv.iloc[0]["iso2"] == "DK"

    institutions_csv = pd.read_csv(tmp_path / "research_institutions.csv")
    assert institutions_csv.iloc[0]["institution_name"] == "Aarhus University"

    assert (tmp_path / "ne_countries.zip").read_bytes() == ne_zip_path.read_bytes()


def test_main_returns_early_when_resource_dir_missing(tmp_path):
    missing_dir = tmp_path / "does_not_exist"
    with patch(
        "sample_metadata_curation.install_resources.get_checklist_countries"
    ) as mock_fetch:
        main(resource_dir=missing_dir)
    mock_fetch.assert_not_called()
