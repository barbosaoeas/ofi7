

vamos desenvolver esta prd. Product Requirement Document (PRD) — Controle Oficina

1. Visão Geral

O Controle Oficina é um sistema web full-stack desenvolvido em Django e TailwindCSS para a gestão especializada de oficinas de funilaria e pintura. O sistema substitui o controle manual por um fluxo de trabalho visual e automatizado, integrando orçamentos importados (via XML do sistema Cilia), gestão de ordens de serviço por tarefas, quadro Kanban operacional indexado por colaboradores, controle de comissões e fluxo de caixa básico.

2. Sobre o Produto

2.1 Propósito

Otimizar o gargalo operacional e financeiro de oficinas de funilaria e pintura. O sistema reduz o tempo de digitação de orçamentos através da importação de XML, organiza o pátio produtivo através de um Kanban de tarefas por funcionário e garante a previsibilidade financeira atrelando recebimentos (particulares ou de seguradoras) e comissões ao ciclo de vida da reparação do veículo.

2.2 Público-Alvo

Gerente / Administrador : Controla o fluxo de caixa, analisa faturamento e aprova metas.

Orçamentista : Responsável por agendar avaliações, importar XMLs do sistema Cilia e gerenciar negociações.

Financeiro : Controla entradas, saídas, faturamento de seguradoras, franquias e pagamento de comissões.

Operacional (Funileiros, Pintores, Montadores) : Funcionários de chão de fábrica que interagem com o Kanban para iniciar, pausar e finalizar suas respectivas tarefas.

2.3 Objetivos

Eliminar o retrabalho de digitação : Capturar dados de clientes, veículos, peças e serviços direto do XML Cilia.

Garantir ocupação eficiente (Uma tarefa por vez) : Impedir que um colaborador inicie duas tarefas simultâneas no pátio.

Precisão Financeira : Automatizar o cálculo de comissões por tarefa concluída e prever o fluxo de caixa com base nas datas estimadas de entrega dos veículos.

3. Requisitos Funcionais (RF)

Status de Implementação (07/06/2026)

- [x] RF01 - Autenticação Customizada e Nível de Acesso
- [~] RF02 - Agenda e Gestão de Orçamentos (status/validações OK; bloqueio por peças da oficina + opção “seguir sem peças” OK; ajustes finos podem surgir)
- [x] RF03 - Importação de XML Cilia
- [x] RF04 - Gestão de Peças (CRUD + compra/prev. chegada/chegada/atraso + relatório/ impressão)
- [~] RF05 - Cadastro de Atividades e Comissões (Cadastro de Serviços + comissão por serviço e relatório; correlação automática por nome + seleção manual na OS)
- [~] RF06 - Ordem de Serviço (O.S.) e Escalonamento Operacional (OS com agendamento por tarefa/colaborador/data/status; falta agendamento sequencial por hora e regras avançadas)
- [~] RF07 - Kanban Produtivo Dinâmico (por data; iniciar/pausar/finalizar; 1 tarefa em andamento por colaborador; timer; atraso; auto-pausa 17:48; pátio; auto-refresh)
- [ ] RF08 - Fluxo de Caixa e Lançamentos Condicionais (Modais)
- [~] RF09 - Dashboard e Relatórios (comissões + peças OK; dashboard e demais relatórios pendentes)
- [ ] RF10 - Integração WhatsApp ↔️ Financeiro (UAIZAPI / Evolution API) — LANÇAMENTO POR COMANDO DE VOZ/TEXTO NO ZAP

RF01 - Autenticação Customizada e Nível de Acesso

O sistema deve usar o motor nativo do Django, utilizando o E-mail como identificador único no lugar do username.

Cadastro público inicial de usuários com direcionamento para a tela de login.

Níveis de acesso baseados em grupos/funções nativas do Django: Gerente, Financeiro, Orçamentista, Operacional.

RF02 - Agenda e Gestão de Orçamentos

Agendamento com status: Aguardando Resposta , Autorizada , Não Aprovada .

Caso Não Aprovada , exigir obrigatoriamente a justificativa (Ex: Valor Alto, Preço Concorrente Menor, Cliente Sem Recurso, Outros).

Caso Autorizada , exigir: Data de Entrada do Veículo e Data de Início do Reparo.

Se houver peças mapeadas que dependem de fornecedor externo, o início do reparo fica condicionado/bloqueado no sistema até a marcação de chegada das peças.

RF03 - Importação de XML Cilia

Upload de arquivo XML emitido pelo software Cilia.

Parser automatizado para cadastrar/vincular:

Cliente : Se não existir pelo CPF/CNPJ, criar novo cadastro.

Veículo : Vincular ao cliente (um cliente pode ter múltiplos veículos).

Serviços : Mapear a lista de serviços para conversão em tarefas da Ordem de Serviço.

Peças : Identificar a lista de peças necessárias.

RF04 - Gestão de Peças

CRUD de Peças associado diretamente a um Orçamento/Veículo.

Controle do tipo de fornecedor da peça: Cliente , Seguradora ou Oficina .

Regra de Negócio: Peças marcadas como fornecidas pela Oficina devem obrigatoriamente somar o valor de custo/venda no fechamento financeiro da Ordem de Serviço.

RF05 - Cadastro de Atividades e Comissões

CRUD de Atividades Padrão contendo: Nome da Atividade e Valor Fixo ou Percentual da Comissão.

As tarefas importadas do XML devem ser correlacionadas a estas atividades para fins de cálculo de comissão ao colaborador.

RF06 - Ordem de Serviço (O.S.) e Escalonamento Operacional

Para cada etapa/atividade da O.S., definir o Tempo Programado de Execução (horas/minutos) e o Valor da Atividade.

Agendamento sequencial de execução (Data/Hora de início prevista para cada etapa).

Geração automática de cards no Kanban assim que a data programada for atingida ou a etapa anterior for finalizada.

RF07 - Kanban Produtivo Dinâmico

Colunas fixas da esquerda para a direita: Patio , Desmontagem , Funilaria , Preparação , Pintura , Montagem , Polimento , Prep Entrega .

O card deve exibir visualmente: Imagem/Foto do Veículo, Nome/Foto do Funcionário Alocado, Tempo Restante/Programado da Tarefa.

Mecanismo de Play/Pause/Stop no Card para o operador controlar o tempo real trabalhado.

Regra de Bloqueio : O sistema deve impedir que o funcionário clique em "Iniciar/Play" em uma tarefa se ele já possuir outra tarefa ativa com status em andamento.

Ao clicar em finalizar a tarefa, o sistema calcula e provisiona a comissão do respectivo colaborador automaticamente.

RF08 - Fluxo de Caixa e Lançamentos Condicionais (Modais)

Ato de Aprovação do Orçamento : Disparar Modal de Entrada Financeira.

Se Particular : Perguntar se haverá entrada em dinheiro/cartão. Registrar entrada imediata no caixa. O saldo devedor restante é provisionado automaticamente como entrada futura na Data de Previsão de Entrega do Veículo .

Se Seguradora : Perguntar se há Franquia a receber do cliente. Em caso positivo, registrar valor e data. O saldo restante (pago pela seguradora) é provisionado como entrada futura com base na data estimada de faturamento/recebimento da companhia.

Controle de Fluxo de Caixa Básico : Lançamento manual de Entradas e Saídas categorizadas por Tipo de Despesa ( Operacional , Custo Fixo , Custo Variável ).

RF09 - Dashboard e Relatórios

Objetivo: Transformar o sistema em uma ferramenta de tomada de decisão. Hoje o Dashboard Principal (core/dashboard) está vazio (1ª tela que o usuário vê após o login) e 3 relatórios já estão implementados parcialmente — esta RF fecha a documentação do que já existe e define o que falta para entregar visibilidade 360°: Financeiro, Produtivo, Comercial e de Pessoal.

