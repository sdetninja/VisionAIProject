# Combined dataset summary

| class | train | val | test |
|---|---|---|---|
| person | 13409 | 1690 | 1647 |
| helmet | 9426 | 1172 | 1213 |
| no_helmet | 5983 | 783 | 797 |
| vest | 2283 | 255 | 271 |
| gloves | 3489 | 247 | 308 |
| no_gloves | 4513 | 684 | 842 |
| goggles | 1038 | 106 | 145 |
| mask | 519 | 23 | 34 |
| fire | 2468 | 334 | 402 |
| smoke | 2067 | 258 | 348 |

Images: train 8032, val 993, test 1191

## Label provenance per source

- **construction_ppe**: label:gloves 1445, label:goggles 518, label:helmet 1734, label:no_gloves 556, label:no_helmet 485, label:person 2245, label:vest 1618
- **sh17**: derived:no_gloves 5483, derived:no_helmet 4488, label:gloves 2489, label:goggles 771, label:helmet 831, label:mask 576, label:person 6103, label:vest 465
- **hf_ppe**: label:helmet 9246, label:no_helmet 2590, pseudo:gloves 110, pseudo:person 7635, pseudo:vest 726
- **d_fire**: label:fire 3204, label:smoke 2673, pseudo:person 763
