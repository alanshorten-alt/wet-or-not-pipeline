# Radar validation against the Met Éireann hourly composite (2026-11-26)

Tool: `pipeline/validate_hours.py`. Nine hours, all one day (one weather regime
change from dry to steady rain), so treat the numbers as indicative, not final.
Composite stamp `HH0000` is the hour ending at HH.

| hour ending | regime | ours / composite total | pixel corr | 3x3 corr |
|---|---|---|---|---|
| 06, 08, 09 | dry | meaningless (both near 0.05 mm) | -0.16, -0.14, 0.00 | -0.22, -0.30, 0.07 |
| 11 | showery onset | 2.28 | 0.39 | 0.43 |
| 12 | wetting | 1.09 | 0.60 | 0.64 |
| 13 | steady | 0.91 | 0.58 | 0.62 |
| 14 | steady | 0.91 | 0.58 | 0.63 |
| 15 | steady | 0.86 | 0.44 | 0.48 |
| 16 | steady | 0.91 | 0.38 | 0.41 |

- Nothing is written beyond 250 km of both radars (1,826 cells, max 0.0 mm/h in every hour).
- Z-R multiplier: not justified. Steady hours pool to 0.92, but the showery
  hour is 2.3, so any single factor fixes one regime and breaks another. The
  intensity table (ours too high on light, too low on heavy) is mostly regression
  to the mean from a point-versus-area comparison, not evidence of a wrong Z-R.
  Far range (180-250 km) reads 0.5-0.7 in several hours: beam overshoot, a
  range-dependent effect rather than a global one.
- Correlation is modest (0.4-0.6, only ~0.05 better with 3x3 smoothing). Most
  likely the composite is a different product (its own beam-height selection
  and clutter handling) and scans are 5-min snapshots against its integration.
- Clutter mask (`clutter_mask.json`): 301 cells (1.0%) echoing in at least 50% of
  36 dry slots. They sit in three groups: Dublin and Wicklow near the Dublin
  radar, the Mournes at about 85 km, and ~240 cells at 150-250 km from Shannon
  off the west coast (sea clutter or ducting). In widespread rain the composite
  gives these cells about a tenth of the area-average rain, so zeroing them
  matches the official product. It removes only 0.5-1.3% of our rain in wet
  hours. It catches 60%, 46% and 19% of our dry-hour false rain: the 09:00
  echoes (1,373 cells) are transient, not static, so a mask cannot fix them.
- Open: a light-rain floor or a dBZ minimum above 5 for the dry-hour spurious
  echoes, and a rerun of the mask over several days before trusting it.
