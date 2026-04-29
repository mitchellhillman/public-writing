#!/usr/bin/env python3
"""Generate economic charts for US economic conditions post.
Sources:
  - IRS SOI Table 1.1 (2022): income distribution by Adjusted Gross Income bracket
  - Federal Reserve SCF 2022: household net worth distribution
  - Census ACS 2023 (B01001): working-age population 18-74
"""

import urllib.request, json, csv, zipfile, io, xlrd, math

# ── colours ──────────────────────────────────────────────────────────────────
BLUE    = '#454545'
RED     = '#E4493A'
GRID    = '#D4D4D4'
TEXT    = '#1A1A1A'
SUBTEXT = '#767676'
BG      = '#EBEBEB'
FONT    = 'Inter, -apple-system, BlinkMacSystemFont, sans-serif'

# ── helpers ──────────────────────────────────────────────────────────────────
def fmt_n(n):
    if abs(n) >= 1_000_000:
        v = n / 1_000_000
        return f'{v:.1f}m' if v != int(v) else f'{int(v)}m'
    if abs(n) >= 1_000:
        v = n / 1_000
        return f'{v:.0f}k'
    return str(int(n))

def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

# ── IRS raw label → short label ──────────────────────────────────────────────
def fmt_irs_label(raw):
    r = raw.strip()
    if 'No adjusted gross income' in r:  return 'No income'
    if r == '$1 under $5,000':           return 'Under $5k'
    if r == '$5,000 under $10,000':      return '$5–10k'
    if r == '$10,000 under $15,000':     return '$10–15k'
    if r == '$15,000 under $20,000':     return '$15–20k'
    if r == '$20,000 under $25,000':     return '$20–25k'
    if r == '$25,000 under $30,000':     return '$25–30k'
    if r == '$30,000 under $40,000':     return '$30–40k'
    if r == '$40,000 under $50,000':     return '$40–50k'
    if r == '$50,000 under $75,000':     return '$50–75k'
    if r == '$75,000 under $100,000':    return '$75–100k'
    if r == '$100,000 under $200,000':   return '$100–200k'
    if r == '$200,000 under $500,000':   return '$200–500k'
    if r == '$500,000 under $1,000,000': return '$500k–1m'
    if r == '$1,000,000 under $1,500,000': return '$1–1.5m'
    if r == '$1,500,000 under $2,000,000': return '$1.5–2m'
    if r == '$2,000,000 under $5,000,000': return '$2–5m'
    if r == '$5,000,000 under $10,000,000': return '$5–10m'
    if r == '$10,000,000 or more':       return 'Over $10m'
    return r

# ── net worth label cleanup ───────────────────────────────────────────────────
NW_LABELS = [
    '-$500k to -$100k',
    '-$100k to $0',
    '$0–25k',
    '$25–50k',
    '$50–100k',
    '$100–250k',
    '$250–500k',
    '$500k–1m',
    '$1–2.5m',
    '$2.5–5m',
    'Over $5m',
]

# ════════════════════════════════════════════════════════════════════════════
# DATA FETCH
# ════════════════════════════════════════════════════════════════════════════

print('Fetching IRS SOI 2022 income data...')
IRS_URL = 'https://www.irs.gov/pub/irs-soi/22in11si.xls'
req = urllib.request.Request(IRS_URL, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=15) as r:
    irs_bytes = r.read()

wb = xlrd.open_workbook(file_contents=irs_bytes)
ws = wb.sheets()[0]

irs_income = []   # (label, count, agi_thousands)
for i in range(10, 29):
    row = ws.row_values(i)
    raw = str(row[0]).strip()
    try:
        count = int(float(row[1]))
        try:
            agi = float(row[3])
        except (ValueError, TypeError, IndexError):
            agi = 0.0
    except (ValueError, TypeError):
        continue
    if raw and count > 0:
        irs_income.append((fmt_irs_label(raw), count, agi))

