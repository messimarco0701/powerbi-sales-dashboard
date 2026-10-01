"""
Generates the Power BI project (PBIP) from code: semantic model in TMDL, report in PBIR.

Why generate it instead of clicking it together in Power BI Desktop:
  * every measure, relationship and visual is plain text, so git shows exactly what changed
  * the DAX documentation in docs/measures.md comes from the same list, so it can't go stale
  * rebuilding the whole report after a model change takes one command

Re-running this script overwrites any change made by hand in Desktop.
"""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "SalesPipeline"
SM = ROOT / f"{NAME}.SemanticModel"
RP = ROOT / f"{NAME}.Report"
SNAPSHOT = (2026, 9, 30)  # the synthetic data is frozen at this date, so it replaces TODAY()
EURO = r"\€ #,0"
GITHUB_DATA = "https://raw.githubusercontent.com/messimarco0701/powerbi-sales-dashboard/main/data/"
PCT = "0.0%"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, obj) -> None:
    write(path, json.dumps(obj, ensure_ascii=False, indent=2))


def tag() -> str:
    return str(uuid.uuid4())


def q(name: str) -> str:
    return f"'{name}'" if any(c in name for c in " .-/()'%") or name == "Date" else name


def indent(text: str, n: int) -> str:
    return "\n".join(("\t" * n + r) if r.strip() else "" for r in text.strip("\n").split("\n"))


# --------------------------------------------------------------------------- Power Query

def csv_query(file: str, columns: list[tuple[str, str, str]], extra_steps: str = "", last: str = "Typed") -> str:
    """columns: (csv column, model column, M type)"""
    renames = ", ".join(f'{{"{src}", "{dst}"}}' for src, dst, _ in columns)
    types = ", ".join(f'{{"{dst}", {mtype}}}' for _, dst, mtype in columns)
    return f'''
let
    // DataFolder can be a local folder or the raw GitHub URL of this repo's data folder
    Content = if Text.StartsWith(DataFolder, "http") then Web.Contents(DataFolder & "{file}") else File.Contents(DataFolder & "{file}"),
    Source = Csv.Document(Content, [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    // empty strings in the CSV mean "no value", not an empty text
    Nulls = Table.ReplaceValue(Headers, "", null, Replacer.ReplaceValue, Table.ColumnNames(Headers)),
    Renamed = Table.RenameColumns(Nulls, {{{renames}}}),
    Typed = Table.TransformColumnTypes(Renamed, {{{types}}}, "en-US"){extra_steps}
in
    {last}
'''


STAGE_ORDER = '{"Discovery", "Qualified", "Proposal", "Negotiation", "Closed Won", "Closed Lost"}'

M_DEALS = csv_query("fact_deals.csv", [
    ("deal_id", "Deal ID", "Int64.Type"), ("deal_name", "Deal name", "type text"),
    ("company_id", "Company ID", "Int64.Type"), ("owner_id", "Owner ID", "Int64.Type"),
    ("partner_id", "Partner ID", "Int64.Type"), ("channel", "Channel", "type text"),
    ("deal_type", "Deal type", "type text"), ("product", "Product", "type text"),
    ("created_date", "Created date", "type date"), ("close_date", "Close date", "type date"),
    ("stage", "Stage", "type text"), ("last_open_stage", "Last open stage", "type text"),
    ("is_closed", "Is closed", "type logical"), ("is_won", "Is won", "type logical"),
    ("lost_reason", "Lost reason", "type text"), ("employees_covered", "Employees covered", "Int64.Type"),
    ("amount", "Amount", "type number"),
], extra_steps=''',
    Status = Table.AddColumn(Typed, "Status", each if [Is won] then "Won" else if [Is closed] then "Lost" else "Open", type text),
    StatusOrder = Table.AddColumn(Status, "Status order", each List.PositionOf({"Open", "Won", "Lost"}, [Status]) + 1, Int64.Type),
    StageOrder = Table.AddColumn(StatusOrder, "Last open stage order", each List.PositionOf(''' + STAGE_ORDER + ''', [Last open stage]) + 1, Int64.Type),
    Cycle = Table.AddColumn(StageOrder, "Cycle days", each if [Close date] = null then null else Duration.Days([Close date] - [Created date]), Int64.Type),
    Age = Table.AddColumn(Cycle, "Age days", each if [Is closed] then null else Duration.Days(SnapshotDate - [Created date]), Int64.Type),
    // when did the open deal enter the stage it is sitting in: its most recent history row
    Entered = Table.Group(StageHistory, {"Deal ID"}, {{"Stage entered", each List.Max([Entered date]), type date}}),
    Joined = Table.NestedJoin(Age, {"Deal ID"}, Entered, {"Deal ID"}, "E", JoinKind.LeftOuter),
    Expanded = Table.ExpandTableColumn(Joined, "E", {"Stage entered"}),
    InStage = Table.AddColumn(Expanded, "Days in current stage", each if [Is closed] or [Stage entered] = null then null else Duration.Days(SnapshotDate - [Stage entered]), Int64.Type),
    Result = Table.RemoveColumns(InStage, {"Stage entered"})''', last="Result")

