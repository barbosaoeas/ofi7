# Plano: Renovar token do Dropbox expirado e salvar sem erro de quebra de linha

## Diagnóstico

Erro retornado pelo sync_cilia_dropbox:
```
CommandError: Não foi possível comunicar com o Dropbox. expired_access_token
```

Conclusão:
- ✅ O arquivo `.secrets/oficina_env.sh` está sendo lido corretamente
- ✅ O Dropbox API está recebendo a requisição
- ❌ O token salvo expirou (tokens curtos do Dropbox expiram rápido)

## Passos

### Passo 1 — Gerar novo token no Dropbox App Console

1. Acesse https://www.dropbox.com/developers/apps
2. Abra o app **conector_ofi7**
3. Aba **Permissions** → confirme que tem estas permissões marcadas:
   - `files.metadata.read`
   - `files.content.read`
   - `files.content.write`
4. Aba **Settings** → seção **OAuth 2**
5. Clique em **Generate access token** (ou **Regenerate**)
6. Copie o token inteiro (começa com `sl.` normalmente)

### Passo 2 — Salvar o token no PythonAnywhere SEM ERRO de quebra de linha

Usaremos Python para salvar o token, evitando o problema de quebra de linha do shell.

No Bash Console do PythonAnywhere, rodar:

```bash
/home/ofi7ipojuca/.virtualenvs/venv/bin/python <<'PYEOF'
import os

# COLOQUE O NOVO TOKEN AQUI, ENTRE ASPAS DUPLAS, SEM QUEBRAR A LINHA
NOVO_TOKEN = "COLOQUE_O_NOVO_TOKEN_AQUI_NA_MESMA_LINHA"

content = f"""export DROPBOX_CILIA_ENABLED=true
export DROPBOX_ACCESS_TOKEN={NOVO_TOKEN}
export DROPBOX_CILIA_INPUT_PATH=/xml-cilia/entrada
export DROPBOX_CILIA_PROCESSED_PATH=/xml-cilia/processados
export DROPBOX_CILIA_ERROR_PATH=/xml-cilia/erro
"""

path = '/home/ofi7ipojuca/.secrets/oficina_env.sh'
with open(path, 'w') as f:
    f.write(content)
os.chmod(path, 0o600)
print("Arquivo salvo com sucesso!")
print("\nConteúdo salvo:")
with open(path, 'r') as f:
    for i, line in enumerate(f, 1):
        if 'ACCESS_TOKEN' in line:
            print(f"{i}: export DROPBOX_ACCESS_TOKEN=sl.xxxx...(tamanho: {len(line.strip())} chars)")
        else:
            print(f"{i}: {line.rstrip()}")
PYEOF
```

### Passo 3 — Testar manualmente

Rodar o comando de sync para validar:

```bash
bash -lc 'cd /home/ofi7ipojuca/ofi7 && source /home/ofi7ipojuca/.secrets/oficina_env.sh && /home/ofi7ipojuca/.virtualenvs/venv/bin/python manage.py sync_cilia_dropbox'
```

Resultados esperados:
- ✅ Tudo ok: "Nenhum XML novo encontrado" ou XMLs processados
- ❌ path/not_found: pastas /xml-cilia/* não existem → criar no Dropbox
- ❌ invalid_access_token: token copiado errado → repetir Passo 1 e 2

### Passo 4 — Criar a scheduled task de hora em hora

Na aba Tasks do PythonAnywhere:
- Frequência: **Hourly**
- Minute: **00**
- Command:
```bash
bash -lc 'cd /home/ofi7ipojuca/ofi7 && source /home/ofi7ipojuca/.secrets/oficina_env.sh && /home/ofi7ipojuca/.virtualenvs/venv/bin/python manage.py sync_cilia_dropbox'
```
- Clique em **Create**

## Riscos e mitigação

| Risco | Mitigação |
|---|---|
| Token expirar novamente | Dropbox tokens curtos expiram; gerar novo e repetir Passo 2 |
| Token ter quebras de linha ao colar | Sempre usar o script Python do Passo 2 (não usar nano/cat para editar token) |
| Usuário colar token no terminal sem trocar placeholder | O script Python imprime o tamanho do token salvo para conferência |

## Validação final

Após Passo 3:
- Sync roda sem erro de token
- Scheduled task aparece na aba Tasks
- Na próxima hora, a task executa e grava log em XMLImportJob (visível no painel do sistema)
