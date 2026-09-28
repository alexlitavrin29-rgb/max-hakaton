"""Extract code/name pairs from Rosstat's archival OKPDTR publication."""
import csv,gzip,json,re,hashlib
from pathlib import Path
import openpyxl

def main():
    source=Path('tmp/work-v3/occupations-2025.xlsx');rows={}
    workbook=openpyxl.load_workbook(source,read_only=True,data_only=True)
    for sheet in workbook.worksheets[:4]:
        for row in sheet.values:
            code=str(row[0] or '')
            if re.fullmatch(r'[12]\d{4}',code) and isinstance(row[1],str):rows[code]=row[1].strip()
    path=Path('project/llm/data/occupations.csv.gz')
    with gzip.open(path,'wt',encoding='utf-8',newline='') as f:
        w=csv.writer(f);w.writerow(['code','name']);w.writerows(sorted(rows.items()))
    metadata=json.loads(Path('tmp/work-v3/occupations-source.json').read_text(encoding='utf8'))
    path.with_name('occupations-manifest.json').write_text(json.dumps(dict(source='Росстат, справочник ОКПДТР 2025 года',
        **metadata,downloaded='2026-09-23',
        records=len(rows),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        limitation='Архивный словарь названий, не действующая классификация ОК 016-2025. Не ограничивает ввод.'),ensure_ascii=False,indent=2),encoding='utf-8')
    print('Occupation vocabulary:',len(rows))

if __name__=='__main__':main()
