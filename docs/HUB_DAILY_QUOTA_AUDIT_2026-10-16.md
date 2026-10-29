# Por que Claude e Codex tinham apenas “2% hoje”

Rechecagem de 16/10/2026. Amostra de atividade congelada **01:00–12:34 Brasília**, excluindo o trabalho desta nova investigação. Claude teve reset semanal às **09:00**; sua amostra semanal começa nesse horário. Consulta autenticada adicional às **12:37** confirma o cálculo no servidor servido, não apenas no código da worktree.

## Conta reproduzida

O Hub distribui **85/7 = 12,142857 pontos da semana por dia**, com reserva15% e sobras de até um dia. “2% hoje” significa pontos da **semana** que o planejamento permite gastar hoje; não 2% restantes na assinatura nem 2% da parcela diária.

| Às 12:34 | Codex | Claude |
|---|---:|---:|
| Medidor real da assinatura: semana usada | 22% | 2% do Max5x |
| Medidor real: janela5h usada | 70% | 11% |
| Base do dia do Hub | 11% à01:00 | 0% no reset semanal09:00 |
| Conversão do Hub | nenhuma | simulação Pro ainda ativa: ×5 |
| Gasto atribuído ao dia | 22−11 = 11 pontos | 2×5 = 10 pontos simulados |
| Permitido hoje | 12,142857 + 1,142857 de sobra = 13,285714 | 12,142857 |
| Disponível hoje | **2,29 pontos** | **2,14 pontos simulados** |
| Fração da parcela diária ainda disponível | cerca de17,2% | cerca de17,6% |

Para Codex, a sobra deriva de 12,142857 permitidos no primeiro dia menos os11 usados antes da01:00. O código não desconta novamente esses11 hoje. A troca do relógio não apaga consumo da semana. Claude ainda estava identificado como **Max/default_claude_max_5x**, fase `waiting_for_pro`: o número diário é cenário de planejamento, não limitação real do Max. Quando o plano mudar, o monitor espera leitura completa e fresca posterior antes de retirar somente essa simulação. Cada ponto inteiro no medidor Max vira cinco pontos simulados: o número diário pode cair em degraus grandes.

Às12:37, Codex avançou para23% semanal/73%5h, deixando **1,29 ponto** no Hub e **77% da semana real**. Claude continuou2% semanal real, agora12%5h: saldo diário simulado2,14. Trabalho nesta conversa também consome durante a investigação. Ambos permaneciam elegíveis nessa leitura; a janela curta era inferior à proteção85%. Renovação diária seguinte **17/10 às01:00**; reset real Codex semanal22/10 às17:22:43 e janela curta16/10 às16:13:05. Esses relógios são independentes.

## O que executou chamadas

Contagem de registros nativos por resposta/request, deduplicada; não contagem de mensagens humanas ou de polls `model_call`.

| Conversa | Modelo principal | Chamadas reais antes de12:34 | Tokens totais observados |
|---|---|---:|---:|
| Custos e limites Claude Pro (esta lane) | Sol | **76**, em6 turnos nativos | **23.322.483** |
| Bypass de permissões padrão | Sol | **55**, em4 turnos nativos | **14.518.707** |
| Calibração real EasyHeC | Sonnet5.5 | **71** | **15.482.925** |
| Migração cuRobo para Newton (antes “cuRobo MPC Gazebo”) | Opus5.5 | **135** | **50.912.012** |

Codex tinha ainda **34 respostas de `codex-auto-review`**, identificadas como `guardian_review`:24 vinculadas à lane de permissões e10 a esta lane, totalizando1.309.934 tokens. Elas não aparecem como conversas independentes no Hub. Confirmamos execução interna, **não** a cobrança dessas respostas na mesma cota: não há atribuição de percentual por request no medidor.

As131 respostas principais Codex +34 revisões somam **165 registros**,39.151.124 tokens. Cerca de98% da entrada das lanes principais é cache. Mediana de entrada por resposta:327.283,5 tokens nesta lane e252.795 em permissões. Contexto grande repetido em muitas chamadas explica trabalho substancial apesar de poucas mensagens humanas; tokens de cache também são uso, mas não se pode converter esses totais diretamente em pontos da assinatura.

Claude: **206 requests** únicos após reset09:00, conferidos também nos dois históricos nativos, mesmo número de registros.65.483.064 tokens de cache lidos,209.634 de saída,701.765 de escrita de cache e474 de entrada nova. O consumo da conta abrange essas outras tarefas de robótica; não apenas Omnigent/áudio. Históricos locais registraram US$3,2941 EasyHeC eUS$16,6588 cuRobo de custo de referência; não são cobranças adicionais da assinatura nem base do orçamento.

Codex começou hoje com11% semanal. Leituras locais sucessivas:11:16→12%;11:20→13%;11:36→14%;11:47→15%;12:10→16%;12:15→19%;12:19→21%;12:24→22%. Claude:reset09:00→0%;11:36→1%;12:04→2%. A granularidade e o atraso do medidor impedem distribuir exatamente cada ponto entre lanes. Não inventar essa atribuição a partir de tokens ou dólares.

## Discrepância real encontrada no histórico

A aritmética diária acima reproduz os medidores e a configuração. **Há outro defeito no registro local de tokens/custo após compactação/reinício do contexto**, identificado durante esta investigação:

