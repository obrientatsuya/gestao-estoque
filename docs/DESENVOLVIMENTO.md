# V7: Decisoes e Validacao

## Escopo

Redesign operacional dos dois modulos, sem reescrever as integracoes Tiny/Supabase.
Endpoints externos, payloads, regras de custo/estoque/vendas, paginacao e limites
das APIs foram preservados. As adicoes no servidor Coordenadas servem assets
locais `/ui/*` e configuracao publica `/ui/runtime-config.js`.
As credenciais deixaram o HTML/Python versionavel e passaram a configuracao privada.

## Interface

- Public Sans local, numerais tabulares, rotulos compactos e contraste entre dados
  principais e metadados; espacamento sem grandes areas promocionais.
- Fundo neutro, acoes em verde-petroleo e as cores estaveis de setor preservadas.
- Caixas principais retangulares, coordenada centralizada e espaco maior entre
  caixas/prateleiras; bordas seguem a ocupacao. SETOR permanece branco sobre cor.
- Tema claro sem gradiente escuro e tema escuro consistente nos dois modulos.
- Versao discreta no canto, fora da marca principal.
- Icones Lucide, foco visivel e feedback de clique/menu de 120-160 ms.
- Animacoes desligadas quando `prefers-reduced-motion` e solicitado.

Foram usadas as instrucoes locais de `revenue-centric-design` (reduzir carga
cognitiva, encurtar tarefas, estados verdadeiros e feedback rapido) e da skill
atual `taste-skill`. O upstream consultado nao fornecia a skill solicitada com o
nome `minimalist-ui`; foi usada a versao atual disponivel, sem fingir sua instalacao.
Nao foram aplicados mecanismos de venda, urgencia ou manipulacao ao fluxo interno.

## Eficiencia

- Filtro visual do mapa agrupado por animation frame, sem novas chamadas de API.
- Setores fora da tela usam `content-visibility`; componentes e fontes sao locais,
  sem dependencia de CDN em cada abertura.
- Imagens de paginas seguintes da nota usam carregamento lazy/decodificacao async.
- Quantidades tem debounce por item e uma fila de gravacao. A fila e necessaria
  porque as respostas trazem a nota inteira: uma resposta antiga nao pode restaurar
  a quantidade anterior de outro item.
- Troca de nota, impressao, desfazer e reset aguardam quantidades pendentes.
- Se uma gravacao falhar, a quantidade pendente continua disponivel para tentativa.
- Sao otimizacoes direcionadas; nao se afirma ganho percentual sem benchmark em
  carga real. Nao houve refatoracao indiscriminada da codebase.

## Validacao

- 38 testes Python existentes com clientes simulados e SQLite temporario.
- 5 testes Node da fila: itens diferentes, debounce, atualizacao em voo, repeticao
  apos erro e respostas com snapshots antigos da nota completa.
- Testes visuais no Chrome com fixtures sinteticas: claro/escuro, caixas, busca,
  auditoria, mapas, autocomplete com setas, conferencia, historico e configuracoes.
- Inspecao dos dois executaveis: perfis separados, sem access/refresh tokens
  empacotados e sem chave de publicacao no Leitor.
- Nenhuma impressora real ou alteracao de produto foi usada na validacao visual.

## Publicacao

`configuracao_privada/`, configs/tokens reais, executaveis, bancos, logs, PDFs,
diretorios recuperados e artefatos de build nao pertencem ao Git.
Exemplos em `config/` contem apenas placeholders. Screenshots sao sinteticas.
Licencas acompanham Public Sans e Lucide. A ferramenta de seguranca revisa o indice
Git e pode comparar bytes com as credenciais privadas da maquina sem revela-las:

```powershell
python tools/security_check.py --repo CAMINHO_DO_REPOSITORIO --private-root CAMINHO_PRIVADO
git diff --cached --stat
git commit -m "feat(v7): unify operational UI, local assets and safe distribution"
```

### Hipotese de Commit

`feat(v7): unify operational UI, local assets and safe distribution`

Hipotese: uma hierarquia consistente, menos decoracao e feedback imediato reduzem
o tempo de localizar/marcar itens sem mudar as regras do ERP. Validar em operacao
com tempo por cadastro/conferencia, erros corrigidos e consultas por caixa, sem
coletar telemetria automaticamente ou expor dados dos operadores.
