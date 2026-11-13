"""Разбор публичных карточек без исполнения JS и догадок о контактах."""

import ast
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

PROVIDERS = ("2gis", "yandex", "google")
HOSTS = {"2gis": {"2gis.ru"}, "yandex": {"yandex.ru"}, "google": {"www.google.com", "google.com"}}
CITY = {"Москва": ("moscow", "213", ("москва", "moscow")), "Ярославль": ("yaroslavl", "16", ("ярославль", "yaroslavl"))}
SOCIAL = {
    "t.me",
    "telegram.me",
    "max.ru",
    "vk.com",
    "m.vk.com",
    "vk.ru",
    "ok.ru",
    "wa.me",
    "api.whatsapp.com",
    "youtube.com",
    "www.youtube.com",
    "instagram.com",
    "www.instagram.com",
}
LITERAL = re.compile(r'JSON\.parse\(\s*(\'(?:\\.|[^\'\\])*\'|"(?:\\.|[^"\\])*")\s*\)')


CITY_ALIASES = {
    "санкт-петербург": "spb",
    "санкт петербург": "spb",
    "нижний новгород": "n_novgorod",
    "ростов-на-дону": "rostov",
    "набережные челны": "nabchelny",
    "улан-удэ": "ulanude",
}
CYRILLIC = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        (
            "a",
            "b",
            "v",
            "g",
            "d",
            "e",
            "e",
            "zh",
            "z",
            "i",
            "y",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "r",
            "s",
            "t",
            "u",
            "f",
            "h",
            "c",
            "ch",
            "sh",
            "sch",
            "",
            "y",
            "",
            "e",
            "yu",
            "ya",
        ),
        strict=True,
    )
)


def search_value(value: str, limit: int) -> str:
    value = " ".join(value.strip().split())
    if (
        not value
        or len(value) > limit
        or not any(char.isalpha() for char in value)
        or "://" in value
        or any(char in value for char in "<>\x00")
    ):
        raise ValueError("invalid_search_value")
    return value


def city_slug(city: str) -> str:
    for known, values in CITY.items():
        if known.casefold() == city.casefold():
            return values[0]
    key = city.casefold()
    if key in CITY_ALIASES:
        return CITY_ALIASES[key]
    transliterated = "".join(CYRILLIC.get(char, char) for char in key)
    return re.sub(r"[^a-z0-9]+", "_", transliterated).strip("_")


def niche_matches(niche: str, text: str) -> bool:
    value = text.casefold()
    # Сохраняем точность ранее запрошенной специализации, но не делаем её глобальным фильтром.
    if re.search(r"кита[яйею]|china", niche.casefold()) and re.search(r"авто|машин|car", niche.casefold()):
        return bool(
            re.search(r"кита[яйею]|china", value)
            and re.search(r"авто|машин|car|automob", value)
            and re.search(r"привоз|достав|импорт|пригон|из\s+кита|под\s+заказ|import|from\s+china", value)
        )
    ignored = {"в", "и", "из", "на", "по", "для", "с", "под", "компании", "компания", "услуги", "услуга"}
    words = [word for word in re.findall(r"[а-яёa-z0-9]+", niche.casefold()) if word not in ignored]
    roots = [
        word[:6] if len(word) > 6 else re.sub(r"(?:ами|ями|ов|ев|ей|ы|и|а|я)$", "", word) if len(word) > 4 else word
        for word in words
    ]
    if len(roots) > 1:
        roots = [
            root for root in roots if root not in {"салон", "магази", "центр", "центры", "компан", "услуг", "агентс"}
        ]
    synonyms = {
        "красот": ("красот", "парикмах", "маникюр", "косметолог"),
        "кварти": ("квартир", "помещен"),
        "стомат": ("стомат", "dental", "dentist"),
        "цветоч": ("цвет", "флорист"),
    }
    return bool(roots) and all(any(variant in value for variant in synonyms.get(root, (root,))) for root in roots)


