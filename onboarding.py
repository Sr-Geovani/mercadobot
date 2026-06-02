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
AGUARDA_PDV_EMAIL = 1
AGUARDA_PDV_SENHA = 2

def b(t): return f"<b>{t}</b>"
def i(t): return f"<i>{t}</i>"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user    = update.effective_user
    nome    = user.first_name or "Operador"

    usuario = await buscar_usuario(chat_id)

    if usuario and usuario["status"] in ("trial", "ativo"):
        from bot import kb_menu
        await update.message.reply_text(
            f"👋 Bem-vindo de volta, {b(nome)}!\n\n"
            f"Use o menu abaixo para acessar suas análises 👇",
            parse_mode="HTML",
            reply_markup=kb_menu()
        )
        return ConversationHandler.END

    if usuario and usuario["status"] in ("bloqueado", "cancelado"):
        await update.message.reply_text(
            f"⚠️ {b('Sua assinatura está inativa.')}\n\n"
            f"Para reativar o MercadoBot, use /assinar.",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    # Novo usuário
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

    if "@" not in pdv_email or "." not in pdv_email:
        await update.message.reply_text(
            "⚠️ E-mail inválido. Digite o e-mail que você usa para entrar no PDV Legal:"
        )
        return AGUARDA_PDV_EMAIL

    context.user_data["pdv_email"] = pdv_email

    await update.message.reply_text(
        f"✅ E-mail: {i(pdv_email)}\n\n"
        f"🔐 {b('Sobre a segurança das suas credenciais:')}\n\n"
        f"Sua senha é usada {b('exclusivamente')} para acessar o PDV Legal e baixar seus relatórios automaticamente — da mesma forma que você faz hoje manualmente.\n\n"
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
    pdv_email = context.user_data["pdv_email"]
    pdv_senha = update.message.text.strip()

    # Apaga a mensagem com a senha por segurança
    try:
        await update.message.delete()
    except Exception:
        pass

    await update.message.reply_text("⏳ Configurando sua conta, aguarde...")

    try:
        # Cria usuário no banco
        await criar_usuario(chat_id, nome, pdv_email)
        await atualizar_usuario(
            chat_id,
            pdv_email=pdv_email,
            pdv_senha=pdv_senha,
            status="trial"
        )

        # Cria cliente no Asaas
        cliente  = await criar_cliente_asaas(nome, pdv_email)
        asaas_id = cliente.get("id")
        await atualizar_usuario(chat_id, asaas_id=asaas_id)

        # Gera link de pagamento
        link = await gerar_link_pagamento(asaas_id, chat_id)

        # Monta teclado apenas com botões válidos
        botoes = []
        if link:
            botoes.append([InlineKeyboardButton("💳 Ativar trial — cadastrar cartão", url=link)])
        botoes.append([InlineKeyboardButton("📊 Explorar o bot agora", callback_data="atualizar_menu")])
        kb = InlineKeyboardMarkup(botoes)

        msg_link = (
            f"\n\n👇 Cadastre seu cartão para garantir a continuidade após o trial:"
            if link else
            f"\n\n💡 Use /start para gerar seu link de pagamento quando quiser ativar."
        )

        await update.message.reply_text(
            f"🎉 {b('Conta criada!')}\n\n"
            f"Seu {b('trial de 7 dias')} está ativo agora.\n"
            f"A primeira cobrança de {b('R$ 29,90')} só acontece no 8º dia — "
            f"você pode cancelar antes disso sem custo algum."
            f"{msg_link}",
            parse_mode="HTML",
            reply_markup=kb
        )

    except Exception as e:
        logger.error(f"Erro no onboarding: {e}")
        await update.message.reply_text(
            "❌ Erro ao criar sua conta. Tente novamente com /start."
        )

    return ConversationHandler.END


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Cadastro cancelado. Use /start para começar novamente quando quiser."
    )
    return ConversationHandler.END


def conversation_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            AGUARDA_PDV_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_pdv_email)],
            AGUARDA_PDV_SENHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_pdv_senha)],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar)],
        allow_reentry=True,
        per_message=False,
        per_chat=True,
    )
