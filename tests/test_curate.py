from pathlib import Path

import pytest
from shapely.geometry import Polygon

from sample_metadata_curation.curate import SampleCurator, curate_biosample

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "test.json"


def make_sample(lat, lon, location=None):
    chars = {"lat_lon": [{"text": f"{lat} {lon}"}]}
    if location:
        chars["geo_loc_name"] = [{"text": location}]
    return {"accession": "TEST001", "characteristics": chars}


def test_curate_biosample_full():
    result = curate_biosample(FIXTURE_PATH)
    assert result["accession"] == "SAMN39868869"
    assert result["region"] == "Denmark"
    assert result["latitude"] == 55.62115
    assert result["longitude"] == 8.2849

    assert result["01_mfd_sampletype"] == "Soil"
    assert result["project_identifier"] == "P08_1"
    assert result["extraction_method"] == "PowerSoil-Pro-HT"

    assert result["geo_check_status"] == "PASS"
    assert result["geo_check_reason"] == "match"


@pytest.mark.parametrize(
    "lat_lon_str, expected_lat, expected_lon",
    [
        ("55.62115 N 8.2849 E", 55.62115, 8.2849),
        ("34.0522N 118.2437W", 34.0522, -118.2437),
        ("12.345 S 45.678 E", -12.345, 45.678),
        ("55.62115 8.2849", 55.62115, 8.2849),
        ("-33.8688 151.2093", -33.8688, 151.2093),
    ],
)
def test_parse_lat_lon_combined(lat_lon_str, expected_lat, expected_lon):
    sample = {"characteristics": {"lat_lon": [{"text": lat_lon_str}]}}
    result = curate_biosample(sample)
    assert result["latitude"] == pytest.approx(expected_lat, abs=1e-6)
    assert result["longitude"] == pytest.approx(expected_lon, abs=1e-6)


def test_parse_separate_lat_lon():
    sample = {
        "characteristics": {
            "geographic_location_latitude": [{"text": "55.62115"}],
            "geographic_location_longitude": [{"text": "8.2849"}],
        }
    }
    result = curate_biosample(sample)
    assert result["latitude"] == 55.62115
    assert result["longitude"] == 8.2849


@pytest.mark.parametrize(
    # invalid options
    "lat_lon_str",
    [
        "NA",
        "not provided",
        "unknown",
        "55 N 200 E",
        "",
        None,
    ],
)
def test_parse_lat_lon_combined_invalid_returns_none(lat_lon_str):
    sample = {"characteristics": {"lat_lon": [{"text": lat_lon_str}]}}
    result = curate_biosample(sample)
    assert result["latitude"] is None
    assert result["longitude"] is None


def test_parse_lat_lon_switched():
    # Case where lat/lon are swapped: lat=127 invalid as lat but valid as long
    sample = {
        "characteristics": {
            "lat_lon": [{"text": "127.7669 N 35.9078 E"}],
            "geo_loc_name": [{"text": "South Korea"}],
        }
    }
    result = curate_biosample(sample)
    assert result["latitude"] == 35.9078
    assert result["longitude"] == 127.7669
    assert result["coordinates_reversed"] is True


@pytest.mark.parametrize(
    "region_str",
    [
        "not provided",
        "NA",
        "AAAAAAAA",
        "a1b2c3d4",
        "a1b2c3d4",
    ],
)
def test_invalid_region_returns_none(region_str):
    sample = {"characteristics": {"geo_loc_name": [{"text": region_str}]}}
    result = curate_biosample(sample)
    assert result["region"] is None


