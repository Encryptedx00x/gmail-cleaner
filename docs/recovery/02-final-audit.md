# Auditoria final do PR #1

Base: `origin/master`; commit `69ba511` preservado. Nenhuma conta Gmail ou OpenAI real foi acessada.

## Defeitos comprovados e correções

- Cache de remetente vinha antes da proteção de notas fiscais e podia gerar exclusão de mensagens importantes. Regras locais têm precedência; histórico isolado é apenas sugestão sem automação. Credenciais/certificados também precedem padrões de spam.
- Respostas do modelo não tinham validação local de tipos, categorias, ações e confiança. Há política independente do prompt; classificações inválidas caem em revisão, categorias protegidas são preservadas e exclusão de marketing/spam exige 0.85/0.90 no mínimo.
- Staging aceitava flags antigas inconsistentes, confiança baixa e exclusão de certificados. Agora revalida o cache; registros malformados podem ser revistos sem ação insegura.
- `mark-read --dry-run` autenticava e a simulação inicializava o banco. Previews usam leitura, não criam cache, não autenticam nos comandos offline e não escrevem tokens/logs. Leitores da sidebar também não inicializam cache. Novo cache usa journal DELETE para evitar sidecars WAL na leitura.
- Cache não estava vinculado à conta, e limite era aplicado por ação. Planos exigem conta correspondente e limite global; cache legado não vazio exige DB_PATH novo sem apagar o anterior.
- A UI perdia resultados de workers em reruns e não exibia o plano concreto. Estado por sessão persiste; IDs/ações são exibidos, confirmação é renovada ao mudar a prévia e snapshot divergente é rejeitado antes de autenticar.
- OAuth reutilizava tokens sem verificar escopo real persistido e gravava diretamente sobre o arquivo. Verificação de escopo antes de reutilizar, novo fluxo em loopback quando permitido e substituição atômica; falha de gravação preserva o token anterior. Previews usam credencial sem refresh token, impedindo renovação automática pelo SDK.
- Argumento de remetente admitia operadores Gmail. Endereço é validado antes do acesso e consultado como literal.
- Paginação podia consumir limite com IDs duplicados e aceitar página vazia como fim. IDs são únicos; tokens repetidos interrompem como erro, sem resultado parcial apresentado como completo.
- Quota 403 não era retentada; Retry-After era ignorado e havia sleep após última tentativa. Diferencia quota de permissão, respeita espera e encerra preservando a falha final.
- Falha no log contava uma mensagem já movida também como falha; agora não sugere repetir a movimentação. Exclusão permanente no helper requer confirmação explícita.
- Erro OpenAI bruto podia entrar em logs/cache; é reduzido à classe da falha. HTML de relatório escapa categorias legadas. Tokens alternativos e sidecars SQLite ficam ignorados pelo Git.
- Interface local configurada explicitamente em `127.0.0.1`, com proteções CORS/XSRF.

## Evidências

- `python -m pytest -q --tb=short`: **50 aprovados** em 5,3 s, Python 3.13.
- SQLite real temporário; comparação de hashes antes/depois do dry-run para cache/token; token substituído com falha simulada sem corrupção.
- Clientes de Gmail/OpenAI e OAuth sintéticos; conexões externas bloqueadas. Loopback interno é permitido somente para IPC do asyncio/Streamlit no Windows. Nenhuma operação sobre emails reais.
- Streamlit AppTest: resultado de execução e scan sobrevivem ao rerun, prévia visível e aprovação resetada ao mudar o escopo.
- `pip check`, `git diff --check` e ignore de tokens/sidecars: aprovados. Sem alteração de dependências nesta auditoria.
- Configuração carregada pelo Streamlit: `server.address = 127.0.0.1`.

## Decisão

**GO para merge como ferramenta local, com ressalva de integração externa**. OAuth real, acesso Gmail e classificação OpenAI real continuam não homologados. Cache legado não vazio deve ser preservado e substituído por novo DB_PATH antes de LIVE. Classificação continua sujeita a erro semântico; revisão humana do plano é necessária. PR permanece draft para decisão do proprietário.
