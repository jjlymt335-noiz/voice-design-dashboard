"""
Voice Design 核心漏斗日报 - 推送到飞书
"""

import requests
from datetime import datetime, timezone, timedelta
from google.cloud import bigquery

# ── 配置 ──────────────────────────────────────────────
import os
PROJECT_ID = os.getenv("GCP_PROJECT_ID", "noiz-430406")
LARK_WEBHOOK_URL = os.getenv("LARK_WEBHOOK_URL", "https://open.larksuite.com/open-apis/bot/v2/hook/13d3973c-227d-4108-ab14-8da89808340b")
BEIJING_TZ = timezone(timedelta(hours=8))

client = bigquery.Client(project=PROJECT_ID)

def run_query(query):
    """执行查询并返回结果"""
    result = client.query(query).result()
    return [dict(row) for row in result]

def get_funnel_data(date_condition):
    """获取漏斗数据"""
    query = f"""
    WITH funnel AS (
        SELECT
            event_name,
            COUNT(DISTINCT user_pseudo_id) as users
        FROM `noiz-430406.analytics_510746763.events_intraday_*`
        WHERE {date_condition}
            AND event_name IN (
                'page_voice_design_exposure',
                'creation_voice_design_click',
                'voice_library_voice_design_click',
                'voice_design_generate_click',
                'voice_design_select_click',
                'voice_design_save_success'
            )
        GROUP BY event_name
    ),
    entry AS (
        SELECT COUNT(DISTINCT user_pseudo_id) as users
        FROM `noiz-430406.analytics_510746763.events_intraday_*`
        WHERE {date_condition}
            AND event_name IN ('creation_voice_design_click', 'voice_library_voice_design_click')
    )
    SELECT
        (SELECT users FROM funnel WHERE event_name = 'page_voice_design_exposure') as exposure,
        (SELECT users FROM entry) as entry,
        (SELECT users FROM funnel WHERE event_name = 'voice_design_generate_click') as generate,
        (SELECT users FROM funnel WHERE event_name = 'voice_design_select_click') as select_voice,
        (SELECT users FROM funnel WHERE event_name = 'voice_design_save_success') as save
    """
    rows = run_query(query)
    if rows:
        return {
            '曝光': rows[0]['exposure'] or 0,
            '进入创编页': rows[0]['entry'] or 0,
            '生成音色': rows[0]['generate'] or 0,
            '选择音色': rows[0]['select_voice'] or 0,
            '保存音色': rows[0]['save'] or 0,
        }
    return {'曝光': 0, '进入创编页': 0, '生成音色': 0, '选择音色': 0, '保存音色': 0}

def get_funnel_data_with_history(date_condition):
    """获取有历史生成记录用户的漏斗数据"""
    query = f"""
    WITH users_with_history AS (
        -- 近14天有过generate行为的用户
        SELECT DISTINCT user_pseudo_id
        FROM `noiz-430406.analytics_510746763.events_intraday_*`
        WHERE _TABLE_SUFFIX >= FORMAT_DATE("%Y%m%d", DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY))
            AND _TABLE_SUFFIX < FORMAT_DATE("%Y%m%d", CURRENT_DATE())
            AND event_name = 'voice_design_generate_click'
    ),
    funnel AS (
        SELECT
            event_name,
            COUNT(DISTINCT e.user_pseudo_id) as users
        FROM `noiz-430406.analytics_510746763.events_intraday_*` e
        INNER JOIN users_with_history u ON e.user_pseudo_id = u.user_pseudo_id
        WHERE {date_condition}
            AND event_name IN (
                'page_voice_design_exposure',
                'creation_voice_design_click',
                'voice_library_voice_design_click',
                'voice_design_generate_click',
                'voice_design_select_click',
                'voice_design_save_success'
            )
        GROUP BY event_name
    ),
    entry AS (
        SELECT COUNT(DISTINCT e.user_pseudo_id) as users
        FROM `noiz-430406.analytics_510746763.events_intraday_*` e
        INNER JOIN users_with_history u ON e.user_pseudo_id = u.user_pseudo_id
        WHERE {date_condition}
            AND event_name IN ('creation_voice_design_click', 'voice_library_voice_design_click')
    )
    SELECT
        (SELECT users FROM funnel WHERE event_name = 'page_voice_design_exposure') as exposure,
        (SELECT users FROM entry) as entry,
        (SELECT users FROM funnel WHERE event_name = 'voice_design_generate_click') as generate,
        (SELECT users FROM funnel WHERE event_name = 'voice_design_select_click') as select_voice,
        (SELECT users FROM funnel WHERE event_name = 'voice_design_save_success') as save
    """
    rows = run_query(query)
    if rows:
        return {
            '曝光': rows[0]['exposure'] or 0,
            '进入创编页': rows[0]['entry'] or 0,
            '生成音色': rows[0]['generate'] or 0,
            '选择音色': rows[0]['select_voice'] or 0,
            '保存音色': rows[0]['save'] or 0,
        }
    return {'曝光': 0, '进入创编页': 0, '生成音色': 0, '选择音色': 0, '保存音色': 0}

