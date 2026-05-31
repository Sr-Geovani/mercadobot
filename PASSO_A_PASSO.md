# 🤖 MercadoBot — Guia Completo do Zero ao Bot no Ar
### Windows, sem experiência técnica

---

## ⏱️ Tempo estimado: 2–3 horas hoje

---

## ETAPA 1 — Instalar o Python (10 min)

1. Abra o navegador e acesse: **https://www.python.org/downloads/**
2. Clique no botão amarelo **"Download Python 3.12.x"**
3. Execute o instalador baixado
4. ⚠️ IMPORTANTE: na primeira tela do instalador, marque a caixa **"Add Python to PATH"** antes de clicar em Install
5. Clique em **"Install Now"**
6. Aguarde terminar e clique em **"Close"**

**Verificar se funcionou:**
- Pressione `Windows + R`, digite `cmd` e pressione Enter
- No terminal preto que abrir, digite: `python --version`
- Deve aparecer: `Python 3.12.x`
- Se aparecer, está correto ✅

---

## ETAPA 2 — Criar a pasta do projeto (2 min)

1. Abra o **Explorador de Arquivos** (Windows + E)
2. Vá para a pasta **Documentos**
3. Clique com botão direito → **Nova pasta**
4. Nomeie como: `mercadobot`
5. Abra essa pasta

---

## ETAPA 3 — Instalar as bibliotecas (5 min)

1. Dentro da pasta `mercadobot`, clique na barra de endereço do Explorer
2. Digite `cmd` e pressione Enter (abre o terminal já na pasta certa)
3. Cole e execute cada linha abaixo, uma por vez:

```
pip install python-telegram-bot
```
```
pip install anthropic
```
```
pip install pandas openpyxl
```

Aguarde cada uma terminar antes de executar a próxima.

---

## ETAPA 4 — Criar o arquivo do bot (2 min)

1. Copie o arquivo `bot.py` que está junto com este guia
2. Cole dentro da pasta `mercadobot`

---

## ETAPA 5 — Criar seu bot no Telegram (10 min)

1. Abra o Telegram no celular ou computador
2. Na barra de busca, pesquise: **@BotFather**
3. Clique no contato oficial (tem um ✅ azul)
4. Clique em **START** ou envie `/start`
5. Envie o comando: `/newbot`
6. BotFather vai perguntar o **nome** do bot → digite: `MercadoBot`
7. Vai perguntar o **username** → digite algo único como: `meumercadobot` (precisa terminar em "bot")
8. O BotFather vai te enviar uma mensagem com o **TOKEN** — parece com isso:
   `7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
9. **Copie e guarde esse token** — você vai precisar no próximo passo

---

## ETAPA 6 — Criar sua chave da IA (Claude) (10 min)

1. Acesse: **https://console.anthropic.com**
2. Crie uma conta gratuita (ou faça login)
3. No menu lateral clique em **"API Keys"**
4. Clique em **"Create Key"**
5. Dê um nome como `mercadobot`
6. Copie a chave gerada — parece com: `sk-ant-xxxxxxxxxxxxxxxxxxxx`
7. Guarde essa chave

⚠️ Você precisará adicionar créditos (mínimo US$ 5) em **"Billing"** para a IA funcionar.
O custo por uso é muito baixo — US$ 5 dura meses de uso normal.

---

## ETAPA 7 — Configurar as chaves no bot (3 min)

1. Abra o arquivo `bot.py` com o **Bloco de Notas**
   (clique com botão direito no arquivo → Abrir com → Bloco de Notas)
2. Nas primeiras linhas, localize:
   ```
   TELEGRAM_TOKEN = "SEU_TOKEN_AQUI"
   ANTHROPIC_KEY  = "SUA_CHAVE_ANTHROPIC_AQUI"
   ```
3. Substitua `SEU_TOKEN_AQUI` pelo token que o BotFather te deu
4. Substitua `SUA_CHAVE_ANTHROPIC_AQUI` pela chave da Anthropic
5. Salve o arquivo (Ctrl + S)

---

## ETAPA 8 — Rodar o bot (2 min)

1. Volte ao terminal que estava na pasta `mercadobot`
   (se fechou: abra o Explorer, vá na pasta, clique na barra e digite `cmd`)
2. Digite:
   ```
   python bot.py
   ```
3. Deve aparecer: `🤖 MercadoBot rodando...`
4. **Não feche o terminal** — enquanto ele estiver aberto, o bot funciona

---

## ETAPA 9 — Testar o bot (5 min)

1. No Telegram, pesquise pelo username que você escolheu (ex: `@meumercadobot`)
2. Clique em **START**
3. O bot deve responder com o menu de boas-vindas
4. Exporte um relatório do PDV Legal em Excel e envie no chat
5. Use os botões ou comandos como `/briefing`, `/produtos`, `/alertas`

---

## ✅ Bot funcionando! Próximos passos

### Deixar o bot online 24h (sem precisar do seu PC ligado)

Para o bot funcionar mesmo com o computador desligado, você precisa hospedá-lo em nuvem. A opção mais simples e gratuita é o **Railway**:

1. Acesse: **https://railway.app**
2. Crie conta com o GitHub (crie o GitHub primeiro em github.com se não tiver)
3. Siga o tutorial no próximo arquivo: `HOSPEDAGEM.md`

---

## 🆘 Problemas comuns

**"python não é reconhecido como comando"**
→ Reinstale o Python e certifique-se de marcar "Add Python to PATH"

**"ModuleNotFoundError"**
→ Rode novamente o pip install da biblioteca que faltou

**Bot não responde no Telegram**
→ Verifique se o terminal ainda está aberto e rodando
→ Verifique se o token está correto no bot.py

**Erro de API da Anthropic**
→ Verifique se adicionou créditos no console.anthropic.com
→ Confirme se a chave está correta no bot.py

---

## 💰 Custos mensais estimados

| Item | Custo |
|---|---|
| Railway (hospedagem) | Grátis até 500h/mês |
| Anthropic API | ~US$ 2–5/mês (uso normal) |
| **Total** | **~R$ 10–25/mês** |

Com 3 clientes pagando R$ 79/mês = R$ 237/mês de receita.
Margem de ~90% desde o primeiro cliente.