total_filers = int(float(ws.row_values(9)[1]))
total_agi    = float(ws.row_values(9)[3])   # thousands of dollars
print(f'  Total filers: {total_filers:,}')

# Median income bracket
cum = 0
median_idx = 0
median_label = ''
for i, (lbl, cnt, _) in enumerate(irs_income):
    cum += cnt
    if cum >= total_filers / 2:
        median_idx = i
        median_label = lbl
        break

# Mode (most common) bracket
mode_idx = max(range(len(irs_income)), key=lambda i: irs_income[i][1])

# Share of filers with $1m+ AGI
m1plus_labels = {'$1–1.5m', '$1.5–2m', '$2–5m', '$5–10m', 'Over $10m'}
m1plus_count  = sum(cnt for lbl, cnt, _ in irs_income if lbl in m1plus_labels)
m1plus_pct    = m1plus_count / total_filers * 100
print(f'  Median income bracket: {median_label}')
print(f'  Filers with $1m+ AGI:  {m1plus_pct:.2f}%')

print('\nFetching SCF 2022 net worth data...')
SCF_URL = 'https://www.federalreserve.gov/econres/files/scfp2022excel.zip'
req = urllib.request.Request(SCF_URL, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=60) as r:
    scf_bytes = r.read()

nw_bounds = [
    (-500_000,    -100_000),
    (-100_000,           0),
    (0,           25_000),
    (25_000,      50_000),
    (50_000,     100_000),
    (100_000,    250_000),
    (250_000,    500_000),
    (500_000,  1_000_000),
    (1_000_000, 2_500_000),
    (2_500_000, 5_000_000),
    (5_000_000,       None),
]
nw_counts  = [0.0] * len(nw_bounds)
nw_asset   = [0.0] * len(nw_bounds)   # weighted sum of assets per bracket
nw_debt    = [0.0] * len(nw_bounds)   # weighted sum of debt per bracket
total_hh   = 0.0

# Income brackets for retirement savings chart
ret_brackets = [
    (None,    25_000,  'Under $25k'),
    (25_000,  50_000,  '$25–50k'),
    (50_000,  75_000,  '$50–75k'),
    (75_000, 100_000,  '$75–100k'),
    (100_000, 200_000, '$100–200k'),
    (200_000, 300_000, '$200–300k'),
    (300_000, 500_000, '$300–500k'),
    (500_000, 750_000, '$500–750k'),
    (750_000, 1_000_000, '$750k–1m'),
    (1_000_000, 5_000_000, '$1–5m'),
    (5_000_000,      None, 'Over $5m'),
]
ret_wgt  = [0.0] * len(ret_brackets)
ret_rsum = [0.0] * len(ret_brackets)   # weighted sum of RETQLIQ

z = zipfile.ZipFile(io.BytesIO(scf_bytes))
with z.open('SCFP2022.csv') as f:
    reader = csv.DictReader(io.TextIOWrapper(f))
    for row in reader:
        nw    = float(row['NETWORTH'])
        wgt   = float(row['WGT'])
        asset = float(row.get('ASSET') or 0)
        debt  = float(row.get('DEBT') or 0)
        retq  = float(row.get('RETQLIQ') or 0)
        inc   = float(row.get('INCOME') or 0)

        for i, (lo, hi) in enumerate(nw_bounds):
            if (lo is None or nw >= lo) and (hi is None or nw < hi):
                nw_counts[i] += wgt
                nw_asset[i]  += wgt * asset
                nw_debt[i]   += wgt * debt
                break

        for j, (lo, hi, _) in enumerate(ret_brackets):
            if (lo is None or inc >= lo) and (hi is None or inc < hi):
                ret_wgt[j]  += wgt
                ret_rsum[j] += wgt * retq
                break

        total_hh += wgt

print(f'  Total weighted households: {total_hh:,.0f}')

# Share of households with net worth below $25k (includes negative and $0-25k brackets)
lt25k_count = sum(nw_counts[i] for i, (lo, hi) in enumerate(nw_bounds) if hi is not None and hi <= 25_000)
lt25k_pct   = lt25k_count / total_hh * 100
print(f'  Households with net worth <$25k: {lt25k_pct:.1f}%')

