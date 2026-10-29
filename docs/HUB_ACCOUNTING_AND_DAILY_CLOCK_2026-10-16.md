O Hub separa agora a cota da assinatura Codex do uso da API. O orçamento diário renova à **01:00 de Brasília**, independentemente dos relógios da API e das assinaturas. A atualização foi publicada em 16/10/2026 no servidor local `:6767`.

**Medidores independentes**

- Codex lê suas janelas da assinatura ChatGPT. O orçamento do roteador usa esses percentuais, não o ledger de tokens ou o saldo de créditos da API.
- `/v1/plan-limits` volta a expor OpenAI API separadamente, com `meter_type=api_usage`. É uma leitura dos pools locais de tokens, com relógio UTC. Não há consulta de saldo monetário upstream: `credit_balance=null`, mostrado como **credits unavailable**. Esse valor desconhecido não é apresentado como cota de assinatura.
- Codex tem `meter_type=subscription` e aparece no grupo **Subscription quotas**. A API aparece no grupo **API usage**.
- O histórico distingue eventos do proxy API (`openai_call`) das sessões Codex (`model_call`, origem `native`, família GPT). Registros GPT sem origem suficiente ficam em **GPT (other routes)**, sem atribuição à API ou ao medidor Codex. Tokens e custos de referência não são convertidos em saldo de créditos ou percentuais da assinatura.
- Um runner local antigo pode omitir a API. Para o proprietário do mesmo host, o servidor obtém a leitura atualizada localmente sem reiniciar o runner. Não combina créditos locais com os dados de um host remoto ou de outro usuário.

**Relógio do Hub**

Configuração aplicada em `~/.omnigent/hub/budget.json`:

```json
{
  "daily_reset_hour": 1,
  "daily_timezone": "America/Sao_Paulo"
}
```

O intervalo diário é de 01:00 até a próxima 01:00 local. A API mantém seu relógio, e a assinatura mantém seus resets reais de cinco horas e semana. A mudança não recupera créditos gastos nem libera uma janela bloqueada pelo provedor.

Permanecem a reserva semanal de 15%, a parcela diária de 12,14 pontos da semana e sobras limitadas a um dia. O excesso anterior pode reduzir a parcela seguinte. Um reset semanal no meio do dia começa uma semana nova, sem herdar o consumo da semana antiga. O teto semanal de 85% permanece mesmo quando a semana cruza intervalos locais parciais.

Instalações sem `daily_reset_hour` preservam a regra anterior. Horas ou fusos inválidos não ativam uma configuração ambígua. A API de orçamento expõe `daily_started_at`, `daily_resets_at` e `daily_timezone`; `resets_at` continua sendo o reset semanal do provedor. O controlador Max→Pro preserva os novos campos.

O painel diferencia **Hub daily budget spent** de um limite real da assinatura. O saldo é rotulado como pontos da semana disponíveis hoje, e a renovação diária aparece no badge. O tooltip distingue o relógio diário e o reset semanal.

**Entrega e verificações**

Passaram 88 testes de backend, incluindo isolamento entre contas, independência do ledger API, passagem por 01:00, troca de semana e respeito às janelas reais. Passaram também 101 testes de painel/medidores/terminal e 179 do seletor de permissões já publicado. TypeScript, lint e checagem Python passaram; a build terminou com avisos preexistentes de CSS e chunks grandes.

A build preservou o terminal lateral da worktree `hub-terminal` e o seletor de permissões já servido em `friendly-layer`. Os fontes do terminal também foram integrados no diretório servido para evitar regressão numa reconstrução. Os quatro arquivos de permissões de outra lane foram apenas copiados para a build isolada; os originais não foram modificados. O publicador verificou conflitos, guardou os fontes e o índice anteriores e preservou assets antigos.

Foi reiniciado somente o servidor web/API. Runner e processos dos agentes não foram reiniciados. A leitura autenticada após a publicação confirmou os medidores separados, apenas Claude/Codex/Gemini no orçamento de assinaturas e próxima virada diária em **17/10/2026 04:00 UTC = 01:00 Brasília**. Codex estava novamente elegível na seleção automática no instante dessa leitura. Os números variam com trabalho em andamento.

Fontes e build: `/home/nexus/wt/hub-quota-clock`, branch `hub/quota-clock`. As alterações estão aplicadas em `friendly-layer`, ainda sem commit. O plano e os backups estão em `/home/nexus/analysis/hub-quota-clock-2026-10-16/`; `live-api.json` registra a conferência do endpoint, e `check_live_browser.py` verifica o bundle servido e a interface sem enviar pedidos a modelos. A conferência do terminal apenas conecta, sem digitar comandos no shell.

**Como conferir**

Recarregue o Hub com `Ctrl+Shift+R`. No painel Projetos, confira **Subscription quotas** e **API usage**. A API deve indicar saldo de créditos indisponível; Codex deve mostrar o percentual da assinatura. Confira **renews 01:00** no orçamento e compare no tooltip com o reset semanal. O terminal deve continuar abrindo como aba lateral junto a Projetos.
