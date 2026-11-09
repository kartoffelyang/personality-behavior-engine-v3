import json,sys,statistics
from pathlib import Path
root=Path(sys.argv[1]);data=json.loads((root/'summary.json').read_text(encoding='utf-8'));rows=sorted(data['rows'],key=lambda r:(r['case_id'],r['persona']))
lines=['# 固定六情境 × 两个人格测试','', '按主页面原始情境输入，12组独立运行Step2；并发2组，使用系统内置重试，不额外重跑失败组。耗时为各步骤完整处理时间（含内部重试）。未执行不记为调用失败。','',f"最终成功：{sum(r['success'] for r in rows)}/{len(rows)}；批次耗时：{data['elapsed_seconds']:.2f}秒。",'', '|情境|人格|Step2秒|Step7秒|Step8秒|最终结果|','|---|---|---:|---:|---:|---|']
for r in rows:
 times=[str(r['steps'].get(str(n),{}).get('seconds') or '—') for n in [2,7,8]]
 lines.append('|'+ '|'.join([r['title'],r['persona'],*times,'成功' if r['success'] else '未完成'])+'|')
lines+=['','## 分步统计','', '|步骤|执行次数|最终成功|最终失败|平均秒|最短秒|最长秒|失败尝试数|','|---|---:|---:|---:|---:|---:|---:|---:|']
for n in [2,7,8]:
 ss=[r['steps'][str(n)] for r in rows if r['steps'].get(str(n),{}).get('seconds') is not None]
 ts=[s['seconds'] for s in ss];fail=sum(sum(a['status']!='ok' for a in s['attempts']) if s.get('attempts') else s['status']!='ok' for s in ss)
 lines.append(f"|Step{n}|{len(ss)}|{sum(s['status']=='ok' for s in ss)}|{sum(s['status']!='ok' for s in ss)}|{statistics.mean(ts):.2f}|{min(ts):.2f}|{max(ts):.2f}|{fail}|")
lines+=['','## 失败及告警明细','']
for r in rows:
 for n,s in r['steps'].items():
  for a in s.get('attempts') or []:
   if a['status']!='ok':lines.append(f"- {r['case_id']} {r['persona']} Step{n} 第{a['attempt']}次尝试：{json.dumps(a.get('error_info') or a.get('error'),ensure_ascii=False)}")
  if s.get('error'):lines.append(f"- {r['case_id']} {r['persona']} Step{n} 最终错误：{json.dumps(s['error'],ensure_ascii=False)}")
  if s.get('warnings'):lines.append(f"- {r['case_id']} {r['persona']} Step{n} 告警：{json.dumps(s['warnings'],ensure_ascii=False)}")
 if r.get('exception'):lines.append(f"- {r['case_id']} {r['persona']} 异常：{r['exception']}")
(root/'测试报告.md').write_text('\n'.join(lines),encoding='utf-8')
print('\n'.join(lines))
