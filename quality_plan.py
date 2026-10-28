"""Prepare a reviewable, offline motion comparison. This module cannot submit jobs."""
import argparse
from decimal import Decimal
import html
import json
from pathlib import Path
import sqlite3

from generation_spec import digest, encoded, payload, snapshot

SOURCE_JOB = 'cafc6791-c83d-4ec1-8a64-ac2d2870fdd0'
SEED = 20261029
PRICE_SOURCE = 'https://help.aliyun.com/zh/model-studio/wan2-6-t2v'
API_SOURCE = 'https://help.aliyun.com/zh/model-studio/legacy-wan-text-to-video-api-reference'
COMMON = ('15秒，16:9，连续单镜头，中近景包含头、颈、肩和腰。暗调中式木质戏台，'
          '一位成年女性，穿红黑苗疆风服饰，佩戴银色头饰，衣着完整。背景垂挂红丝与黄色符纸，'
          '少量青蓝薄雾，暗红侧光。人物始终只有一个头部，保持同一面容、服装和身体比例；'
          '头、颈、肩连续连接，五官随头部真实朝向被遮挡或显露。双手自然垂于身体两侧，'
          '没有舞蹈、物体变形或人物生成。背景物体固定在原位置，允许随观察角度变化产生自然视差。')
MOTIONS = (
    ('subject-turn', 'A · 镜头固定，人物转身',
     '相机全程固定位置、高度、焦距与取景。0至2秒人物正面静立；2至8秒人物全身在原地'
     '匀速向左转180度，直到背对镜头，头、颈、肩和躯干自然同步转动；8至14秒按原路匀速'
     '转回正面；14至15秒正面静立。相机不环绕、不推拉。'),
    ('camera-orbit', 'B · 人物静立，镜头环绕',
     '人物全程保持同一身体朝向，头、颈、肩和躯干静止。0至2秒相机在人物正前方停留；'
     '2至8秒相机围绕人物按固定半径、高度和焦距匀速移动180度，直到人物正后方；'
     '8至14秒相机按原路匀速返回正前方；14至15秒停留。相机不推拉，人物不追随镜头转头。'),
)
ENDING = '全程保留环境底噪，没有对白。动作速度均匀，前后朝向变化连续，画面不切换。'


def prepare(original_prompt, source_job=SOURCE_JOB):
    cases = []
    for identifier, title, motion in MOTIONS:
        prompt = COMMON + motion + ENDING
        spec = snapshot({'prompt': prompt, 'aspect': '16:9', 'duration': 15})
        spec['parameters']['seed'] = SEED
        body = payload(spec)
        cases.append({'id': identifier, 'title': title, 'status': 'PREPARED',
                      'snapshot': spec, 'request_body': body,
                      'request_sha256': digest(encoded(body)), 'list_cost_cents': 900})
    return {'version': 1, 'status': 'PREPARED', 'prepared_on': '2026-10-29',
            'source': {'job_id': source_job, 'original_prompt': original_prompt,
                       'original_prompt_sha256': digest(original_prompt.encode()),
                       'historical_parameter_state': 'legacy_unrecorded'},
            'purpose': '筛查同一题材中人物转身与相机环绕的方向连续性和人体一致性',
            'cases': cases, 'new_generation_calls': 0,
            'budget': {'region': '北京', 'unit_cny_per_second': '0.60', 'seconds': 30,
                       'list_cost_cents': sum(c['list_cost_cents'] for c in cases),
                       'free_quota': '未重新核对', 'actual_cash_spend_cents': 0,
                       'approval': '待另行确认本轮预算', 'price_checked_on': '2026-10-29',
                       'price_source': PRICE_SOURCE},
            'limits': ['两条是筛查样本，不能据此计算失败率或证明长提示词必然导致变形。',
                       '扩写沿用产品当前设置；wan2.6不返回扩写后的实际文本，因此扩写仍是不可直接观察的变量。',
                       '共享seed便于复核，但不保证两条视觉内容相同，也不保证重复生成逐帧一致。',
                       '此方案只隔离相对视角变化，不验收完整原文的剧情、台词、音效或8K画质。']}


