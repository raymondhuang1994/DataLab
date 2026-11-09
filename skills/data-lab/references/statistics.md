# Statistical methods

Adapted from the upstream statistical-analysis workflow. Use the design of the data, not the desired conclusion, to choose a test.

For numeric distributions report useful center and spread: mean/median, standard deviation/IQR, sample count and tails when relevant. Treat IQR/Z-score flags as candidates for investigation, not an automatic deletion rule.

## Choose inference

| Situation | Starting method | Check |
|---|---|---|
| Two independent numeric groups | Welch t-test | Independence, finite variance, heavy tails, sample size |
| Two ordinal/skewed independent samples | Mann–Whitney | Distributions/ranks; not automatically a test of medians |
| Same units measured twice | Paired analysis | Correct pairing and missing pairs |
| Two conversion rates | Proportion methods or exact tests | Numerator/denominator, small expected counts |
| Several groups | ANOVA/robust alternative and planned comparisons | Group assumptions and multiple testing |
| Association of categorical variables | Chi-square or exact test | Expected cell counts |

Bundled `compare` implements only Welch and Mann–Whitney for independent samples. Use appropriate code for other designs and document it; do not shoehorn repeated observations into an independent test.

Report effect size/direction, confidence interval when supported, p-value, observation counts, exclusions and assumptions. Statistical significance does not establish practical importance or causality. Avoid wording that treats a p-value as the probability the null is true. Handle multiple comparisons and post-hoc selection explicitly.

Time-series comparisons need aligned periods, calendar/seasonality context and appropriate dependence assumptions. Prediction intervals require a justified method; do not invent ranges around point forecasts.
