# Fleet Capacity Optimization

**Deciding whether to repair idle trucks instead of paying spot-market rates**

*Queen's University, Smith School of Business, MMA 861 | Team project*
Team Agnes

> Course case study for a fictional trucking company, Agnes Trucking, using the case dataset supplied with the course.

## The business question

Agnes Trucking owns 107 tractors, but 15 are sitting idle in maintenance. On busy days there are more loads than active trucks, so the overflow goes to the third-party spot market at $3.00 to $3.50 per mile. The company's own cost is about $0.93 to $0.96 per mile. Is it worth paying $2,000 per truck to bring the idle tractors back?

## What we found

All figures come from the notebook in this folder, run on the case workbook for one representative week (377 loads).

| | Baseline (92 trucks) | After repair (107 trucks) |
|---|---|---|
| Net weekly profit | $340,221 | $391,201 |
| Profit on own trucks | $490,882 | $547,357 |
| Profit (loss) on spot market | -$150,661 | -$126,156 |
| Loads sent to spot market | 203 (54%) | 181 (48%) |
| One-time repair cost | none | $30,000 |

- **Yes, repair all 15.** After paying the $30,000, weekly profit rises by about **$51,000**.
- **It pays back quickly.** Each moved mile saves about $2.29 versus the spot market, so a repaired tractor recovers its $2,000 after roughly 870 miles. That is one to two typical loads.
- **The decision is robust to repair cost.** The average break-even is about **$5,400 per truck**, more than two and a half times the quoted price. Above roughly $5,250 the model starts leaving the least useful trucks unrepaired rather than all at once.
- **It holds if spot rates move.** If spot rates rise 20%, the weekly benefit grows to about $74,000. If they fall 20%, repair still adds about $28,000 per week. All 15 trucks are repaired in every case.
- **Spot-market dependence is costly.** Even after repair, the spot market loses money on every load it takes. Trucks that stay idle cost far more than the repair fee.
- **Not all trucks cost the same.** Volvo units run cheapest at about $0.93 per mile and Peterbilt highest at about $0.96, but all are far cheaper than spot rates. Having the trucks available matters more than which brand takes a given load.

## Recommendation

Approve the $30,000 repair budget and bring all 15 trucks back into the dispatch pool. Use the load-assignment model for daily dispatch planning. Then track whether repaired trucks pass the roughly 870-mile break-even point, and consider buying additional tractors if spot-market use stays high.

## Limits to keep in mind

- **One week of data.** The gain is for a single representative week. Weeks with different demand or trailer mix would change the size of the benefit, though the sensitivity tests suggest the direction is stable.
- **No repositioning cost.** Trucks are assumed able to start from any location, so real savings will be somewhat lower, especially when the network runs near full capacity.
- **No trailer matching.** The workbook has no trailer data per truck, so any truck can take any load in the model.
- **Repairs assumed to hold.** The model does not allow for repeat breakdowns, repair costs above the quoted fee, or driver availability for the reactivated trucks.

## How the work was done

The problem is set up as a mixed-integer optimization. Every load is assigned either to a specific truck or to the spot market. Every load must be covered, no truck can carry two loads on the same day, and an idle truck can only be used if its repair fee is paid. Two scenarios are compared: the 92 active trucks alone, versus all 107 with repair allowed where it pays off. The model is solved in Python with Pyomo and the open-source HiGHS solver, and each scenario is solved to proven optimality.

## Tools used

- **Language and environment:** Python, Jupyter Notebook
- **Optimization:** Pyomo (model building), HiGHS (open-source solver), mixed-integer linear programming
- **Data handling:** pandas, openpyxl, Excel (case workbook and schedule output)

## What is in this folder

| Path | Contents |
|---|---|
| `notebooks/Fleet_Capacity_Optimization.ipynb` | The model, results, and sensitivity tests |
| `data/Project_Agnes.xlsx` | Case workbook: 377 loads, 120 trucks, routes, cost assumptions |
| `data/Optimized_Fleet_Schedule.xlsx` | Load-by-load assignment (own truck or spot market) for both scenarios |
| `requirements.txt` | Python packages used |

## Re-running the notebook

`pip install -r requirements.txt`, then open `notebooks/Fleet_Capacity_Optimization.ipynb` in Jupyter and run all cells. The full run takes about three minutes. It also works in Google Colab: upload the workbook and uncomment the install line in the first cell.
