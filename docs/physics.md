# Physics in Schrödinger's Quant

**As of 2026-10-08.** What physics the project actually uses, where, and what
it cannot do. The figures live in
[physics.json](../research/experiments/H1/physics.json) (`make physics`), the
decision in [ADR-0008](adr/0008-physics-informed-nulls.md), the demo page
shows them under "Market structure and null models".

## In short

- **Statistical physics is used in three places:** as the description of the
  market H1 trades (stylized facts), as the source of null models that keep
  those facts but remove what a breakout needs (surrogates, volatility
  cascades), and as the growth-rate view of sizing (ergodicity). None of it
  changes H1's rules. All of it changes how hard the test is.
- **Quantum mechanics is a framing, not a model.** One mapping is exact and
  is written down below: the diffusion that describes a random-walk price is
  an imaginary-time Schrödinger equation, so the −20 % stop is a
  first-passage problem with a potential. It yields the same numbers as the
  diffusion. The name's other half, observation collapsing the state, is the
  multiple-testing problem, and the project charges for it with a ledger and
  the deflated Sharpe ratio.
- **Nothing here is evidence of an edge.** The held-out window was inspected
  before these diagnostics were added, so on that window they are descriptive.
  The one confirmatory use is forward: the calibration says how often the
  predeclared forward test would say GO in a market with no edge.

## 1. What the market looks like (stylized facts)

The econophysics literature of the 1990s established that returns of liquid
assets share a few robust properties regardless of the asset (Mantegna and
Stanley 1995; Gopikrishnan, Plerou, Amaral, Meyer and Stanley 1998; Cont 2001).
`sq.research.stylized` measures them on the 4h BTC/EUR log returns per period:

| Figure | What it measures | Why a breakout trader cares |
| --- | --- | --- |
| Tail index α (Hill) | How fast the frequency of large moves falls: P(\|r\| > x) ~ x^−α. Equities give α ≈ 3, the "inverse cubic law". | α near 2 means the variance is barely finite; the sample mean and Sharpe ratio of 14 trades are then unreliable, and the −20 % stop is reachable in one candle. |
| Excess kurtosis, skewness | How much fatter than Gaussian the tails are, and which side. | A stop on the fat side fires more often than a Gaussian model predicts. |
| Autocorrelation of r and of \|r\| | Linear memory of returns (near zero in an efficient market) and of volatility (long-lived). | Volatility clustering is why H2 sizes by recent volatility and why trade returns are not independent. |
| Hurst exponent H (DFA) | Persistence of the price path: H = 0.5 memoryless, H > 0.5 trends continue, H < 0.5 they revert. | A channel breakout earns money only from persistence. If H sits inside the range that shuffled returns produce, DFA detects no persistence at this sample size; permutation entropy below its shuffle range would still indicate short-range ordinal structure. |
| Permutation entropy | How evenly the up/down orderings of four consecutive candles are distributed; 1 is maximal disorder. | Values below the shuffle range mean there is ordinal structure; values inside it mean none at this horizon. |
| MRW intermittency λ² | Strength of the volatility cascade across time scales (Bacry, Delour and Muzy 2001). | It parametrises the multifractal null model below. |

The Hurst exponent uses detrended fluctuation analysis (Peng et al. 1994), a
method from statistical physics for non-stationary series. The shuffle range
is the physics-style null: the same returns in random order.

## 2. Null models (what a market without an edge produces)

ADR-0006 added two nulls: random timing at equal exposure and constant
exposure. Both keep the real price path and vary the strategy. The physics
nulls do the opposite: keep the strategy, vary the market. Each null preserves
a stated set of the market's properties and destroys the rest; H1's rules are
replayed on thousands of such markets by `sq.research.breakout`, a pure
reimplementation that reproduces every recorded trade
(`tests/test_breakout.py`).

| Null | Keeps | Destroys | Source |
| --- | --- | --- | --- |
| Block shuffle (5-day blocks) | The window's returns and wicks, short-range clustering | Order beyond five days, hence trends | permutation null |
| IAAFT surrogate | The exact return distribution and the linear power spectrum | All nonlinear temporal structure | Schreiber and Schmitz 1996 (nonlinear dynamics) |
| GBM, zero drift | The window's volatility | Everything else | Bachelier 1900, Osborne 1959 |
| GARCH(1,1)-t, zero drift / with the window's mean drift | Volatility clustering, heavy tails | Drift (first variant), multiscale cascade | Engle 1982, Bollerslev 1986 |
| MRW, zero drift | Multifractal volatility cascade with the window's λ² | Drift, any return predictability | Bacry, Delour and Muzy 2001 (turbulence cascades) |

Synthetic paths have no intrabar information of their own; highs and lows are
built from wick ratios resampled from the real candles so the channel sees
realistic extremes. The replay models the StoplossGuard protection but not
the MaxDrawdown protection, and the record never triggered either, so paths
with stop exits (reported per null) leave the validated regime. The output per null is the share of markets on which H1
matched its recorded return and drawdown. A small share says the result is
unusual for that null; it does not say what caused it, and it is computed on a
window that was inspected first.

The maximum-entropy principle is the thread through this table: each null is
the least-structured market consistent with a stated set of constraints, so
the shares answer "how much of H1's result do those constraints alone
explain?"

