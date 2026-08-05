# PRD - Automacao de Importacao de Orcamentos Cilia

## 1. Objetivo

Automatizar a entrada dos XMLs gerados no Cilia no sistema da oficina, eliminando a importacao manual por upload e reaproveitando o parser e as regras de negocio ja existentes no projeto Django.

## 2. Escopo Inicial

- Sem uso de IA nesta fase.
- Sem webhook na primeira entrega.
- Sem atualizacao inteligente de revisoes do mesmo orcamento.
- Sem notificacoes externas.

## 3. Fluxo Proposto

1. O orcamento e concluido no Cilia.
2. O XML e salvo automaticamente em uma pasta padrao do Dropbox.
3. Uma tarefa agendada consulta periodicamente essa pasta.
4. Cada novo XML vira um job de importacao.
5. O sistema baixa o arquivo, calcula hash e valida duplicidade.
6. O mesmo servico de importacao usado pela tela manual processa o XML.
7. O sistema cria ou atualiza os dados internos permitidos.
8. O job recebe status final e fica disponivel para auditoria.
9. O arquivo e movido no Dropbox para uma pasta de processados ou erro.

## 4. Arquitetura Tecnica

### 4.1 Componentes

- `budgets.services.cilia_import_service`: servico central de parse e persistencia.
- `XMLImportJob`: tabela de rastreabilidade para arquivos importados.
- `sync_cilia_dropbox`: comando agendado para buscar novos XMLs no Dropbox.
- Tela de monitoramento de importacoes: sera entregue apos a Sprint 1.

### 4.2 Principios

- Reaproveitar a regra ja validada da importacao manual.
- Garantir idempotencia por `cilia_number`, `file_hash` e `external_file_id`.
- Manter XML bruto salvo para auditoria.
- Isolar erros por arquivo sem interromper os demais.

## 5. Requisitos Funcionais

- O sistema deve consultar automaticamente uma pasta especifica do Dropbox.
- O sistema deve identificar novos arquivos XML ainda nao processados.
- O sistema deve registrar um job para cada arquivo detectado.
- O sistema deve importar o XML usando o mesmo parser atual do projeto.
- O sistema deve impedir duplicidade de importacao.
- O sistema deve registrar status `PENDING`, `PROCESSING`, `IMPORTED`, `DUPLICATE` e `ERROR`.
- O sistema deve guardar mensagem de erro e XML bruto para auditoria.
- O sistema deve permitir reprocessamento manual em fase posterior.

## 6. Regras de Negocio

- No MVP, se o `cilia_number` ja existir, o arquivo sera marcado como duplicado.
- O XML original nao deve ser descartado apos o processamento.
- A importacao automatica deve respeitar as mesmas validacoes da importacao manual.
- Em caso de falha, o job deve registrar claramente o motivo.
- Em caso de XML sem CPF/CNPJ, o sistema pode seguir com cadastro temporario, como ja ocorre hoje.

## 7. Modelo Inicial de Dados

### 7.1 XMLImportJob

- `provider`
- `external_file_id`
- `file_name`
- `file_hash`
- `cilia_number`
- `status`
- `error_message`
- `raw_xml`
- `budget`
- `detected_at`
- `processed_at`
- `updated_at`

## 8. Plano por Sprints

### Sprint 1

- Criar `XMLImportJob`.
- Extrair a logica da importacao manual para um servico reutilizavel.
- Fazer a tela manual usar esse servico.
- Criar testes focados da importacao.

### Sprint 2

- Integrar com Dropbox API.
- Criar comando `sync_cilia_dropbox`.
- Processar arquivos novos automaticamente.

### Sprint 3

- Criar tela de monitoramento dos jobs.
- Adicionar filtros por status.
- Permitir reprocessamento manual.

### Sprint 4

- Evoluir a politica de revisoes do mesmo orcamento.
- Adicionar notificacoes internas.
- Endurecer observabilidade e operacao.

## 9. Fora de Escopo por Enquanto

- Analise por IA.
- Resumo automatico com OpenAI.
- Sugestoes inteligentes de conciliacao.
- Webhook do Dropbox.
- Atualizacao automatica de orcamentos ja iniciados no operacional.

## 10. Definicao de Pronto do MVP

- Upload manual continua funcionando.
- Importacao manual passa a gerar rastreabilidade em job.
- Existe um servico reutilizavel para importacao por bytes.
- O projeto fica pronto para plugar o Dropbox sem duplicar regras.
