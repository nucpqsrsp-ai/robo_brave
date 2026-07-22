"""
ROBÔ DE AUTOMAÇÃO — PREENCHIMENTO DE FORMULÁRIOS DE FISCALIZAÇÃO
=================================================================
Sistema: AngularJS rodando em localhost:4444
Automação: Selenium + ChromeDriver (Brave Browser)

FLUXO POR PDF:
  1.  Lê e sanitiza os dados do PDF
  2.  Acessa localhost:4444/#!/minhapagina/fiscalizacao
  3.  Localiza o CNPJ na lista e clica em "Gerenciar Fiscalização"
  4.  Clica em "Auto de Fiscalização"
  5.  Marca o checkbox "Equipes"
  6.  Preenche sede, aluguel, funcionários, faturamento
  7.  Seleciona "OUTRO COLABORADOR" e preenche dados pessoais
  8.  Para cada inciso presente: abre embasamento, seleciona
      "Nota Fiscal", cola descrição e clica em "Incluir"
  9.  Preenche "Emprego dos produtos químicos" e "Histórico"
  10. Marca "Não Houve" em Apreensão e "Não" em Recusa
  11. Clica em "Salvar" → nova tela
  12. Clica em "Voltar" → volta à lista de CNPJs
  13. Repete para o próximo PDF

PRÉ-REQUISITOS:
  1. Abra o Brave com a flag de depuração remota usando o atalho:
         "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222
  2. Faça login no sistema manualmente
  3. Navegue até a tela da lista de CNPJs
  4. Rode este script:
         python robo_automacao_v4.py
     Ou para testar um PDF sem acionar o sistema:
         python robo_automacao_v4.py testar caminho/formulario.pdf

DEPENDÊNCIAS:
  pip install pypdf selenium webdriver-manager pyperclip pdfplumber
"""

import re
import sys
import time
import logging
import pyperclip
from pathlib import Path
from datetime import datetime

from pypdf import PdfReader
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

# ── Alteração: imports trocados de Edge para Chrome (compatível com Brave) ──
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
# ────────────────────────────────────────────────────────────────────────────

from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, ElementNotInteractableException
)

# ─────────────────────────────────────────────────────────────
# CONFIGURAÇÃO — AJUSTE ANTES DE RODAR
# ─────────────────────────────────────────────────────────────
CONFIG = {
    # Pastas
    "pasta_pdfs":        r"C:\Automacao\PDFs_entrada",
    "pasta_concluidos":  r"C:\Automacao\PDFs_concluidos",
    "pasta_erros":       r"C:\Automacao\PDFs_erros",

    # URL base do sistema
    "url_base": "http://localhost:4444/#!/minhapagina/fiscalizacao",

    # Porta de depuração remota (não altere)
    "debug_port": "localhost:9222",

    # ── Alteração: caminho do executável do Brave ──────────────────────────
    # Se o Brave estiver instalado em outro local, ajuste este caminho.
    "brave_path": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    # ────────────────────────────────────────────────────────────────────────

    # Tempos de espera em segundos
    "espera_elemento":  10,    # timeout para localizar elementos
    "espera_curta":     0.5,   # pausa após ações simples
    "espera_media":     1.0,   # pausa após navegação entre telas
    "espera_longa":     2.0,   # pausa após ações que carregam dados
}

