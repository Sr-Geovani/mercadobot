# PLANO FINAL - CORREÇÃO DE REATIVAÇÃO

## PROBLEMAS A CORRIGIR

### 1. /start mostra menu mesmo com acesso expirado
- **Causa**: `usuario_tem_acesso()` retorna False, mas `/start` ignora e mostra menu anyway
- **Fix**: `/start` BLOQUEIA na mesma hora se `usuario_tem_acesso()` retornar False
- **Resultado**: "Assinatura inativa, use /reativar"

### 2. Duplicação de mensagens "Reativar MercadoBot"
- **Causa**: Múltiplos lugares gerando a mesma mensagem
- **Fix**: 1 função única `enviar_reativacao()` usada em todos os lugares
- **Resultado**: Apenas 1 mensagem com botão de reativar

### 3. Loop de "link expirado" a cada hora
- **Causa**: Webhook enviando "link expirou" repetidamente
- **Fix**: Webhook só envia mensagem UMA VEZ quando link expira
- **Resultado**: Sem spam de "link expirou"

### 4. Status muda para "pendente" após reativar
- **Causa**: Reativação está setando `status="pendente"` 
- **Fix**: Reativação NUNCA muda status para "pendente"
- **Resultado**: Status mantém "expirado" até pagamento confirmar

### 5. Botão "Verificar Status" causa confusão
- **Causa**: Abre verificador que gera mais mensagens
- **Fix**: Remover ou deixar simples (só mostra se pagamento confirmou)
- **Resultado**: 1 fluxo limpo: Reativar → Pagar → Volta automático

---

## IMPLEMENTAÇÃO

### PASSO 1: /start bloqueia imediatamente se expirado

```python
# Em onboarding.py - cmd_start()
if status in ("trial", "ativo", "cancelado_mas_ativo", "expirado"):
    tem_acesso, _ = await usuario_tem_acesso(chat_id)
    
    if not tem_acesso:
        # BLOQUEIA já
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Reativar assinatura", callback_data="reativar")
        ]])
        await update.message.reply_text(
            f"❌ Sua assinatura está inativa.\n\n"
            f"Para continuar usando o MercadoBot:",
            parse_mode="HTML",
            reply_markup=kb
        )
        return ConversationHandler.END
    
    # Se tem acesso, mostra menu
    ... (mostra menu)
```

### PASSO 2: Função única de reativação

```python
# Em bot.py
async def enviar_reativacao(update, context, chat_id):
    """ÚNICA função que envia mensagem de reativação"""
    # Gera link
    # Envia 1 mensagem com botões
    # Pronto
```

### PASSO 3: Remover duplicações

- `cmd_reativar_handler()` → usa `enviar_reativacao()`
- `callback_botoes reativar` → usa `enviar_reativacao()`
- Webhook → NÃO envia mais "link expirou" (remove)

### PASSO 4: Status nunca vira "pendente" após reativar

```python
# Em cmd_reativar_handler
# NUNCA fazer: await atualizar_usuario(chat_id, status="pendente")
# Deixar status como está (expirado, cancelado, etc)
```

### PASSO 5: Webhook - remover spam de "link expirou"

```python
# Em webhook_server.py - evento CHECKOUT_EXPIRED
# Remover a linha que envia mensagem de "link expirou"
# Deixar apenas log
```

---

## FLUXO FINAL ESPERADO

```
Usuário com assinatura_fim = 2026-07-03 (ontem)

/start
  → usuario_tem_acesso() retorna False
  → Mostra: "Assinatura inativa, Reativar"
  → Clica em "Reativar assinatura"
  
→ Uma ÚNICA mensagem: "🔄 Reativar MercadoBot"
  → Clica em "💳 Reativar assinatura"
  → Vai para link Asaas
  → Paga
  → Webhook confirma SUBSCRIPTION_PAYMENT
  → Status vira "ativo", assinatura_fim atualiza
  → Próxima vez que abrir, /start mostra menu
  
Sem mais:
  ❌ "link expirou" repetido
  ❌ Status pendente incoerente
  ❌ Múltiplas mensagens de reativação
  ❌ Menu aberto para expirado
```

---

## CHECKLIST DE IMPLEMENTAÇÃO

- [ ] PASSO 1: `/start` bloqueia se `usuario_tem_acesso() = False`
- [ ] PASSO 2: Criar função única `enviar_reativacao()`
- [ ] PASSO 3: `cmd_reativar_handler()` usa `enviar_reativacao()`
- [ ] PASSO 3b: Callback `reativar` usa `enviar_reativacao()`
- [ ] PASSO 4: Remover `status="pendente"` de reativação
- [ ] PASSO 5: Webhook remove spam de "link expirou"
- [ ] TESTE: /start com expirado
- [ ] TESTE: Clique em reativar (1 mensagem)
- [ ] TESTE: Sem status pendente
- [ ] TESTE: Sem loop de link expirou

---

**FIM DO PLANO**
