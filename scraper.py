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

PDV_URL      = "https://admin.pdvlegal.com.br/loginpdvlegal.aspx"
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


async def exportar_vendas(page, data_ini: str, data_fim: str, sessao_dir: Path = None) -> Path:
    """
    ┌─────────────────────────────────────────────────────────────────────┐
    │ LÓGICA-BASE DO SISTEMA — NÃO ALTERAR sem necessidade absoluta.       │
    │ O faturamento foi validado contra o PDV Legal e BATE. O fluxo que    │
    │ funciona: preencher os campos de data por DIGITAÇÃO direta           │
    │ (txtdatapadrao1/2) com click triplo + type, confirmar os valores     │
    │ lidos de volta, selecionar todas as lojas e gerar o relatório Excel. │
    │ NÃO trocar por datepicker/daterangepicker aqui — esta tela tem       │
    │ campos de data próprios e a digitação direta é o método estável.     │
    └─────────────────────────────────────────────────────────────────────┘
    """
    logger.info(f"Exportando Vendas: {data_ini} → {data_fim}")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    await page.goto("https://admin.pdvlegal.com.br/relatorios.aspx?relatorio=1", wait_until="networkidle")
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
    destino = (sessao_dir or DOWNLOAD_DIR) / "vendas.xlsx"
    await download.save_as(destino)
    logger.info(f"Vendas baixado: {destino}")
    return destino


async def exportar_produtos(page, data_ini: str, data_fim: str, sessao_dir: Path = None) -> Path:
    logger.info(f"Exportando Produtos: {data_ini} → {data_fim}")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    await page.goto("https://admin.pdvlegal.com.br/dashboard_produtos.aspx?relatorio=dp", wait_until="networkidle")
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
        # Após aplicar via API, dispara o filtro explicitamente e aguarda a
        # tabela recarregar. O trigger apply.daterangepicker nem sempre aciona
        # o callback interno de recálculo; clicar em Filtrar garante isso.
        try:
            await page.click("#btnFiltro")
            await page.wait_for_timeout(3000)
        except Exception:
            await page.evaluate("if (typeof GetDadosProdutos === 'function') GetDadosProdutos();")
            await page.wait_for_timeout(3000)

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

    # Diagnóstico: conta as linhas da tabela de produtos na tela antes de
    # baixar. Se a tela tiver poucos produtos, o problema é o filtro/recálculo;
    # se a tela tiver muitos mas o Excel vier truncado, o problema é o download.
    try:
        n_linhas_tela = await page.evaluate("""
            (function() {
                var sels = ['#gdvPaged tbody tr', '#ContentPlaceHolder1_GridView1 tr',
                            'table.table tbody tr', '.dataTable tbody tr'];
                for (var i = 0; i < sels.length; i++) {
                    var n = document.querySelectorAll(sels[i]).length;
                    if (n > 0) return {seletor: sels[i], linhas: n};
                }
                return {seletor: null, linhas: 0};
            })()
        """)
        logger.info(f"Produtos — linhas na tabela da tela antes do download: {n_linhas_tela}")
    except Exception as e:
        logger.warning(f"Produtos — não foi possível contar linhas da tela: {e}")

    # Abre modal de download
    await page.click("#imgDownload")
    await page.wait_for_timeout(1000)

    # Clica em Excel e aguarda download
    async with page.expect_download(timeout=45000) as download_info:
        await page.click("#ContentPlaceHolder1_ImageButton1")

    download = await download_info.value
    destino = (sessao_dir or DOWNLOAD_DIR) / "produtos.xlsx"
    await download.save_as(destino)
    logger.info(f"Produtos baixado: {destino}")
    return destino


