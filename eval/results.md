### Precision / recall by threshold

| Threshold | Precision | Recall | F1 | TP | FP | FN |
|---:|---:|---:|---:|---:|---:|---:|
| 60 | 0.693 | 0.925 | 0.792 | 185 | 82 | 15 |
| 70 | 0.902 | 0.920 | 0.911 | 184 | 20 | 16 |
| 75 | 0.913 | 0.890 | 0.901 | 178 | 17 | 22 |
| 80 | 0.933 | 0.830 | 0.878 | 166 | 12 | 34 |
| 90 | 0.969 | 0.620 | 0.756 | 124 | 4 | 76 |

### Recall by perturbation type (threshold 75)

| Perturbation | Cases | Recall |
|---|---:|---:|
| dropped_middle | 30 | 0.700 |
| nickname | 20 | 0.900 |
| order_swap | 30 | 1.000 |
| spacing_hyphen | 40 | 0.750 |
| transliteration | 40 | 1.000 |
| typo | 40 | 0.975 |

### Latency per screen call

| Metric | Value |
|---|---:|
| p50 | 37.4 ms |
| p95 | 65.0 ms |
| calls | 300 |
| embedding layer | on |
