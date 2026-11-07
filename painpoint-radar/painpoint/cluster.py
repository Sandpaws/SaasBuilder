"""Group near-identical pain statements into themes (TF-IDF + average-linkage agglomerative clustering).

Limitation: TF-IDF matches shared words, not meaning, so heavy paraphrases ("chasing late payers" vs "unpaid
invoices follow-up") can land in separate clusters. Swapping in sentence embeddings is the natural upgrade.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import db

_TOKEN = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _tokenize(text: str) -> list[str]:
    """Lowercase, drop stop words, strip common suffixes (invoices -> invoice, chasing -> chas)."""
    return [_stem(t) for t in _TOKEN.findall(text.lower()) if t not in ENGLISH_STOP_WORDS and len(t) > 1]


def _cluster_rows(rows: list[dict], threshold: float) -> list[tuple[list[int], int]]:
    """Return [(member indexes, medoid index)] for one group of mentions."""
    n = len(rows)
    if n == 1:
        return [([0], 0)]
    texts = [f"{r['problem']}. {r['summary']}" for r in rows]
    try:
        matrix = TfidfVectorizer(
            tokenizer=_tokenize, token_pattern=None, lowercase=False, ngram_range=(1, 2), sublinear_tf=True
        ).fit_transform(texts)
    except ValueError:  # empty vocabulary: nothing to compare on
        return [([i], i) for i in range(n)]
    labels = AgglomerativeClustering(
        n_clusters=None, metric="cosine", linkage="average", distance_threshold=threshold
    ).fit_predict(matrix.toarray())
    groups: dict[int, list[int]] = defaultdict(list)
    for i, lab in enumerate(labels):
        groups[int(lab)].append(i)
    out = []
    for idx in groups.values():
        if len(idx) == 1:
            out.append((idx, idx[0]))
            continue
        sim = cosine_similarity(matrix[idx])
        out.append((idx, idx[int(np.argmax(sim.mean(axis=1)))]))
    return out


def _similarity_order(clusters: list[dict]) -> list[int]:
    """An order in which labels sharing words sit next to each other: start at the first (largest) cluster and keep
    stepping to the most similar label not yet visited. When nothing similar is left, it jumps to the next largest."""
    n = len(clusters)
    try:
        matrix = TfidfVectorizer(
            tokenizer=_tokenize, token_pattern=None, lowercase=False, sublinear_tf=True
        ).fit_transform([c["label"] for c in clusters])
    except ValueError:  # empty vocabulary
        return list(range(n))
    sim = cosine_similarity(matrix)
    visited = np.zeros(n, dtype=bool)
    order = [0]
    visited[0] = True
    for _ in range(n - 1):
        row = np.where(visited, -1.0, sim[order[-1]])
        nxt = int(np.argmax(row))  # ties (e.g. all 0) go to the lowest index, i.e. the largest cluster left
        order.append(nxt)
        visited[nxt] = True
    return order


def merge_clusters(
    clusters: list[dict], llm, batch_size: int, max_passes: int = 1
) -> tuple[list[dict], dict]:
    """Semantic second pass: let the model group labels that mean the same problem (see MERGE_SYSTEM).

    Merging only happens inside a batch of `batch_size` labels, so with more than one batch, two paraphrases that
    land in different batches would never meet. Pass 1 puts the largest clusters together; later passes (up to
    `max_passes`) order labels so that similar ones share a batch. Passes stop as soon as one merges nothing. A failed
    call leaves that batch unmerged (reported in the stats) rather than failing the run.
    """
    stats = {"merged_groups": 0, "merge_errors": 0, "merge_error": None, "merge_passes": 0}
    current = sorted(clusters, key=lambda c: -len(c["members"]))
    for p in range(max(1, max_passes)):
        ordered = current if p == 0 else [current[i] for i in _similarity_order(current)]
        single_batch = len(ordered) <= batch_size  # every label was compared with every other
        current, merged = _merge_pass(ordered, llm, batch_size, stats)
        stats["merge_passes"] += 1
        # Pass 1 finding nothing says little (its batches are by size); a similarity-ordered pass finding nothing
        # means the paraphrases have met.
        if single_batch or (merged == 0 and p >= 1):
            break
    return current, stats


def _merge_pass(ordered: list[dict], llm, batch_size: int, stats: dict) -> tuple[list[dict], int]:
    out: list[dict] = []
    merged = 0
    for start in range(0, len(ordered), batch_size):
        batch = ordered[start : start + batch_size]
        if len(batch) < 2:
            out.extend(batch)
            continue
        result = llm.merge_labels([c["label"] for c in batch])
        if result.status != "ok":
            stats["merge_errors"] += 1
            stats["merge_error"] = result.error
            out.extend(batch)
            continue
        taken: set[int] = set()
        for group in result.groups:
            parts = sorted((batch[i] for i in group), key=lambda c: -len(c["members"]))
            weight: Counter = Counter()
            for c in parts:
                weight[c["category"]] += len(c["members"])
            out.append({
                "label": parts[0]["label"],
                "category": weight.most_common(1)[0][0],
                "members": [m for c in parts for m in c["members"]],
            })
            taken.update(group)
            stats["merged_groups"] += 1
            merged += 1
        out.extend(c for i, c in enumerate(batch) if i not in taken)
    out.sort(key=lambda c: -len(c["members"]))
    return out, merged


def cluster_pain_points(conn, cfg: dict, llm=None) -> dict:
    """TF-IDF first pass, then (if `llm` is given) the semantic merge pass. Replaces stored clusters."""
    rows = db.pain_rows(conn)
    if not rows:
        db.replace_clusters(conn, [])
        return {"mentions": 0, "clusters": 0, "before_merge": 0, "merged_groups": 0, "merge_errors": 0}
    threshold = cfg["cluster"]["distance_threshold"]
    if len(rows) <= cfg["cluster"]["max_global_items"]:
        partitions = [rows]
    else:  # bound memory on very large runs
        by_cat: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_cat[r["category"]].append(r)
        partitions = list(by_cat.values())

    clusters = []
    for part in partitions:
        for members, medoid in _cluster_rows(part, threshold):
            cats = Counter(part[i]["category"] for i in members)
            clusters.append({
                "label": part[medoid]["problem"],
                "category": cats.most_common(1)[0][0],
                "members": [part[i]["post_id"] for i in members],
            })
    clusters.sort(key=lambda c: -len(c["members"]))
    before = len(clusters)
    stats = {"merged_groups": 0, "merge_errors": 0, "merge_error": None, "merge_passes": 0}
    if llm is not None and before > 1:
        clusters, stats = merge_clusters(
            clusters, llm, cfg["cluster"]["merge_batch"], cfg["cluster"].get("merge_passes", 1)
        )
    db.replace_clusters(conn, clusters)
    return {"mentions": len(rows), "clusters": len(clusters), "before_merge": before, **stats}
