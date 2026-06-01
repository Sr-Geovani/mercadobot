"""
scraper.py — Automação PDV Legal
Baixa Resumo de Vendas e Produtos Mais Vendidos do dia anterior.
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
from selenium.webdriver.support.ui import Select

logger = logging.getLogger(__name__)

# ─── CONFIGURAÇÃO ────────────────────────────────────────────
PDV_URL    = "https://pdvlegal.com.br/loginpdvlegal.aspx"
PDV_EMAIL  = os.environ.get("PDV_EMAIL")
PDV_SENHA  = os.environ.get("PDV_SENHA")
DOWNLOAD_DIR = Path("/tmp/pdvlegal")

# URLs dos relatórios (conforme visto no sistema)
URL_VENDAS   = "https://pdvlegal.com.br/relatorios.aspx?relatorio=1"
URL_PRODUTOS = "https://pdvlegal.com.br/relatorios.aspx?relatorio=27"


def criar_driver() -> webdriver.Chrome:
    """Cria o Chrome em modo headless (invisível) para o servidor."""
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


def aguardar_download(timeout: int = 30) -> Path:
    """Aguarda o arquivo .xlsx aparecer na pasta de download."""
    inicio = time.time()
    while time.time() - inicio < timeout:
        arquivos = list(DOWNLOAD_DIR.glob("*.xlsx"))
        # Ignora arquivos temporários do Chrome (.crdownload)
        completos = [f for f in arquivos if not f.name.endswith(".crdownload")]
        if completos:
            return max(completos, key=lambda f: f.stat().st_mtime)
        time.sleep(1)
    raise TimeoutError("Download não completou em tempo hábil.")


def limpar_downloads():
    """Remove arquivos antigos da pasta de download."""
    for f in DOWNLOAD_DIR.glob("*.xlsx"):
        f.unlink(missing_ok=True)


def fazer_login(driver: webdriver.Chrome, wait: WebDriverWait):
    """Faz login no PDV Legal."""
    logger.info("Acessando PDV Legal...")
    driver.get(PDV_URL)
    wait.until(EC.presence_of_element_located((By.ID, "txtEmail")))

    driver.find_element(By.ID, "txtEmail").send_keys(PDV_EMAIL)
    driver.find_element(By.ID, "txtSenha").send_keys(PDV_SENHA)
    driver.find_element(By.ID, "btnEntrar").click()

    wait.until(EC.url_changes(PDV_URL))
    logger.info("Login realizado com sucesso.")


def definir_datas(driver: webdriver.Chrome, wait: WebDriverWait,
                  data_ini: str, data_fim: str):
    """Define as datas de início e fim no filtro do relatório."""
    try:
        campo_ini = wait.until(EC.presence_of_element_located((By.ID, "txtDataIni")))
        campo_ini.clear()
        campo_ini.send_keys(data_ini)
        campo_fim = driver.find_element(By.ID, "txtDataFim")
        campo_fim.clear()
        campo_fim.send_keys(data_fim)
    except Exception:
        try:
            campo_ini = driver.find_element(By.ID, "txtDtInicio")
            campo_ini.clear()
            campo_ini.send_keys(data_ini)
            campo_fim = driver.find_element(By.ID, "txtDtFim")
            campo_fim.clear()
            campo_fim.send_keys(data_fim)
        except Exception as e:
            logger.warning(f"Campo de data não encontrado: {e}")


def exportar_relatorio(driver: webdriver.Chrome, wait: WebDriverWait,
                       url: str, data_ini: str, data_fim: str, nome: str) -> Path:
    """Acessa um relatório, define a data e exporta em Excel."""
    logger.info(f"Exportando {nome}...")
    limpar_downloads()

    driver.get(url)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)  # aguarda carregamento dos filtros

    # Define datas
    definir_datas(driver, wait, data_ini, data_fim)
    time.sleep(1)

    # Clica em pesquisar/filtrar
    try:
        btn = driver.find_element(By.ID, "btnPesquisar")
        btn.click()
    except Exception:
        try:
            btn = driver.find_element(By.ID, "btnFiltrar")
            btn.click()
        except Exception:
            logger.warning("Botão de pesquisa não encontrado, tentando continuar...")

    time.sleep(3)

    # Clica no botão de exportar Excel
    try:
        btn_excel = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//a[contains(@href,'Excel') or contains(text(),'Excel') or contains(@id,'Excel')]")
        ))
        btn_excel.click()
    except Exception:
        try:
            btn_excel = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//img[contains(@src,'excel') or contains(@alt,'Excel')]")
            ))
            btn_excel.click()
        except Exception as e:
            raise RuntimeError(f"Botão de exportar Excel não encontrado em {nome}: {e}")

    arquivo = aguardar_download()
    destino = DOWNLOAD_DIR / f"{nome}.xlsx"
    arquivo.rename(destino)
    logger.info(f"{nome} baixado: {destino}")
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
    logger.info(f"Baixando relatórios de {data_ini} a {data_fim}...")

    driver = criar_driver()
    wait   = WebDriverWait(driver, 20)

    try:
        fazer_login(driver, wait)
        path_vendas   = exportar_relatorio(driver, wait, URL_VENDAS,   data_ini, data_fim, "vendas")
        path_produtos = exportar_relatorio(driver, wait, URL_PRODUTOS, data_ini, data_fim, "produtos")
        return path_vendas, path_produtos
    finally:
        driver.quit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    v, p = baixar_relatorios()
    print(f"Vendas: {v}")
    print(f"Produtos: {p}")
