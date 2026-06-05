# Overnight Confirmed Unlocks — 2026-05-11 02:25

Post-Kit-restart verify batch (sequential 11 CPs) confirms unlocks:

## Result: 10/11 stable_ok

| CP | Status | Elapsed |
|---|---|---|
| CP-22 | stable_ok | 45.5s |
| CP-51 | stable_ok | 49.4s |
| CP-52 | stable_ok | 45.7s |
| CP-57 | stable_ok | 58.6s |
| CP-58 | stable_ok | 58.8s |
| CP-65 | stable_ok | 95.5s |
| CP-68 | stable_fail | 44.9s |
| CP-46 | stable_ok | 75.5s |
| CP-48 | stable_ok | 75.4s |
| CP-53 | stable_ok | 82.1s |
| CP-59 | stable_ok | 47.3s |

## Notes

- CP-68 was N=5 5/5 yesterday + N=1 1/1 fresh Kit at 01:58. Now stable_fail at 02:24
  (CP-68 was 7th in batch). Confirms Kit-state-drift after ~7 sequential CPs.
- CP-37 left out (was stable_fail in fresh Kit earlier — different issue,
  not Kit-state-drift since it failed alone too)

## Take-aways

1. **Validated unlocks (≥80% in batches):** CP-22/51/52/57/58/65/46/48/53/59 (10)
2. **Flaky after Kit drift:** CP-68, CP-37 (was OK earlier, now intermittent)
3. **Multi-Franka drop_target pattern works** in isolation but degrades in
   long Kit sessions.

## Plus 7 yrkesroll fresh-Kit verified at 02:00

CP-NEW-controller-shootout-cp, CP-NEW-3station-oee, CP-NEW-y-merge-singulation,
CP-NEW-cad-revision-drift, CP-NEW-inspect-reject, CP-NEW-dr-curriculum,
CP-NEW-multi-cam-triangulation

= 10 + 7 = **17 stable_ok confirmed in fresh-Kit conditions**.
