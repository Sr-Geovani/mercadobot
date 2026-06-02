"""
onboarding.py — Fluxo de cadastro e ativação de novos usuários.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters

from database import criar_usuario, buscar_usuario, atualizar_usuario, usuario_tem_acesso
from pagamento import criar_cliente_asaas, gerar_link_pagamento

logger = logging.getLogger(__name__)

# Estados da conversa
AGUARDA_PDV_EMAIL       = 1
AGUARDA_PDV_SENHA       = 2
AGUARDA_CPF             = 3
AGUARDA_NOME_MERCADINHO = 4
AGUARDA_NOVO_PDV_EMAIL  = 5
AGUARDA_NOVO_PDV_SENHA  = 6

def b(t): return f"<b>{t}</b>"
def i(t): return f"<i>{t}</i>"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user    = update.effective_user
    nome    = user.first_name or "Operador"

    usuario = await buscar_usuario(chat_id)

    if usuario:
        status = usuario["status"]

        if status in ("trial", "ativo"):
            from bot import kb_menu
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Abrir menu", callback_data="menu_principal")],
                [InlineKeyboardButton("⚙️ Atualizar credenciais PDV Legal", callback_data="atualizar_credenciais")],
            ])
            await update.message.reply_text(
                f"👋 Bem-vindo de volta, {b(nome)}!\n\n"
                f"Sua assinatura está ativa. O que deseja fazer?",
                parse_mode="HTML",
                reply_markup=kb
            )
            return ConversationHandler.END

        if status == "pendente":
            try:
                from pagamento import verificar_pagamento_confirmado
                pago = await verificar_pagamento_confirmado(usuario.get("asaas_id", ""))
                if pago:
                    from datetime import datetime, timedelta
                    from zoneinfo import ZoneInfo
                    brasilia  = ZoneInfo("America/Sao_Paulo")
                    trial_fim = (datetime.now(brasilia) + timedelta(days=7)).isoformat()
                    assin_fim = (datetime.now(brasilia) + timedelta(days=31)).isoformat()
                    await atualizar_usuario(chat_id, status="trial", trial_fim=trial_fim, assinatura_fim=assin_fim)
                    from bot import kb_menu
                    await update.message.reply_text(
                        f"👋 Bem-vindo de volta, {b(nome)}!\n\n"
                        f"✅ Pagamento identificado. Acesso liberado!\n\n"
                        f"Use o menu abaixo para começar 👇",
                        parse_mode="HTML",
                        reply_markup=kb_menu()
                    )
                    return ConversationHandler.END
            except Exception as e:
                logger.warning(f"Erro ao verificar pagamento: {e}")

            await update.message.reply_text(
                f"👋 {b(nome)}, você já tem um cadastro!\n\n"
                f"⏳ Seu pagamento ainda não foi confirmado.\n"
                f"Se já cadastrou o cartão, aguarde e tente /start novamente.",
                parse_mode="HTML"
            )
            return ConversationHandler.END

        if status in ("bloqueado", "cancelado", "expirado"):
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Reativar assinatura", callback_data="reativar")],
            ])
            await update.message.reply_text(
                f"👋 {b(nome)}, bem-vindo de volta!\n\n"
                f"Sua assinatura está inativa. Para reativar:",
                parse_mode="HTML",
                reply_markup=kb
            )
            return ConversationHandler.END

    # Usuário não está no banco — pede email para buscar no Asaas
    await update.message.reply_text(
        f"👋 Olá, {b(nome)}! Bem-vindo ao {b('MercadoBot')}!\n\n"
        f"Sou o assistente de inteligência para mercadinhos autônomos em condomínios. "
        f"Conecto direto ao seu PDV Legal e entrego análises, alertas e insights no Telegram — sem você abrir nenhum relatório.\n\n"
        f"🎁 {b('7 dias grátis')} para experimentar tudo.\n"
        f"Depois, apenas {b('R$ 29,90/mês')} com renovação automática.\n\n"
        f"Para começar, preciso do seu {b('e-mail de login do PDV Legal')}:",
        parse_mode="HTML"
    )
    return AGUARDA_PDV_EMAIL


async def receber_pdv_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pdv_email = update.message.text.strip().lower()
    chat_id   = update.effective_chat.id
    user      = update.effective_user
    nome      = user.first_name or "Operador"

    if "@" not in pdv_email or "." not in pdv_email:
        await update.message.reply_text(
            "⚠️ E-mail inválido. Digite o e-mail que você usa para entrar no PDV Legal:"
        )
        return AGUARDA_PDV_EMAIL

    context.user_data["pdv_email"] = pdv_email

    # Verifica se o email já existe no Asaas com pagamento recente
    try:
        from pagamento import buscar_cliente_por_email, verificar_pagamento_confirmado
        cliente = await buscar_cliente_por_email(pdv_email)
        if cliente:
            asaas_id = cliente.get("id")
            pago = await verificar_pagamento_confirmado(asaas_id)
            if pago:
                # Recria o usuário no banco com acesso ativo
                from datetime import datetime, timedelta
                from zoneinfo import ZoneInfo
                brasilia  = ZoneInfo("America/Sao_Paulo")
                trial_fim = (datetime.now(brasilia) + timedelta(days=7)).isoformat()
                assin_fim = (datetime.now(brasilia) + timedelta(days=31)).isoformat()
                await criar_usuario(chat_id, nome, pdv_email)
                await atualizar_usuario(
                    chat_id,
                    asaas_id=asaas_id,
                    status="trial",
                    trial_fim=trial_fim,
                    assinatura_fim=assin_fim
                )
                from bot import kb_menu
                await update.message.reply_text(
                    f"✅ {b('Conta reconhecida!')}\n\n"
                    f"Identificamos seu pagamento ativo.\n"
                    f"Para finalizar, informe sua {b('senha do PDV Legal')}:",
                    parse_mode="HTML"
                )
                context.user_data["asaas_id"]      = asaas_id
                context.user_data["ja_tem_acesso"]  = True
                return AGUARDA_PDV_SENHA
    except Exception as e:
        logger.warning(f"Erro ao buscar no Asaas: {e}")

    await update.message.reply_text(
        f"✅ E-mail registrado.\n\n"
        f"Qual o nome da sua {b('marca ou operação')}?\n\n"
        f"Exemplo: {i('VenueMarket')} ou {i('CondoShop')}\n\n"
        f"Esse nome será usado para personalizar seu briefing diário:",
        parse_mode="HTML"
    )
    return AGUARDA_NOME_MERCADINHO


async def receber_nome_mercadinho(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome_mercadinho = update.message.text.strip()
    context.user_data["nome_mercadinho"] = nome_mercadinho

    await update.message.reply_text(
        f"✅ {b(nome_mercadinho)} registrado!\n\n"
        f"Para emitir sua cobrança, preciso do seu {b('CPF ou CNPJ')} (só números).\n\n"
        f"🎁 {b('Não se preocupe')} — você terá {b('7 dias de teste gratuito')} "
        f"antes de qualquer cobrança. Pode cancelar a qualquer momento sem custo.\n\n"
        f"Digite seu CPF ou CNPJ:",
        parse_mode="HTML"
    )
    return AGUARDA_CPF


async def receber_cpf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cpf = "".join(filter(str.isdigit, update.message.text.strip()))

    try:
        await update.message.delete()
    except Exception:
        pass

    if len(cpf) not in (11, 14):
        await update.message.reply_text(
            "⚠️ CPF ou CNPJ inválido. Digite apenas os números (11 dígitos para CPF, 14 para CNPJ):"
        )
        return AGUARDA_CPF

    context.user_data["cpf"] = cpf

    await update.message.reply_text(
        f"✅ Documento registrado.\n\n"
        f"🔐 {b('Sobre a segurança das suas credenciais:')}\n\n"
        f"Sua senha é usada {b('exclusivamente')} para acessar o PDV Legal e baixar seus relatórios automaticamente.\n\n"
        f"• Não compartilhamos suas credenciais com terceiros\n"
        f"• Não realizamos nenhuma alteração no seu sistema\n"
        f"• O acesso é somente leitura (download de relatórios)\n"
        f"• Você pode revogar o acesso a qualquer momento\n\n"
        f"Agora informe sua {b('senha do PDV Legal')}:",
        parse_mode="HTML"
    )
    return AGUARDA_PDV_SENHA


async def receber_pdv_senha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id   = update.effective_chat.id
    user      = update.effective_user
    nome      = user.first_name or "Operador"
    pdv_email         = context.user_data["pdv_email"]
    cpf               = context.user_data.get("cpf", "")
    nome_mercadinho   = context.user_data.get("nome_mercadinho", "")
    pdv_senha         = update.message.text.strip()
    ja_tem_acesso     = context.user_data.get("ja_tem_acesso", False)

    try:
        await update.message.delete()
    except Exception:
        pass

    await update.message.reply_text("⏳ Configurando sua conta, aguarde...")

    try:
        # Usuário reconhecido pelo Asaas — só atualiza a senha e libera
        if ja_tem_acesso:
            await atualizar_usuario(chat_id, pdv_email=pdv_email, pdv_senha=pdv_senha)
            from bot import kb_menu
            await update.message.reply_text(
                f"✅ {b('Acesso restaurado com sucesso!')}\n\n"
                f"Suas credenciais foram atualizadas.\n\n"
                f"Use o menu abaixo para começar 👇",
                parse_mode="HTML",
                reply_markup=kb_menu()
            )
            return ConversationHandler.END

        # Novo usuário — cria conta e gera cobrança
        await criar_usuario(chat_id, nome, pdv_email)
        await atualizar_usuario(
            chat_id,
            pdv_email=pdv_email,
            pdv_senha=pdv_senha,
            nome_mercadinho=nome_mercadinho,
            status="pendente"
        )

        cliente  = await criar_cliente_asaas(nome, pdv_email, cpf)
        asaas_id = cliente.get("id")
        await atualizar_usuario(chat_id, asaas_id=asaas_id)

        # Verifica se já tem assinatura ativa no Asaas (reativação)
        from pagamento import buscar_assinatura_ativa
        assinatura_id = await buscar_assinatura_ativa(asaas_id)

        if not assinatura_id:
            # Cria nova assinatura
            link, assinatura_id = await gerar_link_pagamento(asaas_id, chat_id)
        else:
            # Já tem assinatura — só busca o link da próxima cobrança
            from pagamento import buscar_link_assinatura
            link = await buscar_link_assinatura(assinatura_id)
            logger.info(f"Assinatura existente reutilizada: {assinatura_id}")

        if assinatura_id:
            await atualizar_usuario(chat_id, assinatura_asaas_id=assinatura_id)

        if link:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Cadastrar cartão e ativar trial", url=link)],
                [InlineKeyboardButton("🔍 Verificar status do acesso", callback_data="verificar_status")],
            ])
            await update.message.reply_text(
                f"🎉 {b('Conta criada com sucesso!')}\n\n"
                f"Para ativar seu {b('trial de 7 dias')}, cadastre seu {b('cartão de crédito')} agora.\n\n"
                f"• A cobrança de {b('R$ 29,90')} só acontece no 8º dia\n"
                f"• Renovação automática todo mês\n"
                f"• Cancele a qualquer momento antes do 8º dia sem custo\n\n"
                f"⚠️ {i('Aceitamos apenas cartão de crédito para garantir a recorrência automática.')}\n\n"
                f"Após cadastrar, clique em {b('Verificar status')} para confirmar. 👇",
                parse_mode="HTML",
                reply_markup=kb
            )
        else:
            await update.message.reply_text(
                f"🎉 {b('Conta criada!')}\n\n"
                f"Você receberá em breve o link para cadastrar seu cartão e ativar o trial.",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Erro no onboarding: {e}")
        await update.message.reply_text(
            "❌ Erro ao criar sua conta. Tente novamente com /start."
        )

    return ConversationHandler.END


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica o status da assinatura do usuário."""
    from database import buscar_usuario
    from datetime import datetime
    from zoneinfo import ZoneInfo

    chat_id  = update.effective_chat.id
    usuario  = await buscar_usuario(chat_id)
    brasilia = ZoneInfo("America/Sao_Paulo")

    if not usuario:
        await update.message.reply_text(
            "Você ainda não tem cadastro.\nUse /start para se cadastrar."
        )
        return

    status = usuario["status"]
    agora  = datetime.now(brasilia)

    if status == "pendente":
        await update.message.reply_text(
            f"⏳ {b('Aguardando confirmação do pagamento.')}\n\n"
            f"Se já cadastrou o cartão, aguarde alguns instantes.\n"
            f"O acesso é liberado automaticamente após a confirmação.",
            parse_mode="HTML"
        )
    elif status == "trial":
        fim = datetime.fromisoformat(usuario["trial_fim"])
        dias = (fim - agora).days + 1
        await update.message.reply_text(
            f"✅ {b('Trial ativo')}\n\n"
            f"Você tem {b(f'{dias} dias')} restantes de teste gratuito.\n"
            f"A cobrança de R$ 29,90 só acontece após o trial.",
            parse_mode="HTML"
        )
    elif status == "ativo":
        fim = datetime.fromisoformat(usuario["assinatura_fim"])
        dias = (fim - agora).days + 1
        await update.message.reply_text(
            f"✅ {b('Assinatura ativa')}\n\n"
            f"Próxima renovação em {b(f'{dias} dias')}.\n"
            f"Valor: R$ 29,90/mês.",
            parse_mode="HTML"
        )
    elif status in ("bloqueado", "cancelado", "expirado"):
        await update.message.reply_text(
            f"❌ {b('Assinatura inativa.')}\n\n"
            f"Use /start para reativar.",
            parse_mode="HTML"
        )


