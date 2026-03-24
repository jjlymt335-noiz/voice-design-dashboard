"""
Voice Design 数据看板 - 数据获取脚本
从 BigQuery 获取数据并保存为 JSON
支持用户分层分析（高频/中频/低频）
"""

import json
from datetime import datetime, timezone, timedelta
from google.cloud import bigquery

client = bigquery.Client(project='noiz-430406')

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

# 分层定义
TIER_DEFINITIONS = {
    'high': {'label': '高频', 'description': '>=4次/天', 'condition': 'avg_daily_generates >= 4'},
    'mid': {'label': '中频', 'description': '1.5~4次/天', 'condition': 'avg_daily_generates > 1.5 AND avg_daily_generates < 4'},
    'low': {'label': '低频', 'description': '<=1.5次/天', 'condition': 'avg_daily_generates <= 1.5 AND avg_daily_generates > 0'},
    'none': {'label': '未生成', 'description': '无生成行为', 'condition': 'avg_daily_generates = 0'},
}

def run_query(query):
    """执行查询并返回结果"""
    try:
        result = client.query(query).result()
        return [dict(row) for row in result]
    except Exception as e:
        print(f"查询错误: {e}")
        return []

def get_user_tier_cte():
    """返回用户分层的 CTE SQL（基于近14天数据计算平均每天generate次数）

    修复：包含所有曝光用户，没有generate行为的用户归入低频
    """
    return """
    all_exposed_users AS (
        -- 所有在近14天有曝光的用户
        SELECT DISTINCT user_pseudo_id
        FROM `noiz-430406.analytics_510746763.events_intraday_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
            AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
            AND event_name = 'page_voice_design_exposure'
    ),
    user_generate_stats AS (
        -- 有generate行为用户的统计
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
    ),
    user_tiers AS (
        -- 所有曝光用户的分层，无generate行为的独立为"未生成"
        SELECT
            e.user_pseudo_id,
            COALESCE(g.avg_daily_generates, 0) as avg_daily_generates,
            CASE
                WHEN g.avg_daily_generates >= 4 THEN 'high'
                WHEN g.avg_daily_generates > 1.5 AND g.avg_daily_generates < 4 THEN 'mid'
                WHEN g.avg_daily_generates > 0 THEN 'low'
                ELSE 'none'
            END as tier
        FROM all_exposed_users e
        LEFT JOIN user_generate_stats g ON e.user_pseudo_id = g.user_pseudo_id
    )
    """

def get_user_tier_stats():
    """获取用户分层统计信息"""
    query = f"""
    WITH {get_user_tier_cte()}
    SELECT
        tier,
        COUNT(*) as user_count,
        ROUND(AVG(avg_daily_generates), 2) as tier_avg
    FROM user_tiers
    GROUP BY tier
    """

    rows = run_query(query)
    total_users = sum(row['user_count'] for row in rows)

    result = {}
    for row in rows:
        tier = row['tier']
        result[tier] = {
            'label': TIER_DEFINITIONS[tier]['label'],
            'description': TIER_DEFINITIONS[tier]['description'],
            'users': row['user_count'],
            'percentage': round(row['user_count'] / total_users * 100, 1) if total_users > 0 else 0,
            'avg_generates': row['tier_avg']
        }

    result['total_users'] = total_users
    result['definition'] = '按用户平均每天generate次数分层（总次数/活跃天数，基于近14天）'

    return result

def get_funnel_data():
    """获取漏斗数据 - 昨天/近3天/近7天，支持分层"""

    events = [
        'page_voice_design_exposure',
        'creation_voice_design_click',
        'voice_library_voice_design_click',
        'voice_design_generate_click',
        'voice_design_select_click',
        'voice_design_save_success',
        'voice_design_save_voice_use',
        'voice_design_complete_back',
    ]

    periods = [
        ('yesterday', '昨天'),
        ('3', '近3天'),
        ('7', '近7天'),
    ]

    tiers = ['大盘', 'high', 'mid', 'low', 'none']
    results = {}

    for period_key, period_name in periods:
        if period_key == 'yesterday':
            date_condition = '_TABLE_SUFFIX = FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))'
        else:
            date_condition = f'_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL {period_key} DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'

        period_result = {}

        for tier in tiers:
            if tier == '大盘':
                # 大盘数据 - 不过滤用户
                query = f"""
                SELECT
                    event_name,
                    COUNT(*) as event_count,
                    COUNT(DISTINCT user_pseudo_id) as unique_users
                FROM `noiz-430406.analytics_510746763.events_intraday_*`
                WHERE {date_condition}
                    AND event_name IN ({','.join([f'"{e}"' for e in events])})
                GROUP BY event_name
                """
            else:
                # 分层数据 - 按用户分层过滤
                query = f"""
                WITH {get_user_tier_cte()}
                SELECT
                    e.event_name,
                    COUNT(*) as event_count,
                    COUNT(DISTINCT e.user_pseudo_id) as unique_users
                FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                WHERE {date_condition}
                    AND e.event_name IN ({','.join([f'"{ev}"' for ev in events])})
                    AND ut.tier = '{tier}'
                GROUP BY e.event_name
                """

            rows = run_query(query)
            tier_data = {}
            for row in rows:
                tier_data[row['event_name']] = {
                    'count': row['event_count'],
                    'users': row['unique_users']
                }
            period_result[tier] = tier_data

        results[period_name] = period_result

    return results

