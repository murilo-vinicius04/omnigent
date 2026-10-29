A conversa “Terminal no painel lateral” mostra uma diferença importante entre implementar uma correção e entregá-la na interface em uso. Sol implementou a solução e, na segunda tentativa, encontrou o bundle desatualizado. Opus concluiu a publicação e verificou o resultado no servidor real em aproximadamente dois minutos. O caso favorece Opus na conclusão da entrega, mas não é uma comparação independente da capacidade dos modelos de escrever a correção.

Sessão: `8565a2bf5e6d42248142d7acebf91baa`. Corte da coleta: **15/10/2026, 19:55, America/Sao_Paulo**. Os tempos abaixo vão da entrada do pedido na conversa até a resposta final; incluem ferramentas e testes, e não representam somente tempo de inferência. O intervalo ocioso entre os modelos fica fora desses tempos.

| Tentativa | Horário local | Tempo | Custo registrado | Resultado para a interface em uso |
|---|---|---:|---:|---|
| Sol, primeira | 17:22:40–17:28:25 | 5 min 45 s | US$ 0,57 | Alteração na worktree e 179 testes; interface em uso permaneceu antiga. |
| Sol, segunda | 17:30:00–17:40:11 | 10 min 11 s | US$ 0,91 | Diagnóstico do bundle antigo, correção da corrida de carregamento, navegador de teste e 203 testes; publicação ficou pendente. |
| Opus, continuação | 19:52:13–19:53:59 | 1 min 46 s | US$ 3,03 | Publicou os assets preparados pelo Sol e verificou o terminal em `:6767`. |

O total registrado é **US$ 4,51**: Sol US$ 1,48 e Opus US$ 3,03. São custos locais de referência, não uma fatura. Os eventos de consumo conferidos somam esses mesmos valores. O Opus foi mais rápido para concluir esta etapa, mas custou aproximadamente duas vezes o conjunto das duas tentativas anteriores. A comparação financeira inclui contextos, tarefas e estágios diferentes.

**Primeira tentativa do Sol: a implementação ficou separada da entrega.** O pedido era abrir o terminal como aba do painel lateral existente, mantendo o chat visível. Sol criou `/home/nexus/wt/hub-terminal`, branch `hub/terminal-no-painel-lateral`, e alterou a ligação entre o Hub, a sessão de shell e as abas. Os testes de componentes, TypeScript e lint dos arquivos alterados passaram. O lint global apontou problemas preexistentes.

A resposta final informou que estava implementado na worktree e pediu ao usuário que executasse `npm run dev` nela. Não foi uma afirmação explícita de implantação em produção. Mesmo assim, deixou incompleto o resultado esperado: o usuário continuava na interface do Hub já aberta. Faltou identificar desde o início o diretório efetivamente servido e incluir a publicação/verificação desse frontend no critério de conclusão. Os testes da worktree não demonstravam que o usuário receberia aquela versão.

**Segunda tentativa do Sol: o diagnóstico principal já estava feito.** Às 17:30 o usuário enviou uma imagem e disse que continuava bugado. Sol reconheceu o painel antigo na imagem e, às **17:31:38**, registrou: “Confirmei que o Hub em execução ainda usa o bundle antigo; a alteração anterior ficou apenas na worktree.” Também identificou a corrida que apagava a seleção enquanto a lista de shells carregava e implementou a correção.

O navegador dessa tentativa carregava o frontend de teste em **`:6781`**, conectado ao backend real em **`:6767`**. Portanto houve teste de integração relevante, incluindo digitação, retorno de comando, troca e criação/fechamento de abas. O que ainda faltava era testar o frontend efetivamente entregue por `:6767` após publicá-lo.

Sol preparou `publish_frontend.py` e um plano concreto de publicação: adicionar 58 assets, preservar os antigos, guardar o índice anterior e trocar o `index.html` sem reiniciar o servidor. O comando executado sem `--apply` registrou **`applied: false`**. A partir das 17:37:52, o agente pediu confirmação para publicar na `friendly-layer`, interpretando a proteção da worktree de outra lane como motivo para essa confirmação.

No trecho auditado, a orientação inicial era deixar os arquivos das outras lanes em paz e trabalhar em uma worktree própria. Não há ali uma exigência explícita de obter confirmação antes de publicar apenas assets gerados. Essa ambiguidade de escopo não foi resolvida até o fim da tentativa. A falha de conclusão envolve interpretação das instruções, autonomia e comunicação do estado da entrega; não pode ser descrita como incapacidade de descobrir o problema.

