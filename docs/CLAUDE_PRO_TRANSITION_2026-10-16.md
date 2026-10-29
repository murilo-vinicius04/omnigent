O Omnigent está preparado para a mudança de Claude Max 5x para Pro em algum momento de 16/10. Às 11:35, horário de São Paulo, os metadados locais ainda indicavam Max 5x. O reset semanal das 09:00 ocorreu, mas não foi tratado como confirmação da troca de assinatura.

**Configuração aplicada no Hub**

- Removida a exceção `spend_freely_until.claude` de `~/.omnigent/hub/budget.json`. As configurações de Codex e Gemini foram preservadas.
- Mantidos 15% de reserva semanal, orçamento diário de `(100 − 15) / 7 = 12,14%` e proteção de seleção automática quando a janela de cinco horas chega a 85%. Sobras carregam no máximo um dia de orçamento.
- Mantida a simulação conservadora de Pro enquanto a conta informa Max. No novo ciclo semanal, o simulador usa cinco vezes o medidor Max, hipótese local já documentada; isso não é uma cota oficial de tokens publicada pela Anthropic.
- Ativado `omnigent-claude-pro-transition.timer`, a cada minuto. Ele lê somente os nomes da assinatura nas credenciais e o cache de cotas. Não chama modelos, não envia mensagens, não muda a assinatura e não reinicia o servidor.
- Ao detectar `subscriptionType=pro`, o monitor aguarda uma leitura completa das duas janelas, coletada após essa detecção, com no máximo cinco minutos de idade e resets ainda válidos. Só então remove `plan_mocks.claude`. A partir daí o orçamento usa o medidor Pro real, sem multiplicá-lo novamente por cinco.
- Toda alteração de orçamento cria um backup e preserva configurações de outros provedores. Metadados ausentes e leituras antigas deixam a transição pendente, em vez de serem interpretados como uma cota nova disponível.

O estado está em `~/.omnigent/hub/claude-pro-transition.json`. `waiting_for_pro` significa que os metadados ainda dizem Max; `pro_waiting_for_fresh_meter` significa que falta uma leitura nova; `pro_active` confirma que a simulação foi retirada. Credenciais locais podem demorar a refletir a assinatura: o monitor não consegue confirmar uma mudança que o login ainda não informou. O Hub atualiza os medidores ao avaliar orçamentos; abrir os anéis de uso também atualiza essa leitura.

**Verificação no serviço que está no ar**

O endpoint autenticado `GET http://127.0.0.1:6767/v1/hub/budgets` respondeu 200 às 11:35 com Claude em 5% semanal e 25% de cinco horas na simulação, orçamento diário de 12,14%, gasto de 5% hoje e saldo de 7,14%. O medidor bruto era 1% semanal e 5% de cinco horas no Max. A exceção de gasto livre estava ausente. O servidor permaneceu no mesmo processo, iniciado às 11:19:57; não houve reinício para aplicar esta preparação.

**Limite de aplicação e operação das lanes**

O orçamento orienta a seleção automática de provedor para novas lanes. Envios para conversas existentes continuam no modelo daquela conversa, com julgamento de tier registrado em modo de observação. Um modelo explicitamente escolhido pelo usuário e a contingência de roteador indisponível também podem passar fora da seleção automática por orçamento. O limite de 85% não cancela chamadas em andamento, não é uma trava por ferramenta e não garante que uma tarefa longa não ultrapasse o orçamento diário. O bloqueio efetivo da assinatura continua sendo da Anthropic.

Por isso, durante a primeira semana Pro:

1. Manter tier e esforço como decisões do modelo de roteamento; regras fixas escolhem provedor por orçamento. Não impor Sonnet/low a todos os pedidos nem alternar modelos no meio de conversas longas automaticamente.
2. Usar a capacidade disponível de Codex e Gemini para novas tarefas independentes quando o orçamento e o tier permitirem. No instante da verificação, Codex já estava sem orçamento diário; Gemini tinha saldo. Não assumir capacidade infinita nesses provedores.
3. Nas conversas Claude existentes, concluir trabalho em andamento e registrar uma retomada curta antes de transferir trabalho independente. Não copiar históricos completos indiscriminadamente: a releitura pode gastar uma parte relevante da janela.
4. Manter benchmarks pausados conforme a instrução anterior. Priorizar tarefas reais, medir custo e resultado por execução e reservar Opus para tarefas que o roteador ou o usuário julgar que precisam dele.
5. Conferir os dois medidores do plano real após a confirmação de Pro. O orçamento de aproximadamente 12% é da semana, não da janela de cinco horas. Créditos extras/API não foram ativados.
6. Calibrar as estimativas locais de tokens contra o medidor Pro. O ledger histórico tinha lacunas; médias antigas não servem como garantia de quantas tarefas caberão. Registrar explicitamente lacunas e consumo de trabalhadores.

Os limites Pro oficiais incluem janelas de cinco horas e semanais, e o consumo depende do contexto e do modelo. O horário de reset semanal pertence à conta; não confirma o instante de mudança da assinatura. Fontes: [Pro](https://support.claude.com/en/articles/8325606-what-is-the-pro-plan), [Max](https://support.claude.com/en/articles/11049741-what-is-the-max-plan). As estimativas históricas e suas limitações estão em [USAGE_PRO_PROJECTIONS_2026-10-15.md](./USAGE_PRO_PROJECTIONS_2026-10-15.md).

**Como conferir e reproduzir**

No Hub, abra os anéis de uso e o painel de orçamento. Enquanto Max estiver ativo, o orçamento Claude é a simulação de Pro e pode mostrar números diferentes dos anéis brutos. Depois da detecção de Pro e de uma nova leitura, o orçamento deve usar os valores reais sem o fator cinco.

Consulta local sem alterar o orçamento:

```bash
cd /home/nexus/wt/friendly-layer
.venv/bin/python -m omnigent.claude_pro_transition
cat /home/nexus/.omnigent/hub/claude-pro-transition.json
systemctl --user status omnigent-claude-pro-transition.timer
```

Foram aprovados 20 testes de transição e orçamento. Eles verificam preservação das demais configurações, retirada da exceção, espera por leitura nova, rejeição de leitura futura ou reset expirado, remoção da multiplicação e ausência de dados de autenticação no estado. Lint e formatação passaram nos arquivos alterados.

```bash
.venv/bin/pytest -p no:rerunfailures -q tests/test_claude_pro_transition.py tests/server/test_budget.py
```

O plugin de repetição foi desabilitado nesse comando porque abre um socket não permitido pelo sandbox. O monitor foi instalado como serviço de usuário e sua primeira execução terminou com `Result=success`, `ExecMainStatus=0`. A preparação não depende de publicar um novo bundle web. Os arquivos novos do controlador e deste documento ainda não foram commitados.

Para interromper somente o monitor: `systemctl --user disable --now omnigent-claude-pro-transition.timer`. Isso não desfaz o orçamento já aplicado. O backup original da preparação é `~/.omnigent/hub/budget-before-pro-20261016T143454156301Z.json`; não o restaure após a mudança para Pro, pois contém a antiga simulação Max e a exceção removida.
