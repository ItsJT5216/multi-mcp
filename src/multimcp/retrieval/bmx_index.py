from __future__ import annotations

# retrieval/bmx.py
"""
BMX (BM𝒳) — Entropy-Weighted Lexical Search Index for Hybrid Retrieval.

Implements the BMX algorithm (arXiv:2408.06643, Li et al., August 2024),
a deterministic, CPU-only successor to BM25 that adds entropy-weighted
query-document similarity scoring atop a restructured TF-IDF core.

═══════════════════════════════════════════════════════════════════════════
HOW BMX SCORING DIFFERS FROM STANDARD BM25
═══════════════════════════════════════════════════════════════════════════

BM25 scores each query term independently:

  score_BM25(D, Q) = Σᵢ IDF(qᵢ) · [tf · (k₁+1)] / [tf + k₁·(1-b+b·|D|/avgdl)]

BMX replaces this with a two-component formula per query term:

  score_BMX(D, Q) = Σᵢ { IDF(qᵢ) · σ(α·(tf − K/2)/K)
                         + β · E(qᵢ) · S(Q, D) }

Where:
  α = clamp(avgdl / 100, 0.5, 1.5)   — self-tuning TF saturation / length norm
  β = 1 / log(1 + N)                  — corpus-size-scaled entropy weight
  K = α · (|D|/avgdl + Ē)             — per-document length-entropy normalization
  σ(x) = 1/(1+e⁻ˣ)                    — sigmoid TF saturation (smoother than BM25 rational)
  E(qᵢ)                               — normalized distribution entropy of term qᵢ:
                                         H(t) = −Σ_d p(d|t)·log(p(d|t)) / log(df(t))
                                         where p(d|t) = tf(t,d) / Σ tf(t)
  Ē                                    — mean E(qᵢ) across all query terms
  S(Q, D) = |Q ∩ D| / |Q|             — fraction of query tokens present in D

═══════════════════════════════════════════════════════════════════════════
KEY IMPROVEMENTS OVER BM25
═══════════════════════════════════════════════════════════════════════════

1. ENTROPY-WEIGHTED SIMILARITY — Documents matching more query tokens
   receive a bonus scaled by how informative each matching token is.
   BM25 treats every term as fully independent; BMX rewards holistic
   query coverage, particularly for multi-term queries.

2. SELF-TUNING PARAMETERS — α and β are computed from corpus statistics
   (avgdl and N), eliminating manual k₁/b tuning. This makes BMX more
   robust across heterogeneous corpora without hyperparameter sweeps.

3. RESTRUCTURED LENGTH NORMALIZATION — The normalization factor K combines
   document length ratio (|D|/avgdl) with average query entropy (Ē),
   injecting corpus-level entropy into length penalization.

4. SIGMOID TF SATURATION — Term frequencies are mapped through a logistic
   sigmoid σ(α·(tf − K/2)/K) instead of BM25's rational form. This caps
   keyword-stuffed documents more aggressively (tf > ~5 fully saturates)
   while providing a smoother transition than the rational form.

5. BUILT-IN SCORE NORMALIZATION — Optional [0, 1] normalization enables
   cross-query score comparison and threshold-based retrieval.

═══════════════════════════════════════════════════════════════════════════
BENCHMARK CONTEXT (from the BMX paper, BEIR evaluation)
═══════════════════════════════════════════════════════════════════════════

  BM25 variants (Robertson, BM25+, BM25L): ~39.9–40.4 avg nDCG@10
  BMX (core, no WQA):                       41.52 avg nDCG@10
  Improvement:                              ~+1.15 points (11/15 datasets)

This implementation is the CORE BMX algorithm only — no WQA (LLM query
augmentation), no GPU, no neural components. Fully deterministic.

═══════════════════════════════════════════════════════════════════════════
USAGE
═══════════════════════════════════════════════════════════════════════════

  index = BMXIndex()
  index.build_index([
      {"chunk_id": "c1", "text": "Python programming language"},
      {"chunk_id": "c2", "text": "JavaScript for web development"},
  ])
  results = index.search("programming", top_k=10)
  # Returns: [("c1", 0.85), ...]

Drop-in replacement for BM25Index — identical public API.
"""

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ToolDoc

logger = logging.getLogger(__name__)


