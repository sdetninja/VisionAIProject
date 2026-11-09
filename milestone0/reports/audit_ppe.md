# Dataset audit

Images: {'train': 1087, 'val': 194, 'test': 135}

| class | train | val | test | median box h (img frac) | % small (<3%) |
|---|---|---|---|---|---|
| person | 1738 | 302 | 205 | 0.660 | 0% |
| helmet | 1339 | 251 | 144 | 0.093 | 2% |
| no_helmet | 393 | 53 | 39 | 0.116 | 0% |
| vest | 1250 | 231 | 137 | 0.290 | 0% |
| no_vest | 0 | 0 | 0 | 0.000 | 0% |
| gloves | 1089 | 209 | 147 | 0.087 | 1% |
| no_gloves | 439 | 64 | 53 | 0.099 | 1% |
| goggles | 724 | 114 | 91 | 0.048 | 16% |
| mask | 0 | 0 | 0 | 0.000 | 0% |
| forklift | 0 | 0 | 0 | 0.000 | 0% |
| truck | 0 | 0 | 0 | 0.000 | 0% |

## Warnings

- `no_vest`: only 0 test instances (<30); metrics unreliable - label more own-camera data
- `mask`: only 0 test instances (<30); metrics unreliable - label more own-camera data
- `forklift`: only 0 test instances (<30); metrics unreliable - label more own-camera data
- `truck`: only 0 test instances (<30); metrics unreliable - label more own-camera data