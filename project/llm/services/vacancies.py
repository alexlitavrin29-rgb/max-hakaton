"""Provider-independent vacancies returned to backend business logic."""

from dataclasses import dataclass


@dataclass(frozen=True)
class VacancyContact:
    kind: str
    value: str


@dataclass(frozen=True)
class Vacancy:
    id: str
    source: str
    title: str
    company: str
    salary_from: float | None
    salary_to: float | None
    region: str | None
    city: str | None
    address: str | None
    experience: str | None
    # None is missing data; False is an explicit negative in the source record.
    accommodation: bool | None
    requirements: str | None
    responsibilities: str | None
    url: str
    contacts: tuple[VacancyContact, ...] = ()
    contact_person: str | None = None
    education: str | None = None
    schedule: str | None = None
    employment: str | None = None
    salary_period: str | None = None
    salary_tax: str | None = None
    experience_min_years: int | None = None
    work_formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class VacancyPage:
    items: tuple[Vacancy, ...]
    total: int
    limit: int
    # Trudvsem uses a zero-based PAGE number, not a number of skipped records.
    offset: int
    attempt_count: int = 1
    stale_age_seconds: int | None = None

    @property
    def next_offset(self) -> int | None:
        if self.items and (self.offset + 1) * self.limit < self.total:
            return self.offset + 1
        return None
