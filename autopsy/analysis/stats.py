"""Small, dependency-light statistics.

Every number this tool shows a creator is an estimate from a noisy sample, so
every number ships with an interval and a p-value. A hackathon demo that says
"long shots cost you 2.4x more viewers" is only worth anything if it can also
say how sure it is.
"""

from __future__ import annotations

import numpy as np

RNG_SEED = 20260813


def rng(seed: int | None = None) -> np.random.Generator:
    return np.random.default_rng(RNG_SEED if seed is None else seed)


def mad(values: np.ndarray) -> float:
    """Median absolute deviation, scaled to be comparable to a std dev."""
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return 0.0
    med = float(np.median(values))
    return float(1.4826 * np.median(np.abs(values - med)))


def robust_threshold(values: np.ndarray, k: float = 3.0, floor: float = 0.0) -> float:
    """median + k*MAD.

    Used to find retention cliffs. A mean+std threshold would be dragged upward
    by the very cliffs we are trying to detect; the median and MAD are not.
    """
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return floor
    return max(floor, float(np.median(values)) + k * mad(values))


def bootstrap_ci(
    values: np.ndarray,
    statistic=np.mean,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int | None = None,
) -> tuple[float, float, float]:
    """Percentile bootstrap. Returns (point estimate, low, high)."""
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return 0.0, 0.0, 0.0
    point = float(statistic(values))
    if values.size < 8:
        return point, point, point
    generator = rng(seed)
    idx = generator.integers(0, values.size, size=(n_boot, values.size))
    samples = statistic(values[idx], axis=1)
    low = float(np.quantile(samples, alpha / 2))
    high = float(np.quantile(samples, 1 - alpha / 2))
    return point, low, high


MIN_BOOT = 4