def render(plan):
    e = lambda value: html.escape(str(value))
    cases = ''
    for case in plan['cases']:
        cases += f'''<article><span class="tag">未生成 · 15 秒</span><h2>{e(case['title'])}</h2>
<p class="cost">公开标价 ¥9.00 · 同一模型与测试种子</p>
<label for="{e(case['id'])}">拟提交的完整描述</label><textarea id="{e(case['id'])}" readonly>{e(case['snapshot']['submitted_prompt'])}</textarea>
<details><summary>查看拟提交参数与请求指纹</summary><pre>{e(json.dumps(case['request_body'], ensure_ascii=False, indent=2))}</pre>
<p class="hash">SHA256 {e(case['request_sha256'])}</p></details></article>'''
    limits = ''.join('<li>'+e(value)+'</li>' for value in plan['limits'])
    cost = Decimal(plan['budget']['list_cost_cents']) / 100
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>序幕 · 15 秒动作对照方案</title><style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(ellipse at 15% 0,#343b68 0,transparent 45rem),#0a1020;color:#f0eef9;font:16px/1.85 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}a{{color:#dfc4ff;text-underline-offset:5px}}header,main{{max-width:1120px;margin:auto;padding:25px 28px}}header{{display:flex;justify-content:space-between;border-bottom:1px solid #ffffff20;font-size:14px}}main{{padding-top:52px;padding-bottom:70px}}.eyebrow,.muted,.cost{{color:#b9c3de;font-size:14px}}.eyebrow{{letter-spacing:.17em}}h1{{font-size:clamp(30px,4vw,44px);font-weight:500;line-height:1.45}}h2{{font-size:22px;font-weight:500}}h3{{font-size:19px;font-weight:500}}p{{max-width:880px}}.note,article,.panel{{border:1px solid #c8b5f345;background:#c2b1f00b;border-radius:18px;padding:24px}}.note{{margin:27px 0}}.note strong{{color:#e2cdff;font-size:22px;font-weight:500}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:24px 0}}article{{background:linear-gradient(140deg,#242d49b8,#171e35b8)}}.tag{{display:inline-block;font-size:12px;padding:3px 11px;border:1px solid #d7b7f55c;border-radius:30px;color:#d9c6f2}}label{{display:block;font-size:14px;margin:16px 0 9px}}textarea{{width:100%;min-height:420px;resize:vertical;padding:16px;border:1px solid #c8b5f345;border-radius:12px;color:#e3e6f4;background:#080e1e80;font:14px/1.95 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}textarea:focus-visible,summary:focus-visible{{outline:2px solid #d4baf4;outline-offset:3px}}details{{margin-top:16px}}summary{{cursor:pointer;color:#e0cbfb;padding:7px 0;font-size:14px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.65;background:#090d1b7a;padding:14px;border-radius:9px}}.hash{{font-size:12px;overflow-wrap:anywhere;color:#b9c3de}}li{{padding:5px 0}}.panel{{margin:22px 0}}.original textarea{{min-height:230px}}table{{width:100%;border-collapse:collapse;font-size:14px}}td{{padding:10px;border-bottom:1px solid #ffffff19;vertical-align:top}}td:first-child{{color:#b9c3de;width:140px}}footer{{border-top:1px solid #ffffff20;padding-top:20px;margin-top:35px;font-size:13px;color:#b9c3de}}@media(max-width:740px){{.grid{{grid-template-columns:1fr}}header,main{{padding-left:20px;padding-right:20px}}article,.panel,.note{{padding:18px}}textarea{{min-height:460px}}td:first-child{{width:95px}}}}
</style><header><span>PRELUDE / 序幕 · 内部质量验证</span><a href="/">检查报告</a></header><main>
<div class="eyebrow">MOTION STUDY / 2026.10.29</div><h1>把人物转身与镜头环绕，分别看清</h1>
<p>两条 15 秒筛查方案，沿用当前文生模型与扩写设置，观察相对视角改变时，五官、头颈和身体能否连续保持。下方是待确认方案，没有提交生成。</p>
<div class="note"><strong>准备完成 · 本轮实际支出 ¥0</strong><p>两条共 30 秒，按北京区 720P ¥0.60/秒计算，公开标价合计 ¥{cost:.2f}。免费额度尚未重新核对。此前的 9 元上限对应已完成的另一轮，不作为本轮授权；预算确认后才可生成。<a href="{PRICE_SOURCE}" target="_blank" rel="noreferrer">官方定价 ↗</a></p></div>
<section class="panel"><h2>共同设置</h2><table><tr><td>模型 / 输入</td><td>wan2.6-t2v / 纯文字，不使用参考首帧</td></tr><tr><td>画幅 / 输出</td><td>16:9 · 1280×720 · 每条 15 秒</td></tr><tr><td>生成设置</td><td>prompt_extend=true · shot_type=single · watermark=false</td></tr><tr><td>拟用 seed</td><td>{SEED}，两条共用；普通创作入口仍保持随机</td></tr><tr><td>时间结构</td><td>0–2 秒正面停留 → 2–8 秒相对视角到背面 → 8–14 秒返回正面 → 14–15 秒停留</td></tr></table></section>
<div class="grid">{cases}</div>
<section class="panel"><h2>怎样验收</h2><ol><li>完整播放，先确认 A 是人物转身、B 是相机环绕，时间顺序和前后朝向得到执行。</li><li>重点慢看朝向变化区间，并按约 0.25 秒抽帧核对。五官不能从后脑或另一侧凭空长出；不能出现额外脸、额外头、头颈与肩部断接。</li><li>对比开头和结尾的面容、服装、比例与站位。记录问题发生的秒数，保留原片，不只展示最好一帧。</li><li>同时记录提交、生成完成、保存完成、首次可播放与重播等待时间，将成片结构问题和网络播放问题分别验收。</li></ol><p class="muted">任何一条不通过都先留证和分析，不自动补抽；这两条尚未产生结果。</p></section>
<section class="panel"><h2>这轮能回答什么</h2><ul>{limits}</ul><p class="muted"><a href="{API_SOURCE}" target="_blank" rel="noreferrer">官方 API：seed、扩写与单镜头条件 ↗</a></p></section>
<details class="panel original"><summary>保留的完整原文 · {len(plan['source']['original_prompt'])} 字，未改写历史作品</summary><textarea aria-label="历史完整原文" readonly>{e(plan['source']['original_prompt'])}</textarea><p class="hash">原文 SHA256 {e(plan['source']['original_prompt_sha256'])}</p><p class="muted">这是原有复杂剧情的保留副本。A/B 有意去掉罗盘穿门、爆裂生成人物、舞蹈和对白，先定位朝向变化；不会覆盖你的原作品或草稿。历史任务没有完整参数快照，不按当前默认值补写。</p></details>
<footer>PREPARED · 本页只供审阅，不提供生成操作，不读取密钥，不改变作品、通行证或额度。</footer></main></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('.studio'))
    parser.add_argument('--output-dir', type=Path, default=Path('preview/quality-20261029'))
    parser.add_argument('--html', type=Path, default=Path('preview/audit-20261028/quality-plan.html'))
    args = parser.parse_args()
    with sqlite3.connect((args.data_dir/'studio.sqlite3').resolve().as_uri()+'?mode=ro', uri=True) as db:
        row = db.execute('SELECT prompt FROM jobs WHERE id=?', (SOURCE_JOB,)).fetchone()
    if row is None:
        raise SystemExit('Source work missing; no plan written')
    plan = prepare(row[0])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2)+'\n')
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.html.write_text(render(plan))
    print(f'PREPARED: {len(plan["cases"])} cases; 0 submissions; list price ¥18.00')


if __name__ == '__main__':
    main()
