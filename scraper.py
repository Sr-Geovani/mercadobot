"""
scraper.py — Automação PDV Legal
Baixa Resumo de Vendas e Produtos Mais Vendidos para o período informado.
"""
import os
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger(__name__)

# ─── CONFIGURAÇÃO ────────────────────────────────────────────
PDV_URL      = "https://pdvlegal.com.br/loginpdvlegal.aspx"
PDV_EMAIL    = os.environ.get("PDV_EMAIL")
PDV_SENHA    = os.environ.get("PDV_SENHA")
DOWNLOAD_DIR = Path("/tmp/pdvlegal")

URL_VENDAS   = "https://pdvlegal.com.br/relatorios.aspx?relatorio=1"
URL_PRODUTOS = "https://pdvlegal.com.br/dashboard_produtos.aspx?relatorio=dp"


def criar_driver() -> webdriver.Chrome:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_experimental_option("prefs", {
        "download.default_directory": str(DOWNLOAD_DIR),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    })
    return webdriver.Chrome(options=opts)


def aguardar_download(timeout: int = 45) -> Path:
    """Aguarda o arquivo .xlsx aparecer na pasta de download."""
    inicio = time.time()
    while time.time() - inicio < timeout:
        arquivos = [
            f for f in DOWNLOAD_DIR.glob("*.xlsx")
            if not f.name.endswith(".crdownload")
        ]
        if arquivos:
            return max(arquivos, key=lambda f: f.stat().st_mtime)
        time.sleep(1)
    raise TimeoutError("Download não completou em tempo hábil.")


def limpar_downloads():
    for f in DOWNLOAD_DIR.glob("*"):
        f.unlink(missing_ok=True)


def fazer_login(driver: webdriver.Chrome, wait: WebDriverWait):
    logger.info("Fazendo login no PDV Legal...")
    driver.get(PDV_URL)
    wait.until(EC.presence_of_element_located((By.ID, "txtEmail")))
    driver.find_element(By.ID, "txtEmail").send_keys(PDV_EMAIL)
    driver.find_element(By.ID, "txtSenha").send_keys(PDV_SENHA)
    driver.find_element(By.ID, "btnEntrar").click()
    wait.until(EC.url_changes(PDV_URL))
    logger.info("Login realizado.")


def definir_datas_vendas(driver: webdriver.Chrome, wait: WebDriverWait,
                         data_ini: str, data_fim: str):
    """Preenche os campos de data do Resumo de Vendas com os IDs reais."""
    campo_ini = wait.until(EC.presence_of_element_located(
        (By.ID, "ContentPlaceHolder1_txtdatapadrao1")
    ))
    campo_ini.clear()
    campo_ini.send_keys(data_ini)

    campo_fim = driver.find_element(By.ID, "ContentPlaceHolder1_txtdatapadrao2")
    campo_fim.clear()
    campo_fim.send_keys(data_fim)
    logger.info(f"Datas definidas: {data_ini} → {data_fim}")


def selecionar_todas_lojas(driver: webdriver.Chrome):
    """Seleciona 'Todos' no dropdown de filial."""
    from selenium.webdriver.support.ui import Select
    dropdown = driver.find_element(By.ID, "ContentPlaceHolder1_ddlfilialpadrao")
    Select(dropdown).select_by_value("0")
    logger.info("Filtro de lojas: Todos selecionado.")


def definir_datas(driver: webdriver.Chrome, wait: WebDriverWait,
                  data_ini: str, data_fim: str):
    """Fallback genérico para a página de produtos (tenta IDs comuns)."""
    pares = [
        ("txtDataIni",  "txtDataFim"),
        ("txtDtInicio", "txtDtFim"),
        ("txtInicio",   "txtFim"),
    ]
    for id_ini, id_fim in pares:
        try:
            campo = wait.until(EC.presence_of_element_located((By.ID, id_ini)))
            campo.clear()
            campo.send_keys(data_ini)
            driver.find_element(By.ID, id_fim).clear()
            driver.find_element(By.ID, id_fim).send_keys(data_fim)
            logger.info(f"Datas definidas via {id_ini}/{id_fim}")
            return
        except Exception:
            continue
    logger.warning("Campos de data não encontrados pelos IDs conhecidos.")


def exportar_vendas(driver: webdriver.Chrome, wait: WebDriverWait,
                    data_ini: str, data_fim: str) -> Path:
    """
    Exporta o Resumo Geral de Vendas (relatorio=1).
    Fluxo: preenche datas → seleciona Todos → clica Gerar Relatório → baixa Excel.
    """
    logger.info("Exportando Resumo de Vendas...")
    limpar_downloads()

    driver.get(URL_VENDAS)
    time.sleep(2)

    # Preenche datas com IDs reais
    definir_datas_vendas(driver, wait, data_ini, data_fim)

    # Seleciona todas as lojas
    selecionar_todas_lojas(driver)
    time.sleep(1)

    # Clica em Gerar Relatório — já baixa o Excel direto
    try:
        btn = wait.until(EC.element_to_be_clickable(
            (By.ID, "ContentPlaceHolder1_btnGerarRelatorio")
        ))
        btn.click()
        logger.info("Botão Gerar Relatório clicado.")
    except Exception as e:
        raise RuntimeError(f"Botão Gerar Relatório não encontrado: {e}")

    arquivo = aguardar_download()
    destino = DOWNLOAD_DIR / "vendas.xlsx"
    arquivo.rename(destino)
    logger.info(f"Vendas baixado: {destino}")
    return destino


