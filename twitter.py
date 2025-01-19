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
                            continue
                    except Exception:
                        pass
                result.append(tweet)
                if len(result) >= limit:
                    break
            return result

        try:
            from urllib.parse import quote as _q, urlparse
            full_query = self._build_search_query(query, min_likes, min_retweets, lang)
            url1 = "https://x.com/i/api/2/search/adaptive.json"
            self._refresh_request_headers(
                referer=f"https://x.com/search?q={_q(full_query)}&src=typed_query&f=live",
                method="GET", path=urlparse(url1).path
            )
            p1 = {
                "q":                        full_query,
                "count":                    "40",
                "query_source":             "typed_query",
                "pc":                       "1",
                "spelling_corrections":     "1",
                "include_ext_edit_control": "true",
                "tweet_mode":               "extended",
                "include_entities":         "true",
            }
            resp1 = await self._client.get(url1, params=p1)
            body_len = len(resp1.content)
            logger.info(f"[search:adaptive] HTTP {resp1.status_code} body={body_len}b")
            if resp1.status_code == 200 and body_len > 0:
                try:
                    data1 = resp1.json()
                except Exception as json_err:
                    logger.debug(f"[search:adaptive] JSON parse error: {json_err}")
                    data1 = {}
                raw_tweets_map = data1.get("globalObjects", {}).get("tweets", {})
                users_map      = data1.get("globalObjects", {}).get("users", {})
                raw_list = []
                for tid, t in raw_tweets_map.items():
                    user_id = str(t.get("user_id_str", t.get("user_id", "")))
                    user = users_map.get(user_id, {})
                    t["user"] = {
                        "id_str":      user_id,
                        "screen_name": user.get("screen_name", "unknown"),
                        "protected":   user.get("protected", False),
                    }
                    raw_list.append(t)
                raw_list.sort(key=lambda x: x.get("favorite_count", 0), reverse=True)
                res1 = _filter_v1(raw_list)
                if res1:
                    logger.info(f"[search:adaptive] '{query}' → {len(res1)} tweets")
                    return res1
                logger.debug(f"[search:adaptive] 0 after filters (raw={len(raw_list)})")
            else:
                if body_len == 0 and resp1.status_code == 200:
                    self._search_account_restricted = True
                    logger.warning(
                        f"[search] ⚠️  Аккаунт НЕ ИМЕЕТ ДОСТУПА К ПОИСКУ (X Search Restricted). "
                        f"adaptive.json вернул HTTP 200 + 0 байт — X блокирует поиск для этой сессии. "
                        f"Решение: используйте другой/более старый аккаунт, "
                        f"или включите браузерный поиск (Playwright)."
                    )
                else:
                    logger.info(f"[search:adaptive] rejected — HTTP {resp1.status_code}")
        except Exception as e:
            logger.debug(f"[search:adaptive] Failed: {e}")

        try:
            url2 = "https://x.com/i/api/1.1/search/tweets.json"
            self._refresh_request_headers(
                referer=f"https://x.com/search?q={_q(full_query)}&src=typed_query",
                method="GET", path=urlparse(url2).path
            )
            p2 = {
                "q":            full_query,
                "result_type":  "recent",
                "count":        "40",
                "tweet_mode":   "extended",
                "include_entities": "true",
            }
            resp2 = await self._client.get(url2, params=p2)
            body_len2 = len(resp2.content)
            logger.info(f"[search:v1.1] HTTP {resp2.status_code} body={body_len2}b")
            if resp2.status_code == 200 and body_len2 > 0:
                try:
                    data2 = resp2.json()
                except Exception as json_err:
                    logger.debug(f"[search:v1.1] JSON parse error: {json_err}")
                    data2 = {}
                res2 = _filter_v1(data2.get("statuses", []))
                if res2:
                    logger.info(f"[search:v1.1] '{query}' → {len(res2)} tweets")
                    return res2
            else:
                if body_len2 == 0:
                    logger.info(f"[search:v1.1] empty body (HTTP 200, 0b) — session may lack search access")
                else:
                    logger.info(f"[search:v1.1] rejected — HTTP {resp2.status_code}")
        except Exception as e:
            logger.debug(f"[search:v1.1] Failed: {e}")

        guest_results = await self._search_via_guest_token(
            query, min_likes, min_retweets, max_age_minutes, limit, lang
        )
        if guest_results:
            return guest_results

        logger.warning(
            f"[search] All methods failed for '{query}' — "
            f"check logs above for HTTP status codes."
        )
        return []

    async def _search_via_guest_token(
        self, query: str, min_likes: int = 0, min_retweets: int = 0,
        max_age_minutes: int = 60, limit: int = 20, lang: str = "en"
    ) -> list[Tweet]:
        from urllib.parse import quote as _q
        from datetime import datetime, timezone, timedelta
        BEARER = (
            "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
            "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
        )
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)

        try:
            import httpx as _httpx
            from config import get_browser_headers
            base_headers = get_browser_headers()
            base_headers["Authorization"] = f"Bearer {BEARER}"
            base_headers["x-twitter-client-language"] = "en"
            base_headers["x-twitter-active-user"] = "yes"
            base_headers["Sec-Fetch-Site"] = "same-origin"
            base_headers["Sec-Fetch-Mode"] = "cors"

            async with _httpx.AsyncClient(
                headers=base_headers,
                timeout=_httpx.Timeout(20.0, connect=10.0),
                follow_redirects=True,
                http2=True,
            ) as guest_client:
                activate_resp = await guest_client.post(
                    "https://api.twitter.com/1.1/guest/activate.json"
                )
                if activate_resp.status_code != 200:
                    logger.debug(f"[search:guest] activate failed HTTP {activate_resp.status_code}")
                    return []
                guest_token = activate_resp.json().get("guest_token", "")
                if not guest_token:
                    logger.debug("[search:guest] no guest_token in response")
                    return []

                guest_client.headers.update({
                    "x-guest-token": guest_token,
                    "Referer": f"https://x.com/search?q={_q(query)}&src=typed_query&f=live",
                })

                raw_query = f"{query} lang:{lang}" if lang else query
                for qid in self._SEARCH_TIMELINE_QUERY_IDS[:3]:
                    endpoint = f"https://x.com/i/api/graphql/{qid}/SearchTimeline"
                    params = {
                        "variables": json.dumps({
                            "rawQuery": raw_query,
                            "count": 40,
                            "querySource": "typed_query",
                            "product": "Latest",
                        }),
                        "features": FEATURES_SEARCH,
                    }
                    try:
                        resp = await guest_client.get(endpoint, params=params)
                        if resp.status_code != 200 or not resp.content:
                            logger.debug(f"[search:guest] qid {qid} → HTTP {resp.status_code} {len(resp.content)}b")
                            continue
                        data = resp.json()
                        tweets = self._extract_timeline_tweets(data)
                        result = []
                        for t in tweets:
                            if t.likes < min_likes or t.retweets < min_retweets:
                                continue
                            if lang and t.lang and t.lang.lower() != lang.lower():
                                continue
                            if t.created_at:
                                dt = self._parse_created_at(t.created_at)
                                if dt and dt < cutoff:
                                    continue
                            result.append(t)
                            if len(result) >= limit:
                                break
                        if result:
                            logger.success(f"[search:guest] '{query}' → {len(result)} tweets (guest token)")
                            return result
                        logger.debug(f"[search:guest] qid {qid} → 0 after filter (raw={len(tweets)})")
                    except Exception as e:
                        logger.debug(f"[search:guest] qid {qid} failed: {e}")
                        continue

        except Exception as e:
            logger.debug(f"[search:guest] Failed: {e}")

        logger.info("[search:guest] no results via guest token")
        self._search_graphql_broken = False
        return []

    # ── X List feed ────────────────────────────────────────────────────

    async def get_list_tweets(self, list_url: str, min_likes: int = 200, min_retweets: int = 0,
                               max_age_minutes: int = 60, limit: int = 20,
                               lang: str = "en") -> list[Tweet]:
        match = re.search(r"/lists/(\d+)", list_url)
        if not match:
            logger.error(f"Invalid list URL: {list_url}")
            return []
        list_id = match.group(1)
        params = {
            "variables": json.dumps({"listId": list_id, "count": 40}),
            "features": FEATURES_SEARCH,
        }
        last_err = None
        for qid in self._LIST_TIMELINE_QUERY_IDS:
            endpoint = f"{GRAPHQL}/{qid}/ListLatestTweetsTimeline"
            try:
                data = await self._get(endpoint, params=params)
                if data:
                    if qid != self._LIST_TIMELINE_QUERY_IDS[0]:
                        self._LIST_TIMELINE_QUERY_IDS.remove(qid)
                        self._LIST_TIMELINE_QUERY_IDS.insert(0, qid)
                        logger.info(f"[QueryID] ListLatestTweetsTimeline updated to {qid}")
                    tweets = self._extract_timeline_tweets(data)
                    result = self._filter_tweets(tweets, min_likes, min_retweets, max_age_minutes, limit, lang=lang)
                    logger.info(f"[list:{list_id}] lang:{lang} → {len(result)} tweets")
                    return result
            except Exception as e:
                last_err = e
                logger.debug(f"[list] queryId {qid} failed: {e}")
                continue
        logger.error(f"[list] All ListLatestTweetsTimeline queryIds failed. Last: {last_err}")
        return []

    # ── Recommendations (For You) ──────────────────────────────────────

    async def _search_via_home_timeline(self, query: str, min_likes: int = 10,
                                          max_age_minutes: int = 60,
                                          lang: str = "en", limit: int = 20) -> list[Tweet]:
        if max_age_minutes > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        else:
            cutoff = None

        logger.info(f"[search:home] Keyword filter on HomeTimeline for '{query[:60]}'")
        try:
            import json as _json
            endpoint = f"{GRAPHQL}/HJFjzBgCs16TqxewQOeLNg/HomeTimeline"
            params = {
                "variables": _json.dumps({"count": 100, "includePromotedContent": False,
                                          "latestControlAvailable": True,
                                          "requestContext": "launch", "withCommunity": True,
                                          "seenTweetIds": []}),
                "features": FEATURES_SEARCH,
            }
            data = await self._get(endpoint, params=params)
            if not data:
                logger.warning("[search:home] HomeTimeline returned empty response")
                return []

            tweets = self._extract_timeline_tweets(data)

            import re
            clean = re.sub(r'\b(OR|AND)\b|-?filter:\S+|min_faves:\d+|since:\S+|until:\S+|lang:\S+', '', query)
            keywords = [w.strip('()').lower() for w in clean.split() if len(w.strip('()')) > 2]

            def _matches(t: Tweet) -> bool:
                text = t.text.lower()
                return any(kw in text for kw in keywords) if keywords else True

            result: list[Tweet] = []
            for t in tweets:
                if t.likes < min_likes:
                    continue
                if lang and t.lang and t.lang.lower() != lang.lower():
                    continue
                if cutoff is not None and t.created_at:
                    dt = self._parse_created_at(t.created_at)
                    if dt and dt < cutoff:
                        continue
                if not _matches(t):
                    continue
                result.append(t)
                if len(result) >= limit:
                    break

            logger.info(f"[search:home] HomeTimeline keyword filter → {len(result)} tweets (from {len(tweets)} total)")
            return result
        except Exception as e:
            logger.warning(f"[search:home] Failed: {e}")
            return []

    async def get_recommended_tweets(self, min_likes: int = 200, limit: int = 20,
                                      lang: str = "en") -> list[Tweet]:
        endpoint = f"{GRAPHQL}/HJFjzBgCs16TqxewQOeLNg/HomeTimeline"
        params = {
            "variables": json.dumps({"count": 40, "includePromotedContent": False,
                                     "latestControlAvailable": True,
                                     "requestContext": "launch", "withCommunity": True,
                                     "seenTweetIds": []}),
            "features": FEATURES_SEARCH,
        }
        data = await self._get(endpoint, params=params)
        tweets = self._extract_timeline_tweets(data)
        result = [
            t for t in tweets
            if t.likes >= min_likes
            and (not lang or not t.lang or t.lang.lower() == lang.lower())
            and self._is_finance_topic(t.text)
        ][:limit]
        filtered_out = len([t for t in tweets if t.likes >= min_likes]) - len(result)
        if filtered_out:
            logger.debug(f"[recommendations] topic filter removed {filtered_out} off-topic tweets")
        logger.info(f"[recommendations] lang:{lang} → {len(result)} tweets")
        return result

    # ── Get top comment ────────────────────────────────────────────────

    _TWEET_DETAIL_QUERY_IDS = [
        "flqCy6kvOMolEquuRpOaHQ",  # 2026-Q1
        "Ml4xGbzfNSYMpxr_JOvYtA",  # 2025-Q1
        "xOhkmRKjbxDMkgApYMXgJg",  # 2024-Q4
        "0hWvDhmW8YQ-S_ib3azIrw",  # 2024-Q3
    ]
    _SEARCH_TIMELINE_QUERY_IDS = [
        "flaR-PUMshxFWZWPNpq4zA",  # 2026-Q1
        "gkjsKepM6gl_HmFWoWKfgg",  # 2025-Q4
        "nK1dw4oV3k4w5TdtcAdSww",  # 2025-Q2
        "lZ0GCEojmtQfiUQa5oJSEw",  # 2024-Q4
    ]
    _LIST_TIMELINE_QUERY_IDS = [
        "BbCrSoXIR7z93lLCVFlQ2Q",
        "whF0_KH1fCkdHFTsdLEjCg",
    ]
    _query_id_discovered: bool = False

    @classmethod
    async def discover_query_ids(cls) -> None:
        if cls._query_id_discovered:
            return
        cls._query_id_discovered = True
        try:
            import httpx as _httpx
            from config import get_browser_headers

            async with _httpx.AsyncClient(
                headers=get_browser_headers(),
                timeout=_httpx.Timeout(20.0),
                follow_redirects=True,
            ) as client:
                js_urls: list[str] = []
                for page_url in ["https://x.com/home", "https://x.com/search?q=bitcoin&f=live"]:
                    try:
                        r = await client.get(page_url)
                        patterns = [
                            r"https://abs\.twimg\.com/responsive-web/client-web/[^\s\"']+api[^\s\"']*\.js",
                            r"https://abs\.twimg\.com/responsive-web/client-web/main\.[^\s\"']+\.js",
                            r"https://abs\.twimg\.com/responsive-web/client-web/[^\s\"']+bundle[^\s\"']*\.js",
                            r"https://abs\.twimg\.com/responsive-web/client-web/[^\s\"']{10,80}\.js",
                        ]
                        for pat in patterns:
                            found_urls = re.findall(pat, r.text)
                            for u in found_urls:
                                if u not in js_urls:
                                    js_urls.append(u)
                            if js_urls:
                                break
                    except Exception as e:
                        logger.debug(f"[QueryID] Failed to fetch {page_url}: {e}")

                if not js_urls:
                    logger.debug("[QueryID] No JS bundles found on any page")
                    return

                logger.debug(f"[QueryID] Found {len(js_urls)} JS bundle URLs to scan")

                _operations = {
                    "TweetDetail":               cls._TWEET_DETAIL_QUERY_IDS,
                    "SearchTimeline":            cls._SEARCH_TIMELINE_QUERY_IDS,
                    "ListLatestTweetsTimeline":  cls._LIST_TIMELINE_QUERY_IDS,
                }
                found: set = set()

                def _extract_qid(js_text: str, op_name: str) -> Optional[str]:
                    patterns = [
                        rf'queryId:"([A-Za-z0-9_-]{{15,30}})".{{0,80}}operationName:"{op_name}"',
                        rf'operationName:"{op_name}".{{0,80}}queryId:"([A-Za-z0-9_-]{{15,30}})"',
                        rf"queryId:'([A-Za-z0-9_-]{{15,30}})'.{{0,80}}operationName:'{op_name}'",
                        rf'\"queryId\":\"([A-Za-z0-9_-]{{15,30}})\".{{0,80}}\"{op_name}\"',
                    ]
                    for pat in patterns:
                        m = re.search(pat, js_text)
                        if m:
                            return m.group(1)
                    return None

                for js_url in js_urls[:8]:
                    if len(found) >= len(_operations):
                        break
                    try:
                        jr = await client.get(js_url)
                        js_text = jr.text
                        for op_name, id_list in _operations.items():
                            if op_name in found:
                                continue
                            qid = _extract_qid(js_text, op_name)
                            if qid:
                                found.add(op_name)
                                if qid not in id_list:
                                    id_list.insert(0, qid)
                                    logger.success(f"[QueryID] New {op_name}: {qid}")
                                else:
                                    logger.info(f"[QueryID] {op_name} confirmed: {qid}")
                    except Exception as e:
                        logger.debug(f"[QueryID] JS parse error for {js_url}: {e}")

                if not found:
                    logger.debug("[QueryID] Could not extract queryIds — using known IDs")
                else:
                    missing = set(_operations) - found