@pytest.mark.parametrize(
    "location_str, lat_lon_str, expected_region, expected_locality, "
    "expected_geo_match, expected_geo_match_reason",
    [
        (
            "Denmark",
            "55.62115 N 8.2849 E",
            "Denmark",
            None,
            "PASS",
            "match",
        ),  # Matching
        (
            "India",
            "55.62115 N 8.2849 E",
            None,
            None,
            "FAIL",
            "country_mismatch",
        ),  # Non-matching
        (
            "United States",
            "40.7128 N 74.0060 W",
            "United States",
            None,
            "PASS",
            "match",
        ),  # Matching
        (
            "Denmark",
            "40.7128 N 74.0060 W",
            None,
            None,
            "FAIL",
            "country_mismatch",
        ),  # Non-matching
        (
            "United Kingdom: England",
            "51.5074 N 0.1278 W",
            "United Kingdom",
            "England",
            "PASS",
            "match",
        ),  # Matching with locality
        (
            "Atlantic Ocean:Charlie Gibbs Fracture Zone",
            "52.45, -35.08",
            "Atlantic Ocean",
            "Charlie Gibbs Fracture Zone",
            "PASS",
            "ocean_or_sea",
        ),
    ],
)
def test_coordinate_region_match(
    location_str,
    lat_lon_str,
    expected_region,
    expected_locality,
    expected_geo_match,
    expected_geo_match_reason,
):
    sample = {
        "characteristics": {
            "geo_loc_name": [{"text": location_str}],
            "lat_lon": [{"text": lat_lon_str}],
        }
    }
    result = curate_biosample(sample)
    assert result["region"] == expected_region
    assert result["locality"] == expected_locality
    assert result["geo_check_status"] == expected_geo_match
    assert result["geo_check_reason"] == expected_geo_match_reason


def test_curate_biosample_accepts_natural_earth_zip_override(tmp_path, make_ne_zip):
    """
    natural_earth_zip lets an external caller (cartogenomics) supply a
    fresher Natural Earth download than whatever this package last bundled,
    independently of resources_dir (which still supplies the other 3 files).
    """
    resources_dir = tmp_path / "resources"
    resources_dir.mkdir()
    (resources_dir / "country_to_cc_mapping.csv").write_text("Denmark,Denmark,DK\n")
    (resources_dir / "oceans_and_seas.txt").write_text("")
    (resources_dir / "country_centroids_and_capitals.csv").write_text(
        "iso2,centroid.lon,centroid.lat,capital.lon,capital.lat\n"
    )
    override_zip = make_ne_zip(
        {
            "ISO_A2_EH": ["DK"],
            "NAME": ["Denmark"],
            "geometry": [Polygon([(8, 54), (8, 58), (13, 58), (13, 54)])],
        }
    )

    curator = SampleCurator(resources_dir=resources_dir, natural_earth_zip=override_zip)
    assert curator.location_curator.natural_earth_zip == override_zip

    result = curator.curate_sample(make_sample(55.62115, 8.2849, "Denmark"))
    assert result["geo_check_status"] == "PASS"
    assert result["geo_check_reason"] == "match"


def test_biome_extraction():
    biome_keys = [
        "01_mfd_sampletype",
        "02_mfd_areatype",
        "03_mfd_hab1",
        "04_mfd_hab2",
        "05_mfd_hab3",
    ]
    result = curate_biosample(FIXTURE_PATH, biome_keys=biome_keys)
    expected_biome = (
        "Soil;Natural;Bogs, mires and fens;Calcareous fens;Petrifying springs"
    )
    assert result.get("biome") == expected_biome


def test_ocean_swapping_skipped():
    """
    Test that for an ocean, the lat/long swapping is skipped even if the
    original coordinates would trigger it (e.g. lat > 90).
    """
    sample = {
        "characteristics": {
            "geo_loc_name": [{"text": "Atlantic Ocean:Test Locality"}],
            "lat_lon": [{"text": "120.0 N 20.0 E"}],
        }
    }

    result = curate_biosample(sample)

    assert result["region"] == "Atlantic Ocean"
    assert result["latitude"] == 120.0
    assert result["longitude"] == 20.0
    assert result["geo_check_status"] == "PASS"
    assert result["geo_check_reason"] == "ocean_or_sea"
    assert result["coordinates_reversed"] is False


# ── Coordinate quality checks ──────────────────────────────────────────────


def test_null_island():
    result = curate_biosample(make_sample(0.0, 0.0))
    assert result["geo_check_status"] == "FAIL"
    assert result["geo_check_reason"] == "null_island"


