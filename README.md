# SAMBAL

**SA**mple **M**etadata for **B**iogeographic **AL**ignment

A Python package for curating and cleaning BioSample metadata, specifically focusing on location and coordinate (latitude/longitude) normalization.

## Features

- **Location Cleaning**: Validates and normalizes geographic location names.
- **Coordinate Parsing**: Extracts latitude and longitude from various formats, including combined `lat_lon` strings.
- **Coordinate Validation**: Checks for valid latitude (-90 to 90) and longitude (-180 to 180) ranges.
- **Automatic Switching**: Intelligent swapping of latitude and longitude if they are provided in the wrong order but fall within valid ranges when swapped.
- **Flexible Input**: Supports BioSample JSON data as either a string or a local file path.

## Source Data
The folder "resources" contains country codes from one of the sources used by INSDC https://www.cia.gov/the-world-factbook/references/country-data-codes/

## Installation

### For Users

To install the package in your environment:

```bash
pip install .
setup-sample-resources
```

### For Developers

To install with development tools (Black, isort, Flake8, pytest):

```bash
pip install -e ".[dev]"
setup-sample-resources
```

## Usage

### Command Line Interface

The package provides two main command-line tools:

1. **`curate-sample`**: Curates BioSample metadata.
   ```bash
   curate-sample --sample_json path/to/sample.json
   ```

2. **`setup-sample-resources`**: Fetches and prepares external resources required by the package.
   ```bash
   setup-sample-resources
   ```

Or using a JSON string:

```bash
curate-sample --sample_json '{"accession": "SAMN...", "characteristics": {...}}'
```

### As a Library

You can also use the curation logic in your own Python scripts. The `curate_biosample` function is flexible and accepts a dictionary, a JSON string, or a path to a JSON file:

```python
from sample_metadata_curation import curate_biosample

# Option 1: Use a dictionary
sample_data = {
    "accession": "SAMN39868869",
    "characteristics": {
        "geo_loc_name": [{"text": "Denmark"}],
        "lat_lon": [{"text": "55.62115 N 8.2849 E"}]
    }
}
result = curate_biosample(sample_data)

# Option 2: Use a JSON string
json_string = '{"accession": "SAMN39868869", ...}'
result = curate_biosample(json_string)

# Option 3: Use a path to a JSON file
result = curate_biosample("path/to/sample.json")

print(result)
# Output: {'accession': 'SAMN39868869', 'location': 'Denmark', 'latitude': 55.62115, 'longitude': 8.2849, 'coord_precision_deg': 0.0001}
```

coord_precision_deg is an estimate of the coordinate precision in degrees, where approximately: 1.0 ≈ 111km, 0.1 ≈ 11km, 0.01 ≈ 1km, 0.001 ≈ 111m, 0.0001 ≈ 11m.

## Development

### Linting and Formatting

We use `black`, `isort`, and `flake8` to maintain code quality.

```bash
black .
isort .
flake8 .
```

### Testing

Run the test suite using `pytest`:

```bash
pytest
```

### Pre-commit Hooks

To ensure all code meets quality standards before committing, install the pre-commit hooks:

```bash
pre-commit install
```

## CI/CD

This project uses GitHub Actions for continuous integration. Every push and pull request to the `main` branch triggers:
- Linting checks (Black, isort, Flake8)
- Automated testing (pytest)
