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

        # Busca 30 dias para evolução semanal — período separado
        d30 = (agora - timedelta(days=30)).strftime("%d/%m/%Y")
        try:
            path_v30, _, _ = await loop.run_in_executor(
                None, baixar_relatorios_periodo, d30, ontem, pdv_email, pdv_senha
            )
            vendas_30 = normalizar_vendas(pd.read_excel(path_v30))
        except Exception:
            vendas_30 = vendas  # fallback: usa ontem mesmo

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

    # Alertas proativos às 13h — só zero vendas
    scheduler.add_job(
        lambda: asyncio.ensure_future(enviar_alertas_proativos(modo="basico")),
        trigger="cron",
        hour=13,
        minute=0,
        id="alertas_tarde",
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

    # Alertas 22h — todos os dias: só zero vendas
    scheduler.add_job(
        lambda: asyncio.ensure_future(enviar_alertas_proativos(modo="basico")),
        trigger="cron",
        day_of_week="mon,tue,wed",
        hour=22,
        minute=0,
        id="alertas_noite_basico",
        replace_existing=True,
    )

    # Alertas 22h — qui a dom: completo (cancelamentos + pico noturno + zero vendas)
    scheduler.add_job(
        lambda: asyncio.ensure_future(enviar_alertas_proativos(modo="completo")),
        trigger="cron",
        day_of_week="thu,fri,sat,sun",
        hour=22,
        minute=0,
        id="alertas_noite_completo",
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
            path_vendas, path_produtos, _ = await asyncio.get_event_loop().run_in_executor(
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


async def enviar_alertas_proativos(modo: str = "completo"):
    """
    Busca dados frescos e envia alertas relevantes.
    modo='basico'   → só zero vendas (seg–qua)
    modo='completo' → zero vendas + cancelamentos + pico noturno (qui–dom)
    """
    from database import listar_usuarios_ativos
    usuarios = await listar_usuarios_ativos()
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
            dados_usuario[chat_id] = {
                "vendas":        vendas,
                "produtos":      produtos,
                "total_cancel":  total_cancel,
                "periodo_label": f"Hoje ({hoje})",
                "data_ini":      hoje,
                "data_fim":      hoje,
            }
            logger.info(f"Alertas: dados_usuario[{chat_id}] atualizado com dados de hoje ({hoje})")

            if len(vendas) == 0:
                continue

            alertas = []
            hora_atual = agora.hour

            # Alerta de cancelamentos — só no modo completo (qui-dom às 22h)
            if modo == "completo":
                cancel = vendas["ValorItensCancelados"].sum() if "ValorItensCancelados" in vendas.columns else 0
                total  = vendas["valor"].sum()
                if total > 0 and cancel > 0 and (cancel / total) > 0.05:
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
                        f"Limite saudável: até 5%."
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
                alertas.append(
                    f"🚨 Nenhuma venda registrada hoje até às {hora_atual}h. "
                    f"Verifique se o sistema está operando normalmente."
                )

            # Pico noturno — só no modo completo (qui-dom às 22h)
            if modo == "completo" and "hora" in vendas2.columns:
                vendas_noite = vendas2[vendas2["hora"].between(19, 21)]
                if len(vendas_noite) == 0:
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
    from database import listar_usuarios_ativos
    usuarios = await listar_usuarios_ativos()
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
