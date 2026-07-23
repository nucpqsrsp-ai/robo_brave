"""
ROBÔ DE AUTOMAÇÃO — PREENCHIMENTO DE FORMULÁRIOS DE FISCALIZAÇÃO
=================================================================
Sistema: AngularJS rodando em Edge (localhost:4444)
Automação: Selenium + Edge WebDriver

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
  1. Abra o Edge com a flag de depuração remota usando o atalho:
         msedge.exe --remote-debugging-port=9333
  2. Faça login no sistema manualmente
  3. Navegue até a tela da lista de CNPJs
  4. Rode este script:
         python robo_automacao_v3.py
     Ou para testar um PDF sem acionar o sistema:
         python robo_automacao_v3.py testar caminho/formulario.pdf

DEPENDÊNCIAS:
  pip install pypdf selenium webdriver-manager pyperclip
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
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
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

    # Porta de depuração remota do Edge (deve bater com o atalho usado para abrir o Edge)
    "debug_port": "localhost:9333",

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
    usa OCR via Claude API como fallback automático.
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
        log.warning(f"  PDF sem campos detectáveis — tentando OCR via Claude API...")
        campos = ler_pdf_ocr(caminho_pdf)
        log.info(f"  OCR concluído: {len(campos)} campos extraídos")

    return campos


def ler_pdf_ocr(caminho_pdf: Path) -> dict:
    """
    Fallback GRATUITO para PDFs sem campos editáveis (salvos pelo navegador).
    Usa pdfplumber para extrair texto com coordenadas precisas (x, y),
    emparelhando cada rótulo com o valor na linha imediatamente abaixo,
    dentro das bordas horizontais conhecidas do formulário.
    """
    try:
        import pdfplumber
    except ImportError:
        log.error("  pdfplumber não instalado. Adicione nas dependências.")
        return {}

    # Campos da página 1: (chave, top_do_rotulo, x0_inicio, x1_fim)
    # Coordenadas baseadas no layout fixo do formulário
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

    # Campos da página 2: checkboxes e textos livres
    # Os checkboxes marcados aparecem como pequenos quadrados — detectamos
    # pela presença de texto de valor nas linhas do embasamento
    CAMPOS_P2_TEXTOS = [
        ("emprego_quimicos",  None,   39.7,  600.0),  # top detectado dinamicamente
        ("historico",         None,   39.7,  600.0),
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
        """Extrai palavras na faixa vertical abaixo do rótulo e dentro dos limites x."""
        val = []
        for w in words:
            if label_top + v_min <= w['top'] <= label_top + v_max:
                if x0 - 2 <= w['x0'] < x1:
                    val.append(w['text'])
        return " ".join(val).strip()

    def encontrar_top_rotulo(words, texto_rotulo, tolerancia=5):
        """
        Encontra a coordenada top de um rótulo pelo texto.
        Suporta rótulos compostos por múltiplas palavras na mesma linha.
        """
        texto_lower = texto_rotulo.lower().strip()
        # Tenta encontrar como palavra única primeiro
        for w in words:
            if texto_lower in w['text'].lower():
                return w['top']
        # Tenta montar o texto combinando palavras da mesma linha
        from itertools import groupby
        linhas = {}
        for w in words:
            top_key = round(w['top'])
            if top_key not in linhas:
                linhas[top_key] = []
            linhas[top_key].append(w['text'])
        for top_key, palavras in linhas.items():
            linha_texto = " ".join(palavras).lower()
            if texto_lower in linha_texto:
                return float(top_key)
        return None

    try:
        with pdfplumber.open(str(caminho_pdf)) as pdf:

            # ── Página 1 ──────────────────────────────────────
            if len(pdf.pages) >= 1:
                words1 = pdf.pages[0].extract_words(x_tolerance=3, y_tolerance=3)
                for chave, top, x0, x1 in CAMPOS_P1:
                    val = extrair_campo(words1, top, x0, x1)
                    if val:
                        campos[chave] = val

            # ── Página 2 ──────────────────────────────────────
            if len(pdf.pages) >= 2:
                words2 = pdf.pages[1].extract_words(x_tolerance=3, y_tolerance=3)

                # ── Localiza tops de todos os marcos da página 2 ─────────
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

                # ── Extrai embasamento de cada inciso ────────────────────
                IGNORAR_EMB = {
                    "embasamento", "do", "inciso", "iii", "v", "vi",
                    "(se", "aplicável)", "possui", "inciso?"
                }
                for idx, (inciso, chave_chk, chave_emb, top_bloco, top_emb) in enumerate(tops_incisos):
                    if not top_emb:
                        continue

                    # Limite inferior: topo do próximo bloco ou emprego
                    proximos = [tops_incisos[j][3] for j in range(idx+1, len(tops_incisos)) if tops_incisos[j][3]]
                    if proximos:
                        limite_inf = proximos[0] - 2
                    elif top_eq:
                        limite_inf = top_eq - 2
                    else:
                        limite_inf = top_emb + 150

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

                # ── Emprego dos produtos químicos ─────────────────────────
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

                # ── Histórico ─────────────────────────────────────────────
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
def sanitizar_dados(dados: dict) -> dict:
    """
    Limpa os dados extraídos do PDF antes de enviar ao sistema.
    - Campos numéricos: remove tudo que não for dígito
    - Campos monetários: remove R$, pontos de milhar
    - CNPJ: remove formatação e valida 14 dígitos
    - Campos de texto: strip de espaços extras
    """
    dados_limpos = dict(dados)

    # Apenas dígitos
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

    # CNPJ
    cnpj_original = str(dados_limpos.get("cnpj", ""))
    cnpj_limpo = re.sub(r"\D", "", cnpj_original)
    if cnpj_limpo != cnpj_original and cnpj_original not in ("", "False"):
        log.warning(f"  Sanitizado [cnpj]: '{cnpj_original}' → '{cnpj_limpo}'")
    if cnpj_limpo and len(cnpj_limpo) != 14:
        log.warning(f"  CNPJ suspeito — {len(cnpj_limpo)} dígitos: '{cnpj_limpo}'")
    dados_limpos["cnpj"] = cnpj_limpo

    # Campos monetários
    for campo in ["aluguel_valor", "faturamento_anual"]:
        original = str(dados_limpos.get(campo, ""))
        limpo = re.sub(r"[^\d,]", "", original.replace("R$", "").strip())
        if limpo != original and original not in ("", "False"):
            log.warning(f"  Sanitizado [{campo}]: '{original}' → '{limpo}'")
        dados_limpos[campo] = limpo

    # Strip em campos de texto
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

    # UF e Município sempre em maiúsculas
    for campo in ["uf", "municipio"]:
        valor = dados_limpos.get(campo)
        if isinstance(valor, str):
            dados_limpos[campo] = valor.strip().upper()

    return dados_limpos


# ─────────────────────────────────────────────────────────────
# MÓDULO 3 — CONEXÃO COM O EDGE
# ─────────────────────────────────────────────────────────────
def conectar_edge() -> webdriver.Edge:
    """
    Conecta ao Edge já aberto com --remote-debugging-port=9333.
    Usa o EdgeDriver embutido no executável (sem baixar da internet).
    """
    import os, sys

    options = webdriver.EdgeOptions()
    options.add_experimental_option("debuggerAddress", CONFIG["debug_port"])

    # Localiza o EdgeDriver embutido pelo PyInstaller
    if getattr(sys, "frozen", False):
        # Rodando como .exe — driver está na pasta temporária do PyInstaller
        base = sys._MEIPASS
    else:
        # Rodando como script Python normal
        base = os.path.dirname(os.path.abspath(__file__))

    driver_path = os.path.join(base, "msedgedriver.exe")

    try:
        service = Service(driver_path)
        driver = webdriver.Edge(options=options)
        log.info("Conectado ao Edge com sucesso.")
        return driver
    except Exception as e:
        log.error(f"Não foi possível conectar ao Edge: {e}")
        log.error(
            "Certifique-se de que o Edge foi aberto com o atalho:\n"
            "  msedge.exe --remote-debugging-port=9333\n"
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
    Cola um texto em um elemento usando JavaScript.
    Mais confiável que send_keys para textos longos com acentos.
    """
    if not texto:
        return
    # Limpa o campo e cola via clipboard
    elemento.click()
    driver.execute_script("arguments[0].value = '';", elemento)
    pyperclip.copy(str(texto))
    elemento.send_keys(Keys.CONTROL, "v")
    time.sleep(CONFIG["espera_curta"])