M_HISTORY = csv_query("fact_stage_history.csv", [
    ("deal_id", "Deal ID", "Int64.Type"), ("stage", "Stage", "type text"),
    ("entered_date", "Entered date", "type date"), ("exited_date", "Exited date", "type date"),
], extra_steps=''',
    Order = Table.AddColumn(Typed, "Stage order", each List.PositionOf(''' + STAGE_ORDER + ''', [Stage]) + 1, Int64.Type),
    Days = Table.AddColumn(Order, "Days in stage", each if [Exited date] = null then null else Duration.Days([Exited date] - [Entered date]), Int64.Type)''',
    last="Days")

M_COMPANIES = csv_query("dim_company.csv", [
    ("company_id", "Company ID", "Int64.Type"), ("company_name", "Company name", "type text"),
    ("sector", "Sector", "type text"), ("region", "Region", "type text"),
    ("employees", "Employees", "Int64.Type"), ("size_segment", "Size segment", "type text"),
], extra_steps=''',
    Order = Table.AddColumn(Typed, "Segment order", each List.PositionOf({"Micro", "Small", "Medium", "Large"}, [Size segment]) + 1, Int64.Type)''',
    last="Order")

M_PARTNERS = csv_query("dim_partner.csv", [
    ("partner_id", "Partner ID", "Int64.Type"), ("partner_name", "Partner name", "type text"),
    ("partner_type", "Partner type", "type text"), ("region", "Partner region", "type text"),
    ("onboarded_date", "Onboarded date", "type date"),
])

M_OWNERS = csv_query("dim_owner.csv", [
    ("owner_id", "Owner ID", "Int64.Type"), ("owner_name", "Owner", "type text"), ("team", "Team", "type text"),
])

M_TARGETS = csv_query("targets.csv", [
    ("month", "Month", "type date"), ("channel", "Channel", "type text"), ("target", "Target", "type number"),
])

M_CHANNELS = '''
let
    Source = Table.FromRows({{"Direct", 1}, {"Partner", 2}, {"Referral", 3}}, type table [Channel = text, #"Channel order" = Int64.Type])
in
    Source
'''

M_DATE = '''
let
    Dates = List.Dates(#date(2024, 1, 1), Duration.Days(#date(2026, 12, 31) - #date(2024, 1, 1)) + 1, #duration(1, 0, 0, 0)),
    Base = Table.FromList(Dates, Splitter.SplitByNothing(), type table [Date = date]),
    Year = Table.AddColumn(Base, "Year", each Date.Year([Date]), Int64.Type),
    Quarter = Table.AddColumn(Year, "Quarter", each "Q" & Text.From(Date.QuarterOfYear([Date])), type text),
    MonthNo = Table.AddColumn(Quarter, "Month number", each Date.Month([Date]), Int64.Type),
    Month = Table.AddColumn(MonthNo, "Month", each Date.ToText([Date], "MMM", "en-US"), type text),
    YearMonth = Table.AddColumn(Month, "Year month", each Date.ToText([Date], "yyyy-MM"), type text),
    MonthStart = Table.AddColumn(YearMonth, "Month start", each Date.StartOfMonth([Date]), type date)
in
    MonthStart
'''

# --------------------------------------------------------------------------- columns
# (name, dataType, formatString, hidden, sortBy)
COLUMNS = {
    "Deals": [
        ("Deal ID", "int64", "0", False, None), ("Deal name", "string", None, False, None),
        ("Company ID", "int64", "0", True, None), ("Owner ID", "int64", "0", True, None),
        ("Partner ID", "int64", "0", True, None), ("Channel", "string", None, True, None),
        ("Deal type", "string", None, False, None), ("Product", "string", None, False, None),
        ("Created date", "dateTime", "dd/mm/yyyy", False, None), ("Close date", "dateTime", "dd/mm/yyyy", False, None),
        ("Stage", "string", None, False, None),
        ("Last open stage", "string", None, False, "Last open stage order"),
        ("Last open stage order", "int64", "0", True, None),
        ("Is closed", "boolean", None, False, None), ("Is won", "boolean", None, False, None),
        ("Lost reason", "string", None, False, None), ("Employees covered", "int64", "#,0", False, None),
        ("Amount", "double", EURO, False, None),
        ("Status", "string", None, False, "Status order"), ("Status order", "int64", "0", True, None),
        ("Cycle days", "int64", "0", False, None), ("Age days", "int64", "0", False, None),
        ("Days in current stage", "int64", "0", False, None),
    ],
    "StageHistory": [
        ("Deal ID", "int64", "0", True, None), ("Stage", "string", None, False, "Stage order"),
        ("Entered date", "dateTime", "dd/mm/yyyy", False, None), ("Exited date", "dateTime", "dd/mm/yyyy", False, None),
        ("Stage order", "int64", "0", True, None), ("Days in stage", "int64", "0", False, None),
    ],
    "Companies": [
        ("Company ID", "int64", "0", True, None), ("Company name", "string", None, False, None),
        ("Sector", "string", None, False, None), ("Region", "string", None, False, None),
        ("Employees", "int64", "#,0", False, None),
        ("Size segment", "string", None, False, "Segment order"), ("Segment order", "int64", "0", True, None),
    ],
    "Partners": [
        ("Partner ID", "int64", "0", True, None), ("Partner name", "string", None, False, None),
        ("Partner type", "string", None, False, None), ("Partner region", "string", None, False, None),
        ("Onboarded date", "dateTime", "dd/mm/yyyy", False, None),
    ],
    "Owners": [
        ("Owner ID", "int64", "0", True, None), ("Owner", "string", None, False, None), ("Team", "string", None, False, None),
    ],
    "Targets": [
        ("Month", "dateTime", "mmm yyyy", True, None), ("Channel", "string", None, True, None),
        ("Target", "double", EURO, True, None),
    ],
    "Channels": [
        ("Channel", "string", None, False, "Channel order"), ("Channel order", "int64", "0", True, None),
    ],
    "Date": [
        ("Date", "dateTime", "dd/mm/yyyy", False, None), ("Year", "int64", "0", False, None),
        ("Quarter", "string", None, False, None), ("Month number", "int64", "0", True, None),
        ("Month", "string", None, False, "Month number"), ("Year month", "string", None, False, None),
        ("Month start", "dateTime", "mmm yyyy", False, None),
    ],
}

