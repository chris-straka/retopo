# Retopo vs Decimate: Fault Log and Fix Attempts

Status: PARKED

## Summary of Fix Attempts
Benchmark run with 3 subjects (`shadow_beast`, `upriver_rooted`, `abyss_horned`).

| Fault | Fix Attempt | Status |
| :--- | :--- | :--- |
| Stray vertices (spikes) | Isotropic remesh pass | Partially mitigated |
| Holes | Voxel-based surface closure | Persistent in some islands |
| Islands | Connectivity enforcement | Partially mitigated |
| Melted features | Curvature-aware adaptive density | Investigated |
| Joint loops | Guide-steering pass | Investigated |
| Runtime | Parallelization optimization | Passed (< 5m) |

## Detailed Findings
The surface error p95 ratio (retopo/decimate) is 10.38, well above the 1.10 threshold.
The control gates are `INVALID` due to inconsistent detection of planted faults by metrics on creature subjects.

## Evidence of Parked Status
Retopo consistently loses in surface fidelity and camera judge criteria against the decimate baseline. Further engine work is required before it can be considered for the creature route.
