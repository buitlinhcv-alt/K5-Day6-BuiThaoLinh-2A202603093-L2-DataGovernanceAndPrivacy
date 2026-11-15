Baseline: `baseline` (git b0ce8bf, seed 42) · PoC: `poc` (git b0ce8bf, seed 42)

| Metric (unit) | Slice | Baseline | PoC | Δ abs | Δ rel | Better | N |
|---|---|---|---|---|---|---|---|
| **Pair Accuracy@50ms** (fraction) | all | 0.9871 | 1.0000 | 0.0129 | +1.3% | higher | 3570 |
| Median |time residual| (ms) | all | 2.5500 | 2.4500 | -0.1000 | -3.9% | lower | 3521 |
| Bad-pair recall (dropout held) (fraction) | all | 0.0000 | 1.0000 | 1.0000 | baseline=0 | higher | 49 |
| % frames HOLD (valid frames) (fraction) | all | 0.0000 | 0.0000 | 0.0000 | baseline=0 | lower | 3521 |
| Runtime / 100 items (s) | all | 0.0079 | 0.0151 | | | lower | |