QUERIES = {"Deals": M_DEALS, "StageHistory": M_HISTORY, "Companies": M_COMPANIES, "Partners": M_PARTNERS,
           "Owners": M_OWNERS, "Targets": M_TARGETS, "Channels": M_CHANNELS, "Date": M_DATE}

RELATIONSHIPS = [
    # (from, to, active)
    ("Deals.'Close date'", "'Date'.Date", True),
    ("Deals.'Created date'", "'Date'.Date", False),
    ("Deals.'Company ID'", "Companies.'Company ID'", True),
    ("Deals.'Owner ID'", "Owners.'Owner ID'", True),
    ("Deals.'Partner ID'", "Partners.'Partner ID'", True),
    ("Deals.Channel", "Channels.Channel", True),
    ("StageHistory.'Deal ID'", "Deals.'Deal ID'", True),
    ("Targets.Month", "'Date'.Date", True),
    ("Targets.Channel", "Channels.Channel", True),
]

# --------------------------------------------------------------------------- measures
SNAP = f"DATE ( {SNAPSHOT[0]}, {SNAPSHOT[1]}, {SNAPSHOT[2]} )"
WON = "Deals[Is won] = TRUE ()"
OPEN = "Deals[Is closed] = FALSE ()"

# (name, folder, format, description, DAX)
MEASURES = [
    ("Snapshot date", "Info", "dd/mm/yyyy",
     "Date the data was extracted. Used instead of TODAY() because the dataset is frozen.",
     SNAP),
    ("Deal count", "Volume", "#,0", "Number of deals in the current filter context, by close date.",
     "COUNTROWS ( Deals )"),
    ("Deals created", "Volume", "#,0",
     "Deals by creation date. Activates the inactive relationship on Created date.",
     "CALCULATE ( COUNTROWS ( Deals ), USERELATIONSHIP ( Deals[Created date], 'Date'[Date] ) )"),
    ("Won deals", "Volume", "#,0", "Deals closed as won.", f"CALCULATE ( COUNTROWS ( Deals ), {WON} )"),
    ("Closed deals", "Volume", "#,0", "Deals closed, won or lost.",
     "CALCULATE ( COUNTROWS ( Deals ), Deals[Is closed] = TRUE () )"),
    ("Lost deals", "Volume", "#,0", "Deals closed as lost.", 'CALCULATE ( COUNTROWS ( Deals ), Deals[Status] = "Lost" )'),
    ("Win rate", "Conversion", PCT,
     "Won deals over closed deals. Open deals are left out on purpose: they haven't had the chance to be won yet.",
     "DIVIDE ( [Won deals], [Closed deals] )"),
    ("Bookings", "Revenue", EURO, "Annual value of won deals, by close date.",
     f"CALCULATE ( SUM ( Deals[Amount] ), {WON} )"),
    ("Avg deal size", "Revenue", EURO, "Average annual value of a won deal.",
     f"CALCULATE ( AVERAGE ( Deals[Amount] ), {WON} )"),
    ("Bookings LY", "Revenue", EURO, "Bookings in the same period one year earlier.",
     "CALCULATE ( [Bookings], SAMEPERIODLASTYEAR ( 'Date'[Date] ) )"),
    ("Bookings YoY %", "Revenue", PCT, "Change of bookings against the same period last year.",
     "DIVIDE ( [Bookings] - [Bookings LY], [Bookings LY] )"),
    ("Bookings YTD", "Revenue", EURO,
     "Bookings from 1 January to the snapshot date of the snapshot year.",
     f"""VAR snap = {SNAP}
RETURN
    CALCULATE ( [Bookings], DATESBETWEEN ( 'Date'[Date], DATE ( YEAR ( snap ), 1, 1 ), snap ) )"""),
    ("Bookings YTD LY", "Revenue", EURO,
     "Same window as Bookings YTD, one year earlier. Like for like, so a partial year is never compared with a full one.",
     f"""VAR snap = {SNAP}
RETURN
    CALCULATE ( [Bookings], DATESBETWEEN ( 'Date'[Date], DATE ( YEAR ( snap ) - 1, 1, 1 ), EDATE ( snap, -12 ) ) )"""),
    ("Bookings YTD vs LY %", "Revenue", PCT, "Year to date growth, like for like.",
     "DIVIDE ( [Bookings YTD] - [Bookings YTD LY], [Bookings YTD LY] )"),
    ("Target", "Targets", EURO, "Monthly sales target by channel.", "SUM ( Targets[Target] )"),
    ("Target YTD", "Targets", EURO, "Target from 1 January to the end of the snapshot month.",
     f"""VAR snap = {SNAP}
RETURN
    CALCULATE ( [Target], DATESBETWEEN ( 'Date'[Date], DATE ( YEAR ( snap ), 1, 1 ), snap ) )"""),
    ("Attainment %", "Targets", PCT, "Bookings over target in the current context.",
     "DIVIDE ( [Bookings], [Target] )"),
    ("Attainment YTD %", "Targets", PCT, "Year to date bookings over year to date target.",
     "DIVIDE ( [Bookings YTD], [Target YTD] )"),
    ("Median sales cycle (days)", "Conversion", "0",
     "Median days from creation to close for won deals. Median, not average: a few deals that drag on for a year would distort the mean.",
     f"CALCULATE ( MEDIAN ( Deals[Cycle days] ), {WON} )"),
    ("Open deals", "Pipeline", "#,0", "Deals still open at the snapshot date. Ignores date filters, since open deals have no close date.",
     f"CALCULATE ( COUNTROWS ( Deals ), {OPEN}, REMOVEFILTERS ( 'Date' ) )"),
    ("Open pipeline", "Pipeline", EURO, "Total value of open deals.",
     f"CALCULATE ( SUM ( Deals[Amount] ), {OPEN}, REMOVEFILTERS ( 'Date' ) )"),
    ("Stage win probability", "Pipeline", PCT,
     "Share of closed deals that were won, among those that reached the stage in context. Computed on the whole history, ignoring slicers, so it stays stable.",
     """VAR s = SELECTEDVALUE ( Deals[Last open stage] )
VAR reached =
    CALCULATETABLE ( VALUES ( StageHistory[Deal ID] ), REMOVEFILTERS ( Deals ), StageHistory[Stage] = s )
VAR closedReached =
    CALCULATE ( COUNTROWS ( Deals ), REMOVEFILTERS ( Deals ), TREATAS ( reached, Deals[Deal ID] ), Deals[Is closed] = TRUE () )
VAR wonReached =
    CALCULATE ( COUNTROWS ( Deals ), REMOVEFILTERS ( Deals ), TREATAS ( reached, Deals[Deal ID] ), Deals[Is won] = TRUE () )
RETURN
    DIVIDE ( wonReached, closedReached )"""),
    ("Weighted pipeline", "Pipeline", EURO,
     "Open value multiplied by the historical win probability of the stage each deal is in. A deal in Discovery counts much less than one in Negotiation.",
     """SUMX (
    CALCULATETABLE ( VALUES ( Deals[Last open stage] ), REMOVEFILTERS ( 'Date' ) ),
    [Stage win probability] * [Open pipeline]
)"""),
    ("Target Q4 remaining", "Pipeline", EURO, "Target for the months after the snapshot date, to the end of the year.",
     f"""VAR snap = {SNAP}
RETURN
    CALCULATE ( [Target], DATESBETWEEN ( 'Date'[Date], snap + 1, DATE ( YEAR ( snap ), 12, 31 ) ) )"""),
    ("Pipeline coverage", "Pipeline", '0.00"x"',
     "Weighted pipeline over the target still to reach this year. Below 1 means the open deals alone won't close the gap.",
     "DIVIDE ( [Weighted pipeline], [Target Q4 remaining] )"),
    ("Stage p90 days", "Pipeline", "0",
     "90th percentile of the days closed deals spent in the stage in context. Used to spot open deals that are stuck.",
     """VAR s = SELECTEDVALUE ( Deals[Last open stage] )
RETURN
    CALCULATE (
        PERCENTILE.INC ( StageHistory[Days in stage], 0.9 ),
        REMOVEFILTERS ( Deals ),
        StageHistory[Stage] = s,
        NOT ISBLANK ( StageHistory[Exited date] )
    )"""),
    ("Stuck deals", "Pipeline", "#,0",
     "Open deals that have been in their current stage longer than 90% of the deals before them.",
     f"""SUMX (
    CALCULATETABLE ( VALUES ( Deals[Last open stage] ), REMOVEFILTERS ( 'Date' ) ),
    VAR p90 = [Stage p90 days]
    RETURN
        CALCULATE ( COUNTROWS ( Deals ), {OPEN}, Deals[Days in current stage] > p90, REMOVEFILTERS ( 'Date' ) )
)"""),
    ("Stuck colour", "Formatting", None, "Background colour for the open deals table: red when the deal is stuck.",
     """VAR d = MAX ( Deals[Days in current stage] )
RETURN
    IF ( NOT ISBLANK ( d ) && d > [Stage p90 days], "#F4C7CC" )"""),
    ("Deals reached stage", "Funnel", "#,0",
     "Distinct deals that entered the stage in context, by creation date of the deal.",
     "CALCULATE ( DISTINCTCOUNT ( StageHistory[Deal ID] ), USERELATIONSHIP ( Deals[Created date], 'Date'[Date] ) )"),
    ("Stage conversion %", "Funnel", PCT, "Deals that reached this stage over deals that reached the previous one.",
     """VAR stageNo = SELECTEDVALUE ( StageHistory[Stage order] )
VAR reachedThis = [Deals reached stage]
VAR reachedPrevious =
    CALCULATE ( [Deals reached stage], REMOVEFILTERS ( StageHistory[Stage], StageHistory[Stage order] ), StageHistory[Stage order] = stageNo - 1 )
RETURN
    IF ( stageNo > 1 && stageNo <= 5, DIVIDE ( reachedThis, reachedPrevious ) )"""),
    ("Partner share %", "Partners", PCT, "Share of partner bookings brought by the partner in context.",
     'DIVIDE ( [Bookings], CALCULATE ( [Bookings], REMOVEFILTERS ( Partners ), Deals[Channel] = "Partner" ) )'),
    ("Partner rank", "Partners", "0", "Rank of the partner by bookings.",
     """IF (
    HASONEVALUE ( Partners[Partner name] ) && NOT ISBLANK ( [Bookings] ),
    RANKX ( ALLNOBLANKROW ( Partners ), [Bookings] )
)"""),
    ("Partner cumulative %", "Partners", PCT,
     "Cumulative share of partner bookings, from the best partner down. The classic Pareto line.",
     """VAR thisPartner = [Bookings]
VAR ranked = ADDCOLUMNS ( ALLNOBLANKROW ( Partners ), "@b", [Bookings] )
VAR cumulated = SUMX ( FILTER ( ranked, [@b] >= thisPartner ), [@b] )
RETURN
    IF ( HASONEVALUE ( Partners[Partner name] ) && NOT ISBLANK ( thisPartner ), DIVIDE ( cumulated, SUMX ( ranked, [@b] ) ) )"""),
    ("Last refresh", "Info", None, "Text shown in the page header.",
     f'"Data as of " & FORMAT ( {SNAP}, "d mmm yyyy" )'),
]


