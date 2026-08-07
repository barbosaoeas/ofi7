#!/bin/bash
# Wrapper seguro para a tarefa agendada de reschedule de tarefas atrasadas
# (dispara diariamente apos o fim do expediente).
# Como usar no painel Tasks do PythonAnywhere:
#   /home/ofi7ipojuca/ofi7/scripts/run_reschedule_overdue.sh
#
# Horario recomendado no painel Tasks (UTC): 20:50 UTC = 17:50 BRT
#   (2 minutos apos o cutoff KANBAN_CUTOFF_TIME = 17:48 BRT).

set -u

PROJ_DIR="/home/ofi7ipojuca/ofi7"
VENV_PYTHON="/home/ofi7ipojuca/.virtualenvs/venv/bin/python"
ENV_FILE="/home/ofi7ipojuca/.secrets/oficina_env.sh"
LOG_DIR="${PROJ_DIR}/logs"
LOG_FILE="${LOG_DIR}/reschedule_overdue.log"

mkdir -p "${LOG_DIR}"

if [ -f "${LOG_FILE}" ]; then
    LOG_SIZE=$(stat -c%s "${LOG_FILE}" 2>/dev/null || echo 0)
    if [ "${LOG_SIZE}" -gt 2097152 ]; then
        mv -f "${LOG_FILE}" "${LOG_FILE}.old"
    fi
fi

echo "" >> "${LOG_FILE}"
echo "============================================================" >> "${LOG_FILE}"
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Iniciando reschedule_overdue_tasks" >> "${LOG_FILE}"
echo "============================================================" >> "${LOG_FILE}"

if [ ! -f "${ENV_FILE}" ]; then
    echo "ERRO: Arquivo de segredos nao encontrado: ${ENV_FILE}" >> "${LOG_FILE}"
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

cd "${PROJ_DIR}" || {
    echo "ERRO: Nao foi possivel entrar em ${PROJ_DIR}" >> "${LOG_FILE}"
    exit 2
}

"${VENV_PYTHON}" manage.py reschedule_overdue_tasks >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] reschedule_overdue_tasks finalizado. exit_code=${EXIT_CODE}" >> "${LOG_FILE}"
exit "${EXIT_CODE}"
