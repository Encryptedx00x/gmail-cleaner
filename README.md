# Gmail Cleaner

Aplicação Python com CLI e Streamlit para organizar uma caixa do Gmail usando heurísticas locais e OpenAI como classificador opcional. A integração atual usa OpenAI, não Claude.

## Fluxo

```
Scan → Heurísticas locais → OpenAI (ambíguos) → Staging/Dry-run → Confirmação manual → Execute
```

---

## 1. Setup Google Cloud

### 1.1 Criar projeto

1. Acesse [console.cloud.google.com](https://console.cloud.google.com)
2. Clique em **"Novo Projeto"** → dê um nome (ex: `gmail-cleaner`)
3. Selecione o projeto criado

### 1.2 Ativar Gmail API

1. Menu lateral → **APIs e Serviços** → **Biblioteca**
2. Busque por **"Gmail API"**
3. Clique em **Ativar**

### 1.3 Criar credenciais OAuth2

1. Menu lateral → **APIs e Serviços** → **Credenciais**
2. Clique em **"+ Criar credenciais"** → **"ID do cliente OAuth"**
3. Tipo de aplicativo: **"App para computador" (Desktop app)**
4. Nome: `gmail-cleaner`
5. Clique em **Criar**
6. Clique em **"Baixar JSON"**
7. Salve o arquivo como `credentials/credentials.json`

### 1.4 Configurar tela de consentimento (se necessário)

1. Menu lateral → **APIs e Serviços** → **Tela de consentimento OAuth**
2. Tipo: **Externo** → Criar
3. Preencha nome do app e email
4. O aplicativo solicita somente `gmail.modify`, necessário para as ações de organização disponíveis. O escopo `gmail.labels` adicional não é solicitado.
5. Usuários de teste: adicione seu Gmail

---

## 2. Instalação

```bash
# Clone ou entre na pasta
cd "Gmail Cleaner"

# Crie ambiente virtual
python -m venv .venv
source .venv/bin/activate      # Linux/Mac
.venv\Scripts\activate         # Windows

# Instale dependências
pip install -r requirements.txt
# Para executar os testes automatizados:
pip install -r requirements-dev.txt

# Configure variáveis de ambiente
cp .env.example .env
# Edite .env com suas chaves
```

### .env mínimo

```env
OPENAI_API_KEY=sk-...
GMAIL_CREDENTIALS_PATH=./credentials/credentials.json
GMAIL_TOKEN_PATH=./credentials/token.json
```

---

## 3. Primeiro uso

```bash
# 1. Scan completo (abre browser para autenticação na primeira vez)
python main.py scan

# 2. Scan limitado para teste
python main.py scan --limit 100

# 3. Ver plano de ação (sem executar nada)
python main.py staging

# 4. Simular execução (dry-run)
python main.py execute --dry-run

# 5. Executar de verdade (pede confirmação)
python main.py execute --category marketing

# 6. Gerar relatório
python main.py report
```

---

## 4. Referência de comandos

### `scan`
```bash
python main.py scan                          # scan completo
python main.py scan --limit 500             # limita a 500 emails
python main.py scan --query "is:unread"     # filtra por query Gmail
python main.py scan --skip-claude           # só heurísticas (mais rápido)
python main.py scan --batch-size 20         # lotes menores para Claude
```

### `classify`
```bash
python main.py classify                      # reclassifica emails 'unknown'
python main.py classify --batch-size 30
```

### `staging`
```bash
python main.py staging                       # plano completo
python main.py staging --category marketing  # filtra categoria
python main.py staging --action delete       # filtra ação
python main.py staging --preview 20          # mais exemplos
```

### `execute`
```bash
python main.py execute                                  # simula (padrão)
python main.py execute --category marketing             # simula só marketing
python main.py execute --live --category marketing      # LIVE: executa após confirmação
python main.py execute --live --confirm --category marketing  # LIVE: confirmação via flag
python main.py execute --live --action archive          # LIVE: arquiva após confirmação
```

### `show`
```bash
python main.py show priority                 # emails que exigem ação manual
python main.py show category certificates   # lista categoria
python main.py show category marketing --limit 50
```

### `delete-sender`
```bash
python main.py delete-sender "noreply@shopify.com"     # simula (padrão)
python main.py delete-sender "noreply@shopify.com" --live  # pede confirmação antes de mover para lixeira
```

### `mark-read`
```bash
python main.py mark-read --category marketing           # simula (padrão)
python main.py mark-read --category newsletter --live   # pede confirmação antes de marcar como lido
```

### `report`
```bash
python main.py report                        # HTML (padrão)
python main.py report --format json
python main.py report --output ./meu_relatorio.html
```

### `unsubscribe`
```bash
python main.py unsubscribe --dry-run         # lista links (padrão)
python main.py unsubscribe --limit 100
```

---

## 5. Categorias e ações

| Categoria | Ação padrão | Auto? | Descrição |
|---|---|---|---|
| `marketing` | delete | ✓ se conf ≥ 0.85 | Promoções, ofertas |
| `spam` | delete | ✓ se conf ≥ 0.90 | Spam/golpes |
| `newsletter` | archive | ✓ | Newsletters com unsubscribe |
| `transactional` | archive | ✓ | Notificações de sistemas |
| `certificate` | label | ✓ | Certificados de cursos |
| `course_access` | label | ✓ | Acesso a plataformas |
| `important` | keep | ✗ | Emails importantes |
| `invoice` | keep | ✗ | NF-e, boletos, contratos |
| `platform_credential` | keep | ✗ | Senhas, 2FA |
| `action_required` | keep | ✗ | Requer ação manual |
| `unknown` | review | ✗ | Não classificado |

---

## 6. Segurança e testes sem Gmail real

- **Deleções são sempre reversíveis**: emails vão para a lixeira, não são deletados permanentemente
- **Dry-run por padrão**: `execute`, `delete-sender` e `mark-read` só alteram a caixa com `--live`
- **Confirmação LIVE**: operações de escrita pedem confirmação. `execute --confirm` permite automação explícita; use após revisar o staging
- **Safe mode**: variável `SAFE_MODE=true` bloqueia ações sem confirmação
- **Log imutável**: todo email movido para lixeira é registrado em `reports/deleted_log.jsonl`
- **Categorias protegidas**: `invoice`, `important`, `platform_credential` nunca são deletadas automaticamente
- **Confiança mínima**: emails com confiança < 0.80 vão para revisão manual
- **Escopo OAuth mínimo**: somente `gmail.modify`; tokens locais são ignorados pelo Git

Os testes usam mocks e não autenticam no Gmail:

```bash
python -m pytest
```

Depois de reduzir o escopo OAuth, remova `credentials/token.json` e autentique novamente para emitir um token com o novo escopo. Os testes não iniciam OAuth.

---

## 7. Arquitetura

```
Scan → Heurísticas locais → [ambíguos] → OpenAI API (lotes de 30) → SQLite cache
                                                    ↓
                                            Staging (dry-run)
                                                    ↓
                                         Confirmação manual
                                                    ↓
                                            Executor (ações)
```

O cache SQLite evita reclassificar emails já vistos e aprende padrões por remetente.
