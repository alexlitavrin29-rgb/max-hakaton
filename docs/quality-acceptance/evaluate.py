"""Exact binomial summaries; does not certify corpus independence or oracle quality."""

import argparse
import json
import math
from pathlib import Path


METRICS = ("conditions", "decision", "results")
BRANCHES = ("work", "housing")


def binomial_sum(n, p, start, stop):
    if p == 0:
        return float(start == 0)
    if p == 1:
        return float(stop == n)
    logs = [
        math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
        + k * math.log(p) + (n - k) * math.log1p(-p)
        for k in range(start, stop + 1)
    ]
    peak = max(logs)
    return min(1.0, math.exp(peak) * math.fsum(math.exp(x - peak) for x in logs))


def inverse_tail(n, k, alpha, lower):
    lo, hi = 0.0, 1.0
    for _ in range(70):
        mid = (lo + hi) / 2
        probability = binomial_sum(n, mid, k, n) if lower else binomial_sum(n, mid, 0, k)
        if (probability < alpha) == lower:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def intervals(successes, total, attempt_alpha=0.05):
    if type(successes) is not int or type(total) is not int or not 0 <= successes <= total or total < 1:
        raise ValueError("Require integer 0 <= successes <= total and total >= 1")
    if type(attempt_alpha) not in (float, int) or not math.isfinite(attempt_alpha) or not 0 < attempt_alpha <= 0.05:
        raise ValueError("attempt_alpha must be a finite number in (0, .05]")
    lower = 0.0 if successes == 0 else inverse_tail(total, successes, 0.025, True)
    upper = 1.0 if successes == total else inverse_tail(total, successes, 0.025, False)
    simultaneous_lower = 0.0 if successes == 0 else inverse_tail(total, successes, attempt_alpha / 6, True)
    return {
        "successes": successes, "errors": total - successes, "total": total,
        "observed": successes / total, "cp_two_sided_95": [lower, upper],
        "cp_one_sided_bonferroni_6_lower": simultaneous_lower,
    }


def string_list(value, name, nonempty=False):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{name} must be a list of nonempty strings")
    if nonempty and not value:
        raise ValueError(f"{name} must not be empty")
    if len(value) != len(set(value)):
        raise ValueError(f"{name} contains duplicate values")


def validate(data):
    expected = data.get("expected_ids", {})
    if set(expected) != set(BRANCHES):
        raise ValueError("expected_ids must contain exactly work and housing")
    all_expected = set()
    for branch in BRANCHES:
        string_list(expected[branch], f"expected_ids.{branch}", True)
        if all_expected.intersection(expected[branch]):
            raise ValueError("IDs must be unique across both branches")
        all_expected.update(expected[branch])
    records = data.get("dialogues")
    if not isinstance(records, list):
        raise ValueError("dialogues must be a list")
    seen = set()
    for row in records:
        identifier = row.get("id")
        branch = row.get("branch")
        if not isinstance(identifier, str) or identifier in seen:
            raise ValueError("Every dialogue needs a unique string id")
        if branch not in BRANCHES or identifier not in expected[branch]:
            raise ValueError(f"Unexpected dialogue or branch: {identifier}")
        seen.add(identifier)
        metrics = row.get("metrics", {})
        if set(metrics) != set(METRICS) or any(type(v) is not bool for v in metrics.values()):
            raise ValueError(f"{identifier}: exactly three boolean metrics required")
        string_list(row.get("groups"), f"{identifier}.groups", True)
        string_list(row.get("critical_errors"), f"{identifier}.critical_errors")
        if row["critical_errors"] and all(metrics.values()):
            raise ValueError(f"{identifier}: critical error must fail at least one metric")
        for component in ("provider_available", "source_available"):
            if component not in row or (row[component] is not None and type(row[component]) is not bool):
                raise ValueError(f"{identifier}.{component}: boolean or null required")
    if seen != all_expected:
        raise ValueError(f"Missing {len(all_expected - seen)} manifest dialogue(s); do not omit failures")
    return records


def proportion(values):
    return {"successes": sum(values), "total": len(values), "observed": sum(values) / len(values) if values else None}


