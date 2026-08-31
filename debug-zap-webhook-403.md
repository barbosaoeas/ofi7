#[OPEN] zap-webhook-403

## Sintoma
- Webhook da Zap API retorna HTTP 403.

## Expectativa
- Webhook retornar 200 e, quando aplicável, criar item na fila financeira.

## Hipóteses (falsificáveis)
1) O processo WSGI não está carregando o `ZAP_WEBHOOK_SECRET` correto (secret vazio ou diferente).
2) O header de assinatura usado na validação não está sendo extraído corretamente (header diferente do esperado).
3) O corpo bruto usado no HMAC não é o mesmo que chegou (body lido/alterado antes da validação, encoding ou middleware).
4) O algoritmo esperado pela Zap API difere do que estamos calculando (prefixo `sha256=`, base string com timestamp, etc.).
5) O 403 não vem da validação do webhook e sim de outra camada (roteamento/permite POST/CSRF/permission mixin).

## Próximas evidências a coletar
- Confirmar no runtime qual secret_len/hash da secret, headers presentes e raw_body_len, e qual branch retornou 403.