Acesso por role:
- Dashboard Principal (após login): TODOS os papéis (com KPIs diferentes por role).
- Dashboard Financeiro Insights (Chart.js): MANAGER / FINANCE.
- Relatórios Operacionais (Peças, Comissões, Produtividade): MANAGER / FINANCE / ORCAMENTISTA.
- Relatórios Comerciais (Conversão de Orçamentos, Motivos de Recusa): MANAGER / ORCAMENTISTA.
- Operacional (VISUAL): Apenas o relatório pessoal de comissões do colaborador logado (restrito aos seus próprios lançamentos).

---

### Módulo 9.1 — Dashboard Principal (core/dashboard) — TELA VAZIA HOJE (PRIORIDADE 1)

Hoje o usuário faz login e cai em uma tela com apenas 1 título "Dashboard" + e-mail. Substituir por 5 seções, com conteúdo diferente por role:

KPIs de topo (cinturão de 4 a 6 cards, 1 linha):
1.  📊 **Faturamento do Mês** (verde) — Total de entradas REALIZADAS no mês; comparação percentual vs mês anterior (↑ 12% ou ↓ 8%).
2.  🚗 **Veículos em Produção hoje** (azul) — Total de WorkOrderTasks com status Em Execução / Pendentes / Pausados (contagem simples).
3.  💸 **A Receber em aberto** (amarelo) — CashMovement direction=IN + is_realized=False (até 30 dias).
4.  ⚠️ **Atrasados hoje** (vermelho) — Tarefas atrasadas + Lançamentos vencidos (atrasados = contador total com badge).
5.  🎯 **Orçamentos aguardando resposta** (dourado) — Budget.status = Aguardando Resposta (mostra total + link direto).
6.  👤 **Comissão do mês (colaborador logado)** — Somente para papéis OPERACIONAL/VISUAL: R$ de comissão provisionado no mês para o próprio user.

Blocos do meio:
- **Bloco ESQUERDA (66%)** — "Pátio Hoje": mini Kanban horizontal em tempo real, top 5 tarefas em andamento com nome do colaborador + timer (igual preview do kanban_today, porém resumido).
- **Bloco DIREITA (33%)** — "Próximos 5 lançamentos a vencer nos próximos 7 dias": lista bulleted de CashMovement (valor + vencimento + cliente/fornecedor).

Rodapé do Dashboard:
- **Atalhos rápidos** (cards com ícone): Novo Orçamento · Importar XML · Nova OS · Novo Lançamento · Kanban Hoje · Relatório Comissões.

---

### Módulo 9.2 — Dashboard Financeiro + Insights (JÁ IMPLEMENTADO parcialmente em finance_dashboard + finance_insights)

Documentando o que existe hoje e o que falta:

**Tela 1 — Financeiro / Lançamentos (finance_dashboard.html, JÁ EXISTE):**
- Filtros padrão (topo): Data De · Data Até · Direção (IN/OUT/Todas) · Status (Todos / Em aberto / Realizados) · Origem (Particular / Seguradora / Fornecedor / etc).
- Cinturão de 4 cards (JÁ EXISTE):
  1.  💹 **Previsto** — Entradas − Saídas no período.
  2.  ✅ **Realizado** — Valor já efetivado no caixa.
  3.  🟡 **Em aberto** — Valor a receber + a pagar.
  4.  🔴 **Atrasado** — Lançamentos vencidos até hoje, ainda abertos (linha inteira fica `bg-[#160808]` na tabela).
- Tabela de lançamentos (20+ colunas úteis, JÁ EXISTE): Data lançamento, Data vencimento, Direção, Origem, Banco, Cliente/Fornecedor, Descrição, Orçamento/OS #, Valor, Status, Ações (Editar, Excluir, Marcar realizado).
- Badge **Atrasado** (vermelho sólido) aparece automaticamente quando due_date < today e is_realized=False.

**Tela 2 — Insights Gráficos (finance_insights.html, JÁ EXISTE com Chart.js via CDN):**
- Alternador de Período no topo (3 botões): **Mês atual · 3 meses · 12 meses** (com toggle visual da aba ativa).
- Filtros adicionais: Direção (IN/OUT/Todas) · Origem (categoria).
- Cinturão KPIs (4 cards, JÁ EXISTE): A Receber aberto · A Pagar aberto · Em atraso · Saldo projetado (receber − pagar).
- Gráficos (Chart.js canvas, JÁ IMPLEMENTADOS parcialmente — falta garantir responsividade e legenda):
  1.  📊 **Gráfico 1 (Bar, 66% width)** — "Entradas e Saídas por Mês": 2 barras por mês (Entrada verde, Saída vermelha) — comparativo previsto vs realizado (2 séries).
  2.  🍩 **Gráfico 2 (Doughnut, 33% width)** — "Situação dos Lançamentos": 4 fatias (Realizado / Em aberto / Atrasado / Vence hoje).
  3.  📊 **Gráfico 3 (Bar horizontal, 60% width)** — "Despesas por Categoria": Top 8 categorias CashCategory com valores de saída.
  4.  🥧 **Gráfico 4 (Pie, 40% width)** — "Origem das Entradas": % Particular vs % Seguradora vs % Outros.

**Falta documentar (o que já existe mas precisa ser garantido):**
- Cada gráfico tem exportação PNG: botão 📤 "Baixar gráfico PNG" no topo do card (usa canvas.toDataURL()).
- Painel inferior "Ranking de Clientes (R$ faturados no período)" — tabela com Top 10 clientes ordenados por total de entradas CashMovement vinculadas.

---

### Módulo 9.3 — Relatório de Comissões (JÁ IMPLEMENTADO em commission_open_list.html)

Documentando o que já existe hoje em produção:
- Filtros: **Data De** · **Data Até** · **Colaborador** (select dropdown com todos ou o próprio user logado quando role=VISUAL/OPERACIONAL) · **Checkbox "Mostrar pagos"** (padrão desmarcado → só mostra abertas).
- Cinturão superior: Total (filtrado) R$ — quando Mostrar pagos=False = "Total em aberto", quando True = "Total (filtrado)".
- **Regra de segurança OPERACIONAL/VISUAL:** colabs veem SOMENTE as próprias comissões — coluna Colaborador some, filtro de colaborador fica `disabled` e hidden input força `collaborator_id = request.user.collaborator.id`.
- Botão **🖨️ Imprimir** (canto superior direito): layout A4 portrait 10mm margem, fundo branco, tabela preta e branca, total no rodapé, @media print completa (JÁ IMPLEMENTADO via `#commission-print-host` + classe `body.print-commission`).
- Colunas da tabela: Data conclusão, OS #, Tarefa, Valor atividade, % comissão, Valor comissão, Status (Paga / Em aberto), Pagamento (data prevista / data efetiva).
- Ação por linha: Marcar como paga (MANAGER/FINANCE) — clica e fecha a comissão (atualiza CashMovement ou WorkOrderCommission.paid=True).

---

### Módulo 9.4 — Relatório de Peças (JÁ IMPLEMENTADO em report_pieces.html)

O que já existe hoje (validado no template):
- Filtro único: **Data referência** (datepicker). Hoje mostra o status de chegada das peças até aquela data.
- Layout IMPRESSÃO A4 LANDSCAPE completo: @page size A4 landscape margem 8mm; fundo branco, bordas cinza claro, thead cinza, 0 sombra, cores neutras.
- Colunas da tabela de peças: Peça, Fornecedor (Cliente / Seguradora / Oficina), Valor custo, Valor venda, Data prevista chegada, Status (Pendente / Em rota / Chegou — badge colorido), OS # associada, Cliente, Veículo.
- Agrupamento: 1 seção por OS (WorkOrder), 1 bloco com dados do veículo no topo, depois a tabela de peças desta OS.
- Totalizadores por fornecedor no rodapé: Total peças Seguradora R$, Total peças Oficina R$, Total Geral R$.

