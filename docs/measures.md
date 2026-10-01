# DAX measures

Generated from `build/generate_pbip.py`, the same list that writes the model, so it is always in sync.

## Conversion

### Win rate

Won deals over closed deals. Open deals are left out on purpose: they haven't had the chance to be won yet.

```dax
Win rate =
DIVIDE ( [Won deals], [Closed deals] )
```

### Median sales cycle (days)

Median days from creation to close for won deals. Median, not average: a few deals that drag on for a year would distort the mean.

```dax
Median sales cycle (days) =
CALCULATE ( MEDIAN ( Deals[Cycle days] ), Deals[Is won] = TRUE () )
```

## Formatting

### Stuck colour

Background colour for the open deals table: red when the deal is stuck.

```dax
Stuck colour =
VAR d = MAX ( Deals[Days in current stage] )
RETURN
    IF ( NOT ISBLANK ( d ) && d > [Stage p90 days], "#F4C7CC" )
```

## Funnel

### Deals reached stage

Distinct deals that entered the stage in context, by creation date of the deal.

```dax
Deals reached stage =
CALCULATE ( DISTINCTCOUNT ( StageHistory[Deal ID] ), USERELATIONSHIP ( Deals[Created date], 'Date'[Date] ) )
```

### Stage conversion %

Deals that reached this stage over deals that reached the previous one.

```dax
Stage conversion % =
VAR stageNo = SELECTEDVALUE ( StageHistory[Stage order] )
VAR reachedThis = [Deals reached stage]
VAR reachedPrevious =
    CALCULATE ( [Deals reached stage], REMOVEFILTERS ( StageHistory[Stage], StageHistory[Stage order] ), StageHistory[Stage order] = stageNo - 1 )
RETURN
    IF ( stageNo > 1 && stageNo <= 5, DIVIDE ( reachedThis, reachedPrevious ) )
```

## Info

### Snapshot date

Date the data was extracted. Used instead of TODAY() because the dataset is frozen.

```dax
Snapshot date =
DATE ( 2026, 9, 30 )
```

### Last refresh

Text shown in the page header.

```dax
Last refresh =
"Data as of " & FORMAT ( DATE ( 2026, 9, 30 ), "d mmm yyyy" )
```

## Partners

### Partner share %

Share of partner bookings brought by the partner in context.

```dax
Partner share % =
DIVIDE ( [Bookings], CALCULATE ( [Bookings], REMOVEFILTERS ( Partners ), Deals[Channel] = "Partner" ) )
```

### Partner rank

Rank of the partner by bookings.

```dax
Partner rank =
IF (
    HASONEVALUE ( Partners[Partner name] ) && NOT ISBLANK ( [Bookings] ),
    RANKX ( ALLNOBLANKROW ( Partners ), [Bookings] )
)
```

### Partner cumulative %

Cumulative share of partner bookings, from the best partner down. The classic Pareto line.

```dax
Partner cumulative % =
VAR thisPartner = [Bookings]
VAR ranked = ADDCOLUMNS ( ALLNOBLANKROW ( Partners ), "@b", [Bookings] )
VAR cumulated = SUMX ( FILTER ( ranked, [@b] >= thisPartner ), [@b] )
RETURN
    IF ( HASONEVALUE ( Partners[Partner name] ) && NOT ISBLANK ( thisPartner ), DIVIDE ( cumulated, SUMX ( ranked, [@b] ) ) )
```

## Pipeline

### Open deals

Deals still open at the snapshot date. Ignores date filters, since open deals have no close date.

```dax
Open deals =
CALCULATE ( COUNTROWS ( Deals ), Deals[Is closed] = FALSE (), REMOVEFILTERS ( 'Date' ) )
```

### Open pipeline

Total value of open deals.

```dax
Open pipeline =
CALCULATE ( SUM ( Deals[Amount] ), Deals[Is closed] = FALSE (), REMOVEFILTERS ( 'Date' ) )
```

### Stage win probability

Share of closed deals that were won, among those that reached the stage in context. Computed on the whole history, ignoring slicers, so it stays stable.

```dax
Stage win probability =
VAR s = SELECTEDVALUE ( Deals[Last open stage] )
VAR reached =
    CALCULATETABLE ( VALUES ( StageHistory[Deal ID] ), REMOVEFILTERS ( Deals ), StageHistory[Stage] = s )
VAR closedReached =
    CALCULATE ( COUNTROWS ( Deals ), REMOVEFILTERS ( Deals ), TREATAS ( reached, Deals[Deal ID] ), Deals[Is closed] = TRUE () )
VAR wonReached =
    CALCULATE ( COUNTROWS ( Deals ), REMOVEFILTERS ( Deals ), TREATAS ( reached, Deals[Deal ID] ), Deals[Is won] = TRUE () )
RETURN
    DIVIDE ( wonReached, closedReached )
```

### Weighted pipeline

Open value multiplied by the historical win probability of the stage each deal is in. A deal in Discovery counts much less than one in Negotiation.

```dax
Weighted pipeline =
SUMX (
    CALCULATETABLE ( VALUES ( Deals[Last open stage] ), REMOVEFILTERS ( 'Date' ) ),
    [Stage win probability] * [Open pipeline]
)
```