async def iniciar_atualizacao_credenciais(update_or_msg, chat_id: int):
    """Inicia o fluxo de atualização de credenciais PDV Legal."""
    msg = update_or_msg if hasattr(update_or_msg, 'reply_text') else update_or_msg.message
    await msg.reply_text(
        f"⚙️ {b('Atualizar credenciais PDV Legal')}\n\n"
        f"Digite seu novo {b('e-mail de login do PDV Legal')}:\n\n"
        f"{i('ou envie /cancelar para voltar ao menu')}",
        parse_mode="HTML"
    )
    return AGUARDA_NOVO_PDV_EMAIL


async def receber_novo_pdv_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    novo_email = update.message.text.strip().lower()
    if "@" not in novo_email or "." not in novo_email:
        await update.message.reply_text("⚠️ E-mail inválido. Tente novamente:")
        return AGUARDA_NOVO_PDV_EMAIL
    context.user_data["novo_pdv_email"] = novo_email
    await update.message.reply_text(
        f"✅ E-mail: {i(novo_email)}\n\n"
        f"Agora digite sua nova {b('senha do PDV Legal')}:",
        parse_mode="HTML"
    )
    return AGUARDA_NOVO_PDV_SENHA


async def receber_novo_pdv_senha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id   = update.effective_chat.id
    novo_email = context.user_data.get("novo_pdv_email")
    novo_senha = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    await atualizar_usuario(chat_id, pdv_email=novo_email, pdv_senha=novo_senha)

    from bot import kb_menu
    await update.message.reply_text(
        f"✅ {b('Credenciais atualizadas com sucesso!')}\n\n"
        f"Suas novas credenciais do PDV Legal foram salvas.\n"
        f"Use o menu abaixo para continuar:",
        parse_mode="HTML",
        reply_markup=kb_menu()
    )
    return ConversationHandler.END


