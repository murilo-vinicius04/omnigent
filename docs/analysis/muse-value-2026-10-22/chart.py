"""Render the frozen 22 October quota comparison with matplotlib."""
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter

OUT = Path(__file__).resolve().parent
# Tokens include cached input and output; reasoning is already within output.
# Claude: separate 20 October window. High: official 5x allowance projection.
ROWS = [
    dict(name='Muse Everyday · $5', status='Measured · 22 Oct', tokens=80786795,
         session_points=41, weekly_points=14, color='#087f8c'),
    dict(name='Muse High · $15', status='Projected · 5× allowance', tokens=80786795,
         session_points=8.2, weekly_points=2.8, color='#59aeb6'),
    dict(name='Codex Plus · $20', status='Measured · 22 Oct', tokens=5652341,
         session_points=23, weekly_points=4, color='#6554c0'),
    dict(name='Claude Pro · $20', status='Historical · 20 Oct', tokens=268071323,
         session_points=None, weekly_points=27, color='#bd7041'),
]
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11,
                     'axes.spines.top': False, 'axes.spines.right': False,
                     'axes.spines.left': False, 'axes.edgecolor': '#cad2dc',
                     'text.color': '#172536', 'axes.labelcolor': '#334155',
                     'xtick.color': '#475569', 'ytick.color': '#334155',
                     'figure.facecolor': '#f8fafc', 'axes.facecolor': '#f8fafc'})
fig, axes = plt.subplots(1, 2, figsize=(15, 6.9))
fig.subplots_adjust(left=.20, right=.96, top=.73, bottom=.36, wspace=.32)
fig.text(.055, .94, 'How many tokens did each quota point buy?', fontsize=23, weight='bold')
fig.text(.055, .884, 'Observed context throughput • millions of input + output tokens per 1 percentage point',
         fontsize=12, color='#475569')
fig.text(.055, .827, 'More tokens per point = more recorded throughput. This does not measure task quality.',
         fontsize=11, color='#475569')
for ax, field, title in zip(axes, ['session_points', 'weekly_points'],
                            ['Five-hour allowance', 'Weekly allowance']):
    for idx, row in enumerate(ROWS):
        y=3-idx
        points=row[field]
        if points is None:
            ax.text(.02, y, 'No comparable five-hour measurement', va='center',
                    transform=ax.get_yaxis_transform(), fontsize=9, color='#64748b')
            continue
        value=row['tokens']/1e6/points
        projected=row['status'].startswith('Projected')
        ax.barh(y, value, height=.55, color=row['color'],
                edgecolor=row['color'] if projected else 'none',
                hatch='///' if projected else None, alpha=.55 if projected else .95)
        ax.text(value + (.15 if field=='session_points' else .4), y,
                f'{value:.2f}M' + (' *' if projected else ''), va='center', weight='bold', fontsize=11)
    ax.set_ylim(-.6,3.6)
    ax.set_xlim(0,12 if field=='session_points' else 35)
    ax.set_yticks([3,2,1,0], [r['name'] for r in ROWS] if ax is axes[0] else ['']*4)
    ax.tick_params(axis='y', length=0, pad=12)
    ax.set_title(title, loc='left', fontsize=14, weight='bold', pad=17)
    ax.set_xlabel('Million tokens per 1 quota point', labelpad=12)
    ax.grid(axis='x', color='#dce3eb', linewidth=.7)
    ax.set_axisbelow(True)
fig.text(.055,.235,'MEASURED WINDOW',fontsize=9,weight='bold',color='#64748b')
fig.text(.055,.18,'22 Oct, 12:10–14:06 UTC: Muse 80.79M tokens / +41 five-hour points / +14 weekly points;'
         '\nCodex 5.65M tokens / +23 five-hour points / +4 weekly points.',fontsize=10,linespacing=1.6)
fig.text(.055,.085,'* Muse High is a 5× allowance projection, not a measurement. Claude uses 20 Oct: 268.07M / +27 weekly points.'
         '\nCache-heavy, different tasks and models; background reviews included. Gemini lacks a comparable token/quota pair.'
         '\nRounded account meters and reporting lag make these approximate workload-specific rates, not fixed token allowances.',
         fontsize=9,color='#64748b',linespacing=1.5)
for ext in ['png','svg','pdf']:
    fig.savefig(OUT/f'tokens-per-quota-point.{ext}', dpi=180, facecolor=fig.get_facecolor())
plt.close(fig)

# A literal tokens-versus-percentage view. Dashed lines are proportional
# interpolation/projection of observed endpoints, not measured intermediate samples.
fig, axes = plt.subplots(1,2,figsize=(14,7))
fig.subplots_adjust(left=.08,right=.97,top=.77,bottom=.28,wspace=.24)
fig.text(.06,.94,'Tokens vs quota consumed',fontsize=24,weight='bold')
fig.text(.06,.881,'Dots are observed window totals. Lines show the average rate, not a measured per-task conversion.',
         fontsize=11,color='#475569')
for ax, field, title in zip(axes,['session_points','weekly_points'],['Five-hour allowance','Weekly allowance']):
    for row in ROWS:
        pts=row[field]
        if pts is None:continue
        projected=row['status'].startswith('Projected')
        ax.plot([0,pts],[0,row['tokens']/1e6],color=row['color'],
                linestyle='--' if projected else '-',linewidth=2.3,
                label=row['name']+(' (projection)' if projected else ''))
        ax.scatter([pts],[row['tokens']/1e6],s=65,facecolor='white' if projected else row['color'],
                   edgecolor=row['color'],linewidth=1.7,zorder=5)
    ax.set_title(title,loc='left',weight='bold',pad=15)
    ax.set_xlabel('Quota consumed (percentage points)',labelpad=12)
    ax.set_ylabel('Processed tokens (millions)' if ax is axes[0] else '')
    ax.set_xlim(left=0);ax.set_ylim(bottom=0)
    ax.grid(color='#dce3eb',linewidth=.7)
handles, labels=axes[1].get_legend_handles_labels()
fig.legend(handles,labels,loc='lower left',bbox_to_anchor=(.06,.12),ncol=2,frameon=False,fontsize=10)
fig.text(.06,.065,'Muse and Codex: same 22 Oct window. Claude: separate 20 Oct window. Cached input and background reviews included.'
         '\nHigh = proportional 5× allowance scenario. Different work/model mixes; throughput does not establish equal-quality task value.',
         fontsize=9,color='#64748b',linespacing=1.6)
for ext in ['png','svg','pdf']:
    fig.savefig(OUT/f'tokens-vs-quota.{ext}',dpi=180,facecolor=fig.get_facecolor())
plt.close(fig)
with (OUT/'chart-data.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['plan','evidence','tokens','five_hour_points','weekly_points','million_tokens_per_five_hour_point','million_tokens_per_weekly_point'])
    w.writeheader()
    for r in ROWS:
        w.writerow(dict(plan=r['name'],evidence=r['status'],tokens=r['tokens'],
                        five_hour_points=r['session_points'],weekly_points=r['weekly_points'],
                        million_tokens_per_five_hour_point=r['tokens']/1e6/r['session_points'] if r['session_points'] else '',
                        million_tokens_per_weekly_point=r['tokens']/1e6/r['weekly_points']))
(OUT/'chart-data.json').write_text(json.dumps(ROWS,indent=2)+'\n')
print(OUT/'tokens-vs-quota.png')
print(OUT/'tokens-per-quota-point.png')