def acceptance_plan(preregistration):
    """Legacy reports keep their original alpha; new attempts require the fixed plan."""
    if preregistration is None:
        return dict(attempt_index=None, attempt_alpha=0.05, minimum_n=dict.fromkeys(BRANCHES, 500), fixed_n=False)
    if not isinstance(preregistration, dict):
        raise ValueError('preregistration must be an object')
    index = preregistration.get('attempt_index')
    alpha = preregistration.get('attempt_alpha')
    if type(index) is not int or not 1 <= index <= 1000:
        raise ValueError('attempt_index must be an integer from 1 to 1000')
    intervals(0, 1, alpha)
    required_alpha = math.ldexp(0.05, -index)
    if not math.isclose(alpha, required_alpha, rel_tol=1e-12, abs_tol=0):
        raise ValueError('attempt_alpha must equal .05 / 2**attempt_index')
    sizes = preregistration.get('branch_sample_sizes')
    if not isinstance(sizes, dict) or set(sizes) != set(BRANCHES) or any(type(n) is not int or n < 500 for n in sizes.values()):
        raise ValueError('branch_sample_sizes requires an integer >= 500 for both branches')
    if preregistration.get('posthoc_n_increase_allowed') is not False:
        raise ValueError('The registered plan must prohibit posthoc sample size increases')
    if 'metric_alpha' in preregistration and (type(preregistration['metric_alpha']) not in (float, int) or
            not math.isclose(preregistration['metric_alpha'], alpha / 6, rel_tol=1e-12, abs_tol=0)):
        raise ValueError('metric_alpha must equal attempt_alpha / 6')
    return dict(attempt_index=index, attempt_alpha=alpha, minimum_n=dict(sizes), fixed_n=True)


def evaluate(data, preregistration=None):
    records = validate(data)
    plan = acceptance_plan(preregistration)
    alpha = plan['attempt_alpha']
    report = {
        "method": "Clopper-Pearson; two-sided alpha=.05; lower one-sided alpha=" + ('.05' if preregistration is None else str(alpha)) + "/6",
        "acceptance": "Not certified: independent reviewer must verify corpus, oracles, freeze and sampling assumptions",
        "branches": {},
    }
    if preregistration is not None:
        report['preregistration'] = dict(plan_id=preregistration.get('plan_id'), **plan,
                                         metric_alpha=alpha / 6, sample_size_rule='exact preregistered n; no posthoc increase')
    eligible = True
    for branch in BRANCHES:
        rows = [row for row in records if row["branch"] == branch]
        metrics = {metric: intervals(sum(row["metrics"][metric] for row in rows), len(rows), alpha) for metric in METRICS}
        critical = [{"id": row["id"], "errors": row["critical_errors"]} for row in rows if row["critical_errors"]]
        availability = {}
        for component in ("provider_available", "source_available"):
            attempted = [row[component] for row in rows if row[component] is not None]
            availability[component] = {**proportion(attempted), "not_required": len(rows) - len(attempted)}
        end_to_end = proportion([
            all(row["metrics"].values()) and row["provider_available"] is not False and row["source_available"] is not False
            for row in rows
        ])
        groups = {}
        for group in sorted({group for row in rows for group in row["groups"]}):
            members = [row for row in rows if group in row["groups"]]
            groups[group] = {metric: proportion([row["metrics"][metric] for row in members]) for metric in METRICS}
        size_met = len(rows) == plan['minimum_n'][branch] if plan['fixed_n'] else len(rows) >= plan['minimum_n'][branch]
        branch_met = size_met and not critical and all(value["cp_one_sided_bonferroni_6_lower"] >= 0.99 for value in metrics.values())
        eligible = eligible and branch_met
        report["branches"][branch] = {
            "metrics": metrics, "critical_errors": critical, "availability": availability,
            "end_to_end": end_to_end, "groups": groups, "statistical_threshold_met": branch_met,
        }
        if preregistration is not None:
            report['branches'][branch]['preregistered_sample_size_met'] = size_met
    report["statistical_threshold_met"] = eligible
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preregistration", type=Path, help="Public fixed-n alpha-spending plan for this attempt")
    args = parser.parse_args()
    plan = json.loads(args.preregistration.read_text(encoding='utf-8-sig')) if args.preregistration else None
    result = evaluate(json.loads(args.input.read_text(encoding="utf-8-sig")), plan)
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
