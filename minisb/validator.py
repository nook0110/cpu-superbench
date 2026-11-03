import numpy as np


def _step_cdfs(observed, criteria):
    observed = np.sort(np.asarray(observed, dtype=float))
    criteria = np.sort(np.asarray(criteria, dtype=float))
    grid = np.unique(np.concatenate([observed, criteria]))
    cdf_observed = np.searchsorted(observed, grid, side="right") / len(observed)
    cdf_criteria = np.searchsorted(criteria, grid, side="right") / len(criteria)
    return grid, cdf_observed[:-1], cdf_criteria[:-1]


def _integrate(grid, numerator, cdf_a, cdf_b, scale):
    widths = np.diff(grid) / scale
    largest = np.maximum(cdf_a, cdf_b)
    ratio = np.divide(numerator, largest, out=np.zeros_like(numerator), where=largest > 0)
    return float(np.sum(ratio * widths))


def scale_of(criteria):
    return float(np.median(criteria))


def distance(a, b, scale=None):
    scale = scale_of(b) if scale is None else scale
    grid, cdf_a, cdf_b = _step_cdfs(a, b)
    return _integrate(grid, np.abs(cdf_a - cdf_b), cdf_a, cdf_b, scale)


def one_sided_distance(observed, criteria, scale=None):
    scale = scale_of(criteria) if scale is None else scale
    grid, cdf_observed, cdf_criteria = _step_cdfs(observed, criteria)
    return _integrate(grid, np.maximum(0.0, cdf_observed - cdf_criteria), cdf_observed, cdf_criteria, scale)


def similarity(a, b, scale=None):
    return 1.0 - distance(a, b, scale)


def centroid(samples, scale):
    totals = [sum(similarity(a, b, scale) for b in samples) for a in samples]
    return samples[int(np.argmax(totals))]


def criteria(samples, alpha):
    samples = [np.asarray(sample, dtype=float) for sample in samples]
    scale = float(np.median(np.concatenate(samples)))
    defects = set()
    center = centroid(samples, scale)
    while True:
        rest = [index for index in range(len(samples)) if index not in defects]
        found = {index for index in rest if similarity(center, samples[index], scale) <= alpha}
        if not found:
            return center, sorted(defects)
        defects |= found
        if len(defects) == len(samples):
            raise ValueError("all calibration samples were rejected; lower alpha")
        center = centroid([samples[index] for index in range(len(samples)) if index not in defects], scale)


def verdict(observed, center, alpha):
    score = 1.0 - one_sided_distance(observed, center)
    return {"similarity": score, "defective": score <= alpha}

