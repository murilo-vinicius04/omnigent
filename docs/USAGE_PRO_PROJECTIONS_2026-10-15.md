O valor de US$ 1.952,23 pertence à conversa longa “Corrigir áudio omnigent”, acumulado entre **10/09 e 14/10/2026**. Não é consumo de uma semana nem da intervenção no terminal. A projeção para o Claude Pro precisa separar esse custo registrado, os tokens preservados e os medidores da conta.

Este documento usa o medidor de **15/10/2026 às 19:57:04, America/Sao_Paulo**, e a configuração de simulação disponível nessa coleta. Os números de Pro abaixo são cenários do estimador local, não uma medição de uso de uma conta Pro.

| Dado | Período exato, horário de São Paulo | Cobertura |
|---|---|---|
| Conversa de áudio: US$ 1.952,23 acumulados | 10/09/2026 10:39:17–14/10/2026 17:04:22 | Total persistido; o primeiro instante é a criação da conversa, não necessariamente o primeiro evento faturável. |
| Eventos de custo preservados dessa conversa: US$ 1.869,39 | 17/09/2026 12:40:35–14/10/2026 17:04:22 | Visão parcial do mesmo total; diferença não reconciliada de US$ 82,84. |
| Ledger de tokens dessa conversa: 1.783.338.967 | Primeiro registro 18/09/2026 12:27:56; último 14/10/2026 17:02:36 | Descontínuo: registros em 18–27/09 e em 14/10, onze datas UTC. Não cobre todos os dias do intervalo. |
| Terminal: US$ 4,51 registrados, Sol + Opus | 15/10/2026 17:22:40–19:53:59 | Três tentativas, com intervalo ocioso entre a segunda e a terceira. |
| Conta Claude: 70% da semana Max 5x | Ciclo com reset em 09/10/2026 09:00, medido em 15/10/2026 19:57:04 | Todos os projetos e superfícies que compartilham a conta; não apenas áudio. |

**Correção da interpretação anterior dos tokens:** 1,783 bilhão continua correto para os registros encontrados, mas o primeiro e o último dia não definem cobertura contínua. Há atividade de custo em quinze datas sem registros correspondentes no ledger, incluindo 17/09 e datas entre 29/09 e 13/10. Não se deve dividir o total de tokens por 27 dias e tratar o resultado como consumo médio completo: isso esconderia trabalho ocorrido nas lacunas.

