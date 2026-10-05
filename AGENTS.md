# AGENTS.md — Developer and AI Agent Guidelines for `iso2nsp`

This repository provides a standalone converter and graphical utility for translating CNC ISO G-code (produced by CAM systems such as Lantek) into CypNest / FSCUT nesting project XML files (`.nsp`).

---

## 1. Project Architecture

The codebase consists of two primary modules and a standalone executable:

- **`iso2nsp.py`**: The core headless engine and CLI utility.
  - Built strictly using Python's standard library (`math`, `re`, `uuid`, `xml.etree.ElementTree`, `xml.dom.minidom`, `argparse`).
  - Contains all geometry classes, G-code parsing, topology hierarchy algorithms, and CypNest `.nsp` XML serialization.
- **`iso2nsp_gui.py`**: Interactive desktop GUI built with `tkinter`.
  - Provides a 2D interactive canvas (zoom, pan, grid, hover coordinates).
  - Sheet dimensions and material editor.
  - Transformations panel (swap axes, rotate, flip, margin align, center).
  - Interactive parts table with visual highlighting on the canvas and renaming capabilities.
- **`iso2nsp.exe`**: Precompiled standalone Windows executable bundled with PyInstaller. Requires no Python runtime and no administrative privileges.

---

## 2. Mathematical Conventions & Geometry Engine

### 2.1. Coordinate Transformations (Lantek CAM Dialect)
On typical plasma/laser cutting tables, the long rail is axis $X$ ($3000$ mm) and the bridge is axis $Y$ ($1500$ mm). In Lantek, sheets are displayed vertically ($1500 \times 3000$), with the machine origin ($X=0$) corresponding to the top edge of the sheet ($Y=3000$).
The canonical mapping for vertical sheets is:
$$X_{\text{sheet}} = Y_{\text{iso}}$$
$$Y_{\text{sheet}} = H_{\text{sheet}} - X_{\text{iso}}$$
*(Swapping axes and reflecting across $Y$).*

### 2.2. Arc Bulge & Arc Direction Inversion
CypNest polylines encode curved segments using a bulge factor:
$$\text{bulge} = \tan\left(\frac{\theta}{4}\right)$$
- Positive sign for counter-clockwise (`G03`);
- Negative sign for clockwise (`G02`);
- Linear segments omit the `bul` attribute.

**Critical Rule on Reflections:** Any reflection (e.g. `flip_x`, `flip_y`, or `swap_xy`) has a negative determinant and inverts the winding order of the plane. When transforming arcs, the algorithm must toggle the direction (`G02` $\leftrightarrow$ `G03`) and flip the bulge sign.

### 2.3. Circle Handling ($360^\circ$ Closed Contours)
Full circles where start point equals end point must be decomposed into two $180^\circ$ semi-circles:
- Node 1: $(x_{\text{start}}, y_{\text{start}})$ with `bul="1"` (for CCW) or `"-1"` (for CW);
- Node 2: $(2 \cdot cx - x_{\text{start}}, 2 \cdot cy - y_{\text{start}})$ with `bul="1"` or `"-1"`;
- Node 3: Closing node at $(x_{\text{start}}, y_{\text{start}})$;
- Analytical node: `<originShape><CircleNode><CommonProperty/><infos x="..." y="..." r="..."/></CircleNode></originShape>`.

### 2.4. Topology Hierarchy (Parts vs. Holes)
Contour classification uses the Ray Casting algorithm (`point_in_polygon`):
- Closed contours inside another closed contour are marked as holes (`layer="I"`).
- Outermost closed contours define the outer perimeter of the part.
- Open contours (e.g. guillotine remnant cutoff lines) are added as root `<PolylineNode>` elements under `<PlateNode><shapes>`.

---

## 3. Output Structure (`.nsp`)

The XML structure requires a two-level hierarchy:
1. `<CutPieceNode>`: Master library templates in normalized local coordinates $(0, 0)$ with a unique `Guid`.
2. `<PlateNode>`:
   - `<shapes><PartNode>`: Instances placed in absolute sheet coordinates, referencing `partGuid`.
   - `<PlateProfilesInfo><PlateLocationInfo>`: Insertion coordinates and rotation angle (`PartInsertAngles`).
   - `<PlateBoardPnts>`: Sheet perimeter boundary.

---

## 4. Building the Standalone Executable

To compile `iso2nsp.exe`:
```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name iso2nsp iso2nsp_gui.py
```
This produces a self-contained single-file executable that does not require Python or administrative permissions.

---

## 5. Guidelines for Future Modifications

- Maintain strict independence from heavy external dependencies for `iso2nsp.py` (standard library only).
- Keep `iso2nsp.exe` up-to-date whenever modifications are made to `iso2nsp.py` or `iso2nsp_gui.py`.
- Preserve backward compatibility with G-code variants (support modal codes, optional decimal points, negative coordinates).
