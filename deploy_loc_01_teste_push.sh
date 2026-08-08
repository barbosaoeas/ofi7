#!/bin/bash
# ============================================================================
#  CHECKLIST LOCAL (Windows WSL/Git Bash OU PowerShell usando bash)
#  Ou use os comandos equivalentes no PowerShell (o arquivo e' a referencia).
#  Objetivo: rodar ANTES de voce MANUALMENTE fazer `git commit/push`
#            para NAO subir nada quebrado!
#
#  IMPORTANTE (regras 2026-08-08):
#   ------------------------------------------------------------------
#   ESTE SCRIPT NAO FAZ `git add`, NAO FAZ `git commit`, NAO FAZ `git push`.
#   ELE SO RODA OS CHECKS ABAIXO.
#   A SUBIDA PARA O GIT SO ACONTECE QUANDO VOCE PEDIR (MANUALMENTE).
#   ------------------------------------------------------------------
#
#  (Este arquivo eh DE REFERENCIA local, nao precisa commitar pro GitHub)
# ============================================================================
set -e
cd "$(dirname "$0")"
START=$(date +%s)
echo ""
echo "========================================================="
echo "   CHECKLIST LOCAL - ANTES DE COMMIT/PUSH (MANUAL)"
echo "   (NAO envia NADA ao git automaticamente)"
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
echo "  FIM. Tempo: ${ELAPSED}s."
echo "========================================================="
echo ""
echo " STATUS DOS CHECKS: ACIMA (deu erro em algum? corrige antes de subir)"
echo ""
echo " ====> SUBIDA PRO GIT EH 100% MANUAL (voce pede quando precisar):"
echo "   1) Revise o que vai subir:  git status -sb"
echo "   2) Se OK: git add -A"
echo "   3) Commit:      git commit -m \"<descreva o que mudou>\""
echo "   4) Subir:       git push origin main"
echo ""
echo " ====> DEPOIS que o push subir, va para o PythonAnywhere e rode:"
echo "        bash deploy_pa_01_gitpull.sh   (isso DA PULL no servidor e roda checks + migrate)"
echo "        Depois clique em [Reload] no painel WEB do PA."
echo ""
