"""Anonymous profile algorithms. No cryptographic transport or secret storage.

This module accepts only fixed-dimension numeric arrays. Password ownership and
real policy enforcement live in a separate OFFLINE ideal-function evaluator.
"""
from __future__ import annotations

import numpy as np

VERSION = 'structure-conditional-anonymous-profile-v1'


def decreasing_projection(values):
    """L2 isotonic projection with a nonnegative lower bound (PAVA)."""
    blocks = []
    for value in np.asarray(values, dtype=float):
        blocks.append([float(value), 1])
        while len(blocks) > 1 and blocks[-2][0] < blocks[-1][0]:
            right, left = blocks.pop(), blocks.pop()
            size = left[1] + right[1]
            blocks.append([(left[0] * left[1] + right[0] * right[1]) / size, size])
    return np.concatenate([np.full(n, max(0.0, mean)) for mean, n in blocks])


def simplex_projection(values, total):
    """Euclidean projection of noisy structure counts onto a fixed-size simplex."""
    values = np.asarray(values, dtype=float)
    ordered = np.sort(values)[::-1]
    running = np.cumsum(ordered) - total
    valid = np.flatnonzero(ordered - running / np.arange(1, len(values) + 1) > 0)
    if not len(valid):
        return np.zeros_like(values)
    rho = valid[-1]
    return np.maximum(values - running[rho] / (rho + 1), 0)


def private_conditional(counts, spectra, epsilon, rng):
    """Replacement sensitivity <=4; one fixed-shape vector release."""
    noisy_counts = simplex_projection(counts + rng.laplace(0, 4 / epsilon, counts.shape), float(sum(counts)))
    raw = spectra + rng.laplace(0, 4 / epsilon, spectra.shape)
    projected = np.stack([decreasing_projection(row) for row in raw])
    for index, row in enumerate(projected):
        # Both monotonicity and sum(prefix)<=structure_count are public constraints.
        if row.sum() > noisy_counts[index]:
            projected[index] *= noisy_counts[index] / row.sum()
    return {'counts': noisy_counts, 'spectra': projected}


def private_marginal(counts, frequencies, epsilon, rng, split=(0.5, 0.5)):
    """Two sensitivity-2 releases, total epsilon equals their budget sum."""
    eps_counts, eps_frequency = epsilon * split[0], epsilon * split[1]
    c = simplex_projection(counts + rng.laplace(0, 2 / eps_counts, counts.shape), float(sum(counts)))
    f = decreasing_projection(frequencies + rng.laplace(0, 2 / eps_frequency, frequencies.shape))
    if f.sum() > sum(counts):
        f *= sum(counts) / f.sum()
    return {'counts': c, 'frequencies': f}


def choose_shapes(counts, j, other_index):
    eligible = [i for i in range(len(counts)) if i != other_index and counts[i] >= 20]
    return tuple(sorted(eligible, key=lambda i: (-counts[i], i))[:j])


def choose_positions(profile, k, shapes=(), residual=False):
    """Return anonymous (structure, rank) positions; never return password values."""
    spectra = profile['spectra']
    positions = [(s, r) for s in range(len(spectra)) for r in range(spectra.shape[1])
                 if spectra[s, r] >= 2 and (not residual or s not in shapes)]
    return tuple(sorted(positions, key=lambda p: (-spectra[p], p[0], p[1]))[:k])


def predict_conditional(profile, positions, shapes):
    return float(sum(profile['counts'][s] for s in shapes) +
                 sum(profile['spectra'][s, r] for s, r in positions if s not in shapes))


def predict_marginal(profile, ranks, shapes, total):
    # Predeclared independence approximation because the marginal profile lacks ownership.
    structure_mass = sum(profile['counts'][s] for s in shapes)
    exact_mass = sum(profile['frequencies'][r] for r in ranks)
    return float(structure_mass + exact_mass - structure_mass * exact_mass / total)
