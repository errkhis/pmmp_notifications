import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
from bs4 import BeautifulSoup


@dataclass
class Bidder:
    rank: int
    name: str
    admin_status: str
    financial_status: str
    price: Optional[float]
    technical_score: Optional[float] = None
    price_before_raw: str = ""
    price_after_raw: str = ""


@dataclass
class ConsultationData:
    reference: str
    object: str
    estimated_price: Optional[float]
    estimated_price_currency: str
    procedure: str
    category: str
    bidders: list[Bidder] = field(default_factory=list)
    technical_weight: Optional[float] = None
    financial_weight: Optional[float] = None
    lot_id: Optional[str] = None
    lot_label: Optional[str] = None
    lots: list["ConsultationData"] = field(default_factory=list)


@dataclass
class _ParsedBidderRow:
    rank: int
    name: str
    admin_status: str
    financial_status: str
    price_before_raw: str
    price_after_raw: str
    price_before: Optional[float]
    price_after: Optional[float]
    generic_price: Optional[float]
    technical_score: Optional[float]


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def scrape_consultation(url: str) -> ConsultationData:
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        response = client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        data = _build_consultation_data(url, soup)

        lot_options = _extract_lot_options(soup)
        lot_estimates = _fetch_lot_estimates(client, url)
        if len(lot_options) > 1:
            lots = []
            for lot_id, lot_label in lot_options:
                lot_soup = _fetch_lot_soup(client, url, soup, lot_id)
                lot_data = _build_consultation_data(url, lot_soup, lot_id, lot_label)
                if lot_id in lot_estimates:
                    lot_data.estimated_price, lot_data.estimated_price_currency = lot_estimates[lot_id]
                lots.append(lot_data)
            _attach_lot_results(data, lots)
        elif "1" in lot_estimates:
            data.estimated_price, data.estimated_price_currency = lot_estimates["1"]

        return data


def _attach_lot_results(data: ConsultationData, lots: list[ConsultationData]) -> None:
    # Prado lot callbacks can occasionally return an empty replacement payload.
    # When that happens, falling back to the base page is safer than treating all
    # lots as empty and blocking notifications entirely.
    non_empty_lots = [lot for lot in lots if lot.bidders]
    if not non_empty_lots:
        return
    data.lots = non_empty_lots
    data.bidders = [bidder for lot in non_empty_lots for bidder in lot.bidders]


def consultation_meta_from_url(url: str) -> tuple[Optional[str], Optional[str]]:
    ref_match = re.search(r"refConsultation=([^&]+)", url)
    org_match = re.search(r"orgAcronyme=([^&]+)", url)
    if not ref_match:
        return None, None
    return ref_match.group(1), org_match.group(1) if org_match else ""


def build_consultation_url(reference: str, org: str) -> str:
    url = (
        "https://www.marchespublics.gov.ma/index.php"
        f"?page=entreprise.SuiviConsultation&refConsultation={reference}"
    )
    if org:
        url += f"&orgAcronyme={org}"
    return url


def _build_consultation_data(
    url: str,
    soup: BeautifulSoup,
    lot_id: Optional[str] = None,
    lot_label: Optional[str] = None,
) -> ConsultationData:
    estimated_price, currency = _extract_estimated_price(soup)
    technical_weight, financial_weight = _extract_weights(soup)
    return ConsultationData(
        reference=_meta_from_url(url),
        object=_extract_object(soup),
        estimated_price=estimated_price,
        estimated_price_currency=currency,
        procedure=_extract_labeled_field(soup, r"proc[eé]dure"),
        category=_extract_labeled_field(soup, r"cat[eé]gorie"),
        bidders=_extract_bidders(soup),
        technical_weight=technical_weight,
        financial_weight=financial_weight,
        lot_id=lot_id,
        lot_label=lot_label,
    )