async def exportar_cancelamentos(page, data_ini: str, data_fim: str, sessao_dir: Path = None) -> dict:
    """
    Retorna dict com cancelamentos por filial + total.

    ┌─────────────────────────────────────────────────────────────────────┐
    │ LÓGICA-BASE DO SISTEMA — NÃO ALTERAR sem necessidade absoluta.       │
    │ Esta abordagem foi validada contra o dashboard do PDV Legal e os     │
    │ valores BATEM (total e por filial). O fluxo correto é:               │
    │  1. Confirmar o período na tela (com retry) ANTES de ler nada.       │
    │  2. Iterar filial por filial: selecionar UMA, reaplicar período,     │
    │     CLICAR no botão #btnFiltro (é o clique que recalcula o card —    │
    │     chamar GetDadosProdutos() via JS NÃO basta).                     │
    │  3. Ler o valor do card "vendas com cancelamentos": como não há      │
    │     .info-box-number, pega-se o MAIOR valor monetário no bloco       │
    │     rotulado com "cancelamentos" (subindo no máx. 2 níveis no DOM    │
    │     para não capturar o faturamento).                                │
    │  4. Total = soma dos valores reais de cada filial.                   │
    │ Qualquer mudança aqui deve ser testada filial a filial contra o PDV  │
    │ Legal antes de subir.                                                │
    └─────────────────────────────────────────────────────────────────────┘
    """
    logger.info(f"Buscando cancelamentos: {data_ini} -> {data_fim}")
    new_page = None
    resultado = {}
    try:
        context  = page.context
        new_page = await context.new_page()

        await new_page.goto(
            "https://admin.pdvlegal.com.br/dashboard_vendas.aspx?tp=2",
            wait_until="domcontentloaded",
            timeout=60000
        )
        await new_page.wait_for_function("typeof $ !== 'undefined'", timeout=10000)
        await new_page.wait_for_timeout(1000)

        ini_d, ini_m, ini_y = data_ini.split("/")
        fim_d, fim_m, fim_y = data_fim.split("/")

        # ── Funções auxiliares ────────────────────────────────────────────
        def _aplica_periodo_js() -> str:
            return f"""
                (function() {{
                    var ini = moment('{ini_y}-{ini_m}-{ini_d}', 'YYYY-MM-DD');
                    var fim = moment('{fim_y}-{fim_m}-{fim_d}', 'YYYY-MM-DD');
                    var el  = $('#reportrange');
                    var dr  = el.data('daterangepicker');
                    if (!dr) return 'sem_dr';
                    dr.setStartDate(ini);
                    dr.setEndDate(fim);
                    el.find('span').html(ini.format('DD/MM/YYYY') + ' - ' + fim.format('DD/MM/YYYY'));
                    el.trigger('apply.daterangepicker', dr);
                    return dr.startDate.format('DD/MM/YYYY') + ' - ' + dr.endDate.format('DD/MM/YYYY');
                }})();
            """

        async def _confirma_periodo() -> bool:
            """Garante que a tela está no período pedido (com retry)."""
            esperado_local = f"{data_ini} - {data_fim}"
            for _ in range(3):
                exibido = await new_page.evaluate(
                    "document.querySelector('#reportrange span') ? "
                    "document.querySelector('#reportrange span').textContent.trim() : ''"
                )
                if exibido == esperado_local:
                    return True
                await new_page.evaluate(_aplica_periodo_js())
                await new_page.wait_for_timeout(500)
                await new_page.evaluate("if (typeof GetDadosProdutos === 'function') GetDadosProdutos();")
                await new_page.wait_for_timeout(1200)
            return False

        async def _ler_card_cancelado(diagnostico=False) -> float:
            """
            Lê o card 'vendas com cancelamentos' da tela. Tenta várias
            estratégias de seletor porque a estrutura HTML pode variar.
            Se diagnostico=True, loga a estrutura real dos cards encontrados.
            """
            dados_card = await new_page.evaluate("""
                (function() {
                    var resultado = {valor: null, diag_boxes: [], diag_cancel: []};

                    // Captura TODOS os info-box-number com o label do seu box
                    var nums = document.querySelectorAll('.info-box-number');
                    for (var i = 0; i < nums.length; i++) {
                        var c = nums[i];
                        var box = c.closest('.info-box');
                        var label = '';
                        if (box) {
                            var lblEl = box.querySelector('.info-box-text');
                            if (lblEl) label = lblEl.textContent.trim();
                        }
                        resultado.diag_boxes.push({valor: c.textContent.trim(), label: label});
                        var lbl = label.toLowerCase();
                        if (lbl.includes('cancelament') || (lbl.includes('cancelad') && !lbl.includes('estorn'))) {
                            resultado.valor = c.textContent.trim();
                        }
                    }
                    if (resultado.valor) return resultado;

                    // Fallback robusto: o card "vendas com cancelamentos" tem o
                    // texto-label e o número-valor próximos na árvore DOM. Para
                    // cada elemento que contém EXATAMENTE o label de cancelamento
                    // (texto curto, não um container gigante), subimos só alguns
                    // níveis e pegamos o maior valor monetário ali perto — que é
                    // o número do card (não uma linha de tabela solta).
                    function valoresEm(el) {
                        var txt = el ? (el.textContent || '') : '';
                        var matches = txt.match(/[\\d]{1,3}(?:[.][\\d]{3})*,[\\d]{2}/g) || [];
                        return matches.map(function(s){
                            return parseFloat(s.replace(/[.]/g,'').replace(',','.'));
                        });
                    }

                    var melhores = [];
                    var todos = document.querySelectorAll('div, span, h3, h4, p, td, b, strong');
                    for (var j = 0; j < todos.length; j++) {
                        var el = todos[j];
                        var txt = (el.textContent || '');
                        var low = txt.toLowerCase();
                        // Label do card: texto curto que fala de cancelamento mas
                        // NÃO é "nenhuma venda..." e não é um container enorme.
                        if (low.includes('cancelament') && txt.length < 40 && !low.includes('nenhuma')) {
                            // Sobe até 2 níveis procurando valores monetários
                            // (mais que isso arrisca pegar o faturamento geral)
                            var node = el;
                            for (var up = 0; up < 3 && node; up++) {
                                var vals = valoresEm(node);
                                if (vals.length > 0) {
                                    var maxv = Math.max.apply(null, vals);
                                    melhores.push(maxv);
                                    resultado.diag_cancel.push({
                                        contexto: txt.trim().substring(0, 40),
                                        nivel: up,
                                        valores: vals,
                                        escolhido: maxv
                                    });
                                    break;
                                }
                                node = node.parentElement;
                            }
                        }
                    }
                    if (melhores.length > 0) {
                        // O card oficial é o maior valor encontrado entre os
                        // blocos rotulados com "cancelamentos".
                        var v = Math.max.apply(null, melhores);
                        resultado.valor = v.toFixed(2).replace('.', ',');
                    }
                    resultado.url = window.location.href;
                    return resultado;
                })()
            """)

            if diagnostico:
                logger.info(f"Cancelamentos — DIAG url: {dados_card.get('url')}")
                logger.info(f"Cancelamentos — DIAG info-boxes: {dados_card.get('diag_boxes')}")
                logger.info(f"Cancelamentos — DIAG contexto 'cancelament': {dados_card.get('diag_cancel')}")
                # Diagnóstico extra: TODOS os valores monetários >= 100 da página,
                # com o id/classe/tag do elemento — para localizar onde está o
                # total real (ex: 2.357,23) que o seletor atual não acha.
                mapa_valores = await new_page.evaluate("""
                    (function() {
                        var out = [];
                        var todos = document.querySelectorAll('div, span, h3, h4, p, td, b, strong, a, li');
                        for (var i = 0; i < todos.length; i++) {
                            var el = todos[i];
                            // só folhas (sem filhos com texto) para não duplicar
                            if (el.children.length > 0) continue;
                            var t = (el.textContent || '').trim();
                            var m = t.match(/^R?\\$?\\s*([\\d]{1,3}(?:[.][\\d]{3})*,[\\d]{2})$/);
                            if (!m) continue;
                            var num = parseFloat(m[1].replace(/[.]/g,'').replace(',','.'));
                            if (num < 100) continue;
                            var pai = el.parentElement;
                            out.push({
                                valor: t,
                                tag: el.tagName,
                                id: el.id || (pai ? pai.id : '') || '',
                                cls: (el.className || '').toString().substring(0,40)
                            });
                        }
                        return out;
                    })()
                """)
                logger.info(f"Cancelamentos — DIAG valores >= 100 na pagina: {mapa_valores}")

            txt = dados_card.get("valor")
            if not txt:
                return 0.0
            try:
                return float(txt.replace("R$", "").replace(".", "").replace(",", ".").strip())
            except ValueError:
                return 0.0

        # ── Lê a lista de filiais disponíveis ──────────────────────────────
        filiais = await new_page.evaluate("""
            (function() {
                var opts = document.querySelectorAll('#ContentPlaceHolder1_ddlfilial option');
                return Array.from(opts).map(function(o) {
                    return {value: o.value, nome: o.textContent.trim()};
                }).filter(function(o) { return o.value !== ''; });
            })()
        """)
        logger.info(f"Cancelamentos — {len(filiais)} filiais encontradas no filtro")

        # ── Confirma o período antes de qualquer leitura ───────────────────
        await new_page.wait_for_function("typeof GetDadosProdutos === 'function'", timeout=10000)
        if not await _confirma_periodo():
            logger.error(
                f"Cancelamentos — não foi possível aplicar o período {data_ini}-{data_fim}. "
                f"Retornando zero para não reportar período errado."
            )
            await new_page.close()
            return {"_total": 0.0}

        resultado = {}

        # Função que clica no botão Filtrar real (id=btnFiltro, onclick=GetDadosProdutos).
        # Clicar no botão é o que REALMENTE recalcula o card — chamar GetDadosProdutos()
        # direto via JS nem sempre atualiza o card "vendas com cancelamentos".
        async def _clicar_filtrar():
            try:
                await new_page.click("#btnFiltro", timeout=5000)
            except Exception:
                # Fallback: chama a função diretamente se o botão não for clicável
                await new_page.evaluate("if (typeof GetDadosProdutos === 'function') GetDadosProdutos();")

        # ── PASSO 1: lê o TOTAL com TODAS as filiais selecionadas ──────────
        # O diagnóstico mostrou que NÃO existe '.info-box-number' nesta tela e
        # que o número correto fica perto do texto "vendas com cancelamentos".
        # Com todas as filiais, o PDV Legal calcula o total certo (bate com o
        # dashboard). Esse é o TOTAL OFICIAL — exato.
        await new_page.evaluate("""
            (function() {
                var opts = Array.from(document.querySelectorAll('#ContentPlaceHolder1_ddlfilial option'))
                               .map(function(o){ return o.value; })
                               .filter(function(v){ return v !== ''; });
                var $sel = $('#ContentPlaceHolder1_ddlfilial');
                $sel.val(opts);
                $sel.selectpicker('refresh');
                $sel.trigger('change');
            })();
        """)
        await new_page.wait_for_timeout(300)
        await new_page.evaluate(_aplica_periodo_js())
        await new_page.wait_for_timeout(300)
        await _clicar_filtrar()
        await new_page.wait_for_timeout(1800)

        total_oficial = 0.0
        for tentativa in range(4):
            total_oficial = await _ler_card_cancelado(diagnostico=(tentativa == 0))
            if total_oficial > 0:
                break
            await new_page.wait_for_timeout(1200)
        logger.info(f"Cancelamentos — TOTAL oficial (todas filiais): R$ {total_oficial:.2f}")

        # ── PASSO 2: lê cada filial individualmente para obter a PROPORÇÃO ──
        # As leituras por filial podem ser imperfeitas, mas servem para ratear
        # o total oficial proporcionalmente entre as filiais.
        prop_filial = {}
        if filiais:
            for fil in filiais:
                try:
                    await new_page.evaluate(f"""
                        (function() {{
                            var $sel = $('#ContentPlaceHolder1_ddlfilial');
                            $sel.val(['{fil["value"]}']);
                            $sel.selectpicker('refresh');
                            $sel.trigger('change');
                        }})();
                    """)
                    await new_page.wait_for_timeout(300)
                    await new_page.evaluate(_aplica_periodo_js())
                    await new_page.wait_for_timeout(300)
                    await _clicar_filtrar()
                    await new_page.wait_for_timeout(1500)

                    valor_fil = 0.0
                    for _ in range(3):
                        valor_fil = await _ler_card_cancelado()
                        if valor_fil > 0:
                            break
                        await new_page.wait_for_timeout(900)
                    prop_filial[fil["nome"]] = valor_fil
                    logger.info(f"Cancelamentos — {fil['nome']}: R$ {valor_fil:.2f} (leitura individual)")
                except Exception as e:
                    logger.warning(f"Cancelamentos — erro ao ler filial {fil.get('nome')}: {e}")

        # ── PASSO 3: monta o resultado. Total = oficial (exato). Filiais =
        # rateadas proporcionalmente às leituras individuais. Se o total
        # oficial não veio, cai na soma das leituras individuais. ──────────
        soma_individual = sum(prop_filial.values())
        if total_oficial > 0:
            if soma_individual > 0:
                fator = total_oficial / soma_individual
                for nome_fil, val in prop_filial.items():
                    resultado[nome_fil] = round(val * fator, 2)
            resultado["_total"] = round(total_oficial, 2)
            if abs(soma_individual - total_oficial) > 0.01:
                logger.info(
                    f"Cancelamentos — soma das filiais (R$ {soma_individual:.2f}) ajustada ao "
                    f"total oficial (R$ {total_oficial:.2f})."
                )
        else:
            for nome_fil, val in prop_filial.items():
                resultado[nome_fil] = round(val, 2)
            resultado["_total"] = round(soma_individual, 2)
            logger.warning(
                f"Cancelamentos — total oficial não lido; usando soma das filiais R$ {soma_individual:.2f}"
            )

        for k, v in resultado.items():
            if not k.startswith("_"):
                logger.info(f"Cancelamentos — {k}: R$ {v:.2f}")
        logger.info(f"Cancelamentos — total: R$ {resultado['_total']:.2f}")

        # ── DETALHE POR LINHA (produto + hora) ──────────────────────────────
        # Seleciona todas as filiais e lê a tabela linha-a-linha para permitir
        # análises de cancelamento por produto e por horário. Primeiro mapeia
        # os cabeçalhos da tabela para descobrir quais colunas têm produto/hora,
        # depois extrai as linhas. O DIAG abaixo mostra a estrutura real.
        try:
            await new_page.evaluate("""
                (function() {
                    var opts = Array.from(document.querySelectorAll('#ContentPlaceHolder1_ddlfilial option'))
                                   .map(function(o){ return o.value; }).filter(function(v){ return v !== ''; });
                    var $sel = $('#ContentPlaceHolder1_ddlfilial');
                    $sel.val(opts); $sel.selectpicker('refresh'); $sel.trigger('change');
                })();
            """)
            await new_page.wait_for_timeout(300)
            await new_page.evaluate(_aplica_periodo_js())
            await new_page.wait_for_timeout(300)
            await _clicar_filtrar()
            await new_page.wait_for_timeout(2000)

            detalhe = await new_page.evaluate("""
                (function() {
                    // Acha a tabela de cancelamentos (a que tem mais linhas com
                    // valores monetários) entre as candidatas.
                    var tabelas = document.querySelectorAll('table');
                    var melhor = null, maxLinhas = 0;
                    for (var t = 0; t < tabelas.length; t++) {
                        var trs = tabelas[t].querySelectorAll('tbody tr');
                        if (trs.length > maxLinhas) { maxLinhas = trs.length; melhor = tabelas[t]; }
                    }
                    if (!melhor) return {headers: [], linhas: [], total_linhas: 0};

                    var headers = [];
                    var ths = melhor.querySelectorAll('thead th, thead td');
                    for (var h = 0; h < ths.length; h++) headers.push((ths[h].textContent||'').trim());

                    // **EXTRAI TODAS AS LINHAS** (não só amostra)
                    var linhas = melhor.querySelectorAll('tbody tr');
                    var todas_linhas = [];
                    for (var i = 0; i < linhas.length; i++) {
                        var tds = linhas[i].querySelectorAll('td');
                        var cols = [];
                        for (var j = 0; j < tds.length; j++) cols.push((tds[j].textContent||'').trim());
                        todas_linhas.push(cols);
                    }
                    
                    return {headers: headers, linhas: todas_linhas, total_linhas: linhas.length};
                })()
            """)
            logger.info(f"Cancelamentos — DETALHE headers: {detalhe.get('headers')}")
            logger.info(f"Cancelamentos — DETALHE total_linhas: {detalhe.get('total_linhas')}")
            logger.info(f"Cancelamentos — DETALHE amostra (5 primeiras linhas): {detalhe.get('amostra')}")
            
            # Debug: mapeia valores com headers para identificar produto
            if detalhe.get('amostra') and detalhe.get('headers'):
                primeira_linha = detalhe['amostra'][0]
                logger.info(f"Cancelamentos — DEBUG PRIMEIRA LINHA MAPEADA:")
                for idx, header in enumerate(detalhe['headers']):
                    valor = primeira_linha[idx] if idx < len(primeira_linha) else "N/A"
                    logger.info(f"  [{idx}] {header}: '{valor}'")
            
            # **SALVA** a tabela detalhada em JSON para uso posterior
            import json
            from pathlib import Path
            detalhe_path = (sessao_dir or DOWNLOAD_DIR) / "cancelamentos_detalhe.json"
            detalhe_path.parent.mkdir(exist_ok=True, parents=True)
            with open(detalhe_path, "w", encoding="utf-8") as f:
                json.dump(detalhe, f, ensure_ascii=False, indent=2)
            logger.info(f"Cancelamentos — DETALHE salvo em: {detalhe_path}")
            
            # Adiciona os detalhes ao resultado para uso posterior
            resultado["_detalhe"] = detalhe
            resultado["_detalhe_path"] = str(detalhe_path)
        except Exception as e:
            logger.warning(f"Cancelamentos — não foi possível ler detalhe por linha: {e}")
            resultado["_detalhe"] = {"headers": [], "amostra": [], "total_linhas": 0}

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
    import hashlib, uuid

    _email = email or PDV_EMAIL
    _senha = senha or PDV_SENHA

    # ─── ISOLAMENTO POR USUÁRIO (CRÍTICO) ──────────────────────────────
    # Cada execução usa um diretório ÚNICO, para que dois usuários rodando
    # o scraper ao mesmo tempo NUNCA compartilhem os arquivos vendas.xlsx /
    # produtos.xlsx / cancelamentos_detalhe.json. Antes, o nome fixo em
    # /tmp/pdvlegal fazia um usuário ler os dados baixados por outro
    # (vazamento de dados entre lojas).
    _hash_email = hashlib.md5((_email or "anon").encode()).hexdigest()[:8]
    _sessao_dir = DOWNLOAD_DIR / f"{_hash_email}_{uuid.uuid4().hex[:8]}"
    _sessao_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(accept_downloads=True)
        page    = await context.new_page()
        try:
            await fazer_login(page, _email, _senha)
            path_vendas   = await exportar_vendas(page, data_ini, data_fim, _sessao_dir)
            path_produtos = await exportar_produtos(page, data_ini, data_fim, _sessao_dir)
            total_cancel  = await exportar_cancelamentos(page, data_ini, data_fim, _sessao_dir)
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
    # Retry com espera para falhas de RECURSO temporário (BlockingIOError /
    # Errno 11 "Resource temporarily unavailable"), que ocorrem quando vários
    # scrapers Playwright disputam memória/processos no mesmo minuto (ex: dia 1
    # do mês, quando o fechamento mensal coincide com outros jobs). Esperar
    # alguns segundos deixa o outro scraper terminar e liberar recursos.
    ultimo_erro = None
    for tentativa in range(3):
        try:
            return asyncio.run(_baixar_async(data_ini, data_fim, email, senha))
        except (BlockingIOError, OSError) as e:
            # Errno 11 = EAGAIN (recurso temporariamente indisponível)
            ultimo_erro = e
            espera = 5 * (tentativa + 1)  # 5s, 10s, 15s
            logger.warning(
                f"[SCRAPER-RETRY] Falha de recurso ({type(e).__name__}: {e}) na tentativa "
                f"{tentativa + 1}/3. Aguardando {espera}s antes de tentar de novo..."
            )
            import time
            time.sleep(espera)
        except Exception as e:
            # Outros erros não são de recurso — não adianta repetir
            raise
    # Esgotou as tentativas
    logger.error(f"[SCRAPER-RETRY] Falhou após 3 tentativas por recurso indisponível: {ultimo_erro}")
    raise ultimo_erro


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
