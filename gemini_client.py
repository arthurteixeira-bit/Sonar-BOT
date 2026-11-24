"""
Integração com Gemini API — transcreve áudio E extrai dados num passo só.
Usa o free tier do Google AI Studio (15 req/min, 1500 req/dia).
"""
import json
import logging
from datetime import datetime

import google.generativeai as genai

logger = logging.getLogger(__name__)

# Model global — inicializado em configure()
_model = None


def configure(api_key: str):
    """Configura a API key do Gemini. Chamar uma vez na inicialização."""
    global _model
    genai.configure(api_key=api_key)
    _model = genai.GenerativeModel("gemini-2.0-flash")


def extract_from_audio(audio_path: str, mime_type: str = "audio/ogg") -> dict:
    """
    Recebe path de arquivo de áudio, manda pro Gemini, retorna dict estruturado.
    O Gemini transcreve E extrai os campos num passo só.
    """
    audio_file = genai.upload_file(path=audio_path, mime_type=mime_type)
    try:
        response = _model.generate_content([audio_file, _build_prompt()])
        return _parse_response(response.text)
    finally:
        # Limpa arquivo do Gemini (não deixa acumular)
        try:
            genai.delete_file(audio_file.name)
        except Exception:
            pass


def extract_from_text(text: str) -> dict:
    """
    Recebe texto do parceiro, manda pro Gemini, retorna dict estruturado.
    """
    prompt = f"{_build_prompt()}\n\nTexto do parceiro:\n{text}"
    response = _model.generate_content(prompt)
    return _parse_response(response.text)


def _build_prompt() -> str:
    hoje = datetime.now().strftime("%d/%m/%Y")
    return f"""Você é um assistente que processa relatos de reuniões comerciais de parceiros de vendas.

A partir do áudio ou texto, extraia as informações e retorne APENAS um JSON válido (sem markdown, sem ```):

{{
  "empresa": "nome da empresa",
  "site": "site da empresa ou null",
  "cargo": "cargo do decisor/contato ou null",
  "status": "um dos valores exatos: Novo lead | Contato feito | Reunião agendada | Reunião realizada | Proposta enviada | Negociando | Fechado | Perdido",
  "temperatura": "um dos valores exatos: 🔥 Quente | 🌡️ Morno | ❄️ Frio",
  "proximo_passo": "próximo passo acordado ou null",
  "data_proxima_acao": "DD/MM/AAAA ou null",
  "observacoes": "qualquer detalhe relevante ou null"
}}

Regras:
- "empresa" é OBRIGATÓRIO. Se não foi mencionada, retorne: {{"erro": "empresa_nao_mencionada"}}
- Infira o status pelo contexto do relato:
  - Fez reunião → "Reunião realizada"
  - Vai mandar proposta → "Proposta enviada"
  - Primeiro contato → "Contato feito"
  - Fechou negócio → "Fechado"
  - Não teve interesse → "Perdido"
  - Vai pensar / voltou sem definição → "Negociando"
- Infira a temperatura pelo tom:
  - Animado, positivo, interesse claro → 🔥 Quente
  - Neutro, vai pensar, sem urgência → 🌡️ Morno
  - Difícil, sem interesse, enrolando → ❄️ Frio
- Para datas relativas ("semana que vem", "segunda", "daqui 3 dias"), calcule a data real. Hoje é {hoje}.
- Retorne SOMENTE o JSON válido. Nada mais."""


def _parse_response(text: str) -> dict:
    """Parseia resposta do Gemini, limpando possível markdown."""
    text = text.strip()

    # Remove ```json ... ``` se o Gemini envolveu
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove primeira e última linha (``` markers)
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error(f"Gemini retornou JSON inválido: {text[:200]}")
        return {"erro": "json_invalido", "raw": text[:200]}
