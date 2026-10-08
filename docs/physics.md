# Physics in Schrödinger's Quant

**Reviewed 2026-10-08.** The reproducible diagnostics are in
[physics.json](../research/experiments/H1/physics.json), rebuilt with
`make physics`. [ADR-0008](adr/0008-physics-informed-nulls.md) records their
scope. Schema 3 includes all three configured protections as well as the
mathematical and interpretation corrections introduced in schema 2. H1's frozen rules and forward thresholds
are unchanged.

## What the diagnostics measure

Statistical physics supplies useful descriptions of fluctuations, surrogate
series, stochastic volatility and multiplicative growth. Quantum mechanics
supplies a formal diffusion correspondence, not a trading model or evidence
of an edge. The held-out data had already been inspected before these
analyses: all historical comparisons are descriptive.

| Diagnostic | Interpretation and limitation |
| --- | --- |
| Hill tail index α | Estimates a power-law tail from the largest 5% of observations. Under a genuine asymptotic power law, variance exists for α > 2. A finite-sample estimate near 2 neither establishes that law nor establishes finite variance. |
| Skewness, excess kurtosis | Describe asymmetry and tail weight in the sample; extreme observations can dominate both. |
| Autocorrelation of r and \|r\| | Measures linear dependence and volatility clustering at selected lags. Zero return autocorrelation does not imply independence. |
| DFA exponent H | Describes scaling over the fitted candle ranges. H near 0.5 is consistent with iid increments at those scales. It does not exclude drift or nonlinear predictability; H > 0.5 is not necessary or sufficient for breakout profitability. |
| Order-4 permutation entropy | Measures frequencies of ordinal patterns of four returns. Near one means those patterns are nearly uniform, not that every kind of predictability is absent. |
| MRW intermittency λ² | Fits logarithmically decaying covariance of log absolute returns. A positive fitted slope alone does not establish multifractality. |

Shuffle ranges compare these statistics with reordered observations. They
are not confidence intervals for a trading edge, and inspecting many periods
and diagnostics introduces further selection opportunities.

## Surrogates and fitted markets

The replay preserves the strategy and varies the input market. The recorded
41 trades reproduce at both cost levels. Synthetic paths additionally exercise
protection boundaries that the historical trades do not establish. Cooldown,
StoplossGuard and MaxDrawdown use the pinned backtest's rounded lock boundaries
and strict elapsed-time lookback limits, including across missing candles.

MaxDrawdown uses Freqtrade 2026.8's default **ratios** mode. Within the last 540
candles, sum the closed trades' net profit ratios chronologically, starting at
zero. If the greatest absolute fall from a running peak exceeds 0.25, entry
is locked until the next candle boundary after the latest close plus 180
candles. This is neither compounded drawdown nor a 25% fall from account equity.
It is separate from the forward protocol's F3 account-curve measurement.

The training replay triggers one such lock after the 2021-06-20 12:00 UTC
signal exit, expiring 2021-07-20 16:00 UTC. It changes no historical trade:
there is no entry during the lock. The previous assertion that no protection
fired was therefore incorrect. Synthetic boundary cases are checked directly
against the pinned protection. Reports include the share of paths with at
least one MaxDrawdown lock; calibration's lock frequency uses the full
counterfactual horizon, even after F3.

| Model | Constraints and limitations |
| --- | --- |
| Five-day block shuffle | Preserves returns, wicks and order within each block; disrupts order across blocks. It can retain drift and some longer-lived structure. |
| IAAFT | Preserves the empirical return distribution exactly and the power spectrum approximately. It disrupts temporal structure beyond these constraints, but does not guarantee destruction of every nonlinear dependency. |
| Gaussian log increments (GBM) | Constant fitted volatility, independent increments; zero **log** drift. |
| GARCH(1,1)-t | Conditional variance clustering and Student-t log-return innovations, with either zero or historical mean log drift. Long-window fits reach the persistence cap. |
| MRW | Gaussian innovations multiplied by a correlated lognormal volatility field; fitted log-volatility covariance and zero log drift. FFT clipping, if needed, changes covariance; normalization uses the resulting variance. |