### Target Q4 remaining

Target for the months after the snapshot date, to the end of the year.

```dax
Target Q4 remaining =
VAR snap = DATE ( 2026, 9, 30 )
RETURN
    CALCULATE ( [Target], DATESBETWEEN ( 'Date'[Date], snap + 1, DATE ( YEAR ( snap ), 12, 31 ) ) )
```

### Pipeline coverage

Weighted pipeline over the target still to reach this year. Below 1 means the open deals alone won't close the gap.

```dax
Pipeline coverage =
DIVIDE ( [Weighted pipeline], [Target Q4 remaining] )
```

### Stage p90 days

90th percentile of the days closed deals spent in the stage in context. Used to spot open deals that are stuck.

```dax
Stage p90 days =
VAR s = SELECTEDVALUE ( Deals[Last open stage] )
RETURN
    CALCULATE (
        PERCENTILE.INC ( StageHistory[Days in stage], 0.9 ),
        REMOVEFILTERS ( Deals ),
        StageHistory[Stage] = s,
        NOT ISBLANK ( StageHistory[Exited date] )
    )
```

### Stuck deals

Open deals that have been in their current stage longer than 90% of the deals before them.

```dax
Stuck deals =
SUMX (
    CALCULATETABLE ( VALUES ( Deals[Last open stage] ), REMOVEFILTERS ( 'Date' ) ),
    VAR p90 = [Stage p90 days]
    RETURN
        CALCULATE ( COUNTROWS ( Deals ), Deals[Is closed] = FALSE (), Deals[Days in current stage] > p90, REMOVEFILTERS ( 'Date' ) )
)
```

## Revenue

### Bookings

Annual value of won deals, by close date.

```dax
Bookings =
CALCULATE ( SUM ( Deals[Amount] ), Deals[Is won] = TRUE () )
```

### Avg deal size

Average annual value of a won deal.

```dax
Avg deal size =
CALCULATE ( AVERAGE ( Deals[Amount] ), Deals[Is won] = TRUE () )
```

### Bookings LY

Bookings in the same period one year earlier.

```dax
Bookings LY =
CALCULATE ( [Bookings], SAMEPERIODLASTYEAR ( 'Date'[Date] ) )
```

### Bookings YoY %

Change of bookings against the same period last year.

```dax
Bookings YoY % =
DIVIDE ( [Bookings] - [Bookings LY], [Bookings LY] )
```

### Bookings YTD

Bookings from 1 January to the snapshot date of the snapshot year.

```dax
Bookings YTD =
VAR snap = DATE ( 2026, 9, 30 )
RETURN
    CALCULATE ( [Bookings], DATESBETWEEN ( 'Date'[Date], DATE ( YEAR ( snap ), 1, 1 ), snap ) )
```

### Bookings YTD LY

Same window as Bookings YTD, one year earlier. Like for like, so a partial year is never compared with a full one.

```dax
Bookings YTD LY =
VAR snap = DATE ( 2026, 9, 30 )
RETURN
    CALCULATE ( [Bookings], DATESBETWEEN ( 'Date'[Date], DATE ( YEAR ( snap ) - 1, 1, 1 ), EDATE ( snap, -12 ) ) )
```

### Bookings YTD vs LY %

Year to date growth, like for like.

```dax
Bookings YTD vs LY % =
DIVIDE ( [Bookings YTD] - [Bookings YTD LY], [Bookings YTD LY] )
```

## Targets

### Target

Monthly sales target by channel.

```dax
Target =
SUM ( Targets[Target] )
```

### Target YTD

Target from 1 January to the end of the snapshot month.

```dax
Target YTD =
VAR snap = DATE ( 2026, 9, 30 )
RETURN
    CALCULATE ( [Target], DATESBETWEEN ( 'Date'[Date], DATE ( YEAR ( snap ), 1, 1 ), snap ) )
```

### Attainment %

Bookings over target in the current context.

```dax
Attainment % =
DIVIDE ( [Bookings], [Target] )
```

### Attainment YTD %

Year to date bookings over year to date target.

```dax
Attainment YTD % =
DIVIDE ( [Bookings YTD], [Target YTD] )
```

## Volume

### Deal count

Number of deals in the current filter context, by close date.

```dax
Deal count =
COUNTROWS ( Deals )
```

### Deals created

Deals by creation date. Activates the inactive relationship on Created date.

```dax
Deals created =
CALCULATE ( COUNTROWS ( Deals ), USERELATIONSHIP ( Deals[Created date], 'Date'[Date] ) )
```

### Won deals

Deals closed as won.

```dax
Won deals =
CALCULATE ( COUNTROWS ( Deals ), Deals[Is won] = TRUE () )
```

### Closed deals

Deals closed, won or lost.

```dax
Closed deals =
CALCULATE ( COUNTROWS ( Deals ), Deals[Is closed] = TRUE () )
```

### Lost deals

Deals closed as lost.

```dax
Lost deals =
CALCULATE ( COUNTROWS ( Deals ), Deals[Status] = "Lost" )
```
