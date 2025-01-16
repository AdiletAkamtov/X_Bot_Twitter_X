"""
twitter.py — X API operations: search, list feed, recommendations, comments, post reply
"""
from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import logger
from twitter_auth import FEATURES_SEARCH, TwitterAuth
from twitter_auth import GRAPHQL as _GRAPHQL_ORIG
# Force x.com domain — twitter.com returns 404 for SearchTimeline
GRAPHQL = _GRAPHQL_ORIG.replace("https://twitter.com", "https://x.com")

# Sentinel returned by post_reply when the target post is unavailable
POST_UNAVAILABLE = object()

# ─────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────

@dataclass
class Tweet:
    id: str
    text: str
    author_id: str
    author_username: str
    likes: int = 0
    retweets: int = 0
    views: int = 0
    reply_count: int = 0
    created_at: str = ""
    conversation_id: str = ""
    lang: str = "en"
    image_urls: list = None  # pbs.twimg.com CDN URLs — public, usable by vision AI

    def __post_init__(self):
        if self.image_urls is None:
            self.image_urls = []

    def __repr__(self):
        img = f", 🖼{len(self.image_urls)}" if self.image_urls else ""
        return f"Tweet({self.id}, @{self.author_username}, ❤{self.likes}{img})"


@dataclass
class Comment:
    id: str
    text: str
    author_username: str
    likes: int = 0
    views: int = 0
    created_at: str = ""

    def __repr__(self):
        return f"Comment({self.id}, @{self.author_username}, ❤{self.likes})"


# ─────────────────────────────────────────────
# TWITTER CLIENT — extends auth with API logic
# ─────────────────────────────────────────────