print('\nFetching Census ACS 2023 population data...')
male_vars   = [f'B01001_{str(i).zfill(3)}E' for i in range(7, 23)]
female_vars = [f'B01001_{str(i).zfill(3)}E' for i in range(31, 47)]
all_vars    = ','.join(male_vars + female_vars)
census_url  = f'https://api.census.gov/data/2023/acs/acs1?get={all_vars}&for=us:1'
with urllib.request.urlopen(census_url, timeout=15) as r:
    census_data = json.loads(r.read())

working_age_pop = sum(int(v) for v in census_data[1][:-1])

# Married Filing Jointly returns cover 2 people each.
# IRS 2022: ~55.5M MFJ returns → add the second person from each joint return.
MFJ_RETURNS  = 55_500_000
adj_filers   = total_filers + MFJ_RETURNS
non_filers   = working_age_pop - adj_filers

print(f'  Working-age population 18-74:  {working_age_pop:,}')
print(f'  IRS returns filed:             {total_filers:,}')
print(f'  + MFJ second filers:           {MFJ_RETURNS:,}')
print(f'  Adjusted people covered:       {adj_filers:,} ({adj_filers/working_age_pop*100:.1f}%)')
print(f'  Estimated non-filers:          {non_filers:,} ({non_filers/working_age_pop*100:.1f}%)')

print('\nFetching BLS unemployment rate...')
BLS_URL = 'https://api.bls.gov/publicAPI/v1/timeseries/data/LNS14000000'
req = urllib.request.Request(BLS_URL, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=15) as r:
    bls_resp = json.loads(r.read())
_bls_series = bls_resp['Results']['series'][0]['data']
unemp_rate     = float(_bls_series[0]['value'])
unemp_date_str = f"{_bls_series[0]['periodName']} {_bls_series[0]['year']}"
print(f'  Unemployment rate: {unemp_rate:.1f}% ({unemp_date_str})')

# ════════════════════════════════════════════════════════════════════════════
# SVG GENERATION
# ════════════════════════════════════════════════════════════════════════════

PAD_X = 15
PAD_Y = 5

def svg_open(w, h):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {w + PAD_X*2} {h + PAD_Y*2}" width="{w + PAD_X*2}" height="{h + PAD_Y*2}" '
            f'style="font-family: {FONT}; background: {BG}; display: block;">\n'
            f'<g transform="translate({PAD_X},{PAD_Y})">\n')

def svg_close():
    return '</g>\n</svg>\n'

# ── shared pyramid geometry ───────────────────────────────────────────────────
# axis_x shifted right of centre to leave room for labels on the left;
# max_half chosen so the widest bar reaches close to the right edge.
W_CHART  = 660
AXIS_X   = 400   # centre spine x position
MAX_HALF = 224   # widest bar = AXIS_X ± 224 → right edge 624, ~30px for label before W_CHART edge

# labels right-aligned just left of the bar zone
LABEL_X  = AXIS_X - MAX_HALF - 6   # = 150