**Como o Pro limita o uso.** A documentação oficial descreve uma janela de uso de cinco horas e um limite semanal, com limites adicionais possíveis. O consumo depende do modelo, esforço e conteúdo; as diferentes superfícies Claude compartilham a cota. O plano Pro mensal tem preço de referência de US$ 20 nos EUA, com variações regionais. Esse preço não é uma conversão entre os dólares mostrados pelo agente e a cota da assinatura. [Plano Pro](https://support.claude.com/en/articles/8325606-what-is-the-pro-plan), [limites de uso](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work).

O plano identificado localmente é **Max 5x** (`default_claude_max_5x`). A documentação garante cinco vezes a capacidade Pro **por sessão**; também descreve um limite semanal, mas não garante nessa descrição que a razão semanal seja exatamente cinco. A projeção semanal abaixo usa a hipótese já adotada pelo Omnigent e precisa ser calibrada após a migração para Pro. [Plano Max](https://support.claude.com/en/articles/11049741-what-is-the-max-plan).

O [estimador local](</home/nexus/wt/friendly-layer/omnigent/pro_equivalent.py>) usa:

`tokens ponderados = entrada + 5 × saída + 0,1 × leitura de cache + 1,25 × escrita de cache`

Sua calibração de referência é **11,55 milhões ponderados por cinco horas** e **84 milhões por semana Pro**. O próprio código trata os limites como estimativas e informa que o ajuste semanal foi extrapolado do aumento medido da janela de cinco horas. Não é uma fórmula oficial da Anthropic.

**Projeção da conta inteira.** Na leitura das 19:57:04, a conta tinha usado 70% da semana Max 5x e 6% da janela de cinco horas. Pelo cenário de conversão local, isso corresponde a **350% de uma semana Pro** e **30% da janela Pro de cinco horas**. O ciclo corria havia aproximadamente 154,95 horas. Mantendo o mesmo ritmo médio e a mesma mistura de modelos, a projeção semanal é **379,5% da capacidade semanal Pro estimada**.

| Parcela da carga Claude atual mantida no Pro | Semana Pro projetada pelo simulador |
|---|---:|
| 100% | 379% |
| 50% | 190% |
| 25% | 95% |
| 20% | 76% |

Nesse cenário, manter toda a carga consumiria uma semana Pro em cerca de **1,8 dia de calendário**, em média. No histórico observado, o medidor Max chegou a 20% da semana em 11/10 às 11:33; pelo fator cinco local, isso já equivaleria a uma semana Pro. A média não garante quantas horas de trabalho cabem: concentrar chamadas também pode atingir primeiro o limite de cinco horas.

Para deixar os 15% de margem previstos no orçamento local, a projeção sugere manter aproximadamente **22% da carga atual no Claude** e transferir ou eliminar cerca de **78% do consumo**. São percentuais de consumo sob a mesma mistura de trabalho, não de quantidade de pedidos. Não há evidência de que simplesmente trocar de modelo ou encaminhar certa porcentagem das mensagens garanta essa redução.

**A simulação já configurada confirma o problema de ritmo.** `~/.omnigent/hub/budget.json` inicia um cenário Pro em **14/10 às 18:40:53**, com 52% de uso semanal simulado, enquanto o medidor real Max estava em 53%. A conta chegou a 70% no instante de referência. A regra existente resulta em:

`52% + 5 × (70% − 53%) = 137% de uso semanal Pro simulado`

Ou seja, nesse cenário os 48 pontos que restavam não bastariam. A primeira leitura preservada com 63% Max é de 15/10 às 11:00:31, quando a simulação já daria 102%. A configuração também contém uma exceção para gastar o saldo Max até o reset; esta auditoria não alterou as regras nem a assinatura. O próximo reset semanal registrado é **16/10 por volta de 09:00 local**; o da janela de cinco horas é **15/10 por volta de 23:30**.

**O que é possível dizer especificamente da conversa de áudio.** O total de tokens preservados corresponde a cerca de 228,8 milhões de tokens ponderados, mas, por ser descontínuo, não permite uma previsão semanal completa. Só os tokens da sessão principal preservados em **14/10** equivalem a **45,6% de uma semana Pro estimada**. Esse número já é relevante para um único dia e não inclui todos os trabalhadores. Não se deve somá-lo ao medidor da conta: ele representa parte desse consumo.

**O caso do terminal é muito menor e usa duas cotas diferentes.** As duas tentativas Sol custaram US$ 1,48 registrados na conta Codex; não consomem Claude Pro. A continuação Opus custou US$ 3,03 registrados e corresponde a aproximadamente **5,71% da janela Pro de cinco horas** e **0,785% da semana Pro**, pela fórmula local. Vinte continuações com o mesmo perfil corresponderiam a cerca de **15,7% da semana Pro**. Esse cenário é de intervenções que aproveitam implementação pronta, não de vinte tarefas completas iniciadas do zero. A [análise dos modelos](./TERMINAL_MODEL_PERFORMANCE_2026-10-15.md) descreve essa distinção.

**Projeção de custo registrado da conversa de áudio.** A janela recente de **08–14/10**, sete dias de calendário UTC, contém US$ 482,71 em eventos de custo. Se esse ritmo e essa mistura de trabalho se repetissem:

| Horizonte | Custo de referência registrado projetado |
|---|---:|
| Um dia de calendário | US$ 68,96 |
| Sete dias | US$ 482,71 |
| Trinta dias | US$ 2.068,78 |

**Esclarecimento sobre a amostra:** essa projeção usa apenas os sete dias 08–14/10 UTC, não a média de várias semanas. Em São Paulo, a janela de calendário corresponde a 07/10 às 21:00 até 14/10 às 21:00, com o fim exclusivo. Ela mistura benchmarks e desenvolvimento anteriores com a implantação do Hub e das lanes; não representa uma semana estável de uso do Hub com múltiplas lanes. Exclui 15/10, quando ocorreram as tentativas do terminal e outras lanes novas, e não soma as demais conversas do projeto ou da conta.

| Dia UTC | Custo registrado da conversa de áudio |
|---|---:|
| 08/10 | US$ 94,03 |
| 09/10 | US$ 76,33 |
| 10/10 | US$ 48,80 |
| 11/10 | US$ 16,09 |
| 12/10 | US$ 19,26 |
| 13/10 | US$ 114,14 |
| 14/10 | US$ 114,07 |
| Total, calculado antes do arredondamento diário | US$ 482,71 |

A fórmula exata foi `482,7148277 / 7 × 30 = 2.068,777833`. Os cinco primeiros dias somam US$ 254,51 (média US$ 50,90/dia); os dois últimos, US$ 228,21 (média US$ 114,10/dia). Isso mostra que a janela é variável, e não permite atribuir o aumento exclusivamente ao Hub. Como comparação separada, a média dos 28 dias de custo preservado, 17/09–14/10, projetaria US$ 2.002,91 em trinta dias; esse cálculo histórico não foi o usado para chegar a US$ 2.068,78.

Isso projeta o contador local que já inclui benchmarks e desenvolvimento do Hub. **Não é uma previsão da fatura do Pro**, não calcula créditos extras e não inclui custos desconhecidos de outros provedores, voz ou GPU. A projeção de custo dessa conversa e a projeção de cota da conta inteira têm escopos diferentes e não devem ser convertidas uma na outra. Se parte do trabalho for distribuída a Codex ou Gemini, será necessário observar o consumo desses provedores para projetar o total combinado.

Os dados sustentam que o ritmo atual de toda a conta é incompatível com Pro **sob as hipóteses do simulador**. Ainda não sustentam uma previsão exata para uma semana de Pro real. A primeira semana após a mudança deve comparar os medidores reais de cinco horas e semana com pedidos concluídos, modelos e tokens por execução; essa comparação permitirá corrigir a calibração e o orçamento sem atribuir zeros às lacunas.

A coleta e os cálculos estão em [projections.json](</home/nexus/analysis/omnigent-audio-2026-10-15/projections.json>). Para conferir a aritmética do cenário fixo, execute `python3 /home/nexus/analysis/omnigent-audio-2026-10-15/projections.py`. As fontes locais são `summary.json`, os CSVs da auditoria, `pro_equivalent.py`, o medidor Claude e a configuração de orçamento. O cálculo não consulta tokens de autenticação, não altera cotas e não inicia tarefas de agentes.