@dataclass
class BMXIndex:
    """
    In-memory BMX index for lexical search.

    Builds index from chunk texts, enables keyword matching with
    entropy-weighted scoring. Drop-in replacement for BM25Index.

    BMX automatically computes its parameters from corpus statistics:
      α (alpha): TF saturation / length normalization — derived from avgdl
      β (beta):  Entropy-similarity weight — derived from corpus size

    Optional overrides:
      alpha_override: Force a specific α value (None = auto-compute)
      beta_override:  Force a specific β value (None = auto-compute)
      normalize_scores: If True, scores are mapped to [0, 1]
    """

    # Optional parameter overrides (None = auto-compute from corpus)
    alpha_override: float | None = None
    beta_override: float | None = None

    # Score normalization toggle
    normalize_scores: bool = False

    # --- Internal state (not constructor args) ---
    _documents: dict[str, list[str]] = field(default_factory=dict)
    _doc_lengths: dict[str, int] = field(default_factory=dict)
    _avg_doc_length: float = 0.0
    _doc_freqs: dict[str, int] = field(default_factory=dict)
    _idf_cache: dict[str, float] = field(default_factory=dict)
    _total_docs: int = 0
    _is_built: bool = False

    # BMX-specific precomputed data
    _alpha: float = 1.0
    _beta: float = 0.01
    _term_entropy: dict[str, float] = field(default_factory=dict)  # term -> normalized entropy [0,1]
    _term_total_freqs: dict[str, int] = field(default_factory=dict)  # term -> total tf across corpus
    _posting_list_tfs: dict[str, dict[str, float]] = field(default_factory=dict)  # term -> {chunk_id: tf}
    _inverted_index: dict[str, set[str]] = field(default_factory=dict)  # term -> set of chunk_ids
    _dirty_terms: set[str] = field(default_factory=set)  # terms needing entropy recomputation

    def __post_init__(self):
        """Initialize mutable default fields."""
        self._reset_index_state()

    def _reset_index_state(self) -> None:
        """Reset all corpus-derived state."""
        self._documents = {}
        self._doc_lengths = {}
        self._doc_freqs = {}
        self._idf_cache = {}
        self._total_docs = 0
        self._avg_doc_length = 0.0
        self._alpha = 1.0
        self._beta = 0.01
        self._is_built = False
        self._term_entropy = {}
        self._term_total_freqs = {}
        self._posting_list_tfs = {}
        self._inverted_index = {}
        self._dirty_terms = set()
        self._field_indexes = {}
        self._field_weights = {}

    # ─── Tokenization ────────────────────────────────────────────────

    def _tokenize(self, text: str) -> list[str]:
        """
        Tokenize text for BMX indexing.

        Converts to lowercase, splits on non-alphanumeric characters,
        and filters out very short tokens.

        Args:
            text: Text to tokenize

        Returns:
            List of tokens
        """
        if not text:
            return []

        text = text.lower()
        tokens = re.findall(r"[a-z0-9_]+", text)
        tokens = [t for t in tokens if len(t) > 1 or t in {"a", "i"}]

        return tokens

    # ─── Core math primitives ────────────────────────────────────────

    @staticmethod
    def _sigmoid(x: float) -> float:
        """Sigmoid function with overflow protection."""
        if x >= 500:
            return 1.0
        if x <= -500:
            return 0.0
        return 1.0 / (1.0 + math.exp(-x))

    @staticmethod
    def _shannon_entropy(probabilities: list[float]) -> float:
        """
        Compute Shannon entropy H = -Σ p·log(p).

        Args:
            probabilities: List of probability values in (0, 1).

        Returns:
            Non-negative entropy value.
        """
        h = 0.0
        for p in probabilities:
            if 0.0 < p < 1.0:
                h -= p * math.log(p)
        return h

    def _compute_alpha(self) -> float:
        """Compute α from average document length: clamp(avgdl/100, 0.5, 1.5)."""
        if self.alpha_override is not None:
            return self.alpha_override
        return max(0.5, min(1.5, self._avg_doc_length / 100.0))

    def _compute_beta(self) -> float:
        """Compute β from corpus size: 1 / log(1 + N)."""
        if self.beta_override is not None:
            return self.beta_override
        if self._total_docs <= 0:
            return 0.0
        return 1.0 / math.log(1.0 + self._total_docs)

    # ─── Entropy computation ─────────────────────────────────────────

    def _compute_term_entropies(self, terms: set[str] | None = None) -> None:
        """
        Compute normalized distribution entropy for terms.

        If `terms` is None, recomputes ALL terms (used during build_index).
        If `terms` is a set, recomputes only those terms (used for dirty flush).

        For each term t, compute p(d|t) = tf(t,d) / total_tf(t) for each
        document d containing t, then H(t) = -Σ p·log(p) normalized by
        log(df) to produce a value in [0, 1]:
          0 = term concentrated in one document (highly discriminative)
          1 = term uniformly distributed across all containing docs

        Terms appearing in only one document get entropy 0 (maximally
        concentrated). This is the most expensive part of index building —
        O(Σ posting list sizes).
        """
        target_terms = terms if terms is not None else set(self._doc_freqs.keys())
        for term in target_terms:
            doc_freq = self._doc_freqs.get(term, 0)
            if doc_freq <= 1:
                self._term_entropy[term] = 0.0
                continue

            tf_map = self._posting_list_tfs.get(term, {})
            total_tf = self._term_total_freqs.get(term, 0)
            if not tf_map or total_tf <= 0:
                self._term_entropy[term] = 0.0
                continue

            # Distribution probabilities: p(d|t) = tf(t,d) / Σ tf(t)
            probs = [tf / total_tf for tf in tf_map.values()]
            raw_entropy = self._shannon_entropy(probs)

            # Normalize by max possible entropy: log(df)
            max_entropy = math.log(doc_freq)
            self._term_entropy[term] = raw_entropy / max_entropy if max_entropy > 0 else 0.0

    def _flush_dirty_entropies(self, query_terms: set[str] | None = None) -> None:
        """Recompute entropy for dirty terms, optionally scoped to query terms.

        If query_terms is provided, only flushes dirty terms that overlap
        with the query (just-in-time). Otherwise flushes all dirty terms.
        """
        if not self._dirty_terms:
            return

        if query_terms is not None:
            to_flush = self._dirty_terms & query_terms
        else:
            to_flush = set(self._dirty_terms)

        if to_flush:
            self._compute_term_entropies(to_flush)
            self._dirty_terms -= to_flush

    def _get_normalized_entropy(self, query_tokens: list[str]) -> dict[str, float]:
        """
        Compute per-token informativeness weight for a specific query.

        Converts entropy to informativeness (1 − entropy), then normalizes
        so the most discriminative query token receives weight 1.0:
          0 = uniform term (appears everywhere, low signal)
          1 = most concentrated term in this query (strongest discriminator)

        Unknown terms (not in corpus) default to weight 0.0.

        Args:
            query_tokens: List of query tokens (may contain duplicates).

        Returns:
            Dict mapping each unique query token to its informativeness [0, 1].
        """
        unique_tokens = set(query_tokens)
        # Invert: low entropy (concentrated) → high informativeness
        raw_info = {t: 1.0 - self._term_entropy.get(t, 1.0) for t in unique_tokens}

        max_info = max(raw_info.values()) if raw_info else 0.0
        if max_info <= 0.0:
            return {t: 0.0 for t in unique_tokens}

        return {t: i / max_info for t, i in raw_info.items()}

    # ─── Index building ──────────────────────────────────────────────

    def build_index(self, chunks: list[dict]) -> None:
        """
        Build BMX index from chunk texts.

        Args:
            chunks: List of dicts with 'chunk_id' and 'text' keys.

        Raises:
            ValueError: If chunks is empty or missing required keys.
        """
        # Reset before validation so empty or invalid rebuilds cannot retain
        # stale postings, cached scores, or field subindexes.
        self._reset_index_state()

        if not chunks:
            logger.warning("Building BMX index with empty chunk list")
            self._is_built = True
            return

        total_length = 0

        # ── Pass 1: tokenize, compute doc lengths ──
        for chunk in chunks:
            chunk_id = chunk.get("chunk_id")
            text = chunk.get("text", "")

            if not chunk_id:
                logger.warning("Skipping chunk without chunk_id")
                continue

            tokens = self._tokenize(text)
            self._documents[chunk_id] = tokens
            self._doc_lengths[chunk_id] = len(tokens)
            total_length += len(tokens)

        self._total_docs = len(self._documents)

        if self._total_docs == 0:
            logger.warning("No valid chunks found for BMX index")
            self._is_built = True
            return

        self._avg_doc_length = total_length / self._total_docs

        # ── Pass 2: document frequencies + posting list TFs + inverted index + total freqs ──
        for chunk_id, tokens in self._documents.items():
            term_counts = Counter(tokens)
            for term, count in term_counts.items():
                self._doc_freqs[term] = self._doc_freqs.get(term, 0) + 1
                self._term_total_freqs[term] = self._term_total_freqs.get(term, 0) + count
                if term not in self._posting_list_tfs:
                    self._posting_list_tfs[term] = {}
                self._posting_list_tfs[term][chunk_id] = float(count)
                if term not in self._inverted_index:
                    self._inverted_index[term] = set()
                self._inverted_index[term].add(chunk_id)

        # ── Precompute IDF (Lucene variant, same as BM25) ──
        for term, df in self._doc_freqs.items():
            self._idf_cache[term] = math.log(
                (self._total_docs - df + 0.5) / (df + 0.5) + 1.0
            )

        # ── Precompute term entropies (full build — recompute all) ──
        self._term_entropy.clear()
        self._dirty_terms.clear()
        self._compute_term_entropies()

        # ── Compute dynamic parameters ──
        self._alpha = self._compute_alpha()
        self._beta = self._compute_beta()

        self._is_built = True
        logger.info(
            f"BMX index built: {self._total_docs} documents, "
            f"{len(self._doc_freqs)} unique terms, "
            f"avg length {self._avg_doc_length:.1f}, "
            f"α={self._alpha:.4f}, β={self._beta:.6f}"
        )

    # ─── Search ──────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 30,
        normalize: bool | None = None,
    ) -> list[tuple[str, float]]:
        """
        Search for chunks matching query using BMX scoring.

        Args:
            query: Search query string.
            top_k: Maximum number of results to return.
            normalize: Override instance-level normalize_scores for this call.
                       None = use self.normalize_scores.

        Returns:
            List of (chunk_id, score) tuples, sorted by score descending.
        """
        if not self._is_built:
            logger.warning("BMX index not built, returning empty results")
            return []

        if not query:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        unique_query = set(query_tokens)
        m = len(query_tokens)

        # Flush dirty entropies only for terms in this query (lazy recompute)
        self._flush_dirty_entropies(unique_query)

        # Precompute query-level values (after flush so entropy is fresh)
        norm_entropy = self._get_normalized_entropy(query_tokens)

        # Average normalized entropy across query tokens
        e_bar = sum(norm_entropy.get(t, 0.0) for t in query_tokens) / m if m > 0 else 0.0

        # Should we normalize?
        do_normalize = normalize if normalize is not None else self.normalize_scores

        # Collect candidate documents via inverted index
        candidate_ids: set[str] = set()
        for token in unique_query:
            posting = self._inverted_index.get(token)
            if posting:
                candidate_ids.update(posting)

        scores: dict[str, float] = {}

        for chunk_id in candidate_ids:
            doc_tokens = self._documents[chunk_id]
            score = self._score_document(
                query_tokens, unique_query, doc_tokens, chunk_id,
                norm_entropy, e_bar, m,
            )
            if score > 0:
                scores[chunk_id] = score

        # Optional normalization to [0, 1]
        if do_normalize and scores:
            score_max = self._compute_score_max(m)
            if score_max > 0:
                scores = {cid: s / score_max for cid, s in scores.items()}

        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results[:top_k]

    def _score_document(
        self,
        query_tokens: list[str],
        unique_query: set[str],
        doc_tokens: list[str],
        chunk_id: str,
        norm_entropy: dict[str, float],
        e_bar: float,
        m: int,
    ) -> float:
        """
        Compute BMX score for a single document.

        BMX formula per query token qᵢ:

          IDF(qᵢ) · σ(α·(tf − K/2) / K)  +  β · E(qᵢ) · S(Q, D)

        where K = α · (|D|/avgdl + Ē) is the length-entropy normalization
        factor and σ is the logistic sigmoid.

        Args:
            query_tokens: Tokenized query (with duplicates).
            unique_query: Set of unique query tokens.
            doc_tokens: Tokenized document.
            chunk_id: Document identifier.
            norm_entropy: Per-token normalized entropy for this query.
            e_bar: Average normalized entropy across query tokens.
            m: Number of query tokens (with duplicates).

        Returns:
            BMX score (non-negative float).
        """
        doc_length = self._doc_lengths.get(chunk_id, 0)
        if doc_length == 0:
            return 0.0

        term_freqs = Counter(doc_tokens)
        unique_doc = set(doc_tokens)

        # S(Q, D) = |Q ∩ D| / m  — query-document overlap fraction
        overlap = len(unique_query & unique_doc)
        s_qd = overlap / m if m > 0 else 0.0

        # Length-entropy normalization factor
        len_ratio = doc_length / self._avg_doc_length if self._avg_doc_length > 0 else 1.0
        alpha = self._alpha
        beta = self._beta
        K = alpha * (len_ratio + e_bar)

        score = 0.0

        for token in query_tokens:
            tf = term_freqs.get(token, 0)
            if tf == 0:
                continue

            idf = self._idf_cache.get(token, 0.0)
            if idf <= 0:
                # Ubiquitous term — informativeness weight naturally suppresses
                # the bonus, but still let it contribute its (near-zero) share
                e_qi = norm_entropy.get(token, 0.0)
                score += beta * e_qi * s_qd
                continue

            # ── Component 1: Sigmoid-saturated TF × IDF ──
            tf_sat = self._sigmoid(alpha * (tf - K / 2.0) / max(K, 0.01))
            tf_component = idf * tf_sat

            # ── Component 2: Entropy-weighted similarity ──
            # Floor of 0.1 ensures matching tokens always get coverage credit
            # even in overlap-heavy corpora where all terms have similar entropy
            e_qi = max(norm_entropy.get(token, 0.0), 0.1)
            entropy_component = beta * e_qi * s_qd

            score += tf_component + entropy_component

        return score

    def _compute_score_max(self, m: int) -> float:
        """
        Compute theoretical maximum score for normalization.

        With sigmoid TF saturation (max → 1.0):
          score_max = m · (max_idf + β)

        Args:
            m: Number of query tokens.

        Returns:
            Maximum possible score (positive float).
        """
        if self._total_docs <= 0 or m <= 0:
            return 1.0
        max_idf = math.log(1.0 + (self._total_docs - 0.5) / 1.5)
        return m * (max_idf + self._beta)

    # ─── Incremental updates ─────────────────────────────────────────

    def update_index(self, chunk_id: str, text: str) -> None:
        """
        Add or update a single chunk in the index.

        Note: For bulk updates, prefer rebuild with build_index().
        Recomputes affected entropies and dynamic parameters.

        Args:
            chunk_id: Unique chunk identifier.
            text: Chunk text content.
        """
        if chunk_id in self._documents:
            self.remove_from_index(chunk_id)

        tokens = self._tokenize(text)
        if not tokens:
            return

        self._documents[chunk_id] = tokens
        self._doc_lengths[chunk_id] = len(tokens)
        self._total_docs += 1

        # Recalculate average document length
        total_length = sum(self._doc_lengths.values())
        self._avg_doc_length = total_length / self._total_docs if self._total_docs > 0 else 0.0

        # Update document frequencies and posting list TFs
        term_counts = Counter(tokens)
        affected_terms: set[str] = set()

        for term, count in term_counts.items():
            self._doc_freqs[term] = self._doc_freqs.get(term, 0) + 1
            self._term_total_freqs[term] = self._term_total_freqs.get(term, 0) + count
            if term not in self._posting_list_tfs:
                self._posting_list_tfs[term] = {}
            self._posting_list_tfs[term][chunk_id] = float(count)
            if term not in self._inverted_index:
                self._inverted_index[term] = set()
            self._inverted_index[term].add(chunk_id)
            affected_terms.add(term)

        # Recompute IDF for affected terms (cheap — just a log per term)
        for term in affected_terms:
            df = self._doc_freqs[term]
            self._idf_cache[term] = math.log(
                (self._total_docs - df + 0.5) / (df + 0.5) + 1.0
            )

        # Mark affected terms for lazy entropy recomputation
        self._dirty_terms.update(affected_terms)

        # Recompute dynamic parameters
        self._alpha = self._compute_alpha()
        self._beta = self._compute_beta()

        self._is_built = True
        logger.debug(f"Updated BMX index with chunk {chunk_id}")

    def remove_from_index(self, chunk_id: str) -> bool:
        """
        Remove a chunk from the index.

        Args:
            chunk_id: Chunk identifier to remove.

        Returns:
            True if chunk was removed, False if not found.
        """
        if chunk_id not in self._documents:
            return False

        tokens = self._documents[chunk_id]
        term_counts = Counter(tokens)
        affected_terms: set[str] = set()

        del self._documents[chunk_id]
        del self._doc_lengths[chunk_id]
        self._total_docs -= 1

        # Recalculate average document length
        total_length = sum(self._doc_lengths.values())
        self._avg_doc_length = total_length / self._total_docs if self._total_docs > 0 else 0.0

        # Update document frequencies, posting list TFs, and inverted index
        for term, count in term_counts.items():
            self._doc_freqs[term] = self._doc_freqs.get(term, 0) - 1
            self._term_total_freqs[term] = self._term_total_freqs.get(term, 0) - count

            # Remove this doc from the posting list
            if term in self._posting_list_tfs:
                self._posting_list_tfs[term].pop(chunk_id, None)
                if not self._posting_list_tfs[term]:
                    del self._posting_list_tfs[term]

            # Remove from inverted index
            if term in self._inverted_index:
                self._inverted_index[term].discard(chunk_id)
                if not self._inverted_index[term]:
                    del self._inverted_index[term]

            if self._doc_freqs[term] <= 0:
                del self._doc_freqs[term]
                self._idf_cache.pop(term, None)
                self._term_entropy.pop(term, None)
                self._posting_list_tfs.pop(term, None)
                self._inverted_index.pop(term, None)
                self._term_total_freqs.pop(term, None)
            else:
                affected_terms.add(term)

        # Recompute IDF for remaining affected terms; mark entropy dirty
        for term in affected_terms:
            df = self._doc_freqs[term]
            self._idf_cache[term] = math.log(
                (self._total_docs - df + 0.5) / (df + 0.5) + 1.0
            )
        self._dirty_terms.update(affected_terms)

        # Recompute dynamic parameters
        self._alpha = self._compute_alpha()
        self._beta = self._compute_beta()

        logger.debug(f"Removed chunk {chunk_id} from BMX index")
        return True

    # ─── Diagnostics ─────────────────────────────────────────────────

    def get_index_stats(self) -> dict:
        """
        Get statistics about the current index.

        Returns:
            Dict with index statistics including BMX-specific parameters.
        """
        return {
            "total_documents": self._total_docs,
            "unique_terms": len(self._doc_freqs),
            "avg_doc_length": self._avg_doc_length,
            "is_built": self._is_built,
            "alpha": self._alpha,
            "beta": self._beta,
            "alpha_override": self.alpha_override,
            "beta_override": self.beta_override,
            "normalize_scores": self.normalize_scores,
            "avg_entropy": (
                sum(self._term_entropy.values()) / len(self._term_entropy)
                if self._term_entropy else 0.0
            ),
        }

    def clear(self) -> None:
        """Clear the entire index."""
        self._reset_index_state()
        logger.info("BMX index cleared")

    # ─── BMXF field-weighted wrapper ─────────────────────────────

    def build_field_index(self, tool_docs: list["ToolDoc"]) -> None:
        """Build separate BMX sub-indexes per field with BMXF weights.

        Creates self._field_indexes (5 BMXIndex instances) and self._field_weights.
        Each field's sub-index uses the same alpha_override/beta_override/normalize_scores
        as this instance, propagating any explicit overrides.

        Field weights:
          tool_name: 3.0    (strongest signal — exact name matches)
          namespace: 2.5    (server identity — strong discriminator)
          retrieval_aliases: 1.5  (curated synonyms — vocabulary bridge)
          description: 1.0  (supporting evidence)
          parameter_names: 0.5  (domain specificity — weakest)
        """
        self._field_indexes: dict[str, "BMXIndex"] = {}
        self._field_weights: dict[str, float] = {
            "tool_name": 3.0,
            "namespace": 2.5,
            "retrieval_aliases": 1.5,
            "description": 1.0,
            "parameter_names": 0.5,
        }
        for field_name in self._field_weights:
            field_idx = BMXIndex(
                alpha_override=self.alpha_override,
                beta_override=self.beta_override,
                normalize_scores=self.normalize_scores,
            )
            chunks = []
            for doc in tool_docs:
                text = getattr(doc, field_name, "") or ""
                if isinstance(text, list):
                    text = " ".join(text)
                chunks.append({"chunk_id": doc.tool_key, "text": text})
            field_idx.build_index(chunks)
            self._field_indexes[field_name] = field_idx

    def search_fields(self, query: str, top_k: int = 30) -> list[tuple[str, float]]:
        """Score tools across all BMXF fields with weighted sum.

        Calls each field sub-index's search() method, sums weighted scores,
        then returns top_k results sorted descending.

        Each field sub-index searches up to top_k*2 results to avoid
        truncating tools that rank high in one field but not others.
        """
        if not hasattr(self, "_field_indexes") or not self._field_indexes:
            return []
        combined: dict[str, float] = {}
        for field_name, weight in self._field_weights.items():
            field_idx = self._field_indexes.get(field_name)
            if not field_idx:
                continue
            results = field_idx.search(query, top_k=top_k * 2)
            for chunk_id, score in results:
                combined[chunk_id] = combined.get(chunk_id, 0.0) + weight * score
        sorted_results = sorted(combined.items(), key=lambda x: x[1], reverse=True)
        return sorted_results[:top_k]
