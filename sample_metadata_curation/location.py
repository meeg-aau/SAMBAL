import csv
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import geopandas as gpd
from shapely.geometry import Point

from sample_metadata_curation.constants import (
    LOCATION_KEYS,
    MISSING_VALUES,
    SMALL_ISLAND_CC,
    TERRITORY_TO_PARENT_CC,
)

"""
regex for lat lon e.g.
match = 55.62115 N 8.2849 E
lat = 55.62115
lat_dir = N
lon = 8.2849
lon_dir = E
"""
LATLON_COORD_RE = re.compile(
    r"""
    ^\s*
    (?P<lat>[-+]?\d+(?:[.,]\d+)?)\s*(?P<lat_dir>[NS])?
    [\s,;/\-]+
    (?P<lon>[-+]?\d+(?:[.,]\d+)?)\s*(?P<lon_dir>[EW])?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# For coordinates are given in degrees-minutes-seconds format
"""
match = 55° 37' 17.94" N 8° 17' 5.64" E
lat_deg = 55, lat_min = 37, lat_sec = 17.94, lat_dir = N
lon_deg = 8, lon_min = 17, lon_sec = 5.64, lon_dir = E
"""
DMS_COORD_RE = re.compile(
    r"""
    ^\s*
    (?P<lat_deg>\d+(?:[.,]\d+)?)\s*(?:°|d)?\s*
    (?:(?P<lat_min>\d+(?:[.,]\d+)?)\s*(?:'|m)?\s*
    (?:(?P<lat_sec>\d+(?:[.,]\d+)?)\s*(?:"|s)?\s*)?)?
    (?P<lat_dir>[NS])\s*
    [,\s/;]+
    (?P<lon_deg>\d+(?:[.,]\d+)?)\s*(?:°|d)?\s*
    (?:(?P<lon_min>\d+(?:[.,]\d+)?)\s*(?:'|m)?\s*
    (?:(?P<lon_sec>\d+(?:[.,]\d+)?)\s*(?:"|s)?\s*)?)?
    (?P<lon_dir>[EW])
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

REJECTED_LOCATION_CHARACTERS = set("@#$%^*=+<>[]{}|\\~")


class LocationCurator:
    def __init__(
        self,
        resources_dir: Optional[Path] = None,
        natural_earth_zip: Optional[Path] = None,
    ):
        if resources_dir is None:
            resources_dir = Path(__file__).parent / "resources"

        self.mapping_csv = resources_dir / "country_to_cc_mapping.csv"
        self.oceans_txt = resources_dir / "oceans_and_seas.txt"
        self.centroids_and_capitals_csv = (
            resources_dir / "country_centroids_and_capitals.csv"
        )
        #   provide a preferred path for the file
        #   for cartogenomics to sync the same versions
        self.natural_earth_zip = (
            Path(natural_earth_zip)
            if natural_earth_zip
            else resources_dir / "ne_countries.zip"
        )
        self.institutions_csv = resources_dir / "research_institutions.csv"
        self.institution_coords = self.load_institution_coords(self.institutions_csv)

        missing = [
            p
            for p in [
                self.mapping_csv,
                self.oceans_txt,
                self.centroids_and_capitals_csv,
                self.natural_earth_zip,
            ]
            if not p.exists()
        ]
        if missing:
            missing_names = ", ".join(p.name for p in missing)
            raise FileNotFoundError(
                f"Missing required resource files: {missing_names}. "
                "Please run 'setup-sample-resources' to download them."
            )

        self.name_to_cc = self.load_country_mapping()
        self.oceans_and_seas = self.load_oceans_and_seas()
        self.reference_coords = self.load_reference_coords(
            self.centroids_and_capitals_csv
        )
        self.countries_gdf = self.load_countries(self.natural_earth_zip)
        self.countries_gdf_proj = self.countries_gdf.to_crs("EPSG:3857")

    def load_oceans_and_seas(self) -> set:
        oceans = set()
        if self.oceans_txt.exists():
            with self.oceans_txt.open(newline="", encoding="utf-8") as f:
                for line in f:
                    name = line.strip()
                    if name:
                        oceans.add(name)
        return oceans

    @staticmethod
    def load_countries(path: Path) -> gpd.GeoDataFrame:
        """
        Load Natural Earth country boundaries from zip file.
        Returns a GeoDataFrame with ISO_A2 and geometry columns.
        The spatial index is built automatically by geopandas/rtree.
        """
        gdf = gpd.read_file(path)[["ISO_A2_EH", "geometry"]]
        return gdf.rename(columns={"ISO_A2_EH": "ISO_A2"})

    # Loads country and province centroids and capitals
    @staticmethod
    def load_reference_coords(
        csv_path: Path,
    ) -> List[Tuple[float, float, Optional[str]]]:
        coords = []
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                iso2 = row.get("iso2")
                if not iso2 or iso2.lower() in ("nan", "none", ""):
                    iso2 = None
                for lat_col, lon_col in [
                    ("centroid.lat", "centroid.lon"),
                    ("capital.lat", "capital.lon"),
                ]:
                    try:
                        coords.append((float(row[lat_col]), float(row[lon_col]), iso2))
                    except (ValueError, TypeError):
                        pass
        return coords

    @staticmethod
    def load_institution_coords(csv_path: Path) -> List[Tuple[float, float]]:
        coords = []
        if not csv_path.exists():
            return coords
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    coords.append((float(row["latitude"]), float(row["longitude"])))
                except (ValueError, TypeError):
                    pass
        return coords

    @staticmethod
    def _apply_direction(value: float, direction: Optional[str]) -> float:
        if not direction:
            return value
        d = direction.upper()
        if d in ("S", "W"):
            return -abs(value)
        return abs(value)  # N/E

    @staticmethod
    def _dms_to_decimal(
        degrees: float, minutes: float, seconds: float, direction: str
    ) -> float:
        decimal = degrees + minutes / 60 + seconds / 3600
        if direction.upper() in ("S", "W"):
            decimal = -decimal
        return decimal

    @staticmethod
    def _dms_component_precision(
        minutes: Optional[str], seconds: Optional[str]
    ) -> float:
        """Return precision in degrees based on which DMS components are present."""
        if seconds is not None:
            return 1 / 3600
        elif minutes is not None:
            return 1 / 60
        return 1.0

    @staticmethod
    def _dms_precision_deg(
        lat_min: Optional[str],
        lat_sec: Optional[str],
        lon_min: Optional[str],
        lon_sec: Optional[str],
    ) -> float:
        """Determine precision in degrees from which DMS components are present."""
        return max(
            LocationCurator._dms_component_precision(lat_min, lat_sec),
            LocationCurator._dms_component_precision(lon_min, lon_sec),
        )

    # Finds number of decimals for precision tests
    @staticmethod
    def _decimal_places(v: float) -> int:
        return len(str(v).rstrip("0").split(".")[-1])

    # Finds the precision in the coordinates (stripping trailing zeroes)
    @staticmethod
    def _coord_precision_deg(latitude: float, longitude: float) -> float:
        dp = min(
            LocationCurator._decimal_places(abs(latitude)),
            LocationCurator._decimal_places(abs(longitude)),
        )
        return 10**-dp

    def _is_centroid_or_capital(
        self,
        latitude: float,
        longitude: float,
        reported_cc: Optional[str],
        threshold_deg: float = 0.01,
    ) -> bool:
        return any(
            abs(latitude - lat) <= threshold_deg
            and abs(longitude - lon) <= threshold_deg
            and (iso2 is None or reported_cc is None or iso2 == reported_cc)
            for lat, lon, iso2 in self.reference_coords
        )

    def _is_known_institution(
        self, latitude: float, longitude: float, threshold_deg: float = 0.001
    ) -> bool:
        return any(
            abs(latitude - lat) <= threshold_deg
            and abs(longitude - lon) <= threshold_deg
            for lat, lon in self.institution_coords
        )

    def _is_near_border(self, latitude, longitude, reported_cc, threshold_m=10000):
        point_proj = (
            gpd.GeoSeries([Point(longitude, latitude)], crs="EPSG:4326")
            .to_crs("EPSG:3857")
            .iloc[0]
        )
        reported_polygon = self.countries_gdf_proj[
            self.countries_gdf_proj["ISO_A2"] == reported_cc
        ]
        if reported_polygon.empty:
            return False
        return reported_polygon.geometry.iloc[0].distance(point_proj) <= threshold_m

    def _polygon_country_code(self, latitude: float, longitude: float) -> Optional[str]:
        try:
            point = Point(longitude, latitude)
            matches = self.countries_gdf[self.countries_gdf.geometry.contains(point)]
            if matches.empty:
                return None
            cc = matches.iloc[0]["ISO_A2"]
            if cc == "-99":
                return "XX"  # disputed or unrecognised territory
            return cc
        except Exception:
            return None

    def _parse_single_coord(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)

        s = str(value).strip()
        m = re.match(r"^\s*([-+]?\d+(?:[.,]\d+)?)\s*([NSEW])?\s*$", s, re.IGNORECASE)
        if not m:
            return None
        num = float(m.group(1).replace(",", "."))
        dir_ = m.group(2)
        return self._apply_direction(num, dir_) if dir_ else num

    @staticmethod
    def _first_present_key(d: Dict[str, Any], keys: List[str]) -> Optional[str]:
        for k in keys:
            if k in d and d[k] not in (None, ""):
                if str(d[k]).lower() not in MISSING_VALUES:
                    return k
        return None

    @staticmethod
    def sanity_check_location(
        location: Optional[str], latitude: Optional[float], longitude: Optional[float]
    ) -> bool:
        if location is None:
            return False

        s = str(location).strip()
        if s.lower() in MISSING_VALUES:
            return False

        # Too short/too long - not sure about this one
        if len(s) < 2 or len(s) > 200:
            return False

        for ch in s:
            # no numbers
            if ch.isdigit():
                return False
            if ch in REJECTED_LOCATION_CHARACTERS:
                return False

        # Reject lots of repeated characters (often random)
        if re.search(r"(.)\1\1\1", s):  # 4 of the same char in a row
            return False

        return True

    def load_country_mapping(self) -> Dict[str, str]:
        name_to_cc = {}

        with self.mapping_csv.open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                input_name, canonical_name, cc = row[0], row[1], row[2]
                name_to_cc[input_name] = cc
                # Also index by canonical name to ensure it's always found
                if canonical_name not in name_to_cc:
                    name_to_cc[canonical_name] = cc
        return name_to_cc

    def infer_reported_country_code(
        self,
        reported_location: Optional[str],
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Check for location based on INSDC controlled vocab
        and try to get ISO 2 digit country code
        """
        if not reported_location:
            return None, None, None

        loc = reported_location.strip()

        # exact match to ENA/INSDC label
        if loc in self.name_to_cc:
            region_cc = self.name_to_cc.get(loc)
            region = loc
            locality = None
            return region, locality, region_cc

        if loc in self.oceans_and_seas:
            return loc, None, None

        """
        INSDC location format
        Value format “<geo_loc_name>[:<region>][, <locality>]” where
        geographic location name (geo_loc_name) is any value from the
        controlled vocabulary

        Example

        /geo_loc_name=”Canada:Vancouver”
        /geo_loc_name=”France:Cote d’Azur, Antibes”
        /geo_loc_name=”Atlantic Ocean:Charlie Gibbs Fracture Zone”
        """
        if ":" in loc:
            region = loc.split(":")[0].strip()
            if region in self.name_to_cc:
                region_cc = self.name_to_cc.get(region)
                locality = loc.split(":")[1].strip()
                return region, locality, region_cc
            if region in self.oceans_and_seas:
                locality = loc.split(":")[1].strip()
                return region, locality, None
        return None, None, None

    def geo_consistency_check(
        self,
        reported_location: Optional[str],
        latitude: Optional[float],
        longitude: Optional[float],
        flipped_coords: bool = False,
    ) -> Dict[str, Any]:
        """
        Check if reported location matches polygon-based reverse geocoder result.
        Several levels of reporting to account for mismatches in data sources
        """
        out = {
            "geo_check_status": "SKIP",
            "geo_check_reason": None,
            "coordinates_reversed": flipped_coords,
            "reported_country_code": None,
            "reverse_country_code": None,
            "region": None,
            "locality": None,
        }

        region, locality, reported_cc = self.infer_reported_country_code(
            reported_location
        )
        out["reported_country_code"] = reported_cc
        out["region"] = region
        out["locality"] = locality

        if region in self.oceans_and_seas:
            out["geo_check_status"] = "PASS"
            out["geo_check_reason"] = "ocean_or_sea"
            return out

        if latitude is None or longitude is None:
            out["geo_check_status"] = "SKIP"
            out["geo_check_reason"] = "no_coordinates"
            return out

        # Always run polygon lookup if we have coordinates
        reverse_cc = self._polygon_country_code(latitude, longitude)
        out["reverse_country_code"] = reverse_cc

        if latitude == 0.0 and longitude == 0.0:
            out["geo_check_status"] = "FAIL"
            out["geo_check_reason"] = "null_island"
            return out

        if latitude == longitude:
            out["geo_check_status"] = "WARN"
            out["geo_check_reason"] = "identical_lat_long"
            return out

        if latitude == int(latitude) and longitude == int(longitude):
            out["geo_check_status"] = "WARN"
            out["geo_check_reason"] = "coordinates_suspiciously_round"
            return out

        if self._decimal_places(latitude) > 6 or self._decimal_places(longitude) > 6:
            out["geo_check_status"] = "PASS"
            out["geo_check_reason"] = "implausibly_precise"
            return out

        if self._is_centroid_or_capital(latitude, longitude, reported_cc):
            out["geo_check_status"] = "WARN"
            out["geo_check_reason"] = "centroid_or_capital"
            return out

        if self._is_known_institution(latitude, longitude):
            out["geo_check_status"] = "WARN"
            out["geo_check_reason"] = "known_institution"
            return out

        # Ocean: polygon lookup returns None for points in the sea
        # unless the reported_cc is a small island or a territory
        if reverse_cc is None:
            if reported_cc and reported_cc in TERRITORY_TO_PARENT_CC:
                out["geo_check_status"] = "PASS"
                out["geo_check_reason"] = "match_territory"
            elif reported_cc in SMALL_ISLAND_CC:
                out["geo_check_status"] = "WARN"
                out["geo_check_reason"] = "small_island_not_in_reference"
            else:
                out["geo_check_status"] = "PASS"
                out["geo_check_reason"] = "ocean_or_sea"
            return out

        if not reported_cc:
            out["geo_check_status"] = "WARN"
            out["geo_check_reason"] = "no_reported_country_code"
            return out

        if reverse_cc == "XX":
            out["geo_check_status"] = "WARN"
            out["geo_check_reason"] = "disputed_or_unrecognised_territory"
            return out

        if reverse_cc == reported_cc:
            out["geo_check_status"] = "PASS"
            out["geo_check_reason"] = "match"
        elif TERRITORY_TO_PARENT_CC.get(reverse_cc) == reported_cc:
            # reverse is a territory of the reported parent e.g. reverse=HK, reported=CN
            out["geo_check_status"] = "PASS"
            out["geo_check_reason"] = "match_territory"
        elif (
            reported_cc in TERRITORY_TO_PARENT_CC
            and TERRITORY_TO_PARENT_CC[reported_cc] == reverse_cc
        ):
            out["geo_check_status"] = "PASS"
            out["geo_check_reason"] = "match_territory"
        elif self._is_near_border(latitude, longitude, reported_cc):
            out["geo_check_status"] = "PASS"
            out["geo_check_reason"] = "match_near_border"
        else:
            out["geo_check_status"] = "FAIL"
            out["geo_check_reason"] = "country_mismatch"

        return out

    def curate_location(
        self, cleaned_dict: Dict[str, Any], accession: Optional[str] = None
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "accession": accession,
            "location": None,
            "latitude": None,
            "longitude": None,
        }

        lat = None
        lon = None
        flipped_coords = False

        # try combined lat_lon
        lat_lon_key = self._first_present_key(cleaned_dict, LOCATION_KEYS["lat_lon"])
        if lat_lon_key:
            lat_lon_str = str(cleaned_dict[lat_lon_key]).strip()
            m = LATLON_COORD_RE.match(lat_lon_str)
            if m:
                lat = self._apply_direction(
                    float(m.group("lat").replace(",", ".")), m.group("lat_dir")
                )
                lon = self._apply_direction(
                    float(m.group("lon").replace(",", ".")), m.group("lon_dir")
                )
            else:
                m = DMS_COORD_RE.match(lat_lon_str)
                if m:
                    lat = self._dms_to_decimal(
                        float(m.group("lat_deg").replace(",", ".")),
                        (
                            float(m.group("lat_min").replace(",", "."))
                            if m.group("lat_min")
                            else 0.0
                        ),
                        (
                            float(m.group("lat_sec").replace(",", "."))
                            if m.group("lat_sec")
                            else 0.0
                        ),
                        m.group("lat_dir"),
                    )
                    lon = self._dms_to_decimal(
                        float(m.group("lon_deg").replace(",", ".")),
                        (
                            float(m.group("lon_min").replace(",", "."))
                            if m.group("lon_min")
                            else 0.0
                        ),
                        (
                            float(m.group("lon_sec").replace(",", "."))
                            if m.group("lon_sec")
                            else 0.0
                        ),
                        m.group("lon_dir"),
                    )
                    result["coord_precision_deg"] = self._dms_precision_deg(
                        m.group("lat_min"),
                        m.group("lat_sec"),
                        m.group("lon_min"),
                        m.group("lon_sec"),
                    )

        # Individual fields if not found yet
        if lat is None:
            lat_key = self._first_present_key(cleaned_dict, LOCATION_KEYS["lat"])
            if lat_key:
                lat = self._parse_single_coord(cleaned_dict[lat_key])

        if lon is None:
            lon_key = self._first_present_key(cleaned_dict, LOCATION_KEYS["lon"])
            if lon_key:
                lon = self._parse_single_coord(cleaned_dict[lon_key])

        # Validate ranges and try switching if invalid otherwise set both as None
        if lat is not None and lon is not None:
            is_valid = abs(lat) <= 90 and abs(lon) <= 180
            if is_valid:
                result["latitude"] = lat
                result["longitude"] = lon
            else:
                # Try switching
                if abs(lon) <= 90 and abs(lat) <= 180:
                    result["latitude"] = lon
                    result["longitude"] = lat
                    flipped_coords = True
                else:
                    result["latitude"] = None
                    result["longitude"] = None

        # Record precision from decimal places if not already set by DMS parser
        if result["latitude"] is not None and result["longitude"] is not None:
            if "coord_precision_deg" not in result:
                result["coord_precision_deg"] = self._coord_precision_deg(
                    result["latitude"], result["longitude"]
                )
        else:
            result["coord_precision_deg"] = None

        loc_key = self._first_present_key(cleaned_dict, LOCATION_KEYS["location"])
        if loc_key:
            is_location = self.sanity_check_location(
                cleaned_dict[loc_key], result["latitude"], result["longitude"]
            )
            result["location"] = cleaned_dict[loc_key] if is_location else None

        geo = self.geo_consistency_check(
            result["location"], result["latitude"], result["longitude"], flipped_coords
        )
        if geo.get("region") in self.oceans_and_seas:
            result["latitude"] = lat
            result["longitude"] = lon
            # re-run check with original coords and flipped_coords=False
            geo = self.geo_consistency_check(
                result["location"], result["latitude"], result["longitude"], False
            )

        result.update(geo)

        if result.get("geo_check_reason") == "implausibly_precise":
            result["coord_precision_deg"] = max(result["coord_precision_deg"], 0.0001)

        if geo.get("geo_check_status") == "FAIL":
            result["region"] = None
        result.pop("location", None)

        return result
