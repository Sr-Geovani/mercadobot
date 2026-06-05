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

import pandas as pd
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton

logger         = logging.getLogger(__name__)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
BRASILIA       = ZoneInfo("America/Sao_Paulo")
HORARIO_HORA   = int(os.environ.get("BRIEFING_HORA",   "7"))
HORARIO_MINUTO = int(os.environ.get("BRIEFING_MINUTO", "0"))


def b(t): return f"<b>{t}</b>"
def i(t): return f"<i>{t}</i>"


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

        # Importa o scraper e processa com as credenciais do usuário
        from scraper import baixar_relatorios
        loop = asyncio.get_event_loop()
        path_vendas, path_produtos = await loop.run_in_executor(
            None, baixar_relatorios, pdv_email, pdv_senha
        )

        vendas   = pd.read_excel(path_vendas)
        produtos = pd.read_excel(path_produtos)

        # Normaliza dados
        from bot import normalizar_vendas, normalizar_produtos, bloco_faturamento, \
            bloco_categorias, bloco_pagamentos, bloco_semanal, \
            g_faturamento, g_categorias, g_pagamentos, g_semanal, \
            insight_ia, resumo_dados, kb_menu

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

        # Envia blocos separados
        await bot.send_message(chat_id=chat_id, text=bloco_faturamento(vendas, produtos), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_faturamento(vendas))

        await bot.send_message(chat_id=chat_id, text=bloco_categorias(produtos), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_categorias(produtos))

        await bot.send_message(chat_id=chat_id, text=bloco_pagamentos(vendas), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_pagamentos(vendas))

        await bot.send_message(chat_id=chat_id, text=bloco_semanal(vendas), parse_mode="HTML")
        await bot.send_photo(chat_id=chat_id, photo=g_semanal(vendas))

        # Insight IA aleatório
        from bot import resumo_dados as _resumo
        ctx = (
            f"VENDAS: {len(vendas)} transações, R$ {vendas['valor'].sum():.2f} total\n"
            f"TICKET MÉDIO: R$ {vendas['valor'].mean():.2f}\n"
            f"CANCELAMENTOS: R$ {vendas['ValorItensCancelados'].sum():.2f}"
        )
        insight = await insight_ia(ctx)
        await bot.send_message(
            chat_id=chat_id,
            text=f"💡 {b('INSIGHT DO DIA')}\n\n{insight}",
            parse_mode="HTML"
        )

        # Menu
        await bot.send_message(
            chat_id=chat_id,
            text="O que deseja analisar agora?",
            reply_markup=kb_menu()
        )

        logger.info(f"Briefing enviado com sucesso para {chat_id}")

    except Exception as e:
        logger.error(f"Erro no briefing do usuário {chat_id}: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ Não consegui gerar o briefing automático hoje.\n\n"
                f"Possíveis causas:\n"
                f"• PDV Legal fora do ar\n"
                f"• Instabilidade na conexão\n\n"
                f"Use {b('🔄 Atualizar dados agora')} para tentar manualmente."
            ),
            parse_mode="HTML"
        )


async def enviar_briefing_automatico():
    """Busca todos os usuários ativos e envia o briefing para cada um."""
    from database import listar_usuarios_ativos

    agora    = datetime.now(BRASILIA)
    usuarios = await listar_usuarios_ativos()

    if not usuarios:
        logger.info("Nenhum usuário ativo para o briefing.")
        return

    logger.info(f"Briefing automático — {agora:%d/%m/%Y %H:%M} — {len(usuarios)} usuário(s)")
    bot = Bot(token=TELEGRAM_TOKEN)

    for usuario in usuarios:
        try:
            await briefing_usuario(bot, usuario)
            # Pequena pausa entre usuários para não sobrecarregar o PDV Legal
            await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"Erro no briefing do usuário {usuario['chat_id']}: {e}")


def iniciar_scheduler():
    """Inicia o agendador diário."""
    scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")

    # Briefing diário às 7h
    scheduler.add_job(
        enviar_briefing_automatico,
        trigger="cron",
        hour=HORARIO_HORA,
        minute=HORARIO_MINUTO,
        id="briefing_diario",
        replace_existing=True,
    )

    # Alertas proativos às 13h
    scheduler.add_job(
        enviar_alertas_proativos,
        trigger="cron",
        hour=13,
        minute=0,
        id="alertas_tarde",
        replace_existing=True,
    )

    # Alerta de pico quinta a domingo às 20h
    scheduler.add_job(
        enviar_alerta_pico,
        trigger="cron",
        day_of_week="thu,fri,sat,sun",
        hour=20,
        minute=0,
        id="alerta_pico",
        replace_existing=True,
    )

    # Alertas proativos às 22h
    scheduler.add_job(
        enviar_alertas_proativos,
        trigger="cron",
        hour=22,
        minute=0,
        id="alertas_noite",
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

    scheduler.start()
    logger.info(f"Briefing agendado para {HORARIO_HORA:02d}:{HORARIO_MINUTO:02d} (Brasília)")
    logger.info("Alertas proativos agendados para 13h e 19h")
    return scheduler


async def enviar_fechamento_semanal():
    """Envia resumo da semana todo domingo às 23h59."""
    from database import listar_usuarios_ativos
    usuarios = await listar_usuarios_ativos()
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
            path_vendas, path_produtos = await asyncio.get_event_loop().run_in_executor(
                None, baixar_relatorios_periodo, inicio_semana, fim_semana, pdv_email, pdv_senha
            )
            vendas   = pd.read_excel(path_vendas)
            produtos = pd.read_excel(path_produtos)

            from bot import (normalizar_vendas, normalizar_produtos,
                             bloco_faturamento, bloco_comparativo,
                             bloco_produto_mes, bloco_score,
                             bloco_projecao_mes, kb_menu, b)

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

            await bot.send_message(
                chat_id=chat_id,
                text="Boa semana! 🚀",
                reply_markup=kb_menu()
            )

            await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"Erro no fechamento semanal para {chat_id}: {e}")


