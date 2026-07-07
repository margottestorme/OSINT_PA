"""Énumération de pseudos multi-plateformes (Module 15, type Sherlock/Maigret).

À partir d'un username, teste sa présence sur 120+ plateformes publiques via de simples
requêtes HTTP, en parallèle borné (semaphore) et avec un User-Agent rotatif. Le registre
`PLATFORMS` est **déclaratif** : ajouter une plateforme = ajouter un dict (aucun `if/elif`).

Trois méthodes de détection par plateforme :
- `status_code`   : 200 → compte trouvé, 404 (ou 4xx) → pas trouvé.
- `not_found_text`: la page renvoie 200 même si le compte n'existe pas (soft-404) ; on
  cherche alors un texte caractéristique de l'ABSENCE (`not_found_text`) dans le corps —
  marqueur présent = pas trouvé.
- `found_text`    : symétrique — certains sites renvoient un shell générique 200 sans texte
  d'absence stable, mais le profil réel contient un marqueur de PRÉSENCE fiable
  (`found_text`) — marqueur présent = trouvé (ex : Bluesky).

Résilience totale : toute erreur individuelle (timeout, DNS, TLS…) est capturée et renvoyée
dans le champ `error` — une plateforme qui échoue n'interrompt jamais l'énumération.

**Fiabilité connue (audit 2026-07-07, 2 vagues)** : plus du tiers du registre initial (basé sur
`status_code` seul) s'est révélé donner de FAUX POSITIFS. Deux causes distinctes identifiées :
1. Un pseudo aléatoire garanti inexistant était déclaré "trouvé" tout court — sites modernes qui
   renvoient un 200 générique (SPA rendue côté client, ou mur anti-bot Cloudflare) quel que soit
   le compte (ex : Instagram, YouTube, TikTok, Twitch — contenu chargé par API après coup ;
   eBay, CodePen, Discord, Signal — mur anti-bot intermittent ou URL non pertinente pour un
   pseudo).
2. Plus insidieux : certaines plateformes (Facebook, Twitter/X, Trakt, ENS, Root-Me, Spotify,
   Yahoo Answers, WordPress...) réussissent le test avec un pseudo aléatoire "propre", mais
   échouent dès qu'une variante générée contient un point/underscore/tiret que la plateforme
   n'accepte pas comme caractère de pseudo valide (ex : Twitter/X, Trakt, ENS n'acceptent pas le
   point) : le routeur serveur bascule alors sur un shell SPA générique (200) au lieu d'un vrai
   404, et le status_code seul s'y trompe. Détecté en testant des pseudos bidons AVEC séparateurs.
Aucune de ces plateformes n'est réparable simplement (pas de marqueur texte fiable observable
côté serveur) — retirées du registre actif plutôt que laissées à mentir. Les réparer demanderait
une API officielle (avec clé) ou un navigateur headless (Playwright) pour exécuter le JS — hors
périmètre d'un simple registre HTTP. Il est probable que d'autres plateformes du registre actif
partagent encore ce défaut sur des variantes non testées — voir roadmap.
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from typing import AsyncGenerator

import httpx
from loguru import logger

# Timeout par plateforme (s) et timeout global de l'énumération (s)
PLATFORM_TIMEOUT = 8.0
# Le set élargi de variantes (§ generate_username_variants) multiplie le nombre de requêtes
# par plateforme, et _check_platform_variants n'a plus d'arrêt anticipé (pour ne rater aucun
# compte trouvé sur une variante) : la concurrence reste bornée par le même semaphore, mais
# le temps total augmente nettement — le timeout global est relevé en conséquence.
GLOBAL_TIMEOUT = 180.0
DEFAULT_MAX_CONCURRENT = 20
MAX_VARIANTS = 25  # borne haute, évite un nombre de requêtes incontrôlé sur un pseudo pathologique

# Format attendu pour un pseudo (identique à la validation de l'API) — filtre les variantes
# générées qui ne seraient pas exploitables en URL (ex : variante vide ou trop longue).
_VARIANT_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")

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
    {"name": "Hashnode", "url": "https://hashnode.com/@{username}", "category": "dev", "check_method": "status_code", "reliable": False},
    {"name": "ProductHunt", "url": "https://www.producthunt.com/@{username}", "category": "dev", "check_method": "status_code"},
    {"name": "Keybase", "url": "https://keybase.io/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "GitBook", "url": "https://{username}.gitbook.io/", "category": "dev", "check_method": "status_code"},
    {"name": "CodePen", "url": "https://codepen.io/{username}", "category": "dev", "check_method": "status_code", "reliable": False},
    {"name": "JSFiddle", "url": "https://jsfiddle.net/user/{username}/", "category": "dev", "check_method": "status_code"},
    {"name": "Replit", "url": "https://replit.com/@{username}", "category": "dev", "check_method": "status_code", "reliable": False},
    {"name": "Kaggle", "url": "https://www.kaggle.com/{username}", "category": "dev", "check_method": "status_code", "reliable": False},
    {"name": "HuggingFace", "url": "https://huggingface.co/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "DockerHub", "url": "https://hub.docker.com/u/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "NPM", "url": "https://www.npmjs.com/~{username}", "category": "dev", "check_method": "status_code"},
    {"name": "PyPI", "url": "https://pypi.org/user/{username}/", "category": "dev", "check_method": "status_code", "reliable": False},
    # Faux positif intermittent détecté par scripts/audit_username_platforms.py (2 exécutions
    # sur 7, ~29%) : comportement anti-bot sporadique, pas un bug de charset déterministe
    # (contrairement à Telegram/Twitter) — pas de marqueur texte fiable à trouver.
    {"name": "RubyGems", "url": "https://rubygems.org/profiles/{username}", "category": "dev", "check_method": "status_code", "reliable": False},
    {"name": "Crates.io", "url": "https://crates.io/users/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "LeetCode", "url": "https://leetcode.com/{username}/", "category": "dev", "check_method": "status_code"},
    {"name": "HackerRank", "url": "https://www.hackerrank.com/{username}", "category": "dev", "check_method": "status_code", "reliable": False},
    {"name": "Codewars", "url": "https://www.codewars.com/users/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "Exercism", "url": "https://exercism.org/profiles/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "TryHackMe", "url": "https://tryhackme.com/p/{username}", "category": "dev", "check_method": "status_code"},
    {"name": "HackTheBox", "url": "https://app.hackthebox.com/users/{username}", "category": "dev", "check_method": "status_code", "reliable": False},
    {"name": "Root-Me", "url": "https://www.root-me.org/{username}", "category": "dev", "check_method": "status_code", "reliable": False},
    {"name": "FreeCodeCamp", "url": "https://www.freecodecamp.org/{username}", "category": "dev", "check_method": "status_code"},

    # --- social ---
    # Page web = SPA sans signal fiable (cf. audit initial). Ré-investigué le 2026-07-07 :
    # l'API officielle v2 (`GET /2/users/by/username/:username`) n'a plus de palier gratuit
    # depuis février 2026 (passage au pay-per-use, ~0,01 $/appel lié à un utilisateur) — donc
    # aucune solution gratuite/légale exploitable sans facturation récurrente. Reste désactivé.
    {"name": "Twitter/X", "url": "https://x.com/{username}", "category": "social", "check_method": "status_code", "reliable": False},
    # Page web = SPA (aucun signal fiable, cf. audit 2026-07-07). L'API interne
    # `web_profile_info` (utilisée par le client web d'Instagram lui-même) répond en JSON
    # avec un vrai 200/404 selon l'existence du compte — X-IG-App-ID est une constante
    # publique du client web, pas un secret. `url` reste la page de profil (lien cliquable).
    {"name": "Instagram", "url": "https://www.instagram.com/{username}/",
     "check_url": "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
     "category": "social", "check_method": "status_code",
     "headers": {"X-IG-App-ID": "936619743392459", "Accept": "application/json"}},
    # Ré-investigué le 2026-07-07 : le lookup par username a été retiré du Graph API dès la
    # v2.0 (2015) — seul `/search?type=user` existe encore, et il exige un access token d'app
    # avec revue Meta (usage professionnel justifié), donc inexploitable pour ce projet. Aucune
    # alternative officielle gratuite ; reste désactivé.
    {"name": "Facebook", "url": "https://www.facebook.com/{username}", "category": "social", "check_method": "status_code", "reliable": False},
    # status_code seul est inutilisable : Reddit sert une page anti-bot ("Please wait for
    # verification") à toute requête automatisée, avec un code 200 identique que le pseudo
    # existe ou non — ce qui déclarait systématiquement "trouvé" quel que soit le pseudo.
    {"name": "Reddit", "url": "https://www.reddit.com/user/{username}", "category": "social",
     "check_method": "not_found_text", "not_found_text": "Please wait for verification"},
    # Ré-investigué le 2026-07-07 : les endpoints officiels (`/v2/user/info/`, Research API)
    # exigent tous un bearer token obtenu via consentement OAuth de l'utilisateur ciblé
    # lui-même — inutilisable pour vérifier un pseudo arbitraire. Reste désactivé.
    {"name": "TikTok", "url": "https://www.tiktok.com/@{username}", "category": "social", "check_method": "status_code", "reliable": False},
    # SPA : shell 200 générique quel que soit le compte, MAIS le payload JSON embarqué côté
    # serveur contient un message d'erreur exploitable pour les comptes inexistants.
    {"name": "Pinterest", "url": "https://www.pinterest.com/{username}/", "category": "social",
     "check_method": "not_found_text", "not_found_text": "User not found."},
    {"name": "Tumblr", "url": "https://{username}.tumblr.com/", "category": "social", "check_method": "status_code"},
    {"name": "Mastodon.social", "url": "https://mastodon.social/@{username}", "category": "social", "check_method": "status_code"},
    # SPA : shell 200 générique pour un pseudo inexistant, sans texte d'absence stable ; en
    # revanche le profil réel contient un marqueur de PRÉSENCE fiable (found_text).
    {"name": "Bluesky", "url": "https://bsky.app/profile/{username}.bsky.social", "category": "social",
     "check_method": "found_text", "found_text": "bsky_profile_summary"},
    {"name": "Threads", "url": "https://www.threads.net/@{username}", "category": "social", "check_method": "status_code", "reliable": False},
    {"name": "VK", "url": "https://vk.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "OK.ru", "url": "https://ok.ru/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Snapchat", "url": "https://www.snapchat.com/add/{username}", "category": "social", "check_method": "status_code"},
    {"name": "BeReal", "url": "https://bere.al/{username}", "category": "social", "check_method": "status_code", "reliable": False},
    {"name": "Vero", "url": "https://vero.co/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Ello", "url": "https://ello.co/{username}", "category": "social", "check_method": "status_code", "reliable": False},
    {"name": "Minds", "url": "https://www.minds.com/{username}/", "category": "social", "check_method": "status_code", "reliable": False},
    {"name": "Gab", "url": "https://gab.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "CounterSocial", "url": "https://counter.social/@{username}", "category": "social", "check_method": "status_code", "reliable": False},
    # L'ancien marqueur ("tgme_page_extra") ne comptait que les abonnés d'une CHAÎNE — absent
    # sur les comptes personnels/bots pourtant réels, d'où des faux négatifs. Un marqueur
    # d'ABSENCE ("tgme_icon_user") semblait fonctionner mais restait fragile : les usernames
    # Telegram n'acceptent que lettres/chiffres/underscore (pas de point ni de tiret) — une
    # variante générée avec un point (ex: "margotte.storme") atterrit sur la page d'accueil
    # générique de Telegram, qui n'a NI l'un NI l'autre marqueur, et se lit alors à tort comme
    # "trouvé". `found_text` exige une preuve positive (photo de profil, présente pour tout
    # compte réel, y compris les bots) — robuste aussi face à un charset de pseudo invalide.
    {"name": "Telegram", "url": "https://t.me/{username}", "category": "social",
     "check_method": "found_text", "found_text": "tgme_page_photo"},
    {"name": "Signal", "url": "https://signal.me/#p/{username}", "category": "social", "check_method": "status_code", "reliable": False},
    {"name": "WhatsApp Business", "url": "https://wa.me/{username}", "category": "social", "check_method": "status_code"},
    {"name": "AboutMe", "url": "https://about.me/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Linktree", "url": "https://linktr.ee/{username}", "category": "social", "check_method": "status_code"},
    {"name": "AllMyLinks", "url": "https://allmylinks.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Carrd", "url": "https://{username}.carrd.co/", "category": "social", "check_method": "status_code"},
    {"name": "Gravatar", "url": "https://gravatar.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Strava", "url": "https://www.strava.com/athletes/{username}", "category": "social", "check_method": "status_code", "reliable": False},
    {"name": "Komoot", "url": "https://www.komoot.com/user/{username}", "category": "social", "check_method": "status_code"},
    {"name": "Garmin Connect", "url": "https://connect.garmin.com/modern/profile/{username}", "category": "social", "check_method": "status_code", "reliable": False},
    {"name": "Yelp", "url": "https://www.yelp.com/user_details?userid={username}", "category": "social", "check_method": "status_code", "reliable": False},
    {"name": "Foursquare", "url": "https://foursquare.com/{username}", "category": "social", "check_method": "status_code"},
    {"name": "TripAdvisor", "url": "https://www.tripadvisor.com/Profile/{username}", "category": "social", "check_method": "status_code"},

    # --- forum ---
    {"name": "HackerNews", "url": "https://news.ycombinator.com/user?id={username}", "category": "forum",
     "check_method": "not_found_text", "not_found_text": "No such user."},
    {"name": "Quora", "url": "https://www.quora.com/profile/{username}", "category": "forum", "check_method": "status_code"},
    {"name": "Yahoo Answers", "url": "https://answers.yahoo.com/activity/questions?show={username}", "category": "forum", "check_method": "status_code", "reliable": False},
    {"name": "Fandom", "url": "https://community.fandom.com/wiki/User:{username}", "category": "forum", "check_method": "status_code"},
    {"name": "Amino", "url": "https://aminoapps.com/u/{username}", "category": "forum", "check_method": "status_code"},
    {"name": "Disqus", "url": "https://disqus.com/by/{username}/", "category": "forum", "check_method": "status_code"},
    {"name": "Wikipedia (en)", "url": "https://en.wikipedia.org/wiki/User:{username}", "category": "forum", "check_method": "status_code"},
    {"name": "Wikipedia (fr)", "url": "https://fr.wikipedia.org/wiki/Utilisateur:{username}", "category": "forum", "check_method": "status_code"},

    # --- gaming ---
    {"name": "Steam", "url": "https://steamcommunity.com/id/{username}", "category": "gaming",
     "check_method": "not_found_text", "not_found_text": "The specified profile could not be found."},
    {"name": "Twitch", "url": "https://www.twitch.tv/{username}", "category": "gaming", "check_method": "status_code", "reliable": False},
    # Ré-investigué le 2026-07-07 : l'API Discord ne propose aucun endpoint de recherche par
    # username sur l'espace de noms global — seul un lookup par ID (snowflake) déjà connu est
    # possible, via un bot déjà en contact avec l'utilisateur (serveur commun). Impossible de
    # vérifier un pseudo arbitraire sans cette relation préalable. Reste désactivé.
    {"name": "Discord", "url": "https://discord.com/users/{username}", "category": "gaming", "check_method": "status_code", "reliable": False},
    {"name": "Xbox Gamertag", "url": "https://xboxgamertag.com/search/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "PSN Profiles", "url": "https://psnprofiles.com/{username}", "category": "gaming", "check_method": "status_code", "reliable": False},
    {"name": "Chess.com", "url": "https://www.chess.com/member/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Lichess", "url": "https://lichess.org/@/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Roblox", "url": "https://www.roblox.com/user.aspx?username={username}", "category": "gaming", "check_method": "status_code"},
    # Faux positif détecté (audit) + mur anti-bot confirmé qui bloque même les requêtes
    # légitimes (403 sur un pseudo réel) : signal totalement inutilisable en l'état.
    {"name": "NameMC (Minecraft)", "url": "https://namemc.com/profile/{username}", "category": "gaming", "check_method": "status_code", "reliable": False},
    {"name": "osu!", "url": "https://osu.ppy.sh/users/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Speedrun.com", "url": "https://www.speedrun.com/user/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "GameJolt", "url": "https://gamejolt.com/@{username}", "category": "gaming", "check_method": "status_code", "reliable": False},
    {"name": "Itch.io", "url": "https://{username}.itch.io/", "category": "gaming", "check_method": "status_code"},
    {"name": "Newgrounds", "url": "https://{username}.newgrounds.com/", "category": "gaming", "check_method": "status_code"},
    {"name": "Trovo", "url": "https://trovo.live/s/{username}", "category": "gaming", "check_method": "status_code"},
    {"name": "Kick", "url": "https://kick.com/{username}", "category": "gaming", "check_method": "status_code", "reliable": False},
    {"name": "Duolingo", "url": "https://www.duolingo.com/profile/{username}", "category": "gaming", "check_method": "status_code", "reliable": False},

    # --- creative ---
    {"name": "YouTube", "url": "https://www.youtube.com/@{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "Vimeo", "url": "https://vimeo.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Dailymotion", "url": "https://www.dailymotion.com/{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "Spotify", "url": "https://open.spotify.com/user/{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "SoundCloud", "url": "https://soundcloud.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Bandcamp", "url": "https://{username}.bandcamp.com/", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "Last.fm", "url": "https://www.last.fm/user/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Behance", "url": "https://www.behance.net/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Dribbble", "url": "https://dribbble.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Flickr", "url": "https://www.flickr.com/people/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "500px", "url": "https://500px.com/p/{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "DeviantArt", "url": "https://www.deviantart.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "ArtStation", "url": "https://www.artstation.com/{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "VSCO", "url": "https://vsco.co/{username}/gallery", "category": "creative", "check_method": "status_code"},
    {"name": "Unsplash", "url": "https://unsplash.com/@{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Pexels", "url": "https://www.pexels.com/@{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Imgur", "url": "https://imgur.com/user/{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "Giphy", "url": "https://giphy.com/{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "Medium", "url": "https://medium.com/@{username}", "category": "creative",
     "check_method": "not_found_text", "not_found_text": "PAGE NOT FOUND"},
    {"name": "Substack", "url": "https://{username}.substack.com/", "category": "creative", "check_method": "status_code"},
    {"name": "Blogger", "url": "https://{username}.blogspot.com/", "category": "creative", "check_method": "status_code"},
    {"name": "WordPress", "url": "https://{username}.wordpress.com/", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "Wattpad", "url": "https://www.wattpad.com/user/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "FanFiction.net", "url": "https://www.fanfiction.net/~{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Archive of Our Own", "url": "https://archiveofourown.org/users/{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "SlideShare", "url": "https://www.slideshare.net/{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "Scribd", "url": "https://www.scribd.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Rumble", "url": "https://rumble.com/user/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "Odysee", "url": "https://odysee.com/@{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "BitChute", "url": "https://www.bitchute.com/channel/{username}/", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "Goodreads", "url": "https://www.goodreads.com/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "LibraryThing", "url": "https://www.librarything.com/profile/{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "Letterboxd", "url": "https://letterboxd.com/{username}/", "category": "creative", "check_method": "status_code"},
    {"name": "Trakt", "url": "https://trakt.tv/users/{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "IMDb", "url": "https://www.imdb.com/user/{username}/", "category": "creative", "check_method": "status_code"},
    {"name": "MyAnimeList", "url": "https://myanimelist.net/profile/{username}", "category": "creative", "check_method": "status_code"},
    {"name": "AniList", "url": "https://anilist.co/user/{username}/", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "Kitsu", "url": "https://kitsu.io/users/{username}", "category": "creative", "check_method": "status_code", "reliable": False},
    {"name": "Untappd", "url": "https://untappd.com/user/{username}", "category": "creative", "check_method": "status_code"},

    # --- professional ---
    {"name": "LinkedIn", "url": "https://www.linkedin.com/in/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Fiverr", "url": "https://www.fiverr.com/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Freelancer", "url": "https://www.freelancer.com/u/{username}", "category": "professional", "check_method": "status_code", "reliable": False},
    {"name": "Upwork", "url": "https://www.upwork.com/freelancers/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "AngelList", "url": "https://angel.co/u/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Crunchbase", "url": "https://www.crunchbase.com/person/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Notion", "url": "https://www.notion.so/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Patreon", "url": "https://www.patreon.com/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Ko-fi", "url": "https://ko-fi.com/{username}", "category": "professional", "check_method": "status_code", "reliable": False},
    {"name": "Buy Me a Coffee", "url": "https://www.buymeacoffee.com/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Gumroad", "url": "https://{username}.gumroad.com/", "category": "professional", "check_method": "status_code"},
    {"name": "Etsy", "url": "https://www.etsy.com/shop/{username}", "category": "professional", "check_method": "status_code"},
    # Mur anti-bot intermittent (timeout/challenge selon le rythme des requêtes) : le
    # marqueur ci-dessous a été confirmé cassé (faux positif) lors de l'audit du 2026-07-07.
    {"name": "eBay", "url": "https://www.ebay.com/usr/{username}", "category": "professional",
     "check_method": "not_found_text", "not_found_text": "The User ID you entered was not found",
     "reliable": False},
    {"name": "Amazon Author", "url": "https://www.amazon.com/author/{username}", "category": "professional", "check_method": "status_code"},
    {"name": "Coursera", "url": "https://www.coursera.org/user/{username}", "category": "professional", "check_method": "status_code", "reliable": False},
    {"name": "Udemy", "url": "https://www.udemy.com/user/{username}/", "category": "professional", "check_method": "status_code", "reliable": False},

    # --- dating ---
    {"name": "Tinder", "url": "https://tinder.com/@{username}", "category": "dating", "check_method": "status_code", "reliable": False},
    {"name": "OkCupid", "url": "https://www.okcupid.com/profile/{username}", "category": "dating", "check_method": "status_code", "reliable": False},
    {"name": "MeetMe", "url": "https://www.meetme.com/{username}", "category": "dating", "check_method": "status_code"},
    {"name": "Badoo", "url": "https://badoo.com/profile/{username}", "category": "dating", "check_method": "status_code", "reliable": False},

    # --- crypto ---
    {"name": "CoinMarketCap", "url": "https://coinmarketcap.com/community/profile/{username}/", "category": "crypto", "check_method": "status_code", "reliable": False},
    {"name": "ENS (Etherscan)", "url": "https://app.ens.domains/{username}", "category": "crypto", "check_method": "status_code", "reliable": False},
    {"name": "OpenSea", "url": "https://opensea.io/{username}", "category": "crypto", "check_method": "status_code"},
    {"name": "Rarible", "url": "https://rarible.com/{username}", "category": "crypto", "check_method": "status_code", "reliable": False},
    {"name": "Mirror.xyz", "url": "https://mirror.xyz/{username}", "category": "crypto", "check_method": "status_code", "reliable": False},
    {"name": "Gitcoin", "url": "https://gitcoin.co/{username}", "category": "crypto", "check_method": "status_code"},

    # --- news ---
    {"name": "HackerNoon", "url": "https://hackernoon.com/u/{username}", "category": "news", "check_method": "status_code", "reliable": False},
    {"name": "Steemit", "url": "https://steemit.com/@{username}", "category": "news", "check_method": "status_code"},
    {"name": "Hive", "url": "https://hive.blog/@{username}", "category": "news", "check_method": "status_code"},
    {"name": "Vocal", "url": "https://vocal.media/authors/{username}", "category": "news", "check_method": "status_code"},
]

# Plateformes confirmées non fiables (audit du 2026-07-07 : faux positif sur un pseudo garanti
# inexistant, ou mur anti-bot intermittent) — conservées ci-dessus pour ne pas perdre le travail
# de catégorisation/URL en vue d'une réparation future (API officielle ou navigateur headless),
# mais exclues du registre actif tant qu'aucun marqueur fiable n'est trouvé.
ALL_PLATFORM_DEFINITIONS = PLATFORMS
PLATFORMS: list[dict] = [p for p in ALL_PLATFORM_DEFINITIONS if p.get("reliable", True)]

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


def generate_username_variants(username: str) -> list[str]:
    """Génère les variantes plausibles d'un pseudo « prenom.nom » (set élargi).

    Un utilisateur cherche rarement le pseudo exact d'une personne — testons donc les
    formats les plus courants qu'elle a pu choisir sur telle ou telle plateforme :
    séparateurs (point/underscore/tiret/aucun), ordre inversé (nom.prenom), initiales,
    casse capitalisée et suffixes numériques usuels (année de naissance, `1`, `123`...).

    L'original saisi est toujours renvoyé en premier (priorité d'affichage). Les pseudos
    à un seul « mot » (pas de séparateur détecté) ne reçoivent que des suffixes numériques,
    les autres transformations n'ayant pas de sens sans prenom/nom distincts.
    """
    base = normalize_username(username)
    if not base:
        return []

    variants: list[str] = [base]
    seen = {base.lower()}

    def add(candidate: str) -> None:
        if not candidate or candidate.lower() in seen:
            return
        if not _VARIANT_RE.match(candidate):
            return
        seen.add(candidate.lower())
        variants.append(candidate)

    parts = [p for p in re.split(r"[._-]", base) if p]
    numeric_suffixes = ("1", "11", "123", "2024")

    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        no_sep = "".join(parts)

        # Séparateurs alternatifs
        add(no_sep)
        add("_".join(parts))
        add("-".join(parts))

        # Ordre inversé (nom.prenom)
        add(f"{last}.{first}")
        add(f"{last}_{first}")
        add(f"{last}{first}")

        # Initiales
        add(f"{first[0]}.{last}")
        add(f"{first}.{last[0]}")
        add(f"{first[0]}{last}")

        # Casse capitalisée
        add(f"{first.capitalize()}.{last.capitalize()}")
        add(no_sep.capitalize())

        # Suffixes numériques (sur l'original et sur la forme sans séparateur)
        for suffix in numeric_suffixes:
            add(f"{base}{suffix}")
            add(f"{no_sep}{suffix}")
    else:
        # Pseudo à un seul mot (aucun séparateur détecté) : on ne connaît pas le point de
        # coupure prénom/nom, donc on tente d'insérer un séparateur à des positions
        # plausibles. Ex : "somargotte" ne retrouverait jamais un compte "so_margotte" sans
        # ça — "so" est un préfixe court typique (diminutif/initiales du prénom).
        # Préfixe court en tête (initiale(s) de prénom + reste = nom) : jusqu'à 4 caractères.
        separators = (".", "_", "-")
        prefix_len = min(4, len(base) - 1)
        for i in range(1, prefix_len + 1):
            for sep in separators:
                add(f"{base[:i]}{sep}{base[i:]}")
        # Suffixe court en fin (prénom + initiale(s) de nom) : jusqu'à 2 caractères, en
        # évitant les positions déjà couvertes par la boucle préfixe ci-dessus.
        suffix_len = min(2, len(base) - 1)
        for i in range(1, suffix_len + 1):
            split = len(base) - i
            if split <= prefix_len:
                continue
            for sep in separators:
                add(f"{base[:split]}{sep}{base[split:]}")
        for suffix in numeric_suffixes:
            add(f"{base}{suffix}")

    return variants[:MAX_VARIANTS]


def _build_url(platform: dict, username: str) -> str:
    return platform["url"].format(username=username)


def _build_check_url(platform: dict, username: str) -> str:
    """URL réellement interrogée pour la vérification.

    Diffère de `url` (toujours la page de profil humaine, affichée/cliquable) quand la
    plateforme expose une API de vérification distincte — ex : Instagram, dont la page web
    est une SPA (aucun signal fiable), mais dont l'API interne `web_profile_info` répond en
    JSON avec un vrai 404/200 selon l'existence du compte.
    """
    return platform.get("check_url", platform["url"]).format(username=username)


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
    if method == "found_text":
        # Symétrique de not_found_text : certains sites renvoient un shell générique 200
        # pour un pseudo inexistant, sans texte d'absence stable — mais le profil réel,
        # lui, contient un marqueur de PRÉSENCE fiable (ex : Bluesky).
        if response.status_code >= 400:
            return False
        marker = platform.get("found_text")
        return bool(marker) and marker in response.text
    # status_code : 200 = trouvé, tout le reste = pas trouvé.
    return response.status_code == 200


async def check_username(
    username: str,
    platform: dict,
    session: httpx.AsyncClient,
) -> dict:
    """Teste la présence d'un `username` sur une plateforme donnée.

    Ne lève jamais : toute exception est capturée et exposée dans `error` (found=False).
    Retour : {platform, url, category, found, response_time_ms, error}. `url` est toujours la
    page de profil humaine (cliquable), même quand la vérification interroge un autre
    endpoint (`check_url` — voir `_build_check_url`).
    """
    url = _build_url(platform, username)
    check_url = _build_check_url(platform, username)
    headers = dict(platform.get("headers") or {})
    headers.setdefault("User-Agent", random.choice(USER_AGENTS))
    headers.setdefault("Accept-Language", "en-US,en;q=0.9,fr;q=0.8")

    start = time.perf_counter()
    try:
        response = await session.get(
            check_url,
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


# --------------------------------------------------------------------------- #
# Sources à API officielle (clé optionnelle) — YouTube, Twitch                 #
# --------------------------------------------------------------------------- #
# Contrairement au registre PLATFORMS (une simple requête GET + status_code/marqueur texte),
# ces deux plateformes n'ont pas de signal fiable en scrapant leur page web (SPA côté client,
# cf. audit 2026-07-07) : leur API officielle est utilisée à la place. Même principe que les
# sources OSINT freemium (VirusTotal, Hunter.io — app/osint/engine.py) : désactivées sans clé,
# aucune requête réseau, aucune erreur. Injectées dynamiquement dans check_all_platforms() via
# `_build_dynamic_platforms()`, jamais dans le registre statique PLATFORMS (dépendent de
# `get_settings()`, réévaluées à chaque appel plutôt que figées à l'import du module).

async def _check_youtube(username: str, platform: dict, session: httpx.AsyncClient) -> dict:
    """Vérifie l'existence d'une chaîne YouTube via l'API officielle Data API v3.

    `forHandle` interroge directement le @handle (identique à l'URL publique) ; `items` est
    vide si la chaîne n'existe pas. Nécessite `YOUTUBE_API_KEY` (.env, gratuite) — voir
    `_build_dynamic_platforms()` pour la condition d'activation.
    """
    from app.core.config import get_settings

    display_url = f"https://www.youtube.com/@{username}"
    start = time.perf_counter()
    try:
        response = await session.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "id", "forHandle": username, "key": get_settings().YOUTUBE_API_KEY},
            timeout=PLATFORM_TIMEOUT,
        )
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        found = response.status_code == 200 and bool(response.json().get("items"))
        return {"platform": "YouTube", "url": display_url, "category": "creative",
                "found": found, "response_time_ms": elapsed_ms, "error": None}
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        return {"platform": "YouTube", "url": display_url, "category": "creative",
                "found": False, "response_time_ms": elapsed_ms, "error": type(exc).__name__}


# Cache du token d'application Twitch (grant_type=client_credentials, valide ~60 jours) —
# évite de renégocier un token à chaque vérification (limite de taux sur l'endpoint OAuth).
_twitch_token_cache: dict = {"token": None, "expires_at": 0.0}
_twitch_token_lock = asyncio.Lock()


async def _get_twitch_app_token(session: httpx.AsyncClient, client_id: str, client_secret: str) -> str | None:
    """Récupère (et met en cache) un token d'application Twitch. `None` si indisponible."""
    if _twitch_token_cache["token"] and time.time() < _twitch_token_cache["expires_at"] - 60:
        return _twitch_token_cache["token"]
    async with _twitch_token_lock:
        if _twitch_token_cache["token"] and time.time() < _twitch_token_cache["expires_at"] - 60:
            return _twitch_token_cache["token"]
        try:
            response = await session.post(
                "https://id.twitch.tv/oauth2/token",
                params={"client_id": client_id, "client_secret": client_secret,
                        "grant_type": "client_credentials"},
                timeout=PLATFORM_TIMEOUT,
            )
            data = response.json()
            _twitch_token_cache["token"] = data["access_token"]
            _twitch_token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
            return _twitch_token_cache["token"]
        except Exception as exc:
            logger.warning(f"[username] Échec récupération token Twitch : {exc}")
            return None


async def _check_twitch(username: str, platform: dict, session: httpx.AsyncClient) -> dict:
    """Vérifie l'existence d'un compte Twitch via l'API officielle Helix.

    Nécessite `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET` (.env, gratuits) — voir
    `_build_dynamic_platforms()` pour la condition d'activation.
    """
    from app.core.config import get_settings

    settings = get_settings()
    display_url = f"https://www.twitch.tv/{username}"
    start = time.perf_counter()
    try:
        token = await _get_twitch_app_token(session, settings.TWITCH_CLIENT_ID, settings.TWITCH_CLIENT_SECRET)
        if not token:
            raise RuntimeError("token Twitch indisponible")
        response = await session.get(
            "https://api.twitch.tv/helix/users",
            params={"login": username},
            headers={"Client-Id": settings.TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"},
            timeout=PLATFORM_TIMEOUT,
        )
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        found = response.status_code == 200 and bool(response.json().get("data"))
        return {"platform": "Twitch", "url": display_url, "category": "gaming",
                "found": found, "response_time_ms": elapsed_ms, "error": None}
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        return {"platform": "Twitch", "url": display_url, "category": "gaming",
                "found": False, "response_time_ms": elapsed_ms, "error": type(exc).__name__}


def _build_dynamic_platforms() -> list[dict]:
    """Plateformes à API officielle, ajoutées uniquement si leur clé est configurée.

    Réévalué à chaque appel de `check_all_platforms()` (pas figé à l'import) : activer une
    clé dans `.env` prend effet immédiatement, sans redémarrage à froid du registre.
    """
    from app.core.config import get_settings

    settings = get_settings()
    dynamic: list[dict] = []
    if settings.YOUTUBE_API_KEY:
        dynamic.append({"name": "YouTube", "url": "https://www.youtube.com/@{username}",
                         "category": "creative", "checker": _check_youtube})
    if settings.TWITCH_CLIENT_ID and settings.TWITCH_CLIENT_SECRET:
        dynamic.append({"name": "Twitch", "url": "https://www.twitch.tv/{username}",
                         "category": "gaming", "checker": _check_twitch})
    return dynamic


async def _check_platform_variants(
    variants: list[str],
    platform: dict,
    session: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """Teste **toutes** les variantes d'un pseudo sur une seule plateforme.

    Pas d'arrêt anticipé : plusieurs comptes réels distincts peuvent exister sous des
    variantes différentes (ex : `margottestorme` ET `mstorme` tous les deux sur GitHub) —
    s'arrêter à la première trouvée en ferait disparaître d'autres. On attend donc que
    toutes les variantes aient répondu.

    Renvoie une **liste** :
    - une entrée par variante trouvée (triée dans l'ordre de `variants`, donc la plus
      « probable » en premier), chacune avec son propre `url`/`variant` ;
    - à défaut, une seule entrée « non trouvé » sur la variante originale (`variants[0]`).

    Le premier élément porte `is_primary=True` : l'appelant s'en sert pour compter les
    plateformes traitées (157) sans compter deux fois les comptes bonus trouvés en plus.
    """
    check_fn = platform.get("checker", check_username)

    async def _bounded(v: str) -> tuple[str, dict]:
        async with semaphore:
            return v, await check_fn(v, platform, session)

    tasks = [asyncio.create_task(_bounded(v)) for v in variants]
    results = await asyncio.gather(*tasks)

    order = {v: i for i, v in enumerate(variants)}
    found = sorted((vr for vr in results if vr[1]["found"]), key=lambda vr: order[vr[0]])

    if found:
        return [
            dict(result, variant=variant, is_primary=(i == 0))
            for i, (variant, result) in enumerate(found)
        ]

    original = next((result for variant, result in results if variant == variants[0]), None)
    original = original or {
        "platform": platform["name"], "url": _build_url(platform, variants[0]),
        "category": platform["category"], "found": False,
        "response_time_ms": 0.0, "error": None,
    }
    return [dict(original, variant=variants[0], is_primary=True)]


async def check_all_platforms(
    username: str,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> AsyncGenerator[dict, None]:
    """Énumère le `username` (et ses variantes plausibles) sur toutes les plateformes,
    et **yield au fil de l'eau** un résultat par compte trouvé (ou un par plateforme si rien
    n'est trouvé dessus).

    - `generate_username_variants()` élargit la recherche (séparateurs, ordre inversé,
      initiales, casse, suffixes numériques) — chaque plateforme est testée sur **toutes**
      les variantes, sans arrêt anticipé, pour ne rater aucun compte réel qui existerait
      sous plusieurs variantes distinctes (voir `_check_platform_variants`).
    - Concurrence bornée par un `Semaphore` unique (défaut 20), partagé entre **toutes** les
      requêtes (plateformes × variantes) : le nombre de requêtes explose avec les variantes,
      mais jamais la concurrence réelle envoyée aux sites distants.
    - Timeout individuel (8s) par requête + **timeout global** (180s, généreux car aucune
      variante n'est plus court-circuitée) : au-delà, les requêtes encore en vol sont
      annulées et l'énumération s'arrête proprement.
    - Chaque événement porte `is_primary` : `True` pour le premier résultat d'une plateforme
      (qu'il soit trouvé ou non — sert à compter les 157 plateformes traitées côté
      consommateur), `False` pour un compte bonus trouvé en plus sur la même plateforme.
    """
    username = normalize_username(username)
    variants = generate_username_variants(username)
    semaphore = asyncio.Semaphore(max_concurrent)

    async with httpx.AsyncClient(follow_redirects=True) as session:

        async def _bounded(platform: dict) -> list[dict]:
            return await _check_platform_variants(variants, platform, session, semaphore)

        all_platforms = PLATFORMS + _build_dynamic_platforms()
        tasks = [asyncio.create_task(_bounded(p)) for p in all_platforms]
        try:
            for future in asyncio.as_completed(tasks, timeout=GLOBAL_TIMEOUT):
                try:
                    platform_results = await future
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[username] Timeout global ({GLOBAL_TIMEOUT}s) atteint pour "
                        f"'{username}' — arrêt des vérifications restantes"
                    )
                    break
                for result in platform_results:
                    yield result
        finally:
            # Annule proprement les tâches encore en cours (timeout global ou client SSE parti)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
