# Aero-Space: free disk space

**Aero > Aero-Space** opens a window that finds the old Metashape projects eating your disk. Pick a drive (or **Folder...**) and
click **Scan**: the heaviest projects come first. For each one you see the `.psx` file, the three folders above it (to recognise
the job), **Dense + depth maps** (what lightening frees, in GB), **Whole project** (to judge whether deleting it is worth it),
**Kept** (the files next to the project that deleting leaves alone, see below), the share of the first in the second, the date, and the state (*Open*, *Old lock* left by a crash, or an orphan `.files` folder
whose `.psx` is gone). Click a header to sort. Hover the size to see the detail (dense cloud, depth maps, tie points, DEMs,
orthos). Tick the projects, then:

- **Lighten (gentle)** removes the dense cloud and the depth maps, which can be recomputed. The alignment, markers, DEMs and
  orthos stay, and Metashape opens and saves each project so that it stays consistent.
- **Delete project** removes the `.psx` and its `.files` folder, and nothing else. The files exported next to the project (ortho,
  DEM, clouds, reports, in the same folder or one level down) are not touched: the **Kept** column counts them, hover it for the
  biggest ones. A warning sign there means the project holds a DEM or an ortho built in Metashape and no exported file (tif,
  ecw, las, dxf...) was found next to it: those are inside the `.files` and would go with the project, so export them first if
  you need them.

**Delete project** asks where it goes: **Recycle Bin** (you can restore it; the space is freed when you empty the bin; not available
on a network drive) or **Delete permanently**, which asks you to type `DELETE`. **Lighten** is always final and always asks you to
type `DELETE`: Metashape erases those files itself when it saves the project, so there is no Recycle Bin for it. A project that is open in a Metashape (even on another
computer) is never touched. Every action is written to a log (the **Action log** button).

**Test it on a copy first.** Copy a small project (the `.psx` and its `.files` folder) to a test folder, scan that folder, and
try both modes on the copy before using it on real work.
