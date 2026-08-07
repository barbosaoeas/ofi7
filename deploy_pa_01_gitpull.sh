#!/bin/bash
# ============================================================================
#  DEPLOY PYTHONANYWHERE - PASSO 01 (rodar no BASH do PA, dentro de ~/ofi7)
#  Objetivo: 1 clique para:
#    1. Ativar venv
#    2. git pull origin main
#    3. Django check (0 erros?)
#    4. makemigrations --check (esperado: 0 migrations novas!)
#    5. Roda 1 teste rápido do TimeCapping
#  Depois, manualmente: clique no botao VERDE "Reload" na WEB TAB do PA.
# ============================================================================
set -e
echo ""
echo "========================================================="
echo "   DEPLOY OFICINA 7 - PYTHONANYWHERE (PASSO 1: GIT PULL)"
echo "========================================================="
START=$(date +%s)

# 1) Garante que estamos no diretorio do app
cd "$HOME/ofi7" || { echo "ERRO: diretorio ~/ofi7 nao encontrado! Abortando."; exit 1; }
echo "[OK] Diretorio: $(pwd)"

# 2) Ativa virtualenv (PythonAnywhere padrao)
VENV_PATH="$HOME/.virtualenvs/venv/bin/activate"
if [ -f "$VENV_PATH" ]; then
  # shellcheck disable=SC1090
  source "$VENV_PATH"
  echo "[OK] venv ativada: $(which python)"
else
  echo "[AVISO] venv padrão $VENV_PATH NAO ENCONTRADA. Tentando usar python do PATH..."
fi

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