def _extract_lot_options(soup: BeautifulSoup) -> list[tuple[str, str]]:
    select = soup.find("select", id=re.compile(r"lotsDropDownList", re.I))
    if not select:
        return []
    lots = []
    for opt in select.find_all("option"):
        value = (opt.get("value") or "").strip()
        label = opt.get_text(" ", strip=True)
        if value and label:
            lots.append((value, label))
    return lots


def _fetch_lot_soup(
    client: httpx.Client,
    url: str,
    base_soup: BeautifulSoup,
    lot_id: str,
) -> BeautifulSoup:
    form = base_soup.find("form")
    select = base_soup.find("select", id=re.compile(r"lotsDropDownList", re.I))
    if not form or not select or not select.get("name"):
        return base_soup

    data = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        input_type = (inp.get("type") or "").lower()
        if name and input_type not in ("image", "submit", "button"):
            data[name] = inp.get("value", "")

    select_name = select["name"]
    data[select_name] = lot_id
    data["PRADO_CALLBACK_TARGET"] = select_name
    data["PRADO_CALLBACK_PARAMETER"] = lot_id
    data["PRADO_POSTBACK_TARGET"] = ""
    data["PRADO_POSTBACK_PARAMETER"] = ""

    action = form.get("action") or url
    action_url = str(httpx.URL(url).join(action))
    response = client.post(
        action_url,
        data=data,
        headers={
            **HEADERS,
            "X-Requested-With": "XMLHttpRequest",
            "X-Prototype-Version": "1.7",
            "Accept": "text/javascript, text/html, application/xml, text/xml, */*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    response.raise_for_status()
    return BeautifulSoup(response.text, "lxml")


def _fetch_lot_estimates(client: httpx.Client, url: str) -> dict[str, tuple[float, str]]:
    reference = _meta_from_url(url)
    org = _meta_from_url_param(url, "orgAcronyme") or _meta_from_url_param(url, "orgAccronyme")
    if not reference or not org:
        return {}

    popup_url = (
        "https://www.marchespublics.gov.ma/index.php"
        f"?page=commun.PopUpDetailLots&orgAccronyme={org}"
        f"&refConsultation={reference}&lang=fr"
    )
    try:
        response = client.get(popup_url)
        response.raise_for_status()
    except httpx.HTTPError:
        return {}

    soup = BeautifulSoup(response.text, "lxml")
    estimates: dict[str, tuple[float, str]] = {}
    for tag in soup.find_all(id=re.compile(r"repeaterLots_ctl(\d+).*panelReferentielZoneText", re.I)):
        text = tag.get_text(" ", strip=True)
        if not re.search(r"estimation", text, re.I):
            continue
        value = _parse_first_price(text)
        if value is None:
            continue
        idx_match = re.search(r"repeaterLots_ctl(\d+)", tag.get("id", ""), re.I)
        if not idx_match:
            continue
        lot_id = str(int(idx_match.group(1)) + 1)
        currency = "MAD TTC" if re.search(r"TTC", text, re.I) else "MAD"
        estimates[lot_id] = (value, currency)
    return estimates


def _extract_object(soup: BeautifulSoup) -> str:
    for tag in soup.find_all("span", id=re.compile(r"labelReferentielZoneText", re.I)):
        container = tag.find_parent()
        if container:
            context = container.get_text(separator=" ", strip=True)
            if re.search(r"objet", context, re.IGNORECASE):
                return tag.get_text(strip=True)

    for tag in soup.find_all(string=re.compile(r"\bobjet\b", re.IGNORECASE)):
        parent = tag.find_parent()
        if parent:
            nxt = parent.find_next_sibling()
            if nxt:
                return nxt.get_text(strip=True)
    return ""


def _extract_labeled_field(soup: BeautifulSoup, pattern: str) -> str:
    for tag in soup.find_all(string=re.compile(pattern, re.IGNORECASE)):
        parent = tag.find_parent()
        if parent:
            container = parent.find_parent()
            if container:
                value_span = container.find("span", id=re.compile(r"labelReferentielZoneText", re.I))
                if value_span:
                    return value_span.get_text(strip=True)
            nxt = parent.find_next_sibling()
            if nxt:
                value = nxt.get_text(strip=True)
                if value and value != ":":
                    return value
    return "N/A"


def _extract_estimated_price(soup: BeautifulSoup) -> tuple[Optional[float], str]:
    for tag in soup.find_all("span", id=re.compile(r"labelReferentielZoneText", re.I)):
        container = tag.find_parent()
        if not container:
            continue
        context = container.get_text(separator=" ", strip=True)
        if re.search(r"estimat|prix\s*estimatif|budget|montant", context, re.IGNORECASE):
            value = _parse_price_fr(tag.get_text(strip=True))
            if value and value > 100:
                currency = "MAD TTC" if "TTC" in context else ("MAD HT" if "HT" in context else "MAD")
                return value, currency
    return None, "MAD"


def _extract_weights(soup: BeautifulSoup) -> tuple[Optional[float], Optional[float]]:
    technical_weight = None
    financial_weight = None
    for tag in soup.find_all(string=re.compile(r"poids|pond[eé]ration|weight", re.IGNORECASE)):
        row = tag.find_parent("tr")
        if not row:
            continue
        cells = row.find_all(["td", "th"])
        for index, cell in enumerate(cells):
            cell_text = cell.get_text(strip=True).lower()
            if "tech" in cell_text and index + 1 < len(cells):
                value = _parse_price_fr(cells[index + 1].get_text(strip=True))
                if value is not None:
                    technical_weight = value
            elif "fin" in cell_text and index + 1 < len(cells):
                value = _parse_price_fr(cells[index + 1].get_text(strip=True))
                if value is not None:
                    financial_weight = value
    return technical_weight, financial_weight


def _extract_bidders(soup: BeautifulSoup) -> list[Bidder]:
    target_table = _find_bidder_table(soup)
    if not target_table:
        return []

    rows = target_table.find_all("tr")
    if len(rows) < 3:
        return []

    header_idx = 0
    for index, row in enumerate(rows):
        text = row.get_text().lower()
        if "entreprise" in text or "soumissionnaire" in text:
            header_idx = index
            break

    data_start = header_idx + 2
    subheader_row = rows[header_idx + 1] if header_idx + 1 < len(rows) else None
    columns = _infer_columns(rows[header_idx], subheader_row)

    parsed_rows: list[_ParsedBidderRow] = []
    for row_index, row in enumerate(rows[data_start:], start=1):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        texts = [cell.get_text(strip=True) for cell in cells]
        if len(texts) < 3:
            continue

        name = texts[columns["name"]] if columns["name"] < len(texts) else ""
        if not name or re.match(r"^\d+$", name) or name.lower() in ("total", ""):
            continue

        admin_status = texts[columns["admin"]] if columns["admin"] < len(texts) else ""
        financial_status = texts[columns["fin"]] if columns["fin"] < len(texts) else ""
        price_before_raw = texts[columns["price_before"]] if columns["price_before"] is not None and columns["price_before"] < len(texts) else ""
        price_after_raw = texts[columns["price_after"]] if columns["price_after"] is not None and columns["price_after"] < len(texts) else ""
        generic_price_raw = texts[columns["price"]] if columns["price"] is not None and columns["price"] < len(texts) else ""
        score_raw = texts[columns["score"]] if columns["score"] is not None and columns["score"] < len(texts) else ""

        price_after = _parse_price_fr(price_after_raw)
        price_before = _parse_price_fr(price_before_raw)
        generic_price = _parse_price_fr(generic_price_raw)
        score = _parse_price_fr(score_raw) if score_raw else None

        parsed_rows.append(
            _ParsedBidderRow(
                rank=row_index,
                name=name,
                admin_status=admin_status,
                financial_status=financial_status,
                price_before_raw=price_before_raw,
                price_after_raw=price_after_raw,
                price_before=price_before,
                price_after=price_after,
                generic_price=generic_price,
                technical_score=score if score and score <= 100 else None,
            )
        )

    use_after_prices = any(row.price_after is not None for row in parsed_rows)
    use_before_prices = not use_after_prices and any(row.price_before is not None for row in parsed_rows)

    bidders: list[Bidder] = []
    for row in parsed_rows:
        if use_after_prices:
            price = row.price_after
        elif use_before_prices:
            price = row.price_before
        else:
            price = row.generic_price

        bidders.append(
            Bidder(
                rank=row.rank,
                name=row.name,
                admin_status=row.admin_status,
                financial_status=row.financial_status,
                price=price,
                technical_score=row.technical_score,
                price_before_raw=row.price_before_raw,
                price_after_raw=row.price_after_raw,
            )
        )
    return bidders


def _find_bidder_table(soup: BeautifulSoup) -> Optional[BeautifulSoup]:
    for table in soup.find_all("table"):
        text = table.get_text().lower()
        if ("admissible" in text or "écartée" in text or "ecartee" in text) and "entreprise" in text:
            return table
    tables = soup.find_all("table")
    if tables:
        return max(tables, key=lambda table: len(table.find_all("tr")))
    return None


def _infer_columns(header_row, subheader_row) -> dict:
    columns = {
        "name": 0,
        "admin": 1,
        "fin": 2,
        "price": 3,
        "price_before": 3,
        "price_after": 4,
        "score": None,
    }
    if header_row is None:
        return columns

    headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]
    subheaders = [th.get_text(strip=True).lower() for th in subheader_row.find_all(["th", "td"])] if subheader_row else []

    for index, header in enumerate(headers):
        normalized = _norm(header)
        if any(key in normalized for key in ["entreprise", "soumissionnaire", "societe", "raison sociale"]):
            columns["name"] = index
        elif "admin" in normalized:
            columns["admin"] = index
        elif "financ" in normalized:
            columns["fin"] = index
        elif any(key in normalized for key in ["note", "score", "technique"]):
            columns["score"] = index

    generic_price_idx = None
    for index, header in enumerate(subheaders):
        normalized = _norm(header)
        if "apres" in normalized:
            columns["price_after"] = index
        elif "avant" in normalized:
            columns["price_before"] = index
        elif "prix" in normalized or "montant" in normalized or "offre" in normalized:
            generic_price_idx = index

    if generic_price_idx is not None:
        columns["price"] = generic_price_idx
    if columns["price"] is None:
        columns["price"] = 3
    return columns


def _parse_price_fr(text: str) -> Optional[float]:
    if not text:
        return None
    value = text.strip().replace("\xa0", " ").replace(" ", " ")
    value = re.sub(r"(MAD|DH|TTC|HT|Dhs?)\s*", "", value, flags=re.IGNORECASE).strip()
    if not value or value in {"-", "—", "N/A"}:
        return None
    value = value.replace(" ", "")
    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    elif "," in value:
        value = value.replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _parse_first_price(text: str) -> Optional[float]:
    for match in re.findall(r"\d[\d\s.\xa0 ]*,\d{2}", text):
        value = _parse_price_fr(match)
        if value is not None:
            return value
    return _parse_price_fr(text)


def _meta_from_url(url: str) -> str:
    match = re.search(r"refConsultation=(\w+)", url)
    return match.group(1) if match else "Unknown"


def _meta_from_url_param(url: str, name: str) -> Optional[str]:
    match = re.search(rf"[?&]{re.escape(name)}=([^&]+)", url)
    return match.group(1) if match else None


def _norm(text: str) -> str:
    return (
        text.strip()
        .lower()
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("â", "a")
        .replace("î", "i")
        .replace("ô", "o")
        .replace("û", "u")
        .replace("ç", "c")
    )
