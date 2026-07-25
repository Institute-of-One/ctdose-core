"""Helpers for working with public data from The Cancer Imaging Archive.

Only the public REST API is used, and only for metadata and image retrieval of
collections the archive already publishes as de-identified. No credentials, no
private endpoints, and nothing is written back.

Network access is optional: :func:`survey_directory` works entirely offline on data
already on disk, which is what the reproducibility tests use.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import ScanSeries, load_series

TCIA_BASE_URL = "https://services.cancerimagingarchive.net/nbia-api/services/v1"

#: Collections known to retain per-slice tube current, i.e. usable for the tube-current
#: modulation work. Verify licence terms per collection before redistribution.
CANDIDATE_COLLECTIONS = (
    "LDCT-and-Projection-data",
    "CT-Lymph-Nodes",
    "CT-COLONOGRAPHY",
)

DATA_USE_NOTE = (
    "TCIA data are subject to the TCIA Data Usage Policy and each collection's own "
    "licence (often CC BY 3.0/4.0). Record the collection name, licence and access "
    "date in any publication; do not redistribute images without checking the licence."
)


def _get_json(endpoint: str, **params: Any) -> Any:
    url = f"{TCIA_BASE_URL}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - fixed public host
        return json.loads(resp.read().decode("utf-8"))


def list_collections() -> list[str]:
    """Every collection name the archive publishes."""
    return [row["Collection"] for row in _get_json("getCollectionValues")]


def list_series(collection: str, modality: str = "CT") -> list[dict[str, Any]]:
    """Series-level metadata for a collection."""
    return _get_json("getSeries", Collection=collection, Modality=modality)


def download_series(series_uid: str, dest: Path | str) -> Path:
    """Download one series as a zip archive.

    Returns the path to the written file. The caller is responsible for extraction and
    for honouring the collection's licence.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{TCIA_BASE_URL}/getImage?" + urllib.parse.urlencode({"SeriesInstanceUID": series_uid})
    with urllib.request.urlopen(url, timeout=600) as resp:  # noqa: S310 - fixed public host
        dest.write_bytes(resp.read())
    return dest


@dataclass
class SeriesInventory:
    """What a series carries, from the dose-surveillance point of view."""

    path: Path
    series_uid: str
    manufacturer: str
    model_name: str
    n_slices: int
    has_ctdivol_tag: bool
    has_dlp_record: bool
    has_tube_current: bool
    tube_current_is_modulated: bool
    kvp: float | None
    protocol_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "series_uid": self.series_uid,
            "manufacturer": self.manufacturer,
            "model_name": self.model_name,
            "n_slices": self.n_slices,
            "has_ctdivol_tag": self.has_ctdivol_tag,
            "has_dlp_record": self.has_dlp_record,
            "has_tube_current": self.has_tube_current,
            "tube_current_is_modulated": self.tube_current_is_modulated,
            "kvp": self.kvp,
            "protocol_name": self.protocol_name,
        }


def inventory(series: ScanSeries, path: Path | None = None) -> SeriesInventory:
    """Summarise which dose-relevant attributes a loaded series actually has."""
    import pydicom

    uid = ""
    if series.files:
        ds = pydicom.dcmread(str(series.files[0]), stop_before_pixels=True)
        uid = str(ds.get((0x0020, 0x000E), "").value if (0x0020, 0x000E) in ds else "")
    currents = series.tube_current_ma
    return SeriesInventory(
        path=path or (series.files[0].parent if series.files else Path(".")),
        series_uid=uid,
        manufacturer=series.manufacturer,
        model_name=series.model_name,
        n_slices=series.n_slices,
        has_ctdivol_tag=bool(series.ctdivol_tag and series.ctdivol_tag.value),
        has_dlp_record=bool(series.dlp_tag and series.dlp_tag.value),
        has_tube_current=currents.size > 0,
        tube_current_is_modulated=bool(currents.size > 1 and float(currents.std()) > 1e-6),
        kvp=series.kvp,
        protocol_name=series.protocol_name,
    )


def iter_series_directories(root: Path | str) -> Iterator[Path]:
    """Yield every directory under ``root`` that directly contains DICOM files."""
    root = Path(root)
    yield from sorted({p.parent for p in root.rglob("*") if p.is_file()})


def survey_directory(root: Path | str, *, limit: int | None = None) -> list[SeriesInventory]:
    """Inventory every series found under ``root``.

    This is how a public collection is screened before analysis: it answers "how many
    of these series actually carry CTDIvol, and how many need the physics fallback?"
    without downloading anything further.
    """
    out: list[SeriesInventory] = []
    for directory in iter_series_directories(root):
        try:
            series = load_series(directory)
        except (FileNotFoundError, ValueError):
            continue
        out.append(inventory(series, directory))
        if limit and len(out) >= limit:
            break
    return out


def tag_coverage(inventories: Iterable[SeriesInventory]) -> dict[str, Any]:
    """Aggregate coverage statistics over a set of series."""
    items = list(inventories)
    n = len(items)
    if n == 0:
        return {"n_series": 0}
    count = lambda pred: sum(1 for i in items if pred(i))  # noqa: E731
    return {
        "n_series": n,
        "with_ctdivol_tag": count(lambda i: i.has_ctdivol_tag),
        "with_dlp_record": count(lambda i: i.has_dlp_record),
        "with_tube_current": count(lambda i: i.has_tube_current),
        "with_modulated_tube_current": count(lambda i: i.tube_current_is_modulated),
        "fraction_needing_fallback": count(lambda i: not i.has_ctdivol_tag) / n,
        "note": DATA_USE_NOTE,
    }
