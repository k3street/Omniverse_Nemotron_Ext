"""Select the oracle episodes worth training on and renumber them contiguously.

Two things make this more than a copy loop:

* The LeRobot converter pairs ``run_N.hdf5`` with ``episode_{N:06d}_policy.mp4``
  **by index**, so dropping a failed episode without renumbering silently
  misaligns every later episode with someone else's video.
* Success is not always the recorder's ``success`` attribute. A multi-object
  task only sets it when *every* object is placed, so an episode that cleanly
  placed the objects it attempted still reads as a failure. For those, judge
  from telemetry instead: an object that was lifted and ended inside the
  container counts, and an object that moved but did not get there is a failed
  grasp -- which is worse than no demonstration, because it teaches the policy
  to close on nothing.
"""
import argparse
import os
import shutil
from pathlib import Path

# Staging only ever reads. Without this, selecting episodes while the generator
# is still writing later ones fails with "unable to lock file", which is a
# needless serialisation of two jobs that do not conflict.
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

import numpy as np

# h5py is imported lazily: the judging logic below is pure numpy so it stays
# importable (and testable) without the recorder's dependencies installed.

DEMO_KEY = "demo_0"


def attempted_objects(demo) -> tuple[str, ...] | None:
    """The objects the controller tried to move, if the recording says."""
    raw = demo.attrs.get("attempted_objects")
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return tuple(x for x in str(raw).split(",") if x)


def object_tracks(demo) -> dict[str, np.ndarray]:
    """Per-object xyz over the episode, pulled out of the recorder's groups.

    An episode where the controller found nothing worth attempting records no
    object states at all, so this has to come back empty rather than raise --
    a degenerate recording is something to drop, not something to crash on.
    """
    states = demo.get("states")
    rigid = states.get("rigid_object") if states is not None else None
    if rigid is None:
        return {}
    return {
        name: np.asarray(rigid[name]["root_pose"])[:, :3] for name in rigid
    }


def judge_by_attribute(demo) -> tuple[bool, str]:
    success = bool(demo.attrs.get("success", False))
    return success, "recorder success attr"


def judge_by_placement(
    tracks: dict[str, np.ndarray],
    *,
    container: str,
    radius_m: float,
    min_lift_m: float,
    attempted: tuple[str, ...] | None = None,
) -> tuple[bool, str]:
    """Decide an episode from where the objects ended up.

    Lift is the signal, not lateral movement. A grasp that misses leaves its
    target sitting exactly where it was -- measured on the cluttered scene, the
    gripper stopped 2.7 cm short and closed on nothing, and the block never
    moved. Objects the arm merely brushed past on its way through the scene do
    shift a centimetre or two, and counting that as an attempt threw away whole
    episodes whose actual grasp was clean.

    ``attempted`` restricts the verdict to the objects the controller set out
    to move, when the recording says which those were.
    """
    if container not in tracks:
        return False, "no recorded object states"
    target_xy = tracks[container][-1, :2]
    placed, dropped = [], []
    for name, track in tracks.items():
        if name in {container, "table"}:
            continue
        on_purpose = attempted is not None and name in attempted
        if attempted is not None and not on_purpose:
            continue
        lifted = float(track[:, 2].max() - track[0, 2])
        if lifted < min_lift_m:
            # Never left the table. Without a record of intent that is
            # indistinguishable from clutter brushed in passing, so ignore it;
            # but when the controller says it went for this object, not
            # lifting it is a grasp that closed on nothing -- the precise
            # demonstration we must keep out of the training set.
            if on_purpose:
                dropped.append(name)
            continue
        if float(np.linalg.norm(track[-1, :2] - target_xy)) <= radius_m:
            placed.append(name)
        else:
            dropped.append(name)
    if dropped:
        return False, f"failed: {','.join(dropped)}"
    if not placed:
        return False, "nothing placed"
    return True, f"placed: {','.join(placed)}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--judge", choices=("attr", "placement"), default="attr")
    parser.add_argument("--container", default="grey_bin")
    parser.add_argument("--radius", type=float, default=0.13)
    parser.add_argument("--min-lift", type=float, default=0.05)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import h5py  # noqa: F811  (deferred; see module docstring)

    sources = []
    for directory in args.input:
        for hdf5 in sorted(
            directory.glob("run_*.hdf5"), key=lambda p: int(p.stem.split("_")[1])
        ):
            sources.append(hdf5)

    if not args.dry_run:
        args.output.mkdir(parents=True, exist_ok=True)

    kept = 0
    for hdf5 in sources:
        index = int(hdf5.stem.split("_")[1])
        video = hdf5.parent / f"episode_{index:06d}_policy.mp4"
        with h5py.File(hdf5, "r") as handle:
            demo = handle["data"][DEMO_KEY]
            if args.judge == "attr":
                ok, why = judge_by_attribute(demo)
            else:
                ok, why = judge_by_placement(
                    object_tracks(demo),
                    container=args.container,
                    radius_m=args.radius,
                    min_lift_m=args.min_lift,
                    attempted=attempted_objects(demo),
                )
        status = "KEEP" if ok else "drop"
        print(f"[stage] {status} {hdf5.parent.name}/{hdf5.name}: {why}")
        if not ok:
            continue
        if not args.dry_run:
            staged = args.output / f"run_{kept}.hdf5"
            shutil.copy2(hdf5, staged)
            # Record who admitted this episode, without touching the
            # recorder's own `success` attribute. A multi-object task only
            # sets that when every object is placed, so a clean single-object
            # pick-and-place reads as a failure there and would otherwise be
            # refused downstream.
            with h5py.File(staged, "r+") as target:
                target[f"data/{DEMO_KEY}"].attrs["stage_verdict"] = why
            if video.is_file():
                shutil.copy2(video, args.output / f"episode_{kept:06d}_policy.mp4")
            else:
                raise FileNotFoundError(f"missing video for {hdf5}: {video}")
        kept += 1

    print(f"[stage] kept {kept}/{len(sources)} episodes -> {args.output}")


if __name__ == "__main__":
    main()