# ── Chart 1: Income distribution ─────────────────────────────────────────────
def chart_income():
    W, H   = W_CHART, 760
    mt, mb = 70, 16

    n     = len(irs_income)
    row_h = (H - mt - mb) / n
    bar_h = row_h

    max_count = max(c for _, c, _ in irs_income)
    def half_len(c): return c / max_count * MAX_HALF

    lines = [svg_open(W, H)]
    lines.append(f'<text x="0" y="26" font-size="14" font-weight="700" '
                 f'fill="{TEXT}">Bee shaped population</text>\n')
    lines.append(f'<text x="0" y="44" font-size="10" font-weight="400" fill="{SUBTEXT}">'
                 f'{total_filers/1e6:.1f}m returns filed  ·  IRS Statistics of Income, Table 1.1, 2022</text>\n')

    for idx, (label, count, _) in enumerate(reversed(irs_income)):
        y         = mt + idx * row_h
        hl        = half_len(count)
        cy        = y + bar_h * 0.63
        is_median = (idx == n - 1 - median_idx)
        is_mode   = (idx == n - 1 - mode_idx)
        fill      = '#888888' if is_median else (RED if is_mode else BLUE)
        bar_mid   = y + bar_h / 2
        lines.append(f'<line x1="0" y1="{y:.1f}" x2="{W}" y2="{y:.1f}" stroke="{GRID}" stroke-width="0.75"/>\n')
        lines.append(f'<rect x="{AXIS_X - hl:.1f}" y="{y:.1f}" width="{hl*2:.1f}" height="{bar_h:.1f}" '
                     f'fill="{fill}"/>\n')
        lines.append(f'<text x="0" y="{cy:.1f}" font-size="10.5" '
                     f'fill="{TEXT}">{esc(label)}</text>\n')
        lines.append(f'<text x="{AXIS_X + hl + 5:.1f}" y="{cy:.1f}" '
                     f'font-size="9" fill="{SUBTEXT}">{fmt_n(count)}</text>\n')
        if is_median:
            lines.append(f'<text x="{AXIS_X}" y="{bar_mid:.1f}" font-size="9" dominant-baseline="middle" '
                         f'text-anchor="middle" fill="{BG}" font-style="italic">median</text>\n')
        if is_mode:
            lines.append(f'<text x="{AXIS_X}" y="{bar_mid:.1f}" font-size="9" dominant-baseline="middle" '
                         f'text-anchor="middle" fill="{BG}" font-style="italic">majority</text>\n')

    lines.append(f'<line x1="0" y1="{H - mb:.1f}" x2="{W}" y2="{H - mb:.1f}" stroke="{GRID}" stroke-width="0.75"/>\n')
    lines.append(svg_close())
    return ''.join(lines)


# ── Chart 2: Net worth distribution ───────────────────────────────────────────
def chart_networth():
    W, H   = W_CHART, 580
    mt, mb = 70, 16

    n     = len(nw_bounds)
    row_h = (H - mt - mb) / n
    bar_h = row_h

    max_count = max(nw_counts)
    def half_len(c): return c / max_count * MAX_HALF

    lines = [svg_open(W, H)]
    lines.append(f'<text x="0" y="26" font-size="14" font-weight="700" '
                 f'fill="{TEXT}">Household Net Worth Distribution, 2022</text>\n')
    lines.append(f'<text x="0" y="44" font-size="10" font-weight="400" fill="{SUBTEXT}">'
                 f'{total_hh/1e6:.0f}m weighted households  ·  Federal Reserve Survey of Consumer Finances 2022</text>\n')

    for idx in range(n):
        i_orig = n - 1 - idx
        label  = NW_LABELS[i_orig]
        count  = nw_counts[i_orig]
        lo, hi = nw_bounds[i_orig]
        y      = mt + idx * row_h
        hl     = half_len(count)
        cy     = y + bar_h * 0.63
        fill   = RED if (hi is not None and hi <= 0) else BLUE

        lines.append(f'<line x1="0" y1="{y:.1f}" x2="{W}" y2="{y:.1f}" stroke="{GRID}" stroke-width="0.75"/>\n')
        lines.append(f'<rect x="{AXIS_X - hl:.1f}" y="{y:.1f}" width="{hl*2:.1f}" height="{bar_h:.1f}" '
                     f'fill="{fill}"/>\n')
        lines.append(f'<text x="0" y="{cy:.1f}" font-size="10.5" '
                     f'fill="{TEXT}">{esc(label)}</text>\n')
        lines.append(f'<text x="{AXIS_X + hl + 5:.1f}" y="{cy:.1f}" '
                     f'font-size="9" fill="{SUBTEXT}">{fmt_n(count)}</text>\n')

    lines.append(f'<line x1="0" y1="{H - mb:.1f}" x2="{W}" y2="{H - mb:.1f}" stroke="{GRID}" stroke-width="0.75"/>\n')
    lines.append(svg_close())
    return ''.join(lines)


