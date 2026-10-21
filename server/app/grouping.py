"""Deterministic story grouping (no AI).

Each article is turned into a TF-IDF vector over its headline (weighted x2) and feed excerpt. An unassigned
article joins an existing story only when ALL of these hold:

  * cosine similarity to the story centroid >= MIN_COSINE
  * at least MIN_SHARED_KEYS shared key terms (top TF-IDF terms and named-entity tokens) - one shared
    keyword is never enough
  * borderline similarity (< BORDERLINE_COSINE) additionally needs BORDERLINE_MIN_SHARED shared key terms,
    so related-but-separate stories that share a few names (e.g. two different Elon Musk/SpaceX stories)
    stay apart. IDF weights are only meaningful over a real window of articles, so this rule applies once
    at least BORDERLINE_MIN_CORPUS active articles exist (a normal hourly window holds ~200)
  * the article's headline shares at least one term with a headline already in the story, so a live blog
    or news round-up whose excerpt merely mentions the story does not join it
  * at least one shared named-entity token, unless similarity is very high (>= STRONG_COSINE)
  * the article was published within WINDOW of the story's existing coverage

Otherwise it starts a new story (single-source stories are kept). Existing assignments are never changed
and stories are never merged, so story IDs stay stable while new coverage joins them.
"""
import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .textutil import entities, stem, term_counts, tokens

MIN_COSINE = 0.25
STRONG_COSINE = 0.45
MIN_SHARED_KEYS = 2
BORDERLINE_COSINE = 0.30
BORDERLINE_MIN_SHARED = 6
BORDERLINE_MIN_CORPUS = 50
WINDOW = timedelta(hours=48)
ARTICLE_TOP_TERMS = 8
STORY_TOP_TERMS = 12


@dataclass
class Doc:
    id: str
    title: str
    excerpt: str
    published_at: datetime
    publisher_id: str = ""


@dataclass
class _Cluster:
    id: str
    vec: Counter = field(default_factory=Counter)  # sum of member unit vectors
    entity_tokens: set = field(default_factory=set)
    title_terms: set = field(default_factory=set)
    earliest: datetime | None = None
    latest: datetime | None = None
    members: list = field(default_factory=list)

    def keys(self) -> set:
        top = [t for t, _ in self.vec.most_common(STORY_TOP_TERMS)]
        return set(top) | self.entity_tokens

    def add(self, doc: Doc, vec: dict, ents: set) -> None:
        for t, w in vec.items():
            self.vec[t] += w
        self.entity_tokens |= ents
        self.title_terms |= set(tokens(doc.title))
        self.members.append(doc.id)
        self.earliest = doc.published_at if self.earliest is None else min(self.earliest, doc.published_at)
        self.latest = doc.published_at if self.latest is None else max(self.latest, doc.published_at)


def entity_tokens(doc: Doc) -> set:
    out = set()
    for phrase in entities(doc.title) + entities(doc.excerpt):
        out.update(tokens(phrase))
    return out


def _idf(docs: list[Doc]) -> dict:
    df = Counter()
    for d in docs:
        df.update(set(term_counts(d.title, d.excerpt)))
    n = len(docs)
    return {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}


def _unit(vec: dict) -> dict:
    norm = math.sqrt(sum(w * w for w in vec.values()))
    return {t: w / norm for t, w in vec.items()} if norm else {}


def vectorize(doc: Doc, idf: dict) -> dict:
    tf = term_counts(doc.title, doc.excerpt)
    return _unit({t: (1 + math.log(c)) * idf.get(t, 1.0) for t, c in tf.items()})


def cosine(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    dot = sum(w * b.get(t, 0.0) for t, w in a.items())
    nb = math.sqrt(sum(w * w for w in b.values()))
    na = math.sqrt(sum(w * w for w in a.values()))
    return dot / (na * nb) if na and nb else 0.0


def new_story_id(seed_article_id: str, taken: set) -> str:
    base = "s" + hashlib.sha1(seed_article_id.encode()).hexdigest()[:15]
    sid, i = base, 1
    while sid in taken:
        sid = f"{base[:13]}{i:03d}"
        i += 1
    return sid


@dataclass
class Decision:
    doc_id: str
    story_id: str
    created: bool
    score: float
    shared: list


def group(docs: list[Doc], assignments: dict[str, str], taken_ids: set | None = None) -> list[Decision]:
    """Assign every doc missing from `assignments` to an existing or new story.

    `docs` should contain all active articles (assigned and unassigned) so that IDF and centroids are
    computed over the whole active window. Returns one Decision per newly assigned doc.
    """
    taken = set(taken_ids or ()) | set(assignments.values())
    idf = _idf(docs)
    vecs = {d.id: vectorize(d, idf) for d in docs}
    ents = {d.id: entity_tokens(d) for d in docs}
    borderline_rule = len(docs) >= BORDERLINE_MIN_CORPUS

    clusters: dict[str, _Cluster] = {}
    for d in sorted(docs, key=lambda d: (d.published_at, d.id)):
        sid = assignments.get(d.id)
        if sid:
            clusters.setdefault(sid, _Cluster(sid)).add(d, vecs[d.id], ents[d.id])

    index: dict[str, set] = defaultdict(set)
    for c in clusters.values():
        for k in c.keys():
            index[k].add(c.id)

    decisions = []
    pending = sorted((d for d in docs if d.id not in assignments), key=lambda d: (d.published_at, d.id))
    for d in pending:
        v, e = vecs[d.id], ents[d.id]
        title_terms = set(tokens(d.title))
        top = {t for t, _ in sorted(v.items(), key=lambda kv: -kv[1])[:ARTICLE_TOP_TERMS]}
        keys = top | e
        candidates = set().union(*(index.get(k, set()) for k in keys)) if keys else set()

        best, best_score, best_shared = None, 0.0, []
        for cid in candidates:
            c = clusters[cid]
            if d.published_at < c.earliest - WINDOW or d.published_at > c.latest + WINDOW:
                continue
            shared = keys & c.keys()
            if len(shared) < MIN_SHARED_KEYS or not (title_terms & c.title_terms):
                continue
            score = cosine(v, c.vec)
            if score < MIN_COSINE or (
                borderline_rule and score < BORDERLINE_COSINE and len(shared) < BORDERLINE_MIN_SHARED
            ):
                continue
            if not (e & c.entity_tokens) and score < STRONG_COSINE:
                continue
            if score > best_score:
                best, best_score, best_shared = c, score, sorted(shared)

        if best is None:
            sid = new_story_id(d.id, taken)
            taken.add(sid)
            best = clusters[sid] = _Cluster(sid)
            created = True
        else:
            created = False
        best.add(d, v, e)
        for k in best.keys():
            index[k].add(best.id)
        decisions.append(Decision(d.id, best.id, created, round(best_score, 3), best_shared))
    return decisions


def pick_headline(docs: list[Doc]) -> Doc:
    """The member whose vector is closest to the story centroid (ties: earliest published)."""
    if len(docs) == 1:
        return docs[0]
    idf = _idf(docs)
    vecs = {d.id: vectorize(d, idf) for d in docs}
    centroid = Counter()
    for v in vecs.values():
        centroid.update(v)
    return max(sorted(docs, key=lambda d: (d.published_at, d.id)), key=lambda d: cosine(vecs[d.id], centroid))


__all__ = ["Doc", "Decision", "group", "pick_headline", "new_story_id", "stem"]
