"""
Google Reviews Tool.

Fetches reviews from Google My Business and posts owner responses
via the Google My Business API (now part of Business Profile API).

In demo mode, returns sample reviews and logs responses without posting.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from backend.src.config import settings

logger = logging.getLogger(__name__)

# Google Business Profile API base URL
GMB_API_BASE = "https://mybusinessaccountmanagement.googleapis.com/v1"
GMB_REVIEWS_BASE = "https://mybusiness.googleapis.com/v4"


class ReviewsTool:
    """
    Fetch and respond to Google Reviews.

    In production: authenticates with OAuth2 service account.
    In demo: returns sample review data and logs responses.
    """

    async def fetch_reviews(
        self,
        place_id: str,
        limit: int = 10,
        unresponded_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent Google reviews for the business.

        Returns a list of review dicts with keys:
          - review_id, reviewer_name, rating, text, create_time, responded
        """
        if not settings.GOOGLE_REVIEWS_API_KEY:
            return self._demo_reviews()

        try:
            return await self._fetch_real_reviews(place_id, limit, unresponded_only)
        except Exception as exc:
            logger.warning("Google Reviews fetch failed, using demo data: %s", exc)
            return self._demo_reviews()

    async def _fetch_real_reviews(
        self, place_id: str, limit: int, unresponded_only: bool
    ) -> List[Dict[str, Any]]:
        """Call the Google Business Profile API to get reviews."""
        headers = {"Authorization": f"Bearer {settings.GOOGLE_REVIEWS_API_KEY}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            # List locations first
            resp = await client.get(
                f"{GMB_REVIEWS_BASE}/accounts/-/locations/{place_id}/reviews",
                headers=headers,
                params={"pageSize": limit},
            )
            resp.raise_for_status()
            data = resp.json()

        reviews = []
        for r in data.get("reviews", []):
            reviews.append({
                "review_id": r.get("reviewId"),
                "reviewer_name": r.get("reviewer", {}).get("displayName", "Anonymous"),
                "rating": self._star_rating_to_int(r.get("starRating", "FIVE")),
                "text": r.get("comment", ""),
                "create_time": r.get("createTime", ""),
                "responded": bool(r.get("reviewReply")),
            })

        if unresponded_only:
            reviews = [r for r in reviews if not r["responded"]]
        return reviews

    async def post_response(
        self, review_id: str, response_text: str, place_id: str = ""
    ) -> Dict[str, Any]:
        """
        Post a public response to a Google Review.

        Returns: {"success": True, "review_id": str}
        """
        if not settings.GOOGLE_REVIEWS_API_KEY:
            logger.info(
                "[DEMO] Review response posted for review_id=%s: %.80s...",
                review_id,
                response_text,
            )
            return {"success": True, "review_id": review_id, "_demo": True}

        try:
            headers = {
                "Authorization": f"Bearer {settings.GOOGLE_REVIEWS_API_KEY}",
                "Content-Type": "application/json",
            }
            pid = place_id or settings.GOOGLE_PLACE_ID
            url = f"{GMB_REVIEWS_BASE}/accounts/-/locations/{pid}/reviews/{review_id}/reply"

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.put(url, headers=headers, json={"comment": response_text})
                resp.raise_for_status()

            return {"success": True, "review_id": review_id}
        except Exception as exc:
            logger.exception("Failed to post review response: %s", exc)
            return {"success": False, "error": str(exc), "_demo": True}

    def _demo_reviews(self) -> List[Dict[str, Any]]:
        """Sample reviews for demo mode."""
        now = datetime.utcnow()
        return [
            {
                "review_id": "review-001",
                "reviewer_name": "Sarah Connor",
                "rating": 5,
                "text": "Pete came out the same day and fixed our burst pipe in under an hour! Incredible service. Will definitely use again.",
                "create_time": (now - timedelta(days=1)).isoformat(),
                "responded": False,
            },
            {
                "review_id": "review-002",
                "reviewer_name": "John Doe",
                "rating": 3,
                "text": "Good work but had to wait a bit longer than expected. Price was fair.",
                "create_time": (now - timedelta(days=3)).isoformat(),
                "responded": False,
            },
            {
                "review_id": "review-003",
                "reviewer_name": "Maria Garcia",
                "rating": 1,
                "text": "Did not show up for my appointment and did not call. Very disappointing.",
                "create_time": (now - timedelta(days=5)).isoformat(),
                "responded": False,
            },
        ]

    @staticmethod
    def _star_rating_to_int(star_rating: str) -> int:
        mapping = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
        return mapping.get(star_rating.upper(), 5)