# ── Chart 3: Filer share vs. income share (true two-sided pyramid) ─────────────
def chart_income_share():
    W, H   = W_CHART, 760
    mt, mb = 70, 16

    n     = len(irs_income)
    row_h = (H - mt - mb) / n
    bar_h = row_h

    filer_pcts  = [count / total_filers * 100  for _, count, _   in irs_income]
    income_pcts = [max(0, agi / total_agi * 100) for _, _, agi in irs_income]

    max_filer  = max(filer_pcts)
    max_income = max(income_pcts)

    def lhl(i): return filer_pcts[i]  / max_filer  * MAX_HALF
    def rhl(i): return income_pcts[i] / max_income * MAX_HALF

    lines = [svg_open(W, H)]
    lines.append(f'<text x="0" y="26" font-size="14" font-weight="700" '
                 f'fill="{TEXT}">Where Filers Are vs. Where Income Is, 2022</text>\n')
    lines.append(f'<text x="0" y="44" font-size="10" font-weight="400" fill="{SUBTEXT}">'
                 f'Share of total filers (left) and share of total AGI (right) per bracket  ·  IRS SOI Table 1.1</text>\n')

    # side-labels just above bar area
    lines.append(f'<text x="{AXIS_X - 8}" y="{mt - 4}" font-size="9.5" '
                 f'text-anchor="end" fill="{RED}">← share of filers</text>\n')
    lines.append(f'<text x="{AXIS_X + 8}" y="{mt - 4}" font-size="9.5" '
                 f'text-anchor="start" fill="{BLUE}">share of income →</text>\n')

    for idx, (label, count, agi) in enumerate(reversed(irs_income)):
        orig  = n - 1 - idx
        y     = mt + idx * row_h
        cy    = y + bar_h * 0.63
        left  = lhl(orig)
        right = rhl(orig)

        lines.append(f'<line x1="0" y1="{y:.1f}" x2="{W}" y2="{y:.1f}" stroke="{GRID}" stroke-width="0.75"/>\n')
        # left bar (filer share) starts at axis, extends left
        lines.append(f'<rect x="{AXIS_X - left:.1f}" y="{y:.1f}" width="{left:.1f}" height="{bar_h:.1f}" '
                     f'fill="{RED}"/>\n')
        # right bar (income share) starts at axis, extends right
        lines.append(f'<rect x="{AXIS_X:.1f}" y="{y:.1f}" width="{right:.1f}" height="{bar_h:.1f}" '
                     f'fill="{BLUE}"/>\n')
        lines.append(f'<text x="0" y="{cy:.1f}" font-size="10.5" '
                     f'fill="{TEXT}">{esc(label)}</text>\n')
        # count labels
        lines.append(f'<text x="{AXIS_X - left - 4:.1f}" y="{cy:.1f}" font-size="9" '
                     f'text-anchor="end" fill="{SUBTEXT}">{filer_pcts[orig]:.1f}%</text>\n')
        lines.append(f'<text x="{AXIS_X + right + 4:.1f}" y="{cy:.1f}" font-size="9" '
                     f'fill="{SUBTEXT}">{income_pcts[orig]:.1f}%</text>\n')

    lines.append(f'<line x1="0" y1="{H - mb:.1f}" x2="{W}" y2="{H - mb:.1f}" stroke="{GRID}" stroke-width="0.75"/>\n')
    lines.append(svg_close())
    return ''.join(lines)


