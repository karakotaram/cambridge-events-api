"""LightFM hybrid collaborative filtering recommender for personalized event scoring."""
import re
from typing import List, Dict, Optional, Tuple

import numpy as np

from src.services.preferences import classify_timing_slot, TIMING_SLOTS

# Category values matching EventCategory enum
CATEGORIES = [
    "music", "arts and culture", "food and drink", "theater",
    "lectures", "sports", "community", "other",
]

# Price bucket labels
PRICE_BUCKETS = ["free", "cheap", "moderate", "expensive", "unknown"]


def _classify_price_bucket(cost: Optional[str]) -> str:
    """Classify event cost string into a price bucket."""
    if not cost:
        return "unknown"
    cost_lower = cost.lower()
    if any(x in cost_lower for x in ["free", "$0", "no cost", "no charge"]):
        return "free"
    match = re.search(r"\$(\d+)", cost)
    if match:
        price = int(match.group(1))
        if price == 0:
            return "free"
        elif price <= 15:
            return "cheap"
        elif price <= 40:
            return "moderate"
        else:
            return "expensive"
    return "unknown"


class LightFMRecommender:
    """Hybrid collaborative filtering recommender using LightFM."""

    def __init__(self):
        self.model = None
        self.user_id_map = {}   # uuid_str -> internal index
        self.item_id_map = {}   # event_id -> internal index
        self.item_features = None
        self._n_users = 0
        self._n_items = 0

    def build_interaction_matrix(
        self,
        likes: List[Tuple[str, str]],
        clicks: List[Tuple[str, str, Optional[int]]],
        event_ids: List[str],
    ):
        """
        Build user x item sparse interaction matrix.

        Args:
            likes: List of (user_uuid_str, event_id) from OnboardingLikes
            clicks: List of (user_uuid_str, event_id, position) from ClickTracking
            event_ids: All valid event IDs (defines item space)

        Returns:
            scipy.sparse.coo_matrix of interactions
        """
        from scipy.sparse import coo_matrix

        # Build ID maps
        user_set = set()
        for uid, _ in likes:
            user_set.add(uid)
        for uid, _, _ in clicks:
            user_set.add(uid)

        self.user_id_map = {uid: idx for idx, uid in enumerate(sorted(user_set))}
        self.item_id_map = {eid: idx for idx, eid in enumerate(event_ids)}
        self._n_users = len(self.user_id_map)
        self._n_items = len(self.item_id_map)

        if self._n_users == 0 or self._n_items == 0:
            return None

        rows, cols, weights = [], [], []

        # OnboardingLikes -> weight 1.0
        for uid, eid in likes:
            if uid in self.user_id_map and eid in self.item_id_map:
                rows.append(self.user_id_map[uid])
                cols.append(self.item_id_map[eid])
                weights.append(1.0)

        # ClickTracking -> position-weighted
        for uid, eid, position in clicks:
            if uid not in self.user_id_map or eid not in self.item_id_map:
                continue
            # Position bias correction: clicks at lower positions (further down)
            # are stronger signals
            if position is not None and position <= 3:
                w = 0.5
            elif position is not None and position <= 5:
                w = 0.7
            else:
                w = 1.0
            rows.append(self.user_id_map[uid])
            cols.append(self.item_id_map[eid])
            weights.append(w)

        if not rows:
            return None

        interactions = coo_matrix(
            (np.array(weights, dtype=np.float32), (np.array(rows), np.array(cols))),
            shape=(self._n_users, self._n_items),
        )
        return interactions

    def _encode_item_features(self, events, venues: List[str]):
        """
        Build item feature matrix (sparse).

        Features per item:
        - Category one-hot (8)
        - Timing slot one-hot (6)
        - Venue/source one-hot (len(venues))
        - Price bucket one-hot (5)
        - Family-friendly binary (1)
        - Item identity (n_items) for collaborative signal

        Args:
            events: List of Event objects (order matches item_id_map)
            venues: Sorted list of unique venue/source names

        Returns:
            scipy.sparse.csr_matrix of shape (n_items, n_features)
        """
        from scipy.sparse import lil_matrix

        venue_map = {v: i for i, v in enumerate(venues)}
        n_cat = len(CATEGORIES)
        n_timing = len(TIMING_SLOTS)
        n_venue = len(venues)
        n_price = len(PRICE_BUCKETS)
        n_ff = 1
        n_identity = self._n_items
        total_features = n_cat + n_timing + n_venue + n_price + n_ff + n_identity

        features = lil_matrix((self._n_items, total_features), dtype=np.float32)

        cat_map = {c: i for i, c in enumerate(CATEGORIES)}
        price_map = {p: i for i, p in enumerate(PRICE_BUCKETS)}
        timing_map = {t: i for i, t in enumerate(TIMING_SLOTS)}

        for event in events:
            idx = self.item_id_map.get(event.id)
            if idx is None:
                continue

            offset = 0

            # Category one-hot
            cat_val = event.category.value if hasattr(event.category, "value") else str(event.category) if event.category else "other"
            cat_idx = cat_map.get(cat_val, cat_map["other"])
            features[idx, offset + cat_idx] = 1.0
            offset += n_cat

            # Timing slot one-hot
            slot = classify_timing_slot(event.start_datetime)
            slot_idx = timing_map.get(slot, 0)
            features[idx, offset + slot_idx] = 1.0
            offset += n_timing

            # Venue one-hot
            source = event.source_name or ""
            v_idx = venue_map.get(source)
            if v_idx is not None:
                features[idx, offset + v_idx] = 1.0
            offset += n_venue

            # Price bucket one-hot
            bucket = _classify_price_bucket(event.cost)
            p_idx = price_map.get(bucket, price_map["unknown"])
            features[idx, offset + p_idx] = 1.0
            offset += n_price

            # Family-friendly binary
            if event.family_friendly:
                features[idx, offset] = 1.0
            offset += n_ff

            # Item identity (for collaborative signal)
            features[idx, offset + idx] = 1.0

        return features.tocsr()

    def train(self, events, likes, clicks):
        """
        Full training pipeline: build interactions, features, fit model.

        Args:
            events: List of Event objects
            likes: List of (user_uuid_str, event_id)
            clicks: List of (user_uuid_str, event_id, position)

        Returns:
            True if training succeeded, False otherwise
        """
        try:
            from lightfm import LightFM
        except ImportError:
            print("[LightFM] lightfm not installed, skipping training")
            return False

        event_ids = [e.id for e in events]
        interactions = self.build_interaction_matrix(likes, clicks, event_ids)

        if interactions is None or interactions.nnz == 0:
            print("[LightFM] No interactions to train on")
            return False

        # Build item features
        venues = sorted({e.source_name for e in events if e.source_name})
        self.item_features = self._encode_item_features(events, venues)

        # Choose components based on user count
        n_components = 10 if self._n_users < 3 else 30

        self.model = LightFM(
            loss="warp",
            no_components=n_components,
            learning_rate=0.05,
        )

        self.model.fit(
            interactions,
            item_features=self.item_features,
            epochs=30,
            num_threads=1,
        )

        print(f"[LightFM] Trained: {self._n_users} users, {self._n_items} items, "
              f"{interactions.nnz} interactions, {n_components} components")
        return True

    def predict_scores(
        self,
        user_uuid: str,
        candidate_event_ids: List[str],
    ) -> Dict[str, float]:
        """
        Predict scores for candidate events for a given user.

        Args:
            user_uuid: User UUID string
            candidate_event_ids: List of event IDs to score

        Returns:
            Dict mapping event_id -> score. Empty dict if user unknown or model not trained.
        """
        if self.model is None or self.item_features is None:
            return {}

        user_idx = self.user_id_map.get(user_uuid)
        if user_idx is None:
            return {}

        # Map candidate IDs to internal indices
        item_indices = []
        valid_event_ids = []
        for eid in candidate_event_ids:
            idx = self.item_id_map.get(eid)
            if idx is not None:
                item_indices.append(idx)
                valid_event_ids.append(eid)

        if not item_indices:
            return {}

        item_indices_arr = np.array(item_indices)
        scores = self.model.predict(
            user_idx,
            item_indices_arr,
            item_features=self.item_features,
        )

        return {eid: float(s) for eid, s in zip(valid_event_ids, scores)}