@dataclass(frozen=True)
class Company:
    provider: str
    external_id: str
    source_url: str
    name: str
    description: str
    city: str
    address: str
    phones: tuple[str, ...]
    telegram: tuple[str, ...]
    max_links: tuple[str, ...]
    websites: tuple[str, ...]
    website_state: str
    in_scope: bool

    def payload(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    def fingerprint(self) -> str:
        return hashlib.sha256(self.payload().encode()).hexdigest()


def phone(value: str) -> str | None:
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return "+" + digits if 10 <= len(digits) <= 15 else None


def public_url(value: str) -> str | None:
    value = unescape(str(value)).strip()
    if value.startswith("//"):
        value = "https:" + value
    if "://" not in value and re.match(r"[\w.-]+\.[a-z]{2,}/?", value, re.IGNORECASE):
        value = "https://" + value
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return None
    # Никогда не разыменовываем сайты и мессенджеры, только показываем опубликованную ссылку.
    return value


def contacts(urls):
    tg, mx, web = [], [], []
    for value in urls:
        value = public_url(value)
        if not value:
            continue
        host = (urlsplit(value).hostname or "").lower().removeprefix("www.")
        if host in {"t.me", "telegram.me"}:
            tg.append(value)
        elif host in {"max.ru"}:
            mx.append(value)
        elif host not in SOCIAL and host not in {
            "2gis.ru",
            "link.2gis.ru",
            "yandex.ru",
            "google.com",
            "maps.google.com",
        }:
            web.append(value)
    return tuple(dict.fromkeys(tg)), tuple(dict.fromkeys(mx)), tuple(dict.fromkeys(web))


def city_matches(city: str, location: str) -> bool:
    text = location.casefold()
    terms = CITY.get(city, (None, None, (city.casefold(),)))[2]
    for term in terms:
        for match in re.finditer(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text):
            if not re.match(r"\s+(?:oblast|region|область)\b", text[match.end() :]):
                return True
    return False


def in_scope(city: str, location: str, description: str, niche: str | None = None) -> bool:
    if niche is not None:
        return city_matches(city, location) and niche_matches(niche, description)
    city_ok = city_matches(city, location)
    text = description.casefold()
    return bool(
        city_ok
        and re.search(r"кита[яйею]|\bchina\b", text)
        and re.search(r"авто|машин|\bcars?\b|automob", text)
        and re.search(r"привоз|достав|импорт|пригон|из\s+кита|под\s+заказ|import|from\s+china", text)
    )


def search_url(provider: str, city: str, query: str, page: int = 1) -> str:
    city = search_value(city, 64)
    query = search_value(query, 100)
    slug = city_slug(city)
    if provider == "2gis":
        return f"https://2gis.ru/{slug}/search/{quote(query, safe='')}" + (f"/page/{page}" if page > 1 else "")
    if provider == "yandex":
        return "https://yandex.ru/maps/?text=" + quote(city + " " + query, safe="")
    if provider == "google":
        return "https://www.google.com/maps/search/" + quote(query + " " + city, safe="") + "/?hl=ru"
    raise ValueError("unknown_provider")


def canonical(provider: str, value: str, base: str) -> tuple[str, str] | None:
    if len(value) > 3000:
        return None
    url = urljoin(base, unescape(value))
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return None
    if (
        p.scheme != "https"
        or p.hostname not in HOSTS.get(provider, set())
        or p.username
        or p.password
        or port not in {None, 443}
    ):
        return None
    if provider == "2gis":
        if re.fullmatch(r"/firm/\d+", p.path):
            slug = urlsplit(base).path.strip("/").split("/")[0]
            if re.fullmatch(r"[a-z0-9_-]{1,80}", slug):
                p = p._replace(path="/" + slug + p.path)
        match = re.fullmatch(r"/([a-z_]+)/firm/(\d+)(?:/[^?#]*)?", p.path)
        if not match:
            return None
        return match[2], f"https://2gis.ru/{match[1]}/firm/{match[2]}"
    if provider == "yandex":
        match = re.fullmatch(r"/maps/org/(?:(?P<slug>[^/]+)/)?(?P<id>\d+)/?", p.path)
        return (match["id"], "https://yandex.ru" + p.path.rstrip("/") + "/") if match else None
    if p.path.startswith("/maps/place/"):
        # cid/hex из ссылки надёжнее заголовка, название не используется как ID.
        match = re.search(r"!1s([^!/?]+)", p.path)
        place_id = parse_qs(p.query).get("query_place_id", [None])[0]
        identity = unquote(match[1]) if match else place_id
        if identity:
            return identity, "https://www.google.com" + p.path
    return None


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.links = []
        self.active_script = False
        self.buffer = []
        self.title = []
        self.in_h1 = False
        self.in_category = False
        self.description = ""
        self.meta_description = ""
        self.address = ""
        self.phones = []
        self.authority = []
        self.profile_markers = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("property") == "og:description":
            self.meta_description = a.get("content", "")
        if tag == "script":
            self.active_script = True
            self.buffer = []
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "h1":
            self.in_h1 = True
        item = a.get("data-item-id", "")
        label = a.get("aria-label", "")
        if item == "authority" and a.get("href"):
            self.authority.append(a["href"])
        if item.startswith("phone:tel:"):
            self.phones.append(item.removeprefix("phone:tel:"))
            self.profile_markers.add("phone")
        if item == "address":
            self.address = re.sub(r"^(?:Адрес|Address):\s*", "", label)
            self.profile_markers.add("address")
        if tag == "button" and a.get("jsaction", "").startswith("pane.rating.category"):
            self.description = label
            self.in_category = True

    def handle_data(self, value):
        if self.active_script:
            self.buffer.append(value)
        if self.in_h1:
            self.title.append(value)
        if self.in_category:
            self.description += value

    def handle_endtag(self, tag):
        if tag == "script" and self.active_script:
            self.scripts.append("".join(self.buffer))
            self.active_script = False
        if tag == "h1":
            self.in_h1 = False
        if tag == "button":
            self.in_category = False


def json_roots(page: Page):
    for script in page.scripts:
        try:
            yield json.loads(script)
        except ValueError:
            pass
        for match in LITERAL.finditer(script):
            try:
                value = ast.literal_eval(match[1])
                if isinstance(value, str):
                    yield json.loads(value)
            except (ValueError, SyntaxError, RecursionError):
                continue


def walk(root):
    stack = [(root, 0)]
    visited = 0
    while stack and visited < 100000:
        item, depth = stack.pop()
        visited += 1
        if depth > 40:
            continue
        yield item
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)


