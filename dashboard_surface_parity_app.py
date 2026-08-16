"""V3.32.6 yüzey paritesi yardımcıları.

Bu modül yeni sinyal üretmez ve işlem çekirdeğine yazmaz. Mevcut ürün sözleşmesinde
masaüstünde bulunan kullanıcı işlerini JS'siz mobil yüzeye taşımak için yalnız sunum,
GET filtreleme, public OKX okuması ve tarayıcıya ait tercih çerezi kullanır.
"""
from __future__ import annotations

import copy
import html
import re
import urllib.parse
from http.cookies import SimpleCookie
from typing import Any

import dashboard_commercial_app as commercial
import dashboard_market_app as market
import dashboard_mobile_market_app as mobilemarket
from dashboard_live_app import OKXMarketDataClient

WATCH_COOKIE = "kripto_watch_v3326"
MAX_WATCH = 12
ALLOWED_FILTERS = {"all", "score80", "up", "down", "active", "volume"}
ALLOWED_SORTS = {"default", "score", "change", "volume"}


def _esc(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _symbol(value: Any) -> str:
    try:
        return OKXMarketDataClient.normalize_symbol(str(value or ""))
    except ValueError:
        return ""


def _query_value(query: dict[str, list[str]], key: str, default: str = "") -> str:
    return str((query.get(key) or [default])[0] or default).strip()


def premium_plan(plan: str) -> bool:
    return str(plan or "").upper() in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}


def mobile_nav(plan: str, active: str) -> str:
    """Bütün JS'siz mobil sayfalarda aynı çekirdek navigasyonu üretir."""
    if not premium_plan(plan):
        items = [
            ("home", "/mobile", "⌂", "Ana"),
            ("market", "/mobile/market", "⌁", "Piyasa"),
            ("premium", "/mobile/premium", "◆", "Premium"),
            ("account", "/mobile/account", "○", "Hesap"),
        ]
        cls = "nav4"
    else:
        items = [
            ("home", "/mobile", "⌂", "Ana"),
            ("signals", "/mobile?view=signals", "⚡", "Sinyal"),
            ("trades", "/mobile?view=trades", "↕", "İşlem"),
            ("results", "/mobile?view=results", "✓", "Sonuç"),
            ("account", "/mobile/account", "○", "Hesap"),
        ]
        cls = "nav5"
    links = "".join(
        f'<a class="{"active" if key == active else ""}" href="{href}"><span>{icon}</span>{label}</a>'
        for key, href, icon, label in items
    )
    return f'<nav class="bottomnav {cls}">{links}</nav>'


def replace_mobile_nav(body: str, *, plan: str, active: str) -> str:
    return re.sub(
        r'<nav class="bottomnav[^\"]*">.*?</nav>',
        mobile_nav(plan, active), body, count=1, flags=re.S,
    )


def correct_product_copy(body: str) -> str:
    """Vitrindeki cihaz-geneli görünen ses vaadini gerçek masaüstü kapsamıyla eşler."""
    replacements = {
        "Sesli ve renkli yeni sinyal uyarısı": "Masaüstünde sesli ve renkli yeni sinyal uyarısı",
        "Canlı sinyal detayları, sesli-renkli uyarılar, izleme ve gelişmiş analiz Premium kullanıcıya açılır.":
            "Canlı sinyal detayları, izleme ve gelişmiş analiz Premium kullanıcıya açılır; sesli/renkli tarayıcı uyarıları masaüstünde kullanılabilir.",
        "🔒 Sesli ve renkli sinyal uyarıları": "🔒 Sesli ve renkli sinyal uyarıları (masaüstü)",
    }
    for old, new in replacements.items():
        body = body.replace(old, new)
    return body


def _row_text(row: dict[str, Any]) -> str:
    values = [
        row.get("symbol"), row.get("system"), row.get("system_label"), row.get("source"),
        row.get("direction"), row.get("outcome"), row.get("result"), row.get("status"),
    ]
    return " ".join(str(v or "") for v in values).upper()