# ── Chart 4: Average assets vs. debt by net worth bracket ─────────────────────
def chart_assets_debt():
    W, H   = W_CHART, 580
    mt, mb = 70, 16

    n     = len(nw_bounds)
    row_h = (H - mt - mb) / n
    bar_h = row_h

    avg_asset = [nw_asset[i] / nw_counts[i] if nw_counts[i] > 0 else 0 for i in range(n)]
    avg_debt  = [nw_debt[i]  / nw_counts[i] if nw_counts[i] > 0 else 0 for i in range(n)]

    max_asset = max(avg_asset)
    max_debt  = max(avg_debt)

    def lhl(i): return avg_debt[i]  / max_debt  * MAX_HALF
    def rhl(i): return avg_asset[i] / max_asset * MAX_HALF

    lines = [svg_open(W, H)]
    lines.append(f'<text x="0" y="26" font-size="14" font-weight="700" '
                 f'fill="{TEXT}">Average Household Assets and Debt by Net Worth, 2022</text>\n')
    lines.append(f'<text x="0" y="44" font-size="10" font-weight="400" fill="{SUBTEXT}">'
                 f'Average per household in bracket  ·  Federal Reserve Survey of Consumer Finances 2022</text>\n')

    lines.append(f'<text x="{AXIS_X - 8}" y="{mt - 4}" font-size="9.5" '
                 f'text-anchor="end" fill="{RED}">← avg debt</text>\n')
    lines.append(f'<text x="{AXIS_X + 8}" y="{mt - 4}" font-size="9.5" '
                 f'text-anchor="start" fill="{BLUE}">avg assets →</text>\n')

    for idx in range(n):
        i_orig = n - 1 - idx
        label  = NW_LABELS[i_orig]
        y      = mt + idx * row_h
        cy     = y + bar_h * 0.63
        left   = lhl(i_orig)
        right  = rhl(i_orig)

        lines.append(f'<line x1="0" y1="{y:.1f}" x2="{W}" y2="{y:.1f}" stroke="{GRID}" stroke-width="0.75"/>\n')
        lines.append(f'<rect x="{AXIS_X - left:.1f}" y="{y:.1f}" width="{left:.1f}" height="{bar_h:.1f}" '
                     f'fill="{RED}"/>\n')
        lines.append(f'<rect x="{AXIS_X:.1f}" y="{y:.1f}" width="{right:.1f}" height="{bar_h:.1f}" '
                     f'fill="{BLUE}"/>\n')
        lines.append(f'<text x="0" y="{cy:.1f}" font-size="10.5" '
                     f'fill="{TEXT}">{esc(label)}</text>\n')
        lines.append(f'<text x="{AXIS_X - left - 4:.1f}" y="{cy:.1f}" font-size="9" '
                     f'text-anchor="end" fill="{SUBTEXT}">{fmt_n(avg_debt[i_orig])}</text>\n')
        lines.append(f'<text x="{AXIS_X + right + 4:.1f}" y="{cy:.1f}" font-size="9" '
                     f'fill="{SUBTEXT}">{fmt_n(avg_asset[i_orig])}</text>\n')

    lines.append(f'<line x1="0" y1="{H - mb:.1f}" x2="{W}" y2="{H - mb:.1f}" stroke="{GRID}" stroke-width="0.75"/>\n')
    lines.append(svg_close())
    return ''.join(lines)