def delta_str(current, previous):
    """生成 DoD 变化字符串"""
    if previous == 0:
        return "NEW" if current > 0 else "-"
    pct = (current - previous) / previous * 100
    if pct > 0:
        return f"+{pct:.1f}%"
    elif pct < 0:
        return f"{pct:.1f}%"
    return "0%"

def pct(a, b):
    """计算百分比"""
    return f"{a/b*100:.1f}%" if b > 0 else "-"

def build_lark_card(today_data, yesterday_data, history_data):
    """构造飞书卡片"""
    now = datetime.now(BEIJING_TZ)
    date_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # 大盘数据
    exp = today_data['曝光']
    entry = today_data['进入创编页']
    gen = today_data['生成音色']
    sel = today_data['选择音色']
    save = today_data['保存音色']

    # 前日数据
    p_exp = yesterday_data['曝光']
    p_entry = yesterday_data['进入创编页']
    p_gen = yesterday_data['生成音色']
    p_sel = yesterday_data['选择音色']
    p_save = yesterday_data['保存音色']

    # 有历史生成的用户数据
    h_exp = history_data['曝光']
    h_save = history_data['保存音色']

    content = f"""**大盘漏斗（昨日）**

**曝光** {exp:,}人 (DoD {delta_str(exp, p_exp)})
  ↓ {pct(entry, exp)}
**进入创编页** {entry:,}人 (DoD {delta_str(entry, p_entry)})
  ↓ {pct(gen, entry)}
**生成音色** {gen:,}人 (DoD {delta_str(gen, p_gen)})
  ↓ {pct(sel, gen)}
**选择音色** {sel:,}人 (DoD {delta_str(sel, p_sel)})
  ↓ {pct(save, sel)}
**保存音色** {save:,}人 (DoD {delta_str(save, p_save)})

---
**整体转化率（曝光→保存）**
- 大盘: **{pct(save, exp)}** ({exp:,}人曝光)
- 有生成历史用户: **{pct(h_save, h_exp)}** ({h_exp:,}人曝光)"""

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"Voice Design 日报 - {date_str}"},
                "template": "purple",
            },
            "elements": [{"tag": "markdown", "content": content}],
        },
    }
    return card

def send_to_lark(card):
    """发送到飞书"""
    resp = requests.post(LARK_WEBHOOK_URL, json=card, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0 and body.get("StatusCode") != 0:
        raise RuntimeError(f"Lark error: {body}")
    print("OK - feishu push success")

def main():
    print("Generating Voice Design report...")

    yesterday = "_TABLE_SUFFIX = FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))"
    day_before = "_TABLE_SUFFIX = FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY))"

    print("  Fetching overall funnel...")
    today_data = get_funnel_data(yesterday)
    yesterday_data = get_funnel_data(day_before)

    print("  Fetching users with history...")
    history_data = get_funnel_data_with_history(yesterday)

    print("  Building card...")
    card = build_lark_card(today_data, yesterday_data, history_data)

    print("  Sending to Lark...")
    send_to_lark(card)
    print("Done!")

if __name__ == "__main__":
    main()