def filter_mobile_data(data: dict[str, Any], query: dict[str, list[str]], view: str) -> dict[str, Any]:
    """Sinyal/işlem/sonuç mobil filtrelerini sunucu tarafında uygular."""
    result = copy.deepcopy(data if isinstance(data, dict) else {})
    q = _query_value(query, "q").upper()
    system = _query_value(query, "system").upper()
    direction = _query_value(query, "direction", "all").upper()
    outcome = _query_value(query, "outcome", "all").upper()

    if view in {"signals", "trades"}:
        rows = [r for r in (result.get("open_trades") or []) if isinstance(r, dict)]
        filtered = []
        for row in rows:
            text = _row_text(row)
            if q and q not in text:
                continue
            if system and system not in text:
                continue
            if direction in {"LONG", "SHORT"} and str(row.get("direction") or "").upper() != direction:
                continue
            filtered.append(row)
        result["open_trades"] = filtered
    elif view == "results":
        rows = [r for r in (result.get("recent_results") or []) if isinstance(r, dict)]
        filtered = []
        for row in rows:
            text = _row_text(row)
            row_outcome = str(row.get("outcome") or row.get("result") or "").upper()
            if q and q not in text:
                continue
            if system and system not in text:
                continue
            if outcome == "TP" and not row_outcome.startswith("TP"):
                continue
            if outcome == "SL" and not (row_outcome == "SL" or row_outcome.startswith("SL_")):
                continue
            if outcome == "BE" and "BE" not in row_outcome:
                continue
            filtered.append(row)
        result["recent_results"] = filtered
    return result


def _filter_css() -> str:
    return """
.v3326-filter{display:grid;grid-template-columns:1.3fr .9fr .9fr auto;gap:6px;margin:4px 0 13px;padding:8px;border:1px solid var(--line);border-radius:11px;background:#09151d}
.v3326-filter input,.v3326-filter select{min-width:0;width:100%;border:1px solid var(--line);background:#07131b;color:var(--text);border-radius:8px;padding:9px;font:inherit;font-size:10px}.v3326-filter button{padding:9px 11px;color:var(--teal)}
.v3326-tools{display:flex;gap:7px;margin:8px 0 12px}.v3326-tools a{flex:1;border:1px solid var(--line);background:#0b1821;border-radius:10px;padding:10px;text-align:center;font-size:9px;font-weight:850}.v3326-tools a.primary{color:var(--teal);border-color:#275b50;background:#0d2a24}
@media(max-width:520px){.v3326-filter{grid-template-columns:1fr 1fr}.v3326-filter input{grid-column:1/-1}.v3326-filter button{grid-column:1/-1}}
"""


def filter_form(view: str, query: dict[str, list[str]]) -> str:
    q = html.escape(_query_value(query, "q"), quote=True)
    system = html.escape(_query_value(query, "system"), quote=True)
    if view in {"signals", "trades"}:
        direction = _query_value(query, "direction", "all").upper()
        options = "".join(
            f'<option value="{value}" {"selected" if direction == value.upper() else ""}>{label}</option>'
            for value, label in (("all", "Tüm yönler"), ("LONG", "LONG"), ("SHORT", "SHORT"))
        )
        selector = f'<select name="direction" aria-label="Yön">{options}</select>'
    else:
        outcome = _query_value(query, "outcome", "all").upper()
        options = "".join(
            f'<option value="{value}" {"selected" if outcome == value.upper() else ""}>{label}</option>'
            for value, label in (("all", "Tüm sonuçlar"), ("TP", "TP"), ("SL", "SL"), ("BE", "BE"))
        )
        selector = f'<select name="outcome" aria-label="Sonuç">{options}</select>'
    return (
        f'<form class="v3326-filter" method="get" action="/mobile">'
        f'<input type="hidden" name="view" value="{html.escape(view, quote=True)}">'
        f'<input name="q" value="{q}" placeholder="Coin ara: BTC, ETH…" maxlength="24">'
        f'{selector}<input name="system" value="{system}" placeholder="Sistem" maxlength="30">'
        '<button type="submit">Filtrele</button></form>'
    )


def _insert_after_mobile_hero(body: str, fragment: str) -> str:
    """Mobil sunucu şablonunda hero/user kapanışından sonra içerik ekler."""
    marker = "</form></div></div></div>"
    pos = body.find(marker)
    if pos >= 0:
        end = pos + len(marker)
        return body[:end] + fragment + body[end:]
    nav = re.search(r'<nav class="bottomnav', body)
    if nav:
        return body[:nav.start()] + fragment + body[nav.start():]
    return body + fragment


