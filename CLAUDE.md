# gmail-cleaner

## O que é
CLI em Python para organizar Gmail em massa usando heurísticas + Claude API como classificador.

## Fluxo obrigatório
Scan → Heurísticas → Claude (ambíguos) → Staging → Aprovação manual → Execute

## Regras de segurança
- NUNCA deletar sem --confirm explícito
- Emails com confiança < 0.80 vão para revisão manual
- Nunca tocar em: nota fiscal, nfe, boleto, contrato, certificado

## Stack
- Python 3.11+
- Gmail API (OAuth2)
- Claude API com tool_use (claude-sonnet-4-20250514)
- SQLite para cache
- Typer para CLI
- Rich para logs/progress

## Variáveis de ambiente
Ver .env.example — ANTHROPIC_API_KEY e GMAIL_CREDENTIALS_PATH obrigatórias

## Ordem de implementação
auth → gmail client → heuristics → claude classifier → cache → pipeline → CLI
```

## Ordem correta para rodar

Para projeto **novo** (seu caso):
```
1. Crie a pasta do projeto
2. Crie o CLAUDE.md manualmente (conteúdo acima)
3. Abra o terminal no Antigravity dentro dessa pasta
4. rode: claude
5. Cole o prompt que montamos
```

Para projeto **existente** com código já criado:
```
1. Entre na pasta
2. rode: claude
3. Digite /init  ← ele lê o código e gera o CLAUDE.md
4. Revise o CLAUDE.md gerado e ajuste