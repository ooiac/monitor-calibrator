# Profiles

Drop calibrations here and the app picks them up for the matching monitor.

**Your own settings.** Tune a screen on the Adjust page and press *Save for this monitor*. That writes a small `.json` file here. You can also write one by hand, see `_example.json`. Files starting with `_` are ignored.

**ICC files** (`.icc` / `.icm`), for example from a review site or a colorimeter. The app matches them to a monitor by its id (like `DEL4123`) or its model number appearing in the file name. *Load an ICC file* on the Overview page copies one here with the right name. Only ICC files that carry calibration curves can be applied.

Anything you put here stays on your PC. It's ignored by git.
