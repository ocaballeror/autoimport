"""Tests for PackageFinder disk and in-memory cache behaviour."""

import pickle
from pathlib import Path

from autoimport.finder import PackageFinder


def extract_package_objects(package_name: str) -> dict[str, list[str]]:
    return PackageFinder().extract_package_objects(package_name)


def test_extraction_uses_cache_on_second_call(package: Path):
    """
    Given: extract_package_objects has been called once (cache written).
    When: The same package is extracted again without any file changes.
    Then: The result is identical (cache hit path exercised).
    """
    (package / "cached.py").write_text("def cached_func(): pass")

    first = extract_package_objects("package")
    second = extract_package_objects("package")

    assert first == second
    assert "cached_func" in second


def test_cache_fast_path_skips_assembly_on_identical_mtimes(package: Path):
    """
    Given: The disk cache is warm (first call already wrote fingerprint + compiled result).
    When: _load_package_objects is called again with no file changes.
    Then: The compiled objects are returned directly from the fingerprint fast path
          (no per-module parsing or re-assembly).
    """
    (package / "mod.py").write_text("class Fast:\n pass\n")

    sc = PackageFinder()
    first = sc._load_package_objects("package")

    # Tamper with the per-module data to prove the fast path returns compiled result.
    cache_path = sc.get_cache_path("package")
    cached = pickle.loads(cache_path.read_bytes())
    cached["modules"] = {}  # wipe modules — a rebuild would produce an empty result
    cache_path.write_bytes(pickle.dumps(cached))

    second = sc._load_package_objects("package")

    assert first == second
    assert "Fast" in first[0]


def test_cache_in_memory_avoids_disk_on_second_call(package: Path):
    """
    Given: _extract_package_objects_with_files has been called once on an instance.
    When: It is called again for the same package on the same instance.
    Then: The in-memory _pkg_cache is used (no disk read needed).
    """
    (package / "mod.py").write_text("class Memo:\n pass\n")

    sc = PackageFinder()
    first = sc._extract_package_objects_with_files("package")

    sc.get_cache_path("package").unlink()

    second = sc._extract_package_objects_with_files("package")

    assert first is second


def test_cache_invalidates_when_file_changes(package: Path):
    """
    Given: The disk cache is warm.
    When: A source file is modified (mtime advances).
    Then: The fingerprint no longer matches, the file is re-parsed,
          and the cache is rewritten with the new content.
    """
    mod = package / "mod.py"
    mod.write_text("class OldName:\n pass\n")

    sc = PackageFinder()
    sc._load_package_objects("package")

    mod.write_text("class NewName:\n pass\n")
    mod.touch()

    sc2 = PackageFinder()
    result, _ = sc2._load_package_objects("package")

    assert "NewName" in result
    assert "OldName" not in result


def test_load_package_objects_recovers_from_corrupted_pickle(package: Path):
    """
    Given: The disk cache for a package contains corrupt pickle data.
    When: _load_package_objects is called.
    Then: The bad cache is deleted, the package is re-parsed, and the correct result
          is returned.
    """
    (package / "mod.py").write_text("class Recovered:\n pass\n")

    sc = PackageFinder()
    sc._load_package_objects("package")

    sc.get_cache_path("package").write_bytes(b"not valid pickle data at all")

    sc2 = PackageFinder()
    result, _ = sc2._load_package_objects("package")

    assert "Recovered" in result
