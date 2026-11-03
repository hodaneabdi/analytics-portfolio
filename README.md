# Hodane Abdi | Analytics Portfolio

Financial analyst completing a Master of Management Analytics at Queen's University, Smith School of Business. This portfolio collects graduate projects where the goal was a business decision, not a model score. Each project starts from a management question, shows the evidence, and ends with a recommendation and its limits.

The code for every project is included so the results can be checked and re-run.

## Projects

| Project | Business question | What the analysis found | Tools |
|---|---|---|---|
| [**Home Credit: Default Risk**](home-credit-default-risk/) | Which loan applicants should a lender send to manual review? | Reviewing the riskiest 20% of applicants catches about 53% of future defaults. A transparent scorecard gets within 0.012 AUC of the best machine-learning model, so it is the recommended decision tool. | Python, scikit-learn, XGBoost, statsmodels |
| [**Rossmann: Daily Sales Forecast**](rossmann-sales-forecast/) | Can a retailer forecast each store's daily sales well enough to plan promotions, stock and staffing? | A store-specific model explains about 83% of daily sales variation on unseen data. Promotion uplift is not uniform: it ranges from about 79% on a Monday to about 37% on a Friday in the model's reference case. | Python, statsmodels, pandas, HTML dashboard |
| [**Fleet Capacity Optimization**](fleet-capacity-optimization/) | Is it worth repairing 15 idle trucks to reduce expensive spot-market freight? | Repairing all 15 trucks adds roughly $51K of weekly profit on a $30K one-time cost, and the decision holds unless repair costs rise above roughly $5,400 per truck. | Python, Pyomo (optimization), Excel |

## Working approach

- **Start with the decision.** Each project README opens with the question management has to answer and ends with the recommended action.
- **Plain-language results.** Technical detail is kept in the notebooks, so the summary can be read without a statistics background.
- **State the limits.** Every project lists what the data could not show and what should be checked before acting.

## Background

Seven years of progressive experience in Government of Canada finance (budgeting, forecasting, executive reporting). These projects extend that foundation into predictive and prescriptive analytics.

## Repository layout

```
home-credit-default-risk/       notebook, charts
rossmann-sales-forecast/        notebook, executive dashboard
fleet-capacity-optimization/    notebook, case workbook, optimized schedule
```

All three were completed as team projects within the MMA program. Each project README names the course and team.