def ratio_bootstrap_ci(
    high_group: np.ndarray,
    low_group: np.ndarray,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int | None = None,
) -> tuple[float, float, float]:
    """Bootstrap CI for mean(high) / mean(low).

    On a group too small to resample, this returns the point estimate with an
    infinite upper bound rather than 1.0. Returning 1.0 would be a silent lie:
    it reads as "measured, no effect" when the truth is "not measured yet".
    """
    high_group = np.asarray(high_group, dtype=float)
    low_group = np.asarray(low_group, dtype=float)
    denom = float(np.mean(low_group)) if low_group.size else 0.0
    if high_group.size == 0 or denom <= 1e-12:
        return float("nan"), float("nan"), float("nan")
    point = float(np.mean(high_group)) / denom
    if high_group.size < MIN_BOOT or low_group.size < MIN_BOOT:
        return point, 0.0, float("inf")
    generator = rng(seed)
    hi_idx = generator.integers(0, high_group.size, size=(n_boot, high_group.size))
    lo_idx = generator.integers(0, low_group.size, size=(n_boot, low_group.size))
    hi_means = np.mean(high_group[hi_idx], axis=1)
    lo_means = np.mean(low_group[lo_idx], axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = np.where(lo_means > 1e-12, hi_means / lo_means, np.nan)
    ratios = ratios[np.isfinite(ratios)]
    if ratios.size == 0:
        return point, point, point
    return point, float(np.quantile(ratios, alpha / 2)), float(np.quantile(ratios, 1 - alpha / 2))


def permutation_test(
    group_a: np.ndarray,
    group_b: np.ndarray,
    n_perm: int = 5000,
    seed: int | None = None,
) -> float:
    """Two-sided permutation test on the difference of means.

    Non-parametric on purpose: hazard values are bounded below at zero and
    heavily right-skewed, so a t-test would be quietly wrong.
    """
    group_a = np.asarray(group_a, dtype=float)
    group_b = np.asarray(group_b, dtype=float)
    if group_a.size < MIN_BOOT or group_b.size < MIN_BOOT:
        return 1.0
    observed = abs(float(np.mean(group_a)) - float(np.mean(group_b)))
    pooled = np.concatenate([group_a, group_b])
    n_a = group_a.size
    generator = rng(seed)
    count = 0
    for _ in range(n_perm):
        generator.shuffle(pooled)
        diff = abs(float(np.mean(pooled[:n_a])) - float(np.mean(pooled[n_a:])))
        if diff >= observed:
            count += 1
    return (count + 1) / (n_perm + 1)


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Rank correlation, no scipy required."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 3 or x.size != y.size:
        return 0.0
    rx = _rankdata(x)
    ry = _rankdata(y)
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    denom = float(np.sqrt((rx**2).sum() * (ry**2).sum()))
    return 0.0 if denom < 1e-12 else float((rx * ry).sum() / denom)


def _rankdata(values: np.ndarray) -> np.ndarray:
    """Average ranks, ties shared."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=float)
    ranks[order] = np.arange(1, values.size + 1, dtype=float)
    sorted_vals = values[order]
    i = 0
    while i < sorted_vals.size:
        j = i
        while j + 1 < sorted_vals.size and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        if j > i:
            ranks[order[i : j + 1]] = ranks[order[i : j + 1]].mean()
        i = j + 1
    return ranks


def find_breakpoint(
    values: np.ndarray,
    target: np.ndarray,
    min_group: int = 40,
    n_candidates: int = 25,
    higher_is_worse: bool = True,
) -> dict | None:
    """Find the threshold where the target actually changes behaviour.

    Fixed quartiles systematically understate a real threshold effect. If shots
    only start costing viewers past 12s but the top quartile begins at 7s, that
    bin averages harmless 7-12s shots together with harmful ones and the
    measured effect comes out roughly half its true size.

    So we scan candidate thresholds and keep the strongest. That search is
    itself a multiple-comparisons problem -- take the max of 17 ratios and some
    will look impressive on pure noise -- so significance is assessed with a
    max-statistic permutation test: shuffle the target, redo the *entire*
    search, and compare against the best ratio the search finds under the null.
    """
    values = np.asarray(values, dtype=float)
    target = np.asarray(target, dtype=float)
    if values.size < 2 * min_group:
        return None

    # The range is deliberately wide. A real threshold often sits in the tail --
    # shots only hurt past 12s, which can be the 92nd percentile -- and a search
    # capped at q0.85 cannot reach it, silently reporting roughly half the true
    # effect. `min_group` is what keeps the tail honest, not an arbitrary
    # quantile ceiling.
    lo_q, hi_q = 0.05, 0.95
    candidates = sorted(
        set(float(np.quantile(values, q)) for q in np.linspace(lo_q, hi_q, n_candidates))
    )

    def best_split(t: np.ndarray) -> tuple[float, float]:
        """(best ratio, threshold) over all candidates."""
        best_ratio, best_edge = 1.0, float("nan")
        for edge in candidates:
            upper = t[values >= edge]
            lower = t[values < edge]
            if upper.size < min_group or lower.size < min_group:
                continue
            up_mean, low_mean = float(np.mean(upper)), float(np.mean(lower))
            if higher_is_worse:
                if low_mean <= 1e-12:
                    continue
                ratio = up_mean / low_mean
            else:
                if up_mean <= 1e-12:
                    continue
                ratio = low_mean / up_mean
            if ratio > best_ratio:
                best_ratio, best_edge = ratio, edge
        return best_ratio, best_edge

    observed_ratio, edge = best_split(target)
    if not np.isfinite(edge):
        return None

    generator = rng()
    shuffled = target.copy()
    exceed = 0
    n_perm = 400
    for _ in range(n_perm):
        generator.shuffle(shuffled)
        null_ratio, _ = best_split(shuffled)
        if null_ratio >= observed_ratio:
            exceed += 1
    p_value = (exceed + 1) / (n_perm + 1)

    if higher_is_worse:
        worse, better = target[values >= edge], target[values < edge]
    else:
        worse, better = target[values < edge], target[values >= edge]

    _, ci_low, ci_high = ratio_bootstrap_ci(worse, better, n_boot=1500)
    return {
        "edge": float(edge),
        "effect": float(observed_ratio),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
        "p_value": float(p_value),
        "n_worse": int(worse.size),
        "n_better": int(better.size),
        "worse_mean": float(np.mean(worse)),
        "better_mean": float(np.mean(better)),
    }


def quantile_bins(values: np.ndarray, n_bins: int = 4) -> list[tuple[float, float]]:
    """Edges for roughly equal-count bins, collapsing duplicates."""
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return []
    qs = np.linspace(0, 1, n_bins + 1)
    edges = sorted(set(float(np.quantile(values, q)) for q in qs))
    if len(edges) < 2:
        return []
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
