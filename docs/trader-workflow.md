# Using the research desk on a small VPS

The decision desk turns the archived experiment into a risk and operating-cost
worksheet. It runs offline in Python's standard library, reads two small JSON
files, and performs no downloads, backtests, parameter search or resampling.
Run it on your laptop or the VPS; Docker and Freqtrade are not required.

```sh
PYTHONPATH=src python -m sq.research.desk
PYTHONPATH=src python -m sq.research.desk \
  --experiment research/experiments/H1 --out-dir build/desk \
  --monthly-cost-eur 10
```

The second command's €10 monthly cost is an example input. Supply your own VPS
cost, or your defensible allocated share when it also hosts other services.
The default is zero; the desk never invents a hosting price. Outputs overwrite
`report.md`, `report.json` and `monthly-returns.csv` in the chosen directory.
The statistics source SHA-256 must match the equity file exactly, preventing
an old uncertainty assessment from silently attaching to a changed curve.

## Review risk before economics

Read the base and stress runs together, then compare with buy-and-hold.
The archived **4h drawdown remains the authoritative research result**. The
desk's daily drawdowns include initial capital and measure a different, coarser
path. Daily marks cannot reveal a fall and recovery inside one day. Missing
early marks are disclosed and never filled with fabricated prices.

Longest and current underwater durations describe time since the last peak at
daily boundaries; a recovery at the peak resets the count. A long spell under
the peak is a practical capital and patience constraint even when final return
is positive. Compare monthly strategy and buy-and-hold returns to see which
market periods generated or lost the apparent advantage. First and last partial
months are labelled. The first interval includes initial equity, so multiplying
monthly growth factors reconciles the final curve return. Excess percentage
points are a comparison, not a causal alpha estimate.

The 25%, 50%, 75% and 100% strategy sleeves show the recorded fixed-stake P&L
scaled within a larger account, with the remaining cash earning zero. For
example a 50% sleeve starts with half of the account as the fixed trading stake.
Profits do not increase that stake, and the account is never rebalanced.
The curves use `account equity = initial + sleeve × (recorded equity − initial)`.
These are retrospective scenarios, not a sizing recommendation. Drawdown is
recomputed for each curve; it is not simply original drawdown times the sleeve.
Base and stress rows reuse archived fee assumptions. The desk cannot estimate
new execution costs, liquidity, lot sizes or altered trade paths from this data.

## Account for the cost of running the experiment

The desk reports average annual cash gain using total historical P&L divided
by elapsed years, then deducts `12 × monthly VPS cost`. This rate deliberately
uses fixed-stake cash economics; it is not a compounded CAGR or a forecast.
The same observed rate is shown at hypothetical €1,000, €5,000 and €10,000
account sizes, with a historical capital threshold for covering the annual VPS
bill. A null threshold means the historical cash rate was nonpositive.

A threshold does not make that capital safe to deploy. Linear scaling assumes
the same fills and fee percentages, ignores cash yield and taxes, and cannot
capture capital-dependent execution costs. Keep deployment decisions outside
this historical worksheet. For a forward paper experiment, the VPS bill is a
research budget even when the strategy produces no trading income.

## Qualify the evidence and decide what to investigate next

H1's held-out result is carried by a few trades: dropping the two largest
positive rounded trade returns leaves a negative descriptive sum. That deletion
check does not rerun the strategy or produce an executable alternative path.
Bootstrap confidence intervals and the share of random-timing trials exceeding
the actual return come from `statistics.json`; no new samples are generated.
A CI spanning zero is inconclusive evidence, and the timing tail share is not
the probability the strategy has no edge.

H1's held-out period was run twice after a stake-sizing correction. The initial
compounding run failed K2; the corrected fixed-stake result passed the archived
rule. That history and these post-record diagnostics consume the historical
window as fresh validation. Use [the forward-test workflow](forward-test.md)
and predeclared criteria for new evidence. The desk neither infers an edge nor
grants capital approval.

Keep the JSON/CSV and their input hashes beside your research notes, record your
actual hosting cost, and review future forward evidence on the same metric
basis. This keeps expensive historical computation off the VPS and gives the
VPS a bounded job: collecting forward evidence and monitoring execution.
