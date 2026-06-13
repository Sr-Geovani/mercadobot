"""
scraper.py — Automação PDV Legal com Playwright
"""
import os
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

PDV_URL      = "https://pdvlegal.com.br/loginpdvlegal.aspx"
PDV_EMAIL    = os.environ.get("PDV_EMAIL")
PDV_SENHA    = os.environ.get("PDV_SENHA")
DOWNLOAD_DIR = Path("/tmp/pdvlegal")
BRASILIA     = ZoneInfo("America/Sao_Paulo")


def agora_brasilia():
    return datetime.now(BRASILIA)


def testar_login(email: str, senha: str) -> bool:
    """Testa se as credenciais são válidas."""
    async def _testar():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = await browser.new_page()
            try:
                await fazer_login(page, email, senha)
                return True
            except ValueError:
                return False
            except Exception:
                raise
            finally:
                await browser.close()
    return asyncio.run(_testar())


async def fazer_login(page, email: str, senha: str):
    logger.info("Fazendo login no PDV Legal...")
    # Tenta até 2 vezes — PDV Legal pode estar lento
    for tentativa in range(2):
        try:
            await page.goto(PDV_URL, wait_until="domcontentloaded", timeout=60000)
            break
        except Exception as e:
            if tentativa == 0:
                logger.warning(f"Timeout no goto (tentativa 1) — tentando novamente")
                await page.wait_for_timeout(3000)
            else:
                raise
    await page.fill("#txtEmail", email)
    await page.fill("#txtSenha", senha)
    await page.click("#btnEntrar")
    try:
        await page.wait_for_url(lambda url: "loginpdvlegal" not in url, timeout=15000)
    except Exception:
        try:
            erro = await page.locator(".alert, .error, .msg-error").text_content(timeout=2000)
            if erro:
                raise ValueError(f"Login inválido: {erro.strip()}")
        except ValueError:
            raise
        except Exception:
            pass
        raise ValueError("Login inválido — verifique seu e-mail e senha do PDV Legal.")
    logger.info("Login realizado.")


async def exportar_vendas(page, data_ini: str, data_fim: str) -> Path:
    logger.info(f"Exportando Vendas: {data_ini} → {data_fim}")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    await page.goto("https://pdvlegal.com.br/relatorios.aspx?relatorio=1", wait_until="networkidle")
    await page.wait_for_timeout(1500)
    logger.info("Página de vendas carregada")

    # Preenche datas com click triplo + type (quirk do PDV Legal)
    for field_id, value in [
        ("ContentPlaceHolder1_txtdatapadrao1", data_ini),
        ("ContentPlaceHolder1_txtdatapadrao2", data_fim),
    ]:
        await page.click(f"#{field_id}", click_count=3)
        await page.type(f"#{field_id}", value)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(200)

    # Verifica datas preenchidas
    val_ini = await page.input_value("#ContentPlaceHolder1_txtdatapadrao1")
    val_fim = await page.input_value("#ContentPlaceHolder1_txtdatapadrao2")
    logger.info(f"Datas confirmadas: ini='{val_ini}' fim='{val_fim}'")

    # Seleciona todas as lojas
    await page.select_option("#ContentPlaceHolder1_ddlfilialpadrao", value="0")
    await page.wait_for_timeout(500)
    logger.info("Todas as lojas selecionadas")

    # Clica em Gerar Relatório
    logger.info("Clicando em Gerar Relatório...")
    await page.click("#ContentPlaceHolder1_btnGerarRelatorio")

    # Nova etapa: PDV Legal mostra modal SweetAlert com botão "Baixar Excel"
    try:
        await page.wait_for_selector(".swal-button--confirm", state="visible", timeout=10000)
        logger.info("Modal SweetAlert detectado — clicando em Baixar Excel...")
        async with page.expect_download(timeout=60000) as download_info:
            await page.click(".swal-button--confirm")
    except Exception:
        # Fallback: se não aparecer o modal, tenta download direto
        logger.info("Modal não detectado — aguardando download direto...")
        async with page.expect_download(timeout=60000) as download_info:
            await page.click("#ContentPlaceHolder1_btnGerarRelatorio")

    download = await download_info.value
    destino = DOWNLOAD_DIR / "vendas.xlsx"
    await download.save_as(destino)
    logger.info(f"Vendas baixado: {destino}")
    return destino


