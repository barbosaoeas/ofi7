

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

Indicadores visuais rápidos de faturamento mensal, veículos na oficina por status e contas a pagar/receber da semana.

Relatório de Comissões: Filtro por período de datas e por Funcionário, detalhando tarefas concluídas e valores a pagar.

Relatório de Motivos de Recusa de Orçamentos para análise de conversão comercial.

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

10. Métricas de Sucesso & KPIs

KPI de Produto : Tempo médio de conversão entre a importação do XML Cilia e a abertura real da Ordem de Serviço (Alvo: < 15 minutos).

KPI Operacional : Eficiência de Pátio (Diferença entre o tempo programado na O.S. e o tempo real consolidado pelo contador de play/pause do Kanban).

KPI de Negócio/Financeiro : Índice de Recusa de Orçamentos (Mapeamento percentual volumétrico dos motivos informados no cancelamento).

11. Riscos e Mitigações

Risco : Divergência estrutural nas tags ou atualizações de layout do arquivo XML emitido pelo Cilia.

Mitigação : Isolar o mecanismo de parse dentro de um helper service encapsulado com tratamento genérico de exceções ( try/except ), registrando logs detalhados sem derrubar a aplicação.

Risco : O colaborador esquecer uma tarefa rodando eternamente no "Play" ao ir embora da oficina.

Mitigação : Implementar uma rotina simples na view do dashboard do gestor para forçar o "Pause" ou encerramento manual de tarefas ativas por parte da gerência.

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

Subtarefa 8.1: Executar criação da app loc
