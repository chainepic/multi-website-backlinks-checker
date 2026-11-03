#!/usr/bin/env python3
"""Crawl the owned-site list and record links between those sites only."""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SITES_PATH = ROOT / "sites.json"
REPORT_PATH = ROOT / "data" / "report.json"

USER_AGENT = "ExchangeLinkChecker/1.0 (owned-site reciprocal check)"
TIMEOUT = 12
MAX_BYTES = 2_000_000
SITEMAP_BYTES = 700_000
MAX_SITEMAPS = 12
MAX_SITEMAP_URLS = 1500
STATIC_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".js",
    ".mjs", ".pdf", ".zip", ".gz", ".mp4", ".mp3", ".woff", ".woff2", ".ttf",
    ".xml", ".json", ".txt", ".rss", ".atom",
}
DENY_PREFIXES = ("/wp-admin", "/wp-login", "/admin", "/api/", "/cdn-cgi/", "/_next/static")
EXACT_PATH_SCORE = {
    "/about": 100,
    "/products": 120,
    "/catalog": 110,
    "/partners": 120,
    "/partner": 90,
    "/links": 120,
    "/friends": 120,
    "/blog": 50,
    "/zh": 30,
    "/en": 30,
    "/zh/products": 120,
    "/zh/about": 80,
    "/zh/blog": 40,
    "/en/about": 70,
    "/en/blog": 40,
    "/pages/about": 80,
}
EXTRA_PATHS = (
    "/about",
    "/products",
    "/catalog",
    "/blog",
    "/links",
    "/friends",
    "/partners",
    "/zh",
    "/en",
    "/zh/products",
    "/zh/about",
    "/zh/blog",
    "/en/about",
    "/en/blog",
    "/pages/about",
)
SLUG_PAGE_LIMIT = 40
CTX = ssl.create_default_context()


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    body: bytes
    content_type: str
    error: str | None

    def text(self) -> str:
        return decode_body(self.body, self.content_type)


def load_config(path: Path = SITES_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(config: dict, path: Path = SITES_PATH) -> None:
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def host_from_token(token: str) -> str | None:
    raw = (token or "").strip().strip("<>\"'")
    if not raw or any(ch.isspace() for ch in raw):
        return None
    candidate = raw if "://" in raw else "https://" + raw
    host = canonical_host(urlparse(candidate).hostname or "")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", host or ""):
        return None
    return host


def parse_host_list(text: str) -> tuple[list[str], list[str]]:
    hosts: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,，;；]+", text or ""):
        if not token.strip():
            continue
        host = host_from_token(token)
        if not host:
            invalid.append(token.strip())
            continue
        if host in seen:
            continue
        seen.add(host)
        hosts.append(host)
    return hosts, invalid


def apply_site_edits(config: dict, add_text: str = "", remove: list[str] | None = None) -> dict:
    incoming, invalid = parse_host_list(add_text)
    remove_set: set[str] = set()
    for item in remove or []:
        host = host_from_token(str(item))
        if host:
            remove_set.add(host)
    existing = {
        canonical_host(site["host"])
        for group in config.get("groups") or []
        for site in group.get("sites") or []
    }
    added = [host for host in incoming if host not in existing]
    skipped = [host for host in incoming if host in existing]
    removed: list[str] = []
    groups = []
    for group in config.get("groups") or []:
        sites = []
        for site in group.get("sites") or []:
            host = canonical_host(site["host"])
            if host in remove_set:
                removed.append(host)
                continue
            sites.append(site)
        if sites:
            groups.append({**group, "sites": sites})
    if added:
        target = next((group for group in groups if group["id"] == "added"), None)
        if target is None:
            target = {"id": "added", "label": "新加", "sites": []}
            groups.append(target)
        for host in added:
            target["sites"].append({"host": host})
    saved = {
        "max_pages_per_site": config.get("max_pages_per_site", 18),
        "groups": groups,
    }
    return {"config": saved, "added": added, "skipped": skipped, "invalid": invalid, "removed": removed}


def flatten_sites(config: dict) -> list[dict]:
    sites = []
    for group in config["groups"]:
        for site in group["sites"]:
            sites.append({
                "host": site["host"].strip().lower(),
                "group": group["id"],
                "group_label": group["label"],
                "note": site.get("note") or "",
            })
    return sites