# --------------------------------------------------------------------------- TMDL

def tmdl_column(name, dtype, fmt, hidden, sort, is_key=False):
    lines = [f"\tcolumn {q(name)}", f"\t\tdataType: {dtype}"]
    if fmt:
        lines.append(f"\t\tformatString: {fmt}")
    if hidden:
        lines.append("\t\tisHidden")
    if is_key:
        lines.append("\t\tisKey")
    lines += [f"\t\tlineageTag: {tag()}", "\t\tsummarizeBy: none", f"\t\tsourceColumn: {name}"]
    if sort:
        lines.append(f"\t\tsortByColumn: {q(sort)}")
    lines += ["", "\t\tannotation SummarizationSetBy = User", ""]
    return "\n".join(lines)


def tmdl_measure(name, folder, fmt, description, dax):
    lines = [f"\t/// {description}"]
    if "\n" in dax:
        lines += [f"\tmeasure {q(name)} =", indent(dax, 3)]
    else:
        lines.append(f"\tmeasure {q(name)} = {dax}")
    if fmt:
        lines.append(f"\t\tformatString: {fmt}")
    lines += [f"\t\tdisplayFolder: {folder}", f"\t\tlineageTag: {tag()}", ""]
    return "\n".join(lines)


def tmdl_table(name, columns, m, measures=(), date_table=False):
    out = [f"table {q(name)}", f"\tlineageTag: {tag()}"]
    if date_table:
        out.append("\tdataCategory: Time")
    out.append("")
    out += [tmdl_measure(*ms) for ms in measures]
    for c in columns:
        out.append(tmdl_column(*c, is_key=date_table and c[0] == "Date"))
    out += [f"\tpartition {q(name)} = m", "\t\tmode: import", "\t\tsource =", indent(m, 4), "",
            "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(out)


def build_model():
    if SM.exists():
        shutil.rmtree(SM)
    d = SM / "definition"
    write_json(SM / "definition.pbism", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {}})
    write(d / "database.tmdl", "database\n\tcompatibilityLevel: 1601\n\n")
    tables = list(QUERIES)
    write(d / "model.tmdl", "\n".join([
        "model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US", "\tdataAccessOptions", "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", "",
        "annotation __PBI_TimeIntelligenceEnabled = 0", "",
        "annotation PBI_QueryOrder = " + json.dumps(["DataFolder", "SnapshotDate"] + tables), "",
        *[f"ref table {q(t)}" for t in tables], ""]))
    # Default: read the CSVs straight from GitHub, so the project opens anywhere without setup.
    # Pass --local to read them from this folder instead (useful while changing the data).
    data_folder = str(ROOT / "data") + "\\" if "--local" in sys.argv else GITHUB_DATA
    y, mth, day = SNAPSHOT
    write(d / "expressions.tmdl", "\n".join([
        f'expression DataFolder = "{data_folder}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]',
        f"\tlineageTag: {tag()}", "", "\tannotation PBI_ResultType = Text", "",
        f'expression SnapshotDate = #date({y}, {mth}, {day}) meta [IsParameterQuery=true, Type="Date", IsParameterQueryRequired=true]',
        f"\tlineageTag: {tag()}", "", "\tannotation PBI_ResultType = Date", ""]))
    rel = []
    for frm, to, active in RELATIONSHIPS:
        rel += [f"relationship {tag()}"]
        if not active:
            rel.append("\tisActive: false")
        rel += [f"\tfromColumn: {frm}", f"\ttoColumn: {to}", ""]
    write(d / "relationships.tmdl", "\n".join(rel))
    for t, m in QUERIES.items():
        write(d / "tables" / f"{t}.tmdl",
              tmdl_table(t, COLUMNS[t], m, MEASURES if t == "Deals" else (), date_table=(t == "Date")))