def selecionar_periodo_produtos(driver: webdriver.Chrome, wait: WebDriverWait,
                                data_ini: str, data_fim: str):
    """
    Seleciona o período no date range picker da página de produtos.
    Mapeia o período solicitado para a opção mais próxima do dropdown.
    """
    from datetime import datetime, timedelta

    hoje  = datetime.now().strftime("%d/%m/%Y")
    ontem = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")

    # Mapeia para a opção do dropdown
    if data_ini == data_fim == hoje:
        range_key = "Hoje"
    elif data_ini == data_fim == ontem:
        range_key = "Ontem"
    elif data_ini == (datetime.now() - timedelta(days=7)).strftime("%d/%m/%Y"):
        range_key = "Ultimos 7 dias"
    elif data_ini == (datetime.now() - timedelta(days=15)).strftime("%d/%m/%Y"):
        range_key = "Ultimos 15 dias"
    elif data_ini == (datetime.now() - timedelta(days=30)).strftime("%d/%m/%Y"):
        range_key = "Ultimos 30 dias"
    elif data_ini == (datetime.now() - timedelta(days=60)).strftime("%d/%m/%Y"):
        range_key = "Ultimos 60 dias"
    elif data_ini == (datetime.now() - timedelta(days=90)).strftime("%d/%m/%Y"):
        range_key = "Ultimos 90 dias"
    else:
        range_key = "Ultimos 30 dias"  # fallback padrão
        logger.warning(f"Período não mapeado ({data_ini}→{data_fim}), usando 30 dias.")

    # Abre o dropdown
    btn = wait.until(EC.element_to_be_clickable((By.ID, "reportrange")))
    btn.click()
    time.sleep(1)

    # Clica na opção correta pelo data-range-key
    opcao = wait.until(EC.element_to_be_clickable(
        (By.XPATH, f"//li[@data-range-key='{range_key}']")
    ))
    opcao.click()
    logger.info(f"Período selecionado: {range_key}")
    time.sleep(1)


def exportar_produtos(driver: webdriver.Chrome, wait: WebDriverWait,
                      data_ini: str, data_fim: str) -> Path:
    """
    Exporta Produtos Mais Vendidos (dashboard_produtos.aspx).
    Seleciona período via date range picker e exporta via modal de download.
    """
    logger.info("Exportando Produtos Mais Vendidos...")
    limpar_downloads()

    driver.get(URL_PRODUTOS)
    time.sleep(2)

    # Seleciona período no date range picker
    selecionar_periodo_produtos(driver, wait, data_ini, data_fim)
    time.sleep(1)

    # Clica no botão Filtrar (btnFiltro)
    try:
        btn_busca = wait.until(EC.element_to_be_clickable((By.ID, "btnFiltro")))
        btn_busca.click()
        time.sleep(3)
    except Exception:
        logger.warning("Botão btnFiltro não encontrado, continuando...")

    # Abre o modal de download
    try:
        btn_modal = wait.until(EC.element_to_be_clickable((By.ID, "imgDownload")))
        btn_modal.click()
        time.sleep(1)
    except Exception as e:
        raise RuntimeError(f"Botão de download (modal) não encontrado: {e}")

    # Clica no botão Excel dentro do modal
    try:
        btn_excel = wait.until(EC.element_to_be_clickable(
            (By.ID, "ContentPlaceHolder1_ImageButton1")
        ))
        btn_excel.click()
    except Exception as e:
        raise RuntimeError(f"Botão Excel no modal não encontrado: {e}")

    arquivo = aguardar_download()
    destino = DOWNLOAD_DIR / "produtos.xlsx"
    arquivo.rename(destino)
    logger.info(f"Produtos baixado: {destino}")
    return destino


def baixar_relatorios() -> tuple:
    """Baixa relatórios do dia anterior."""
    ontem = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
    return baixar_relatorios_periodo(ontem, ontem)


def baixar_relatorios_periodo(data_ini: str, data_fim: str) -> tuple:
    """
    Baixa os dois relatórios para o período especificado.
    data_ini, data_fim: formato DD/MM/YYYY
    Retorna (path_vendas, path_produtos).
    """
    logger.info(f"Período: {data_ini} → {data_fim}")
    driver = criar_driver()
    wait   = WebDriverWait(driver, 25)

    try:
        fazer_login(driver, wait)
        path_vendas   = exportar_vendas(driver, wait, data_ini, data_fim)
        path_produtos = exportar_produtos(driver, wait, data_ini, data_fim)
        return path_vendas, path_produtos
    finally:
        driver.quit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    v, p = baixar_relatorios()
    print(f"Vendas:   {v}")
    print(f"Produtos: {p}")
