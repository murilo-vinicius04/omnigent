Às 11:43 de 16/10/2026 (São Paulo), o painel confundia facilmente o orçamento diário do Hub com a cota restante na assinatura. A investigação confirmou que os dois números têm significados diferentes.

| Provedor | Semana usada no medidor | Semana restante | Janela de 5h usada | Orçamento diário do Hub |
|---|---:|---:|---:|---|
| Codex | 14% | 86% | 21% | Esgotado: saldo calculado −1,86 ponto da semana |
| Gemini | 3% | 97% | 3% | Restam 9,14 pontos da semana para hoje |

Esses números são os caches recentes dos provedores, não uma conversão dos custos em dólares. A reserva de 15% é um parâmetro geral do orçamento: o valor diário é 85/7 = 12,14 pontos da semana para cada provedor. Codex e Gemini não têm a simulação de plano aplicada ao Claude.

**Como a reserva é acessada hoje**

Não existe uma classificação de emergência nem uma regra “os outros acabaram, libere a reserva do Claude”. `budget.pick` considera provedores com saldo diário e capacidade na janela curta, no tier solicitado. Na abertura automática, `Hub._lane_run` tenta tiers próximos caso nenhum provedor atenda ao tier escolhido. Se nenhum puder receber, não abre a lane e informa que o usuário pode escolher explicitamente um modelo.

Essa escolha explícita de modelo pode passar fora do orçamento diário e consumir a reserva; o Hub acrescenta o motivo do bloqueio à explicação. Envios a conversas existentes também continuam no modelo da conversa e não passam por uma trava de orçamento por chamada. Assim, os 15% são uma reserva do planejamento automático, não uma separação técnica de créditos na Anthropic. Esgotar o orçamento diário pode levar a uma escolha explícita muito antes de consumir os 85% semanais planejados. A indisponibilidade dos outros provedores, sozinha, não libera os 15%.

Há ainda uma exceção preexistente: `budget.pick` admite um medidor antigo como último recurso quando não há um atual elegível. Isso não é uma política de liberação de reserva e não equivale a saldo confirmado. Esta investigação não alterou essa regra.

**Por que o Codex aparece esgotado**

O dia do orçamento acompanha o reset semanal de cada provedor, não a meia-noite. Para Codex, o ciclo diário atual começou em **15/10 às 17:22:43** e termina em **16/10 às 17:22:43**. O reset semanal está em 22/10 às 17:22:43.

O histórico do medidor chegou a 3% semanal às 17:40 de ontem, 8% às 19:04, 10% às 19:57 e 11% às 20:03. Hoje chegou a 12% às 11:16, 13% às 11:20 e 14% às 11:36. Portanto a maior parte do consumo contado neste dia orçamentário aconteceu ontem, e a manhã de hoje não começa com orçamento novo.

Os eventos locais mostram trabalho nas lanes “Terminal no painel lateral”, “Bypass de permissões padrão” e nesta conversa de custos/limites Pro, executadas com Sol. As leituras de cota pertencem à conta e não permitem atribuir exatamente cada ponto percentual a uma dessas lanes. Poucos pedidos humanos podem gerar muitas chamadas de modelo e reutilizações de contexto.

O primeiro medidor após o início desse ciclo já estava em 1%. O algoritmo usa esse 1% como base: calcula 13 pontos gastos depois dela e reduz o permitido hoje para 11,14 pontos. O saldo final é o mesmo que 12,14 − 14 = −1,86. Não se trata de uma segunda cobrança desses 1%.

**Por que o Gemini mostra somente 9%**

O medidor Gemini informa 3% de semana usada. O saldo diário é 12,14 − 3 = 9,14 pontos da semana, aproximadamente 75% da parcela diária inicial. Não são apenas 9% restantes na conta: há 97% da semana no provedor.

O ciclo diário Gemini começou em **15/10 às 20:59:49**. O Hub e seu companion usam Gemini para roteamento e resumos, portanto não é necessário iniciar manualmente uma tarefa longa em Gemini para haver consumo. Desde esse marco há 19 decisões no log de roteamento e 23 retornos de conclusão no histórico do Hub na amostra consultada; essas contagens demonstram atividade, mas não equivalem a 42 chamadas cobradas e não provam a atribuição exata dos 3%.

Também houve uma leitura semanal transitória de 15% às 21:06 de ontem, que recuou a 1% às 21:16. Não foi possível determinar a causa upstream dessa oscilação. A leitura atual está em 3% e o cálculo atual usa o medidor mais recente; não usa aquele pico como consumo permanente.

**Apresentação no painel e como conferir**

`web/src/shell/HubProjectsPanel.tsx::BudgetStrip` exibe “left today” ou “today's budget is spent”. A porcentagem é expressa em pontos da cota semanal, e o saldo real da conta só aparece nos anéis/tooltip. Isso explica a impressão de pouco saldo apesar de pouca atividade na manhã.

No Hub, compare o orçamento com os anéis de uso. Passe o mouse no orçamento para ver semana usada, dia do ciclo e gasto/permitido. Em Codex deve aparecer 14% semanal usado nesta amostra; no Gemini, 3%. Os saldos mudam com novas atividades, inclusive esta investigação. O orçamento diário é recalculado no início do próximo período de cada provedor; não significa reset da assinatura inteira.

Fontes locais: `~/.omnigent/plan-limits-codex.json`, `plan-limits-antigravity.json`, `hub/budget.json`, `usage-history.jsonl`, `hub/morys5/router.jsonl` e `log.jsonl`; implementação em `omnigent/server/budget.py` e `Hub._lane_run`. A investigação leu dados e registrou este esclarecimento, sem mudar a reserva, quotas ou modelos das sessões.