async def exportar_produtos(page, data_ini: str, data_fim: str) -> Path:
    logger.info(f"Exportando Produtos: {data_ini} → {data_fim}")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    await page.goto("https://pdvlegal.com.br/dashboard_produtos.aspx?relatorio=dp", wait_until="networkidle")
    await page.wait_for_timeout(1500)

    # Mapa de períodos padrão
    br = ZoneInfo("America/Sao_Paulo")
    agora = datetime.now(br)
    hoje  = agora.strftime("%d/%m/%Y")
    ontem = (agora - timedelta(days=1)).strftime("%d/%m/%Y")
    d7    = (agora - timedelta(days=7)).strftime("%d/%m/%Y")
    d15   = (agora - timedelta(days=15)).strftime("%d/%m/%Y")
    d30   = (agora - timedelta(days=30)).strftime("%d/%m/%Y")
    d60   = (agora - timedelta(days=60)).strftime("%d/%m/%Y")
    d90   = (agora - timedelta(days=90)).strftime("%d/%m/%Y")

    mapa = {
        (hoje,  hoje):  "Hoje",
        (ontem, ontem): "Ontem",
        (d7,    hoje):  "Ultimos 7 dias",
        (d15,   hoje):  "Ultimos 15 dias",
        (d30,   hoje):  "Ultimos 30 dias",
        (d60,   hoje):  "Ultimos 60 dias",
        (d90,   hoje):  "Ultimos 90 dias",
    }
    range_key = mapa.get((data_ini, data_fim))

    # Abre o date picker
    await page.click("#reportrange")
    await page.wait_for_timeout(500)

    if range_key:
        # Opção pré-definida
        logger.info(f"Produtos — range_key: '{range_key}'")
        await page.click(f"li[data-range-key='{range_key}']")
        await page.wait_for_timeout(500)
    else:
        # Período customizado via "Intervalo"
        logger.info(f"Produtos — usando Intervalo: {data_ini} → {data_fim}")
        await page.click("li[data-range-key='Intervalo']")
        await page.wait_for_timeout(1000)

        # Converte dd/mm/yyyy → mm/dd/yyyy para o datepicker americano
        def br_to_us(d):
            dd, mm, yyyy = d.split("/")
            return f"{mm}/{dd}/{yyyy}"

        ini_us = br_to_us(data_ini)
        fim_us = br_to_us(data_fim)

        # Preenche via JavaScript direto nos inputs do daterangepicker
        await page.evaluate(f"""
            var inputs = document.querySelectorAll('.daterangepicker input[type="text"]');
            if (inputs.length >= 2) {{
                inputs[0].value = '{ini_us}';
                inputs[1].value = '{fim_us}';
                inputs[0].dispatchEvent(new Event('change'));
                inputs[1].dispatchEvent(new Event('change'));
            }}
        """)
        await page.wait_for_timeout(500)

        # Clica no botão Apply
        await page.evaluate("""
            var btn = document.querySelector('.daterangepicker .applyBtn');
            if (btn) btn.click();
        """)
        await page.wait_for_timeout(800)
        logger.info(f"Intervalo aplicado: {ini_us} → {fim_us}")

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