---

### Módulo 9.5 — Relatório de Motivos de Recusa de Orçamentos (A IMPLEMENTAR)

Objetivo: Melhorar taxa de conversão comercial identificando os gargalos.
- Filtros: **Data De** · **Data Até** · **Orçamentista** (todos / 1 específico).
- 2 partes na tela:
  1.  **Cinturão KPIs:** Total orçamentos no período · Autorizadas % · Não aprovadas % · Aguardando %.
  2.  **Gráfico de Barras (Chart.js):** Top motivos de recusa ordenados por volume (Valor Alto · Preço concorrente menor · Cliente sem recurso · Reparos não autorizados pela seguradora · Prazo de entrega · Outros).
  3.  **Tabela detalhada:** Orçamento #, Cliente, Veículo, Valor total, Data visita, Motivo principal, Observações recusa (justificativa livre informada no Budget.refusal_reason), Orçamentista responsável.
- Botão "Baixar CSV" para planilha do Google Sheets / Excel.

---

### Módulo 9.6 — Relatório de Produtividade por Colaborador (A IMPLEMENTAR)

Objetivo: Calcular eficiência do pátio (KPI Operacional definido na seção 10 — KPI de Eficiência de Pátio = Tempo programado vs tempo real).
- Filtros: **Data De** · **Data Até** · **Colaborador** (todos / 1) · **Atividade (Kanban column)** (Funilaria / Pintura / Montagem etc).
- Cinturão superior:
  1.  Total tarefas concluídas no período.
  2.  Horas programadas totais.
  3.  Horas reais totais (play/pause somados).
  4.  Índice de Eficiência global % = (programado / real) × 100 (acima de 100% = verde, abaixo de 85% = vermelho).
- Gráfico de linhas: Eficiência por dia do período (linha azul, linha de referência 100% cinza tracejada).
- Tabela por colaborador: Nome, # tarefas concluídas, HH programado, HH real, HH HH (diferença), Eficiência %, Ranking (1º com medalha 🥇 no ranking).
- Regra de Filtro automático: Colaboradores **inativos (is_active=False)** NÃO aparecem neste relatório (a não ser que seja marcado checkbox "Incluir inativos"). Garante o requisito do usuário "prestador inativo não entra no rateio HH".

---

### Módulo 9.7 — Relatório Funil de Conversão Orçamento → OS → Entrega (A IMPLEMENTAR)

Objetivo: Medir % de orçamentos que efetivamente viram OS e % de OS que entregamos no prazo.
- Etapas do funil:
  1.  Total Orçamentos criados no período (100%)
  2.  Orçamentos Autorizadas (% vs etapa 1)
  3.  Geraram OS (% vs etapa 2)
  4.  OS Iniciadas (% vs etapa 3)
  5.  OS Concluídas (% vs etapa 4)
  6.  OS Entregues no prazo (% vs etapa 5 — sem atraso na data de entrega contratada)
- Gráfico de Funil (Chart.js Funnel plugin ou barras horizontais progressivas).
- Tabela: por mês do ano, mostrando as % em cada etapa com cores semáforo (verde > 80%, amarelo 60-80%, vermelho < 60%).

---

### Filtros Padrão — REGRAS de UX para TODOS os relatórios (todos os módulos 9.1 a 9.7)

Esses padrões já existem em alguns relatórios e agora são documentados como OBRIGATÓRIOS em todos:
1.  **Filtro de Período:** Sempre apresentar "Data De" + "Data Até" em formatos nativos `<input type="date">`. Atalhos rápidos em botão: Hoje · Semana atual · Mês atual · Últimos 30 dias · Últimos 90 dias · Ano atual.
2.  **Botão Filtrar** sempre dourado (primário). Botão Limpar sempre cinza com borda (secundário), ao lado, direita do Filtrar.
3.  **Filtros são sempre GET (na URL)** — permite compartilhar o link do relatório com outro gerente (ex: `?start=2026-08-01&end=2026-08-31&collaborator_id=7`).
4.  **Responsividade:** Grid de filtros `grid-cols-1 md:grid-cols-4 gap-3` (1 coluna mobile, 4 colunas desktop).
5.  **Totalizadores SEMPRE aparecem acima da tabela:** R$ filtrado, itens encontrados.
6.  **Impressão:** Em todos os relatórios existe botão 🖨️ "Imprimir" no cabeçalho (topo direita). Usa `@media print` com fundo branco, preto e branco, tamanho A4 (landscape quando tem muitas colunas, portrait quando tem poucas), margens 8–10mm, total no rodapé, cabeçalho com logo da oficina + data de emissão do relatório.
7.  **CSV / Excel (exportação):** Botão 📥 "Exportar CSV" quando a tabela tiver + de 100 registros. Gera CSV separado por `;` com encoding UTF-8 BOM (compatível com Excel Brasil).

---

### Regras de Negócio e Segurança nos Relatórios

1.  **Isolamento de Comissão:** Colaboradores (role OPERACIONAL ou VISUAL) conseguem abrir o relatório de comissões mas o filtro de colaborador trava SEU ID — não conseguem ver comissão de colegas.
2.  **Colaboradores Inativos:** No Relatório de Produtividade e em qualquer dropdown de seleção de colaborador em relatórios de RH, `is_active=False` vem oculto por padrão. Checkbox "Incluir inativos" permite visualizar histórico (só MANAGER/FINANCE).
3.  **Logs de auditoria:** Todo acesso a relatórios de Financeiro (9.2 / 9.3) registra horário + usuário em `ReportAccessLog` (opcional, futuro — pelo menos hoje nenhum dado sensível é exportado sem estar logado, garante RF01 e a correção de segurança anterior).
4.  **Proteção de Rotas:** TODAS as views de Dashboard/Relatórios são protegidas com `RoleRequiredMixin` (LoginRequiredMixin obrigatório), como corrigido no commit de segurança anterior — NENHUM relatório fica acessível sem login.

---

### Modelos Envolvidos (utilizados hoje para gerar os relatórios)

(Não são modelos NOVOS — são os que já existem no banco e são consumidos pelas consultas):
- **Budget** (Orçamento) — usado em RF09.5 (Recusas), RF09.7 (Funil).
- **Budget.refusal_reason + refusal_category** (Motivo recusa + categoria agrupada).
- **WorkOrder** + **WorkOrderTask** — usados em RF09.6 (Produtividade), RF09.7 (Funil).
- **WorkOrderTaskBatch + Lotes** — usados para produtividade e taxa de ocupação do pátio.
- **CashMovement** — usado em RF09.2 (Insights), parte financeira de tudo.
- **CashCategory** — categorias usadas no gráfico "Despesas por Categoria".
- **BankAccount** — usado em filtros de banco/origem.
- **Collaborator (is_active=True filtrado padrão)** — Relatórios de Produtividade, Comissões.
- **WorkOrderCommission (ou coluna `commission_amount` em WorkOrderTask + `paid_at/paid_by`)** — usada no relatório de comissões.
- **Piece** — usada no Relatório de Peças (9.4), Status chegada.
- **Supplier + Piece.provider_type** (Cliente / Seguradora / Oficina).

---

### KPIs Alvo (seção 10 alinhada com estes relatórios)

- **KPI Produtividade Pátio (Eficiência):** Meta ≥ 90% (HH programado / HH real × 100).
- **KPI Conversão Orçamento → Autorizada:** Meta ≥ 70%.
- **KPI Recusa Orçamentos:** Nenhum motivo de recusa deve representar mais de 30% do total de não aprovadas (indica gargalo específico que pode ser atacado).
- **KPI % Entrada atrasada:** Meta ≤ 5% do valor de entradas totais do mês deve estar com status "Atrasado".
- **KPI Pontualidade de Entrega OS no Prazo:** Meta ≥ 85% (concluídas em ≤ data contratada de entrega).

