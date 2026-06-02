"""
scraper.py — Automação PDV Legal com Playwright
Mais estável que Selenium no Railway.
"""
import os
import time
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BRASILIA = ZoneInfo("America/Sao_Paulo")

def agora_brasilia():
    return datetime.now(BRASILIA)

PDV_URL      = "https://pdvlegal.com.br/loginpdvlegal.aspx"
PDV_EMAIL    = os.environ.get("PDV_EMAIL")
PDV_SENHA    = os.environ.get("PDV_SENHA")
DOWNLOAD_DIR = Path("/tmp/pdvlegal")


async def fazer_login(page):
    logger.info("Fazendo login no PDV Legal...")
    await page.goto(PDV_URL, wait_until="networkidle")
    await page.fill("#txtEmail", PDV_EMAIL)
    await page.fill("#txtSenha", PDV_SENHA)
    await page.click("#btnEntrar")
    await page.wait_for_url(lambda url: "loginpdvlegal" not in url, timeout=15000)
    logger.info("Login realizado.")


async def exportar_vendas(page, context, data_ini: str, data_fim: str) -> Path:
    logger.info(f"Exportando Vendas: {data_ini} → {data_fim}")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    await page.goto(
        "https://pdvlegal.com.br/relatorios.aspx?relatorio=1",
        wait_until="networkidle"
    )
    await page.wait_for_timeout(1500)
    logger.info("Página de vendas carregada")

    # Preenche data início digitando e confirmando com Tab
    await page.click("#ContentPlaceHolder1_txtdatapadrao1")
    await page.wait_for_timeout(300)
    await page.keyboard.press("Control+a")
    await page.keyboard.type(data_ini, delay=80)
    await page.keyboard.press("Tab")
    await page.wait_for_timeout(500)

    # Preenche data fim digitando e confirmando com Tab
    await page.click("#ContentPlaceHolder1_txtdatapadrao2")
    await page.wait_for_timeout(300)
    await page.keyboard.press("Control+a")
    await page.keyboard.type(data_fim, delay=80)
    await page.keyboard.press("Tab")
    await page.wait_for_timeout(500)

    # Confirma valores
    val_ini = await page.evaluate("document.getElementById('ContentPlaceHolder1_txtdatapadrao1').value")
    val_fim = await page.evaluate("document.getElementById('ContentPlaceHolder1_txtdatapadrao2').value")
    logger.info(f"Datas confirmadas: ini='{val_ini}' fim='{val_fim}'")

    # Seleciona todas as lojas
    await page.select_option("#ContentPlaceHolder1_ddlfilialpadrao", value="0")
    await page.wait_for_timeout(500)
    logger.info("Todas as lojas selecionadas")

    # Clica em Gerar Relatório via JS
    logger.info("Clicando em Gerar Relatório...")
    async with page.expect_download(timeout=45000) as download_info:
        await page.evaluate(
            "document.getElementById('ContentPlaceHolder1_btnGerarRelatorio').click()"
        )

    download = await download_info.value
    destino = DOWNLOAD_DIR / "vendas.xlsx"
    await download.save_as(destino)
    logger.info(f"Vendas baixado: {destino}")
    return destino


async def exportar_produtos(page, context, data_ini: str, data_fim: str) -> Path:
    logger.info(f"Exportando Produtos: {data_ini} → {data_fim}")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    await page.goto(
        "https://pdvlegal.com.br/dashboard_produtos.aspx?relatorio=dp",
        wait_until="networkidle"
    )
    await page.wait_for_timeout(1500)

    # Mapeia período para o data-range-key
    hoje  = datetime.now().strftime("%d/%m/%Y")
    ontem = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
    d7    = (datetime.now() - timedelta(days=7)).strftime("%d/%m/%Y")
    d15   = (datetime.now() - timedelta(days=15)).strftime("%d/%m/%Y")
    d30   = (datetime.now() - timedelta(days=30)).strftime("%d/%m/%Y")

    mapa = {
        (hoje,  hoje):  "Hoje",
        (ontem, ontem): "Ontem",
        (d7,    hoje):  "Ultimos 7 dias",
        (d15,   hoje):  "Ultimos 15 dias",
        (d30,   hoje):  "Ultimos 30 dias",
    }
    range_key = mapa.get((data_ini, data_fim), "Ultimos 30 dias")

    # Abre o date picker e seleciona opção
    await page.click("#reportrange")
    await page.wait_for_timeout(500)
    await page.click(f"li[data-range-key='{range_key}']")
    await page.wait_for_timeout(500)

    # Clica em Filtrar
    await page.click("#btnFiltro")
    await page.wait_for_timeout(3000)

    # Abre modal de download
    await page.click("#imgDownload")
    await page.wait_for_timeout(1000)

    # Clica em Excel e aguarda download
    async with page.expect_download(timeout=45000) as download_info:
        await page.click("#ContentPlaceHolder1_ImageButton1")

    download = await download_info.value
    destino = DOWNLOAD_DIR / "produtos.xlsx"
    await download.save_as(destino)
    logger.info(f"Produtos baixado: {destino}")
    return destino


async def _baixar_async(data_ini: str, data_fim: str) -> tuple:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(accept_downloads=True)
        page    = await context.new_page()

        try:
            await fazer_login(page)
            path_vendas   = await exportar_vendas(page, context, data_ini, data_fim)
            path_produtos = await exportar_produtos(page, context, data_ini, data_fim)
            return path_vendas, path_produtos
        finally:
            await browser.close()


def baixar_relatorios() -> tuple:
    """Baixa relatórios do dia anterior no horário de Brasília."""
    ontem = (agora_brasilia() - timedelta(days=1)).strftime("%d/%m/%Y")
    return baixar_relatorios_periodo(ontem, ontem)


def baixar_relatorios_periodo(data_ini: str, data_fim: str) -> tuple:
    """Baixa os dois relatórios para o período. Síncrono para compatibilidade."""
    logger.info(f"Período: {data_ini} → {data_fim}")
    return asyncio.run(_baixar_async(data_ini, data_fim))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    v, p = baixar_relatorios()
    print(f"Vendas:   {v}")
    print(f"Produtos: {p}")
# MercadoBot v1780365935
