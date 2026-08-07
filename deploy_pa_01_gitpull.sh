#!/bin/bash
# ============================================================================
#  DEPLOY PYTHONANYWHERE - PASSO 01 (rodar no BASH do PA)
#  COMPATIVEL 100% COM PYTHONANYWHERE (Ubuntu Linux Bash Padrao)
#
#  ANTES DE RODAR A PRIMEIRA VEZ: AJUSTE ABAIXO 2 LINHAS (CONFIG):
#    1. PROJECT_DIR = onde esta o seu projeto (geralmente ~/ofi7 ou ~/ofi7)
#    2. VENV_PATH   = caminho do activate da sua virtualenv
#       * DICA: No PythonAnywhere, Aba WEB -> "Virtualenv:" mostra o caminho
#               Ex: /home/SEU_USUARIO/.virtualenvs/NOME_DA_VENV/bin/activate
# ============================================================================

# ============================================================
#  [CONFIG] EDITE AQUI SE PRECISAR (1 vez so!)
# ============================================================
PROJECT_DIR="$HOME/ofi7"
VENV_PATH="$HOME/.virtualenvs/venv/bin/activate"
# ============================================================

set -e
echo ""
echo "========================================================="
echo "   DEPLOY OFICINA 7 - PYTHONANYWHERE (PASSO 1: GIT PULL)"
echo "========================================================="
echo "CONFIG usada:"
echo "  - PROJECT_DIR: $PROJECT_DIR"
echo "  - VENV_PATH:   $VENV_PATH"
echo "---------------------------------------------------------"
START=$(date +%s)

# 1) Garante que estamos no diretorio do app
if [ ! -d "$PROJECT_DIR" ]; then
  echo ""
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo "  ERRO: PROJECT_DIR NAO EXISTE -> $PROJECT_DIR"
  echo "  Verifique editar as 2 linhas de CONFIG no topo deste script."
  echo "  Lista do seu HOME ($HOME):"
  ls -la "$HOME"
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  exit 1
fi
cd "$PROJECT_DIR" || { echo "ERRO: nao consegui entrar em $PROJECT_DIR"; exit 1; }
echo "[OK] Diretorio projeto: $(pwd)"
echo ""

# 2) Ativa virtualenv
if [ -f "$VENV_PATH" ]; then
  # shellcheck disable=SC1090
  source "$VENV_PATH"
  echo "[OK] venv ativada: $(which python)"
else
  echo ""
  echo "[AVISO] VENV_PATH NAO ENCONTRADA: $VENV_PATH"
  echo ""
  echo "  DICA - Virtualenvs disponiveis em ~/.virtualenvs:"
  if [ -d "$HOME/.virtualenvs" ]; then
    ls -la "$HOME/.virtualenvs" | head -n 20
  else
    echo "  (pasta ~/.virtualenvs nao existe - provavelmente sua venv esta em outro lugar)"
  fi
  echo ""
  echo "  CONTINUANDO usando python do PATH... (se falhar, edite VENV_PATH no topo)"
  echo "---------------------------------------------------------"
fi
echo ""

# 3) Git status antes
echo ""
echo "----- 1. GIT STATUS antes do pull -----"
git -c color.ui=always status -sb
echo ""

# 4) Git pull origin main
echo "----- 2. GIT PULL ORIGIN MAIN -----"
git pull origin main
echo ""
echo "Ultimo commit aplicado:"
git -c color.ui=always log --oneline -1
echo ""

# 5) Django check
echo "----- 3. DJANGO CHECK (esperado: 0 issues) -----"
python manage.py check || {
  echo ""
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo "  ERRO: Django check encontrou problemas! Veja acima."
  echo "  NAO recarregue o Web App ate corrigir."
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  exit 2
}
echo "[OK] Django check: sem erros."
echo ""

# 6) Verifica migrations NÃO EXISTEM (esperado 0 novas)
echo "----- 4. MAKEMIGRATIONS CHECK (esperado: No changes detected) -----"
set +e
MIGS_OUT=$(python manage.py makemigrations budgets --check --dry-run 2>&1)
MIGS_RC=$?
set -e
if [ "$MIGS_RC" -eq 0 ]; then
  echo "$MIGS_OUT"
  echo "[OK] Nenhuma migration nova detectada. NAO precisa rodar migrate."
else
  echo ""
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo "  AVISO: Detectou migrations novas (exit=$MIGS_RC)."
  echo "  Conteudo: $MIGS_OUT"
  echo ""
  echo "  Para aplicar as migrations, DESCOMENTE as linhas abaixo e re-rode:"
  echo "    python manage.py migrate"
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  # python manage.py migrate || { echo "ERRO NO MIGRATE"; exit 3; }
fi
echo ""

# 7) Teste rapido (1 teste)
echo "----- 5. TESTE RAPIDO: budgets.tests.TimeCappingTests (1 teste) -----"
set +e
TESTS_OUT=$(python manage.py test budgets.tests.TimeCappingTests -v 1 2>&1)
TESTS_RC=$?
set -e
echo "$TESTS_OUT"
if [ "$TESTS_RC" -eq 0 ]; then
  echo "[OK] Testes: passou (1/1)."
else
  echo ""
  echo "[AVISO] 1 teste falhou (rc=$TESTS_RC). Ver log acima."
  echo "  Nao recarregue o web app se esse teste for importante."
fi
echo ""

# FIM
END=$(date +%s)
ELAPSED=$(( END - START ))
echo "========================================================="
echo "  FIM DO SCRIPT. Tempo total: ${ELAPSED}s"
echo "========================================================="
echo ""
echo " PROXIMO PASSO (MANUAL):"
echo "   1. Aba WEB no PythonAnywhere: https://www.pythonanywhere.com/user/$USER/webapps/"
echo "   2. Clique no botao VERDE  [ Reload ofi7.pythonanywhere.com ]"
echo "   3. Teste no navegador: /desempenho/  /os/  /kanban-today/"
echo ""
