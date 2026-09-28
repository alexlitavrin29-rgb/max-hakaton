"""Execution-only freeze and conservative request budget guards; no runtime edits."""
import asyncio
import hashlib
import json
from decimal import Decimal
from pathlib import Path


class Ledger:
    """Exclusive, flushed hash-chain log. Existing attempts cannot be overwritten.

    Tampering is detectable by hashes; this is not filesystem WORM storage.
    """
    def __init__(self, path):
        self.file = Path(path).open('x', encoding='utf-8')
        self.previous = '0' * 64
        self.sequence = 0

    def append(self, value):
        import os
        self.sequence += 1
        item = dict(sequence=self.sequence, previous_sha256=self.previous, payload=value)
        encoded = json.dumps(item, sort_keys=True, ensure_ascii=False)
        self.previous = hashlib.sha256(encoded.encode()).hexdigest()
        self.file.write(json.dumps(dict(**item, sha256=self.previous), ensure_ascii=False) + '\n')
        self.file.flush()
        os.fsync(self.file.fileno())

    def close(self):
        self.file.close()


def claim_attempt(directory, attempt_index, plan_hash, output):
    path = Path(directory) / ('attempt-' + str(attempt_index) + '.claim.jsonl')
    ledger = Ledger(path)
    ledger.append(dict(event='attempt_claimed', preflight_sha256=plan_hash,
                      output=str(Path(output).resolve()), attempt_index=attempt_index))
    ledger.close()


def interleave_branches(rows):
    """Fixed manifest-order alternation; never inspect expectations or outcomes."""
    from itertools import zip_longest
    branches = [[row for row in rows if row['branch'] == branch] for branch in ('work', 'housing')]
    return [row for pair in zip_longest(*branches) for row in pair if row is not None]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_hashes(root, expected):
    bad = [name for name, digest in expected.items()
           if not (root / name).is_file() or sha256(root / name) != digest]
    if bad:
        raise RuntimeError('Frozen file mismatch: ' + ', '.join(bad))


class BudgetStopped(RuntimeError):
    pass


class Budget:
    """Reserve before each request; uncertain/error usage never releases reserve.

    Byte bound assumes the provider's byte-level tokenizer and no additional billed
    hidden input beyond a 4096-token framing allowance per message plus request.
    It is a local conservative guard, not a provider-enforced ruble spending cap.
    """
    def __init__(self, rubles, input_rate='20', output_rate='40'):
        self.limit = Decimal(str(rubles))
        self.input_rate = Decimal(input_rate)
        self.output_rate = Decimal(output_rate)
        self.committed = Decimal(0)
        self.reservations = {}
        self.actual_known = Decimal(0)
        self.lock = asyncio.Lock()

    def cost(self, input_tokens, output_tokens):
        return (self.input_rate * input_tokens + self.output_rate * output_tokens) / 1000000

    async def reserve(self, key, messages, max_tokens, response_schema=None):
        if type(max_tokens) is not int or not 0 < max_tokens <= 4096:
            raise BudgetStopped('Missing or unexpected output token ceiling')
        body = json.dumps(dict(messages=messages, schema=response_schema), ensure_ascii=False)
        bound = len(body.encode('utf-8')) + 4096 * (len(messages) + 1)
        ceiling = self.cost(bound, max_tokens)
        async with self.lock:
            if key in self.reservations:
                raise BudgetStopped('Duplicate request key; retries forbidden')
            if self.committed + ceiling > self.limit:
                raise BudgetStopped('Local budget exhausted before request')
            self.reservations[key] = ceiling
            self.committed += ceiling
        return dict(input_token_bound=bound, output_token_cap=max_tokens, reserve_rub=str(ceiling))

    async def settle(self, key, usage):
        if not isinstance(usage, dict):
            return
        inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if type(inp) is not int or type(out) is not int or min(inp, out) < 0:
            return
        actual = self.cost(inp, out)
        async with self.lock:
            reserve = self.reservations[key]
            self.committed += actual - reserve
            self.reservations[key] = actual
            self.actual_known += actual
            if actual > reserve:
                raise BudgetStopped('Provider usage exceeded conservative local bound')


def validate_semantic_review(corpus, captures, reviews):
    """No results are released without independent review of EVERY expected turn.

    Reviewer supplies explicit evidence and all three metric decisions against the
    preregistered oracle; executor cannot self-assign a passing semantic label.
    Extra/missing reviews, traces or unsupported true defaults are fatal.
    """
    expected = {(r['id'], t['index']) for r in corpus for t in r['turns']}
    def index(rows):
        result = {(r['id'], r['turn']): r for r in rows}
        if len(result) != len(rows) or set(result) != expected:
            raise ValueError('Missing, extra or duplicate turn coverage')
        return result
    actual, marked = index(captures), index(reviews)
    for key, mark in marked.items():
        trace_hash = hashlib.sha256(json.dumps(actual[key], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if mark.get('capture_sha256') != trace_hash or mark.get('reviewer_role') != 'independent_reviewer':
            raise ValueError('Review identity or trace binding missing')
        if set(mark.get('metrics', {})) != {'understanding', 'decision', 'cards'}:
            raise ValueError('All three metrics must be explicitly reviewed')
        for metric in mark['metrics'].values():
            if type(metric.get('pass')) is not bool or not metric.get('evidence'):
                raise ValueError('Semantic pass needs a boolean and evidence')
        if not isinstance(mark.get('critical_flags'), list) or not mark.get('critical_review_evidence'):
            raise ValueError('Critical error review missing')
        if actual[key].get('execution_error') or actual[key].get('wire_errors'):
            if mark['metrics']['cards']['pass']:
                raise ValueError('Mechanical execution/source failure cannot pass cards')
    return marked