def enhance_mobile_core(body: str, *, plan: str, active: str, query: dict[str, list[str]] | None = None) -> str:
    body = replace_mobile_nav(body, plan=plan, active=active)
    body = body.replace("Öne çıkan sinyaller", "Güncel sinyaller")
    css = _filter_css()
    if css not in body and "</style>" in body:
        body = body.replace("</style>", css + "\n</style>", 1)
    if premium_plan(plan) and active in {"signals", "trades", "results"} and query is not None:
        form = filter_form(active, query)
        if '<form class="v3326-filter"' not in body:
            body = _insert_after_mobile_hero(body, form)
    return body


def enhance_mobile_market(body: str, *, plan: str, active: str = "market") -> str:
    body = replace_mobile_nav(body, plan=plan, active=active)
    css = _filter_css()
    if css not in body and "</style>" in body:
        body = body.replace("</style>", css + "\n</style>", 1)
    if premium_plan(plan) and active == "market" and "v3326-tools" not in body:
        tools = '<div class="v3326-tools"><a class="primary" href="/mobile/watchlist">İzleme Listesi</a><a href="/mobile/opportunities">Fırsat Merkezi</a></div>'
        search_end = body.find("</form>")
        if search_end >= 0:
            end = search_end + len("</form>")
            body = body[:end] + tools + body[end:]
        else:
            nav = re.search(r'<nav class="bottomnav', body)
            if nav:
                body = body[:nav.start()] + tools + body[nav.start():]
    return body


def read_watchlist(cookie_header: str | None) -> list[str]:
    if not cookie_header:
        return []
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
        raw = urllib.parse.unquote(jar.get(WATCH_COOKIE).value) if jar.get(WATCH_COOKIE) else ""
    except Exception:
        return []
    result: list[str] = []
    for part in raw.split(","):
        symbol = _symbol(part)
        if symbol and symbol not in result:
            result.append(symbol)
        if len(result) >= MAX_WATCH:
            break
    return result


