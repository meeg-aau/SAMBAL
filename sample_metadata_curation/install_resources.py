import csv
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree

import geopandas as gpd
import pandas as pd
import pyreadr
import requests

from sample_metadata_curation.constants import (
    MISSING_COUNTRY_MAPPING,
    MISSING_VALUES,
    NON_COUNTRIES,
)

logging.basicConfig(level=logging.INFO)
logging = logging.getLogger()


ENA_URL = "https://www.ebi.ac.uk/ena/browser/api/xml/ERC000011?download=true"
COORDINATE_CLEANER_URL = (
    "https://raw.githubusercontent.com/ropensci/CoordinateCleaner/"
    "master/data/countryref.rda"
)
ROR_ZENODO_ID = "6347574"
ROR_URL = (
    f"https://zenodo.org/api/records?q=conceptrecid:{ROR_ZENODO_ID}"
    "&sort=mostrecent&size=1"
)
NATURAL_EARTH_URL = (
    "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip"
)

#   NAME_CIAWF comes from the CIA World Factbook which has been discontinued
NATURAL_EARTH_NAME_COLUMNS = [
    "NAME_CIAWF",
    "NAME",
    "NAME_LONG",
    "ADMIN",
    "FORMAL_EN",
    "BRK_NAME",
]

ROR_COLUMN_MAP = {
    "names.types.ror_display": "institution_name",
    "locations.geonames_details.country_code": "country_code",
    "locations.geonames_details.lat": "latitude",
    "locations.geonames_details.lng": "longitude",
}


def get_ror_download_url() -> str:
    try:
        r = requests.get(ROR_URL)
        r.raise_for_status()
        files = r.json()["hits"]["hits"][0]["files"]
    except Exception as e:
        logging.error(f"Error fetching ROR download URL: {e}")
        raise
    return next(f["links"]["self"] for f in files if f["key"].endswith(".zip"))


def get_checklist_countries(download_natural_earth: bool = True):
    try:
        logging.info("Downloading ENA country list...")
        response_ena = requests.get(ENA_URL)
        logging.info("Downloading CoordinateCleaner country reference...")
        response_cc = requests.get(COORDINATE_CLEANER_URL)
        logging.info("Fetching latest ROR download URL...")
        ror_url = get_ror_download_url()
        logging.info(f"Downloading ROR institution data from {ror_url}...")
        response_ror = requests.get(ror_url)
        ne_content = None
        if download_natural_earth:
            logging.info("Downloading Natural Earth country boundaries...")
            ne_content = requests.get(NATURAL_EARTH_URL).content
        return (
            response_ena.text,
            response_cc.content,
            response_ror.content,
            ne_content,
            ror_url,
        )
    except Exception as e:
        logging.error(f"Error downloading country data: {e}")
        raise


def parse_ena_xml(ena_xml: str) -> List[str]:
    """
    Parse the ENA checklist XML to extract INSDC accepted countries and seas.
    """
    try:
        root = ElementTree.fromstring(ena_xml)
        countries = []

        # Find the field 'geographic_location_country_andor_sea'
        for field in root.findall(".//FIELD"):
            name_elem = field.find("NAME")
            if (
                name_elem is not None
                and name_elem.text == "geographic_location_country_andor_sea"
            ):
                # Extract all VALUE tags
                for value_elem in field.findall(".//TEXT_VALUE/VALUE"):
                    if value_elem.text:
                        val = value_elem.text.strip()
                        if val.lower() not in MISSING_VALUES:
                            countries.append(val)
                break

        return sorted(list(set(countries)))
    except Exception as e:
        logging.error(f"Error parsing ENA XML: {e}")
        return []


def parse_natural_earth_country_codes(ne_zip_path: Path) -> Dict[str, str]:
    """
    Build a country name -> ISO 3166-1 alpha-2 code crosswalk from a Natural
    Earth zip already saved to disk.
    """
    gdf = gpd.read_file(ne_zip_path)

    mapping: Dict[str, str] = {}
    for _, row in gdf.iterrows():
        iso = row.get("ISO_A2_EH")
        if not isinstance(iso, str) or not iso.strip() or iso == "-99":
            continue
        iso = iso.strip()
        for col in NATURAL_EARTH_NAME_COLUMNS:
            name = row.get(col)
            if isinstance(name, str) and name.strip():
                mapping.setdefault(name.strip(), iso)

    return mapping