# ─────────────────────────────────────────────────────────────
# LOG
# ─────────────────────────────────────────────────────────────
Path(CONFIG["pasta_pdfs"]).mkdir(parents=True, exist_ok=True)
log_path = (
    Path(CONFIG["pasta_pdfs"])
    / f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# MÓDULO 1 — LEITURA DO PDF
# ─────────────────────────────────────────────────────────────
def ler_pdf(caminho_pdf: Path) -> dict:
    """
    Lê todos os campos do PDF preenchido.
    Retorna dicionário {nome_campo: valor}.
    Checkboxes são normalizados para True/False.
    Se o PDF não tiver campos detectáveis (salvo incorretamente),
    usa OCR via pdfplumber como fallback automático.
    """
    reader = PdfReader(str(caminho_pdf))
    campos = {}

    if reader.get_fields():
        for nome, campo in reader.get_fields().items():
            valor = campo.get("/V", "")
            if str(valor) in ("/Yes", "/On", "Yes"):
                valor = True
            elif str(valor) in ("/Off", "/No", "No", ""):
                valor = False
            else:
                valor = str(valor).replace("/", "").strip()
            campos[nome] = valor
        log.info(f"  PDF lido: {len(campos)} campos encontrados")
    else:
        log.warning(f"  PDF sem campos detectáveis — tentando OCR via pdfplumber...")
        campos = ler_pdf_ocr(caminho_pdf)
        log.info(f"  OCR concluído: {len(campos)} campos extraídos")

    return campos


def ler_pdf_ocr(caminho_pdf: Path) -> dict:
    """
    Fallback para PDFs sem campos editáveis (salvos pelo navegador).
    Usa pdfplumber para extrair texto com coordenadas precisas (x, y),
    emparelhando cada rótulo com o valor na linha imediatamente abaixo,
    dentro das bordas horizontais conhecidas do formulário.
    """
    try:
        import pdfplumber
    except ImportError:
        log.error("  pdfplumber não instalado. Adicione nas dependências.")
        return {}

    CAMPOS_P1 = [
        ("cnpj",              38.4,   39.7,  220.0),
        ("nome_empresa",      38.4,  220.0,  600.0),
        ("endereco_empresa",  171.6,  39.7,  600.0),
        ("sede_tipo",         234.0,  39.7,  290.0),
        ("aluguel_valor",     234.0, 316.0,  600.0),
        ("num_funcionarios",  296.4,  39.7,  205.0),
        ("faturamento_anual", 296.4, 235.0,  600.0),
        ("nome_completo",     387.1,  39.7,  600.0),
        ("cpf",               449.4,  39.7,  205.0),
        ("identidade",        449.4, 205.0,  375.0),
        ("orgao_emissor",     449.4, 375.0,  600.0),
        ("cargo",             511.8,  39.7,  600.0),
        ("cep",               574.2,  39.7,  148.0),
        ("endereco",          574.2, 148.0,  600.0),
        ("numero",            636.5,  39.7,  120.0),
        ("complemento",       636.5, 120.0,  290.0),
        ("bairro",            636.5, 290.0,  600.0),
        ("uf",                698.9,  39.7,  105.0),
        ("municipio",         698.9, 105.0,  600.0),
        ("telefone",          761.2,  39.7,  205.0),
        ("celular",           761.2, 205.0,  375.0),
        ("email",             761.2, 375.0,  600.0),
    ]

    campos = {
        "cnpj": "", "nome_empresa": "", "endereco_empresa": "",
        "sede_tipo": "", "aluguel_valor": "", "num_funcionarios": "",
        "faturamento_anual": "", "nome_completo": "", "cpf": "",
        "identidade": "", "orgao_emissor": "", "cargo": "", "cep": "",
        "endereco": "", "numero": "", "complemento": "", "bairro": "",
        "uf": "", "municipio": "", "telefone": "", "celular": "",
        "email": "", "inciso_iii": False, "embasamento_iii": "",
        "inciso_v": False, "embasamento_v": "", "inciso_vi": False,
        "embasamento_vi": "", "emprego_quimicos": "", "historico": "",
    }

    def extrair_campo(words, label_top, x0, x1, v_min=8, v_max=30):
        val = []
        for w in words:
            if label_top + v_min <= w['top'] <= label_top + v_max:
                if x0 - 2 <= w['x0'] < x1:
                    val.append(w['text'])
        return " ".join(val).strip()

    def encontrar_top_rotulo(words, texto_rotulo):
        texto_lower = texto_rotulo.lower().strip()
        for w in words:
            if texto_lower in w['text'].lower():
                return w['top']
        linhas = {}
        for w in words:
            top_key = round(w['top'])
            if top_key not in linhas:
                linhas[top_key] = []
            linhas[top_key].append(w['text'])
        for top_key, palavras in linhas.items():
            if texto_lower in " ".join(palavras).lower():
                return float(top_key)
        return None

    try:
        with pdfplumber.open(str(caminho_pdf)) as pdf:

            if len(pdf.pages) >= 1:
                words1 = pdf.pages[0].extract_words(x_tolerance=3, y_tolerance=3)
                for chave, top, x0, x1 in CAMPOS_P1:
                    val = extrair_campo(words1, top, x0, x1)
                    if val:
                        campos[chave] = val

            if len(pdf.pages) >= 2:
                words2 = pdf.pages[1].extract_words(x_tolerance=3, y_tolerance=3)

                top_eq   = encontrar_top_rotulo(words2, "Emprego dos produtos")
                top_hist = encontrar_top_rotulo(words2, "Histórico")

                tops_incisos = []
                for inciso, chave_chk, chave_emb in [
                    ("III", "inciso_iii", "embasamento_iii"),
                    ("V",   "inciso_v",   "embasamento_v"),
                    ("VI",  "inciso_vi",  "embasamento_vi"),
                ]:
                    top_bloco = encontrar_top_rotulo(words2, f"INCISO {inciso}")
                    top_emb   = encontrar_top_rotulo(words2, f"Embasamento do Inciso {inciso}")
                    tops_incisos.append((inciso, chave_chk, chave_emb, top_bloco, top_emb))

                IGNORAR_EMB = {
                    "embasamento", "do", "inciso", "iii", "v", "vi",
                    "(se", "aplicável)", "possui", "inciso?"
                }
                for idx, (inciso, chave_chk, chave_emb, top_bloco, top_emb) in enumerate(tops_incisos):
                    if not top_emb:
                        continue
                    proximos = [tops_incisos[j][3] for j in range(idx+1, len(tops_incisos)) if tops_incisos[j][3]]
                    limite_inf = proximos[0] - 2 if proximos else (top_eq - 2 if top_eq else top_emb + 150)
                    emb_words = [
                        w['text'] for w in words2
                        if top_emb + 8 <= w['top'] < limite_inf
                        and 39.7 <= w['x0'] <= 600.0
                        and w['text'].lower() not in IGNORAR_EMB
                    ]
                    emb_texto = " ".join(emb_words).strip()
                    if emb_texto:
                        campos[chave_chk] = True
                        campos[chave_emb] = emb_texto

                if top_eq:
                    limite_inf_eq = (top_hist - 5) if top_hist and top_hist > top_eq else (top_eq + 80)
                    eq_words = [
                        w['text'] for w in words2
                        if top_eq + 8 <= w['top'] < limite_inf_eq
                        and 39.7 <= w['x0'] <= 600.0
                        and w['text'].lower() not in {
                            "emprego", "dos", "produtos", "químicos",
                            "histórico", "historico", "*"
                        }
                    ]
                    campos["emprego_quimicos"] = " ".join(eq_words).strip()

                if top_hist:
                    hist_words = [
                        w['text'] for w in words2
                        if top_hist + 8 <= w['top'] <= 800
                        and 39.7 <= w['x0'] <= 600.0
                        and w['text'].lower() not in {
                            "histórico", "historico", "documento",
                            "confidencial", "uso", "restrito", "página", "*"
                        }
                    ]
                    campos["historico"] = " ".join(hist_words).strip()

        log.info("  pdfplumber extraiu os campos com sucesso.")
    except Exception as e:
        log.error(f"  Falha na extração via pdfplumber: {e}")

    return campos


# ─────────────────────────────────────────────────────────────
# MÓDULO 2 — SANITIZAÇÃO
# ─────────────────────────────────────────────────────────────
def sanitizar_dados(dados: dict) -> dict:
    """
    Limpa os dados extraídos do PDF antes de enviar ao sistema.
    - Campos numéricos: remove tudo que não for dígito
    - Campos monetários: remove R$, pontos de milhar
    - CNPJ: remove formatação e valida 14 dígitos
    - Campos de texto: strip de espaços extras
    """
    dados_limpos = dict(dados)

    CAMPOS_SO_DIGITOS = [
        "num_funcionarios", "cpf", "cep",
        "numero", "telefone", "celular",
    ]
    for campo in CAMPOS_SO_DIGITOS:
        original = str(dados_limpos.get(campo, ""))
        limpo = re.sub(r"\D", "", original)
        if limpo != original and original not in ("", "False"):
            log.warning(f"  Sanitizado [{campo}]: '{original}' → '{limpo}'")
        dados_limpos[campo] = limpo

    cnpj_original = str(dados_limpos.get("cnpj", ""))
    cnpj_limpo = re.sub(r"\D", "", cnpj_original)
    if cnpj_limpo != cnpj_original and cnpj_original not in ("", "False"):
        log.warning(f"  Sanitizado [cnpj]: '{cnpj_original}' → '{cnpj_limpo}'")
    if cnpj_limpo and len(cnpj_limpo) != 14:
        log.warning(f"  CNPJ suspeito — {len(cnpj_limpo)} dígitos: '{cnpj_limpo}'")
    dados_limpos["cnpj"] = cnpj_limpo

    for campo in ["aluguel_valor", "faturamento_anual"]:
        original = str(dados_limpos.get(campo, ""))
        limpo = re.sub(r"[^\d,]", "", original.replace("R$", "").strip())
        if limpo != original and original not in ("", "False"):
            log.warning(f"  Sanitizado [{campo}]: '{original}' → '{limpo}'")
        dados_limpos[campo] = limpo

    CAMPOS_TEXTO = [
        "nome_completo", "identidade", "orgao_emissor", "cargo",
        "endereco_empresa", "endereco", "complemento", "bairro",
        "municipio", "email", "nome_empresa", "uf",
        "embasamento_iii", "embasamento_v", "embasamento_vi",
        "emprego_quimicos", "historico",
    ]
    for campo in CAMPOS_TEXTO:
        valor = dados_limpos.get(campo)
        if isinstance(valor, str):
            dados_limpos[campo] = valor.strip()

    for campo in ["uf", "municipio"]:
        valor = dados_limpos.get(campo)
        if isinstance(valor, str):
            dados_limpos[campo] = valor.strip().upper()

    return dados_limpos


# ─────────────────────────────────────────────────────────────
# MÓDULO 3 — CONEXÃO COM O BRAVE
# ─────────────────────────────────────────────────────────────
def conectar_brave() -> webdriver.Chrome:
    """
    Conecta ao Brave já aberto com --remote-debugging-port=9222.
    Usa webdriver.Chrome pois o Brave é baseado em Chromium.
    O chromedriver precisa estar na mesma pasta do script (ou no PATH).
    """
    import os

    options = Options()
    options.add_experimental_option("debuggerAddress", CONFIG["debug_port"])

    # Informa ao Selenium onde está o executável do Brave
    options.binary_location = CONFIG["brave_path"]

    # Localiza o chromedriver embutido pelo PyInstaller ou na pasta do script
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    driver_path = os.path.join(base, "chromedriver.exe")

    try:
        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=options)
        log.info("Conectado ao Brave com sucesso.")
        return driver
    except Exception as e:
        log.error(f"Não foi possível conectar ao Brave: {e}")
        log.error(
            "Certifique-se de que o Brave foi aberto com o atalho:\n"
            '  "C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe"'
            " --remote-debugging-port=9222\n"
            "e que você fez login no sistema."
        )
        raise