async def enviar_alertas_proativos():
    """Envia alertas apenas quando há algo relevante — sem spam."""
    from database import listar_usuarios_ativos
    usuarios = await listar_usuarios_ativos()
    if not usuarios:
        return

    bot  = Bot(token=TELEGRAM_TOKEN)
    agora = datetime.now(BRASILIA)

    for usuario in usuarios:
        chat_id   = usuario["chat_id"]
        pdv_email = usuario.get("pdv_email")
        pdv_senha = usuario.get("pdv_senha")
        if not pdv_email or not pdv_senha:
            continue
        try:
            from scraper import baixar_relatorios_periodo
            hoje = agora.strftime("%d/%m/%Y")
            path_vendas, _ = await asyncio.get_event_loop().run_in_executor(
                None, baixar_relatorios_periodo, hoje, hoje, pdv_email, pdv_senha
            )

            vendas = pd.read_excel(path_vendas)
            from bot import normalizar_vendas
            vendas = normalizar_vendas(vendas)

            if len(vendas) == 0:
                continue

            alertas = []

            # Alerta 1: cancelamentos altos
            cancel = vendas["ValorItensCancelados"].sum()
            total  = vendas["valor"].sum()
            if total > 0 and (cancel / total) > 0.05:
                alertas.append(
                    f"⚠️ Cancelamentos em {cancel/total*100:.1f}% do faturamento hoje "
                    f"(R$ {cancel:.2f}). Acima do ideal de 5%."
                )

            # Alerta 2: queda brusca comparado à média
            hora_atual = agora.hour
            vendas2 = vendas.copy()
            vendas2["hora"] = pd.to_datetime(vendas2["HoraAbertura"], format="%H:%M:%S").dt.hour
            vendas_ate_agora = vendas2[vendas2["hora"] <= hora_atual]
            fat_hoje = vendas_ate_agora["valor"].sum()
            n_vendas  = len(vendas_ate_agora)

            if n_vendas == 0 and hora_atual >= 10:
                alertas.append(
                    f"🚨 Nenhuma venda registrada hoje até às {hora_atual}h. "
                    f"Verifique se o sistema está operando normalmente."
                )

            # Alerta 3: horário de pico sem vendas
            pico_horas = [19, 20, 21, 22]
            if hora_atual in pico_horas:
                vendas_pico = vendas2[vendas2["hora"] == hora_atual]
                if len(vendas_pico) == 0:
                    alertas.append(
                        f"🕐 Às {hora_atual}h (horário de pico) não houve vendas ainda. "
                        f"Verifique se o totem está funcionando."
                    )

            # Só envia se tiver algo relevante
            if alertas:
                texto = f"🔔 <b>Alertas do dia</b>\n\n" + "\n\n".join(alertas)
                await bot.send_message(chat_id=chat_id, text=texto, parse_mode="HTML")
                logger.info(f"Alerta proativo enviado para {chat_id}: {len(alertas)} alerta(s)")

            await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Erro no alerta proativo para {chat_id}: {e}")


async def enviar_alerta_pico():
    """Avisa às 20h que entrou no horário de pico — informativo, sem pedir interação."""
    from database import listar_usuarios_ativos
    usuarios = await listar_usuarios_ativos()
    if not usuarios:
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    for usuario in usuarios:
        chat_id = usuario["chat_id"]
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "🕐 <b>Horário de pico iniciando!</b>\n\n"
                    "São 20h — seus moradores estão chegando em casa.\n\n"
                    "✅ Garanta que os totens estão operando\n"
                    "✅ Produtos âncora repostos (bebidas, snacks)\n"
                    "✅ Conexão com internet estável\n\n"
                    "Boas vendas nessa noite! 🚀"
                ),
                parse_mode="HTML"
            )
            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"Erro no alerta de pico para {chat_id}: {e}")


async def enviar_onboarding_guiado():
    """
    Mensagens automáticas ao longo do trial e do mês.
    Trial: dias 1, 2, 4 e 2 dias antes do fim (só valor)
    Mês: engajamento nos dias 10, 15, 20, 25
    """
    from database import listar_usuarios_ativos
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    usuarios = await listar_usuarios_ativos()
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
