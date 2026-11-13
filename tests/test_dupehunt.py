import os

import pytest

from dupehunt.core import (
    human_bytes,
    link_duplicates,
    plan,
    quarantine,
    render_html,
    render_markdown,
    scan,
)


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_scan_finds_exact_duplicates(tmp_path):
    write(tmp_path / "a.txt", b"same" * 100)
    write(tmp_path / "copy" / "b.txt", b"same" * 100)
    write(tmp_path / "unique.txt", b"different")
    result = plan(scan(tmp_path, images=False), strategy="oldest")
    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert cluster.kind == "exact"
    assert len(cluster.files) == 2
    assert cluster.wasted_bytes == 400
    assert result.duplicate_count == 1
    assert cluster.keep in {entry.path for entry in cluster.files}


def test_min_size_and_globs_are_respected(tmp_path):
    write(tmp_path / "a.txt", b"x" * 10)
    write(tmp_path / "b.txt", b"x" * 10)
    assert scan(tmp_path, min_size=1000, images=False).clusters == ()
    assert scan(tmp_path, include=("a*",), images=False).clusters == ()
    assert len(scan(tmp_path, images=False).clusters) == 1
    assert scan(tmp_path, exclude=("b.txt",), images=False).clusters == ()


def test_keep_strategy_picks_the_expected_file(tmp_path):
    first = write(tmp_path / "old.txt", b"identical")
    second = write(tmp_path / "new.txt", b"identical")
    os.utime(first, (1000, 1000))
    os.utime(second, (2000, 2000))
    assert plan(scan(tmp_path, images=False), "oldest").clusters[0].keep == first
    assert plan(scan(tmp_path, images=False), "newest").clusters[0].keep == second
    assert plan(scan(tmp_path, images=False), "shortest").clusters[0].keep in {first, second}


def test_quarantine_is_dry_run_by_default(tmp_path):
    keep = write(tmp_path / "a.txt", b"payload")
    duplicate = write(tmp_path / "b.txt", b"payload")
    result = plan(scan(tmp_path, images=False), "first")

    preview = quarantine(result, tmp_path / "outbox")
    assert preview["apply"] is False and preview["count"] == 1
    assert duplicate.exists() and keep.exists()

    outcome = quarantine(result, tmp_path / "outbox", apply=True)
    assert outcome["count"] == 1
    assert not duplicate.exists()
    assert (tmp_path / "outbox" / "b.txt").read_bytes() == b"payload"
    assert keep.exists()


def test_link_replaces_duplicates_with_hard_links(tmp_path):
    keep = write(tmp_path / "a.txt", b"linked data")
    duplicate = write(tmp_path / "b.txt", b"linked data")
    with pytest.raises(ValueError):
        link_duplicates(scan(tmp_path, images=False))

    result = plan(scan(tmp_path, images=False), "first")
    outcome = link_duplicates(result, apply=True)
    assert outcome["count"] == 1
    assert duplicate.exists() and keep.exists()
    assert duplicate.stat().st_ino == keep.stat().st_ino
    assert duplicate.read_bytes() == b"linked data"


def test_similar_images_are_clustered(tmp_path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image, ImageOps

    base = Image.linear_gradient("L")
    base.save(tmp_path / "one.png")
    base.point(lambda value: min(255, value + 4)).save(tmp_path / "two.png")
    ImageOps.flip(base).save(tmp_path / "three.png")

    result = scan(tmp_path, images=True, threshold=4)
    similar = [cluster for cluster in result.clusters if cluster.kind == "similar"]
    assert similar, "expected the two gradients to be grouped"
    paths = {str(entry.path.name) for entry in similar[0].files}
    assert {"one.png", "two.png"} <= paths


def test_reports_describe_the_result(tmp_path):
    write(tmp_path / "a.txt", b"abc")
    write(tmp_path / "b.txt", b"abc")
    result = plan(scan(tmp_path, images=False), "first")
    markdown = render_markdown(result)
    assert "dupehunt report" in markdown and "b.txt" in markdown
    assert "keep" in markdown and "copy" in markdown
    assert "<table>" in render_html(result)
    assert result.to_dict()["duplicate_count"] == 1


def test_human_bytes_reads_naturally():
    assert human_bytes(999) == "999 B"
    assert human_bytes(2048) == "2.0 KB"
    assert human_bytes(5 * 1024**2) == "5.0 MB"