def esperar(driver, by, seletor, timeout=None) -> object:
    """Aguarda um elemento ficar visível e retorna ele."""
    t = timeout or CONFIG["espera_elemento"]
    return WebDriverWait(driver, t).until(
        EC.visibility_of_element_located((by, seletor))
    )


def esperar_clicavel(driver, by, seletor, timeout=None) -> object:
    """Aguarda um elemento ficar clicável e retorna ele."""
    t = timeout or CONFIG["espera_elemento"]
    return WebDriverWait(driver, t).until(
        EC.element_to_be_clickable((by, seletor))
    )


def colar(driver, elemento, texto: str):
    """
    Cola um texto em um elemento usando clipboard.
    Mais confiável que send_keys para textos longos com acentos.
    """
    if not texto:
        return
    elemento.click()
    driver.execute_script("arguments[0].value = '';", elemento)
    pyperclip.copy(str(texto))
    elemento.send_keys(Keys.CONTROL, "v")
    time.sleep(CONFIG["espera_curta"])


def preencher_por_label(driver, texto_label: str, valor: str):
    """
    Localiza um input/textarea pelo texto do label associado e preenche.
    Usado para campos com id dinâmico.
    """
    if not valor:
        return
    try:
        campo = esperar(driver, By.XPATH,
                        f"//div[contains(@class,'form-group')]"
                        f"[.//label[contains(normalize-space(),'{texto_label}')]]"
                        f"//*[self::input or self::textarea]")
        campo.click()
        campo.clear()
        colar(driver, campo, valor)
    except TimeoutException:
        log.warning(f"  Campo '{texto_label}' não encontrado na página.")


