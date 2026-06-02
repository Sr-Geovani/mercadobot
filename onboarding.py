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
AGUARDA_EMAIL = 1
AGUARDA_PDV_EMAIL = 2
AGUARDA_PDV_SENHA = 3

def b(t): return f"<b>{t}</b>"
def i(t): return f"<i>{t}</i>"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ponto de entrada — verifica se já é usuário ou inicia cadastro."""
    chat_id = update.effective_chat.id
    user    = update.effective_user
    nome    = user.first_name or "Operador"

    usuario = await buscar_usuario(chat_id)

    if usuario and usuario["status"] in ("trial", "ativo"):
        # Usuário já cadastrado e ativo — manda para o menu
        from bot import kb_menu
        await update.message.reply_text(
            f"👋 Bem-vindo de volta, {b(nome)}!\n\n"
            f"Seus dados estão carregados. Use o menu abaixo 👇",
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

    # Novo usuário — inicia onboarding
    await update.message.reply_text(
        f"👋 Olá, {b(nome)}! Bem-vindo ao {b('MercadoBot')}!\n\n"
        f"Sou o assistente de inteligência para mercadinhos autônomos em condomínios.\n\n"
        f"🎁 {b('7 dias grátis')} para experimentar tudo.\n"
        f"Depois, apenas {b('R$ 29,90/mês')} com cobrança automática.\n\n"
        f"Vamos começar? Me informe seu {b('e-mail')}:",
        parse_mode="HTML"
    )
    return AGUARDA_EMAIL


async def receber_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip().lower()

    if "@" not in email or "." not in email:
        await update.message.reply_text("⚠️ E-mail inválido. Tente novamente:")
        return AGUARDA_EMAIL

    context.user_data["email"] = email
    await update.message.reply_text(
        f"✅ E-mail: {i(email)}\n\n"
        f"Agora informe seu {b('e-mail de login do PDV Legal')}:",
        parse_mode="HTML"
    )
    return AGUARDA_PDV_EMAIL


async def receber_pdv_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pdv_email = update.message.text.strip()
    context.user_data["pdv_email"] = pdv_email
    await update.message.reply_text(
        f"✅ Login PDV Legal salvo.\n\n"
        f"Agora informe sua {b('senha do PDV Legal')}:\n\n"
        f"{i('Suas credenciais são criptografadas e usadas apenas para baixar seus relatórios.')}",
        parse_mode="HTML"
    )
    return AGUARDA_PDV_SENHA


async def receber_pdv_senha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id   = update.effective_chat.id
    user      = update.effective_user
    nome      = user.first_name or "Operador"
    email     = context.user_data["email"]
    pdv_email = context.user_data["pdv_email"]
    pdv_senha = update.message.text.strip()

    await update.message.reply_text("⏳ Configurando sua conta...")

    # Apaga a mensagem com a senha por segurança
    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        # Cria usuário no banco
        await criar_usuario(chat_id, nome, email)
        await atualizar_usuario(
            chat_id,
            pdv_email=pdv_email,
            pdv_senha=pdv_senha,
            status="trial"
        )

        # Cria cliente no Asaas
        cliente = await criar_cliente_asaas(nome, email)
        asaas_id = cliente.get("id")
        await atualizar_usuario(chat_id, asaas_id=asaas_id)

        # Gera link de pagamento
        link = await gerar_link_pagamento(asaas_id, chat_id)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Cadastrar cartão e ativar trial", url=link)],
            [InlineKeyboardButton("📊 Explorar o bot agora", callback_data="menu_principal")],
        ])

        await update.message.reply_text(
            f"🎉 {b('Conta criada com sucesso!')}\n\n"
            f"Seu {b('trial de 7 dias')} começa agora.\n"
            f"A cobrança de {b('R$ 29,90')} só acontece no 8º dia.\n\n"
            f"👇 Cadastre seu cartão para garantir a continuidade:",
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
    await update.message.reply_text("Cadastro cancelado. Use /start para começar novamente.")
    return ConversationHandler.END


def conversation_handler():
    """Retorna o ConversationHandler do onboarding."""
    return ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            AGUARDA_EMAIL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_email)],
            AGUARDA_PDV_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_pdv_email)],
            AGUARDA_PDV_SENHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_pdv_senha)],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar)],
        allow_reentry=True,
    )
