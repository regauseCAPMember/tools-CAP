"""ArcGIS Pro Python toolbox for estimating an ELT location.

The tool accepts WGS 84 or NAD83 observations in DD, DDM, DMS, or MGRS.
It supports directional bearings, circumcenter observations, and mixed
solutions. Calculations are performed in an automatically selected local UTM
coordinate system; geographic results are returned in the selected datum.

Designed for ArcGIS Pro 3.x. No third-party Python packages are required.
"""

import math
import os
import re
import traceback
from datetime import datetime, timezone

import arcpy


TOOL_VERSION = "1.0.1"
FORMAT_DD = "Decimal Degrees (DD)"
FORMAT_DDM = "Degrees Decimal Minutes (DDM)"
FORMAT_DMS = "Degrees Minutes Seconds (DMS)"
FORMAT_MGRS = "MGRS"

TYPE_DIRECTIONAL = "Directional"
TYPE_CIRCUMCENTER = "Circumcenter"
REF_TRUE = "True"
REF_MAGNETIC = "Magnetic"

WGS84 = 4326
NAD83 = 4269
EARTH_RADIUS_M = 6378137.0


class Toolbox:
    def __init__(self):
        self.label = "ELT Locator"
        self.alias = "eltlocator"
        self.tools = [LocateELT]