# --------------------------------------------------------------------------- report (PBIR)
SCH = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
INK, MUTED = "#1B2A41", "#5B6573"
BLUE, GREEN, RED, AMBER, GREY = "#2563A8", "#2E8B57", "#C8475B", "#D99A2B", "#9AA3AE"
CHANNEL_COLOURS = {"Direct": BLUE, "Partner": "#3FA7A0", "Referral": AMBER}

THEME = {
    "name": "Sales pipeline",
    "dataColors": [BLUE, "#3FA7A0", AMBER, "#7A5CB8", RED, GREEN, "#5C7A99", GREY],
    "foreground": INK, "foregroundNeutralSecondary": MUTED, "background": "#FFFFFF",
    "backgroundLight": "#F4F6F9", "tableAccent": BLUE, "good": GREEN, "bad": RED, "neutral": AMBER,
    "textClasses": {
        "callout": {"fontSize": 22, "fontFace": "Segoe UI Semibold", "color": INK},
        "title": {"fontSize": 11, "fontFace": "Segoe UI Semibold", "color": INK},
        "header": {"fontSize": 10, "fontFace": "Segoe UI Semibold", "color": INK},
        "label": {"fontSize": 9, "fontFace": "Segoe UI", "color": MUTED},
    },
    "visualStyles": {"*": {"*": {
        "border": [{"show": False}],
        "background": [{"show": True, "transparency": 0}],
        "dropShadow": [{"show": False}],
    }}, "page": {"*": {"background": [{"color": {"solid": {"color": "#F4F6F9"}}, "transparency": 0}]}}},
}


def lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def col(prop, ent="Deals"):
    return {"Column": {"Expression": {"SourceRef": {"Entity": ent}}, "Property": prop}}


def mea(prop, ent="Deals"):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": ent}}, "Property": prop}}


def proj(f, name=None):
    k = "Column" if "Column" in f else "Measure"
    ent, prop = f[k]["Expression"]["SourceRef"]["Entity"], f[k]["Property"]
    p = {"field": f, "queryRef": f"{ent}.{prop}", "nativeQueryRef": prop}
    if name:
        p["displayName"] = name
    return p


def filter_in(name, ent, prop, values):
    return {"name": name, "field": col(prop, ent), "type": "Categorical",
            "filter": {"Version": 2, "From": [{"Name": "t", "Entity": ent, "Type": 0}],
                       "Where": [{"Condition": {"In": {
                           "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": prop}}],
                           "Values": [[{"Literal": {"Value": f"'{v}'"}}] for v in values]}}}]}}


def series_colours(prop, mapping, ent="Deals"):
    return [{"properties": {"fill": {"solid": {"color": lit(f"'{c}'")}}},
             "selector": {"data": [{"scopeId": {"Comparison": {"ComparisonKind": 0, "Left": col(prop, ent),
                                                               "Right": {"Literal": {"Value": f"'{v}'"}}}}}]}}
            for v, c in mapping.items()]


def single_colour(c):
    return [{"properties": {"fill": {"solid": {"color": lit(f"'{c}'")}}}}]


class Page:
    def __init__(self, name, title):
        self.name, self.title, self.visuals = name, title, []

    def add(self, vtype, x, y, w, h, roles=None, title=None, objects=None, filters=None, sort=None):
        n = f"v{len(self.visuals) + 1:02d}"
        v = {"$schema": f"{SCH}/visualContainer/1.3.0/schema.json", "name": n,
             "position": {"x": x, "y": y, "z": len(self.visuals) * 1000, "height": h, "width": w,
                          "tabOrder": len(self.visuals) * 1000},
             "visual": {"visualType": vtype, "drillFilterOtherVisuals": True}}
        if roles:
            qs = {r: {"projections": [proj(*f) if isinstance(f, tuple) else proj(f) for f in fs]}
                  for r, fs in roles.items()}
            v["visual"]["query"] = {"queryState": qs}
            if sort:
                v["visual"]["query"]["sortDefinition"] = {
                    "sort": [{"field": f, "direction": d} for f, d in sort], "isDefaultSort": True}
        if objects:
            v["visual"]["objects"] = objects
        vco = {"title": [{"properties": {"show": lit("true" if title else "false"),
                                         **({"text": lit(f"'{title}'")} if title else {})}}]}
        if vtype == "textbox":
            vco = {"background": [{"properties": {"show": lit("false")}}]}
        v["visual"]["visualContainerObjects"] = vco
        if filters:
            v["filterConfig"] = {"filters": filters}
        self.visuals.append(v)

    def text(self, x, y, w, h, text, size=20, bold=True, colour=INK):
        self.add("textbox", x, y, w, h, objects={"general": [{"properties": {"paragraphs": [{"textRuns": [
            {"value": text, "textStyle": {"fontWeight": "bold" if bold else "normal", "fontSize": f"{size}pt",
                                          "color": colour}}]}]}}]})

    def slicer(self, x, y, w, h, field, title):
        self.add("slicer", x, y, w, h, {"Values": [(field, title)]},
                 objects={"data": [{"properties": {"mode": lit("'Dropdown'")}}]})

    def card(self, x, y, w, h, measure, title):
        self.add("card", x, y, w, h, {"Values": [mea(measure)]}, title,
                 objects={"labels": [{"properties": {"fontSize": lit("20D"), "labelDisplayUnits": lit("1D")}}],
                          "categoryLabels": [{"properties": {"show": lit("false")}}]})

    def table(self, x, y, w, h, fields, title, filters=None, sort=None, extra=None):
        objects = {"total": [{"properties": {"totals": lit("false")}}]}
        objects.update(extra or {})
        self.add("tableEx", x, y, w, h, {"Values": fields}, title, objects=objects, filters=filters, sort=sort)

    def header(self, subtitle):
        self.text(20, 6, 900, 44, self.title, 18)
        self.text(20, 42, 900, 30, subtitle, 10, False, MUTED)
        self.add("card", 1000, 16, 260, 40, {"Values": [mea("Last refresh")]},
                 objects={"labels": [{"properties": {"fontSize": lit("10D"),
                                                     "color": {"solid": {"color": lit(f"'{MUTED}'")}}}}],
                          "categoryLabels": [{"properties": {"show": lit("false")}}]})

    def standard_slicers(self):
        for i, (field, title) in enumerate([(col("Year", "Date"), "Year"), (col("Channel", "Channels"), "Channel"),
                                            (col("Product"), "Product"), (col("Size segment", "Companies"), "Segment")]):
            self.slicer(20 + i * 312, 78, 300, 54, field, title)


