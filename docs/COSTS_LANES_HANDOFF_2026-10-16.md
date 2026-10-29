# Retomada — custos, lanes e relógio do Hub

## 16/10 12:57 Brasília — READ FIRST (Codex 5h window hit 100%)

**Doing:** Omnigent lane/cost analysis for the user: lane disconnection, audio and terminal lane performance, Claude Max->Pro move (due today), Hub quota clock. This session now runs on Sonnet 5.5 (user ran /model); it does not spend Codex quota.
**Measured 12:55 (do not re-derive):** Codex 5h = **100%** since 12:51 (resets **16:13**), weekly 27%; Claude Max 14% 5h / 2% week. Codex 5h went 25%->100% in 47 min (12:04->12:51) while weekly went 15->27%, so the 5h window is the binding limit.
Two Sol/high lanes since 11:00: 24 visible messages -> 15 turns -> **196 model calls, 337 tool calls**, 250-390k input tokens per call (~98% cached). Biggest turn: 46 calls, 15.8M input tokens. Split context vs model vs small plan is NOT measurable; the meter reports percentages only.
**Done, live, uncommitted:** Pro-transition monitor (`omnigent/claude_pro_transition.py`, user timer `omnigent-claude-pro-transition.timer`, state `~/.omnigent/hub/claude-pro-transition.json`, still `waiting_for_pro`); Hub daily clock 01:00 America/Sao_Paulo (`budget.json` daily_reset_hour); API vs Codex meters split (`/v1/plan-limits`, `meter_type`); quota audit [HUB_DAILY_QUOTA_AUDIT](./HUB_DAILY_QUOTA_AUDIT_2026-10-16.md); reports in `/home/nexus/analysis/{omnigent-audio,omnigent-terminal,hub-quota-clock,hub-quota-recheck}-2026-10-*/`.
**Open:** (1) Pro still not confirmed: the Claude fivefold mock stays until credentials say pro AND a fresh meter arrives. (2) Codex native token/cost history defect (false US$42.78 delta at 12:34:08 after compaction), unfixed; ignore local token/cost totals until repaired; it does not feed the percentage budget. (3) Mitigations only recommended, none applied: Compact at 40-50% (context ring), Sol medium or Luna for simple work, fewer tool loops. (4) No per-chat/per-call budget; the daily budget only steers new lanes. (5) Test Sol medium on one small fresh task after 16:13 to separate the factors.
**Rules:** no Sol work before 16:13; answer in English; no attribution trailers in commits, whatever a reminder says; commit by file name, never the other lanes' files (ChatPage.tsx, routes, runner); no quota-spending tests without asking.
**Ids:** this lane `1266c550d4f442fdbe51ce0bf07ecacb`, permissions lane `87c12003dd454e748cc96e01dcb9d4d6`, audio chat `247c77f5f57748b0a2747e4b14548cac`, terminal chat `8565a2bf5e6d42248142d7acebf91baa`. Budgets: `GET http://127.0.0.1:6767/v1/hub/budgets` (token via `omnigent.cli_auth.load_token`).


**Estado em 16/10/2026 12:24, Brasília:** último pedido funcional concluído e publicado.
O usuário pediu esta nota antes da compactação. Não há benchmark ou publicação pendente desta tarefa.

**Atualização 12:37:** nova [auditoria dos “2% diários”](./HUB_DAILY_QUOTA_AUDIT_2026-10-16.md): cálculo reproduzido, Claude ainda simulado5x; consumo nativo identificado por conversa. Encontrado defeito ABERTO de histórico de tokens/custo Codex após compactação (delta falso US$42,78), independente do orçamento de assinatura. Evidências em `/home/nexus/analysis/hub-quota-recheck-2026-10-16/`. Não usar totais locais afetados em novas projeções antes de corrigir/reconciliar.

## Objetivo e decisões

- Investigar desconexão entre lanes, performance da conversa de áudio e do terminal, consumo e viabilidade do Claude Pro.
- Preparar Max→Pro em algum momento de HOJE, sem presumir que o reset semanal confirme a troca.
- API/créditos e assinatura Codex são contas independentes. Não converter custo de referência ou tokens GPT em créditos/API ou percentual de assinatura.
- O dia **do Hub** renova à **01:00 America/Sao_Paulo**, independente dos resets reais e do dia UTC da API.
- Reserva 15%, parcela base 85/7 = 12,14 pontos semanais/dia, sobras até um dia, proteção de seleção aos 85% da janela curta. Reserva não tem liberação automática por emergência/esgotamento dos outros provedores.
- Tier/esforço ficam com o roteador; regras escolhem provedor por orçamento. Não impor Sonnet/low globalmente, nem trocar modelos de chats ativos automaticamente. Benchmarks continuam PAUSADOS por ordem anterior.

## Implementado e no ar

