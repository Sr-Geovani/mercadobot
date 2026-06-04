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


async def fazer_login(page, email: str, senha: str):
    logger.info("Fazendo login no PDV Legal...")
    await page.goto(PDV_URL, wait_until="networkidle")
    await page.fill("#txtEmail", email)
    await page.fill("#txtSenha", senha)
    await page.click("#btnEntrar")

    try:
        await page.wait_for_url(lambda url: "loginpdvlegal" not in url, timeout=10000)
    except Exception:
        # Verifica se apareceu mensagem de erro na tela
        try:
            erro_visivel = await page.locator(".alert, .error, .msg-error").text_content(timeout=2000)
            if erro_visivel:
                raise ValueError(f"Login inválido: {erro_visivel.strip()}")
        except ValueError:
            raise
        except Exception:
            pass
        raise ValueError("Login inválido — verifique seu e-mail e senha do PDV Legal.")

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

    # Mapeia período para o data-range-key do PDV Legal
    from zoneinfo import ZoneInfo
    brasilia  = ZoneInfo("America/Sao_Paulo")
    agora_br  = datetime.now(brasilia)
    hoje      = agora_br.strftime("%d/%m/%Y")
    ontem     = (agora_br - timedelta(days=1)).strftime("%d/%m/%Y")
    d7        = (agora_br - timedelta(days=7)).strftime("%d/%m/%Y")
    d15       = (agora_br - timedelta(days=15)).strftime("%d/%m/%Y")
    d30       = (agora_br - timedelta(days=30)).strftime("%d/%m/%Y")
    mes_ini   = agora_br.replace(day=1).strftime("%d/%m/%Y")

    mapa = {
        (hoje,    hoje):  "Hoje",
        (ontem,   ontem): "Ontem",
        (d7,      hoje):  "Ultimos 7 dias",
        (d15,     hoje):  "Ultimos 15 dias",
        (d30,     hoje):  "Ultimos 30 dias",
    }
    range_key = mapa.get((data_ini, data_fim))

    # Fallback baseado no intervalo real
    if not range_key:
        try:
            d_ini_dt = datetime.strptime(data_ini, "%d/%m/%Y")
            d_fim_dt = datetime.strptime(data_fim, "%d/%m/%Y")
            delta    = (d_fim_dt - d_ini_dt).days
            if delta <= 1 and data_fim == hoje:   range_key = "Hoje"
            elif delta == 0 and data_fim == ontem: range_key = "Ontem"
            elif delta <= 7:                       range_key = "Ultimos 7 dias"
            elif delta <= 15:                      range_key = "Ultimos 15 dias"
            elif delta <= 30:                      range_key = "Ultimos 30 dias"
            elif delta <= 60:                      range_key = "Ultimos 60 dias"
            elif delta <= 90:                      range_key = "Ultimos 90 dias"
            else:                                  range_key = "Ultimos 90 dias"
        except Exception:
            range_key = "Ontem"
        logger.warning(f"Período ({data_ini} → {data_fim}) → '{range_key}'")

    logger.info(f"Produtos — range_key: '{range_key}'")

    # Abre o date picker e seleciona opção
    await page.click("#reportrange")
    await page.wait_for_timeout(800)
    await page.click(f"li[data-range-key='{range_key}']", timeout=5000)
    await page.wait_for_timeout(500)

    # Clica em Filtrar
    await page.click("#btnFiltro")
    # Meses têm mais dados — timeout maior
    wait_ms = 8000 if (data_ini != data_fim) else 3000
    await page.wait_for_timeout(wait_ms)

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


async def _baixar_async(data_ini: str, data_fim: str,
                        email: str = None, senha: str = None) -> tuple:
    from playwright.async_api import async_playwright

    # Usa credenciais passadas ou fallback para variáveis de ambiente
    _email = email or PDV_EMAIL
    _senha = senha or PDV_SENHA

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(accept_downloads=True)
        page    = await context.new_page()

        try:
            await fazer_login(page, _email, _senha)
            path_vendas   = await exportar_vendas(page, context, data_ini, data_fim)
            path_produtos = await exportar_produtos(page, context, data_ini, data_fim)
            return path_vendas, path_produtos
        finally:
            await browser.close()


def testar_login(email: str, senha: str) -> bool:
    """Testa se as credenciais são válidas. Retorna True se OK."""
    import asyncio as _asyncio

    async def _testar():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = await browser.new_page()
            try:
                await fazer_login(page, email, senha)
                return True
            except ValueError:
                return False
            except Exception:
                raise  # Propaga erros de conectividade
            finally:
                await browser.close()

    return _asyncio.run(_testar())


def baixar_relatorios(email: str = None, senha: str = None) -> tuple:
    """Baixa relatórios do dia anterior no horário de Brasília."""
    ontem = (agora_brasilia() - timedelta(days=1)).strftime("%d/%m/%Y")
    return baixar_relatorios_periodo(ontem, ontem, email, senha)


def baixar_relatorios_periodo(data_ini: str, data_fim: str,
                               email: str = None, senha: str = None) -> tuple:
    """Baixa os dois relatórios para o período com as credenciais informadas."""
    logger.info(f"Período: {data_ini} → {data_fim}")
    return asyncio.run(_baixar_async(data_ini, data_fim, email, senha))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    v, p = baixar_relatorios()
    print(f"Vendas:   {v}")
    print(f"Produtos: {p}")
# MercadoBot v1780365935
