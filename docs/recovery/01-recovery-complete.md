# Checkpoint 01 — Gmail Cleaner recuperado

## Resultado

**FUNCIONAL COM RESSALVAS.** A aplicação e os comandos locais foram recuperados; o uso real da Gmail API e da OpenAI não foi homologado nesta tarefa. Nenhum login OAuth, acesso a conta pessoal ou alteração de mensagens foi realizado.

## Alterações

- Corrigidos os nomes e instruções da integração de IA para refletir o uso de OpenAI.
- Reduzido o escopo OAuth ao `gmail.modify` necessário ao conjunto atual de leitura e organização.
- `execute`, `delete-sender`, `mark-read` e as funções auxiliares de escrita usam dry-run por padrão. CLI exige `--live` para operações reais; cada gravação precisa de confirmação, com `execute --confirm` como exceção explícita para automação.
- A lixeira exige a confirmação exata `DELETE` mesmo com `SAFE_MODE=false`.
- Corrigidas dependências reportadas pelo `pip-audit`: `python-dotenv`, `Jinja2`, `Streamlit` e `Pillow`.
- Criados testes de CLI com mocks e documentação para configuração, testes e renovação do token OAuth após a redução de escopo.

## Evidências

- Ambientes isolados: Python 3.13 (`.venv`) e Python 3.11 (`.venv311`), ambos não rastreados.
- `python -m pytest -q`: 5 testes aprovados. Nenhum teste iniciou OAuth ou acessou Gmail.
- `python -m pip check`: sem dependências quebradas.
- `python -m pip_audit -r requirements.txt`: nenhuma vulnerabilidade conhecida nas dependências declaradas.
- `python main.py execute --help`: confirmou dry-run como padrão e flags `--dry-run` / `--live`.
- `git diff --check`: aprovado.
- Instalação validada no Python 3.13 após atualizar Pandas e Pydantic; também testada no Python 3.11.

## Pendências

- Validar o fluxo OAuth e uma leitura pequena com uma conta autorizada, fora do escopo desta etapa e sem autorização específica para acessar conta Gmail real.
- A classificação OpenAI requer `OPENAI_API_KEY`; não foi feita chamada externa.
- A conexão com serviços reais requer credenciais fornecidas pelo operador e ainda precisa de homologação autorizada.
