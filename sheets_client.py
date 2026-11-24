"""
Integração com Google Sheets via gspread + service account.
Todos os parceiros usam a aba única "CRM-B".
Filtragem por parceiro é feita pela coluna D (Parceiro responsável).
"""
import json
import logging
from datetime import datetime, date

import gspread
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_NAME = "CRM-B"


def _get_client() -> gspread.Client:
    """Cria client autenticado com service account."""
    creds_dict = json.loads(config.GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_sheet() -> gspread.Worksheet:
    """Abre a aba CRM-B."""
    client = _get_client()
    spreadsheet = client.open_by_key(config.SPREADSHEET_ID)
    return spreadsheet.worksheet(SHEET_NAME)


def add_or_update_row(partner_name: str, data: dict) -> tuple[str, int]:
    """
    Se a empresa já existe E pertence a esse parceiro, atualiza a linha.
    Se não existe, adiciona nova linha.
    Retorna: ("added"|"updated", número da linha)
    """
    sheet = _get_sheet()
    values = _data_to_row(data)
    all_rows = sheet.get_all_values()

    # Procura empresa existente DO MESMO parceiro (coluna A = empresa, coluna D = parceiro)
    for i, row in enumerate(all_rows[1:], start=2):  # pula header
        if row[0].strip().lower() == data["empresa"].strip().lower() and row[3] == partner_name:
            sheet.update(f"A{i}:H{i}", [values])
            return "updated", i

    # Empresa nova — adiciona no final
    sheet.append_row(values, table_range="A1")
    return "added", len(all_rows) + 1


def _data_to_row(data: dict) -> list:
    """Converte dict de dados pra lista de valores na ordem das colunas."""
    return [
        data.get("empresa", ""),
        data.get("site") or "",
        data.get("cargo") or "",
        data.get("parceiro", ""),
        data.get("status", ""),
        data.get("temperatura", ""),
        data.get("proximo_passo") or "",
        data.get("data_proxima_acao") or "",
    ]


def get_partner_summary(partner_name: str) -> dict:
    """
    Lê a aba CRM-B e retorna resumo APENAS das linhas do parceiro:
    total, quente, morno, frio, próxima ação.
    """
    sheet = _get_sheet()
    records = sheet.get_all_values()

    if len(records) <= 1:  # Só header
        return {"total": 0, "quente": 0, "morno": 0, "frio": 0, "proxima_acao": None}

    # Filtra só as linhas desse parceiro (coluna D = index 3)
    rows = [r for r in records[1:] if r[3] == partner_name]

    if not rows:
        return {"total": 0, "quente": 0, "morno": 0, "frio": 0, "proxima_acao": None}

    quente = sum(1 for r in rows if "Quente" in r[5])
    morno = sum(1 for r in rows if "Morno" in r[5])
    frio = sum(1 for r in rows if "Frio" in r[5])

    # Encontra próxima ação mais próxima
    proxima = None
    for r in rows:
        if r[7]:  # Coluna H = Data
            try:
                dt = datetime.strptime(r[7], "%d/%m/%Y")
                if proxima is None or dt < proxima[0]:
                    proxima = (dt, r[0], r[6])  # data, empresa, próximo_passo
            except ValueError:
                pass

    return {
        "total": len(rows),
        "quente": quente,
        "morno": morno,
        "frio": frio,
        "proxima_acao": proxima,
    }


def get_pending_followups() -> list[dict]:
    """
    Varre a aba CRM-B inteira e retorna empresas com data_proxima_acao
    igual a hoje ou atrasada. Usado pelo cron de follow-up.
    """
    today = date.today()
    sheet = _get_sheet()
    records = sheet.get_all_values()

    if len(records) <= 1:
        return []

    # Monta mapeamento reverso: nome do parceiro → telegram_id
    nome_to_tid = {}
    for tid, info in config.PARTNERS.items():
        nome_to_tid[info["nome"]] = tid

    followups = []
    for row in records[1:]:
        parceiro_nome = row[3]  # Coluna D = Parceiro responsável
        data_str = row[7]       # Coluna H = Data

        if not data_str or parceiro_nome not in nome_to_tid:
            continue

        try:
            dt = datetime.strptime(data_str, "%d/%m/%Y").date()
            if dt <= today:
                followups.append({
                    "telegram_id": nome_to_tid[parceiro_nome],
                    "nome": parceiro_nome,
                    "empresa": row[0],
                    "proximo_passo": row[6],
                    "data": data_str,
                    "atrasado": dt < today,
                })
        except ValueError:
            pass

    return followups