def test_identical_lat_long():
    result = curate_biosample(make_sample(45.0, 45.0))
    assert result["geo_check_status"] == "WARN"
    assert result["geo_check_reason"] == "identical_lat_long"


def test_coordinates_suspiciously_round():
    result = curate_biosample(make_sample(55.0, 8.0))
    assert result["geo_check_status"] == "WARN"
    assert result["geo_check_reason"] == "coordinates_suspiciously_round"


def test_implausibly_precise():
    result = curate_biosample(make_sample(55.1234567, 8.1234567))
    assert result["geo_check_status"] == "PASS"
    assert result["geo_check_reason"] == "implausibly_precise"


def test_centroid_or_capital():
    # Kabul, Afghanistan capital from reference file
    result = curate_biosample(make_sample(34.52, 69.18))
    assert result["geo_check_status"] == "WARN"
    assert result["geo_check_reason"] == "centroid_or_capital"


def test_valid_coordinates_pass():
    # Normal Danish coordinates should pass through all checks
    result = curate_biosample(make_sample(55.62115, 8.2849, "Denmark"))
    assert result["geo_check_status"] == "PASS"
    assert result["geo_check_reason"] == "match"


# ── Coordinate precision ───────────────────────────────────────────────────


def test_coord_precision_deg():
    # lat has 5 dp, lon has 4 dp — worst is 4
    result = curate_biosample(make_sample(55.62115, 8.2849))
    assert result["coord_precision_deg"] == 0.0001


def test_coord_precision_none_when_no_coords():
    sample = {"characteristics": {"lat_lon": [{"text": "NA"}]}}
    result = curate_biosample(sample)
    assert result["coord_precision_deg"] is None


# ── DMS parsing ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "lat_lon_str, expected_lat, expected_lon, expected_precision",
    [
        (
            "55° 37' 17.94\" N 8° 17' 5.64\" E",
            55.62165,
            8.28490,
            pytest.approx(1 / 3600, rel=1e-3),
        ),
        (
            "55 37 17.94 N 8 17 5.64 E",
            55.62165,
            8.28490,
            pytest.approx(1 / 3600, rel=1e-3),
        ),
        ("55° 37' N 8° 17' E", 55.61667, 8.28333, pytest.approx(1 / 60, rel=1e-3)),
        ("55° N 8° E", 55.0, 8.0, 1.0),
    ],
)
def test_parse_dms(lat_lon_str, expected_lat, expected_lon, expected_precision):
    sample = {"characteristics": {"lat_lon": [{"text": lat_lon_str}]}}
    result = curate_biosample(sample)
    assert result["latitude"] == pytest.approx(expected_lat, abs=1e-4)
    assert result["longitude"] == pytest.approx(expected_lon, abs=1e-4)
    assert result["coord_precision_deg"] == expected_precision


def test_disputed_territory():
    # Somaliland - Natural Earth assigns -99
    result = curate_biosample(make_sample(9.5, 45.0, "Somalia"))
    assert result["geo_check_status"] == "WARN"
    assert result["geo_check_reason"] == "disputed_or_unrecognised_territory"


def test_territory_match():
    # French Guiana coordinates (territory of France, no NE polygon)
    result = curate_biosample(make_sample(2.8, -53.8, "French Guiana"))
    assert result["geo_check_status"] == "PASS"
    assert result["geo_check_reason"] == "match_territory"


def test_near_border():
    # Point just inside Germany near Danish border, reported as Denmark
    result = curate_biosample(make_sample(54.866, 9.04807, "Denmark"))
    assert result["geo_check_status"] == "PASS"
    assert result["geo_check_reason"] == "match_near_border"


def test_institution_coordinates():
    # Natural History Museum Aarhus,DK,56.15674,10.21076
    result = curate_biosample(make_sample(56.1567, 10.2107, "Denmark"))
    assert result["geo_check_status"] == "WARN"
    assert result["geo_check_reason"] == "known_institution"