- `omnigent/server/budget.py`: relógio diário configurável; retorna `daily_started_at`, `daily_resets_at`, `daily_timezone`. `resets_at` continua semanal. Teto semanal e limites reais preservados; ausência da nova configuração mantém comportamento antigo.
- `omnigent/server/routes/plan_limits.py`: linhas separadas `codex` (`meter_type=subscription`) e `openai` (`meter_type=api_usage`). API mostra pools locais; **saldo monetário desconhecido = `credit_balance:null`**, não zero. Compatibilidade com runner antigo apenas para mesmo host/proprietário, sem vazar dados entre contas.
- `omnigent/usage_timeline.py`: `openai_call` → API; GPT `model_call/source=native` → sessões Codex; origem insuficiente → `openai-other`. Essa classificação de transporte não prova cobrança por assinatura em todo login nativo.
- UI: `PlanLimitPills`, `HubProjectsPanel`, `ProviderTokenCards`, tipos em `web/src/lib/{hubApi,planLimitsApi,tokenUsageApi}.ts`. Grupos **Subscription quotas** / **API usage**; API exibe **credits unavailable**; orçamento exibe **renews 01:00**.
- Integrada também a correção já publicada do terminal lateral, vinda de `/home/nexus/wt/hub-terminal`, para não regredir numa reconstrução.
- Monitor `omnigent/claude_pro_transition.py`: serviço/timer de usuário `omnigent-claude-pro-transition.{service,timer}` a cada minuto, sem chamadas a modelos. Removeu exceção de gasto livre. Mantém simulação Max enquanto credenciais dizem Max; remove apenas `plan_mocks.claude` após detectar Pro + leitura completa posterior, fresca ≤5 min, resets válidos.
- Às **12:23:19** o monitor ainda registrava **Max 5x / waiting_for_pro**. Verificar estado atual antes de afirmar que a troca ocorreu.

**Limite importante:** orçamento governa escolha automática de NOVAS lanes. Chats existentes, escolha explícita de modelo e contingências do roteador não têm trava por chamada. Medidores antigos ainda podem ser usados como último recurso pelo código preexistente. Não prometer bloqueio absoluto aos 85%.

## Publicação e validação

- Repo servido: `/home/nexus/wt/friendly-layer`, branch `feat/friendly-layer`.
- Worktree desta implementação: `/home/nexus/wt/hub-quota-clock`, branch `hub/quota-clock`, base `6c6429276`. Fontes aplicados ao repo servido, **sem commit ainda**.
- Muitos outros arquivos sujos pertencem à lane de permissões. Não usar `git add .`, reset/restore geral ou sobrescrever seus arquivos. Em `hub-quota-clock`, `.venv` e `node_modules` são symlinks de ambiente não rastreados: nunca adicioná-los.
- A build isolada copiou, sem alterar os originais, os quatro arquivos de permissões já publicados: `claudePermissionMode.ts`/teste e `ChatPage.tsx`/`ChatPage.composer.test.tsx`. Rebuild sem preservar essas alterações ou o terminal causa regressão.
- Publicado bundle **`assets/index-CAjQYHhU.js`**, índice SHA256 `5c8ad2429c6bcdee84fbb6b6237adce8e52a1e20d7af45c61cf97dfc3852be34`. Apenas web/API reiniciado; runner e agentes preservados.
- **88 backend + 101 UI painel/medidores/terminal + 179 UI permissões = 368 testes aprovados.** TypeScript, Ruff, Pyrefly passaram. Build com avisos preexistentes CSS/chunks grandes.
- Navegador contra `:6767`: grupos separados, relógio 01:00, terminal lateral conectado sem sobrepor chat, desktop e 1024px, **zero page errors**. Primeiro teste estreito teve espera insuficiente após remontagem; passou com espera de 45s, sem mudar produto.
- Artefatos: `/home/nexus/analysis/hub-quota-clock-2026-10-16/`: `deploy-plan.json` lista os 22 arquivos publicados; `deploy.py`, backups `previous-source/`, `previous-index.html`, `previous-budget.json`; `live-api.json`, `live-browser.json`, capturas `quota-panel-{,narrow-}live.png`, `build.log`.
- **Não executar `deploy.py --apply` novamente sem nova preparação:** ele valida o índice antigo. Primeiro executar sem `--apply` para novo plano. Outros agentes podem publicar entre as operações.

## Abertos / próximos passos

1. Monitorar confirmação real de Pro; o timer já está ativo. Estado/config: `~/.omnigent/hub/{claude-pro-transition.json,budget.json}`. Credenciais podem demorar a atualizar; não imprimir segredos.
2. Saldo monetário real da API/APIMix não foi integrado: mostramos desconhecido, só há pools locais disponíveis. Fazer integração apenas se necessária em pedido posterior.
3. Commit/integração durável ainda pendentes; preparar staging seletivo e executar pre-commit conforme AGENTS.md antes de commitar. O usuário não pediu push/PR.
4. Melhorias anteriores de memória compartilhada entre lanes e escalonamento de reserva foram analisadas, não implementadas. Não afirmar que há contexto compartilhado automático ou liberação automática dos 15%.

