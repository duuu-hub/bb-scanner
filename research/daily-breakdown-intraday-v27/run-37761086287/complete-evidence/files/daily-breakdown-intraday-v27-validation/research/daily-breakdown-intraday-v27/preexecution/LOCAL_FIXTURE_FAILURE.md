# Local synthetic fixture correction, before market dispatch

The first full synthetic scan yielded 192 rather than 256 outcomes. Its second
test period reused a signal close of 97 while the earlier synthetic crash had
already set the 20-day low near 94. Therefore the 20-day policies correctly did
not signal. This was not a strategy, source, chronology or market result.

The synthetic second event was changed to a close of 90 and a following open
of 80 so it independently crosses both prior channels. Actual strategy
parameters, registered policies, costs, caps, delays and data boundaries remain
unchanged. Preserve the first synthetic log and retest the full scan-to-account
pipeline. No actual V27 market outcome has been generated at this checkpoint.