- Às12:34:08, request nativo de24.341 tokens virou delta Hub de22.515.093 tokens, com crescimento de custo local **US$42,7832308**.
- Às12:34:14, request nativo de35.194 tokens, incluindo22.784 de cache, virou delta Hub de22.513.787 tokens, incluindo22.513.536 de cache.
- Antes disso,12:34:02 já tinha delta incompatível. O `/v1/plan-limits` às12:37 exibia87,17M tokens Codex, apoiado nesse histórico afetado. Não usar esse total ou seu custo para faturamento/projeção.
- Caminho a investigar/corrigir: `codex_native_forwarder.py::_session_usage_data_from_params` e `server/routes/_sessions/orchestration.py::_persist_native_cumulative_usage`: snapshots cumulativos, separação de cache e rebase. O ponto preciso de origem do snapshot inconsistente ainda não foi isolado; não afirmar causa raiz encerrada.
- **Esse defeito não explica os2% diários:** `budget.py` usa percentual semanal do provedor e histórico de percentuais, não soma tokens/custos. A leitura nativa Codex confirma22% semanal no mesmo intervalo. Nenhuma evidência de débito da API entrando nessa conta.

Nenhuma quota, reserva, modelo, ledger ou custo persistido foi alterado nesta rechecagem. O defeito do histórico permanece aberto; não houve correção nem reparo retroativo. Preservar evidências antes de qualquer reparo.

## Evidências e reprodução

Artefatos `/home/nexus/analysis/hub-quota-recheck-2026-10-16/`: `audit.py`, `audit.json`, `history-anomaly.json`, `live-api.json`. O script lê SQLite em modo somente leitura, ledger Claude e rollouts Codex; não chama modelos. Executar da worktree servida com `.venv/bin/python /home/nexus/analysis/hub-quota-recheck-2026-10-16/audit.py`. Janela de atividade fica congelada; medidores “at collection” variam.

IDs desta lane e permissões e sessões nativas constam em `audit.json`; fontes: `~/.omnigent/{usage-history.jsonl,claude-token-ledger.jsonl,plan-limits-codex.json,plan-limits-claude.json,chat.db}`, `codex-native/**/sessions/2026/10/16/`, `claude-history/-home-nexus/`. Configuração `~/.omnigent/hub/budget.json`; monitor `claude-pro-transition.json`. Conferência humana: no Hub, comparar tooltip do saldo diário com anéis da assinatura; Claude real2% usado não deve ser interpretado como2% restante. A virada01:00 continua configurada.

## Complemento: modelo, contexto e mitigação

Pergunta seguinte: por que poucas interações queimam tanto? Rollouts e `conversations.session_overrides` confirmam **gpt-6.1-sol / high nas duas lanes Codex**. Os76 requests desta lane vieram de6 turnos nativos, e55 requests de permissões vieram de4: um pedido humano causa vários ciclos de leitura/ferramenta/teste/resposta. Contextos medianos327k/253k e cerca de98% da entrada em cache são trabalho repetido; cache não implica uso gratuito. Não temos decomposição oficial dos11 pontos entre contexto, esforço e modelo, nem multiplicador de cota para `high`.

Documentação oficial consultada16/10: [limites e consumo](https://learn.chatgpt.com/docs/pricing) informa que modelo, tamanho/complexidade/contexto, raciocínio, ferramentas e cache afetam uso. Preço de API não deve ser convertido diretamente em percentual de assinatura. Janelas5h e semanal têm bases diferentes: seus percentuais não se somam; o incremento de18%5h mencionado pelo usuário não foi reconciliado a uma janela exata nesta etapa.

Opções existentes, **recomendações ainda não aplicadas**:

- **Mesma lane, contexto menor:** clicar no anel de contexto → `Compact at`. Padrão atual60%, sem override nas duas lanes; testar40–50% para tarefas com muitas chamadas, preservando notas. O gatilho roda ao fim do turno e pode ultrapassar o limiar durante uma execução longa. Compactação custa processamento e pode reconstruir cache; não prometer economia linear nem compactar a cada mensagem. Fontes `web/src/components/ContextRing.tsx`, `omnigent/server/auto_compact.py` e `_run_auto_compaction`.
- **Modelo/esforço por tarefa:** seletor da lane e `Hub.set_model` permitem configuração. Para consulta simples, experimentar Sol medium ou Luna quando adequado; manter capacidade maior para diagnóstico/arquitetura difícil. Não trocar globalmente nem modificar chats ativos sem pedido. Modelo mais simples que exige várias tentativas pode perder a economia.
- **Menos chamadas e material:** consultas de ledger com script e resumo único, buscas/saídas limitadas, testes pertinentes sem repetições sem justificativa, reutilização dos relatórios existentes. Medir resultado por tarefa concluída, não mensagens humanas.
- **Memória em arquivo:** compactar mantendo a lane; em mudança real de escopo, usar breve handoff com arquivos/decisões em vez de transportar toda conversa. Não abrir lanes automaticamente só para responder dúvidas.
- **Melhoria ainda ausente:** orçamento por execução/chamada para chats já ativos. O orçamento diário atual decide novas lanes, sem interromper as existentes; ampliar parcela/reserva não reduz consumo upstream. Corrigir o defeito do histórico antes de medir economia com seus tokens/custos.

Não há evidência nesta etapa de Fast/Ultrafast ativo; não atribuir o gasto a esses modos. Nenhuma configuração foi alterada neste complemento.
