"""
KBC Belgium — bank sync via GoCardless Bank Account Data (PSD2).
Credentials needed in Streamlit secrets: GOCARDLESS_ID, GOCARDLESS_KEY.
Free for personal use: https://bankaccountdata.gocardless.com

Flow:
  1. get_token()          → access_token
  2. create_requisition() → {"id": ..., "link": "https://..."}  (user opens link, authorizes KBC)
  3. get_accounts()       → ["account_id_1", ...]
  4. fetch_transactions() → [{"date", "amount", "description", "currency"}, ...]
"""
import requests

BASE = "https://bankaccountdata.gocardless.com/api/v2"
KBC_INSTITUTION = "KBC_KREDBEBB"

INCOME_KEYWORDS = [
    "SALARIS", "LOON", "BEZOLDIGING", "SALAIRE", "VERGOEDING",
    "INTERIMVERGOEDING", "NOMINA", "NOMINA", "INKOMEN",
]

CATEGORY_KEYWORDS = {
    "Comida":        ["CARREFOUR", "LIDL", "DELHAIZE", "ALDI", "COLRUYT", "SPAR",
                      "ALBERT HEIJN", "JUMBO", "OKAY", "MATCH", "INTERMARCHE", "BIOPLANET"],
    "Gasolina":      ["SHELL", "Q8", "TOTAL", "TEXACO", "ESSO", "BP", "FUEL", "BENZINE"],
    "Transporte":    ["NMBS", "SNCB", "DE LIJN", "TEC", "STIB", "MIVB", "PARKING", "VILLO"],
    "Suscripciones": ["NETFLIX", "SPOTIFY", "AMAZON", "DISNEY", "APPLE.COM",
                      "GOOGLE", "YOUTUBE", "PRIME VIDEO"],
    "Ocio":          ["CINEMA", "RESTAURANT", "CAFE ", "BAR ", "BOWLING", "ESCAPE",
                      "BIOSCOOP", "KEBAB", "FRITUUR"],
    "Salud":         ["APOTHEEK", "PHARMACIE", "DOKTER", "MEDISCH", "ZIEKTE", "TANDARTS"],
    "Hogar":         ["IKEA", "BRICO", "LEROY MERLIN", "GAMMA", "TORFS", "HEMA", "ZARA HOME"],
    "Ropa":          ["H&M", "ZARA", "PRIMARK", "C&A", "PULL&BEAR", "BERSHKA"],
    "Formación":     ["UDEMY", "COURSERA", "PLURALSIGHT", "LINKEDIN PREMIUM"],
}


def get_token(secret_id: str, secret_key: str) -> str:
    """Returns access_token string. Raises on failure."""
    resp = requests.post(
        f"{BASE}/token/new/",
        json={"secret_id": secret_id, "secret_key": secret_key},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access"]


def create_requisition(access_token: str, redirect_uri: str) -> dict:
    """
    Create a KBC bank connection.
    Returns {"id": requisition_id, "link": oauth_url_for_user}.
    """
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    agreement_resp = requests.post(
        f"{BASE}/agreements/enduser/",
        headers=headers,
        json={
            "institution_id": KBC_INSTITUTION,
            "max_historical_days": 90,
            "access_valid_for_days": 30,
            "access_scope": ["balances", "details", "transactions"],
        },
        timeout=15,
    )
    agreement_resp.raise_for_status()
    agreement_id = agreement_resp.json()["id"]

    req_resp = requests.post(
        f"{BASE}/requisitions/",
        headers=headers,
        json={
            "redirect":        redirect_uri,
            "institution_id":  KBC_INSTITUTION,
            "agreement":       agreement_id,
            "reference":       "kbc-geostyn",
            "user_language":   "NL",
        },
        timeout=15,
    )
    req_resp.raise_for_status()
    data = req_resp.json()
    return {"id": data["id"], "link": data["link"]}


def get_accounts(access_token: str, requisition_id: str) -> list[str]:
    """Returns list of account IDs after user has authorized."""
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(f"{BASE}/requisitions/{requisition_id}/", headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json().get("accounts", [])


def fetch_transactions(access_token: str, account_id: str, date_from: str, date_to: str) -> list[dict]:
    """Returns list of transaction dicts: {date, amount, description, currency}."""
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(
        f"{BASE}/accounts/{account_id}/transactions/",
        params={"date_from": date_from, "date_to": date_to},
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    booked = resp.json().get("transactions", {}).get("booked", [])
    result = []
    for t in booked:
        amount = float((t.get("transactionAmount") or {}).get("amount", 0))
        description = (
            t.get("remittanceInformationUnstructured") or
            t.get("remittanceInformationStructured")   or
            t.get("creditorName") or
            t.get("debtorName")   or
            "Transacción"
        )
        result.append({
            "date":        t.get("bookingDate", ""),
            "amount":      amount,
            "description": description.strip(),
            "currency":    (t.get("transactionAmount") or {}).get("currency", "EUR"),
        })
    return result


def auto_categorize(description: str) -> tuple[str, str]:
    """Returns (table: 'gastos'|'ingresos', category_name)."""
    upper = description.upper()
    for kw in INCOME_KEYWORDS:
        if kw in upper:
            return "ingresos", "Nómina"
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in upper:
                return "gastos", category
    return "gastos", "Extra"