async def cmd_cancelar_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela o fluxo de cadastro."""
    await update.message.reply_text(
        "Cadastro cancelado. Use /start para começar novamente quando quiser."
    )
    return ConversationHandler.END


async def cmd_cancelar_assinatura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela a assinatura do usuário."""
    from database import buscar_usuario, atualizar_usuario
    from pagamento import cancelar_assinatura

    chat_id = update.effective_chat.id
    usuario = await buscar_usuario(chat_id)

    if not usuario or usuario["status"] not in ("trial", "ativo"):
        await update.message.reply_text(
            "Você não tem uma assinatura ativa para cancelar."
        )
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Sim, cancelar assinatura", callback_data="confirmar_cancelamento")],
        [InlineKeyboardButton("↩️ Não, manter assinatura",  callback_data="menu_principal")],
    ])
    await update.message.reply_text(
        f"⚠️ {b('Cancelar assinatura')}\n\n"
        f"Tem certeza que deseja cancelar?\n\n"
        f"• Seu acesso será encerrado imediatamente\n"
        f"• Não haverá novas cobranças\n"
        f"• Você pode reativar a qualquer momento com /start",
        parse_mode="HTML",
        reply_markup=kb
    )


def conversation_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            AGUARDA_PDV_EMAIL:       [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_pdv_email)],
            AGUARDA_NOME_MERCADINHO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_nome_mercadinho)],
            AGUARDA_CPF:             [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_cpf)],
            AGUARDA_PDV_SENHA:       [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_pdv_senha)],
            AGUARDA_NOVO_PDV_EMAIL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_novo_pdv_email)],
            AGUARDA_NOVO_PDV_SENHA:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_novo_pdv_senha)],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar_onboarding)],
        allow_reentry=True,
        per_message=False,
        per_chat=True,
    )
