"""Nothing that must not be published may be tracked in this repository.

The manuscript and the licence terms both make claims about what is *not* here: no
patient imaging, no MATLAB sources or vendor binaries, no GPL sample objects, no
institutional identifiers. Those claims are checked here rather than by remembering to
run a command before pushing, because the first public push is the moment they stop
being reversible.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: The one imaging fixture the repository is allowed to carry: a dummy-patient phantom
#: acquisition used only as a regression fixture for the DICOM reader.
FIXTURE_DIR = "data/DOSE_TARO/"

#: Extensions that must never be tracked. `.m`/`.fig` would be MATLAB source from the
#: read-only reference implementation; `.exe`/`.mexw*` vendor binaries; `.nii*` would be
#: segmentation output derived from imaging.
FORBIDDEN_SUFFIXES = (
    ".ima", ".nii", ".nii.gz", ".m", ".fig", ".exe", ".mat", ".mexw32", ".mexw64", ".dll",
)


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


@pytest.fixture(scope="module")
def tracked() -> list[str]:
    try:
        return tracked_files()
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
        pytest.skip(f"git not available: {exc}")


def test_no_forbidden_file_types_are_tracked(tracked):
    offenders = [
        f for f in tracked if f.lower().endswith(FORBIDDEN_SUFFIXES)
    ]
    assert not offenders, f"these must not be published: {offenders}"


def test_the_only_tracked_dicom_is_the_regression_fixture(tracked):
    dicom = [f for f in tracked if f.lower().endswith(".dcm")]
    stray = [f for f in dicom if not f.startswith(FIXTURE_DIR)]
    assert not stray, (
        f"DICOM outside the fixture is tracked: {stray}. The manuscript states that no "
        "imaging is redistributed."
    )
    assert dicom, "the fixture itself is missing"


def test_the_fixture_carries_no_identifiers():
    """A dummy patient, and every other identifying attribute empty or absent."""
    pydicom = pytest.importorskip("pydicom")
    files = sorted((REPO / "data" / "DOSE_TARO").glob("*.dcm"))
    if not files:
        pytest.skip("fixture not present")

    must_be_empty = (
        "PatientBirthDate", "InstitutionName", "InstitutionAddress", "StationName",
        "AccessionNumber", "ReferringPhysicianName", "PerformingPhysicianName",
        "OperatorsName", "StudyID", "DeviceSerialNumber", "InstitutionalDepartmentName",
    )
    for path in files:
        ds = pydicom.dcmread(str(path), stop_before_pixels=True)
        assert str(ds.PatientName) == "DOSE^TARO", f"{path.name} names a real patient"
        assert str(ds.PatientID) == "CTDOSE-FIXTURE", f"{path.name} has a real patient ID"
        for attr in must_be_empty:
            value = getattr(ds, attr, "")
            assert not str(value).strip(), f"{path.name}: {attr} = {value!r}"

        # Any *other* person name anywhere in the dataset is a leak the loop above
        # would miss, because it only checks the attributes we thought to name.
        for elem in ds:
            if elem.VR == "PN" and str(elem.value).strip():
                assert str(elem.value) == "DOSE^TARO", (
                    f"{path.name}: {elem.name} = {elem.value!r}"
                )


def test_no_gpl_rdsr_samples_are_tracked(tracked):
    """The Layer-1 objects are GPL-3.0 third-party files; this repository is MIT."""
    offenders = [f for f in tracked if "openrem" in f.lower() or "rdsr_sample" in f.lower()]
    assert not offenders, f"GPL sample objects must not be vendored: {offenders}"


def test_the_excluded_institution_appears_nowhere_in_tracked_text(tracked):
    """A named institution must not reach the public repository in any file."""
    forbidden = ("National Cancer Center", "国立がん研究センター")
    text_like = (
        ".py", ".md", ".json", ".toml", ".cff", ".yml", ".yaml", ".txt", ".cfg", ".ini",
    )
    hits: list[str] = []
    for name in tracked:
        if not name.endswith(text_like):
            continue
        path = REPO / name
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - binary misnamed
            continue
        # The consistency tests assert the manuscript is clean; those assertions
        # necessarily contain the string, so the test files themselves are exempt.
        if name.startswith("tests/"):
            continue
        hits += [f"{name}: {word}" for word in forbidden if word in body]
    assert not hits, hits
