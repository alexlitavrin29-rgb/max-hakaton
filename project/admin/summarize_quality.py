"""Summarize completed working runs without combining versions or claiming holdout."""
import importlib.util
import json
from pathlib import Path

ROOT=Path('docs/test-results/quality-2026-09-26')
spec=importlib.util.spec_from_file_location('acceptance',Path('docs/quality-acceptance/evaluate.py'))
stats=importlib.util.module_from_spec(spec);spec.loader.exec_module(stats)
GROUPS={
 'all_nine':['W01'],'order':['W02','W16'],'parent':['W03','W04'],
 'corrections':['W05','W06','W07'],'money':['W08','W09','W17'],
 'education_negation':['W10'],'ambiguous_geography':['W11','W12'],
 'occupation_choice':['W13','W14'],'schedule_negation':['W15'],'vacancy_conflicts':['W18','W19'],
 'rental_basic_and_money':['H01','H02','H03','H04','H05'],
 'rental_geography':['H06','H11','H14'],'unsupported':['H07','H08','H13'],
 'different_intent':['H09','H10'],'listing_conflicts':['H12'],
}

def main():
    results=[]
    for name in ['live-development-first.json','live-development-final.json','live-development-accepted.json']:
        run=json.loads((ROOT/name).read_text(encoding='utf-8'))
        assert len(run['cases'])==33 and len({c['id'] for c in run['cases']})==33,'Do not summarize unfinished runs'
        summary=dict(file=name,code_sha256=run['code_sha256'],scenario_sha256=run['scenario_sha256'],oracle_sha256=run['oracle_sha256'],
                     cost_upper_rub=run['cost_upper_rub'],branches={},groups={},errors=[])
        calls=[usage for c in run['cases'] for t in c['turns'] for usage in t['usage']]
        summary['provider_calls']=len(calls)
        summary['usage_tokens']={k:sum((c.get('usage') or {}).get(k,0) or 0 for c in calls) for k in ['prompt_tokens','completion_tokens','total_tokens']}
        summary['estimated_rub_no_cache']=summary['usage_tokens']['prompt_tokens']*20/1e6+summary['usage_tokens']['completion_tokens']*40/1e6
        for b in ['work','rental']:
            cases=[c for c in run['cases'] if c['branch']==b]
            summary['branches'][b]=dict(metrics=[stats.intervals(sum(c['metrics'][i] for c in cases),len(cases)) for i in range(3)],
                provider_available=sum(c['available'] for c in cases),total=len(cases),
                end_to_end=sum(all(c['metrics']) and c['available'] for c in cases),source='controlled fixtures')
        for group,ids in GROUPS.items():
            rows=[c for c in run['cases'] if c['id'] in ids]
            summary['groups'][group]=dict(ids=ids,total=len(rows),successes=[sum(c['metrics'][i] for c in rows) for i in range(3)])
        summary['errors']=[dict(id=c['id'],turn=n+1,errors=t['errors']) for c in run['cases'] for n,t in enumerate(c['turns']) if any(t['errors'])]
        results.append(summary)
    (ROOT/'summary.json').write_text(json.dumps(dict(kind='development_only_not_holdout',runs=results),ensure_ascii=False,indent=2),encoding='utf-8')
    final=results[-1]
    lines=['## Результаты заключительной рабочей серии','',
        'Это рабочие примеры после исправлений, **не независимая оценка качества**. Интервалы ниже — формальный биномиальный расчёт для этих чисел, без права обобщать его на пользователей.','',
        '| Ветка | Показатель полного диалога | Успех / ошибки | Доля | Точный двусторонний 95% ДИ | Односторонняя нижняя граница, Bonferroni ×6 |',
        '|---|---|---:|---:|---:|---:|']
    for b,label in [('work','Работа'),('rental','Жильё')]:
        for metric,title in zip(final['branches'][b]['metrics'],['Условия каждого хода','Уточнение или поиск','Запросы и карточки']):
            lo,hi=metric['cp_two_sided_95']
            lines.append(f"| {label} | {title} | {metric['successes']} / {metric['errors']} | {metric['observed']:.1%} | {lo:.2%}–{hi:.2%} | {metric['cp_one_sided_bonferroni_6_lower']:.2%} |")
    lines+=['','| Сложная группа | Диалогов | Условия / решение / карточки |','|---|---:|---:|']
    for group,data in final['groups'].items():lines.append(f"| {group}: {', '.join(data['ids'])} | {data['total']} | {' / '.join(map(str,data['successes']))} |")
    for b,label in [('work','Работа'),('rental','Жильё')]:
        v=final['branches'][b];lines+=['',f"{label}: доступность провайдера на уровне полного диалога {v['provider_available']}/{v['total']}; сквозной успех с включёнными тайм-аутами {v['end_to_end']}/{v['total']}. Источник в этой серии — fixture, его искусственная доступность не является живой метрикой."]
    lines+=['',f"Runtime SHA-256: `{final['code_sha256']}`. Вызовов LLM: {final['provider_calls']}; токены: {final['usage_tokens']}. Консервативный предел использованной серии: {final['cost_upper_rub']:.2f} ₽; оценка по usage без скидки за кеш: {final['estimated_rub_no_cache']:.2f} ₽.",
        '', 'В заключительной рабочей серии критических ошибок не выявлено в пределах её оракулов. Это не доказывает их отсутствия за пределами набора.',
        '', 'Закрытый контроль не проводился: выполнено 0 из требуемых 500 диалогов работы и 0 из 500 жилья. Все шесть показателей, доверительные интервалы и статус критических ошибок независимого контроля **не измерены**.',
        '', '391 автоматический тест прошёл: 383 проверки проекта и 8 проверок статистического оценщика. Отдельно проходят два теста каналов на одном снимке; они включены в 383, повторно к общему числу не прибавляются.',
        '', 'Предыдущие серии (не суммируются с заключительной):','', '| Серия | Работа: три метрики | Жильё: три метрики |','|---|---|---|']
    for v in results[:-1]:
        values=[' / '.join(str(m['successes'])+'/'+str(m['total']) for m in v['branches'][b]['metrics']) for b in ['work','rental']]
        lines.append(f"| {v['file']} | {values[0]} | {values[1]} |")
    (ROOT/'metrics.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(final['branches'],ensure_ascii=False,indent=2))

if __name__=='__main__':main()
