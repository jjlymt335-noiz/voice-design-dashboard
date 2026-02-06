# -*- coding: utf-8 -*-
"""临时查询脚本 - Generate 次数分层分析（按用户平均）"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from google.cloud import bigquery
import warnings
warnings.filterwarnings('ignore')

client = bigquery.Client(project='noiz-430406')

print('=' * 60)
print('Voice Design Generate 分层分析')
print('口径：每用户平均generate次数 = 总generate次数 / 有generate天数')
print('时间范围：上线至今（近14天数据）')
print('=' * 60)

# 计算每个用户的平均每天 generate 次数，然后按平均值分层
query = """
WITH user_stats AS (
    SELECT
        user_pseudo_id,
        COUNT(*) as total_generates,
        COUNT(DISTINCT event_date) as active_days,
        COUNT(*) * 1.0 / COUNT(DISTINCT event_date) as avg_daily_generates
    FROM `noiz-430406.analytics_510746763.events_intraday_*`
    WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
        AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
        AND event_name = 'voice_design_generate_click'
    GROUP BY user_pseudo_id
)
SELECT
    CASE
        WHEN avg_daily_generates >= 4 THEN 'high_4plus'
        WHEN avg_daily_generates > 1.5 AND avg_daily_generates < 4 THEN 'mid_1.5to4'
        ELSE 'low_1.5or_less'
    END as tier,
    COUNT(*) as user_count,
    AVG(avg_daily_generates) as tier_avg,
    SUM(total_generates) as tier_total_generates,
    SUM(active_days) as tier_total_days
FROM user_stats
GROUP BY tier
"""

result = client.query(query).result()

data = {
    'high_4plus': {'users': 0, 'avg': 0, 'generates': 0, 'days': 0},
    'mid_1.5to4': {'users': 0, 'avg': 0, 'generates': 0, 'days': 0},
    'low_1.5or_less': {'users': 0, 'avg': 0, 'generates': 0, 'days': 0}
}
total_users = 0

for row in result:
    data[row['tier']] = {
        'users': row['user_count'],
        'avg': round(row['tier_avg'], 2),
        'generates': row['tier_total_generates'],
        'days': row['tier_total_days']
    }
    total_users += row['user_count']

print(f'\n总用户数: {total_users:,}')
print('-' * 60)

tier_labels = [
    ('high_4plus', '>=4次/天 (高频)'),
    ('mid_1.5to4', '1.5~4次/天 (中频)'),
    ('low_1.5or_less', '<=1.5次/天 (低频)')
]

for key, label in tier_labels:
    d = data[key]
    pct = (d['users'] / total_users * 100) if total_users > 0 else 0
    print(f"{label}:")
    print(f"  用户数: {d['users']:,} ({pct:.1f}%)")
    print(f"  该层平均: {d['avg']:.2f} 次/天")
    print(f"  总generate: {d['generates']:,} 次 | 总活跃天: {d['days']:,} 天")
    print()
