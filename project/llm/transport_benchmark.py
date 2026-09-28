"""Fixture-only transport benchmark; it never opens a network connection."""

import asyncio
import json
import math
import statistics
import time


RUNS = 20


def percentile(values, fraction):
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


async def delay(milliseconds):
    await asyncio.sleep(milliseconds / 1000)


async def serial_users(delays):
    for value in delays:
        await delay(value)


async def bounded_users(delays, limit=4):
    slots = asyncio.Semaphore(limit)

    async def one(value):
        async with slots:
            await delay(value)

    await asyncio.gather(*(one(value) for value in delays))


async def pipeline(*delays):
    for value in delays:
        await delay(value)


async def measure(factory):
    samples = []
    for _ in range(RUNS):
        started = time.perf_counter()
        await factory()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "p50_ms": round(statistics.median(samples), 2),
        "p90_ms": round(percentile(samples, .90), 2),
        "p95_ms": round(percentile(samples, .95), 2),
        "max_ms": round(max(samples), 2),
    }


async def main():
    scenarios = {
        "rental_fast": (lambda: pipeline(2, 1), lambda: pipeline(1)),
        "rental_slow_locations": (lambda: pipeline(3, 15, 1), lambda: pipeline(15, 1)),
        "rental_slow_search": (lambda: pipeline(3, 1, 18), lambda: pipeline(1, 18)),
        "rental_slow_llm": (lambda: pipeline(3, 20, 1, 2), lambda: pipeline(20, 1, 2)),
        "work_one_page": (lambda: pipeline(3, 8), lambda: pipeline(8)),
        "work_slow_llm": (lambda: pipeline(3, 20, 8), lambda: pipeline(20, 8)),
        "max_ten_equal": (lambda: serial_users([8] * 10), lambda: bounded_users([8] * 10)),
        "max_one_slow_nine_fast": (
            lambda: serial_users([30] + [3] * 9),
            lambda: bounded_users([30] + [3] * 9),
        ),
        # Card messages cannot be merged safely because their buttons belong to each card.
        "max_five_cards": (lambda: serial_users([2] * 5), lambda: serial_users([2] * 5)),
        "miniapp_full_semaphore": (lambda: pipeline(12, 3), lambda: pipeline(12, 3)),
    }
    result = {}
    for name, (before, after) in scenarios.items():
        result[name] = {"before": await measure(before), "after": await measure(after)}
    result["counters"] = {
        "clients_before_per_two_calls": 2,
        "clients_after_per_two_calls": 1,
        "network_calls_before_per_two_calls": 2,
        "network_calls_after_per_two_calls": 2,
        "cached_location_network_calls_per_two_equal_lookups": 1,
        "max_messages_before_five_cards": 5,
        "max_messages_after_five_cards": 5,
        "location_cache_hit_rate_fixture": .5,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