class LocateELT:
    def __init__(self):
        self.label = "Locate Emergency Locator Transmitter"
        self.description = (
            "Estimates an ELT location from directional compass bearings, "
            "circumcenter observations, or a mixture of both."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        datum = arcpy.Parameter(
            displayName="Geographic datum",
            name="geographic_datum",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        datum.filter.type = "ValueList"
        datum.filter.list = ["WGS 84", "NAD83"]
        datum.value = "WGS 84"

        coord_format = arcpy.Parameter(
            displayName="Coordinate entry and result format",
            name="coordinate_format",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        coord_format.filter.type = "ValueList"
        coord_format.filter.list = [FORMAT_DD, FORMAT_DDM, FORMAT_DMS, FORMAT_MGRS]
        coord_format.value = FORMAT_DDM

        observations = arcpy.Parameter(
            displayName="ELT observations",
            name="observations",
            datatype="GPValueTable",
            parameterType="Required",
            direction="Input",
        )
        observations.columns = [
            ["GPString", "Point ID"],
            ["GPString", "Coordinate 1 (Latitude or complete/first MGRS part)"],
            ["GPString", "Coordinate 2 (Longitude or second MGRS part)"],
            ["GPString", "Observation Type"],
            ["GPDouble", "Bearing to ELT (degrees)"],
            ["GPString", "Bearing Reference"],
            ["GPDouble", "Declination (east + / west -)"],
        ]
        observations.filters[3].type = "ValueList"
        observations.filters[3].list = [TYPE_DIRECTIONAL, TYPE_CIRCUMCENTER]
        observations.filters[5].type = "ValueList"
        observations.filters[5].list = [REF_TRUE, REF_MAGNETIC]
        observations.value = [
            ["1", "", "", TYPE_DIRECTIONAL, None, REF_TRUE, 0.0],
            ["2", "", "", TYPE_DIRECTIONAL, None, REF_TRUE, 0.0],
            ["3", "", "", TYPE_CIRCUMCENTER, None, REF_TRUE, 0.0],
            ["4", "", "", TYPE_CIRCUMCENTER, None, REF_TRUE, 0.0],
        ]

        line_length = arcpy.Parameter(
            displayName="Minimum displayed bearing/bisector length (nautical miles)",
            name="display_line_length_nm",
            datatype="GPDouble",
            parameterType="Optional",
            direction="Input",
        )
        line_length.value = 25.0
        line_length.filter.type = "Range"
        line_length.filter.list = [0.1, 1000.0]

        output_gdb = arcpy.Parameter(
            displayName="Output geodatabase",
            name="output_geodatabase",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input",
        )
        output_gdb.filter.list = ["Local Database"]
        output_gdb.value = arcpy.env.scratchGDB

        output_name = arcpy.Parameter(
            displayName="Output name prefix",
            name="output_name",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
        )
        output_name.value = "ELT_Locator"

        add_to_map = arcpy.Parameter(
            displayName="Add and label results in current map",
            name="add_to_map",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
        )
        add_to_map.value = True

        estimated_elt = arcpy.Parameter(
            displayName="Estimated ELT output",
            name="estimated_elt_output",
            datatype="DEFeatureClass",
            parameterType="Derived",
            direction="Output",
        )

        result_text = arcpy.Parameter(
            displayName="Estimated ELT coordinate",
            name="estimated_elt_coordinate",
            datatype="GPString",
            parameterType="Derived",
            direction="Output",
        )

        return [
            datum,
            coord_format,
            observations,
            line_length,
            output_gdb,
            output_name,
            add_to_map,
            estimated_elt,
            result_text,
        ]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        # Keep a safe geodatabase feature-class prefix as the user types.
        if parameters[5].altered and parameters[5].valueAsText:
            workspace = parameters[4].valueAsText or arcpy.env.scratchGDB
            parameters[5].value = arcpy.ValidateTableName(parameters[5].valueAsText, workspace)

    def updateMessages(self, parameters):
        observations = _value_table_rows(parameters[2])
        if not observations:
            parameters[2].setErrorMessage("Enter at least two usable observations.")
            return

        errors, warnings = _validate_rows(
            observations,
            parameters[0].valueAsText or "WGS 84",
            parameters[1].valueAsText or FORMAT_DDM,
            parse_coordinates=False,
        )
        if errors:
            parameters[2].setErrorMessage(" ".join(errors[:4]))
        elif warnings:
            parameters[2].setWarningMessage(" ".join(warnings[:4]))

    def execute(self, parameters, messages):
        datum_name = parameters[0].valueAsText
        coord_format = parameters[1].valueAsText
        raw_rows = _value_table_rows(parameters[2])
        min_line_nm = float(parameters[3].valueAsText or 25.0)
        output_gdb = parameters[4].valueAsText
        prefix = arcpy.ValidateTableName(parameters[5].valueAsText, output_gdb)
        add_to_map = _as_bool(parameters[6].value)

        arcpy.env.overwriteOutput = True
        geographic_sr = arcpy.SpatialReference(WGS84 if datum_name == "WGS 84" else NAD83)

        errors, warnings = _validate_rows(
            raw_rows, datum_name, coord_format, parse_coordinates=True
        )
        for warning in warnings:
            arcpy.AddWarning(warning)
        if errors:
            raise arcpy.ExecuteError("\n".join(errors))

        try:
            observations = _parse_observations(raw_rows, coord_format, geographic_sr)
            calc_sr = _local_utm_spatial_reference(observations, datum_name)
            for obs in observations:
                obs["geo"] = obs["geometry"]
                obs["geometry"] = obs["geometry"].projectAs(calc_sr)
                obs["x"] = obs["geometry"].firstPoint.X
                obs["y"] = obs["geometry"].firstPoint.Y

            constraints, chords = _build_constraints(observations)
            elt_x, elt_y, method, rms_m, max_residual_m = _solve_location(
                observations, constraints
            )
            elt_calc = arcpy.PointGeometry(arcpy.Point(elt_x, elt_y), calc_sr)
            elt_geo = elt_calc.projectAs(geographic_sr)

            output_paths = _create_outputs(
                output_gdb=output_gdb,
                prefix=prefix,
                geographic_sr=geographic_sr,
                calc_sr=calc_sr,
                observations=observations,
                constraints=constraints,
                chords=chords,
                elt_calc=elt_calc,
                elt_geo=elt_geo,
                method=method,
                rms_m=rms_m,
                max_residual_m=max_residual_m,
                min_line_nm=min_line_nm,
                selected_format=coord_format,
                datum_name=datum_name,
            )

            result_coord = _format_coordinate(elt_geo, coord_format)
            parameters[7].value = output_paths["elt"]
            parameters[8].value = result_coord

            arcpy.AddMessage("Estimated ELT: {0}".format(result_coord))
            arcpy.AddMessage("Method: {0}".format(method))
            arcpy.AddMessage("Calculation CRS: {0}".format(calc_sr.name))
            arcpy.AddMessage("RMS constraint residual: {0:.1f} m".format(rms_m))

            if add_to_map:
                _add_outputs_to_current_map(output_paths)
        except arcpy.ExecuteError:
            raise
        except Exception as exc:
            arcpy.AddError("ELT Locator failed: {0}".format(exc))
            arcpy.AddError(traceback.format_exc())
            raise


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def _value_table_rows(parameter):
    """Return GPValueTable rows across ArcGIS Pro representations.

    Depending on the ArcGIS Pro release and whether a default was assigned as
    a Python sequence, a GPValueTable parameter can surface as either an
    arcpy.ValueTable (rowCount/getValue) or a native list of row lists.
    """
    try:
        value = parameter.values
    except (AttributeError, RuntimeError):
        value = None
    if value is None:
        value = parameter.value
    if value is None:
        return []

    if hasattr(value, "rowCount") and hasattr(value, "getValue"):
        raw_rows = [
            [value.getValue(r, c) for c in range(value.columnCount)]
            for r in range(value.rowCount)
        ]
    elif isinstance(value, (list, tuple)):
        raw_rows = value
    else:
        # A GPValueTable should not normally reach this branch, but returning
        # no rows produces a useful validation message instead of a callback
        # traceback while ArcGIS Pro is refreshing the tool dialog.
        return []

    rows = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, (list, tuple)):
            continue
        row = []
        for cell in list(raw_row)[:7]:
            row.append(None if cell is None else str(cell).strip())
        # Protect validation while ArcGIS is in the middle of adding a row.
        row.extend([None] * (7 - len(row)))
        # Ignore untouched starter rows.
        if any(row[c] not in (None, "") for c in (1, 2)):
            rows.append(row)
    return rows


def _validate_rows(rows, datum_name, coord_format, parse_coordinates=False):
    errors = []
    warnings = []
    directional = 0
    circumcenter = 0
    ids = set()
    geographic_sr = arcpy.SpatialReference(WGS84 if datum_name == "WGS 84" else NAD83)

    for index, row in enumerate(rows, start=1):
        point_id = (row[0] or str(index)).strip()
        obs_type = (row[3] or "").strip()
        bearing_text = row[4]
        bearing_ref = (row[5] or REF_TRUE).strip()
        declination_text = row[6]

        if point_id.lower() in ids:
            errors.append("Point ID '{0}' is duplicated.".format(point_id))
        ids.add(point_id.lower())

        if obs_type not in (TYPE_DIRECTIONAL, TYPE_CIRCUMCENTER):
            errors.append("Point {0} has an invalid observation type.".format(point_id))
            continue

        if obs_type == TYPE_DIRECTIONAL:
            directional += 1
            if bearing_text in (None, ""):
                errors.append("Directional point {0} requires a bearing.".format(point_id))
            else:
                try:
                    bearing = float(bearing_text)
                    if not 0.0 <= bearing <= 360.0:
                        errors.append("Point {0} bearing must be between 0 and 360.".format(point_id))
                except ValueError:
                    errors.append("Point {0} bearing is not numeric.".format(point_id))
            if bearing_ref not in (REF_TRUE, REF_MAGNETIC):
                errors.append("Point {0} bearing reference must be True or Magnetic.".format(point_id))
            if bearing_ref == REF_MAGNETIC:
                if declination_text in (None, ""):
                    warnings.append(
                        "Magnetic point {0} has no declination; 0 degrees will be used.".format(point_id)
                    )
                else:
                    try:
                        declination = float(declination_text)
                        if not -90.0 <= declination <= 90.0:
                            errors.append("Point {0} declination is outside -90 to 90.".format(point_id))
                    except ValueError:
                        errors.append("Point {0} declination is not numeric.".format(point_id))
        else:
            circumcenter += 1

        if parse_coordinates:
            try:
                _parse_coordinate(row[1], row[2], coord_format, geographic_sr)
            except Exception as exc:
                errors.append("Point {0}: {1}".format(point_id, exc))

    if directional < 2 and not (circumcenter >= 3) and not (
        circumcenter >= 2 and directional >= 1
    ):
        errors.append(
            "Insufficient geometry: provide 2+ directional points, 3+ "
            "circumcenter points, or 2+ circumcenter points plus 1+ directional point."
        )
    if len(rows) > 25:
        warnings.append("More than 25 observations may create a crowded map display.")
    return errors, warnings


def _parse_observations(rows, coord_format, geographic_sr):
    observations = []
    for index, row in enumerate(rows, start=1):
        point_id = row[0] or str(index)
        geometry, original = _parse_coordinate(row[1], row[2], coord_format, geographic_sr)
        obs_type = row[3]
        bearing_input = None if row[4] in (None, "") else float(row[4]) % 360.0
        bearing_ref = row[5] or REF_TRUE
        declination = 0.0 if row[6] in (None, "") else float(row[6])
        bearing_true = None
        if obs_type == TYPE_DIRECTIONAL:
            bearing_true = bearing_input
            if bearing_ref == REF_MAGNETIC:
                # East declination is positive: True = Magnetic + Declination.
                bearing_true = (bearing_input + declination) % 360.0
        observations.append(
            {
                "id": point_id,
                "type": obs_type,
                "geometry": geometry,
                "original": original,
                "bearing_input": bearing_input,
                "bearing_ref": bearing_ref,
                "declination": declination,
                "bearing_true": bearing_true,
            }
        )
    return observations


def _parse_coordinate(coord1, coord2, coord_format, geographic_sr):
    first = (coord1 or "").strip()
    second = (coord2 or "").strip()
    if not first:
        raise ValueError("Coordinate 1 is blank.")

    if coord_format == FORMAT_MGRS:
        # Ground teams sometimes split an MGRS string across radio/log fields.
        # Concatenation also handles zone/grid/easting in field 1 and northing
        # in field 2. Whitespace is removed before ArcGIS parses the notation.
        mgrs = re.sub(r"\s+", "", first + (" " + second if second else ""))
        try:
            # ArcPy parses MGRS in WGS 84. Project afterward when the user has
            # selected NAD83 as the common working geographic datum.
            geom = arcpy.FromCoordString(mgrs, "MGRS")
        except Exception as exc:
            raise ValueError("Invalid MGRS coordinate '{0}': {1}".format(mgrs, exc))
        if geom is None or geom.isEmpty:
            raise ValueError("Invalid MGRS coordinate '{0}'.".format(mgrs))
        return geom.projectAs(geographic_sr), mgrs

    if not second:
        raise ValueError("Coordinate 2 (longitude) is blank.")
    latitude = _parse_angular_component(first, True, coord_format)
    longitude = _parse_angular_component(second, False, coord_format)
    if not -90.0 <= latitude <= 90.0:
        raise ValueError("Latitude is outside -90 to 90 degrees.")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError("Longitude is outside -180 to 180 degrees.")
    geom = arcpy.PointGeometry(arcpy.Point(longitude, latitude), geographic_sr)
    return geom, "{0} | {1}".format(first, second)


def _parse_angular_component(text, is_latitude, coord_format):
    raw = text.strip().upper().replace("−", "-")
    hemisphere_match = re.search(r"[NSEW]", raw)
    hemisphere = hemisphere_match.group(0) if hemisphere_match else None
    if hemisphere:
        if is_latitude and hemisphere not in ("N", "S"):
            raise ValueError("Latitude hemisphere must be N or S.")
        if not is_latitude and hemisphere not in ("E", "W"):
            raise ValueError("Longitude hemisphere must be E or W.")

    cleaned = re.sub(r"[NSEW]", " ", raw)
    cleaned = re.sub(r"[°º'\"′″:,;]", " ", cleaned)
    pieces = re.findall(r"[-+]?\d+(?:\.\d+)?", cleaned)
    expected = {FORMAT_DD: 1, FORMAT_DDM: 2, FORMAT_DMS: 3}[coord_format]
    if len(pieces) != expected:
        raise ValueError(
            "Expected {0} numeric component(s) for {1}; found {2}.".format(
                expected, coord_format, len(pieces)
            )
        )
    values = [float(piece) for piece in pieces]
    degrees = values[0]
    sign = -1.0 if degrees < 0 else 1.0
    degrees = abs(degrees)
    if expected >= 2:
        if not 0.0 <= values[1] < 60.0:
            raise ValueError("Minutes must be at least 0 and less than 60.")
        degrees += values[1] / 60.0
    if expected == 3:
        if not 0.0 <= values[2] < 60.0:
            raise ValueError("Seconds must be at least 0 and less than 60.")
        degrees += values[2] / 3600.0

    if hemisphere:
        hemi_sign = -1.0 if hemisphere in ("S", "W") else 1.0
        if sign < 0 and hemi_sign > 0:
            raise ValueError("A negative value conflicts with the hemisphere.")
        sign = hemi_sign
    return sign * degrees


def _local_utm_spatial_reference(observations, datum_name):
    longitudes = [obs["geometry"].centroid.X for obs in observations]
    latitudes = [obs["geometry"].centroid.Y for obs in observations]
    lon = sum(longitudes) / len(longitudes)
    lat = sum(latitudes) / len(latitudes)
    zone = max(1, min(60, int(math.floor((lon + 180.0) / 6.0)) + 1))
    if datum_name == "NAD83" and lat >= 0 and zone <= 23:
        wkid = 26900 + zone
    else:
        wkid = (32600 if lat >= 0 else 32700) + zone
    return arcpy.SpatialReference(wkid)


def _line_constraint(point_id, line_type, px, py, dx, dy, source_ids):
    length = math.hypot(dx, dy)
    if length < 1.0e-12:
        raise ValueError("Cannot construct a zero-length constraint.")
    dx /= length
    dy /= length
    # Unit normal and n dot x = c representation.
    nx, ny = dy, -dx
    return {
        "id": point_id,
        "type": line_type,
        "px": px,
        "py": py,
        "dx": dx,
        "dy": dy,
        "nx": nx,
        "ny": ny,
        "c": nx * px + ny * py,
        "sources": source_ids,
    }


def _build_constraints(observations):
    constraints = []
    chords = []
    directionals = [o for o in observations if o["type"] == TYPE_DIRECTIONAL]
    circumcenters = [o for o in observations if o["type"] == TYPE_CIRCUMCENTER]

    for obs in directionals:
        theta = math.radians(obs["bearing_true"])
        constraints.append(
            _line_constraint(
                "D_{0}".format(obs["id"]),
                "Directional",
                obs["x"],
                obs["y"],
                math.sin(theta),
                math.cos(theta),
                [obs["id"]],
            )
        )

    # All point pairs are displayed as chords. For four or more C points,
    # pair constraints are normalized later so C geometry does not overwhelm
    # directional bearings merely because pair count grows quadratically.
    for i in range(len(circumcenters)):
        for j in range(i + 1, len(circumcenters)):
            a, b = circumcenters[i], circumcenters[j]
            dx = b["x"] - a["x"]
            dy = b["y"] - a["y"]
            chord_length = math.hypot(dx, dy)
            if chord_length < 0.01:
                raise ValueError(
                    "Circumcenter points {0} and {1} are duplicates.".format(a["id"], b["id"])
                )
            mx = (a["x"] + b["x"]) / 2.0
            my = (a["y"] + b["y"]) / 2.0
            chord_id = "C_{0}_{1}".format(a["id"], b["id"])
            chords.append(
                {
                    "id": chord_id,
                    "a": a,
                    "b": b,
                    "mx": mx,
                    "my": my,
                    "length": chord_length,
                }
            )
            constraints.append(
                _line_constraint(
                    "PB_{0}_{1}".format(a["id"], b["id"]),
                    "Perpendicular Bisector",
                    mx,
                    my,
                    -dy,
                    dx,
                    [a["id"], b["id"]],
                )
            )
    return constraints, chords


def _solve_location(observations, constraints):
    directionals = [c for c in constraints if c["type"] == "Directional"]
    bisectors = [c for c in constraints if c["type"] == "Perpendicular Bisector"]
    c_points = [o for o in observations if o["type"] == TYPE_CIRCUMCENTER]

    # Pure C solutions are more stable as an algebraic least-squares circle
    # fit than as many pairwise bisectors.
    if len(c_points) >= 3 and not directionals:
        x, y = _least_squares_circle_center(c_points)
        method = "Circumcenter circle fit ({0} points)".format(len(c_points))
    else:
        selected = list(directionals) + list(bisectors)
        if len(selected) < 2:
            raise ValueError("At least two independent constraint lines are required.")
        weights = []
        d_weight = 1.0
        # Keep the total influence of all C pair lines comparable to the
        # number of original C observations, not the number of pairs.
        c_weight = (max(1.0, len(c_points)) / max(1.0, len(bisectors)))
        for c in selected:
            weights.append(d_weight if c["type"] == "Directional" else c_weight)
        x, y = _least_squares_line_intersection(selected, weights)
        if directionals and bisectors:
            method = "Mixed directional/perpendicular-bisector solution"
        else:
            method = "Directional bearing intersection ({0} bearings)".format(len(directionals))

    residuals = [abs(c["nx"] * x + c["ny"] * y - c["c"]) for c in constraints]
    rms = math.sqrt(sum(r * r for r in residuals) / max(1, len(residuals)))
    maximum = max(residuals) if residuals else 0.0
    return x, y, method, rms, maximum


def _least_squares_line_intersection(lines, weights):
    a11 = a12 = a22 = b1 = b2 = 0.0
    for line, weight in zip(lines, weights):
        nx, ny, c = line["nx"], line["ny"], line["c"]
        a11 += weight * nx * nx
        a12 += weight * nx * ny
        a22 += weight * ny * ny
        b1 += weight * nx * c
        b2 += weight * ny * c
    determinant = a11 * a22 - a12 * a12
    scale = max(a11 * a22, 1.0)
    if abs(determinant) < 1.0e-10 * scale:
        raise ValueError(
            "Constraint lines are parallel or nearly parallel; a reliable intersection cannot be calculated."
        )
    x = (b1 * a22 - b2 * a12) / determinant
    y = (a11 * b2 - a12 * b1) / determinant
    return x, y


def _least_squares_circle_center(points):
    # Subtracting a local origin keeps the normal equations well-conditioned.
    ox = sum(p["x"] for p in points) / len(points)
    oy = sum(p["y"] for p in points) / len(points)
    sxx = sxy = syy = sxq = syq = 0.0
    for p in points:
        x = p["x"] - ox
        y = p["y"] - oy
        q = x * x + y * y
        sxx += x * x
        sxy += x * y
        syy += y * y
        sxq += x * q
        syq += y * q
    determinant = sxx * syy - sxy * sxy
    if abs(determinant) < 1.0e-10 * max(sxx * syy, 1.0):
        raise ValueError("Circumcenter observations are collinear or nearly collinear.")
    cx_local = 0.5 * (sxq * syy - syq * sxy) / determinant
    cy_local = 0.5 * (sxx * syq - sxy * sxq) / determinant
    return ox + cx_local, oy + cy_local


def _feature_class(gdb, name, geometry_type, spatial_reference, fields):
    path = os.path.join(gdb, name)
    if arcpy.Exists(path):
        arcpy.management.Delete(path)
    arcpy.management.CreateFeatureclass(gdb, name, geometry_type, spatial_reference=spatial_reference)
    for field_name, field_type, length in fields:
        kwargs = {"field_length": length} if length else {}
        arcpy.management.AddField(path, field_name, field_type, **kwargs)
    return path


def _create_outputs(
    output_gdb,
    prefix,
    geographic_sr,
    calc_sr,
    observations,
    constraints,
    chords,
    elt_calc,
    elt_geo,
    method,
    rms_m,
    max_residual_m,
    min_line_nm,
    selected_format,
    datum_name,
):
    suffixes = {
        "observations": "Observations",
        "elt": "Estimated_ELT",
        "directional": "Directional_Lines",
        "chords": "Chords",
        "midpoints": "Chord_Midpoints",
        "bisectors": "Perpendicular_Bisectors",
        "circle": "Best_Fit_Circle",
    }
    paths = {}
    paths["observations"] = _feature_class(
        output_gdb,
        "{0}_{1}".format(prefix, suffixes["observations"]),
        "POINT",
        geographic_sr,
        [
            ("PointID", "TEXT", 40), ("ObsType", "TEXT", 20),
            ("MapLabel", "TEXT", 20), ("InputCoord", "TEXT", 160),
            ("Latitude", "DOUBLE", None), ("Longitude", "DOUBLE", None),
            ("BearingIn", "DOUBLE", None), ("BearRef", "TEXT", 12),
            ("Declin", "DOUBLE", None), ("BearingTr", "DOUBLE", None),
            ("DistELT_m", "DOUBLE", None), ("DistELT_nm", "DOUBLE", None),
        ],
    )
    paths["elt"] = _feature_class(
        output_gdb,
        "{0}_{1}".format(prefix, suffixes["elt"]),
        "POINT",
        geographic_sr,
        [
            ("MapLabel", "TEXT", 10), ("Method", "TEXT", 120),
            ("Datum", "TEXT", 20), ("CoordFmt", "TEXT", 45),
            ("CoordText", "TEXT", 160), ("Latitude", "DOUBLE", None),
            ("Longitude", "DOUBLE", None), ("DD", "TEXT", 100),
            ("DDM", "TEXT", 100), ("DMS", "TEXT", 100),
            ("MGRS", "TEXT", 100), ("DirCount", "LONG", None),
            ("CircCount", "LONG", None), ("RMS_m", "DOUBLE", None),
            ("MaxRes_m", "DOUBLE", None), ("CreatedUTC", "DATE", None),
            ("ToolVer", "TEXT", 16),
        ],
    )
    line_fields = [
        ("LineID", "TEXT", 60), ("LineType", "TEXT", 40),
        ("SourceIDs", "TEXT", 100), ("MapLabel", "TEXT", 100),
        ("Bearing", "DOUBLE", None), ("DistELT_m", "DOUBLE", None),
        ("DistELT_nm", "DOUBLE", None), ("Residual_m", "DOUBLE", None),
    ]
    paths["directional"] = _feature_class(
        output_gdb, "{0}_{1}".format(prefix, suffixes["directional"]),
        "POLYLINE", geographic_sr, line_fields,
    )
    paths["chords"] = _feature_class(
        output_gdb, "{0}_{1}".format(prefix, suffixes["chords"]),
        "POLYLINE", geographic_sr, line_fields,
    )
    paths["bisectors"] = _feature_class(
        output_gdb, "{0}_{1}".format(prefix, suffixes["bisectors"]),
        "POLYLINE", geographic_sr, line_fields,
    )
    paths["circle"] = _feature_class(
        output_gdb, "{0}_{1}".format(prefix, suffixes["circle"]),
        "POLYLINE", geographic_sr, line_fields,
    )
    paths["midpoints"] = _feature_class(
        output_gdb, "{0}_{1}".format(prefix, suffixes["midpoints"]),
        "POINT", geographic_sr,
        [("PointID", "TEXT", 60), ("MapLabel", "TEXT", 30),
         ("DistELT_m", "DOUBLE", None), ("DistELT_nm", "DOUBLE", None)],
    )

    elt_x, elt_y = elt_calc.firstPoint.X, elt_calc.firstPoint.Y
    with arcpy.da.InsertCursor(
        paths["observations"],
        ["SHAPE@", "PointID", "ObsType", "MapLabel", "InputCoord", "Latitude",
         "Longitude", "BearingIn", "BearRef", "Declin", "BearingTr", "DistELT_m", "DistELT_nm"],
    ) as cursor:
        type_counts = {TYPE_DIRECTIONAL: 0, TYPE_CIRCUMCENTER: 0}
        for obs in observations:
            type_counts[obs["type"]] += 1
            label = ("D" if obs["type"] == TYPE_DIRECTIONAL else "C") + str(type_counts[obs["type"]])
            p = obs["geo"].firstPoint
            distance = math.hypot(obs["x"] - elt_x, obs["y"] - elt_y)
            cursor.insertRow([
                obs["geo"], obs["id"], obs["type"], label, obs["original"], p.Y, p.X,
                obs["bearing_input"], obs["bearing_ref"], obs["declination"],
                obs["bearing_true"], distance, distance / 1852.0,
            ])

    dd = _format_coordinate(elt_geo, FORMAT_DD)
    ddm = _format_coordinate(elt_geo, FORMAT_DDM)
    dms = _format_coordinate(elt_geo, FORMAT_DMS)
    mgrs = _safe_mgrs(elt_geo)
    result_coord = _format_coordinate(elt_geo, selected_format)
    geo_point = elt_geo.firstPoint
    with arcpy.da.InsertCursor(
        paths["elt"],
        ["SHAPE@", "MapLabel", "Method", "Datum", "CoordFmt", "CoordText", "Latitude",
         "Longitude", "DD", "DDM", "DMS", "MGRS", "DirCount", "CircCount", "RMS_m",
         "MaxRes_m", "CreatedUTC", "ToolVer"],
    ) as cursor:
        cursor.insertRow([
            elt_geo, "E", method, datum_name, selected_format, result_coord,
            geo_point.Y, geo_point.X, dd, ddm, dms, mgrs,
            sum(o["type"] == TYPE_DIRECTIONAL for o in observations),
            sum(o["type"] == TYPE_CIRCUMCENTER for o in observations),
            # File geodatabase Date fields do not retain a time-zone offset.
            rms_m, max_residual_m, datetime.now(timezone.utc).replace(tzinfo=None), TOOL_VERSION,
        ])

    min_display_m = min_line_nm * 1852.0
    directionals = [c for c in constraints if c["type"] == "Directional"]
    with arcpy.da.InsertCursor(paths["directional"], _line_cursor_fields()) as cursor:
        for line in directionals:
            along = (elt_x - line["px"]) * line["dx"] + (elt_y - line["py"]) * line["dy"]
            display_length = max(min_display_m, along * 1.15, 100.0)
            x2 = line["px"] + line["dx"] * display_length
            y2 = line["py"] + line["dy"] * display_length
            geom = _polyline(calc_sr, [(line["px"], line["py"]), (x2, y2)])
            distance = math.hypot(elt_x - line["px"], elt_y - line["py"])
            residual = abs(line["nx"] * elt_x + line["ny"] * elt_y - line["c"])
            bearing = (math.degrees(math.atan2(line["dx"], line["dy"])) + 360.0) % 360.0
            cursor.insertRow(_line_row(
                geom.projectAs(geographic_sr), line["id"], line["type"], line["sources"],
                "{0:.2f} NM".format(distance / 1852.0), bearing, distance, residual,
            ))

    with arcpy.da.InsertCursor(paths["chords"], _line_cursor_fields()) as cursor:
        for chord in chords:
            geom = _polyline(calc_sr, [(chord["a"]["x"], chord["a"]["y"]),
                                        (chord["b"]["x"], chord["b"]["y"])])
            cursor.insertRow(_line_row(
                geom.projectAs(geographic_sr), chord["id"], "Chord", [chord["a"]["id"], chord["b"]["id"]],
                "Chord {0:.2f} NM".format(chord["length"] / 1852.0), None,
                chord["length"], None,
            ))

    with arcpy.da.InsertCursor(
        paths["midpoints"], ["SHAPE@", "PointID", "MapLabel", "DistELT_m", "DistELT_nm"]
    ) as cursor:
        for index, chord in enumerate(chords, start=1):
            distance = math.hypot(elt_x - chord["mx"], elt_y - chord["my"])
            cursor.insertRow([
                arcpy.PointGeometry(arcpy.Point(chord["mx"], chord["my"]), calc_sr).projectAs(geographic_sr),
                "M_{0}".format(chord["id"]), "M{0}".format(index), distance, distance / 1852.0,
            ])

    bisectors = [c for c in constraints if c["type"] == "Perpendicular Bisector"]
    with arcpy.da.InsertCursor(paths["bisectors"], _line_cursor_fields()) as cursor:
        for line in bisectors:
            along = (elt_x - line["px"]) * line["dx"] + (elt_y - line["py"]) * line["dy"]
            half = max(min_display_m / 2.0, abs(along) * 1.15, 100.0)
            geom = _polyline(calc_sr, [
                (line["px"] - line["dx"] * half, line["py"] - line["dy"] * half),
                (line["px"] + line["dx"] * half, line["py"] + line["dy"] * half),
            ])
            distance = math.hypot(elt_x - line["px"], elt_y - line["py"])
            residual = abs(line["nx"] * elt_x + line["ny"] * elt_y - line["c"])
            cursor.insertRow(_line_row(
                geom.projectAs(geographic_sr), line["id"], line["type"], line["sources"],
                "{0:.2f} NM to E".format(distance / 1852.0), None, distance, residual,
            ))

    c_observations = [o for o in observations if o["type"] == TYPE_CIRCUMCENTER]
    if len(c_observations) >= 2:
        radius = sum(math.hypot(o["x"] - elt_x, o["y"] - elt_y) for o in c_observations) / len(c_observations)
        circle = _circle_polyline(calc_sr, elt_x, elt_y, radius)
        with arcpy.da.InsertCursor(paths["circle"], _line_cursor_fields()) as cursor:
            cursor.insertRow(_line_row(
                circle.projectAs(geographic_sr), "BestFitCircle", "Best-fit Circle",
                [o["id"] for o in c_observations],
                "Radius {0:.2f} NM".format(radius / 1852.0), None, radius, None,
            ))

    return paths


def _line_cursor_fields():
    return ["SHAPE@", "LineID", "LineType", "SourceIDs", "MapLabel", "Bearing",
            "DistELT_m", "DistELT_nm", "Residual_m"]


def _line_row(geometry, line_id, line_type, source_ids, label, bearing, distance, residual):
    return [geometry, line_id, line_type, ",".join(source_ids), label, bearing,
            distance, None if distance is None else distance / 1852.0, residual]


def _polyline(spatial_reference, coordinates):
    return arcpy.Polyline(
        arcpy.Array([arcpy.Point(x, y) for x, y in coordinates]), spatial_reference
    )


def _circle_polyline(spatial_reference, cx, cy, radius, segments=180):
    coordinates = []
    for index in range(segments + 1):
        angle = 2.0 * math.pi * index / segments
        coordinates.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return _polyline(spatial_reference, coordinates)


def _format_coordinate(geometry, coord_format):
    point = geometry.firstPoint
    lat, lon = point.Y, point.X
    if coord_format == FORMAT_DD:
        return "{0:.6f}, {1:.6f}".format(lat, lon)
    if coord_format == FORMAT_DDM:
        return "{0}, {1}".format(_format_ddm(lat, True), _format_ddm(lon, False))
    if coord_format == FORMAT_DMS:
        return "{0}, {1}".format(_format_dms(lat, True), _format_dms(lon, False))
    return _safe_mgrs(geometry)


def _format_ddm(value, is_latitude):
    hemisphere = ("N" if value >= 0 else "S") if is_latitude else ("E" if value >= 0 else "W")
    absolute = abs(value)
    degrees = int(absolute)
    minutes = (absolute - degrees) * 60.0
    width = 2 if is_latitude else 3
    return "{0:0{1}d} {2:06.3f} {3}".format(degrees, width, minutes, hemisphere)


def _format_dms(value, is_latitude):
    hemisphere = ("N" if value >= 0 else "S") if is_latitude else ("E" if value >= 0 else "W")
    absolute = abs(value)
    degrees = int(absolute)
    minutes_full = (absolute - degrees) * 60.0
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60.0
    width = 2 if is_latitude else 3
    return "{0:0{1}d} {2:02d} {3:05.2f} {4}".format(
        degrees, width, minutes, seconds, hemisphere
    )


def _safe_mgrs(geometry):
    try:
        return geometry.toCoordString("MGRS")
    except Exception:
        return "Unavailable"


def _add_outputs_to_current_map(paths):
    try:
        project = arcpy.mp.ArcGISProject("CURRENT")
        active_map = project.activeMap
        if active_map is None:
            arcpy.AddWarning("No active map is available; outputs were created but not added.")
            return

        # Add in reverse display order so E and observations finish on top.
        ordered = ["circle", "chords", "bisectors", "directional", "midpoints", "observations", "elt"]
        added = {}
        for key in ordered:
            if arcpy.Exists(paths[key]):
                added[key] = active_map.addDataFromPath(paths[key])

        _style_line(added.get("circle"), [60, 120, 255, 100], 1.5, True)
        _style_line(added.get("chords"), [70, 70, 70, 100], 1.2, True)
        _style_line(added.get("bisectors"), [255, 140, 0, 100], 1.5, True)
        _style_line(added.get("directional"), [30, 90, 220, 100], 2.0, True)
        _style_point(added.get("midpoints"), [255, 170, 0, 100], 5, True)
        _style_point(added.get("observations"), [30, 90, 220, 100], 8, True)
        _style_point(added.get("elt"), [220, 30, 30, 100], 13, True)

        if added.get("elt"):
            active_map.defaultCamera.setExtent(added["elt"].getExtent())
            active_map.defaultCamera.scale = max(active_map.defaultCamera.scale, 50000)
        arcpy.AddMessage("Result layers were added to the active map.")
    except Exception as exc:
        arcpy.AddWarning("Outputs were created, but map styling was not completed: {0}".format(exc))


def _enable_labels(layer):
    if layer is None or not layer.supports("SHOWLABELS"):
        return
    layer.showLabels = True
    for label_class in layer.listLabelClasses():
        label_class.expressionEngine = "Arcade"
        label_class.expression = "$feature.MapLabel"


def _style_line(layer, color, width, label):
    if layer is None:
        return
    try:
        sym = layer.symbology
        if hasattr(sym, "renderer") and hasattr(sym.renderer, "symbol"):
            sym.renderer.symbol.color = color
            sym.renderer.symbol.outlineColor = color
            sym.renderer.symbol.size = width
            layer.symbology = sym
    except Exception:
        pass
    if label:
        _enable_labels(layer)


def _style_point(layer, color, size, label):
    if layer is None:
        return
    try:
        sym = layer.symbology
        if hasattr(sym, "renderer") and hasattr(sym.renderer, "symbol"):
            sym.renderer.symbol.color = color
            sym.renderer.symbol.size = size
            layer.symbology = sym
    except Exception:
        pass
    if label:
        _enable_labels(layer)
