"""Aero-Exports: the export queue and the white background of the orthomosaic (without Metashape or Qt)."""
import datetime
import os

from aerotools.tools.aeroexports import exportqueue, plan

DAY = datetime.date(2030, 7, 4)


def test_every_ortho_format_but_jpeg_can_leave_the_white_background_out():
    for key in ("full", "quick", "jp2", "ecw"):
        spec = plan.spec_for(key, "ortho", clip_white=True)
        assert spec["alpha"] is True and spec["white"] is False, key
    jpeg = plan.spec_for("jpeg", "ortho", clip_white=True)
    assert jpeg["white"] is True and jpeg["alpha"] is False                      # a JPEG cannot be transparent
    assert not plan.clip_white_possible("jpeg") and plan.clip_white_possible(plan.CAD)


def test_the_white_background_is_kept_unless_asked_so_the_older_callers_are_unchanged():
    assert plan.spec_for("quick", "ortho") == plan.preset("quick")["ortho"]
    assert plan.spec_for("jp2", "ortho")["white"] is True


def test_the_dem_and_the_drawing_images_ignore_the_switch():
    assert plan.spec_for("full", "dem", clip_white=True) == plan.spec_for("full", "dem")
    cad = plan.spec_for(plan.CAD, "ortho", cad={"images": "ecw"}, clip_white=True)
    assert cad["white"] is True and cad["ext"] == "ecw"                          # cropped by a polygon in the drawing instead


def test_build_jobs_carries_the_switch_into_the_ortho_spec(tmp_path):
    jobs = plan.build_jobs("SITE", DAY, {"ortho": 0.05, "dem": 0.2}, "quick", str(tmp_path), clip_white=True)
    ortho = [job for job in jobs if job.kind == "ortho"][0]
    dem = [job for job in jobs if job.kind == "dem"][0]
    assert ortho.spec["alpha"] and not ortho.spec["white"] and dem.spec == plan.spec_for("quick", "dem")


def test_reserved_names_count_as_taken_when_a_free_version_is_looked_for(tmp_path):
    folder = str(tmp_path)
    first = plan.build_jobs("SITE", DAY, {"ortho": 0.05, "dem": 0.2}, "full", folder)
    taken = plan.planned_names(first, "full", folder)
    second = plan.build_jobs("SITE", DAY, {"ortho": 0.05, "dem": 0.2}, "quick", folder, version=None, reserved=taken)
    assert {job.version for job in first} == {1} and {job.version for job in second} == {2}
    assert not {os.path.basename(j.path) for j in first} & {os.path.basename(j.path) for j in second}


def test_reserved_names_and_files_on_disk_both_count(tmp_path):
    folder = str(tmp_path)
    open(os.path.join(folder, "SITE_300704_5cm_ortho.tif"), "w").close()
    first = plan.build_jobs("SITE", DAY, {"ortho": 0.05}, "full", folder, version=None)
    assert first[0].version == 2
    second = plan.build_jobs("SITE", DAY, {"ortho": 0.05}, "full", folder, version=None,
                             reserved=plan.planned_names(first, "full", folder))
    assert second[0].version == 3


def test_reserved_names_work_for_a_drawing_and_for_tiles(tmp_path):
    folder = str(tmp_path)
    cad = plan.build_jobs("SITE", DAY, {"ortho": 0.05, "dem": 0.2}, plan.CAD, folder)
    names = plan.planned_names(cad, plan.CAD, folder)
    assert "TOPO.dxf" in names and any(n.startswith("00_Assets/") for n in names)
    again = plan.build_jobs("SITE", DAY, {"ortho": 0.05, "dem": 0.2}, plan.CAD, folder, version=None, reserved=names)
    assert again[0].version == 2 and plan.cad_dxf_name(2) == "TOPO_v2.dxf"
    tiled = plan.build_jobs("SITE", DAY, {"ortho": 0.05}, "full", folder, version=None, tiled=True, reserved=["SITE_300704_5cm_ortho.tif"])
    assert tiled[0].version == 2


def test_item_counts_its_files_and_the_queue_sums_them():
    one = exportqueue.Item("full", {}, [], [("a.tif", "Orthomosaic"), ("b.tif", "DEM heights")])
    two = exportqueue.Item("jpeg", {}, [], [("a.jpg", "Orthomosaic"), ("a.jgw", "World file"), ("a.prj", "Projection")])
    assert (one.files, two.files, exportqueue.total_files([one, two]), one.failed) == (2, 3, 5, "")
    summary = exportqueue.Summary()
    assert summary.done == [] and summary.failed == []
    assert exportqueue.MAX_ITEMS >= 4