class TwitterClient(TwitterAuth):
    """Full X client: auth + search + comments + posting."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._search_graphql_broken: bool = False
        self._search_account_restricted: bool = False

    # ── Tweet parsing ──────────────────────────────────────────────────

    @staticmethod
    def _parse_tweet(result: dict) -> Optional[Tweet]:
        try:
            if result.get("__typename") == "TweetWithVisibilityResults":
                result = result.get("tweet", result)
            core = result.get("core", {})

            _user_result = (
                core.get("user_results", {}).get("result", {})
                or result.get("author_results", {}).get("result", {})
                or result.get("user_results", {}).get("result", {})
            )
            _user_legacy = _user_result.get("legacy", {})
            _user_core   = _user_result.get("core", {})

            screen_name = (
                _user_core.get("screen_name", "")
                or _user_legacy.get("screen_name", "")
                or _user_result.get("screen_name", "")
            )

            legacy_user = _user_legacy or _user_core

            legacy = result.get("legacy", {})
            views = result.get("views", {})
            tweet_id = legacy.get("id_str") or result.get("rest_id", "")
            if not tweet_id:
                return None

            _privacy = _user_result.get("privacy", {})
            is_protected = (
                legacy_user.get("protected", False)
                or _privacy.get("protected", False)
            )
            if is_protected:
                return None

            _rs_raw = (
                legacy.get("reply_settings")
                or result.get("reply_settings")
                or result.get("tweet", {}).get("legacy", {}).get("reply_settings")
                or _user_result.get("reply_settings")
            )
            _rs = (_rs_raw or "everyone").strip().lower()
            if _rs not in ("everyone", "", "following", "mentionedusers"):
                logger.debug(
                    f"[Filter] Skipping tweet {tweet_id} — reply_settings={_rs_raw!r}"
                )
                return None

            if not screen_name:
                user_typename = _user_result.get("__typename", "")
                if user_typename in ("UserUnavailable", "UserBlocked"):
                    logger.debug(f"[Parse] tweet {tweet_id} — user {user_typename}, skipping")
                    return None
                logger.debug(f"[Parse] tweet {tweet_id} — screen_name missing (typename={user_typename!r}), skipping")
                return None

            author_id = _user_legacy.get("id_str", "") or str(_user_result.get("id", ""))
            image_urls = []
            for media in (
                legacy.get("extended_entities", {}).get("media", [])
                or legacy.get("entities", {}).get("media", [])
            ):
                if media.get("type") in ("photo", "animated_gif"):
                    url = media.get("media_url_https") or media.get("media_url")
                    if url:
                        image_urls.append(url + "?format=jpg&name=large")

            return Tweet(
                id=tweet_id,
                text=legacy.get("full_text", legacy.get("text", "")),
                author_id=author_id,
                author_username=screen_name,
                likes=legacy.get("favorite_count", 0),
                retweets=legacy.get("retweet_count", 0),
                views=int(views.get("count", 0)) if views.get("count") else 0,
                reply_count=legacy.get("reply_count", 0),
                created_at=legacy.get("created_at", ""),
                conversation_id=legacy.get("conversation_id_str", tweet_id),
                lang=legacy.get("lang", "en"),
                image_urls=image_urls,
            )
        except Exception as e:
            logger.debug(f"Tweet parse error: {e}")
            return None

    @staticmethod
    def _extract_timeline_tweets(data: dict) -> list[Tweet]:
        tweets: list[Tweet] = []
        try:
            d = data.get("data", {})
            instructions = (
                d.get("search_by_raw_query", {}).get("search_timeline", {})
                 .get("timeline", {}).get("instructions", [])
                or d.get("list", {}).get("tweets_timeline", {})
                    .get("timeline", {}).get("instructions", [])
                or d.get("timeline_by_id", {}).get("timeline", {}).get("instructions", [])
                or d.get("home", {}).get("home_timeline_urt", {}).get("instructions", [])
            )
            for instr in instructions:
                for entry in instr.get("entries", []):
                    item = entry.get("content", {}).get("itemContent", {})
                    if item.get("itemType") == "TimelineTweet":
                        t = TwitterClient._parse_tweet(
                            item.get("tweet_results", {}).get("result", {})
                        )
                        if t:
                            tweets.append(t)
        except Exception as e:
            logger.debug(f"Timeline extract error: {e}")
        return tweets

    @staticmethod
    def _parse_created_at(ts: str) -> Optional[datetime]:
        try:
            dt = datetime.strptime(ts, "%a %b %d %H:%M:%S +0000 %Y")
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def _filter_tweets(self, tweets: list[Tweet], min_likes: int,
                       min_retweets: int, max_age_minutes: int, limit: int,
                       lang: str = "en") -> list[Tweet]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        result = []
        for t in tweets:
            if t.likes < min_likes or t.retweets < min_retweets:
                continue
            if t.created_at:
                dt = self._parse_created_at(t.created_at)
                if dt and dt < cutoff:
                    continue
            if lang and t.lang and t.lang.lower() != lang.lower():
                logger.debug(f"[Filter] Skipping tweet {t.id} — lang={t.lang!r} (expected {lang!r})")
                continue
            result.append(t)
            if len(result) >= limit:
                break
        return result

    _RAW_QUERY_SIGNALS = (
        "min_faves:", "min_retweets:", "since:", "until:",
        "-filter:", "filter:", "from:", "to:", "lang:",
        " OR ", " AND ", " -",
    )

    @staticmethod
    def _is_raw_query(query: str) -> bool:
        for signal in TwitterClient._RAW_QUERY_SIGNALS:
            if signal in query:
                return True
        return False

    @staticmethod
    def _refresh_since_date(query: str) -> str:
        import re
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        return re.sub(r'since:\d{4}-\d{2}-\d{2}', f'since:{today}', query)

    _FINANCE_KEYWORDS = (
        "market", "stock", "trade", "trading", "invest", "crypto", "bitcoin", "btc",
        "eth", "fed", "inflation", "economy", "macro", "gdp", "rate", "bond",
        "yield", "equity", "hedge", "fund", "ipo", "earnings", "revenue", "profit",
        "loss", "bull", "bear", "rally", "dip", "correction", "recession", "oil",
        "gold", "silver", "forex", "dollar", "euro", "yen", "yuan", "spx", "spy",
        "qqq", "etf", "options", "futures", "gamma", "vix", "volatility", "margin",
        "leverage", "portfolio", "asset", "defi", "altcoin", "nft", "wallet",
        "exchange", "liquidity", "capital", "debt", "fiscal", "monetary", "bank",
        "finance", "financial", "money", "cash", "revenue", "nasdaq", "dow",
    )

    @staticmethod
    def _is_finance_topic(text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in TwitterClient._FINANCE_KEYWORDS)

    @staticmethod
    def _parse_min_faves(query: str) -> int:
        import re
        m = re.search(r"min_faves:(\d+)", query)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _build_search_query(query: str, min_likes: int = 0, min_retweets: int = 0,
                             lang: str = "en") -> str:
        if TwitterClient._is_raw_query(query):
            refreshed = TwitterClient._refresh_since_date(query)
            if refreshed != query:
                logger.debug(f"[QueryBuilder] Auto-refreshed since: date in raw query")
            if "min_faves:" not in refreshed and min_likes > 0:
                refreshed = refreshed.strip() + f" min_faves:{min_likes}"
                logger.debug(f"[QueryBuilder] Injected min_faves:{min_likes} into raw query")
            logger.debug(f"[QueryBuilder] Raw query detected — passing through: {refreshed[:120]}")
            return refreshed.strip()

        keywords = [k.strip() for k in query.split(",") if k.strip()]
        if len(keywords) > 1:
            query_part = "(" + " OR ".join(keywords) + ")"
        else:
            query_part = keywords[0] if keywords else query

        parts = [query_part]
        if min_likes > 0:
            parts.append(f"min_faves:{min_likes}")
        if min_retweets > 0:
            parts.append(f"min_retweets:{min_retweets}")
        parts.append("-filter:replies")
        if lang:
            parts.append(f"lang:{lang}")
        return " ".join(parts)

    # ── Search by keywords ─────────────────────────────────────────────

    async def search_tweets(self, query: str, min_likes: int = 200, min_retweets: int = 0,
                             max_age_minutes: int = 60, limit: int = 20,
                             lang: str = "en") -> list[Tweet]:
        try:
            from browser_poster import get_browser_poster
            poster = await get_browser_poster()
            results = await poster.search_tweets(
                account_id=self.account_id,
                auth_token=self._auth_token,
                ct0=self._ct0,
                query=query,
                min_likes=min_likes,
                min_retweets=min_retweets,
                max_age_minutes=max_age_minutes,
                limit=limit,
                lang=lang,
            )
            if results:
                return results
            logger.debug(f"[search:browser] 0 results — falling back to API")
        except Exception as e:
            logger.debug(f"[search:browser] unavailable ({e}) — falling back to API")

        if self._search_graphql_broken:
            logger.debug(f"[search] GraphQL known broken — trying REST fallbacks first")
            if self._search_account_restricted:
                logger.debug("[search] Account search restricted — skipping REST, using guest token")
                return await self._search_via_guest_token(
                    query, min_likes, min_retweets, max_age_minutes, limit, lang
                )
            rest_result = await self._search_tweets_rest(
                query, min_likes, min_retweets, max_age_minutes, limit, lang
            )
            if rest_result:
                return rest_result
            logger.debug(f"[search] REST also failed — falling back to guest token")
            return await self._search_via_guest_token(
                query, min_likes, min_retweets, max_age_minutes, limit, lang
            )

        full_query = self._build_search_query(query, min_likes, min_retweets, lang)

        if not TwitterClient._is_raw_query(query):
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if "since:" not in full_query:
                full_query += f" since:{today}"
            full_query += " -filter:replies lang:en"

        is_raw = self._is_raw_query(query)
        effective_min_likes = self._parse_min_faves(query) if is_raw else 0
        effective_lang      = ""
        from urllib.parse import quote
        search_referer = f"https://x.com/search?q={quote(full_query)}&src=typed_query&f=live"
        params = {
            "variables": json.dumps({"rawQuery": full_query, "count": 40,
                                     "querySource": "typed_query", "product": "Latest"}),
            "features": FEATURES_SEARCH,
        }
        for qid in self._SEARCH_TIMELINE_QUERY_IDS:
            endpoint = f"{GRAPHQL}/{qid}/SearchTimeline"
            try:
                data = await self._get(endpoint, params=params, referer=search_referer)
                if data:
                    if qid != self._SEARCH_TIMELINE_QUERY_IDS[0]:
                        self._SEARCH_TIMELINE_QUERY_IDS.remove(qid)
                        self._SEARCH_TIMELINE_QUERY_IDS.insert(0, qid)
                        logger.info(f"[QueryID] SearchTimeline updated to {qid}")
                    tweets = self._extract_timeline_tweets(data)
                    _ml = effective_min_likes if is_raw else 0
                    _lg = effective_lang if is_raw else lang
                    result = self._filter_tweets(tweets, _ml, 0, max_age_minutes, limit, lang=_lg)
                    logger.info(f"[search:graphql] '{query[:80]}' → {len(result)} tweets")
                    return result
            except Exception as e:
                logger.debug(f"[search] queryId {qid} failed: {e}")
                continue

        logger.warning(f"[search] All GraphQL SearchTimeline queryIds failed — trying REST fallback")
        result = await self._search_tweets_rest(query, min_likes, min_retweets, max_age_minutes, limit, lang=lang)
        if not result:
            self._search_graphql_broken = True
            logger.info(f"[search] Marking GraphQL search broken — future calls use guest token")
            logger.info(f"[search] Last resort: HomeTimeline keyword filter for '{query[:60]}'")
            result = await self._search_via_home_timeline(
                query, min_likes, max_age_minutes, lang, limit,
            )
        return result

    async def _search_tweets_rest(self, query: str, min_likes: int = 0, min_retweets: int = 0,
                                   max_age_minutes: int = 60, limit: int = 20,
                                   lang: str = "en") -> list[Tweet]:
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)

        def _parse_v1_tweet(t: dict) -> Optional[Tweet]:
            if t.get("retweeted_status"):
                return None
            user = t.get("user", {})
            if user.get("protected", False):
                return None
            if not user.get("screen_name", ""):
                return None
            _rs2 = (t.get("reply_settings") or "everyone").strip().lower()
            if _rs2 not in ("everyone", ""):
                return None
            return Tweet(
                id=str(t.get("id_str", t.get("id", ""))),
                text=t.get("full_text", t.get("text", "")),
                author_id=str(user.get("id_str", "")),
                author_username=user.get("screen_name", "unknown"),
                likes=t.get("favorite_count", 0),
                retweets=t.get("retweet_count", 0),
                views=0,
                reply_count=t.get("reply_count", 0),
                created_at=t.get("created_at", ""),
                conversation_id=str(t.get("conversation_id_str", t.get("id_str", ""))),
                lang=t.get("lang", "en"),
            )

        def _filter_v1(raw: list) -> list:
            result = []
            for t in raw:
                tweet = _parse_v1_tweet(t)
                if not tweet:
                    continue
                if tweet.likes < min_likes or tweet.retweets < min_retweets:
                    continue
                if lang and tweet.lang and tweet.lang.lower() != lang.lower():
                    continue
                if tweet.created_at:
                    try:
                        dt = datetime.strptime(tweet.created_at, "%a %b %d %H:%M:%S +0000 %Y").replace(tzinfo=timezone.utc)
                        if dt < cutoff:
