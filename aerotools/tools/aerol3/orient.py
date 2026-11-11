"""Aero-L3: camera orientation of the Zenmuse L3, from the DJI angles in the XMP of the photos. Pure Python 3.9.

Conventions (measured on aligned cameras of test data, see docs/AERO_L3.md):

* DJI angles (`CameraYaw/Pitch/Roll`, `GimbalYaw/Pitch/Roll`): degrees, local north-east-down frame, yaw clockwise from north, rotations
  applied in the order yaw (Z), pitch (Y), roll (X), on a body frame that points forward, right, down. For a camera the forward axis is
  the optical axis, the right axis is the image right and the down axis is the image bottom.
* Our rotation matrices map the Metashape camera frame (x = image right, y = image bottom, z = optical axis) to the local
  east-north-up frame, as nested tuples `m[row][column]`. The matrix of a camera that looks at the nadir with the top of its
  picture to the north is `((1,0,0),(0,-1,0),(0,0,-1))`.
* The two lenses of the L3 are tilted by 22.5 degrees on each side of the gimbal frame, around the image-bottom axis: left = rotation by
  -22.5 degrees, right = +22.5 degrees (the right lens is the left one rotated by exactly +45 degrees, `RIG_ANGLE`).
"""

import math

LENS_TILT = 22.5
RIG_ANGLE = 45.0
LEFT, RIGHT = "L", "R"

IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def rot_x(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def rot_y(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def rot_z(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def mul(a, b):
    """Matrix product a x b (3 x 3)."""
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def transpose(a):
    return tuple(tuple(a[j][i] for j in range(3)) for i in range(3))


def apply(a, v):
    """Matrix x vector."""
    return tuple(sum(a[i][k] * v[k] for k in range(3)) for i in range(3))


def column(a, j):
    return (a[0][j], a[1][j], a[2][j])


def dot(u, v):
    return sum(x * y for x, y in zip(u, v))


def as_matrix(rows):
    """A 3 x 3 nested tuple of floats from any 3 x 3 nested sequence."""
    return tuple(tuple(float(rows[i][j]) for j in range(3)) for i in range(3))


def angle_between(r1, r2):
    """Rotation angle in degrees that takes `r1` to `r2` (0 to 180)."""
    m = mul(transpose(r1), r2)
    cos = (m[0][0] + m[1][1] + m[2][2] - 1.0) / 2.0
    sin = 0.5 * math.sqrt((m[2][1] - m[1][2]) ** 2 + (m[0][2] - m[2][0]) ** 2 + (m[1][0] - m[0][1]) ** 2)      # exact for tiny angles, where acos is not
    return math.degrees(math.atan2(sin, cos))


_NED_TO_ENU = ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0))


def dji_matrix(yaw, pitch, roll):
    """Metashape camera frame -> east-north-up, for a camera whose forward-right-down body has these DJI angles (degrees)."""
    body = mul(mul(rot_z(yaw), rot_y(pitch)), rot_x(roll))           # body (forward, right, down) -> north-east-down
    cam = tuple((row[1], row[2], row[0]) for row in body)             # reorder the columns: right, down, forward = Metashape x, y, z
    return mul(_NED_TO_ENU, cam)


def lens_offset(lens):
    """Rotation of a lens relative to the gimbal frame, in the Metashape camera frame."""
    return rot_y(-LENS_TILT if lens == LEFT else LENS_TILT)


def rig_rotation():
    """Rotation of the right lens relative to the left one (the slave sensor relative to the master)."""
    return rot_y(RIG_ANGLE)


def nominal_from_camera_angles(angles):
    """Orientation from `CameraYaw/Pitch/Roll`, or None if one of the three is missing. `angles` has keys 'camera_yaw' ... as floats or None."""
    if any(angles.get("camera_" + k) is None for k in ("yaw", "pitch", "roll")):
        return None
    return dji_matrix(angles["camera_yaw"], angles["camera_pitch"], angles["camera_roll"])


def nominal_from_gimbal_angles(angles, lens):
    """Orientation from `GimbalYaw/Pitch/Roll` plus the 22.5 degree tilt of the lens, or None if the angles are missing."""
    if any(angles.get("gimbal_" + k) is None for k in ("yaw", "pitch", "roll")) or lens not in (LEFT, RIGHT):
        return None
    return mul(dji_matrix(angles["gimbal_yaw"], angles["gimbal_pitch"], angles["gimbal_roll"]), lens_offset(lens))


def nominal_orientation(angles, lens):
    """(matrix or None, source) with source 'camera', 'gimbal' or ''. The camera angles are preferred; the gimbal angles are the fallback
    (they agree to 0.01 degree on all the photos of a large test project, so each is also a cross-check of the other)."""
    matrix = nominal_from_camera_angles(angles)
    if matrix is not None:
        return matrix, "camera"
    matrix = nominal_from_gimbal_angles(angles, lens)
    if matrix is not None:
        return matrix, "gimbal"
    return None, ""


def optical_axis(r):
    """Unit vector (east, north, up) the camera looks along."""
    return column(r, 2)


def image_up(r):
    """Unit vector (east, north, up) of the top of the picture."""
    c = column(r, 1)
    return (-c[0], -c[1], -c[2])


def image_right(r):
    return column(r, 0)


def tilt_from_nadir(r):
    """Angle in degrees between the optical axis and the straight down direction (0 = looking at the nadir)."""
    return math.degrees(math.acos(max(-1.0, min(1.0, -optical_axis(r)[2]))))


def horizontal_azimuth(v):
    """Compass bearing in degrees of the horizontal part of an (east, north, up) vector, or None if it is vertical."""
    if math.hypot(v[0], v[1]) < 1e-6:
        return None
    return (math.degrees(math.atan2(v[0], v[1])) + 360.0) % 360.0


def up_azimuth(r):
    """Compass bearing of the top of the picture, taken from the optical axis when the top of the picture points to the sky."""
    az = horizontal_azimuth(image_up(r))
    return az if az is not None else horizontal_azimuth(optical_axis(r))


def side_of_travel(r, heading):
    """+1 when the optical axis leans to the right of a travel `heading` (compass degrees), -1 to the left, 0 when it is vertical."""
    axis = optical_axis(r)
    h = math.radians(heading)
    right = (math.cos(h), -math.sin(h))
    lean = axis[0] * right[0] + axis[1] * right[1]
    if abs(lean) < 1e-6:
        return 0
    return 1 if lean > 0 else -1