def parse_coordinate_cleaner_ref(rda_bytes: bytes) -> pd.DataFrame:
    """
    Parse CoordinateCleaner countryref.rda and return a DataFrame
    with centroid and capital coordinates per country.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".rda")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(rda_bytes)
        result = pyreadr.read_r(tmp_path)
    finally:
        os.remove(tmp_path)

    df = result["countryref"]

    df = df[["iso2", "centroid.lon", "centroid.lat", "capital.lon", "capital.lat"]]

    return df


def parse_ror_institutions(ror_bytes: bytes) -> pd.DataFrame:
    """
    Parse ROR data dump and return a DataFrame with institution
    name, country code, and coordinates.
    """
    # ROR data dump is a zip file containing a CSV
    with zipfile.ZipFile(io.BytesIO(ror_bytes)) as z:
        # Find the CSV file inside the zip
        csv_files = [f for f in z.namelist() if f.endswith(".csv")]
        if not csv_files:
            raise ValueError(
                f"No CSV file found in ROR zip. Files present: {z.namelist()}"
            )
        if len(csv_files) > 1:
            raise ValueError(f"Multiple CSV files found in ROR zip: {csv_files}")
        logging.info(f"Files in ROR zip: {z.namelist()}")
        with z.open(csv_files[0]) as f:
            df = pd.read_csv(f)

    df = df[list(ROR_COLUMN_MAP)].rename(columns=ROR_COLUMN_MAP)
    df = df.dropna(subset=["latitude", "longitude"])
    return df


def save_natural_earth(ne_bytes: bytes, output_path: Path) -> None:
    """
    Save Natural Earth zip directly to resources — geopandas reads it natively.
    """
    with open(output_path, "wb") as f:
        f.write(ne_bytes)
    logging.info(f"Natural Earth boundaries saved to {output_path}")


def create_final_cc_mapping(
    ena_countries: List[str],
    iso_cc: Dict[str, str],
) -> tuple[Dict[str, list], List[str]]:

    final_mapping = {}
    oceans_and_seas = []

    for country in ena_countries:
        if "Ocean" in country or "Sea" in country:
            oceans_and_seas.append(country)
            continue

        if country in NON_COUNTRIES:
            oceans_and_seas.append(country)
            continue

        original_country = country
        if country not in iso_cc:
            iso_country = MISSING_COUNTRY_MAPPING.get(country, None)
            # target may not exist in the Natural Earth crosswalk either
            if iso_country and iso_country in iso_cc:
                country = iso_country
            else:
                logging.warning(
                    f"Warning: country {country} not found in "
                    "Natural Earth name crosswalk"
                )
                continue

        final_mapping[original_country] = [country, iso_cc[country]]

    return final_mapping, oceans_and_seas


def get_tool_version() -> str:
    """
    Version of this package, read from installed metadata (pyproject).

    Falls back to "unknown" when the package isn't installed (e.g. run
    straight from a source checkout without an editable install).
    """
    try:
        return pkg_version("SAMBAL")
    except PackageNotFoundError:
        return "unknown"


def _digest(data: bytes) -> dict:
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def _file_digest(path: Path) -> dict:
    return _digest(path.read_bytes())


def natural_earth_version(ne_zip_path: Path) -> Optional[str]:
    """
    Natural Earth bundles its release (e.g. "5.1.1") in a *.VERSION.txt file
    inside the zip — authoritative and present whether downloaded or supplied
    locally. Returns None if absent/unreadable.
    """
    try:
        with zipfile.ZipFile(ne_zip_path) as z:
            for name in z.namelist():
                if name.endswith(".VERSION.txt"):
                    return z.read(name).decode("utf-8").strip()
    except Exception as e:
        logging.warning(f"Could not read Natural Earth version: {e}")
    return None


def ror_version(ror_url: str) -> Optional[str]:
    """ROR dump filenames encode the release, e.g. 'v1.63-2025-...-ror-data.zip'."""
    match = re.search(r"v\d+\.\d+", ror_url)
    return match.group(0) if match else None


def ena_checklist_version(ena_xml: str) -> Optional[str]:
    """The ENA checklist accession (e.g. 'ERC000011') is its stable identifier."""
    try:
        root = ElementTree.fromstring(ena_xml)
        checklist = root if root.tag == "CHECKLIST" else root.find(".//CHECKLIST")
        if checklist is not None:
            return checklist.get("version") or checklist.get("accession")
    except Exception as e:
        logging.warning(f"Could not read ENA checklist version: {e}")
    return None


def coordinate_cleaner_commit() -> Optional[str]:
    """
    COORDINATE_CLEANER_URL tracks a moving branch, so pin provenance to the
    commit that last touched countryref.rda. Best-effort — returns None on any
    failure so it never blocks the build.
    """
    api = (
        "https://api.github.com/repos/ropensci/CoordinateCleaner/commits"
        "?path=data/countryref.rda&per_page=1"
    )
    try:
        r = requests.get(api, timeout=10)
        r.raise_for_status()
        return r.json()[0]["sha"]
    except Exception as e:
        logging.warning(f"Could not read CoordinateCleaner commit: {e}")
        return None


def main(
    resource_dir: Optional[Path] = None,
    natural_earth_zip: Optional[Path] = None,
):

    logging.info("Running geographical mapping setup...")

    if resource_dir is None:
        resource_dir = Path(__file__).parent / "resources"

    if not resource_dir.exists():
        logging.error(f"Resource directory {resource_dir} does not exist. Exiting...")
        return

    # Resolve the Natural Earth source once. After this block natural_earth_zip
    # is either a usable path or None (meaning: download a fresh copy).
    if natural_earth_zip is not None:
        natural_earth_zip = Path(natural_earth_zip)
        if not natural_earth_zip.exists():
            logging.warning(
                f"Provided Natural Earth zip {natural_earth_zip} not found; "
                "downloading instead."
            )
            natural_earth_zip = None
    use_provided_ne = natural_earth_zip is not None

    generated_at = datetime.now(timezone.utc).isoformat()
    ena_xml, cc_rda, ror_bytes, ne_bytes, ror_url = get_checklist_countries(
        download_natural_earth=not use_provided_ne
    )

    natural_earth_path = resource_dir / "ne_countries.zip"
    if use_provided_ne:
        logging.info(f"Using provided Natural Earth zip {natural_earth_zip}")
        ne_bytes = natural_earth_zip.read_bytes()
    save_natural_earth(ne_bytes, natural_earth_path)

    ena_countries = parse_ena_xml(ena_xml)
    logging.info(f"{len(ena_countries)} countries found in ENA checklist")
    iso_cc = parse_natural_earth_country_codes(natural_earth_path)
    logging.info(f"{len(iso_cc)} country name variants found in Natural Earth")

    final_mapping_path = resource_dir / "country_to_cc_mapping.csv"
    oceans_and_seas_path = resource_dir / "oceans_and_seas.txt"
    centroids_and_capitals_path = resource_dir / "country_centroids_and_capitals.csv"

    final_mapping, oceans_and_seas = create_final_cc_mapping(ena_countries, iso_cc)

    # Ensure all missing country mappings are included
    for original, mapped in MISSING_COUNTRY_MAPPING.items():
        if original not in final_mapping and mapped in final_mapping:
            final_mapping[original] = final_mapping[mapped]

    with open(final_mapping_path, "w") as f:
        writer = csv.writer(f)
        for key, value in final_mapping.items():
            writer.writerow([key, value[0], value[1]])
    with open(oceans_and_seas_path, "w") as f:
        f.writelines("\n".join(oceans_and_seas))

    centroids_df = parse_coordinate_cleaner_ref(cc_rda)
    centroids_df.to_csv(centroids_and_capitals_path, index=False)
    logging.info(
        f"{len(centroids_df)} centroid/capital records saved to "
        f"{centroids_and_capitals_path}"
    )

    institutions_path = resource_dir / "research_institutions.csv"
    institutions_df = parse_ror_institutions(ror_bytes)
    institutions_df.to_csv(institutions_path, index=False)
    logging.info(
        f"{len(institutions_df)} institution records saved to {institutions_path}"
    )

    manifest = {
        "tool": {
            "name": "SAMBAL",
            "version": get_tool_version(),
        },
        "generated_at": generated_at,
        "inputs": {
            "ena_checklist": {
                "source_url": ENA_URL,
                "version": ena_checklist_version(ena_xml),
                **_digest(ena_xml.encode("utf-8")),
            },
            "coordinate_cleaner": {
                "source_url": COORDINATE_CLEANER_URL,
                "commit": coordinate_cleaner_commit(),
                **_digest(cc_rda),
            },
            "ror": {
                "source_url": ror_url,
                "zenodo_concept_id": ROR_ZENODO_ID,
                "version": ror_version(ror_url),
                **_digest(ror_bytes),
            },
            "natural_earth": {
                "source_url": (
                    str(natural_earth_zip) if use_provided_ne else NATURAL_EARTH_URL
                ),
                "source": "user-provided" if use_provided_ne else "download",
                "version": natural_earth_version(natural_earth_path),
                **_file_digest(natural_earth_path),
            },
        },
        "outputs": {
            "ne_countries.zip": _file_digest(natural_earth_path),
            "country_to_cc_mapping.csv": {
                **_file_digest(final_mapping_path),
                "rows": len(final_mapping),
            },
            "oceans_and_seas.txt": {
                **_file_digest(oceans_and_seas_path),
                "rows": len(oceans_and_seas),
            },
            "country_centroids_and_capitals.csv": {
                **_file_digest(centroids_and_capitals_path),
                "rows": len(centroids_df),
            },
            "research_institutions.csv": {
                **_file_digest(institutions_path),
                "rows": len(institutions_df),
            },
        },
    }
    manifest_path = resource_dir / "resource_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    logging.info(f"Resource manifest written to {manifest_path}")

    logging.info("Mapping complete")


def cli():
    import argparse

    parser = argparse.ArgumentParser(
        prog="setup-sample-resources",
        description="Download and build the reference resource files.",
    )
    parser.add_argument(
        "--resource-dir",
        type=Path,
        default=None,
        help="Directory to write resource files to (default: package resources).",
    )
    parser.add_argument(
        "--natural-earth-zip",
        type=Path,
        default=None,
        help=(
            "Path to an existing Natural Earth 10m admin_0 countries zip to use "
            "instead of downloading it."
        ),
    )
    args = parser.parse_args()
    main(
        resource_dir=args.resource_dir,
        natural_earth_zip=args.natural_earth_zip,
    )


if __name__ == "__main__":
    cli()
