import zipfile

import pytest


@pytest.fixture
def make_ne_zip(tmp_path):
    """
    Factory fixture: build a small synthetic Natural Earth
    admin_0_countries shapefile zip from the given attribute rows, so tests
    can exercise real shapefile parsing without network access or a full
    real Natural Earth download.
    """

    def _make(rows, filename="ne_10m_admin_0_countries"):
        import geopandas as gpd

        gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
        shp_dir = tmp_path / f"{filename}_shp"
        shp_dir.mkdir()
        shp_path = shp_dir / f"{filename}.shp"
        gdf.to_file(shp_path, driver="ESRI Shapefile")

        zip_path = tmp_path / f"{filename}.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for f in shp_dir.iterdir():
                zf.write(f, f.name)
        return zip_path

    return _make