**O que Opus fez diferente.** O Hub mudou a conversa para Opus 5.5 às 19:50:43; após uma alteração passageira, o esforço ficou em high. Sol também havia iniciado em high. Na nova solicitação, às 19:52:13, Opus:

1. Consultou a memória do projeto sobre o servidor executar na `friendly-layer`.
2. Comparou diretamente o bundle servido, `index-CC1rN8nV.js` das 16:28, com o preparado na worktree, `index-D2VXJv3_.js` das 17:33.
3. Conferiu logs: havia criação do terminal e conexões aceitas, sem evidência que justificasse reescrever o backend.
4. Executou **o publicador escrito pelo Sol**, agora com `--apply`, e confirmou por HTTP que `:6767` entregava o índice novo.
5. Mudou a base **do teste escrito pelo Sol** de `:6781` para `:6767` e o executou contra a interface em uso.

O teste retornou `HUB_TERMINAL_TAB_OK`, confirmou o terminal conectado, verificou que chat e painel não se sobrepunham e registrou `errors: []`. Opus informou o resultado e orientou recarregar a página. Nesta continuação não reescreveu os componentes do produto: aproveitou a implementação, a correção da corrida, o build, o publicador e o teste já disponíveis.

**Como registrar o caso para avaliação futura.** O relato do usuário — duas tentativas sem resolução visível, depois resolução rápida com Opus — é compatível com o histórico. A atribuição técnica deve preservar o trabalho de cada etapa:

- Sol 1: implementação local útil; conclusão insuficiente para a interface em uso.
- Sol 2: diagnóstico correto e correção funcional preparada; entrega interrompida na publicação.
- Opus: boa decisão operacional, reutilização do trabalho existente e validação após a publicação.

O desenho é uma continuação com passagem de contexto, não três execuções independentes. Opus recebeu código, evidências, script de publicação, testes e memória prévia. Este caso não demonstra que escreveria a solução inteira em 1 min 46 s, nem sustenta um ranking geral Sol versus Opus. Ele sustenta avaliar a capacidade de verificar a versão servida e concluir o fluxo real como parte do sucesso da tarefa. “Testes passaram” e “usuário recebeu a correção” precisam ser resultados distintos na avaliação.

**Consumo em relação ao Claude Pro.** Sol consumiu a conta Codex, não a cota Claude Pro. O ledger nativo da continuação Opus tem nove registros reais, com 18 tokens de entrada sem cache, 297.713 de escrita de cache, 2.514.791 de leitura e 7.132 de saída. Pelo estimador local do Omnigent, isso equivale a cerca de **5,71% de uma janela Pro de cinco horas** ou **0,785% de uma semana Pro**. São estimativas pela calibração local, não percentuais medidos no Pro. Projetar vinte continuações iguais daria aproximadamente 15,7% da semana Pro; isso não estima vinte implementações completas começando do zero.

**Risco que permaneceu ao fim da resolução.** As alterações estavam preparadas na branch da tarefa, sem integração na `feat/friendly-layer`. Opus alertou que reconstruir o frontend servido a partir da branch antiga poderia reintroduzir o comportamento. A publicação resolveu a interface naquele momento; a integração durável ainda estava pendente. Esta auditoria não realizou merge, publicação ou novos testes que criem shells.

Evidências: posições 88, 115, 163, 178, 182–183, 202–204, 211–218 e 221 da conversa. O [registro estruturado](</home/nexus/analysis/omnigent-terminal-2026-10-15/metrics.json>) e os [trechos de evidência](</home/nexus/analysis/omnigent-terminal-2026-10-15/evidence.json>) preservam as fases e permitem conferir as atribuições. Para reproduzir a coleta, execute `python3 /home/nexus/analysis/omnigent-terminal-2026-10-15/audit.py`.

Para conferir o comportamento descrito, abra o Hub, recarregue a página e clique no terminal: ele deve aparecer na aba lateral, com o chat visível. Digite `printf 'teste\n'`, alterne para Projetos e volte ao terminal. Essa verificação descreve o resultado esperado; não faz parte da coleta somente leitura desta auditoria.
