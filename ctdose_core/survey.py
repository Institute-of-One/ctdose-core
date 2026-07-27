"""Layer-2 survey: how much recorded dose does a public archive actually carry?

Layer 1 (:mod:`ctdose_core.rdsr`) establishes that a recorded dose is read correctly
when one exists. This module measures the case that motivates the package: public CT
archives where it does not.

Two quantities are measured, stratified by manufacturer:

**(a) Tag absence.**  For each series, one image header is fetched and inspected for
CTDIvol (0018,9345) and a DLP record. Alongside it, *fallback feasibility* is recorded
-- whether the acquisition parameters the physics reconstruction needs are present at
all -- because a series with neither a recorded dose nor a usable technique description
cannot be surveyed by any method.

**(b) Fallback error.**  For series that *do* carry CTDIvol, the whole series is
fetched, the recorded value hidden, and the physics reconstruction compared against it.
This is the only way to put an uncertainty on the reconstruction, and it can only be
done where a recorded value exists.

Networking uses the public NBIA REST API directly. ``tcia_utils`` is used instead when
it is importable; it is not required, because its transitive ``idc-index`` dependency
pins an old pandas and does not install on every supported Python.

Nothing here writes to the archive, and no credentials are used: only public,
de-identified collections are read. Each series' licence is carried through to the
results so a data-availability statement can be written from them.
"""

from __future__ import annotations

import io
import json
import random
import statistics
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pydicom

from .io import ScanSeries, load_series
from .metrics import Acquisition, acquisition_from_series, estimate_ctdivol, fallback_error
from .provenance import Source
from .scanners import AmbiguousScannerError, resolve_model
from .tables import UnknownScannerError, lookup_scanner

NBIA_BASE = "https://services.cancerimagingarchive.net/nbia-api/services/v1"

#: Manufacturer strings as they appear in TCIA's own index, grouped to a vendor label.
#: TCIA indexes the DICOM Manufacturer verbatim, so one vendor has several spellings.
VENDOR_QUERIES: dict[str, tuple[str, ...]] = {
    "GE": ("GE MEDICAL SYSTEMS", "GE"),
    "Siemens": ("SIEMENS",),
    "Canon/Toshiba": ("TOSHIBA", "Canon Medical Systems"),
    "Philips": ("Philips",),
}

DATA_USE_NOTE = (
    "TCIA data are subject to the TCIA Data Usage Policy and each collection's own "
    "licence. The per-series licence recorded in these results must be reproduced in "
    "any publication, together with the collection name and the access date."
)


# --- transport --------------------------------------------------------------------------