def parse_2gis(item: dict, city: str, source_url: str, niche: str | None = None) -> Company | None:
    identity = str(item.get("id", ""))
    name = item.get("name")
    if not re.fullmatch(r"\d+", identity) or not isinstance(name, str) or "contact_groups" not in item:
        return None
    groups = item["contact_groups"]
    if not isinstance(groups, list):
        return None
    phones = []
    urls = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        for c in group.get("contacts", []):
            if not isinstance(c, dict):
                continue
            if c.get("type") == "phone":
                value = phone(c.get("value", c.get("text", "")))
                if value:
                    phones.append(value)
            elif c.get("url"):
                if c.get("type") == "website" and (
                    not public_url(c["url"]) or urlsplit(c["url"]).hostname in {"link.2gis.ru", "2gis.ru"}
                ):
                    return None
                urls.append(c["url"])
            elif c.get("type") in {"website", "telegram", "max"}:
                # Не раскрываем redirect-ссылки link.2gis.ru и не считаем их отсутствием сайта.
                value = c.get("value", "")
                if public_url(value) and urlsplit(value).hostname != "link.2gis.ru":
                    urls.append(value)
                else:
                    return None
    tg, mx, web = contacts(urls)
    extra = item.get("name_ex") or {}
    description = str(item.get("description") or extra.get("extension") or "").strip()
    if not description:
        description = "; ".join(str(r.get("name", "")) for r in item.get("rubrics", []) if isinstance(r, dict))
    location = ", ".join(
        str(a.get("name", "")) for a in item.get("adm_div", []) if isinstance(a, dict) and a.get("type") == "city"
    )
    address = str(item.get("address_name", ""))
    if not location:
        location = str(item.get("full_address_name", ""))
    # Собственное название может уточнять импорт, но поисковый запрос не подставляется в описание.
    return Company(
        "2gis",
        identity,
        source_url,
        name,
        description,
        city if city_matches(city, location) else "",
        address,
        tuple(dict.fromkeys(phones)),
        tg,
        mx,
        web,
        "listed" if web else "not_listed",
        in_scope(city, location, name + " " + description, niche),
    )


