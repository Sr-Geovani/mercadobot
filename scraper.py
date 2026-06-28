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
        # "Últimos N dias" do PDV Legal terminam ONTEM (não incluem hoje).
        # O bot agora manda o fim como ontem, então casamos com o range_key
        # pré-definido (caminho mais confiável que o Intervalo customizado).
        (d7,    ontem): "Ultimos 7 dias",
        (d15,   ontem): "Ultimos 15 dias",
        (d30,   ontem): "Ultimos 30 dias",
        (d60,   ontem): "Ultimos 60 dias",
        (d90,   ontem): "Ultimos 90 dias",
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
        # Período customizado. Em vez de preencher os inputs de texto (frágil —
        # o daterangepicker ignora e fica só na data final), usamos a API do
        # próprio daterangepicker via moment.js + evento apply. É o MESMO
        # mecanismo que funciona de forma confiável no exportar_cancelamentos.
        logger.info(f"Produtos — usando Intervalo via API daterangepicker: {data_ini} → {data_fim}")
        ini_d, ini_m, ini_y = data_ini.split("/")
        fim_d, fim_m, fim_y = data_fim.split("/")
        aplicado = await page.evaluate(f"""
            (function() {{
                if (typeof moment === 'undefined' || typeof $ === 'undefined') return 'sem_libs';
                var ini = moment('{ini_y}-{ini_m}-{ini_d}', 'YYYY-MM-DD');
                var fim = moment('{fim_y}-{fim_m}-{fim_d}', 'YYYY-MM-DD');
                var el  = $('#reportrange');
                var dr  = el.data('daterangepicker');
                if (!dr) return 'sem_daterangepicker';
                dr.setStartDate(ini);
                dr.setEndDate(fim);
                el.find('span').html(ini.format('DD/MM/YYYY') + ' - ' + fim.format('DD/MM/YYYY'));
                el.trigger('apply.daterangepicker', dr);
                return dr.startDate.format('DD/MM/YYYY') + ' - ' + dr.endDate.format('DD/MM/YYYY');
            }})();
        """)
        logger.info(f"Produtos — Intervalo aplicado via API: {aplicado}")
        await page.wait_for_timeout(800)

    # Clica em Filtrar
    await page.click("#btnFiltro")
    await page.wait_for_timeout(3000)

    # Verificação de segurança CRÍTICA: confirma que o período aplicado na tela
    # bate com o solicitado ANTES de baixar. Se divergir, reaplica via API e
    # tenta de novo (até 3x). Sem isso, o relatório de produtos sai com período
    # errado (ex: só hoje em vez do mês inteiro) — foi o bug do "mês atual".
    ini_d, ini_m, ini_y = data_ini.split("/")
    fim_d, fim_m, fim_y = data_fim.split("/")
    esperado = f"{data_ini} - {data_fim}"
    periodo_ok = False
    for tentativa in range(3):
        try:
            periodo_exibido = await page.evaluate(
                "document.querySelector('#reportrange span') ? "
                "document.querySelector('#reportrange span').textContent.trim() : ''"
            )
        except Exception:
            periodo_exibido = ""

        if periodo_exibido == esperado:
            periodo_ok = True
            logger.info(f"Produtos — período confirmado na tela: {periodo_exibido}")
            break

        logger.warning(
            f"Produtos — período exibido '{periodo_exibido}' != esperado '{esperado}' "
            f"(tentativa {tentativa+1}/3). Reaplicando via API..."
        )
        await page.evaluate(f"""
            (function() {{
                if (typeof moment === 'undefined' || typeof $ === 'undefined') return;
                var ini = moment('{ini_y}-{ini_m}-{ini_d}', 'YYYY-MM-DD');
                var fim = moment('{fim_y}-{fim_m}-{fim_d}', 'YYYY-MM-DD');
                var el  = $('#reportrange');
                var dr  = el.data('daterangepicker');
                if (dr) {{
                    dr.setStartDate(ini);
                    dr.setEndDate(fim);
                    el.find('span').html(ini.format('DD/MM/YYYY') + ' - ' + fim.format('DD/MM/YYYY'));
                    el.trigger('apply.daterangepicker', dr);
                }}
            }})();
        """)
        await page.wait_for_timeout(500)
        await page.click("#btnFiltro")
        await page.wait_for_timeout(2500)

    if not periodo_ok:
        logger.error(
            f"Produtos — NÃO foi possível aplicar o período {esperado} após 3 tentativas. "
            f"O relatório pode sair incorreto."
        )

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
    Carrega todas as filiais de uma vez e lê a tabela do DOM — mais rápido e confiável.
    """
    logger.info(f"Buscando cancelamentos: {data_ini} -> {data_fim}")
    new_page = None
    resultado = {}
    try:
        context  = page.context
        new_page = await context.new_page()

        await new_page.goto(
            "https://pdvlegal.com.br/dashboard_vendas.aspx?tp=2",
            wait_until="domcontentloaded",
            timeout=60000
        )
        await new_page.wait_for_function("typeof $ !== 'undefined'", timeout=10000)
        await new_page.wait_for_timeout(1000)

        # Injeta datas via daterangepicker API e dispara o evento que o PDV Legal escuta
        ini_d, ini_m, ini_y = data_ini.split("/")
        fim_d, fim_m, fim_y = data_fim.split("/")
        data_aplicada = await new_page.evaluate(f"""
            (function() {{
                var ini = moment('{ini_y}-{ini_m}-{ini_d}', 'YYYY-MM-DD');
                var fim = moment('{fim_y}-{fim_m}-{fim_d}', 'YYYY-MM-DD');
                var el  = $('#reportrange');
                var dr  = el.data('daterangepicker');
                if (!dr) return 'sem_daterangepicker';
                dr.setStartDate(ini);
                dr.setEndDate(fim);
                el.find('span').html(ini.format('DD/MM/YYYY') + ' - ' + fim.format('DD/MM/YYYY'));
                // Dispara o evento que o PDV Legal escuta para atualizar variáveis internas
                el.trigger('apply.daterangepicker', dr);
                return dr.startDate.format('DD/MM/YYYY') + ' - ' + dr.endDate.format('DD/MM/YYYY');
            }})();
        """)
        logger.info(f"Cancelamentos — daterangepicker aplicado: {data_aplicada}")
        await new_page.wait_for_timeout(500)

        # Garante todas as filiais selecionadas
        await new_page.evaluate("""
            var opts = Array.from(document.querySelectorAll('#ContentPlaceHolder1_ddlfilial option'))
                           .map(o => o.value);
            $('#ContentPlaceHolder1_ddlfilial').val(opts);
            $('#ContentPlaceHolder1_ddlfilial').selectpicker('refresh');
        """)
        await new_page.wait_for_timeout(300)

        # Aguarda GetDadosProdutos disponível e filtra
        await new_page.wait_for_function("typeof GetDadosProdutos === 'function'", timeout=10000)
        await new_page.evaluate("GetDadosProdutos();")
        await new_page.wait_for_timeout(800)

        # Verificação de segurança CRÍTICA: confirma que o período exibido na
        # tela é exatamente o solicitado antes de ler qualquer dado. Se não
        # bater, reaplica o daterangepicker + GetDadosProdutos e tenta de novo.
        # Sem isso, a tela pode ficar no período padrão (hoje) e retornar o
        # cancelamento do dia errado — foi o bug "briefing de ontem trouxe
        # cancelamento de hoje".
        esperado = f"{data_ini} - {data_fim}"
        periodo_ok = False
        for tentativa in range(3):
            periodo_exibido = await new_page.evaluate(
                "document.querySelector('#reportrange span') ? "
                "document.querySelector('#reportrange span').textContent.trim() : ''"
            )
            if periodo_exibido == esperado:
                periodo_ok = True
                break
            logger.warning(
                f"Cancelamentos — período exibido '{periodo_exibido}' != esperado "
                f"'{esperado}' (tentativa {tentativa+1}/3). Reaplicando período..."
            )
            # Reaplica o daterangepicker do zero e refiltra
            await new_page.evaluate(f"""
                (function() {{
                    var ini = moment('{ini_y}-{ini_m}-{ini_d}', 'YYYY-MM-DD');
                    var fim = moment('{fim_y}-{fim_m}-{fim_d}', 'YYYY-MM-DD');
                    var el  = $('#reportrange');
                    var dr  = el.data('daterangepicker');
                    if (dr) {{
                        dr.setStartDate(ini);
                        dr.setEndDate(fim);
                        el.find('span').html(ini.format('DD/MM/YYYY') + ' - ' + fim.format('DD/MM/YYYY'));
                        el.trigger('apply.daterangepicker', dr);
                    }}
                }})();
            """)
            await new_page.wait_for_timeout(500)
            await new_page.evaluate("GetDadosProdutos();")
            await new_page.wait_for_timeout(1500)

        if not periodo_ok:
            logger.error(
                f"Cancelamentos — NÃO foi possível aplicar o período {esperado} após 3 tentativas. "
                f"Retornando zero para evitar reportar cancelamento do período errado."
            )
            await new_page.close()
            return {"_total": 0.0}

        # Aguarda tabela carregar com dados reais (até 6s — reduzido pois pode legitimamente estar vazia)
        tabela_tem_dados = True
        try:
            await new_page.wait_for_function(
                "document.querySelectorAll('#gdvPaged tbody tr').length > 0 && "
                "document.querySelector('#gdvPaged tbody tr td') && "
                "document.querySelector('#gdvPaged tbody tr td').textContent.trim().length > 0",
                timeout=6000
            )
        except Exception:
            tabela_tem_dados = False

        # Estratégia: o botão "Mais" (GetRecords) ANEXA linhas à tabela —
        # ela cresce a cada clique. Então primeiro carregamos TODAS as páginas
        # (clicando em "Mais" até ele sumir), e só depois lemos a tabela
        # inteira de uma vez, somando TODAS as linhas SEM deduplicação.
        # IMPORTANTE: não deduplica por conteúdo — dois cancelamentos legítimos
        # podem ser idênticos (mesma data/filial/valor/produto), e descartá-los
        # como "duplicata" fazia o total vir a menos em períodos longos (bug
        # que aparecia a partir de ~15 dias, quando há mais linhas repetidas).
        if tabela_tem_dados:
            max_paginas = 100  # rede de segurança contra loop infinito
            for pagina in range(max_paginas):
                tem_botao_mais = await new_page.evaluate("""
                    (function() {
                        var btn = document.getElementById('Mais');
                        if (!btn) return false;
                        var estilo = window.getComputedStyle(btn);
                        return estilo.display !== 'none' && !btn.classList.contains('escondido');
                    })()
                """)
                if not tem_botao_mais:
                    break

                n_antes = await new_page.evaluate(
                    "document.querySelectorAll('#gdvPaged tbody tr').length"
                )
                await new_page.evaluate("if (typeof GetRecords === 'function') GetRecords();")
                # Aguarda a tabela crescer (novas linhas anexadas)
                try:
                    await new_page.wait_for_function(
                        f"document.querySelectorAll('#gdvPaged tbody tr').length > {n_antes}",
                        timeout=8000
                    )
                except Exception:
                    logger.info(f"Cancelamentos — paginação parou na página {pagina + 1} (sem novas linhas)")
                    break
            else:
                logger.warning(f"Cancelamentos — atingiu limite de {max_paginas} páginas")

            # Agora soma a tabela COMPLETA de uma vez, todas as linhas, sem dedup
            dados = await new_page.evaluate("""
                (function() {
                    var rows = document.querySelectorAll('#gdvPaged tbody tr');
                    var por_filial = {};
                    var total = 0;
                    var n = 0;
                    rows.forEach(function(row) {
                        var cells = row.querySelectorAll('td');
                        if (cells.length < 6) return;
                        var filial = cells[2].textContent.trim();
                        var valStr = cells[5].textContent.trim().replace(/[.]/g,'').replace(',','.');
                        var val = parseFloat(valStr);
                        if (isNaN(val) || val <= 0) return;
                        por_filial[filial] = (por_filial[filial] || 0) + val;
                        total += val;
                        n += 1;
                    });
                    return {por_filial: por_filial, total: parseFloat(total.toFixed(2)), n_linhas: n};
                })()
            """)
            logger.info(
                f"Cancelamentos — {dados.get('n_linhas', 0)} linhas somadas (sem dedup), "
                f"total R$ {dados.get('total', 0.0):.2f}"
            )
        else:
            dados = {"por_filial": {}, "total": 0.0}

        if not tabela_tem_dados:
            # Diagnóstico: confirma se é "sem cancelamentos" ou erro real
            diag = await new_page.evaluate("""
                (function() {
                    var faturado = document.getElementById('ContentPlaceHolder1_LiteralFaturado');
                    var nrows = document.querySelectorAll('#gdvPaged tbody tr').length;
                    var msgVazio = document.body.innerText.includes('Nenhuma') || document.body.innerText.includes('nenhum');
                    return {
                        literal_faturado: faturado ? faturado.textContent.trim() : null,
                        n_rows: nrows,
                        tem_msg_vazio: msgVazio
                    };
                })()
            """)
            logger.info(f"Cancelamentos — tabela vazia, diagnóstico: {diag}")

        resultado = dados.get("por_filial", {})
        soma_tabela = dados.get("total", 0.0)

        # FONTE DA VERDADE DO TOTAL: a soma da TABELA, que está sincronizada
        # com o período filtrado (GetDadosProdutos após apply.daterangepicker)
        # e agora carrega todas as páginas (paginação corrigida) — então não
        # trunca mais. O card "Total cancelado" da tela NÃO é confiável como
        # fonte primária porque pode refletir o período padrão da tela (hoje),
        # não o período que pedimos — foi o que causou "briefing de ontem
        # trazendo cancelamento de hoje". Usamos o card apenas para VALIDAR.
        resultado["_total"] = round(soma_tabela, 2)

        # Validação: lê o card e, se divergir muito da tabela, apenas registra
        # um aviso no log (não sobrescreve — a tabela é quem respeita o período).
        total_card = None
        try:
            total_oficial = await new_page.evaluate("""
                (function() {
                    var cards = document.querySelectorAll('.info-box-number');
                    for (var c of cards) {
                        var box = c.closest('.info-box');
                        var label = box ? box.querySelector('.info-box-text') : null;
                        if (label && label.textContent.toLowerCase().includes('cancelad')) {
                            return c.textContent.trim();
                        }
                    }
                    return null;
                })()
            """)
            if total_oficial:
                limpo = total_oficial.replace("R$", "").replace(".", "").replace(",", ".").strip()
                total_card = float(limpo) if limpo else None
        except Exception:
            total_card = None

        if total_card is not None and soma_tabela > 0 and abs(soma_tabela - total_card) > 0.01:
            logger.info(
                f"Cancelamentos — soma da tabela (R$ {soma_tabela:.2f}, período {data_ini}-{data_fim}) "
                f"difere do card da tela (R$ {total_card:.2f}). Usando a tabela, que respeita o "
                f"período filtrado (o card pode estar no período padrão da tela)."
            )

        # Fallback: se a tabela veio vazia/zerada, tenta LiteralFaturado
        if resultado["_total"] == 0:
            val_str = await new_page.evaluate(
                "document.getElementById('ContentPlaceHolder1_LiteralFaturado') ? "
                "document.getElementById('ContentPlaceHolder1_LiteralFaturado').textContent.trim() : '0'"
            )
            try:
                total_fb = float(val_str.replace(".", "").replace(",", ".")) if val_str not in ("0", "0,00", "") else 0.0
                if total_fb > 0:
                    resultado["_total"] = total_fb
                    logger.info(f"Cancelamentos — fallback LiteralFaturado: R$ {total_fb:.2f}")
            except ValueError:
                pass

        for k, v in resultado.items():
            if not k.startswith("_"):
                logger.info(f"Cancelamentos — {k}: R$ {v:.2f}")
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
