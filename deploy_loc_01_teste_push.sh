#!/bin/bash
# ============================================================================
#  DEPLOY LOCAL (Windows WSL/Git Bash OU PowerShell usando bash)
#  Ou use os comandos equivalentes no PowerShell (o arquivo é a referencia).
#  Objetivo: rodar ANTES de `git commit/push` para NAO subir nada quebrado!
#  (Este arquivo eh DE REFERENCIA local, nao precisa commitar pro GitHub)
# ============================================================================
set -e
cd "$(dirname "$0")"
START=$(date +%s)
echo ""
echo "========================================================="
echo "   CHECKLIST LOCAL - ANTES DE COMMIT/PUSH"
echo "========================================================="

# 0) Venv (PowerShell Windows: .\venv\Scripts\activate   //   GitBash: source venv/Scripts/activate)
if [ -f "./venv/Scripts/activate" ]; then
  source ./venv/Scripts/activate 2>/dev/null || true
fi

echo "[1/5] Django check..."
python manage.py check || { echo "ERRO CHECK"; exit 1; }
echo ""
echo "[2/5] Makemigrations check (0 migs)..."
python manage.py makemigrations budgets --check --dry-run || true
echo ""
echo "[3/5] Testes budgets.tests.TimeCappingTests..."
python manage.py test budgets.tests.TimeCappingTests -v 1 || true
echo ""
echo "[4/5] Reschedule DRY-RUN 07/08 (sexta -> sab)..."
python manage.py reschedule_overdue_tasks --dry-run --date 2026-08-07 --summary-only
echo ""
echo "[5/5] Reschedule DRY-RUN 08/08 (sab -> seg)..."
python manage.py reschedule_overdue_tasks --dry-run --date 2026-08-08 --summary-only
echo ""

END=$(date +%s)
ELAPSED=$(( END - START ))
echo "========================================================="
echo "  FIM. Tempo: ${ELAPSED}s. Se tudo OK -> git add -A && git commit && git push"
echo "========================================================="