class TciaClient:
    """Minimal public NBIA REST client.

    Args:
        base_url: API root. Overridable for testing against a stub.
        timeout: per-request timeout in seconds.
        cache_dir: when set, downloaded objects are cached so a re-run costs nothing.
    """

    def __init__(
        self,
        base_url: str = NBIA_BASE,
        *,
        timeout: float = 300.0,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _url(self, endpoint: str, params: dict[str, Any]) -> str:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        return f"{self.base_url}/{endpoint}" + (f"?{query}" if query else "")

    def get_json(self, endpoint: str, **params: Any) -> list[dict[str, Any]]:
        url = self._url(endpoint, params)
        with urllib.request.urlopen(url, timeout=self.timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8").strip()
        return json.loads(body) if body else []

    def get_bytes(self, endpoint: str, cache_key: str | None = None, **params: Any) -> bytes:
        cached = self.cache_dir / cache_key if (self.cache_dir and cache_key) else None
        if cached is not None and cached.exists():
            return cached.read_bytes()
        url = self._url(endpoint, params)
        with urllib.request.urlopen(url, timeout=self.timeout) as resp:  # noqa: S310
            data = resp.read()
        if cached is not None:
            cached.write_bytes(data)
        return data

    # -- endpoints ------------------------------------------------------------------

    def series(self, *, modality: str = "CT", manufacturer: str | None = None) -> list[dict]:
        """Series-level index entries, optionally restricted to one manufacturer string."""
        return self.get_json("getSeries", Modality=modality, Manufacturer=manufacturer)

    def sop_instance_uids(self, series_uid: str) -> list[str]:
        rows = self.get_json("getSOPInstanceUIDs", SeriesInstanceUID=series_uid)
        return [r["SOPInstanceUID"] for r in rows if r.get("SOPInstanceUID")]

    def single_image(self, series_uid: str, sop_uid: str) -> pydicom.Dataset:
        raw = self.get_bytes(
            "getSingleImage",
            cache_key=f"img_{sop_uid}.dcm",
            SeriesInstanceUID=series_uid,
            SOPInstanceUID=sop_uid,
        )
        return pydicom.dcmread(io.BytesIO(raw), stop_before_pixels=True, force=True)

    def download_series(self, series_uid: str, dest: Path) -> Path:
        """Fetch a whole series and extract its DICOM files into ``dest``."""
        raw = self.get_bytes("getImage", cache_key=f"series_{series_uid}.zip", SeriesInstanceUID=series_uid)
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".dcm"):
                    continue  # the archive also carries a LICENSE text file
                (dest / Path(name).name).write_bytes(zf.read(name))
        return dest


def tcia_utils_available() -> bool:
    """True when the optional ``tcia_utils`` package can be imported."""
    try:  # pragma: no cover - depends on optional install
        from tcia_utils import nbia  # noqa: F401
    except Exception:
        return False
    return True


def series_index(
    vendor_query: str, *, client: TciaClient | None = None, modality: str = "CT"
) -> list[dict]:
    """Series index for one manufacturer string, via ``tcia_utils`` if it is installed."""
    try:  # pragma: no cover - depends on optional install
        from tcia_utils import nbia

        rows = nbia.getSeries(modality=modality, manufacturer=vendor_query)
        if rows:
            return list(rows)
    except Exception:
        pass
    return (client or TciaClient()).series(modality=modality, manufacturer=vendor_query)


# --- (a) header screening -------------------------------------------------------------


def _present(ds: pydicom.Dataset, tag: tuple[int, int]) -> bool:
    if tag not in ds:
        return False
    value = ds[tag].value
    return value is not None and str(value).strip() not in ("", "None")


@dataclass
class SeriesScreen:
    """What one series' image header carries, from the dose-surveillance point of view."""

    vendor: str
    manufacturer_raw: str
    collection: str
    series_uid: str
    model_name: str
    n_images: int | None
    licence: str = ""
    licence_url: str = ""
    body_part: str = ""
    has_ctdivol_tag: bool = False
    has_dlp_record: bool = False
    has_tube_current: bool = False
    has_timing: bool = False
    has_collimation: bool = False
    has_pitch: bool = False
    has_slice_thickness: bool = False
    has_slice_location: bool = False
    model_resolved: str | None = None
    model_resolution: str = ""  # "resolved" | "ambiguous" | "unknown"
    error: str = ""

    @property
    def fallback_feasible(self) -> bool:
        """Whether the physics reconstruction has the inputs it needs.

        Pitch may legitimately be absent (an axial acquisition defaults to 1.0), and
        collimation can be rebuilt from table feed, so neither is required here; the
        remainder are.
        """
        return (
            self.has_tube_current
            and self.has_timing
            and self.has_collimation
            and self.has_slice_thickness
            and self.has_slice_location
        )

    @property
    def surveyable(self) -> bool:
        """Either a recorded dose exists, or it can be reconstructed."""
        return self.has_ctdivol_tag or self.fallback_feasible

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fallback_feasible"] = self.fallback_feasible
        d["surveyable"] = self.surveyable
        return d


def screen_series(
    entry: dict,
    vendor: str,
    *,
    client: TciaClient,
) -> SeriesScreen:
    """Fetch one image of a series and record which dose-relevant attributes it has."""
    screen = SeriesScreen(
        vendor=vendor,
        manufacturer_raw=str(entry.get("Manufacturer", "")),
        collection=str(entry.get("Collection", "")),
        series_uid=str(entry.get("SeriesInstanceUID", "")),
        model_name=str(entry.get("ManufacturerModelName", "") or ""),
        n_images=entry.get("ImageCount"),
        licence=str(entry.get("LicenseName", "") or ""),
        licence_url=str(entry.get("LicenseURI", "") or ""),
        body_part=str(entry.get("BodyPartExamined", "") or ""),
    )
    try:
        sops = client.sop_instance_uids(screen.series_uid)
        if not sops:
            screen.error = "no SOP instances"
            return screen
        # Mid-series image: a localiser or the first slice is less representative.
        ds = client.single_image(screen.series_uid, sops[len(sops) // 2])
    except Exception as exc:
        screen.error = f"{type(exc).__name__}: {exc}"
        return screen

    screen.model_name = str(ds.get("ManufacturerModelName", "") or screen.model_name)
    screen.has_ctdivol_tag = _present(ds, (0x0018, 0x9345))
    screen.has_dlp_record = _present(ds, (0x0040, 0x0310))
    screen.has_tube_current = _present(ds, (0x0018, 0x1151))
    screen.has_timing = any(
        _present(ds, t) for t in ((0x0019, 0x1027), (0x0018, 0x9305), (0x0018, 0x1150))
    )
    screen.has_collimation = _present(ds, (0x0018, 0x9307)) or any(
        _present(ds, t) for t in ((0x0018, 0x9310), (0x0019, 0x1023), (0x7005, 0x100A))
    )
    screen.has_pitch = any(
        _present(ds, t) for t in ((0x0043, 0x1027), (0x0018, 0x9311), (0x7005, 0x1023))
    )
    screen.has_slice_thickness = _present(ds, (0x0018, 0x0050))
    screen.has_slice_location = _present(ds, (0x0020, 0x1041)) or _present(ds, (0x0020, 0x0032))

    try:
        model, _ = resolve_model(screen.model_name, screen.manufacturer_raw)
        screen.model_resolved, screen.model_resolution = model, "resolved"
    except AmbiguousScannerError as exc:
        screen.model_resolution = "ambiguous"
        screen.model_resolved = "|".join(c.table_model for c in exc.candidates)
    except LookupError:
        screen.model_resolution = "unknown"
    return screen


# --- (b) fallback error -----------------------------------------------------------------


#: Plausible range for a recorded CTDIvol in mGy. Values outside it are corrupt --
#: encountered in practice as a nonsense binary value in one vendor's header -- and are
#: excluded from the error statistics rather than allowed to dominate them.
PLAUSIBLE_CTDIVOL_MGY = (0.01, 1000.0)


def implausible_recorded_ctdivol(value: float) -> bool:
    lo, hi = PLAUSIBLE_CTDIVOL_MGY
    return not (lo <= value <= hi)


#: A recorded CTDIvol below this is not a diagnostic acquisition. Localisers, scan-plan
#: topograms and dose-check placeholders land here. They are physically valid values, so
#: they are kept in the data, but comparing one against a helical reconstruction of the
#: reconstructed series is comparing two different acquisitions, and the resulting
#: relative error is enormous and meaningless. The error distribution is therefore also
#: reported restricted to diagnostic acquisitions.
DIAGNOSTIC_CTDIVOL_MGY = 0.5


@dataclass
class FallbackCase:
    """One series where a recorded CTDIvol let the reconstruction be checked."""

    vendor: str
    collection: str
    series_uid: str
    model_name: str
    table_model: str
    n_slices: int
    recorded_ctdivol_mgy: float
    estimated_ctdivol_mgy: float
    relative_error: float
    percent_error: float
    region: str
    #: True when the DICOM model name maps to several coefficient rows -- a bowtie /
    #: scan-field variant (``small``/``medium``/``large BS``) or an acquisition mode
    #: (``Definition AS`` vs ``Definition Edge``, standard vs cardiac) that the header
    #: does not distinguish.
    model_ambiguous: bool = False
    #: Estimate per candidate coefficient row when the model name cannot pick one.
    alternatives: dict[str, float] = field(default_factory=dict)
    #: Smallest absolute error achievable across the candidates -- what the method could
    #: do if the scanner variant were identified. Equals ``percent_error`` when it is.
    best_case_percent_error: float | None = None
    #: True when total collimation was read from the header rather than reconstructed.
    #: The over-beaming factor grows roughly as 1/collimation once the collimation falls
    #: below the reference beam width, so an assumed value dominates the error: a header
    #: that omits collimation and forces a fall back to slice thickness can inflate
    #: CTDIvol several-fold.
    collimation_measured: bool = True
    collimation_mm: float | None = None
    collimation_note: str = ""
    pitch_measured: bool = True
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _candidate_models(screen: SeriesScreen) -> list[str]:
    if screen.model_resolution == "resolved" and screen.model_resolved:
        return [screen.model_resolved]
    if screen.model_resolution == "ambiguous" and screen.model_resolved:
        return screen.model_resolved.split("|")
    return []


#: Region used when the series header does not say. The body branch of the coefficient
#: table is the right default for trunk CT, which is what public archives mostly hold.
DEFAULT_REGION = "Chest"

HEAD_BODY_PARTS = {"HEAD", "BRAIN", "SKULL", "NECK", "HEADNECK"}


def region_for(body_part: str) -> str:
    return "Head" if str(body_part).strip().upper() in HEAD_BODY_PARTS else DEFAULT_REGION


def evaluate_fallback(
    screen: SeriesScreen,
    series: ScanSeries,
    *,
    region: str | None = None,
) -> FallbackCase | None:
    """Compare the physics reconstruction against this series' recorded CTDIvol.

    Returns ``None`` when the series cannot be evaluated -- no recorded value, an
    unresolvable scanner model, or missing acquisition parameters. Those exclusions are
    counted by the caller rather than hidden.
    """
    recorded = series.ctdivol_tag.value if series.ctdivol_tag else None
    if not recorded or implausible_recorded_ctdivol(float(recorded)):
        return None
    models = _candidate_models(screen)
    if not models:
        return None
    region = region or region_for(screen.body_part)

    try:
        acq: Acquisition = acquisition_from_series(series)
    except ValueError:
        return None

    estimates: dict[str, float] = {}
    for model in models:
        try:
            coef = lookup_scanner(model, region, "Adults")
        except UnknownScannerError:
            continue
        estimates[model] = float(estimate_ctdivol(acq, coef))
    if not estimates:
        return None

    notes: list[str] = []
    if len(estimates) == 1:
        primary_model, primary = next(iter(estimates.items()))
    else:
        # The DICOM model name does not identify the scanner variant -- the bowtie /
        # scan field of view, or the acquisition mode -- so there is no principled way
        # to pick one row. Report the median across the candidates, a neutral choice
        # that neither flatters nor punishes the method, and carry every candidate plus
        # the best achievable error alongside it.
        ranked = sorted(estimates.items(), key=lambda kv: kv[1])
        primary_model, primary = ranked[len(ranked) // 2]
        notes.append(
            "the DICOM model name does not identify the scanner variant (bowtie / scan "
            "field of view, or acquisition mode); the median across candidate "
            "coefficient rows is reported, and every candidate is listed in "
            "'alternatives'"
        )

    err = fallback_error(float(recorded), primary)
    best = min(
        (fallback_error(float(recorded), v)["percent_error"] for v in estimates.values()),
        key=abs,
    )
    return FallbackCase(
        vendor=screen.vendor,
        collection=screen.collection,
        series_uid=screen.series_uid,
        model_name=screen.model_name,
        table_model=primary_model,
        n_slices=series.n_slices,
        recorded_ctdivol_mgy=float(recorded),
        estimated_ctdivol_mgy=primary,
        relative_error=err["relative_error"],
        percent_error=err["percent_error"],
        region=region,
        model_ambiguous=len(estimates) > 1,
        alternatives=estimates,
        best_case_percent_error=best,
        collimation_measured=(
            series.collimation_mm is not None
            and series.collimation_mm.source is not Source.PHYSICS_FALLBACK
        ),
        collimation_mm=acq.collimation_mm,
        collimation_note=(series.collimation_mm.note if series.collimation_mm else ""),
        pitch_measured=(
            series.pitch is not None and series.pitch.source is not Source.PHYSICS_FALLBACK
        ),
        notes=tuple(notes),
    )


# --- aggregation --------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarise_screens(screens: Sequence[SeriesScreen]) -> dict[str, Any]:
    """Tag-absence and fallback-feasibility rates, overall and per vendor."""

    def block(rows: Sequence[SeriesScreen]) -> dict[str, Any]:
        ok = [r for r in rows if not r.error]
        n = len(ok)
        with_ctdi = sum(1 for r in ok if r.has_ctdivol_tag)
        feasible = sum(1 for r in ok if r.fallback_feasible)
        # The marginal feasibility rate is over all series, including those that already
        # carry a recorded dose. The question the reconstruction exists to answer is the
        # conditional one -- of the series with no recorded dose, how many can be
        # reconstructed -- so both are reported.
        without_ctdi = [r for r in ok if not r.has_ctdivol_tag]
        recoverable = [r for r in without_ctdi if r.fallback_feasible]
        return {
            "n_series_screened": len(rows),
            "n_series_readable": n,
            "n_errors": len(rows) - n,
            "with_ctdivol_tag": with_ctdi,
            "without_ctdivol_tag": n - with_ctdi,
            "ctdivol_absence_rate": _rate(n - with_ctdi, n),
            "n_without_ctdivol_reconstructable": len(recoverable),
            "conditional_reconstructability_rate": _rate(len(recoverable), len(without_ctdi)),
            "with_dlp_record": sum(1 for r in ok if r.has_dlp_record),
            "dlp_absence_rate": _rate(n - sum(1 for r in ok if r.has_dlp_record), n),
            "fallback_feasible": feasible,
            "fallback_feasibility_rate": _rate(feasible, n),
            "surveyable": sum(1 for r in ok if r.surveyable),
            "surveyable_rate": _rate(sum(1 for r in ok if r.surveyable), n),
            "attribute_presence": {
                name: _rate(sum(1 for r in ok if getattr(r, name)), n)
                for name in (
                    "has_tube_current",
                    "has_timing",
                    "has_collimation",
                    "has_pitch",
                    "has_slice_thickness",
                    "has_slice_location",
                )
            },
            "model_resolution": {
                state: sum(1 for r in ok if r.model_resolution == state)
                for state in ("resolved", "ambiguous", "unknown")
            },
            "collections": sorted({r.collection for r in ok}),
            "licences": sorted({r.licence for r in ok if r.licence}),
            # A series the archive index gives no licence for cannot be cited in a
            # data-availability statement without checking its collection by hand.
            "n_series_without_licence": sum(1 for r in ok if not r.licence),
        }

    vendors = sorted({r.vendor for r in screens})
    unmatched: dict[str, int] = {}
    for r in screens:
        if not r.error and r.model_resolution == "unknown" and r.model_name:
            unmatched[r.model_name] = unmatched.get(r.model_name, 0) + 1

    return {
        "overall": block(screens),
        "by_vendor": {v: block([r for r in screens if r.vendor == v]) for v in vendors},
        "unmatched_models": dict(sorted(unmatched.items(), key=lambda kv: -kv[1])),
        "reconstructability_note": (
            "'fallback_feasibility_rate' is marginal (over all screened series, including "
            "those that already carry a recorded CTDIvol). "
            "'conditional_reconstructability_rate' is over the series that lack one, which "
            "is the population the reconstruction exists to serve. Report both: the "
            "marginal rate describes the archive, the conditional rate describes the method."
        ),
    }


def _describe(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "stdev": statistics.stdev(ordered) if len(ordered) > 1 else None,
        "min": ordered[0],
        "max": ordered[-1],
        "p25": ordered[max(0, round(0.25 * (len(ordered) - 1)))],
        "p75": ordered[max(0, round(0.75 * (len(ordered) - 1)))],
        "mean_absolute": statistics.fmean(abs(v) for v in ordered),
        "within_20_percent": sum(1 for v in ordered if abs(v) <= 20.0) / len(ordered),
        "within_50_percent": sum(1 for v in ordered if abs(v) <= 50.0) / len(ordered),
    }


def summarise_fallback(cases: Sequence[FallbackCase]) -> dict[str, Any]:
    """Signed relative-error distribution of the reconstruction, overall and per vendor."""
    vendors = sorted({c.vendor for c in cases})
    best = [c.best_case_percent_error for c in cases if c.best_case_percent_error is not None]
    measured = [c for c in cases if c.collimation_measured]
    assumed = [c for c in cases if not c.collimation_measured]
    diagnostic = [c for c in cases if c.recorded_ctdivol_mgy >= DIAGNOSTIC_CTDIVOL_MGY]
    complete = [c for c in diagnostic if c.collimation_measured]
    return {
        # The headline: what the reconstruction achieves when the series is a diagnostic
        # acquisition AND its header actually records the collimation. Everything else
        # measures the archive's metadata quality rather than the dose model.
        "diagnostic_and_collimation_measured": {
            **_describe([c.percent_error for c in complete]),
            "n_excluded": len(cases) - len(complete),
            "by_vendor": {
                v: _describe([c.percent_error for c in complete if c.vendor == v])
                for v in vendors
            },
            "note": (
                "Diagnostic acquisitions (recorded CTDIvol >= "
                f"{DIAGNOSTIC_CTDIVOL_MGY} mGy) whose header records total collimation. "
                "This is the stratum that isolates the dose model from the archive's "
                "metadata gaps, and the one to quote as the method's accuracy. Check "
                "'vendor_by_collimation_provenance' first: collimation availability is "
                "itself vendor-dependent, so this stratum is not balanced across vendors."
            ),
        },
        "vendor_by_collimation_provenance": {
            v: {
                "collimation_measured": sum(
                    1 for c in measured if c.vendor == v
                ),
                "collimation_reconstructed": sum(1 for c in assumed if c.vendor == v),
            }
            for v in vendors
        },
        "overall": _describe([c.percent_error for c in cases]),
        "diagnostic_acquisitions_only": {
            **_describe([c.percent_error for c in diagnostic]),
            "threshold_mgy": DIAGNOSTIC_CTDIVOL_MGY,
            "n_excluded": len(cases) - len(diagnostic),
            "note": (
                "Restricted to series whose recorded CTDIvol is at least "
                f"{DIAGNOSTIC_CTDIVOL_MGY} mGy. Below that the recorded value belongs to "
                "a localiser or another non-diagnostic acquisition, and comparing it "
                "against a reconstruction of the diagnostic series compares two "
                "different irradiations. Those cases are kept in 'overall' and in the "
                "per-case rows; this stratum is the one to quote."
            ),
        },
        "by_vendor": {
            v: _describe([c.percent_error for c in cases if c.vendor == v]) for v in vendors
        },
        "by_collimation_provenance": {
            "measured": _describe([c.percent_error for c in measured]),
            "reconstructed": _describe([c.percent_error for c in assumed]),
            "note": (
                "The over-beaming factor grows roughly as 1/collimation once the "
                "collimation falls below the scanner's reference beam width, so a "
                "series whose header omits total collimation -- leaving it to be "
                "rebuilt from table feed, or in the worst case from slice thickness -- "
                "has a much wider error than one that records it. The two strata are "
                "reported separately because pooling them would misattribute a "
                "missing-metadata problem to the dose model."
            ),
        },
        "by_vendor_collimation_measured": {
            v: _describe([c.percent_error for c in measured if c.vendor == v]) for v in vendors
        },
        "best_case_if_model_variant_known": _describe(best),
        "n_model_ambiguous": sum(1 for c in cases if c.model_ambiguous),
        "n_collimation_measured": len(measured),
        "n_collimation_reconstructed": len(assumed),
        "independence": {
            "n_cases": len(cases),
            "n_distinct_recorded_estimated_pairs": len(
                {(c.recorded_ctdivol_mgy, c.estimated_ctdivol_mgy) for c in cases}
            ),
            "note": (
                "Public archives contain re-releases of the same acquisition under "
                "different collection names (curated/synthetic variants, phantom "
                "repeats). Identical (recorded, estimated) pairs indicate such "
                "duplicates, so the cases are not fully independent and the effective "
                "sample size is the distinct count, not n."
            ),
        },
        "note": (
            "percent_error is 100*(estimated - recorded)/recorded for CTDIvol. A positive "
            "value means the physics reconstruction overestimates the scanner's own "
            "recorded figure. Errors are reported as a distribution, not a single "
            "correction factor. Where the DICOM model name does not identify the scanner "
            "variant (bowtie / scan field of view, or acquisition mode), the median "
            "across candidate coefficient rows is used; "
            "'best_case_if_model_variant_known' is the same set of cases scored with the "
            "closest candidate, i.e. an optimistic bound on what the method could do if "
            "the variant were recorded."
        ),
    }


# --- orchestration -------------------------------------------------------------------------


@dataclass
class SurveyResult:
    screens: list[SeriesScreen] = field(default_factory=list)
    fallback_cases: list[FallbackCase] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameters": self.parameters,
            "data_use_note": DATA_USE_NOTE,
            "tag_absence": summarise_screens(self.screens),
            "fallback_error": summarise_fallback(self.fallback_cases),
            "fallback_exclusions": self.excluded,
            "series": [s.to_dict() for s in self.screens],
            "fallback_cases": [c.to_dict() for c in self.fallback_cases],
        }


def run_survey(
    *,
    vendors: Iterable[str] = tuple(VENDOR_QUERIES),
    screen_per_vendor: int = 60,
    fallback_per_vendor: int = 8,
    max_images_for_download: int = 250,
    seed: int = 20260726,
    client: TciaClient | None = None,
    work_dir: Path | str | None = None,
    progress: bool = True,
) -> SurveyResult:
    """Screen public CT series by manufacturer, then quantify the fallback where possible.

    Args:
        screen_per_vendor: series to screen per vendor for part (a). Sampling is
            stratified across the vendor's collections so one large collection cannot
            dominate the rate.
        fallback_per_vendor: series to download in full for part (b). Only series that
            carry a recorded CTDIvol are eligible.
        max_images_for_download: skip very large series in part (b) to bound the
            download; recorded and reconstructed CTDIvol do not depend on series size,
            so this does not bias the error.
        seed: sampling seed, recorded in the results for reproducibility.
    """
    client = client or TciaClient(cache_dir=Path(work_dir) / "cache" if work_dir else None)
    rng = random.Random(seed)
    work = Path(work_dir) if work_dir else None
    result = SurveyResult(
        parameters={
            "vendors": list(vendors),
            "screen_per_vendor": screen_per_vendor,
            "fallback_per_vendor": fallback_per_vendor,
            "max_images_for_download": max_images_for_download,
            "seed": seed,
            "modality": "CT",
            "api": client.base_url,
            "tcia_utils_used": tcia_utils_available(),
            "sampling": "stratified across each vendor's collections, seeded",
        }
    )
    excluded: dict[str, int] = {}

    for vendor in vendors:
        entries: list[dict] = []
        for query in VENDOR_QUERIES.get(vendor, (vendor,)):
            try:
                entries.extend(series_index(query, client=client))
            except Exception as exc:  # pragma: no cover - network dependent
                if progress:
                    print(f"  {vendor}: index query {query!r} failed: {exc}")
        if not entries:
            continue

        by_collection: dict[str, list[dict]] = {}
        for e in entries:
            by_collection.setdefault(str(e.get("Collection", "")), []).append(e)
        for items in by_collection.values():
            rng.shuffle(items)

        # Round-robin over collections so the sample is not dominated by LIDC-IDRI.
        chosen: list[dict] = []
        collections = sorted(by_collection)
        depth = 0
        while len(chosen) < screen_per_vendor:
            added = False
            for coll in collections:
                if depth < len(by_collection[coll]) and len(chosen) < screen_per_vendor:
                    chosen.append(by_collection[coll][depth])
                    added = True
            if not added:
                break
            depth += 1

        if progress:
            print(
                f"{vendor}: {len(entries)} series in {len(collections)} collections; "
                f"screening {len(chosen)}",
                flush=True,
            )
        vendor_screens = [screen_series(e, vendor, client=client) for e in chosen]
        result.screens.extend(vendor_screens)
        if progress:
            ok = [s for s in vendor_screens if not s.error]
            n_ctdi = sum(1 for s in ok if s.has_ctdivol_tag)
            print(
                f"  screened {len(ok)} readable; CTDIvol present in {n_ctdi} "
                f"({(n_ctdi / len(ok) * 100) if ok else 0:.0f}%)",
                flush=True,
            )

        # (b) fallback error, on the screened series that carry a recorded CTDIvol.
        eligible = [
            s
            for s in vendor_screens
            if s.has_ctdivol_tag
            and not s.error
            and s.model_resolution in ("resolved", "ambiguous")
            and (s.n_images or 0) <= max_images_for_download
        ]
        skipped_model = sum(
            1 for s in vendor_screens if s.has_ctdivol_tag and s.model_resolution == "unknown"
        )
        skipped_size = sum(
            1
            for s in vendor_screens
            if s.has_ctdivol_tag and (s.n_images or 0) > max_images_for_download
        )
        excluded["no_recorded_ctdivol"] = excluded.get("no_recorded_ctdivol", 0) + sum(
            1 for s in vendor_screens if not s.error and not s.has_ctdivol_tag
        )
        excluded["model_not_in_table"] = excluded.get("model_not_in_table", 0) + skipped_model
        excluded["series_too_large"] = excluded.get("series_too_large", 0) + skipped_size

        rng.shuffle(eligible)
        for screen in eligible[:fallback_per_vendor]:
            dest = (work or Path(".")) / "series" / screen.series_uid[-24:]
            try:
                client.download_series(screen.series_uid, dest)
                series = load_series(dest)
                case = evaluate_fallback(screen, series)
            except Exception as exc:  # pragma: no cover - network dependent
                excluded["download_or_parse_failed"] = (
                    excluded.get("download_or_parse_failed", 0) + 1
                )
                if progress:
                    print(f"  {screen.series_uid[-12:]}: {type(exc).__name__}: {exc}", flush=True)
                continue
            if case is None:
                recorded = series.ctdivol_tag.value if series.ctdivol_tag else None
                if recorded and implausible_recorded_ctdivol(float(recorded)):
                    excluded["implausible_recorded_ctdivol"] = (
                        excluded.get("implausible_recorded_ctdivol", 0) + 1
                    )
                    if progress:
                        print(
                            f"  {screen.series_uid[-12:]}: recorded CTDIvol {recorded!r} is "
                            "outside the plausible range; excluded",
                            flush=True,
                        )
                else:
                    excluded["insufficient_parameters"] = (
                        excluded.get("insufficient_parameters", 0) + 1
                    )
                continue
            result.fallback_cases.append(case)
            if progress:
                print(
                    f"  {case.table_model[:32]:32s} recorded {case.recorded_ctdivol_mgy:7.2f} "
                    f"estimated {case.estimated_ctdivol_mgy:7.2f}  {case.percent_error:+7.1f}%",
                    flush=True,
                )

    result.excluded = excluded
    return result