def preencher_por_label(driver, texto_label: str, valor: str):
    """
    Localiza um input/textarea pelo texto do label associado e preenche.
    Usado para campos com id dinâmico (undefined_inputNumerico).
    """
    if not valor:
        return
    # Encontra o form-group que contém o label com o texto
    xpath = (
        f"//div[contains(@class,'form-group')]"
        f"[.//label[contains(normalize-space(.),'{ texto_label}')]]"
        f"//input | //div[contains(@class,'form-group')]"
        f"[.//label[contains(normalize-space(),'{texto_label}')]]"
        f"//textarea"
    )
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
    """
    Aguarda o overlay de carregamento (block-ui-overlay) desaparecer.
    Se não sumir no tempo limite, remove via JavaScript.
    """
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
    Retorna True se encontrado.
    """
    log.info(f"  Procurando CNPJ {cnpj} na lista...")

    # Formata o CNPJ no padrão exibido na tela: XX.XXX.XXX/XXXX-XX
    cnpj_fmt = (
        f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}"
        f"/{cnpj[8:12]}-{cnpj[12:14]}"
    )

    try:
        # Localiza a célula com o CNPJ
        celula = esperar(
            driver, By.XPATH,
            f"//td[contains(@class,'ng-binding') and "
            f"normalize-space(text())='{cnpj_fmt}']"
        )
        # Sobe para a linha (<tr>) e clica no botão "Gerenciar Fiscalização"
        linha = celula.find_element(By.XPATH, "./ancestor::tr")
        btn_gerenciar = linha.find_element(
            By.XPATH,
            ".//button[@uib-tooltip='Gerenciar Fiscalização'] | "
            ".//a[@uib-tooltip='Gerenciar Fiscalização']"
        )

        # Aguarda overlay desaparecer, rola até o botão e usa JS como padrão
        aguardar_sem_overlay(driver)
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", btn_gerenciar
        )
        time.sleep(CONFIG["espera_curta"])

        # Tenta clique normal; se bloqueado, usa JS diretamente
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

    # Funcionários e faturamento
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
    time.sleep(CONFIG["espera_media"])  # aguarda auto-completar CEP
    preencher_id("endereco",    dados.get("endereco", ""))

    # Número e Complemento — mesmo id "endereco", usar ng-model
    preencher_ng(
        "$ctrl.representanteFuncionarioAuto.endereco.numero",
        dados.get("numero", "")
    )
    preencher_ng(
        "$ctrl.representanteFuncionarioAuto.endereco.complementar",
        dados.get("complemento", "") or ""
    )

    preencher_id("bairro",      dados.get("bairro", ""))

    # UF — select
    try:
        uf_select = Select(esperar_clicavel(driver, By.ID, "uf"))
        uf_select.select_by_value(str(dados.get("uf", "")).upper())
        time.sleep(CONFIG["espera_curta"])
    except Exception:
        log.warning("  Dropdown UF não encontrado ou valor inválido.")

    # Município — select
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
    Ignora incisos não marcados no PDF.
    """
    log.info("  Verificando incisos...")

    # Mapeamento: código → texto exato no dropdown "Dispositivo Legal"
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

        # Verifica se há botões de embasamento visíveis na página
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

        # a) Clica em "Editar/Adicionar embasamento"
        botoes_emb[idx].click()
        time.sleep(CONFIG["espera_longa"])

        # b) Seleciona o inciso no dropdown "Dispositivo Legal"
        # ng-model="$ctrl.infracao.inciso" — opções via ng-repeat com texto visível
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

        # c) Seleciona "Nota Fiscal" no dropdown "Tipo"
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

        # d) Cola a descrição no campo "Descrição"
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

        # e) Clica em "Incluir"
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

    # Apreensão — checkbox "Não Houve"
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

    # Recusa — radio "Não"
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

    # Tenta clicar em Voltar até 3 vezes com esperas progressivas
    for tentativa in range(1, 4):
        try:
            btn_voltar = esperar_clicavel(driver, By.ID, "voltar", timeout=10)
            try:
                btn_voltar.click()
            except Exception:
                clicar_js(driver, btn_voltar)
            time.sleep(CONFIG["espera_longa"])

            # Verifica se voltou à lista de CNPJs pela URL
            url_atual = driver.current_url
            if "fiscalizacao" in url_atual and "gerenciar" not in url_atual:
                log.info("  Voltou à lista com sucesso.")
                return
            # Se ainda estiver na tela errada, aguarda mais
            time.sleep(CONFIG["espera_longa"] * tentativa)

        except Exception as e:
            log.warning(f"  Tentativa {tentativa} de Voltar falhou: {e}")
            time.sleep(CONFIG["espera_longa"] * tentativa)

    # Último recurso: navega diretamente para a lista
    log.warning("  Navegando diretamente para a lista de CNPJs...")
    driver.get(CONFIG["url_base"])
    time.sleep(CONFIG["espera_longa"])