# ─────────────────────────────────────────────────────────────
# MÓDULO 4 — NAVEGAÇÃO E PREENCHIMENTO
# ─────────────────────────────────────────────────────────────
def aguardar_sem_overlay(driver, timeout=20):
    """Aguarda o overlay de carregamento desaparecer."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located(
                (By.CSS_SELECTOR, "div.block-ui-overlay")
            )
        )
    except TimeoutException:
        log.warning("  Overlay ainda presente — removendo via JavaScript.")
        driver.execute_script(
            "var el = document.querySelector('div.block-ui-overlay');"
            "if(el) el.parentNode.removeChild(el);"
        )
    time.sleep(CONFIG["espera_media"])


def clicar_js(driver, elemento):
    """Clica em um elemento via JavaScript, ignorando overlays."""
    driver.execute_script("arguments[0].click();", elemento)


def localizar_cnpj_e_gerenciar(driver, cnpj: str) -> bool:
    """
    Percorre a lista de CNPJs, localiza o correspondente ao PDF
    e clica em "Gerenciar Fiscalização".
    """
    log.info(f"  Procurando CNPJ {cnpj} na lista...")

    cnpj_fmt = (
        f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}"
        f"/{cnpj[8:12]}-{cnpj[12:14]}"
    )

    try:
        celula = esperar(
            driver, By.XPATH,
            f"//td[contains(@class,'ng-binding') and "
            f"normalize-space(text())='{cnpj_fmt}']"
        )
        linha = celula.find_element(By.XPATH, "./ancestor::tr")
        btn_gerenciar = linha.find_element(
            By.XPATH,
            ".//button[@uib-tooltip='Gerenciar Fiscalização'] | "
            ".//a[@uib-tooltip='Gerenciar Fiscalização']"
        )

        aguardar_sem_overlay(driver)
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", btn_gerenciar
        )
        time.sleep(CONFIG["espera_curta"])

        try:
            btn_gerenciar.click()
        except Exception:
            log.warning("  Clique normal bloqueado — usando JavaScript.")
            clicar_js(driver, btn_gerenciar)

        time.sleep(CONFIG["espera_longa"])
        log.info(f"  CNPJ encontrado. Clicou em 'Gerenciar Fiscalização'.")
        return True

    except TimeoutException:
        log.error(f"  CNPJ {cnpj_fmt} não encontrado na lista.")
        return False
    except NoSuchElementException:
        log.error(f"  Botão 'Gerenciar Fiscalização' não encontrado na linha do CNPJ.")
        return False


def clicar_auto_fiscalizacao(driver):
    """Clica no botão 'Auto de Fiscalização'."""
    log.info("  Clicando em 'Auto de Fiscalização'...")
    btn = esperar_clicavel(
        driver, By.XPATH,
        "//a[contains(@ng-click,\"navigateTo('app.gerenciarAutoFiscalizacao')\")]"
    )
    btn.click()
    time.sleep(CONFIG["espera_longa"])


def marcar_equipes(driver):
    """Marca o checkbox 'Equipes'."""
    log.info("  Marcando 'Equipes'...")
    checkbox = esperar_clicavel(
        driver, By.XPATH,
        "//input[@type='checkbox' and contains(@ng-click,'selectedAll')]"
    )
    if not checkbox.is_selected():
        checkbox.click()
    time.sleep(CONFIG["espera_media"])


def preencher_sede(driver, dados: dict):
    """Seleciona sede própria ou alugada e preenche o valor do aluguel."""
    sede = str(dados.get("sede_tipo", "")).strip()
    log.info(f"  Sede: {sede}")

    if sede == "Alugada":
        radio = esperar_clicavel(driver, By.ID, "tipoSede2")
        radio.click()
        time.sleep(CONFIG["espera_media"])
        preencher_por_label(driver, "Valor do Aluguel",
                            dados.get("aluguel_valor", ""))
    else:
        radio = esperar_clicavel(driver, By.ID, "tipoSede1")
        radio.click()

    time.sleep(CONFIG["espera_curta"])
    preencher_por_label(driver, "Nº de funcionários",
                        dados.get("num_funcionarios", ""))
    preencher_por_label(driver, "Faturamento Anual Bruto",
                        dados.get("faturamento_anual", ""))


def selecionar_outro_colaborador(driver):
    """Seleciona o radio 'OUTRO COLABORADOR' (value='F')."""
    log.info("  Selecionando 'OUTRO COLABORADOR'...")
    radio = esperar_clicavel(
        driver, By.XPATH,
        "//input[@type='radio' and @ng-value=\"'F'\"]"
    )
    radio.click()
    time.sleep(CONFIG["espera_media"])


def preencher_dados_pessoais(driver, dados: dict):
    """Preenche todos os campos de dados pessoais do responsável."""
    log.info("  Preenchendo dados pessoais...")

    def preencher_id(field_id: str, valor: str):
        if not valor:
            return
        try:
            campo = esperar_clicavel(driver, By.ID, field_id)
            campo.click()
            campo.clear()
            colar(driver, campo, valor)
        except TimeoutException:
            log.warning(f"  Campo id='{field_id}' não encontrado.")

    def preencher_ng(ng_model: str, valor: str):
        if not valor:
            return
        try:
            campo = esperar_clicavel(
                driver, By.XPATH,
                f"//*[@ng-model='{ng_model}']"
            )
            campo.click()
            campo.clear()
            colar(driver, campo, valor)
        except TimeoutException:
            log.warning(f"  Campo ng-model='{ng_model}' não encontrado.")

    preencher_id("nome",        dados.get("nome_completo", ""))
    preencher_id("cpf",         dados.get("cpf", ""))
    preencher_id("identidade",  dados.get("identidade", ""))
    preencher_id("cargo",       dados.get("cargo", ""))
    preencher_por_label(driver, "CEP", dados.get("cep", ""))
    time.sleep(CONFIG["espera_media"])
    preencher_id("endereco",    dados.get("endereco", ""))
    preencher_ng(
        "$ctrl.representanteFuncionarioAuto.endereco.numero",
        dados.get("numero", "")
    )
    preencher_ng(
        "$ctrl.representanteFuncionarioAuto.endereco.complementar",
        dados.get("complemento", "") or ""
    )
    preencher_id("bairro",      dados.get("bairro", ""))

    try:
        uf_select = Select(esperar_clicavel(driver, By.ID, "uf"))
        uf_select.select_by_value(str(dados.get("uf", "")).upper())
        time.sleep(CONFIG["espera_curta"])
    except Exception:
        log.warning("  Dropdown UF não encontrado ou valor inválido.")

    try:
        mun_select = Select(esperar_clicavel(driver, By.ID, "municipio"))
        mun_select.select_by_visible_text(str(dados.get("municipio", "")))
        time.sleep(CONFIG["espera_curta"])
    except Exception:
        log.warning("  Dropdown Município não encontrado ou valor inválido.")

    preencher_id("telefone",    dados.get("telefone", "") or "")
    preencher_id("celular",     dados.get("celular", ""))
    preencher_id("email",       dados.get("email", ""))


def preencher_incisos(driver, dados: dict):
    """
    Para cada inciso marcado no PDF:
      a) Clica em "Editar/Adicionar embasamento"
      b) Seleciona o inciso no dropdown "Dispositivo Legal"
      c) Seleciona "Nota Fiscal" no dropdown "Tipo"
      d) Cola a descrição no campo "Descrição"
      e) Clica em "Incluir"
    """
    log.info("  Verificando incisos...")

    INCISOS = [
        ("iii", "III", "Inciso III"),
        ("v",   "V",   "Inciso V"),
        ("vi",  "VI",  "Inciso VI"),
    ]

    for codigo, rotulo, dispositivo_legal in INCISOS:
        tem = dados.get(f"inciso_{codigo}", False)
        emb = str(dados.get(f"embasamento_{codigo}", "")).strip()

        if not tem or not emb:
            log.info(f"  Inciso {rotulo}: não marcado, pulando.")
            continue

        botoes_emb = driver.find_elements(
            By.XPATH,
            "//button[@uib-tooltip='Editar/Adicionar embasamento']"
        )
        if not botoes_emb:
            log.warning(f"  Inciso {rotulo}: nenhum botão de embasamento encontrado.")
            continue

        idx = ["iii", "v", "vi"].index(codigo)
        if idx >= len(botoes_emb):
            log.warning(f"  Inciso {rotulo}: índice {idx} fora do range ({len(botoes_emb)} botões).")
            continue

        log.info(f"  Preenchendo Inciso {rotulo}...")
        botoes_emb[idx].click()
        time.sleep(CONFIG["espera_longa"])

        try:
            select_disp = Select(esperar_clicavel(
                driver, By.XPATH,
                "//select[@ng-model='$ctrl.infracao.inciso']"
            ))
            select_disp.select_by_visible_text(dispositivo_legal)
            time.sleep(CONFIG["espera_curta"])
            log.info(f"    Dispositivo Legal: '{dispositivo_legal}' selecionado.")
        except Exception as e:
            log.warning(f"  Dropdown 'Dispositivo Legal' não encontrado para Inciso {rotulo}: {e}")

        try:
            select_tipo = Select(esperar_clicavel(
                driver, By.XPATH,
                "//select[@ng-model='$ctrl.infracao.embasamento']"
            ))
            select_tipo.select_by_visible_text("Nota Fiscal")
            time.sleep(CONFIG["espera_curta"])
            log.info(f"    Tipo: 'Nota Fiscal' selecionado.")
        except Exception as e:
            log.warning(f"  Dropdown 'Tipo' não encontrado para Inciso {rotulo}: {e}")

        try:
            campo_desc = esperar_clicavel(
                driver, By.XPATH,
                "//textarea[@ng-model='$ctrl.infracao.descricao']"
            )
            campo_desc.click()
            campo_desc.clear()
            colar(driver, campo_desc, emb)
            log.info(f"    Descrição colada.")
        except Exception as e:
            log.warning(f"  Campo 'Descrição' não encontrado para Inciso {rotulo}: {e}")

        try:
            btn_incluir = esperar_clicavel(
                driver, By.XPATH,
                "//a[contains(@class,'btn') and normalize-space(text())='Incluir']"
            )
            btn_incluir.click()
            time.sleep(CONFIG["espera_longa"])
            log.info(f"  Inciso {rotulo} incluído com sucesso.")
        except Exception as e:
            log.warning(f"  Botão 'Incluir' não encontrado para Inciso {rotulo}: {e}")


def preencher_campos_tecnicos(driver, dados: dict):
    """Preenche Emprego dos produtos químicos e Histórico."""
    log.info("  Preenchendo campos técnicos...")

    try:
        campo_eq = esperar_clicavel(
            driver, By.XPATH,
            "//textarea[@ng-model='$ctrl.auto.empregoProdutos']"
        )
        campo_eq.click()
        campo_eq.clear()
        colar(driver, campo_eq, dados.get("emprego_quimicos", ""))
    except TimeoutException:
        log.warning("  Campo 'Emprego dos produtos químicos' não encontrado.")

    try:
        campo_hist = esperar_clicavel(
            driver, By.XPATH,
            "//textarea[@ng-model='$ctrl.auto.historicoObservacoesMedidas']"
        )
        campo_hist.click()
        campo_hist.clear()
        colar(driver, campo_hist, dados.get("historico", ""))
    except TimeoutException:
        log.warning("  Campo 'Histórico' não encontrado.")


def marcar_apreensao_e_recusa(driver):
    """Marca 'Não Houve' em Apreensão e 'Não' em Recusa."""
    log.info("  Marcando Apreensão e Recusa...")

    try:
        chk = esperar_clicavel(
            driver, By.XPATH,
            "//input[@type='checkbox' and "
            "@ng-model='$ctrl.naoHouveApreensaoDepositoColeta']"
        )
        if not chk.is_selected():
            chk.click()
        time.sleep(CONFIG["espera_curta"])
    except TimeoutException:
        log.warning("  Checkbox 'Não Houve Apreensão' não encontrado.")

    try:
        radio = esperar_clicavel(driver, By.ID, "recusaAssinaturaNao")
        radio.click()
        time.sleep(CONFIG["espera_curta"])
    except TimeoutException:
        log.warning("  Radio 'Recusa Não' não encontrado.")


def salvar_e_voltar(driver):
    """Clica em Salvar e depois em Voltar, com retry robusto."""
    log.info("  Salvando...")
    aguardar_sem_overlay(driver)
    btn_salvar = esperar_clicavel(
        driver, By.XPATH,
        "//a[contains(@class,'btn-success') and "
        "contains(@ng-click,'$ctrl.salvar()') and "
        "normalize-space(text())='Salvar']"
    )
    try:
        btn_salvar.click()
    except Exception:
        clicar_js(driver, btn_salvar)
    time.sleep(CONFIG["espera_longa"])

    log.info("  Voltando à lista...")
    aguardar_sem_overlay(driver)

    for tentativa in range(1, 4):
        try:
            btn_voltar = esperar_clicavel(driver, By.ID, "voltar", timeout=10)
            try:
                btn_voltar.click()
            except Exception:
                clicar_js(driver, btn_voltar)
            time.sleep(CONFIG["espera_longa"])

            url_atual = driver.current_url
            if "fiscalizacao" in url_atual and "gerenciar" not in url_atual:
                log.info("  Voltou à lista com sucesso.")
                return
            time.sleep(CONFIG["espera_longa"] * tentativa)

        except Exception as e:
            log.warning(f"  Tentativa {tentativa} de Voltar falhou: {e}")
            time.sleep(CONFIG["espera_longa"] * tentativa)

    log.warning("  Navegando diretamente para a lista de CNPJs...")
    driver.get(CONFIG["url_base"])
    time.sleep(CONFIG["espera_longa"])


# ─────────────────────────────────────────────────────────────
# MÓDULO 5 — ORQUESTRADOR PRINCIPAL
# ─────────────────────────────────────────────────────────────
def selecionar_pdfs() -> list:
    """Abre o explorador de arquivos para seleção dos PDFs."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    arquivos = filedialog.askopenfilenames(
        title="Selecione os formulários PDF para processar",
        filetypes=[("Formulários PDF", "*.pdf"), ("Todos os arquivos", "*.*")],
        multiple=True,
    )

    root.destroy()
    return [Path(a) for a in arquivos] if arquivos else []