def get_step_details():
    """获取各步骤细分数据 - 按时间周期，支持分层"""

    periods = [
        ('yesterday', '昨天', '_TABLE_SUFFIX = FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))'),
        ('3', '近3天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
        ('7', '近7天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
    ]

    tiers = ['大盘', 'high', 'mid', 'low', 'none']
    results = {}

    for period_key, period_name, date_condition in periods:
        period_result = {}

        for tier in tiers:
            if tier == '大盘':
                # 大盘 - 不过滤用户
                tier_filter = ""
                tier_join = ""
                tier_cte = ""
            else:
                tier_cte = f"WITH {get_user_tier_cte()},"
                tier_join = "INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id"
                tier_filter = f"AND ut.tier = '{tier}'"

            # Prompt 调整
            if tier == '大盘':
                query_prompt = f"""
                WITH generate_events AS (
                    SELECT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_generate_click'
                ),
                prompt_events AS (
                    SELECT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_prompt_click'
                ),
                generate_users AS (
                    SELECT DISTINCT user_pseudo_id FROM generate_events
                ),
                prompt_users AS (
                    SELECT DISTINCT user_pseudo_id FROM prompt_events
                )
                SELECT
                    (SELECT COUNT(*) FROM generate_users) as total_generate_users,
                    (SELECT COUNT(*) FROM prompt_users) as prompt_users,
                    (SELECT COUNT(*) FROM generate_users g JOIN prompt_users p ON g.user_pseudo_id = p.user_pseudo_id) as generate_with_prompt,
                    (SELECT COUNT(*) FROM generate_events) as total_generate_count,
                    (SELECT COUNT(*) FROM prompt_events) as prompt_count
                """
            else:
                query_prompt = f"""
                WITH {get_user_tier_cte()},
                generate_events AS (
                    SELECT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'voice_design_generate_click'
                        AND ut.tier = '{tier}'
                ),
                prompt_events AS (
                    SELECT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'voice_design_prompt_click'
                        AND ut.tier = '{tier}'
                ),
                generate_users AS (
                    SELECT DISTINCT user_pseudo_id FROM generate_events
                ),
                prompt_users AS (
                    SELECT DISTINCT user_pseudo_id FROM prompt_events
                )
                SELECT
                    (SELECT COUNT(*) FROM generate_users) as total_generate_users,
                    (SELECT COUNT(*) FROM prompt_users) as prompt_users,
                    (SELECT COUNT(*) FROM generate_users g JOIN prompt_users p ON g.user_pseudo_id = p.user_pseudo_id) as generate_with_prompt,
                    (SELECT COUNT(*) FROM generate_events) as total_generate_count,
                    (SELECT COUNT(*) FROM prompt_events) as prompt_count
                """

            # 入口分布
            if tier == '大盘':
                query_entry = f"""
                SELECT
                    event_name,
                    COUNT(*) as count,
                    COUNT(DISTINCT user_pseudo_id) as users
                FROM `noiz-430406.analytics_510746763.events_intraday_*`
                WHERE {date_condition}
                    AND event_name IN ('creation_voice_design_click', 'voice_library_voice_design_click')
                GROUP BY event_name
                """
            else:
                query_entry = f"""
                WITH {get_user_tier_cte()}
                SELECT
                    e.event_name,
                    COUNT(*) as count,
                    COUNT(DISTINCT e.user_pseudo_id) as users
                FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                WHERE {date_condition}
                    AND e.event_name IN ('creation_voice_design_click', 'voice_library_voice_design_click')
                    AND ut.tier = '{tier}'
                GROUP BY e.event_name
                """

            # 保存行为（标签/描述修改）
            if tier == '大盘':
                query_save = f"""
                WITH save_events AS (
                    SELECT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_save_success'
                ),
                label_events AS (
                    SELECT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_label_adjust'
                ),
                desc_events AS (
                    SELECT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_description_adjust'
                ),
                save_users AS (SELECT DISTINCT user_pseudo_id FROM save_events),
                label_users AS (SELECT DISTINCT user_pseudo_id FROM label_events),
                desc_users AS (SELECT DISTINCT user_pseudo_id FROM desc_events)
                SELECT
                    (SELECT COUNT(*) FROM save_users) as total_save_users,
                    (SELECT COUNT(*) FROM save_users s JOIN label_users l ON s.user_pseudo_id = l.user_pseudo_id) as with_label_adjust,
                    (SELECT COUNT(*) FROM save_users s JOIN desc_users d ON s.user_pseudo_id = d.user_pseudo_id) as with_desc_adjust,
                    (SELECT COUNT(*) FROM save_events) as total_save_count,
                    (SELECT COUNT(*) FROM label_events) as with_label_adjust_count,
                    (SELECT COUNT(*) FROM desc_events) as with_desc_adjust_count
                """
            else:
                query_save = f"""
                WITH {get_user_tier_cte()},
                save_events AS (
                    SELECT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'voice_design_save_success'
                        AND ut.tier = '{tier}'
                ),
                label_events AS (
                    SELECT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'voice_design_label_adjust'
                        AND ut.tier = '{tier}'
                ),
                desc_events AS (
                    SELECT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'voice_design_description_adjust'
                        AND ut.tier = '{tier}'
                ),
                save_users AS (SELECT DISTINCT user_pseudo_id FROM save_events),
                label_users AS (SELECT DISTINCT user_pseudo_id FROM label_events),
                desc_users AS (SELECT DISTINCT user_pseudo_id FROM desc_events)
                SELECT
                    (SELECT COUNT(*) FROM save_users) as total_save_users,
                    (SELECT COUNT(*) FROM save_users s JOIN label_users l ON s.user_pseudo_id = l.user_pseudo_id) as with_label_adjust,
                    (SELECT COUNT(*) FROM save_users s JOIN desc_users d ON s.user_pseudo_id = d.user_pseudo_id) as with_desc_adjust,
                    (SELECT COUNT(*) FROM save_events) as total_save_count,
                    (SELECT COUNT(*) FROM label_events) as with_label_adjust_count,
                    (SELECT COUNT(*) FROM desc_events) as with_desc_adjust_count
                """

            prompt_data = run_query(query_prompt)
            entry_data = run_query(query_entry)
            save_data = run_query(query_save)

            period_result[tier] = {
                'prompt_adjustment': prompt_data[0] if prompt_data else {},
                'entry_distribution': {row['event_name']: {'count': row['count'], 'users': row['users']} for row in entry_data},
                'save_adjustment': save_data[0] if save_data else {}
            }

        results[period_name] = period_result

    return results

def get_rating_data():
    """获取点赞点踩数据 - 使用 action 参数（int类型：1=点赞，2=点踩），支持分层"""

    tiers = ['大盘', 'high', 'mid', 'low', 'none']
    results = {}

    for tier in tiers:
        if tier == '大盘':
            query = """
            SELECT
                (SELECT ep.value.int_value FROM UNNEST(event_params) ep WHERE ep.key = 'action') as action,
                COUNT(*) as count,
                COUNT(DISTINCT user_pseudo_id) as users
            FROM `noiz-430406.analytics_510746763.events_intraday_*`
            WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
                AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
                AND event_name = 'voice_design_listen_grade'
            GROUP BY action
            """
        else:
            query = f"""
            WITH {get_user_tier_cte()}
            SELECT
                (SELECT ep.value.int_value FROM UNNEST(e.event_params) ep WHERE ep.key = 'action') as action,
                COUNT(*) as count,
                COUNT(DISTINCT e.user_pseudo_id) as users
            FROM `noiz-430406.analytics_510746763.events_intraday_*` e
            INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
            WHERE e._TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
                AND e._TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
                AND e.event_name = 'voice_design_listen_grade'
                AND ut.tier = '{tier}'
            GROUP BY action
            """

        rows = run_query(query)
        tier_result = {'like': 0, 'dislike': 0, 'unknown': 0, 'total': 0, 'like_users': 0, 'dislike_users': 0}
        for row in rows:
            action = row.get('action')
            count = row.get('count', 0)
            users = row.get('users', 0)
            if action == 1:  # 点赞
                tier_result['like'] += count
                tier_result['like_users'] += users
            elif action == 2:  # 点踩
                tier_result['dislike'] += count
                tier_result['dislike_users'] += users
            else:
                tier_result['unknown'] += count
            tier_result['total'] += count

        # 计算好评率：点赞 / (点赞 + 点踩)
        valid_total = tier_result['like'] + tier_result['dislike']
        tier_result['like_rate'] = round(tier_result['like'] / valid_total * 100, 1) if valid_total > 0 else 0
        # 人均点赞/点踩次数
        tier_result['avg_like'] = round(tier_result['like'] / tier_result['like_users'], 1) if tier_result['like_users'] > 0 else 0
        tier_result['avg_dislike'] = round(tier_result['dislike'] / tier_result['dislike_users'], 1) if tier_result['dislike_users'] > 0 else 0

        results[tier] = tier_result

    return results

def get_upgrade_data():
    """获取付费弹窗数据 - 支持分层"""

    tiers = ['大盘', 'high', 'mid', 'low', 'none']
    events = ['voice_design_upgrade_popup', 'voice_design_upgrade_confirm_click', 'voice_design_upgrade_cancel_click']
    results = {}

    for tier in tiers:
        if tier == '大盘':
            query = f"""
            SELECT
                event_name,
                COUNT(*) as count,
                COUNT(DISTINCT user_pseudo_id) as users
            FROM `noiz-430406.analytics_510746763.events_intraday_*`
            WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
                AND event_name IN ({','.join([f'"{e}"' for e in events])})
            GROUP BY event_name
            """
        else:
            query = f"""
            WITH {get_user_tier_cte()}
            SELECT
                e.event_name,
                COUNT(*) as count,
                COUNT(DISTINCT e.user_pseudo_id) as users
            FROM `noiz-430406.analytics_510746763.events_intraday_*` e
            INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
            WHERE e._TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
                AND e.event_name IN ({','.join([f'"{ev}"' for ev in events])})
                AND ut.tier = '{tier}'
            GROUP BY e.event_name
            """

        rows = run_query(query)
        tier_result = {}
        for row in rows:
            tier_result[row['event_name']] = {'count': row['count'], 'users': row['users']}

        results[tier] = tier_result

    return results

def get_credit_data():
    """获取 voice design credit 消耗占比（近7天 vs 上一个7天）"""

    def query_credits(start_interval, end_interval):
        """查询指定时间范围的 credit 数据"""
        query = f"""
        WITH daily_dates AS (
            SELECT DISTINCT _TABLE_SUFFIX as dt
            FROM `noiz-430406.analytics_510746763.events_*`
            WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL {start_interval} DAY))
                AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL {end_interval} DAY))
        ),
        all_events AS (
            SELECT event_params
            FROM `noiz-430406.analytics_510746763.events_*`
            WHERE event_name = 'credit_reduce'
                AND _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL {start_interval} DAY))
                AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL {end_interval} DAY))
            UNION ALL
            SELECT event_params
            FROM `noiz-430406.analytics_510746763.events_intraday_*`
            WHERE event_name = 'credit_reduce'
                AND _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL {start_interval} DAY))
                AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL {end_interval} DAY))
                AND _TABLE_SUFFIX NOT IN (SELECT dt FROM daily_dates)
        ),
        base AS (
            SELECT
                (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'type') AS ep_type,
                COALESCE(
                    (SELECT CAST(value.int_value AS FLOAT64) FROM UNNEST(event_params) WHERE key = 'credits'),
                    (SELECT value.double_value FROM UNNEST(event_params) WHERE key = 'credits'),
                    (SELECT SAFE_CAST(value.string_value AS FLOAT64) FROM UNNEST(event_params) WHERE key = 'credits')
                ) AS credits
            FROM all_events
        )
        SELECT
            COALESCE(SUM(IF(ep_type = 'voice_design', credits, 0)), 0) AS voice_design_credits,
            COALESCE(SUM(credits), 0) AS total_credits
        FROM base
        WHERE credits IS NOT NULL
        """
        rows = run_query(query)
        if rows:
            return rows[0]
        return {'voice_design_credits': 0, 'total_credits': 0}

    # 近7天
    current = query_credits(7, 0)
    # 上一个7天
    prev = query_credits(14, 7)

    vd_credits = float(current.get('voice_design_credits', 0))
    total_credits = float(current.get('total_credits', 0))
    share = round(vd_credits / total_credits * 100, 2) if total_credits > 0 else 0

    prev_vd = float(prev.get('voice_design_credits', 0))
    prev_total = float(prev.get('total_credits', 0))
    prev_share = round(prev_vd / prev_total * 100, 2) if prev_total > 0 else 0

    return {
        'voice_design_credits': round(vd_credits, 1),
        'total_credits': round(total_credits, 1),
        'share': share,
        'prev_voice_design_credits': round(prev_vd, 1),
        'prev_total_credits': round(prev_total, 1),
        'prev_share': prev_share,
        'share_delta': round(share - prev_share, 2)
    }


def _query_design_voice_snapshot(end_date_condition):
    """查询截止某日的 design 音色累计指标快照

    Args:
        end_date_condition: SQL 条件，如 '_TABLE_SUFFIX < FORMAT_DATE(...)'，用于统计累计拥有 design 音色的用户
    Returns:
        dict with design_voice_users, total_users (最近14天有TTS生成的用户), pct, avg, total
    """
    query = f"""
    WITH design_voices AS (
        SELECT
            user_pseudo_id,
            (SELECT ep.value.string_value FROM UNNEST(event_params) ep WHERE ep.key = 'voice_id') as voice_id
        FROM `noiz-430406.analytics_510746763.events_intraday_*`
        WHERE {end_date_condition}
            AND event_name = 'voice_design_save_success'
    ),
    user_voice_counts AS (
        SELECT
            user_pseudo_id,
            COUNT(DISTINCT CASE WHEN voice_id IS NOT NULL THEN voice_id ELSE GENERATE_UUID() END) as voice_count
        FROM design_voices
        GROUP BY user_pseudo_id
    ),
    total_tts_users AS (
        SELECT COUNT(DISTINCT user_pseudo_id) as total
        FROM `noiz-430406.analytics_510746763.events_intraday_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
            AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
            AND event_name = 'tts_generate_click'
    )
    SELECT
        (SELECT COUNT(*) FROM user_voice_counts) as design_voice_users,
        (SELECT total FROM total_tts_users) as total_users,
        (SELECT ROUND(AVG(voice_count), 2) FROM user_voice_counts) as avg_design_voices,
        (SELECT CAST(SUM(voice_count) AS INT64) FROM user_voice_counts) as total_design_voices
    """
    rows = run_query(query)
    if rows and rows[0]:
        r = rows[0]
        design_users = r.get('design_voice_users') or 0
        total_users = r.get('total_users') or 0
        return {
            'design_voice_users': design_users,
            'total_users': total_users,
            'design_voice_users_pct': round(design_users / total_users * 100, 1) if total_users > 0 else 0,
            'avg_design_voices': float(r.get('avg_design_voices') or 0),
            'total_design_voices': r.get('total_design_voices') or 0,
        }
    return {
        'design_voice_users': 0, 'total_users': 0,
        'design_voice_users_pct': 0, 'avg_design_voices': 0,
        'total_design_voices': 0,
    }


def get_tts_adoption_data():
    """获取 TTS 采纳率数据 - 保存 design 音色后 10 分钟内是否使用 TTS

    纯 GA4 事件方案（不依赖 analysis_tmp.design 表）：
    - save 事件: voice_design_save_success (无 voice_id，用 user_pseudo_id + timestamp)
    - tts 事件: tts_generate_click (有 voice_id, from 参数)
    - 判断 design 音色使用: tts from='/voice/design'
    """
    query = """
    WITH daily_dates AS (
        SELECT DISTINCT _TABLE_SUFFIX as dt
        FROM `noiz-430406.analytics_510746763.events_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
            AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
    ),
    save_events AS (
        SELECT user_pseudo_id, TIMESTAMP_MICROS(event_timestamp) as save_ts
        FROM `noiz-430406.analytics_510746763.events_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
            AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
            AND event_name = 'voice_design_save_success'
        UNION ALL
        SELECT user_pseudo_id, TIMESTAMP_MICROS(event_timestamp) as save_ts
        FROM `noiz-430406.analytics_510746763.events_intraday_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
            AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_ADD(CURRENT_DATE(), INTERVAL 1 DAY))
            AND event_name = 'voice_design_save_success'
            AND _TABLE_SUFFIX NOT IN (SELECT dt FROM daily_dates)
    ),
    tts_events AS (
        SELECT user_pseudo_id, TIMESTAMP_MICROS(event_timestamp) as tts_ts,
            (SELECT ep.value.string_value FROM UNNEST(event_params) ep WHERE ep.key = 'from') as from_path
        FROM `noiz-430406.analytics_510746763.events_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
            AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
            AND event_name = 'tts_generate_click'
        UNION ALL
        SELECT user_pseudo_id, TIMESTAMP_MICROS(event_timestamp) as tts_ts,
            (SELECT ep.value.string_value FROM UNNEST(event_params) ep WHERE ep.key = 'from') as from_path
        FROM `noiz-430406.analytics_510746763.events_intraday_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
            AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_ADD(CURRENT_DATE(), INTERVAL 1 DAY))
            AND event_name = 'tts_generate_click'
            AND _TABLE_SUFFIX NOT IN (SELECT dt FROM daily_dates)
    ),
    joined AS (
        SELECT
            s.user_pseudo_id,
            s.save_ts,
            t.tts_ts,
            t.from_path,
            CASE WHEN t.from_path = '/voice/design' THEN 1 ELSE 0 END as is_design_tts
        FROM save_events s
        JOIN tts_events t ON s.user_pseudo_id = t.user_pseudo_id
        WHERE t.tts_ts BETWEEN s.save_ts AND TIMESTAMP_ADD(s.save_ts, INTERVAL 10 MINUTE)
    ),
    saves_with_tts AS (
        SELECT DISTINCT user_pseudo_id, save_ts
        FROM joined
    ),
    saves_with_design_tts AS (
        SELECT DISTINCT user_pseudo_id, save_ts
        FROM joined
        WHERE is_design_tts = 1
    )
    SELECT
        (SELECT COUNT(*) FROM save_events) as total_save_events,
        (SELECT COUNT(*) FROM saves_with_tts) as saves_with_any_tts,
        (SELECT COUNT(*) FROM saves_with_design_tts) as saves_with_design_tts,
        COUNT(*) as total_tts_10m,
        SUM(is_design_tts) as design_voice_tts_10m,
        SUM(1 - is_design_tts) as switch_tts_10m
    FROM joined
    """

    rows = run_query(query)
    if rows and rows[0]:
        r = rows[0]
        total_saves = r.get('total_save_events') or 0
        saves_tts = r.get('saves_with_any_tts') or 0
        saves_design = r.get('saves_with_design_tts') or 0
        total_tts = r.get('total_tts_10m') or 0
        design_tts = r.get('design_voice_tts_10m') or 0
        switch_tts = r.get('switch_tts_10m') or 0
        return {
            'total_save_events': total_saves,
            'saves_with_any_tts': saves_tts,
            'saves_with_design_tts': saves_design,
            'total_tts_10m': total_tts,
            'design_voice_tts_10m': design_tts,
            'switch_tts_10m': switch_tts,
            'adoption_rate': round(saves_tts / total_saves * 100, 1) if total_saves > 0 else 0,
            'design_usage_rate': round(saves_design / saves_tts * 100, 1) if saves_tts > 0 else 0,
        }
    return {
        'total_save_events': 0, 'saves_with_any_tts': 0, 'saves_with_design_tts': 0,
        'total_tts_10m': 0, 'design_voice_tts_10m': 0, 'switch_tts_10m': 0,
        'adoption_rate': 0, 'design_usage_rate': 0,
    }


def get_non_gen_flow_data():
    """获取未生成用户的行为流数据（近14天，前3步，top3+其他）"""
    from collections import Counter, defaultdict

    EVENT_LABELS = {
        'page_voice_design_bounce': '离开Design页',
        'voice_design_prompt_click': '点击Prompt',
        'voice_design_upgrade_confirm_click': '点击Upgrade',
        'signup_success': '注册成功',
        'signup_click': '点击注册',
        'google_login_click': '谷歌登录',
        'login_success': '登录成功',
        'page_tts_creation_exposure': '进入TTS创作页',
        'page_tts_creation_bounce': '离开TTS创作页',
        'page_voice_clone_exposure': '进入Voice Clone页',
        'page_voice_clone_bounce': '离开Voice Clone页',
        'page_voice_lib_exposure': '进入音色库',
        'page_voice_lib_bounce': '离开音色库',
        'page_voice_sound_exposure': '进入Sound Design页',
        'page_voice_sound_bounce': '离开Sound Design页',
        'page_landing_exposure': '进入Landing页',
        'page_landing_bounce': '离开Landing页',
        'page_explore_exposure': '进入探索页',
        'guide_exposure': '引导曝光',
        'guide_click': '点击引导',
        'guide_dismiss': '关闭引导',
        'voice_search_result_exposure': '搜索结果曝光',
        'voice_clone_add_voice_click': '添加克隆音色',
        'voice_play_click': '播放音色',
        'tts_generate_click': 'TTS生成',
        'tts_inputdetail': 'TTS输入内容',
        'tts_playback_click': 'TTS播放',
        'creation_voice_card_click': '点击音色卡片',
        'creation_voice_clone_click': '点击Voice Clone',
        'creation_voice_design_click': '点击Voice Design',
        'voice_library_voice_design_click': '从音色库进Design',
        'credit_reduce': 'Credits消耗',
        'page_video_videoList_exposure': '进入视频列表',
        'NEX-page_voice_design_bounce': '离开Design页(NEX)',
        'NEX-page_tts_creation_exposure': '进入TTS创作页(NEX)',
        'voice_clone_preview_listen_play_click': '试听克隆音色',
    }
    def get_label(e):
        return EVENT_LABELS.get(e, e)

    query = """
    WITH daily_dates AS (
        SELECT DISTINCT _TABLE_SUFFIX as dt
        FROM `noiz-430406.analytics_510746763.events_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
          AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
    ),
    all_events AS (
        SELECT user_pseudo_id, event_name, event_timestamp
        FROM `noiz-430406.analytics_510746763.events_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
          AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
        UNION ALL
        SELECT user_pseudo_id, event_name, event_timestamp
        FROM `noiz-430406.analytics_510746763.events_intraday_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
          AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_ADD(CURRENT_DATE(), INTERVAL 1 DAY))
          AND _TABLE_SUFFIX NOT IN (SELECT dt FROM daily_dates)
    ),
    design_exposures AS (
        SELECT user_pseudo_id, MIN(event_timestamp) as exposure_ts
        FROM all_events WHERE event_name = 'page_voice_design_exposure'
        GROUP BY user_pseudo_id
    ),
    generators AS (
        SELECT DISTINCT user_pseudo_id FROM all_events
        WHERE event_name = 'voice_design_generate_click'
    ),
    non_gen_exposures AS (
        SELECT de.user_pseudo_id, de.exposure_ts
        FROM design_exposures de
        LEFT JOIN generators g ON de.user_pseudo_id = g.user_pseudo_id
        WHERE g.user_pseudo_id IS NULL
    ),
    post_events AS (
        SELECT e.user_pseudo_id, e.event_name,
            ROW_NUMBER() OVER (PARTITION BY e.user_pseudo_id ORDER BY e.event_timestamp) as step_num
        FROM all_events e
        JOIN non_gen_exposures ng ON e.user_pseudo_id = ng.user_pseudo_id
        WHERE e.event_timestamp > ng.exposure_ts + 1000000  -- 1秒缓冲，排除并发事件
          AND e.event_timestamp <= ng.exposure_ts + 1800000000
          AND e.event_name NOT IN (
              'page_voice_design_exposure',
              'session_start', 'first_visit', 'first_open',
              'user_engagement', 'scroll', 'page_view',
              'click', 'file_download', 'view_search_results'
          )
          AND (NOT ENDS_WITH(e.event_name, '_bounce') OR e.event_name = 'page_voice_design_bounce')  -- 排除其他页bounce，保留Design bounce
    ),
    user_paths AS (
        SELECT user_pseudo_id,
            MAX(CASE WHEN step_num = 1 THEN event_name END) as step1,
            MAX(CASE WHEN step_num = 2 THEN event_name END) as step2,
            MAX(CASE WHEN step_num = 3 THEN event_name END) as step3
        FROM post_events WHERE step_num <= 3
        GROUP BY user_pseudo_id
    )
    SELECT step1, step2, step3, COUNT(*) as user_count
    FROM user_paths WHERE step1 IS NOT NULL
    GROUP BY step1, step2, step3
    ORDER BY user_count DESC
    """
    rows = run_query(query)
    if not rows:
        return {}

    # 同时查总数
    total_query = """
    WITH daily_dates2 AS (
        SELECT DISTINCT _TABLE_SUFFIX as dt
        FROM `noiz-430406.analytics_510746763.events_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
          AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
    ),
    all_events AS (
        SELECT user_pseudo_id, event_name
        FROM `noiz-430406.analytics_510746763.events_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
          AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
          AND event_name IN ('page_voice_design_exposure', 'voice_design_generate_click')
        UNION ALL
        SELECT user_pseudo_id, event_name
        FROM `noiz-430406.analytics_510746763.events_intraday_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
          AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_ADD(CURRENT_DATE(), INTERVAL 1 DAY))
          AND event_name IN ('page_voice_design_exposure', 'voice_design_generate_click')
          AND _TABLE_SUFFIX NOT IN (SELECT dt FROM daily_dates2)
    )
    SELECT
        COUNT(DISTINCT CASE WHEN event_name = 'page_voice_design_exposure' THEN user_pseudo_id END) as total_exposed,
        COUNT(DISTINCT CASE WHEN event_name = 'voice_design_generate_click' THEN user_pseudo_id END) as total_generated
    FROM all_events
    """
    totals = run_query(total_query)
    t = totals[0] if totals else {}
    total_exposed = t.get('total_exposed', 0)
    total_generated = t.get('total_generated', 0)
    non_gen_total = total_exposed - total_generated

    # 构建树
    step1_counts = Counter()
    step1_step2 = defaultdict(Counter)
    step1_step2_step3 = defaultdict(lambda: defaultdict(Counter))

    for r in rows:
        s1, s2, s3, cnt = r['step1'], r['step2'], r['step3'], r['user_count']
        step1_counts[s1] += cnt
        if s2:
            step1_step2[s1][s2] += cnt
            if s3:
                step1_step2_step3[s1][s2][s3] += cnt

    total_with_steps = sum(step1_counts.values())

    def build_top3(counter, total, min_pct=0):
        """取top3，低于 min_pct% 的也归入其他"""
        top3 = counter.most_common(3)
        result = []
        kept_count = 0
        for name, count in top3:
            pct = round(count / total * 100, 1) if total else 0
            if pct >= min_pct:
                result.append({'name': get_label(name), 'event': name,
                               'count': count, 'pct': pct})
                kept_count += count
        others_count = total - kept_count
        if others_count > 0:
            result.append({'name': '其他', 'event': 'others',
                           'count': others_count, 'pct': round(others_count / total * 100, 1)})
        return result

    flow_tree = {
        'total_exposed': total_exposed,
        'total_generated': total_generated,
        'non_gen_total': non_gen_total,
        'non_gen_with_events': total_with_steps,
        'step1': []
    }

    # 第1步：只保留 top1（离开Design页），其余合并为"其他"且不可展开
    step1_top1 = step1_counts.most_common(1)
    s1_name, s1_count = step1_top1[0]
    s1_others_count = total_with_steps - s1_count

    # 构建 top1 的 step2/step3
    s1_total_for_s2 = sum(step1_step2[s1_name].values())
    s1_node = {
        'name': get_label(s1_name), 'event': s1_name,
        'count': s1_count, 'pct': round(s1_count / total_with_steps * 100, 1),
        'step2': []
    }
    if s1_total_for_s2 > 0:
        s2_top3 = step1_step2[s1_name].most_common(3)
        s2_others = s1_total_for_s2 - sum(c for _, c in s2_top3)
        for s2_name, s2_count in s2_top3:
            s2_total_for_s3 = sum(step1_step2_step3[s1_name][s2_name].values())
            s2_node = {
                'name': get_label(s2_name), 'event': s2_name,
                'count': s2_count, 'pct': round(s2_count / s1_total_for_s2 * 100, 1),
                'step3': build_top3(step1_step2_step3[s1_name][s2_name], s2_total_for_s3, min_pct=5) if s2_total_for_s3 else []
            }
            s1_node['step2'].append(s2_node)
        if s2_others > 0:
            s1_node['step2'].append({'name': '其他', 'event': 'others',
                                     'count': s2_others, 'pct': round(s2_others / s1_total_for_s2 * 100, 1),
                                     'step3': []})
    flow_tree['step1'].append(s1_node)

    if s1_others_count > 0:
        flow_tree['step1'].append({'name': '其他', 'event': 'others',
                                   'count': s1_others_count, 'pct': round(s1_others_count / total_with_steps * 100, 1),
                                   'step2': []})
    return flow_tree


def get_design_voice_metrics():
    """获取 design 音色拥有量指标（上线至今累计 + 相比7天前的变化）

    从 voice_design_save_success 事件的 event_params 中提取 voice_id，
    按用户维度统计：
    1. 至少拥有1个 design 音色的用户占比（分母：最近14天有TTS生成的用户）
    2. 人均拥有的 design 音色数（上线至今累计）
    3. 与7天前的对比变化（+X pp / +X.XX）
    """
    # 当前累计（截止昨天，因为今天数据不完整）
    current = _query_design_voice_snapshot(
        '_TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'
    )

    # 7天前累计（截止8天前 = 7天前的"昨天"）
    prev_7d = _query_design_voice_snapshot(
        '_TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))'
    )

    # 计算变化量
    pct_delta = round(current['design_voice_users_pct'] - prev_7d['design_voice_users_pct'], 1)
    avg_delta = round(current['avg_design_voices'] - prev_7d['avg_design_voices'], 2)

    return {
        # 当前绝对值
        'design_voice_users': current['design_voice_users'],
        'total_users': current['total_users'],
        'design_voice_users_pct': current['design_voice_users_pct'],
        'avg_design_voices': current['avg_design_voices'],
        'total_design_voices': current['total_design_voices'],
        # 7天前绝对值（供参考）
        'prev_7d_design_voice_users': prev_7d['design_voice_users'],
        'prev_7d_design_voice_users_pct': prev_7d['design_voice_users_pct'],
        'prev_7d_avg_design_voices': prev_7d['avg_design_voices'],
        # 变化量
        'pct_delta_7d': pct_delta,     # 占比变化，单位 pp
        'avg_delta_7d': avg_delta,     # 人均变化
    }


def get_deep_metrics():
    """获取第二层深层指标 - 按时间周期，支持分层"""

    periods = [
        ('yesterday', '昨天', '_TABLE_SUFFIX = FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))'),
        ('3', '近3天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
        ('7', '近7天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
    ]

    tiers = ['大盘', 'high', 'mid', 'low', 'none']
    results = {}

    for period_key, period_name, date_condition in periods:
        period_result = {}

        for tier in tiers:
            # 完成率 - 支持分层
            if tier == '大盘':
                query_completion = f"""
                WITH exposure_users AS (
                    SELECT DISTINCT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'page_voice_design_exposure'
                ),
                save_users AS (
                    SELECT DISTINCT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_save_success'
                )
                SELECT
                    (SELECT COUNT(*) FROM exposure_users) as exposure_users,
                    (SELECT COUNT(*) FROM save_users) as save_users
                """
            else:
                query_completion = f"""
                WITH {get_user_tier_cte()},
                exposure_users AS (
                    SELECT DISTINCT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'page_voice_design_exposure'
                        AND ut.tier = '{tier}'
                ),
                save_users AS (
                    SELECT DISTINCT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'voice_design_save_success'
                        AND ut.tier = '{tier}'
                )
                SELECT
                    (SELECT COUNT(*) FROM exposure_users) as exposure_users,
                    (SELECT COUNT(*) FROM save_users) as save_users
                """

            completion = run_query(query_completion)

            # TTS和Payment数据只在大盘时获取（不需要分层）
            if tier == '大盘':
                query_tts_from_design = f"""
                SELECT COUNT(*) as tts_from_design
                FROM `noiz-430406.analytics_510746763.events_intraday_*`
                WHERE {date_condition}
                    AND event_name = 'tts_generate_click'
                    AND (SELECT ep.value.string_value FROM UNNEST(event_params) ep WHERE ep.key = 'from') = '/voice/design'
                """

                query_tts_total = f"""
                SELECT COUNT(*) as total_tts
                FROM `noiz-430406.analytics_510746763.events_intraday_*`
                WHERE {date_condition}
                    AND event_name = 'tts_generate_click'
                """

                query_design_payment = f"""
                WITH design_upgrade_users AS (
                    SELECT DISTINCT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_upgrade_confirm_click'
                ),
                all_payment_users AS (
                    SELECT DISTINCT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name LIKE '%payment_success%'
                )
                SELECT
                    (SELECT COUNT(*) FROM design_upgrade_users) as design_upgrade_users,
                    (SELECT COUNT(*) FROM all_payment_users) as all_payment_users,
                    COUNT(*) as design_to_payment
                FROM design_upgrade_users d
                JOIN all_payment_users p ON d.user_pseudo_id = p.user_pseudo_id
                """

                tts_design = run_query(query_tts_from_design)
                tts_total = run_query(query_tts_total)
                payment = run_query(query_design_payment)

                period_result[tier] = {
                    'completion': completion[0] if completion else {},
                    'tts_from_design': tts_design[0]['tts_from_design'] if tts_design else 0,
                    'tts_total': tts_total[0]['total_tts'] if tts_total else 0,
                    'payment': payment[0] if payment else {},
                    'design_tts_download_rate': None,
                    'total_tts_download_rate': None,
                }
            else:
                period_result[tier] = {
                    'completion': completion[0] if completion else {},
                }

        results[period_name] = period_result

    return results

def get_trend_data():
    """获取趋势数据（最近14天每天的数据），支持分层"""

    tiers = ['大盘', 'high', 'mid', 'low', 'none']
    events = [
        'page_voice_design_exposure',
        'creation_voice_design_click',
        'voice_library_voice_design_click',
        'voice_design_generate_click',
        'voice_design_select_click',
        'voice_design_save_success'
    ]

    results = {}

    for tier in tiers:
        if tier == '大盘':
            event_list = ','.join([f'"{e}"' for e in events])
            query = f"""
            WITH daily AS (
                SELECT user_pseudo_id, event_name, event_date
                FROM `noiz-430406.analytics_510746763.events_*`
                WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
                    AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
                    AND event_name IN ({event_list})
            ),
            daily_dates AS (
                SELECT DISTINCT _TABLE_SUFFIX as dt
                FROM `noiz-430406.analytics_510746763.events_*`
                WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
                    AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
            ),
            combined AS (
                SELECT * FROM daily
                UNION ALL
                SELECT user_pseudo_id, event_name, event_date
                FROM `noiz-430406.analytics_510746763.events_intraday_*`
                WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
                    AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_ADD(CURRENT_DATE(), INTERVAL 1 DAY))
                    AND event_name IN ({event_list})
                    AND _TABLE_SUFFIX NOT IN (SELECT dt FROM daily_dates)
            )
            SELECT
                event_date,
                event_name,
                COUNT(*) as count,
                COUNT(DISTINCT user_pseudo_id) as users
            FROM combined
            GROUP BY event_date, event_name
            ORDER BY event_date
            """
        else:
            event_list = ','.join([f'"{ev}"' for ev in events])
            query = f"""
            WITH {get_user_tier_cte()},
            daily AS (
                SELECT user_pseudo_id, event_name, event_date
                FROM `noiz-430406.analytics_510746763.events_*`
                WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
                    AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
                    AND event_name IN ({event_list})
            ),
            daily_dates AS (
                SELECT DISTINCT _TABLE_SUFFIX as dt
                FROM `noiz-430406.analytics_510746763.events_*`
                WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
                    AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
            ),
            combined AS (
                SELECT * FROM daily
                UNION ALL
                SELECT user_pseudo_id, event_name, event_date
                FROM `noiz-430406.analytics_510746763.events_intraday_*`
                WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
                    AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_ADD(CURRENT_DATE(), INTERVAL 1 DAY))
                    AND event_name IN ({event_list})
                    AND _TABLE_SUFFIX NOT IN (SELECT dt FROM daily_dates)
            )
            SELECT
                e.event_date,
                e.event_name,
                COUNT(*) as count,
                COUNT(DISTINCT e.user_pseudo_id) as users
            FROM combined e
            INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
            WHERE ut.tier = '{tier}'
            GROUP BY e.event_date, e.event_name
            ORDER BY e.event_date
            """

        rows = run_query(query)
        tier_result = {}
        for row in rows:
            date = row['event_date']
            if date not in tier_result:
                tier_result[date] = {}
            tier_result[date][row['event_name']] = {'count': row['count'], 'users': row['users']}

        # 计算每天的"进入"复合指标
        for date in tier_result:
            creation = tier_result[date].get('creation_voice_design_click', {'count': 0, 'users': 0})
            library = tier_result[date].get('voice_library_voice_design_click', {'count': 0, 'users': 0})
            tier_result[date]['entry_composite'] = {
                'count': creation['count'] + library['count'],
                'users': creation['users'] + library['users']
            }

        results[tier] = tier_result

    return results

def get_exit_distribution():
    """获取离开路径分布 - 按时间周期，支持分层"""

    periods = [
        ('yesterday', '昨天', '_TABLE_SUFFIX = FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))'),
        ('3', '近3天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
        ('7', '近7天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
    ]

    tiers = ['大盘', 'high', 'mid', 'low', 'none']
    results = {}

    for period_key, period_name, date_condition in periods:
        period_result = {}

        for tier in tiers:
            if tier == '大盘':
                query = f"""
                SELECT
                    event_name,
                    COUNT(*) as count,
                    COUNT(DISTINCT user_pseudo_id) as users
                FROM `noiz-430406.analytics_510746763.events_intraday_*`
                WHERE {date_condition}
                    AND event_name IN ('voice_design_save_voice_use', 'voice_design_complete_back')
                GROUP BY event_name
                """
            else:
                query = f"""
                WITH {get_user_tier_cte()}
                SELECT
                    e.event_name,
                    COUNT(*) as count,
                    COUNT(DISTINCT e.user_pseudo_id) as users
                FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                WHERE {date_condition}
                    AND e.event_name IN ('voice_design_save_voice_use', 'voice_design_complete_back')
                    AND ut.tier = '{tier}'
                GROUP BY e.event_name
                """

            rows = run_query(query)
            period_result[tier] = {row['event_name']: {'count': row['count'], 'users': row['users']} for row in rows}

        results[period_name] = period_result

    return results


# Template ID → 语言映射
TEMPLATE_LANG = {}
CN_TEMPLATES = ['午夜电台', '创世女神', '外星来客', '财经博主', '营销号解说', '人工智能', '鸡血带货', '悲伤诉说', '胡同老大爷', '激动的包子', '数学老师', '猫猫网红']
EN_TEMPLATES = ['Sorrow American Narrator', 'Cat Influencer', 'Elegant British Narrator', 'Goddess of Creation', 'Soft ASMR Mindfulness', 'The Dark Commander', 'Youtube Indian Teacher', 'Alien', 'Silicon Valley Prodigy', 'Energy Comms', 'AI Robotic Engine', 'Speaking Hamburger']
JP_TEMPLATES = ['長官', '創世の女神', '宇宙人', '激烈な論争', '華麗なる悪役', '人工知能', '優雅なお嬢様', '内なる独白']
for t in CN_TEMPLATES:
    TEMPLATE_LANG[f'voice_design_templates_{t}'] = '中文'
for t in EN_TEMPLATES:
    TEMPLATE_LANG[f'voice_design_templates_{t}'] = '英文'
for t in JP_TEMPLATES:
    TEMPLATE_LANG[f'voice_design_templates_{t}'] = '日文'


def get_template_data():
    """获取 template 点击和保存数据（近7天）"""
    query = """
    WITH daily_dates AS (
        SELECT DISTINCT _TABLE_SUFFIX as dt
        FROM `noiz-430406.analytics_510746763.events_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
            AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
    ),
    combined AS (
        SELECT user_pseudo_id, event_name, event_timestamp,
            (SELECT ep.value.int_value FROM UNNEST(event_params) ep WHERE ep.key = 'ga_session_id') as session_id,
            (SELECT ep.value.string_value FROM UNNEST(event_params) ep WHERE ep.key = 'templateId') as template_id
        FROM `noiz-430406.analytics_510746763.events_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
            AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
            AND event_name IN ('voice_design_template_click', 'voice_design_save_success', 'page_voice_design_exposure')
        UNION ALL
        SELECT user_pseudo_id, event_name, event_timestamp,
            (SELECT ep.value.int_value FROM UNNEST(event_params) ep WHERE ep.key = 'ga_session_id') as session_id,
            (SELECT ep.value.string_value FROM UNNEST(event_params) ep WHERE ep.key = 'templateId') as template_id
        FROM `noiz-430406.analytics_510746763.events_intraday_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
            AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_ADD(CURRENT_DATE(), INTERVAL 1 DAY))
            AND event_name IN ('voice_design_template_click', 'voice_design_save_success', 'page_voice_design_exposure')
            AND _TABLE_SUFFIX NOT IN (SELECT dt FROM daily_dates)
    ),
    -- template点击统计
    template_clicks AS (
        SELECT template_id, COUNT(*) as clicks, COUNT(DISTINCT user_pseudo_id) as click_users
        FROM combined
        WHERE event_name = 'voice_design_template_click'
        GROUP BY template_id
    ),
    -- 总曝光
    total_exposure AS (
        SELECT COUNT(*) as exposure_count
        FROM combined WHERE event_name = 'page_voice_design_exposure'
    ),
    -- 总保存
    total_saves AS (
        SELECT COUNT(*) as save_count
        FROM combined WHERE event_name = 'voice_design_save_success'
    ),
    -- 每次 save 归因到同 session 中保存前最近一次 template_click
    save_template_pairs AS (
        SELECT
            s.user_pseudo_id,
            s.session_id,
            s.event_timestamp as save_ts,
            tc2.template_id,
            tc2.event_timestamp as click_ts,
            ROW_NUMBER() OVER (
                PARTITION BY s.user_pseudo_id, s.session_id, s.event_timestamp
                ORDER BY tc2.event_timestamp DESC
            ) as rn
        FROM combined s
        JOIN combined tc2
            ON tc2.user_pseudo_id = s.user_pseudo_id
            AND tc2.session_id = s.session_id
            AND tc2.event_name = 'voice_design_template_click'
            AND tc2.event_timestamp <= s.event_timestamp
        WHERE s.event_name = 'voice_design_save_success'
    ),
    template_saves AS (
        SELECT template_id, COUNT(*) as saves
        FROM save_template_pairs
        WHERE rn = 1
        GROUP BY template_id
    )
    SELECT
        tc.template_id,
        tc.clicks,
        tc.click_users,
        COALESCE(ts.saves, 0) as saves,
        te.exposure_count,
        tsa.save_count as total_saves
    FROM template_clicks tc
    CROSS JOIN total_exposure te
    CROSS JOIN total_saves tsa
    LEFT JOIN template_saves ts ON tc.template_id = ts.template_id
    ORDER BY tc.clicks DESC
    """

    rows = run_query(query)
    if not rows:
        return {}

    exposure_count = rows[0].get('exposure_count', 0) if rows else 0
    total_saves = rows[0].get('total_saves', 0) if rows else 0

    # 按语言分组
    lang_data = {'中文': {'templates': [], 'total_clicks': 0, 'total_saves': 0},
                 '英文': {'templates': [], 'total_clicks': 0, 'total_saves': 0},
                 '日文': {'templates': [], 'total_clicks': 0, 'total_saves': 0}}

    for row in rows:
        tid = row.get('template_id', '')
        lang = TEMPLATE_LANG.get(tid, '其他')
        if lang not in lang_data:
            continue

        # 提取 template 显示名
        name = tid.replace('voice_design_templates_', '') if tid else 'unknown'
        clicks = row.get('clicks', 0)
        saves = row.get('saves', 0)

        lang_data[lang]['templates'].append({
            'id': tid,
            'name': name,
            'clicks': clicks,
            'click_users': row.get('click_users', 0),
            'saves': saves,
            'save_click_rate': round(saves / clicks * 100, 1) if clicks > 0 else 0,
        })
        lang_data[lang]['total_clicks'] += clicks
        lang_data[lang]['total_saves'] += saves

    # 计算比例
    for lang in lang_data:
        d = lang_data[lang]
        d['click_rate'] = round(d['total_clicks'] / exposure_count * 100, 1) if exposure_count > 0 else 0
        d['save_rate'] = round(d['total_saves'] / total_saves * 100, 1) if total_saves > 0 else 0
        d['save_click_rate'] = round(d['total_saves'] / d['total_clicks'] * 100, 1) if d['total_clicks'] > 0 else 0
        for t in d['templates']:
            t['click_pct'] = round(t['clicks'] / d['total_clicks'] * 100, 1) if d['total_clicks'] > 0 else 0

    return {
        'exposure_count': exposure_count,
        'total_saves': total_saves,
        'languages': lang_data,
    }


def main():
    print("开始获取数据...")

    # 使用北京时间
    beijing_now = datetime.now(BEIJING_TZ)

    print("  获取用户分层统计...")
    user_tiers = get_user_tier_stats()

    print("  获取漏斗数据（含分层）...")
    funnel = get_funnel_data()

    print("  获取步骤细分（含分层）...")
    step_details = get_step_details()

    print("  获取点赞点踩数据...")
    rating = get_rating_data()

    print("  获取付费弹窗数据...")
    upgrade = get_upgrade_data()

    print("  获取 credit 消耗数据...")
    credit = get_credit_data()

    print("  获取深层指标...")
    deep_metrics = get_deep_metrics()

    print("  获取趋势数据（含分层）...")
    trend = get_trend_data()

    print("  获取离开分布（含分层）...")
    exit_distribution = get_exit_distribution()

    print("  获取 design 音色累计指标...")
    design_voice = get_design_voice_metrics()

    print("  获取 TTS 采纳率数据...")
    tts_adoption = get_tts_adoption_data()

    print("  获取未生成用户行为流...")
    non_gen_flow = get_non_gen_flow_data()

    print("  获取 template 使用数据...")
    template_data = get_template_data()

    data = {
        'update_time': beijing_now.strftime('%Y-%m-%d %H:%M:%S') + ' (北京时间)',
        'user_tiers': user_tiers,
        'funnel': funnel,
        'step_details': step_details,
        'rating': rating,
        'upgrade': upgrade,
        'credit': credit,
        'deep_metrics': deep_metrics,
        'trend': trend,
        'exit_distribution': exit_distribution,
        'design_voice': design_voice,
        'tts_adoption': tts_adoption,
        'non_gen_flow': non_gen_flow,
        'template_data': template_data,
    }

    # 保存为 JSON - 使用脚本所在目录
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, 'data', 'dashboard_data.json')

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    print(f"数据已保存到 {output_path}")
    print(f"更新时间: {data['update_time']}")

if __name__ == '__main__':
    main()
