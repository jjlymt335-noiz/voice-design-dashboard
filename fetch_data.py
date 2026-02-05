"""
Voice Design 数据看板 - 数据获取脚本
从 BigQuery 获取数据并保存为 JSON
"""

import json
from datetime import datetime, timezone, timedelta
from google.cloud import bigquery

client = bigquery.Client(project='noiz-430406')

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

def run_query(query):
    """执行查询并返回结果"""
    try:
        result = client.query(query).result()
        return [dict(row) for row in result]
    except Exception as e:
        print(f"查询错误: {e}")
        return []

def get_funnel_data():
    """获取漏斗数据 - 昨天/近3天/近7天"""

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

    # 注意：INTERVAL 1 DAY 表示今天，INTERVAL 2 DAY 包含昨天和今天
    # 昨天 = 只看昨天一天的数据
    # 近3天 = 昨天 + 前天 + 大前天
    # 近7天 = 最近7天
    periods = [
        ('yesterday', '昨天'),    # 只看昨天
        ('3', '近3天'),           # 最近3天
        ('7', '近7天'),           # 最近7天
    ]

    results = {}

    for period_key, period_name in periods:
        if period_key == 'yesterday':
            # 昨天：只看昨天一天
            date_condition = """
                _TABLE_SUFFIX = FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))
            """
        else:
            # 近N天：从N天前到昨天（不包含今天，因为今天数据不完整）
            date_condition = f"""
                _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL {period_key} DAY))
                AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
            """

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

        rows = run_query(query)
        period_data = {}
        for row in rows:
            period_data[row['event_name']] = {
                'count': row['event_count'],
                'users': row['unique_users']
            }
        results[period_name] = period_data

    return results

def get_step_details():
    """获取各步骤细分数据 - 按时间周期"""

    periods = [
        ('yesterday', '昨天', '_TABLE_SUFFIX = FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))'),
        ('3', '近3天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
        ('7', '近7天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
    ]

    results = {}

    for period_key, period_name, date_condition in periods:
        # Step 3 细分：是否调整了 prompt
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

        # Step 5 细分：保存时是否修改了标签/描述
        query_save_detail = f"""
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

        # 入口分布
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

        prompt_data = run_query(query_prompt)
        save_data = run_query(query_save_detail)
        entry_data = run_query(query_entry)

        results[period_name] = {
            'prompt_adjustment': prompt_data[0] if prompt_data else {},
            'save_adjustment': save_data[0] if save_data else {},
            'entry_distribution': {row['event_name']: {'count': row['count'], 'users': row['users']} for row in entry_data}
        }

    return results

def get_rating_data():
    """获取点赞点踩数据 - 使用 action 参数（int类型：2=点赞，1=点踩）"""

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

    rows = run_query(query)
    result = {'like': 0, 'dislike': 0, 'unknown': 0, 'total': 0}
    for row in rows:
        action = row.get('action')
        count = row.get('count', 0)
        if action == 2:  # 点赞
            result['like'] += count
        elif action == 1:  # 点踩
            result['dislike'] += count
        else:
            result['unknown'] += count
        result['total'] += count

    # 计算好评率：点赞 / (点赞 + 点踩)
    valid_total = result['like'] + result['dislike']
    result['like_rate'] = round(result['like'] / valid_total * 100, 1) if valid_total > 0 else 0

    return result

def get_upgrade_data():
    """获取付费弹窗数据"""

    query = """
    SELECT
        event_name,
        COUNT(*) as count,
        COUNT(DISTINCT user_pseudo_id) as users
    FROM `noiz-430406.analytics_510746763.events_intraday_*`
    WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
        AND event_name IN ('voice_design_upgrade_popup', 'voice_design_upgrade_confirm_click', 'voice_design_upgrade_cancel_click')
    GROUP BY event_name
    """

    rows = run_query(query)
    result = {}
    for row in rows:
        result[row['event_name']] = {'count': row['count'], 'users': row['users']}

    return result

def get_deep_metrics():
    """获取第二层深层指标 - 按时间周期"""

    periods = [
        ('yesterday', '昨天', '_TABLE_SUFFIX = FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))'),
        ('3', '近3天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
        ('7', '近7天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
    ]

    results = {}

    for period_key, period_name, date_condition in periods:
        # 完成率
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

        # Design音色TTS使用量
        query_tts_from_design = f"""
        SELECT
            COUNT(*) as tts_from_design
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

        # 通过design入口进入付费的比例
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

        completion = run_query(query_completion)
        tts_design = run_query(query_tts_from_design)
        tts_total = run_query(query_tts_total)
        payment = run_query(query_design_payment)

        results[period_name] = {
            'completion': completion[0] if completion else {},
            'tts_from_design': tts_design[0]['tts_from_design'] if tts_design else 0,
            'tts_total': tts_total[0]['total_tts'] if tts_total else 0,
            'payment': payment[0] if payment else {},
            # 占位：需要埋点支持的指标
            'design_voice_users': None,
            'avg_design_voices': None,
            'design_tts_download_rate': None,
            'total_tts_download_rate': None,
        }

    return results

def get_trend_data():
    """获取趋势数据（最近14天每天的数据）"""

    query = """
    SELECT
        event_date,
        event_name,
        COUNT(*) as count,
        COUNT(DISTINCT user_pseudo_id) as users
    FROM `noiz-430406.analytics_510746763.events_intraday_*`
    WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
        AND event_name IN (
            'page_voice_design_exposure',
            'creation_voice_design_click',
            'voice_library_voice_design_click',
            'voice_design_generate_click',
            'voice_design_select_click',
            'voice_design_save_success'
        )
    GROUP BY event_date, event_name
    ORDER BY event_date
    """

    rows = run_query(query)
    result = {}
    for row in rows:
        date = row['event_date']
        if date not in result:
            result[date] = {}
        result[date][row['event_name']] = {'count': row['count'], 'users': row['users']}

    # 计算每天的"进入"复合指标
    for date in result:
        creation = result[date].get('creation_voice_design_click', {'count': 0, 'users': 0})
        library = result[date].get('voice_library_voice_design_click', {'count': 0, 'users': 0})
        result[date]['entry_composite'] = {
            'count': creation['count'] + library['count'],
            'users': creation['users'] + library['users']
        }

    return result

def get_exit_distribution():
    """获取离开路径分布 - 按时间周期"""

    periods = [
        ('yesterday', '昨天', '_TABLE_SUFFIX = FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))'),
        ('3', '近3天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
        ('7', '近7天', '_TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)) AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())'),
    ]

    results = {}

    for period_key, period_name, date_condition in periods:
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

        rows = run_query(query)
        results[period_name] = {row['event_name']: {'count': row['count'], 'users': row['users']} for row in rows}

    return results

def main():
    print("开始获取数据...")

    # 使用北京时间
    beijing_now = datetime.now(BEIJING_TZ)

    data = {
        'update_time': beijing_now.strftime('%Y-%m-%d %H:%M:%S') + ' (北京时间)',
        'funnel': get_funnel_data(),
        'step_details': get_step_details(),
        'rating': get_rating_data(),
        'upgrade': get_upgrade_data(),
        'deep_metrics': get_deep_metrics(),
        'trend': get_trend_data(),
        'exit_distribution': get_exit_distribution(),
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