RF10 - Integração WhatsApp ↔️ Financeiro (UAIZAPI / Evolution API)

Objetivo: Permitir que o usuário FINANCEIRA registre LANÇAMENTOS DE ENTRADA E SAÍDA no módulo Financeiro (CashMovement) DIRETAMENTE POR MENSAGEM DE TEXTO/ÁUDIO DO WHATSAPP (sem precisar abrir o sistema no navegador).
Funciona como um "assistente financeiro" no WhatsApp, com comandos de texto simples (slashed (/pix, /cartao, /dinheiro, /despesa, /boleto) ou transcrição de áudio.

Provedores suportados (2 opções futuras):
1.  UAIZAPI / Z-API (assinatura paga mensal ~R$25 a 50/mês por número — custo baixo, sem bloqueios baixo, suporte.
2.  Evolution API (open-source self-hosted gratuita — hospedada no próprio PythonAnywhere ou VPS; sem mensalidade; configuração inicial de instalação de containers.
3.  (Alternativa oficial sem bloqueios: Meta WhatsApp Cloud API (R$0,008/mensagem — pagamento por uso, 100% segura, sem risco de bloqueio do WhatsApp oficial).

Fluxo de Funcionamento:
1.  Número de WhatsApp dedicado para o Financeiro (ex: (11) 9XXXX-XXXX) conectado via UAIZAPI/Evolution API.
2.  Usuário (Financeiro / Gerente autorizado envia mensagem no formato de comando (ex: "/pix 500 os 435 cliente Fulano") ou áudio curto "Pix de R$500 da OS 435 do cliente Fulano").
3.  UAIZAPI envia Webhook POST JSON (com texto/transcrição para endpoint Django em SUA_OFICINA.pythonanywhere.com/webhooks/zap/.
4.  Django valida Token de autenticação do webhook (proteger de requisições não autorizadas).
5.  Parser inteligente de texto/transcrição interpreta os campos: direção (IN/OUT), valor, método pagamento (PIX/CARTÃO/DINHEIRO/BOLETO), OS número, cliente/seguradora/fornecedor, categoria, vencimento.
6.  Cria automaticamente um registro CashMovement compatível 100% com o módulo financeiro já existente (CashMovement já existente (CashCategory, BankAccount, Customer, Budget, Source (particular/seguradora).
7.  Responde imediatamente no WhatsApp: confirmação ✅ "Lançamento #XXX confirmado" ou erro "⚠️ Formato correto: /pix VALOR os NNN cliente" se não reconhecer.
8.  Logs de requisição webhook recebida em tabela WhatsAppWebhookLog para auditoria futura.

Comandos padrão (atalhos para o usuário FINANCEIRO (texto direto no WhatsApp, barra-inicial "/" (facilidade):
-   /pix 500 os 435 cliente
-   /cartao 1200 os 440 seguradora Porto
-   /dinheiro 300 os 450 particular
-   /despesa 230 fornecedor "Oficina Jose" categoria material
-   /boleto 800 categoria aluguel vencimento 30/10
-   /salario 3000 colaborador Leo categoria pessoal
-   /ajuda → lista todos comandos no chat do Zap

Regras de Negócio e Segurança:
-   Whitelist WHITELIST DE NÚMEROS: Somente números de usuários autorizados cadastrados (users.phone registrados no CustomUser vinculados com role MANAGER/FINANCE liberados acessam o assistente. Números bloqueados retornam erro de bloqueado e não faz nada no DB.
-   Auditoria 100% Log: Toda chamada webhook recebida no banco WhatsAppWebhookLog (com body, remetente, data, status, erro se houve.
-   Idempotência: Não lança duas vezes o mesmo lançamento mesmo se o webhook enviar duas vezes (chave idempotência = hash do texto + remetente + minuto).
-   Validação de campos obrigatórios: valor > 0, categoria existe? OS # tem permissão?

Modelos Novos (criar na app budgets / ou app dedicada integrations:
-   WhatsAppWebhookLog: id, received_at, sender_phone, message_text, audio_transcript, parsed_ok, error_message, cash_movement_id FK (FK para CashMovement), raw_body JSON.
-   WhatsAppIntegrationConfig: provedor (UAIZAPI/EVOLUTION/META_CLOUD), api_token, webhook_secret, numero_dedicado, active, default_bank_account_id FK (padrão para Pix

4. Flowchart Mermaid com os Fluxos de UX

mermaid

graph TD

A[Visitante: Index Pública] -->|Link Cadastre-se| B[Formulário de Cadastro]

A -->|Link Login| C[Tela de Login - Email/Senha]

B --> C

C -->|Autenticado| D[Dashboard Principal]

D --> E[Menu: Orçamentos]

E --> E1[Agenda de Orçamentos]

E --> E2[Importar XML Cilia]

E1 -->|Aprovar Orçamento| E3{Modal de Entrada}

E3 -->|Particular| E4[Lança Entrada + Saldo na Data de Entrega]

E3 -->|Seguradora| E5[Lança Franquia + Saldo Seguradora]

D --> F[Menu: Oficina / OS]

F --> F1[Programar Datas e Alocar Equipe]

F1 --> F2[Quadro Kanban Operacional]

F2 -->|Play/Pause Tarefa| F3{Valida: Colaborador Livre?}

F3 -->|Sim| F4[Inicia Cronômetro da Atividade]

F3 -->|Não| F5[Alerta: Conclua a tarefa atual primeiro]

F4 -->|Finalizar Tarefa| F6[Gera Comissão do Funcionário]

D --> G[Menu: Financeiro]

G --> G1[Fluxo de Caixa: Entradas/Saídas]

G --> G2[Relatório de Comissões por Período]

Use o código com cuidado.

5. Requisitos Não-Funcionais (RNF)

RNF01 - Banco de Dados : Uso exclusivo do SQLite padrão em ambiente local/desenvolvimento para simplificação de portabilidade nesta fase inicial.

RNF02 - Padronização de Código : Seguir estritamente a PEP8. Utilizar obrigatoriamente aspas simples ( ' ) para strings em Python e Javascript. Código-fonte escrito em inglês (classes, métodos, variáveis, tabelas).

RNF03 - Interface e Idioma : Frontend inteiramente em português brasileiro (PT-BR) visando a usabilidade dos funcionários da oficina.

RNF04 - UI/UX Architecture : Construção visual monolítica com Django Template Language (DTL) e TailwindCSS via CDN ou compilação simples integrada. Sem frameworks SPA (React/Vue) para evitar over-engineering .

RNF05 - Desempenho e Restrições : O parsing do XML Cilia deve ocorrer de forma síncrona nativa com bibliotecas integradas ( xml.etree.ElementTree ), limitando uploads de arquivos a 10MB.

RNF06 - Arquitetura Limpa : Isolar escopos de negócios por Apps Django separados. Utilizar Views baseadas em Classes ( CBV ) nativas para os CRUDs primários.

RNF07 - Arquivos de Extensão : Caso use signals no projeto, eles devem ficar obrigatoriamente em um arquivo signals.py dentro da app correspondente do signal.

6. Arquitetura Técnica & Stack

6.1 Stack Tecnológica

Linguagem : Python 3.11+

Framework Web : Django 5.0+ (Full Stack Monolítico)

Banco de Dados : SQLite 3

CSS Framework : TailwindCSS

Ícones e Elementos Visuais : Heroicons (via SVG embutido ou biblioteca nativa em template)

Componentes Interativos Céleres (Kanban Drag/Click, Modais) : Vanilla Javascript puro embutido nas tags <script> do DTL.

6.2 Estrutura de Apps Django

text

controle_oficina/

├── core/             # Configurações globais, templates base, páginas institucionais

├── users/            # Custom User Model (Email login), Grupos, Permissões

├── customers/        # Clientes e Veículos (Um para Muitos)

├── budgets/          # Orçamentos, Importador XML Cilia, Agenda, Peças

├── operations/       # Ordens de Serviço, Atividades, Kanban, Controle de Tempos/Tarefas

└── finance/          # Lançamentos de Caixa, Comissões e Relatórios

Use o código com cuidado.

7. Estrutura de Dados com Schemas em Formato Mermaid

mermaid

erDiagram

USER {

int id PK

string email UK

string password

string role "Gerente, Financeiro, Orcamentista, Operacional"

datetime created_at

datetime updated_at

}

CUSTOMER {

int id PK

string name

string document_cpf_cnpj UK

string phone

string email

datetime created_at

datetime updated_at

}

VEHICLE {

int id PK

int customer_id FK

string plate UK

string model

string brand

string color

string year

string image_url

datetime created_at

datetime updated_at

}

BUDGET {

int id PK

int customer_id FK

int vehicle_id FK

string status "Aguardando, Autorizada, Nao Aprovada"

string refusal_reason

date entry_date

date repair_start_date

decimal total_amount

datetime created_at

datetime updated_at

}

PIECE {

int id PK

int budget_id FK

string name

decimal cost_price

string provider_type "Cliente, Seguradora, Oficina"

boolean arrived

datetime created_at

datetime updated_at

}

ACTIVITY_CATALOG {

int id PK

string name UK

decimal commission_rate_or_value

datetime created_at

datetime updated_at

}

SERVICE_ORDER {

int id PK

int budget_id FK

string status "Aberto, Em Execucao, Finalizado"

datetime created_at

datetime updated_at

}

TASK {

int id PK

int service_order_id FK

int activity_id FK

int assigned_user_id FK

string kanban_column "Patio, Desmontagem, Funilaria, etc"

int scheduled_duration_minutes

decimal value

datetime started_at

datetime paused_at

int total_elapsed_seconds

string status "Pendente, Executando, Pausado, Concluido"

datetime created_at

datetime updated_at

}

FINANCIAL_TRANSACTION {

int id PK

int budget_id FK "Optional"

string type "Entrada, Saida"

string category "Operacional, Custo Fixo, Custo Variavel"

decimal amount

date payment_due_date

boolean is_paid

datetime created_at

datetime updated_at

}

CUSTOMER ||--o{ VEHICLE : "possui"

CUSTOMER ||--o{ BUDGET : "solicita"

VEHICLE ||--o{ BUDGET : "recebe"

BUDGET ||--o{ PIECE : "contem"

BUDGET ||--o| SERVICE_ORDER : "gera"

SERVICE_ORDER ||--o{ TASK : "contem"

ACTIVITY_CATALOG ||--o{ TASK : "define"

USER ||--o{ TASK : "executa"

BUDGET ||--o{ FINANCIAL_TRANSACTION : "vincula"

Use o código com cuidado.

8. Design System (TailwindCSS Premium Dark Pattern)

Toda a interface do sistema adotará um tema escuro premium ( Premium Dark Pattern ), combinando pretos profundos, grafites e detalhes dourados elegantes.

html

<!-- Exemplo de Paleta de Cores Aplicada no Template Base -->

<body class="bg-[#0D0D0D] text-[#F5F5F5] font-sans antialiased" >

Use o código com cuidado.

8.1 Diretrizes de UI e Identidade Visual

Cores de Fundo : Fundo principal bg-[#0D0D0D] (Preto puro). Superfícies, cards, modais e containers usam bg-[#1A1A1A] (Grafite Escuro) com bordas border-[#262626] .

Cores de Texto : Textos principais em dourado corporativo text-[#D4AF37] para títulos, destaques e labels críticos. Textos secundários ou de leitura longa em text-[#F5F5F5] ou text-[#A3A3A3] .

Padrão de Botões :

Botão Primário : Gradiente dourado bg-gradient-to-r from-[#D4AF37] to-[#AA882C] com texto escuro text-[#0D0D0D] .

Botão Secundário : Fundo escuro bg-[#262626] com borda clara border-[#404040] e texto claro text-[#F5F5F5] .

Padrão de Inputs e Forms : Fundo cinza profundo bg-[#121212] , borda sutil border-[#262626] , texto em text-[#F5F5F5] , alterando o foco para a borda dourada focus:border-[#D4AF37] .

Padrão de Grids e Menos : Sidebar de navegação estática em bg-[#141414] com divisores dourados leves. Grids de conteúdo padronizados usando a classe grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 .

9. User Stories (Histórias de Usuário)

Épico 1: Adaptação de Entrada e XML

Como Orçamentista,

Quero fazer o upload do XML gerado pelo sistema Cilia,

Para que o cliente, o carro e as peças sejam importados sem eu precisar digitar um por um.

Critérios de Aceite :

Se o CPF do cliente contido no XML já existir no banco, ele deve reaproveitar o registro.

Se o carro já existir cadastrado sob a posse daquele cliente pela placa, ele não duplica o veículo.

Todos os itens de peças listados no XML devem aparecer vinculados como peças do orçamento.

Épico 2: Bloqueio Operacional de Chão de Fábrica

Como Mecânico / Pintor da Oficina,

Quero clicar em dar "Play" na minha tarefa designada no Kanban,

Para que o tempo comece a rodar e minha produtividade seja computada.

Critérios de Aceite :

Ao tentar clicar em "Iniciar" em uma tarefa, o sistema verifica se o ID do usuário logado já possui um registro de tarefa com status 'Executando' .

Caso possua, o botão é bloqueado na tela e exibe um alerta toast na cor vermelha/ouro informando: "Você já possui uma tarefa ativa em andamento."

Ao clicar em finalizar, o status muda para 'Concluido' e limpa o flag de ocupação do funcionário.

Épico 3: Assistente Financeiro por WhatsApp (Integração UAIZAPI / Evolution API)

Como Usuário do Financeiro / Gerente,
Quero lançar ENTRADAS e SAÍDAS no Fluxo de Caixa escrevendo uma mensagem rápida no WhatsApp (ex: "/pix 500 os 435 cliente"), sem precisar abrir o computador / navegador e fazer login no sistema,
Para que eu consiga registrar pagamentos e recebimentos NA HORA, inclusive fora da oficina (ex: recebendo um Pix no fim de semana).

Critérios de Aceite:
Ao enviar uma mensagem no formato /pix 500 os 435 cliente Fulano, o sistema cria automaticamente um CashMovement (Entrada IN, fonte PARTICULAR, banco=PIX, OS=435 vinculada).
O sistema responde NO PRÓPRIO WHATSAPP ✅ "Lançamento #77 criado: Entrada R$500,00 OS#435 — Particular Fulano" confirmando o registro.
Apenas números de WHITELIST cadastrados (Financeiro / Gerente) têm permissão — números desconhecidos recebem mensagem de bloqueio e não alteram o banco de dados.
Tudo já aparece normalmente no Dashboard Financeiro sem necessidade de importação (tudo integra 100% ao modelo CashMovement já existente).
Se o usuário errar o comando (ex: valor faltando), o sistema responde automático: ⚠️ "Formato correto: /pix VALOR os NNN cliente NOME"

10. Métricas de Sucesso & KPIs

KPI de Produto : Tempo médio de conversão entre a importação do XML Cilia e a abertura real da Ordem de Serviço (Alvo: < 15 minutos).

KPI Operacional : Eficiência de Pátio (Diferença entre o tempo programado na O.S. e o tempo real consolidado pelo contador de play/pause do Kanban).

KPI de Negócio/Financeiro : Índice de Recusa de Orçamentos (Mapeamento percentual volumétrico dos motivos informados no cancelamento).

KPI do Assistente WhatsApp Financeiro: Taxa de Acerto do Parser — % de mensagens recebidas no WhatsApp que são automaticamente parseadas e salvas com sucesso como CashMovement, sem intervenção manual (Alvo: ≥ 90%).

11. Riscos e Mitigações

Risco : Divergência estrutural nas tags ou atualizações de layout do arquivo XML emitido pelo Cilia.

Mitigação : Isolar o mecanismo de parse dentro de um helper service encapsulado com tratamento genérico de exceções ( try/except ), registrando logs detalhados sem derrubar a aplicação.

Risco : O colaborador esquecer uma tarefa rodando eternamente no "Play" ao ir embora da oficina.

Mitigação : Implementar uma rotina simples na view do dashboard do gestor para forçar o "Pause" ou encerramento manual de tarefas ativas por parte da gerência.

Risco : Bloqueio temporário do número de WhatsApp (risco inerente a APIs NÃO OFICIAIS tipo UAIZAPI) por suspeita de automação não autorizada.

Mitigação: (1) Para uso crítico em produção, priorizar META WHATSAPP CLOUD API (oficial, 0 risco de bloqueio). Se optar por UAIZAPI: (2) Usar um número de WhatsApp DEDICADO SOMENTE para o financeiro (não enviar spams, não enviar mensagens em massa — só responder o financeiro com mensagens curtas de confirmação, 1:1). (3) Rotina manual fallback: tela de lançamento manual no sistema continua disponível normalmente (não depende do Zap para a rotina diária ser executada).

12. Lista de Tarefas (Backlog Separado em Sprints)

Sprint 1: Fundação, Autenticação Customizada e Design System Base

Tarefa 1: Configuração Estrutural do Repositório Inicial

Subtarefa 1.1: Criar diretório do projeto e inicializar repositório Git local com .gitignore focado em Python/Django e SQLite.

Subtarefa 1.2: Executar comando de inicialização django-admin startproject core_project . .

Subtarefa 1.3: Editar settings.py aplicando as configurações globais de fuso horário brasileiro ( America/Sao_Paulo ) e idioma pt-br .

Tarefa 2: Implementação da App users com Login por E-mail

Subtarefa 2.1: Criar o app local via comando do terminal python manage.py startapp users .

Subtarefa 2.2: Escrever a classe CustomUser estendendo AbstractUser , definindo username = None e email com propriedade unique=True .

Subtarefa 2.3: Configurar propriedade USERNAME_FIELD = 'email' e incluir a lista REQUIRED_FIELDS = [] .

Subtarefa 2.4: Incluir campo de escolha textual ( choices ) role contendo: Gerente, Financeiro, Orçamentista, Operacional.

Subtarefa 2.5: Adicionar campos de auditoria temporal created_at e updated_at na classe CustomUser .

Subtarefa 2.6: Criar classe customizada CustomUserManager para gerenciar a criação correta de usuários comuns e superusuários usando o e-mail como chave única.

Subtarefa 2.7: Mapear a configuração global adicionando AUTH_USER_MODEL = 'users.CustomUser' no escopo do settings.py .

Subtarefa 2.8: Executar comandos de terminal python manage.py makemigrations users e python manage.py migrate .

Tarefa 3: Desenvolvimento do Frontend Base com TailwindCSS Premium Dark Pattern

Subtarefa 3.1: Criar o diretório raiz unificado templates/ e incluir o arquivo mestre de layout base.html .

Subtarefa 3.2: Incluir CDN oficial do TailwindCSS no cabeçalho do template mestre e definir as cores estruturais bg-[#0D0D0D] e text-[#F5F5F5] .

Subtarefa 3.3: Desenhar componente de bloco reutilizável para Inputs usando fundo bg-[#121212] , borda border-[#262626] e foco dourado.

Subtarefa 3.4: Estruturar as classes de layout dos Botões Primários com gradiente from-[#D4AF37] to-[#AA882C] e Secundários em formato macro.

Tarefa 4: Telas Públicas de Login, Cadastro Inicial e Redirecionamentos

Subtarefa 4.1: Desenvolver a view RegisterView baseada em classe herdando de CreateView para permitir o autoregistro público.

Subtarefa 4.2: Configurar a view baseada em classe LoginView nativa do Django apontando para autenticação do e-mail.

Subtarefa 4.3: Escrever o template login.html implementando o formulário unificado preto e dourado conforme o Design System.

Subtarefa 4.4: Configurar propriedade LOGIN_REDIRECT_URL direcionando usuários validados para a URL do Dashboard Principal.

Sprint 2: Core Domain - Módulo de Clientes e Veículos (App customers )

Tarefa 5: Estruturação de Models e Banco de Dados para Clientes

Subtarefa 5.1: Executar criação da app local com python manage.py startapp customers .

Subtarefa 5.2: Registrar a app recém-criada na lista de INSTALLED_APPS nas configurações centrais do sistema.

Subtarefa 5.3: Desenvolver a model Customer contendo campos: name , document_cpf_cnpj (único), phone , email .

Subtarefa 5.4: Incluir os campos obrigatórios de auditoria de data created_at e updated_at na model Customer .

Tarefa 6: Estruturação de Models e Banco de Dados para Veículos

Subtarefa 6.1: Escrever a model Vehicle contendo relacionamento de chave estrangeira ( ForeignKey ) para a model Customer .

Subtarefa 6.2: Adicionar campos na model Vehicle : plate (única), model , brand , color , year , e image_url para guardar a imagem associada.

Subtarefa 6.3: Incluir os campos obrigatórios de auditoria de data created_at e updated_at na model Vehicle .

Subtarefa 6.4: Rodar no terminal os comandos sequenciais de migração estrutural: makemigrations e migrate .

Tarefa 7: Telas de Visualização e Cadastro de Clientes/Veículos (CRUD)

Subtarefa 7.1: Codificar a view baseada em classe CustomerListView e seu respectivo template mestre formatado como tabela dark com detalhes dourados.

Subtarefa 7.2: Codificar a view baseada em classe CustomerCreateView vinculando formulário estilizado de acordo com o design system.

Subtarefa 7.3: Criar interface de detalhes do cliente exibindo a lista de veículos anexados a ele com botão rápido de adição de novo veículo.

Sprint 3: Módulo Cilia - Orçamentos, Peças e Engine de Parse XML (App budgets )

Tarefa 8: Configuração das Models de Orçamentos e Peças

Subtarefa 8.1: Executar criação da app budgets (app de orçamentos) e cadastrar INSTALLED_APPS, models Budget, Piece, etc.

[...]

Sprint 4: Operacional (Ordem de Serviço OS + Kanban, implementado parcial)
Sprint 5: Financeiro Básico (CashMovement, Dashboard Financeiro, implementado parcial)
Sprint 6: Lotes de Tarefas Fase 1 e 2 (lotes, rateio cutoff 17:48, implementado)
Sprint 7: Dashboard Insights Financeiros, Relatórios, Agendamento por Hora

Sprint 8: 🟡 Implementação FUTURA — Assistente Financeiro WhatsApp (RF10 - UAIZAPI / Evolution API)

Objetivo da Sprint: Entregar a integração completa WhatsApp ↔️ CashMovement, permitindo que o financeiro lance entrada/saída por mensagem de texto/áudio no Zap.

Tarefa 1: Definir Provedor WhatsApp (UAIZAPI pago vs Evolution open-source vs Meta Cloud API oficial)
Subtarefa 1.1: Escolher provedor com base em custo x risco de bloqueio.
Subtarefa 1.2: Criar conta no provedor e adquirir/ conectar o número WhatsApp DEDICADO do Financeiro.
Subtarefa 1.3: Obter API TOKEN, SECRET WEBHOOK e URL BASE do provedor escolhido.

Tarefa 2: Criar Modelos novos no banco (models.py budgets ou integrations app dedicada)
Subtarefa 2.1: Model WhatsAppIntegrationConfig: provedor, api_token, webhook_secret, numero_dedicado, active, default_bank_account_id FK, created_at, updated_at.
Subtarefa 2.2: Model WhatsAppWebhookLog: received_at, sender_phone, message_text, audio_transcript, parsed_ok, error_message, cash_movement_id FK (para CashMovement), raw_body JSON, idempotency_key unique_together.
Subtarefa 2.3: Criar migration: makemigrations budgets + migrate.
Subtarefa 2.4: Criar tela de Admin Django / Configuração no painel para o Gerente poder configurar/ligar/desligar a integração, editar tokens, WHITELIST numeros autorizados (salvar em campo JSON em WhatsAppIntegrationConfig.authorized_phones).

Tarefa 3: Criar Webhook Endpoint no Django (budgets/urls.py e budgets/views.py)
Subtarefa 3.1: Rota POST /webhooks/zap/ (ou /api/integrations/whatsapp/webhook) com autenticação token/secret no header ou query string.
Subtarefa 3.2: View WhatsAppWebhookView(View): POST recebe body JSON, valida secret, salva raw_body + remetente + texto em WhatsAppWebhookLog.
Subtarefa 3.3: Validação WHITELIST: sender_phone não está em authorized_phones → salva log com erro bloqueado → envia mensagem resposta de bloqueio e não cria CashMovement → return HTTP 200.
Subtarefa 3.4: Chave idempotência (minute_key + hash texto + remetente) — se já existe WhatsAppWebhookLog com idempotency_key igual → retorna HTTP 200 sem duplicar lançamento.

Tarefa 4: Implementar Parser Inteligente de Texto / Comandos do WhatsApp
Subtarefa 4.1: Helper service parse_whatsapp_command(text: str, sender_phone: str) -> dict: detecta o comando ("/pix", "/cartao", "/dinheiro", "/despesa", "/boleto", "/salario", "/ajuda").
Subtarefa 4.2: Extrai campos do comando: valor Decimal, direcao IN/OUT, bank_account (PIX/CARTÃO/DINHEIRO/BOLETO → busca BankAccount correspondente no banco), budget_id (OS #NNN, busca WorkOrder.budget → budget FK), source (particular/seguradora/empresa), category (busca CashCategory por nome), supplier/fornecedor, due_date (vencimento DD/MM/YYYY).
Subtarefa 4.3: Caso comando = /ajuda → retorna mensagem texto lista de todos comandos.
Subtarefa 4.4: Validação: valor > 0, categoria encontrada, OS # existente (se informado). Campos ausentes → retorna mensagem erro de formato correto.

Tarefa 5: Criar CashMovement e Enviar Resposta Automática WhatsApp
Subtarefa 5.1: Após parse com sucesso: Criar CashMovement.objects.create() com todos campos preenchidos (direction, amount, bank_account, budget, source, category, supplier, description, due_date, launch_date=hoje, is_realized=True padrão recebidos PIX/CARTÃO).
Subtarefa 5.2: Salva cash_movement_id no WhatsAppWebhookLog.
Subtarefa 5.3: Função send_whatsapp_message(phone, text): HTTP POST para endpoint do provedor (UAIZAPI / Evolution / Meta Cloud) envia a resposta de confirmação ✅ "Lançamento #XX criado: Entrada R$500,00 OS#435 — Particular Fulano" ou mensagem de erro ⚠️.
Subtarefa 5.4: Trata resposta de erro do provedor de envio → salva log.

Tarefa 6: Testes Unitários + Django check 0 + Documentação
Subtarefa 6.1: TestCase para parse_whatsapp_command (parser 100% coberto de casos sucesso/erro).
Subtarefa 6.2: TestCase view webhook com token válido / inválido / número fora whitelist / OS inexistente.
Subtarefa 6.3: TestCase CashMovement criado corretamente, idempotência (mesma mensagem 2x não duplica).
Subtarefa 6.4: python manage.py check → 0 issues.
Subtarefa 6.5: Criar documento / ou seção PRD atualizada com passo a passo de como conectar o número, exemplos comandos para enviar no WhatsApp impresso na recepção.

Tarefa 7: Deploy em Produção (PythonAnywhere)
Subtarefa 7.1: Adicionar /webhooks/zap/ em ALLOWED_HOSTS se necessário; CSRF exempt (por ser webhook POST externo sem csrf cookie).
Subtarefa 7.2: Configurar Webhook URL no provedor UAIZAPI apontando para https://SEU_USUARIO.pythonanywhere.com/webhooks/zap/.
Subtarefa 7.3: Testar conexão: enviar /ajuda no WhatsApp pelo número financeiro → receber resposta de lista comandos.
Subtarefa 7.4: Testar lançamento real /pix 1 os 1 particular → CashMovement criado, resposta automática ✅.

Sprint 9: 🟡 Implementação FUTURA — Dashboard Principal + Relatórios (RF09)

Objetivo da Sprint: Retirar o core/dashboard da situação de TELA VAZIA (hoje só tem um h1 após login), concluir os 3 relatórios que faltam implementar (Motivos de Recusa, Produtividade, Funil de Conversão) e garantir que os 4 já implementados (Financeiro, Insights, Peças, Comissões) tenham filtros padrão, impressão A4 correta e exportação CSV.

Prioridades internas da Sprint: Sprint 9.1 é BLOQUEANTE (não tem como usuário logar e ver tela vazia), depois 9.4, 9.5, 9.6 por ordem de impacto operacional e comercial.

Tarefa 1: Dashboard Principal (core/dashboard.html) — Transformar tela vazia em centro de comando da oficina (Módulo RF09.1)
Subtarefa 1.1: Implementar cinturão de 6 KPI cards responsivos (grid md:grid-cols-2 xl:grid-cols-3): Faturamento Mês (verde, % vs mês anterior), Veículos Produção hoje (azul), A Receber aberto 30 dias (amarelo), Atrasados hoje (vermelho badge), Orçamentos aguardando resposta (dourado com link), e Card Pessoal de Comissão do mês (só aparece se role != MANAGER/FINANCE).
Subtarefa 1.2: Implementar bloco ESQUERDA "Pátio Hoje" — mini Kanban horizontal com top 5 tarefas EM ANDAMENTO de hoje: card com foto veículo, nome colaborador, timer em tempo real (JS atualiza a cada 30s), coluna kanban atual. Link no rodapé "Abrir Kanban completo".
Subtarefa 1.3: Implementar bloco DIREITA "Lançamentos a vencer (7 dias)" — 5 linhas bulleted: CashMovement due_date entre hoje e hoje+7, ordenados por due_date. Badge Atrasado (vermelho) automático para quem já venceu.
Subtarefa 1.4: Implementar rodapé com 6 cards de Atalhos Rápidos (ícone + texto + href): Novo Orçamento, Importar XML Cilia, Nova OS, Novo Lançamento, Kanban Hoje, Relatório Comissões.
Subtarefa 1.5: Diferenciação por ROLE: Se role=VISUAL oculta Financeiro; se role=ORCAMENTISTA oculta KPIs de caixa detalhado e mostra orçamentos; se role=OPERACIONAL mostra SOMENTE cartão de comissão pessoal + mini kanban da suas tarefas + atalhos Kanban/Comissões.

Tarefa 2: Concluir Dashboard Financeiro Insights (finance_insights.html) — Gráficos 3 e 4 que faltam + exportações
Subtarefa 2.1: Garantir responsividade e legenda visível em todos 4 gráficos Chart.js (mobile / desktop / impressão).
Subtarefa 2.2: Adicionar Gráfico 4 ("Origem das Entradas" Pie Chart, já documentado em RF09.2) com dados reais agrupados por CashCategory.source (Particular / Seguradora / Outros).
Subtarefa 2.3: Implementar exportação PNG em cada card de gráfico: botão 📤 "Baixar PNG" com canvas.toDataURL() e download automático do nome do gráfico + data.
Subtarefa 2.4: Adicionar painel inferior "Ranking de Clientes (R$ faturados no período)" — tabela Top 10 clientes ordenada desc por soma de CashMovement IN realizados no período, colunas Posição, Cliente, Qtd OS concluídas, Valor total, % do faturamento.
Subtarefa 2.5: Implementar botão 📥 "Exportar CSV" no rodapé dos lançamentos financeiros (finance_dashboard.html já existente) — gera CSV com encoding UTF-8 BOM, separador ;, compatível Excel Brasil.

Tarefa 3: Ajustar relatórios JÁ IMPLEMENTADOS para seguir o Padrão de Filtros RF09 (backward compat)
Subtarefa 3.1: Relatório de Peças (report_pieces.html): adicionar também "Data De / Data Até" (hoje só tem 1 data referência). Adicionar filtro por Fornecedor (Cliente / Seguradora / Oficina / Todos). Manter compatibilidade com impressão A4 landscape.
Subtarefa 3.2: Relatório de Comissões (commission_open_list.html): adicionar botão 📥 "Exportar CSV". Garantir no backend a regra de segurança OPERACIONAL/VISUAL só enxerga as próprias (testar com usuário de role VISUAL para confirmar que colaborador alheio NÃO aparece).
Subtarefa 3.3: Nos 2 relatórios (Peças + Comissões), adicionar atalhos rápidos de período: Hoje · Semana · Mês · Últimos 30 dias · Últimos 90 dias (alinhado com RF09 padrões UX item 1).
Subtarefa 3.4: Garantir em templates base de relatório: cores dos botões Filtrar (primário dourado) / Limpar (cinza borda) / Imprimir (secundário) / CSV (secundário azul) consistentes com Design System Premium Dark Pattern.

Tarefa 4: Implementar Relatório de Motivos de Recusa de Orçamentos (RF09.5)
Subtarefa 4.1: Criar URL `budgets:report_refusals` (GET) e view `BudgetRefusalReportView` (Role: MANAGER, ORCAMENTISTA).
Subtarefa 4.2: Adicionar campo `Budget.refusal_category` (choices: HIGH_PRICE, COMPETITOR, NO_BUDGET, INSURER_DENIED, LONG_DEADLINE, OTHER) + `refusal_reason_obs` (campo texto livre já existe hoje, hoje em refusal_reason). Criar migration para mapear refusal_category automaticamente por palavras-chave na importação / reclassificação manual.
Subtarefa 4.3: Implementar cinturão de 4 KPIs: Total orçamentos período, % Autorizadas, % Não aprovadas, % Aguardando.
Subtarefa 4.4: Implementar Chart.js Bar "Top 5 Motivos de Recusa por Volume" (ordenado decrescente).
Subtarefa 4.5: Implementar tabela detalhada colunas: Orçamento #, Cliente, Veículo, Valor total R$, Data visita, Motivo agrupado (categoria), Observação (justificativa), Orçamentista.
Subtarefa 4.6: Filtros da tela (Data De / Data Até / Orçamentista (todos ou específico)).
Subtarefa 4.7: Botão "Exportar CSV".

Tarefa 5: Implementar Relatório de Produtividade por Colaborador (RF09.6) — MAIS CRÍTICO do pátio
Subtarefa 5.1: Criar view `ProductivityReportView` e URL `budgets:report_productivity` (roles: MANAGER, FINANCE, ORCAMENTISTA).
Subtarefa 5.2: Filtros: Data De · Data Até · Colaborador (todos / 1 específico) · Coluna Kanban (atividade específica: Desmontagem / Funilaria / Pintura / etc).
Subtarefa 5.3: Implementar cinturão KPIs: Total tarefas concluídas, HH programado total, HH real total (soma play/pause WorkOrderTask.total_elapsed_seconds convertido em horas), Índice Eficiência % (HH programado / HH real × 100) com cores semáforo (verde ≥ 90, amarelo 75-90, vermelho < 75).
Subtarefa 5.4: REGRA OBRIGATÓRIA: Filtrar `Collaborator.objects.filter(is_active=True)` por padrão no backend — colaboradores inativos (prestadores fora de serviço) NÃO aparecem. Adicionar checkbox "Incluir inativos" (só aparece para MANAGER / FINANCE) que tira o filtro. Garante o requisito do usuário: "prestador inativo não entra no rateio de HH disponível".
Subtarefa 5.5: Gráfico de linhas (Chart.js Line): Eficiência por dia do período — 1 linha azul = eficiência diária; linha cinza tracejada = 100% de referência.
Subtarefa 5.6: Tabela ranking (por colaborador, ordenado decrescente por Eficiência %): Posição (🥇🥈🥉 para top 3), Nome colaborador, # tarefas, HH programado, HH real, HH diferença (horas saldo positivo/negativo), Eficiência % (cor semáforo). Rows colaboradores inativos ficam opacity-60 quando "Incluir inativos" estiver marcado.
Subtarefa 5.7: Botão Imprimir (A4 landscape) + Exportar CSV.

Tarefa 6: Implementar Relatório Funil de Conversão Orçamento → OS → Entrega (RF09.7)
Subtarefa 6.1: View `ConversionFunnelReportView` + URL `budgets:report_funnel` (roles: MANAGER, ORCAMENTISTA).
Subtarefa 6.2: Filtros: Data De / Data Até / Orçamentista (todos / específico).
Subtarefa 6.3: Implementar cálculo das 6 etapas (100% orçamentos criados → autorizadas → geraram OS → iniciadas → concluídas → entregues no prazo). Prazo contratada = Budget.delivered_at estimado vs WorkOrder.completed_at real; se concluídas ≤ data contratada = Conta como "Entregue no prazo".
Subtarefa 6.4: Gráfico de Funil (Chart.js barras horizontais decrescentes, uma por etapa). Cada barra tem R$ e quantidade + % da etapa anterior.
Subtarefa 6.5: Tabela de resumo por Mês do período (linhas = meses, colunas = 6 etapas em % com cores semáforo: >80% verde, 60-80% amarelo, <60% vermelho).
Subtarefa 6.6: Exportar CSV.

Tarefa 7: Testes Unitários + Validação + Documentação
Subtarefa 7.1: TestCase Dashboard Principal (core/dashboard): superuser vê todos 6 cards; role VISUAL enxerga SOMENTE seu card de comissão + kanban resumido (não vê caixa).
Subtarefa 7.2: TestCase Produtividade: colaborador is_active=False NÃO aparece por padrão no queryset inicial; marca "Incluir inativos" → sim aparece.
Subtarefa 7.3: TestCase Comissões Isolamento: user com role OPERACIONAL (e colaborador.id=5) faz GET na página → tabela contém APENAS comissões com workordertask.assigned_collaborator_id=5 (não de colegas).
Subtarefa 7.4: TestCase Relatório Recusas: filtrar por período com 0 orçamentos → cinturão zeros, tabela "sem registros" sem crashar.
Subtarefa 7.5: `python manage.py check` → 0 issues. `makemigrations --check` → No changes detected.
Subtarefa 7.6: Atualizar Menu Lateral (sidebar no base.html) para incluir links: Dashboard · Financeiro · Kanban Hoje · OS · Orçamentos · Clientes · Cadastros · Relatórios dropdown com os 4 relatórios: Comissões · Peças · Motivos Recusa · Produtividade · Funil Conversão.

Tarefa 8: Deploy em Produção (PythonAnywhere)
Subtarefa 8.1: Git pull + migrate + collectstatic + touch wsgi.
Subtarefa 8.2: Testar Dashboard Principal (cinturão KPI) com usuário Gerente, Orçamentista, Operacional (3 papéis) para confirmar diferenciação de conteúdo por role.
Subtarefa 8.3: Testar Impressão (Ctrl+P / botão Imprimir) em todos 4 relatórios (Comissões, Peças, Recusas, Produtividade) para confirmar layout A4, fundo branco, margens.
Subtarefa 8.4: Validar CSV exportando 1 relatório grande (100+ linhas) — abrir no Excel Brasil, confirmar separador ; e acentos PT-BR corretos (UTF-8 BOM).
