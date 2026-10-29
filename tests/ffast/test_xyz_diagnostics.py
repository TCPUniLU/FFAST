"""Actionable (ext)xyz parse diagnostics.

ASE's reader fails a malformed multi-frame xyz with a bare
``invalid literal for int() with base 10: 'Properties=...'`` — no file, no line,
no cause. ``diagnose_xyz_lines`` walks the frames and pinpoints the first
structural defect (the common one being a frame boundary whose newline was lost
during chunk concatenation, fusing the next frame's atom-count onto the previous
atom line). Pure over a line list — no ASE, no file needed.
"""
from ffast.io.xyz import diagnose_xyz_lines

VALID = [
    "2", "Properties=species:S:1:pos:R:3 energy=-1.0",
    "H 0 0 0", "H 0 0 0.74",
    "2", "Properties=species:S:1:pos:R:3 energy=-1.1",
    "H 0 0 0", "H 0 0 0.75",
]


def test_valid_xyz_has_no_defect():
    assert diagnose_xyz_lines(VALID) is None


def test_trailing_blank_lines_are_tolerated():
    assert diagnose_xyz_lines(VALID + ["", ""]) is None


def test_fused_frame_boundary_is_pinpointed():
    # Frame 1's last atom line fused with frame 2's count "2" (missing newline),
    # so frame 2's Properties line lands where an atom-count is expected.
    fused = [
        "2", "Properties=species:S:1:pos:R:3 energy=-1.0",
        "H 0 0 0", "H 0 0 0.742",                 # was "H 0 0 0.74" + "2"
        "Properties=species:S:1:pos:R:3 energy=-1.1",
        "H 0 0 0", "H 0 0 0.75",
    ]
    msg = diagnose_xyz_lines(fused)
    assert msg is not None
    assert "line 5" in msg                        # the Properties line's 1-based index
    assert "newline" in msg.lower()               # the actionable hint
    assert "1 frame" in msg or "frame 1" in msg.lower()


def test_truncated_final_frame_is_reported():
    truncated = ["3", "comment", "H 0 0 0", "H 0 0 1"]  # declares 3, only 2 atoms
    msg = diagnose_xyz_lines(truncated)
    assert msg is not None
    assert "declares 3" in msg