def canonical_host(host: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def match_owned(host: str, owned: set[str]) -> str | None:
    host = canonical_host(host)
    if not host or host in {"localhost"}:
        return None
    if host in owned:
        return host
    parts = host.split(".")
    for i in range(1, len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in owned:
            return cand
    return None


def decode_body(raw: bytes, content_type: str) -> str:
    charset = None
    match = re.search(r"charset=([^\s;]+)", content_type or "", re.I)
    if match:
        charset = match.group(1).strip("\"'")
    if not charset:
        head = raw[:2500].decode("utf-8", "replace")
        meta = re.search(
            r"charset=[\"']?\s*([a-zA-Z0-9._-]+)",
            head,
            re.I,
        )
        if meta:
            charset = meta.group(1)
    for enc in (charset, "utf-8", "gb18030"):
        if not enc:
            continue
        try:
            return raw.decode(enc, "replace")
        except LookupError:
            continue
    return raw.decode("utf-8", "replace")


def html_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    return re.sub(r"\s+", " ", unescape(text)).strip()[:160]


def html_h1(html: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    return re.sub(r"\s+", " ", unescape(text)).strip()[:160]


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self._attrs: dict | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        data = {k.lower(): (v or "") for k, v in attrs}
        if data.get("href"):
            self._attrs = data
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._attrs is None:
            return
        text = re.sub(r"\s+", " ", "".join(self._text)).strip()[:160]
        self.links.append({
            "href": self._attrs.get("href", ""),
            "rel": self._attrs.get("rel", ""),
            "text": text,
        })
        self._attrs = None
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._attrs is not None:
            self._text.append(data)


def extract_anchors(html: str) -> list[dict]:
    parser = LinkExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return parser.links
    return parser.links


def rel_flags(rel: str) -> tuple[bool, bool]:
    tokens = {part.strip().lower() for part in re.split(r"\s+", rel or "") if part.strip()}
    nofollow = bool(tokens & {"nofollow", "sponsored", "ugc"})
    sponsored = "sponsored" in tokens
    return nofollow, sponsored


def fetch(url: str, retries: int = 0, max_bytes: int = MAX_BYTES) -> FetchResult:
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    try:
        with urlopen(req, timeout=TIMEOUT, context=CTX) as response:
            body = response.read(max_bytes)
            return FetchResult(
                url=url,
                final_url=response.geturl(),
                status=getattr(response, "status", 200),
                body=body,
                content_type=response.headers.get("Content-Type", ""),
                error=None,
            )
    except HTTPError as exc:
        body = b""
        try:
            body = exc.read(min(max_bytes, 120_000))
        except Exception:
            body = b""
        return FetchResult(
            url=url,
            final_url=getattr(exc, "url", url) or url,
            status=exc.code,
            body=body,
            content_type=exc.headers.get("Content-Type", "") if exc.headers else "",
            error=f"HTTP {exc.code}",
        )
    except Exception as exc:
        if retries > 0 and "timed out" in str(exc).lower():
            return fetch(url, retries=retries - 1, max_bytes=max_bytes)
        return FetchResult(url, url, 0, b"", "", friendly_fetch_error(exc))


def friendly_fetch_error(exc: Exception) -> str:
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
        return "连接超时"
    if isinstance(exc, URLError) and "timed out" in str(exc).lower():
        return "连接超时"
    text = str(exc)
    if "CERTIFICATE" in text.upper() or "SSL" in text.upper():
        return "证书校验失败"
    name = type(exc).__name__
    detail = text.strip() or name
    return detail[:180]


def looks_like_html(result: FetchResult) -> bool:
    ctype = (result.content_type or "").lower()
    if "html" in ctype or "xml" in ctype:
        return True
    if ctype.startswith("text/"):
        return True
    sample = result.body[:200].lstrip().lower()
    return sample.startswith(b"<!doctype html") or sample.startswith(b"<html") or sample.startswith(b"<head")


def is_page_url(url: str, site_host: str, owned: set[str]) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    matched = match_owned(parsed.hostname or "", owned)
    if matched != site_host:
        return None
    host = canonical_host(parsed.hostname or "")
    path = parsed.path or "/"
    if any(path.lower().startswith(prefix) for prefix in DENY_PREFIXES):
        return None
    suffix = Path(path.lower()).suffix
    if suffix in STATIC_EXT:
        return None
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunparse(("https", host, path or "/", "", urlencode(query_pairs), ""))


def score_url(url: str) -> int:
    path = urlparse(url).path.lower() or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    segments = [part for part in path.split("/") if part]
    return EXACT_PATH_SCORE.get(path, 0) + max(0, 24 - len(segments) * 10)


def host_aliases(host: str) -> set[str]:
    host = host.lower()
    aliases = {host, host.replace(".", "-"), host.replace(".", "")}
    label = host.split(".")[0]
    if len(label) >= 6:
        aliases.add(label)
    return {alias for alias in aliases if len(alias) >= 4}


def mentions_other_site(url: str, site_host: str, owned: set[str]) -> bool:
    path = urlparse(url).path.lower()
    padded = f"/{path.strip('/')}/"
    for other in owned:
        if other == site_host:
            continue
        for alias in host_aliases(other):
            if f"/{alias}/" in padded or f"/{alias}-" in padded or f"-{alias}/" in padded:
                return True
    return False


def collect_sitemap_urls(site_host: str, owned: set[str]) -> list[str]:
    seeds = [f"https://{site_host}/sitemap.xml"]
    robots = fetch(f"https://{site_host}/robots.txt", max_bytes=100_000)
    if robots.status and robots.status < 400:
        for line in robots.text().splitlines():
            if line.lower().startswith("sitemap:"):
                seeds.append(line.split(":", 1)[1].strip())

    seen: set[str] = set()
    queue = list(dict.fromkeys(seeds))
    found: list[str] = []
    while queue and len(seen) < MAX_SITEMAPS and len(found) < MAX_SITEMAP_URLS:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen:
            continue
        seen.add(sitemap_url)
        result = fetch(sitemap_url, max_bytes=SITEMAP_BYTES)
        if not result.status or result.status >= 400 or not result.body:
            continue
        text = result.text()
        locs = [unescape(item.strip()) for item in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text, re.I)]
        if re.search(r"<sitemapindex\b", text, re.I):
            for loc in locs:
                if loc in seen:
                    continue
                child_host = match_owned(urlparse(loc).hostname or "", owned)
                if child_host == site_host:
                    queue.append(loc)
            continue
        for loc in locs:
            normalized = is_page_url(loc, site_host, owned)
            if normalized:
                found.append(normalized)
                if len(found) >= MAX_SITEMAP_URLS:
                    break
    return list(dict.fromkeys(found))


def select_pages(
    site_host: str,
    owned: set[str],
    homepage_html: str,
    final_url: str,
    sitemap_urls: list[str],
    budget: int,
) -> list[str]:
    scored: dict[str, int] = {}

    def add(raw: str, bonus: int = 0) -> None:
        absolute = urljoin(final_url, raw)
        normalized = is_page_url(absolute, site_host, owned)
        if not normalized:
            return
        scored[normalized] = max(scored.get(normalized, -1), score_url(normalized) + bonus)

    home = f"https://{site_host}/"
    for anchor in extract_anchors(homepage_html):
        absolute = urljoin(final_url, anchor["href"])
        normalized = is_page_url(absolute, site_host, owned)
        if not normalized:
            continue
        segments = [part for part in urlparse(normalized).path.split("/") if part]
        add(normalized, 70 if len(segments) <= 2 else 0)
    for sitemap_url in sitemap_urls:
        bonus = 300 if mentions_other_site(sitemap_url, site_host, owned) else 0
        add(sitemap_url, bonus)

    ranked = sorted(
        ((score, url) for url, score in scored.items() if url.rstrip("/") != home.rstrip("/")),
        key=lambda item: (-item[0], len(item[1]), item[1]),
    )
    slug_hits = [url for _, url in ranked if mentions_other_site(url, site_host, owned)][:SLUG_PAGE_LIMIT]
    slug_set = set(slug_hits)
    generic = [url for _, url in ranked if url not in slug_set][: max(0, budget - 1)]
    chosen_set = set(slug_hits) | set(generic)
    extras = []
    for path in EXTRA_PATHS:
        normalized = is_page_url(f"https://{site_host}{path}", site_host, owned)
        if normalized and normalized.rstrip("/") != home.rstrip("/") and normalized not in chosen_set:
            extras.append(normalized)
            chosen_set.add(normalized)
    ordered = []
    seen_urls: set[str] = set()
    for url in [home, *extras, *slug_hits, *generic]:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        ordered.append(url)
    return ordered


def absolute_targets(href: str, base_url: str, site_host: str, owned: set[str]) -> list[tuple[str, str]]:
    absolute = urljoin(base_url, href.strip())
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def push(target_url: str) -> None:
        parsed = urlparse(target_url)
        if parsed.scheme not in {"http", "https"}:
            return
        matched = match_owned(parsed.hostname or "", owned)
        if not matched or matched == site_host:
            return
        key = (matched, target_url.split("#", 1)[0])
        if key in seen:
            return
        seen.add(key)
        found.append(key)

    push(absolute)
    for _, value in parse_qsl(urlparse(absolute).query, keep_blank_values=True):
        if value.startswith("http://") or value.startswith("https://"):
            push(value)
    return found


def mine_edges(html: str, source_url: str, site_host: str, owned: set[str], edges: dict) -> None:
    for anchor in extract_anchors(html):
        nofollow, sponsored = rel_flags(anchor["rel"])
        for target_host, target_url in absolute_targets(anchor["href"], source_url, site_host, owned):
            edge = edges.setdefault(target_host, {
                "links": 0,
                "dofollow": 0,
                "nofollow": 0,
                "sponsored": 0,
                "pages": set(),
                "examples": [],
            })
            edge["links"] += 1
            edge["pages"].add(source_url)
            if nofollow:
                edge["nofollow"] += 1
            else:
                edge["dofollow"] += 1
            if sponsored:
                edge["sponsored"] += 1
            signature = (source_url, target_url, anchor["text"])
            if any(
                (item["source_url"], item["target_url"], item["anchor"]) == signature
                for item in edge["examples"]
            ):
                continue
            if len(edge["examples"]) < 30:
                edge["examples"].append({
                    "source_url": source_url,
                    "target_url": target_url,
                    "anchor": anchor["text"],
                    "rel": anchor["rel"],
                    "nofollow": nofollow,
                    "sponsored": sponsored,
                })


def trim_examples(examples: list[dict]) -> list[dict]:
    unique_pages = []
    seen_pages = set()
    ordered = sorted(examples, key=lambda item: (item["nofollow"], item["source_url"]))
    for item in ordered:
        if item["source_url"] in seen_pages:
            continue
        seen_pages.add(item["source_url"])
        unique_pages.append(item)
        if len(unique_pages) == 8:
            break
    return unique_pages


def crawl_site(site: dict, owned: set[str], budget: int) -> dict:
    host = site["host"]
    home = fetch(f"https://{host}/", retries=1)
    title = html_title(home.text()) if home.body else ""
    result = {
        "host": host,
        "group": site["group"],
        "group_label": site["group_label"],
        "note": site["note"],
        "ok": False,
        "http_status": home.status,
        "final_url": home.final_url,
        "title": title,
        "error": None,
        "pages_fetched": 0,
        "duplicate_skipped": 0,
        "fetch_errors": [],
        "fetched_urls": [],
        "edges": {},
    }
    if home.status == 0 or home.status >= 400 or not looks_like_html(home):
        label = title or html_h1(home.text())
        if home.error and home.status == 0:
            message = home.error
        else:
            message = home.error or f"HTTP {home.status}"
            if label:
                message = f"{message} · {label}"
        result["error"] = message
        return result

    result["ok"] = True
    sitemap_urls = collect_sitemap_urls(host, owned)
    pages = select_pages(host, owned, home.text(), home.final_url, sitemap_urls, budget)
    edges: dict = {}
    seen_hashes: set[str] = set()
    seen_final: set[str] = set()

    def load(url: str) -> tuple[str, FetchResult]:
        if url.rstrip("/") == f"https://{host}":
            return url, home
        return url, fetch(url)

    loaded: list[tuple[str, FetchResult]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        loaded.extend(pool.map(load, pages))

    for url, page in loaded:
        page_url = page.final_url or url
        if url.rstrip("/") != f"https://{host}":
            if page.status == 0 or page.status >= 400 or not looks_like_html(page):
                if len(result["fetch_errors"]) < 8:
                    result["fetch_errors"].append({
                        "url": url,
                        "error": page.error or f"HTTP {page.status}",
                    })
                continue
            final_host = match_owned(urlparse(page_url).hostname or "", owned)
            if final_host != host:
                continue

        final_key = is_page_url(page_url, host, owned) or page_url
        if final_key in seen_final:
            result["duplicate_skipped"] += 1
            continue
        seen_final.add(final_key)
        digest = hashlib.sha256(page.body).hexdigest()
        result["pages_fetched"] += 1
        if len(result["fetched_urls"]) < 80:
            result["fetched_urls"].append(page_url)
        if digest in seen_hashes:
            result["duplicate_skipped"] += 1
            continue
        seen_hashes.add(digest)
        mine_edges(page.text(), page_url, host, owned, edges)

    serialized = {}
    for target, edge in sorted(edges.items()):
        serialized[target] = {
            "links": edge["links"],
            "dofollow": edge["dofollow"],
            "nofollow": edge["nofollow"],
            "sponsored": edge["sponsored"],
            "pages": len(edge["pages"]),
            "examples": trim_examples(edge["examples"]),
        }
    result["edges"] = serialized
    return result


def build_report(config: dict, site_results: list[dict], started: float) -> dict:
    edges = []
    sites = []
    for item in site_results:
        edges_map = item.pop("edges")
        sites.append(item)
        for target, edge in edges_map.items():
            edges.append({
                "from": item["host"],
                "to": target,
                **edge,
            })
    sites.sort(key=lambda site: [s["host"] for s in flatten_sites(config)].index(site["host"]))
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_sec": round(time.time() - started, 1),
        "scope": "只统计这些站页面里指向名单内域名的链接。优先抓首页、关于/友链/产品页，以及网址里带了其他站名的页面。不查站外 backlink，不执行 JavaScript。",
        "limits": {
            "max_pages_per_site": config.get("max_pages_per_site", 18),
        },
        "groups": [
            {
                "id": group["id"],
                "label": group["label"],
                "hosts": [site["host"] for site in group["sites"]],
            }
            for group in config["groups"]
        ],
        "sites": sites,
        "edges": edges,
    }


def run_scan(config: dict | None = None, on_progress=None) -> dict:
    config = config or load_config()
    sites = flatten_sites(config)
    owned = {site["host"] for site in sites}
    budget = int(config.get("max_pages_per_site", 18))
    started = time.time()
    results: dict[str, dict] = {}

    def report(host: str, state: str) -> None:
        if on_progress:
            on_progress(host, state)

    def crawl_one(site: dict) -> dict:
        report(site["host"], "抓取中")
        return crawl_site(site, owned, budget)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for site in sites:
            report(site["host"], "排队")
            futures[pool.submit(crawl_one, site)] = site["host"]
        for future in as_completed(futures):
            host = futures[future]
            try:
                results[host] = future.result()
            except Exception as exc:
                site = next(item for item in sites if item["host"] == host)
                results[host] = {
                    "host": host,
                    "group": site["group"],
                    "group_label": site["group_label"],
                    "note": site["note"],
                    "ok": False,
                    "http_status": 0,
                    "final_url": f"https://{host}/",
                    "title": "",
                    "error": f"检查失败：{exc}",
                    "pages_fetched": 0,
                    "duplicate_skipped": 0,
                    "fetch_errors": [],
                    "fetched_urls": [],
                    "edges": {},
                }
            report(host, "完成" if results[host]["ok"] else "打不开")

    ordered = [results[site["host"]] for site in sites]
    return build_report(config, ordered, started)


def write_report(report: dict, path: Path = REPORT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def format_summary(report: dict) -> str:
    sites = {site["host"]: site for site in report["sites"]}
    edge_map = {(edge["from"], edge["to"]): edge for edge in report["edges"]}
    lines = [
        f"检查完成 {report['generated_at']} ，用时 {report['duration_sec']}s",
        report["scope"],
        "",
    ]
    down = [site for site in report["sites"] if not site["ok"]]
    if down:
        lines.append("打不开：")
        for site in down:
            lines.append(f"  {site['host']}  {site['error']}")
        lines.append("")
    lines.append("有向链接：")
    if not report["edges"]:
        lines.append("  （没有）")
    for edge in report["edges"]:
        back = (edge["to"], edge["from"]) in edge_map
        target_ok = sites[edge["to"]]["ok"]
        if back and target_ok:
            kind = "互链"
        elif not target_ok:
            kind = "单向，对方打不开"
        else:
            kind = "单向"
        flags = []
        if edge["dofollow"] == 0:
            flags.append("全部 nofollow/sponsored")
        elif edge["nofollow"]:
            flags.append("含 nofollow")
        if edge["sponsored"]:
            flags.append("含 sponsored")
        flag_text = f"（{'，'.join(flags)}）" if flags else ""
        anchor = edge["examples"][0]["anchor"] if edge["examples"] else ""
        anchor_text = f" 「{anchor}」" if anchor else ""
        lines.append(f"  {kind}  {edge['from']} → {edge['to']}{anchor_text}{flag_text}  {edge['pages']} 页")
    return "\n".join(lines)


def main() -> None:
    report = run_scan(on_progress=lambda host, state: print(f"[{state}] {host}", flush=True))
    write_report(report)
    print(format_summary(report))
    print(f"\n报告已写入 {REPORT_PATH}")


if __name__ == "__main__":
    main()