def criar_janela_progresso(total: int):
    """Cria e retorna uma janela tkinter de progresso."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Robô de Fiscalização")
    root.geometry("420x130")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    root.update_idletasks()
    x = (root.winfo_screenwidth()  - 420) // 2
    y = (root.winfo_screenheight() - 130) // 2
    root.geometry(f"420x130+{x}+{y}")

    tk.Label(root, text="Processando formulários...",
             font=("Segoe UI", 10, "bold")).pack(pady=(12, 2))

    label_status = tk.Label(root, text="Iniciando...",
                            font=("Segoe UI", 9), fg="#444")
    label_status.pack()

    label_contador = tk.Label(root, text=f"0 de {total}",
                              font=("Segoe UI", 9, "bold"), fg="#1a3a5c")
    label_contador.pack(pady=(2, 6))

    barra = ttk.Progressbar(root, length=380, maximum=total, mode="determinate")
    barra.pack(padx=20)

    root.update()
    return root, label_status, label_contador, barra


def atualizar_progresso(root, label_status, label_contador, barra, atual, total, nome_pdf):
    """Atualiza os elementos da janela de progresso."""
    label_status.config(text=f"Processando: {nome_pdf}")
    label_contador.config(text=f"{atual} de {total}")
    barra["value"] = atual
    root.update()


def processar_lote():
    """
    Fluxo principal: seleciona PDFs, conecta ao Brave e processa o lote.
    """
    import shutil
    import tkinter as tk
    from tkinter import messagebox

    log.info("Aguardando seleção dos PDFs...")
    pdfs = selecionar_pdfs()

    if not pdfs:
        log.warning("Nenhum PDF selecionado. Encerrando.")
        return

    pasta_origem     = pdfs[0].parent
    pasta_concluidos = pasta_origem / "PDFs concluidos"
    pasta_erros      = pasta_origem / "PDFs com erro"

    pasta_concluidos.mkdir(exist_ok=True)
    pasta_erros.mkdir(exist_ok=True)

    log.info(f"Pasta de origem:    {pasta_origem}")
    log.info(f"PDFs concluidos em: {pasta_concluidos}")
    log.info(f"PDFs com erro em:   {pasta_erros}")

    total = len(pdfs)
    log.info("=" * 60)
    log.info(f"Iniciando lote: {total} PDF(s)")
    log.info("=" * 60)

    root_prog, lbl_status, lbl_contador, barra = criar_janela_progresso(total)

    # ── Alteração: chama conectar_brave() em vez de conectar_edge() ─────────
    driver  = conectar_brave()
    # ─────────────────────────────────────────────────────────────────────────
    sucesso = 0
    falha   = 0

    for i, pdf in enumerate(pdfs, 1):
        log.info(f"[{i}/{total}] {pdf.name}")
        atualizar_progresso(root_prog, lbl_status, lbl_contador,
                            barra, i - 1, total, pdf.name)

        try:
            dados = ler_pdf(pdf)
            log.info("  Sanitizando dados...")
            dados = sanitizar_dados(dados)

            cnpj = dados.get("cnpj", "").strip()
            if not cnpj:
                raise ValueError("CNPJ não encontrado no PDF.")
            if len(cnpj) != 14:
                raise ValueError(f"CNPJ inválido ({len(cnpj)} dígitos): '{cnpj}'")

            driver.get(CONFIG["url_base"])
            time.sleep(CONFIG["espera_longa"])

            if not localizar_cnpj_e_gerenciar(driver, cnpj):
                raise ValueError(f"CNPJ {cnpj} não encontrado na lista.")

            clicar_auto_fiscalizacao(driver)
            marcar_equipes(driver)
            preencher_sede(driver, dados)
            selecionar_outro_colaborador(driver)
            preencher_dados_pessoais(driver, dados)
            preencher_incisos(driver, dados)
            preencher_campos_tecnicos(driver, dados)
            marcar_apreensao_e_recusa(driver)
            salvar_e_voltar(driver)

            shutil.move(str(pdf), str(pasta_concluidos / pdf.name))
            log.info(f"  ✅ Concluído: {pdf.name}")
            sucesso += 1

        except Exception as e:
            log.error(f"  ❌ Erro em {pdf.name}: {e}")
            shutil.move(str(pdf), str(pasta_erros / pdf.name))
            falha += 1
            try:
                driver.get(CONFIG["url_base"])
                time.sleep(CONFIG["espera_longa"])
            except Exception:
                pass

    atualizar_progresso(root_prog, lbl_status, lbl_contador,
                        barra, total, total, "Concluído!")
    root_prog.destroy()

    log.info("=" * 60)
    log.info(f"Lote concluído — ✅ {sucesso} sucesso(s)  ❌ {falha} erro(s)")
    log.info(f"Log: {log_path}")
    log.info("=" * 60)

    msg = (
        f"Lote concluido!\n\n"
        f"Processados com sucesso: {sucesso}\n"
        f"Processados com erro:    {falha}\n\n"
        f"Log salvo em:\n{log_path}"
    )
    root_final = tk.Tk()
    root_final.withdraw()
    root_final.attributes("-topmost", True)
    messagebox.showinfo("Robô de Fiscalização — Concluído", msg)
    root_final.destroy()


# ─────────────────────────────────────────────────────────────
# UTILITÁRIO — TESTAR LEITURA DE PDF
# ─────────────────────────────────────────────────────────────
def testar_pdf(caminho: str):
    """
    Testa leitura e sanitização de um PDF sem acionar o sistema.
    Uso: python robo_automacao_v4.py testar caminho/formulario.pdf
    """
    pdf = Path(caminho)
    if not pdf.exists():
        print(f"Arquivo não encontrado: {caminho}")
        return

    print(f"\n{'='*60}\nTestando: {pdf.name}\n{'='*60}")
    dados = ler_pdf(pdf)

    print(f"\nDados brutos ({len(dados)} campos):")
    for k, v in sorted(dados.items()):
        print(f"  {k:<35} = {repr(v)}")

    print("\nApós sanitização:")
    dados_limpos = sanitizar_dados(dados)
    alterados = 0
    for k, v in sorted(dados_limpos.items()):
        original = dados.get(k)
        if str(original) != str(v):
            print(f"  * {k:<35} = {repr(v)}  (era: {repr(original)})")
            alterados += 1
        else:
            print(f"    {k:<35} = {repr(v)}")

    print(f"\n{alterados} campo(s) alterado(s) pela sanitização.")
    cnpj = dados_limpos.get("cnpj", "")
    status = "✅ OK" if len(cnpj) == 14 else f"⚠️  INVÁLIDO ({len(cnpj)} dígitos)"
    print(f"CNPJ: {cnpj} — {status}")


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "testar" and len(sys.argv) > 2:
        testar_pdf(sys.argv[2])
    else:
        processar_lote()