def parse_yandex(item: dict, city: str, source_url: str, niche: str | None = None) -> Company | None:
    meta = item.get("CompanyMetaData")
    if not isinstance(meta, dict) or not meta.get("id") or not meta.get("name"):
        return None
    # Эта структура пригодна и для законно полученного JSON-экспорта; отсутствие Phones != отсутствие сайта.
    if "Phones" not in meta:
        return None
    phones = tuple(
        dict.fromkeys(
            v
            for p in meta.get("Phones", [])
            if isinstance(p, dict) and (v := phone(p.get("formatted", p.get("number", ""))))
        )
    )
    urls = [meta["url"]] if meta.get("url") else []
    for contact in meta.get("Links", []):
        if isinstance(contact, dict) and contact.get("href"):
            urls.append(contact["href"])
    tg, mx, web = contacts(urls)
    description = str(
        meta.get("description")
        or "; ".join(c.get("name", "") for c in meta.get("Categories", []) if isinstance(c, dict))
    )
    address = str(meta.get("address", ""))
    return Company(
        "yandex",
        str(meta["id"]),
        source_url,
        str(meta["name"]),
        description,
        city if city_matches(city, address) else "",
        address,
        phones,
        tg,
        mx,
        web,
        "listed" if web else "not_listed",
        in_scope(city, address, str(meta["name"]) + " " + description, niche),
    )


def parse_google(page: Page, city: str, url: str, niche: str | None = None) -> Company | None:
    identity = canonical("google", url, url)
    name = " ".join(page.title).strip()
    if not identity or not name or not {"phone", "address"} <= page.profile_markers:
        return None
    tg, mx, web = contacts(page.authority + page.links)
    # Ссылки навигации Google не считаются сайтом организации. Authority — явно указанное поле сайта.
    authority = []
    for value in page.authority:
        parsed = urlsplit(value)
        if parsed.hostname in {"www.google.com", "google.com"} and parsed.path == "/url":
            value = parse_qs(parsed.query).get("q", parse_qs(parsed.query).get("url", [""]))[0]
        if not public_url(value) or urlsplit(value).hostname in {"www.google.com", "google.com", "maps.google.com"}:
            return None
        authority.append(value)
    _, _, web = contacts(authority)
    phones = tuple(dict.fromkeys(p for value in page.phones if (p := phone(value))))
    return Company(
        "google",
        identity[0],
        identity[1],
        name,
        page.description or page.meta_description,
        city if city_matches(city, page.address) else "",
        page.address,
        phones,
        tg,
        mx,
        web,
        "listed" if web else "not_listed",
        in_scope(city, page.address, name + " " + (page.description or page.meta_description), niche),
    )


def parse_page(
    provider: str, city: str, url: str, html: str, *, niche: str | None = None
) -> tuple[tuple[Company, ...], tuple[str, ...]]:
    page = Page()
    page.feed(html)
    found = {}
    if provider == "google":
        item = parse_google(page, city, url, niche)
        if item:
            found[item.external_id] = item
    for root in json_roots(page):
        for item in walk(root):
            if not isinstance(item, dict):
                continue
            company = (
                parse_2gis(item, city, url, niche)
                if provider == "2gis"
                else parse_yandex(item, city, url, niche)
                if provider == "yandex"
                else None
            )
            if (
                provider == "2gis"
                and isinstance(item.get("org"), dict)
                and re.fullmatch(r"\d{10,}", str(item.get("id", "")))
            ):
                slug = item.get("city_alias")
                if slug == city_slug(city):
                    page.links.append(f"https://2gis.ru/{slug}/firm/{item['id']}")
            elif provider == "yandex" and isinstance(item.get("CompanyMetaData"), dict):
                identity = str(item["CompanyMetaData"].get("id", ""))
                if identity.isdigit():
                    page.links.append(f"https://yandex.ru/maps/org/{identity}/")
            if company:
                # Профили из рекомендаций сохраняются только после посещения собственной карточки.
                own = canonical(provider, url, url)
                if own and own[0] == company.external_id:
                    found[company.external_id] = company
    links = []
    for href in page.links:
        ref = canonical(provider, href, url)
        if ref and ref[1] not in links:
            links.append(ref[1])
    return tuple(found.values()), tuple(links)
