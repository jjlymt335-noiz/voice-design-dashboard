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
                WITH generate_users AS (
                    SELECT DISTINCT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_generate_click'
                ),
                prompt_users AS (
                    SELECT DISTINCT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_prompt_click'
                )
                SELECT
                    (SELECT COUNT(*) FROM generate_users) as total_generate_users,
                    (SELECT COUNT(*) FROM prompt_users) as prompt_users,
                    COUNT(*) as generate_with_prompt
                FROM generate_users g
                JOIN prompt_users p ON g.user_pseudo_id = p.user_pseudo_id
                """
            else:
                query_prompt = f"""
                WITH {get_user_tier_cte()},
                generate_users AS (
                    SELECT DISTINCT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'voice_design_generate_click'
                        AND ut.tier = '{tier}'
                ),
                prompt_users AS (
                    SELECT DISTINCT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'voice_design_prompt_click'
                        AND ut.tier = '{tier}'
                )
                SELECT
                    (SELECT COUNT(*) FROM generate_users) as total_generate_users,
                    (SELECT COUNT(*) FROM prompt_users) as prompt_users,
                    COUNT(*) as generate_with_prompt
                FROM generate_users g
                JOIN prompt_users p ON g.user_pseudo_id = p.user_pseudo_id
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
                WITH save_users AS (
                    SELECT DISTINCT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_save_success'
                ),
                label_users AS (
                    SELECT DISTINCT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_label_adjust'
                ),
                desc_users AS (
                    SELECT DISTINCT user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*`
                    WHERE {date_condition}
                        AND event_name = 'voice_design_description_adjust'
                )
                SELECT
                    (SELECT COUNT(*) FROM save_users) as total_save_users,
                    (SELECT COUNT(*) FROM save_users s JOIN label_users l ON s.user_pseudo_id = l.user_pseudo_id) as with_label_adjust,
                    (SELECT COUNT(*) FROM save_users s JOIN desc_users d ON s.user_pseudo_id = d.user_pseudo_id) as with_desc_adjust
                """
            else:
                query_save = f"""
                WITH {get_user_tier_cte()},
                save_users AS (
                    SELECT DISTINCT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'voice_design_save_success'
                        AND ut.tier = '{tier}'
                ),
                label_users AS (
                    SELECT DISTINCT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'voice_design_label_adjust'
                        AND ut.tier = '{tier}'
                ),
                desc_users AS (
                    SELECT DISTINCT e.user_pseudo_id
                    FROM `noiz-430406.analytics_510746763.events_intraday_*` e
                    INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
                    WHERE {date_condition}
                        AND e.event_name = 'voice_design_description_adjust'
                        AND ut.tier = '{tier}'
                )
                SELECT
                    (SELECT COUNT(*) FROM save_users) as total_save_users,
                    (SELECT COUNT(*) FROM save_users s JOIN label_users l ON s.user_pseudo_id = l.user_pseudo_id) as with_label_adjust,
                    (SELECT COUNT(*) FROM save_users s JOIN desc_users d ON s.user_pseudo_id = d.user_pseudo_id) as with_desc_adjust
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
                COUNT(*) as count
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
                COUNT(*) as count
            FROM `noiz-430406.analytics_510746763.events_intraday_*` e
            INNER JOIN user_tiers ut ON e.user_pseudo_id = ut.user_pseudo_id
            WHERE e._TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
                AND e._TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
                AND e.event_name = 'voice_design_listen_grade'
                AND ut.tier = '{tier}'
            GROUP BY action
            """

        rows = run_query(query)
        tier_result = {'like': 0, 'dislike': 0, 'unknown': 0, 'total': 0}
        for row in rows:
            action = row.get('action')
            count = row.get('count', 0)
            if action == 1:  # 点赞
                tier_result['like'] += count
            elif action == 2:  # 点踩
                tier_result['dislike'] += count
            else:
                tier_result['unknown'] += count
            tier_result['total'] += count

        # 计算好评率：点赞 / (点赞 + 点踩)
        valid_total = tier_result['like'] + tier_result['dislike']
        tier_result['like_rate'] = round(tier_result['like'] / valid_total * 100, 1) if valid_total > 0 else 0

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
            WITH combined AS (
                SELECT user_pseudo_id, event_name, event_date
                FROM `noiz-430406.analytics_510746763.events_intraday_*`
                WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
                    AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_ADD(CURRENT_DATE(), INTERVAL 1 DAY))
                    AND event_name IN ({event_list})
                UNION ALL
                SELECT user_pseudo_id, event_name, event_date
                FROM `noiz-430406.analytics_510746763.events_*`
                WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
                    AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
                    AND event_name IN ({event_list})
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
            combined AS (
                SELECT user_pseudo_id, event_name, event_date
                FROM `noiz-430406.analytics_510746763.events_intraday_*`
                WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
                    AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", DATE_ADD(CURRENT_DATE(), INTERVAL 1 DAY))
                    AND event_name IN ({event_list})
                UNION ALL
                SELECT user_pseudo_id, event_name, event_date
                FROM `noiz-430406.analytics_510746763.events_*`
                WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
                    AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
                    AND event_name IN ({event_list})
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

    print("  获取深层指标...")
    deep_metrics = get_deep_metrics()

    print("  获取趋势数据（含分层）...")
    trend = get_trend_data()

    print("  获取离开分布（含分层）...")
    exit_distribution = get_exit_distribution()

    print("  获取 design 音色累计指标...")
    design_voice = get_design_voice_metrics()

    data = {
        'update_time': beijing_now.strftime('%Y-%m-%d %H:%M:%S') + ' (北京时间)',
        'user_tiers': user_tiers,
        'funnel': funnel,
        'step_details': step_details,
        'rating': rating,
        'upgrade': upgrade,
        'deep_metrics': deep_metrics,
        'trend': trend,
        'exit_distribution': exit_distribution,
        'design_voice': design_voice,
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
