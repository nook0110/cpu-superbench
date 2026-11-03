from itertools import combinations


def coverage(subset, detections):
    found = set().union(*detections.values()) if detections else set()
    if not found:
        return 1.0
    covered = set().union(*(detections[name] for name in subset)) if subset else set()
    return len(covered & found) / len(found)


def residual_risk(subset, detections, probability):
    return probability * (1.0 - coverage(subset, detections))


def greedy(costs, detections, probability=1.0, target=0.05):
    selected = []
    risk = residual_risk(selected, detections, probability)
    while risk > target and len(selected) < len(costs):
        best, best_gain = None, 0.0
        for name in costs:
            if name in selected:
                continue
            gain = (risk - residual_risk(selected + [name], detections, probability)) / costs[name]
            if gain > best_gain:
                best, best_gain = name, gain
        if best is None:
            break
        selected.append(best)
        risk = residual_risk(selected, detections, probability)
    return selected


def optimal(costs, detections, probability=1.0, target=0.05):
    names = sorted(costs)
    best, best_cost = list(names), sum(costs.values())
    for size in range(len(names) + 1):
        for subset in combinations(names, size):
            cost = sum(costs[name] for name in subset)
            if cost < best_cost and residual_risk(list(subset), detections, probability) <= target:
                best, best_cost = list(subset), cost
    return best
