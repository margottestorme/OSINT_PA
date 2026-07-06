"""Énumération de pseudos multi-plateformes (Module 15, type Sherlock/Maigret).

À partir d'un username, teste sa présence sur 120+ plateformes publiques via de simples
requêtes HTTP, en parallèle borné (semaphore) et avec un User-Agent rotatif. Le registre
`PLATFORMS` est **déclaratif** : ajouter une plateforme = ajouter un dict (aucun `if/elif`).

Deux méthodes de détection par plateforme :
- `status_code`   : 200 → compte trouvé, 404 (ou 4xx) → pas trouvé.
- `not_found_text`: la page renvoie 200 même si le compte n'existe pas (soft-404) ; on
  cherche alors un texte caractéristique de l'absence (`not_found_text`) dans le corps.

Résilience totale : toute erreur individuelle (timeout, DNS, TLS…) est capturée et renvoyée
dans le champ `error` — une plateforme qui échoue n'interrompt jamais l'énumération.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import AsyncGenerator

import httpx
from loguru import logger

# Timeout par plateforme (s) et timeout global de l'énumération (s)
PLATFORM_TIMEOUT = 8.0
GLOBAL_TIMEOUT = 30.0
DEFAULT_MAX_CONCURRENT = 20

# User-Agents rotatifs (navigateurs récents) — réduit les blocages/anti-bot triviaux.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
]


# --------------------------------------------------------------------------- #
# Registre déclaratif des plateformes                                         #
# --------------------------------------------------------------------------- #
# Chaque entrée : name, url ({username}), category, check_method, et optionnellement
# not_found_text (pour check_method="not_found_text") et headers (headers spéciaux).

PLATFORMS: list[dict] = [
    # --- dev ---
    {"name": "GitHub", "url": "https://github.com/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "GitLab", "url": "https://gitlab.com/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "BitBucket", "url": "https://bitbucket.org/{username}/", "category": "dev", "check_method": "status_code"},
    {"name": "Codeberg", "url": "https://codeberg.org/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "SourceForge", "url": "https://sourceforge.net/u/{username}/profile/", "category": "dev", "check_method": "status_code"},
    {"name": "Launchpad", "url": "https://launchpad.net/~{username}", "category": "dev", "check_method": "status_code"},
    {"name": "StackOverflow", "url": "https://stackoverflow.com/users/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "Dev.to", "url": "https://dev.to/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "Hashnode", "url": "https://hashnode.com/@{username}", "category": "dev", "check_method": "status_code"},
    {"name": "ProductHunt", "url": "https://www.producthunt.com/@{username}", "category": "dev", "check_method": "status_code"},
    {"name": "Keybase", "url": "https://keybase.io/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "GitBook", "url": "https://{username}.gitbook.io/", "category": "dev", "check_method": "status_code"},
    {"name": "CodePen", "url": "https://codepen.io/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "JSFiddle", "url": "https://jsfiddle.net/user/{username}/", "category": "dev", "check_method": "status_code"},
    {"name": "Replit", "url": "https://replit.com/@{username}", "category": "dev", "check_method": "status_code"},
    {"name": "Kaggle", "url": "https://www.kaggle.com/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "HuggingFace", "url": "https://huggingface.co/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "DockerHub", "url": "https://hub.docker.com/u/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "NPM", "url": "https://www.npmjs.com/~{username}", "category": "dev", "check_method": "status_code"},
    {"name": "PyPI", "url": "https://pypi.org/user/{username}/", "category": "dev", "check_method": "status_code"},
    {"name": "RubyGems", "url": "https://rubygems.org/profiles/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "Crates.io", "url": "https://crates.io/users/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "LeetCode", "url": "https://leetcode.com/{username}/", "category": "dev", "check_method": "status_code"},
    {"name": "HackerRank", "url": "https://www.hackerrank.com/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "Codewars", "url": "https://www.codewars.com/users/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "Exercism", "url": "https://exercism.org/profiles/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "TryHackMe", "url": "https://tryhackme.com/p/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "HackTheBox", "url": "https://app.hackthebox.com/users/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "Root-Me", "url": "https://www.root-me.org/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "FreeCodeCamp", "url": "https://www.freecodecamp.org/{username}", "category": "dev", "check_method": "status_code"},

    # --- social ---
    {"name": "Twitter/X", "url": "https://x.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Instagram", "url": "https://www.instagram.com/{username}/", "category": "social", "check_method": "status_code"},
    {"name": "Facebook", "url": "https://www.facebook.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Reddit", "url": "https://www.reddit.com/user/{username}", "category": "social", "check_method": "status_code"},
    {"name": "TikTok", "url": "https://www.tiktok.com/@{username}", "category": "social", "check_method": "status_code"},
    {"name": "Pinterest", "url": "https://www.pinterest.com/{username}/", "category": "social", "check_method": "status_code"},
    {"name": "Tumblr", "url": "https://{username}.tumblr.com/", "category": "social", "check_method": "status_code"},
    {"name": "Mastodon.social", "url": "https://mastodon.social/@{username}", "category": "social", "check_method": "status_code"},
    {"name": "Bluesky", "url": "https://bsky.app/profile/{username}.bsky.social", "category": "social", "check_method": "status_code"},
    {"name": "Threads", "url": "https://www.threads.net/@{username}", "category": "social", "check_method": "status_code"},
    {"name": "VK", "url": "https://vk.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "OK.ru", "url": "https://ok.ru/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Snapchat", "url": "https://www.snapchat.com/add/{username}", "category": "social", "check_method": "status_code"},
    {"name": "BeReal", "url": "https://bere.al/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Vero", "url": "https://vero.co/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Ello", "url": "https://ello.co/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Minds", "url": "https://www.minds.com/{username}/", "category": "social", "check_method": "status_code"},
    {"name": "Gab", "url": "https://gab.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "CounterSocial", "url": "https://counter.social/@{username}", "category": "social", "check_method": "status_code"},
    {"name": "Telegram", "url": "https://t.me/{username}", "category": "social", "check_method": "not_found_text",
     "not_found_text": "tgme_page_extra"},
    {"name": "Signal", "url": "https://signal.me/#p/{username}", "category": "social", "check_method": "status_code"},
    {"name": "WhatsApp Business", "url": "https://wa.me/{username}", "category": "social", "check_method": "status_code"},
    {"name": "AboutMe", "url": "https://about.me/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Linktree", "url": "https://linktr.ee/{username}", "category": "social", "check_method": "status_code"},
    {"name": "AllMyLinks", "url": "https://allmylinks.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Carrd", "url": "https://{username}.carrd.co/", "category": "social", "check_method": "status_code"},
    {"name": "Gravatar", "url": "https://gravatar.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Strava", "url": "https://www.strava.com/athletes/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Komoot", "url": "https://www.komoot.com/user/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Garmin Connect", "url": "https://connect.garmin.com/modern/profile/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Yelp", "url": "https://www.yelp.com/user_details?userid={username}", "category": "social", "check_method": "status_code"},
    {"name": "Foursquare", "url": "https://foursquare.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "TripAdvisor", "url": "https://www.tripadvisor.com/Profile/{username}", "category": "social", "check_method": "status_code"},

    # --- forum ---
    {"name": "HackerNews", "url": "https://news.ycombinator.com/user?id={username}", "category": "forum",
     "check_method": "not_found_text", "not_found_text": "No such user."},
    {"name": "Quora", "url": "https://www.quora.com/profile/{username}", "category": "forum", "check_method": "status_code"},
    {"name": "Yahoo Answers", "url": "https://answers.yahoo.com/activity/questions?show={username}", "category": "forum", "check_method": "status_code"},
    {"name": "Fandom", "url": "https://community.fandom.com/wiki/User:{username}", "category": "forum", "check_method": "status_code"},
    {"name": "Amino", "url": "https://aminoapps.com/u/{username}", "category": "forum", "check_method": "status_code"},
    {"name": "Disqus", "url": "https://disqus.com/by/{username}/", "category": "forum", "check_method": "status_code"},
    {"name": "Wikipedia (en)", "url": "https://en.wikipedia.org/wiki/User:{username}", "category": "forum", "check_method": "status_code"},
    {"name": "Wikipedia (fr)", "url": "https://fr.wikipedia.org/wiki/Utilisateur:{username}", "category": "forum", "check_method": "status_code"},

    # --- gaming ---
    {"name": "Steam", "url": "https://steamcommunity.com/id/{username}", "category": "gaming",
     "check_method": "not_found_text", "not_found_text": "The specified profile could not be found."},
    {"name": "Twitch", "url": "https://www.twitch.tv/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Discord", "url": "https://discord.com/users/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Xbox Gamertag", "url": "https://xboxgamertag.com/search/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "PSN Profiles", "url": "https://psnprofiles.com/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Chess.com", "url": "https://www.chess.com/member/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Lichess", "url": "https://lichess.org/@/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Roblox", "url": "https://www.roblox.com/user.aspx?username={username}", "category": "gaming", "check_method": "status_code"},
    {"name": "NameMC (Minecraft)", "url": "https://namemc.com/profile/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "osu!", "url": "https://osu.ppy.sh/users/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Speedrun.com", "url": "https://www.speedrun.com/user/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "GameJolt", "url": "https://gamejolt.com/@{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Itch.io", "url": "https://{username}.itch.io/", "category": "gaming", "check_method": "status_code"},
    {"name": "Newgrounds", "url": "https://{username}.newgrounds.com/", "category": "gaming", "check_method": "status_code"},
    {"name": "Trovo", "url": "https://trovo.live/s/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Kick", "url": "https://kick.com/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Duolingo", "url": "https://www.duolingo.com/profile/{username}", "category": "gaming", "check_method": "status_code"},

    # --- creative ---
    {"name": "YouTube", "url": "https://www.youtube.com/@{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Vimeo", "url": "https://vimeo.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Dailymotion", "url": "https://www.dailymotion.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Spotify", "url": "https://open.spotify.com/user/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "SoundCloud", "url": "https://soundcloud.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Bandcamp", "url": "https://{username}.bandcamp.com/", "category": "creative", "check_method": "status_code"},
    {"name": "Last.fm", "url": "https://www.last.fm/user/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Behance", "url": "https://www.behance.net/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Dribbble", "url": "https://dribbble.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Flickr", "url": "https://www.flickr.com/people/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "500px", "url": "https://500px.com/p/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "DeviantArt", "url": "https://www.deviantart.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "ArtStation", "url": "https://www.artstation.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "VSCO", "url": "https://vsco.co/{username}/gallery", "category": "creative", "check_method": "status_code"},
    {"name": "Unsplash", "url": "https://unsplash.com/@{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Pexels", "url": "https://www.pexels.com/@{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Imgur", "url": "https://imgur.com/user/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Giphy", "url": "https://giphy.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Medium", "url": "https://medium.com/@{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Substack", "url": "https://{username}.substack.com/", "category": "creative", "check_method": "status_code"},
    {"name": "Blogger", "url": "https://{username}.blogspot.com/", "category": "creative", "check_method": "status_code"},
    {"name": "WordPress", "url": "https://{username}.wordpress.com/", "category": "creative", "check_method": "status_code"},
    {"name": "Wattpad", "url": "https://www.wattpad.com/user/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "FanFiction.net", "url": "https://www.fanfiction.net/~{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Archive of Our Own", "url": "https://archiveofourown.org/users/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "SlideShare", "url": "https://www.slideshare.net/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Scribd", "url": "https://www.scribd.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Rumble", "url": "https://rumble.com/user/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Odysee", "url": "https://odysee.com/@{username}", "category": "creative", "check_method": "status_code"},
    {"name": "BitChute", "url": "https://www.bitchute.com/channel/{username}/", "category": "creative", "check_method": "status_code"},
    {"name": "Goodreads", "url": "https://www.goodreads.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "LibraryThing", "url": "https://www.librarything.com/profile/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Letterboxd", "url": "https://letterboxd.com/{username}/", "category": "creative", "check_method": "status_code"},
    {"name": "Trakt", "url": "https://trakt.tv/users/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "IMDb", "url": "https://www.imdb.com/user/{username}/", "category": "creative", "check_method": "status_code"},
    {"name": "MyAnimeList", "url": "https://myanimelist.net/profile/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "AniList", "url": "https://anilist.co/user/{username}/", "category": "creative", "check_method": "status_code"},
    {"name": "Kitsu", "url": "https://kitsu.io/users/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Untappd", "url": "https://untappd.com/user/{username}", "category": "creative", "check_method": "status_code"},

    # --- professional ---
    {"name": "LinkedIn", "url": "https://www.linkedin.com/in/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Fiverr", "url": "https://www.fiverr.com/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Freelancer", "url": "https://www.freelancer.com/u/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Upwork", "url": "https://www.upwork.com/freelancers/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "AngelList", "url": "https://angel.co/u/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Crunchbase", "url": "https://www.crunchbase.com/person/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Notion", "url": "https://www.notion.so/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Patreon", "url": "https://www.patreon.com/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Ko-fi", "url": "https://ko-fi.com/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Buy Me a Coffee", "url": "https://www.buymeacoffee.com/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Gumroad", "url": "https://{username}.gumroad.com/", "category": "professional", "check_method": "status_code"},
    {"name": "Etsy", "url": "https://www.etsy.com/shop/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "eBay", "url": "https://www.ebay.com/usr/{username}", "category": "professional",
     "check_method": "not_found_text", "not_found_text": "The User ID you entered was not found"},
    {"name": "Amazon Author", "url": "https://www.amazon.com/author/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Coursera", "url": "https://www.coursera.org/user/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Udemy", "url": "https://www.udemy.com/user/{username}/", "category": "professional", "check_method": "status_code"},

    # --- dating ---
    {"name": "Tinder", "url": "https://tinder.com/@{username}", "category": "dating", "check_method": "status_code"},
    {"name": "OkCupid", "url": "https://www.okcupid.com/profile/{username}", "category": "dating", "check_method": "status_code"},
    {"name": "MeetMe", "url": "https://www.meetme.com/{username}", "category": "dating", "check_method": "status_code"},
    {"name": "Badoo", "url": "https://badoo.com/profile/{username}", "category": "dating", "check_method": "status_code"},

    # --- crypto ---
    {"name": "CoinMarketCap", "url": "https://coinmarketcap.com/community/profile/{username}/", "category": "crypto", "check_method": "status_code"},
    {"name": "ENS (Etherscan)", "url": "https://app.ens.domains/{username}", "category": "crypto", "check_method": "status_code"},
    {"name": "OpenSea", "url": "https://opensea.io/{username}", "category": "crypto", "check_method": "status_code"},
    {"name": "Rarible", "url": "https://rarible.com/{username}", "category": "crypto", "check_method": "status_code"},
    {"name": "Mirror.xyz", "url": "https://mirror.xyz/{username}", "category": "crypto", "check_method": "status_code"},
    {"name": "Gitcoin", "url": "https://gitcoin.co/{username}", "category": "crypto", "check_method": "status_code"},

    # --- news ---
    {"name": "HackerNoon", "url": "https://hackernoon.com/u/{username}", "category": "news", "check_method": "status_code"},
    {"name": "Steemit", "url": "https://steemit.com/@{username}", "category": "news", "check_method": "status_code"},
    {"name": "Hive", "url": "https://hive.blog/@{username}", "category": "news", "check_method": "status_code"},
    {"name": "Vocal", "url": "https://vocal.media/authors/{username}", "category": "news", "check_method": "status_code"},
]

# Nombre total et catégories disponibles (utilisés par l'endpoint/l'UI)
PLATFORM_COUNT = len(PLATFORMS)


def platform_categories() -> list[str]:
    """Catégories distinctes présentes dans le registre, triées."""
    return sorted({p["category"] for p in PLATFORMS})


def normalize_username(username: str) -> str:
    """Nettoie le username pour la construction d'URL (strip des espaces).

    La casse est **préservée** dans l'URL (certaines plateformes y sont sensibles) ;
    la mise en minuscules est réservée à la déduplication/recherche en base (Module 15 §2).
    """
    return (username or "").strip()


def _build_url(platform: dict, username: str) -> str:
    return platform["url"].format(username=username)


def _evaluate_found(platform: dict, response: httpx.Response) -> bool:
    """Détermine si le compte existe selon la méthode déclarée par la plateforme."""
    method = platform.get("check_method", "status_code")
    if method == "not_found_text":
        # Soft-404 : la page renvoie 200 même absente → on cherche le marqueur d'absence.
        if response.status_code >= 400:
            return False
        marker = platform.get("not_found_text")
        if not marker:
            return response.status_code == 200
        return marker not in response.text
    # status_code : 200 = trouvé, tout le reste = pas trouvé.
    return response.status_code == 200


async def check_username(
    username: str,
    platform: dict,
    session: httpx.AsyncClient,
) -> dict:
    """Teste la présence d'un `username` sur une plateforme donnée.

    Ne lève jamais : toute exception est capturée et exposée dans `error` (found=False).
    Retour : {platform, url, category, found, response_time_ms, error}.
    """
    url = _build_url(platform, username)
    headers = dict(platform.get("headers") or {})
    headers.setdefault("User-Agent", random.choice(USER_AGENTS))
    headers.setdefault("Accept-Language", "en-US,en;q=0.9,fr;q=0.8")

    start = time.perf_counter()
    try:
        response = await session.get(
            url,
            headers=headers,
            timeout=PLATFORM_TIMEOUT,
            follow_redirects=True,
        )
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "platform": platform["name"],
            "url": url,
            "category": platform["category"],
            "found": _evaluate_found(platform, response),
            "response_time_ms": elapsed_ms,
            "error": None,
        }
    except Exception as exc:  # timeout, DNS, TLS, redirect loop… → jamais propagé
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "platform": platform["name"],
            "url": url,
            "category": platform["category"],
            "found": False,
            "response_time_ms": elapsed_ms,
            "error": type(exc).__name__,
        }


async def check_all_platforms(
    username: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> AsyncGenerator[dict, None]:
    """Énumère le `username` sur toutes les plateformes et **yield au fil de l'eau**.

    - Concurrence bornée par un `Semaphore` (défaut 20).
    - Timeout individuel (8s) par plateforme + **timeout global** (30s) : au-delà, les
      requêtes encore en vol sont annulées et l'énumération s'arrête proprement.
    - Chaque résultat est renvoyé dès qu'il est disponible (pas d'attente globale),
      ce qui alimente le stream SSE / la grille live.
    """
    username = normalize_username(username)
    semaphore = asyncio.Semaphore(max_concurrent)

    async with httpx.AsyncClient(follow_redirects=True) as session:

        async def _bounded(platform: dict) -> dict:
            async with semaphore:
                return await check_username(username, platform, session)

        tasks = [asyncio.create_task(_bounded(p)) for p in PLATFORMS]
        try:
            for future in asyncio.as_completed(tasks, timeout=GLOBAL_TIMEOUT):
                try:
                    yield await future
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[username] Timeout global ({GLOBAL_TIMEOUT}s) atteint pour "
                        f"'{username}' — arrêt des vérifications restantes"
                    )
                    break
        finally:
            # Annule proprement les tâches encore en cours (timeout global ou client SSE parti)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