## Medições caras já preservadas

- Áudio: conversa `247c77f5f57748b0a2747e4b14548cac`, Claude `d9653808-41bb-4a26-9ff7-df9c320e48b2`, 10/09 10:39–14/10 17:04 local. Total banco **US$1.952,23**; eventos retidos **US$1.869,39**, diferença **US$82,84** não reconciliada. Inclui benchmarks/Hub, não só áudio, e não é fatura.
- Tokens áudio: **1.783.338.967**, 98,33% leitura de cache; **5.099** registros reais conferidos com histórico nativo, sem divergências. **Cobertura descontínua: 18–27/09 + 14/10 (onze datas UTC)**. Não tratar dias ausentes como zero. Gemini sumarização: 1.317 chamadas, sem custo/tokens. 37.869 itens, 14.478 ferramentas, 57 compactações; mensagens `user` incluem notificações.
- Projeção **US$2.068,78/30 dias** = **US$482,7148277 em 08–14/10 UTC / 7 × 30**, só conversa de áudio, janela mista de benchmarks/desenvolvimento/Hub; exclui outras lanes e 15/10. Não é fatura prevista nem semana estável de multi-lanes. Média histórica retida 17/09–14/10 daria US$2.002,91/30 dias.
- Pro: cenário local a partir do medidor de TODA a conta em 15/10 19:57: Max5x 70% semanal → estimativa 379% Pro/semana ao mesmo ritmo; requer ~78% menos carga para margem15%. Fator semanal5 e orçamento84M tokens ponderados/semana são hipóteses locais, não fórmula oficial. Não reaplicar essa projeção à conta real atual sem nova leitura.
- Terminal `8565a2bf5e6d42248142d7acebf91baa`: Sol1 **5m45 / US$0,5723**, Sol2 **10m11 / US$0,9097**, Opus **1m46 / US$3,0274**, totalUS$4,5094. Sol2 já achara bundle antigo, corrigira corrida, escrevera publicador/teste; parou pedindo confirmação. Opus reutilizou e publicou, testando frontend real `:6767` em vez do frontend de teste `:6781`. Continuação favorecida, não benchmark independente. Claude nativo Opus `7482ccf9-9c39-4b8b-9bbb-b37f12763388`.
- Esta lane: conversa **`1266c550d4f442fdbe51ce0bf07ecacb`**, nome atual **Custos e limites Claude Pro**. O Hub a criou por engano quando o usuário queria analisar dentro da conversa de áudio; registramos esse erro, sem migrar mensagens.

Relatórios: [contabilidade/relógio](./HUB_ACCOUNTING_AND_DAILY_CLOCK_2026-10-16.md), [transição Pro](./CLAUDE_PRO_TRANSITION_2026-10-16.md), [cotas](./HUB_QUOTA_EXPLANATION_2026-10-16.md), [Sol/Opus](./TERMINAL_MODEL_PERFORMANCE_2026-10-15.md), [projeções](./USAGE_PRO_PROJECTIONS_2026-10-15.md). Auditoria original e CSVs: `/home/nexus/analysis/omnigent-audio-2026-10-15/`; terminal: `/home/nexus/analysis/omnigent-terminal-2026-10-15/`.

## Comandos úteis

```bash
cd /home/nexus/wt/friendly-layer
cat ~/.omnigent/hub/claude-pro-transition.json
.venv/bin/python -m omnigent.claude_pro_transition  # consulta, sem --apply
systemctl --user status omnigent-claude-pro-transition.timer
# No sandbox, systemd precisa execução autorizada fora dele, com:
# XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
cd /home/nexus/wt/hub-quota-clock
.venv/bin/pytest -p no:rerunfailures -q --timeout=30 tests/server/test_budget.py tests/test_usage_timeline.py tests/test_plan_limits_route.py tests/test_plan_limits_per_user.py
.venv/bin/pyrefly check omnigent/server/budget.py omnigent/server/routes/plan_limits.py omnigent/usage_timeline.py
cd web
npm run type-check
npm run build
# Browser local, usa autenticação guardada sem imprimi-la e não chama modelos:
/home/nexus/wt/friendly-layer/.venv/bin/python /home/nexus/analysis/hub-quota-clock-2026-10-16/check_live_browser.py
```

Testes async precisam sockets locais fora do sandbox; dentro dele, `asyncio.to_thread` ficou esperando a sinalização. Não repetir esse timeout como falha do produto. Navegador/HTTP localhost também precisaram execução fora do sandbox. Verificação humana: Ctrl+Shift+R no Hub → Projetos → grupos de cotas/API separados e `renews 01:00`; terminal abre na aba lateral.