## 3. The stop as a first-passage problem, and the Schrödinger mapping

A long position with a catastrophe stop is a particle diffusing above an
absorbing barrier. For a log price X with drift μ and volatility σ, the
probability that X falls by b = −ln(1 − 0.2) within time t is

P(t) = Φ((−b − μt) / (σ√t)) + exp(−2μb / σ²) · Φ((−b + μt) / (σ√t)),

which for μ = 0 reduces to 2Φ(−b / (σ√t)). `physics.json` compares this
closed form with simulated 4h paths at the held-out window's volatility.

The density p(x, t) of X obeys the Fokker–Planck equation
∂p/∂t = −μ ∂p/∂x + (σ²/2) ∂²p/∂x². Substituting p = exp(μx/σ² − μ²t/(2σ²)) ψ
turns it into ∂ψ/∂t = (σ²/2) ∂²ψ/∂x², the free Schrödinger equation in
imaginary time with ħ²/(2m) ↔ σ²/2; a mean-reverting price (Ornstein–Uhlenbeck)
adds a quadratic potential and becomes the harmonic oscillator. The absorbing
stop is a Dirichlet boundary, the exit channel a moving boundary, and the
holding-time distribution is the first-passage density of that boundary
problem. The mapping is exact (Risken 1989); it is the same correspondence
Baaquie's quantum-finance formalism builds on. It changes no number: the
project reports the diffusion result and keeps the mapping as the honest
content behind the name.

What is *not* used: path-integral option pricing (no derivatives here),
quantum walks as price models (no evidence they describe markets), and
quantum-inspired portfolio optimisers (one asset, nothing to optimise).

## 4. Calibrating the forward test

The forward protocol ([forward-test.md](forward-test.md)) judges H1 after 30
closed trades with P1 (net return > 0) and P2 (drawdown ≤ 0.6 × buy-and-hold's)
and stops on F3 (drawdown > 31.31 %). A test is only as good as its false
positive rate. `physics.json` reports, for each fitted null with no drift and
for GARCH with the historical mean drift: how long 30 trades take, how often
F3 stops the test first, and how often P1 and P2 both pass. Under a no-drift
model that GO share is the protocol's false-GO rate; under the drifted model
it is the pass rate in a rising market without timing skill. No market with
an edge is simulated, so the protocol's power is not measured. A future GO is read against these
rates, not as proof of an edge.

## 5. Growth, Kelly and ergodicity

The fixed stake of the record reports the ensemble average: the mean of many
parallel €1,000 bets. A single account that compounds experiences the time
average, the mean of ln(1 + r), lower by the volatility drag (≈ σ²/2 per
trade). This is the distinction ergodicity economics insists on (Peters 2019;
Peters and Gell-Mann 2016). The superseded compounding run is the same
distinction seen from the drawdown side: a stake that grows with the account
turns the same trades into a deeper fall from a higher peak. `physics.json`
reports both averages, the Kelly fraction (the stake share that maximises the
time average, capped at 1 because the project does not borrow) with a
bootstrap interval, and a growth-versus-drawdown frontier of the recorded
marks. H2's volatility targeting is a special case of this view.

## 6. Observation and the ledger

Looking at data is a measurement. Every strategy variant evaluated on the
same history is a trial, and the best of N trials is inflated by selection
alone. [research/ledger.json](../research/ledger.json) lists every look at the
record; the deflated Sharpe ratio (Bailey and López de Prado 2014) computes
what the best of that many unrelated trials would show by luck and the
probability that H1's held-out Sharpe exceeds it after adjusting for the
skewness and kurtosis of its daily returns. This is the project's version of
the observer effect, and the reason every hypothesis is predeclared.

## What this can and cannot show

- It can say what the market looks like, which of its properties alone
  reproduce H1's result, how long the forward test will take, and how often
  it would pass with no edge.
- It cannot create evidence from 14 trades, and it cannot be used to tune H1:
  a physics-derived entry filter on existing data is a new hypothesis for
  forward data only.
- The null set was declared in the code and in the ADR draft before the
  first build; the repository history cannot show that order, so the claim
  rests on the author. No null was added or removed after the first build.

## References

Baaquie (2004) Quantum Finance. Bachelier (1900) Théorie de la spéculation.
Bacry, Delour, Muzy (2001) Phys. Rev. E 64, 026103. Bailey, López de Prado
(2014) J. Portfolio Management 40(5). Bandt, Pompe (2002) Phys. Rev. Lett.
88, 174102. Bollerslev (1986) J. Econometrics 31. Cont (2001) Quantitative
Finance 1. Engle (1982) Econometrica 50. Gopikrishnan, Meyer, Amaral,
Stanley (1998) Eur. Phys. J. B 3, 139. Gopikrishnan, Plerou, Amaral, Meyer,
Stanley (1999) Phys. Rev. E 60, 5305. Mantegna, Stanley (1995) Nature 376.
Osborne (1959) Operations Research 7. Peng et al. (1994) Phys. Rev. E 49.
Peters (2019) Nature Physics 15. Peters, Gell-Mann (2016) Chaos 26. Risken
(1989) The Fokker–Planck Equation. Schreiber, Schmitz (1996) Phys. Rev.
Lett. 77.
