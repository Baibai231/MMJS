"""Monotone, common policy actions for registration cohorts."""
from __future__ import annotations

import dataclasses

from policy.open_policy import Rule


def initial_policy():
    return Rule('length-8', min_length=8, origin='dynamic')


def compatible(previous, proposed):
    return (proposed.min_length >= previous.min_length
            and proposed.classes >= previous.classes
            and set(proposed.deny) >= set(previous.deny)
            and proposed.blocklist >= previous.blocklist)


def next_actions(current, development_train, history, *,
                 lengths=(8, 10, 12, 14, 16),
                 blocklist_sizes=(100, 1000, 10000),
                 include_pattern_rules=False):
    """Return one-step policy updates, plus hold, with explicit action names."""
    choices = [('hold', current)]
    longer = next((n for n in lengths if n > current.min_length), None)
    if longer is not None:
        choices.append((f'length-{longer}', dataclasses.replace(
            current, name=f'{current.name}+length-{longer}', min_length=longer)))
    if current.classes < 3:
        n = max(2, current.classes + 1)
        choices.append((f'classes-{n}', dataclasses.replace(
            current, name=f'{current.name}+classes-{n}', classes=n)))
    ranked = sorted(development_train, key=lambda w: (-development_train[w], w))
    for n in blocklist_sizes:
        block = current.blocklist | frozenset(ranked[:n])
        if block != current.blocklist:
            choices.append((f'train-block-top-{n}', dataclasses.replace(
                current, name=f'{current.name}+train-block-{n}', blocklist=block)))
            break
    if history:
        head = sorted(history, key=lambda w: (-history[w], w))
        block = current.blocklist | frozenset(w for w in head[:100] if history[w] >= 2)
        if block != current.blocklist:
            choices.append(('history-hotspots', dataclasses.replace(
                current, name=f'{current.name}+history-hotspots', blocklist=block)))
    if include_pattern_rules:
        for feature in ('repeated', 'sequential_digits'):
            if feature not in current.deny:
                choices.append((f'deny-{feature}', dataclasses.replace(
                    current, name=f'{current.name}+deny-{feature}',
                    deny=tuple((*current.deny, feature)))))
    assert all(compatible(current, candidate) for _, candidate in choices)
    return choices
