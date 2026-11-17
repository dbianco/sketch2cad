"""Dependency graph of metric constraints (spec section 11.3, REQ-06).

An entity fixed by triangulation or baseline plus offset depends on the entities in its
``constraint.refs``. Dependencies are one-way and must not form cycles, so a reference can
never depend, directly or indirectly, on the entity being fixed (V-07, H3-4).

Graphs are mappings ``entity -> refs`` (the entities it depends on); nodes are any
hashable id, typically ``uuid.UUID``. Pure functions, iterative (no recursion limit).
"""

from collections import deque
from collections.abc import Hashable, Iterable, Mapping


def find_dependency_cycle[K: Hashable](graph: Mapping[K, Iterable[K]]) -> list[K] | None:
    """One cycle of ``graph`` as ``[n0, n1, ..., n0]`` (each depends on the next), or ``None``.

    A self-reference gives ``[n, n]``. Nodes that only appear as references are leaves.
    """
    refs = {node: list(targets) for node, targets in graph.items()}
    done: set[K] = set()
    for root in refs:
        if root in done:
            continue
        path: list[K] = [root]
        on_path: dict[K, int] = {root: 0}
        stack = [iter(refs[root])]
        while stack:
            try:
                child = next(stack[-1])
            except StopIteration:
                stack.pop()
                finished = path.pop()
                del on_path[finished]
                done.add(finished)
                continue
            if child in on_path:
                return [*path[on_path[child] :], child]
            if child in done:
                continue
            on_path[child] = len(path)
            path.append(child)
            stack.append(iter(refs.get(child, ())))
    return None


def has_dependency_cycle[K: Hashable](graph: Mapping[K, Iterable[K]]) -> bool:
    """Whether ``graph`` (``entity -> refs``) contains a dependency cycle."""
    return find_dependency_cycle(graph) is not None


def would_create_cycle[K: Hashable](
    graph: Mapping[K, Iterable[K]], entity: K, new_refs: Iterable[K]
) -> bool:
    """Whether making ``entity`` depend on ``new_refs`` (replacing its refs) closes a cycle.

    True when a new reference is ``entity`` itself or depends on it directly or indirectly.
    """
    updated: dict[K, Iterable[K]] = dict(graph)
    updated[entity] = list(new_refs)
    return entity in _reachable(updated, entity)


def _reachable[K: Hashable](graph: Mapping[K, Iterable[K]], start: K) -> set[K]:
    seen: set[K] = set()
    queue = deque(graph.get(start, ()))
    while queue:
        node = queue.popleft()
        if node not in seen:
            seen.add(node)
            queue.extend(graph.get(node, ()))
    return seen


def dependents_breadth_first[K: Hashable](graph: Mapping[K, Iterable[K]], changed: K) -> list[K]:
    """Entities that depend on ``changed`` directly or indirectly, nearest first.

    Each entity appears once and ``changed`` itself is never returned, so the walk ends even
    if the stored graph has a cycle.
    """
    dependents: dict[K, list[K]] = {}
    for node, targets in graph.items():
        for target in targets:
            dependents.setdefault(target, []).append(node)
    seen: set[K] = {changed}
    order: list[K] = []
    queue = deque([changed])
    while queue:
        for node in dependents.get(queue.popleft(), []):
            if node not in seen:
                seen.add(node)
                order.append(node)
                queue.append(node)
    return order
