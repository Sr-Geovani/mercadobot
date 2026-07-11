"""
scheduler.py — Briefing diário multi-usuário
Roda às 7h, busca todos os usuários ativos e envia briefing com suas credenciais.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from functools import partial

import pandas as pd
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton

logger         = logging.getLogger(__name__)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
BRASILIA       = ZoneInfo("America/Sao_Paulo")

# Controle de deduplicação de alertas por dia.
# Estrutura: { "AAAA-MM-DD": { chat_id: set(assinaturas_ja_enviadas) } }
# Evita reenviar o MESMO alerta em cada janela (13h15, 19h15, 20h15, 22h).
# Só dispara se a assinatura do alerta for nova ou tiver mudado (ex: cancelamento
# que cresceu de valor). Reseta a cada novo dia automaticamente.
_alertas_enviados_dia = {}


def _ja_alertou_hoje(chat_id: int, assinatura: str) -> bool:
    """Retorna True se essa assinatura de alerta já foi enviada hoje para o chat."""
    hoje_str = datetime.now(BRASILIA).strftime("%Y-%m-%d")
    # Limpa dias antigos (mantém só hoje)
    for dia in list(_alertas_enviados_dia.keys()):
        if dia != hoje_str:
            del _alertas_enviados_dia[dia]
    do_dia = _alertas_enviados_dia.setdefault(hoje_str, {})
    enviadas = do_dia.setdefault(chat_id, set())
    if assinatura in enviadas:
        return True
    enviadas.add(assinatura)
    return False
HORARIO_HORA   = int(os.environ.get("BRIEFING_HORA",   "7"))
HORARIO_MINUTO = int(os.environ.get("BRIEFING_MINUTO", "0"))


def b(t): return f"<b>{t}</b>"
def i(t): return f"<i>{t}</i>"


async def reconciliar_assinaturas():
    """
    Rede de segurança: corrige usuários travados por falha no webhook do Asaas.
    Roda diariamente. Para cada usuário bloqueado ou com assinatura_fim vencida,
    consulta o Asaas diretamente (fonte da verdade) e reativa se houver pagamento confirmado.
    """
    from database import get_pool, atualizar_usuario
    from pagamento import verificar_pagamento_confirmado
    from bot import kb_menu

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM usuarios WHERE status IN ('bloqueado', 'ativo', 'trial') AND asaas_id IS NOT NULL"
        )

    agora = datetime.now(BRASILIA)
    corrigidos = 0
    bot = Bot(token=TELEGRAM_TOKEN)

    for usuario in rows:
        usuario   = dict(usuario)
        chat_id   = usuario["chat_id"]
        asaas_id  = usuario.get("asaas_id")
        status    = usuario["status"]

        # Verifica se assinatura_fim já está vencida (ou usuário já bloqueado)
        precisa_checar = (status == "bloqueado")
        # Verifica status ativo, trial, ou cancelado_mas_ativo
        if status in ("ativo", "trial", "cancelado_mas_ativo") and usuario.get("assinatura_fim"):
            try:
                fim = datetime.fromisoformat(usuario["assinatura_fim"])
                if agora > fim:
                    precisa_checar = True
            except Exception:
                pass

        if not precisa_checar:
            continue

        try:
            tem_pagamento = await verificar_pagamento_confirmado(asaas_id)
            if tem_pagamento:
                novo_fim = (agora + timedelta(days=31)).isoformat()
                await atualizar_usuario(
                    chat_id,
                    status="ativo",
                    assinatura_fim=novo_fim,
                )
                corrigidos += 1
                logger.warning(
                    f"Reconciliação: usuário {chat_id} estava '{status}' mas tem pagamento "
                    f"confirmado no Asaas — reativado até {novo_fim}."
                )
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "✅ <b>Seu acesso foi restaurado!</b>\n\n"
                            "Identificamos seu pagamento confirmado e reativamos sua conta. "
                            "Pedimos desculpas por qualquer inconveniente."
                        ),
                        parse_mode="HTML",
                        reply_markup=kb_menu()
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Reconciliação: erro ao verificar {chat_id}: {e}")

    logger.info(f"Reconciliação de assinaturas concluída — {corrigidos} usuário(s) corrigido(s).")


async def enviar_onboarding_progressivo():
    """
    Envia dicas de onboarding distribuídas ao longo dos 7 dias de trial.
    Cada dia recebe uma mensagem educativa sobre uma capacidade do bot.
    
    Dia 1: já enviado (boas-vindas)
    Dia 2: Relatórios automáticos
    Dia 3: Ferramentas de IA (parte 1)
    Dia 4: Ferramentas de IA (parte 2)
    Dia 5: Alertas automáticos
    Dia 6: Investigação de queda
    Dia 7: Recursos avançados (SEM aviso de conversão — evita cancelamento)
    """
    from database import listar_usuarios_em_trial
    usuarios = await listar_usuarios_em_trial()
    if not usuarios:
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    agora = datetime.now(BRASILIA)
    
    logger.info(f"Onboarding progressivo — {len(usuarios)} usuário(s) em trial")

    for usuario in usuarios:
        chat_id = usuario["chat_id"]
        trial_fim = usuario.get("trial_fim")
        
        if not trial_fim:
            continue
        
        trial_fim_dt = datetime.fromisoformat(trial_fim)
        dias_restantes = (trial_fim_dt - agora).days
        dia_do_trial = 7 - dias_restantes
        
        # Não envia se não é dia inteiro ou já passou
        if dia_do_trial < 1 or dia_do_trial > 7:
            continue
        
        try:
            if dia_do_trial == 2:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"💡 <b>Dica do dia 2</b>\n\n"
                        f"<b>Relatórios automáticos</b>\n\n"
                        f"Você recebe todos os dias às 7h:\n"
                        f"  • Faturamento do dia anterior\n"
                        f"  • Top 5 produtos\n"
                        f"  • Cancelamentos\n"
                        f"  • Score de saúde\n\n"
                        f"Sem spam — é só uma mensagem. Automático."
                    ),
                    parse_mode="HTML"
                )
            
            elif dia_do_trial == 3:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"💡 <b>Dica do dia 3</b>\n\n"
                        f"<b>Ferramentas de IA — Parte 1</b>\n\n"
                        f"Você pode perguntar qualquer coisa:\n"
                        f"  💬 <i>\"Quanto vendi de Coca?\"</i>\n"
                        f"  💬 <i>\"Qual foi minha última venda?\"</i>\n"
                        f"  💬 <i>\"Quais são meus top 10 produtos?\"</i>\n\n"
                        f"Tenta aí no menu → Pergunte à IA"
                    ),
                    parse_mode="HTML"
                )
            
            elif dia_do_trial == 4:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"💡 <b>Dica do dia 4</b>\n\n"
                        f"<b>Ferramentas de IA — Parte 2</b>\n\n"
                        f"Pergunte também:\n"
                        f"  🔍 <i>\"O que eu deveria vender?\"</i> (descobrir novos produtos)\n"
                        f"  🔍 <i>\"Qual meu padrão de vendas?\"</i> (detectar padrões)\n"
                        f"  🔍 <i>\"Como me comparo?\"</i> (benchmark com outras lojas)\n\n"
                        f"Menu → Pergunte à IA"
                    ),
                    parse_mode="HTML"
                )
            
            elif dia_do_trial == 5:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"💡 <b>Dica do dia 5</b>\n\n"
                        f"<b>Alertas automáticos</b>\n\n"
                        f"Você recebe avisos em:\n"
                        f"  🔔 13h — parcial do dia\n"
                        f"  🔔 19h–20h — aviso de pico (qui–dom)\n"
                        f"  🔔 22h — alerta noturno\n\n"
                        f"Sem spam — só quando algo está errado."
                    ),
                    parse_mode="HTML"
                )
            
            elif dia_do_trial == 6:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"💡 <b>Dica do dia 6</b>\n\n"
                        f"<b>Investigação de queda</b>\n\n"
                        f"Se suas vendas caem, o bot avisa automaticamente.\n\n"
                        f"Ou você pergunta: <i>\"Por que caiu?\"</i>\n\n"
                        f"A IA então analisa:\n"
                        f"  • Qual filial foi mais impactada\n"
                        f"  • Qual horário ficou sem vendas\n"
                        f"  • Qual produto top faltou\n\n"
                        f"Tudo em uma resposta."
                    ),
                    parse_mode="HTML"
                )
            
            elif dia_do_trial == 7:
                from bot import kb_menu
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"⭐ <b>Dica do dia 7</b>\n\n"
                        f"<b>Recursos avançados ativados</b>\n\n"
                        f"Parabéns! Você conhece tudo que o MercadoBot oferece:\n\n"
                        f"✅ 7 ferramentas de IA\n"
                        f"✅ Relatórios automáticos\n"
                        f"✅ Alertas inteligentes\n"
                        f"✅ Investigação de quedas\n"
                        f"✅ Benchmark de mercado\n"
                        f"✅ Descoberta de mix\n"
                        f"✅ Análise de padrões\n\n"
                        f"Pronto para começar de verdade? Use o menu abaixo. 👇"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb_menu()
                )
            
            logger.info(f"Onboarding dia {dia_do_trial} enviado para {chat_id}")

        except Exception as e:
            logger.error(f"Erro ao enviar onboarding dia {dia_do_trial} para {chat_id}: {e}")


async def enviar_fechamento_mes():
    """
    Dispara no dia 01 do mês às 7h.
    Busca dados do mês ANTERIOR completo (01 a 28/29/30/31) e envia fechamento com:
    - Sumário executivo (faturamento por filial)
    - Evolução semana-a-semana
    - Top 10 produtos (agregado)
    - Cancelamentos
    - Categorias
    - Mix de pagamentos
    - Score de saúde
    - Recomendações + overdelivery
    + 4 gráficos (evolução, top 10, categorias, filiais)
    """
    from database import listar_usuarios_com_acesso
    usuarios = await listar_usuarios_com_acesso()
    if not usuarios:
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    agora = datetime.now(BRASILIA)
    
    # Mês anterior (se hoje for 01/07, busca 01/06 a 30/06)
    primeiro_dia_mes = agora.replace(day=1)
    ultimo_dia_mes_anterior = primeiro_dia_mes - timedelta(days=1)
    primeiro_dia_mes_anterior = ultimo_dia_mes_anterior.replace(day=1)
    
    data_ini = primeiro_dia_mes_anterior.strftime("%d/%m/%Y")
    data_fim = ultimo_dia_mes_anterior.strftime("%d/%m/%Y")
    mes_nome = ultimo_dia_mes_anterior.strftime("%B/%Y").replace("January", "Janeiro").replace("February", "Fevereiro").replace("March", "Março").replace("April", "Abril").replace("May", "Maio").replace("June", "Junho").replace("July", "Julho").replace("August", "Agosto").replace("September", "Setembro").replace("October", "Outubro").replace("November", "Novembro").replace("December", "Dezembro")
    
    logger.info(f"Fechamento de mês — {mes_nome} — {len(usuarios)} usuário(s)")

    for usuario in usuarios:
        chat_id = usuario["chat_id"]
        pdv_email = usuario.get("pdv_email")
        pdv_senha = usuario.get("pdv_senha")
        if not pdv_email or not pdv_senha:
            continue

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"📊 Gerando fechamento de {mes_nome}...",
                parse_mode="HTML"
            )

            from scraper import baixar_relatorios_periodo
            from bot import normalizar_vendas, normalizar_produtos, bloco_fechamento_mes, g_semanal, g_top_produtos, g_categorias, g_filiais, kb_menu

            loop = asyncio.get_event_loop()
            path_vendas, path_produtos, total_cancel = await loop.run_in_executor(
                None, baixar_relatorios_periodo, data_ini, data_fim, pdv_email, pdv_senha
            )

            vendas = normalizar_vendas(pd.read_excel(path_vendas))
            produtos = normalizar_produtos(pd.read_excel(path_produtos))

            if len(vendas) == 0:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"📊 Fechamento de {mes_nome}\n\nSem dados disponíveis para este período.",
                    parse_mode="HTML"
                )
                continue

            # Bloco principal
            texto_fechamento = bloco_fechamento_mes(vendas, produtos, total_cancel)
            await bot.send_message(
                chat_id=chat_id,
                text=texto_fechamento,
                parse_mode="HTML",
                reply_markup=kb_menu(f"Mês anterior")
            )
            await asyncio.sleep(2)

            # Gráficos (4 deles)
            g1 = g_semanal(vendas)
            if g1:
                await bot.send_photo(chat_id=chat_id, photo=g1)
                await asyncio.sleep(1)

            g2 = g_top_produtos(produtos)
            if g2:
                await bot.send_photo(chat_id=chat_id, photo=g2)
                await asyncio.sleep(1)

            g3 = g_categorias(produtos)
            if g3:
                await bot.send_photo(chat_id=chat_id, photo=g3)
                await asyncio.sleep(1)

            g4 = g_filiais(vendas)
            if g4:
                await bot.send_photo(chat_id=chat_id, photo=g4)
                await asyncio.sleep(1)

            logger.info(f"Fechamento de mês enviado para {chat_id}")

        except Exception as e:
            logger.error(f"Erro ao enviar fechamento de mês para {chat_id}: {e}")


async def briefing_usuario(bot: Bot, usuario: dict):
    """Executa o briefing completo para um usuário específico."""
    chat_id   = usuario["chat_id"]
    pdv_email = usuario.get("pdv_email")
    pdv_senha = usuario.get("pdv_senha")
    nome      = usuario.get("nome", "Operador")

    if not pdv_email or not pdv_senha:
        logger.warning(f"Usuário {chat_id} sem credenciais PDV — pulando.")
        return

    logger.info(f"Iniciando briefing para {chat_id} ({pdv_email})")

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"☀️ {b('Bom dia, ' + nome + '!')} Gerando seu briefing de ontem...",
            parse_mode="HTML"
        )

        from scraper import baixar_relatorios, baixar_relatorios_periodo
        from bot import normalizar_vendas, normalizar_produtos, bloco_faturamento, \
            bloco_categorias, bloco_pagamentos, bloco_semanal, \
            g_faturamento, g_categorias, g_pagamentos, g_semanal, \
            insight_ia, kb_menu, dados_usuario

        loop = asyncio.get_event_loop()

        # Dados de ontem — base do briefing
        agora  = datetime.now(BRASILIA)
        ontem  = (agora - timedelta(days=1)).strftime("%d/%m/%Y")
        path_vendas, path_produtos, total_cancel = await loop.run_in_executor(
            None, baixar_relatorios, pdv_email, pdv_senha
        )

        vendas   = pd.read_excel(path_vendas)
        produtos = pd.read_excel(path_produtos)
        vendas   = normalizar_vendas(vendas)
        produtos = normalizar_produtos(produtos)

        if len(vendas) == 0:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ Não encontrei vendas para ontem.\n\n"
                    f"Se houve vendas, use {b('🔄 Atualizar dados agora')} para buscar manualmente."
                ),
                parse_mode="HTML"
            )
            return

        # Busca 30 dias para evolução semanal — em arquivo separado
        d30 = (agora - timedelta(days=30)).strftime("%d/%m/%Y")
        try:
            path_v30, _, _ = await loop.run_in_executor(
                None, baixar_relatorios_periodo, d30, ontem, pdv_email, pdv_senha
            )
            # Lê imediatamente antes de qualquer sobrescrita
            vendas_30 = normalizar_vendas(pd.read_excel(path_v30))
        except Exception as e:
            logger.warning(f"Briefing: erro ao buscar 30 dias para semanal: {e}")
            vendas_30 = vendas  # fallback

        # ── Salva dados de ontem em dados_usuario para o menu funcionar ──
        dados_usuario[chat_id] = {
            "vendas":       vendas,
            "produtos":     produtos,
            "total_cancel": total_cancel,
            "periodo_label": f"Ontem ({ontem})",
            "data_ini":     ontem,
            "data_fim":     ontem,
        }
        logger.info(f"Briefing: dados_usuario[{chat_id}] atualizado com dados de ontem")

        # Envia blocos
        await bot.send_message(chat_id=chat_id, text=bloco_faturamento(vendas, produtos, total_cancel), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_faturamento(vendas))

        await bot.send_message(chat_id=chat_id, text=bloco_categorias(produtos), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_categorias(produtos))

        await bot.send_message(chat_id=chat_id, text=bloco_pagamentos(vendas), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_pagamentos(vendas))

        # Evolução semanal usa os 30 dias
        await bot.send_message(chat_id=chat_id, text=bloco_semanal(vendas_30), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_semanal(vendas_30))

        # Insight IA
        ctx = (
            f"VENDAS: {len(vendas)} transações, R$ {vendas['valor'].sum():.2f} total\n"
            f"TICKET MÉDIO: R$ {vendas['valor'].mean():.2f}\n"
            f"CANCELAMENTOS: R$ {total_cancel.get('_total', 0) if isinstance(total_cancel, dict) else total_cancel:.2f}"
        )
        insight = await insight_ia(ctx)
        await bot.send_message(
            chat_id=chat_id,
            text=f"💡 {b('INSIGHT DO DIA')}\n\n{insight}",
            parse_mode="HTML"
        )

        await bot.send_message(
            chat_id=chat_id,
            text=f"📅 Período carregado: {b('Ontem (' + ontem + ')')}\nUse o menu para análises adicionais.",
            reply_markup=kb_menu(f"Ontem ({ontem})")
        )

        logger.info(f"Briefing enviado com sucesso para {chat_id}")

    except Exception as e:
        logger.error(f"Erro no briefing do usuário {chat_id}: {e}")
        from bot import kb_menu
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ Não consegui gerar o briefing automático hoje.\n\n"
                f"Possíveis causas:\n"
                f"• PDV Legal fora do ar ou lento\n"
                f"• Instabilidade na conexão\n\n"
                f"Use o menu abaixo para tentar manualmente quando quiser."
            ),
            parse_mode="HTML",
            reply_markup=kb_menu()
        )


async def enviar_briefing_automatico():
    """Busca todos os usuários com acesso ativo e envia o briefing para cada um."""
    from database import listar_usuarios_com_acesso

    agora    = datetime.now(BRASILIA)
    usuarios = await listar_usuarios_com_acesso()

    if not usuarios:
        logger.info("Nenhum usuário com acesso ativo para o briefing.")
        return

    logger.info(f"Briefing automático — {agora:%d/%m/%Y %H:%M} — {len(usuarios)} usuário(s) com acesso")
    bot = Bot(token=TELEGRAM_TOKEN)

    for usuario in usuarios:
        try:
            await briefing_usuario(bot, usuario)
            # Pequena pausa entre usuários para não sobrecarregar o PDV Legal
            await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"Erro no briefing do usuário {usuario['chat_id']}: {e}")


async def briefing_condicional():
    """Roda briefing todos os dias EXCETO dia 01 (quando roda fechamento de mês)."""
    agora = datetime.now(BRASILIA)
    if agora.day != 1:
        await enviar_briefing_automatico()


def iniciar_scheduler():
    """Inicia o agendador diário."""
    scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")

    # Reconciliação de assinaturas às 6h — rede de segurança contra falha de webhook
    scheduler.add_job(
        reconciliar_assinaturas,
        trigger="cron",
        hour=6,
        minute=0,
        id="reconciliar_assinaturas",
        replace_existing=True,
    )

    # Fechamento de mês — dia 01 às 7h (substitui o briefing diário neste dia)
    scheduler.add_job(
        enviar_fechamento_mes,
        trigger="cron",
        day=1,
        hour=HORARIO_HORA,
        minute=HORARIO_MINUTO,
        id="fechamento_mes",
        replace_existing=True,
    )

    # Briefing diário às 7h (não roda no dia 01, pois fechamento já rodou)
    scheduler.add_job(
        briefing_condicional,
        trigger="cron",
        hour=HORARIO_HORA,
        minute=HORARIO_MINUTO,
        id="briefing_diario",
        replace_existing=True,
    )

    # Parcial do dia às 13h — sempre envia resumo + atualiza dados_usuario
    scheduler.add_job(
        enviar_parcial_dia,
        trigger="cron",
        hour=13,
        minute=0,
        id="parcial_dia",
        replace_existing=True,
    )

    # Alertas às 13h — zero vendas + cancelamentos suspeitos
    scheduler.add_job(
        partial(enviar_alertas_proativos, modo="completo"),
        trigger="cron",
        hour=13,
        minute=15,
        id="alertas_13h",
        replace_existing=True,
    )

    # Atualiza dados às 19h (sem alerta) — quinta a domingo
    scheduler.add_job(
        enviar_alerta_pico,
        trigger="cron",
        day_of_week="thu,fri,sat,sun",
        hour=19,
        minute=0,
        id="atualiza_pico_19h",
        replace_existing=True,
    )

    # Alerta de tarde — 17h15 TODOS os dias: sem vendas entre 13h e 17h
    scheduler.add_job(
        enviar_alerta_tarde,
        trigger="cron",
        hour=17,
        minute=15,
        id="alerta_tarde",
        replace_existing=True,
    )

    # Alertas às 19h — zero vendas + cancelamentos suspeitos
    scheduler.add_job(
        partial(enviar_alertas_proativos, modo="completo"),
        trigger="cron",
        hour=19,
        minute=15,
        id="alertas_19h",
        replace_existing=True,
    )

    # Verifica pico às 20h (alerta se sem vendas 19h-20h) — quinta a domingo
    scheduler.add_job(
        enviar_alerta_pico,
        trigger="cron",
        day_of_week="thu,fri,sat,sun",
        hour=20,
        minute=0,
        id="alerta_pico_20h",
        replace_existing=True,
    )

    # Alertas às 20h — zero vendas + cancelamentos suspeitos (qui-dom)
    scheduler.add_job(
        partial(enviar_alertas_proativos, modo="completo"),
        trigger="cron",
        day_of_week="thu,fri,sat,sun",
        hour=20,
        minute=15,
        id="alertas_20h",
        replace_existing=True,
    )

    # Alertas às 22h — zero vendas + cancelamentos suspeitos
    scheduler.add_job(
        partial(enviar_alertas_proativos, modo="completo"),
        trigger="cron",
        hour=22,
        minute=0,
        id="alertas_22h",
        replace_existing=True,
    )

    # Onboarding progressivo — 12h todo dia (dias 2-7 de trial, dicas personalizadas)
    scheduler.add_job(
        enviar_onboarding_progressivo,
        trigger="cron",
        hour=12,
        minute=0,
        id="onboarding_progressivo",
        replace_existing=True,
    )

    # Onboarding guiado — dia 2 do trial às 9h
    scheduler.add_job(
        enviar_onboarding_guiado,
        trigger="cron",
        hour=9,
        minute=0,
        id="onboarding_guiado",
        replace_existing=True,
    )

    # Fechamento semanal domingo 21h30
    scheduler.add_job(
        enviar_fechamento_semanal,
        trigger="cron",
        day_of_week="sun",
        hour=21,
        minute=30,
        id="fechamento_semanal",
        replace_existing=True,
    )

    # Detecção de padrões por IA — semanal (segunda 16h). Horário escolhido
    # de propósito longe do briefing das 7h e da parcial das 13h: evita
    # sessões de scraping concorrentes e mensagens próximas demais umas das
    # outras. Chega como um "segundo olhar" no meio da tarde, antes do
    # movimento de pico começar.
    scheduler.add_job(
        enviar_padroes_detectados_automatico,
        trigger="cron",
        day_of_week="mon",
        hour=16,
        minute=0,
        id="padroes_semanais",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(f"Briefing agendado para {HORARIO_HORA:02d}:{HORARIO_MINUTO:02d} (Brasília)")
    logger.info("Alertas (zero vendas + cancelamentos suspeitos) agendados para 13h15, 19h15, 20h15 (qui-dom) e 22h00")
    logger.info("Alerta de tarde (sem vendas 13h-17h) agendado para 17h15 todos os dias")
    return scheduler


async def enviar_padroes_detectados_automatico():
    """
    Roda semanalmente (segunda 8h). Busca os últimos 30 dias de cada usuário
    ativo, detecta padrões via IA, e notifica só os que ainda não foram
    avisados nos últimos 7 dias (anti-spam). Também registra os produtos
    campeões no benchmark agregado entre clientes nesse mesmo passo —
    aproveitando o mesmo download de dados.
    """
    from database import listar_usuarios_com_acesso
    from padroes import detectar_padroes_vendas, notificar_padroes_novos, registrar_produto_campeao_benchmark, consolidar_fatos_cliente
    from bot import normalizar_vendas, normalizar_produtos, kb_menu, b

    usuarios = await listar_usuarios_com_acesso()
    if not usuarios:
        return

    bot   = Bot(token=TELEGRAM_TOKEN)
    agora = datetime.now(BRASILIA)
    d30   = (agora - timedelta(days=30)).strftime("%d/%m/%Y")
    hoje  = agora.strftime("%d/%m/%Y")

    for usuario in usuarios:
        chat_id   = usuario["chat_id"]
        pdv_email = usuario.get("pdv_email")
        pdv_senha = usuario.get("pdv_senha")
        if not pdv_email or not pdv_senha:
            continue
        try:
            from scraper import baixar_relatorios_periodo
            path_vendas, path_produtos, _ = await asyncio.get_event_loop().run_in_executor(
                None, baixar_relatorios_periodo, d30, hoje, pdv_email, pdv_senha
            )
            vendas   = normalizar_vendas(pd.read_excel(path_vendas))
            produtos = normalizar_produtos(pd.read_excel(path_produtos))

            if len(vendas) == 0:
                continue

            # Registra benchmark (produtos campeões) — independente de haver
            # padrão notificável ou não, alimenta a base de comparação.
            try:
                await registrar_produto_campeao_benchmark(chat_id, produtos, d30, hoje, top_n=20)
            except Exception as e:
                logger.warning(f"Erro ao registrar benchmark para {chat_id}: {e}")

            # Consolida fatos persistentes do cliente (dia da semana forte/fraco,
            # ticket médio histórico, produto campeão) — memória parcial que
            # alimenta cruzamentos inteligentes do agente.
            try:
                await consolidar_fatos_cliente(chat_id, vendas, produtos)
            except Exception as e:
                logger.warning(f"Erro ao consolidar fatos para {chat_id}: {e}")

            # Detecta padrões e filtra pelos ainda não notificados
            padroes = await detectar_padroes_vendas(chat_id, vendas, produtos)
            if not padroes:
                continue

            padroes_novos = await notificar_padroes_novos(chat_id, padroes, janela_dias=7)
            if not padroes_novos:
                logger.info(f"Padrões: {chat_id} tem {len(padroes)} padrão(ões), mas já notificados recentemente")
                continue

            texto = f"🔍 {b('Padrões identificados na sua operação')}\n\n"
            texto += "\n".join(p.get("descricao", "") for p in padroes_novos)
            texto += "\n\n<i>Análise baseada nos últimos 30 dias de vendas.</i>"

            await bot.send_message(chat_id=chat_id, text=texto, parse_mode="HTML", reply_markup=kb_menu())
            logger.info(f"Padrões: {len(padroes_novos)} padrão(ões) novo(s) notificado(s) para {chat_id}")
            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"Erro na detecção de padrões para {chat_id}: {e}")


async def enviar_fechamento_semanal():
    """Envia resumo da semana todo domingo às 23h59."""
    from database import listar_usuarios_com_acesso
    usuarios = await listar_usuarios_com_acesso()
    if not usuarios:
        return

    bot   = Bot(token=TELEGRAM_TOKEN)
    agora = datetime.now(BRASILIA)

    # Período: segunda a domingo desta semana
    inicio_semana = (agora - pd.Timedelta(days=agora.weekday())).strftime("%d/%m/%Y")
    fim_semana    = agora.strftime("%d/%m/%Y")

    logger.info(f"Fechamento semanal — {inicio_semana} a {fim_semana}")

    for usuario in usuarios:
        chat_id   = usuario["chat_id"]
        pdv_email = usuario.get("pdv_email")
        pdv_senha = usuario.get("pdv_senha")
        nome      = usuario.get("nome_mercadinho") or usuario.get("nome", "Operador")
        if not pdv_email or not pdv_senha:
            continue
        try:
            from scraper import baixar_relatorios_periodo
            path_vendas, path_produtos, _ = await asyncio.get_event_loop().run_in_executor(
                None, baixar_relatorios_periodo, inicio_semana, fim_semana, pdv_email, pdv_senha
            )
            vendas   = pd.read_excel(path_vendas)
            produtos = pd.read_excel(path_produtos)

            from bot import (normalizar_vendas, normalizar_produtos,
                             bloco_faturamento, bloco_comparativo,
                             bloco_produto_mes, bloco_score,
                             bloco_projecao_mes, kb_menu, b, i)

            vendas   = normalizar_vendas(vendas)
            produtos = normalizar_produtos(produtos)

            if len(vendas) == 0:
                continue

            # Cabeçalho do fechamento
            total = vendas["valor"].sum()
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"📋 {b(f'Fechamento da Semana — {nome}')}\n\n"
                    f"Semana de {inicio_semana} a {fim_semana}\n"
                    f"💰 Total da semana: {b(f'R$ {total:,.2f}')}"
                ),
                parse_mode="HTML"
            )

            # Blocos analíticos
            await bot.send_message(chat_id=chat_id, text=bloco_comparativo(vendas), parse_mode="HTML")
            await bot.send_message(chat_id=chat_id, text=bloco_produto_mes(produtos), parse_mode="HTML")
            await bot.send_message(chat_id=chat_id, text=bloco_score(vendas), parse_mode="HTML")

            projecao = bloco_projecao_mes(vendas)
            if projecao:
                await bot.send_message(chat_id=chat_id, text=projecao, parse_mode="HTML")

            # Sugestão de aumento de preço por alto giro (oportunidade de margem)
            try:
                from padroes import sugerir_aumento_preco_alto_giro
                sugestoes_preco = sugerir_aumento_preco_alto_giro(produtos, top_n=5, giro_minimo=30)
                if sugestoes_preco:
                    linhas = [f"💰 {b('Oportunidade de margem — produtos de alto giro')}\n"]
                    linhas.append(
                        "Estes são seus produtos que mais saem. Por venderem muito, "
                        "costumam aguentar um pequeno reajuste sem perder cliente — "
                        "vale avaliar:\n"
                    )
                    for s in sugestoes_preco:
                        linhas.append(
                            f"• {b(s['produto'])}\n"
                            f"   {s['quantidade_vendida']} un. na semana · "
                            f"hoje R$ {s['preco_medio_atual']:.2f}\n"
                            f"   Testar R$ {s['novo_preco_sugerido_5pct']:.2f} (+5%) "
                            f"a R$ {s['novo_preco_sugerido_8pct']:.2f} (+8%)"
                        )
                    linhas.append(
                        f"\n{i('Sugestão para avaliação — considere seu custo e a concorrência local antes de reajustar.')}"
                    )
                    await bot.send_message(
                        chat_id=chat_id, text="\n".join(linhas), parse_mode="HTML"
                    )
            except Exception as e:
                logger.warning(f"Erro ao gerar sugestão de preço para {chat_id}: {e}")

            await bot.send_message(
                chat_id=chat_id,
                text="Boa semana! 🚀",
                reply_markup=kb_menu()
            )

            await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"Erro no fechamento semanal para {chat_id}: {e}")


async def verificar_alertas_proativos(bot, chat_id, pdv_email, pdv_senha, vendas_hoje, total_cancel, hora_atual=None):
    """
    Verificação PROATIVA reutilizável nas janelas (13h/19h/20h/22h):
    1. Ritmo de faturamento (acumulado até a hora vs mesmo dia da semana) + projeção
    2. Cancelamentos suspeitos (valor alto, concentração, etc)

    Baixa 6 semanas de histórico (para o Z-score de ritmo) e envia alertas
    somente quando há sinal relevante. Não envia nada se está tudo normal.

    Retorna lista de mensagens enviadas (para log/controle).
    """
    from datetime import datetime, timedelta
    from bot import (detectar_queda_ritmo, detectar_cancelamentos_suspeitos,
                     normalizar_vendas, kb_menu)
    import json
    from pathlib import Path

    mensagens_enviadas = []
    agora = datetime.now(BRASILIA)
    if hora_atual is None:
        hora_atual = agora.hour

    # ─── 1. RITMO DE FATURAMENTO ───
    try:
        # Baixa histórico de 6 semanas (só uma vez por execução)
        d_ini = (agora - timedelta(days=42)).strftime("%d/%m/%Y")
        d_fim = (agora - timedelta(days=1)).strftime("%d/%m/%Y")

        from scraper import baixar_relatorios_periodo
        path_v_hist, _, _ = await asyncio.get_event_loop().run_in_executor(
            None, baixar_relatorios_periodo, d_ini, d_fim, pdv_email, pdv_senha
        )
        vendas_hist = normalizar_vendas(pd.read_excel(path_v_hist))

        resultado_ritmo = detectar_queda_ritmo(vendas_hoje, vendas_hist, hora_atual=hora_atual)

        if resultado_ritmo.get("tem_queda"):
            msg = _formatar_ritmo_queda(resultado_ritmo)
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML",
                                    reply_markup=kb_menu())
            mensagens_enviadas.append("ritmo")
            await asyncio.sleep(2)
    except Exception as e:
        logger.warning(f"Alerta ritmo falhou para {chat_id}: {e}")

    # ─── 2. CANCELAMENTOS SUSPEITOS (6 camadas) ───
    # Fonte primária: o detalhe que o scraper já retorna dentro de total_cancel
    # ("_detalhe"), que está em memória e não depende de arquivo temporário.
    # Fallback: o arquivo /tmp/pdvlegal/cancelamentos_detalhe.json (pode não
    # existir após reinício do Railway ou se a janela não baixou cancelamentos).
    try:
        todas_linhas = []
        headers = []

        # 1) Tenta pegar do total_cancel (em memória, confiável)
        if isinstance(total_cancel, dict) and isinstance(total_cancel.get("_detalhe"), dict):
            det = total_cancel["_detalhe"]
            todas_linhas = det.get("linhas", det.get("amostra", [])) or []
            headers = det.get("headers", []) or []
            if todas_linhas and headers:
                logger.info(f"[ALERTA-CANCEL] Usando detalhe em memória para {chat_id} ({len(todas_linhas)} linhas)")

        # 2) Fallback: arquivo /tmp
        if not (todas_linhas and headers):
            detalhe_path = Path("/tmp/pdvlegal/cancelamentos_detalhe.json")
            if detalhe_path.exists():
                with open(detalhe_path, "r", encoding="utf-8") as f:
                    detalhe = json.load(f)
                todas_linhas = detalhe.get("linhas", detalhe.get("amostra", [])) or []
                headers = detalhe.get("headers", []) or []
                if todas_linhas and headers:
                    logger.info(f"[ALERTA-CANCEL] Usando detalhe do arquivo /tmp para {chat_id}")
            else:
                logger.warning(
                    f"[ALERTA-CANCEL] Sem detalhe em memória E sem arquivo /tmp para {chat_id} "
                    f"na janela {hora_atual}h — detecção das 6 camadas pulada. "
                    f"O alerta de % ainda roda pelo total_cancel."
                )

        if todas_linhas and headers:
            # Só considera cancelamentos de HOJE (Brasília) e usa piso R$ 30.
            hoje_br = datetime.now(BRASILIA).date()
            deteccao = detectar_cancelamentos_suspeitos(
                todas_linhas, headers,
                piso_valor_alto=30.0,
                apenas_data=hoje_br,
            )
            if deteccao.get("tem_alerta"):
                # Dedup: assinatura pela quantidade de cancelamentos suspeitos.
                # Se surgir um novo cancelamento (quantidade muda), reenvia;
                # se for a mesma quantidade da janela anterior, não repete.
                qtd_suspeitos = len(deteccao.get("alertas", []))
                n_linhas_cancel = len([l for l in todas_linhas if l])  # total detectado
                assinatura_6c = f"cancel6c:{qtd_suspeitos}:{n_linhas_cancel}"
                if not _ja_alertou_hoje(chat_id, assinatura_6c):
                    from agente import _formatar_alertas_cancelamento
                    bloco = _formatar_alertas_cancelamento(deteccao["alertas"])
                    if bloco:
                        await bot.send_message(chat_id=chat_id, text=bloco.strip(),
                                                parse_mode="HTML", reply_markup=kb_menu())
                        mensagens_enviadas.append("cancelamento")
                        await asyncio.sleep(2)
    except Exception as e:
        logger.warning(f"Alerta cancelamento falhou para {chat_id}: {e}")

    return mensagens_enviadas


def _formatar_ritmo_queda(r: dict) -> str:
    """Formata o alerta proativo de ritmo de faturamento."""
    dia = r.get("dia_semana", "hoje")
    hora = r.get("hora_atual", "")
    acum = r.get("acumulado_atual", 0)
    esperado = r.get("esperado_ate_agora_media", 0)
    projecao = r.get("projecao_fechamento")
    media_dia = r.get("media_fechamento_dia", 0)

    linhas = [f"⚠️ {b(f'Ritmo abaixo do normal — {dia}')}\n"]
    linhas.append(f"Até as {hora}h você fez: {b(f'R$ {acum:,.2f}')}")
    linhas.append(f"Um {dia} normal já teria: R$ {esperado:,.2f} neste horário")

    if projecao and media_dia:
        linhas.append(
            f"\n📉 No ritmo atual, o dia fecha em ~{b(f'R$ {projecao:,.2f}')} "
            f"(um {dia} costuma fechar R$ {media_dia:,.2f})"
        )

    causas = r.get("causas", [])
    if causas:
        linhas.append(f"\n🔍 {b('O que pode explicar:')}")
        for c in causas:
            linhas.append(f"  • {c}")

    linhas.append(f"\n{i('Ainda dá tempo de reagir — verifique totens, reposição e acesso.')}")
    return "\n".join(linhas)


async def enviar_parcial_dia():
    """
    Dispara às 13h todos os dias.
    Busca dados frescos do dia, atualiza dados_usuario,
    e envia SEMPRE um resumo parcial do dia — independente de alertas.
    """
    from database import listar_usuarios_com_acesso
    usuarios = await listar_usuarios_com_acesso()
    if not usuarios:
        return

    bot   = Bot(token=TELEGRAM_TOKEN)
    agora = datetime.now(BRASILIA)
    hoje  = agora.strftime("%d/%m/%Y")

    for usuario in usuarios:
        chat_id   = usuario["chat_id"]
        pdv_email = usuario.get("pdv_email")
        pdv_senha = usuario.get("pdv_senha")
        nome      = usuario.get("nome", "Operador")
        if not pdv_email or not pdv_senha:
            continue
        try:
            from scraper import baixar_relatorios_periodo
            from bot import normalizar_vendas, normalizar_produtos, \
                bloco_faturamento, dados_usuario, kb_menu, b

            path_vendas, path_produtos, total_cancel = await asyncio.get_event_loop().run_in_executor(
                None, baixar_relatorios_periodo, hoje, hoje, pdv_email, pdv_senha
            )
            vendas   = normalizar_vendas(pd.read_excel(path_vendas))
            produtos = normalizar_produtos(pd.read_excel(path_produtos))

            # Atualiza dados_usuario — garante que menu funciona após restart
            dados_usuario[chat_id] = {
                "vendas":        vendas,
                "produtos":      produtos,
                "total_cancel":  total_cancel,
                "periodo_label": f"Hoje ({hoje})",
                "data_ini":      hoje,
                "data_fim":      hoje,
            }
            logger.info(f"Parcial 13h: dados_usuario[{chat_id}] atualizado ({hoje})")

            if len(vendas) == 0:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"📊 <b>Parcial do dia — {hoje}</b>\n\n"
                        f"🚨 Nenhuma venda registrada até as 13h.\n"
                        f"Verifique se os totens estão operando normalmente."
                    ),
                    parse_mode="HTML",
                    reply_markup=kb_menu(f"Hoje ({hoje})")
                )
                await asyncio.sleep(3)
                continue

            # ─── VERIFICAÇÃO PROATIVA (ritmo + cancelamento suspeito) ───
            try:
                await verificar_alertas_proativos(
                    bot, chat_id, pdv_email, pdv_senha, vendas, total_cancel, hora_atual=13
                )
            except Exception as e:
                logger.warning(f"Parcial 13h: alertas proativos falharam para {chat_id}: {e}")

            total  = vendas["valor"].sum()
            n      = len(vendas)
            ticket = vendas["valor"].mean()

            # Verifica cancelamentos
            cancel_total = total_cancel.get("_total", 0) if isinstance(total_cancel, dict) else float(total_cancel or 0)
            pct_cancel   = (cancel_total / (total + cancel_total) * 100) if (total + cancel_total) > 0 else 0

            # ─── SEMPRE ENVIA RESUMO ─────────────────────────────────────
            # (antes só enviava se tinha alerta de cancelamento > 25%)
            
            # Faturamento por filial
            filiais_txt = ""
            if "nomeFilial" in vendas.columns:
                por_filial = vendas.groupby("nomeFilial")["valor"].sum()
                linhas_f   = [f"  • {f.title()}: R$ {v:,.2f}" for f, v in por_filial.items()]
                filiais_txt = "\n" + "\n".join(linhas_f)

            # Cancelamentos por filial
            cancel_txt = ""
            if cancel_total > 0:
                cancel_txt = f"\n\n⚠️ <b>Cancelamentos: R$ {cancel_total:.2f} ({pct_cancel:.1f}%)</b>"
                if isinstance(total_cancel, dict):
                    linhas_c = [f"  • {f.title()}: R$ {v:.2f}" for f, v in total_cancel.items() if not f.startswith("_") and v > 0]
                    if linhas_c:
                        cancel_txt += "\n" + "\n".join(linhas_c)

            # Mensagem de resumo SEMPRE enviada
            msg_resumo = (
                f"📊 <b>Parcial do dia — {hoje}</b>\n\n"
                f"💰 Até as 13h:\n"
                f"  • Total: <b>R$ {total:,.2f}</b>\n"
                f"  • Transações: {n}\n"
                f"  • Ticket médio: R$ {ticket:.2f}"
                f"{filiais_txt}"
                f"{cancel_txt}"
            )
            
            await bot.send_message(
                chat_id=chat_id,
                text=msg_resumo,
                parse_mode="HTML",
                reply_markup=kb_menu(f"Hoje ({hoje})")
            )
            logger.info(f"Parcial 13h enviada para {chat_id} ✅")
            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"Erro em enviar_parcial_dia para {chat_id}: {e}")
            await asyncio.sleep(3)
            continue


async def enviar_alertas_proativos(modo: str = "completo"):
    """
    Busca dados frescos e envia alertas relevantes.
    modo='basico'   → só zero vendas (seg–qua)
    modo='completo' → zero vendas + cancelamentos + pico noturno (qui–dom)
    """
    from database import listar_usuarios_com_acesso
    usuarios = await listar_usuarios_com_acesso()
    if not usuarios:
        return

    bot   = Bot(token=TELEGRAM_TOKEN)
    agora = datetime.now(BRASILIA)
    hoje  = agora.strftime("%d/%m/%Y")

    for usuario in usuarios:
        chat_id   = usuario["chat_id"]
        pdv_email = usuario.get("pdv_email")
        pdv_senha = usuario.get("pdv_senha")
        if not pdv_email or not pdv_senha:
            continue
        try:
            from scraper import baixar_relatorios_periodo
            from bot import normalizar_vendas, normalizar_produtos, dados_usuario, kb_menu

            # Busca dados frescos do dia atual
            path_vendas, path_produtos, total_cancel = await asyncio.get_event_loop().run_in_executor(
                None, baixar_relatorios_periodo, hoje, hoje, pdv_email, pdv_senha
            )

            vendas   = normalizar_vendas(pd.read_excel(path_vendas))
            produtos = normalizar_produtos(pd.read_excel(path_produtos))

            # Atualiza dados_usuario com dados frescos de hoje
            if chat_id not in dados_usuario:
                dados_usuario[chat_id] = {}
            
            dados_usuario[chat_id].update({
                "vendas":        vendas,
                "produtos":      produtos,
                "total_cancel":  total_cancel,
                "periodo_label": f"Hoje ({hoje})",
                "data_ini":      hoje,
                "data_fim":      hoje,
            })
            logger.info(f"Alertas: dados_usuario[{chat_id}] atualizado com dados de hoje ({hoje})")

            if len(vendas) == 0:
                continue

            hora_atual = agora.hour

            # ─── VERIFICAÇÃO PROATIVA (ritmo + cancelamento suspeito) ───
            # Detecta os 6 padrões de cancelamentos suspeitos
            deteccao_proativa = None
            try:
                deteccao_proativa = await verificar_alertas_proativos(
                    bot, chat_id, pdv_email, pdv_senha, vendas, total_cancel, hora_atual=hora_atual
                )
            except Exception as e:
                logger.warning(f"Alertas proativos falharam para {chat_id} às {hora_atual}h: {e}")

            alertas = []

            # ─── CANCELAMENTOS SUSPEITOS (das 6 camadas de detecção) ───
            # Captura alertas de padrões anormais (valor alto, múltiplos, etc)
            if deteccao_proativa and deteccao_proativa.get("tem_alerta"):
                from agente import _formatar_alertas_cancelamento
                alertas_suspeitos = deteccao_proativa.get("alertas", [])
                if alertas_suspeitos:
                    bloco_formatado = _formatar_alertas_cancelamento(alertas_suspeitos)
                    if bloco_formatado:
                        alertas.append(bloco_formatado)
                        logger.info(f"Alertas: {len(alertas_suspeitos)} alertas de cancelamento suspeito para {chat_id}")

            # Alerta de cancelamentos acima de 25% — em todas as janelas completas.
            # Usa o TOTAL REAL de cancelamentos lido do PDV (total_cancel['_total']),
            # a MESMA fonte da parcial das 13h. Antes usava a coluna
            # ValorItensCancelados da planilha de vendas, que NÃO inclui
            # cancelamentos integrais (desistências), gerando divergência: a
            # parcial mostrava 27% mas o alerta calculava ~0% e não disparava.
            if modo == "completo":
                if isinstance(total_cancel, dict):
                    cancel = total_cancel.get("_total", 0) or 0
                else:
                    cancel = float(total_cancel or 0)
                total = vendas["valor"].sum() if "valor" in vendas.columns else 0
                if total > 0 and cancel > 0 and (cancel / total) > 0.25:
                    # Assinatura por faixa de valor: só reenvia se o total de
                    # cancelamentos subiu de faixa (ex: cada R$ 50 a mais é "novo").
                    faixa = int(cancel // 50)
                    assinatura_cancel = f"cancel25:{faixa}"
                    if not _ja_alertou_hoje(chat_id, assinatura_cancel):
                        detalhe_filiais = ""
                        if isinstance(total_cancel, dict):
                            linhas_filial = []
                            for filial, val in total_cancel.items():
                                if filial.startswith("_") or val == 0:
                                    continue
                                linhas_filial.append(f"  • {filial.title()}: R$ {val:.2f}")
                            if linhas_filial:
                                detalhe_filiais = "\n" + "\n".join(linhas_filial)
                        alertas.append(
                            f"⚠️ <b>Cancelamentos acima do limite</b>\n"
                            f"Total: R$ {cancel:.2f} ({cancel/total*100:.1f}% do faturamento){detalhe_filiais}\n"
                            f"Limite saudável: até 25%."
                        )

            # Alerta zero vendas — todos os modos
            vendas2 = vendas.copy()
            if "HoraAbertura" in vendas2.columns:
                vendas2["hora"] = pd.to_datetime(vendas2["HoraAbertura"], format="%H:%M:%S", errors="coerce").dt.hour
                vendas_ate_agora = vendas2[vendas2["hora"] <= hora_atual]
                n_vendas = len(vendas_ate_agora)
            else:
                n_vendas = len(vendas2)

            if n_vendas == 0 and hora_atual >= 10:
                # Zero vendas: alerta uma vez por dia (não repete a cada janela)
                if not _ja_alertou_hoje(chat_id, "zero_vendas"):
                    alertas.append(
                        f"🚨 Nenhuma venda registrada hoje até às {hora_atual}h. "
                        f"Verifique se o sistema está operando normalmente."
                    )

            # Pico noturno — só faz sentido DEPOIS que o período 19h-21h já passou.
            # Antes das 21h essas horas ainda não ocorreram no dia, então
            # "zero vendas entre 19h e 22h" seria sempre falso-positivo.
            if modo == "completo" and hora_atual >= 21 and "hora" in vendas2.columns:
                vendas_noite = vendas2[vendas2["hora"].between(19, 21)]
                if len(vendas_noite) == 0:
                    if not _ja_alertou_hoje(chat_id, "pico_noturno"):
                        alertas.append(
                            f"🌙 Nenhuma venda registrada entre 19h e 22h. "
                            f"Pico noturno sem movimento — verifique os totens."
                        )

            if not alertas:
                logger.info(f"Alertas: nenhum alerta para {chat_id} às {hora_atual}h")
                continue

            texto = f"🔔 <b>Alertas do MercadoBot</b> — {agora:%H:%M}\n\n" + "\n\n".join(alertas)
            texto += f"\n\n<i>Dados atualizados às {agora:%H:%M}. Use o menu para analisar.</i>"

            await bot.send_message(
                chat_id=chat_id,
                text=texto,
                parse_mode="HTML",
                reply_markup=kb_menu(f"Hoje ({hoje})")
            )
            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"Erro nos alertas proativos para {chat_id}: {e}")


async def enviar_alerta_pico():
    """
    Dispara às 19h qui-dom.
    Busca dados frescos, atualiza dados_usuario,
    e alerta só se não houver vendas entre 19h e 19h59.
    """
    from database import listar_usuarios_com_acesso
    usuarios = await listar_usuarios_com_acesso()
    if not usuarios:
        return

    bot   = Bot(token=TELEGRAM_TOKEN)
    agora = datetime.now(BRASILIA)
    hoje  = agora.strftime("%d/%m/%Y")

    for usuario in usuarios:
        chat_id   = usuario["chat_id"]
        pdv_email = usuario.get("pdv_email")
        pdv_senha = usuario.get("pdv_senha")
        if not pdv_email or not pdv_senha:
            continue
        try:
            from scraper import baixar_relatorios_periodo
            from bot import normalizar_vendas, normalizar_produtos, dados_usuario, kb_menu

            # Busca dados frescos do dia
            path_vendas, path_produtos, total_cancel = await asyncio.get_event_loop().run_in_executor(
                None, baixar_relatorios_periodo, hoje, hoje, pdv_email, pdv_senha
            )
            vendas   = normalizar_vendas(pd.read_excel(path_vendas))
            produtos = normalizar_produtos(pd.read_excel(path_produtos))

            # Atualiza dados_usuario
            dados_usuario[chat_id] = {
                "vendas":        vendas,
                "produtos":      produtos,
                "total_cancel":  total_cancel,
                "periodo_label": f"Hoje ({hoje})",
                "data_ini":      hoje,
                "data_fim":      hoje,
            }
            logger.info(f"Alerta pico: dados_usuario[{chat_id}] atualizado ({hoje})")

            if vendas.empty or "HoraAbertura" not in vendas.columns:
                continue

            vendas["hora"] = pd.to_datetime(vendas["HoraAbertura"], format="%H:%M:%S", errors="coerce").dt.hour

            # Às 19h: verifica se houve vendas entre 17h e 18h59
            # Se não houve → alerta preditivo antes do pico
            if agora.hour == 19:
                vendas_pre_pico = vendas[vendas["hora"].between(17, 18)]
                if len(vendas_pre_pico) == 0:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "⚠️ <b>Atenção — sem vendas nas últimas 2h!</b>\n\n"
                            "Não registrei nenhuma venda entre 17h e 19h.\n\n"
                            "O horário de pico está prestes a começar — verifique a operação:\n"
                            "• Totens ligados e conectados\n"
                            "• PDV Legal sincronizando\n"
                            "• Produtos disponíveis nas prateleiras"
                        ),
                        parse_mode="HTML",
                        reply_markup=kb_menu(f"Hoje ({hoje})")
                    )
                    logger.info(f"Alerta pré-pico disparado para {chat_id} — sem vendas 17h-19h")
                else:
                    logger.info(f"Alerta pico 19h: {len(vendas_pre_pico)} venda(s) entre 17h-19h para {chat_id} — sem alerta")
                await asyncio.sleep(3)
                continue

            # Às 20h: verifica se houve vendas entre 19h e 20h
            vendas_pico = vendas[vendas["hora"] == 19]
            if len(vendas_pico) == 0:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "⚠️ <b>Atenção — sem vendas no horário de pico!</b>\n\n"
                        "Não registrei nenhuma venda entre 19h e 20h.\n\n"
                        "Verifique:\n"
                        "• Totens ligados e conectados\n"
                        "• PDV Legal sincronizando\n"
                        "• Produtos disponíveis nas prateleiras"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb_menu(f"Hoje ({hoje})")
                )
                logger.info(f"Alerta pico 20h disparado para {chat_id} — sem vendas às 19h")
            else:
                logger.info(f"Alerta pico 20h: {len(vendas_pico)} venda(s) às 19h para {chat_id} — sem alerta")

            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"Erro no alerta de pico para {chat_id}: {e}")


async def enviar_alerta_tarde():
    """
    Dispara às 17h15 TODOS os dias.
    Verifica se houve vendas entre 13h e 16h59. Se não houve nenhuma,
    alerta que a operação pode ter parado de vender à tarde.
    Só envia quando o período 13h-17h está sem nenhuma venda.
    """
    from database import listar_usuarios_com_acesso
    usuarios = await listar_usuarios_com_acesso()
    if not usuarios:
        return

    bot   = Bot(token=TELEGRAM_TOKEN)
    agora = datetime.now(BRASILIA)
    hoje  = agora.strftime("%d/%m/%Y")

    for usuario in usuarios:
        chat_id   = usuario["chat_id"]
        pdv_email = usuario.get("pdv_email")
        pdv_senha = usuario.get("pdv_senha")
        if not pdv_email or not pdv_senha:
            continue
        try:
            from scraper import baixar_relatorios_periodo
            from bot import normalizar_vendas, normalizar_produtos, dados_usuario, kb_menu

            # Busca dados frescos do dia
            path_vendas, path_produtos, total_cancel = await asyncio.get_event_loop().run_in_executor(
                None, baixar_relatorios_periodo, hoje, hoje, pdv_email, pdv_senha
            )
            vendas   = normalizar_vendas(pd.read_excel(path_vendas))
            produtos = normalizar_produtos(pd.read_excel(path_produtos))

            # Atualiza dados_usuario
            dados_usuario[chat_id] = {
                "vendas":        vendas,
                "produtos":      produtos,
                "total_cancel":  total_cancel,
                "periodo_label": f"Hoje ({hoje})",
                "data_ini":      hoje,
                "data_fim":      hoje,
            }
            logger.info(f"Alerta tarde: dados_usuario[{chat_id}] atualizado ({hoje})")

            if vendas.empty or "HoraAbertura" not in vendas.columns:
                continue

            vendas["hora"] = pd.to_datetime(vendas["HoraAbertura"], format="%H:%M:%S", errors="coerce").dt.hour

            # Verifica se houve vendas entre 13h e 16h59
            vendas_tarde = vendas[vendas["hora"].between(13, 16)]
            if len(vendas_tarde) == 0:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "⚠️ <b>Atenção — sem vendas à tarde!</b>\n\n"
                        "Não registrei nenhuma venda entre 13h e 17h.\n\n"
                        "Pode ser um problema na operação — verifique:\n"
                        "• Totens ligados e conectados\n"
                        "• PDV Legal sincronizando\n"
                        "• Produtos disponíveis nas prateleiras"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb_menu(f"Hoje ({hoje})")
                )
                logger.info(f"Alerta tarde disparado para {chat_id} — sem vendas 13h-17h")
            else:
                logger.info(f"Alerta tarde: {len(vendas_tarde)} venda(s) entre 13h-17h para {chat_id} — sem alerta")

            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"Erro no alerta de tarde para {chat_id}: {e}")


async def enviar_onboarding_guiado():
    """
    Mensagens automáticas ao longo do trial e do mês.
    Trial: dias 1, 2, 4 e 2 dias antes do fim (só valor)
    Mês: engajamento nos dias 10, 15, 20, 25
    """
    from database import listar_usuarios_com_acesso
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    usuarios = await listar_usuarios_com_acesso()
    if not usuarios:
        return

    bot   = Bot(token=TELEGRAM_TOKEN)
    agora = datetime.now(BRASILIA)

    for usuario in usuarios:
        chat_id   = usuario["chat_id"]
        status    = usuario.get("status")
        trial_fim = usuario.get("trial_fim")
        nome      = usuario.get("nome_mercadinho") or usuario.get("nome", "Operador")

        if status not in ("trial", "ativo"):
            continue

        try:
            # ─── Mensagens do trial ──────────────────────────
            if status == "trial" and trial_fim:
                fim           = datetime.fromisoformat(trial_fim)
                dias_rest     = (fim - agora).days
                dias_no_trial = 7 - dias_rest

                if dias_no_trial == 1:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"👋 <b>{nome}, bem-vindo ao MercadoBot!</b>\n\n"
                            f"💡 <b>Dica do dia — Lista de Reposição</b>\n\n"
                            f"Use /reposicao para gerar automaticamente o que precisa "
                            f"comprar em cada unidade — baseado no que realmente saiu "
                            f"da prateleira. Você pode baixar em Excel por loja ou unificado."
                        ),
                        parse_mode="HTML"
                    )

                elif dias_no_trial == 2:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"📅 <b>Dica — Analise qualquer período com um toque</b>\n\n"
                            f"No menu principal você tem o botão <b>📅 Analisar outro período</b>.\n\n"
                            f"Com ele você escolhe o período que quer ver e o bot busca tudo automaticamente no PDV Legal:\n"
                            f"• Hoje\n"
                            f"• Ontem\n"
                            f"• Últimos 7, 15 ou 30 dias\n"
                            f"• Mês atual ou anterior\n\n"
                            f"O período carregado aparece sempre no topo do menu para você saber exatamente qual está analisando.\n\n"
                            f"💡 O botão <b>📊 Briefing</b> também pede o período e já entrega o relatório completo automaticamente."
                        ),
                        parse_mode="HTML"
                    )

                elif dias_no_trial == 4:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"⚡ <b>Alertas proativos ativos!</b>\n\n"
                            f"Às 13h, 20h e 22h o MercadoBot verifica suas operações "
                            f"automaticamente. Você só recebe mensagem se houver algo "
                            f"relevante — cancelamentos altos, totem parado, queda de vendas.\n\n"
                            f"Se não tiver nada, silêncio total. Só o que importa."
                        ),
                        parse_mode="HTML"
                    )

                elif dias_rest == 2:
                    # Só valor — sem mencionar fim ou cancelamento
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"⭐ <b>{nome}, o MercadoBot está trabalhando por você!</b>\n\n"
                            f"Nas últimas semanas você teve:\n"
                            f"• Briefing automático todo dia às 7h\n"
                            f"• Alertas proativos sem precisar pedir\n"
                            f"• Análises completas com um toque\n"
                            f"• Lista de reposição inteligente\n\n"
                            f"Tudo isso por menos de <b>R$ 1 por dia</b>. 🚀"
                        ),
                        parse_mode="HTML"
                    )

            # ─── Engajamento ao longo do mês ─────────────────
            dia_do_mes = agora.day
            hora_atual = agora.hour

            if hora_atual != 9:
                continue

            if dia_do_mes == 10:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"💡 <b>Você conhece o Score de Saúde?</b>\n\n"
                        f"Use /score para ver a nota das suas operações — "
                        f"de 0 a 10, calculada por faturamento, cancelamentos, "
                        f"ticket médio e consistência.\n\n"
                        f"É a forma mais rápida de saber se sua operação "
                        f"está saudável ou precisa de atenção."
                    ),
                    parse_mode="HTML"
                )

            elif dia_do_mes == 15:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Sim, quero ver a parcial", callback_data="atualizar_mes")],
                    [InlineKeyboardButton("Agora não",                   callback_data="menu_principal")],
                ])
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"📅 <b>Meio do mês!</b>\n\n"
                        f"Quer ver como estão suas operações na primeira "
                        f"quinzena? Posso gerar uma parcial agora com "
                        f"faturamento, produtos e projeção do mês. 👇"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb
                )

            elif dia_do_mes == 20:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"📈 <b>Dica — Compare suas unidades</b>\n\n"
                        f"Use /comparativo para ver um ranking lado a lado "
                        f"de todas as suas unidades — faturamento, ticket médio "
                        f"e cancelamentos em uma só tela.\n\n"
                        f"Ótimo para identificar qual unidade precisa de atenção."
                    ),
                    parse_mode="HTML"
                )

            elif dia_do_mes == 25:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎯 Sim, ver projeção", callback_data="projecao")],
                    [InlineKeyboardButton("Agora não",            callback_data="menu_principal")],
                ])
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🎯 <b>Faltam ~5 dias para fechar o mês!</b>\n\n"
                        f"Quer ver a projeção de fechamento com base "
                        f"no ritmo atual das suas operações?"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb
                )

            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"Erro no onboarding guiado para {chat_id}: {e}")
