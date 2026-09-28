"""One real public search, printed as readable text rather than raw JSON."""

import argparse
import asyncio

from .integrations.trudvsem import TrudvsemError, search_vacancies


async def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка поиска «Работа России» без API-ключа")
    parser.add_argument("text", nargs="?", default="грузчик")
    parser.add_argument("--region-code")
    parser.add_argument("--experience-from", type=int)
    parser.add_argument("--experience-to", type=int)
    parser.add_argument("--accommodation", action="store_true", default=None)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    try:
        page = await search_vacancies(**vars(args))
    except TrudvsemError as error:
        print(f"Сейчас не получилось получить вакансии. Категория: {error.code}.")
        return 1
    except ValueError as error:
        parser.error(str(error))
    print(f"Источник: Работа России. Всего по данным API: {page.total}. Получено: {len(page.items)}.")
    if not page.items:
        print("По этим условиям вакансий не найдено. Можно изменить условия поиска.")
    for vacancy in page.items:
        print(f"\n{vacancy.title} — {vacancy.company}")
        print(vacancy.address or vacancy.region or "Место работы не указано")
        lower = f"от {vacancy.salary_from:,.0f}" if vacancy.salary_from is not None else ""
        upper = f"до {vacancy.salary_to:,.0f}" if vacancy.salary_to is not None else ""
        print(f"Зарплата: {' '.join(part for part in (lower, upper) if part) or 'не указана'}")
        print(f"Опыт (лет по данным источника): {vacancy.experience or 'не указан'}")
        print("Жильё: указано источником" if vacancy.accommodation else "Жильё: не подтверждено")
        print(f"Открыть вакансию: {vacancy.url}")
    if page.next_offset is not None:
        print(f"\nСледующая страница: --offset {page.next_offset} (с тем же --limit).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
