#!/bin/bash
# Wrapper seguro para a tarefa agendada do PythonAnywhere (hora em hora).
# Como usar no painel Tasks:
#   /home/ofi7ipojuca/ofi7/scripts/run_sync_cilia_dropbox.sh
#
# - Nao depende de "bash -lc" com multiplos && e aspas (evita parsing errado).
# - Escreve log com timestamp em ~/ofi7/logs/sync_cilia_dropbox.log
# - Faz automaticamente "roll" do log se ficar > 2MB (mantem 1 copia antiga).

set -u

PROJ_DIR="/home/ofi7ipojuca/ofi7"
VENV_PYTHON="/home/ofi7ipojuca/.virtualenvs/venv/bin/python"
ENV_FILE="/home/ofi7ipojuca/.secrets/oficina_env.sh"
LOG_DIR="${PROJ_DIR}/logs"
LOG_FILE="${LOG_DIR}/sync_cilia_dropbox.log"

mkdir -p "${LOG_DIR}"

if [ -f "${LOG_FILE}" ]; then
    LOG_SIZE=$(stat -c%s "${LOG_FILE}" 2>/dev/null || echo 0)
    if [ "${LOG_SIZE}" -gt 2097152 ]; then
        mv -f "${LOG_FILE}" "${LOG_FILE}.old"
    fi
fi

echo "" >> "${LOG_FILE}"
echo "============================================================" >> "${LOG_FILE}"
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Iniciando sync_cilia_dropbox" >> "${LOG_FILE}"
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

echo "DROPBOX_CILIA_ENABLED = ${DROPBOX_CILIA_ENABLED:-<vazio>}" >> "${LOG_FILE}"
echo "DROPBOX_APP_KEY       = $(if [ -n "${DROPBOX_APP_KEY:-}" ]; then echo "CONFIGURADO (${#DROPBOX_APP_KEY} chars)"; else echo "AUSENTE"; fi)" >> "${LOG_FILE}"
echo "DROPBOX_REFRESH_TOKEN = $(if [ -n "${DROPBOX_REFRESH_TOKEN:-}" ]; then echo "CONFIGURADO (${#DROPBOX_REFRESH_TOKEN} chars)"; else echo "AUSENTE"; fi)" >> "${LOG_FILE}"

"${VENV_PYTHON}" manage.py sync_cilia_dropbox >> "${LOG_FILE}" 2>&1
SYNC_EXIT=$?

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] sync_cilia_dropbox finalizado. exit_code=${SYNC_EXIT}" >> "${LOG_FILE}"
exit "${SYNC_EXIT}"