def labels_on():
    return [{"properties": {"show": lit("true")}}]


def build_pages():
    pages = []

    # 1. Overview -----------------------------------------------------------
    p = Page("overview", "Sales overview")
    p.header("Where we stand against target, and how this year compares with the last one")
    p.standard_slicers()
    for i, (m, t) in enumerate([("Bookings YTD", "Bookings YTD"), ("Bookings YTD vs LY %", "vs last year YTD"),
                                ("Attainment YTD %", "Target attainment YTD"), ("Win rate", "Win rate"),
                                ("Avg deal size", "Avg deal size"), ("Weighted pipeline", "Weighted pipeline")]):
        p.card(20 + i * 208, 142, 200, 92, m, t)
    p.add("lineClusteredColumnComboChart", 20, 246, 820, 458,
          {"Category": [col("Year month", "Date")], "Y": [mea("Bookings")], "Y2": [mea("Target")]},
          "Monthly bookings against target",
          objects={"dataPoint": single_colour(BLUE),
                   "categoryAxis": [{"properties": {"axisType": lit("'Categorical'")}}]},
          sort=[(col("Year month", "Date"), "Ascending")])
    p.add("clusteredBarChart", 852, 246, 408, 224,
          {"Category": [col("Channel", "Channels")], "Y": [mea("Win rate")], "Tooltips": [mea("Closed deals")]},
          "Win rate by channel",
          objects={"labels": labels_on(), "dataPoint": series_colours("Channel", CHANNEL_COLOURS, "Channels")},
          sort=[(mea("Win rate"), "Descending")])
    p.add("donutChart", 852, 480, 408, 224,
          {"Category": [col("Channel", "Channels")], "Y": [mea("Bookings")]},
          "Bookings by channel",
          objects={"dataPoint": series_colours("Channel", CHANNEL_COLOURS, "Channels"), "labels": labels_on()})
    pages.append(p)

    # 2. Funnel -------------------------------------------------------------
    p = Page("funnel", "Funnel and sales cycle")
    p.header("How many deals survive each stage, why the others are lost, and how long it takes")
    p.standard_slicers()
    p.add("funnel", 20, 142, 410, 562,
          {"Category": [col("Stage", "StageHistory")], "Y": [mea("Deals reached stage")]},
          "Deals reaching each stage (by creation date)",
          objects={"dataPoint": single_colour(BLUE), "labels": labels_on()},
          filters=[filter_in("f_funnel", "StageHistory", "Stage",
                             ["Discovery", "Qualified", "Proposal", "Negotiation", "Closed Won"])],
          sort=[(col("Stage", "StageHistory"), "Ascending")])
    p.add("clusteredColumnChart", 442, 142, 400, 270,
          {"Category": [col("Stage", "StageHistory")], "Y": [mea("Stage conversion %")]},
          "Conversion from the previous stage",
          objects={"dataPoint": single_colour("#3FA7A0"), "labels": labels_on()},
          filters=[filter_in("f_conv", "StageHistory", "Stage",
                             ["Qualified", "Proposal", "Negotiation", "Closed Won"])],
          sort=[(col("Stage", "StageHistory"), "Ascending")])
    p.add("clusteredColumnChart", 854, 142, 406, 270,
          {"Category": [col("Size segment", "Companies")], "Y": [mea("Median sales cycle (days)")]},
          "Median sales cycle by company size (days)",
          objects={"dataPoint": single_colour(AMBER), "labels": labels_on()},
          sort=[(col("Size segment", "Companies"), "Ascending")])
    p.add("barChart", 442, 424, 818, 280,
          {"Category": [col("Last open stage")], "Series": [col("Lost reason")], "Y": [mea("Lost deals")]},
          "Lost deals: stage where they were lost and reason given",
          objects={"labels": labels_on()},
          filters=[filter_in("f_lost", "Deals", "Status", ["Lost"])],
          sort=[(col("Last open stage"), "Ascending")])
    pages.append(p)

    # 3. Partners -----------------------------------------------------------
    p = Page("partners", "Partner contribution")
    p.header("A handful of partners bring most of the business: the line shows the cumulative share")
    p.slicer(20, 78, 300, 54, col("Year", "Date"), "Year")
    p.slicer(332, 78, 300, 54, col("Partner type", "Partners"), "Partner type")
    p.slicer(644, 78, 300, 54, col("Product"), "Product")
    p.add("lineClusteredColumnComboChart", 20, 142, 1240, 300,
          {"Category": [col("Partner name", "Partners")], "Y": [mea("Bookings")], "Y2": [mea("Partner cumulative %")]},
          "Partner bookings, Pareto view",
          objects={"dataPoint": single_colour("#3FA7A0")},
          filters=[filter_in("f_pch", "Deals", "Channel", ["Partner"])],
          sort=[(mea("Bookings"), "Descending")])
    p.table(20, 454, 1240, 250,
            [(mea("Partner rank"), "Rank"), col("Partner name", "Partners"), col("Partner type", "Partners"),
             col("Partner region", "Partners"), (mea("Deals created"), "Deals created"), mea("Won deals"),
             mea("Win rate"), mea("Bookings"), (mea("Partner share %"), "Share"), mea("Avg deal size")],
            "Partner scorecard",
            filters=[filter_in("f_pch2", "Deals", "Channel", ["Partner"])],
            sort=[(mea("Bookings"), "Descending")])
    pages.append(p)

    # 4. Pipeline -----------------------------------------------------------
    p = Page("pipeline", "Open pipeline")
    p.header("Open deals at the snapshot date, weighted by how often deals in the same stage end up won. Red rows are stuck.")
    p.slicer(20, 78, 300, 54, col("Channel", "Channels"), "Channel")
    p.slicer(332, 78, 300, 54, col("Owner", "Owners"), "Owner")
    p.slicer(644, 78, 300, 54, col("Product"), "Product")
    for i, (m, t) in enumerate([("Open deals", "Open deals"), ("Open pipeline", "Open pipeline"),
                                ("Weighted pipeline", "Weighted pipeline"), ("Pipeline coverage", "Coverage of Q4 target"),
                                ("Stuck deals", "Stuck deals")]):
        p.card(20 + i * 250, 142, 240, 92, m, t)
    p.add("clusteredBarChart", 20, 246, 400, 458,
          {"Category": [col("Last open stage")], "Y": [mea("Open pipeline"), mea("Weighted pipeline")]},
          "Open vs weighted value by stage",
          objects={"labels": labels_on()},
          filters=[filter_in("f_open", "Deals", "Status", ["Open"])],
          sort=[(col("Last open stage"), "Ascending")])
    highlight = [{"properties": {"backColor": {"solid": {"color": {"expr": mea("Stuck colour")}}}},
                  "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"Deals.{c}"}}
                 for c in ("Days in current stage", "Last open stage")]
    p.table(432, 246, 828, 458,
            [col("Deal name"), col("Owner", "Owners"), col("Channel", "Channels"), col("Last open stage"),
             col("Days in current stage"), col("Age days"), col("Amount"),
             (mea("Stage win probability"), "Win prob.")],
            "Open deals",
            filters=[filter_in("f_open2", "Deals", "Status", ["Open"])],
            sort=[(col("Days in current stage"), "Descending")],
            extra={"values": highlight})
    pages.append(p)
    return pages


def build_report():
    if RP.exists():
        shutil.rmtree(RP)
    d = RP / "definition"
    write_json(RP / "definition.pbir", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/1.0.0/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    write_json(d / "version.json", {"$schema": f"{SCH}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    write_json(d / "report.json", {
        "$schema": f"{SCH}/report/1.2.0/schema.json",
        "themeCollection": {"customTheme": {"name": "SalesTheme.json", "reportVersionAtImport": "5.61",
                                            "type": "RegisteredResources"}},
        "layoutOptimization": "None",
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                              "items": [{"name": "SalesTheme.json", "path": "SalesTheme.json", "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True,
                     "allowChangeFilterTypes": True, "useEnhancedTooltips": True, "useDefaultAggregateDisplayName": True}})
    write_json(RP / "StaticResources" / "RegisteredResources" / "SalesTheme.json", THEME)
    pages = build_pages()
    write_json(d / "pages" / "pages.json", {
        "$schema": f"{SCH}/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p.name for p in pages], "activePageName": pages[0].name})
    for p in pages:
        pdir = d / "pages" / p.name
        write_json(pdir / "page.json", {
            "$schema": f"{SCH}/page/1.3.0/schema.json", "name": p.name, "displayName": p.title,
            "displayOption": "FitToPage", "height": 720, "width": 1280})
        for v in p.visuals:
            write_json(pdir / "visuals" / v["name"] / "visual.json", v)


def build_pbip():
    write_json(ROOT / f"{NAME}.pbip", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True}})


def build_docs():
    lines = ["# DAX measures", "",
             "Generated from `build/generate_pbip.py`, the same list that writes the model, so it is always in sync.", ""]
    folder = None
    for name, fold, fmt, desc, dax in sorted(MEASURES, key=lambda m: m[1]):
        if fold != folder:
            lines += [f"## {fold}", ""]
            folder = fold
        lines += [f"### {name}", "", desc, "", "```dax", f"{name} =", dax, "```", ""]
    write(ROOT / "docs" / "measures.md", "\n".join(lines))


if __name__ == "__main__":
    build_model()
    build_report()
    build_pbip()
    build_docs()
    print(f"Project generated: {NAME}.pbip ({len(MEASURES)} measures, "
          f"{len(QUERIES)} tables, {len(RELATIONSHIPS)} relationships)")