# ── Chart 5: Average retirement savings by income bracket ─────────────────────
def chart_retirement():
    W, H   = W_CHART, 370
    mt, mb = 70, 16

    n     = len(ret_brackets)
    row_h = (H - mt - mb) / n
    bar_h = row_h

    avg_ret = [ret_rsum[j] / ret_wgt[j] if ret_wgt[j] > 0 else 0 for j in range(n)]
    max_ret = max(avg_ret)
    def half_len(v): return v / max_ret * MAX_HALF

    lines = [svg_open(W, H)]
    lines.append(f'<text x="0" y="26" font-size="14" font-weight="700" '
                 f'fill="{TEXT}">Average Retirement Account Value by Income, 2022</text>\n')
    lines.append(f'<text x="0" y="44" font-size="10" font-weight="400" fill="{SUBTEXT}">'
                 f'Weighted average IRA / 401(k) balance per household  ·  Federal Reserve Survey of Consumer Finances 2022</text>\n')

    for idx, (lo, hi, label) in enumerate(reversed(ret_brackets)):
        j_orig = n - 1 - idx
        y      = mt + idx * row_h
        hl     = half_len(avg_ret[j_orig])
        cy     = y + bar_h * 0.63

        lines.append(f'<line x1="0" y1="{y:.1f}" x2="{W}" y2="{y:.1f}" stroke="{GRID}" stroke-width="0.75"/>\n')
        lines.append(f'<rect x="{AXIS_X - hl:.1f}" y="{y:.1f}" width="{hl*2:.1f}" height="{bar_h:.1f}" '
                     f'fill="{BLUE}"/>\n')
        lines.append(f'<text x="0" y="{cy:.1f}" font-size="10.5" '
                     f'fill="{TEXT}">{esc(label)}</text>\n')
        lines.append(f'<text x="{AXIS_X + hl + 5:.1f}" y="{cy:.1f}" font-size="9" '
                     f'fill="{SUBTEXT}">{fmt_n(avg_ret[j_orig])}</text>\n')

    lines.append(f'<line x1="0" y1="{H - mb:.1f}" x2="{W}" y2="{H - mb:.1f}" stroke="{GRID}" stroke-width="0.75"/>\n')
    lines.append(svg_close())
    return ''.join(lines)




# ── Chart 3: Pie chart — working-age adults vs. adjusted filer coverage ───────
def chart_pie():
    W, H  = W_CHART, 342
    cx    = 200          # pie centre x
    cy    = 190          # pie centre y (top of pie = cy-R = 70, matching bar chart mt)
    R     = 120          # outer radius
    r     = 56           # inner radius (donut hole)

    filer_frac    = adj_filers / working_age_pop
    nonfiler_frac = 1.0 - filer_frac

    # arc from angle a1 to a2, donut between r_in and r_out, clockwise
    def donut_path(a1, a2, r_out, r_in):
        large = 1 if (a2 - a1) % (2*math.pi) > math.pi else 0
        def pt(a, rad): return (cx + rad*math.cos(a), cy + rad*math.sin(a))
        x1,y1 = pt(a1, r_out); x2,y2 = pt(a2, r_out)
        x3,y3 = pt(a2, r_in);  x4,y4 = pt(a1, r_in)
        return (f'M{x1:.1f},{y1:.1f} A{r_out},{r_out} 0 {large} 1 {x2:.1f},{y2:.1f} '
                f'L{x3:.1f},{y3:.1f} A{r_in},{r_in} 0 {large} 0 {x4:.1f},{y4:.1f} Z')

    start   = -math.pi / 2                          # 12 o'clock
    end_f   = start + 2 * math.pi * filer_frac      # end of filer slice
    end_nf  = start + 2 * math.pi                   # full circle

    lines = [svg_open(W, H)]

    lines.append(f'<text x="0" y="26" font-size="14" font-weight="700" '
                 f'fill="{TEXT}">Working-Age Adults vs. Income Tax Coverage, 2022–23</text>\n')
    lines.append(f'<text x="0" y="44" font-size="10" font-weight="400" fill="{SUBTEXT}">'
                 f'Ages 18–74  ·  Adjusted for married couples filing jointly  ·  '
                 f'Census ACS 2023; IRS SOI 2022</text>\n')

    # filer slice (blue)
    lines.append(f'<path d="{donut_path(start, end_f, R, r)}" fill="{BLUE}"/>\n')
    # non-filer slice (red)
    lines.append(f'<path d="{donut_path(end_f, end_nf, R, r)}" fill="{RED}"/>\n')

    # centre label
    lines.append(f'<text x="{cx}" y="{cy-6}" font-size="11" text-anchor="middle" '
                 f'font-weight="700" fill="{TEXT}">{working_age_pop/1e6:.0f}m</text>\n')
    lines.append(f'<text x="{cx}" y="{cy+10}" font-size="9" text-anchor="middle" '
                 f'fill="{SUBTEXT}">adults 18–74</text>\n')

    # legend
    lx = cx + R + 28
    items = [
        (BLUE, 'Filed a return (incl. joint)',  adj_filers,  filer_frac),
        (RED,  'Estimated non-filers',           non_filers,  nonfiler_frac),
    ]
    for i, (colour, label, count, frac) in enumerate(items):
        ly = cy - 24 + i * 48
        lines.append(f'<rect x="{lx}" y="{ly}" width="13" height="13" fill="{colour}"/>\n')
        lines.append(f'<text x="{lx+19}" y="{ly+11}" font-size="10.5" fill="{TEXT}">{esc(label)}</text>\n')
        lines.append(f'<text x="{lx+19}" y="{ly+27}" font-size="12" font-weight="700" '
                     f'fill="{TEXT}">{fmt_n(count)} ({frac*100:.0f}%)</text>\n')

    lines.append(svg_close())
    return ''.join(lines)


