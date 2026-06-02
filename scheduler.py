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
        import os as _os
        _os.environ["PDV_EMAIL"] = pdv_email
        _os.environ["PDV_SENHA"] = pdv_senha

        from scraper import baixar_relatorios
        loop = asyncio.get_event_loop()
        path_vendas, path_produtos = await loop.run_in_executor(
            None, baixar_relatorios
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
        await bot.send_message(chat_id=chat_id, text=bloco_faturamento(vendas), parse_mode="HTML")
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

    # Alertas proativos às 19h
    scheduler.add_job(
        enviar_alertas_proativos,
        trigger="cron",
        hour=19,
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

    scheduler.start()
    logger.info(f"Briefing agendado para {HORARIO_HORA:02d}:{HORARIO_MINUTO:02d} (Brasília)")
    logger.info("Alertas proativos agendados para 13h e 19h")
    return scheduler


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
            import os as _os
            _os.environ["PDV_EMAIL"] = pdv_email
            _os.environ["PDV_SENHA"] = pdv_senha

            from scraper import baixar_relatorios_periodo
            hoje = agora.strftime("%d/%m/%Y")
            path_vendas, _ = await asyncio.get_event_loop().run_in_executor(
                None, baixar_relatorios_periodo, hoje, hoje
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


async def enviar_onboarding_guiado():
    """
    Mensagens automáticas nos primeiros dias do trial.
    Dia 2: mostra reposição
    Dia 3: mostra alertas
    Dia 5: lembrete do trial
    """
    from database import listar_usuarios_ativos
    usuarios = await listar_usuarios_ativos()
    if not usuarios:
        return

    bot   = Bot(token=TELEGRAM_TOKEN)
    agora = datetime.now(BRASILIA)

    for usuario in usuarios:
        chat_id  = usuario["chat_id"]
        status   = usuario.get("status")
        trial_fim = usuario.get("trial_fim")
        nome     = usuario.get("nome", "Operador")

        if status != "trial" or not trial_fim:
            continue

        try:
            fim  = datetime.fromisoformat(trial_fim)
            dias_restantes = (fim - agora).days
            dias_no_trial  = 7 - dias_restantes

            if dias_no_trial == 1:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"👋 {nome}, bem-vindo ao MercadoBot!\n\n"
                        f"💡 <b>Dica do dia:</b> Use o botão "
                        f"<b>🛒 Lista de Reposição</b> no menu para gerar automaticamente "
                        f"a lista do que precisa repor em cada unidade — baseado no que "
                        f"realmente saiu da prateleira.\n\n"
                        f"Experimente agora! 👇"
                    ),
                    parse_mode="HTML"
                )

            elif dias_no_trial == 2:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"📊 <b>Sabia que você pode consultar qualquer período?</b>\n\n"
                        f"Clique em <b>🔄 Atualizar dados agora</b> e escolha: "
                        f"hoje, ontem, últimos 7 dias ou o mês inteiro.\n\n"
                        f"O MercadoBot busca tudo direto no PDV Legal — sem você "
                        f"precisar exportar nenhum arquivo."
                    ),
                    parse_mode="HTML"
                )

            elif dias_no_trial == 4:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"⚡ <b>Alertas proativos ativados!</b>\n\n"
                        f"Às 13h e 19h o MercadoBot verifica automaticamente "
                        f"se há algo importante nas suas operações — cancelamentos "
                        f"acima do normal, totem parado, queda de vendas.\n\n"
                        f"Se não tiver nada relevante, não enviamos nada. "
                        f"Só o que importa, no momento certo."
                    ),
                    parse_mode="HTML"
                )

            elif dias_restantes == 2:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"⭐ <b>Seu trial termina em 2 dias</b>\n\n"
                        f"Esperamos que o MercadoBot esteja transformando a forma "
                        f"como você gerencia suas operações!\n\n"
                        f"A partir do dia {fim.strftime('%d/%m')}, sua assinatura de "
                        f"<b>R$ 29,90/mês</b> continua automaticamente — "
                        f"menos de R$ 1 por dia para ter um gestor inteligente "
                        f"trabalhando por você 24h.\n\n"
                        f"📊 Amanhã você recebe seu último briefing do trial. "
                        f"Aproveite para explorar tudo que o bot oferece!"
                    ),
                    parse_mode="HTML"
                )

            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"Erro no onboarding guiado para {chat_id}: {e}")