def watch_cookie(symbols: list[str], *, secure: bool) -> str:
    clean: list[str] = []
    for raw in symbols:
        symbol = _symbol(raw)
        if symbol and symbol not in clean:
            clean.append(symbol)
        if len(clean) >= MAX_WATCH:
            break
    value = urllib.parse.quote(",".join(clean), safe=",")
    parts = [f"{WATCH_COOKIE}={value}", "Path=/", "Max-Age=31536000", "SameSite=Lax", "HttpOnly"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def update_watchlist(symbols: list[str], *, add: str = "", remove: str = "") -> list[str]:
    result = list(symbols)
    add_symbol = _symbol(add)
    remove_symbol = _symbol(remove)
    if remove_symbol:
        result = [s for s in result if s != remove_symbol]
    if add_symbol and add_symbol not in result and len(result) < MAX_WATCH:
        result.append(add_symbol)
    return result


def _change(value: Any) -> tuple[str, str]:
    n = _num(value)
    if n is None:
        return "—", ""
    return f"{n:+.2f}%", "up" if n >= 0 else "down"


def render_watchlist_page(session: dict[str, Any], *, plan: str, plan_label: str, symbols: list[str], items: list[dict[str, Any]], data: dict[str, Any], score: dict[str, Any] | None = None, score_symbol: str = "") -> str:
    context = market.market_context(data if isinstance(data, dict) else {})
    by_symbol = {str(item.get("symbol") or ""): item for item in items if isinstance(item, dict)}
    cards: list[str] = []
    for symbol in symbols:
        item = dict(by_symbol.get(symbol) or {"symbol": symbol})
        item.update(context.get(symbol, {}))
        change, cls = _change(item.get("change_24h_pct"))
        direction = str(item.get("direction") or "").upper()
        live = f'<span class="context {"long" if direction == "LONG" else "short"}">{_esc(direction)} · açık</span>' if item.get("kind") == "OPEN" else ""
        technical = ""
        if score and score_symbol == symbol:
            metrics = score.get("metrics") if isinstance(score.get("metrics"), dict) else {}
            technical = (
                '<div class="detailgrid">'
                f'<div class="mini"><small>Skor</small><b>{_esc(score.get("score"))}/100</b></div>'
                f'<div class="mini"><small>Teknik yön</small><b>{_esc(score.get("direction"))}</b></div>'
                f'<div class="mini"><small>RSI 15m</small><b>{_esc(metrics.get("rsi_15m"))}</b></div>'
                f'<div class="mini"><small>Hacim 15m</small><b>{_esc(metrics.get("volume_ratio_15m"))}x</b></div>'
                '</div>'
            )
        cards.append(
            f'<article class="market-card"><div class="market-main"><div><strong>{_esc(symbol)}</strong>{live}</div><div class="price"><b>{_esc(mobilemarket._fmt(item.get("last")))}</b><span class="{cls}">{_esc(change)}</span></div></div>'
            f'<div class="v3326-tools"><a href="/mobile/coin?symbol={urllib.parse.quote(symbol)}">Coin Merkezi</a><a href="/mobile/watchlist?tech={urllib.parse.quote(symbol)}">Teknik özet</a><a href="/mobile/watchlist?remove={urllib.parse.quote(symbol)}">Kaldır</a></div>{technical}</article>'
        )
    if not cards:
        cards.append('<div class="empty">İzleme listen boş. Aşağıdaki hızlı ekleme seçeneklerini kullanabilirsin.</div>')
    quick = "".join(f'<a href="/mobile/watchlist?add={s}">{s[:-4]}</a>' for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"))
    body = (
        '<form class="search" method="get" action="/mobile/watchlist"><input name="add" placeholder="Coin ekle: BTCUSDT" autocomplete="off"><button type="submit">Ekle</button></form>'
        f'<div class="v3326-tools">{quick}</div>'
        '<section><div class="sectionhead"><h2>Takip ettiklerin</h2><span>en fazla 12</span></div>' + "".join(cards) + '</section>'
        '<div class="notice">Liste yalnız bu tarayıcıda tercih çereziyle saklanır. Teknik özet işlem sinyali veya başarı olasılığı değildir.</div>'
    )
    page = mobilemarket._shell(title="İzleme Listesi", subtitle="Favori coinlerini tek yerde takip et", plan_label=plan_label, username=str(session.get("username") or "üye"), body=body, nav=mobile_nav(plan, ""), top_link="/mobile/market", top_text="Piyasa")
    return enhance_mobile_market(page, plan=plan, active="")


def _opportunity_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("active", "rising", "falling", "volume"):
        for item in groups.get(key) or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            copy_item = dict(item)
            copy_item["group"] = key
            result.append(copy_item)
    return result


def prepare_opportunities(payload: dict[str, Any], query: dict[str, list[str]], score_service=None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = _opportunity_candidates(payload)
    q = _query_value(query, "q").upper()
    filter_key = _query_value(query, "filter", "all").lower()
    sort_key = _query_value(query, "sort", "default").lower()
    if filter_key not in ALLOWED_FILTERS:
        filter_key = "all"
    if sort_key not in ALLOWED_SORTS:
        sort_key = "default"
    if q:
        rows = [row for row in rows if q in str(row.get("symbol") or "").upper()]

    need_score = filter_key in {"score80", "up", "down", "volume"} or sort_key in {"score", "volume"}
    if need_score and score_service is not None:
        for row in rows[:16]:
            try:
                row["analysis"] = score_service.get_score(str(row.get("symbol") or ""))
            except Exception:
                row["analysis"] = {}

    def analysis(row: dict[str, Any]) -> dict[str, Any]:
        return row.get("analysis") if isinstance(row.get("analysis"), dict) else {}

    if filter_key == "score80":
        rows = [r for r in rows if (_num(analysis(r).get("score")) or 0) >= 80]
    elif filter_key == "up":
        rows = [r for r in rows if str(analysis(r).get("direction") or "").upper() == "YUKARI"]
    elif filter_key == "down":
        rows = [r for r in rows if str(analysis(r).get("direction") or "").upper() == "AŞAĞI"]
    elif filter_key == "active":
        rows = [r for r in rows if str(r.get("kind") or "") == "OPEN" or str(r.get("group") or "") == "active"]
    elif filter_key == "volume":
        rows = [r for r in rows if (_num((analysis(r).get("metrics") or {}).get("volume_ratio_15m")) or 0) >= 1.5]

    if sort_key == "score":
        rows.sort(key=lambda r: _num(analysis(r).get("score")) or -1, reverse=True)
    elif sort_key == "change":
        rows.sort(key=lambda r: abs(_num(r.get("change_24h_pct")) or 0), reverse=True)
    elif sort_key == "volume":
        rows.sort(key=lambda r: _num((analysis(r).get("metrics") or {}).get("volume_ratio_15m")) or -1, reverse=True)
    meta = {"filter": filter_key, "sort": sort_key, "q": q, "need_score": need_score}
    return rows[:24], meta


def render_opportunities_page(session: dict[str, Any], *, plan: str, plan_label: str, rows: list[dict[str, Any]], meta: dict[str, Any], summary: dict[str, Any]) -> str:
    options_filter = (("all", "Tümü"), ("score80", "80+ skor"), ("up", "Teknik ↑"), ("down", "Teknik ↓"), ("active", "Aktif sinyal"), ("volume", "Hacim 1.5x+"))
    options_sort = (("default", "Grup sırası"), ("score", "Skor yüksek"), ("change", "24s hareket"), ("volume", "Hacim oranı"))
    filter_html = "".join(f'<option value="{v}" {"selected" if meta.get("filter") == v else ""}>{label}</option>' for v, label in options_filter)
    sort_html = "".join(f'<option value="{v}" {"selected" if meta.get("sort") == v else ""}>{label}</option>' for v, label in options_sort)
    q = html.escape(str(meta.get("q") or ""), quote=True)
    form = f'<form class="v3326-filter" method="get" action="/mobile/opportunities"><input name="q" value="{q}" placeholder="Coin ara"><select name="filter">{filter_html}</select><select name="sort">{sort_html}</select><button type="submit">Uygula</button></form>'
    cards: list[str] = []
    labels = {"active": "Aktif", "rising": "Yükselen", "falling": "Düşen", "volume": "Hacim"}
    for row in rows:
        symbol = str(row.get("symbol") or "—")
        change, cls = _change(row.get("change_24h_pct"))
        analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
        score_value = analysis.get("score")
        score_direction = analysis.get("direction")
        score_chip = f'<span class="context recent">{_esc(score_value)}/100 · {_esc(score_direction)}</span>' if score_value not in (None, "") else ""
        system_dir = str(row.get("direction") or "").upper()
        active_chip = f'<span class="context {"long" if system_dir == "LONG" else "short"}">{_esc(system_dir)} · açık</span>' if str(row.get("kind") or "") == "OPEN" else ""
        cards.append(
            f'<article class="market-card"><a class="market-main" href="/mobile/coin?symbol={urllib.parse.quote(symbol)}"><div><strong>{_esc(symbol)}</strong>{active_chip}{score_chip}<small style="display:block;color:var(--muted);font-size:7px;margin-top:3px">{_esc(labels.get(str(row.get("group") or ""), "Piyasa"))}</small></div><div class="price"><b>{_esc(mobilemarket._fmt(row.get("last")))}</b><span class="{cls}">{_esc(change)}</span></div></a></article>'
        )
    content = "".join(cards) or '<div class="empty">Bu filtreye uyan coin bulunamadı.</div>'
    body = (
        form
        + f'<div class="metrics"><div class="metric"><small>İncelenen evren</small><b>{int(summary.get("universe") or 0)}</b></div><div class="metric"><small>Yükselen</small><b class="green">{int(summary.get("up") or 0)}</b></div><div class="metric"><small>Düşen</small><b class="red">{int(summary.get("down") or 0)}</b></div></div>'
        + '<div class="v3326-tools"><a href="/mobile/market">Piyasa Merkezi</a><a class="primary" href="/mobile/watchlist">İzleme Listesi</a></div>'
        + '<section><div class="sectionhead"><h2>Fırsatlar</h2><span>teknik inceleme</span></div>' + content + '</section>'
        + '<div class="notice">Fırsat grupları ve İnceleme Skoru teknik önceliklendirmedir; yeni işlem sinyali veya başarı ihtimali değildir.</div>'
    )
    page = mobilemarket._shell(title="Fırsat Merkezi", subtitle="Mevcut masaüstü keşif araçlarının JS'siz mobil karşılığı", plan_label=plan_label, username=str(session.get("username") or "üye"), body=body, nav=mobile_nav(plan, ""), top_link="/mobile/market", top_text="Piyasa")
    return page.replace("</style>", _filter_css() + "\n</style>", 1)
