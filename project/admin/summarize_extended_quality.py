"""Describe open runs separately; never certify independent acceptance."""
import importlib.util
import json
from pathlib import Path

ROOT = Path('docs/test-results/quality-independent-2026-09-26/development')
spec = importlib.util.spec_from_file_location('quality_stats', 'docs/quality-acceptance/evaluate.py')
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)


def main():
    summaries = []
    for filename in ('live-first.json', 'live-after-work.json', 'live-after-housing.json', 'live-address-final.json'):
        run = json.loads((ROOT / filename).read_text(encoding='utf-8'))
        expected = 20 if filename == 'live-first.json' else 1 if filename == 'live-address-final.json' else 10
        assert len(run['cases']) == expected and len({c['id'] for c in run['cases']}) == expected
        calls = [u for c in run['cases'] for t in c['turns'] for u in t['usage']]
        tokens = {k: sum((u.get('usage') or {}).get(k, 0) or 0 for u in calls)
                  for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        summary = dict(file=filename, runtime_sha256=run['code_sha256'], scenario_sha256=run['scenario_sha256'],
                       oracle_sha256=run['oracle_sha256'], calls=len(calls), usage=tokens,
                       estimated_rub_no_cache=tokens['prompt_tokens'] * 20 / 1e6 + tokens['completion_tokens'] * 40 / 1e6,
                       preflight_bound_rub=run['cost_upper_rub'], branches={}, errors=[])
        for branch in ('work', 'rental'):
            cases = [c for c in run['cases'] if c['branch'] == branch]
            if not cases:
                continue
            summary['branches'][branch] = dict(
                metrics={name: stats.intervals(sum(c['metrics'][i] for c in cases), len(cases))
                         for i, name in enumerate(('conditions', 'decision', 'results'))},
                provider_available=sum(c['available'] for c in cases), total=len(cases),
                end_to_end=sum(c['available'] and all(c['metrics']) for c in cases), source='fixtures')
        summary['errors'] = [dict(id=c['id'], turn=i+1, errors=t['errors'])
                             for c in run['cases'] for i, t in enumerate(c['turns']) if any(t['errors'])]
        summaries.append(summary)
    (ROOT / 'summary.json').write_text(json.dumps(dict(kind='open_development_only', runs=summaries),
                                                  ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Новые открытые живые диалоги', '',
             'Это рабочие проверки, не независимая оценка. Интервалы ниже — только описательный биномиальный расчёт; '
             'он не корректирует подбор открытых примеров/повторы и не доказывает качество для пользователей.', '',
             '| Серия | Ветка | Показатель | Успех / ошибки | Доля | CP95 | Нижняя ×6 |',
             '|---|---|---|---:|---:|---:|---:|']
    for run in summaries:
        for branch, result in run['branches'].items():
            for name, m in result['metrics'].items():
                lo, hi = m['cp_two_sided_95']
                lines.append(f"| {run['file']} | {branch} | {name} | {m['successes']} / {m['errors']} | "
                             f"{m['observed']:.1%} | {lo:.2%}–{hi:.2%} | {m['cp_one_sided_bonferroni_6_lower']:.2%} |")
    lines += ['', 'Разные серии не складываются. Две after-серии — заранее разделённые ветки одной рабочей проверки; '
              'их runtime-хеши совпадают. Последующая поправка распознавания номера дома проверена отдельно '
              'соседними тестами, replay и отдельным живым D-H11; исходная after-серия не объявляется запуском последнего хеша.', '',
              'Первая серия: ошибочное снятие жилья от работодателя (D-W04), сохранение цены комнаты как бюджета квартиры (D-H05). '
              'Оба воспроизведения исправлены. В after-сериях ошибок в пределах рабочего оценщика не найдено; '
              'его ограничения описаны в README. Доступность провайдера:20/20 в первой серии,10/10 и10/10 в after. '
              'Доступность fixture не равна доступности реального источника.', '',
              'Каждая сложная новая ситуация представлена лишь одним диалогом: снять/вернуть опыт, занятость, жильё, '
              'бюджет и город; оплата за смену и диапазон с налогами; родитель/сын; отрицание графика; прошлое место; '
              'учёба в колледже; непроверяемые требования; комната, покупка и недельная оплата. '
              'Качество99% ни для одной этой подгруппы не подтверждено.', '',
              f"По четырём сетевым файлам: {sum(r['calls'] for r in summaries)} вызовов; оценка по usage без кеш-скидки "
              f"{sum(r['estimated_rub_no_cache'] for r in summaries):.5f} ₽. Это не фактический счёт провайдера. "
              'Полные usage и отдельные хеши — в summary.json. Sandbox connection-серия хранится отдельно.', '',
              '**Независимый контроль:0 диалогов исполнено; все шесть показателей и их интервалы не измерены.**']
    (ROOT / 'metrics.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps([dict(file=r['file'], calls=r['calls'], estimated_rub=r['estimated_rub_no_cache'])
                      for r in summaries], ensure_ascii=False))


if __name__ == '__main__':
    main()
