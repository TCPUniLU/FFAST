"""Cut the first whole frames of an extxyz file into a smaller file (ADR 0109 §3)."""

import shutil
from pathlib import Path

from ffast.io.frame_index import scan


def slice_frames(source, out, target_bytes):
    """Copy whole frames from the start of ``source`` until ``target_bytes`` is reached.

    A frame is a count line, a comment line and that many atom lines. The cut falls at
    the end of the frame that reaches the size. Returns ``(frames, bytes)``; the result
    is read back with FFAST's own frame scan and must give the same frame count.
    """
    source, out = Path(source), Path(out)
    if out.exists():
        raise FileExistsError(f"{out} already exists")
    free = shutil.disk_usage(out.parent).free
    if free < 1.2 * target_bytes:
        raise RuntimeError(f"only {free / 1e9:.1f} GB free for a slice of {target_bytes / 1e9:.1f} GB")
    frames = written = 0
    temp = out.with_name(out.name + ".tmp")
    try:
        with open(source, "rb") as f, open(temp, "wb") as dest:
            while written < target_bytes:
                head = f.readline()
                if not head:
                    break
                atoms = int(head.strip())
                lines = [head, f.readline()] + [f.readline() for _ in range(atoms)]
                if not lines[-1] and atoms:
                    raise ValueError(f"{source.name} ends inside frame {frames}")
                block = b"".join(lines)
                dest.write(block)
                written += len(block)
                frames += 1
        index = scan(str(temp))
        if len(index.starts) - 1 != frames or int(index.starts[-1]) != written:
            raise RuntimeError(f"the slice has {len(index.starts) - 1} frames by FFAST's scan, "
                               f"{frames} written")
        temp.replace(out)
    finally:
        temp.unlink(missing_ok=True)
    return frames, written