These are specified stochastic constructions. No maximum-entropy derivation
has been established for this set. IAAFT's constraints and limitations are
discussed by [Schreiber and Schmitz](https://arxiv.org/abs/chao-dyn/9909037).

**Zero log drift is not zero expected price return.** If
`Δlog P ~ N(0, σ²Δt)`, then `E[P(t)/P(0)] = exp(σ²t/2)`.
A price-martingale GBM would instead require log drift `−σ²/2`.
Student-t log increments have no finite positive exponential moment:
exponentiating them gives infinite expected prices even though every generated
finite path is finite. Lognormal-volatility mixtures also need care with
exponential moments. These fitted paths are stress scenarios, not demonstrated
no-edge markets. See the [GBM derivation](https://www.columbia.edu/~ks20/FE-Notes/4700-07-Notes-GBM.pdf).

Resampled wicks in fitted models and IAAFT are independent of the generated
return; they are not extrema derived from the same continuous diffusion.
Return/drawdown shares therefore depend on this candle construction, costs,
fitted parameters and replay limitations. They are not model-free p-values.

## Forward protocol calibration

The unchanged protocol requires 30 completed trades, positive fixed-stake
return (P1), drawdown at most 0.6 times buy-and-hold's (P2), and no drawdown
above 31.31% (F3). A breach is latched: subsequent recovery or trades cannot
make that path complete the test.

Schema 2 reports completion and time to completion only on paths surviving
F3. It keeps time-to-30 **ignoring F3** separately as a counterfactual. The
full-horizon trades/year statistic also ignores stopping. P1/P2 pass shares
require eligible completion; all shares use all trials as their denominator.
These are model-conditioned GO rates, not established statistical false-GO
rates. Operational F1/F2 failures are not simulated, and no alternative with
an edge is simulated, so power is not estimated. Reconsidering F3 would require
a separate protocol decision; this review does not change it.

## First passage and the Schrödinger correspondence

Let log price relative to entry satisfy `dX = μ dt + σ dW`, with constant
coefficients, `X(0)=0` and absorbing barrier `−b = ln(0.8)`. For `t > 0` and
`σ > 0`, the reflection-principle result is

`P(τ ≤ t) = Φ((-b − μt)/(σ√t)) + exp(−2μb/σ²) Φ((-b + μt)/(σ√t))`.

At zero log drift this is `2Φ(−b/(σ√t))`. The original 4h-close simulation
misses crossings between observations. The new Brownian-bridge estimator
integrates them: conditional on consecutive endpoints `x,y > −b`, the
crossing probability is `exp(−2(x+b)(y+b)/(σ²Δt))`. Multiplying interval
survival probabilities gives each sampled skeleton's conditional survival.
The report supplies its mean and Monte Carlo standard error, alongside the
analytic result and the discrete-close estimate. This construction is covered
by [Glasserman and Staum](https://business.columbia.edu/sites/default/files-efs/pubfiles/4311/one-step_survival.pdf).

This is an unconditional constant-volatility barrier benchmark. H1 entries
select market states, its channel exit competes with the stop, and real gaps
and fees alter losses. It is not H1's conditional stop probability.

For diffusion constant `D=σ²/2`, the forward density satisfies
`∂t p = −μ ∂x p + D ∂xx p`. Substituting
`p = exp(μx/σ² − μ²t/(2σ²)) ψ` gives `∂t ψ = D ∂xx ψ`.
The imaginary-time quantum equation is `∂t ψ = −Hψ/ħ`; thus with the same
time coordinate the kinetic correspondence is **D = ħ/(2m)**, or
`D=1/(2m)` in units `ħ=1`. For Ornstein–Uhlenbeck drift `−κx`, the spatial
transform gives

`∂t ψ = D ∂xx ψ − [κ²x²/(4D) − κ/2] ψ`.

This is a harmonic-oscillator operator with an energy shift. An absorbing
barrier becomes a Dirichlet boundary. H1's rolling channel is path-dependent;
it cannot be reduced to a prescribed moving boundary in a one-dimensional
price-only PDE without augmenting the state. The correspondence changes no
probability and adds no quantum advantage. See
[Pavliotis, Stochastic Processes and Applications](https://www.ma.imperial.ac.uk/~pavl/PavliotisBook.pdf).

## Growth and sizing

For empirical trade returns `r`, report the arithmetic mean `m=mean(r)` and
log growth `g=mean(ln(1+r))`. A finite recorded average is an estimator, not
proof of a long-run ensemble or time limit. Under suitable stationary,
ergodic assumptions, `g` describes multiplicative growth.

The volatility drag in common log units is the Jensen gap
`ln(1+m) − g`, nonnegative and zero for constant returns. For small returns
it is approximately `Var(r)/2`. The different quantity `m−g` additionally
contains the simple-return/log-return conversion and is approximately
`E[r²]/2`. Schema 2 reports both rather than calling the latter volatility.
Annualized growth here is a log rate; its corresponding simple CAGR is
`exp(g_annual)−1`.

Empirical Kelly maximizes `mean(ln(1+f*r))` on a grid with `0 ≤ f ≤ 1`.
The wider search is capped at **5**, so its result is not an unconstrained
optimum. The trade bootstrap is IID and descriptive; it does not account for
serial dependence, unobserved tail losses or selection. The daily frontier
synthetically compounds fixed-notional daily P&L increments. It is not a
self-financing replay of H1 with stakes resized only at entries, and does not
supply revised fills or rebalancing costs.

## Research ledger and deflated Sharpe

The ledger records seven counted evaluations. Four lack Sharpe ratios; the
three available values come from different periods of the same strategy.
Their dispersion is not the cross-strategy variance on a common evaluation
sample required for the selection adjustment. Schema 2 therefore leaves the
expected maximum and deflated Sharpe **unavailable**, replacing the previous
0.74 score. Missing evidence is not a zero selection penalty.

Even with comparable inputs, a raw count of correlated evaluations is not an
estimated effective independent-trial count. The implemented asymptotic
formula adjusts for skewness and kurtosis, not serial dependence; its score
is not a posterior probability that H1 has an edge. The original
[Bailey and López de Prado paper](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
states the required selection inputs. Quantum observation and research
selection are distinct phenomena; “observer effect” is only a metaphor here.