async def exportar_cancelamentos(page, data_ini: str, data_fim: str) -> dict:
    """
    Retorna dict com cancelamentos por filial + total.
    Ex: {"CONDOMINIO FRANCA": 123.45, "CALIFÓRNIA": 67.89, "_total": 191.34}
    Seleciona cada filial individualmente e lê o LiteralFaturado.
    """
    logger.info(f"Buscando cancelamentos: {data_ini} -> {data_fim}")
    new_page = None
    resultado = {}
    try:
        context  = page.context
        new_page = await context.new_page()

        await new_page.goto(
            "https://pdvlegal.com.br/dashboard_vendas.aspx?tp=2",
            wait_until="networkidle"
        )
        await new_page.wait_for_timeout(2000)
        await new_page.wait_for_function("typeof $ !== 'undefined'", timeout=10000)
        await new_page.wait_for_timeout(500)

        # Injeta datas no daterangepicker
        ini_d, ini_m, ini_y = data_ini.split("/")
        fim_d, fim_m, fim_y = data_fim.split("/")
        await new_page.evaluate(f"""
            (function() {{
                var ini = moment('{ini_y}-{ini_m}-{ini_d}', 'YYYY-MM-DD');
                var fim = moment('{fim_y}-{fim_m}-{fim_d}', 'YYYY-MM-DD');
                var dr = $('#reportrange').data('daterangepicker');
                if (dr) {{
                    dr.setStartDate(ini);
                    dr.setEndDate(fim);
                    $('#reportrange span').html(ini.format('DD/MM/YYYY') + ' - ' + fim.format('DD/MM/YYYY'));
                }}
            }})();
        """)
        await new_page.wait_for_timeout(300)

        # Descobre as filiais disponíveis no select
        filiais_options = await new_page.evaluate("""
            Array.from(document.querySelectorAll('#ContentPlaceHolder1_ddlfilial option'))
                .map(o => ({value: o.value, text: o.text.trim()}))
        """)
        logger.info(f"Cancelamentos — filiais encontradas: {filiais_options}")

        await new_page.wait_for_function("typeof GetDadosProdutos === 'function'", timeout=10000)

        for opt in filiais_options:
            filial_val  = opt["value"]
            filial_nome = opt["text"]

            try:
                # Seleciona apenas essa filial via selectpicker
                await new_page.evaluate(f"""
                    $('#ContentPlaceHolder1_ddlfilial').val(['{filial_val}']);
                    $('#ContentPlaceHolder1_ddlfilial').selectpicker('refresh');
                """)
                await new_page.wait_for_timeout(300)

                # Filtra
                await new_page.evaluate("GetDadosProdutos();")
                await new_page.wait_for_timeout(4000)

                # Lê total cancelado
                val_str = await new_page.evaluate(
                    "document.getElementById('ContentPlaceHolder1_LiteralFaturado') ? "
                    "document.getElementById('ContentPlaceHolder1_LiteralFaturado').textContent.trim() : '0'"
                )
                try:
                    val = float(val_str.replace(".", "").replace(",", ".")) if val_str and val_str not in ("0", "0,00") else 0.0
                except ValueError:
                    val = 0.0

                resultado[filial_nome] = val
                logger.info(f"Cancelamentos — {filial_nome}: R$ {val:.2f}")

            except Exception as e_filial:
                logger.warning(f"Cancelamentos — erro na filial {filial_nome}: {e_filial}")
                resultado[filial_nome] = 0.0

        resultado["_total"] = sum(v for k, v in resultado.items() if not k.startswith("_"))
        logger.info(f"Cancelamentos — total: R$ {resultado['_total']:.2f}")
        await new_page.close()
        return resultado

    except Exception as e:
        logger.error(f"Erro ao buscar cancelamentos: {e}")
        if new_page:
            try:
                await new_page.close()
            except Exception:
                pass
        return {"_total": 0.0}


async def _baixar_async(data_ini: str, data_fim: str,
                        email: str = None, senha: str = None) -> tuple:
    """Executa login + downloads + cancelamentos em sequência."""
    from playwright.async_api import async_playwright

    _email = email or PDV_EMAIL
    _senha = senha or PDV_SENHA

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(accept_downloads=True)
        page    = await context.new_page()
        try:
            await fazer_login(page, _email, _senha)
            path_vendas   = await exportar_vendas(page, data_ini, data_fim)
            path_produtos = await exportar_produtos(page, data_ini, data_fim)
            total_cancel  = await exportar_cancelamentos(page, data_ini, data_fim)
            return path_vendas, path_produtos, total_cancel
        finally:
            await browser.close()


def baixar_relatorios(email: str = None, senha: str = None) -> tuple:
    """Baixa relatórios do dia anterior no horário de Brasília."""
    ontem = (datetime.now(BRASILIA) - timedelta(days=1)).strftime("%d/%m/%Y")
    return baixar_relatorios_periodo(ontem, ontem, email, senha)


def baixar_relatorios_periodo(data_ini: str, data_fim: str,
                               email: str = None, senha: str = None) -> tuple:
    """Baixa os relatórios e cancelamentos para o período. Retorna (path_vendas, path_produtos, total_cancel)."""
    logger.info(f"Período: {data_ini} → {data_fim}")
    return asyncio.run(_baixar_async(data_ini, data_fim, email, senha))


def garantir_browser():
    """Instala o Chromium do Playwright se necessário."""
    import subprocess
    try:
        result = subprocess.run(
            ["python", "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            logger.info("✅ Playwright Chromium instalado com sucesso.")
        else:
            logger.warning(f"Playwright install: {result.stderr[:200]}")
    except Exception as e:
        logger.error(f"Erro ao instalar Playwright: {e}")
