"""
查询用户行为数据
1. 用户平均 generate 音色及保存音色的数量
2. 按点赞次数分用户（针对所有生成结果，平均每次generate的点赞次数）
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from google.cloud import bigquery
import warnings
warnings.filterwarnings('ignore')

client = bigquery.Client(project='noiz-430406')

print('=' * 80)
print('查询用户行为数据')
print('=' * 80)

# ============= 查询1：点赞/点踩用户的平均 generate 和 save 音色数量（对比大盘）=============
print('\n[查询1] 点赞/点踩用户的平均 generate 音色及保存音色的数量（对比大盘）...')
query1 = """
WITH rating_users AS (
    -- 有点赞或点踩行为的用户
    SELECT DISTINCT user_pseudo_id
    FROM `noiz-430406.analytics_510746763.events_intraday_*`
    WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
        AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
        AND event_name = 'voice_design_listen_grade'
        AND (SELECT ep.value.int_value FROM UNNEST(event_params) ep WHERE ep.key = 'action') IN (1, 2)
),
-- 点赞/点踩用户的统计
rating_user_stats AS (
    SELECT
        e.user_pseudo_id,
        COUNTIF(e.event_name = 'voice_design_generate_click') as generate_count,
        COUNTIF(e.event_name = 'voice_design_generate_click') * 3 as generated_voices,
        COUNTIF(e.event_name = 'voice_design_save_success') as saved_voices
    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
    INNER JOIN rating_users r ON e.user_pseudo_id = r.user_pseudo_id
    WHERE e._TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
        AND e._TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
        AND e.event_name IN ('voice_design_generate_click', 'voice_design_save_success')
    GROUP BY e.user_pseudo_id
    HAVING generate_count > 0
),
-- 大盘所有用户的统计
all_user_stats AS (
    SELECT
        user_pseudo_id,
        COUNTIF(event_name = 'voice_design_generate_click') as generate_count,
        COUNTIF(event_name = 'voice_design_generate_click') * 3 as generated_voices,
        COUNTIF(event_name = 'voice_design_save_success') as saved_voices
    FROM `noiz-430406.analytics_510746763.events_intraday_*`
    WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
        AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
        AND event_name IN ('voice_design_generate_click', 'voice_design_save_success')
    GROUP BY user_pseudo_id
    HAVING generate_count > 0
)
SELECT
    'rating_users' as segment,
    COUNT(*) as total_users,
    ROUND(AVG(generated_voices), 2) as avg_generated_voices,
    ROUND(AVG(saved_voices), 2) as avg_saved_voices,
    ROUND(AVG(generate_count), 2) as avg_generate_sessions
FROM rating_user_stats
UNION ALL
SELECT
    'all_users' as segment,
    COUNT(*) as total_users,
    ROUND(AVG(generated_voices), 2) as avg_generated_voices,
    ROUND(AVG(saved_voices), 2) as avg_saved_voices,
    ROUND(AVG(generate_count), 2) as avg_generate_sessions
FROM all_user_stats
"""

result1 = client.query(query1).result()
data = {}
for row in result1:
    data[row['segment']] = {
        'users': row['total_users'],
        'generated': row['avg_generated_voices'],
        'saved': row['avg_saved_voices'],
        'sessions': row['avg_generate_sessions']
    }

rating = data.get('rating_users', {})
all_users = data.get('all_users', {})

if rating and all_users:
    gen_diff = rating['generated'] - all_users['generated']
    save_diff = rating['saved'] - all_users['saved']
    session_diff = rating['sessions'] - all_users['sessions']

    print(f"\n点赞/点踩用户:")
    print(f"  总用户数: {rating['users']:,}")
    print(f"  人均生成音色数: {rating['generated']:.2f} ({'+' if gen_diff >= 0 else ''}{gen_diff:.2f})")
    print(f"  人均保存音色数: {rating['saved']:.2f} ({'+' if save_diff >= 0 else ''}{save_diff:.2f})")
    print(f"  人均 generate 次数: {rating['sessions']:.2f} ({'+' if session_diff >= 0 else ''}{session_diff:.2f})")

    print(f"\n大盘（所有有生成行为用户）:")
    print(f"  总用户数: {all_users['users']:,}")
    print(f"  人均生成音色数: {all_users['generated']:.2f}")
    print(f"  人均保存音色数: {all_users['saved']:.2f}")
    print(f"  人均 generate 次数: {all_users['sessions']:.2f}")

# ============= 查询2：点赞/点踩用户中按点赞次数分层 =============
print('\n' + '=' * 80)
print('[查询2] 点赞/点踩用户中按点赞次数分层（平均每次generate的点赞次数）...')
query2 = """
WITH rating_users AS (
    -- 有点赞或点踩行为的用户
    SELECT DISTINCT user_pseudo_id
    FROM `noiz-430406.analytics_510746763.events_intraday_*`
    WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
        AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
        AND event_name = 'voice_design_listen_grade'
        AND (SELECT ep.value.int_value FROM UNNEST(event_params) ep WHERE ep.key = 'action') IN (1, 2)
),
user_generate AS (
    -- 这些用户的 generate 次数
    SELECT
        e.user_pseudo_id,
        COUNT(*) as generate_count
    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
    INNER JOIN rating_users r ON e.user_pseudo_id = r.user_pseudo_id
    WHERE e._TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
        AND e._TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
        AND e.event_name = 'voice_design_generate_click'
    GROUP BY e.user_pseudo_id
),
user_likes AS (
    -- 这些用户的点赞次数（action=1）
    SELECT
        e.user_pseudo_id,
        COUNT(*) as like_count
    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
    INNER JOIN rating_users r ON e.user_pseudo_id = r.user_pseudo_id
    WHERE e._TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
        AND e._TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
        AND e.event_name = 'voice_design_listen_grade'
        AND (SELECT ep.value.int_value FROM UNNEST(event_params) ep WHERE ep.key = 'action') = 1
    GROUP BY e.user_pseudo_id
),
user_stats AS (
    SELECT
        g.user_pseudo_id,
        g.generate_count,
        COALESCE(l.like_count, 0) as like_count,
        -- 平均每次 generate 的点赞次数
        COALESCE(l.like_count, 0) * 1.0 / g.generate_count as avg_likes_per_generate
    FROM user_generate g
    LEFT JOIN user_likes l ON g.user_pseudo_id = l.user_pseudo_id
),
segmented AS (
    SELECT
        CASE
            WHEN avg_likes_per_generate <= 0.2 THEN '<=0.2'
            WHEN avg_likes_per_generate > 0.2 AND avg_likes_per_generate < 0.5 THEN '>0.2且<0.5'
            ELSE '>=0.5'
        END as segment,
        COUNT(*) as user_count
    FROM user_stats
    GROUP BY segment
)
SELECT
    segment,
    user_count,
    (SELECT SUM(user_count) FROM segmented) as total_users
FROM segmented
ORDER BY
    CASE segment
        WHEN '<=0.2' THEN 1
        WHEN '>0.2且<0.5' THEN 2
        ELSE 3
    END
"""

result2 = client.query(query2).result()
total = 0
segments_data = []
for row in result2:
    total = row['total_users']
    pct = (row['user_count'] / total * 100) if total > 0 else 0
    segments_data.append({
        'segment': row['segment'],
        'count': row['user_count'],
        'pct': pct
    })

print(f"\n总用户数（有点赞/点踩行为且有生成行为）: {total:,}")
print('-' * 80)
for seg in segments_data:
    print(f"{seg['segment']:>12} : {seg['count']:>6,} 人 ({seg['pct']:>5.1f}%)")

print('\n查询完成！')
