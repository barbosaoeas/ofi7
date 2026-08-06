# Plano: Modal obrigatório ao aprovar orçamento + Tipo de Cliente no orçamento + Resultado por Tipo no Financeiro

## Diagnóstico dos pedidos do usuário

**Pedido A:** "ao aprovar o orcamento o modal deveria abrir."
Conclusão do código atual:
- O `form_valid` de BudgetUpdateView já redireciona para `?finance=1` ao salvar quando status=AUTHORIZED e não há CashMovement.
- Entretanto, existem 2 cenários em que o modal NÃO abre:
  1. Usuário clica em "Pular" ou "Voltar" no modal (discard_finance=1) e depois volta na página (não há ?finance=1).
  2. Orçamento já estava AUTHORIZED e usuário só atualiza datas/salva de novo. Hoje o redirect com ?finance=1 só roda se o status NÃO tiver CashMovement E role permitida. O que a gente quer é: **se aprovou e não tem financeiro, SEMPRE abrir o modal**, inclusive ao visitar a tela de novo.
  3. No get_context_data, show_finance_modal precisa virar True automaticamente quando status=AUTHORIZED e não tem financeiro (mesmo sem o parâmetro ?finance=1).

**Pedido B:** "criar um campo de tipo cliente, seguro, particular ou empresa. para podermos ver no financeiro quem esta dando mais resultado por mes"
Conclusão:
- Criar campo `Budget.customer_type` com opções: PARTICULAR / SEGURADORA / EMPRESA.
- **Copiar** esse valor para os `CashMovement` criados via BudgetFinanceCreateView, para poder agrupar no financeiro.
- Adicionar seção de resultado/agrupamento no dashboard financeiro (ou na tela de insights existente) com filtro por mês e total por tipo.
- O campo também deve aparecer no modal de financeiro (seletor Tipo) e pré-selecionar automaticamente:
  - Se kind = SEGURADORA → customer_type = SEGURADORA
  - Se kind = PARTICULAR → customer_type = PARTICULAR
  - Usuário pode trocar se quiser (ex: uma empresa que paga direto → EMPRESA).

---

## Etapas

### Etapa 1 — Garantir abertura do modal SEMPRE ao aprovar (sem depender de ?finance=1)
**Arquivos:** `budgets/views.py`, `templates/budgets/budget_form.html`

Mudanças:
1. Em `get_context_data`, mudar a regra de `show_finance_modal`:
   - Se status=AUTHORIZED AND needs_finance=True AND (não tem CashMovement) → abrir modal automaticamente, mesmo sem parâmetro `?finance=1`.
   - O parâmetro `?finance=1` continua servindo para **forçar** abertura (e não fecha o trigger automático).
2. Garantir que no `get` também seja exibido.

Risco: Usuário não consegue acessar a tela de edição sem abrir o modal. Mitigação:
- Deixar o botão "Pular" e "Voltar" existirem (já existem).
- Incluir um check no contexto: se `discard_finance=1` foi clicado, NÃO abrir automaticamente até o próximo POST. (pode ser salvo numa flag de sessão tipo `_skip_finance_modal_{id}`)

### Etapa 2 — Criar campo `customer_type` em Budget + CashMovement
**Arquivos:** `budgets/models.py`, criar migration nova, `budgets/views.py` (BudgetFinanceCreateView), `templates/budgets/budget_form.html`

Budget.CustomerType choices:
- PARTICULAR
- SEGURADORA
- EMPRESA

CashMovement.customer_type (mesmas choices, permite nulo por causa de lançamentos não orçamentários).

### Etapa 3 — Integrar o tipo cliente no modal de aprovação / financeiro
**Arquivos:** `templates/budgets/budget_form.html`, `budgets/views.py` (BudgetUpdateView.get_context_data, BudgetFinanceCreateView.post)

Mudanças:
- No modal, adicionar campo "Tipo cliente" acima/abaixo do "Tipo".
- Quando o usuário alterar o select "Tipo" (Particular / Seguradora):
  - mudar automaticamente o "Tipo cliente" default correspondente (particular → PARTICULAR, seguradora → SEGURADORA)
  - Usuário pode trocar para EMPRESA depois, se quiser.
- Ao salvar o financeiro, gravar `budget.customer_type` do modal e também **propagar** para todos os CashMovement criados.
- No orçamento, quando já tiver tipo, mostrar no formulário principal de edição também (para edição direta, sem precisar do modal).

### Etapa 4 — Tela Financeira: resultado por tipo no mês
**Arquivos:** `budgets/views.py` (FinanceDashboard ou insights), `templates/budgets/finance_dashboard.html` ou `finance_insights.html`

Mudanças:
- Adicionar cards/tabela com:
  - Período (mês padrão = mês atual, com select para mudar)
  - Total recebido / em aberto por TIPO (Particular / Seguradora / Empresa / Não classificado)
  - Gráfico simples (barras) ou tabela com
    - Quantidade de orçamentos
    - Valor total previsto
    - Valor recebido
    - Valor em aberto

### Etapa 5 — Testes e deploy
- Rodar migrate local, manage.py check, core.tests, subir para PythonAnywhere.

---

## Arquivos a serem alterados

| Arquivo | O que muda |
|---|---|
| budgets/models.py | Adiciona CustomerType em Budget e CashMovement |
| budgets/migrations/NEW | Migration dos campos |
| budgets/views.py | (A) show_finance_modal agora abre automaticamente, (B) BudgetFinanceCreateView salva customer_type e propaga para CashMovement |
| templates/budgets/budget_form.html | (A) modal abre, (B) novo campo Tipo Cliente no modal + edição principal |
| templates/budgets/finance_dashboard.html (ou insights) | Cards + tabela de resultado por tipo por mês |
| budgets/services/ (opcional, se criar serviço para agrupamento) | |

---

## Riscos

| Risco | Mitigação |
|---|---|
| Modal abrir sempre incomoda o usuário | Flag `_skip_finance_modal_{id}` por sessão ao clicar em "Pular". Só fecha até próximo POST de aprovação. |
| Orçamento velho não tem customer_type | Campo com default = null / opcional; relatório mostra "Não classificado" |
| CashMovement de recebimento manual não tem tipo | Pode adicionar o campo também em telas de CashMovement manual (opcional, não é MVP) |

---

## MVP vs opcional

**MVP (entrega 1 sprint):** Etapas 1, 2, 3 (modal obrigatório + campo no orçamento + propagação para CashMovement).  
**Opcional próxima sprint:** Etapa 4 (dashboard de resultado por tipo por mês).