# ─────────────────────────────────────────────────────────────
# MÓDULO 5 — ORQUESTRADOR PRINCIPAL
# ─────────────────────────────────────────────────────────────
def selecionar_pdfs() -> list:
    """
    Abre o explorador de arquivos do Windows para o usuário
    selecionar um ou mais PDFs. Retorna lista de Path.
    Permite selecionar arquivos de qualquer pasta.
    """
    import tkinter as tk
    from tkinter import filedialog, messagebox

    # Janela raiz oculta (necessária para o filedialog)
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)   # garante que abre na frente

    arquivos = filedialog.askopenfilenames(
        title="Selecione os formulários PDF para processar",
        filetypes=[("Formulários PDF", "*.pdf"), ("Todos os arquivos", "*.*")],
        multiple=True,
    )

    root.destroy()

    if not arquivos:
        return []

    pdfs = [Path(a) for a in arquivos]
    return pdfs


def criar_janela_progresso(total: int):
    """
    Cria e retorna uma janela tkinter de progresso.
    Retorna (root, label_status, label_contador, barra).
    """
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Robô de Fiscalização")
    root.geometry("420x130")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    # Centraliza na tela
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
    Abre o explorador de arquivos para seleção dos PDFs,
    cria as subpastas "PDFs concluidos" e "PDFs com erro" no mesmo
    diretório dos arquivos selecionados, exibe janela de progresso
    e popup ao concluir.
    """
    import shutil
    import tkinter as tk
    from tkinter import messagebox

    # Abre o explorador de arquivos
    log.info("Aguardando seleção dos PDFs...")
    pdfs = selecionar_pdfs()

    if not pdfs:
        log.warning("Nenhum PDF selecionado. Encerrando.")
        return

    # Cria subpastas no mesmo diretório dos PDFs selecionados
    pasta_origem     = pdfs[0].parent
    pasta_concluidos = pasta_origem / "PDFs concluidos"
    pasta_erros      = pasta_origem / "PDFs com erro"

    pasta_concluidos.mkdir(exist_ok=True)
    pasta_erros.mkdir(exist_ok=True)

    log.info(f"Pasta de origem:    {pasta_origem}")
    log.info(f"PDFs concluidos em: {pasta_concluidos}")
    log.info(f"PDFs com erro em:   {pasta_erros}")

    total   = len(pdfs)
    log.info("=" * 60)
    log.info(f"Iniciando lote: {total} PDF(s)")
    log.info("=" * 60)

    # Janela de progresso
    root_prog, lbl_status, lbl_contador, barra = criar_janela_progresso(total)

    driver  = conectar_edge()
    sucesso = 0
    falha   = 0

    for i, pdf in enumerate(pdfs, 1):
        log.info(f"[{i}/{total}] {pdf.name}")
        atualizar_progresso(root_prog, lbl_status, lbl_contador,
                            barra, i - 1, total, pdf.name)

        try:
            # 1. Ler e sanitizar dados do PDF
            dados = ler_pdf(pdf)
            log.info("  Sanitizando dados...")
            dados = sanitizar_dados(dados)

            cnpj = dados.get("cnpj", "").strip()
            if not cnpj:
                raise ValueError("CNPJ não encontrado no PDF.")
            if len(cnpj) != 14:
                raise ValueError(f"CNPJ inválido ({len(cnpj)} dígitos): '{cnpj}'")

            # Garante que está na tela da lista de CNPJs
            driver.get(CONFIG["url_base"])
            time.sleep(CONFIG["espera_longa"])

            # 2. Localizar CNPJ e clicar em Gerenciar Fiscalização
            if not localizar_cnpj_e_gerenciar(driver, cnpj):
                raise ValueError(f"CNPJ {cnpj} não encontrado na lista.")

            # 3. Clicar em Auto de Fiscalização
            clicar_auto_fiscalizacao(driver)

            # 4. Marcar Equipes
            marcar_equipes(driver)

            # 5. Preencher sede, funcionários, faturamento
            preencher_sede(driver, dados)

            # 6. Selecionar OUTRO COLABORADOR e preencher dados pessoais
            selecionar_outro_colaborador(driver)
            preencher_dados_pessoais(driver, dados)

            # 7. Preencher incisos (somente os marcados no PDF)
            preencher_incisos(driver, dados)

            # 8. Preencher campos técnicos
            preencher_campos_tecnicos(driver, dados)

            # 9. Marcar Apreensão e Recusa
            marcar_apreensao_e_recusa(driver)

            # 10. Salvar e voltar
            salvar_e_voltar(driver)

            # 11. Mover PDF para concluídos
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

    # Atualiza barra para 100%
    atualizar_progresso(root_prog, lbl_status, lbl_contador,
                        barra, total, total, "Concluído!")
    root_prog.destroy()

    log.info("=" * 60)
    log.info(f"Lote concluído — ✅ {sucesso} sucesso(s)  ❌ {falha} erro(s)")
    log.info(f"Log: {log_path}")
    log.info("=" * 60)

    # Popup de conclusão
    icone = "OK" if falha == 0 else "ATENCAO"
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
    Uso: python robo_automacao_v3.py testar caminho/formulario.pdf
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
