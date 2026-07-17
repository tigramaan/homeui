import pathlib
import subprocess


def source_root():
    path = pathlib.Path(__file__).resolve()
    pybuild_dir = next((parent for parent in path.parents if parent.name == ".pybuild"), None)
    return pybuild_dir.parent if pybuild_dir else path.parents[2]


def test_debian_source_and_binary_versions_match_extension_contract():
    """The leading changelog stanza must version both lock-stepped binary packages."""
    version = subprocess.run(
        ["dpkg-parsechangelog", "-SVersion", "-l", str(source_root() / "debian/changelog")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    control = (source_root() / "debian/control").read_text(encoding="utf-8")

    assert version == "2.238.5"
    assert "Package: wb-mqtt-homeui" in control
    assert "Package: wb-homeui-backend" in control
    assert "wb-homeui-backend (= ${binary:Version})" in control
