import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from sample_metadata_curation.biome import BiomeCurator
from sample_metadata_curation.location import LocationCurator
from sample_metadata_curation.sample_parser import (
    load_json,
    parse_arguments,
    standardise_keys,
)


class SampleCurator:
    def __init__(
        self,
        resources_dir: Optional[Path] = None,
        biome_keys: Optional[List[str]] = None,
        natural_earth_zip: Optional[Path] = None,
    ):
        if resources_dir is None:
            resources_dir = Path(__file__).parent / "resources"

        self.location_curator = LocationCurator(
            resources_dir=resources_dir, natural_earth_zip=natural_earth_zip
        )
        self.biome_curator = BiomeCurator(biome_keys=biome_keys)

    def curate_sample(self, sample_json: Dict[str, Any]) -> Dict[str, Any]:
        """
        Takes a BioSamples JSON dictionary and returns a cleaned dictionary
        with extracted region, locality, latitude, longitude and reasons
        for pass or failed sanity check
        """
        cleaned_dict = standardise_keys(sample_json)

        accession = sample_json.get("accession")
        result = self.location_curator.curate_location(
            cleaned_dict, accession=accession
        )

        for key, value in cleaned_dict.items():
            if key not in result and key != "accession":
                from .constants import LOCATION_KEYS

                if (
                    key not in LOCATION_KEYS["lat_lon"]
                    and key not in LOCATION_KEYS["location"]
                    and key not in LOCATION_KEYS["lat"]
                    and key not in LOCATION_KEYS["lon"]
                ):
                    result[key] = value

        # Biome extraction
        biome_result = self.biome_curator.curate_biome(cleaned_dict)
        result.update(biome_result)

        return result


def curate_biosample(
    input_data: Any,
    biome_keys: Optional[List[str]] = None,
    natural_earth_zip: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Curate one biosample
    Input can be a BioSamples JSON dict, a JSON string, or a path to a JSON file.
    """
    if isinstance(input_data, (str, os.PathLike)):
        sample_json = load_json(str(input_data))
    else:
        sample_json = input_data

    if not sample_json:
        return {}

    curator = SampleCurator(biome_keys=biome_keys, natural_earth_zip=natural_earth_zip)
    return curator.curate_sample(sample_json)


def main():
    args = parse_arguments()
    biome_keys = args.biome.split(",") if args.biome else None
    result = curate_biosample(args.sample_json, biome_keys=biome_keys)
    if result:
        print(json.dumps(result, indent=2))
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