# ════════════════════════════════════════════════════════════════════════════
# WRITE POST
# ════════════════════════════════════════════════════════════════════════════
print('\nGenerating charts...')
svg1 = chart_income()
svg2 = chart_networth()
svg5 = chart_retirement()
svg6 = chart_pie()

IRS_SOURCE  = 'https://www.irs.gov/statistics/soi-tax-stats-individual-statistical-tables-by-size-of-adjusted-gross-income'
SCF_SOURCE  = 'https://www.federalreserve.gov/econres/scfindex.htm'
CEN_SOURCE  = 'https://data.census.gov/table/ACSDT1Y2023.B01001'

post = f"""---
layout: post
title: "Bee Shaped Population"
byline: "Mitchell Hillman"
standfirst: "The middle class remains robust in the USA."
date: 2026-04-29
image: /img/frankfurt.jpg
---

Comparison is the thief of joy, but an accurate picture of your financial neighborhood can be its own kind of consolation. Marketing and ambition conspire to distort perceptions of wealth, and what passes for normal bears little resemblance to the statistical median. The charts below draw on the most recent household financial data available. Unfortunately, 2022 is the most recent public data across these metrics.

<div class="chart-block">
{svg1}
<p class="chart-source"><a href="{IRS_SOURCE}">IRS Statistics of Income, Individual Income Tax Returns, Table 1.1 (Tax Year 2022)</a></p>
</div>

Rather than a pyramid with a mass of poverty at the base, the actual income distribution looks more like a bee. Households reporting more than $1m in adjusted gross income make up only {m1plus_pct:.2f}% of all filers. The median filer falls in the {median_label} bracket, shown in grey above.

<div class="chart-block">
{svg2}
<p class="chart-source"><a href="{SCF_SOURCE}">Federal Reserve Survey of Consumer Finances 2022</a></p>
</div>

Negative net worth is less common than popular perception suggests, though more research is needed to understand the character of debts that burden these households. Some {lt25k_pct:.0f}% of households have less than $25,000 in net worth, and past that threshold there is a noticeable drop. Saving is difficult, but those who manage it tend to build from there.

<div class="chart-block">
{svg5}
<p class="chart-source"><a href="{SCF_SOURCE}">Federal Reserve Survey of Consumer Finances 2022</a></p>
</div>

Retirement savings scale smoothly, which reflects that most accounts are set on rational autopilot.

<div class="chart-block">
{svg6}
<p class="chart-source"><a href="{CEN_SOURCE}">Census Bureau ACS 1-Year Estimates 2023 (Table B01001)</a> · <a href="{IRS_SOURCE}">IRS Statistics of Income 2022</a></p>
</div>

The share of working-age adults not filing a return sits at approximately {non_filers/working_age_pop*100:.1f}%. The officially published unemployment rate is {unemp_rate:.1f}% as of {unemp_date_str}, which counts only those actively seeking work. The gap reflects retirement, caregiving, disability, and other exits from the labor force.
"""

out_path = '/Users/mitchellhillman/Projects/private-writing/_posts/2026-04-29-economic-conditions.md'
with open(out_path, 'w') as f:
    f.write(post)

print(f'Written to {out_path}')
